"""浮亏加仓(镜像 packages/trading-core/src/replay/scale-in.ts)。

同向浮亏持仓 + ADX 门槛 + 距上次入场距离/浮亏/技术位校验通过后,按衰减系数
加仓并给出全仓统一 SL 与加权均价 TP。dict 键保持 TS camelCase。
"""

from __future__ import annotations

import math
import time
from fractions import Fraction
from typing import Any

from backend.trading_core.engine.config import StrategyConfig

__all__ = [
    "ScaleInResult",
    "ScaleInSignal",
    "check_scale_in",
]

ScaleInSignal = dict[str, Any]
"""镜像 ScaleInSignal:side / entry / stop_loss / tp1 / tp2 / score / strategy + scale-in 附加键。"""

ScaleInResult = dict[str, Any]
"""镜像 ScaleInResult:signal / reason。"""

_Position = dict[str, Any]
_Bar = dict[str, Any]


def _math_round(value: float) -> int:
    """镜像 JS Math.round:等价于 floor(x + 0.5)(半值朝 +∞ 取整)。"""
    return math.floor(value + 0.5)


def _round_to_precision(value: float, precision: int) -> float:
    factor = math.pow(10, precision)
    return _math_round(value * factor) / factor


def _round_down_scale_in_lot(value: float) -> float:
    # Round down to 0.01 precision
    return math.floor(value * 100) / 100


def _coalesce_zero(value: Any | None) -> Any:
    """镜像 TS `?? 0`:仅替换 null/undefined,保留 0/NaN/负值。"""
    return value if value is not None else 0


def _near_any_level(price: float, threshold: float, *levels: float) -> bool:
    for level in levels:
        if level <= 0:
            continue
        if abs(price - level) <= threshold:
            return True
    return False


def _scale_in_take_profit(
    weighted_avg: float, atr: float, mult: float, side: str, precision: int
) -> float:
    if side == "BUY":
        return _round_to_precision(weighted_avg + atr * mult, precision)
    return _round_to_precision(weighted_avg - atr * mult, precision)


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


def _js_num(value: Any) -> str:
    """镜像 JS Number → string:整数 float 不带尾随 .0(如 25.0 → "25")。"""
    if isinstance(value, int):
        return str(value)
    text = repr(value)
    if text.endswith(".0"):
        return text[:-2]
    return text


def _calculate_unified_sl(
    positions: list[_Position],
    new_price: float,
    new_lots: float,
    atr: float,
    sl_atr: float,
    side: str,
    precision: int,
) -> tuple[float, float]:
    """镜像 calculateUnifiedSL:返回 [weightedAvgEntry, unifiedSL]。"""
    total_lots = new_lots
    weighted_entry = new_price * new_lots

    for pos in positions:
        total_lots += pos["lots"]
        weighted_entry += pos["openPrice"] * pos["lots"]

    avg_entry = weighted_entry / total_lots
    if side == "BUY":
        unified_sl = _round_to_precision(avg_entry - atr * sl_atr, precision)
    else:
        unified_sl = _round_to_precision(avg_entry + atr * sl_atr, precision)

    return avg_entry, unified_sl


