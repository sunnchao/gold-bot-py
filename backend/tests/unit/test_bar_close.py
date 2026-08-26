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

    assert await service.dispatch("acc-1", "XAUUSD", "M15", "2026-08-25T08:00:00Z") is True
    await asyncio.wait_for(started.wait(), timeout=1)
    assert release.is_set() is False

    release.set()
    await asyncio.sleep(0)


async def test_dispatch_triggers_llm_on_m15_close() -> None:
    calls: list[tuple[str, str, str, str]] = []

    async def llm_trigger(account: str, symbol: str, timeframe: str, bar_time: str) -> None:
        calls.append((account, symbol, timeframe, bar_time))

    service = BarCloseEventService(create_in_memory_store(), llm_trigger=llm_trigger)

    assert await service.dispatch("acc-1", "XAUUSD", "M15", "2026-08-25T08:15:00Z") is True
    await asyncio.sleep(0)

    assert calls == [("acc-1", "XAUUSD", "M15", "2026-08-25T08:15:00Z")]


async def test_dispatch_triggers_each_m15_close_only_once() -> None:
    calls: list[str] = []

    async def llm_trigger(_account: str, _symbol: str, _timeframe: str, bar_time: str) -> None:
        calls.append(bar_time)

    service = BarCloseEventService(create_in_memory_store(), llm_trigger=llm_trigger)

    assert await service.dispatch("acc-1", "XAUUSD", "M15", "2026-08-25T08:15:00Z") is True
    assert await service.dispatch("acc-1", "XAUUSD", "M15", "2026-08-25T08:15:00Z") is False
    await asyncio.sleep(0)

    assert calls == ["2026-08-25T08:15:00Z"]


async def test_dispatch_does_not_trigger_llm_on_m30_close() -> None:
    calls: list[str] = []

    async def llm_trigger(_account: str, _symbol: str, timeframe: str, _bar_time: str) -> None:
        calls.append(timeframe)

    service = BarCloseEventService(create_in_memory_store(), llm_trigger=llm_trigger)

    assert await service.dispatch("acc-1", "XAUUSD", "M30", "2026-08-25T08:30:00Z") is False
    await asyncio.sleep(0)

    assert calls == []
