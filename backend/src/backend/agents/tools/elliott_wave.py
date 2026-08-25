"""镜像 apps/app-agent/src/tools/elliott-wave.ts。

波浪理论纯函数:swing points 检测 / 冲动浪标注 / 修正浪标注 / 波浪规则校验。
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Literal

from backend.agents.types.analysis import (
    ElliottWaveAnalysis,
    ElliottWaveLabel,
    ElliottWaveSegment,
    ElliottWaveSwingPoint,
    ElliottWaveValidation,
)

__all__ = [
    "analyze_elliott_wave",
    "detect_swing_points",
    "label_corrective_waves",
    "label_impulse_waves",
    "validate_wave_rules",
]

Direction = Literal["bullish", "bearish"]
SwingType = Literal["high", "low"]


def _relative_move(frm: float, to: float) -> float:
    if frm == 0:
        return 0
    return abs(to - frm) / abs(frm)


def _build_segment(
    wave: ElliottWaveLabel,
    start: ElliottWaveSwingPoint,
    end: ElliottWaveSwingPoint,
) -> ElliottWaveSegment:
    return ElliottWaveSegment(
        wave=wave,
        startIndex=start.index,
        endIndex=end.index,
        startPrice=start.price,
        endPrice=end.price,
        direction="up" if end.price >= start.price else "down",
        length=abs(end.price - start.price),
    )


def _expected_pattern(direction: Direction, kind: Literal["impulse", "correction"]) -> list[SwingType]:
    if kind == "impulse":
        return (
            ["low", "high", "low", "high", "low", "high"]
            if direction == "bullish"
            else ["high", "low", "high", "low", "high", "low"]
        )
    return (
        ["high", "low", "high", "low"]
        if direction == "bullish"
        else ["low", "high", "low", "high"]
    )


def _matches_pattern(points: Sequence[ElliottWaveSwingPoint], pattern: Sequence[SwingType]) -> bool:
    return len(points) == len(pattern) and all(
        point.type == pattern[index] for index, point in enumerate(points)
    )


def _has_impulse_structure(points: Sequence[ElliottWaveSwingPoint], direction: Direction) -> bool:
    if direction == "bullish":
        return (
            points[1].price > points[0].price
            and points[2].price > points[0].price
            and points[3].price > points[1].price
            and points[4].price > points[2].price
            and points[5].price > points[3].price
        )
    return (
        points[1].price < points[0].price
        and points[2].price < points[0].price
        and points[3].price < points[1].price
        and points[4].price < points[2].price
        and points[5].price < points[3].price
    )


def _clamp_confidence(value: int) -> int:
    return max(0, min(100, _math_round(value)))


def _math_round(value: float) -> int:
    # 镜像 JS Math.round:floor(x + 0.5)
    return math.floor(value + 0.5)


def detect_swing_points(prices: Sequence[float], min_move_percent: float = 0.01) -> list[ElliottWaveSwingPoint]:
    """镜像 detectSwingPoints:zigzag 过滤后的摆动高低点。"""
    if len(prices) == 0:
        return []

    raw: list[ElliottWaveSwingPoint] = [ElliottWaveSwingPoint(index=0, price=prices[0], type="low")]

    for index in range(1, len(prices) - 1):
        prev = prices[index - 1]
        current = prices[index]
        next_ = prices[index + 1]

        if current > prev and current >= next_:
            raw.append(ElliottWaveSwingPoint(index=index, price=current, type="high"))
            continue

        if current < prev and current <= next_:
            raw.append(ElliottWaveSwingPoint(index=index, price=current, type="low"))

    last_index = len(prices) - 1
    last_type: SwingType = "low" if raw[-1].type == "high" else "high"
    raw.append(ElliottWaveSwingPoint(index=last_index, price=prices[last_index], type=last_type))

    filtered: list[ElliottWaveSwingPoint] = []

    for point in raw:
        previous = filtered[-1] if filtered else None
        if previous is None:
            filtered.append(point)
            continue

        if point.type == previous.type:
            should_replace = (point.type == "high" and point.price >= previous.price) or (
                point.type == "low" and point.price <= previous.price
            )
            if should_replace:
                filtered[-1] = point
            continue

        if _relative_move(previous.price, point.price) < min_move_percent:
            continue

        filtered.append(point)

    if len(filtered) > 1 and filtered[0].type == filtered[1].type:
        filtered.pop(0)

    return filtered


def label_impulse_waves(
    swing_points: Sequence[ElliottWaveSwingPoint],
    direction: Direction,
) -> list[ElliottWaveSegment]:
    """镜像 labelImpulseWaves:最近 6 个交替摆动点中寻找 5 浪推动结构。"""
    pattern = _expected_pattern(direction, "impulse")

    for start in range(max(0, len(swing_points) - 6), -1, -1):
        candidate = swing_points[start : start + 6]
        if not _matches_pattern(candidate, pattern):
            continue
        if not _has_impulse_structure(candidate, direction):
            continue

        return [
            _build_segment(1, candidate[0], candidate[1]),
            _build_segment(2, candidate[1], candidate[2]),
            _build_segment(3, candidate[2], candidate[3]),
            _build_segment(4, candidate[3], candidate[4]),
            _build_segment(5, candidate[4], candidate[5]),
        ]

    return []


def label_corrective_waves(
    swing_points: Sequence[ElliottWaveSwingPoint],
    direction: Direction,
    impulse_end_index: int | None = None,
) -> list[ElliottWaveSegment]:
    """镜像 labelCorrectiveWaves:推动浪后 4 个摆动点中寻找 ABC 修正。"""
    if impulse_end_index is None:
        start_at = 0
    else:
        found = next(
            (index for index, point in enumerate(swing_points) if point.index == impulse_end_index),
            None,
        )
        start_at = max(0, found) if found is not None else 0
    pattern = _expected_pattern(direction, "correction")

    for offset in range(start_at, len(swing_points) - 3):
        candidate = swing_points[offset : offset + 4]
        if not _matches_pattern(candidate, pattern):
            continue

        if direction == "bullish":
            if not (
                candidate[1].price < candidate[0].price
                and candidate[2].price < candidate[0].price
                and candidate[3].price < candidate[1].price
            ):
                continue
        elif not (
            candidate[1].price > candidate[0].price
            and candidate[2].price > candidate[0].price
            and candidate[3].price > candidate[1].price
        ):
            continue

        return [
            _build_segment("A", candidate[0], candidate[1]),
            _build_segment("B", candidate[1], candidate[2]),
            _build_segment("C", candidate[2], candidate[3]),
        ]

    return []


def validate_wave_rules(
    impulse_waves: Sequence[ElliottWaveSegment],
    direction: Direction,
) -> ElliottWaveValidation:
    """镜像 validateWaveRules:波浪 3 不是最短推动浪 / 2 不破 1 起点 / 4 不破 1 终点。"""
    violations: list[str] = []

    if len(impulse_waves) != 5:
        return ElliottWaveValidation(
            isValid=False,
            violations=["A valid impulse requires exactly 5 labeled waves."],
        )

    wave1, wave2, wave3, wave4, wave5 = impulse_waves
    motive_lengths = [wave1.length, wave3.length, wave5.length]

    if wave3.length == min(motive_lengths):
        violations.append("Wave 3 cannot be the shortest motive wave.")

    if direction == "bullish":
        if wave2.endPrice <= wave1.startPrice:
            violations.append("Wave 2 cannot retrace beyond the start of wave 1.")
        if wave4.endPrice <= wave1.endPrice:
            violations.append("Wave 4 cannot overlap the price territory of wave 1.")
    else:
        if wave2.endPrice >= wave1.startPrice:
            violations.append("Wave 2 cannot retrace beyond the start of wave 1.")
        if wave4.endPrice >= wave1.endPrice:
            violations.append("Wave 4 cannot overlap the price territory of wave 1.")

    return ElliottWaveValidation(isValid=len(violations) == 0, violations=violations)


def _determine_recent_direction(swing_points: Sequence[ElliottWaveSwingPoint]) -> Direction:
    if len(swing_points) < 2:
        return "bullish"

    recent = swing_points[-6:]
    return "bullish" if recent[-1].price >= recent[0].price else "bearish"


def analyze_elliott_wave(prices: Sequence[float], min_move_percent: float = 0.01) -> ElliottWaveAnalysis:
    """镜像 analyzeElliottWave:完整分析,含置信度聚合。"""
    swing_points = detect_swing_points(prices, min_move_percent)
    direction = _determine_recent_direction(swing_points)
    impulse_waves = label_impulse_waves(swing_points, direction)
    corrective_waves = (
        label_corrective_waves(swing_points, direction, impulse_waves[4].endIndex)
        if len(impulse_waves) == 5
        else []
    )
    validation = validate_wave_rules(impulse_waves, direction)

    confidence = 20
    if len(swing_points) >= 6:
        confidence += 20
    if len(impulse_waves) == 5:
        confidence += 30
    if len(corrective_waves) == 3:
        confidence += 10
    if validation.isValid:
        confidence += 20
    else:
        confidence -= len(validation.violations) * 5

    return ElliottWaveAnalysis(
        direction=direction,
        swingPoints=swing_points,
        impulseWaves=impulse_waves,
        correctiveWaves=corrective_waves,
        validation=validation,
        confidence=_clamp_confidence(confidence),
    )
