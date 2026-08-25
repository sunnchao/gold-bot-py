"""镜像 apps/app-agent/src/tools/pattern-detector.ts。

基于回归趋势线的价格形态检测。所有函数为纯函数,数值/舍入语义与 TS 一致。
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

__all__ = [
    "ChannelPattern",
    "RegressionPoint",
    "TrianglePattern",
    "WedgePattern",
    "detect_channel",
    "detect_triangle",
    "detect_wedge",
    "linear_regression",
]

Direction3 = Literal["bullish", "bearish", "neutral"] | None


@dataclass
class WedgePattern:
    type: Literal["rising_wedge", "falling_wedge", "none"]
    direction: Literal["bearish", "bullish"] | None
    upperLine: LineDescriptor
    lowerLine: LineDescriptor
    breakoutPrice: float | None
    confidence: float
    barsCount: int


@dataclass
class ChannelPattern:
    type: Literal["ascending_channel", "descending_channel", "horizontal_channel", "none"]
    direction: Direction3
    upperLine: LineDescriptor
    lowerLine: LineDescriptor
    confidence: float
    barsCount: int


@dataclass
class TrianglePattern:
    type: Literal["symmetrical", "ascending", "descending", "none"]
    direction: Literal["continuation", "breakout_up", "breakout_down"] | None
    upperLine: LineDescriptor
    lowerLine: LineDescriptor
    apexPrice: float | None
    confidence: float
    barsCount: int


@dataclass
class LineDescriptor:
    start: float
    end: float
    slope: float


@dataclass
class RegressionLine:
    slope: float
    intercept: float


@dataclass
class WindowData:
    highs: list[float]
    lows: list[float]
    closes: list[float] | None
    volumes: list[float] | None
    startIndex: int


@dataclass(frozen=True)
class RegressionPoint:
    x: float
    y: float


_MAX_SAFE_INTEGER = 9007199254740991


def _math_round(value: float) -> float:
    # 镜像 JS Math.round:floor(x + 0.5)
    return math.floor(value + 0.5)


def linear_regression(points: Sequence[RegressionPoint]) -> RegressionLine:
    """最小二乘线性回归。空点集 → slope 0, intercept 0。"""
    if len(points) == 0:
        return RegressionLine(slope=0, intercept=0)

    count = len(points)
    sum_x = 0.0
    sum_y = 0.0
    sum_xy = 0.0
    sum_xx = 0.0

    for point in points:
        sum_x += point.x
        sum_y += point.y
        sum_xy += point.x * point.y
        sum_xx += point.x * point.x

    denominator = count * sum_xx - sum_x * sum_x
    if denominator == 0:
        return RegressionLine(slope=0, intercept=sum_y / count)

    slope = (count * sum_xy - sum_x * sum_y) / denominator
    intercept = (sum_y - slope * sum_x) / count

    return RegressionLine(slope=slope, intercept=intercept)


def detect_wedge(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    volumes: Sequence[float],
    lookback: int = 50,
) -> list[WedgePattern]:
    """镜像 detectWedge:上升/下降楔形,带量能收缩与突破确认。"""
    window = build_window(
        highs,
        lows,
        list(closes) if closes is not None else None,
        list(volumes) if volumes is not None else None,
        lookback,
    )
    if window is None:
        return []

    upper_regression = build_regression(window.highs)
    lower_regression = build_regression(window.lows)
    upper_line = build_line_descriptor(upper_regression, window.startIndex, len(window.highs))
    lower_line = build_line_descriptor(lower_regression, window.startIndex, len(window.lows))
    last_index = len(window.highs) - 1
    upper_at_last = project(upper_regression, last_index)
    lower_at_last = project(lower_regression, last_index)
    converging = width_at(upper_regression, lower_regression, 0) > width_at(
        upper_regression, lower_regression, last_index
    )
    volume_contracting = is_volume_contracting(window.volumes or [])
    last_close = window.closes[last_index] if window.closes else 0

    if (
        upper_regression.slope > 0.05
        and lower_regression.slope > 0.05
        and lower_regression.slope > upper_regression.slope
        and converging
        and volume_contracting
        and last_close < lower_at_last
    ):
        return [
            WedgePattern(
                type="rising_wedge",
                direction="bearish",
                upperLine=upper_line,
                lowerLine=lower_line,
                breakoutPrice=window.closes[last_index] if window.closes else None,
                confidence=clamp_confidence(
                    45
                    + slope_gap_score(lower_regression.slope - upper_regression.slope)
                    + convergence_score(upper_regression, lower_regression, last_index)
                    + 15
                ),
                barsCount=len(window.highs),
            )
        ]

    if (
        upper_regression.slope < -0.05
        and lower_regression.slope < -0.05
        and abs(upper_regression.slope) > abs(lower_regression.slope)
        and converging
        and volume_contracting
        and last_close > upper_at_last
    ):
        return [
            WedgePattern(
                type="falling_wedge",
                direction="bullish",
                upperLine=upper_line,
                lowerLine=lower_line,
                breakoutPrice=window.closes[last_index] if window.closes else None,
                confidence=clamp_confidence(
                    45
                    + slope_gap_score(abs(upper_regression.slope) - abs(lower_regression.slope))
                    + convergence_score(upper_regression, lower_regression, last_index)
                    + 15
                ),
                barsCount=len(window.highs),
            )
        ]

    return []


def detect_channel(
    highs: Sequence[float],
    lows: Sequence[float],
    lookback: int = 50,
) -> list[ChannelPattern]:
    """镜像 detectChannel:平行通道(上升/下降/水平)。"""
    window = build_window(highs, lows, None, None, lookback)
    if window is None:
        return []

    upper_regression = build_regression(window.highs)
    lower_regression = build_regression(window.lows)
    upper_line = build_line_descriptor(upper_regression, window.startIndex, len(window.highs))
    lower_line = build_line_descriptor(lower_regression, window.startIndex, len(window.lows))
    slope_diff = abs(upper_regression.slope - lower_regression.slope)
    width_start = width_at(upper_regression, lower_regression, 0)
    width_end = width_at(upper_regression, lower_regression, len(window.highs) - 1)
    width_stability = 0 if width_start == 0 else 1 - min(abs(width_end - width_start) / width_start, 1)

    if slope_diff > 0.12 or width_start <= 0 or width_end <= 0:
        return []

    pattern_type: Literal["ascending_channel", "descending_channel", "horizontal_channel", "none"] = "none"
    direction: Direction3 = None

    if upper_regression.slope > 0.05 and lower_regression.slope > 0.05:
        pattern_type = "ascending_channel"
        direction = "bullish"
    elif upper_regression.slope < -0.05 and lower_regression.slope < -0.05:
        pattern_type = "descending_channel"
        direction = "bearish"
    elif abs(upper_regression.slope) <= 0.05 and abs(lower_regression.slope) <= 0.05:
        pattern_type = "horizontal_channel"
        direction = "neutral"

    if pattern_type == "none":
        return []

    return [
        ChannelPattern(
            type=pattern_type,
            direction=direction,
            upperLine=upper_line,
            lowerLine=lower_line,
            confidence=clamp_confidence(
                50 + (1 - min(slope_diff / 0.12, 1)) * 25 + width_stability * 25
            ),
            barsCount=len(window.highs),
        )
    ]


def detect_triangle(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    lookback: int = 50,
) -> list[TrianglePattern]:
    """镜像 detectTriangle:对称/上升/下降三角形。"""
    window = build_window(highs, lows, list(closes) if closes is not None else None, None, lookback)
    if window is None:
        return []

    upper_regression = build_regression(window.highs)
    lower_regression = build_regression(window.lows)
    upper_line = build_line_descriptor(upper_regression, window.startIndex, len(window.highs))
    lower_line = build_line_descriptor(lower_regression, window.startIndex, len(window.lows))
    last_index = len(window.highs) - 1

    pattern_type: Literal["symmetrical", "ascending", "descending", "none"] = "none"
    base_confidence = 0.0

    if (
        upper_regression.slope < -0.05
        and lower_regression.slope > 0.05
        and abs(abs(upper_regression.slope) - abs(lower_regression.slope)) <= 0.12
    ):
        pattern_type = "symmetrical"
        base_confidence = 55
    elif abs(upper_regression.slope) <= 0.05 and lower_regression.slope > 0.05:
        pattern_type = "ascending"
        base_confidence = 60
    elif upper_regression.slope < -0.05 and abs(lower_regression.slope) <= 0.05:
        pattern_type = "descending"
        base_confidence = 60

    if pattern_type == "none":
        return []

    upper_at_last = project(upper_regression, last_index)
    lower_at_last = project(lower_regression, last_index)
    breakout_buffer = max((upper_at_last - lower_at_last) * 0.05, 0.15)
    direction: Literal["continuation", "breakout_up", "breakout_down"] = "continuation"
    last_close = window.closes[last_index] if window.closes else 0

    if last_close > upper_at_last + breakout_buffer:
        direction = "breakout_up"
    elif last_close < lower_at_last - breakout_buffer:
        direction = "breakout_down"

    return [
        TrianglePattern(
            type=pattern_type,
            direction=direction,
            upperLine=upper_line,
            lowerLine=lower_line,
            apexPrice=calculate_apex_price(upper_regression, lower_regression),
            confidence=clamp_confidence(
                base_confidence + convergence_score(upper_regression, lower_regression, last_index)
            ),
            barsCount=len(window.highs),
        )
    ]


def build_window(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: list[float] | None,
    volumes: list[float] | None,
    lookback: int,
) -> WindowData | None:
    count = min(
        len(highs),
        len(lows),
        len(closes) if closes is not None else _MAX_SAFE_INTEGER,
        len(volumes) if volumes is not None else _MAX_SAFE_INTEGER,
    )

    if count < 5:
        return None

    bars_count = min(count, lookback)
    start_index = count - bars_count

    return WindowData(
        highs=list(highs[start_index:count]),
        lows=list(lows[start_index:count]),
        closes=closes[start_index:count] if closes is not None else None,
        volumes=volumes[start_index:count] if volumes is not None else None,
        startIndex=start_index,
    )


def build_regression(values: Sequence[float]) -> RegressionLine:
    return linear_regression([RegressionPoint(x=float(index), y=float(value)) for index, value in enumerate(values)])


def build_line_descriptor(line: RegressionLine, start_index: int, length: int) -> LineDescriptor:
    return LineDescriptor(
        start=project(line, 0),
        end=project(line, max(length - 1, 0)),
        slope=line.slope,
    )


def project(line: RegressionLine, x: float) -> float:
    return line.intercept + line.slope * x


def width_at(upper: RegressionLine, lower: RegressionLine, x: float) -> float:
    return project(upper, x) - project(lower, x)


def is_volume_contracting(volumes: Sequence[float]) -> bool:
    if len(volumes) < 6:
        return False

    half = math.floor(len(volumes) / 2)
    first_half = average(volumes[:half])
    second_half = average(volumes[half:])

    return second_half < first_half * 0.95


def average(values: Sequence[float]) -> float:
    if len(values) == 0:
        return 0
    return sum(values) / len(values)


def calculate_apex_price(upper: RegressionLine, lower: RegressionLine) -> float | None:
    slope_delta = upper.slope - lower.slope
    if abs(slope_delta) < 1e-9:
        return None

    x = (lower.intercept - upper.intercept) / slope_delta
    return project(upper, x)


def clamp_confidence(value: float) -> float:
    return max(0, min(100, _math_round(value)))


def slope_gap_score(gap: float) -> float:
    return min(abs(gap) * 40, 20)


def convergence_score(upper: RegressionLine, lower: RegressionLine, last_index: int) -> float:
    start_width = width_at(upper, lower, 0)
    end_width = width_at(upper, lower, last_index)
    if start_width <= 0:
        return 0

    return min(((start_width - end_width) / start_width) * 20, 20)
