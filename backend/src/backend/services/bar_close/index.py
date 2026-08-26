"""Route completed-bar uploads to the matching analysis pipeline."""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable

from backend.persistence.store import EaStore

AnalysisTrigger = Callable[[str, str, str, str], Awaitable[None] | None]


class BarCloseEventService:
    def __init__(
        self,
        store: EaStore,
        llm_trigger: AnalysisTrigger | None = None,
        technical_trigger: AnalysisTrigger | None = None,
    ) -> None:
        self._store = store
        self._llm_trigger = llm_trigger
        self._technical_trigger = technical_trigger
        self._tasks: set[asyncio.Future[None]] = set()
        self._logger = logging.getLogger("goldbot.bar_close")

    async def dispatch(self, account_id: str, symbol: str, timeframe: str, bar_time: str) -> bool:
        normalized_timeframe = timeframe.strip().upper()
        trigger = self._trigger_for(normalized_timeframe)
        normalized_bar_time = bar_time.strip()
        if trigger is None or not normalized_bar_time:
            return False

        claimed = await self._store.claim_bar_close_event(
            account_id,
            symbol.strip().upper(),
            normalized_timeframe,
            normalized_bar_time,
        )
        if not claimed:
            return False

        try:
            pending = trigger(account_id, symbol.strip().upper(), normalized_timeframe, normalized_bar_time)
        except Exception:
            self._logger.exception(
                "bar-close trigger failed account=%s symbol=%s timeframe=%s bar_time=%s",
                account_id,
                symbol,
                normalized_timeframe,
                normalized_bar_time,
            )
            return True

        if inspect.isawaitable(pending):
            task = asyncio.ensure_future(pending)
            self._tasks.add(task)
            task.add_done_callback(self._on_task_done)
        return True

    def _trigger_for(self, timeframe: str) -> AnalysisTrigger | None:
        if timeframe == "M15":
            return self._llm_trigger
        return None

    def _on_task_done(self, task: asyncio.Future[None]) -> None:
        self._tasks.discard(task)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            self._logger.error("bar-close analysis task failed", exc_info=error)
