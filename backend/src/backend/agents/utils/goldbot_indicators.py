"""镜像 apps/app-agent/src/utils/goldbot-indicators.ts。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

__all__ = ["select_indicator"]

EMPTY_INDICATOR: dict[str, Any] = {}


def select_indicator(indicators: Mapping[str, Any], *timeframes: str) -> Mapping[str, Any]:
    """按优先级取第一个存在的时间框架指标包;都没有返回空快照。"""
    for timeframe in timeframes:
        indicator = indicators.get(timeframe)
        if indicator:
            return indicator
    return EMPTY_INDICATOR
