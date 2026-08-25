"""EA 兼容路由(镜像 apps/app-server/src/routes/ea.ts + app.ts 的 EA 面辅助)。"""

from __future__ import annotations

import inspect
import json
import math
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.api.http.json import parse_json_object
from backend.api.http.response import JsonResponse, error, ok
from backend.api.middleware.auth import (
    authorize_route_account,
    extract_auth_token,
)
from backend.persistence.store import EaStore

__all__ = [
    "DEFAULT_STRATEGY_MAPPING",
    "EA_LIFECYCLE_LOG_FIELDS",
    "EA_LIFECYCLE_LOG_PREFIX",
    "base_symbol",
    "current_ea_release",
    "ea_download_response",
    "ea_version_check_response",
    "ea_version_response",
    "filter_positions_for_symbol",
    "format_ea_lifecycle_log",
    "handle_ea_route",
    "handle_trade_history",
    "has_invalid_optional_boolean",
    "has_invalid_optional_integer",
    "has_invalid_optional_number",
    "has_invalid_optional_string",
    "has_invalid_optional_string_array",
    "is_record",
    "normalize_bars_payload",
    "normalize_heartbeat_payload",
    "normalize_mt_time",
    "normalize_positions_payload",
    "normalize_register_payload",
    "normalize_tick_payload",
    "resolve_order_class_field",
    "resolve_position_strategy",
    "strategy_from_comment",
    "string_field_or_empty",
    "symbol_default",
    "validate_ea_payload",
]

DEFAULT_STRATEGY_MAPPING: dict[str, str] = {
    "20250231": "pullback",
    "20250232": "breakout_retest",
    "20250233": "divergence",
    "20250234": "breakout_pyramid",
    "20250235": "counter_pullback",
    "20250236": "range",
    # '20250237': 'momentum_scalp', // NOTE: disabled for intraday trading focus
    "20250238": "ai_signal",
}

EA_LIFECYCLE_LOG_PREFIX: dict[str, str] = {
    "register": "[EA-REGISTER]",
    "heartbeat": "[EA-HEARTBEAT]",
    "tick": "[EA-TICK]",
}

EA_LIFECYCLE_LOG_FIELDS: dict[str, list[str]] = {
    "register": [
        "account_id",
        "broker",
        "server_name",
        "account_name",
        "account_type",
        "currency",
        "leverage",
        "max_daily_loss",
        "strategies",
        "ai_symbols",
    ],
    "heartbeat": [
        "account_id",
        "balance",
        "equity",
        "margin",
        "free_margin",
        "market_open",
        "is_trade_allowed",
        "server_time",
        "max_spread",
        "max_daily_loss",
        "ai_symbols",
    ],
    "tick": ["account_id", "symbol", "bid", "ask", "spread", "max_spread", "time"],
}


# ---------------------------------------------------------------- 字段助手(app.ts)


def is_record(value: Any) -> bool:
    return value is not None and isinstance(value, dict) and not isinstance(value, list)


def string_field_or_empty(record: dict, field: str) -> str:
    value = record.get(field)
    return value if isinstance(value, str) else ""


def symbol_default(payload: dict) -> str:
    return payload["symbol"] if isinstance(payload.get("symbol"), str) and len(payload["symbol"]) > 0 else "XAUUSD"


def has_invalid_optional_string(record: dict, fields: list[str]) -> bool:
    return any(record.get(field) is not None and not isinstance(record[field], str) for field in fields)


def has_invalid_optional_number(record: dict, fields: list[str]) -> bool:
    for field in fields:
        value = record.get(field)
        if value is not None and (not isinstance(value, (int, float)) or isinstance(value, bool)):
            return True
        if isinstance(value, (int, float)) and not isinstance(value, bool) and not _is_finite(value):
            return True
    return False


def has_invalid_optional_integer(record: dict, fields: list[str]) -> bool:
    for field in fields:
        value = record.get(field)
        if value is not None and (not isinstance(value, int) or isinstance(value, bool)):
            return True
        if isinstance(value, int) and not isinstance(value, bool) and not _is_finite(value):
            return True
    return False


def has_invalid_optional_boolean(record: dict, fields: list[str]) -> bool:
    return any(record.get(field) is not None and not isinstance(record[field], bool) for field in fields)


