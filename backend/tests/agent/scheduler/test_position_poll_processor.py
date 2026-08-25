"""Mirror of gold-bot position-poll.processor.test.ts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from backend.agents.scheduler.position_poll_processor import PositionPollProcessor


@dataclass
class SimpleSignal:
    bias: str
    confidence: int
    exit_suggestion: str
    risk_alert: bool


class FakeWorkflow:
    def __init__(self, results: list[dict[str, Any]]) -> None:
        self.results = list(results)
        self.run_calls: list[tuple[str, list[str], dict[str, Any] | None]] = []

    async def run(
        self,
        account_id: str,
        symbols: list[str],
        initial_state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.run_calls.append((account_id, symbols, initial_state))
        return self.results.pop(0)


class SimpleConfig:
    def __init__(self, accounts: list[Any]) -> None:
        self.accounts = accounts


@pytest.mark.asyncio
async def test_runs_workflow_for_every_configured_symbol_and_posts_non_hold_results() -> None:
    hold_signal = SimpleSignal(bias="neutral", confidence=60, exit_suggestion="hold", risk_alert=False)
    close_signal = SimpleSignal(bias="bearish", confidence=75, exit_suggestion="close", risk_alert=True)
    config = SimpleConfig(
        accounts=[
            {"id": "acc-001", "symbols": ["XAUUSD", "XAGUSD"]},
            {"id": "acc-002", "symbols": ["XAUEUR"]},
        ]
    )
    workflow = FakeWorkflow(
        results=[
            {"finalSignal": hold_signal},
            {"finalSignal": hold_signal},
            {"finalSignal": close_signal},
        ]
    )
    processor = PositionPollProcessor(config, workflow)

    result = await processor.process({"id": "job-1", "name": "position-poll"})

    assert [(c[0], c[1], c[2]) for c in workflow.run_calls] == [
        ("acc-001", ["XAUUSD"], {"skipFeishu": True}),
        ("acc-001", ["XAGUSD"], {"skipFeishu": True}),
        ("acc-002", ["XAUEUR"], {"skipFeishu": True}),
    ]
    assert result["analyzed"] == 3
    assert result["posted"] == 1
    assert result["skipped"] == 2
    assert result["total"] == 3
