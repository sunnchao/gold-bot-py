"""ComprehensiveAnalystService 单元测试(镜像 gold-bot comprehensive-analyst.test.ts)。

覆盖:
- 分层 prompt 缓存集成(慢->快 tier 顺序、cacheable 前缀稳定性、未收盘 bar 隔离)
- 空流回退到非流式;工具调用二阶段 tradeAction
- 账户价格偏差/ATR 不可用守卫
- 谐波 volatile 字段(score/completion_pct)不进入半静态层
- 截断 ARBITRATION 拒绝触发强制 tool_use 结构化重试
- 双格式解析失败后的结构化重试与中性兜底
"""

from backend.agents.agents import comprehensive_analyst as ca
from backend.agents.agents._support import CacheStats, StreamResult, ToolUse


class FakeLlmClient:
    """实现 LlmClient 协议的测试替身:stream/invoke 按队列返回。"""

    def __init__(
        self,
        stream_results=None,
        invoke_results=None,
        stream_exceptions=None,
        model="deepseek-v4-pro",
    ):
        self._stream_results = list(stream_results or [])
        self._invoke_results = list(invoke_results or [])
        self._stream_exceptions = list(stream_exceptions or [])
        self.model = model
        self.stream_calls = []
        self.invoke_calls = []
        self.stream_invoke_calls = []

    async def stream_layered(self, system_blocks, user_layers, opts=None):
        self.stream_calls.append((system_blocks, user_layers, opts))
        if self._stream_exceptions:
            raise self._stream_exceptions.pop(0)
        if not self._stream_results:
            raise AssertionError("FakeLlmClient: no stream result queued")
        return self._stream_results.pop(0)

    async def invoke_layered(self, system_blocks, user_layers, opts=None):
        self.invoke_calls.append((system_blocks, user_layers, opts))
        if not self._invoke_results:
            raise AssertionError("FakeLlmClient: no invoke result queued")
        return self._invoke_results.pop(0)

    async def stream_invoke(self, prompt, system_message=None):
        self.stream_invoke_calls.append((prompt, system_message))
        return ""

    def get_model(self):
        return self.model

    def get_cache_strategy(self):
        return {"type": "auto_prefix"}


def sr(content):
    return StreamResult(content=content, cache_stats=CacheStats())


def sr_tool(name, input_):
    return StreamResult(
        content="",
        cache_stats=CacheStats(),
        tool_use=ToolUse(id="t1", name=name, input=input_),
    )


def make_service(client, trade_client=None):
    return ca.ComprehensiveAnalystService(client, trade_client=trade_client)


# ─────────────────────── 输入 fixtures(与 TS 逐字节一致) ─────────────────────

MARKDOWN_RESPONSE = """## TECHNICAL
- Bias: neutral
- Confidence: 50
- Phase: consolidation
- Indicators Summary: 震荡整理 (Consolidation)
- Support Levels:
  - 2300 | support | strong | H1 | 3
- Resistance Levels:
  - 2400 | resistance | strong | H1 | 3
- Recommendation: none
- Rationale: 观望 (Hold)

## WAVE
- Confirmation: partial
- Extension Wave: 3
- Corrective Type: zigzag
- Trend Strength: moderate
- Target Level 1.618: 2380
- Target Level 2.0: 2420
- Confidence: 50
- Rationale: 部分确认 (Partial)

## CHANLUN
- Trend: range
- Strength: moderate
- Latest Signal: hold
- Hub State: active
- Confidence: 50
- Rationale: 中枢震荡 (Range)

## HARMONIC
- Detected Pattern: none
- Direction: neutral
- Timeframe: N/A
- Completion: 0
- Confidence: 0
- D Zone Price: 0
- Entry Zone: N/A
- Stop Loss: 0
- Take Profit 1: 0
- Take Profit 2: 0
- Rationale: 无形态 (None)

## RISK
- Risk Level: medium
- Max Position Size: 0.1
- Suggested SL: 2290
- Suggested TP: 2410
- Warnings: 无 (None)
- Add On: false

## ARBITRATION
- Final Direction: hold
- Confidence: 50
- Action: hold
- Primary Contradiction:
- Phase: consolidation
- United Front Analysis: 观望 (Hold)
- Reasoning: 市场整理。波浪和缠论未形成一致突破。风险收益不明确。
- Dow Primary Trend: neutral
- Dow Primary Phase: accumulation
- Dow Secondary Trend: neutral
- Dow Short Term Trend: neutral
- Dow Multi TF Confirm: false
- Dow Rationale: neutral
- Wave Current Wave: 3
- Wave Direction: unclear
- Wave Count: partial
- Wave Next Target: 2380
- Wave Confidence: 50
- Wave Rationale: partial
- Chanlun Trend: range
- Chanlun Bi Direction: none
- Chanlun Duan Direction: none
- Chanlun Zhongshu State: active
- Chanlun Buy Sell Point: none
- Chanlun Confidence: 50
- Chanlun Rationale: range
- Harmonic Pattern: none
- Harmonic Direction: neutral
- Harmonic Confidence: 0
- Harmonic Rationale: none
- Trade Direction: hold
- Trade Entry Price: 0
- Trade Stop Loss: 0
- Trade Take Profit 1: 0
- Trade Take Profit 2: 0
- Trade Risk Reward Ratio: 0
- Trade Position Size Lots: 0.01
- Trade Rationale: hold"""

