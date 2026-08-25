"""下单动作转换器(1:1 镜像 gold-bot apps/app-agent/src/agents/trade-action-converter.ts)。

将 LLM 第二阶段 tool_use(name + input) 转换为 TradeAction:
- tool_use_to_trade_action():账户感知(market-first);无 account_id 时返回 None
- tool_use_to_trade_action_legacy():旧契约(无 account_id/symbol 字段)

同时导出 TRADE_ACTION_TOOLS / TRADE_ACTION_TOOLS_LEGACY
(镜像 types/trade-action.ts 的 function-calling tool schema)。
"""

from __future__ import annotations

import math
from typing import Any

from backend.agents.agents._support import DEFAULT_MAX_LOTS, DEFAULT_MIN_LOTS

__all__ = [
    "TRADE_ACTION_TOOLS",
    "TRADE_ACTION_TOOLS_LEGACY",
    "tool_use_to_trade_action",
    "tool_use_to_trade_action_legacy",
]

JSONDict = dict[str, Any]

NumberParseResult = tuple[bool, float | None, str | None]
"""语义与 TS 一致:(ok, value, reason);ok=False 时 reason 非空。"""


# ─── tool schemas(镜像 types/trade-action.ts) ─────────────────────────────────

TRADE_ACTION_TOOLS: list[JSONDict] = [
    {
        "name": "place_pending_order",
        "description": (
            "Place a pending order (BUY_LIMIT or SELL_LIMIT) that triggers when price reaches a target level. "
            'Use this when the LLM suggests a precise entry price DIFFERENT from the current market price '
            '(e.g., "等待回调至 4145 入场" — wait for pullback to 4145). '
            "Required when entry_price != current market price. "
            "The order auto-expires in 4 hours if not triggered."
        ),
        "input_schema": {
            "type": "object",
            "required": [
                "account_id",
                "symbol",
                "side",
                "entry_price",
                "stop_loss",
                "take_profit_1",
                "lots",
                "order_type",
                "reason",
            ],
            "properties": {
                "account_id": {
                    "type": "string",
                    "description": "Target account id. Must match the provided account context.",
                },
                "symbol": {
                    "type": "string",
                    "description": "Exact tradable contract symbol loaded by the target account.",
                },
                "side": {"type": "string", "enum": ["buy", "sell"]},
                "entry_price": {
                    "type": "number",
                    "description": "Pending order trigger price (must differ from current price)",
                },
                "stop_loss": {"type": "number"},
                "take_profit_1": {"type": "number"},
                "take_profit_2": {"type": "number"},
                "lots": {"type": "number"},
                "order_type": {
            "type": "string",
            "enum": ["limit", "stop"],
            "description": "limit=回调入场, stop=突破入场",
        },
                "expiry_hours": {"type": "number", "default": 4},
                "reason": {
                    "type": "string",
                    "description": "Bilingual explanation (Chinese first, English in parens)",
                },
            },
        },
    },
    {
        "name": "place_market_order",
        "description": (
            "Place a market order at the current bid/ask. Use only when the LLM wants to open IMMEDIATELY "
            "at the current price (no entry target)."
        ),
        "input_schema": {
            "type": "object",
            "required": ["account_id", "symbol", "side", "stop_loss", "take_profit_1", "lots", "reason"],
            "properties": {
                "account_id": {
                    "type": "string",
                    "description": "Target account id. Must match the provided account context.",
                },
                "symbol": {
                    "type": "string",
                    "description": "Exact tradable contract symbol loaded by the target account.",
                },
                "side": {"type": "string", "enum": ["buy", "sell"]},
                "stop_loss": {"type": "number"},
                "take_profit_1": {"type": "number"},
                "take_profit_2": {"type": "number"},
                "lots": {"type": "number"},
                "reason": {"type": "string"},
            },
        },
    },
    {
        "name": "modify_order",
        "description": (
            "Modify an existing order for the specified account only. "
            "The tuple (account_id, symbol, ticket) must match a position visible in that account context."
        ),
        "input_schema": {
            "type": "object",
            "required": ["account_id", "symbol", "ticket", "reason"],
            "properties": {
                "account_id": {"type": "string"},
                "symbol": {"type": "string"},
                "ticket": {"type": "number"},
                "new_sl": {"type": "number"},
                "new_tp1": {"type": "number"},
                "new_tp2": {"type": "number"},
                "reason": {"type": "string"},
            },
        },
    },
    {
        "name": "close_order",
        "description": (
            "Close an existing order for the specified account only. "
            "The tuple (account_id, symbol, ticket) must match a position visible in that account context."
        ),
        "input_schema": {
            "type": "object",
            "required": ["account_id", "symbol", "ticket", "reason"],
            "properties": {
                "account_id": {"type": "string"},
                "symbol": {"type": "string"},
                "ticket": {"type": "number"},
                "reason": {"type": "string"},
            },
        },
    },
    {
        "name": "do_nothing",
        "description": (
            "No trade action. Use when the LLM recommends hold / wait for confirmation / no edge. "
            "MUST provide a `reasoning` string."
        ),
        "input_schema": {
            "type": "object",
            "required": ["account_id", "reasoning"],
            "properties": {
                "account_id": {
                    "type": "string",
                    "description": "Target account id. Must match the provided account context.",
                },
                "reasoning": {"type": "string"},
            },
        },
    },
]

