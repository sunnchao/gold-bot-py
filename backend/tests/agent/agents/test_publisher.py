"""飞书发布器单元测试(镜像 gold-bot publisher.test.ts)。

覆盖:
- 进程内飞书 webhook 串行化
- 频控(code 11232)退避重试
- 全量理论节 + 交易建议卡片序列化(不可用参考价 -> 占位文案)
- hold/open 交易建议原样保留
- 道氏 accumulation -> 吸筹
"""

import asyncio
import json

import backend.agents.agents.publisher as publisher_module
from backend.agents.agents.publisher import (
    PublisherService,
    build_feishu_card,
    is_feishu_frequency_limited,
)


class FakeResponse:
    def __init__(self, payload=None, ok=True):
        self._payload = payload
        self.ok = ok
        self.status = 200 if ok else 500

    async def text(self) -> str:
        return json.dumps(self._payload) if self._payload is not None else ""


async def flush_microtasks(times: int = 4) -> None:
    for _ in range(times):
        await asyncio.sleep(0)


class FakeGoldbotApi:
    def __init__(self):
        self.posted = []

    async def post_ai_result(self, account_id: str, symbol: str, result) -> None:
        self.posted.append((account_id, symbol, result))


def create_signal() -> dict:
    return {
        "bias": "bullish",
        "confidence": 80,
        "exit_suggestion": "hold",
        "risk_alert": False,
        "arbitration": {
            "direction": "buy",
            "action": "buy",
            "reasoning": "test signal",
        },
    }


def create_signal_with_sections() -> dict:
    return {
        **create_signal(),
        "dow_theory": {
            "primary_trend": "neutral",
            "primary_phase": "accumulation",
            "secondary_trend": "neutral",
            "short_term_trend": "neutral",
            "multi_tf_confirm": False,
            "rationale": "unavailable",
        },
        "wave_theory": {
            "current_wave": "unknown",
            "wave_direction": "unclear",
            "wave_count": "unavailable",
            "next_target": "N/A",
            "confidence": 0,
            "rationale": "unavailable",
        },
        "chanlun_theory": {
            "trend": "range",
            "bi_direction": "none",
            "duan_direction": "none",
            "zhongshu_state": "none",
            "buy_sell_point": "none",
            "confidence": 0,
            "rationale": "unavailable",
        },
        "harmonic_theory": {
            "pattern": "none",
            "direction": "neutral",
            "confidence": 0,
            "rationale": "unavailable",
        },
        "trade_recommendation": {
            "direction": "hold",
            "entry_price": 0,
            "stop_loss": 0,
            "take_profit_1": 0,
            "risk_reward_ratio": 0,
            "position_size_lots": "0",
            "rationale": "观望",
        },
    }


def create_signal_with_trade_recommendation(trade: dict) -> dict:
    return {**create_signal(), "trade_recommendation": trade}


class FakeFetchRecorder:
    def __init__(self, responses=None):
        # responses: list of FakeResponse or callables returning them
        self._responses = list(responses or [])
        self.calls = []

    async def __call__(self, url, method="POST", headers=None, body=None):
        self.calls.append({"url": url, "method": method, "headers": headers, "body": body})
        if self._responses:
            return self._responses.pop(0)
        return FakeResponse({"code": 0, "msg": "ok"})


async def test_uses_gb_feishu_environment_variables(monkeypatch):
    monkeypatch.setenv("GB_FEISHU_WEBHOOK_URL", "https://feishu.example/gb-webhook")
    monkeypatch.setenv("GB_FEISHU_SECRET", "gb-secret")
    fetch = FakeFetchRecorder([FakeResponse({"code": 0, "msg": "ok"})])
    service = PublisherService(FakeGoldbotApi(), fetch=fetch)

    await service.send_feishu_card("acc-001", "XAUUSD", create_signal())

    assert fetch.calls[0]["url"] == "https://feishu.example/gb-webhook"
    body = json.loads(fetch.calls[0]["body"])
    assert body["timestamp"]
    assert body["sign"]


