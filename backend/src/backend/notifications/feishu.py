"""飞书 webhook 通知(镜像 packages/notifications/src/feishu.ts)。"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

__all__ = [
    "DEFAULT_FEISHU_COOLDOWN_MS",
    "FeishuNotifier",
    "FeishuCardOptions",
    "sign_feishu_payload",
]

DEFAULT_FEISHU_COOLDOWN_MS = 10 * 60 * 1_000


def sign_feishu_payload(timestamp: int, secret: str) -> str:
    """镜像 signFeishuPayload:HMAC-SHA256("timestamp\\nsecret") 的 base64。"""
    string_to_sign = f"{timestamp}\n{secret}".encode()
    return base64.b64encode(hmac.new(string_to_sign, digestmod=hashlib.sha256).digest()).decode("utf-8")


class FeishuCardOptions(dict):
    """交互卡片参数:title / content / template(默认 'blue')。"""


@dataclass
class FeishuNotifier:
    """镜像 FeishuNotifier:交互卡片 + secret 签名 + 冷却抑制 + fire-and-forget。"""

    webhook_url: str
    secret: str = ""
    cooldown_ms: int = DEFAULT_FEISHU_COOLDOWN_MS
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

    async def send(self, card: FeishuCardOptions) -> bool:
        if not self.is_configured():
            return False
        now_dt = self.now() if self.now is not None else datetime.now(UTC)
        now_ms = int(now_dt.timestamp() * 1000)
        if self._last_sent_ms > 0 and now_ms - self._last_sent_ms < self.cooldown_ms:
            return False
        self._last_sent_ms = now_ms

        timestamp = now_ms // 1000
        payload: dict[str, Any] = {
            "timestamp": timestamp,
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {"tag": "plain_text", "content": card.get("title", "")},
                    "template": card.get("template", "blue"),
                },
                "elements": [{"tag": "markdown", "content": card.get("content", "")}],
            },
        }
        if self.secret != "":
            payload["sign"] = sign_feishu_payload(timestamp, self.secret)

        asyncio.get_running_loop().create_task(self._fire(payload))
        return True

    async def _fire(self, payload: dict[str, Any]) -> None:
        log = self.log or (lambda _message: None)
        try:
            body = json.dumps(payload)
        except (TypeError, ValueError) as error:
            log(f"[FEISHU] marshal payload failed: {error}")
            return
        try:
            response = await self.fetch_impl(  # type: ignore[misc]
                self.webhook_url,
                {"method": "POST", "headers": {"Content-Type": "application/json"}, "body": body},
            )
        except Exception as error:  # noqa: BLE001
            log(f"[FEISHU] send notification failed: {error}")
            return
        if int(getattr(response, "status", 0)) != 200:
            log(f"[FEISHU] webhook status: {getattr(response, 'status', 'unknown')}")
