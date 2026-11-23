from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any, Literal, TypedDict, get_args

from backend.trading_core.indicators.index import atr, pivot_points
from backend.trading_core.smc.detector import find_swing_points

LevelSide = Literal["support", "resistance"]
BreakState = Literal["closed_confirmed", "intrabar_broken", "touched"]
TriggerBasis = Literal["pullback_limit", "closed_breakout", "intrabar_breakout", "range_fade"]
TRIGGER_BASES = get_args(TriggerBasis)
UNCONFIRMED_BREAKOUT = "breakout.unconfirmed"


class BreakoutLevel(TypedDict):
    price: float
    side: LevelSide
    timeframe: str
    state: BreakState


def _number(value: Any) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return float(value)
    return 0.0


def _bar_time(bar: dict) -> float:
    value = str(bar.get("time", ""))
    if value.isdecimal():
        return float(value)
    try:
        if value[4:5] == ".":
            value = value.replace(".", "-", 2)
        parsed = datetime.fromisoformat(value)
        return (parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)).timestamp()
    except ValueError:
        return 0.0


def _closed_atrs(bars: list[dict], count: int) -> list[float]:
    sample = bars[-count:]
    reported = [_number(bar.get("atr")) for bar in sample]
    if not sample or all(value > 0 for value in reported):
        return reported
    high, low, close = ([_number(bar.get(field)) for bar in bars] for field in ("high", "low", "close"))
    if min(*high, *low, *close) <= 0:
        return reported
    computed = atr(high, low, close, 14)[-len(sample):]
    return [value or _number(fallback) for value, fallback in zip(reported, computed, strict=True)]


def _valid_bar(bar: dict) -> bool:
    low, high, opening, closing = (_number(bar.get(field)) for field in ("low", "high", "open", "close"))
    return 0 < low <= min(opening, closing) <= max(opening, closing) <= high


def build_breakout_levels(
    bars: dict[str, list[dict]], current_price: float, smc_context: dict | None = None,
) -> list[BreakoutLevel]:
    """Classify tested swing, pivot and sweep levels. Each EA series ends in a forming bar."""
    m15 = bars.get("M15") or []
    h1 = bars.get("H1") or []
    if m15 and not _valid_bar(m15[-1]):
        m15 = []
    if h1 and not _valid_bar(h1[-1]):
        h1 = []
    if not m15 and not h1:
        return []
    h1_atr = next(iter(_closed_atrs(h1[:-1], 1)), 0.0)
    m15_atrs = _closed_atrs(m15[:-1], 2)
    m15_times = [_bar_time(bar) for bar in m15[-3:]]
    adjacent_m15 = len(m15_times) == 3 and m15_times[0] > 0 and (
        m15_times[1] - m15_times[0] == m15_times[2] - m15_times[1] == 900
    )
    latest_closed = m15[-2] if len(m15) >= 2 else h1[-2] if len(h1) >= 2 else {}
    m15_closed_after_h1 = adjacent_m15 and bool(h1) and m15_times[-1] > _bar_time(h1[-1]) > 0
    recent_bars = m15[-3:] or h1[-2:]
    levels: list[BreakoutLevel] = []

    for timeframe in ("D1", "H4", "H1", "M30", "M15"):
        series = bars.get(timeframe) or []
        closed = series[:-1]
        swings = find_swing_points(
            [bar for bar in closed if _number(bar.get("low")) > 0 and _number(bar.get("high")) > 0], 2, 2
        )
        anchors: dict[tuple[LevelSide, float], bool] = {}
        for swing in swings["swingHighs"][-3:]:
            anchors[("resistance", swing["price"])] = False
        for swing in swings["swingLows"][-3:]:
            anchors[("support", swing["price"])] = False
        pivot_source = closed[:-2] if timeframe == "M15" else closed[:-1]
        if pivot_source:
            previous = pivot_source[-1]
            high, low, close = (_number(previous.get(field)) for field in ("high", "low", "close"))
            if high > low > 0 and low <= close <= high:
                pivots = pivot_points(high, low, close)
                anchors.setdefault(("resistance", pivots["r1"]), False)
                anchors.setdefault(("support", pivots["s1"]), False)
        for sweep in (smc_context or {}).get(f"{timeframe.lower()}_sweeps", []):
            if series and sweep["reversed"] and sweep["index"] >= len(series) - 2:
                side: LevelSide = "resistance" if sweep["side"] == "BEAR" else "support"
                anchors[(side, sweep["level"])] = True

        for (level_side, price), swept in anchors.items():
            if price <= 0:
                continue
            direction = 1 if level_side == "resistance" else -1
            last_close = _number(latest_closed.get("close"))
            h1_confirmed = (
                timeframe in ("D1", "H4", "H1") and len(h1) >= 2
                and _valid_bar(h1[-2]) and _h1_body_breaks(h1[-2], price, direction, h1_atr)
                and not (m15_closed_after_h1 and last_close > 0 and direction * (last_close - price) <= 0)
            )
            m15_confirmed = adjacent_m15 and all(
                _valid_bar(bar) and _beyond(_number(bar.get("close")), price, direction, baseline)
                for bar, baseline in zip(m15[-3:-1], m15_atrs, strict=True)
            )
            price_crossed = (
                current_price > 0 and last_close > 0
                and direction * (last_close - price) <= 0 < direction * (current_price - price)
            )
            anchor_bars = recent_bars if timeframe in ("M15", "M30") else [*recent_bars, *h1[-2:]]
            if h1_confirmed or m15_confirmed:
                state: BreakState = "closed_confirmed"
            elif swept or price_crossed or any(_wick_crosses(bar, price, direction) for bar in anchor_bars):
                state = "intrabar_broken"
            elif _touches(anchor_bars, current_price, price, direction, h1_atr * 0.15):
                state = "touched"
            else:
                continue
            levels.append({"price": price, "side": level_side, "timeframe": timeframe, "state": state})
    return levels


