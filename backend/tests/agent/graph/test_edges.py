"""Mirror of apps/app-agent/src/graph/edges.test.ts."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from backend.agents.graph.edges import route_after_arbitration, route_after_fetch
from backend.agents.graph.state import AnalysisGraphState
from backend.agents.types.agent import AISignalResult


def create_state(overrides: dict[str, Any]) -> AnalysisGraphState:
    return {
        "accountId": "acc-001",
        "symbol": "XAUUSD",
        "symbols": ["XAUUSD"],
        "timestamp": datetime.now(UTC).isoformat(),
        "logs": [],
        "errors": [],
        **overrides,
    }


class TestRouteAfterFetch:
    def test_routes_to_analyze_when_fetched_payloads_are_mixed_open_and_closed(self) -> None:
        decision = route_after_fetch(
            create_state(
                {
                    "symbol": "XAUUSD",
                    "symbols": [],
                    "payloads": {
                        "XAUUSD": {"market_status": {"market_open": False}},
                        "GBPJPY": {"market_status": {"market_open": True}},
                    },
                }
            )
        )
        assert decision == "analyze"

    def test_routes_to_skip_only_when_all_fetched_payloads_are_closed(self) -> None:
        decision = route_after_fetch(
            create_state(
                {
                    "symbols": ["XAUUSD", "XAGUSD"],
                    "payloads": {
                        "XAUUSD": {"market_status": {"market_open": False}},
                        "XAGUSD": {"market_status": {"market_open": False}},
                    },
                }
            )
        )
        assert decision == "skip"


class TestRouteAfterArbitration:
    def test_publishes_when_final_signal_has_arbitration(self) -> None:
        state = create_state(
            {
                "finalSignal": AISignalResult(
                    bias="bullish",
                    confidence=80,
                    exit_suggestion="hold",
                    risk_alert=False,
                    arbitration={"direction": "buy", "action": "open", "reasoning": "x"},
                )
            }
        )
        assert route_after_arbitration(state) == "publish"

    def test_skips_publish_when_final_signal_missing_arbitration(self) -> None:
        state = create_state(
            {
                "finalSignal": AISignalResult(
                    bias="neutral",
                    confidence=50,
                    exit_suggestion="hold",
                    risk_alert=False,
                    arbitration=None,
                )
            }
        )
        assert route_after_arbitration(state) == "skip_publish"

    def test_publishes_when_any_final_signal_has_arbitration(self) -> None:
        state = create_state(
            {
                "finalSignals": {
                    "XAUUSD": AISignalResult(
                        bias="bearish",
                        confidence=70,
                        exit_suggestion="close",
                        risk_alert=True,
                        arbitration={"direction": "sell", "action": "close", "reasoning": "y"},
                    ),
                    "XAGUSD": AISignalResult(
                        bias="neutral",
                        confidence=40,
                        exit_suggestion="hold",
                        risk_alert=False,
                        arbitration=None,
                    ),
                }
            }
        )
        assert route_after_arbitration(state) == "publish"

    def test_skips_publish_when_no_final_signal_has_arbitration(self) -> None:
        state = create_state(
            {
                "finalSignals": {
                    "XAUUSD": AISignalResult(
                        bias="neutral",
                        confidence=40,
                        exit_suggestion="hold",
                        risk_alert=False,
                        arbitration=None,
                    ),
                }
            }
        )
        assert route_after_arbitration(state) == "skip_publish"