_TRADE_ACTION_PENDING_ORDER_SCHEMA: JSONDict = {
    "type": "object",
    "required": [
        "side",
        "entry_price",
        "stop_loss",
        "take_profit_1",
        "lots",
        "order_type",
        "reason",
    ],
    "properties": {
        "side": {"type": "string", "enum": ["buy", "sell"]},
        "entry_price": {
            "type": "number",
            "description": "Pending order trigger price (must differ from current price)",
        },
        "stop_loss": {"type": "number"},
        "take_profit_1": {"type": "number"},
        "take_profit_2": {"type": "number"},
        "lots": {"type": "number"},
        "order_type": {
            "type": "string",
            "enum": ["limit", "stop"],
            "description": "limit=回调入场, stop=突破入场",
        },
        "expiry_hours": {"type": "number", "default": 4},
        "reason": {"type": "string", "description": "Bilingual explanation (Chinese first, English in parens)"},
    },
}

_TRADE_ACTION_MARKET_ORDER_SCHEMA: JSONDict = {
    "type": "object",
    "required": ["side", "stop_loss", "take_profit_1", "lots", "reason"],
    "properties": {
        "side": {"type": "string", "enum": ["buy", "sell"]},
        "stop_loss": {"type": "number"},
        "take_profit_1": {"type": "number"},
        "take_profit_2": {"type": "number"},
        "lots": {"type": "number"},
        "reason": {"type": "string"},
    },
}

TRADE_ACTION_TOOLS_LEGACY: list[JSONDict] = [
    {
        "name": "place_pending_order",
        "description": (
            "Place a pending order (BUY_LIMIT or SELL_LIMIT) that triggers when price reaches a target level. "
            'Use this when the LLM suggests a precise entry price DIFFERENT from the current market price '
            '(e.g., "等待回调至 4145 入场" — wait for pullback to 4145). '
            "Required when entry_price != current market price. "
            "The order auto-expires in 4 hours if not triggered."
        ),
        "input_schema": _TRADE_ACTION_PENDING_ORDER_SCHEMA,
    },
    {
        "name": "place_market_order",
        "description": (
            "Place a market order at the current bid/ask. Use only when the LLM wants to open IMMEDIATELY "
            "at the current price (no entry target)."
        ),
        "input_schema": _TRADE_ACTION_MARKET_ORDER_SCHEMA,
    },
    {
        "name": "do_nothing",
        "description": (
            "No trade action. Use when the LLM recommends hold / wait for confirmation / no edge. "
            "MUST provide a `reasoning` string."
        ),
        "input_schema": {
            "type": "object",
            "required": ["reasoning"],
            "properties": {"reasoning": {"type": "string"}},
        },
    },
]


# ─── 数值读取(镜像 TS 的 Number()/parseFloat 语义) ────────────────────────────


def _js_number(raw: Any) -> float | None:
    """镜像 Number(raw):'' -> 0,非数字字符串 -> NaN(返回 None)。"""
    if isinstance(raw, bool):
        return 1.0 if raw else 0.0
    if isinstance(raw, (int, float)):
        return float(raw) if math.isfinite(float(raw)) else None
    if isinstance(raw, str):
        value = raw.strip()
        try:
            return float(value) if value else 0.0
        except ValueError:
            return None
    return None


