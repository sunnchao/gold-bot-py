"""Analysis job processor (mirror of apps/app-agent/src/scheduler/analysis.processor.ts).

Runs one workflow invocation per (account, symbol) pair concurrently, saves the
final signal with its duration, and reports succeeded/failed/saveFailed totals.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Protocol, TypedDict


class AnalysisJobResult(TypedDict):
    succeeded: int
    failed: int
    saveFailed: int
    total: int
    totalDuration: int


def _get(obj: Any, name: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _monotonic_ms() -> int:
    return int(time.monotonic() * 1000)


class AnalysisProcessorConfigLike(Protocol):
    accounts: list[Any]


class WorkflowLike(Protocol):
    async def run(
        self,
        account_id: str,
        symbols: list[str],
        initial_state: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...


class AnalysisStoreLike(Protocol):
    def save_result(
        self, account_id: str, symbol: str, result: Any, duration: int
    ) -> None: ...


class QueueLike(Protocol):
    async def clean(self, grace_period: int, limit: int, state: str) -> Any: ...


class AnalysisProcessor:
    def __init__(
        self,
        config: AnalysisProcessorConfigLike,
        workflow: WorkflowLike,
        store: AnalysisStoreLike,
        analysis_queue: QueueLike,
    ) -> None:
        self.config = config
        self.workflow = workflow
        self.store = store
        self.analysis_queue = analysis_queue

    async def on_module_init(self) -> None:
        await self.analysis_queue.clean(0, 100, "completed")
        await self.analysis_queue.clean(0, 50, "failed")

    async def process(self, job: Any) -> AnalysisJobResult:
        job_start = _monotonic_ms()
        tasks: list[tuple[str, str]] = []
        for account in self.config.accounts:
            account_id = _get(account, "id", "")
            for symbol in list(_get(account, "symbols") or []):
                tasks.append((account_id, symbol))

        outcomes = await asyncio.gather(
            *(self._run_one(account_id, symbol) for account_id, symbol in tasks)
        )

        total_duration = _monotonic_ms() - job_start
        return {
            "succeeded": sum(1 for outcome in outcomes if outcome["ok"]),
            "failed": len(outcomes) - sum(1 for outcome in outcomes if outcome["ok"]),
            "saveFailed": sum(1 for outcome in outcomes if outcome["save_failed"]),
            "total": len(tasks),
            "totalDuration": total_duration,
        }

    async def _run_one(self, account_id: str, symbol: str) -> dict[str, bool]:
        item_start = _monotonic_ms()
        save_failed = False
        try:
            result = await self.workflow.run(account_id, [symbol])
            durations = result.get("durations") or {}
            if symbol in durations:
                duration: int | None = durations[symbol]
            else:
                duration = result.get("duration")
            if duration is None:
                duration = _monotonic_ms() - item_start

            final_signals = result.get("finalSignals") or {}
            if symbol in final_signals:
                final_signal: Any = final_signals[symbol]
            else:
                final_signal = result.get("finalSignal")
            if final_signal is not None:
                try:
                    self.store.save_result(account_id, symbol, final_signal, duration)
                except Exception:  # noqa: BLE001
                    save_failed = True
            return {"ok": True, "save_failed": save_failed}
        except Exception:  # noqa: BLE001
            return {"ok": False, "save_failed": False}
