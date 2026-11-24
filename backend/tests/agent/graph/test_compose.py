"""Mirror of apps/app-agent/src/graph/compose.test.ts — trade_plan.v1."""

from __future__ import annotations

import re
from dataclasses import asdict

import pytest
from pydantic import ValidationError

from backend.agents.graph.compose import compose_final_signal
from backend.agents.graph.state import AnalysisGraphState
from backend.agents.types.analysis import ArbitrationResult, RiskAssessment, SRLevel, TechnicalAnalysis
from backend.agents.types.goldbot import AccountInfo, GoldbotPayload, MarketData, MarketStatus
from backend.agents.types.schemas import TradePlanSchema


def _base_state() -> AnalysisGraphState:
    return {
        "accountId": "90011087",
        "symbol": "XAUUSD",
        "timestamp": "2026-06-06T09:00:00.000Z",
        "payload": GoldbotPayload(
            account=AccountInfo(
                account_id="90011087",
                balance=10000,
                equity=10100,
                margin=200,
                free_margin=9900,
                currency="USD",
                leverage=500,
            ),
            market=MarketData(symbol="XAUUSD", bid=3335.55, ask=3335.75, spread=0.2),
            indicators={},
            positions=[],
            market_status=MarketStatus(market_open=True, is_trade_allowed=True, tradeable=True),
            strategy_mapping={},
        ),
        "technicalAnalysis": TechnicalAnalysis(
            bias="bullish",
            confidence=74,
            phase="trending",
            indicators_summary="H1 momentum aligned",
            support_levels=[
                SRLevel(price=3328, type="support", strength="strong", timeframe="H1", touches=3),
            ],
            resistance_levels=[
                SRLevel(price=3350, type="resistance", strength="moderate", timeframe="H1", touches=2),
            ],
            recommendation="hold",
            rationale="trend continuation",
        ),
        "riskAssessment": RiskAssessment(
            riskLevel="medium",
            maxPositionSize=0.2,
            suggestedSL=3328,
            warnings=["spread normal"],
        ),
        "arbitration": ArbitrationResult(
            final_direction="buy",
            confidence=82,
            primary_contradiction="none",
            phase="trend-following",
            reasoning="multi-timeframe bullish alignment",
            action="open",
            united_front_analysis="aligned",
        ),
        "logs": [],
        "errors": [],
    }


def test_exports_a_real_trade_plan_schema_validator() -> None:
    assert TradePlanSchema is not None
    assert hasattr(TradePlanSchema, "model_validate")


def test_adds_a_traceable_trade_plan_beside_legacy_ai_result_fields() -> None:
    signal = compose_final_signal(_base_state())
    assert signal is not None
    assert signal.bias == "bullish"
    plan = signal.trade_plan
    assert plan is not None
    assert plan.schema_version == "trade_plan.v1"
    assert plan.account_id == "90011087"
    assert plan.symbol == "XAUUSD"
    assert plan.mode == "approve"
    assert plan.side == "buy"
    assert plan.confidence == 82
    assert plan.stop_loss == 3328
    assert plan.max_lots == 0.2
    assert {"mode.approve", "side.buy"} <= set(plan.reason_codes)
    assert re.match(r"^tpv1_[a-f0-9]{16}$", plan.decision_id)
    assert plan.entry_zone.min == 3335.55
    assert plan.entry_zone.max == 3335.75
    assert plan.take_profit == [3350]
    assert plan.expires_at == "2026-06-06T09:15:00.000Z"
    assert plan.add_on is False
    parsed = TradePlanSchema.model_validate(asdict(plan))
    assert parsed.decision_id == plan.decision_id


def test_rejects_active_trade_plans_with_zero_execution_fields() -> None:
    with pytest.raises(ValidationError):
        TradePlanSchema.model_validate(
            {
                "schema_version": "trade_plan.v1",
                "decision_id": "tpv1_bad",
                "account_id": "90011087",
                "symbol": "XAUUSD",
                "mode": "approve",
                "side": "buy",
                "confidence": 70,
                "entry_zone": {"min": 0, "max": 0},
                "stop_loss": 0,
                "take_profit": [],
                "max_lots": 0,
                "expires_at": "2026-06-06T09:15:00.000Z",
                "reason_codes": ["mode.approve"],
                "conflicts": [],
                "narrative": "invalid active plan",
                "add_on": False,
            }
        )


def test_defaults_add_on_to_false_when_omitted_from_trade_plan_input() -> None:
    parsed = TradePlanSchema.model_validate(
        {
            "schema_version": "trade_plan.v1",
            "decision_id": "tpv1_default_add_on",
            "account_id": "90011087",
            "symbol": "XAUUSD",
            "mode": "observe",
            "side": "none",
            "confidence": 50,
            "entry_zone": {"min": 0, "max": 0},
            "stop_loss": 0,
            "take_profit": [],
            "max_lots": 0,
            "expires_at": "2026-06-06T09:15:00.000Z",
            "reason_codes": ["mode.observe"],
            "conflicts": [],
            "narrative": "default add_on behavior",
        }
    )
    assert parsed.add_on is False


