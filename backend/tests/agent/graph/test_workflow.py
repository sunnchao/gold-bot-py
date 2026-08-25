"""Mirror of apps/app-agent/src/graph/workflow.service.test.ts."""

from __future__ import annotations

from typing import Any

import pytest

from backend.agents.graph.state import AnalysisGraphState
from backend.agents.graph.workflow import WorkflowService


class FakeNodes:
    def __init__(self, fetch_result: dict[str, Any]) -> None:
        self._fetch_result = fetch_result
        self.fetch_data_calls = 0
        self.skip_node_calls = 0

    async def fetch_data(self, state: AnalysisGraphState) -> dict[str, Any]:
        self.fetch_data_calls += 1
        return dict(self._fetch_result)

    async def dispatch_analysis(self, state: AnalysisGraphState) -> dict[str, Any]:
        return {}

    async def comprehensive_analysis(self, state: AnalysisGraphState) -> dict[str, Any]:
        raise AssertionError("comprehensiveAnalysis should not run when skipping")

    async def compose_signal(self, state: AnalysisGraphState) -> dict[str, Any]:
        raise AssertionError("composeSignal should not run when skipping")

    async def publish_result(self, state: AnalysisGraphState) -> dict[str, Any]:
        raise AssertionError("publishResult should not run when skipping")

    async def skip_node(self, state: AnalysisGraphState) -> dict[str, Any]:
        self.skip_node_calls += 1
        return {"logs": []}

    async def error_node(self, state: AnalysisGraphState) -> dict[str, Any]:
        raise AssertionError("errorNode should not run when skipping")


@pytest.mark.asyncio
async def test_runs_compiled_workflow_for_a_single_symbol_and_appends_duration() -> None:
    nodes = FakeNodes({"payload": {"market_status": {"market_open": False}}, "logs": []})
    service = WorkflowService(nodes)  # type: ignore[arg-type]

    result = await service.run("acc-001", "XAUUSD")

    assert result["accountId"] == "acc-001"
    assert result["symbol"] == "XAUUSD"
    assert result["symbols"] == ["XAUUSD"]
    assert isinstance(result["duration"], int)
    assert nodes.fetch_data_calls == 1
    assert nodes.skip_node_calls == 1


@pytest.mark.asyncio
async def test_runs_compiled_workflow_for_multiple_symbols_in_one_invocation() -> None:
    nodes = FakeNodes(
        {
            "payloads": {
                "XAUUSD": {"market_status": {"market_open": False}},
                "XAGUSD": {"market_status": {"market_open": False}},
            },
            "logs": [],
        }
    )
    service = WorkflowService(nodes)  # type: ignore[arg-type]

    result = await service.run("acc-001", ["XAUUSD", "XAGUSD"])

    assert result["accountId"] == "acc-001"
    assert result["symbol"] == "XAUUSD"
    assert result["symbols"] == ["XAUUSD", "XAGUSD"]
    assert isinstance(result["duration"], int)
    assert nodes.fetch_data_calls == 1
    assert nodes.skip_node_calls == 1


@pytest.mark.asyncio
async def test_builds_workflow_once_and_reuses_compiled_graph() -> None:
    nodes = FakeNodes({"payload": {"market_status": {"market_open": False}}, "logs": []})
    service = WorkflowService(nodes)  # type: ignore[arg-type]

    first = service._get_workflow()
    second = service._get_workflow()
    assert first is second
