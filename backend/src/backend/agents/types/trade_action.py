"""镜像 apps/app-agent/src/types/trade-action.ts。

TradeAction — function calling 下单动作类型,由 comprehensive-analyst 第二阶段
invokeWithTools() 产生。TRADE_ACTION_TOOLS / TRADE_ACTION_TOOLS_LEGACY 逐字镜像
TS 常量(名称/描述/input_schema 的 required、properties、default)。
TS 常量(名称/描述/input_schema 的 required、properties、default)。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

__all__ = [
    "CloseOrderAction",
    "DoNothingAction",
    "MarketOrderAction",
    "ModifyOrderAction",
    "PendingOrderAction",
    "TRADE_ACTION_TOOLS",
    "TRADE_ACTION_TOOLS_LEGACY",
    "TradeAction",
]


@dataclass
class PendingOrderAction:
    type: Literal["place_pending_order"]
    side: Literal["buy", "sell"]
    entry_price: float  # 挂单触发价格
    stop_loss: float
    take_profit_1: float
    lots: float  # profile-constrained MT4 lots
    order_type: Literal["limit", "stop"]  # limit=回调入场, stop=突破入场
    reason: str  # 中英双语
    account_id: str | None = None
    symbol: str | None = None
    take_profit_2: float | None = None
    expiry_hours: int | None = None  # 默认 4


@dataclass
class MarketOrderAction:
    type: Literal["place_market_order"]
    side: Literal["buy", "sell"]
    stop_loss: float
    take_profit_1: float
    lots: float
    reason: str
    account_id: str | None = None
    symbol: str | None = None
    take_profit_2: float | None = None


@dataclass
class DoNothingAction:
    type: Literal["do_nothing"]
    reasoning: str
    account_id: str | None = None


@dataclass
class ModifyOrderAction:
    type: Literal["modify_order"]
    account_id: str
    symbol: str
    ticket: int
    reason: str
    new_sl: float | None = None
    new_tp1: float | None = None
    new_tp2: float | None = None


@dataclass
class CloseOrderAction:
    type: Literal["close_order"]
    account_id: str
    symbol: str
    ticket: int
    reason: str


TradeAction = (
    PendingOrderAction
    | MarketOrderAction
    | ModifyOrderAction
    | CloseOrderAction
    | DoNothingAction
)

# ---------------------------------------------------------------------------
# OpenAI Chat Completions function-calling tool schema(second-phase tool call)
# ---------------------------------------------------------------------------

TRADE_ACTION_TOOLS: list[dict[str, Any]] = [
    {
        "name": "place_pending_order",
        "description": (
            "Place a pending order (BUY_LIMIT or SELL_LIMIT) that triggers when price reaches a target level. "
            "Use this when the LLM suggests a precise entry price DIFFERENT from the current market price "
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

# ---------------------------------------------------------------------------
# Legacy second-phase tool schema(MARKET_FIRST_ENABLED=false)
# ---------------------------------------------------------------------------

TRADE_ACTION_TOOLS_LEGACY: list[dict[str, Any]] = [
    {
        "name": "place_pending_order",
        "description": (
            "Place a pending order (BUY_LIMIT or SELL_LIMIT) that triggers when price reaches a target level. "
            "Use this when the LLM suggests a precise entry price DIFFERENT from the current market price "
            '(e.g., "等待回调至 4145 入场" — wait for pullback to 4145). '
            "Required when entry_price != current market price. "
            "The order auto-expires in 4 hours if not triggered."
        ),
        "input_schema": {
            "type": "object",
            "required": ["side", "entry_price", "stop_loss", "take_profit_1", "lots", "order_type", "reason"],
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
            "required": ["side", "stop_loss", "take_profit_1", "lots", "reason"],
            "properties": {
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
