"""镜像 packages/notifications/src/feishu.spec.ts 的语义。"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac as hmac_module
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from backend.notifications.feishu import (
    DEFAULT_FEISHU_COOLDOWN_MS,
    FeishuNotifier,
    sign_feishu_payload,
)


@dataclass
class FakeResponse:
    status: int


class FakeFetch:
    def __init__(self, status: int = 200) -> None:
        self.status = status
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def __call__(self, url: str, init: dict[str, Any]) -> FakeResponse:
        self.calls.append((url, init))
        return FakeResponse(status=self.status)


def feishu_sign(timestamp: int, secret: str) -> str:
    string_to_sign = f"{timestamp}\n{secret}".encode()
    return base64.b64encode(hmac_module.new(string_to_sign, digestmod=hashlib.sha256).digest()).decode("utf-8")


async def drain_tasks() -> None:
    for _ in range(3):
        await asyncio.sleep(0)


class TestSignFeishuPayload:
    def test_matches_go_hmac_sha256_over_timestamp_newline_secret(self) -> None:
        timestamp = 1_735_688_600
        secret = "my-secret"
        assert sign_feishu_payload(timestamp, secret) == feishu_sign(timestamp, secret)


class TestFeishuNotifier:
    @pytest.mark.asyncio
    async def test_returns_false_when_webhook_url_is_empty(self) -> None:
        notifier = FeishuNotifier(webhook_url="")
        assert await notifier.send({"title": "t", "content": "c"}) is False

    @pytest.mark.asyncio
    async def test_sends_interactive_card_with_sign_when_secret_is_set(self) -> None:
        fetch = FakeFetch(200)
        now = datetime(2026, 1, 1, tzinfo=UTC)
        notifier = FeishuNotifier(
            webhook_url="https://feishu.test/hook", secret="shh", fetch_impl=fetch, now=lambda: now
        )

        sent = await notifier.send({"title": "Alert", "content": "price up"})
        await drain_tasks()

        assert sent is True
        assert len(fetch.calls) == 1
        url, init = fetch.calls[0]
        assert url == "https://feishu.test/hook"
        assert init["method"] == "POST"
        assert init["headers"] == {"Content-Type": "application/json"}
        body = json.loads(init["body"])
        assert body["msg_type"] == "interactive"
        timestamp = body["timestamp"]
        assert body["sign"] == feishu_sign(timestamp, "shh")
        card = body["card"]
        assert card["header"]["title"]["content"] == "Alert"
        assert card["header"]["template"] == "blue"
        assert card["elements"][0]["tag"] == "markdown"
        assert card["elements"][0]["content"] == "price up"

    @pytest.mark.asyncio
    async def test_omits_sign_when_secret_is_empty(self) -> None:
        fetch = FakeFetch(200)
        now = datetime(2026, 1, 1, tzinfo=UTC)
        notifier = FeishuNotifier(webhook_url="https://feishu.test/hook", fetch_impl=fetch, now=lambda: now)

        await notifier.send({"title": "t", "content": "c"})
        await drain_tasks()

        body = json.loads(fetch.calls[0][1]["body"])
        assert "sign" not in body
        assert body["timestamp"] == int(now.timestamp())

    @pytest.mark.asyncio
    async def test_suppressed_within_cooldown_window(self) -> None:
        fetch = FakeFetch(200)
        clock = datetime(2026, 1, 1, tzinfo=UTC)
        notifier = FeishuNotifier(
            webhook_url="https://feishu.test/hook",
            cooldown_ms=DEFAULT_FEISHU_COOLDOWN_MS,
            fetch_impl=fetch,
            now=lambda: clock,
        )

        assert await notifier.send({"title": "t", "content": "a"}) is True
        clock += timedelta(minutes=5)
        assert await notifier.send({"title": "t", "content": "b"}) is False
        await drain_tasks()
        assert len(fetch.calls) == 1

    @pytest.mark.asyncio
    async def test_logs_on_non_200_status(self) -> None:
        logs: list[str] = []
        fetch = FakeFetch(400)
        now = datetime(2026, 1, 1, tzinfo=UTC)
        notifier = FeishuNotifier(
            webhook_url="https://feishu.test/hook",
            fetch_impl=fetch,
            now=lambda: now,
            log=logs.append,
        )

        sent = await notifier.send({"title": "t", "content": "c"})
        await drain_tasks()

        assert sent is True
        assert any("webhook status: 400" in message for message in logs)
