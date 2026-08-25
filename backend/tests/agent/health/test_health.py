"""Agent 健康检查契约(镜像 apps/app-agent/src/health/health.controller.test.ts)。"""

from __future__ import annotations

import pytest

from backend.agents.health.health import HealthError, check_health


async def test_returns_ok_when_redis_and_goldbot_reachable() -> None:
    async def ping() -> str:
        return "PONG"

    async def probe() -> bool:
        return True

    result = await check_health(ping, probe, start_time=1000, now_millis=lambda: 4000)

    assert result["status"] == "ok"
    assert result["redis"] is True
    assert result["goldbot"] is True
    assert result["uptime"] == 3


async def test_throws_503_degraded_when_redis_unavailable() -> None:
    async def ping() -> str:
        raise RuntimeError("down")

    async def probe() -> bool:
        return True

    with pytest.raises(HealthError) as exc_info:
        await check_health(ping, probe, start_time=1000)
    body = exc_info.value.body
    assert body["status"] == "degraded"
    assert body["redis"] is False
    assert body["goldbot"] is True


async def test_redis_ping_not_pong_is_degraded() -> None:
    async def ping() -> str:
        return "NOPE"

    async def probe() -> bool:
        return True

    with pytest.raises(HealthError) as exc_info:
        await check_health(ping, probe, start_time=0)
    assert exc_info.value.body["redis"] is False


async def test_goldbot_probe_failure_reports_false_but_keeps_ok_status() -> None:
    async def ping() -> str:
        return "PONG"

    async def probe() -> bool:
        raise RuntimeError("connect refused")

    result = await check_health(ping, probe, start_time=1000, now_millis=lambda: 2000)

    assert result["status"] == "ok"
    assert result["redis"] is True
    assert result["goldbot"] is False
