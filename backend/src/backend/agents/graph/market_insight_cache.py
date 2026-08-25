"""Market-first insight cache (mirror of apps/app-agent/src/graph/market-insight-cache.service.ts).

Bounded in-memory TTL cache with single-flight ``get_or_build`` semantics.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable


class MarketInsightCacheValue[T]:
    """A cached insight plus its benchmark metadata."""

    __slots__ = ("insight", "benchmark_price", "computed_at", "source_account")

    def __init__(
        self,
        insight: T,
        benchmark_price: float,
        computed_at: int,
        source_account: str,
    ) -> None:
        self.insight = insight
        self.benchmark_price = benchmark_price
        self.computed_at = computed_at
        self.source_account = source_account


class _CacheEntry[T](MarketInsightCacheValue[T]):
    __slots__ = ("expires_at",)
    expires_at: int

    def __init__(self, value: MarketInsightCacheValue[T], expires_at: int) -> None:
        super().__init__(
            insight=value.insight,
            benchmark_price=value.benchmark_price,
            computed_at=value.computed_at,
            source_account=value.source_account,
        )
        self.expires_at = expires_at


class MarketInsightCache[T]:
    """TTL cache with a 1:1 get / get_or_build / clear API to the TS service."""

    def __init__(self, ttl_ms: int, now_ms: Callable[[], int] | None = None) -> None:
        self.ttl_ms = ttl_ms
        self._now_ms = now_ms or (lambda: int(time.time() * 1000))
        self._cache: dict[str, _CacheEntry[T]] = {}
        self._in_flight: dict[str, asyncio.Future[MarketInsightCacheValue[T]]] = {}

    @staticmethod
    def _key(canonical_symbol: str) -> str:
        return f"market:insight:{canonical_symbol.strip().upper()}"

    def get(self, canonical_symbol: str) -> MarketInsightCacheValue[T] | None:
        key = self._key(canonical_symbol)
        entry = self._cache.get(key)
        if entry is None:
            return None
        if entry.expires_at <= self._now_ms():
            self._cache.pop(key, None)
            return None
        return entry

    async def get_or_build(
        self,
        canonical_symbol: str,
        build_fn: Callable[[], Awaitable[MarketInsightCacheValue[T]]],
    ) -> MarketInsightCacheValue[T]:
        cached = self.get(canonical_symbol)
        if cached is not None:
            return cached

        key = self._key(canonical_symbol)
        running = self._in_flight.get(key)
        if running is not None:
            return await running

        loop = asyncio.get_running_loop()
        future: asyncio.Future[MarketInsightCacheValue[T]] = loop.create_future()

        async def _build() -> None:
            try:
                value = await build_fn()
                self._cache[key] = _CacheEntry(value, self._now_ms() + self.ttl_ms)
                if not future.done():
                    future.set_result(value)
            except Exception as exc:  # noqa: BLE001
                if not future.done():
                    future.set_exception(exc)
            finally:
                self._in_flight.pop(key, None)

        self._in_flight[key] = future
        loop.create_task(_build())
        return await future

    def clear(self, canonical_symbol: str | None = None) -> None:
        if canonical_symbol is not None:
            self._cache.pop(self._key(canonical_symbol), None)
            return
        self._cache.clear()
