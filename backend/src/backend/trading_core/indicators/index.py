"""指标计算(镜像 packages/trading-core/src/indicators/index.ts)。

全部按 TS/Go oracle 语义逐字移植:滚动窗口、Wilders 平滑、NaN 语义一致。
"""

from __future__ import annotations

import math
from typing import Any

__all__ = [
    "adx",
    "atr",
    "bollinger",
    "calculate_fib_extension",
    "detect_macd_divergence",
    "detect_rsi_divergence",
    "ema",
    "fibonacci",
    "is_price_in_fib_zone",
    "macd",
    "pivot_points",
    "rsi",
    "stoch",
    "IndicatorBar",
]

IndicatorBar = dict[str, Any]
"""镜像 IndicatorBar:time/open/high/low/close + macdHist/rsi 可选。"""

_NAN = float("nan")


def ema(values: list[float], period: int) -> list[float]:
    out = [0.0] * len(values)
    if len(values) == 0 or period <= 0:
        return out

    k = 2 / (period + 1)
    out[0] = values[0]
    for i in range(1, len(values)):
        out[i] = values[i] * k + out[i - 1] * (1 - k)
    return out


def atr(high: list[float], low: list[float], close: list[float], period: int) -> list[float]:
    tr = [0.0] * len(close)
    for i in range(len(close)):
        if i == 0:
            tr[i] = high[i] - low[i]
            continue
        tr[i] = max(high[i] - low[i], max(abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1])))
    return wilders_smoothing(tr, period)


def rsi(close: list[float], period: int) -> list[float]:
    out = [_NAN] * len(close)
    if len(close) == 0 or period <= 0:
        return out

    gains = [0.0] * len(close)
    losses = [0.0] * len(close)
    for i in range(1, len(close)):
        delta = close[i] - close[i - 1]
        if delta > 0:
            gains[i] = delta
        elif delta < 0:
            losses[i] = -delta

    avg_gain = wilders_smoothing(gains, period)
    avg_loss = wilders_smoothing(losses, period)
    for i in range(len(close)):
        if math.isnan(avg_gain[i]) or math.isnan(avg_loss[i]) or avg_loss[i] == 0:
            continue
        rs = avg_gain[i] / avg_loss[i]
        out[i] = 100 - 100 / (1 + rs)
    return out


def macd(close: list[float]) -> dict[str, list[float]]:
    ema12 = ema(close, 12)
    ema26 = ema(close, 26)
    macd_line = [ema12[i] - ema26[i] for i in range(len(close))]
    signal = ema(macd_line, 9)
    histogram = [macd_line[i] - signal[i] for i in range(len(close))]
    return {"macd": macd_line, "signal": signal, "histogram": histogram}


def fibonacci(highs: list[float], lows: list[float], window: int) -> dict[str, float]:
    if len(highs) < window or len(lows) < window or window < 2:
        return {"fib236": 0, "fib382": 0, "fib500": 0, "fib618": 0, "fib786": 0}

    swing_high = highs[0]
    swing_low = lows[0]
    for i in range(1, min(window, len(highs))):
        if highs[i] > swing_high:
            swing_high = highs[i]
        if lows[i] < swing_low:
            swing_low = lows[i]

    diff = swing_high - swing_low
    return {
        "fib236": swing_high - diff * 0.236,
        "fib382": swing_high - diff * 0.382,
        "fib500": swing_high - diff * 0.5,
        "fib618": swing_high - diff * 0.618,
        "fib786": swing_high - diff * 0.786,
    }


def calculate_fib_extension(swing_high: float, swing_low: float, trend: str) -> dict[str, float]:
    diff = abs(swing_high - swing_low)
    if trend == "UP":
        return {
            "level1272": round2(swing_high + diff * 1.272),
            "level1618": round2(swing_high + diff * 1.618),
            "level2618": round2(swing_high + diff * 2.618),
        }
    return {
        "level1272": round2(swing_low - diff * 1.272),
        "level1618": round2(swing_low - diff * 1.618),
        "level2618": round2(swing_low - diff * 2.618),
    }


def is_price_in_fib_zone(
    price: float, fib382: float, fib618: float, atr_value: float, buffer_atr: float
) -> bool:
    buffer = atr_value * buffer_atr
    low = min(fib382, fib618)
    high = max(fib382, fib618)
    return low - buffer <= price <= high + buffer


def pivot_points(prev_high: float, prev_low: float, prev_close: float) -> dict[str, float]:
    pp = (prev_high + prev_low + prev_close) / 3
    return {
        "pp": pp,
        "r1": 2 * pp - prev_low,
        "r2": pp + (prev_high - prev_low),
        "r3": prev_high + 2 * (pp - prev_low),
        "s1": 2 * pp - prev_high,
        "s2": pp - (prev_high - prev_low),
        "s3": prev_low - 2 * (prev_high - pp),
    }


