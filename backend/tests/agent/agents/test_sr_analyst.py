"""支撑/阻力分析器单元测试(TS 侧无对应 test 文件,Python 侧冒烟覆盖)。

覆盖 parse_markdown_sr / parse_response / SrAnalystService.run 的空结果兜底
与价格过滤。
"""

from backend.agents.agents.sr_analyst import SrAnalystService, parse_markdown_sr, parse_response


class FakeLlmClient:
    def __init__(self, response=""):
        self._response = response
        self.calls = []

    async def stream_invoke(self, prompt, system_message=None):
        self.calls.append((prompt, system_message))
        return self._response

    async def stream_layered(self, system_blocks, user_layers, opts=None):
        raise NotImplementedError

    async def invoke_layered(self, system_blocks, user_layers, opts=None):
        raise NotImplementedError

    def get_model(self):
        return "deepseek-v4-pro"

    def get_cache_strategy(self):
        return {"type": "auto_prefix"}


SR_MARKDOWN = """## SUPPORT LEVELS
  - 2290 | support | strong | H1 | 5
  - 2280 | support | moderate | M30 | 3

## RESISTANCE LEVELS
  - 2310 | resistance | strong | H1 | 4

## SUMMARY
- recommendation: buy
- rationale: 接近支撑区域 (Near support zone)
"""


def payload(market_price=2300.0):
    return {
        "market": {"symbol": "XAUUSD", "bid": market_price, "ask": market_price + 0.2},
        "indicators": {
            "M15": {"close": market_price, "low": market_price - 1, "high": market_price + 1},
            "H1": {"close": market_price, "low": market_price - 2, "high": market_price + 2},
            "H4": {"close": market_price, "low": market_price - 3, "high": market_price + 3},
        },
    }


def test_parse_markdown_sr_extracts_levels_and_summary():
    parsed = parse_markdown_sr(SR_MARKDOWN)
    assert parsed is not None
    assert [lvl["price"] for lvl in parsed["support_levels"]] == [2290, 2280]
    assert [lvl["price"] for lvl in parsed["resistance_levels"]] == [2310]
    assert parsed["support_levels"][0]["strength"] == "strong"
    assert parsed["support_levels"][0]["touches"] == 5
    assert parsed["recommendation"] == "buy"
    assert "支撑区域" in parsed["rationale"]


def test_parse_response_falls_back_to_empty_on_missing_sr_sections():
    parsed = parse_response("## SUMMARY\n- recommendation: hold\n- rationale: nothing useful")
    assert parsed is None


async def test_run_parses_markdown_and_filters_levels():
    client = FakeLlmClient(response=SR_MARKDOWN)
    service = SrAnalystService(client)

    result = await service.run(payload(), "XAUUSD")

    assert len(client.calls) == 1
    assert result["support_levels"][0]["price"] == 2290
    assert result["resistance_levels"][0]["price"] == 2310
    assert result["recommendation"] == "buy"


async def test_run_returns_empty_fallback_when_parse_fails():
    client = FakeLlmClient(response="completely unparseable output")
    service = SrAnalystService(client)

    result = await service.run(payload(), "XAUUSD")

    assert result["support_levels"] == []
    assert result["resistance_levels"] == []
    assert "解析失败" in result["rationale"]


async def test_run_filters_levels_far_from_current_price():
    far_levels = SR_MARKDOWN.replace("  - 2290 | support | strong | H1 | 5", "  - 900 | support | strong | H1 | 5")
    client = FakeLlmClient(response=far_levels)
    service = SrAnalystService(client)

    result = await service.run(payload(market_price=2300.0), "XAUUSD")

    # 900 超出当前价 ±50% 区间被过滤,2280 保留
    assert [lvl["price"] for lvl in result["support_levels"]] == [2280]
    assert result["resistance_levels"][0]["price"] == 2310
