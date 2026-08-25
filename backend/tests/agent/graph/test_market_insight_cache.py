"""Mirror of apps/app-agent/src/graph/market-insight-cache.service.test.ts."""

from __future__ import annotations

import asyncio

import pytest

from backend.agents.graph.market_insight_cache import MarketInsightCache, MarketInsightCacheValue


@pytest.mark.asyncio
async def test_reuses_a_cached_insight_until_ttl_expiry() -> None:
    clock = {"now": 1_000_000}
    cache: MarketInsightCache[dict[str, str]] = MarketInsightCache(
        ttl_ms=1000,
        now_ms=lambda: clock["now"],
    )
    calls = 0

    async def build() -> MarketInsightCacheValue[dict[str, str]]:
        nonlocal calls
        calls += 1
        return MarketInsightCacheValue(
            insight={"trend_bias": "bullish"},
            benchmark_price=100,
            computed_at=clock["now"],
            source_account="90011087",
        )

    first = await cache.get_or_build("XAUUSD", build)
    second = await cache.get_or_build("xauusd", build)
    assert calls == 1
    assert second.insight == first.insight

    clock["now"] += 1001
    await cache.get_or_build("XAUUSD", build)
    assert calls == 2


@pytest.mark.asyncio
async def test_single_flights_concurrent_builders_for_the_same_key() -> None:
    cache = MarketInsightCache(ttl_ms=600_000)
    build_trigger: asyncio.Future[None] = asyncio.get_running_loop().create_future()
    calls = 0

    async def build() -> MarketInsightCacheValue[dict[str, str]]:
        nonlocal calls
        calls += 1
        await build_trigger
        return MarketInsightCacheValue(
            insight={"trend_bias": "neutral"},
            benchmark_price=100,
            computed_at=1,
            source_account="90011087",
        )

    first_task = asyncio.create_task(cache.get_or_build("XAUUSD", build))
    second_task = asyncio.create_task(cache.get_or_build("XAUUSD", build))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert calls == 1

    build_trigger.set_result(None)
    results = await asyncio.gather(first_task, second_task)
    assert len(results) == 2
    assert results[0].insight == {"trend_bias": "neutral"}


@pytest.mark.asyncio
async def test_clear_removes_key_or_whole_cache() -> None:
    cache: MarketInsightCache[int] = MarketInsightCache(ttl_ms=600_000)
    called = 0

    async def build() -> MarketInsightCacheValue[int]:
        nonlocal called
        called += 1
        return MarketInsightCacheValue(
            insight=called,
            benchmark_price=1,
            computed_at=1,
            source_account="a",
        )

    await cache.get_or_build("XAUUSD", build)
    await cache.get_or_build("XAGUSD", build)
    assert called == 2

    cache.clear("XAUUSD")
    await cache.get_or_build("XAUUSD", build)
    assert called == 3

    cache.clear()
    await cache.get_or_build("XAGUSD", build)
    assert called == 4
