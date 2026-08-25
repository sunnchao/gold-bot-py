"""AI approve 规则层(镜像 gold-bot apps/app-server/src/services/ai-approve/rules.ts)。

全部按 TS 语义逐字移植:`??` → None 检查、`||` → `or`、`Math.round` → `floor(x + 0.5)`、
数组排序/连续去重/`.slice(0, 2)` 与 R:R 判定(`rr + 1e-12 < min`)均与源文件一致;
输入输出统一使用 dict[str, Any],字段键保持 TS 的 camelCase 命名。
"""

from __future__ import annotations

import math
from typing import Any, cast

from backend.persistence.records import EaRecord

__all__ = [
    "AI_APPROVE_MIN_RR",
    "calc_ai_approve_lots",
    "first_positive_ai_approve_take_profit",
    "pick_ai_approve_entry_price",
    "primary_ai_approve_take_profit",
    "resolve_ai_approve_executable_take_profits",
    "resolve_ai_approve_order_intent",
    "round2",
    "validate_ai_approve_protection_direction",
]

# R:R 下限(镜像 TS AI_APPROVE_MIN_RR)
AI_APPROVE_MIN_RR = 1.25


def pick_ai_approve_entry_price(entry_zone: EaRecord | None) -> float:
    """镜像 pickAIApproveEntryPrice:min/max 缺失或非法时返回 0,相等取单值,否则取中点。"""
    min_value = 0.0 if entry_zone is None else _number_field(entry_zone, "min")
    max_value = 0.0 if entry_zone is None else _number_field(entry_zone, "max")
    if min_value <= 0 or max_value <= 0:
        return 0.0
    return min_value if min_value == max_value else (min_value + max_value) / 2.0


def calc_ai_approve_lots(max_lots: float) -> float:
    """镜像 calcAIApproveLots:服务端不下发 lots(返回 0),max_lots 仅表示 LLM 开仓意图。"""
    if max_lots <= 0:
        return 0.0
    return 0.0


def first_positive_ai_approve_take_profit(values: list[float]) -> float:
    """镜像 firstPositiveAIApproveTakeProfit:第一个 > 0 的目标,无则 0。"""
    for value in values:
        if value > 0:
            return value
    return 0.0


def primary_ai_approve_take_profit(values: list[float]) -> float:
    """镜像 primaryAIApproveTakeProfit:最后一个 > 0 的目标(数组末尾),无则 0。"""
    positive_values = [value for value in values if value > 0]
    return positive_values[-1] if positive_values else 0.0


def resolve_ai_approve_executable_take_profits(input: dict[str, Any]) -> EaRecord:
    """镜像 resolveAIApproveExecutableTakeProfits:按方向排序、去重、取前 2 个可执行目标。

    盈亏比严格按最远目标(TP2 / primary)计算并卡 minRiskReward;TP1 只要求方向/几何有效。
    """
    raw_side = input.get("side")
    side = raw_side.strip().lower() if isinstance(raw_side, str) else ""
    if side not in ("buy", "sell"):
        return _reject_take_profit("rr.invalid", "TP1")
    entry = cast(float, input.get("entry"))
    stop_loss = cast(float, input.get("stopLoss"))
    if not _is_positive_finite(entry) or not _is_positive_finite(stop_loss):
        return _reject_take_profit("rr.invalid", "TP1")

    risk = entry - stop_loss if side == "buy" else stop_loss - entry
    if not _is_positive_finite(risk):
        return _reject_take_profit("rr.invalid", "TP1")

    take_profit_values = input.get("takeProfitValues")
    if not isinstance(take_profit_values, list):
        take_profit_values = []
    positive_targets: list[float] = []
    for value in take_profit_values:
        if not _is_finite_number(value):
            return _reject_take_profit("rr.invalid", _label_for_target_index(len(positive_targets)))
        if value > 0:
            positive_targets.append(value)
    if len(positive_targets) == 0:
        return _reject_take_profit("rr.invalid", "TP1")

    ordered_targets = sorted(positive_targets, reverse=(side == "sell"))
    executable_values = _unique_sorted_numbers(ordered_targets)
    executable_values = executable_values[:2]
    min_risk_reward = input.get("minRiskReward")
    if min_risk_reward is None:
        min_risk_reward = AI_APPROVE_MIN_RR

    targets: list[EaRecord] = []
    for index, value in enumerate(executable_values):
        label = _label_for_target_index(index)
        reward = value - entry if side == "buy" else entry - value
        if not _is_positive_finite(reward):
            return _reject_take_profit("rr.invalid", label)
        rr = reward / risk
        if not _is_finite_number(rr):
            return _reject_take_profit("rr.invalid", label)
        is_primary = index == len(executable_values) - 1
        if is_primary and min_risk_reward > 0 and rr + 1e-12 < min_risk_reward:
            return _reject_take_profit("rr.below_minimum", label)
        targets.append({"label": label, "value": value, "rr": rr})

    tp1 = targets[0]["value"] if len(targets) > 0 else 0.0
    tp2 = targets[1]["value"] if len(targets) > 1 else 0.0
    tp_split = tp1 > 0 and tp2 > 0 and tp1 != tp2
    return {
        "accepted": True,
        "tp1": tp1,
        "tp2": tp2 if tp_split else 0.0,
        "legacyTakeProfit": tp2 if tp_split else tp1,
        "tpSplit": tp_split,
        "targets": targets,
    }


