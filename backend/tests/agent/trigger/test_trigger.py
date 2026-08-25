"""分析触发契约(镜像 apps/app-agent/src/trigger/trigger.controller.test.ts)。"""

from __future__ import annotations

import pytest

from backend.agents.trigger.trigger import TriggerError, _recent_triggers, trigger_analysis


class FakeBarSource:
    def __init__(self, symbols=None):
        self.symbols = symbols if symbols is not None else ["XAUUSD", "GBPJPY", "XAGUSD"]

    async def account_symbols(self, account: str):
        return self.symbols


class FakeWorkflow:
    def __init__(self):
        self.runs: list[tuple] = []

    async def run(self, account: str, symbols: list[str], options: dict):
        self.runs.append((account, symbols, options))


async def test_triggers_workflow_when_no_auth_configured() -> None:
    workflow = FakeWorkflow()
    result = await trigger_analysis(
        workflow, FakeBarSource(), "acc-001", "XAUUSD", "", None, now_iso_fn=lambda: "2026-06-06T09:00:00.000Z"
    )
    assert workflow.runs == [("acc-001", ["XAUUSD"], {"forceAnalyze": False})]
    assert result["triggered"] is True


async def test_rejects_requests_with_invalid_symbol() -> None:
    workflow = FakeWorkflow()
    with pytest.raises(TriggerError, match="not allowed") as exc:
        await trigger_analysis(workflow, FakeBarSource(), "acc-001", "INVALID!", "")
    assert exc.value.status == 400
    assert workflow.runs == []


async def test_rejects_requests_with_wrong_api_token() -> None:
    workflow = FakeWorkflow()
    with pytest.raises(TriggerError, match="Invalid") as exc:
        await trigger_analysis(workflow, FakeBarSource(), "acc-001", "XAUUSD", "wrong-token", "my-secret")
    assert exc.value.status == 403
    assert workflow.runs == []


async def test_accepts_requests_with_correct_api_token() -> None:
    workflow = FakeWorkflow()
    result = await trigger_analysis(
        workflow, FakeBarSource(), "acc-002", "GBPJPY", "my-secret", "my-secret",
        now_iso_fn=lambda: "2026-06-06T09:00:00.000Z",
    )
    assert result["triggered"] is True
    assert workflow.runs == [("acc-002", ["GBPJPY"], {"forceAnalyze": False})]


async def test_respects_idempotency_window() -> None:
    _recent_triggers.clear()
    workflow = FakeWorkflow()
    bar_source = FakeBarSource()
    now = [1000]

    r1 = await trigger_analysis(workflow, bar_source, "acc-003", "XAGUSD", "", now_millis=now[0],
                                now_iso_fn=lambda: "2026-06-06T09:00:00.000Z")
    assert r1["triggered"] is True
    assert len(workflow.runs) == 1

    r2 = await trigger_analysis(workflow, bar_source, "acc-003", "XAGUSD", "", now_millis=now[0] + 30_000,
                                now_iso_fn=lambda: "2026-06-06T09:00:00.000Z")
    assert r2["triggered"] is False
    assert r2["reason"] == "recently_triggered"
    assert len(workflow.runs) == 1
    _recent_triggers.clear()


async def test_rejects_allowed_symbol_not_loaded_by_account() -> None:
    workflow = FakeWorkflow()
    bar_source = FakeBarSource(["GOLDm#"])
    with pytest.raises(TriggerError) as exc:
        await trigger_analysis(workflow, bar_source, "81124211", "XAUUSD", "")
    assert exc.value.error == "symbol_not_loaded"
    assert exc.value.status == 400
    assert workflow.runs == []


async def test_matches_loaded_account_symbols_case_insensitively() -> None:
    workflow = FakeWorkflow()
    bar_source = FakeBarSource(["GOLDm#"])
    result = await trigger_analysis(
        workflow, bar_source, "81124211", " goldm# ", "", now_iso_fn=lambda: "2026-06-06T09:00:00.000Z"
    )
    assert workflow.runs == [("81124211", ["GOLDm#"], {"forceAnalyze": False})]
    assert result == {"triggered": True, "account": "81124211", "symbol": "GOLDm#",
                      "timestamp": "2026-06-06T09:00:00.000Z"}