def adx(high: list[float], low: list[float], close: list[float], period: int) -> list[float]:
    plus_dm = [0.0] * len(close)
    minus_dm = [0.0] * len(close)
    tr = [0.0] * len(close)

    for i in range(len(close)):
        if i == 0:
            tr[i] = high[i] - low[i]
            continue

        plus_raw = high[i] - high[i - 1]
        minus_raw = low[i - 1] - low[i]

        plus_dm[i] = plus_raw if plus_raw > minus_raw and plus_raw > 0 else 0
        minus_dm[i] = minus_raw if minus_raw > plus_dm[i] and minus_raw > 0 else 0
        tr[i] = max(high[i] - low[i], max(abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1])))

    atr_mean = rolling_mean(tr, period)
    plus_avg = rolling_mean(plus_dm, period)
    minus_avg = rolling_mean(minus_dm, period)
    dx = [_NAN] * len(close)

    for i in range(len(close)):
        if math.isnan(atr_mean[i]) or atr_mean[i] == 0:
            continue
        plus_di = 100 * (plus_avg[i] / atr_mean[i])
        minus_di = 100 * (minus_avg[i] / atr_mean[i])
        denominator = plus_di + minus_di
        if denominator == 0:
            continue
        dx[i] = 100 * (abs(plus_di - minus_di) / denominator)

    return rolling_mean(dx, period)


def bollinger(close: list[float], period: int, width: float) -> dict[str, list[float]]:
    mid = rolling_mean(close, period)
    std = rolling_std(close, period)
    upper = [mid[i] + width * std[i] for i in range(len(close))]
    lower = [mid[i] - width * std[i] for i in range(len(close))]
    return {"upper": upper, "mid": mid, "lower": lower}


def stoch(
    high: list[float], low: list[float], close: list[float], period: int, smooth: int
) -> dict[str, list[float]]:
    low_n = rolling_min(low, period)
    high_n = rolling_max(high, period)
    k = [_NAN] * len(close)

    for i in range(len(close)):
        if math.isnan(low_n[i]) or math.isnan(high_n[i]):
            continue
        denominator = high_n[i] - low_n[i]
        if denominator == 0:
            continue
        k[i] = (100 * (close[i] - low_n[i])) / denominator

    return {"k": k, "d": rolling_mean(k, smooth)}


def detect_macd_divergence(bars: list[IndicatorBar]) -> dict[str, Any] | None:
    if len(bars) < 20:
        return None

    recent = bars[-min(20, len(bars)) :]
    price_lows = find_local_lows(recent, 3)
    price_highs = find_local_highs(recent, 3)
    macd_lows = find_macd_lows(recent, 3)
    macd_highs = find_macd_highs(recent, 3)

    if len(price_lows) >= 2 and len(macd_lows) >= 2:
        pl1 = price_lows[-2]
        pl2 = price_lows[-1]
        ml1 = find_nearest_macd_low(recent, pl1)
        ml2 = find_nearest_macd_low(recent, pl2)

        if (
            ml1 != -1
            and ml2 != -1
            and recent[pl2]["low"] < recent[pl1]["low"]
            and macd_hist(recent[ml2]) >= macd_hist(recent[ml1])
        ):
            return {
                "type": "bullish_macd",
                "strength": calculate_divergence_strength(recent, pl1, pl2, ml1, ml2),
                "confidence": calculate_confidence(recent, pl1, pl2, ml1, ml2),
                "priceLevel": recent[pl2]["low"],
                "time": recent[pl2].get("time", ""),
            }

    if len(price_highs) >= 2 and len(macd_highs) >= 2:
        ph1 = price_highs[-2]
        ph2 = price_highs[-1]
        mh1 = find_nearest_macd_high(recent, ph1)
        mh2 = find_nearest_macd_high(recent, ph2)

        if (
            mh1 != -1
            and mh2 != -1
            and recent[ph2]["high"] > recent[ph1]["high"]
            and macd_hist(recent[mh2]) <= macd_hist(recent[mh1])
        ):
            return {
                "type": "bearish_macd",
                "strength": calculate_divergence_strength(recent, ph1, ph2, mh1, mh2),
                "confidence": calculate_confidence(recent, ph1, ph2, mh1, mh2),
                "priceLevel": recent[ph2]["high"],
                "time": recent[ph2].get("time", ""),
            }

    return None


