"""镜像 gold-bot `apps/app-agent/src/types/types.test.ts`。

注:TS 测试里向 IndicatorPack/MarketData 塞入的 camelCase 键(macdSignal 等)
与 gold-bot.ts 现行 snake_case 接口不符(esbuild 不做类型检查故 TS 侧能跑)。
Python 侧按现行接口构造并断言相同的可观察值。
"""

from backend.agents.types.analysis import (
    ArbitrationResult,
    RiskAssessment,
    SRLevel,
    TechnicalAnalysis,
)
from backend.agents.types.goldbot import (
    AccountInfo,
    GoldbotBar,
    GoldbotPayload,
    IndicatorPack,
    MarketData,
    MarketStatus,
)


def test_should_define_indicator_pack_with_all_required_fields():
    # TS: Goldbot types 'should define IndicatorPack with all required fields'
    indicators = IndicatorPack(
        close=2351,
        open=2348,
        high=2352,
        low=2347,
        ema20=2348,
        ema50=2340,
        rsi=55,
        macd=1.2,
        macd_signal=0.8,
        macd_hist=0.4,
        adx=25,
        bb_upper=2360,
        bb_middle=2350,
        bb_lower=2340,
        atr=15,
        stoch_k=65,
        stoch_d=60,
    )

    assert indicators.ema20 == 2348
    assert indicators.rsi == 55
    assert indicators.adx == 25


def test_should_define_market_data_with_bars():
    # TS: Goldbot types 'should define MarketData with bars'
    market = MarketData(symbol="XAUUSD", bid=2350.30, ask=2350.70, spread=0.40)
    payload = GoldbotPayload(
        account=AccountInfo(
            account_id="acc-001",
            equity=10000,
            balance=10000,
            margin=0,
            free_margin=10000,
            currency="USD",
            leverage=100,
        ),
        market=market,
        positions=[],
        market_status=MarketStatus(market_open=True, is_trade_allowed=True, tradeable=True),
        strategy_mapping={},
        bars={
            "H1": [
                GoldbotBar(time="2026-05-01T08:00:00Z", open=2348, high=2352, low=2347, close=2351, volume=1000)
            ]
        },
    )

    assert payload.market.symbol == "XAUUSD"
    assert payload.bars is not None
    assert len(payload.bars["H1"]) == 1
    assert payload.bars["H1"][0].close == 2351


def test_should_define_technical_analysis_with_all_timeframes():
    # TS: Analysis types 'should define TechnicalAnalysis with all timeframes'
    ta = TechnicalAnalysis(
        bias="bullish",
        confidence=75,
        phase="trending",
        indicators_summary="Multi-TF bullish alignment with H4 consolidation",
        support_levels=[
            SRLevel(price=2340, type="support", strength="strong", timeframe="H1", touches=3)
        ],
        resistance_levels=[
            SRLevel(price=2360, type="resistance", strength="moderate", timeframe="H4", touches=2)
        ],
        recommendation="hold",
        rationale="Multi-TF bullish alignment with H4 consolidation",
    )

    assert ta.bias == "bullish"
    assert len(ta.support_levels) == 1


def test_should_define_risk_assessment_with_warnings():
    # TS: Analysis types 'should define RiskAssessment with warnings'
    risk = RiskAssessment(
        riskLevel="medium",
        maxPositionSize=0.1,
        suggestedSL=2330,
        warnings=["High spread detected", "News event in 30min"],
    )

    assert len(risk.warnings) == 2


def test_should_define_arbitration_result():
    # TS: Analysis types 'should define ArbitrationResult'
    arb = ArbitrationResult(
        final_direction="buy",
        confidence=70,
        primary_contradiction="none",
        phase="trending",
        reasoning="All timeframes aligned bullish",
        action="open",
        united_front_analysis="Strong consensus across timeframes",
    )

    assert arb.final_direction == "buy"
    assert arb.action == "open"
