"""K 线形态检测(镜像 packages/trading-core/src/indicators/candlestick.ts)。

注释中沿用 Go 版 internal/strategy/indicator/candlestick.go 的实现语义。
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "CandleBar",
    "detect_all_candlestick_patterns",
    "detect_bearish_engulfing",
    "detect_bullish_engulfing",
    "detect_dark_cloud_cover",
    "detect_evening_star",
    "detect_hammer",
    "detect_morning_star",
    "detect_piercing_line",
    "detect_shooting_star",
    "detect_three_black_crows",
    "detect_three_white_soldiers",
    "is_bearish",
    "is_bullish",
]

CandleBar = dict[str, Any]
"""镜像 CandleBar:open/high/low/close + ema50/s1/s2/r1/r2/atr 可选。"""

_BULLISH_SIGNALS = frozenset(
    {"hammer", "bullish_engulfing", "piercing_line", "morning_star", "three_white_soldiers"}
)

_BEARISH_SIGNALS = frozenset(
    {"shooting_star", "bearish_engulfing", "dark_cloud_cover", "evening_star", "three_black_crows"}
)


def is_bullish(signal: str) -> bool:
    return signal in _BULLISH_SIGNALS


def is_bearish(signal: str) -> bool:
    return signal in _BEARISH_SIGNALS


# ---------------------------------------------------------------- Helpers


def _body(b: CandleBar) -> float:
    return abs(b["close"] - b["open"])


def _upper_shadow(b: CandleBar) -> float:
    return b["high"] - max(b["open"], b["close"])


def _lower_shadow(b: CandleBar) -> float:
    return min(b["open"], b["close"]) - b["low"]


def _is_bullish_bar(b: CandleBar) -> bool:
    return b["close"] > b["open"]


def _is_bearish_bar(b: CandleBar) -> bool:
    return b["close"] < b["open"]


def _clamp(val: float, low: float, high: float) -> float:
    return max(low, min(high, val))


def _min_float(a: float, b: float) -> float:
    return a if a < b else b


def _local_trend(bars: list[CandleBar], idx: int) -> str:
    if idx < 10:
        return "neutral"

    high_count = 0
    low_count = 0
    for i in range(idx - 9, idx):
        if bars[i + 1]["high"] > bars[i]["high"]:
            high_count += 1
        if bars[i + 1]["low"] < bars[i]["low"]:
            low_count += 1

    if idx < 5:
        return "neutral"
    ema50_current = bars[idx].get("ema50", 0) or 0
    ema50_prior = bars[idx - 5].get("ema50", 0) or 0
    if ema50_prior == 0:
        return "neutral"
    ema_slope = (ema50_current - ema50_prior) / ema50_prior

    if high_count >= 6 and ema_slope > 0.001:
        return "bull"
    if low_count >= 6 and ema_slope < -0.001:
        return "bear"
    return "neutral"


# ---------------------------------------------------------------- 单根 K 线


def detect_hammer(bars: list[CandleBar], i: int, atr: float) -> dict[str, Any] | None:
    if i < 0 or i >= len(bars):
        return None
    bar = bars[i]
    b = _body(bar)
    lower = _lower_shadow(bar)
    upper = _upper_shadow(bar)

    if b < atr * 0.01:
        b = atr * 0.01

    min_shadow = max(b * 2, atr * 0.15)
    if lower < min_shadow:
        return None
    if upper > b * 0.3:
        return None
    if bar["close"] < (bar["high"] + bar["low"]) / 2:
        return None

    if _local_trend(bars, i) == "bull":
        return None

    return {"signal": "hammer", "bullish": True, "barIndex": i, "strength": _pattern_strength("hammer", bars, i, atr)}


def detect_shooting_star(bars: list[CandleBar], i: int, atr: float) -> dict[str, Any] | None:
    if i < 0 or i >= len(bars):
        return None
    bar = bars[i]
    b = _body(bar)
    lower = _lower_shadow(bar)
    upper = _upper_shadow(bar)

    if b < atr * 0.01:
        b = atr * 0.01

    min_shadow = max(b * 2, atr * 0.15)
    if upper < min_shadow:
        return None
    if lower > b * 0.3:
        return None
    if bar["close"] > (bar["high"] + bar["low"]) / 2:
        return None

    if _local_trend(bars, i) == "bear":
        return None

    return {
        "signal": "shooting_star",
        "bullish": False,
        "barIndex": i,
        "strength": _pattern_strength("shooting_star", bars, i, atr),
    }


# ---------------------------------------------------------------- 双根 K 线


def detect_bullish_engulfing(bars: list[CandleBar], i: int, atr: float) -> dict[str, Any] | None:
    if i < 1 or i >= len(bars):
        return None
    prev = bars[i - 1]
    curr = bars[i]

    if not _is_bearish_bar(prev) or not _is_bullish_bar(curr):
        return None
    if curr["open"] > prev["close"] or curr["close"] < prev["open"]:
        return None
    if _body(curr) <= _body(prev):
        return None

    if _local_trend(bars, i) == "bull":
        return None

    return {
        "signal": "bullish_engulfing",
        "bullish": True,
        "barIndex": i,
        "strength": _pattern_strength("bullish_engulfing", bars, i, atr),
    }


def detect_bearish_engulfing(bars: list[CandleBar], i: int, atr: float) -> dict[str, Any] | None:
    if i < 1 or i >= len(bars):
        return None
    prev = bars[i - 1]
    curr = bars[i]

    if not _is_bullish_bar(prev) or not _is_bearish_bar(curr):
        return None
    if curr["open"] < prev["close"] or curr["close"] > prev["open"]:
        return None
    if _body(curr) <= _body(prev):
        return None

    if _local_trend(bars, i) == "bear":
        return None

    return {
        "signal": "bearish_engulfing",
        "bullish": False,
        "barIndex": i,
        "strength": _pattern_strength("bearish_engulfing", bars, i, atr),
    }


def detect_piercing_line(bars: list[CandleBar], i: int, atr: float) -> dict[str, Any] | None:
    if i < 1 or i >= len(bars):
        return None
    prev = bars[i - 1]
    curr = bars[i]

    if not _is_bearish_bar(prev) or not _is_bullish_bar(curr):
        return None
    if curr["open"] >= prev["close"]:
        return None

    prev_body = _body(prev)
    if prev_body < atr * 0.01:
        prev_body = atr * 0.01
    penetration_level = prev["open"] - (prev_body * 0.5)
    if curr["close"] < penetration_level:
        return None

    if _local_trend(bars, i) == "bull":
        return None

    strength = _pattern_strength("piercing_line", bars, i, atr)
    penetration63_level = prev["open"] - (prev_body * 0.37)
    if curr["close"] >= penetration63_level:
        strength = _clamp(strength + 0.1, 0.0, 1.0)

    return {"signal": "piercing_line", "bullish": True, "barIndex": i, "strength": strength}


def detect_dark_cloud_cover(bars: list[CandleBar], i: int, atr: float) -> dict[str, Any] | None:
    if i < 1 or i >= len(bars):
        return None
    prev = bars[i - 1]
    curr = bars[i]

    if not _is_bullish_bar(prev) or not _is_bearish_bar(curr):
        return None
    if curr["open"] <= prev["close"]:
        return None

    prev_body = _body(prev)
    if prev_body < atr * 0.01:
        prev_body = atr * 0.01
    penetration_level = prev["close"] - (prev_body * 0.5)
    if curr["close"] > penetration_level:
        return None

    if _local_trend(bars, i) == "bear":
        return None

    strength = _pattern_strength("dark_cloud_cover", bars, i, atr)
    penetration63_level = prev["close"] - (prev_body * 0.63)
    if curr["close"] <= penetration63_level:
        strength = _clamp(strength + 0.1, 0.0, 1.0)

    return {"signal": "dark_cloud_cover", "bullish": False, "barIndex": i, "strength": strength}


# ---------------------------------------------------------------- 三根 K 线


def detect_morning_star(bars: list[CandleBar], i: int, atr: float) -> dict[str, Any] | None:
    if i < 2 or i >= len(bars):
        return None
    bar0 = bars[i - 2]
    bar1 = bars[i - 1]
    bar2 = bars[i]

    if not _is_bearish_bar(bar0) or _body(bar0) < atr * 0.3:
        return None
    if _body(bar1) > atr * 0.15 or bar1["high"] > bar0["close"]:
        return None
    if not _is_bullish_bar(bar2) or _body(bar2) < atr * 0.3:
        return None

    bar0_midpoint = (bar0["open"] + bar0["close"]) / 2
    if bar2["close"] < bar0_midpoint:
        return None

    if _local_trend(bars, i) == "bull":
        return None

    return {
        "signal": "morning_star",
        "bullish": True,
        "barIndex": i,
        "strength": _pattern_strength("morning_star", bars, i, atr),
    }


def detect_evening_star(bars: list[CandleBar], i: int, atr: float) -> dict[str, Any] | None:
    if i < 2 or i >= len(bars):
        return None
    bar0 = bars[i - 2]
    bar1 = bars[i - 1]
    bar2 = bars[i]

    if not _is_bullish_bar(bar0) or _body(bar0) < atr * 0.3:
        return None
    if _body(bar1) > atr * 0.15 or bar1["low"] < bar0["close"]:
        return None
    if not _is_bearish_bar(bar2) or _body(bar2) < atr * 0.3:
        return None

    bar0_midpoint = (bar0["open"] + bar0["close"]) / 2
    if bar2["close"] > bar0_midpoint:
        return None

    if _local_trend(bars, i) == "bear":
        return None

    return {
        "signal": "evening_star",
        "bullish": False,
        "barIndex": i,
        "strength": _pattern_strength("evening_star", bars, i, atr),
    }


def detect_three_white_soldiers(bars: list[CandleBar], i: int, atr: float) -> dict[str, Any] | None:
    if i < 2 or i >= len(bars):
        return None
    bar0 = bars[i - 2]
    bar1 = bars[i - 1]
    bar2 = bars[i]

    if not _is_bullish_bar(bar0) or not _is_bullish_bar(bar1) or not _is_bullish_bar(bar2):
        return None
    if bar1["close"] <= bar0["close"] or bar2["close"] <= bar1["close"]:
        return None

    bar0_upper_half = (bar0["open"] + bar0["close"]) / 2
    bar1_upper_half = (bar1["open"] + bar1["close"]) / 2
    if bar1["open"] < bar0_upper_half or bar2["open"] < bar1_upper_half:
        return None

    body0 = _body(bar0)
    body1 = _body(bar1)
    body2 = _body(bar2)
    max_body = max(body0, body1, body2)
    min_body = min(body0, body1, body2)
    if min_body < atr * 0.01:
        min_body = atr * 0.01
    if max_body / min_body > 1.5:
        return None

    if _local_trend(bars, i) == "bear":
        return None

    return {
        "signal": "three_white_soldiers",
        "bullish": True,
        "barIndex": i,
        "strength": _pattern_strength("three_white_soldiers", bars, i, atr),
    }


def detect_three_black_crows(bars: list[CandleBar], i: int, atr: float) -> dict[str, Any] | None:
    if i < 2 or i >= len(bars):
        return None
    bar0 = bars[i - 2]
    bar1 = bars[i - 1]
    bar2 = bars[i]

    if not _is_bearish_bar(bar0) or not _is_bearish_bar(bar1) or not _is_bearish_bar(bar2):
        return None
    if bar1["close"] >= bar0["close"] or bar2["close"] >= bar1["close"]:
        return None

    bar0_lower_half = (bar0["open"] + bar0["close"]) / 2
    bar1_lower_half = (bar1["open"] + bar1["close"]) / 2
    if bar1["open"] > bar0_lower_half or bar2["open"] > bar1_lower_half:
        return None

    body0 = _body(bar0)
    body1 = _body(bar1)
    body2 = _body(bar2)
    max_body = max(body0, body1, body2)
    min_body = min(body0, body1, body2)
    if min_body < atr * 0.01:
        min_body = atr * 0.01
    if max_body / min_body > 1.5:
        return None

    if _local_trend(bars, i) == "bull":
        return None

    return {
        "signal": "three_black_crows",
        "bullish": False,
        "barIndex": i,
        "strength": _pattern_strength("three_black_crows", bars, i, atr),
    }


# ---------------------------------------------------------------- 形态强度


def _pattern_strength(signal: str, bars: list[CandleBar], i: int, atr: float) -> float:
    base = 0.5
    bar = bars[i]
    b = _body(bar)
    if b < atr * 0.01:
        b = atr * 0.01

    # 1. 实体/影线比加成(0.0-0.2)
    ratio_bonus = 0.0
    if signal in ("hammer", "shooting_star"):
        required_ratio = 2.0
        actual_ratio = (_lower_shadow(bar) if signal == "hammer" else _upper_shadow(bar)) / b
        if actual_ratio > required_ratio:
            ratio_bonus = _min_float((actual_ratio - required_ratio) / (required_ratio * 2), 0.2)
    elif signal in ("bullish_engulfing", "bearish_engulfing"):
        if i >= 1:
            prev_body = _body(bars[i - 1])
            if prev_body < atr * 0.01:
                prev_body = atr * 0.01
            engulf_ratio = b / prev_body
            if engulf_ratio > 1.5:
                ratio_bonus = _min_float((engulf_ratio - 1.5) / 2.0, 0.2)
    elif signal in ("morning_star", "evening_star"):
        if i >= 2:
            middle_body = _body(bars[i - 1])
            if middle_body < atr * 0.05:
                ratio_bonus = 0.15
            elif middle_body < atr * 0.1:
                ratio_bonus = 0.1
    elif signal in ("piercing_line", "dark_cloud_cover"):
        ratio_bonus = 0.0
    elif signal in ("three_white_soldiers", "three_black_crows"):
        ratio_bonus = 0.1

    # 2. 趋势一致加成(0.0-0.2)
    trend_bonus = 0.0
    trend = _local_trend(bars, i)
    is_bull_pattern = is_bullish(signal)
    is_bear_pattern = is_bearish(signal)

    if is_bull_pattern and trend != "bull":
        trend_bonus = 0.2
    elif is_bear_pattern and trend != "bear":
        trend_bonus = 0.2

    # 3. 支撑/阻力临近加成(0.0-0.1)
    sr_bonus = 0.0
    if is_bull_pattern:
        if bar.get("s1") is not None and abs(bar["close"] - bar["s1"]) < atr * 0.5:
            sr_bonus = 0.1
        elif bar.get("s2") is not None and abs(bar["close"] - bar["s2"]) < atr * 0.5:
            sr_bonus = 0.1
    elif is_bear_pattern:
        if bar.get("r1") is not None and abs(bar["close"] - bar["r1"]) < atr * 0.5:
            sr_bonus = 0.1
        elif bar.get("r2") is not None and abs(bar["close"] - bar["r2"]) < atr * 0.5:
            sr_bonus = 0.1

    return _clamp(base + ratio_bonus + trend_bonus + sr_bonus, 0.0, 1.0)


# ---------------------------------------------------------------- 全形态检测


def detect_all_candlestick_patterns(bars: list[CandleBar], i: int) -> list[str]:
    """运行全部形态检测器,返回索引 i 处 K 线的形态名列表。"""
    if i < 0 or i >= len(bars):
        return []

    atr = bars[i].get("atr", 0) or 0
    if atr <= 0:
        if i > 0:
            atr = bars[i]["high"] - bars[i]["low"]
        else:
            return []

    results: list[str] = []

    # 单根 K 线
    hammer = detect_hammer(bars, i, atr)
    if hammer is not None and hammer["strength"] >= 0.5:
        results.append(hammer["signal"])
    star = detect_shooting_star(bars, i, atr)
    if star is not None and star["strength"] >= 0.5:
        results.append(star["signal"])

    # 双根 K 线
    if i >= 1:
        bull_eng = detect_bullish_engulfing(bars, i, atr)
        if bull_eng is not None and bull_eng["strength"] >= 0.5:
            results.append(bull_eng["signal"])
        bear_eng = detect_bearish_engulfing(bars, i, atr)
        if bear_eng is not None and bear_eng["strength"] >= 0.5:
            results.append(bear_eng["signal"])
        pierce = detect_piercing_line(bars, i, atr)
        if pierce is not None and pierce["strength"] >= 0.5:
            results.append(pierce["signal"])
        dark = detect_dark_cloud_cover(bars, i, atr)
        if dark is not None and dark["strength"] >= 0.5:
            results.append(dark["signal"])

    # 三根 K 线
    if i >= 2:
        morning = detect_morning_star(bars, i, atr)
        if morning is not None and morning["strength"] >= 0.5:
            results.append(morning["signal"])
        evening = detect_evening_star(bars, i, atr)
        if evening is not None and evening["strength"] >= 0.5:
            results.append(evening["signal"])
        soldiers = detect_three_white_soldiers(bars, i, atr)
        if soldiers is not None and soldiers["strength"] >= 0.5:
            results.append(soldiers["signal"])
        crows = detect_three_black_crows(bars, i, atr)
        if crows is not None and crows["strength"] >= 0.5:
            results.append(crows["signal"])

    return results
