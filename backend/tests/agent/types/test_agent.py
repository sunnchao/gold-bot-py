"""镜像 gold-bot `apps/app-agent/src/types/agent.test.ts`。"""
from datetime import UTC, datetime

from backend.agents.types.agent import create_initial_state
from backend.agents.types.analysis import SRLevel, TechnicalAnalysis
from backend.agents.types.goldbot import (
    AccountInfo,
    GoldbotPayload,
    IndicatorPack,
    MarketData,
    MarketStatus,
)


def test_should_create_a_valid_initial_state():
    # TS: createInitialState 'should create a valid initial state'
    state = create_initial_state("acc-001", "XAUUSD")

    assert state.accountId == "acc-001"
    assert state.symbol == "XAUUSD"
    assert state.timestamp is not None
    assert state.logs == []
    assert state.errors == []
    assert state.payload is None
    assert state.technicalAnalysis is None


def test_should_have_iso_timestamp():
    # TS: createInitialState 'should have ISO timestamp'
    state = create_initial_state("acc-001", "XAUUSD")

    parsed = datetime.fromisoformat(state.timestamp.replace("Z", "+00:00"))
    assert parsed.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z") == state.timestamp


def _payload() -> GoldbotPayload:
    return GoldbotPayload(
        account=AccountInfo(
            account_id="acc-001",
            balance=10000,
            equity=10050,
            margin=500,
            free_margin=9550,
            leverage=100,
            currency="USD",
        ),
        market=MarketData(symbol="XAUUSD", bid=2350.30, ask=2350.70, spread=0.40),
        indicators={
            "H1": IndicatorPack(
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
        },
        positions=[],
        market_status=MarketStatus(market_open=True, is_trade_allowed=True, tradeable=True),
        strategy_mapping={"strategyId": "default", "strategyName": "Default Strategy", "parameters": "{}"},
    )


def test_should_allow_setting_all_optional_fields():
    # TS: AnalysisState types 'should allow setting all optional fields'
    state = create_initial_state("acc-001", "XAUUSD")
    state.payload = _payload()
    state.technicalAnalysis = TechnicalAnalysis(
        bias="bullish",
        confidence=75,
        phase="trending",
        indicators_summary="Multi-timeframe bullish alignment",
        support_levels=[SRLevel(price=2340, type="support", strength="strong", timeframe="H1", touches=3)],
        resistance_levels=[SRLevel(price=2360, type="resistance", strength="moderate", timeframe="H4", touches=2)],
        recommendation="hold",
        rationale="Multi-timeframe bullish alignment",
    )

    assert state.payload is not None
    assert state.payload.market.symbol == "XAUUSD"
    assert state.technicalAnalysis is not None
    assert state.technicalAnalysis.bias == "bullish"
