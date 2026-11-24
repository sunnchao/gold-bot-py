"""AI 路由契约(镜像 routes/ai.spec.ts + ai-result-method.spec.ts)。"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from backend.api.app import create_api_app
from backend.api.routes.ai.index import _notify_ai_result
from backend.persistence.store import EaStore, create_in_memory_store

pytestmark = pytest.mark.contract

ACCOUNT_ID = "90011087"
TOKEN = "fixture-user-token"


def make_app(**options) -> TestClient:
    store = options.pop("store", None) or create_in_memory_store()
    defaults = {
        "store": store,
        "valid_tokens": {TOKEN},
        "token_accounts": {TOKEN: {ACCOUNT_ID}},
        "admin_tokens": set(),
        "now_iso": lambda: "2026-04-13T08:00:00.000Z",
    }
    return TestClient(create_api_app({**defaults, **options}))


def headers() -> dict:
    return {"X-API-Token": TOKEN}


async def test_ai_result_notification_uses_dict_dependencies() -> None:
    class RecordingFeishu:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        async def send(self, payload: dict) -> bool:
            self.calls.append(payload)
            return True

    feishu = RecordingFeishu()
    _notify_ai_result(
        {"discord": None, "feishu": feishu},
        ACCOUNT_ID,
        "XAUUSD",
        {"mode": "approve", "side": "buy", "confidence": 82},
        "buy",
    )
    await asyncio.sleep(0)

    assert feishu.calls == [
        {
            "title": "GOLD-BOT",
            "content": "[GOLD-BOT] AI Signal: XAUUSD buy (mode=approve confidence=82.0 account=90011087)",
        }
    ]


# ---------------------------------------------------------------- analysis_payload 路由


async def test_allows_post_legacy_analysis_payload_like_go_handler() -> None:
    client = make_app()
    response = client.post(f"/api/analysis_payload/{ACCOUNT_ID}", json={}, headers=headers())
    assert response.status_code == 200
    body = response.json()
    assert body["account"]["account_id"] == ACCOUNT_ID
    assert body["market"]["symbol"] == "XAUUSD"
    assert body["status"] == "OK"


async def test_allows_put_v2_analysis_payload_like_go_handler() -> None:
    client = make_app()
    response = client.put(f"/api/v2/analysis_payload/{ACCOUNT_ID}/XAUUSD", json={}, headers=headers())
    assert response.status_code == 200
    body = response.json()
    assert body["account"]["account_id"] == ACCOUNT_ID
    assert body["market"]["symbol"] == "XAUUSD"
    assert body["status"] == "OK"


async def test_analysis_payload_requires_token_and_api_account() -> None:
    client = make_app()
    no_token = client.post(f"/api/analysis_payload/{ACCOUNT_ID}", json={})
    assert no_token.status_code == 401
    assert no_token.json() == {"status": "ERROR", "message": "invalid token"}

    other = client.post("/api/analysis_payload/99999999", json={}, headers=headers())
    assert other.status_code == 403
    assert other.json() == {"status": "ERROR", "message": "forbidden"}


async def test_unknown_ai_path_returns_404() -> None:
    client = make_app()
    response = client.post("/api/analysis_payload/90011087/extra", json={}, headers=headers())
    assert response.status_code == 404
    assert response.json() == {"status": "ERROR", "message": "not found"}


# ---------------------------------------------------------------- analysis_payload 结构


def ea_headers() -> dict:
    return {"X-API-Token": TOKEN, "Content-Type": "application/json"}


async def inject_heartbeat(client: TestClient, extra: dict | None = None) -> None:
    payload = {
        "account_id": ACCOUNT_ID,
        "balance": 10000,
        "equity": 10500,
        "margin": 500,
        "free_margin": 10000,
        "market_open": True,
        "is_trade_allowed": True,
        "server_time": "2026.04.13 08:00:00",
    }
    if extra:
        payload.update(extra)
    response = client.post("/heartbeat", json=payload, headers=ea_headers())
    assert response.status_code == 200


async def inject_tick(client: TestClient, extra: dict | None = None) -> None:
    payload = {"account_id": ACCOUNT_ID, "symbol": "XAUUSD", "bid": 3335.5, "ask": 3335.7, "spread": 21}
    if extra:
        payload.update(extra)
    response = client.post("/tick", json=payload, headers=ea_headers())
    assert response.status_code == 200


async def test_analysis_payload_assembles_full_snapshot() -> None:
    client, store = await make_app_with_store()
    await store.save_registration(
        {"account_id": ACCOUNT_ID, "broker": "ICMarkets", "currency": "USD", "leverage": 500}
    )
    await inject_heartbeat(client)
    await inject_tick(client)
    await store.save_bars(
        {
            "account_id": ACCOUNT_ID,
            "symbol": "XAUUSD",
            "timeframe": "H1",
            "bars": [
                {
                    "time": i,
                    "open": 3300.0 + i,
                    "high": 3301.0 + i,
                    "low": 3299.0 + i,
                    "close": 3300.5 + i,
                    "volume": 100,
                }
                for i in range(40)
            ],
        }
    )
    response = client.post(f"/api/analysis_payload/{ACCOUNT_ID}", json={}, headers=headers())
    assert response.status_code == 200
    body = response.json()
    assert body["account"]["account_id"] == ACCOUNT_ID
    assert body["account"]["broker"] == "ICMarkets"
    assert body["account"]["connected"] is True
    assert body["account"]["leverage"] == 500
    assert body["market"]["bid"] == 3335.5
    assert body["market"]["ask"] == 3335.7
    assert body["market"]["symbol"] == "XAUUSD"
    assert body["market_status"]["tradeable"] is True
    assert body["market_status"]["stale"] is False
    assert body["market_status"]["is_trade_allowed"] is True
    assert body["market_status"]["market_open"] is True
    assert set(body["market_filters"].keys()) == {"blocked", "blocking", "warnings", "reason_codes"}
    assert body["market_filters"]["blocked"] is True  # tick 无 time → tick.missing
    assert "tick.missing" in body["market_filters"]["reason_codes"]
    assert body["status"] == "OK"
    assert body["bars"]["H1"][-1]["close"] == 3339.5
    # 富化指标写入
    assert "ema20" in body["bars"]["H1"][-1]
    assert "atr" in body["bars"]["H1"][-1]
    assert "fib_618" in body["bars"]["H1"][-1]
    # indicator packs: <20 根 → None
    assert body["indicators"]["M15"] is None
    assert body["indicators"]["H1"]["bars_count"] == 40
    # trend_context 有默认值
    assert body["trend_context"]["consensus_direction"] in ("BULL", "BEAR", "NEUTRAL")
    # timestamp 转上海时区
    assert body["timestamp"].endswith("+08:00")
    assert body["positions"] == []
    assert body["harmonic_context"]["h4_patterns"] == []
    assert body["harmonic_context"]["active_pattern"] is None
    assert body["harmonic_context"]["direction_bias"] == "neutral"
    assert body["smc_context"]["h4_obs"] == []
    assert "h1_breaks" in body["smc_context"]
    assert body["candlestick_patterns"]["h4"] == []
    assert body["candlestick_patterns"]["m30"] == []
    assert body["candlestick_patterns"]["h1"] == ["three_white_soldiers"]


async def make_app_with_store() -> tuple[TestClient, EaStore]:
    store = create_in_memory_store()
    return make_app(store=store), store


async def test_analysis_payload_market_status_stale_when_tick_old() -> None:
    client, store = await make_app_with_store()
    await store.save_heartbeat(
        {
            "account_id": ACCOUNT_ID,
            "market_open": True,
            "is_trade_allowed": True,
            "last_heartbeat_at": "2026-04-13T07:45:00.000Z",
            "server_time": "2026.04.13 08:00:00",
        }
    )
    # tick 的 time-only 时间 + 服务端日期组合(rollover 语义)
    await store.save_tick(
        {
            "account_id": ACCOUNT_ID,
            "symbol": "XAUUSD",
            "bid": 3335.5,
            "ask": 3335.7,
            "time": "08:00:00",
            "received_at": "2026-04-13T07:30:00.000Z",
        }
    )
    response = client.post(f"/api/analysis_payload/{ACCOUNT_ID}", json={}, headers=headers())
    assert response.status_code == 200
    status = response.json()["market_status"]
    # tick 接收时间 7:30,now 8:00,超过 15min TTL → stale
    assert status["stale"] is True
    assert status["stale_reason"] == "tick_stale"
    assert status["tick_age_ms"] == 30 * 60 * 1000


async def test_strategy_mapping_filters_unknown_values() -> None:
    client, store = await make_app_with_store()
    await store.save_registration(
        {
            "account_id": ACCOUNT_ID,
            "strategy_mapping": {
                "20250231": "pullback",
                "20250232": "not-a-strategy",
                "99999999": "divergence",
            },
        }
    )
    response = client.post(f"/api/analysis_payload/{ACCOUNT_ID}", json={}, headers=headers())
    assert response.status_code == 200
    mapping = response.json()["strategy_mapping"]
    # TS: defaults 与注册合并,再按 allowed keys + 合法策略名过滤
    assert mapping == {
        "20250231": "pullback",
        "20250233": "divergence",
        "20250234": "breakout_pyramid",
        "20250235": "counter_pullback",
        "20250236": "range",
        "20250238": "ai_signal",
    }


# ---------------------------------------------------------------- ai_result 路由


async def test_accepts_non_post_ai_result_like_go_handler() -> None:
    client, store = await make_app_with_store()
    response = client.put(
        f"/api/ai_result/{ACCOUNT_ID}",
        json={"bias": "bullish", "confidence": 82},
        headers=headers(),
    )
    assert response.status_code == 200
    assert response.json() == {"status": "OK", "received": True}
    results = await store.get_ai_results(ACCOUNT_ID)
    assert len(results) == 1
    assert results[0]["symbol"] == "XAUUSD"
    assert results[0]["bias"] == "bullish"
    assert results[0]["confidence"] == 82


async def test_accepts_non_post_v2_ai_result_like_go_handler() -> None:
    client, store = await make_app_with_store()
    response = client.patch(
        f"/api/v2/ai_result/{ACCOUNT_ID}/GBPJPY",
        json={"bias": "bearish", "confidence": 64},
        headers=headers(),
    )
    assert response.status_code == 200
    assert response.json() == {"status": "OK", "received": True}
    results = await store.get_ai_results(ACCOUNT_ID)
    assert len(results) == 1
    assert results[0]["symbol"] == "GBPJPY"
    assert results[0]["bias"] == "bearish"
    assert results[0]["confidence"] == 64


async def test_ai_result_requires_valid_token() -> None:
    client = make_app()
    response = client.post(f"/api/ai_result/{ACCOUNT_ID}", json={})
    assert response.status_code == 401
    assert response.json() == {"status": "ERROR", "message": "invalid token"}


async def test_ai_result_rejects_invalid_json_strictly() -> None:
    client = make_app()
    response = client.post(
        f"/api/ai_result/{ACCOUNT_ID}",
        data="[]",
        headers={"Content-Type": "application/json", **headers()},
    )
    assert response.status_code == 400
    assert response.json() == {"status": "ERROR", "message": "invalid JSON"}


async def test_ai_result_with_trade_plan_returns_decision_and_risk_gate() -> None:
    client, store = await make_app_with_store()
    await store.save_registration(
        {"account_id": ACCOUNT_ID, "leverage": 500}
    )
    await store.save_heartbeat(
        {
            "account_id": ACCOUNT_ID,
            "equity": 10500,
            "free_margin": 10000,
            "market_open": True,
            "is_trade_allowed": True,
            "server_time": "2026.04.13 08:00:00",
        }
    )
    await store.save_tick(
        {"account_id": ACCOUNT_ID, "symbol": "XAUUSD", "bid": 3335.5, "ask": 3335.7, "spread": 21}
    )
    response = client.post(
        f"/api/ai_result/{ACCOUNT_ID}",
        json={
            "bias": "bullish",
            "suggested_sl": 3320,
            "suggested_tp": 3360,
            "trade_plan": {
                "schema_version": "trade_plan.v1",
                "decision_id": "d-100",
                "account_id": ACCOUNT_ID,
                "symbol": "XAUUSD",
                "mode": "approve",
                "side": "buy",
                "confidence": 82,
                "expires_at": "2026-04-13T10:00:00.000Z",
                "reason_codes": ["trend.bullish"],
                "narrative": "bullish momentum",
                "add_on": False,
                "entry_zone": {"min": 3330.0, "max": 3338.0},
                "stop_loss": 3320.0,
                "take_profit": [3360.0],
                "max_lots": 0.5,
            },
        },
        headers=headers(),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "OK"
    assert body["received"] is True
    assert body["decision"]["decision_id"] == "d-100"
    assert body["decision"]["symbol"] == "XAUUSD"
    assert body["decision"]["mode"] == "approve"
    assert "risk_gate" in body
    assert body["risk_gate"]["mode"] == "approve"
    assert body["trade_plan_validation"] == {"valid": True}
    # 决策时间线落库(镜像 TS:filter 必带 account_id)
    events = await store.list_decision_events({"account_id": ACCOUNT_ID})
    stages = {event["stage"] for event in events}
    assert {"ai_result", "risk_gate"} <= stages


async def test_ai_result_trade_plan_validation_error() -> None:
    client, store = await make_app_with_store()
    response = client.post(
        f"/api/ai_result/{ACCOUNT_ID}",
        json={
            "bias": "bullish",
            "trade_plan": {
                "schema_version": "trade_plan.v1",
                "decision_id": "d-bad",
                "account_id": ACCOUNT_ID,
                "symbol": "XAUUSD",
                "mode": "approve",
                "side": "buy",
                "confidence": 82,
                "expires_at": "2026-04-13T10:00:00.000Z",
                "reason_codes": ["x"],
                "narrative": "n",
                "entry_zone": {"min": 0, "max": 0},
                "stop_loss": 0,
                "take_profit": [],
                "max_lots": 0,
            },
        },
        headers=headers(),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "OK"
    assert body["received"] is True
    assert body["trade_plan_validation"]["valid"] is False
    # 无效 trade_plan → 不写决策时间线
    events = await store.list_decision_events({"account_id": ACCOUNT_ID})
    assert events == []


async def test_ai_result_triggers_sse_events() -> None:
    client, store = await make_app_with_store()
    published: list[dict] = []
    hub = client.app.state.events
    hub.subscribe(published.append)

    response = client.post(
        f"/api/ai_result/{ACCOUNT_ID}",
        json={"bias": "bullish", "confidence": 82, "suggested_sl": 0, "suggested_tp": 0},
        headers=headers(),
    )
    assert response.status_code == 200
    # suggested_sl/tp 全 0 → ai_analysis_failed + ai_result 事件
    types = [event["event_type"] for event in published]
    assert "ai_analysis_failed" in types
    assert "ai_result" in types
    failed = next(event for event in published if event["event_type"] == "ai_analysis_failed")
    assert failed["source"] == "api.ai_result"
    assert failed["account_id"] == ACCOUNT_ID


async def test_ai_result_risk_alert_queues_close_command() -> None:
    client, store = await make_app_with_store()
    await store.save_positions(
        {
            "account_id": ACCOUNT_ID,
            "symbol": "XAUUSD",
            "positions": [
                {
                    "ticket": 91001,
                    "symbol": "XAUUSD",
                    "type": "BUY",
                    "lots": 0.1,
                    "open_price": 3330.0,
                    "open_time": 1735689600,
                    "comment": "",
                }
            ],
        }
    )
    response = client.post(
        f"/api/ai_result/{ACCOUNT_ID}",
        json={"risk_alert": True, "exit_suggestion": "close_all", "alert_reason": "crash"},
        headers=headers(),
    )
    assert response.status_code == 200
    assert response.json() == {"status": "OK", "received": True}
    commands = await store.list_commands(ACCOUNT_ID)
    assert len(commands) == 1
    assert commands[0]["action"] == "CLOSE_ALL"
    assert commands[0]["source"] == "ai_risk_alert"
    assert commands[0]["reason"] == "AI风险警报(全平): crash"


async def test_ai_result_close_and_partial_close_queue_per_ticket_symbol_market_close() -> None:
    clock = ["2026-04-13T08:00:00.000Z"]
    store = create_in_memory_store()
    client = make_app(store=store, now_iso=lambda: clock[0])
    await store.save_registration({"account_id": ACCOUNT_ID, "leverage": 500})
    await store.save_heartbeat(
        {
            "account_id": ACCOUNT_ID,
            "equity": 10000,
            "free_margin": 9500,
            "market_open": True,
            "is_trade_allowed": True,
            "server_time": "2026.04.13 08:00:00",
        }
    )
    await store.save_tick(
        {
            "account_id": ACCOUNT_ID,
            "symbol": "XAUUSD",
            "bid": 3625.0,
            "ask": 3625.2,
            "spread": 0.2,
            "time": "2026-04-13T07:59:30.000Z",
        }
    )
    await store.save_positions(
        {
            "account_id": ACCOUNT_ID,
            "symbol": "XAUUSD",
            "positions": [
                {
                    "ticket": 1001,
                    "symbol": "XAUUSD",
                    "type": "BUY",
                    "order_class": "market",
                    "lots": 0.10,
                    "open_price": 3640.0,
                    "open_time": 1735689600,
                    "comment": "",
                },
                {
                    "ticket": 1002,
                    "symbol": "XAUUSD",
                    "type": "SELL_LIMIT",
                    "order_class": "pending",
                    "lots": 0.10,
                    "open_price": 3660.0,
                    "open_time": 1735689600,
                    "comment": "",
                },
                {
                    "ticket": 2001,
                    "symbol": "SILVERM#",
                    "type": "BUY",
                    "order_class": "market",
                    "lots": 0.20,
                    "open_price": 35.0,
                    "open_time": 1735689600,
                    "comment": "",
                },
            ],
        }
    )

    # Low confidence (< 50) must NOT queue close
    low_conf = client.post(
        f"/api/v2/ai_result/{ACCOUNT_ID}/XAUUSD",
        json={"risk_alert": True, "exit_suggestion": "close", "confidence": 35, "alert_reason": "weak"},
        headers=headers(),
    )
    assert low_conf.status_code == 200
    assert await store.list_commands(ACCOUNT_ID) == []

    # High confidence partial_close + trade_plan(mode="reduce") queues half lots only on XAUUSD market ticket
    partial_resp = client.post(
        f"/api/v2/ai_result/{ACCOUNT_ID}/XAUUSD",
        json={
            "risk_alert": True,
            "exit_suggestion": "partial_close",
            "confidence": 78,
            "alert_reason": "trim risk",
            "trade_plan": {
                "schema_version": "trade_plan.v1",
                "decision_id": "d-close-1",
                "account_id": ACCOUNT_ID,
                "symbol": "XAUUSD",
                "mode": "reduce",
                "side": "none",
                "confidence": 78,
                "expires_at": "2099-04-13T10:00:00.000Z",
                "reason_codes": ["risk.trim"],
                "narrative": "reduce half",
                "add_on": False,
                "entry_zone": {"min": 0.0, "max": 0.0},
                "stop_loss": 0.0,
                "take_profit": [],
                "max_lots": 0.0,
            },
        },
        headers=headers(),
    )
    assert partial_resp.status_code == 200
    cmds = await store.list_commands(ACCOUNT_ID)
    assert len(cmds) == 1
    assert cmds[0]["action"] == "CLOSE"
    assert cmds[0]["ticket"] == 1001
    assert cmds[0]["symbol"] == "XAUUSD"
    assert cmds[0]["lots"] == 0.05

    # risk_alert=False must NOT queue close even with confidence=85
    no_alert = client.post(
        f"/api/v2/ai_result/{ACCOUNT_ID}/XAUUSD",
        json={"risk_alert": False, "exit_suggestion": "close", "confidence": 85, "alert_reason": "no alert"},
        headers=headers(),
    )
    assert no_alert.status_code == 200
    assert len(await store.list_commands(ACCOUNT_ID)) == 1

    # Duplicate close for already queued/cooled ticket 1001 must be deduplicated within 240s
    clock[0] = "2026-04-13T08:00:01.000Z"
    dedup_resp = client.post(
        f"/api/v2/ai_result/{ACCOUNT_ID}/XAUUSD",
        json={
            "risk_alert": True,
            "exit_suggestion": "close",
            "confidence": 82,
            "alert_reason": "duplicate signal",
        },
        headers=headers(),
    )
    assert dedup_resp.status_code == 200
    assert len(await store.list_commands(ACCOUNT_ID)) == 1

    # Non-cooled ticket 1003 receives full-lot CLOSE
    await store.save_positions(
        {
            "account_id": ACCOUNT_ID,
            "symbol": "XAUUSD",
            "positions": [
                {
                    "ticket": 1003,
                    "symbol": "XAUUSD",
                    "type": "SELL",
                    "order_class": "market",
                    "lots": 0.08,
                    "open_price": 3630.0,
                    "open_time": 1735689600,
                    "comment": "",
                }
            ],
        }
    )
    clock[0] = "2026-04-13T08:00:05.000Z"
    close_resp = client.post(
        f"/api/v2/ai_result/{ACCOUNT_ID}/XAUUSD",
        json={
            "risk_alert": True,
            "exit_suggestion": "close",
            "confidence": 82,
            "alert_reason": "reversal exit",
        },
        headers=headers(),
    )
    assert close_resp.status_code == 200
    cmds_after = await store.list_commands(ACCOUNT_ID)
    assert len(cmds_after) == 2
    assert cmds_after[-1]["action"] == "CLOSE"
    assert cmds_after[-1]["ticket"] == 1003
    assert cmds_after[-1]["symbol"] == "XAUUSD"
    assert cmds_after[-1]["lots"] == 0.08


async def test_pending_gate_rejection_records_telemetry_and_metrics() -> None:
    store = create_in_memory_store()
    client = make_app(store=store, now_iso=lambda: "2026-04-13T08:00:00.000Z")
    await store.save_registration({"account_id": ACCOUNT_ID, "leverage": 500, "ai_symbols": ["XAUUSD"]})
    await store.save_heartbeat(
        {
            "account_id": ACCOUNT_ID,
            "equity": 10000,
            "free_margin": 9500,
            "market_open": True,
            "is_trade_allowed": True,
            "server_time": "2026.04.13 08:00:00",
        }
    )
    await store.save_tick(
        {
            "account_id": ACCOUNT_ID,
            "symbol": "XAUUSD",
            "bid": 3335.5,
            "ask": 3335.7,
            "spread": 0.2,
            "time": "2026-04-13T07:59:30.000Z",
        }
    )
    await store.save_bars(
        {
            "account_id": ACCOUNT_ID,
            "symbol": "XAUUSD",
            "timeframe": "H1",
            "bars": [{"close": 3335, "ema20": 3335, "ema50": 3335, "adx": 10, "atr": 2.0}],
        }
    )

    resp = client.post(
        f"/api/v2/ai_result/{ACCOUNT_ID}/XAUUSD",
        json={
            "bias": "bullish",
            "confidence": 82,
            "trade_plan": {
                "schema_version": "trade_plan.v1",
                "decision_id": "d-telemetry-1",
                "account_id": ACCOUNT_ID,
                "symbol": "XAUUSD",
                "mode": "approve",
                "side": "buy",
                "confidence": 82,
                "execution_type": "market",
                "requested_order_type": "market",
                "expires_at": "2099-04-13T10:00:00.000Z",
                "reason_codes": ["mode.approve", "side.buy"],
                "narrative": "too far from market",
                "add_on": False,
                "entry_zone": {"min": 3320.0, "max": 3320.0},
                "stop_loss": 3310.0,
                "take_profit": [3360.0],
                "max_lots": 0.1,
            },
        },
        headers=headers(),
    )
    assert resp.status_code == 200
    events = await store.list_decision_events({"account_id": ACCOUNT_ID, "symbol": "XAUUSD"})
    rej = next(
        e for e in events if e.get("summary", {}).get("pending_gate_reason") == "market_entry_mismatch"
    )
    assert rej["summary"]["h1_atr"] == 2.0
    assert rej["summary"]["signal_entry"] == 3320.0
    assert rej["summary"]["execution_price"] == 3335.7
    assert rej["summary"]["slippage"] > 15.0
    metric_val = client.app.state.metrics.risk_gate_rejections.labels(
        ACCOUNT_ID, "pending_gate.market_entry_mismatch"
    )._value.get()
    assert metric_val >= 1.0


async def test_ai_result_zero_confidence_failure_preserves_prior_snapshot() -> None:
    client, store = await make_app_with_store()
    await store.save_ai_result(
        ACCOUNT_ID,
        "XAUUSD",
        {"bias": "bearish", "confidence": 75, "suggested_sl": 3660.0, "suggested_tp": 3620.0},
    )
    resp = client.post(
        f"/api/v2/ai_result/{ACCOUNT_ID}/XAUUSD",
        json={
            "bias": "neutral",
            "confidence": 0,
            "suggested_sl": 0,
            "suggested_tp": 0,
            "arbitration": {"contradiction": "analysis_unavailable"},
        },
        headers=headers(),
    )
    assert resp.status_code == 200
    results = await store.get_ai_results(ACCOUNT_ID)
    xau = next(r for r in results if r["symbol"] == "XAUUSD")
    assert xau["suggested_sl"] == 3660.0
    assert xau["confidence"] == 75


def test_hold_seconds_from_open_time_handles_broker_utc2_offset() -> None:
    from datetime import UTC, datetime

    from backend.api.routes.ai.index import hold_seconds_from_open_time

    # Simulate broker UTC+2 server: UTC now = 10:00:00Z, server_time = 12:00:00, opened 200s ago
    utc_now = "2026-11-24T10:00:00Z"
    open_time_broker_utc2 = int(datetime(2026, 11, 24, 12, 0, 0, tzinfo=UTC).timestamp()) - 200
    secs = hold_seconds_from_open_time(
        open_time_broker_utc2,
        utc_now,
        heartbeat={"server_time": "2026.11.24 12:00:00"},
    )
    assert secs == 200
    # Without heartbeat when open_time is ahead of UTC now, must clamp to >= 0 instead of returning negative
    assert hold_seconds_from_open_time(open_time_broker_utc2, utc_now) == 0
