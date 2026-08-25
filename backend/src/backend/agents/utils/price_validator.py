"""镜像 apps/app-agent/src/utils/price-validator.ts。

价格范围校验 + 交易业务逻辑校验(SL/TP 方向、RR 比值、50% 容差)。
所有警告文案逐字镜像 TS(数值用 JS Number/String 表示)。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Any, TypeVar

from backend.agents.types.analysis import ArbitrationResult, TradeRecommendation

__all__ = [
    "DEFAULT_TOLERANCE",
    "TradeValidationResult",
    "filter_valid_prices",
    "validate_arbitration_result",
    "validate_price_matches_asset_class",
    "validate_price_range",
    "validate_trade_recommendation",
]

DEFAULT_TOLERANCE = 0.5  # ±50% of current price

T = TypeVar("T")


def _warn(message: str) -> None:
    """镜像 console.warn(测试中通常被捕获;这里输出到 logger.warn)。"""
    from backend.agents.utils.logger import get_logger

    get_logger().warn(None, message)


def _js_number_to_string(value: float) -> str:
    """镜像 JS String(number):整数浮点去掉 '.0'(如 4290.0 → '4290')。"""
    if isinstance(value, bool):
        return "true" if value else "false"
    if math.isnan(value):
        return "NaN"
    if math.isinf(value):
        return "Infinity" if value > 0 else "-Infinity"
    if value.is_integer():
        return str(int(value))
    return repr(value)


def _to_fixed(value: float, digits: int) -> str:
    """镜像 JS (number).toFixed(digits):四舍五入(半值远离零)。"""
    if math.isnan(value):
        return "NaN"
    if math.isinf(value):
        return "Infinity" if value > 0 else "-Infinity"
    factor = 10**digits
    scaled = value * factor
    floored = math.floor(abs(scaled) + 0.5)
    if scaled < 0:
        floored = -floored
    if digits == 0:
        return str(floored)
    sign = "-" if floored < 0 else ""
    whole = abs(floored) // factor
    frac = abs(floored) % factor
    return f"{sign}{whole}.{frac:0{digits}d}"


def _is_finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


# ---------------------------------------------------------------------------
# 价格范围校验
# ---------------------------------------------------------------------------


def validate_price_range(
    price: float,
    current_price: float,
    _profile: object,
    label: str,
    tolerance: float = DEFAULT_TOLERANCE,
) -> bool:
    """价格在合理范围内(默认 ±50% current price)。价格明显属于别的品种 → False。"""
    if not _is_finite_number(price) or price <= 0:
        return False
    if not _is_finite_number(current_price) or current_price <= 0:
        return True  # 无参考价,无法校验

    min_price = current_price * (1 - tolerance)
    max_price = current_price * (1 + tolerance)

    if price < min_price or price > max_price:
        _warn(
            f"[price-validator] ⚠️ {label}: price {_js_number_to_string(float(price))} "
            f"outside valid range [{_to_fixed(min_price, 3)}, {_to_fixed(max_price, 3)}] "
            f"for current price {_js_number_to_string(float(current_price))}"
        )
        return False
    return True


def _level_price[T](level: T) -> float:
    return level["price"] if isinstance(level, dict) else level.price  # type: ignore[attr-defined]


def filter_valid_prices[T](
    levels: list[T],
    current_price: float,
    profile: object,
    label: str,
) -> list[T]:
    """过滤出在合理范围内的 S/R 价位,非法项打警告日志。"""
    return [
        level
        for level in levels
        if validate_price_range(_level_price(level), current_price, profile, f"{label} @ {_level_price(level)}")
    ]


def validate_price_matches_asset_class(current_price: float, profile: Any) -> bool:
    """当前价是否符合预期资产类别(黄金 >1000、日元交叉 100-300 等)。"""
    if not _is_finite_number(current_price) or current_price <= 0:
        return False

    asset_class = profile.assetClass
    symbol = profile.symbol

    if asset_class == "metal":
        # Gold: 1000-5000, Silver: 10-100
        if "XAU" in symbol or "GOLD" in symbol:
            return current_price >= 800 and current_price <= 10000
        if "XAG" in symbol:
            return current_price >= 10 and current_price <= 200
        return True
    if asset_class == "forex":
        # JPY crosses: 80-300, other majors: 0.5-3
        if "JPY" in symbol:
            return current_price >= 50 and current_price <= 500
        return current_price >= 0.3 and current_price <= 10
    return True


# ---------------------------------------------------------------------------
# 交易级业务校验
# ---------------------------------------------------------------------------


@dataclass
class TradeValidationResult:
    valid: bool
    warnings: list[str] = field(default_factory=list)
    fixedTrade: TradeRecommendation | None = None
    fixedArbitration: ArbitrationResult | None = None


def validate_trade_recommendation(
    trade: TradeRecommendation,
    current_price: float,
    profile: object,
) -> TradeValidationResult:
    """镜像 validateTradeRecommendation。

    1. entry 必须有限且为正(否则零化,方向改为 hold)
    2. entry/SL/TP1(/TP2 若 >0)必须在 ±50% 内
    3. SL/TP 方向错误则零化(clamp)
    4. RR >= 0.4(以 TP2 优先作为 reward target),并回填 risk_reward_ratio
    5. SL/TP 被零化 → 方向 hold
    """
    warnings: list[str] = []
    fixed = trade

    # 1. Entry price sanity
    entry = fixed.entry_price
    if not _is_finite_number(entry) or entry <= 0:
        warnings.append(
            f"entry_price {_js_number_to_string(float(entry))} is invalid — zeroing trade"
        )
        return TradeValidationResult(
            valid=False,
            warnings=warnings,
            fixedTrade=replace(trade, direction="hold"),
        )

    # 2. Price range check
    price_checks: list[tuple[str, float]] = [
        ("entry_price", fixed.entry_price),
        ("stop_loss", fixed.stop_loss),
        ("take_profit_1", fixed.take_profit_1),
    ]
    if fixed.take_profit_2 is not None and fixed.take_profit_2 > 0:
        price_checks.append(("take_profit_2", fixed.take_profit_2))

    for label, price in price_checks:
        if price > 0 and not validate_price_range(price, current_price, profile, label):
            warnings.append(
                f"{label} {_js_number_to_string(float(price))} outside valid range for current price "
                f"{_js_number_to_string(float(current_price))}"
            )

    # 3. SL/TP direction check
    if fixed.direction == "buy":
        if fixed.stop_loss >= fixed.entry_price and fixed.stop_loss > 0:
            warnings.append(
                f"BUY trade: stop_loss {_js_number_to_string(float(fixed.stop_loss))} >= entry "
                f"{_js_number_to_string(float(fixed.entry_price))} — wrong side, clamping below entry"
            )
            fixed = replace(fixed, stop_loss=0)
        if fixed.take_profit_1 <= fixed.entry_price and fixed.take_profit_1 > 0:
            warnings.append(
                f"BUY trade: take_profit_1 {_js_number_to_string(float(fixed.take_profit_1))} <= entry "
                f"{_js_number_to_string(float(fixed.entry_price))} — wrong side, clamping above entry"
            )
            fixed = replace(fixed, take_profit_1=0)
    elif fixed.direction == "sell":
        if fixed.stop_loss <= fixed.entry_price and fixed.stop_loss > 0:
            warnings.append(
                f"SELL trade: stop_loss {_js_number_to_string(float(fixed.stop_loss))} <= entry "
                f"{_js_number_to_string(float(fixed.entry_price))} — wrong side, clamping above entry"
            )
            fixed = replace(fixed, stop_loss=0)
        if fixed.take_profit_1 >= fixed.entry_price and fixed.take_profit_1 > 0:
            warnings.append(
                f"SELL trade: take_profit_1 {_js_number_to_string(float(fixed.take_profit_1))} >= entry "
                f"{_js_number_to_string(float(fixed.entry_price))} — wrong side, clamping below entry"
            )
            fixed = replace(fixed, take_profit_1=0)

    # 4. RR ratio sanity
    if fixed.stop_loss > 0 and fixed.take_profit_1 > 0 and fixed.entry_price > 0:
        sl_dist = abs(fixed.entry_price - fixed.stop_loss)
        reward_target = (
            fixed.take_profit_2
            if fixed.take_profit_2 is not None and fixed.take_profit_2 > 0
            else fixed.take_profit_1
        )
        tp_dist = abs(reward_target - fixed.entry_price)
        if sl_dist > 0:
            rr = tp_dist / sl_dist
            if rr < 0.4:
                warnings.append(
                    f"Risk/reward ratio {_to_fixed(rr, 2)} < 0.4 — unfavorable trade"
                )
            fixed = replace(fixed, risk_reward_ratio=float(_to_fixed(rr, 2)))

    # 5. If SL or TP got zeroed, direction becomes hold
    is_valid = fixed.direction == "hold" or (fixed.stop_loss > 0 and fixed.take_profit_1 > 0)

    return TradeValidationResult(
        valid=is_valid and len(warnings) == 0,
        warnings=warnings,
        fixedTrade=fixed if is_valid else replace(fixed, direction="hold"),
    )


def validate_arbitration_result(
    arb: ArbitrationResult,
    current_price: float,
    profile: object,
) -> TradeValidationResult:
    """镜像 validateArbitrationResult:校验内嵌 trade_recommendation,
    交易非法时降级仲裁(方向/动作 → hold,confidence 封顶 20)。"""
    if arb.trade_recommendation is None:
        # 无交易建议,无需校验
        return TradeValidationResult(valid=True, warnings=[], fixedArbitration=arb)

    # hold 方向且无 entry/SL/TP — 跳过校验(无动作)
    if arb.trade_recommendation.direction == "hold" and arb.trade_recommendation.entry_price <= 0:
        return TradeValidationResult(valid=True, warnings=[], fixedArbitration=arb)

    # hold 方向但有合法 SL/TP — 仅校验价位(方向保持 hold,不降级)
    if arb.trade_recommendation.direction == "hold":
        trade_result = validate_trade_recommendation(arb.trade_recommendation, current_price, profile)
        return TradeValidationResult(
            valid=trade_result.valid,
            warnings=trade_result.warnings,
            fixedTrade=trade_result.fixedTrade,
            fixedArbitration=(
                replace(arb, trade_recommendation=trade_result.fixedTrade)
                if trade_result.fixedTrade is not None
                else arb
            ),
        )

    trade_result = validate_trade_recommendation(arb.trade_recommendation, current_price, profile)

    if trade_result.valid:
        return TradeValidationResult(
            valid=True,
            warnings=trade_result.warnings,
            fixedArbitration=(
                replace(arb, trade_recommendation=trade_result.fixedTrade)
                if trade_result.fixedTrade is not None
                else arb
            ),
        )

    # Downgrade: invalid trade → hold
    fixed_arb = replace(
        arb,
        final_direction="hold",
        action="hold",
        confidence=min(arb.confidence, 20),  # 非法交易置信度封顶 20
        trade_recommendation=(
            trade_result.fixedTrade
            if trade_result.fixedTrade is not None
            else arb.trade_recommendation
        ),
    )

    return TradeValidationResult(
        valid=False,
        warnings=[*trade_result.warnings, "Trade downgraded to hold due to invalid SL/TP"],
        fixedTrade=trade_result.fixedTrade,
        fixedArbitration=fixed_arb,
    )
