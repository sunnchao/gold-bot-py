"""M30 Breakout Cache(镜像 packages/trading-core/src/replay/breakout-cache.ts)。

两步 Bollinger Band 突破确认:先在 H1 收盘突破 BB 时缓存阈值,再等 M30 收盘
仍在其外时确认信号,降低假突破。dict 键保持 TS camelCase。
"""

from __future__ import annotations

import math
import time
from fractions import Fraction
from typing import Any

__all__ = [
    "BreakoutCache",
    "BreakoutCacheEntry",
    "BreakoutConfirmResult",
    "confirm_breakout_pyramid",
    "get_breakout_cache",
]

BreakoutCacheEntry = dict[str, Any]
"""镜像 BreakoutCacheEntry:bbLevel / triggerTime(Unix 毫秒)/ side。"""

BreakoutConfirmResult = dict[str, Any]
"""镜像 BreakoutConfirmResult:confirmed / signal / reason。"""

_TTL_MS = 3600 * 1000  # 1 hour in milliseconds


class BreakoutCache:
    """镜像 BreakoutCache:内存 Map,按 `SYMBOL:SIDE` 键存待确认突破。

    生产环境多实例时应替换为 Redis。
    """

    def __init__(self) -> None:
        self._cache: dict[str, BreakoutCacheEntry] = {}

    def _make_key(self, symbol: str, side: str) -> str:
        return f"{symbol.upper().strip()}:{side}"

    def set(self, symbol: str, side: str, bb_level: float) -> None:
        key = self._make_key(symbol, side)
        self._cache[key] = {
            "bbLevel": bb_level,
            "triggerTime": time.time() * 1000,
            "side": side,
        }

    def get(self, symbol: str, side: str) -> BreakoutCacheEntry | None:
        key = self._make_key(symbol, side)
        entry = self._cache.get(key)

        if entry is None:
            return None

        # Check TTL
        if time.time() * 1000 - entry["triggerTime"] > _TTL_MS:
            del self._cache[key]
            return None

        return entry

    def delete(self, symbol: str, side: str) -> None:
        key = self._make_key(symbol, side)
        self._cache.pop(key, None)

    def clear(self) -> None:
        self._cache.clear()


_breakout_cache_instance: BreakoutCache | None = None


def get_breakout_cache() -> BreakoutCache:
    """镜像 getBreakoutCache:进程内惰性单例。"""
    global _breakout_cache_instance
    if _breakout_cache_instance is None:
        _breakout_cache_instance = BreakoutCache()
    return _breakout_cache_instance


def _to_fixed(value: float, digits: int) -> str:
    """镜像 JS Number.prototype.toFixed:tie 时取更大的 n(朝 +∞)。

    基于二进制浮点值的精确有理展开(Fraction),与 JS 在精确 double 上取整的
    语义一致,而非 Python 的 round-half-even 字符串格式化。
    """
    if math.isnan(value):
        return "NaN"
    if math.isinf(value):
        return "Infinity" if value > 0 else "-Infinity"
    scaled = Fraction(value) * (10**digits)
    q, r = divmod(scaled.numerator, scaled.denominator)
    n = q + (1 if r * 2 >= scaled.denominator else 0)
    sign = "-" if n < 0 else ""
    digit_str = str(abs(n)).zfill(digits + 1)
    if digits == 0:
        return f"{sign}{digit_str}"
    return f"{sign}{digit_str[:-digits]}.{digit_str[-digits:]}"


def confirm_breakout_pyramid(
    symbol: str,
    side: str,
    bb_level: float,
    m30_bars: list[dict[str, Any]],
    signal: Any,
    signal_message: str,
) -> BreakoutConfirmResult:
    """镜像 confirmBreakoutPyramid:H1 突破后缓存,次日 M30 收盘仍在 BB 外才确认。

    两步流程:
    1. H1 收盘突破 BB → 缓存阈值
    2. M30 收盘仍在 BB 外 → 确认信号(约降低 30% 假突破)
    """
    cache = get_breakout_cache()

    # Check if there's a pending breakout waiting for confirmation
    pending = cache.get(symbol, side)

    if pending is not None:
        # Delete the cache entry (confirmed or rejected)
        cache.delete(symbol, side)

        # Check M30 confirmation
        if len(m30_bars) == 0:
            return {
                "confirmed": True,
                "signal": signal,
                "reason": f"{signal_message} | 二次确认: M30数据不足,按H1突破降级发信号",
            }

        m30_close = m30_bars[-1]["close"]
        confirmed = (side == "BUY" and m30_close > pending["bbLevel"]) or (
            side == "SELL" and m30_close < pending["bbLevel"]
        )

        if confirmed:
            return {
                "confirmed": True,
                "signal": signal,
                "reason": (
                    f"{signal_message} | 二次确认: M30收盘价={_to_fixed(m30_close, 2)} "
                    f"仍在BB外(阈值={_to_fixed(pending['bbLevel'], 2)})"
                ),
            }

        # False breakout detected
        return {
            "confirmed": False,
            "signal": None,
            "reason": (
                f"假突破: {side} M30收盘价={_to_fixed(m30_close, 2)} "
                f"回到BB内(阈值={_to_fixed(pending['bbLevel'], 2)})"
            ),
        }

    # No pending entry → first step: cache and wait for M30
    cache.set(symbol, side, bb_level)

    return {
        "confirmed": False,
        "signal": None,
        "reason": f"待确认: {side} H1收盘价突破BB阈值={_to_fixed(bb_level, 2)},等待M30二次确认",
    }