BUY_SETUP_MARKDOWN = MARKDOWN_RESPONSE
for old, new in [
    ("- Bias: neutral", "- Bias: bullish"),
    ("- Confidence: 50", "- Confidence: 75"),
    ("- Risk Level: medium", "- Risk Level: low"),
    ("- Max Position Size: 0.1", "- Max Position Size: 0.05"),
    ("- Suggested SL: 2290", "- Suggested SL: 4125"),
    ("- Suggested TP: 2410", "- Suggested TP: 4188"),
    ("- Final Direction: hold", "- Final Direction: buy"),
    ("- Action: hold", "- Action: open"),
    (
        "- Reasoning: 市场整理。波浪和缠论未形成一致突破。风险收益不明确。",
        "- Reasoning: 多头趋势仍在，等待回调提供更好盈亏比。",
    ),
    ("- Trade Direction: hold", "- Trade Direction: buy"),
    ("- Trade Entry Price: 0", "- Trade Entry Price: 4145"),
    ("- Trade Stop Loss: 0", "- Trade Stop Loss: 4125"),
    ("- Trade Take Profit 1: 0", "- Trade Take Profit 1: 4188"),
    ("- Trade Take Profit 2: 0", "- Trade Take Profit 2: 4205"),
    ("- Trade Risk Reward Ratio: 0", "- Trade Risk Reward Ratio: 2.15"),
    ("- Trade Position Size Lots: 0.01", "- Trade Position Size Lots: 0.05"),
    ("- Trade Rationale: hold", "- Trade Rationale: 等待回调至 4145 入场 (wait for pullback to 4145)"),
]:
    BUY_SETUP_MARKDOWN = BUY_SETUP_MARKDOWN.replace(old, new)

STRUCTURED_ANALYSIS_INPUT = {
    "technical": {
        "bias": "neutral",
        "confidence": 45,
        "phase": "consolidation",
        "indicators_summary": "震荡整理 (Consolidation)",
        "support_levels": [],
        "resistance_levels": [],
        "recommendation": "none",
        "rationale": "观望 (Hold)",
    },
    "wave": {
        "wave_confirmation": "partial",
        "extension_wave": None,
        "corrective_type": "zigzag",
        "trend_strength": "moderate",
        "target_levels": {"level_1_618": 2380, "level_2_0": 2420},
        "confidence": 45,
        "rationale": "部分确认 (Partial)",
    },
    "chanlun": {
        "trend": "range",
        "strength": "moderate",
        "latest_signal": "hold",
        "hub_state": "active",
        "confidence": 45,
        "rationale": "中枢震荡 (Range)",
    },
    "risk": {
        "riskLevel": "medium",
        "maxPositionSize": 0.1,
        "suggestedSL": 2290,
        "warnings": [],
        "addOn": False,
    },
    "arbitration": {
        "final_direction": "hold",
        "confidence": 45,
        "primary_contradiction": "",
        "phase": "consolidation",
        "reasoning": "市场整理，观望。",
        "action": "hold",
        "united_front_analysis": "观望 (Hold)",
    },
}

