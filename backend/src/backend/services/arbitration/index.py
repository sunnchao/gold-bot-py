"""仲裁服务(1:1 镜像 apps/app-server/src/services/arbitration/service.ts)。

ArbitrationManager:候选 signal 落库为 pending_signal(带 pending_signal_ttl_ms 过期
时间),轮询等待人工仲裁结果,超时后按 timeout_auto_pass_score 阈值自动放行/放弃。
JS 语义逐项保持:numberField/stringField、new Date().toISOString() 毫秒精度、
`??` 用显式 None 检查、JSON.stringify 紧凑分隔符。

ArbitrationService 为 ArbitrationManager 的别名(TS 原类名即 ArbitrationManager)。
"""

from __future__ import annotations

import asyncio
import json
import math
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from backend.persistence.records import EaRecord
from backend.persistence.store import EaStore

__all__ = [
    "DEFAULT_ARBITRATION_AUTO_PASS_SCORE",
    "DEFAULT_ARBITRATION_MAX_WAIT_MS",
    "DEFAULT_ARBITRATION_POLL_INTERVAL_MS",
    "DEFAULT_PENDING_SIGNAL_TTL_MS",
    "ArbitrationManager",
    "ArbitrationService",
    "SignalInput",
    "create_arbitration_service",
    "default_arbitration_config",
]

DEFAULT_ARBITRATION_MAX_WAIT_MS = 30_000
DEFAULT_ARBITRATION_AUTO_PASS_SCORE = 8
DEFAULT_ARBITRATION_POLL_INTERVAL_MS = 1_000
DEFAULT_PENDING_SIGNAL_TTL_MS = 5 * 60 * 1_000

SignalInput = EaRecord
"""信号输入(镜像 TS `type SignalInput = EaRecord`)。"""

ArbitrationConfig = dict[str, int]
"""max_wait_ms / timeout_auto_pass_score / poll_interval_ms / pending_signal_ttl_ms。"""


def default_arbitration_config() -> dict[str, int]:
    """镜像 defaultArbitrationConfig():与 Go 默认值一致。"""
    return {
        "max_wait_ms": DEFAULT_ARBITRATION_MAX_WAIT_MS,
        "timeout_auto_pass_score": DEFAULT_ARBITRATION_AUTO_PASS_SCORE,
        "poll_interval_ms": DEFAULT_ARBITRATION_POLL_INTERVAL_MS,
        "pending_signal_ttl_ms": DEFAULT_PENDING_SIGNAL_TTL_MS,
    }


