"""多周期仲裁(MaoArbitrator)单元测试(TS 侧无对应 test 文件,Python 侧冒烟覆盖)。

覆盖 parse_markdown_arbitration / MaoArbitratorService.run 的 hold 兜底、
交易业务校验(买卖方向错误 SL -> 降级为 hold)。
"""

from backend.agents.agents.mao_arbitrator import MaoArbitratorService, parse_markdown_arbitration


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


MAO_MARKDOWN = """## ARBITRATION
- final_direction: buy
- confidence: 75
- primary_contradiction: 无重大矛盾 (No major contradiction)
- phase: trending
- reasoning: 多周期共振 (Multi-timeframe agreement)
- action: open
- united_front_analysis: 方向一致 (Aligned)

## DOW THEORY
- primary_trend: bullish
- primary_phase: markup
- secondary_trend: bullish
- short_term_trend: bullish
- multi_tf_confirm: true
- rationale: HH/HL structure confirmed

## WAVE THEORY
- current_wave: 3
- wave_direction: impulse_up
- wave_count: 12345
- next_target: 2400
- confidence: 70
- rationale: clean impulse

## CHANLUN THEORY
- trend: up
- bi_direction: up
- duan_direction: up
- zhongshu_state: active
- buy_sell_point: buy_3
- confidence: 65
- rationale: 中枢突破 (Hub breakout)

## TRADE RECOMMENDATION
- direction: buy
- entry_price: 2300
- stop_loss: 2280
- take_profit_1: 2350
- take_profit_2: 2400
- risk_reward_ratio: 2.5
- position_size_lots: 0.05
- rationale: AAPB setup
"""


def run_input(market_price=2300.0):
    return {
        "payload": {
            "market": {"symbol": "XAUUSD", "bid": market_price, "ask": market_price + 0.2},
            "account": {"balance": 10000, "equity": 10000, "currency": "USD", "leverage": 100},
            "positions": [],
            "indicators": {
                "M15": {"close": market_price},
                "H1": {"close": market_price},
                "H4": {"close": market_price},
            },
        }
    }


def test_parse_markdown_arbitration_extracts_five_sections():
    parsed = parse_markdown_arbitration(MAO_MARKDOWN)
    assert parsed is not None
    assert parsed["final_direction"] == "buy"
    assert parsed["confidence"] == 75
    assert parsed["action"] == "open"
    assert parsed["dow_theory"]["primary_trend"] == "bullish"
    assert parsed["wave_theory"]["wave_direction"] == "impulse_up"
    assert parsed["chanlun_theory"]["buy_sell_point"] == "buy_3"
    trade = parsed["trade_recommendation"]
    assert trade["direction"] == "buy"
    assert trade["entry_price"] == 2300
    assert trade["stop_loss"] == 2280
    assert trade["take_profit_2"] == 2400
    assert trade["risk_reward_ratio"] == 2.5


async def test_run_parses_valid_arbitration():
    client = FakeLlmClient(response=MAO_MARKDOWN)
    service = MaoArbitratorService(client)

    result = await service.run(run_input(), "XAUUSD")

    assert len(client.calls) == 1
    assert result["final_direction"] == "buy"
    assert result["action"] == "open"
    assert result["confidence"] == 75
    assert result["trade_recommendation"]["direction"] == "buy"
    assert result["trade_recommendation"]["entry_price"] == 2300


async def test_run_returns_hold_fallback_when_parse_fails():
    client = FakeLlmClient(response="unstructured failure output")
    service = MaoArbitratorService(client)

    result = await service.run(run_input(), "XAUUSD")

    assert result["final_direction"] == "hold"
    assert result["action"] == "hold"
    assert result["confidence"] == 0
    assert "仲裁解析失败" in result["reasoning"]


async def test_run_downgrades_trade_to_hold_for_wrong_side_stop_loss():
    wrong_sl = MAO_MARKDOWN.replace("stop_loss: 2280", "stop_loss: 2350")
    client = FakeLlmClient(response=wrong_sl)
    service = MaoArbitratorService(client)

    result = await service.run(run_input(), "XAUUSD")

    assert result["final_direction"] == "hold"
    assert result["action"] == "hold"
    assert result["confidence"] == 20
    assert result["trade_recommendation"]["direction"] == "hold"
