"""Fibonacci 扩展 TP(镜像 packages/trading-core/src/replay/fib-extension.ts)。

由 H4(优先)/H1 的最后 swing + ADX 门槛判定趋势,把 1.272/1.618 扩展位写入
同向 BUY/SELL 信号的 tp1/tp2。dict 键保持 TS camelCase。
"""

from __future__ import annotations

import math
from typing import Any

from backend.trading_core.engine.config import FibExtensionTPConfig

__all__ = [
    "FibExtension",
    "apply_fib_extension_tp",
    "calculate_fib_extension",
]

FibExtension = dict[str, float]
"""镜像 FibExtension:level1272 / level1618 / level2618。"""

_SwingResult = dict[str, Any]  # swingHigh / swingLow / trend
_Bar = dict[str, Any]
_Signal = dict[str, Any]


def _math_round(value: float) -> int:
    """镜像 JS Math.round:等价于 floor(x + 0.5)(半值朝 +∞ 取整)。"""
    return math.floor(value + 0.5)


def _round_to_precision(value: float, precision: int) -> float:
    factor = math.pow(10, precision)
    return _math_round(value * factor) / factor


def calculate_fib_extension(swing_high: float, swing_low: float, trend: str) -> FibExtension:
    """镜像 calculateFibExtension:上升趋势取 swing high 上方扩展,下降取下方。"""
    diff = abs(swing_high - swing_low)

    if trend == "UP":
        return {
            "level1272": _math_round((swing_high + diff * 1.272) * 100) / 100,
            "level1618": _math_round((swing_high + diff * 1.618) * 100) / 100,
            "level2618": _math_round((swing_high + diff * 2.618) * 100) / 100,
        }
    return {
        "level1272": _math_round((swing_low - diff * 1.272) * 100) / 100,
        "level1618": _math_round((swing_low - diff * 1.618) * 100) / 100,
        "level2618": _math_round((swing_low - diff * 2.618) * 100) / 100,
    }


def _detect_last_swing(bars: list[_Bar], window: int) -> _SwingResult:
    """镜像 detectLastSwing:取窗口内最后 swing high/low 及其先后关系定趋势。"""
    if len(bars) < window:
        return {"swingHigh": 0, "swingLow": 0, "trend": "NONE"}

    recent_bars = bars[-window:]
    swing_high = recent_bars[0]["high"]
    swing_low = recent_bars[0]["low"]
    swing_high_idx = 0
    swing_low_idx = 0

    for i in range(1, len(recent_bars)):
        if recent_bars[i]["high"] > swing_high:
            swing_high = recent_bars[i]["high"]
            swing_high_idx = i
        if recent_bars[i]["low"] < swing_low:
            swing_low = recent_bars[i]["low"]
            swing_low_idx = i

    # Determine trend: if low happened before high, trend is UP
    if swing_low_idx < swing_high_idx:
        trend = "UP"
    elif swing_high_idx < swing_low_idx:
        trend = "DOWN"
    else:
        trend = "NONE"

    return {"swingHigh": swing_high, "swingLow": swing_low, "trend": trend}


def apply_fib_extension_tp(
    signal: _Signal | None,
    h4_bars: list[_Bar],
    h1_bars: list[_Bar],
    price: float,
    atr: float,
    cfg: FibExtensionTPConfig,
    precision: int,
) -> _Signal | None:
    """镜像 applyFibExtensionTP:条件满足时用 Fib 扩展位覆盖信号 tp1/tp2。

    条件:
    - FibExtension 在配置中启用
    - ADX 达到最低门槛
    - H4(优先)或 H1 检测到有效 swing
    - 信号方向与趋势一致
    """
    if signal is None or not cfg.get("enabled"):
        return signal

    swing_result: _SwingResult = {"swingHigh": 0, "swingLow": 0, "trend": "NONE"}
    adx = 0

    # Try H4 first if preferred and available
    if cfg.get("useH4Preference") and len(h4_bars) >= cfg.get("swingWindow", 0):
        swing_result = _detect_last_swing(h4_bars, cfg.get("swingWindow", 0))
        last_h4 = h4_bars[-1]
        h4_adx = last_h4.get("adx")
        adx = h4_adx if h4_adx is not None else 0

    # Fallback to H1 if H4 didn't meet criteria or not preferred
    if (
        (
            not cfg.get("useH4Preference")
            or adx < cfg.get("minADX", 0)
            or swing_result["swingHigh"] == 0
            or swing_result["swingLow"] == 0
        )
        and len(h1_bars) >= cfg.get("swingWindow", 0)
    ):
        swing_result = _detect_last_swing(h1_bars, cfg.get("swingWindow", 0))
        last_h1 = h1_bars[-1]
        h1_adx = last_h1.get("adx")
        adx = h1_adx if h1_adx is not None else 0

    # Exit if ADX too weak or no valid swing detected
    if adx < cfg.get("minADX", 0) or swing_result["swingHigh"] == 0 or swing_result["swingLow"] == 0:
        return signal

    # Skip if no valid trend detected
    if swing_result["trend"] == "NONE":
        return signal

    ext = calculate_fib_extension(swing_result["swingHigh"], swing_result["swingLow"], swing_result["trend"])

    # Apply extension TP only if signal aligns with trend
    if signal["side"] == "BUY" and swing_result["trend"] == "UP":
        # For BUY: extension levels should be above current price
        if ext["level1272"] > price and ext["level1272"] - price > atr * 0.5:
            signal["tp1"] = _round_to_precision(ext["level1272"], precision)
        if ext["level1618"] > price and ext["level1618"] - price > atr * 1.0:
            signal["tp2"] = _round_to_precision(ext["level1618"], precision)
    elif signal["side"] == "SELL" and swing_result["trend"] == "DOWN":
        # For SELL: extension levels should be below current price
        if ext["level1272"] < price and price - ext["level1272"] > atr * 0.5:
            signal["tp1"] = _round_to_precision(ext["level1272"], precision)
        if ext["level1618"] < price and price - ext["level1618"] > atr * 1.0:
            signal["tp2"] = _round_to_precision(ext["level1618"], precision)

    return signal