def detect_rsi_divergence(bars: list[IndicatorBar]) -> dict[str, Any] | None:
    if len(bars) < 20:
        return None

    recent = bars[-min(20, len(bars)) :]
    price_lows = find_local_lows(recent, 3)
    price_highs = find_local_highs(recent, 3)

    if len(price_lows) >= 2:
        pl1 = price_lows[-2]
        pl2 = price_lows[-1]
        rsi1 = rsi_value(recent[pl1])
        rsi2 = rsi_value(recent[pl2])

        if (
            not math.isnan(rsi1)
            and not math.isnan(rsi2)
            and recent[pl2]["low"] < recent[pl1]["low"]
            and rsi2 > rsi1
        ):
            return {
                "type": "bullish_rsi",
                "strength": calculate_rsi_divergence_strength(recent, pl1, pl2),
                "confidence": calculate_rsi_confidence(recent, pl1, pl2),
                "priceLevel": recent[pl2]["low"],
                "time": recent[pl2].get("time", ""),
            }

    if len(price_highs) >= 2:
        ph1 = price_highs[-2]
        ph2 = price_highs[-1]
        rsi1 = rsi_value(recent[ph1])
        rsi2 = rsi_value(recent[ph2])

        if (
            not math.isnan(rsi1)
            and not math.isnan(rsi2)
            and recent[ph2]["high"] > recent[ph1]["high"]
            and rsi2 < rsi1
        ):
            return {
                "type": "bearish_rsi",
                "strength": calculate_rsi_divergence_strength(recent, ph1, ph2),
                "confidence": calculate_rsi_confidence(recent, ph1, ph2),
                "priceLevel": recent[ph2]["high"],
                "time": recent[ph2].get("time", ""),
            }

    return None


# ---------------------------------------------------------------- 滚动窗口原语


def rolling_mean(values: list[float], period: int) -> list[float]:
    out = [_NAN] * len(values)
    if period <= 0:
        return out

    for i in range(period - 1, len(values)):
        total = 0.0
        valid = 0
        for j in range(i - period + 1, i + 1):
            if math.isnan(values[j]):
                continue
            total += values[j]
            valid += 1
        if valid == period:
            out[i] = total / period

    return out


def rolling_min(values: list[float], period: int) -> list[float]:
    out = [_NAN] * len(values)
    if period <= 0:
        return out

    for i in range(period - 1, len(values)):
        min_value = math.inf
        valid = 0
        for j in range(i - period + 1, i + 1):
            if math.isnan(values[j]):
                continue
            min_value = min(min_value, values[j])
            valid += 1
        if valid == period:
            out[i] = min_value

    return out


def rolling_max(values: list[float], period: int) -> list[float]:
    out = [_NAN] * len(values)
    if period <= 0:
        return out

    for i in range(period - 1, len(values)):
        max_value = -math.inf
        valid = 0
        for j in range(i - period + 1, i + 1):
            if math.isnan(values[j]):
                continue
            max_value = max(max_value, values[j])
            valid += 1
        if valid == period:
            out[i] = max_value

    return out


def rolling_std(values: list[float], period: int) -> list[float]:
    out = [_NAN] * len(values)
    if period <= 0:
        return out

    for i in range(period - 1, len(values)):
        total = 0.0
        valid = 0
        for j in range(i - period + 1, i + 1):
            if math.isnan(values[j]):
                continue
            total += values[j]
            valid += 1
        if valid != period or period == 1:
            continue

        mean = total / period
        variance = 0.0
        for j in range(i - period + 1, i + 1):
            diff = values[j] - mean
            variance += diff * diff
        out[i] = math.sqrt(variance / (period - 1))

    return out


def wilders_smoothing(values: list[float], period: int) -> list[float]:
    out = [_NAN] * len(values)
    if len(values) < period or period <= 0:
        return out

    total = 0.0
    for i in range(period):
        total += values[i]
    out[period - 1] = total / period

    for i in range(period, len(values)):
        if math.isnan(values[i]):
            out[i] = out[i - 1]
            continue
        out[i] = out[i - 1] * ((period - 1) / period) + values[i] / period
    return out


def round2(value: float) -> float:
    return round(value * 100) / 100


# ---------------------------------------------------------------- 背离检测原语


def find_local_lows(bars: list[IndicatorBar], min_bars: int) -> list[int]:
    lows: list[int] = []
    for i in range(min_bars, len(bars) - min_bars):
        is_low = True
        for j in range(i - min_bars, i + min_bars + 1):
            if j != i and bars[j]["low"] <= bars[i]["low"]:
                is_low = False
                break
        if is_low:
            lows.append(i)
    return lows


def find_local_highs(bars: list[IndicatorBar], min_bars: int) -> list[int]:
    highs: list[int] = []
    for i in range(min_bars, len(bars) - min_bars):
        is_high = True
        for j in range(i - min_bars, i + min_bars + 1):
            if j != i and bars[j]["high"] >= bars[i]["high"]:
                is_high = False
                break
        if is_high:
            highs.append(i)
    return highs


