"""SMC 上下文评分加成(镜像 packages/trading-core/src/replay/smc-scoring.ts)。

信号方向被近期 CHoCH / 流动性扫荡 / 有效订单块确认时累加 SMC bonus。
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "SMCContext",
    "calculate_smc_bonus",
    "has_confirming_sweep",
    "has_recent_choch",
    "has_valid_ob_near_price",
]

SMCContext = dict[str, Any]
"""镜像 SMCContext:h4/h1/m30/m15 的 breaks / sweeps / obs 列表。"""


def has_recent_choch(
    smc: SMCContext | None,
    side: str,
    timeframe: str,
    max_bars_ago: int = 10,
) -> bool:
    """镜像 hasRecentCHoCH:最近一个 CHoCH 方向与信号方向一致。"""
    if smc is None:
        return False

    breaks_key = f"{timeframe}_breaks"
    breaks = smc.get(breaks_key)

    if breaks is None or not isinstance(breaks, list):
        return False

    # Find most recent CHoCH
    for br in reversed(breaks):
        if "type" in br and br["type"] == "CHoCH":
            # Check if it's recent enough (assumes bars.length - br.index < maxBarsAgo)
            direction = _direction_upper(br)
            if side == "BUY" and direction == "UP":
                return True
            if side == "SELL" and direction == "DOWN":
                return True

    return False


def has_confirming_sweep(
    smc: SMCContext | None,
    side: str,
    timeframe: str,
    price: float,
    atr: float,
    max_distance: float = 2.0,
    last_bar_index: int | None = None,
    max_bars_ago: int = 10,
) -> bool:
    """镜像 hasConfirmingSweep:BUY 找 BULL sweep,SELL 找 BEAR sweep,价距在 ATR 范围内。"""
    if smc is None:
        return False

    sweeps_key = f"{timeframe}_sweeps"
    sweeps = smc.get(sweeps_key)

    if sweeps is None or not isinstance(sweeps, list):
        return False

    target_side = "BULL" if side == "BUY" else "BEAR"

    # Find most recent sweep matching direction
    for sw in reversed(sweeps):
        if (
            "side" in sw
            and "level" in sw
            and isinstance(sw.get("side"), str)
            and sw["side"].upper() == target_side
            and sw.get("reversed") is not False
        ):
            if last_bar_index is not None and "index" in sw and last_bar_index - sw["index"] > max_bars_ago:
                continue
            # Check if price is within reasonable distance from sweep level
            if abs(price - sw["level"]) <= atr * max_distance:
                return True

    return False


def has_valid_ob_near_price(
    smc: SMCContext | None,
    side: str,
    timeframe: str,
    price: float,
    atr: float,
    max_distance: float = 1.0,
) -> bool:
    """镜像 hasValidOBNearPrice:h1 时与 h4 订单块叠加,命中返回 True。"""
    if smc is None:
        return False

    if timeframe == "h1":
        h1_obs = smc.get("h1_obs")
        h4_obs = smc.get("h4_obs")
        obs: Any = []
        if isinstance(h1_obs, list):
            obs = [*h1_obs]
        if isinstance(h4_obs, list):
            obs = [*obs, *h4_obs]
    else:
        obs = smc.get(f"{timeframe}_obs")

    if obs is None or not isinstance(obs, list):
        return False

    target_side = "BUY" if side == "BUY" else "SELL"

    for ob in obs:
        if (
            "side" in ob
            and "high" in ob
            and "low" in ob
            and isinstance(ob.get("side"), str)
            and ob["side"].upper() == target_side
            and ob.get("valid") is not False
        ):
            if ob["high"] >= price - atr * max_distance and ob["low"] <= price + atr * max_distance:
                return True

    return False


def calculate_smc_bonus(
    smc: SMCContext | None,
    side: str,
    price: float,
    atr: float,
    timeframe: str = "h1",
    last_bar_index: int | None = None,
) -> int:
    """镜像 calculateSMCBonus:CHoCH / ConfirmSweep / ValidOB 各 +1。"""
    bonus = 0

    if has_recent_choch(smc, side, timeframe):
        bonus += 1

    if has_confirming_sweep(smc, side, timeframe, price, atr, 2.0, last_bar_index):
        bonus += 1

    if has_valid_ob_near_price(smc, side, timeframe, price, atr, 1.5):
        bonus += 1

    return bonus


def _direction_upper(br: dict[str, Any]) -> str | None:
    """镜像 `br.direction?.toUpperCase()`:缺失或非字符串返回 None。"""
    if "direction" not in br:
        return None
    direction = br["direction"]
    if not isinstance(direction, str):
        return None
    return direction.upper()
