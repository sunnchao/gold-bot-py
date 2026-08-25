"""镜像 packages/breakout-cache/src/cache.ts。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol
from urllib.parse import quote, urlparse

__all__ = [
    "BREAKOUT_CACHE_TTL_SECONDS",
    "InMemoryBreakoutCache",
    "RedisBreakoutCache",
    "breakout_key",
]

TTL_SECONDS = 60 * 60
BREAKOUT_CACHE_TTL_SECONDS = TTL_SECONDS


class RedisLike(Protocol):
    async def ping(self) -> Any: ...
    async def get(self, key: str) -> Any: ...
    async def set(self, key: str, value: str, *rest: Any) -> Any: ...
    async def quit(self) -> Any: ...


def breakout_key(symbol: str, side: str) -> str:
    return f"breakout_confirm:{_normalize_part(symbol)}:{_normalize_part(side)}"


def _normalize_part(value: str) -> str:
    return quote(value.strip().upper(), safe="")


def _iso(now: datetime) -> str:
    return now.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class RedisBreakoutCache:
    def __init__(self, client: RedisLike, now: Callable[[], datetime]) -> None:
        self.client = client
        self.now = now

    @staticmethod
    async def create(options: dict[str, Any]) -> RedisBreakoutCache | None:
        url = str(options.get("url") or "").strip()
        if not url:
            return None
        parsed = urlparse(url)
        if parsed.scheme not in {"redis", "rediss"} or not parsed.netloc:
            _log(options, "[STRATEGY] Redis breakout cache disabled: invalid REDIS_URL")
            return None

        redis_ctor = options.get("redis_ctor") or _default_redis_ctor
        client = redis_ctor({"url": url})
        now = options.get("now") or (lambda: datetime.now(UTC))
        cache = RedisBreakoutCache(client, now)
        ping_timeout_ms = int(options.get("ping_timeout_ms") or 500)
        try:
            await asyncio.wait_for(cache.client.ping(), timeout=ping_timeout_ms / 1000)
        except Exception as err:
            await _quit(cache.client)
            _log(options, f"[STRATEGY] Redis breakout cache disabled: ping failed: {err}")
            return None
        return cache

    async def set(self, symbol: str, side: str, bb_level: float) -> None:
        record = {"bb_level": bb_level, "trigger_time": _iso(self.now())}
        await self.client.set(breakout_key(symbol, side), json.dumps(record), "EX", TTL_SECONDS)

    async def get(self, symbol: str, side: str) -> dict[str, float] | None:
        data = await self.client.get(breakout_key(symbol, side))
        if data is None:
            return None
        if isinstance(data, bytes):
            data = data.decode("utf-8")
        try:
            rec = json.loads(data)
        except (TypeError, ValueError):
            return None
        return {"bbLevel": rec["bb_level"]}

    async def del_(self, symbol: str, side: str) -> None:
        key = breakout_key(symbol, side)
        delete = getattr(self.client, "delete", None)
        if callable(delete):
            await delete(key)
            return
        await self.client.del_(key)  # type: ignore[attr-defined]

    async def close(self) -> None:
        await _quit(self.client)


class InMemoryBreakoutCache:
    def __init__(
        self,
        now: Callable[[], datetime] | None = None,
        ttl_ms: int | None = None,
    ) -> None:
        self.now = now or (lambda: datetime.now(UTC))
        self.ttl_ms = TTL_SECONDS * 1000 if ttl_ms is None else ttl_ms
        self.store: dict[str, dict[str, float]] = {}

    async def set(self, symbol: str, side: str, bb_level: float) -> None:
        self.store[breakout_key(symbol, side)] = {
            "bbLevel": bb_level,
            "expiresAt": self.now().timestamp() * 1000 + self.ttl_ms,
        }

    async def get(self, symbol: str, side: str) -> dict[str, float] | None:
        entry = self.store.get(breakout_key(symbol, side))
        if entry is None:
            return None
        if self.now().timestamp() * 1000 >= entry["expiresAt"]:
            self.store.pop(breakout_key(symbol, side), None)
            return None
        return {"bbLevel": entry["bbLevel"]}

    async def del_(self, symbol: str, side: str) -> None:
        self.store.pop(breakout_key(symbol, side), None)


def _log(options: dict[str, Any], message: str) -> None:
    log = options.get("log")
    if callable(log):
        log(message)


async def _quit(client: Any) -> None:
    quit_fn = getattr(client, "quit", None)
    if callable(quit_fn):
        try:
            await quit_fn()
        except Exception:
            return


def _default_redis_ctor(options: dict[str, str]) -> Any:
    from redis.asyncio import Redis

    return Redis.from_url(options["url"])
