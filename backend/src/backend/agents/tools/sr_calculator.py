"""镜像 apps/app-agent/src/tools/sr-calculator.ts。

支撑/阻力计算器 — 关键位检测纯函数:swing points / Fibonacci / pivot / 心理价位。
数值语义(浮点、Math.round、边界)与 TS 逐条一致。
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from backend.agents.types.goldbot import BarData

__all__ = [
    "FibonacciExtensions",
    "FibonacciLevels",
    "PivotPoints",
    "PsychologicalLevel",
    "SwingPoint",
    "SwingPoints",
    "calculate_fibonacci",
    "calculate_fibonacci_extensions",
    "calculate_pivot_points",
    "calculate_swing_points",
    "find_psychological_levels",
]


@dataclass
class SwingPoint:
    price: float
    index: int


@dataclass
class SwingPoints:
    highs: list[SwingPoint]
    lows: list[SwingPoint]


@dataclass
class FibonacciLevels:
    level_0: float  # Low
    level_0_236: float
    level_0_382: float
    level_0_5: float
    level_0_618: float
    level_0_786: float
    level_1: float  # High


@dataclass
class FibonacciExtensions:
    level_1_272: float
    level_1_618: float
    level_2_0: float
    level_2_618: float


@dataclass
class PivotPoints:
    pivot: float
    r1: float
    r2: float
    r3: float
    s1: float
    s2: float
    s3: float


@dataclass
class PsychologicalLevel:
    price: float
    label: str


def _math_round(value: float) -> float:
    # 镜像 JS Math.round:floor(x + 0.5)
    return math.floor(value + 0.5)


# ---------------------------------------------------------------- Swing Points


def calculate_swing_points(bars: Sequence[BarData], lookback: int = 20) -> SwingPoints:
    """镜像 calculateSwingPoints:lookback 窗口内局部最高/最低。"""
    highs: list[SwingPoint] = []
    lows: list[SwingPoint] = []

    if len(bars) < lookback * 2 + 1:
        return SwingPoints(highs=highs, lows=lows)

    half_lookback = math.floor(lookback / 2)

    for i in range(half_lookback, len(bars) - half_lookback):
        current = bars[i]
        is_swing_high = True
        is_swing_low = True

        for j in range(i - half_lookback, i + half_lookback + 1):
            if j == i:
                continue
            bar = bars[j]
            if bar.high >= current.high:
                is_swing_high = False
            if bar.low <= current.low:
                is_swing_low = False

        if is_swing_high:
            highs.append(SwingPoint(price=current.high, index=i))
        if is_swing_low:
            lows.append(SwingPoint(price=current.low, index=i))

    return SwingPoints(highs=highs, lows=lows)


# ---------------------------------------------------------------- Fibonacci Levels


def calculate_fibonacci(high: float, low: float) -> FibonacciLevels:
    """镜像 calculateFibonacci:基于 high-low 区间的回调位。"""
    diff = high - low
    return FibonacciLevels(
        level_0=low,
        level_0_236=low + diff * 0.236,
        level_0_382=low + diff * 0.382,
        level_0_5=low + diff * 0.5,
        level_0_618=low + diff * 0.618,
        level_0_786=low + diff * 0.786,
        level_1=high,
    )


def calculate_fibonacci_extensions(
    wave_start: float,
    wave_end: float,
    retracement_end: float,
    direction: Literal["bullish", "bearish"],
) -> FibonacciExtensions:
    """镜像 calculateFibonacciExtensions:从冲动波 + 回撤计算延伸目标位。"""
    diff = abs(wave_end - wave_start)

    if direction == "bullish":
        return FibonacciExtensions(
            level_1_272=retracement_end + diff * 1.272,
            level_1_618=retracement_end + diff * 1.618,
            level_2_0=retracement_end + diff * 2.0,
            level_2_618=retracement_end + diff * 2.618,
        )

    return FibonacciExtensions(
        level_1_272=retracement_end - diff * 1.272,
        level_1_618=retracement_end - diff * 1.618,
        level_2_0=retracement_end - diff * 2.0,
        level_2_618=retracement_end - diff * 2.618,
    )


# ---------------------------------------------------------------- Pivot Points


def calculate_pivot_points(bars: Sequence[BarData]) -> PivotPoints | None:
    """镜像 calculatePivotPoints:用最后一根完整 K 线计算经典 pivots。"""
    if len(bars) == 0:
        return None

    last = bars[-1]
    high = last.high
    low = last.low
    close = last.close

    pivot = (high + low + close) / 3

    return PivotPoints(
        pivot=pivot,
        r1=2 * pivot - low,
        r2=pivot + (high - low),
        r3=high + 2 * (pivot - low),
        s1=2 * pivot - high,
        s2=pivot - (high - low),
        s3=low - 2 * (high - pivot),
    )


# ---------------------------------------------------------------- Psychological Levels


def find_psychological_levels(
    price: float,
    range_: float = 100,
    max_distance: float | None = None,
) -> list[PsychologicalLevel]:
    """镜像 findPsychologicalLevels:找出当前价附近的心理价位,过滤过远位。

    - 步长按价格量级:>=1000 → 50(Gold);>=100 → 10(forex major);
      >=10 → 5;其余 → 1。
    - 默认 maxDistance = step * 3。
    """
    levels: list[PsychologicalLevel] = []

    if price >= 1000:
        step = 50.0  # Gold/XAUUSD: 50 points
    elif price >= 100:
        step = 10.0  # Forex major pairs (GBPJPY, EURJPY): 10 points (not 25!)
    elif price >= 10:
        step = 5.0
    else:
        step = 1.0

    effective_max_distance = max_distance if max_distance is not None else step * 3

    lower_bound = max(price - range_, price - effective_max_distance)
    upper_bound = min(price + range_, price + effective_max_distance)

    start = math.ceil(lower_bound / step) * step

    level = start
    while level <= upper_bound:
        rounded_level = _math_round(level * 100) / 100
        distance = abs(rounded_level - price)

        # 只保留在 effectiveMaxDistance 内的价位
        if distance <= effective_max_distance:
            is_round = rounded_level % (step * 10) == 0
            if is_round:
                label = f"Major Round {rounded_level}"
            elif rounded_level % (step * 2) == 0:
                label = f"Round {rounded_level}"
            else:
                label = f"Half {rounded_level}"
            levels.append(PsychologicalLevel(price=rounded_level, label=label))

        level += step

    return levels
