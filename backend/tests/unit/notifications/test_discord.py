"""镜像 packages/notifications/src/discord.spec.ts 的语义。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from backend.notifications.discord import DEFAULT_DISCORD_COOLDOWN_MS, DiscordNotifier


@dataclass
class FakeResponse:
    status: int


class FakeFetch:
    """可注入的 fetch 替身,记录调用。"""

    def __init__(self, status: int = 204) -> None:
        self.status = status
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def __call__(self, url: str, init: dict[str, Any]) -> FakeResponse:
        self.calls.append((url, init))
        return FakeResponse(status=self.status)


async def drain_tasks() -> None:
    """让 fire-and-forget 任务跑完(等价 TS 的两次 Promise.resolve())。"""
    for _ in range(3):
        await asyncio.sleep(0)


class TestDiscordNotifier:
    @pytest.mark.asyncio
    async def test_returns_false_when_webhook_url_is_empty(self) -> None:
        notifier = DiscordNotifier(webhook_url="")
        assert await notifier.send({"content": "x"}) is False

    @pytest.mark.asyncio
    async def test_sends_payload_as_json_post(self) -> None:
        fetch = FakeFetch(204)
        now = datetime(2026, 1, 1, tzinfo=UTC)
        notifier = DiscordNotifier(webhook_url="https://discord.test/hook", fetch_impl=fetch, now=lambda: now)

        sent = await notifier.send({"content": "hello"})
        await drain_tasks()

        assert sent is True
        assert len(fetch.calls) == 1
        url, init = fetch.calls[0]
        assert url == "https://discord.test/hook"
        assert init["method"] == "POST"
        assert init["headers"] == {"Content-Type": "application/json"}
        assert init["body"] == '{"content": "hello"}'

    @pytest.mark.asyncio
    async def test_suppressed_within_cooldown_window(self) -> None:
        fetch = FakeFetch(204)
        clock = datetime(2026, 1, 1, tzinfo=UTC)
        notifier = DiscordNotifier(
            webhook_url="https://discord.test/hook",
            cooldown_ms=DEFAULT_DISCORD_COOLDOWN_MS,
            fetch_impl=fetch,
            now=lambda: clock,
        )

        assert await notifier.send({"content": "a"}) is True
        clock += timedelta(minutes=5)
        assert await notifier.send({"content": "b"}) is False
        await drain_tasks()
        assert len(fetch.calls) == 1

    @pytest.mark.asyncio
    async def test_sends_again_after_cooldown_expires(self) -> None:
        fetch = FakeFetch(204)
        clock = datetime(2026, 1, 1, tzinfo=UTC)
        notifier = DiscordNotifier(
            webhook_url="https://discord.test/hook",
            cooldown_ms=DEFAULT_DISCORD_COOLDOWN_MS,
            fetch_impl=fetch,
            now=lambda: clock,
        )

        assert await notifier.send({"content": "a"}) is True
        clock += timedelta(minutes=15, seconds=1)
        assert await notifier.send({"content": "b"}) is True
        await drain_tasks()
        assert len(fetch.calls) == 2

    @pytest.mark.asyncio
    async def test_logs_on_non_2xx_status_but_does_not_reject_the_caller(self) -> None:
        logs: list[str] = []
        fetch = FakeFetch(500)
        now = datetime(2026, 1, 1, tzinfo=UTC)
        notifier = DiscordNotifier(
            webhook_url="https://discord.test/hook",
            fetch_impl=fetch,
            now=lambda: now,
            log=logs.append,
        )

        sent = await notifier.send({"content": "x"})
        await drain_tasks()

        assert sent is True
        assert any("webhook status: 500" in message for message in logs)

    @pytest.mark.asyncio
    async def test_logs_on_fetch_failure_without_rejecting_the_caller(self) -> None:
        logs: list[str] = []

        async def failing_fetch(url: str, init: dict[str, Any]) -> FakeResponse:
            raise ConnectionError("boom")

        notifier = DiscordNotifier(
            webhook_url="https://discord.test/hook",
            fetch_impl=failing_fetch,
            now=lambda: datetime(2026, 1, 1, tzinfo=UTC),
            log=logs.append,
        )

        assert await notifier.send({"content": "x"}) is True
        await drain_tasks()
        assert any("send notification failed" in message and "boom" in message for message in logs)
