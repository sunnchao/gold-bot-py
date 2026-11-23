"""SOP 五级分层告警路由器 (silent / notify / actionable / urgent / digest) 与飞书直发适配器。"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import httpx

from backend.notifications.display import clean_feishu_card

ALERT_LEVELS: tuple[str, ...] = ("silent", "notify", "actionable", "urgent", "digest")

_LEVEL_TEMPLATES: dict[str, str] = {
    "urgent": "red",
    "actionable": "green",
    "notify": "blue",
    "digest": "wathet",
}


def _default_now() -> datetime:
    return datetime.now(UTC)


async def _default_feishu_fetch(
    url: str,
    *,
    method: str = "POST",
    headers: dict[str, str] | None = None,
    body: str | None = None,
) -> Any:
    async with httpx.AsyncClient(timeout=10.0) as client:
        return await client.request(method, url, headers=headers, content=body)


def _response_status(response: Any) -> int | None:
    for attr in ("status_code", "status"):
        val = getattr(response, attr, None)
        if isinstance(val, int):
            return val
    return None


async def _response_text(response: Any) -> str:
    text_attr = getattr(response, "text", None)
    if callable(text_attr):
        return str(await text_attr())
    if isinstance(text_attr, str):
        return text_attr
    body_attr = getattr(response, "body", None)
    if isinstance(body_attr, bytes):
        return body_attr.decode("utf-8", errors="replace")
    if isinstance(body_attr, str):
        return body_attr
    return ""


def _sign_feishu(timestamp: str, secret: str) -> str:
    string_to_sign = f"{timestamp}\n{secret}"
    return base64.b64encode(
        hmac.new(string_to_sign.encode("utf-8"), b"", hashlib.sha256).digest()
    ).decode("utf-8")


class SopFeishuSender:
    """专用于 SOP 巡检与复盘的飞书发送器: 不受旧通知层全局 10 分钟静音影响, 并校验 code == 0。"""

    def __init__(
        self,
        *,
        webhook_url: str,
        secret: str = "",
        fetch_impl: Callable[..., Awaitable[Any]] | None = None,
        backoff_seconds: tuple[float, ...] = (2.0, 5.0, 10.0),
        log: Callable[[str], None] = print,
        now_fn: Callable[[], datetime] = _default_now,
    ) -> None:
        self._webhook_url = webhook_url.strip()
        self._secret = secret.strip()
        self._fetch = fetch_impl if fetch_impl is not None else _default_feishu_fetch
        self._backoff_seconds = backoff_seconds
        self._log = log
        self._now_fn = now_fn
        self._send_lock = asyncio.Lock()

    def is_configured(self) -> bool:
        return self._webhook_url != ""

    async def send(self, payload: dict[str, Any]) -> bool:
        if not self.is_configured():
            return False

        title = str(payload.get("title") or "GOLD-BOT SOP")
        level = str(payload.get("alert_level") or "notify")
        raw_card = payload.get("card")
        if isinstance(raw_card, dict) and isinstance(raw_card.get("header"), dict):
            card_body = raw_card
        else:
            template = str(payload.get("template") or _LEVEL_TEMPLATES.get(level, "blue"))
            card_body = {
                "config": {"wide_screen_mode": True},
                "header": {
                    "title": {"tag": "plain_text", "content": title},
                    "template": template,
                },
                "elements": [{"tag": "markdown", "content": str(payload.get("content") or "")}],
            }

        now_dt = self._now_fn()
        if now_dt.tzinfo is None:
            now_dt = now_dt.replace(tzinfo=UTC)
        body_dict: dict[str, Any] = {
            "msg_type": "interactive",
            "card": card_body,
        }
        if self._secret:
            timestamp = str(int(now_dt.timestamp()))
            body_dict["timestamp"] = timestamp
            body_dict["sign"] = _sign_feishu(timestamp, self._secret)

        body_json = json.dumps(clean_feishu_card(body_dict), ensure_ascii=False)
        async with self._send_lock:
            for attempt in range(len(self._backoff_seconds) + 1):
                try:
                    response = await self._fetch(
                        self._webhook_url,
                        method="POST",
                        headers={"Content-Type": "application/json"},
                        body=body_json,
                    )
                    status = _response_status(response)
                    raw_text = await _response_text(response)
                except Exception as exc:  # noqa: BLE001
                    self._log(f"[SOP-FEISHU] failed level={level} title={title} err={exc}")
                    return False

                data: dict[str, Any] | None = None
                if raw_text:
                    try:
                        parsed = json.loads(raw_text)
                        data = parsed if isinstance(parsed, dict) else None
                    except ValueError:
                        data = None

                code = data.get("code") if data is not None else None
                msg = str(data.get("msg") or data.get("message") or "") if data is not None else ""
                is_rate_limit = code == 11232 or "frequency" in msg.lower()
                if is_rate_limit and attempt < len(self._backoff_seconds):
                    await asyncio.sleep(self._backoff_seconds[attempt])
                    continue

                if status is None or not (200 <= status < 300):
                    self._log(
                        f"[SOP-FEISHU] failed level={level} title={title} status={status} body={raw_text[:200]}"
                    )
                    return False

                if data is not None and code not in (0, None):
                    self._log(
                        f"[SOP-FEISHU] failed level={level} title={title} status={status} code={code} msg={msg}"
                    )
                    return False

                if raw_text and data is None:
                    self._log(
                        f"[SOP-FEISHU] failed level={level} title={title} status={status} invalid_json={raw_text[:200]}"
                    )
                    return False

                self._log(f"[SOP-FEISHU] delivered level={level} title={title}")
                return True

        self._log(f"[SOP-FEISHU] failed level={level} title={title} reason=rate_limit_exhausted")
        return False


class AlertRouter:
    """按五级告警策略决定是否发送飞书通知, 支持同维度冷却时间与关键消息去重。"""

    def __init__(
        self,
        *,
        feishu: Any = None,
        cooldown_seconds: int = 900,
        now_fn: Callable[[], datetime] = _default_now,
    ) -> None:
        self._feishu = feishu
        self._cooldown_seconds = max(0, int(cooldown_seconds))
        self._now_fn = now_fn
        # key: (account_id, symbol, category) -> (sent_at_dt, last_dedup_key)
        self._last_sent: dict[tuple[str, str, str], tuple[datetime, str]] = {}

    async def route(
        self,
        *,
        account_id: str,
        symbol: str,
        level: str,
        category: str,
        title: str,
        content: str,
        card: dict[str, Any] | None = None,
        template: str | None = None,
        dedup_key: str | None = None,
        bypass_cooldown: bool = False,
    ) -> dict[str, Any]:
        norm_level = level if level in ALERT_LEVELS else "notify"
        norm_sym = symbol.strip().upper()

        if norm_level == "silent":
            return {
                "sent": False,
                "suppressed": False,
                "level": "silent",
                "reason": "silent_level",
            }

        now_dt = self._now_fn()
        if now_dt.tzinfo is None:
            now_dt = now_dt.replace(tzinfo=UTC)

        bucket_key = (account_id, norm_sym, category)
        fp = dedup_key or f"{norm_level}:{title}:{content[:80]}"

        # urgent 告警或显式 bypass_cooldown 穿透冷却; 其余级别按类别/指纹做冷却防抖
        if not bypass_cooldown and norm_level != "urgent" and self._cooldown_seconds > 0:
            prev = self._last_sent.get(bucket_key)
            if prev is not None:
                prev_dt, prev_fp = prev
                elapsed = (now_dt - prev_dt).total_seconds()
                same_fingerprint = prev_fp == fp
                cooldown_limit = (
                    min(self._cooldown_seconds, 180)
                    if norm_level == "actionable"
                    else self._cooldown_seconds
                )
                if elapsed < cooldown_limit and (same_fingerprint or norm_level == "notify"):
                    return {
                        "sent": False,
                        "suppressed": True,
                        "level": norm_level,
                        "reason": "cooldown",
                        "elapsed_seconds": elapsed,
                    }

        if self._feishu is None:
            self._last_sent[bucket_key] = (now_dt, fp)
            return {
                "sent": False,
                "suppressed": False,
                "level": norm_level,
                "reason": "no_feishu_notifier",
            }

        payload: dict[str, Any] = {
            "title": title,
            "content": content,
            "alert_level": norm_level,
        }
        if template is not None:
            payload["template"] = template
        if card is not None:
            payload["card"] = card

        send_outcome = await self._feishu.send(payload)
        sent = send_outcome is not False
        if sent:
            self._last_sent[bucket_key] = (now_dt, fp)
            return {
                "sent": True,
                "suppressed": False,
                "level": norm_level,
                "reason": "delivered",
            }
        return {
            "sent": False,
            "suppressed": False,
            "level": norm_level,
            "reason": "delivery_failed",
        }


__all__ = ["ALERT_LEVELS", "AlertRouter", "SopFeishuSender"]
