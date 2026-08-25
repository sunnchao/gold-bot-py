"""SR 支撑/阻力 SL/TP(镜像 packages/trading-core/src/replay/sr-sltp.ts)。

优先级:AI 建议 > SR 层级(EMA20/50、BB、Fib、Pivot)> ATR 兜底。
"""

from __future__ import annotations

import math
from typing import Any

__all__ = [
    "AIResult",
    "SRSLTPResult",
    "pick_sltp",
]

SRSLTPResult = dict[str, Any]
"""镜像 SRSLTPResult:sl / tp1 / tp2 / usedSR。"""

AIResult = dict[str, Any]
"""镜像 AIResult:suggestedSL / suggested_sl / suggestedTP / suggested_tp。"""

PickSLTPConfig = dict[str, Any]
"""镜像 PickSLTPConfig:sr*ATR 字段 + pullbackSLATR 系列或 pullback 子对象二选一。"""

_Bar = dict[str, Any]


def _math_round(value: float) -> int:
    """镜像 JS Math.round:等价于 floor(x + 0.5)(半值朝 +∞ 取整)。"""
    return math.floor(value + 0.5)


def _round_to_precision(value: float, precision: int) -> float:
    factor = math.pow(10, precision)
    return _math_round(value * factor) / factor


def _atr_fallback(
    side: str, price: float, atr: float, sl_atr: float, tp1_atr: float, tp2_atr: float, precision: int
) -> SRSLTPResult:
    """镜像 BUY/SELL 分支的 ATR 兜底返回。"""
    if side == "BUY":
        return {
            "sl": _round_to_precision(price - atr * sl_atr, precision),
            "tp1": _round_to_precision(price + atr * tp1_atr, precision),
            "tp2": _round_to_precision(price + atr * tp2_atr, precision),
            "usedSR": False,
        }
    return {
        "sl": _round_to_precision(price + atr * sl_atr, precision),
        "tp1": _round_to_precision(price - atr * tp1_atr, precision),
        "tp2": _round_to_precision(price - atr * tp2_atr, precision),
        "usedSR": False,
    }


