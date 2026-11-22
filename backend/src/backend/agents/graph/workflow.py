"""Analysis workflow assembly & invocation (zero-LangGraph async runner)."""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, cast

from backend.agents.graph.edges import route_after_arbitration, route_after_fetch
from backend.agents.graph.state import AnalysisGraphState, _append_errors, _append_logs
from backend.agents.graph.workflow_nodes import WorkflowNodes


def _monotonic_ms() -> int:
    return int(time.monotonic() * 1000)


def _now_iso() -> str:
    return (
        datetime.now(UTC)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


class _CompiledWorkflow:
    """Deterministic state-machine runner replacing LangGraph StateGraph."""

    def __init__(
        self,
        nodes: WorkflowNodes,
        after_fetch: Callable[[AnalysisGraphState], str],
        after_arbitration: Callable[[AnalysisGraphState], str],
    ) -> None:
        self._nodes = nodes
        self._after_fetch = after_fetch
        self._after_arbitration = after_arbitration

    @staticmethod
    def _merge(state: dict[str, Any], update: dict[str, Any] | None) -> dict[str, Any]:
        if not update:
            return state
        merged = dict(state)
        for key, value in update.items():
            if key == "logs":
                merged["logs"] = _append_logs(list(merged.get("logs") or []), list(value or []))
            elif key == "errors":
                merged["errors"] = _append_errors(list(merged.get("errors") or []), list(value or []))
            else:
                merged[key] = value
        return merged

    async def ainvoke(self, initial: dict[str, Any]) -> dict[str, Any]:
        state = self._merge(initial, await self._nodes.fetch_data(initial))  # type: ignore[arg-type]
        branch = self._after_fetch(state)  # type: ignore[arg-type]
        if branch == "error":
            return self._merge(state, await self._nodes.error_node(state))  # type: ignore[arg-type]
        if branch == "skip":
            return self._merge(state, await self._nodes.skip_node(state))  # type: ignore[arg-type]

        for step in (
            self._nodes.dispatch_analysis,
            self._nodes.comprehensive_analysis,
            self._nodes.compose_signal,
        ):
            state = self._merge(state, await step(state))  # type: ignore[arg-type]

        if self._after_arbitration(state) == "publish":  # type: ignore[arg-type]
            state = self._merge(state, await self._nodes.publish_result(state))  # type: ignore[arg-type]
        return state


class WorkflowService:
    """Compiles and runs the analysis workflow."""

    def __init__(self, nodes: WorkflowNodes) -> None:
        self._nodes = nodes
        self._compiled_workflow: _CompiledWorkflow | None = None

    def _build_workflow(self) -> _CompiledWorkflow:
        return _CompiledWorkflow(
            self._nodes,
            self._route_after_fetch,
            self._route_after_arbitration,
        )

    @staticmethod
    def _route_after_fetch(state: AnalysisGraphState) -> str:
        return route_after_fetch(state)

    @staticmethod
    def _route_after_arbitration(state: AnalysisGraphState) -> str:
        return route_after_arbitration(state)

    def _get_workflow(self) -> _CompiledWorkflow:
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
        final: dict[str, Any] = {
            **result,
            "symbol": result.get("symbol") or primary_symbol,
            "symbols": result.get("symbols") or symbols,
            "duration": _monotonic_ms() - start_time,
        }
        return cast(AnalysisGraphState, final)