def has_invalid_optional_string_array(record: dict, fields: list[str]) -> bool:
    for field in fields:
        value = record.get(field)
        if value is None:
            continue
        if not isinstance(value, list) or any(not isinstance(entry, str) for entry in value):
            return True
    return False


def _is_finite(value: int | float) -> bool:
    try:
        from math import isfinite

        return isfinite(float(value))
    except (TypeError, ValueError):
        return False


def normalize_mt_time(value: Any) -> str:
    """MT4/MT5 的 TimeToStr 输出转 ISO 风格(仅用于计算持仓时长)。"""
    if not isinstance(value, str):
        return ""
    text = value.strip()
    match = re.match(r"^(\d{4})\.(\d{2})\.(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?$", text)
    if match is None:
        return text
    return (
        f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
        f"T{match.group(4)}:{match.group(5)}:{match.group(6) or '00'}Z"
    )


# ---------------------------------------------------------------- 位置/策略解析(app.ts)


def resolve_order_class_field(position: dict) -> str:
    explicit = string_field_or_empty(position, "order_class") or string_field_or_empty(position, "orderClass")
    normalized = explicit.strip().lower()
    if normalized == "pending":
        return "pending"
    if normalized == "market":
        return "market"
    position_type = string_field_or_empty(position, "type").strip().upper()
    if position_type in ("BUY", "SELL"):
        return "market"
    if "LIMIT" in position_type or "STOP" in position_type:
        return "pending"
    return "pending"


def resolve_position_strategy(position: dict) -> str:
    """策略身份只来自 strategy/comment,MagicNumber 用户可改不参与。"""
    existing = string_field_or_empty(position, "strategy").strip()
    if len(existing) > 0 and _is_ea_strategy_name(existing):
        return existing
    from_comment = strategy_from_comment(string_field_or_empty(position, "comment"))
    if len(from_comment) > 0:
        return from_comment
    if len(existing) > 0:
        return existing
    return "unknown"


def _is_ea_strategy_name(value: str) -> bool:
    from backend.shared_contracts import is_ea_strategy_name

    return is_ea_strategy_name(value)


def strategy_from_comment(comment: str) -> str:
    """从 GB_<strategy>_* 注释解析策略;最长匹配优先。"""
    text = comment.strip()
    if not text.startswith("GB_"):
        return ""
    rest = text[3:]
    names = list(dict.fromkeys([*DEFAULT_STRATEGY_MAPPING.values(), "momentum_scalp", "scale_in"]))
    names.sort(key=len, reverse=True)
    for name in names:
        if rest == name or rest.startswith(f"{name}_"):
            return name
    return ""


def base_symbol(symbol: str) -> str:
    normalized = re.sub(r"M#$", "", symbol.strip().upper()).replace("#", "")
    return {
        "GOLD": "XAUUSD",
        "XAUUSD": "XAUUSD",
        "US100": "US100CASH",
        "NAS100": "US100CASH",
        "US100CASH": "US100CASH",
        "USOIL": "USOILCASH",
        "WTI": "USOILCASH",
        "USOILCASH": "USOILCASH",
        "UKOIL": "UKOILCASH",
        "BRENT": "UKOILCASH",
        "UKOILCASH": "UKOILCASH",
    }.get(normalized, normalized)


def filter_positions_for_symbol(symbol: str, positions: list[dict]) -> list[dict]:
    base = base_symbol(symbol)
    return [
        position
        for position in positions
        if len(string_field_or_empty(position, "symbol")) == 0
        or base_symbol(string_field_or_empty(position, "symbol")) == base
    ]


# ---------------------------------------------------------------- 载荷校验(app.ts)


def normalize_register_payload(body: dict) -> str | None:
    if has_invalid_optional_string(
        body, ["broker", "server_name", "account_name", "account_type", "currency"]
    ):
        return "invalid JSON"
    leverage = body.get("leverage")
    if leverage is not None and (not isinstance(leverage, int) or isinstance(leverage, bool)):
        return "invalid JSON"
    if has_invalid_optional_number(body, ["max_daily_loss"]):
        return "invalid JSON"
    mapping = body.get("strategy_mapping")
    if mapping is None:
        return None
    if not isinstance(mapping, dict):
        return "invalid JSON"
    for value in mapping.values():
        if not isinstance(value, str):
            return "invalid JSON"
    return None