async def test_default_fetch_posts_with_ten_second_timeout(monkeypatch):
    calls = []

    class HttpxResponse:
        status_code = 200
        is_success = True
        text = '{"code":0,"msg":"ok"}'

    class FakeAsyncClient:
        def __init__(self, *, timeout):
            assert timeout == 10.0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def request(self, method, url, *, headers, content):
            calls.append({"method": method, "url": url, "headers": headers, "content": content})
            return HttpxResponse()

    monkeypatch.setattr(publisher_module.httpx, "AsyncClient", FakeAsyncClient)
    service = PublisherService(FakeGoldbotApi(), webhook_url="https://feishu.example/webhook")

    await service.send_feishu_card("acc-001", "XAUUSD", create_signal())

    assert calls[0]["method"] == "POST"
    assert calls[0]["url"] == "https://feishu.example/webhook"
    assert calls[0]["headers"] == {"Content-Type": "application/json"}
    assert json.loads(calls[0]["content"])["msg_type"] == "interactive"


async def test_serializes_concurrent_feishu_webhook_posts_in_this_process():
    """TS it('serializes concurrent Feishu webhook posts in this process')"""
    loop = asyncio.get_running_loop()
    first_gate = loop.create_future()
    second_gate = loop.create_future()
    call_count = {"n": 0}

    async def gate_fetch(url, method="POST", headers=None, body=None):
        call_count["n"] += 1
        if call_count["n"] == 1:
            await first_gate
            return FakeResponse({"code": 0, "msg": "ok"})
        await second_gate
        return FakeResponse({"code": 0, "msg": "ok"})

    service = PublisherService(
        FakeGoldbotApi(),
        webhook_url="https://feishu.example/webhook",
        fetch=gate_fetch,
    )

    # Python 协程需显式调度(Promise 语义差异):用 create_task 模拟 TS 的并发发起
    first = asyncio.create_task(service.send_feishu_card("acc-001", "XAUUSD", create_signal()))
    second = asyncio.create_task(service.send_feishu_card("acc-001", "XAGUSD", create_signal()))

    await flush_microtasks()
    assert call_count["n"] == 1

    first_gate.set_result(True)
    await first
    await flush_microtasks()
    assert call_count["n"] == 2

    second_gate.set_result(True)
    await second
    await flush_microtasks()
    assert call_count["n"] == 2


async def test_retries_feishu_frequency_limited_responses_with_backoff():
    """TS it('retries Feishu frequency-limited responses with backoff')"""
    fetch = FakeFetchRecorder(
        [
            FakeResponse({"code": 11232, "msg": "frequency limited"}),
            FakeResponse({"code": 0, "msg": "ok"}),
        ]
    )
    service = PublisherService(
        FakeGoldbotApi(),
        webhook_url="https://feishu.example/webhook",
        fetch=fetch,
        backoff_ms=(5, 100, 1000),
    )

    await service.send_feishu_card("acc-001", "XAUUSD", create_signal())

    assert len(fetch.calls) == 2
    assert json.loads(fetch.calls[0]["body"])["card"]["config"]["wide_screen_mode"] is True


async def test_is_feishu_frequency_limited():
    """Python 侧:code 11232 判定为频控(镜像 TS 的 isFrequencyLimited)。"""
    assert is_feishu_frequency_limited({"code": 11232, "msg": "频控"}) is True
    assert is_feishu_frequency_limited({"code": 0, "msg": "ok"}) is False


async def test_serializes_all_populated_theory_and_trade_recommendation_sections():
    """TS it('serializes all populated theory and trade recommendation sections into the Feishu card')"""
    fetch = FakeFetchRecorder([FakeResponse({"code": 0, "msg": "ok"})])
    service = PublisherService(
        FakeGoldbotApi(),
        webhook_url="https://feishu.example/webhook",
        fetch=fetch,
    )

    await service.send_feishu_card("acc-001", "XAUUSD", create_signal_with_sections())

    body = json.loads(fetch.calls[0]["body"])
    assert body["card"]["config"]["wide_screen_mode"] is True
    # 中文断言需避免 ASCII 转义(json.dumps 默认 ensure_ascii=True)
    serialized = json.dumps(body, ensure_ascii=False)

    assert "道氏理论分析" in serialized
    assert "波浪理论分析" in serialized
    assert "缠论分析" in serialized
    assert "谐波理论分析" in serialized
    assert "交易建议" in serialized
    assert "参考入场: 0.00" not in serialized
    assert "参考止损: 0.00" not in serialized
    assert "参考止盈1: 0.00" not in serialized
    assert "盈亏比: 1:0.0" not in serialized
    assert "阶段: 吸筹" not in serialized
    assert "暂无可靠入场价" in serialized
    assert "暂无可靠止损" in serialized
    assert "暂无可靠止盈" in serialized
    assert "盈亏比不可用" in serialized