MARKET_INSIGHT = {
    "technical": {
        "bias": "bullish",
        "confidence": 80,
        "phase": "trending",
        "indicators_summary": "trend",
        "support_levels": [],
        "resistance_levels": [],
        "recommendation": "none",
        "rationale": "trend",
    },
    "wave": {
        "wave_confirmation": "partial",
        "extension_wave": None,
        "corrective_type": None,
        "trend_strength": "moderate",
        "target_levels": {"level_1_618": 4188, "level_2_0": 4205},
        "confidence": 70,
        "rationale": "wave",
    },
    "chanlun": {
        "trend": "up",
        "strength": "moderate",
        "latest_signal": "buy",
        "hub_state": "active",
        "confidence": 70,
        "rationale": "chanlun",
    },
    "harmonic": {
        "detected_pattern": "none",
        "direction": "neutral",
        "timeframe": "N/A",
        "completion_pct": 0,
        "confidence": 0,
        "d_zone_price": 0,
        "entry_zone": "N/A",
        "stop_loss": 0,
        "take_profit_1": 0,
        "take_profit_2": 0,
        "rationale": "none",
    },
    "risk": {
        "riskLevel": "low",
        "maxPositionSize": 0.1,
        "suggestedSL": 4168,
        "suggestedTP": 4188,
        "warnings": [],
        "addOn": False,
    },
    "arbitration": {
        "final_direction": "buy",
        "confidence": 80,
        "primary_contradiction": "none",
        "phase": "trend",
        "reasoning": "buy",
        "action": "open",
        "united_front_analysis": "aligned",
    },
    "sr_levels": {"support": [], "resistance": []},
    "trend_bias": "bullish",
    "confidence": 80,
    "trade_intent": {
        "direction": "buy",
        "entry_trigger": "market",
        "entry_offset_atr": 0,
        "stop_loss_atr": 1.5,
        "take_profit_1_atr": 3,
        "rationale": "buy",
    },
}


def indicator(close):
    return {
        "close": close,
        "open": close - 1,
        "high": close + 2,
        "low": close - 2,
        "ema20": close - 1,
        "ema50": close - 2,
        "ema200": close - 3,
        "rsi": 50,
        "adx": 20,
        "atr": 5,
        "macd": 1,
        "macd_signal": 0.5,
        "macd_hist": 0.5,
        "bb_upper": close + 10,
        "bb_middle": close,
        "bb_lower": close - 10,
        "stoch_k": 50,
        "stoch_d": 50,
    }


def payload_with_last_bar_close(last_close):
    return {
        "account": {
            "account_id": "acc-001",
            "equity": 10000,
            "balance": 10000,
            "margin": 100,
            "free_margin": 9900,
            "currency": "USD",
            "leverage": 100,
        },
        "market": {"symbol": "XAUUSD", "bid": last_close, "ask": last_close + 0.2, "spread": 0.2},
        "indicators": {
            "M15": indicator(last_close),
            "M30": indicator(last_close),
            "H1": indicator(last_close),
            "H4": indicator(last_close),
        },
        "positions": [],
        "market_status": {
            "market_open": True,
            "is_trade_allowed": True,
            "tradeable": True,
        },
        "strategy_mapping": {"trend": "10001"},
        "bars": {
            "H1": [
                {"time": "2026-06-30T00:00:00Z", "open": 2300, "high": 2310, "low": 2290, "close": 2305},
                {"time": "2026-06-30T01:00:00Z", "open": 2305, "high": 2320, "low": 2300, "close": 2315},
                {"time": "2026-06-30T02:00:00Z", "open": 2315, "high": 2330, "low": 2310, "close": 2325},
                {"time": "2026-06-30T03:00:00Z", "open": 2325, "high": 2340, "low": 2320, "close": last_close},
            ]
        },
        "harmonic_context": {
            "h4_patterns": [],
            "h1_patterns": [],
            "m30_patterns": [],
            "active_pattern": None,
            "direction_bias": "neutral",
            "score": 0,
            "summary": "none",
        },
    }