def normalize_heartbeat_payload(body: dict) -> str | None:
    if has_invalid_optional_number(
        body, ["balance", "equity", "margin", "free_margin", "max_spread", "max_daily_loss"]
    ):
        return "invalid JSON"
    if has_invalid_optional_string(body, ["server_time"]):
        return "invalid JSON"
    if has_invalid_optional_boolean(body, ["market_open", "is_trade_allowed"]):
        return "invalid JSON"
    body["connected"] = True
    body["market_open"] = body.get("market_open") is True
    body["is_trade_allowed"] = body.get("is_trade_allowed") is True
    return None


def normalize_tick_payload(body: dict) -> str | None:
    if has_invalid_optional_string(body, ["symbol", "time"]):
        return "invalid JSON"
    if has_invalid_optional_number(body, ["bid", "ask", "spread", "max_spread"]):
        return "invalid JSON"
    if len(string_field_or_empty(body, "symbol").strip()) == 0:
        body["symbol"] = "XAUUSD"
    return None


_BAR_NUMBER_FIELDS = [
    "open", "high", "low", "close", "ema20", "ema50", "ema200", "atr", "rsi", "macd", "macd_signal",
    "macd_hist", "adx", "bb_upper", "bb_lower", "bb_mid", "bb_middle", "stoch_k", "stoch_d", "vol_sma",
    "fib_236", "fib_382", "fib_500", "fib_618", "fib_786", "fib_1272", "fib_1618", "fib_2618", "pp",
    "r1", "r2", "s1", "s2",
]


def normalize_bars_payload(body: dict) -> str | None:
    if has_invalid_optional_string(body, ["symbol", "timeframe"]):
        return "invalid JSON"
    if body.get("bars") is None:
        body["bars"] = []
        return None
    if not isinstance(body["bars"], list):
        return "invalid JSON"
    for bar in body["bars"]:
        if not is_record(bar):
            return "invalid JSON"
        if has_invalid_optional_number(bar, _BAR_NUMBER_FIELDS):
            return "invalid JSON"
        if has_invalid_optional_integer(bar, ["volume"]):
            return "invalid JSON"
        if has_invalid_optional_string(bar, ["macd_divergence", "rsi_divergence"]):
            return "invalid JSON"
        if has_invalid_optional_string_array(bar, ["candlestick_patterns"]):
            return "invalid JSON"
        time_value = bar.get("time")
        if time_value is None:
            continue
        if isinstance(time_value, str):
            continue
        if isinstance(time_value, int) and not isinstance(time_value, bool):
            bar["time"] = str(abs(time_value)) if time_value < 0 else str(time_value)
            continue
        return "invalid JSON"
    return None


def latest_bar_time(body: dict) -> str:
    bars = body.get("bars")
    if not isinstance(bars, list) or not bars:
        return ""
    latest = bars[-1]
    if not isinstance(latest, dict):
        return ""
    value = latest.get("time")
    return value.strip() if isinstance(value, str) else ""


async def normalize_positions_payload(body: dict, store: EaStore) -> str | None:
    if has_invalid_optional_string(body, ["symbol"]):
        return "invalid JSON"
    if body.get("positions") is None:
        body["positions"] = []
        return None
    if not isinstance(body["positions"], list):
        return "invalid JSON"
    for position in body["positions"]:
        if not is_record(position):
            return "invalid JSON"
        if has_invalid_optional_integer(position, ["ticket", "open_time", "magic"]):
            return "invalid JSON"
        if has_invalid_optional_number(position, ["lots", "open_price", "sl", "tp", "profit"]):
            return "invalid JSON"
        if has_invalid_optional_string(
            position, ["symbol", "type", "comment", "strategy", "order_class", "orderClass"]
        ):
            return "invalid JSON"
        # 策略身份来自 strategy/comment,MagicNumber 用户可自定义
        position["strategy"] = resolve_position_strategy(position)
        # 显式 order_class 优先,否则按 type 推断(BUY/SELL=market,*LIMIT/*STOP=pending)
        position["order_class"] = resolve_order_class_field(position)
    return None


