"""API v2 分析触发(镜像 apps/app-agent/src/trigger/trigger.controller.ts)。

白名单/幂等窗/账号 ai_symbols 契约;workflow 与 bar_source 注入,离线可测。
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

ALLOWED_SYMBOLS = frozenset(
    {
        "XAUUSD",
        "XAGUSD",
        "GOLD",
        "GBPJPY",
        "EURJPY",
        "USDJPY",
        "GBPUSD",
        "USDCAD",
        "EURUSD",
        "AUDUSD",
        "NZDUSD",
        "USDCNH",
        "US100CASH",
        "USOILCASH",
        "UKOILCASH",
        "GOLDM#",
        "SILVERM#",
    }
)

IDEMPOTENCY_WINDOW_MS = 60_000

_recent_triggers: dict[str, float] = {}


def _normalize_symbol_for_match(symbol: str) -> str:
    return symbol.strip().upper()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class TriggerError(Exception):
    """镜像 NestJS HttpException:含 error/status 字段。"""

    def __init__(self, error: str, message: str, status: int) -> None:
        super().__init__(message)
        self.error = error
        self.message = message
        self.status = status


async def trigger_analysis(
    workflow: Any,
    bar_source: Any,
    account: str,
    symbol: str,
    request_token: str,
    api_token: str | None = None,
    force: str | None = None,
    now_millis: int | None = None,
    now_iso_fn=None,
) -> dict[str, Any]:
    if api_token and request_token != api_token:
        raise TriggerError("forbidden", "Invalid or missing API token", 403)

    normalized = _normalize_symbol_for_match(symbol)
    if normalized not in ALLOWED_SYMBOLS:
        raise TriggerError("bad_request", f"Symbol '{symbol}' is not allowed", 400)

    account_symbols = await bar_source.account_symbols(account)
    tradable_symbol = next(
        (s for s in account_symbols if _normalize_symbol_for_match(s) == normalized), None
    )
    if tradable_symbol is None:
        raise TriggerError(
            "symbol_not_loaded", f"Symbol '{symbol}' is not loaded by account '{account}'", 400
        )

    idempotency_key = f"{account}:{tradable_symbol}"
    now = now_millis if now_millis is not None else int(time.time() * 1000)
    last_trigger = _recent_triggers.get(idempotency_key)
    if last_trigger is not None and (now - last_trigger) < IDEMPOTENCY_WINDOW_MS:
        return {
            "triggered": False,
            "reason": "recently_triggered",
            "account": account,
            "symbol": tradable_symbol,
            "timestamp": _now_iso() if now_iso_fn is None else now_iso_fn(),
        }
    _recent_triggers[idempotency_key] = now

    if len(_recent_triggers) > 20:
        for key, ts in list(_recent_triggers.items()):
            if now - ts > IDEMPOTENCY_WINDOW_MS * 2:
                del _recent_triggers[key]

    force_analyze = force in ("true", "1")
    await workflow.run(account, [tradable_symbol], {"forceAnalyze": force_analyze})

    return {
        "triggered": True,
        "account": account,
        "symbol": tradable_symbol,
        "timestamp": _now_iso() if now_iso_fn is None else now_iso_fn(),
    }