def _do_nothing(reasoning: str | None) -> JSONDict:
    # 镜像 TS 的 String(input.reasoning ?? '') —— None -> ''
    return {"type": "do_nothing", "reasoning": reasoning or ""}


def _account_do_nothing(account_id: str, reasoning: str | None) -> JSONDict:
    # 镜像 TS 的 String(input.reasoning ?? '') —— None -> ''
    return {"type": "do_nothing", "account_id": account_id, "reasoning": reasoning or ""}


def _read_account_id(input_: JSONDict) -> str | None:
    account_id = input_.get("account_id")
    account_id = account_id.strip() if isinstance(account_id, str) else ""
    return account_id if account_id else None


def _read_string(input_: JSONDict, field: str) -> str | None:
    value = input_.get(field)
    value = value.strip() if isinstance(value, str) else ""
    return value if value else None


def _read_symbol(input_: JSONDict, profile: JSONDict, expected_symbol: str | None = None) -> str:
    return _read_string(input_, "symbol") or expected_symbol or profile["symbol"]


def _format_price(price: float | None, profile: JSONDict) -> str:
    if price is None or not math.isfinite(price):
        return str(price)
    return f"{price:.{profile['price_precision']}f}"


def _read_number(input_: JSONDict, field: str, required: bool = True) -> NumberParseResult:
    raw = input_.get(field)
    if (raw is None or raw == "") and not required:
        return True, None, None
    value = _js_number(raw)
    if value is None:
        return False, None, f"invalid numeric field {field}: {raw}"
    return True, value, None


def _read_required_number(input_: JSONDict, field: str) -> NumberParseResult:
    ok, value, reason = _read_number(input_, field, True)
    if not ok:
        return False, None, reason
    if value is None:
        return False, None, f"missing numeric field {field}"
    return True, value, None


def _read_optional_number(input_: JSONDict, field: str) -> NumberParseResult:
    return _read_number(input_, field, False)


def _get_side(input_: JSONDict) -> str | None:
    side = str(input_.get("side"))
    return side if side in ("buy", "sell") else None


def _validate_lots(lots: float | None, profile: JSONDict) -> str | None:
    raw_min_lots = _js_number(profile.get("min_lots"))
    raw_max_lots = _js_number(profile.get("max_lots"))
    min_lots = raw_min_lots if raw_min_lots is not None else DEFAULT_MIN_LOTS
    max_lots = raw_max_lots if raw_max_lots is not None else DEFAULT_MAX_LOTS
    if lots is None or lots < min_lots or lots > max_lots:
        return f"lots {lots} outside allowed range {min_lots}-{max_lots} for {profile['symbol']}"
    return None


# ─── 转换主函数 ───────────────────────────────────────────────────────────────