async def validate_ea_payload(path: str, body: dict, store: EaStore) -> str | None:
    if path == "/register":
        return normalize_register_payload(body)
    if path == "/heartbeat":
        return normalize_heartbeat_payload(body)
    if path == "/tick":
        return normalize_tick_payload(body)
    if path == "/bars":
        return normalize_bars_payload(body)
    if path == "/positions":
        return await normalize_positions_payload(body, store)
    if path == "/order_result":
        if has_invalid_optional_string(body, ["command_id", "result", "error"]):
            return "invalid JSON"
        if len(string_field_or_empty(body, "command_id").strip()) == 0:
            return "missing command_id"
        if len(string_field_or_empty(body, "result").strip()) == 0:
            return "missing result"
        if has_invalid_optional_integer(body, ["ticket"]):
            return "invalid JSON"
        return None
    return None


# ---------------------------------------------------------------- EA 生命周期日志(ea.ts)


def format_ea_lifecycle_log(kind: str, body: dict) -> str:
    fields = EA_LIFECYCLE_LOG_FIELDS[kind]
    parts = [f"{field}={format_ea_lifecycle_field(body, field)}" for field in fields]
    return f"{EA_LIFECYCLE_LOG_PREFIX[kind]} {' '.join(parts)}"


def format_ea_lifecycle_field(body: dict, field: str) -> str:
    if field == "strategies":
        return format_strategy_mapping(body.get("strategy_mapping"))
    return format_log_value(body.get(field))


def format_strategy_mapping(value: Any) -> str:
    if value is None or not isinstance(value, dict):
        return format_log_value(value)
    return ",".join(f"{sanitize_log_text(key)}:{sanitize_log_text(mapped)}" for key, mapped in value.items())


def format_log_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return ",".join(sanitize_log_text(entry) for entry in value)
    if isinstance(value, dict):
        return ",".join(sorted(sanitize_log_text(key) for key in value))
    return sanitize_log_text(value)


_LOG_CRLF_RE = re.compile(r"[\r\n\t]+")


def sanitize_log_text(value: Any) -> str:
    # 镜像 TS sanitizeLogText:String(true) === 'true'
    if isinstance(value, bool):
        text = "true" if value else "false"
    else:
        text = str(value)
    return _LOG_CRLF_RE.sub(" ", text).strip()


# ---------------------------------------------------------------- 实时事件(ea.ts)

_EA_EVENT_SOURCE = "ea"


def _ea_event_id(prefix: str) -> str:
    """镜像 ai 路由的 evt_{prefix}_{ms} 事件 ID(毫秒时间戳)。"""
    import time

    return f"evt_{prefix}_{int(time.time() * 1000)}"


def _ea_event_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _compact_position(position: Any, helpers: dict) -> dict:
    """EA 持仓 → 实时事件用的紧凑摘要(全量数据仍走 REST 详情)。"""
    if not isinstance(position, dict):
        return {}
    ticket = position.get("ticket")
    lots = _ea_event_number(position.get("lots"))
    open_price = _ea_event_number(position.get("open_price"))
    profit = _ea_event_number(position.get("profit"))
    sl = _ea_event_number(position.get("sl"))
    tp = _ea_event_number(position.get("tp"))
    return {
        "ticket": ticket if isinstance(ticket, (int, float)) and not isinstance(ticket, bool) else None,
        "symbol": helpers["string_field_or_empty"](position, "symbol"),
        "strategy": resolve_position_strategy(position),
        "side": str(helpers["string_field_or_empty"](position, "type")).strip().upper(),
        "lots": lots,
        "open_price": open_price,
        "profit": profit,
        "sl": sl,
        "tp": tp,
    }


def _publish_ea_event(
    events: Any,
    *,
    event_type: str,
    account_id: str,
    timestamp: str,
    payload: dict,
) -> None:
    """向事件 hub 发布实时事件(未配置 hub 时静默跳过)。"""
    if events is None:
        return
    events.publish(
        {
            "event_id": _ea_event_id(event_type),
            "event_type": event_type,
            "account_id": account_id,
            "source": _EA_EVENT_SOURCE,
            "timestamp": timestamp,
            "payload": payload,
        }
    )


# ---------------------------------------------------------------- 主路由(ea.ts)