def pick_sltp(
    side: str,
    price: float,
    last_bar: _Bar,
    atr: float,
    precision: int,
    cfg: PickSLTPConfig,
    ai_result: AIResult | None = None,
) -> SRSLTPResult:
    """镜像 pickSLTP:智能 SL/TP 落点。

    优先级:
    1. AI 建议(如有)
    2. SR 层级(EMA20/50、BB、Fib、Pivot)
    3. ATR 兜底
    """
    if "pullbackSLATR" in cfg:
        pullback_sl_atr = cfg["pullbackSLATR"]
        pullback_tp1_atr = cfg["pullbackTP1ATR"]
        pullback_tp2_atr = cfg["pullbackTP2ATR"]
    else:
        pullback_sl_atr = cfg["pullback"]["slAtr"]
        pullback_tp1_atr = cfg["pullback"]["tp1Atr"]
        pullback_tp2_atr = cfg["pullback"]["tp2Atr"]

    bb_upper = last_bar.get("bbUpper")
    if bb_upper is None:
        bb_upper = last_bar.get("bb_upper")
    if bb_upper is None:
        bb_upper = 0
    bb_lower = last_bar.get("bbLower")
    if bb_lower is None:
        bb_lower = last_bar.get("bb_lower")
    if bb_lower is None:
        bb_lower = 0

    # Step 1: Check AI override
    if ai_result:
        ai_sl = ai_result.get("suggestedSL")
        if ai_sl is None:
            ai_sl = ai_result.get("suggested_sl")
        ai_tp = ai_result.get("suggestedTP")
        if ai_tp is None:
            ai_tp = ai_result.get("suggested_tp")

        if ai_sl is not None and ai_sl > 0:
            sl = _round_to_precision(ai_sl, precision)
            if ai_tp is not None and ai_tp > 0:
                tp1 = _round_to_precision(ai_tp, precision)
            else:
                tp1 = _round_to_precision(
                    price + atr * pullback_tp1_atr if side == "BUY" else price - atr * pullback_tp1_atr,
                    precision,
                )
            tp2 = _round_to_precision(
                price + atr * pullback_tp2_atr if side == "BUY" else price - atr * pullback_tp2_atr,
                precision,
            )
            return {"sl": sl, "tp1": tp1, "tp2": tp2, "usedSR": True}

    # Step 2: Validate inputs
    if (
        price <= 0
        or atr <= 0
        or math.isnan(price)
        or math.isnan(atr)
        or not math.isfinite(price)
        or not math.isfinite(atr)
    ):
        return {"sl": 0, "tp1": 0, "tp2": 0, "usedSR": False}

    min_dist = cfg["srMinDistATR"] * atr
    max_dist = cfg["srMaxDistATR"] * atr
    buffer = cfg["srBufferATR"] * atr

    if min_dist <= 0 or max_dist <= 0 or min_dist > max_dist:
        return {"sl": 0, "tp1": 0, "tp2": 0, "usedSR": False}

    # Step 3: Find closest level within distance constraints
    def _closest_level(levels: list[float], want_below: bool) -> float:
        best = 0.0
        best_dist = float("inf")

        for level in levels:
            if level <= 0 or math.isnan(level) or not math.isfinite(level):
                continue
            if want_below and level >= price:
                continue
            if not want_below and level <= price:
                continue

            dist = abs(price - level)
            if dist < min_dist or dist > max_dist:
                continue

            if dist < best_dist:
                best = level
                best_dist = dist

        return best

    if side == "BUY":
        # BUY: SL below support, TP above resistance
        supports = [
            _coalesce_zero(last_bar.get("ema20")),
            _coalesce_zero(last_bar.get("ema50")),
            bb_lower,
            _coalesce_zero(last_bar.get("fib618")),
            _coalesce_zero(last_bar.get("fib786")),
            _coalesce_zero(last_bar.get("s1")),
        ]
        resistances = [
            _coalesce_zero(last_bar.get("ema20")),
            bb_upper,
            _coalesce_zero(last_bar.get("fib382")),
            _coalesce_zero(last_bar.get("r1")),
        ]

        sl_level = _closest_level(supports, True)
        tp_level = _closest_level(resistances, False)

        # Fallback to ATR if no valid SR found
        if sl_level <= 0 or tp_level <= 0:
            return _atr_fallback(side, price, atr, pullback_sl_atr, pullback_tp1_atr, pullback_tp2_atr, precision)

        sl = _round_to_precision(sl_level - buffer, precision)

        # Validate SL is below price and meets min distance
        if sl >= price or abs(price - sl) < min_dist:
            return _atr_fallback(side, price, atr, pullback_sl_atr, pullback_tp1_atr, pullback_tp2_atr, precision)

        tp1 = _round_to_precision(tp_level, precision)
        tp2 = tp1

        # Try to find a second TP level
        second_tp_level = _closest_level(
            [
                bb_upper,
                _coalesce_zero(last_bar.get("fib382")),
                _coalesce_zero(last_bar.get("r1")),
            ],
            False,
        )
        if second_tp_level > tp_level:
            tp2 = _round_to_precision(second_tp_level, precision)

        if tp2 < tp1:
            tp2 = tp1

        return {"sl": sl, "tp1": tp1, "tp2": tp2, "usedSR": True}

    if side == "SELL":
        # SELL: SL above resistance, TP below support
        resistances = [
            _coalesce_zero(last_bar.get("ema20")),
            bb_upper,
            _coalesce_zero(last_bar.get("fib382")),
            _coalesce_zero(last_bar.get("r1")),
        ]
        supports = [
            _coalesce_zero(last_bar.get("ema20")),
            bb_lower,
            _coalesce_zero(last_bar.get("fib618")),
            _coalesce_zero(last_bar.get("fib786")),
            _coalesce_zero(last_bar.get("s1")),
        ]

        sl_level = _closest_level(resistances, False)
        tp_level = _closest_level(supports, True)

        # Fallback to ATR if no valid SR found
        if sl_level <= 0 or tp_level <= 0:
            return _atr_fallback(side, price, atr, pullback_sl_atr, pullback_tp1_atr, pullback_tp2_atr, precision)

        sl = _round_to_precision(sl_level + buffer, precision)

        # Validate SL is above price and meets min distance
        if sl <= price or abs(price - sl) < min_dist:
            return _atr_fallback(side, price, atr, pullback_sl_atr, pullback_tp1_atr, pullback_tp2_atr, precision)

        tp1 = _round_to_precision(tp_level, precision)
        tp2 = tp1

        # Try to find a deeper TP level
        for level in [
            bb_lower,
            _coalesce_zero(last_bar.get("fib618")),
            _coalesce_zero(last_bar.get("fib786")),
            _coalesce_zero(last_bar.get("s1")),
        ]:
            if (
                level > 0
                and level < tp_level
                and abs(price - level) >= min_dist
                and abs(price - level) <= max_dist
            ):
                tp2 = _round_to_precision(level, precision)
                break

        if tp2 > tp1:
            tp2 = tp1

        return {"sl": sl, "tp1": tp1, "tp2": tp2, "usedSR": True}

    # Invalid side
    return {"sl": 0, "tp1": 0, "tp2": 0, "usedSR": False}


def _coalesce_zero(value: Any) -> Any:
    """镜像 TS `?? 0`:仅替换 null/undefined,保留 0/NaN/负值。"""
    return value if value is not None else 0