def tool_use_to_trade_action(
    tool_use: JSONDict,
    current_price: float,
    profile: JSONDict,
    expected_symbol: str | None = None,
) -> JSONDict | None:
    """镜像 toolUseToTradeAction:账户感知转换;无 account_id 返回 None。"""
    input_ = tool_use.get("input") or {}
    account_id = _read_account_id(input_)
    if not account_id:
        return None

    name = tool_use.get("name")
    if name == "do_nothing":
        return {**_do_nothing(str(input_.get("reasoning") or "")), "account_id": account_id}

    if name == "place_market_order":
        side = _get_side(input_)
        if not side:
            return _account_do_nothing(account_id, f"invalid market order side: {str(input_.get('side'))}")
        stop_loss = _read_required_number(input_, "stop_loss")
        if not stop_loss[0]:
            return _account_do_nothing(account_id, stop_loss[2])
        take_profit_1 = _read_required_number(input_, "take_profit_1")
        if not take_profit_1[0]:
            return _account_do_nothing(account_id, take_profit_1[2])
        take_profit_2 = _read_optional_number(input_, "take_profit_2")
        if not take_profit_2[0]:
            return _account_do_nothing(account_id, take_profit_2[2])
        lots = _read_required_number(input_, "lots")
        if not lots[0]:
            return _account_do_nothing(account_id, lots[2])
        lot_validation_error = _validate_lots(lots[1], profile)
        if lot_validation_error:
            return _account_do_nothing(account_id, lot_validation_error)
        return {
            "type": "place_market_order",
            "account_id": account_id,
            "symbol": _read_symbol(input_, profile, expected_symbol),
            "side": side,
            "stop_loss": stop_loss[1],
            "take_profit_1": take_profit_1[1],
            "take_profit_2": take_profit_2[1],
            "lots": lots[1],
            "reason": str(input_.get("reason") or ""),
        }

    if name == "place_pending_order":
        side = _get_side(input_)
        if not side:
            return _account_do_nothing(account_id, f"invalid pending order side: {str(input_.get('side'))}")
        entry_price = _read_required_number(input_, "entry_price")
        if not entry_price[0]:
            return _account_do_nothing(account_id, entry_price[2])
        assert entry_price[1] is not None
        stop_loss = _read_required_number(input_, "stop_loss")
        if not stop_loss[0]:
            return _account_do_nothing(account_id, stop_loss[2])
        take_profit_1 = _read_required_number(input_, "take_profit_1")
        if not take_profit_1[0]:
            return _account_do_nothing(account_id, take_profit_1[2])
        take_profit_2 = _read_optional_number(input_, "take_profit_2")
        if not take_profit_2[0]:
            return _account_do_nothing(account_id, take_profit_2[2])
        lots = _read_required_number(input_, "lots")
        if not lots[0]:
            return _account_do_nothing(account_id, lots[2])
        lot_validation_error = _validate_lots(lots[1], profile)
        if lot_validation_error:
            return _account_do_nothing(account_id, lot_validation_error)
        expiry_hours = _read_optional_number(input_, "expiry_hours")
        if not expiry_hours[0]:
            return _account_do_nothing(account_id, expiry_hours[2])

        order_type = "stop" if str(input_.get("order_type")) == "stop" else "limit"

        if order_type == "limit" and math.isfinite(current_price) and current_price > 0:
            if side == "buy" and entry_price[1] >= current_price:
                return _account_do_nothing(
                    account_id,
                    f"BUY_LIMIT entry {_format_price(entry_price[1], profile)} >= current "
                    f"{_format_price(current_price, profile)}, should be below current price",
                )
            if side == "sell" and entry_price[1] <= current_price:
                return _account_do_nothing(
                    account_id,
                    f"SELL_LIMIT entry {_format_price(entry_price[1], profile)} <= current "
                    f"{_format_price(current_price, profile)}, should be above current price",
                )

        return {
            "type": "place_pending_order",
            "account_id": account_id,
            "symbol": _read_symbol(input_, profile, expected_symbol),
            "side": side,
            "entry_price": entry_price[1],
            "stop_loss": stop_loss[1],
            "take_profit_1": take_profit_1[1],
            "take_profit_2": take_profit_2[1],
            "lots": lots[1],
            "order_type": order_type,
            "expiry_hours": expiry_hours[1] if expiry_hours[1] is not None else 4,
            "reason": str(input_.get("reason") or ""),
        }

    if name == "modify_order":
        symbol = _read_string(input_, "symbol")
        if not symbol:
            return _account_do_nothing(account_id, "missing modify_order symbol")
        ticket = _read_required_number(input_, "ticket")
        if not ticket[0]:
            return _account_do_nothing(account_id, ticket[2])
        new_sl = _read_optional_number(input_, "new_sl")
        if not new_sl[0]:
            return _account_do_nothing(account_id, new_sl[2])
        new_tp1 = _read_optional_number(input_, "new_tp1")
        if not new_tp1[0]:
            return _account_do_nothing(account_id, new_tp1[2])
        new_tp2 = _read_optional_number(input_, "new_tp2")
        if not new_tp2[0]:
            return _account_do_nothing(account_id, new_tp2[2])
        return {
            "type": "modify_order",
            "account_id": account_id,
            "symbol": symbol,
            "ticket": ticket[1],
            "new_sl": new_sl[1],
            "new_tp1": new_tp1[1],
            "new_tp2": new_tp2[1],
            "reason": str(input_.get("reason") or ""),
        }

    if name == "close_order":
        symbol = _read_string(input_, "symbol")
        if not symbol:
            return _account_do_nothing(account_id, "missing close_order symbol")
        ticket = _read_required_number(input_, "ticket")
        if not ticket[0]:
            return _account_do_nothing(account_id, ticket[2])
        return {
            "type": "close_order",
            "account_id": account_id,
            "symbol": symbol,
            "ticket": ticket[1],
            "reason": str(input_.get("reason") or ""),
        }

    return None