async def handle_ea_route(
    request: dict,
    deps: dict,
    helpers: dict,
) -> JsonResponse:
    """等价 TS handleEaRoute;request={method,path,url,headers,rawBody}。"""
    valid_tokens: set[str] | None = deps["valid_tokens"]
    if valid_tokens is not None:
        token = extract_auth_token(request["headers"], request["url"])
        if token is None or token not in valid_tokens:
            return error(401, "invalid token")

    parsed_ok, parsed_body = parse_json_object(request["rawBody"])
    if not parsed_ok:
        return error(400, "invalid JSON")

    account_id = helpers["string_field_or_empty"](parsed_body, "account_id").strip()
    if len(account_id) == 0:
        return error(400, "missing account_id")

    token = extract_auth_token(request["headers"], request["url"])
    if not authorize_route_account(deps["token_accounts"], token, account_id, deps["admin_tokens"]):
        return error(403, "token not authorized for account")

    validation_error = await helpers["validate_ea_payload"](request["path"], parsed_body, deps["store"])
    if validation_error is not None:
        return error(400, validation_error)

    switch_path = request["path"]
    if switch_path == "/register":
        await deps["store"].save_registration(parsed_body)
        _log_ea_lifecycle(deps.get("log"), "register", parsed_body)
        # 策略映射(EA 上报时推送到实时流)
        _publish_ea_event(
            deps.get("events"),
            event_type="strategy_update",
            account_id=account_id,
            timestamp=deps["now_iso"](),
            payload={
                "account_id": account_id,
                "strategy_mapping": parsed_body.get("strategy_mapping") or {},
            },
        )
        return ok({"status": "OK", "message": "registered"})
    if switch_path == "/heartbeat":
        heartbeat_at = deps["now_iso"]()
        parsed_body["mt4_server_time"] = helpers["string_field_or_empty"](parsed_body, "server_time")
        parsed_body["last_heartbeat_at"] = heartbeat_at
        parsed_body["updated_at"] = heartbeat_at
        await deps["store"].save_heartbeat(parsed_body)
        _log_ea_lifecycle(deps.get("log"), "heartbeat", parsed_body)
        # 账户快照(EQ/余额/连接状态更新时推送到实时流)
        _publish_ea_event(
            deps.get("events"),
            event_type="account_update",
            account_id=account_id,
            timestamp=heartbeat_at,
            payload={
                "account_id": account_id,
                "balance": _ea_event_number(parsed_body.get("balance")),
                "equity": _ea_event_number(parsed_body.get("equity")),
                "margin": _ea_event_number(parsed_body.get("margin")),
                "free_margin": _ea_event_number(parsed_body.get("free_margin")),
                "connected": parsed_body.get("connected") is True,
                "market_open": parsed_body.get("market_open") is True,
                "is_trade_allowed": parsed_body.get("is_trade_allowed") is True,
                "server_time": helpers["string_field_or_empty"](parsed_body, "server_time"),
            },
        )
        return ok({"status": "OK", "server_time": deps["now_unix"]()})
    if switch_path == "/tick":
        received_at = deps["now_iso"]()
        parsed_body["received_at"] = received_at
        parsed_body["updated_at"] = received_at
        await deps["store"].save_tick(parsed_body)
        _log_ea_lifecycle(deps.get("log"), "tick", parsed_body)
        return ok({"status": "OK"})
    if switch_path == "/bars":
        await deps["store"].save_bars(parsed_body)
        on_bars_saved = deps.get("on_bars_saved")
        if on_bars_saved is not None:
            on_bars_saved(
                account_id,
                helpers["symbol_default"](parsed_body),
                helpers["string_field_or_empty"](parsed_body, "timeframe"),
            )
        on_bar_closed = deps.get("on_bar_closed")
        bar_time = latest_bar_time(parsed_body)
        if on_bar_closed is not None and len(bar_time) > 0:
            dispatched = on_bar_closed(
                account_id,
                helpers["symbol_default"](parsed_body),
                helpers["string_field_or_empty"](parsed_body, "timeframe"),
                bar_time,
            )
            if inspect.isawaitable(dispatched):
                await dispatched
        bars = parsed_body.get("bars")
        received = len(bars) if isinstance(bars, list) else 0
        return ok({"status": "OK", "received": received})
    if switch_path == "/positions":
        await deps["store"].save_positions(parsed_body)
        on_positions_saved = deps.get("on_positions_saved")
        if on_positions_saved is not None:
            on_positions_saved(account_id, helpers["symbol_default"](parsed_body))
        # 持仓快照(开/平仓时推送到实时流)
        stored_positions = parsed_body.get("positions")
        raw_positions = stored_positions if isinstance(stored_positions, list) else []
        _publish_ea_event(
            deps.get("events"),
            event_type="positions_update",
            account_id=account_id,
            timestamp=deps["now_iso"](),
            payload={
                "account_id": account_id,
                "symbol": helpers["symbol_default"](parsed_body),
                "count": len(raw_positions),
                "positions": [_compact_position(position, helpers) for position in raw_positions],
            },
        )
        return ok(
            {
                "status": "OK",
                "count": len(parsed_body["positions"]) if isinstance(parsed_body.get("positions"), list) else 0,
            }
        )
    if switch_path == "/poll":
        commands = await deps["store"].poll_commands(account_id)
        return ok({"status": "OK", "commands": commands, "count": len(commands)})
    if switch_path == "/order_result":
        on_order_result = deps.get("on_order_result")
        if on_order_result is not None:
            _fire_on_order_result(
                on_order_result,
                account_id,
                helpers["string_field_or_empty"](parsed_body, "command_id"),
                helpers["string_field_or_empty"](parsed_body, "result"),
                (
                    parsed_body["ticket"]
                    if isinstance(parsed_body.get("ticket"), (int, float))
                    and not isinstance(parsed_body.get("ticket"), bool)
                    else None
                ),
                helpers["string_field_or_empty"](parsed_body, "error"),
                deps["now_iso"](),
            )
        else:
            await deps["store"].save_order_result(parsed_body)
        return ok({"status": "OK"})
    return error(404, "not found")