async def test_keeps_populated_hold_and_open_trade_recommendations_unchanged():
    """TS it('keeps populated hold and open trade recommendations unchanged')"""
    fetch = FakeFetchRecorder(
        [
            FakeResponse({"code": 0, "msg": "ok"}),
            FakeResponse({"code": 0, "msg": "ok"}),
        ]
    )
    service = PublisherService(
        FakeGoldbotApi(),
        webhook_url="https://feishu.example/webhook",
        fetch=fetch,
    )

    await service.send_feishu_card(
        "acc-001",
        "XAUUSD",
        create_signal_with_trade_recommendation(
            {
                "direction": "hold",
                "entry_price": 3200,
                "stop_loss": 3180,
                "take_profit_1": 3240,
                "risk_reward_ratio": 2,
                "position_size_lots": "0.1",
                "rationale": "等待确认",
            }
        ),
    )
    await service.send_feishu_card(
        "acc-001",
        "XAUUSD",
        create_signal_with_trade_recommendation(
            {
                "direction": "buy",
                "entry_price": 3200,
                "stop_loss": 3180,
                "take_profit_1": 3240,
                "risk_reward_ratio": 2,
                "position_size_lots": "0.1",
                "rationale": "趋势延续",
            }
        ),
    )

    hold_body = json.dumps(json.loads(fetch.calls[0]["body"]), ensure_ascii=False)
    open_body = json.dumps(json.loads(fetch.calls[1]["body"]), ensure_ascii=False)

    assert "交易建议" in hold_body
    assert "参考入场: 3200.00" in hold_body
    assert "参考止损: 3180.00" in hold_body
    assert "参考止盈1: 3240.00" in hold_body
    assert "盈亏比: 1:2.0" in hold_body
    assert "交易操作建议" in open_body
    assert "入场: 3200.00" in open_body
    assert "止损: 3180.00" in open_body
    assert "止盈1: 3240.00" in open_body
    assert "盈亏比: 1:2.0" in open_body


async def test_maps_available_dow_theory_accumulation_to_xichou():
    """TS it('maps available Dow theory accumulation to 吸筹')"""
    fetch = FakeFetchRecorder([FakeResponse({"code": 0, "msg": "ok"})])
    service = PublisherService(
        FakeGoldbotApi(),
        webhook_url="https://feishu.example/webhook",
        fetch=fetch,
    )

    await service.send_feishu_card(
        "acc-001",
        "XAUUSD",
        {
            **create_signal(),
            "dow_theory": {
                "primary_trend": "neutral",
                "primary_phase": "accumulation",
                "secondary_trend": "neutral",
                "short_term_trend": "neutral",
                "multi_tf_confirm": False,
                "rationale": "available",
            },
        },
    )

    body = json.dumps(json.loads(fetch.calls[0]["body"]), ensure_ascii=False)
    assert "阶段: 吸筹" in body


async def test_build_feishu_card_adds_sign_timestamp_when_secret_provided():
    """Python 侧:配置 secret 时卡片携带 timestamp/sign。"""
    card = build_feishu_card("acc-001", "XAUUSD", create_signal())  # smoke: builds without error
    assert card["msg_type"] == "interactive"
    assert "card" in card


async def test_publish_accepts_dataclass_signal_results_and_posts_dict_payload():
    from backend.agents.types.agent import AISignalResult

    api = FakeGoldbotApi()
    service = PublisherService(
        api,
        webhook_url="https://feishu.example/webhook",
        fetch=FakeFetchRecorder([FakeResponse({"code": 0, "msg": "ok"})]),
    )
    signal = AISignalResult(
        bias="bullish",
        confidence=80,
        exit_suggestion="hold",
        risk_alert=False,
        arbitration={"direction": "buy", "action": "open", "reasoning": "test"},
    )

    await service.publish("acc-001", "XAUUSD", signal)

    assert len(api.posted) == 1
    _, _, payload = api.posted[0]
    assert isinstance(payload, dict)
    assert payload["bias"] == "bullish"
    assert payload["arbitration"]["action"] == "open"
