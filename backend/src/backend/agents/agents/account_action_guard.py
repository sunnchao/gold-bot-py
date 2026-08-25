"""账户动作守卫(1:1 镜像 gold-bot apps/app-agent/src/agents/account-action-guard.ts)。

纯函数三件套:
- is_symbol_loaded(account_view)      -> ai_symbols 中是否存在账户签约 symbol
- assert_ticket_belongs_to_account()  -> modify/close 的 ticket 归属校验
- validate_trade_action_for_account() -> 下单动作与账户视图的完整校验

返回语义与 TS 一致:{ok: True} 或 {ok: False, reason}。
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "AccountActionGuardResult",
    "assert_ticket_belongs_to_account",
    "is_symbol_loaded",
    "validate_trade_action_for_account",
]

JSONDict = dict[str, Any]

AccountActionGuardResult = dict[str, Any]


def _same_symbol(left: str, right: str) -> bool:
    return left.strip().upper() == right.strip().upper()


def _position_symbol(position: Any) -> str | None:
    if isinstance(position, dict) and "symbol" in position:
        raw = position.get("symbol")
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    return None


def is_symbol_loaded(account_view: JSONDict) -> bool:
    """镜像 isSymbolLoaded:ai_symbols 中任一 symbol 与账户 symbol 匹配。"""
    ai_symbols = account_view.get("aiSymbols") or []
    return any(_same_symbol(symbol, account_view.get("symbol", "")) for symbol in ai_symbols)


def assert_ticket_belongs_to_account(
    account_view: JSONDict,
    action: JSONDict,
) -> AccountActionGuardResult:
    """镜像 assertTicketBelongsToAccount:modify/close 的 account/symbol/ticket 归属。"""
    if action.get("account_id") != account_view.get("accountId"):
        return {"ok": False, "reason": "order.account_mismatch"}
    if not _same_symbol(action.get("symbol", ""), account_view.get("symbol", "")):
        return {"ok": False, "reason": "order.symbol_mismatch"}

    payload = account_view.get("payload") or {}
    positions = payload.get("positions") or []
    position = next(
        (
            item
            for item in positions
            if isinstance(item, dict) and _number_field(item, "ticket") == action.get("ticket")
        ),
        None,
    )
    if position is None:
        return {"ok": False, "reason": "order.ticket_not_found"}
    owned_symbol = _position_symbol(position)
    if not owned_symbol or not _same_symbol(owned_symbol, action.get("symbol", "")):
        return {"ok": False, "reason": "order.symbol_mismatch"}

    return {"ok": True}


def validate_trade_action_for_account(
    action: JSONDict,
    account_view: JSONDict,
) -> AccountActionGuardResult:
    """镜像 validateTradeActionForAccount:下单动作完整校验。"""
    if action.get("account_id") != account_view.get("accountId"):
        return {"ok": False, "reason": "action.account_mismatch"}
    if action.get("type") in ("place_market_order", "place_pending_order"):
        symbol = action.get("symbol")
        if not symbol or not _same_symbol(symbol, account_view.get("symbol", "")):
            return {"ok": False, "reason": "account.symbol_mismatch"}
    if not is_symbol_loaded(account_view):
        return {"ok": False, "reason": "account.symbol_not_loaded"}
    if action.get("type") in ("modify_order", "close_order"):
        return assert_ticket_belongs_to_account(account_view, action)
    return {"ok": True}


def _number_field(record: JSONDict, field: str) -> float | int | None:
    value = record.get(field)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    return None