def _fire_on_order_result(callback: Callable[..., Any], *args: Any) -> None:
    """镜像 TS void deps.onOrderResult?.(...):协程回调 fire-and-forget。"""
    try:
        import asyncio
        import inspect

        outcome = callback(*args)
        if inspect.isawaitable(outcome):

            async def _run(awaitable: Any) -> None:
                await awaitable

            task = asyncio.create_task(_run(outcome))  # fire-and-forget
            del task
    except Exception:  # 回报回调异常不阻断 EA 响应
        pass


def _log_ea_lifecycle(log: Callable[[str], None] | None, kind: str, body: dict) -> None:
    if log is not None:
        log(format_ea_lifecycle_log(kind, body))


# ---------------------------------------------------------------- 已平仓成交(app.ts /api/trade_history)


async def handle_trade_history(
    body: dict,
    store: EaStore,
    metrics: Any = None,
) -> JsonResponse:
    """等价 app.ts POST /api/trade_history:建立 closed_trades 绩效库 + Prometheus 指标。"""
    trades_raw = body if isinstance(body, list) else body.get("trades")
    if not isinstance(trades_raw, list):
        return error(400, "expected array of trades")

    saved = 0
    for raw in trades_raw:
        if raw is None or not isinstance(raw, dict):
            continue
        magic = _ts_number(raw.get("magic"))
        strategy = DEFAULT_STRATEGY_MAPPING.get(_ts_number_string(magic), "unknown")
        open_time = normalize_mt_time(raw.get("open_time"))
        close_time = normalize_mt_time(raw.get("close_time"))
        open_ms = _parse_date_millis(open_time)
        close_ms = _parse_date_millis(close_time)
        if open_ms is not None and close_ms is not None:
            dur_min = max(0, _math_round((close_ms - open_ms) / 60000))
        else:
            dur_min = 0
        side_raw = raw.get("side")
        if side_raw is None:
            side_raw = raw.get("type", "")
        ticket = _ts_number(raw.get("ticket"))
        profit = _ts_number(raw.get("profit"))
        trade = {
            "account_id": _ts_string(raw.get("account_id")),
            "ticket": ticket,
            "magic": magic,
            "symbol": str(raw.get("symbol", "")),
            "strategy": strategy,
            "side": str(side_raw).upper(),
            "open_price": _ts_number(raw.get("open_price")),
            "close_price": _ts_number(raw.get("close_price")),
            "lots": _ts_number(raw.get("lots")),
            "profit": profit,
            "open_time": open_time,
            "close_time": close_time,
            "duration_min": dur_min,
        }
        if ticket <= 0 or trade["account_id"] == "":
            continue
        await store.save_closed_trade(trade)
        if metrics is not None:
            result = "win" if profit > 0 else "loss"
            metrics.orders_total.labels(
                trade["account_id"], trade["symbol"], trade["side"], result, trade["strategy"]
            ).inc()
            metrics.order_profit.labels(
                trade["account_id"], trade["symbol"], trade["strategy"]
            ).observe(trade["profit"])
        saved += 1

    if saved > 0 and metrics is not None:
        account_ids = [_ts_string(raw.get("account_id")) for raw in trades_raw if isinstance(raw, dict)]
        account_ids = list(dict.fromkeys(aid for aid in account_ids if len(aid) > 0))
        for aid in account_ids:
            stats = await store.get_closed_trade_stats(aid)
            for stat in stats:
                metrics.strategy_win_rate.labels(aid, stat["strategy"]).set(stat["win_rate"])
    return ok({"status": "OK", "saved": saved})