def tool_use_to_trade_action_legacy(
    tool_use: JSONDict,
    current_price: float,
    profile: JSONDict,
) -> JSONDict | None:
    """镜像 toolUseToTradeActionLegacy:旧契约转换(无 account_id/symbol)。"""
    input_ = tool_use.get("input") or {}
    name = tool_use.get("name")

    if name == "do_nothing":
        return _do_nothing(str(input_.get("reasoning") or ""))

    if name == "place_market_order":
        side = _get_side(input_)
        if not side:
            return _do_nothing(f"invalid market order side: {str(input_.get('side'))}")
        stop_loss = _read_required_number(input_, "stop_loss")
        if not stop_loss[0]:
            return _do_nothing(stop_loss[2])
        take_profit_1 = _read_required_number(input_, "take_profit_1")
        if not take_profit_1[0]:
            return _do_nothing(take_profit_1[2])
        take_profit_2 = _read_optional_number(input_, "take_profit_2")
        if not take_profit_2[0]:
            return _do_nothing(take_profit_2[2])
        lots = _read_required_number(input_, "lots")
        if not lots[0]:
            return _do_nothing(lots[2])
        lot_validation_error = _validate_lots(lots[1], profile)
        if lot_validation_error:
            return _do_nothing(lot_validation_error)
        return {
            "type": "place_market_order",
            "side": side,
            "stop_loss": stop_loss[1],
            "take_profit_1": take_profit_1[1],
            "take_profit_2": take_profit_2[1],
            "lots": lots[1],
            "reason": str(input_.get("reason") or ""),
        }

    if name == "place_pending_order":
        side = _get_side(input_)
        if not side:
            return _do_nothing(f"invalid pending order side: {str(input_.get('side'))}")
        entry_price = _read_required_number(input_, "entry_price")
        if not entry_price[0]:
            return _do_nothing(entry_price[2])
        assert entry_price[1] is not None
        stop_loss = _read_required_number(input_, "stop_loss")
        if not stop_loss[0]:
            return _do_nothing(stop_loss[2])
        take_profit_1 = _read_required_number(input_, "take_profit_1")
        if not take_profit_1[0]:
            return _do_nothing(take_profit_1[2])
        take_profit_2 = _read_optional_number(input_, "take_profit_2")
        if not take_profit_2[0]:
            return _do_nothing(take_profit_2[2])
        lots = _read_required_number(input_, "lots")
        if not lots[0]:
            return _do_nothing(lots[2])
        lot_validation_error = _validate_lots(lots[1], profile)
        if lot_validation_error:
            return _do_nothing(lot_validation_error)
        expiry_hours = _read_optional_number(input_, "expiry_hours")
        if not expiry_hours[0]:
            return _do_nothing(expiry_hours[2])

        order_type = "stop" if str(input_.get("order_type")) == "stop" else "limit"

        if order_type == "limit" and math.isfinite(current_price) and current_price > 0:
            if side == "buy" and entry_price[1] >= current_price:
                return _do_nothing(
                    f"BUY_LIMIT entry {_format_price(entry_price[1], profile)} >= current "
                    f"{_format_price(current_price, profile)}, should be below current price",
                )
            if side == "sell" and entry_price[1] <= current_price:
                return _do_nothing(
                    f"SELL_LIMIT entry {_format_price(entry_price[1], profile)} <= current "
                    f"{_format_price(current_price, profile)}, should be above current price",
                )

        return {
            "type": "place_pending_order",
            "side": side,
            "entry_price": entry_price[1],
            "stop_loss": stop_loss[1],
            "take_profit_1": take_profit_1[1],
            "take_profit_2": take_profit_2[1],
            "lots": lots[1],
            "order_type": order_type,
            "expiry_hours": expiry_hours[1] if expiry_hours[1] is not None else 4,
            "reason": str(input_.get("reason") or ""),
        }

    return None
