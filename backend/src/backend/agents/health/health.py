"""Agent 健康检查(镜像 apps/app-agent/src/health/health.controller.ts)。

Redis ping 与 goldbot /health 探测全部可注入,测试离线运行。
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol


class RedisPing(Protocol):
    async def ping(self) -> str: ...


class HealthError(Exception):
    """镜像 NestJS ServiceUnavailableException:body 附在异常上。"""

    def __init__(self, body: dict[str, Any]) -> None:
        super().__init__("service unavailable")
        self.body = body


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


async def check_health(
    redis_ping: Callable[[], Any],
    goldbot_probe: Callable[[], Any],
    start_time: int,
    now_millis: Callable[[], int] | None = None,
) -> dict[str, Any]:
    """镜像 HealthController.getHealth():redis_ping 返回 'PONG',goldbot_probe 返回可达 bool。"""
    redis_result, goldbot_result = await asyncio.gather(
        _check_redis(redis_ping),
        _check_goldbot(goldbot_probe),
    )
    now = now_millis() if now_millis is not None else _now_millis()
    body = {
        "status": "ok" if redis_result else "degraded",
        "uptime": math.floor((now - start_time) / 1000),
        "redis": redis_result,
        "goldbot": goldbot_result,
        "timestamp": _now_iso(),
    }
    if not redis_result:
        raise HealthError(body)
    return body


def _now_millis() -> int:
    return int(datetime.now(UTC).timestamp() * 1000)


async def _check_redis(redis_ping: Callable[[], Any]) -> bool:
    try:
        return (await redis_ping()) == "PONG"
    except Exception:
        return False


async def _check_goldbot(goldbot_probe: Callable[[], Any]) -> bool:
    try:
        return bool(await goldbot_probe())
    except Exception:
        return False
