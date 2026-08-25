from __future__ import annotations

import asyncio

from backend.persistence.store import create_in_memory_store
from backend.services.bar_close import BarCloseEventService


async def test_dispatch_does_not_wait_for_async_llm_analysis() -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def llm_trigger(_account: str, _symbol: str, _timeframe: str, _bar_time: str) -> None:
        started.set()
        await release.wait()

    service = BarCloseEventService(create_in_memory_store(), llm_trigger=llm_trigger)

    assert await service.dispatch("acc-1", "XAUUSD", "M30", "2026-08-25T08:00:00Z") is True
    await asyncio.wait_for(started.wait(), timeout=1)
    assert release.is_set() is False

    release.set()
    await asyncio.sleep(0)