def payload_with_only_one_closed_bar(last_close):
    payload = payload_with_last_bar_close(last_close)
    return {
        **payload,
        "bars": {
            "H1": [
                {"time": "2026-06-30T00:00:00Z", "open": 2300, "high": 2310, "low": 2290, "close": 2305},
                {"time": "2026-06-30T01:00:00Z", "open": 2305, "high": 2320, "low": 2300, "close": last_close},
            ]
        },
    }


def payload_with_harmonic_context(score, completion_pct):
    base = payload_with_last_bar_close(2335)
    return {
        **base,
        "harmonic_context": {
            "h4_patterns": [],
            "h1_patterns": [],
            "m30_patterns": [],
            "active_pattern": {
                "type": "bat",
                "direction": "bullish",
                "timeframe": "H1",
                "score": score,
                "completion_pct": completion_pct,
                "x_price": 2300,
                "a_price": 2320,
                "b_price": 2310,
                "c_price": 2330,
                "d_price": 2315,
                "ab_ratio": 0.5,
                "bc_ratio": 0.618,
                "cd_ratio": 1.272,
                "xd_ratio": 0.886,
                "reason": "Bullish bat pattern near D zone",
                "prz_low": 2313,
                "prz_high": 2317,
                "stop_loss": 2308,
                "target_1": 2325,
                "target_2": 2335,
                "confidence": 78,
                "invalidated": False,
                "status": "completed",
            },
            "direction_bias": "bullish",
            "score": score,
            "summary": f"Bullish bat detected, score={score}",
        },
    }


# ─────────────────────────── 测试用例 ───────────────────────────


async def test_sends_ordered_stability_tiers_and_keeps_cacheable_prefix_stable():
    """TS it keeps the cacheable prefix stable when only the unclosed bar changes"""
    client = FakeLlmClient(stream_results=[sr(MARKDOWN_RESPONSE), sr(MARKDOWN_RESPONSE)])
    service = make_service(client)

    await service.run(payload_with_last_bar_close(2335), "XAUUSD")
    await service.run(payload_with_last_bar_close(2348), "XAUUSD")

    assert len(client.stream_calls) == 2
    first_system_blocks, first_user_layers, _ = client.stream_calls[0]
    second_system_blocks, second_user_layers, _ = client.stream_calls[1]

    assert len(first_system_blocks) == 2
    assert first_system_blocks[0].cacheable is True
    assert first_system_blocks[1].cacheable is True
    assert "XAUUSD" not in first_system_blocks[0].text
    assert "XAUUSD" in first_system_blocks[1].text
    # config -> H4 -> H1 -> M30 -> M15 (all cacheable) -> realtime (dynamic)
    assert len(first_user_layers) == 6
    for i in range(5):
        assert first_user_layers[i].cacheable is True
        assert first_user_layers[i].text == second_user_layers[i].text
    assert first_user_layers[5].cacheable is False
    assert first_user_layers[5].text != second_user_layers[5].text
    assert second_system_blocks[0].text == first_system_blocks[0].text


async def test_does_not_use_the_unclosed_bar_in_computed_context_when_closed_bars_are_insufficient():
    """TS it('does not use the unclosed bar in computed context when closed bars are insufficient')"""
    client = FakeLlmClient(stream_results=[sr(MARKDOWN_RESPONSE)])
    service = make_service(client)

    await service.run(payload_with_only_one_closed_bar(2335), "XAUUSD")

    _, user_layers, _ = client.stream_calls[0]
    # No cacheable tier may leak the unclosed bar's close.
    for layer in user_layers[:-1]:
        assert "2335" not in layer.text


async def test_falls_back_to_non_streaming_when_streaming_returns_empty_content():
    """TS it.each(['empty','whitespace-only']) 'falls back to non-streaming when streaming returns %s content'"""
    for stream_content in ("", "   \n"):
        client = FakeLlmClient(
            stream_results=[StreamResult(content=stream_content, cache_stats=CacheStats())],
            invoke_results=[BUY_SETUP_MARKDOWN.replace("- Phase: consolidation", "- Phase: trending")],
        )
        service = make_service(client)

        result = await service.run(
            payload_with_last_bar_close(4174),
            "US100Cash",
            None,
            None,
            {"skipTradeAction": True},
        )

        assert len(client.stream_calls) == 1
        assert len(client.invoke_calls) == 1
        assert client.invoke_calls[0][0] == client.stream_calls[0][0]
        assert client.invoke_calls[0][1] == client.stream_calls[0][1]
        assert result["technical"]["bias"] == "bullish"
        assert result["technical"]["phase"] == "trending"
        assert result["technical"]["confidence"] > 0
        assert result["arbitration"]["primary_contradiction"] != "analysis_unavailable"
        assert result["arbitration"]["final_direction"] == "buy"


