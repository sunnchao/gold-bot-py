"""StateGraph assembly & invocation (mirror of apps/app-agent/src/graph/workflow.service.ts)."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

from langgraph.graph import END, START, StateGraph

from backend.agents.graph.edges import route_after_arbitration, route_after_fetch
from backend.agents.graph.state import AnalysisGraphState
from backend.agents.graph.workflow_nodes import WorkflowNodes


def _monotonic_ms() -> int:
    return int(time.monotonic() * 1000)


def _now_iso() -> str:
    return (
        datetime.now(UTC)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


class WorkflowService:
    """Compiles and runs the analysis StateGraph."""

    def __init__(self, nodes: WorkflowNodes) -> None:
        self._nodes = nodes
        self._compiled_workflow: Any | None = None

    def _build_workflow(self) -> Any:
        nodes = self._nodes
        graph = (
            StateGraph(AnalysisGraphState)
            .add_node("fetchData", nodes.fetch_data)
            .add_node("dispatchAnalysis", nodes.dispatch_analysis)
            .add_node("runComprehensiveAnalysis", nodes.comprehensive_analysis)
            .add_node("composeSignal", nodes.compose_signal)
            .add_node("publishResult", nodes.publish_result)
            .add_node("skipNode", nodes.skip_node)
            .add_node("errorNode", nodes.error_node)
            .add_edge(START, "fetchData")
            .add_conditional_edges(
                "fetchData",
                self._route_after_fetch,
                {"error": "errorNode", "skip": "skipNode", "analyze": "dispatchAnalysis"},
            )
            .add_edge("dispatchAnalysis", "runComprehensiveAnalysis")
            .add_edge("runComprehensiveAnalysis", "composeSignal")
            .add_conditional_edges(
                "composeSignal",
                self._route_after_arbitration,
                {"publish": "publishResult", "skip_publish": END},
            )
            .add_edge("publishResult", END)
            .add_edge("skipNode", END)
            .add_edge("errorNode", END)
        )
        return graph.compile()

    @staticmethod
    def _route_after_fetch(state: AnalysisGraphState) -> str:
        return route_after_fetch(state)

    @staticmethod
    def _route_after_arbitration(state: AnalysisGraphState) -> str:
        return route_after_arbitration(state)

    def _get_workflow(self) -> Any:
        if self._compiled_workflow is None:
            self._compiled_workflow = self._build_workflow()
        return self._compiled_workflow

    async def run(
        self,
        account_id: str,
        symbols_or_symbol: str | list[str],
        initial_state: dict[str, Any] | None = None,
    ) -> AnalysisGraphState:
        """Run the workflow for one or more symbols; appends wall-clock duration."""
        symbols = symbols_or_symbol if isinstance(symbols_or_symbol, list) else [symbols_or_symbol]
        primary_symbol = symbols[0] if symbols else ""
        start_time = _monotonic_ms()
        initial: dict[str, Any] = {
            "accountId": account_id,
            "symbol": primary_symbol,
            "symbols": symbols,
            "timestamp": _now_iso(),
            "logs": [],
            "errors": [],
        }
        if initial_state:
            initial.update(initial_state)

        result = await self._get_workflow().ainvoke(initial)

        return {
            **result,
            "symbol": result.get("symbol") or primary_symbol,
            "symbols": result.get("symbols") or symbols,
            "duration": _monotonic_ms() - start_time,
        }