def find_macd_lows(bars: list[IndicatorBar], min_bars: int) -> list[int]:
    lows: list[int] = []
    for i in range(min_bars, len(bars) - min_bars):
        if math.isnan(macd_hist(bars[i])):
            continue
        is_low = True
        for j in range(i - min_bars, i + min_bars + 1):
            if j != i and not math.isnan(macd_hist(bars[j])) and macd_hist(bars[j]) <= macd_hist(bars[i]):
                is_low = False
                break
        if is_low:
            lows.append(i)
    return lows


def find_macd_highs(bars: list[IndicatorBar], min_bars: int) -> list[int]:
    highs: list[int] = []
    for i in range(min_bars, len(bars) - min_bars):
        if math.isnan(macd_hist(bars[i])):
            continue
        is_high = True
        for j in range(i - min_bars, i + min_bars + 1):
            if j != i and not math.isnan(macd_hist(bars[j])) and macd_hist(bars[j]) >= macd_hist(bars[i]):
                is_high = False
                break
        if is_high:
            highs.append(i)
    return highs


def find_nearest_macd_low(bars: list[IndicatorBar], idx: int) -> int:
    best = -1
    best_dist = 999
    for i in range(len(bars)):
        if math.isnan(macd_hist(bars[i])):
            continue
        is_low = True
        for j in range(max(0, i - 2), min(len(bars) - 1, i + 2) + 1):
            if j != i and not math.isnan(macd_hist(bars[j])) and macd_hist(bars[j]) <= macd_hist(bars[i]):
                is_low = False
                break
        if is_low:
            dist = abs(i - idx)
            if dist < best_dist:
                best_dist = dist
                best = i
    return best


def find_nearest_macd_high(bars: list[IndicatorBar], idx: int) -> int:
    best = -1
    best_dist = 999
    for i in range(len(bars)):
        if math.isnan(macd_hist(bars[i])):
            continue
        is_high = True
        for j in range(max(0, i - 2), min(len(bars) - 1, i + 2) + 1):
            if j != i and not math.isnan(macd_hist(bars[j])) and macd_hist(bars[j]) >= macd_hist(bars[i]):
                is_high = False
                break
        if is_high:
            dist = abs(i - idx)
            if dist < best_dist:
                best_dist = dist
                best = i
    return best


def calculate_divergence_strength(
    bars: list[IndicatorBar], p1: int, p2: int, m1: int, m2: int
) -> str:
    price_diff = abs(bars[p2]["low"] - bars[p1]["low"])
    macd_diff = abs(macd_hist(bars[m2]) - macd_hist(bars[m1]))
    ratio = price_diff / (macd_diff + 0.0001)
    if ratio > 3:
        return "strong"
    if ratio > 1.5:
        return "moderate"
    return "weak"


def calculate_rsi_divergence_strength(bars: list[IndicatorBar], p1: int, p2: int) -> str:
    diff = abs(rsi_value(bars[p2]) - rsi_value(bars[p1]))
    if diff > 10:
        return "strong"
    if diff > 5:
        return "moderate"
    return "weak"


def calculate_confidence(
    bars: list[IndicatorBar], p1: int, p2: int, m1: int, m2: int
) -> float:
    score = 0.5
    price_diff = abs(bars[p2]["low"] - bars[p1]["low"]) / bars[p1]["low"]
    if price_diff > 0.01:
        score += 0.1
    macd_diff = abs(macd_hist(bars[m2]) - macd_hist(bars[m1]))
    if not math.isnan(macd_diff) and macd_diff > 0.1:
        score += 0.1
    if p2 > p1 and p2 - p1 > 5:
        score += 0.1
    return min(score, 1)


def calculate_rsi_confidence(bars: list[IndicatorBar], p1: int, p2: int) -> float:
    score = 0.5
    diff = abs(rsi_value(bars[p2]) - rsi_value(bars[p1]))
    if not math.isnan(diff) and diff > 5:
        score += 0.2
    if not math.isnan(diff) and diff > 10:
        score += 0.1
    rsi2 = rsi_value(bars[p2])
    if not math.isnan(rsi2) and (rsi2 < 30 or rsi2 > 70):
        score += 0.1
    return min(score, 1)


def macd_hist(bar: IndicatorBar) -> float:
    value = bar.get("macdHist")
    if value is None:
        return _NAN
    try:
        return float(value)
    except (TypeError, ValueError):
        return _NAN


def rsi_value(bar: IndicatorBar) -> float:
    value = bar.get("rsi")
    if value is None:
        return _NAN
    try:
        return float(value)
    except (TypeError, ValueError):
        return _NAN
