"""命令生命周期服务(1:1 镜像 apps/app-server/src/services/command-lifecycle/service.ts)。

CommandLifecycleService.acceptCandidate:落库候选命令并按运行时模式(promote /
demote_to_shadow_only)决定投递状态,同时发布 shadow 运行时快照与 shadow 对比行。
reconcile 透传 store.reconcile_command_result,并对 error 4108(订单不存在,
仓位已被 EA 内置 TP/SL 平仓)清理 position_states 幽灵行。
"""

from __future__ import annotations

import re
from typing import Any

from backend.persistence.records import EaRecord, StoredCommand
from backend.persistence.store import EaStore
from backend.services.shadow.index import ShadowService

__all__ = ["CommandLifecycleService", "create_command_lifecycle_service"]

# 镜像 parseInt(value, 10):前导空白 + 可选符号 + 数字前缀,非法输入视为 NaN→0
_PARSE_INT_RE = re.compile(r"^[ \t\n\f\r]*[+-]?\d+")


class CommandLifecycleService:
    """镜像 TS CommandLifecycleService(store, defaultRuntimeMode='oracle', shadow?)。"""

    def __init__(
        self,
        store: EaStore,
        default_runtime_mode: str = "oracle",
        shadow: ShadowService | None = None,
    ) -> None:
        self._store = store
        self._default_runtime_mode = default_runtime_mode
        self._shadow = shadow

    async def accept_candidate(self, account_id: str, candidate: EaRecord) -> StoredCommand:
        stored = await self._store.save_command_candidate(account_id, candidate)
        mode = _resolve_runtime_mode(await self._store.get_runtime_mode(account_id), self._default_runtime_mode)
        if mode == "cutover":
            await self._store.promote_command(stored["command_id"])
        else:
            await self._store.demote_command_to_shadow_only(stored["command_id"])
        resolved = await self._store.get_command(stored["command_id"])
        if resolved is None:
            resolved = stored
        symbol_value = resolved.get("symbol")
        symbol = symbol_value if isinstance(symbol_value, str) and len(symbol_value) > 0 else "XAUUSD"
        source = _shadow_source_for_command(str(resolved.get("source", "")))
        created_at = str(resolved.get("created_at", ""))
        if self._shadow is not None:
            await self._shadow.record_runtime_snapshot(
                {
                    "account_id": account_id,
                    "symbol": symbol,
                    "source": source,
                    "command": resolved,
                    "created_at": created_at,
                }
            )
        await self._store.record_shadow_comparison(
            {
                "account_id": account_id,
                "symbol": symbol,
                "protocol_ok": True,
                "signal_drift": False,
                "command_drift": False,
                "oracle_compared": False,
                "source": source,
                "created_at": created_at,
            }
        )
        return resolved

    async def reconcile(
        self,
        account_id: str,
        command_id: str,
        result: str,
        ticket: int | None = None,
        error_text: str | None = None,
        created_at: str | None = None,
    ) -> bool:
        normalized_error = error_text if error_text is not None else ""
        ok = await self._store.reconcile_command_result(
            account_id, command_id, result, ticket, normalized_error, created_at
        )

        # error 4108 = "order not found" — 仓位已被 EA 内置 TP/SL 平仓,服务端尚未感知。
        # 立即清理 position_states 里的幽灵行,防止 PM 持续对死 ticket 发命令。
        if normalized_error.strip() == "4108":
            target_ticket = _extract_ticket_from_command_id(command_id)
            symbol = _extract_symbol_from_command_id(command_id)
            if target_ticket > 0 and len(symbol) > 0:
                try:
                    states = await self._store.load_position_states(account_id, symbol)
                    keep_tickets = [
                        t for t in (int(s.get("ticket", 0)) for s in states) if t > 0 and t != target_ticket
                    ]
                    await self._store.delete_stale_position_states(account_id, symbol, keep_tickets)
                except Exception:
                    # 清理失败不影响主流程
                    pass

        return ok


def _shadow_source_for_command(source: str) -> str:
    if source == "live_strategy":
        return "ea_analysis"
    if source in ("ai_stop_loss", "position_manager"):
        return "position_review"
    if source in ("ai_risk_alert", "ai_approve"):
        return "ai_result"
    return source


def _resolve_runtime_mode(stored_mode: str, default_runtime_mode: str) -> str:
    if stored_mode == "oracle" and default_runtime_mode in ("shadow", "cutover"):
        return default_runtime_mode
    return stored_mode


def _extract_ticket_from_command_id(command_id: str) -> int:
    """PM 命令 ID 格式 pm_{accountId}_{symbol}_{ticket}_{action}_{reason}_{timestamp}。"""
    parts = command_id.split("_")
    if len(parts) >= 4 and parts[0] == "pm":
        ticket = _js_parse_int(parts[3])
        if ticket > 0:
            return ticket
    return 0


def _extract_symbol_from_command_id(command_id: str) -> str:
    parts = command_id.split("_")
    if len(parts) >= 3 and parts[0] == "pm":
        return parts[2]
    return ""


def _js_parse_int(value: str) -> int:
    match = _PARSE_INT_RE.match(value)
    if match is None:
        return 0
    return int(match.group(0))


def create_command_lifecycle_service(options: dict[str, Any] | None = None) -> CommandLifecycleService:
    """工厂:coordinator 用 options dict 组装(store/default_runtime_mode/shadow)。"""
    opts = options or {}
    return CommandLifecycleService(
        opts["store"],
        str(opts.get("default_runtime_mode", "oracle")),
        opts.get("shadow"),
    )
