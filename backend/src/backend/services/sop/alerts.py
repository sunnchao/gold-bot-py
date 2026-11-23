"""SOP 五级分层告警路由器 (silent / notify / actionable / urgent / digest) 与冷却去重。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

ALERT_LEVELS: tuple[str, ...] = ("silent", "notify", "actionable", "urgent", "digest")


def _default_now() -> datetime:
    return datetime.now(UTC)


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
        dedup_key: str | None = None,
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

        # urgent 告警穿透冷却; 其余级别若同 category + 同 dedup_key 在冷却期内则抑制
        if norm_level != "urgent" and self._cooldown_seconds > 0:
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
        if card is not None:
            payload["card"] = card

        await self._feishu.send(payload)
        self._last_sent[bucket_key] = (now_dt, fp)
        return {
            "sent": True,
            "suppressed": False,
            "level": norm_level,
            "reason": "delivered",
        }


__all__ = ["ALERT_LEVELS", "AlertRouter"]
