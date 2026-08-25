"""镜像 packages/breakout-cache/src/cache.spec.ts。"""

from __future__ import annotations

import json
from typing import Any

import pytest

from backend.breakout_cache.cache import (
    InMemoryBreakoutCache,
    RedisBreakoutCache,
    breakout_key,
)


class RedisStub:
    def __init__(self, overrides: dict[str, Any] | None = None) -> None:
        self.ping_impl = overrides.get("ping") if overrides else None
        self.get_impl = overrides.get("get") if overrides else None
        self.set_calls: list[tuple[Any, ...]] = []
        self.del_calls: list[tuple[Any, ...]] = []
        self.quit_calls = 0
        self.get_return: str | None = None

    async def ping(self) -> str:
        if self.ping_impl is not None:
            return await self.ping_impl()
        return "PONG"

    async def get(self, key: str) -> str | None:
        if self.get_impl is not None:
            return await self.get_impl(key)
        return self.get_return

    async def set(self, key: str, value: str, *rest: Any) -> str:
        self.set_calls.append((key, value, *rest))
        return "OK"

    async def del_(self, key: str) -> int:
        self.del_calls.append((key,))
        return 1

    # redis-py uses delete(); source stub uses del()
    async def delete(self, key: str) -> int:
        return await self.del_(key)

    async def quit(self) -> str:
        self.quit_calls += 1
        return "OK"


def redis_ctor(stub: RedisStub):
    def factory(_options: dict[str, str]) -> RedisStub:
        return stub

    return factory


def test_breakout_key_uppercases_and_escapes() -> None:
    assert breakout_key("xau usd", "buy") == "breakout_confirm:XAU%20USD:BUY"
    assert breakout_key("XAUUSD", "SELL") == "breakout_confirm:XAUUSD:SELL"


@pytest.mark.asyncio
async def test_create_returns_none_for_empty_url() -> None:
    assert await RedisBreakoutCache.create({"url": ""}) is None
    assert await RedisBreakoutCache.create({"url": "   "}) is None


@pytest.mark.asyncio
async def test_create_returns_none_for_invalid_url() -> None:
    logs: list[str] = []
    assert await RedisBreakoutCache.create({"url": "not-a-url", "log": logs.append}) is None
    assert any("invalid REDIS_URL" in message for message in logs)


@pytest.mark.asyncio
async def test_create_returns_none_when_ping_fails() -> None:
    async def fail_ping() -> str:
        raise RuntimeError("connect refused")

    stub = RedisStub({"ping": fail_ping})
    logs: list[str] = []
    cache = await RedisBreakoutCache.create(
        {"url": "redis://localhost:6379", "redis_ctor": redis_ctor(stub), "log": logs.append}
    )
    assert cache is None
    assert stub.quit_calls == 1
    assert any("ping failed" in message for message in logs)


@pytest.mark.asyncio
async def test_create_returns_none_on_ping_timeout() -> None:
    import asyncio

    async def hang() -> str:
        await asyncio.sleep(10)
        return "PONG"

    stub = RedisStub({"ping": hang})
    cache = await RedisBreakoutCache.create(
        {"url": "redis://localhost:6379", "redis_ctor": redis_ctor(stub), "ping_timeout_ms": 50}
    )
    assert cache is None
    assert stub.quit_calls == 1


@pytest.mark.asyncio
async def test_set_get_del_round_trip_with_1h_ttl() -> None:
    stub = RedisStub()
    from datetime import UTC, datetime

    now = datetime(2026, 1, 1, tzinfo=UTC)

    cache = await RedisBreakoutCache.create(
        {
            "url": "redis://localhost:6379",
            "redis_ctor": redis_ctor(stub),
            "now": lambda: now,
        }
    )
    assert cache is not None

    await cache.set("xauusd", "buy", 2050.5)
    assert len(stub.set_calls) == 1
    key, value, *rest = stub.set_calls[0]
    assert key == "breakout_confirm:XAUUSD:BUY"
    parsed = json.loads(value)
    assert parsed["bb_level"] == 2050.5
    assert parsed["trigger_time"] == "2026-01-01T00:00:00.000Z"
    assert rest == ["EX", 3600]

    stub.get_return = value
    got = await cache.get("xauusd", "buy")
    assert got == {"bbLevel": 2050.5}

    await cache.del_("xauusd", "buy")
    assert stub.del_calls == [("breakout_confirm:XAUUSD:BUY",)]


@pytest.mark.asyncio
async def test_get_returns_none_on_missing_key() -> None:
    stub = RedisStub()
    cache = await RedisBreakoutCache.create({"url": "redis://localhost:6379", "redis_ctor": redis_ctor(stub)})
    assert await cache.get("xau", "buy") is None  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_get_returns_none_on_malformed_payload() -> None:
    stub = RedisStub()
    stub.get_return = "not-json{"
    cache = await RedisBreakoutCache.create({"url": "redis://localhost:6379", "redis_ctor": redis_ctor(stub)})
    assert await cache.get("xau", "buy") is None  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_in_memory_set_get_del_round_trip() -> None:
    cache = InMemoryBreakoutCache()
    await cache.set("xauusd", "sell", 1990)
    assert await cache.get("xauusd", "sell") == {"bbLevel": 1990}
    await cache.del_("xauusd", "sell")
    assert await cache.get("xauusd", "sell") is None


@pytest.mark.asyncio
async def test_in_memory_expires_after_ttl() -> None:
    from datetime import UTC, datetime

    clock_ms = {"value": 1_767_225_600_000}  # 2026-01-01T00:00:00Z

    def now() -> datetime:
        return datetime.fromtimestamp(clock_ms["value"] / 1000, tz=UTC)

    cache = InMemoryBreakoutCache(now=now, ttl_ms=1000)
    await cache.set("xau", "buy", 100)
    clock_ms["value"] += 500
    assert await cache.get("xau", "buy") == {"bbLevel": 100}
    clock_ms["value"] += 600
    assert await cache.get("xau", "buy") is None


@pytest.mark.asyncio
async def test_in_memory_normalizes_key_like_redis() -> None:
    cache = InMemoryBreakoutCache()
    await cache.set("xauusd", "buy", 100)
    assert await cache.get("XAUUSD", "BUY") == {"bbLevel": 100}
    assert await cache.get("xau usd", "buy") is None
