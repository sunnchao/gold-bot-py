"""AI 路由(镜像 apps/app-server/src/routes/ai.ts + app.ts 的 AI 面处理)。

analysis_payload(account_symbol 注入)与 handle_ai_result_route(
AI approve 队列、risk command、事件发布、shadow 快照)逐字镜像 gold-bot。
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from backend.api.http.json import parse_strict_json_object
from backend.api.http.response import JsonResponse, error
from backend.api.middleware.auth import (
    authorize_api_account,
    extract_auth_token,
)
from backend.api.routes.ea.index import (
    DEFAULT_STRATEGY_MAPPING,
    resolve_order_class_field,
    resolve_position_strategy,
)
from backend.persistence.records import StoredCommand
from backend.persistence.store import EaStore

__all__ = [
    "AI_APPROVE_QUEUE_MIN_CONFIDENCE",
    "VALID_TRADE_PLAN_MODES",
    "VALID_TRADE_PLAN_SIDES",
    "analysis_payload",
    "handle_ai_route",
    "handle_ai_result_route",
]

# ------------------------------------------------ 常量(app.ts / ai.ts)

VALID_TRADE_PLAN_MODES = {"observe", "veto", "approve", "modify", "reduce", "close"}
VALID_TRADE_PLAN_SIDES = {"buy", "sell", "none"}
AI_RISK_EXIT_SUGGESTIONS = {"close_partial", "close_all", "close_short"}
AI_APPROVE_QUEUE_MIN_CONFIDENCE = 65
ALLOWED_STRATEGY_MAPPING_KEYS = [
    "20250231",
    "20250232",
    "20250233",
    "20250234",
    "20250235",
    "20250236",
    "20250238",
]
ANALYSIS_PAYLOAD_BARS_LIMIT = 1000
MARKET_STATUS_TICK_TTL_MS = 15 * 60 * 1000
MARKET_STATUS_HEARTBEAT_TTL_MS = 15 * 60 * 1000
TIME_ONLY_PATTERN = re.compile(r"^\d{1,2}:\d{2}(?::\d{2})?$")
MT4_WALL_CLOCK_PATTERN = re.compile(r"^(\d{4})\.(\d{2})\.(\d{2})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?)?$")
MAX_TICK_AGE_MS = 10 * 60 * 1000
DAY_MS = 24 * 60 * 60 * 1000


# ------------------------------------------------ 基础字段助手


def _string_field(record: dict, field: str) -> str:
    value = record.get(field)
    return value if isinstance(value, str) else ""


def _optional_number_field(record: dict, field: str) -> float | None:
    value = record.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def number_field(record: dict, field: str) -> float:
    value = record.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return float(value)


def boolean_field(record: dict, field: str) -> bool:
    return record.get(field) is True


def string_array_field(record: dict, field: str) -> list[str]:
    value = record.get(field)
    if not isinstance(value, list):
        return []
    return [entry for entry in value if isinstance(entry, str)]


def record_field(record: dict, field: str) -> dict | None:
    value = record.get(field)
    return value if value is not None and isinstance(value, dict) else None


def _array_number_field(record: dict, field: str) -> list[float]:
    value = record.get(field)
    if not isinstance(value, list):
        return []
    return [float(entry) for entry in value if isinstance(entry, (int, float)) and not isinstance(entry, bool)]


def _parse_date_millis(value: str) -> float | None:
    if len(value) == 0:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000
    except ValueError:
        return None


def _json_type_name(value: Any) -> str:
    if isinstance(value, list):
        return "array"
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _math_floor(value: float) -> int:
    import math

    return math.floor(value)


def _math_round(value: float) -> int:
    import math

    return math.floor(value + 0.5)


def _round2(value: float) -> float:
    # 镜像 TS round2 → roundToEven:银行家舍入(半值取偶数)。仅用于 hold_hours。
    import math

    factor = 100.0
    scaled = value * factor
    floor = math.floor(scaled)
    fraction = scaled - floor
    if abs(fraction - 0.5) < _NUMBER_EPSILON:
        return (float(floor) if floor % 2 == 0 else float(floor + 1)) / factor
    return _math_round(scaled) / factor


_NUMBER_EPSILON = 2.220446049250313e-16


def _round4(value: float) -> float:
    import math

    return math.floor(value * 10000 + 0.5) / 10000


def _pad2(value: int) -> str:
    return str(value).zfill(2)


def _weight_value(item: dict) -> float:
    value = item.get("weight", 0.0)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return float(value)


def _safe_number(value: Any) -> float:
    # 镜像 TS safeNumber(Math.round(v*1e12)/1e12):指标值保留 12 位小数,
    # 消除 ema 等滚动计算的浮点噪声(如 2000.0000000000002 → 2000)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    number = float(value)
    if not _is_finite(number):
        return 0.0
    return _math_round(number * 1_000_000_000_000) / 1_000_000_000_000


def _is_finite(value: float) -> bool:
    import math

    return math.isfinite(value)


# ------------------------------------------------ market status(app.ts)


def _read_positive_ms_env(name: str, fallback: int) -> int:
    import os

    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return fallback
    try:
        parsed = float(raw)
    except ValueError:
        return fallback
    if not _is_finite(parsed) or parsed <= 0:
        return fallback
    return _math_floor(parsed)


def configured_max_spread(latest_tick: dict, heartbeat: dict) -> float | None:
    tick_max = _optional_number_field(latest_tick, "max_spread")
    if tick_max is not None and tick_max > 0:
        return tick_max
    heartbeat_max = _optional_number_field(heartbeat, "max_spread")
    return heartbeat_max if heartbeat_max is not None and heartbeat_max > 0 else None


_MT4_SERVER_TZ = timezone(timedelta(hours=8))


def _parse_wall_clock(value: str) -> float | None:
    """MT4 服务器墙钟(server_time / time-only tick 的 'YYYY.MM.DD HH:MM:SS')解析为绝对 ms。

    镜像 V8 Date.parse 对 '2026.07.07 02:52:52' 宽松格式的接受;Python 侧把墙钟解释为
    MT4 服务器时区(+08:00,与 shanghaiTimestamp 一致;gold-bot 测试也按该时区主机运行),
    因此不依赖运行主机时区。
    """
    match = MT4_WALL_CLOCK_PATTERN.match(value.strip())
    if match is None:
        return None
    year, month, day = (int(match.group(index)) for index in (1, 2, 3))
    hour = int(match.group(4)) if match.group(4) is not None else 0
    minute = int(match.group(5)) if match.group(5) is not None else 0
    second = int(match.group(6)) if match.group(6) is not None else 0
    try:
        return datetime(year, month, day, hour, minute, second, tzinfo=_MT4_SERVER_TZ).timestamp() * 1000
    except ValueError:
        return None


def analysis_heartbeat_freshness_millis(heartbeat: dict) -> float | None:
    for candidate in ["last_heartbeat_at", "updated_at", "received_at"]:
        millis = _parse_date_millis(_string_field(heartbeat, candidate))
        if millis is not None:
            return millis
    server_time = _string_field(heartbeat, "server_time")
    return _parse_date_millis(server_time) or _parse_wall_clock(server_time)


def analysis_tick_freshness_millis(heartbeat: dict, latest_tick: dict, reference_time: float | None) -> float | None:
    for candidate in ["received_at", "updated_at", "last_tick_at"]:
        millis = _parse_date_millis(_string_field(latest_tick, candidate))
        if millis is not None:
            return millis
    return analysis_tick_time_millis(heartbeat, latest_tick, reference_time)


def analysis_tick_time_millis(heartbeat: dict, latest_tick: dict, reference_time: float | None) -> float | None:
    tick_time_str = _string_field(latest_tick, "time")
    if TIME_ONLY_PATTERN.match(tick_time_str) is None:
        return _parse_date_millis(tick_time_str) or _parse_wall_clock(tick_time_str)
    server_time_str = _string_field(heartbeat, "server_time")
    date_part = server_time_str.split(" ")[0]
    if len(date_part) == 0:
        return None
    tick_time = _parse_wall_clock(f"{date_part} {tick_time_str}")
    if tick_time is None:
        return None
    rollover_reference = (
        reference_time
        if reference_time is not None
        else (_parse_date_millis(server_time_str) or _parse_wall_clock(server_time_str))
    )
    if rollover_reference is not None and tick_time - rollover_reference > MAX_TICK_AGE_MS:
        tick_time -= DAY_MS
    return tick_time


def analysis_market_status(heartbeat: dict, latest_tick: dict, timestamp: str) -> dict:
    raw_market_open = boolean_field(heartbeat, "market_open")
    raw_trade_allowed = boolean_field(heartbeat, "is_trade_allowed")
    now = _parse_date_millis(timestamp)
    tick_time = analysis_tick_freshness_millis(heartbeat, latest_tick, now)
    heartbeat_time = analysis_heartbeat_freshness_millis(heartbeat)
    tick_ttl = _read_positive_ms_env("GB_MARKET_STATUS_TICK_TTL_MS", MARKET_STATUS_TICK_TTL_MS)
    heartbeat_ttl = _read_positive_ms_env("GB_MARKET_STATUS_HEARTBEAT_TTL_MS", MARKET_STATUS_HEARTBEAT_TTL_MS)
    tick_age_ms = max(0, now - tick_time) if now is not None and tick_time is not None else None
    heartbeat_age_ms = max(0, now - heartbeat_time) if now is not None and heartbeat_time is not None else None

    def closed(reason: str) -> dict:
        return {
            "marketOpen": False,
            "isTradeAllowed": False,
            "stale": True,
            "staleReason": reason,
            "tickAgeMs": tick_age_ms,
            "heartbeatAgeMs": heartbeat_age_ms,
        }

    if now is None:
        return closed("tick_time_unparseable")
    if tick_time is None and heartbeat_time is None:
        return closed("tick_time_unparseable")
    if tick_age_ms is not None and tick_age_ms > tick_ttl:
        return closed("tick_stale")
    if tick_time is None and heartbeat_age_ms is not None and heartbeat_age_ms > heartbeat_ttl:
        return closed("heartbeat_stale")
    if heartbeat_age_ms is not None and heartbeat_age_ms > heartbeat_ttl:
        return closed("heartbeat_stale")
    if tick_time is None:
        has_price = (_optional_number_field(latest_tick, "bid") or 0.0) > 0 or (
            _optional_number_field(latest_tick, "ask") or 0.0
        ) > 0
        if not has_price or heartbeat_time is None:
            return closed("tick_time_unparseable")
    return {
        "marketOpen": raw_market_open,
        "isTradeAllowed": raw_trade_allowed,
        "stale": False,
        "tickAgeMs": tick_age_ms,
        "heartbeatAgeMs": heartbeat_age_ms,
    }


# ------------------------------------------------ bars 富化(app.ts)


def flatten_bar_records(records: list[dict]) -> list[dict]:
    out: list[dict] = []
    for record in records:
        inner = record.get("bars")
        if isinstance(inner, list):
            out.extend(inner)
        else:
            out.append(record)
    return out


def _rolling_mean(values: list[float], period: int) -> list[float]:
    out = [float("nan")] * len(values)
    if period <= 0:
        return out
    for index in range(period - 1, len(values)):
        total = 0.0
        valid = 0
        for cursor in range(index - period + 1, index + 1):
            if values[cursor] != values[cursor]:  # NaN
                continue
            total += values[cursor]
            valid += 1
        if valid == period:
            out[index] = total / period
    return out


def enrich_analysis_bars(bars: list[dict]) -> list[dict]:
    from backend.trading_core.indicators.index import (
        adx,
        atr,
        bollinger,
        calculate_fib_extension,
        ema,
        fibonacci,
        macd,
        pivot_points,
        rsi,
        stoch,
    )

    out = [{**bar} for bar in bars]
    if len(out) == 0:
        return out
    close = [number_field(bar, "close") for bar in out]
    high = [number_field(bar, "high") for bar in out]
    low = [number_field(bar, "low") for bar in out]
    volume = [number_field(bar, "volume") for bar in out]
    ema20 = ema(close, 20)
    ema50 = ema(close, 50)
    ema200 = ema(close, 200)
    atr14 = atr(high, low, close, 14)
    rsi14 = rsi(close, 14)
    macd_result = macd(close)
    adx14 = adx(high, low, close, 14)
    bb = bollinger(close, 20, 2)
    stochastic = stoch(high, low, close, 14, 3)
    vol_sma = _rolling_mean(volume, 20)

    for index in range(len(out)):
        out[index]["ema20"] = _safe_number(ema20[index])
        out[index]["ema50"] = _safe_number(ema50[index])
        out[index]["ema200"] = _safe_number(ema200[index]) if len(out) >= 200 else 0.0
        out[index]["atr"] = _safe_number(atr14[index])
        out[index]["rsi"] = _safe_number(rsi14[index])
        out[index]["macd"] = _safe_number(macd_result["macd"][index])
        out[index]["macd_signal"] = _safe_number(macd_result["signal"][index])
        out[index]["macd_hist"] = _safe_number(macd_result["histogram"][index])
        out[index]["adx"] = _safe_number(adx14[index])
        out[index]["bb_upper"] = _safe_number(bb["upper"][index])
        out[index]["bb_middle"] = _safe_number(bb["mid"][index])
        out[index]["bb_lower"] = _safe_number(bb["lower"][index])
        out[index]["stoch_k"] = _safe_number(stochastic["k"][index])
        out[index]["stoch_d"] = _safe_number(stochastic["d"][index])
        out[index]["vol_sma"] = _safe_number(vol_sma[index])

        start = max(0, index - 49)
        window_high = high[start : index + 1]
        window_low = low[start : index + 1]
        fib = fibonacci(window_high, window_low, len(window_high))
        out[index]["fib_236"] = _safe_number(fib["fib236"])
        out[index]["fib_382"] = _safe_number(fib["fib382"])
        out[index]["fib_500"] = _safe_number(fib["fib500"])
        out[index]["fib_618"] = _safe_number(fib["fib618"])
        out[index]["fib_786"] = _safe_number(fib["fib786"])

        swing_high = max(window_high) if len(window_high) > 0 else 0.0
        swing_low = min(window_low) if len(window_low) > 0 else 0.0
        trend = "UP" if number_field(out[index], "close") > number_field(out[index], "open") else "DOWN"
        extension = calculate_fib_extension(swing_high, swing_low, trend)
        out[index]["fib_1272"] = _safe_number(extension["level1272"])
        out[index]["fib_1618"] = _safe_number(extension["level1618"])
        out[index]["fib_2618"] = _safe_number(extension["level2618"])

        if index > 0:
            pivots = pivot_points(high[index - 1], low[index - 1], close[index - 1])
            out[index]["pp"] = _safe_number(pivots["pp"])
            out[index]["r1"] = _safe_number(pivots["r1"])
            out[index]["s1"] = _safe_number(pivots["s1"])
    return out


def analysis_bars_by_timeframe(store: EaStore, account_id: str, symbol: str) -> dict[str, list[dict]]:
    return {
        "M15": flatten_bar_records(store.get_bars(account_id, symbol, "M15") or []),  # type: ignore[arg-type]
        "M30": flatten_bar_records(store.get_bars(account_id, symbol, "M30") or []),  # type: ignore[arg-type]
        "H1": flatten_bar_records(store.get_bars(account_id, symbol, "H1") or []),  # type: ignore[arg-type]
        "H4": flatten_bar_records(store.get_bars(account_id, symbol, "H4") or []),  # type: ignore[arg-type]
    }


# ------------------------------------------------ 指标包/趋势上下文(app.ts)


def indicator_packs(bars_by_timeframe: dict[str, list[dict]]) -> dict[str, dict | None]:
    out: dict[str, dict | None] = {}
    for timeframe in ["M15", "M30", "H1", "H4"]:
        bars = bars_by_timeframe.get(timeframe) or []
        if len(bars) < 20:
            out[timeframe] = None
            continue
        last = bars[-1]
        fields = [
            "close",
            "open",
            "high",
            "low",
            "ema20",
            "ema50",
            "ema200",
            "rsi",
            "adx",
            "atr",
            "macd",
            "macd_signal",
            "macd_hist",
            "bb_upper",
            "bb_middle",
            "bb_lower",
            "stoch_k",
            "stoch_d",
            "vol_sma",
            "fib_236",
            "fib_382",
            "fib_500",
            "fib_618",
            "fib_786",
            "fib_1272",
            "fib_1618",
            "fib_2618",
            "pp",
            "r1",
            "s1",
        ]
        pack: dict[str, Any] = {field: number_field(last, field) for field in fields}
        pack["bars_count"] = len(bars)
        out[timeframe] = pack
    return out


def direction_from_bars(bars: list[dict]) -> str:
    if len(bars) == 0:
        return "NEUTRAL"
    last = bars[-1]
    ema20_value = number_field(last, "ema20")
    ema50_value = number_field(last, "ema50")
    close = number_field(last, "close")
    if ema20_value > ema50_value and close > ema20_value:
        return "BULL"
    if ema20_value < ema50_value and close < ema20_value:
        return "BEAR"
    return "NEUTRAL"


def _trend_confidence(adx_value: float) -> float:
    if adx_value < 20:
        return 0.3
    if adx_value <= 30:
        return 0.6
    return 0.9


def consensus_strength(weights: list[dict], bars_by_timeframe: dict[str, list[dict]]) -> float:
    timeframe_by_index = ["D1", "H4", "H1", "M30"]
    total = 0.0
    for index, item in enumerate(weights):
        if item["direction"] == "NEUTRAL":
            continue
        bars = bars_by_timeframe.get(timeframe_by_index[index]) or []
        last = bars[-1] if len(bars) > 0 else {}
        total += item["weight"] * _trend_confidence(number_field(last, "adx"))
    return total


def trend_context(bars_by_timeframe: dict[str, list[dict]]) -> dict:
    d1_direction = direction_from_bars(bars_by_timeframe.get("D1") or [])
    h4_direction = direction_from_bars(bars_by_timeframe.get("H4") or [])
    h1_direction = direction_from_bars(bars_by_timeframe.get("H1") or [])
    m30_direction = direction_from_bars(bars_by_timeframe.get("M30") or [])
    weights = [
        {"direction": d1_direction, "weight": 0.05},
        {"direction": h4_direction, "weight": 0.25},
        {"direction": h1_direction, "weight": 0.35},
        {"direction": m30_direction, "weight": 0.35},
    ]
    # TS: weights.filter((item) => item.direction === 'BULL').reduce((sum, item) => sum + item.weight, 0)
    bull_items = [item for item in weights if item["direction"] == "BULL"]
    bear_items = [item for item in weights if item["direction"] == "BEAR"]
    bull_weight = sum(_weight_value(item) for item in bull_items)
    bear_weight = sum(_weight_value(item) for item in bear_items)
    if bull_weight > bear_weight:
        consensus_direction = "BULL"
    elif bear_weight > bull_weight:
        consensus_direction = "BEAR"
    else:
        consensus_direction = "NEUTRAL"
    return {
        "d1_direction": d1_direction,
        "h4_direction": h4_direction,
        "h1_direction": h1_direction,
        "m30_direction": m30_direction,
        "consensus_direction": consensus_direction,
        "consensus_strength": _round4(consensus_strength(weights, bars_by_timeframe)),
    }


# ------------------------------------------------ 形态上下文(app.ts)


def to_smc_bars(records: list[dict]) -> list[dict]:
    return [
        {
            "high": number_field(record, "high"),
            "low": number_field(record, "low"),
            "close": number_field(record, "close"),
            "open": number_field(record, "open"),
        }
        for record in records
    ]


def to_candle_bars(records: list[dict]) -> list[dict]:
    return [
        {
            "high": number_field(record, "high"),
            "low": number_field(record, "low"),
            "close": number_field(record, "close"),
            "open": number_field(record, "open"),
            "ema50": record.get("ema50") if isinstance(record.get("ema50"), (int, float)) else None,
            "atr": record.get("atr") if isinstance(record.get("atr"), (int, float)) else None,
        }
        for record in records
    ]


def build_smc_context_payload(bars_by_timeframe: dict[str, list[dict]]) -> dict | None:
    from backend.trading_core.smc.detector import build_smc_context

    h4_bars = to_smc_bars(bars_by_timeframe.get("H4") or [])
    h1_bars = to_smc_bars(bars_by_timeframe.get("H1") or [])
    m30_bars = to_smc_bars(bars_by_timeframe.get("M30") or [])
    if len(h4_bars) == 0 and len(h1_bars) == 0 and len(m30_bars) == 0:
        return None
    context = build_smc_context(h4_bars, h1_bars, m30_bars)

    def normalize_ob(ob: Any) -> dict:
        return {
            "index": ob["index"],
            "side": ob["side"],
            "high": ob["high"],
            "low": ob["low"],
            "valid": ob["valid"],
            "mitigated": ob["mitigated"],
            "age_bars": ob["ageBars"],
        }

    def normalize_fvg(fvg: Any) -> dict:
        return {
            "start_index": fvg["startIndex"],
            "end_index": fvg["endIndex"],
            "side": fvg["side"],
            "upper_bound": fvg["upperBound"],
            "lower_bound": fvg["lowerBound"],
            "filled": fvg["filled"],
            "fill_index": fvg["fillIndex"],
        }

    def normalize_break(sb: Any) -> dict:
        return {"index": sb["index"], "direction": sb["direction"], "level": sb["level"], "type": sb["type"]}

    def normalize_sweep(sweep: Any) -> dict:
        return {
            "index": sweep["index"],
            "level": sweep["level"],
            "side": sweep["side"],
            "reversed": sweep["reversed"],
        }

    return {
        "h4_obs": [normalize_ob(ob) for ob in context["h4OBs"]],
        "h1_obs": [normalize_ob(ob) for ob in context["h1OBs"]],
        "h1_short_obs": [normalize_ob(ob) for ob in context["h1ShortOBs"]],
        "h4_fvgs": [normalize_fvg(fvg) for fvg in context["h4FVGs"]],
        "h1_fvgs": [normalize_fvg(fvg) for fvg in context["h1FVGs"]],
        "h4_breaks": [normalize_break(sb) for sb in context["h4Breaks"]],
        "h1_breaks": [normalize_break(sb) for sb in context["h1Breaks"]],
        "h4_sweeps": [normalize_sweep(sweep) for sweep in context["h4Sweeps"]],
        "h1_sweeps": [normalize_sweep(sweep) for sweep in context["h1Sweeps"]],
        "h4_trend_direction": context["h4TrendDirection"],
        "h1_trend_direction": context["h1TrendDirection"],
    }


def build_harmonic_context_payload(bars_by_timeframe: dict[str, list[dict]]) -> dict | None:
    from backend.trading_core.harmonic.detector import build_context

    h4_bars = bars_by_timeframe.get("H4") or []
    h1_bars = bars_by_timeframe.get("H1") or []
    m30_bars = bars_by_timeframe.get("M30") or []
    if len(h4_bars) == 0 and len(h1_bars) == 0 and len(m30_bars) == 0:
        return None
    h4 = [
        {
            "high": number_field(b, "high"),
            "low": number_field(b, "low"),
            "close": number_field(b, "close"),
            "open": number_field(b, "open"),
        }
        for b in h4_bars
    ]
    h1 = [
        {
            "high": number_field(b, "high"),
            "low": number_field(b, "low"),
            "close": number_field(b, "close"),
            "open": number_field(b, "open"),
        }
        for b in h1_bars
    ]
    m30 = [
        {
            "high": number_field(b, "high"),
            "low": number_field(b, "low"),
            "close": number_field(b, "close"),
            "open": number_field(b, "open"),
        }
        for b in m30_bars
    ]
    context = build_context(h4, h1, m30)

    def normalize_pattern(pattern: Any) -> dict:
        return {
            "type": pattern["type"],
            "direction": pattern["direction"],
            "timeframe": pattern["timeframe"],
            "status": pattern["status"],
            "x_index": pattern["xIndex"],
            "a_index": pattern["aIndex"],
            "b_index": pattern["bIndex"],
            "c_index": pattern["cIndex"],
            "d_index": pattern["dIndex"],
            "x_price": pattern["xPrice"],
            "a_price": pattern["aPrice"],
            "b_price": pattern["bPrice"],
            "c_price": pattern["cPrice"],
            "d_price": pattern["dPrice"],
            "ab_ratio": pattern["abRatio"],
            "bc_ratio": pattern["bcRatio"],
            "cd_ratio": pattern["cdRatio"],
            "xd_ratio": pattern["xdRatio"],
            "prz_low": pattern["przLow"],
            "prz_high": pattern["przHigh"],
            "stop_loss": pattern["stopLoss"],
            "target_1": pattern["target1"],
            "target_2": pattern["target2"],
            "invalidated": pattern["invalidated"],
            "score": pattern["score"],
            "confidence": pattern["confidence"],
            "reason": pattern["reason"],
        }

    active = context["activePattern"]
    return {
        "h4_patterns": [normalize_pattern(pattern) for pattern in context["h4Patterns"]],
        "h1_patterns": [normalize_pattern(pattern) for pattern in context["h1Patterns"]],
        "m30_patterns": [normalize_pattern(pattern) for pattern in context["m30Patterns"]],
        "active_pattern": normalize_pattern(active) if active is not None else None,
        "direction_bias": context["directionBias"],
        "score": context["score"],
        "summary": context["summary"],
    }


def build_candlestick_patterns_payload(bars_by_timeframe: dict[str, list[dict]]) -> dict:
    from backend.trading_core.indicators.candlestick import detect_all_candlestick_patterns

    h4_bars = to_candle_bars(bars_by_timeframe.get("H4") or [])
    h1_bars = to_candle_bars(bars_by_timeframe.get("H1") or [])
    m30_bars = to_candle_bars(bars_by_timeframe.get("M30") or [])
    h4_patterns = detect_all_candlestick_patterns(h4_bars, len(h4_bars) - 1) if len(h4_bars) > 0 else []
    h1_patterns = detect_all_candlestick_patterns(h1_bars, len(h1_bars) - 1) if len(h1_bars) > 0 else []
    m30_patterns = detect_all_candlestick_patterns(m30_bars, len(m30_bars) - 1) if len(m30_bars) > 0 else []
    return {"h4": h4_patterns, "h1": h1_patterns, "m30": m30_patterns}


# ------------------------------------------------ 策略映射/位置归一化(app.ts)


def analysis_strategy_mapping(mapping: dict) -> dict:
    from backend.shared_contracts import is_ea_strategy_name

    out: dict = {}
    for key in ALLOWED_STRATEGY_MAPPING_KEYS:
        value = mapping.get(key)
        if isinstance(value, str) and is_ea_strategy_name(value):
            out[key] = value
    return out


def pnl_percent(profit: float, entry_price: float, lots: float) -> float:
    if entry_price == 0 or lots == 0:
        return 0.0
    return _round4((profit / (entry_price * lots)) * 100)


def hold_seconds_from_open_time(open_time: float, timestamp: str) -> int:
    if open_time <= 0:
        return 0
    now_millis = _parse_date_millis(timestamp)
    if now_millis is None:
        return 0
    return _math_floor(now_millis / 1000) - _math_floor(open_time)


def normalize_analysis_position(position: dict, latest_tick: dict, timestamp: str) -> dict:
    type_text = _string_field(position, "type")
    current_price = number_field(latest_tick, "ask")
    entry_price = number_field(position, "entry_price") or number_field(position, "open_price")
    profit = number_field(position, "profit")
    lots = number_field(position, "lots")
    hold_seconds = hold_seconds_from_open_time(number_field(position, "open_time"), timestamp)
    order_class = resolve_order_class_field(position)
    return {
        "comment": _string_field(position, "comment"),
        "current_price": current_price,
        "direction": type_text.strip().upper(),
        "entry_price": entry_price,
        "hold_hours": _round2(hold_seconds / 3600),
        "hold_seconds": hold_seconds,
        "lots": lots,
        "magic": number_field(position, "magic"),
        "order_class": order_class,
        "pnl_percent": number_field(position, "pnl_percent") or pnl_percent(profit, entry_price, lots),
        "profit": profit,
        "sl": number_field(position, "sl"),
        "strategy": resolve_position_strategy(position),
        "ticket": number_field(position, "ticket"),
        "tp": number_field(position, "tp"),
    }


def shanghai_timestamp(timestamp: str) -> str:
    millis = _parse_date_millis(timestamp)
    if millis is None:
        return timestamp

    shifted = datetime.fromtimestamp((millis + 8 * 3600 * 1000) / 1000, tz=UTC)
    return (
        f"{shifted.year:04d}-{_pad2(shifted.month)}-{_pad2(shifted.day)}"
        f"T{_pad2(shifted.hour)}:{_pad2(shifted.minute)}:{_pad2(shifted.second)}+08:00"
    )


def account_connected(heartbeat: dict) -> bool:
    if isinstance(heartbeat.get("connected"), bool):
        return heartbeat["connected"]
    return len(heartbeat) > 0


# ------------------------------------------------ analysis payload(app.ts)


async def analysis_payload(store: EaStore, account_id: str, symbol: str, timestamp: str) -> dict:
    registration = (await store.get_registration(account_id)) or {}
    heartbeat = (await store.get_heartbeat(account_id)) or {}
    latest_tick = (await store.get_latest_tick(account_id, symbol)) or {}
    positions = await store.get_positions(account_id, symbol)
    bars_by_timeframe = {
        "M15": flatten_bar_records(await store.get_bars(account_id, symbol, "M15")),
        "M30": flatten_bar_records(await store.get_bars(account_id, symbol, "M30")),
        "H1": flatten_bar_records(await store.get_bars(account_id, symbol, "H1")),
        "H4": flatten_bar_records(await store.get_bars(account_id, symbol, "H4")),
    }
    enriched = {tf: enrich_analysis_bars(bars) for tf, bars in bars_by_timeframe.items()}
    payload_bars = {
        tf: (bars[-ANALYSIS_PAYLOAD_BARS_LIMIT:] if len(bars) > ANALYSIS_PAYLOAD_BARS_LIMIT else bars)
        for tf, bars in enriched.items()
    }
    market_status = analysis_market_status(heartbeat, latest_tick, timestamp)
    d1_bars = await store.get_bars(account_id, symbol, "D1")
    trend_bars = {**enriched, "D1": enrich_analysis_bars(d1_bars)}
    harmonic_context = build_harmonic_context_payload(enriched)
    smc_context = build_smc_context_payload(enriched)
    candlestick_patterns = build_candlestick_patterns_payload(enriched)
    max_spread = configured_max_spread(latest_tick, heartbeat)
    strategy_mapping = analysis_strategy_mapping(
        {**DEFAULT_STRATEGY_MAPPING, **(record_field(registration, "strategy_mapping") or {})}
    )
    return {
        "account": {
            "account_id": account_id,
            "balance": number_field(heartbeat, "balance"),
            "broker": _string_field(registration, "broker"),
            "connected": account_connected(heartbeat),
            "currency": _string_field(registration, "currency") or "USD",
            "equity": number_field(heartbeat, "equity"),
            "free_margin": number_field(heartbeat, "free_margin"),
            "leverage": number_field(registration, "leverage"),
            "margin": number_field(heartbeat, "margin"),
            "server_name": _string_field(registration, "server_name"),
        },
        "bars": payload_bars,
        "harmonic_context": harmonic_context,
        "smc_context": smc_context,
        "candlestick_patterns": candlestick_patterns,
        "indicators": indicator_packs(enriched),
        "market": {
            "ask": number_field(latest_tick, "ask"),
            "bid": number_field(latest_tick, "bid"),
            "spread": number_field(latest_tick, "spread"),
            "max_spread": max_spread,
            "symbol": symbol,
            "time": _string_field(latest_tick, "time"),
        },
        "market_filters": evaluate_market_filters_payload(
            heartbeat, latest_tick, payload_bars, max_spread, symbol, timestamp
        ),
        "market_status": {
            "is_trade_allowed": market_status["isTradeAllowed"],
            "market_open": market_status["marketOpen"],
            "mt4_server_time": _string_field(heartbeat, "server_time"),
            "tradeable": market_status["marketOpen"] and market_status["isTradeAllowed"],
            "stale": market_status["stale"],
            # 镜像 JSON.stringify:undefined 键(非 stale 分支)不输出
            **(
                {"stale_reason": market_status["staleReason"]}
                if market_status.get("staleReason") is not None
                else {}
            ),
            "tick_age_ms": market_status["tickAgeMs"],
            "heartbeat_age_ms": market_status["heartbeatAgeMs"],
        },
        "positions": [normalize_analysis_position(position, latest_tick, timestamp) for position in positions],
        "status": "OK",
        "strategy_mapping": strategy_mapping,
        "timestamp": shanghai_timestamp(timestamp),
        "trend_context": trend_context(trend_bars),
    }


def evaluate_market_filters_payload(
    heartbeat: dict,
    latest_tick: dict,
    bars_by_timeframe: dict[str, list[dict]],
    max_spread: float | None,
    symbol: str,
    timestamp: str,
) -> dict:
    from backend.trading_core.riskgate.riskgate import evaluate_market_filters

    return evaluate_market_filters(
        {
            "now": timestamp,
            "symbol": symbol,
            "runtime": {
                "marketOpen": boolean_field(heartbeat, "market_open"),
                "isTradeAllowed": boolean_field(heartbeat, "is_trade_allowed"),
                "lastTickAt": _string_field(latest_tick, "time"),
            },
            "state": {
                "tick": {
                    "symbol": symbol,
                    "spread": number_field(latest_tick, "spread"),
                    "maxSpread": max_spread,
                },
                "bars": bars_by_timeframe,
            },
        }
    )


# ------------------------------------------------ trade plan 校验(app.ts)


def validate_trade_plan(trade_plan: dict, expected_account_id: str, expected_symbol: str) -> str | None:
    schema_version = _string_field(trade_plan, "schema_version")
    if schema_version != "trade_plan.v1":
        return f'trade_plan.schema_version = {_js_json(schema_version)}, want "trade_plan.v1"'

    decision_id = _string_field(trade_plan, "decision_id")
    if len(decision_id) == 0:
        return "trade_plan.decision_id is required"

    account_id = _string_field(trade_plan, "account_id")
    if len(account_id) == 0:
        return "trade_plan.account_id is required"
    if len(expected_account_id) > 0 and account_id != expected_account_id:
        return f"trade_plan.account_id = {_js_json(account_id)}, want {_js_json(expected_account_id)}"

    symbol = _string_field(trade_plan, "symbol")
    if len(symbol) == 0:
        return "trade_plan.symbol is required"
    if len(expected_symbol) > 0 and symbol.upper() != expected_symbol.upper():
        return f"trade_plan.symbol = {_js_json(symbol)}, want {_js_json(expected_symbol)}"

    mode = _string_field(trade_plan, "mode")
    if mode not in VALID_TRADE_PLAN_MODES:
        return f"trade_plan.mode = {_js_json(mode)} is invalid"

    side = _string_field(trade_plan, "side")
    if side not in VALID_TRADE_PLAN_SIDES:
        return f"trade_plan.side = {_js_json(side)} is invalid"

    confidence_decoded = trade_plan.get("confidence")
    if confidence_decoded is not None:
        try:
            decoded = _decode_int_field_error(trade_plan, "confidence", "TradePlan.confidence")
            if decoded is not None:
                return decoded
        except RecursionError:
            return None
    confidence = number_field(trade_plan, "confidence")
    if not _is_integer_float(confidence) or confidence < 0 or confidence > 100:
        return f"trade_plan.confidence = {confidence}, want 0..100"

    expires_at_decoded = trade_plan.get("expires_at")
    if expires_at_decoded is not None:
        time_error = _decode_time_field_error(trade_plan, "expires_at")
        if time_error is not None:
            return time_error
    expires_at = _string_field(trade_plan, "expires_at")
    if len(expires_at) == 0 or _parse_date_millis(expires_at) is None:
        return "trade_plan.expires_at is required"

    reason_codes = trade_plan.get("reason_codes")
    if trade_plan.get("reason_codes") is not None:
        decoded = _decode_string_array_field_error(trade_plan, "reason_codes", "TradePlan.reason_codes")
        if decoded is not None:
            return decoded
    if not isinstance(reason_codes, list) or len(reason_codes) == 0:
        return "trade_plan.reason_codes must not be empty"
    for code in reason_codes:
        if not isinstance(code, str) or code.strip() == "":
            return "trade_plan.reason_codes contains an empty code"

    if _string_field(trade_plan, "narrative").strip() == "":
        return "trade_plan.narrative is required"

    if trade_plan.get("add_on") is not None:
        decoded = _decode_bool_field_error(trade_plan, "add_on", "TradePlan.add_on")
        if decoded is not None:
            return decoded

    if mode in ("observe", "veto"):
        return None

    if side == "none":
        return "active trade_plan.side must be buy or sell"

    entry_zone = record_field(trade_plan, "entry_zone")
    entry_zone_error = _decode_entry_zone_error(entry_zone)
    if entry_zone_error is not None:
        return entry_zone_error
    entry_min = 0.0 if entry_zone is None else number_field(entry_zone, "min")
    entry_max = 0.0 if entry_zone is None else number_field(entry_zone, "max")
    if entry_min <= 0 or entry_max <= 0:
        return "active trade_plan.entry_zone must be positive"
    if entry_min > entry_max:
        return "trade_plan.entry_zone.min must be <= max"

    stop_loss_error = _decode_float_field_error(trade_plan, "stop_loss", "TradePlan.stop_loss")
    if stop_loss_error is not None:
        return stop_loss_error
    if number_field(trade_plan, "stop_loss") <= 0:
        return "active trade_plan.stop_loss must be positive"

    take_profit = trade_plan.get("take_profit")
    take_profit_error = _decode_float_array_field_error(trade_plan, "take_profit", "TradePlan.take_profit")
    if take_profit_error is not None:
        return take_profit_error
    if not isinstance(take_profit, list) or len(take_profit) == 0:
        return "active trade_plan.take_profit must not be empty"
    for target in take_profit:
        if (
            isinstance(target, bool)
            or not isinstance(target, (int, float))
            or not _is_finite(float(target))
            or target <= 0
        ):
            return "active trade_plan.take_profit must contain only positive values"

    max_lots_error = _decode_float_field_error(trade_plan, "max_lots", "TradePlan.max_lots")
    if max_lots_error is not None:
        return max_lots_error
    if number_field(trade_plan, "max_lots") <= 0:
        return "active trade_plan.max_lots must be positive"

    return None


def _js_json(value: Any) -> str:
    return json_dumps_compact(value)


def json_dumps_compact(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _is_integer_float(value: float) -> bool:
    import math

    return math.isfinite(value) and value.is_integer()


def _decode_error(go_field_path: str, go_type: str, value: Any, reason: str | None = None) -> str:
    prefix = "decode trade_plan: json: cannot unmarshal"
    if reason is not None:
        return f"{prefix} number {value} into Go struct field {go_field_path} of type {go_type}"
    return f"{prefix} {_json_type_name(value)} into Go struct field {go_field_path} of type {go_type}"


def _decode_int_field_error(record: dict, field: str, go_field_path: str) -> str | None:
    value = record.get(field)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return _decode_error(go_field_path, "int", value)
    if not _is_finite(float(value)) or not _is_integer_float(float(value)):
        return _decode_error(go_field_path, "int", value, reason="number")
    return None


def _decode_float_field_error(record: dict, field: str, go_field_path: str) -> str | None:
    value = record.get(field)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return _decode_error(go_field_path, "float64", value)
    if not _is_finite(float(value)):
        return _decode_error(go_field_path, "float64", value, reason="number")
    return None


def _decode_float_array_field_error(record: dict, field: str, go_field_path: str) -> str | None:
    value = record.get(field)
    if value is None:
        return None
    if not isinstance(value, list):
        return _decode_error(go_field_path, "float64", value)
    for entry in value:
        if isinstance(entry, bool) or not isinstance(entry, (int, float)):
            return _decode_error(go_field_path, "float64", entry)
        if not _is_finite(float(entry)):
            return _decode_error(go_field_path, "float64", entry, reason="number")
    return None


def _decode_string_array_field_error(record: dict, field: str, go_field_path: str) -> str | None:
    value = record.get(field)
    if value is None:
        return None
    if not isinstance(value, list):
        return _decode_error(go_field_path, "[]string", value)
    for entry in value:
        if not isinstance(entry, str):
            return _decode_error(go_field_path, "string", entry)
    return None


def _decode_bool_field_error(record: dict, field: str, go_field_path: str) -> str | None:
    value = record.get(field)
    if value is None:
        return None
    if not isinstance(value, bool):
        return _decode_error(go_field_path, "bool", value)
    return None


def _decode_entry_zone_error(entry_zone: dict | None) -> str | None:
    if entry_zone is None:
        return None
    min_error = _decode_float_field_error(entry_zone, "min", "TradePlan.entry_zone.min")
    if min_error is not None:
        return min_error
    return _decode_float_field_error(entry_zone, "max", "TradePlan.entry_zone.max")


def _decode_time_field_error(record: dict, field: str) -> str | None:
    value = record.get(field)
    if value is None:
        return None
    if not isinstance(value, str):
        return "decode trade_plan: Time.UnmarshalJSON: input is not a JSON string"
    return None


# ------------------------------------------------ risk gate(app.ts)


async def ai_trade_plan_risk_gate(store: EaStore, account_id: str, symbol: str, trade_plan: dict, now: str) -> dict:
    from backend.trading_core.riskgate.riskgate import evaluate_risk_gate

    registration = (await store.get_registration(account_id)) or {}
    heartbeat = (await store.get_heartbeat(account_id)) or {}
    latest_tick = (await store.get_latest_tick(account_id, symbol)) or {}
    positions = await store.get_positions(account_id, symbol)
    result = evaluate_risk_gate(
        {
            "now": now,
            "account": {
                "accountId": account_id,
                "leverage": number_field(registration, "leverage"),
            },
            "runtime": {
                "equity": number_field(heartbeat, "equity"),
                "freeMargin": number_field(heartbeat, "free_margin"),
                "marketOpen": boolean_field(heartbeat, "market_open"),
                "isTradeAllowed": boolean_field(heartbeat, "is_trade_allowed"),
                "lastTickAt": _string_field(latest_tick, "time"),
            },
            "state": {
                "tick": {
                    "symbol": symbol,
                    "bid": number_field(latest_tick, "bid"),
                    "ask": number_field(latest_tick, "ask"),
                    "spread": number_field(latest_tick, "spread"),
                    "maxSpread": configured_max_spread(latest_tick, heartbeat),
                },
                "positions": [
                    {
                        "ticket": number_field(position, "ticket"),
                        "symbol": _string_field(position, "symbol"),
                        "type": _string_field(position, "type"),
                        "lots": number_field(position, "lots"),
                        "strategy": _string_field(position, "strategy"),
                    }
                    for position in positions
                ],
            },
            "plan": {
                "decisionId": _string_field(trade_plan, "decision_id"),
                "accountId": _string_field(trade_plan, "account_id") or account_id,
                "symbol": _string_field(trade_plan, "symbol") or symbol,
                "mode": _string_field(trade_plan, "mode"),
                "side": _string_field(trade_plan, "side"),
                "entryZone": risk_gate_entry_zone(record_field(trade_plan, "entry_zone")),
                "stopLoss": number_field(trade_plan, "stop_loss"),
                "takeProfit": _array_number_field(trade_plan, "take_profit"),
                "maxLots": number_field(trade_plan, "max_lots"),
                "expiresAt": _string_field(trade_plan, "expires_at"),
            },
            "allowAdd": boolean_field(trade_plan, "add_on"),
            "sourceStrategy": "ai_signal",
        }
    )
    return {
        "audit_only": result.get("auditOnly", False),
        "decision_id": result.get("decisionId", ""),
        "mode": result.get("mode", ""),
        "symbol": result.get("symbol", ""),
        "status": result.get("status", ""),
        "reason_codes": result.get("reasonCodes", []),
        "requested_lots": result.get("requestedLots", 0),
        "allowed_lots": result.get("allowedLots", 0),
        "max_risk_lots": result.get("maxRiskLots", 0),
        "max_margin_lots": result.get("maxMarginLots", 0),
        "canProduceLiveCommands": result.get("canProduceLiveCommands", False),
    }


def risk_gate_entry_zone(value: dict | None) -> dict | None:
    if value is None:
        return None
    return {"min": number_field(value, "min"), "max": number_field(value, "max")}


# ------------------------------------------------ 决策事件/命令队列(app.ts)


def risk_gate_decision_status(status: str) -> str:
    if status in ("accepted", "rejected", "clamped"):
        return status
    return "pending"


async def record_ai_decision_timeline(
    store: EaStore, account_id: str, symbol: str, trade_plan: dict, risk_gate: dict, created_at: str
) -> None:
    decision_id = _string_field(trade_plan, "decision_id")
    await store.record_decision_event(
        {
            "decision_id": decision_id,
            "account_id": account_id,
            "symbol": symbol,
            "stage": "ai_result",
            "status": "accepted",
            "reason_codes": string_array_field(trade_plan, "reason_codes"),
            "summary": {
                "decision_id": decision_id,
                "mode": _string_field(trade_plan, "mode"),
                "symbol": _string_field(trade_plan, "symbol") or symbol,
                "confidence": number_field(trade_plan, "confidence"),
            },
            "related_command_id": "",
            "created_at": created_at,
        }
    )
    await store.record_decision_event(
        {
            "decision_id": decision_id,
            "account_id": account_id,
            "symbol": symbol,
            "stage": "risk_gate",
            "status": risk_gate_decision_status(_string_field(risk_gate, "status")),
            "reason_codes": string_array_field(risk_gate, "reason_codes"),
            "summary": {
                "decision_id": _string_field(risk_gate, "decision_id"),
                "mode": _string_field(risk_gate, "mode"),
                "symbol": _string_field(risk_gate, "symbol") or symbol,
                "status": _string_field(risk_gate, "status"),
                "audit_only": boolean_field(risk_gate, "audit_only"),
                "requested_lots": number_field(risk_gate, "requested_lots"),
                "allowed_lots": number_field(risk_gate, "allowed_lots"),
                "max_risk_lots": number_field(risk_gate, "max_risk_lots"),
                "max_margin_lots": number_field(risk_gate, "max_margin_lots"),
            },
            "related_command_id": "",
            "created_at": created_at,
        }
    )


async def record_ai_approve_pending_gate_event(
    store: EaStore, account_id: str, symbol: str, trade_plan: dict, reason: str, created_at: str
) -> None:
    decision_id = _string_field(trade_plan, "decision_id")
    await store.record_decision_event(
        {
            "decision_id": decision_id,
            "account_id": account_id,
            "symbol": symbol,
            "stage": "risk_gate",
            "status": "rejected",
            "reason_codes": [f"pending_gate.{reason}"],
            "summary": {
                "decision_id": decision_id,
                "mode": _string_field(trade_plan, "mode"),
                "symbol": _string_field(trade_plan, "symbol") or symbol,
                "status": "rejected",
                "pending_gate_reason": reason,
            },
            "related_command_id": "",
            "created_at": created_at,
        }
    )


def should_queue_ai_risk_command(payload: dict) -> bool:
    if payload.get("risk_alert") is not True:
        return False
    return _string_field(payload, "exit_suggestion").lower() in AI_RISK_EXIT_SUGGESTIONS


def ai_risk_gate_allows_command(trade_plan: dict | None, risk_gate: dict | None) -> bool:
    if trade_plan is None:
        return True
    status = _string_field(risk_gate or {}, "status")
    return status in ("accepted", "clamped")


def _js_number_string(value: float) -> str:
    # 镜像 JS String(number):数值 222002.0 呈现为 '222002',222002.5 呈现为 '222002.5'
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def ai_risk_command_timestamp(now_iso: str) -> int:
    millis = _parse_date_millis(now_iso)
    if millis is not None and _is_finite(millis):
        return _math_floor(millis / 1000)
    import time

    return _math_floor(time.time())


def ai_risk_command_timestamp_nanos(now_iso: str) -> str:
    millis = _parse_date_millis(now_iso)
    if millis is not None and _is_finite(millis):
        return str(int(millis) * 1_000_000)
    import time

    return str(int(time.time() * 1000) * 1_000_000)


def _attach_ai_risk_trade_plan_metadata(candidate: dict, trade_plan: dict | None, risk_gate: dict | None) -> None:
    if trade_plan is None:
        return
    candidate["decision_id"] = _string_field(trade_plan, "decision_id")
    candidate["trade_plan_mode"] = _string_field(trade_plan, "mode")
    if risk_gate is not None:
        candidate["risk_gate"] = risk_gate


async def build_close_short_risk_commands(
    store: EaStore,
    account_id: str,
    symbol: str,
    timestamp: str,
    alert_reason: str,
    confidence: Any,
    trade_plan: dict | None,
    risk_gate: dict | None,
) -> list[dict]:
    positions = await store.get_positions(account_id, symbol)
    candidates: list[dict] = []
    for position in positions:
        ticket = number_field(position, "ticket")
        if ticket <= 0:
            continue
        position_symbol = _string_field(position, "symbol")
        if len(position_symbol) > 0 and position_symbol.upper() != symbol.upper():
            continue
        if _string_field(position, "type").upper() != "SELL":
            continue
        candidate: dict = {
            # 镜像 TS 模板串 String(number):222002.0 → '222002'(整数浮点去 '.0')
            "command_id": f"ai_close_{timestamp}_{_js_number_string(ticket)}",
            "action": "CLOSE",
            "source": "ai_risk_alert",
            "ticket": ticket,
            "symbol": symbol,
            "reason": f"AI风险警报(平空): {alert_reason}",
            "confidence": confidence,
        }
        _attach_ai_risk_trade_plan_metadata(candidate, trade_plan, risk_gate)
        candidates.append(candidate)
    return candidates


async def build_ai_risk_command_candidates(
    store: EaStore,
    account_id: str,
    symbol: str,
    payload: dict,
    now_iso: str,
    trade_plan: dict | None,
    risk_gate: dict | None,
) -> list[dict]:
    exit_suggestion = _string_field(payload, "exit_suggestion").lower()
    timestamp = ai_risk_command_timestamp(now_iso)
    timestamp_nanos = ai_risk_command_timestamp_nanos(now_iso)
    confidence = payload.get("confidence")
    alert_reason = _string_field(payload, "alert_reason")
    if exit_suggestion == "close_short":
        return await build_close_short_risk_commands(
            store, account_id, symbol, timestamp_nanos, alert_reason, confidence, trade_plan, risk_gate
        )
    action = "CLOSE_ALL" if exit_suggestion == "close_all" else "CLOSE_PARTIAL"
    candidate: dict = {
        "command_id": f"ai_close_{timestamp}",
        "action": action,
        "source": "ai_risk_alert",
        "reason": (
            f"AI风险警报(全平): {alert_reason}"
            if exit_suggestion == "close_all"
            else f"AI风险警报(减仓50%): {alert_reason}"
        ),
        "confidence": confidence,
    }
    if exit_suggestion == "close_partial":
        candidate["lots_pct"] = 0.5
    _attach_ai_risk_trade_plan_metadata(candidate, trade_plan, risk_gate)
    return [candidate]


async def queue_ai_risk_commands(
    store: EaStore,
    account_id: str,
    symbol: str,
    payload: dict,
    now_iso: str,
    trade_plan: dict | None = None,
    risk_gate: dict | None = None,
) -> list[StoredCommand]:
    if not should_queue_ai_risk_command(payload) or not ai_risk_gate_allows_command(trade_plan, risk_gate):
        return []
    candidates = await build_ai_risk_command_candidates(
        store, account_id, symbol, payload, now_iso, trade_plan, risk_gate
    )
    commands: list[StoredCommand] = []
    for candidate in candidates:
        stored = await store.save_command_candidate(account_id, candidate)
        await store.promote_command(stored["command_id"])
        resolved = await store.get_command(stored["command_id"])
        commands.append(resolved if resolved is not None else {**stored, "status": "queued"})
    return commands


def ai_approve_queue_skip_reason(trade_plan: dict, risk_gate: dict) -> str | None:
    mode = _string_field(trade_plan, "mode")
    if mode != "approve":
        return "queue_skip.mode_not_approve"
    side = _string_field(trade_plan, "side")
    if side != "buy" and side != "sell":
        return "queue_skip.bad_side"
    if _string_field(risk_gate, "status") == "rejected":
        return "queue_skip.risk_rejected"
    if boolean_field(risk_gate, "audit_only"):
        return "queue_skip.audit_only"
    if number_field(trade_plan, "confidence") < AI_APPROVE_QUEUE_MIN_CONFIDENCE:
        return "queue_skip.confidence_below_min"
    return None


# ------------------------------------------------ 事件发布(app.ts)


def _event_timestamp(value: str) -> str:
    millis = _parse_date_millis(value)
    if millis is not None and _is_finite(millis):
        return datetime.fromtimestamp(millis / 1000, tz=UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    return value


def ai_result_event_payload(payload: dict, symbol: str, trade_plan: dict | None, risk_gate: dict | None) -> dict:
    if trade_plan is None:
        return payload
    out = {**payload}
    out["trade_plan_summary"] = {
        "decision_id": _string_field(trade_plan, "decision_id"),
        "mode": _string_field(trade_plan, "mode"),
        "symbol": _string_field(trade_plan, "symbol") or symbol,
        "confidence": number_field(trade_plan, "confidence"),
    }
    out["risk_gate"] = risk_gate or {}
    return out


def is_ai_analysis_failure(payload: dict) -> bool:
    return number_field(payload, "suggested_sl") == 0 and number_field(payload, "suggested_tp") == 0


def publish_ai_result_events(
    events: Any,
    account_id: str,
    symbol: str,
    payload: dict,
    trade_plan: dict | None,
    risk_gate: dict | None,
    created_at: str,
) -> None:
    timestamp = _event_timestamp(created_at)
    nanos = ai_risk_command_timestamp_nanos(created_at)
    if is_ai_analysis_failure(payload):
        events.publish(
            {
                "event_id": f"evt_ai_fail_{nanos}",
                "event_type": "ai_analysis_failed",
                "account_id": account_id,
                "source": "api.ai_result",
                "timestamp": timestamp,
                "payload": payload,
            }
        )
    events.publish(
        {
            "event_id": f"evt_ai_{nanos}",
            "event_type": "ai_result",
            "account_id": account_id,
            "source": "api.ai_result",
            "timestamp": timestamp,
            "payload": ai_result_event_payload(payload, symbol, trade_plan, risk_gate),
        }
    )


def _notify_ai_result(state: Any, account_id: str, symbol: str, trade_plan: dict, side: str) -> None:
    plan_symbol = _string_field(trade_plan, "symbol") or symbol
    mode = _string_field(trade_plan, "mode")
    confidence = number_field(trade_plan, "confidence")
    summary = (
        f"[GOLD-BOT] AI Signal: {plan_symbol} {side} "
        f"(mode={mode or 'approve'} confidence={confidence} account={account_id})"
    )
    import asyncio

    async def send() -> None:
        if getattr(state, "discord", None) is not None:
            await state.discord.send({"content": summary})
        if getattr(state, "feishu", None) is not None:
            await state.feishu.send({"title": "GOLD-BOT", "content": summary})

    try:
        asyncio.get_running_loop().create_task(send())
    except RuntimeError:
        pass


# ------------------------------------------------ main handler(ai.ts + app.ts)


def _decode_path_segment(value: str) -> str:
    from urllib.parse import unquote

    try:
        return unquote(value)
    except (ValueError, UnicodeError):
        return value


def _safe_unquote(value: str) -> str:
    return _decode_path_segment(value)


async def handle_ai_route(request: dict, deps: dict, helpers: dict) -> JsonResponse:
    parts = [part for part in request["path"].split("/") if len(part) > 0]
    valid_tokens: set[str] | None = deps["valid_tokens"]
    token = extract_auth_token(request["headers"], request["url"])
    # 镜像 TS handleAIRoute:先走 requireRouteToken(validTokens 为 null 也一律 401),
    # 再走 authorizeApiAccount(不自动绑定)。EA 路由(handleEaRoute)才是 validTokens == null 放行。
    if token is None or valid_tokens is None or token not in valid_tokens:
        return error(401, "invalid token")

    def authorized(account_id: str) -> JsonResponse | None:
        if not authorize_api_account(deps["token_accounts"], token, account_id, deps["admin_tokens"]):
            return error(403, "forbidden")
        return None

    store: EaStore = deps["store"]
    if len(parts) == 3 and parts[0] == "api" and parts[1] == "analysis_payload" and parts[2] != "":
        account_id = _safe_unquote(parts[2])
        guard = authorized(account_id)
        if guard is not None:
            return guard
        return {
            "statusCode": 200,
            "body": await helpers["analysis_payload"](store, account_id, "XAUUSD", deps["now_iso"]()),
        }
    if (
        len(parts) == 5
        and parts[0] == "api"
        and parts[1] == "v2"
        and parts[2] == "analysis_payload"
        and parts[3] != ""
        and parts[4] != ""
    ):
        account_id = _safe_unquote(parts[3])
        symbol = _safe_unquote(parts[4])
        guard = authorized(account_id)
        if guard is not None:
            return guard
        return {
            "statusCode": 200,
            "body": await helpers["analysis_payload"](store, account_id, symbol, deps["now_iso"]()),
        }
    if len(parts) == 3 and parts[0] == "api" and parts[1] == "ai_result" and parts[2] != "":
        account_id = _safe_unquote(parts[2])
        guard = authorized(account_id)
        if guard is not None:
            return guard
        return await helpers["handle_ai_result_route"](
            request["method"], account_id, "XAUUSD", request["rawBody"], deps
        )
    if (
        len(parts) == 5
        and parts[0] == "api"
        and parts[1] == "v2"
        and parts[2] == "ai_result"
        and parts[3] != ""
        and parts[4] != ""
    ):
        account_id = _safe_unquote(parts[3])
        symbol = _safe_unquote(parts[4])
        guard = authorized(account_id)
        if guard is not None:
            return guard
        return await helpers["handle_ai_result_route"](request["method"], account_id, symbol, request["rawBody"], deps)
    return error(404, "not found")


def parse_trade_plan_payload(payload: dict, expected_account_id: str, expected_symbol: str) -> dict:
    trade_plan = record_field(payload, "trade_plan")
    if trade_plan is None:
        return {}
    validation_error = validate_trade_plan(trade_plan, expected_account_id, expected_symbol)
    if validation_error is not None:
        return {"validation": {"valid": False, "error": validation_error}}
    return {"tradePlan": trade_plan, "validation": {"valid": True}}


def parse_dual_trade_plan_payload(payload: dict, expected_account_id: str, expected_symbol: str) -> dict | None:
    outer = record_field(payload, "dual_trade_plan")
    if outer is None:
        return None
    dual = record_field(outer, "dual_trade_plan") or outer
    if boolean_field(dual, "is_dual_direction") is not True:
        return None
    buy = parse_dual_trade_plan_side(dual, "buy", expected_account_id, expected_symbol)
    sell = parse_dual_trade_plan_side(dual, "sell", expected_account_id, expected_symbol)
    return {"valid": True, "buy": buy, "sell": sell}


def parse_dual_trade_plan_side(
    dual_trade_plan: dict, field: str, expected_account_id: str, expected_symbol: str
) -> dict | None:
    trade_plan = record_field(dual_trade_plan, field)
    if trade_plan is None:
        return None
    return trade_plan if validate_trade_plan(trade_plan, expected_account_id, expected_symbol) is None else None


def dual_trade_plans(dual_trade_plan: dict) -> list[dict]:
    return [plan for plan in [dual_trade_plan.get("buy"), dual_trade_plan.get("sell")] if plan is not None]


async def queue_ai_approve_pending_commands(
    deps: dict, account_id: str, symbol: str, trade_plans: list[dict], risk_gate: dict, event_timestamp: str
) -> StoredCommand | None:
    from backend.services.ai_approve.command import build_ai_approve_command_candidate
    from backend.services.ai_approve.gate import evaluate_ai_approve_pending_gate

    first_command: StoredCommand | None = None
    store: EaStore = deps["store"]
    positions = await store.get_positions(account_id, symbol)
    position_states = await store.load_position_states(account_id, symbol)
    for trade_plan in trade_plans:
        queue_skip_reason = ai_approve_queue_skip_reason(trade_plan, risk_gate)
        if queue_skip_reason is not None:
            await record_ai_approve_pending_gate_event(
                store, account_id, symbol, trade_plan, queue_skip_reason, event_timestamp
            )
            continue
        pending_gate = await evaluate_ai_approve_pending_gate(
            {
                "store": store,
                "accountId": account_id,
                "symbol": symbol,
                "tradePlan": trade_plan,
                "nowIso": event_timestamp,
                "cooldown": deps["ai_approve_cooldown"],
                "positionStates": position_states,
            }
        )
        if not pending_gate.get("accepted", False):
            await record_ai_approve_pending_gate_event(
                store, account_id, symbol, trade_plan, pending_gate.get("reason", ""), event_timestamp
            )
            continue
        candidate = build_ai_approve_command_candidate(
            {
                "accountId": account_id,
                "symbol": symbol,
                "tradePlan": trade_plan,
                "riskGate": risk_gate,
                "nowIso": event_timestamp,
                "orderType": pending_gate.get("orderType", ""),
                "positions": positions,
            }
        )
        command = await deps["command_lifecycle"].accept_candidate(account_id, candidate)
        if first_command is None:
            first_command = command
        if command.get("status") == "queued":
            deps["ai_approve_cooldown"].mark(symbol, event_timestamp)
    return first_command


async def handle_ai_result_route(_method: str, account_id: str, symbol: str, raw_body: str, deps: dict) -> JsonResponse:
    parsed_ok, parsed_body = parse_strict_json_object(raw_body)
    if not parsed_ok:
        return {"statusCode": 400, "body": {"status": "ERROR", "message": "invalid JSON"}}

    store: EaStore = deps["store"]
    await store.save_ai_result(account_id, symbol, parsed_body)
    event_timestamp = deps["now_iso"]()
    trade_plan_payload = parse_trade_plan_payload(parsed_body, account_id, symbol)
    trade_plan = trade_plan_payload.get("tradePlan")
    dual_trade_plan = parse_dual_trade_plan_payload(parsed_body, account_id, symbol)
    if trade_plan is None:
        publish_ai_result_events(deps["events"], account_id, symbol, parsed_body, None, None, event_timestamp)
        risk_command_requested = should_queue_ai_risk_command(parsed_body)
        if trade_plan_payload.get("validation") is None:
            await queue_ai_risk_commands(store, account_id, symbol, parsed_body, event_timestamp)
        if not risk_command_requested and dual_trade_plan is not None and dual_trade_plan.get("valid") is True:
            await queue_ai_approve_pending_commands(
                deps, account_id, symbol, dual_trade_plans(dual_trade_plan), {}, event_timestamp
            )
        validation = trade_plan_payload.get("validation")
        no_trade_plan_body: dict[str, Any] = {"status": "OK", "received": True}
        if validation is not None:
            no_trade_plan_body["trade_plan_validation"] = validation
        return {"statusCode": 200, "body": no_trade_plan_body}

    risk_gate = await ai_trade_plan_risk_gate(store, account_id, symbol, trade_plan, event_timestamp)
    decision_id = _string_field(trade_plan, "decision_id")
    mode = _string_field(trade_plan, "mode")
    trade_plan_side = _string_field(trade_plan, "side")
    await record_ai_decision_timeline(store, account_id, symbol, trade_plan, risk_gate, event_timestamp)
    publish_ai_result_events(deps["events"], account_id, symbol, parsed_body, trade_plan, risk_gate, event_timestamp)
    if trade_plan_side in ("buy", "sell"):
        _notify_ai_result(deps, account_id, symbol, trade_plan, trade_plan_side)
    risk_command_requested = should_queue_ai_risk_command(parsed_body)
    risk_commands = await queue_ai_risk_commands(
        store, account_id, symbol, parsed_body, event_timestamp, trade_plan, risk_gate
    )
    if not risk_command_requested:
        command = await queue_ai_approve_pending_commands(
            deps,
            account_id,
            symbol,
            dual_trade_plans(dual_trade_plan)
            if dual_trade_plan is not None and dual_trade_plan.get("valid") is True
            else [trade_plan],
            risk_gate,
            event_timestamp,
        )
    else:
        command = None
    shadow_payload: dict[str, Any] = {"decision_id": decision_id, "mode": mode, "risk_gate": risk_gate}
    if command is not None:
        shadow_command = command
    elif len(risk_commands) > 0:
        shadow_command = risk_commands[0]
    else:
        shadow_command = shadow_payload
    shadow = deps.get("shadow")
    if shadow is not None:
        await shadow.record_runtime_snapshot(
            {
                "account_id": account_id,
                "symbol": symbol,
                "source": "ai_result",
                "signal": None,
                "command": shadow_command,
                "created_at": event_timestamp,
            }
        )
    body: dict[str, Any] = {
        "status": "OK",
        "received": True,
        "decision": {
            "decision_id": decision_id,
            "mode": mode,
            "symbol": _string_field(trade_plan, "symbol") or symbol,
            "confidence": number_field(trade_plan, "confidence"),
        },
        "risk_gate": risk_gate,
        "trade_plan_validation": trade_plan_payload.get("validation"),
    }
    if command is not None:
        body["command_status"] = command.get("status")
    return {"statusCode": 200, "body": body}
