"""镜像 gold-bot `apps/app-agent/src/utils/price-validator.test.ts`。"""
from backend.agents.config.symbol_profile import get_symbol_profile
from backend.agents.types.analysis import TradeRecommendation
from backend.agents.utils.price_validator import validate_trade_recommendation


def trade_with_targets(take_profit_1: float, take_profit_2: float | None = None) -> TradeRecommendation:
    return TradeRecommendation(
        direction="sell",
        entry_price=4290,
        stop_loss=4295,
        take_profit_1=take_profit_1,
        take_profit_2=take_profit_2,
        risk_reward_ratio=0,
        position_size_lots="0.1",
        rationale="test",
    )


def test_uses_tp2_as_the_reward_target_when_tp2_is_present():
    # TS: 'uses TP2 as the reward target when TP2 is present'
    result = validate_trade_recommendation(
        trade_with_targets(4260, 4250),
        4290,
        get_symbol_profile("XAUUSD"),
    )

    assert result.fixedTrade is not None
    assert result.fixedTrade.risk_reward_ratio == 8


def test_falls_back_to_tp1_when_tp2_is_absent_or_zero():
    # TS: 'falls back to TP1 when TP2 is absent or zero'
    without_tp2 = validate_trade_recommendation(
        trade_with_targets(4260),
        4290,
        get_symbol_profile("XAUUSD"),
    )
    zero_tp2 = validate_trade_recommendation(
        trade_with_targets(4260, 0),
        4290,
        get_symbol_profile("XAUUSD"),
    )

    assert without_tp2.fixedTrade is not None
    assert without_tp2.fixedTrade.risk_reward_ratio == 6
    assert zero_tp2.fixedTrade is not None
    assert zero_tp2.fixedTrade.risk_reward_ratio == 6