def _beyond(value: float, price: float, direction: int, baseline: float) -> bool:
    distance = direction * (value - price)
    threshold = baseline * 0.1
    return value > 0 and threshold > 0 and (distance >= threshold or math.isclose(distance, threshold))


def _h1_body_breaks(bar: dict, price: float, direction: int, baseline: float) -> bool:
    opening = _number(bar.get("open"))
    closing = _number(bar.get("close"))
    return opening > 0 and direction * (opening - price) <= 0 and _beyond(closing, price, direction, baseline)


def _wick_crosses(bar: dict, price: float, direction: int) -> bool:
    low, high = _number(bar.get("low")), _number(bar.get("high"))
    if not _valid_bar(bar):
        return False
    return low <= price < high if direction == 1 else low < price <= high


def _touches(bars: list[dict], current: float, price: float, direction: int, buffer: float) -> bool:
    extremes = [_number(bar.get("high" if direction == 1 else "low")) for bar in bars]
    return any(value > 0 and 0 <= direction * (price - value) <= buffer for value in [current, *extremes])


def breakout_entry_rejection(
    levels: list[BreakoutLevel], side: str, current: float, entry: float, *,
    phase: str = "", basis: str = "", market: bool = False,
) -> str | None:
    if side not in ("buy", "sell"):
        return None
    level_side = "resistance" if side == "buy" else "support"
    nearest = min(
        (level for level in levels if level["side"] == level_side),
        key=lambda level: abs(level["price"] - current),
        default=None,
    )
    if nearest and nearest["state"] == "closed_confirmed":
        return None

    explicit = basis in ("closed_breakout", "intrabar_breakout")
    direction = 1 if side == "buy" else -1
    pullback = not market and 0 < entry and direction * (current - entry) > 0 and (
        nearest is None or direction * (nearest["price"] - entry) > 0
    )
    chasing = (
        nearest is not None and nearest["state"] == "intrabar_broken"
        and direction * ((current if market else entry) - nearest["price"]) >= 0
    )
    if explicit or (not pullback and (phase == "breakout" or chasing)):
        return UNCONFIRMED_BREAKOUT
    return None