async def test_populates_trade_action_from_tool_use_second_phase_call():
    """TS it('populates tradeAction from tool_use second-phase call')"""
    client = FakeLlmClient(stream_results=[sr(BUY_SETUP_MARKDOWN)])
    trade_client = FakeLlmClient(
        stream_results=[
            sr_tool(
                "place_pending_order",
                {
                    "account_id": "acc-001",
                    "symbol": "XAUUSD",
                    "side": "buy",
                    "entry_price": 4145,
                    "stop_loss": 4125,
                    "take_profit_1": 4188,
                    "take_profit_2": 4205,
                    "lots": 0.05,
                    "order_type": "limit",
                    "reason": "等待回调至 4145 入场 (wait for pullback to 4145)",
                },
            )
        ],
        model="deepseek-v4-flash-0731",
    )
    service = make_service(client, trade_client=trade_client)

    result = await service.run(payload_with_last_bar_close(4174), "XAUUSD")

    assert len(client.stream_calls) == 1
    assert len(trade_client.stream_calls) == 1
    assert trade_client.get_model() == "deepseek-v4-flash-0731"
    assert trade_client.stream_calls[0][2] == {
        "tools": ca.TRADE_ACTION_TOOLS_LEGACY,
        "toolChoice": {"type": "auto"},
    }
    assert (
        "Lots must be between 0.01 and 0.5 (typically 0.01-0.05 for XAUUSD intraday)"
        in trade_client.stream_calls[0][0][0].text
    )
    assert result["tradeAction"] == {
        "type": "place_pending_order",
        "side": "buy",
        "entry_price": 4145,
        "stop_loss": 4125,
        "take_profit_1": 4188,
        "take_profit_2": 4205,
        "lots": 0.05,
        "order_type": "limit",
        "expiry_hours": 4,
        "reason": "等待回调至 4145 入场 (wait for pullback to 4145)",
    }


async def test_falls_back_to_undefined_when_tool_use_call_fails():
    """TS it('falls back to undefined when tool_use call fails')"""
    client = FakeLlmClient(stream_results=[sr(BUY_SETUP_MARKDOWN)])
    trade_client = FakeLlmClient(stream_exceptions=[TimeoutError("timeout")])
    service = make_service(client, trade_client=trade_client)

    result = await service.run(payload_with_last_bar_close(4174), "XAUUSD")

    assert len(client.stream_calls) == 1
    assert len(trade_client.stream_calls) == 1
    assert "tradeAction" not in result


async def test_returns_do_nothing_when_account_price_deviation_exceeds_atr_tolerance():
    """TS it('returns do_nothing when account price deviation exceeds ATR tolerance')"""
    client = FakeLlmClient()
    service = make_service(client)

    actions = await service.decide_account_actions(
        MARKET_INSIGHT,
        [
            {
                "accountId": "81124211",
                "symbol": "GOLDm#",
                "payload": payload_with_last_bar_close(4176),
                "aiSymbols": ["GOLDm#"],
                "realtimePrice": 4176,
                "atr": 4,
            }
        ],
        4174,
        4,
        0.25,
    )

    assert actions["GOLDm#"] == {
        "type": "do_nothing",
        "account_id": "81124211",
        "reasoning": "price.deviation_too_large",
    }


async def test_returns_do_nothing_when_account_atr_is_unavailable():
    """TS it('returns do_nothing when account ATR is unavailable')"""
    client = FakeLlmClient()
    service = make_service(client)

    actions = await service.decide_account_actions(
        {},
        [
            {
                "accountId": "81124211",
                "symbol": "GOLDm#",
                "payload": payload_with_last_bar_close(4174),
                "aiSymbols": ["GOLDm#"],
                "realtimePrice": 4174,
                "atr": 0,
            }
        ],
        4174,
        0,
        0.25,
    )

    assert actions["GOLDm#"] == {
        "type": "do_nothing",
        "account_id": "81124211",
        "reasoning": "price.atr_unavailable",
    }
    assert client.stream_calls == []


