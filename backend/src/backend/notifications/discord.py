"""Discord webhook 通知(镜像 packages/notifications/src/discord.ts)。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

__all__ = [
    "DEFAULT_DISCORD_COOLDOWN_MS",
    "DiscordNotifier",
    "DiscordPayload",
]

DiscordPayload = dict[str, Any]

DEFAULT_DISCORD_COOLDOWN_MS = 15 * 60 * 1_000


@dataclass
class DiscordNotifier:
    """镜像 DiscordNotifier:冷却期内抑制,发送为 fire-and-forget。"""

    webhook_url: str
    cooldown_ms: int = DEFAULT_DISCORD_COOLDOWN_MS
    fetch_impl: Callable[..., Awaitable[Any]] | None = None
    now: Callable[[], datetime] | None = None
    log: Callable[[str], None] | None = None
    _last_sent_ms: int = field(default=0, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.fetch_impl is None:
            import urllib.request

            def default_fetch(url: str, init: dict[str, Any]) -> Awaitable[Any]:
                async def _fetch() -> Any:
                    request = urllib.request.Request(
                        url,
                        data=init.get("body", "").encode("utf-8"),
                        headers=init.get("headers", {}),
                        method=init.get("method", "POST"),
                    )
                    with urllib.request.urlopen(request) as response:  # noqa: S310
                        return type("Response", (), {"status": response.status})()

                return _fetch()

            self.fetch_impl = default_fetch

    def is_configured(self) -> bool:
        return self.webhook_url.strip() != ""

    async def send(self, payload: DiscordPayload) -> bool:
        """等价 TS send:未配置 / 冷却内返回 False;否则记录发送时刻并后台 fire。"""
        if not self.is_configured():
            return False
        now_ms = int((self.now() if self.now is not None else datetime.now(UTC)).timestamp() * 1000)
        if self._last_sent_ms > 0 and now_ms - self._last_sent_ms < self.cooldown_ms:
            return False
        self._last_sent_ms = now_ms

        asyncio.get_running_loop().create_task(self._fire(payload))
        return True

    async def _fire(self, payload: DiscordPayload) -> None:
        log = self.log or (lambda _message: None)
        try:
            import json

            body = json.dumps(payload)
        except (TypeError, ValueError) as error:
            log(f"[DISCORD] marshal payload failed: {error}")
            return
        try:
            response = await self.fetch_impl(  # type: ignore[misc]
                self.webhook_url,
                {"method": "POST", "headers": {"Content-Type": "application/json"}, "body": body},
            )
        except Exception as error:  # noqa: BLE001
            log(f"[DISCORD] send notification failed: {error}")
            return
        if int(getattr(response, "status", 0)) not in (200, 204):
            log(f"[DISCORD] webhook status: {getattr(response, 'status', 'unknown')}")
