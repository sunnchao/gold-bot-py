"""技术分析器单元测试(TS 侧无对应 test 文件,Python 侧冒烟覆盖)。

覆盖 build_system_prompt / parse_response / normalize_enums /
TechnicalAnalystService.run 的 neutral 兜底与价格过滤。
"""

import json

from backend.agents.agents._support import CacheStats, StreamResult, get_symbol_profile
from backend.agents.agents.technical_analyst import (
    NEUTRAL_FALLBACK,
    TechnicalAnalystService,
    build_dynamic_data,
    build_semi_static_data,
    build_system_prompt,
    normalize_enums,
    parse_response,
)


class FakeLlmClient:
    def __init__(self, stream_result=None, stream_error=None, invoke_result=None, invoke_error=None):
        self._stream_result = stream_result
        self._stream_error = stream_error
        self._invoke_result = invoke_result
        self._invoke_error = invoke_error
        self.stream_calls = []
        self.invoke_calls = []

    async def stream_layered(self, system_blocks, user_layers, opts=None):
        self.stream_calls.append((system_blocks, user_layers, opts))
        if self._stream_error is not None:
            raise self._stream_error
        return self._stream_result

    async def invoke_layered(self, system_blocks, user_layers, opts=None):
        self.invoke_calls.append((system_blocks, user_layers, opts))
        if self._invoke_error is not None:
            raise self._invoke_error
        return self._invoke_result

    async def stream_invoke(self, prompt, system_message=None):
        return ""

    def get_model(self):
        return "deepseek-v4-pro"

    def get_cache_strategy(self):
        return {"type": "auto_prefix"}


VALID_TECHNICAL_JSON = json.dumps(
    {
        "bias": "bullish",
        "confidence": 65,
        "phase": "trending",
        "indicators_summary": "趋势明确 (Trend confirmed)",
        "support_levels": [
            {"price": 2290, "type": "support", "strength": "strong", "timeframe": "H1", "touches": 3}
        ],
        "resistance_levels": [
            {"price": 2310, "type": "resistance", "strength": "moderate", "timeframe": "H1", "touches": 2}
        ],
        "recommendation": "hold",
        "rationale": "多头延续 (Uptrend continues)",
    },
    ensure_ascii=False,
)


def payload(market_bid=2300.0):
    return {
        "market": {"symbol": "XAUUSD", "bid": market_bid, "ask": market_bid + 0.2, "spread": 0.2},
        "indicators": {
            "M15": {"close": market_bid, "ema20": market_bid - 1, "ema50": market_bid - 2},
            "M30": {"close": market_bid, "ema20": market_bid - 1, "ema50": market_bid - 2},
            "H1": {"close": market_bid, "high": market_bid + 2, "low": market_bid - 2},
            "H4": {"close": market_bid, "high": market_bid + 3, "low": market_bid - 3},
        },
        "positions": [],
    }


def test_build_system_prompt_contains_instrument_and_output_rules():
    profile = get_symbol_profile("XAUUSD")
    prompt = build_system_prompt(profile)
    assert "XAUUSD" in prompt
    assert "JSON" in prompt


def test_normalize_enums_maps_fuzzy_llm_values():
    cleaned = normalize_enums(
        {
            "bias": "bullish trend",
            "confidence": "78",
            "phase": "consolidation range",
            "recommendation": "Hold positions",
            "support_levels": [
                {"price": 2290, "type": "support", "strength": "Strong", "timeframe": "H1", "touches": "8"}
            ],
        }
    )
    assert cleaned["bias"] == "bullish"
    assert cleaned["confidence"] == 78
    assert cleaned["phase"] == "consolidation"
    assert cleaned["recommendation"] == "hold"
    assert cleaned["support_levels"][0]["strength"] == "strong"
    assert cleaned["support_levels"][0]["touches"] == 8


def test_parse_response_accepts_valid_json_and_rejects_garbage():
    parsed = parse_response(VALID_TECHNICAL_JSON)
    assert parsed is not None
    assert parsed["bias"] == "bullish"
    assert parse_response("this is not JSON at all") is None


def test_semi_static_and_dynamic_prompts_render_without_error():
    profile = get_symbol_profile("XAUUSD")
    semi = build_semi_static_data(payload()["indicators"], profile)
    dynamic = build_dynamic_data(payload(), profile)
    assert "SEMI-STATIC" in semi
    assert "REAL-TIME" in dynamic


async def test_run_parses_stream_result_and_filters_levels():
    client = FakeLlmClient(stream_result=StreamResult(content=VALID_TECHNICAL_JSON, cache_stats=CacheStats()))
    service = TechnicalAnalystService(client)

    result = await service.run(payload(market_bid=2300.0), "XAUUSD")

    assert len(client.stream_calls) == 1
    assert client.invoke_calls == []
    assert result["bias"] == "bullish"
    assert result["confidence"] == 65
    assert result["support_levels"][0]["price"] == 2290


async def test_run_returns_neutral_fallback_when_stream_and_invoke_fail():
    client = FakeLlmClient(
        stream_error=TimeoutError("timeout"),
        invoke_error=RuntimeError("api down"),
    )
    service = TechnicalAnalystService(client)

    result = await service.run(payload(), "XAUUSD")

    assert len(client.stream_calls) == 1
    assert len(client.invoke_calls) == 1
    assert result == NEUTRAL_FALLBACK


async def test_run_returns_neutral_fallback_when_response_is_unparseable():
    client = FakeLlmClient(stream_result=StreamResult(content="sorry, unavailable", cache_stats=CacheStats()))
    service = TechnicalAnalystService(client)

    result = await service.run(payload(), "XAUUSD")

    assert result == NEUTRAL_FALLBACK
