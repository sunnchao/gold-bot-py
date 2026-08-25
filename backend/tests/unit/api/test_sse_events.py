"""SSE 事件流契约(镜像 app.ts streamEvents:/api/v1/events/stream admin 鉴权)。"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from backend.api.app import _sse_event_stream, create_api_app
from backend.observability.sse import create_sse_hub
from backend.persistence.store import create_in_memory_store

pytestmark = pytest.mark.contract

ROUTE_TOKEN = "route-token"
ADMIN_TOKEN = "admin-token"


def make_app(**options) -> TestClient:
    store = options.pop("store", None) or create_in_memory_store()
    defaults = {
        "store": store,
        "valid_tokens": {ROUTE_TOKEN, ADMIN_TOKEN},
        "admin_tokens": {ADMIN_TOKEN},
        "events": create_sse_hub(),
    }
    return TestClient(create_api_app({**defaults, **options}))


async def test_stream_requires_admin_token() -> None:
    client = make_app()
    no_token = client.get("/api/v1/events/stream")
    assert no_token.status_code == 401
    assert no_token.json() == {"status": "ERROR", "message": "invalid token"}

    non_admin = client.get("/api/v1/events/stream", headers={"X-API-Token": ROUTE_TOKEN})
    assert non_admin.status_code == 403
    assert non_admin.json() == {"status": "ERROR", "message": "admin only"}


async def test_stream_forwards_frames_and_unsubscribes_on_close() -> None:
    hub = create_sse_hub()
    queue: asyncio.Queue = asyncio.Queue()
    stream = _sse_event_stream(hub, queue)

    # 先启动读取任务(生成器订阅后等待),再发布事件 → 帧送达
    first = {"event_id": "e-1", "event_type": "tick", "timestamp": "2026-03-01T00:00:00.000Z"}
    first_task = asyncio.create_task(stream.__anext__())
    await asyncio.sleep(0)
    assert hub.subscriber_count() == 1
    hub.publish(first)
    frame = await first_task
    assert frame == f"data: {json.dumps(first)}\n\n"

    second = {"event_id": "e-2", "event_type": "heartbeat", "timestamp": "2026-03-01T00:00:01.000Z"}
    second_task = asyncio.create_task(stream.__anext__())
    await asyncio.sleep(0)
    hub.publish(second)
    frame2 = await second_task
    assert frame2 == f"data: {json.dumps(second)}\n\n"

    # 生成器关闭(等价客户端断开)→ 退订
    await stream.aclose()
    assert hub.subscriber_count() == 0