async def test_does_not_include_current_price_in_the_second_phase_cacheable_system_block():
    """TS it('does not include current price in the second-phase cacheable system block')"""
    client = FakeLlmClient(stream_results=[sr(BUY_SETUP_MARKDOWN)])
    trade_client = FakeLlmClient(
        stream_results=[sr_tool("do_nothing", {"reason": "test"})],
        model="deepseek-v4-flash-0731",
    )
    service = make_service(client, trade_client=trade_client)

    await service.run(payload_with_last_bar_close(4174), "XAUUSD")

    second_call_system_blocks = trade_client.stream_calls[0][0]
    assert len(second_call_system_blocks) == 2
    assert second_call_system_blocks[0].cacheable is True
    assert second_call_system_blocks[1].cacheable is True
    assert "4174" not in second_call_system_blocks[1].text
    assert "Current price" not in second_call_system_blocks[1].text


async def test_keeps_harmonic_volatile_fields_out_of_semi_static_layer():
    """TS it('keeps harmonic volatile fields (score, completion_pct) out of semi-static layer')"""
    client = FakeLlmClient(stream_results=[sr(MARKDOWN_RESPONSE), sr(MARKDOWN_RESPONSE)])
    service = make_service(client)

    await service.run(payload_with_harmonic_context(75, 90), "XAUUSD")
    await service.run(payload_with_harmonic_context(82, 95), "XAUUSD")

    assert len(client.stream_calls) == 2
    _, first_user_layers, _ = client.stream_calls[0]
    _, second_user_layers, _ = client.stream_calls[1]

    # H1 tier (index 2 of: config, H4, H1, M30, M15, realtime) carries HARMONIC_CTX.
    h1_tier = 2
    assert first_user_layers[h1_tier].text == second_user_layers[h1_tier].text

    assert '"score":75' not in first_user_layers[h1_tier].text
    assert '"completion_pct":90' not in first_user_layers[h1_tier].text
    assert '"reason"' not in first_user_layers[h1_tier].text

    assert '"type":"bat"' in first_user_layers[h1_tier].text
    assert '"direction":"bullish"' in first_user_layers[h1_tier].text
    assert '"x_price":2300' in first_user_layers[h1_tier].text
    assert '"prz_low":2313' in first_user_layers[h1_tier].text
    assert '"stop_loss":2308' in first_user_layers[h1_tier].text
    assert '"target_1":2325' in first_user_layers[h1_tier].text

    assert "75" in first_user_layers[5].text
    assert "82" in second_user_layers[5].text


async def test_should_reject_truncated_arbitration_missing_trade_fields(monkeypatch):
    """TS it('should reject truncated ARBITRATION missing trade fields')"""
    warns = []

    class FakeLogger:
        def warn(self, obj, msg=""):
            warns.append((obj, msg))

        def info(self, obj, msg=""):
            pass

        def error(self, obj, msg=""):
            pass

        def debug(self, obj, msg=""):
            pass

    monkeypatch.setattr(ca, "get_logger", lambda: FakeLogger())

    truncated = (
        MARKDOWN_RESPONSE.replace("- Harmonic Confidence: 0", "- Harmonic Confidence: 85")
        .split("\n- Harmonic Rationale: none")[0]
    )
    client = FakeLlmClient(
        stream_results=[
            StreamResult(content=truncated, cache_stats=CacheStats()),
            sr_tool("submit_comprehensive_analysis", STRUCTURED_ANALYSIS_INPUT),
        ]
    )
    service = make_service(client)

    result = await service.run(
        payload_with_last_bar_close(2335),
        "XAUUSD",
        None,
        None,
        {"skipTradeAction": True},
    )

    assert len(client.stream_calls) == 2
    # TS 用 toMatchObject:实现额外携带 tools 列表,这里只断言 toolChoice
    assert client.stream_calls[1][2]["toolChoice"] == {
        "type": "tool",
        "name": "submit_comprehensive_analysis",
    }

    matching = [
        (obj, msg)
        for obj, msg in warns
        if msg == "Incomplete ARBITRATION section detected, rejecting to trigger retry"
    ]
    assert len(matching) == 1
    obj, _ = matching[0]
    assert obj["symbol"] == "XAUUSD"
    assert obj["rawLength"] == len(truncated)
    assert obj["sectionCount"] == 6
    assert obj["missingTradeFields"] == [
        "trade_direction",
        "trade_entry_price",
        "trade_stop_loss",
        "trade_take_profit_1",
        "trade_risk_reward_ratio",
        "trade_position_size_lots",
    ]
    assert obj["availableFields"] == []

    assert result["arbitration"]["confidence"] == 45
    assert "市场整理" in result["arbitration"]["reasoning"]


