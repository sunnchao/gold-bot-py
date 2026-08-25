"""风控评估器单元测试(TS 侧无对应 test 文件,Python 侧冒烟覆盖)。

覆盖 parse_markdown_risk / RiskManagerService.run 的解析、high-risk 兜底、
suggestedSL/TP 超出品种价格区间时的清零,以及双模型失败时的抛出行为。
"""

import pytest

from backend.agents.agents._support import StreamResult
from backend.agents.agents.risk_manager import RiskManagerService, parse_markdown_risk


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


RISK_MARKDOWN = """## TECHNICAL ANALYSIS
Bias: bullish

## RISK
- risk_level: high
- max_position_size: 0.5
- suggested_sl: 2280
- suggested_tp: 2340
- add_on: false
- warnings: 重要风险提示 (Important warning: keep position small)
"""


def payload(market_price=2300.0):
    return {
        "isDemo": False,
        "account": {"balance": 10000, "equity": 10000, "currency": "USD", "leverage": 100},
        "market": {"symbol": "XAUUSD", "bid": market_price, "ask": market_price + 0.2, "spread": 0.2},
        "positions": [],
        "indicators": {
            "H1": {"atr": 12.0},
            "M15": {"atr": 6.0},
        },
    }


def test_parse_markdown_risk_extracts_assessment():
    parsed = parse_markdown_risk(RISK_MARKDOWN)
    assert parsed is not None
    assert parsed["riskLevel"] == "high"
    assert parsed["maxPositionSize"] == 0.5
    assert parsed["suggestedSL"] == 2280
    assert parsed["suggestedTP"] == 2340
    assert parsed["addOn"] is False
    assert any("重要风险提示" in w for w in parsed["warnings"])


async def test_run_parses_valid_assessment():
    client = FakeLlmClient(stream_result=StreamResult(content=RISK_MARKDOWN))
    service = RiskManagerService(client)

    result = await service.run(technical=None, payload=payload(), symbol="XAUUSD")

    assert len(client.stream_calls) == 1
    assert result["riskLevel"] == "high"
    assert result["maxPositionSize"] == 0.5
    assert result["suggestedSL"] == 2280
    assert result["suggestedTP"] == 2340
    assert len(result["warnings"]) >= 1


async def test_run_returns_high_risk_fallback_when_parse_fails():
    client = FakeLlmClient(stream_result=StreamResult(content="sorry, no structured output"))
    service = RiskManagerService(client)

    result = await service.run(technical=None, payload=payload(), symbol="XAUUSD")

    assert result["riskLevel"] == "high"
    assert result["suggestedSL"] == 0
    assert result["suggestedTP"] == 0
    assert any("解析失败" in w for w in result["warnings"])


async def test_run_zeroes_sl_outside_instrument_price_range():
    out_of_range = RISK_MARKDOWN.replace("suggested_sl: 2280", "suggested_sl: 800")
    client = FakeLlmClient(stream_result=StreamResult(content=out_of_range))
    service = RiskManagerService(client)

    result = await service.run(technical=None, payload=payload(), symbol="XAUUSD")

    assert result["suggestedSL"] == 0
    assert any("AI止损" in w for w in result["warnings"])


async def test_run_raises_when_both_models_fail():
    client = FakeLlmClient(
        stream_error=TimeoutError("timeout"),
        invoke_error=RuntimeError("fallback api down"),
    )
    service = RiskManagerService(client)

    with pytest.raises(RuntimeError, match="fallback api down"):
        await service.run(technical=None, payload=payload(), symbol="XAUUSD")

    assert len(client.stream_calls) == 1
    assert len(client.invoke_calls) == 1