def test_downgrades_an_open_decision_to_observe_when_execution_fields_incomplete() -> None:
    from dataclasses import replace

    state = _base_state()
    technical = state["technicalAnalysis"]
    assert technical is not None
    state["technicalAnalysis"] = replace(technical, resistance_levels=[])
    signal = compose_final_signal(state)
    plan = signal.trade_plan
    assert plan is not None
    assert plan.mode == "observe"
    assert plan.side == "none"
    assert plan.entry_zone.min == 0
    assert plan.entry_zone.max == 0
    assert plan.stop_loss == 0
    assert plan.take_profit == []
    assert plan.max_lots == 0
    assert {"mode.observe", "side.none", "execution.incomplete_fields"} <= set(plan.reason_codes)
    assert "execution.incomplete_fields" in plan.conflicts
    parsed = TradePlanSchema.model_validate(asdict(plan))
    assert parsed.mode == "observe"


def test_maps_blocking_market_filters_to_a_zero_risk_veto_trade_plan() -> None:
    from backend.agents.types.goldbot import MarketFilter, MarketFilters

    state = _base_state()
    payload = state["payload"]
    assert payload is not None
    state["payload"] = GoldbotPayload(
        **{
            **payload.__dict__,
            "market_status": MarketStatus(market_open=True, is_trade_allowed=True, tradeable=False),
            "market_filters": MarketFilters(
                blocked=True,
                blocking=[
                    MarketFilter(code="market.closed", severity="blocking"),
                    MarketFilter(code="spread.too_wide", severity="blocking"),
                ],
                warnings=[MarketFilter(code="session.rollover_window", severity="warning")],
                reason_codes=["market.closed", "spread.too_wide", "session.rollover_window"],
            ),
        }
    )
    from backend.agents.types.analysis import RiskAssessment as RA

    state["riskAssessment"] = RA(riskLevel="low", maxPositionSize=0.2, suggestedSL=3328, warnings=[])

    signal = compose_final_signal(state)
    assert signal is not None
    assert signal.risk_alert is True
    assert "market.closed" in (signal.alert_reason or "")
    assert signal.max_position_size == 0
    plan = signal.trade_plan
    assert plan is not None
    assert plan.mode == "veto"
    assert plan.side == "none"
    assert plan.entry_zone.min == 0
    assert plan.entry_zone.max == 0
    assert plan.stop_loss == 0
    assert plan.take_profit == []
    assert plan.max_lots == 0
    assert {"mode.veto", "side.none", "market.closed", "spread.too_wide"} <= set(plan.reason_codes)
    assert {"market.closed", "spread.too_wide"} <= set(plan.conflicts)
    parsed = TradePlanSchema.model_validate(asdict(plan))
    assert parsed.mode == "veto"


def test_propagates_risk_assessment_add_on_into_trade_plan() -> None:
    from dataclasses import replace

    state = _base_state()
    risk = state["riskAssessment"]
    assert risk is not None
    state["riskAssessment"] = replace(risk, addOn=True, warnings=["trend intact"])
    signal = compose_final_signal(state)
    plan = signal.trade_plan
    assert plan is not None
    assert plan.add_on is True
    parsed = TradePlanSchema.model_validate(asdict(plan))
    assert parsed.add_on is True


def test_event_blackout_vetoes_entries_without_triggering_an_exit_alert() -> None:
    from backend.agents.types.goldbot import MarketFilter, MarketFilters

    state = _base_state()
    state["payload"].market_filters = MarketFilters(
        blocked=True, blocking=[MarketFilter(code="event.blackout_window", severity="blocking")],
    )
    signal = compose_final_signal(state)

    assert signal.arbitration["action"] == "hold"
    assert signal.trade_plan.mode == "veto"
    assert signal.risk_alert is False


def test_final_market_plan_cannot_bypass_an_intrabar_breakout() -> None:
    state = _base_state()
    state["payload"] = {
        **asdict(state["payload"]),
        "breakout_levels": [
            {"price": 3335, "side": "resistance", "timeframe": "H1", "state": "intrabar_broken"},
        ],
    }
    signal = compose_final_signal(state)

    assert signal.arbitration["action"] == "hold"
    assert signal.confidence == 20
    assert signal.trade_plan is None