def check_scale_in(
    h1_bars: list[_Bar],
    price: float,
    atr: float,
    positions: list[_Position],
    cfg: StrategyConfig,
    precision: int,
) -> ScaleInResult:
    """镜像 checkScaleIn:浮亏加仓主逻辑,命中返回信号,否则返回跳过原因。

    触发条件:
    - 存在同向浮亏持仓
    - ADX ≥ 门槛(趋势仍强)
    - 价格距上次入场 ≥ minDistATR
    - 浮亏 ≥ minFloatLossATR
    - 未超最大加仓次数
    - 距上次入场时间 ≥ 最小间隔
    - 价格临近技术位(Fib、Pivot、EMA)
    """
    name = "scale_in"

    # Check if scale-in is enabled
    if not cfg.get("scaleInEnabled"):
        return {"signal": None, "reason": "浮亏加仓未启用 ⏭"}

    # Validate data
    if len(h1_bars) == 0 or atr <= 0 or price <= 0:
        return {"signal": None, "reason": "数据不足，跳过浮亏加仓"}

    last_bar = h1_bars[-1]

    # Check ADX threshold
    last_adx = _coalesce_zero(last_bar.get("adx"))
    if last_adx < cfg["scaleInMinADX"]:
        adx_text = _to_fixed(last_bar["adx"], 1) if last_bar.get("adx") is not None else "0"
        return {
            "signal": None,
            "reason": f"ADX={adx_text} < {_js_num(cfg['scaleInMinADX'])},趋势不够强 ⏭",
        }

    # Find same-direction positions in loss
    same_direction: list[_Position] = []
    side: str | None = None

    for pos in positions:
        pos_side = pos["type"].upper().strip()
        if pos_side != "BUY" and pos_side != "SELL":
            continue

        in_loss = (pos_side == "BUY" and price < pos["openPrice"]) or (
            pos_side == "SELL" and price > pos["openPrice"]
        )
        if not in_loss:
            continue

        if side is None:
            side = pos_side

        if pos_side == side:
            same_direction.append(pos)

    if len(same_direction) == 0 or side is None:
        return {"signal": None, "reason": "无同向浮亏持仓 ⏭"}

    # Count existing scale-in additions
    scale_in_count = 0
    existing_lots = 0.0
    weighted_entry = 0.0
    latest = same_direction[0]

    for pos in same_direction:
        existing_lots += pos["lots"]
        weighted_entry += pos["openPrice"] * pos["lots"]

        comment = pos.get("comment")
        if comment and "scale_in" in comment.lower():
            scale_in_count += 1

        if pos["openTime"] > latest["openTime"]:
            latest = pos

    # Check max add count
    if scale_in_count >= cfg["scaleInMaxAddCount"]:
        return {
            "signal": None,
            "reason": f"加仓次数已达上限: {scale_in_count}/{_js_num(cfg['scaleInMaxAddCount'])} ⏭",
        }

    # Check minimum interval
    if latest["openTime"] > 0 and cfg["scaleInMinIntervalMin"] > 0:
        now_ms = time.time() * 1000
        last_open_ms = latest["openTime"] * 1000
        elapsed_min = (now_ms - last_open_ms) / 1000 / 60

        if elapsed_min < cfg["scaleInMinIntervalMin"]:
            return {
                "signal": None,
                "reason": f"距离最近加仓/开仓不足 {_js_num(cfg['scaleInMinIntervalMin'])} 分钟 ⏭",
            }

    # Check distance from last entry
    last_entry_dist = abs(price - latest["openPrice"])
    if last_entry_dist < cfg["scaleInMinDistATR"] * atr:
        return {
            "signal": None,
            "reason": (
                f"距离最近入场不足: {_to_fixed(last_entry_dist, 2)} < "
                f"{_to_fixed(cfg['scaleInMinDistATR'] * atr, 2)} ATR ⏭"
            ),
        }

    # Check floating loss
    avg_entry = weighted_entry / existing_lots
    float_loss_dist = abs(price - avg_entry)
    if float_loss_dist < cfg["scaleInMinFloatLossATR"] * atr:
        return {
            "signal": None,
            "reason": (
                f"浮亏不足: {_to_fixed(float_loss_dist, 2)} < "
                f"{_to_fixed(cfg['scaleInMinFloatLossATR'] * atr, 2)} ATR ⏭"
            ),
        }

    # Check if price is near technical level
    fib_near = _near_any_level(
        price,
        atr * 0.3,
        _coalesce_zero(last_bar.get("fib382")),
        _coalesce_zero(last_bar.get("fib500")),
        _coalesce_zero(last_bar.get("fib618")),
    )
    pivot_near = _near_any_level(
        price,
        atr * 0.3,
        _coalesce_zero(last_bar.get("pp")),
        _coalesce_zero(last_bar.get("s1")),
        _coalesce_zero(last_bar.get("r1")),
    )
    ema_near = _near_any_level(
        price,
        atr * 0.2,
        _coalesce_zero(last_bar.get("ema50")),
        _coalesce_zero(last_bar.get("ema200")),
    )
    last_rsi = _coalesce_zero(last_bar.get("rsi"))
    rsi_confirm = (side == "BUY" and last_rsi > 0 and last_rsi < 30) or (
        side == "SELL" and last_rsi > 70
    )

    if not fib_near and not pivot_near and not ema_near and not rsi_confirm:
        return {"signal": None, "reason": "未到关键技术位 ⏭"}

    # Calculate new lot size (decay)
    new_lots = _round_down_scale_in_lot(existing_lots * cfg["scaleInLotDecay"])
    if new_lots < 0.01:
        new_lots = 0.01

    # Calculate unified SL and weighted average entry
    weighted_avg, unified_sl = _calculate_unified_sl(
        same_direction, price, new_lots, atr, cfg["scaleInSLATR"], side, precision
    )

    # Calculate score
    score = 5
    if last_adx > 30:
        score += 1
    if rsi_confirm:
        score += 1
    if fib_near:
        score += 1
    if pivot_near:
        score += 1
    last_macd_hist = _coalesce_zero(last_bar.get("macdHist"))
    if (side == "BUY" and last_macd_hist > 0) or (side == "SELL" and last_macd_hist < 0):
        score += 1
    score = min(score, 10)

    signal: ScaleInSignal = {
        "side": side,
        "entry": price,
        "stop_loss": unified_sl,
        "tp1": _scale_in_take_profit(weighted_avg, atr, cfg["scaleInTP1ATR"], side, precision),
        "tp2": _scale_in_take_profit(weighted_avg, atr, cfg["scaleInTP2ATR"], side, precision),
        "score": score,
        "strategy": name,
        "scaleInParentTicket": latest["ticket"],
        "weightedAvgEntry": weighted_avg,
        "unifiedSL": unified_sl,
        "scaleInCount": scale_in_count,
        "lots": new_lots,
    }

    message = (
        f"➕ 浮亏加仓 {side} | 原仓均价={_to_fixed(avg_entry, 2)} | 浮亏={_to_fixed(float_loss_dist / atr, 1)}ATR "
        f"| 加仓价={_to_fixed(price, 2)} | 新手数={_to_fixed(new_lots, 2)} "
        f"| 加权均价={_to_fixed(weighted_avg, 2)} | 统一SL={_to_fixed(unified_sl, 2)}"
    )

    return {"signal": signal, "reason": message}
