"""Scheduled-analysis registration (mirror of apps/app-agent/src/scheduler/scheduler.service.ts).

The BullMQ repeatable jobs of the TS service are mirrored as queue operations on
an injected queue-like collaborator, so tests run fully offline.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any, Protocol, TypedDict

SYMBOL_REFRESH_INTERVAL_MS = 60 * 60 * 1000
ANALYSIS_QUEUE = "gold-analysis"
POSITION_POLL_QUEUE = "position-poll"


class SchedulerStatus(TypedDict):
    running: bool
    lastRunTime: str | None


def _now_iso() -> str:
    return (
        datetime.now(UTC)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _get(obj: Any, name: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


class SchedulerConfigLike(Protocol):
    schedule_cron: str
    analysis_trigger_mode: str
    static_accounts: list[Any]

    def update_account_symbols(self, account_id: str, symbols: list[str]) -> None: ...


class GoldbotApiLike(Protocol):
    async def fetch_accounts(self) -> list[dict[str, Any]]: ...
    async def fetch_account_symbols(self, account_id: str) -> dict[str, list[str]]: ...


class QueueLike(Protocol):
    async def get_repeatable_jobs(self) -> list[dict[str, Any]]: ...
    async def remove_repeatable_by_key(self, key: str) -> None: ...
    async def add(self, name: str, data: dict[str, Any], opts: dict[str, Any]) -> None: ...


class SchedulerService:
    """Registers the cron analysis job + 15-minute position-poll job on init."""

    def __init__(
        self,
        analysis_queue: QueueLike,
        position_poll_queue: QueueLike,
        config: SchedulerConfigLike,
        goldbot_api: GoldbotApiLike,
    ) -> None:
        self.analysis_queue = analysis_queue
        self.position_poll_queue = position_poll_queue
        self.config = config
        self.goldbot_api = goldbot_api
        self.running = False
        self.last_run_time: str | None = None
        self._symbol_refresh_task: asyncio.Task[None] | None = None

    async def on_module_init(self) -> None:
        await self._refresh_account_symbols()
        self._start_symbol_refresh_timer()

        analysis_repeatable = await self.analysis_queue.get_repeatable_jobs()
        for job in analysis_repeatable:
            await self.analysis_queue.remove_repeatable_by_key(job["key"])

        if self.config.analysis_trigger_mode == "cron":
            await self.analysis_queue.add(
                "scheduled-analysis",
                {},
                {
                    "repeat": {"pattern": self.config.schedule_cron},
                    "attempts": 3,
                    "backoff": {"type": "exponential", "delay": 5000},
                    "removeOnComplete": {"age": 86400},
                    "removeOnFail": {"age": 604800},
                },
            )

        position_poll_repeatable = await self.position_poll_queue.get_repeatable_jobs()
        for job in position_poll_repeatable:
            await self.position_poll_queue.remove_repeatable_by_key(job["key"])

        await self.position_poll_queue.add(
            "position-poll",
            {},
            {
                "repeat": {"every": 15 * 60 * 1000},
                "attempts": 3,
                "backoff": {"type": "exponential", "delay": 5000},
                "removeOnComplete": {"age": 86400},
                "removeOnFail": {"age": 604800},
            },
        )

        self.running = True
        self.last_run_time = _now_iso()

    async def on_module_destroy(self) -> None:
        if self._symbol_refresh_task is not None:
            self._symbol_refresh_task.cancel()
            await asyncio.gather(self._symbol_refresh_task, return_exceptions=True)
            self._symbol_refresh_task = None

    def _start_symbol_refresh_timer(self) -> None:
        if self._symbol_refresh_task is not None:
            self._symbol_refresh_task.cancel()

        async def _refresh_loop() -> None:
            while True:
                await asyncio.sleep(SYMBOL_REFRESH_INTERVAL_MS / 1000)
                await self._refresh_account_symbols()

        self._symbol_refresh_task = asyncio.get_running_loop().create_task(_refresh_loop())

    async def _discovered_accounts(self) -> list[Any]:
        accounts_by_id: dict[str, Any] = {}
        for account in (
            *self.config.static_accounts,
            *list(getattr(self.config, "accounts", []) or []),
        ):
            account_id = _get(account, "id", "")
            if account_id:
                accounts_by_id[account_id] = account
        try:
            remote_accounts = await self.goldbot_api.fetch_accounts()
        except Exception:  # noqa: BLE001
            remote_accounts = []
        for item in remote_accounts:
            account_id = _get(item, "account_id") or _get(item, "id") or ""
            if account_id and account_id not in accounts_by_id:
                accounts_by_id[account_id] = {"id": account_id, "symbols": []}
        return list(accounts_by_id.values())

    async def _refresh_account_symbols(self) -> None:
        async def _refresh_one(account: Any) -> None:
            account_id = _get(account, "id", "")
            fallback_symbols = list(_get(account, "symbols") or [])
            try:
                result = await self.goldbot_api.fetch_account_symbols(account_id)
                symbols = list(result.get("symbols") or [])
                if len(symbols) == 0:
                    if fallback_symbols:
                        self.config.update_account_symbols(account_id, fallback_symbols)
                    return
                self.config.update_account_symbols(account_id, symbols)
            except Exception:  # noqa: BLE001
                if fallback_symbols:
                    self.config.update_account_symbols(account_id, fallback_symbols)

        await asyncio.gather(*(_refresh_one(account) for account in await self._discovered_accounts()))

    def get_status(self) -> SchedulerStatus:
        return {
            "running": self.running,
            "lastRunTime": self.last_run_time,
        }
