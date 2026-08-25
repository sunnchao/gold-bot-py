"""镜像 apps/app-agent/src/tools/chanlun-core.ts。

缠论核心:包含处理 / 分型 / 笔 / 中枢。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from typing import Literal

from backend.agents.types.analysis import (
    ChanlunAnalysis,
    ChanlunBar,
    ChanlunFractal,
    ChanlunHub,
    ChanlunStroke,
)

__all__ = ["analyze_chanlun", "build_hubs", "build_strokes", "detect_fractals", "process_containment"]

Direction = Literal["up", "down"]


def _contains(left: ChanlunBar, right: ChanlunBar) -> bool:
    return (
        (left.high >= right.high and left.low <= right.low)
        or (right.high >= left.high and right.low <= left.low)
    )


def _infer_direction(previous: ChanlunBar, current: ChanlunBar) -> Direction:
    if current.high > previous.high or current.low > previous.low:
        return "up"
    return "down"


def process_containment(bars: Sequence[ChanlunBar]) -> list[ChanlunBar]:
    """镜像 processContainment:包含关系合并,按前一周期方向取极值。"""
    if len(bars) <= 1:
        return list(bars)

    processed: list[ChanlunBar] = [bars[0]]

    for index in range(1, len(bars)):
        current = bars[index]
        previous = processed[-1]

        if not _contains(previous, current):
            processed.append(current)
            continue

        base = processed[-2] if len(processed) >= 2 else previous
        direction = _infer_direction(base, previous)
        processed[-1] = replace(
            previous,
            high=max(previous.high, current.high) if direction == "up" else min(previous.high, current.high),
            low=max(previous.low, current.low) if direction == "up" else min(previous.low, current.low),
            close=current.close,
        )

    return processed


def detect_fractals(bars: Sequence[ChanlunBar]) -> list[ChanlunFractal]:
    """镜像 detectFractals:顶/底分型(中间 K 线高/低为局部极值)。"""
    fractals: list[ChanlunFractal] = []

    for index in range(1, len(bars) - 1):
        left = bars[index - 1]
        middle = bars[index]
        right = bars[index + 1]

        if middle.high > left.high and middle.high > right.high:
            fractals.append(
                ChanlunFractal(type="top", index=middle.index, price=middle.high, confirmed=True)
            )
            continue

        if middle.low < left.low and middle.low < right.low:
            fractals.append(
                ChanlunFractal(type="bottom", index=middle.index, price=middle.low, confirmed=True)
            )

    return fractals


def build_strokes(fractals: Sequence[ChanlunFractal]) -> list[ChanlunStroke]:
    """镜像 buildStrokes:交替分型且间隔 >= 2 根成笔。"""
    strokes: list[ChanlunStroke] = []

    for index in range(1, len(fractals)):
        previous = fractals[index - 1]
        current = fractals[index]

        if previous.type == current.type:
            continue

        if current.index - previous.index < 2:
            continue

        strokes.append(
            ChanlunStroke(
                startIndex=previous.index,
                endIndex=current.index,
                startPrice=previous.price,
                endPrice=current.price,
                direction="up" if current.price >= previous.price else "down",
                high=max(previous.price, current.price),
                low=min(previous.price, current.price),
            )
        )

    return strokes


def build_hubs(strokes: Sequence[ChanlunStroke]) -> list[ChanlunHub]:
    """镜像 buildHubs:连续 3 笔重叠成中枢。"""
    hubs: list[ChanlunHub] = []

    for index in range(0, len(strokes) - 2):
        window = strokes[index : index + 3]
        high = min(stroke.high for stroke in window)
        low = max(stroke.low for stroke in window)

        if low > high:
            continue

        hubs.append(
            ChanlunHub(
                startIndex=window[0].startIndex,
                endIndex=window[2].endIndex,
                high=high,
                low=low,
                strokeIndices=(index, index + 1, index + 2),
            )
        )

    return hubs


def analyze_chanlun(bars: Sequence[ChanlunBar]) -> ChanlunAnalysis:
    """镜像 analyzeChanlun:包含 → 分型 → 笔 → 中枢。"""
    processed_bars = process_containment(bars)
    fractals = detect_fractals(processed_bars)
    strokes = build_strokes(fractals)
    hubs = build_hubs(strokes)

    return ChanlunAnalysis(
        processedBars=processed_bars,
        fractals=fractals,
        strokes=strokes,
        hubs=hubs,
    )
