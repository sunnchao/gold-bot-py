"""Mirror of gold-bot analysis.processor.test.ts."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import pytest

from backend.agents.scheduler.analysis_processor import AnalysisProcessor


@dataclass
class SimpleSignal:
    bias: str
    confidence: int
    exit_suggestion: str
    risk_alert: bool
    extra: dict[str, Any] = field(default_factory=dict)


class FakeStore:
    def __init__(self, errors: list[Exception] | None = None) -> None:
        self.saved: list[tuple[str, str, Any, int]] = []
        self.errors = list(errors or [])

    def save_result(self, account_id: str, symbol: str, result: Any, duration: int) -> None:
        if self.errors:
            raise self.errors.pop(0)
        self.saved.append((account_id, symbol, result, duration))


class FakeQueue:
    def __init__(self) -> None:
        self.clean_calls: list[tuple[int, int, str]] = []

    async def clean(self, grace_period: int, limit: int, state: str) -> Any:
        self.clean_calls.append((grace_period, limit, state))
        return None


class FakeWorkflow:
    def __init__(self, results: list[Any] | None = None) -> None:
        self.results = list(results or [])
        self.handler: Any = None
        self.run_calls: list[tuple[str, list[str], dict[str, Any] | None]] = []

    async def run(
        self,
        account_id: str,
        symbols: list[str],
        initial_state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.run_calls.append((account_id, symbols, initial_state))
        if self.handler is not None:
            return await self.handler(account_id, symbols, initial_state)
        if self.results:
            return self.results.pop(0)
        return {"finalSignal": None}


def deferred() -> asyncio.Future[dict[str, Any]]:
    return asyncio.get_running_loop().create_future()


@pytest.mark.asyncio
async def test_runs_workflow_once_per_configured_symbol_and_saves_final_signals() -> None:
    final_signal = SimpleSignal(bias="bullish", confidence=80, exit_suggestion="hold", risk_alert=False)
    workflow = FakeWorkflow(
        results=[
            {"finalSignals": {"XAUUSD": final_signal}, "durations": {"XAUUSD": 100}},
            {"finalSignals": {"XAGUSD": final_signal}, "durations": {"XAGUSD": 110}},
            {"finalSignals": {"XAUUSD": final_signal}, "durations": {"XAUUSD": 90}},
        ]
    )
    config = SimpleConfig(
        accounts=[
            {"id": "acc-001", "symbols": ["XAUUSD", "XAGUSD"]},
            {"id": "acc-002", "symbols": ["XAUUSD"]},
        ]
    )
    store = FakeStore()
    queue = FakeQueue()
    processor = AnalysisProcessor(config, workflow, store, queue)

    result = await processor.process({"id": "job-1", "name": "scheduled-analysis"})

    assert [(c[0], c[1]) for c in workflow.run_calls] == [
        ("acc-001", ["XAUUSD"]),
        ("acc-001", ["XAGUSD"]),
        ("acc-002", ["XAUUSD"]),
    ]
    assert len(store.saved) == 3
    assert result["succeeded"] == 3
    assert result["failed"] == 0
    assert result["saveFailed"] == 0
    assert result["total"] == 3


@pytest.mark.asyncio
async def test_cleans_completed_and_failed_jobs_on_module_init() -> None:
    config = SimpleConfig(accounts=[])
    workflow = FakeWorkflow()
    store = FakeStore()
    queue = FakeQueue()
    processor = AnalysisProcessor(config, workflow, store, queue)

    await processor.on_module_init()

    assert queue.clean_calls == [(0, 100, "completed"), (0, 50, "failed")]


@pytest.mark.asyncio
async def test_tracks_save_failures_in_the_job_result_instead_of_full_success() -> None:
    final_signal = SimpleSignal(bias="bullish", confidence=80, exit_suggestion="hold", risk_alert=False)
    workflow = FakeWorkflow(
        results=[
            {"finalSignals": {"XAUUSD": final_signal}, "durations": {"XAUUSD": 100}},
            {"finalSignals": {"XAGUSD": final_signal}, "durations": {"XAGUSD": 110}},
        ]
    )
    config = SimpleConfig(accounts=[{"id": "acc-001", "symbols": ["XAUUSD", "XAGUSD"]}])
    store = FakeStore(errors=[RuntimeError("disk full")])
    queue = FakeQueue()
    processor = AnalysisProcessor(config, workflow, store, queue)

    result = await processor.process({"id": "job-2", "name": "scheduled-analysis"})

    assert len(store.saved) == 1
    assert result["succeeded"] == 2
    assert result["failed"] == 0
    assert result["saveFailed"] == 1
    assert result["total"] == 2


@pytest.mark.asyncio
async def test_counts_failed_workflow_runs_separately() -> None:
    final_signal = SimpleSignal(bias="bullish", confidence=80, exit_suggestion="hold", risk_alert=False)
    workflow = FakeWorkflow()

    async def run(
        account_id: str,
        symbols: list[str],
        initial_state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if symbols[0] == "XAGUSD":
            raise RuntimeError("workflow exploded")
        return {"finalSignals": {"XAUUSD": final_signal}, "durations": {"XAUUSD": 100}}

    workflow.handler = run
    config = SimpleConfig(accounts=[{"id": "acc-001", "symbols": ["XAUUSD", "XAGUSD"]}])
    store = FakeStore()
    queue = FakeQueue()
    processor = AnalysisProcessor(config, workflow, store, queue)

    result = await processor.process({"id": "job-2b", "name": "scheduled-analysis"})

    assert result["succeeded"] == 1
    assert result["failed"] == 1
    assert result["saveFailed"] == 0
    assert result["total"] == 2
    assert len(store.saved) == 1


@pytest.mark.asyncio
async def test_saves_a_fast_symbol_result_before_a_slower_symbol_workflow_resolves() -> None:
    slow_signal = SimpleSignal(bias="bullish", confidence=80, exit_suggestion="hold", risk_alert=False)
    fast_signal = SimpleSignal(bias="neutral", confidence=60, exit_suggestion="hold", risk_alert=False)
    slow = deferred()
    fast = deferred()

    async def run(
        account_id: str,
        symbols: list[str],
        initial_state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if symbols[0] == "XAUUSD":
            return await slow
        return await fast

    config = SimpleConfig(accounts=[{"id": "acc-001", "symbols": ["XAUUSD", "XAGUSD"]}])
    store = FakeStore()
    queue = FakeQueue()
    workflow = FakeWorkflow()
    workflow.handler = run
    processor = AnalysisProcessor(config, workflow, store, queue)

    processing = asyncio.create_task(
        processor.process({"id": "job-3", "name": "scheduled-analysis"})
    )
    completed = False

    async def _mark_done() -> None:
        nonlocal completed
        await processing
        completed = True

    done_task = asyncio.create_task(_mark_done())
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    fast.set_result({"finalSignal": fast_signal, "duration": 50})
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert store.saved == [("acc-001", "XAGUSD", fast_signal, 50)]
    assert completed is False

    slow.set_result({"finalSignal": slow_signal, "duration": 100})
    result = await processing
    await done_task

    assert store.saved == [
        ("acc-001", "XAGUSD", fast_signal, 50),
        ("acc-001", "XAUUSD", slow_signal, 100),
    ]
    assert result["succeeded"] == 2
    assert result["failed"] == 0
    assert result["saveFailed"] == 0
    assert result["total"] == 2


class SimpleConfig:
    def __init__(self, accounts: list[Any]) -> None:
        self.accounts = accounts