class ArbitrationManager:
    """镜像 TS ArbitrationManager(options)。

    构造参数对应 TS options:store / config / now / log / sleep / signal,
    全部可省略(除 store),默认值与原实现一致。
    """

    def __init__(
        self,
        *,
        store: EaStore,
        config: dict[str, int] | None = None,
        now: Callable[[], datetime] | None = None,
        log: Callable[[str], None] | None = None,
        sleep: Callable[[int], Awaitable[None]] | None = None,
        signal: Callable[[], Any] | None = None,
    ) -> None:
        self._store = store
        self._config: dict[str, int] = default_arbitration_config()
        if config is not None:
            self._config.update(config)
        self._now: Callable[[], datetime] = now if now is not None else (lambda: datetime.now(UTC))
        self._log: Callable[[str], None] = log if log is not None else (lambda message: None)
        self._sleep: Callable[[int], Awaitable[None]] = sleep if sleep is not None else _default_sleep
        self._get_signal: Callable[[], Any] | None = signal
        self._pending_signals: dict[int, dict[str, Any]] = {}

    async def submit_signal(
        self,
        account_id: str,
        symbol: str,
        signal: SignalInput,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """镜像 submitSignal:落库 pending signal 后轮询仲裁,返回 verdict。"""
        score = _number_field(signal, "score")
        pending: EaRecord = {
            "account_id": account_id,
            "symbol": symbol,
            "status": "pending",
            "created_at": _to_iso(self._now()),
            "expires_at": _to_iso(self._now() + timedelta(milliseconds=self._config["pending_signal_ttl_ms"])),
            "indicators": _build_indicators_json(signal),
            "side": _string_field(signal, "side"),
            "score": score,
            "strategy": _string_field(signal, "strategy"),
        }
        await self._store.save_pending_signal(pending)

        # store 内部克隆并分配 id;回读恢复 id(与 TS 注释一致)。
        stored = await self._find_fresh_pending(account_id, symbol, _now_ms(self._now))
        signal_id = int(_number_field(stored, "id")) if stored is not None else 0
        if signal_id <= 0:
            result: dict[str, Any] = {"signalId": 0, "status": "timeout", "reason": "save_failed"}
            self._log(f"[ARBITRATION] save pending signal failed: {account_id}/{symbol}")
            return {"execute": False, "reason": str(result["reason"]), "result": result}

        self._pending_signals[signal_id] = {
            "signalId": signal_id,
            "accountId": account_id,
            "symbol": symbol,
            "score": score,
            "createdAt": _now_ms(self._now),
        }
        self._log(
            f"[ARBITRATION] submit {account_id}/{symbol} {_string_field(signal, 'side')} "
            f"score={score} (ID={signal_id})"
        )

        options_signal = options.get("signal") if options is not None else None
        result = await self._wait_for_arbitration(signal_id, account_id, symbol, options_signal)

        self._pending_signals.pop(signal_id, None)

        if result["status"] == "approved":
            self._log(f"[ARBITRATION] approved {account_id}/{symbol} (ID={signal_id})")
            return {"execute": True, "reason": str(result["reason"]), "result": result}
        if result["status"] == "rejected":
            self._log(f"[ARBITRATION] rejected {account_id}/{symbol} reason={result['reason']} (ID={signal_id})")
            return {"execute": False, "reason": str(result["reason"]), "result": result}
        # timeout 兜底:高分会自动放行,低分放弃
        if score >= self._config["timeout_auto_pass_score"]:
            self._log(f"[ARBITRATION] timeout auto-pass {account_id}/{symbol} score={score} (ID={signal_id})")
            return {"execute": True, "reason": "timeout_auto_pass", "result": result}
        self._log(f"[ARBITRATION] timeout abandoned {account_id}/{symbol} score={score} (ID={signal_id})")
        return {"execute": False, "reason": "timeout_abandoned", "result": result}

    async def _wait_for_arbitration(
        self,
        signal_id: int,
        account_id: str,
        symbol: str,
        signal: Any | None,
    ) -> dict[str, Any]:
        deadline = _now_ms(self._now) + self._config["max_wait_ms"]
        while True:
            await self._sleep(self._config["poll_interval_ms"])

            if _is_aborted(signal) or (self._get_signal is not None and _is_aborted(self._get_signal())):
                return {"signalId": signal_id, "status": "timeout", "reason": "context_cancelled"}
            if _now_ms(self._now) >= deadline:
                return {"signalId": signal_id, "status": "timeout", "reason": "max_wait_exceeded"}

            current = await self._find_pending(signal_id, account_id, symbol)
            if current is None:
                # signal 被外部过期或移除
                return {"signalId": signal_id, "status": "timeout", "reason": "expired"}
            status = _string_field(current, "status")
            if status in ("approved", "rejected"):
                return {
                    "signalId": signal_id,
                    "status": status,
                    "reason": _string_field(current, "arbitration_reason"),
                }

    async def _find_pending(self, signal_id: int, account_id: str, symbol: str) -> EaRecord | None:
        return await self._store.get_pending_signal_by_id(account_id, symbol, signal_id)

    async def _find_fresh_pending(self, account_id: str, symbol: str, created_at_ms: int) -> EaRecord | None:
        signals = await self._store.get_pending_signals(account_id, symbol)
        if len(signals) == 0:
            return None
        target = _to_iso(_from_ms(created_at_ms))
        for entry in signals:
            if _string_field(entry, "created_at") == target:
                return entry
        return signals[0]

    async def get_pending_signals(self, account_id: str, symbol: str) -> list[EaRecord]:
        return await self._store.get_pending_signals(account_id, symbol)

    async def update_arbitration_result(self, signal_id: int, result: str, reason: str) -> bool:
        return await self._store.update_pending_signal_arbitration(signal_id, result, reason)

    async def expire_stale_signals(self) -> int:
        return await self._store.expire_pending_signals(_to_iso(self._now()))

    def active_count(self) -> int:
        return len(self._pending_signals)


ArbitrationService = ArbitrationManager
"""别名:TS 原类名即 ArbitrationManager,同时暴露 ArbitrationService 供协调器使用。"""


def create_arbitration_service(options: dict[str, Any] | None = None) -> ArbitrationManager:
    """工厂:coordinator 用 options dict 组装(store/config/now/log/sleep/signal)。"""
    return ArbitrationManager(**(options or {}))


def _build_indicators_json(signal: SignalInput) -> str:
    data: dict[str, Any] = {
        "side": _string_field(signal, "side"),
        "entry": _number_field(signal, "entry"),
        "stop_loss": _number_field(signal, "stop_loss"),
        "tp1": _number_field(signal, "tp1"),
        "tp2": _number_field(signal, "tp2"),
        "score": _number_field(signal, "score"),
        "strategy": _string_field(signal, "strategy"),
        "atr": _number_field(signal, "atr"),
        "scale_in_parent_ticket": _number_field(signal, "scale_in_parent_ticket"),
        "weighted_avg_entry": _number_field(signal, "weighted_avg_entry"),
        "unified_sl": _number_field(signal, "unified_sl"),
        "scale_in_count": _number_field(signal, "scale_in_count"),
    }
    all_strategies = signal.get("all_strategies")
    if isinstance(all_strategies, list) and len(all_strategies) > 0:
        data["all_strategies"] = all_strategies
    try:
        return json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return "{}"


def _string_field(record: EaRecord, field: str) -> str:
    value = record.get(field)
    return value if isinstance(value, str) else ""


def _number_field(record: EaRecord, field: str) -> int | float:
    value = record.get(field)
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return value
    return 0


def _is_aborted(value: Any) -> bool:
    return bool(getattr(value, "aborted", False))


def _to_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _now_ms(now: Callable[[], datetime]) -> int:
    value = now()
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return int(value.timestamp() * 1000)


def _from_ms(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=UTC)


async def _default_sleep(ms: int) -> None:
    await asyncio.sleep(ms / 1000)