def test_compose_ignores_d1_intrabar_breakout_levels() -> None:
    state = _base_state()
    state["payload"] = {
        **asdict(state["payload"]),
        "breakout_levels": [
            {"price": 3335, "side": "resistance", "timeframe": "D1", "state": "intrabar_broken"},
        ],
    }
    signal = compose_final_signal(state)

    assert signal is not None
    assert signal.arbitration["action"] == "open"
    assert signal.trade_plan is not None
    assert signal.trade_plan.mode == "approve"


def test_compose_suppresses_trade_plan_for_htf_anchor_but_keeps_m15_entry() -> None:
    htf_state = _base_state()
    htf_state["analysisRole"] = "htf_anchor"
    htf_signal = compose_final_signal(htf_state)

    assert htf_signal is not None
    assert htf_signal.trade_plan is None
    assert htf_signal.dual_trade_plan is None
    assert htf_signal.bias == "bullish"
    assert htf_signal.confidence == 82
    assert htf_signal.suggested_sl == 3328
    assert htf_signal.arbitration is not None
    assert htf_signal.arbitration["action"] == "open"
    assert htf_signal.arbitration["direction"] == "buy"

    dual_htf_state = _base_state()
    dual_htf_state["analysisRole"] = "htf_anchor"
    dual_htf_state["arbitration"] = ArbitrationResult(
        final_direction="dual",
        confidence=75,
        primary_contradiction="none",
        phase="consolidation",
        reasoning="range",
        action="open",
        united_front_analysis="range",
    )
    dual_htf_signal = compose_final_signal(dual_htf_state)
    assert dual_htf_signal is not None
    assert dual_htf_signal.trade_plan is None
    assert dual_htf_signal.dual_trade_plan is None

    m15_state = _base_state()
    m15_state["analysisRole"] = "m15_entry"
    m15_signal = compose_final_signal(m15_state)
    assert m15_signal is not None
    assert m15_signal.trade_plan is not None
    assert m15_signal.trade_plan.mode == "approve"


def test_close_and_reduce_plans_keep_mode_and_trigger_auto_risk_alert() -> None:
    from dataclasses import replace

    from backend.agents.types.goldbot import PositionInfo

    state = _base_state()
    tech = state["technicalAnalysis"]
    arb = state["arbitration"]
    risk = state["riskAssessment"]
    assert tech is not None and arb is not None and risk is not None

    state["technicalAnalysis"] = replace(tech, recommendation="close")
    state["arbitration"] = replace(arb, final_direction="hold", action="close", confidence=78)
    state["riskAssessment"] = replace(risk, riskLevel="medium", suggestedSL=0, maxPositionSize=0, warnings=[])
    state["payload"] = replace(
        state["payload"],
        positions=[
            PositionInfo(
                ticket=1001,
                strategy="ai_signal",
                direction="BUY",
                entry_price=3340.0,
                current_price=3335.5,
                lots=0.1,
                profit=-50.0,
                sl=3320.0,
                tp=0.0,
                symbol="XAUUSD",
            ),
        ],
    )
    signal = compose_final_signal(state)
    assert signal is not None
    assert signal.risk_alert is True
    assert signal.alert_reason == "ai_exit_signal"
    assert signal.trade_plan is not None
    assert signal.trade_plan.mode == "close"
    parsed = TradePlanSchema.model_validate(asdict(signal.trade_plan))
    assert parsed.mode == "close"

    # reduce (partial_close) similarly preserved
    state["technicalAnalysis"] = replace(tech, recommendation="partial_close")
    state["arbitration"] = replace(arb, final_direction="hold", action="hold", confidence=72)
    reduce_signal = compose_final_signal(state)
    assert reduce_signal is not None and reduce_signal.trade_plan is not None
    assert reduce_signal.trade_plan.mode == "reduce"
    assert reduce_signal.risk_alert is True
    assert TradePlanSchema.model_validate(asdict(reduce_signal.trade_plan)).mode == "reduce"

    # Without open positions, auto_exit_alert stays False
    state["payload"] = replace(state["payload"], positions=[])
    flat_signal = compose_final_signal(state)
    assert flat_signal is not None
    assert flat_signal.risk_alert is False


def test_compose_propagates_analysis_timing_meta_into_trade_plan() -> None:
    state = _base_state()
    state["comprehensiveAnalysis"] = {
        "_meta": {
            "analysis_started_at": "2026-06-06T08:59:52.000Z",
            "analysis_start_price": 3335.4,
            "inference_latency_ms": 8120,
        }
    }
    signal = compose_final_signal(state)
    assert signal is not None and signal.trade_plan is not None
    assert signal.trade_plan.analysis_started_at == "2026-06-06T08:59:52.000Z"
    assert signal.trade_plan.analysis_start_price == 3335.4
    assert signal.trade_plan.inference_latency_ms == 8120
    parsed = TradePlanSchema.model_validate(asdict(signal.trade_plan))
    assert parsed.inference_latency_ms == 8120