async def test_recovers_via_forced_tool_use_structured_retry_when_both_parse_formats_fail():
    """TS it('recovers via forced tool_use structured retry when both parse formats fail')"""
    client = FakeLlmClient(
        stream_results=[
            StreamResult(content="sorry, I cannot comply in the requested format", cache_stats=CacheStats()),
            sr_tool("submit_comprehensive_analysis", STRUCTURED_ANALYSIS_INPUT),
        ]
    )
    service = make_service(client)

    result = await service.run(payload_with_last_bar_close(2335), "XAUUSD")

    assert len(client.stream_calls) == 2
    # TS 用 toMatchObject:实现额外携带 tools 列表,这里只断言 toolChoice
    assert client.stream_calls[1][2]["toolChoice"] == {
        "type": "tool",
        "name": "submit_comprehensive_analysis",
    }
    assert result["technical"]["confidence"] == 45
    assert result["arbitration"]["confidence"] == 45
    assert "市场整理" in result["arbitration"]["reasoning"]


async def test_falls_back_to_neutral_result_when_the_structured_retry_also_fails():
    """TS it('falls back to neutral result when the structured retry also fails')"""
    client = FakeLlmClient(
        stream_results=[
            StreamResult(content="sorry, I cannot comply in the requested format", cache_stats=CacheStats()),
            # Retry returns tool input that violates the schema
            sr_tool("submit_comprehensive_analysis", {"technical": {"bias": "sideways"}}),
        ]
    )
    service = make_service(client)

    result = await service.run(payload_with_last_bar_close(2335), "XAUUSD")

    assert len(client.stream_calls) == 2
    assert result["technical"]["confidence"] == 0
    assert result["arbitration"]["final_direction"] == "hold"


async def test_preserves_explicit_neutral_theory_sections_and_a_hold_recommendation():
    """TS it preserves neutral theory sections when output and retry are unavailable"""
    client = FakeLlmClient(
        stream_results=[
            StreamResult(content="## TECHNICAL\n- Bias: neutral", cache_stats=CacheStats()),
            StreamResult(content="", cache_stats=CacheStats()),
        ]
    )
    service = make_service(client)

    result = await service.run(payload_with_last_bar_close(2335), "XAUUSD")

    assert len(client.stream_calls) == 2
    theory = result["arbitration"]
    assert theory["dow_theory"]["primary_trend"] == "neutral"
    assert theory["dow_theory"]["secondary_trend"] == "neutral"
    assert theory["dow_theory"]["short_term_trend"] == "neutral"
    assert theory["dow_theory"]["multi_tf_confirm"] is False
    assert theory["wave_theory"]["wave_direction"] == "unclear"
    assert theory["wave_theory"]["confidence"] == 0
    assert theory["chanlun_theory"]["trend"] == "range"
    assert theory["chanlun_theory"]["bi_direction"] == "none"
    assert theory["chanlun_theory"]["duan_direction"] == "none"
    assert theory["chanlun_theory"]["confidence"] == 0
    assert theory["harmonic_theory"]["pattern"] == "none"
    assert theory["harmonic_theory"]["direction"] == "neutral"
    assert theory["harmonic_theory"]["confidence"] == 0
    assert theory["trade_recommendation"]["direction"] == "hold"
    assert "观望" in theory["trade_recommendation"]["rationale"]