def _ts_number(value: Any) -> float | int:
    """等价 TS Number(x) || 0(NaN/null/undefined/空串 → 0;bool → 1/0)。"""
    if value is None:
        return 0
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        try:
            number = float(value)
            return int(number) if number.is_integer() else number
        except (ValueError, TypeError):
            return 0
    return 0


def _ts_number_string(value: float | int) -> str:
    """等价 String(Number(x)):整数值去掉小数点。"""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _ts_string(value: Any) -> str:
    """等价 String(x):None → '',bool → 小写 true/false。"""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _math_round(value: float) -> int:
    """等价 JS Math.round(负数半值向 +∞ 舍入)。"""
    import math

    return math.floor(value + 0.5)


def _parse_date_millis(value: str) -> float | None:
    if len(value) == 0:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000
    except ValueError:
        return None


# ---------------------------------------------------------------- EA 版本/下载(app.ts)


def current_ea_release(release_root: str | Path, platform: str = "mt4") -> dict:
    """等价 currentEaRelease:读 apps/app-mt/{mt4_ea,mt5_ea}/version.json。"""
    fallback = {"version": "0.0.0", "build": 0, "changelog": ""}
    is_mt5 = platform.lower() == "mt5"
    ea_dir = "mt5_ea" if is_mt5 else "mt4_ea"
    version_file = Path(release_root) / "apps" / "app-mt" / ea_dir / "version.json"
    try:
        raw = version_file.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {"ok": True, "info": fallback}
    except OSError as err:
        return {"ok": False, "message": f"read EA version file: {err}"}
    try:
        payload = json.loads(raw)
    except ValueError as err:
        return {"ok": False, "message": f"decode EA version file: {err}"}
    if not isinstance(payload, dict):
        return {"ok": False, "message": "decode EA version file: expected object"}
    info = fallback.copy()
    info["version"] = payload["version"] if isinstance(payload.get("version"), str) else fallback["version"]
    info["build"] = (
        payload["build"]
        if isinstance(payload.get("build"), int) and not isinstance(payload.get("build"), bool)
        else fallback["build"]
    )
    info["changelog"] = (
        payload["changelog"] if isinstance(payload.get("changelog"), str) else fallback["changelog"]
    )
    return {"ok": True, "info": info}


def ea_version_response(release_root: str | Path, platform: str = "mt4") -> JsonResponse:
    release = current_ea_release(release_root, platform)
    if not release["ok"]:
        return error(500, release["message"])
    info = release["info"]
    return ok({"status": "OK", "version": info["version"], "build": info["build"], "changelog": info["changelog"]})


def ea_version_check_response(release_root: str | Path, platform: str = "mt4") -> JsonResponse:
    release = current_ea_release(release_root, platform)
    if not release["ok"]:
        return error(500, release["message"])
    info = release["info"]
    return ok({"latest_version": info["version"], "latest_build": info["build"], "force_update": False})


def ea_download_response(release_root: str | Path, platform: str = "mt4") -> JsonResponse:
    is_mt5 = platform.lower() == "mt5"
    filename = "GoldBolt_Client.mq5" if is_mt5 else "GoldBolt_Client.mq4"
    ea_dir = "mt5_ea" if is_mt5 else "mt4_ea"
    file_path = Path(release_root) / "apps" / "app-mt" / ea_dir / filename
    try:
        payload = file_path.read_bytes()
    except OSError:
        return error(404, "file not found")
    return {
        "statusCode": 200,
        "headers": {"Content-Disposition": f'attachment; filename="{filename}"'},
        "body": None,
        "rawBody": payload,
    }


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()