def resolve_ai_approve_order_intent(
    trade_plan: EaRecord,
    current_price: float,
    entry: float,
    h1_atr: float,
) -> EaRecord:
    """镜像 resolveAIApproveOrderIntent:市价/限价意图解析与触发判定,reason code 逐字一致。"""
    side = _string_field(trade_plan, "side").strip().lower()
    execution_type = _string_field(trade_plan, "execution_type")
    requested_order_type = _string_field(trade_plan, "requested_order_type")

    if execution_type == "stop" or requested_order_type in ("BUY_STOP", "SELL_STOP"):
        return {"accepted": False, "reason": "stop_order.disabled"}
    if execution_type == "" or requested_order_type == "":
        return {"accepted": False, "reason": "order_intent.missing"}
    if execution_type not in ("market", "limit"):
        return {"accepted": False, "reason": "order_intent.mismatch"}
    if requested_order_type not in ("market", "BUY_LIMIT", "SELL_LIMIT"):
        return {"accepted": False, "reason": "order_intent.mismatch"}
    if execution_type == "market" and requested_order_type != "market":
        return {"accepted": False, "reason": "order_intent.mismatch"}
    if execution_type == "limit" and requested_order_type == "market":
        return {"accepted": False, "reason": "order_intent.mismatch"}
    if requested_order_type == "BUY_LIMIT" and side != "buy":
        return {"accepted": False, "reason": "order_intent.mismatch"}
    if requested_order_type == "SELL_LIMIT" and side != "sell":
        return {"accepted": False, "reason": "order_intent.mismatch"}

    if execution_type == "market" or requested_order_type == "market":
        if h1_atr > 0:
            allowed_distance = h1_atr * 0.3
            if abs(current_price - entry) > allowed_distance:
                return {"accepted": False, "reason": "market_entry_mismatch"}
        return {"accepted": True, "orderType": "market"}

    if requested_order_type == "BUY_LIMIT":
        if side != "buy":
            return {"accepted": False, "reason": "limit_direction_mismatch"}
        if entry < current_price:
            return {"accepted": True, "orderType": "BUY_LIMIT"}
        if _is_triggered_limit_within_protection(trade_plan, current_price):
            return {"accepted": True, "orderType": "market"}
        return {"accepted": False, "reason": "limit_direction_mismatch"}

    if requested_order_type == "SELL_LIMIT":
        if side != "sell":
            return {"accepted": False, "reason": "limit_direction_mismatch"}
        if entry > current_price:
            return {"accepted": True, "orderType": "SELL_LIMIT"}
        if _is_triggered_limit_within_protection(trade_plan, current_price):
            return {"accepted": True, "orderType": "market"}
        return {"accepted": False, "reason": "limit_direction_mismatch"}

    return {"accepted": False, "reason": "order_intent.missing"}


def validate_ai_approve_protection_direction(trade_plan: EaRecord, entry: float) -> EaRecord:
    """镜像 validateAIApproveProtectionDirection:SL/TP 相对 entry 的几何方向校验。"""
    side = _string_field(trade_plan, "side").strip().upper()
    stop_loss = _number_field(trade_plan, "stop_loss")
    resolved_take_profits = resolve_ai_approve_executable_take_profits(
        {
            "side": side,
            "entry": entry,
            "stopLoss": stop_loss,
            "takeProfitValues": _array_number_field(trade_plan, "take_profit"),
            "minRiskReward": 0,
        }
    )
    if resolved_take_profits["accepted"]:
        return {"accepted": True}
    return {"accepted": False, "reason": "protection.invalid_direction"}


def round2(value: float) -> float:
    """镜像 round2:`Math.round(v * 100) / 100` = `floor(v * 100 + 0.5) / 100`。"""
    return math.floor(value * 100 + 0.5) / 100.0


def _is_triggered_limit_within_protection(trade_plan: EaRecord, current_price: float) -> bool:
    side = _string_field(trade_plan, "side").strip().upper()
    stop_loss = _number_field(trade_plan, "stop_loss")
    resolved = resolve_ai_approve_executable_take_profits(
        {
            "side": side,
            "entry": current_price,
            "stopLoss": stop_loss,
            "takeProfitValues": _array_number_field(trade_plan, "take_profit"),
            "minRiskReward": 0,
        }
    )
    return bool(resolved["accepted"])


def _number_field(record: EaRecord, field: str) -> float:
    value = record.get(field)
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return value
    return 0.0


def _string_field(record: EaRecord, field: str) -> str:
    value = record.get(field)
    return value if isinstance(value, str) else ""


def _array_number_field(record: EaRecord, field: str) -> list[float]:
    value = record.get(field)
    if not isinstance(value, list):
        return []
    return [
        item
        for item in value
        if isinstance(item, (int, float)) and not isinstance(item, bool) and math.isfinite(item)
    ]


def _reject_take_profit(reason: str, label: str) -> EaRecord:
    return {"accepted": False, "reason": reason, "label": label}


def _label_for_target_index(index: int) -> str:
    return "TP1" if index <= 0 else "TP2"


def _is_positive_finite(value: Any) -> bool:
    return _is_finite_number(value) and value > 0


def _is_finite_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _unique_sorted_numbers(values: list[float]) -> list[float]:
    """镜像 uniqueSortedNumbers:有序数组上的连续去重(保留首个出现的值)。"""
    out: list[float] = []
    for value in values:
        if len(out) == 0 or value != out[-1]:
            out.append(value)
    return out
