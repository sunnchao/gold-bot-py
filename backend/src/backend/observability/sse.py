"""SSE(Server-Sent Events)hub(镜像 packages/observability/src/sse.ts)。"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, TypeVar

from typing_extensions import TypedDict  # noqa: TC002

__all__ = [
    "SseEvent",
    "SseHub",
    "create_sse_hub",
    "event_stream_headers",
    "format_sse_frame",
]


class SseEvent(TypedDict, total=False):
    event_id: str
    event_type: str
    account_id: str
    source: str
    timestamp: str
    payload: Any


T = TypeVar("T")


def event_stream_headers() -> dict[str, str]:
    return {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
    }


def format_sse_frame(data: Any) -> str:
    return f"data: {json.dumps(data)}\n\n"


class SseHub:
    """镜像 SseHub<T>:subscribe 返回退订函数,publish 同步分发。"""

    def __init__(self) -> None:
        self._subscribers: set[Callable[[Any], None]] = set()

    def subscribe(self, subscriber: Callable[[Any], None]) -> Callable[[], None]:
        self._subscribers.add(subscriber)
        return lambda: self._subscribers.discard(subscriber)

    def publish(self, event: Any) -> None:
        for subscriber in list(self._subscribers):
            subscriber(event)

    def subscriber_count(self) -> int:
        return len(self._subscribers)


def create_sse_hub() -> SseHub:
    return SseHub()
