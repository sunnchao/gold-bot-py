"""Mirror of apps/app-agent/src/graph/compose.spec.ts — AI order intent."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from backend.agents.graph.compose import compose_final_signal
from backend.agents.graph.state import AnalysisGraphState
from backend.agents.types.analysis import (
    ArbitrationResult,
    ChanlunTheoryAnalysis,
    DowTheoryAnalysis,
    HarmonicTheoryAnalysis,
    RiskAssessment,
    SRLevel,
    TechnicalAnalysis,
    WaveTheoryAnalysis,
)
from backend.agents.types.goldbot import AccountInfo, GoldbotPayload, MarketData, MarketStatus
from backend.agents.types.trade_action import DoNothingAction, TradeAction


def _payload() -> GoldbotPayload:
    return GoldbotPayload(
        account=AccountInfo(
            account_id="90011087",
            equity=10000,
            balance=10000,
            margin=100,
            free_margin=9900,
            currency="USD",
            leverage=500,
        ),
        market=MarketData(symbol="XAUUSD", bid=3335.5, ask=3335.7, spread=0.2),
        indicators={},
        positions=[],
        market_status=MarketStatus(market_open=True, is_trade_allowed=True, tradeable=True),
        strategy_mapping={},
    )


def _action_field(action: TradeAction, name: str, default: str | None = None) -> str | None:
    if isinstance(action, dict):
        return action.get(name, default)
    return getattr(action, name, default)


def state_with_trade_action(trade_action: TradeAction) -> AnalysisGraphState:
    side_raw = _action_field(trade_action, "side")
    side = "hold" if _action_field(trade_action, "type") == "do_nothing" else (side_raw or "hold")
    assert side is not None
    direction = "buy" if side == "buy" else "sell" if side == "sell" else "hold"
    action = "open" if side in ("buy", "sell") else "hold"
    return {
        "accountId": "90011087",
        "symbol": "XAUUSD",
        "timestamp": "2026-04-13T08:00:00.000Z",
        "payload": _payload(),
        "arbitration": ArbitrationResult(
            final_direction=direction,  # type: ignore[arg-type]
            confidence=80,
            primary_contradiction="none",
            phase="markup",
            action=action,  # type: ignore[arg-type]
            reasoning="AI generated structured order intent",
            united_front_analysis="aligned",
            dow_theory=DowTheoryAnalysis(
                primary_trend="bearish" if side == "sell" else "bullish",
                primary_phase="distribution" if side == "sell" else "markup",
                secondary_trend="bearish" if side == "sell" else "bullish",
                short_term_trend="bearish" if side == "sell" else "bullish",
                multi_tf_confirm=True,
                rationale="trend aligned",
            ),
            wave_theory=WaveTheoryAnalysis(
                current_wave="3",
                wave_direction="impulse_down" if side == "sell" else "impulse_up",
                wave_count="impulse",
                next_target="target",
                confidence=80,
                rationale="wave aligned",
            ),
            chanlun_theory=ChanlunTheoryAnalysis(
                trend="down" if side == "sell" else "up",
                bi_direction="down" if side == "sell" else "up",
                duan_direction="down" if side == "sell" else "up",
                zhongshu_state="none",
                buy_sell_point="sell_2" if side == "sell" else "buy_2",
                confidence=80,
                rationale="chanlun aligned",
            ),
            harmonic_theory=HarmonicTheoryAnalysis(
                pattern="none",
                direction="bearish" if side == "sell" else "bullish",
                confidence=0,
                rationale="no harmonic conflict",
            ),
        ),
        "riskAssessment": RiskAssessment(
            riskLevel="medium",
            maxPositionSize=0.01,
            suggestedSL=3330,
            suggestedTP=3345,
            warnings=[],
            addOn=False,
        ),
        "tradeAction": trade_action,
        "logs": [],
        "errors": [],
    }


@pytest.fixture(autouse=True)
def _clean_market_first_keys() -> None:
    # No module-level mutable state in compose; kept for parity with TS describe layout.
    return None


class TestComposeFinalSignalAiOrderIntent:
    def test_adds_explicit_market_intent_for_current_price_trade_actions(self) -> None:
        signal = compose_final_signal(
            state_with_trade_action(
                {
                    "type": "place_market_order",
                    "account_id": "90011087",
                    "symbol": "XAUUSD",
                    "side": "buy",
                    "stop_loss": 3330,
                    "take_profit_1": 3345,
                    "lots": 0.01,
                    "reason": "可以当前价入场 (enter at current price)",
                }
            )
        )
        plan = signal.trade_plan
        assert plan is not None
        assert plan.mode == "approve"
        assert plan.side == "buy"
        assert plan.entry_zone.min == 3335.5
        assert plan.entry_zone.max == 3335.7
        assert plan.execution_type == "market"
        assert plan.requested_order_type == "market"
        assert "fc.place_market_order" in plan.reason_codes
        assert "order.market" in plan.reason_codes

    def test_maps_arbitration_harmonic_theory_to_top_level(self) -> None:
        state = state_with_trade_action(
            DoNothingAction(type="do_nothing", reasoning="hold")
        )
        signal = compose_final_signal(state)
        assert signal is not None
        assert signal.harmonic_theory == state["arbitration"].harmonic_theory

    def test_maps_buy_limit_trade_actions_to_buy_limit_intent(self) -> None:
        signal = compose_final_signal(
            state_with_trade_action(
                {
                    "type": "place_pending_order",
                    "account_id": "90011087",
                    "symbol": "XAUUSD",
                    "side": "buy",
                    "entry_price": 3332.5,
                    "stop_loss": 3328,
                    "take_profit_1": 3344,
                    "lots": 0.01,
                    "order_type": "limit",
                    "expiry_hours": 4,
                    "reason": "回调到价格做多 (buy the pullback)",
                }
            )
        )
        plan = signal.trade_plan
        assert plan is not None
        assert plan.mode == "approve"
        assert plan.side == "buy"
        assert plan.entry_zone.min == 3332.5
        assert plan.entry_zone.max == 3332.5
        assert plan.execution_type == "limit"
        assert plan.requested_order_type == "BUY_LIMIT"
        assert "fc.place_pending_order" in plan.reason_codes
        assert "order.BUY_LIMIT" in plan.reason_codes

    def test_end_to_end_buy_limit_at_4145(self) -> None:
        state = state_with_trade_action(
            {
                "type": "place_pending_order",
                "account_id": "90011087",
                "symbol": "XAUUSD",
                "side": "buy",
                "entry_price": 4145,
                "stop_loss": 4125,
                "take_profit_1": 4188,
                "take_profit_2": 4205,
                "lots": 0.05,
                "order_type": "limit",
                "expiry_hours": 4,
                "reason": "等待回调至 4145 (Fib 0.382) 入场",
            }
        )
        arbitration = state["arbitration"]
        assert arbitration is not None
        state["arbitration"] = ArbitrationResult(
            **{**arbitration.__dict__, "final_direction": "buy", "action": "open", "confidence": 75}
        )
        payload = state["payload"]
        assert payload is not None
        state["payload"] = GoldbotPayload(
            **{**payload.__dict__, "market": MarketData(symbol="XAUUSD", bid=4174, ask=4174.5, spread=0.5)}
        )

        signal = compose_final_signal(state)
        plan = signal.trade_plan
        assert plan is not None
        assert plan.mode == "approve"
        assert plan.side == "buy"
        assert plan.execution_type == "limit"
        assert plan.requested_order_type == "BUY_LIMIT"
        assert plan.entry_zone.min == 4145
        assert plan.entry_zone.max == 4145
        assert plan.stop_loss == 4125
        assert plan.take_profit == [4188, 4205]
        assert plan.max_lots == 0.05
        expires_ms = datetime.fromisoformat(plan.expires_at.replace("Z", "+00:00")).timestamp() * 1000
        now_ms = datetime.now(UTC).timestamp() * 1000
        assert expires_ms - now_ms > 4 * 3600 * 1000 - 1000

    def test_maps_sell_limit_trade_actions_to_sell_limit_intent(self) -> None:
        signal = compose_final_signal(
            state_with_trade_action(
                {
                    "type": "place_pending_order",
                    "account_id": "90011087",
                    "symbol": "XAUUSD",
                    "side": "sell",
                    "entry_price": 3338.5,
                    "stop_loss": 3344,
                    "take_profit_1": 3322,
                    "lots": 0.01,
                    "order_type": "limit",
                    "expiry_hours": 4,
                    "reason": "反弹到价格做空 (sell the rebound)",
                }
            )
        )
        plan = signal.trade_plan
        assert plan is not None
        assert plan.mode == "approve"
        assert plan.side == "sell"
        assert plan.entry_zone.min == 3338.5
        assert plan.entry_zone.max == 3338.5
        assert plan.execution_type == "limit"
        assert plan.requested_order_type == "SELL_LIMIT"
        assert "fc.place_pending_order" in plan.reason_codes
        assert "order.SELL_LIMIT" in plan.reason_codes

    def test_does_not_publish_executable_approve_plans_for_pending_stop_actions(self) -> None:
        signal = compose_final_signal(
            state_with_trade_action(
                {
                    "type": "place_pending_order",
                    "account_id": "90011087",
                    "symbol": "XAUUSD",
                    "side": "buy",
                    "entry_price": 3342,
                    "stop_loss": 3335,
                    "take_profit_1": 3358,
                    "lots": 0.01,
                    "order_type": "stop",
                    "expiry_hours": 4,
                    "reason": "突破追多 disabled by design",
                }
            )
        )
        assert signal.trade_plan is None

    @pytest.mark.parametrize(
        "reasoning",
        ["price.deviation_too_large", "account.symbol_not_loaded"],
    )
    def test_vetoes_market_first_open_insights_when_account_action_denied(self, reasoning: str) -> None:
        state = state_with_trade_action(
            DoNothingAction(type="do_nothing", account_id="90011087", reasoning=reasoning)
        )
        arbitration = state["arbitration"]
        assert arbitration is not None
        state["accountActions"] = {"XAUUSD": state["tradeAction"]}
        state["arbitration"] = ArbitrationResult(
            final_direction="buy",
            confidence=80,
            primary_contradiction="none",
            phase="markup",
            action="open",
            reasoning="AI generated structured order intent",
            united_front_analysis="aligned",
            dow_theory=arbitration.dow_theory,
            wave_theory=arbitration.wave_theory,
            chanlun_theory=arbitration.chanlun_theory,
            harmonic_theory=arbitration.harmonic_theory,
        )
        signal = compose_final_signal(state)
        assert signal is not None
        assert signal.arbitration is not None
        assert signal.arbitration["direction"] == "hold"
        assert signal.arbitration["action"] == "hold"
        assert signal.trade_plan is None
        assert signal.dual_trade_plan is None

    def test_allows_market_first_trade_plans_only_from_account_aware_open_actions(self) -> None:
        action: TradeAction = {
            "type": "place_market_order",
            "account_id": "90011087",
            "symbol": "XAUUSD",
            "side": "buy",
            "stop_loss": 3330,
            "take_profit_1": 3345,
            "lots": 0.01,
            "reason": "account approved",
        }
        state = state_with_trade_action(action)
        state["accountActions"] = {"XAUUSD": action}
        signal = compose_final_signal(state)
        assert signal is not None
        assert signal.arbitration is not None
        assert signal.arbitration["direction"] == "buy"
        assert signal.arbitration["action"] == "open"
        plan = signal.trade_plan
        assert plan is not None
        assert plan.mode == "approve"
        assert plan.side == "buy"
        assert plan.execution_type == "market"
        assert plan.requested_order_type == "market"

    def test_adds_explicit_market_intent_to_dual_approve_trade_plans(self) -> None:
        base_state = state_with_trade_action(
            DoNothingAction(type="do_nothing", reasoning="dual arbitration falls back to dual_trade_plan")
        )
        arbitration = base_state["arbitration"]
        assert arbitration is not None
        state: AnalysisGraphState = {
            **base_state,
            "arbitration": ArbitrationResult(final_direction="dual", action="open", **{
                k: v for k, v in arbitration.__dict__.items() if k not in ("final_direction", "action")
            }),
            "technicalAnalysis": TechnicalAnalysis(
                bias="neutral",
                confidence=80,
                phase="trending",
                indicators_summary="dual setup around current price",
                support_levels=[
                    SRLevel(price=3325, type="support", strength="strong", timeframe="H1", touches=3)
                ],
                resistance_levels=[
                    SRLevel(price=3345, type="resistance", strength="strong", timeframe="H1", touches=3)
                ],
                recommendation="hold",
                rationale="both directions possible after trigger",
            ),
        }
        signal = compose_final_signal(state)
        assert signal is not None
        assert signal.trade_plan is None
        dual = signal.dual_trade_plan
        assert dual is not None
        assert dual.buy.mode == "approve"
        assert dual.buy.side == "buy"
        assert dual.buy.entry_zone.min == 3335.5
        assert dual.buy.entry_zone.max == 3335.7
        assert dual.buy.execution_type == "market"
        assert dual.buy.requested_order_type == "market"
        assert {"mode.approve", "side.buy", "order.market"} <= set(dual.buy.reason_codes)
        assert dual.sell.mode == "approve"
        assert dual.sell.side == "sell"
        assert dual.sell.entry_zone.min == 3335.5
        assert dual.sell.entry_zone.max == 3335.7
        assert dual.sell.execution_type == "market"
        assert dual.sell.requested_order_type == "market"
        assert {"mode.approve", "side.sell", "order.market"} <= set(dual.sell.reason_codes)
