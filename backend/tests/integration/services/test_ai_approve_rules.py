"""镜像 apps/app-server/src/services/ai-approve/rules.spec.ts(vitest 逐用例)。

每个 describe/it 映射为一个 pytest 用例;toEqual 用逐字段断言,浮点 R:R 用
toBeCloseTo 等价(容差 0.5 * 10^-12)。
"""

from __future__ import annotations

from typing import Any

from backend.services.ai_approve import (
    resolve_ai_approve_executable_take_profits,
    resolve_ai_approve_order_intent,
    validate_ai_approve_protection_direction,
)


def trade_plan(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    plan: dict[str, Any] = {
        "schema_version": "trade_plan.v1",
        "decision_id": "tpv1_rules",
        "account_id": "90011087",
        "symbol": "XAUUSD",
        "mode": "approve",
        "side": "buy",
        "confidence": 80,
        "entry_zone": {"min": 3332.5, "max": 3332.5},
        "execution_type": "limit",
        "requested_order_type": "BUY_LIMIT",
        "stop_loss": 3330,
        "take_profit": [3345],
        "max_lots": 0.1,
        "expires_at": "2099-06-06T09:15:00Z",
        "reason_codes": ["mode.approve", "side.buy"],
        "narrative": "rules fixture",
    }
    if overrides:
        plan.update(overrides)
    return plan


def assert_close(value: float, expected: float, precision: int = 12) -> None:
    tolerance = 0.5 * (10**-precision)
    assert abs(value - expected) < tolerance


# ---------------------------------------------------------------------------
# describe('AI approve order intent rules')
# ---------------------------------------------------------------------------


def test_accepts_market_intent_near_current_price() -> None:
    result = resolve_ai_approve_order_intent(
        trade_plan(
            {
                "execution_type": "market",
                "requested_order_type": "market",
                "entry_zone": {"min": 3335.5, "max": 3335.7},
            }
        ),
        3335.6,
        3335.6,
        2,
    )
    assert result == {"accepted": True, "orderType": "market"}


def test_rejects_market_intent_when_entry_is_not_near_current_price() -> None:
    result = resolve_ai_approve_order_intent(
        trade_plan(
            {
                "execution_type": "market",
                "requested_order_type": "market",
                "entry_zone": {"min": 3330, "max": 3330},
            }
        ),
        3335.6,
        3330,
        2,
    )
    assert result == {"accepted": False, "reason": "market_entry_mismatch"}


def test_accepts_market_intent_when_h1_atr_is_missing_even_if_entry_differs_slightly() -> None:
    result = resolve_ai_approve_order_intent(
        trade_plan(
            {
                "execution_type": "market",
                "requested_order_type": "market",
                "entry_zone": {"min": 4108.83, "max": 4108.83},
            }
        ),
        4108.50,
        4108.83,
        0,
    )
    assert result == {"accepted": True, "orderType": "market"}


def test_accepts_buy_limit_below_current_price() -> None:
    result = resolve_ai_approve_order_intent(
        trade_plan({"side": "buy", "execution_type": "limit", "requested_order_type": "BUY_LIMIT"}),
        3335.6,
        3332.5,
        2,
    )
    assert result == {"accepted": True, "orderType": "BUY_LIMIT"}


def test_accepts_sell_limit_above_current_price() -> None:
    result = resolve_ai_approve_order_intent(
        trade_plan({"side": "sell", "execution_type": "limit", "requested_order_type": "SELL_LIMIT"}),
        3335.6,
        3338.5,
        2,
    )
    assert result == {"accepted": True, "orderType": "SELL_LIMIT"}


def test_converts_limit_orders_to_market_after_price_reaches_the_entry_and_remains_protected() -> None:
    result = resolve_ai_approve_order_intent(
        trade_plan(
            {
                "side": "buy",
                "execution_type": "limit",
                "requested_order_type": "BUY_LIMIT",
                "entry_zone": {"min": 3338, "max": 3338},
            }
        ),
        3335.6,
        3338,
        2,
    )
    assert result == {"accepted": True, "orderType": "market"}

    result = resolve_ai_approve_order_intent(
        trade_plan(
            {
                "side": "sell",
                "execution_type": "limit",
                "requested_order_type": "SELL_LIMIT",
                "entry_zone": {"min": 3332, "max": 3332},
                "stop_loss": 3340,
                "take_profit": [3325],
            }
        ),
        3335.6,
        3332,
        2,
    )
    assert result == {"accepted": True, "orderType": "market"}


def test_converts_limit_orders_at_current_price_to_market_intent() -> None:
    result = resolve_ai_approve_order_intent(
        trade_plan(
            {
                "side": "buy",
                "execution_type": "limit",
                "requested_order_type": "BUY_LIMIT",
                "entry_zone": {"min": 3335.6, "max": 3335.6},
            }
        ),
        3335.6,
        3335.6,
        2,
    )
    assert result == {"accepted": True, "orderType": "market"}

    result = resolve_ai_approve_order_intent(
        trade_plan(
            {
                "side": "sell",
                "execution_type": "limit",
                "requested_order_type": "SELL_LIMIT",
                "entry_zone": {"min": 3335.6, "max": 3335.6},
                "stop_loss": 3340,
                "take_profit": [3325],
            }
        ),
        3335.6,
        3335.6,
        2,
    )
    assert result == {"accepted": True, "orderType": "market"}


def test_converts_already_triggered_limit_entries_to_market_while_price_remains_inside_protection() -> None:
    result = resolve_ai_approve_order_intent(
        trade_plan(
            {
                "side": "sell",
                "execution_type": "limit",
                "requested_order_type": "SELL_LIMIT",
                "entry_zone": {"min": 59.94, "max": 59.94},
                "stop_loss": 60.85,
                "take_profit": [59.15, 58.25],
            }
        ),
        60.6,
        59.94,
        0.5,
    )
    assert result == {"accepted": True, "orderType": "market"}

    result = resolve_ai_approve_order_intent(
        trade_plan(
            {
                "side": "buy",
                "execution_type": "limit",
                "requested_order_type": "BUY_LIMIT",
                "entry_zone": {"min": 3335.6, "max": 3335.6},
                "stop_loss": 3330,
                "take_profit": [3345],
            }
        ),
        3332,
        3335.6,
        2,
    )
    assert result == {"accepted": True, "orderType": "market"}


def test_rejects_triggered_limit_entries_after_price_crosses_beyond_the_stop_loss() -> None:
    result = resolve_ai_approve_order_intent(
        trade_plan(
            {
                "side": "sell",
                "execution_type": "limit",
                "requested_order_type": "SELL_LIMIT",
                "entry_zone": {"min": 59.94, "max": 59.94},
                "stop_loss": 60.85,
                "take_profit": [59.15],
            }
        ),
        60.9,
        59.94,
        0.5,
    )
    assert result == {"accepted": False, "reason": "limit_direction_mismatch"}


def test_rejects_stop_order_intent() -> None:
    result = resolve_ai_approve_order_intent(
        trade_plan({"requested_order_type": "BUY_STOP"}),
        3335.6,
        3338,
        2,
    )
    assert result == {"accepted": False, "reason": "stop_order.disabled"}

    result = resolve_ai_approve_order_intent(
        trade_plan({"requested_order_type": "SELL_STOP"}),
        3335.6,
        3332,
        2,
    )
    assert result == {"accepted": False, "reason": "stop_order.disabled"}

    result = resolve_ai_approve_order_intent(
        trade_plan({"execution_type": "stop"}),
        3335.6,
        3338,
        2,
    )
    assert result == {"accepted": False, "reason": "stop_order.disabled"}


def test_rejects_missing_explicit_order_intent() -> None:
    result = resolve_ai_approve_order_intent(
        trade_plan({"execution_type": None, "requested_order_type": None}),
        3335.6,
        3335.6,
        2,
    )
    assert result == {"accepted": False, "reason": "order_intent.missing"}

    result = resolve_ai_approve_order_intent(
        trade_plan(
            {
                "execution_type": "market",
                "requested_order_type": None,
                "entry_zone": {"min": 3335.5, "max": 3335.7},
            }
        ),
        3335.6,
        3335.6,
        2,
    )
    assert result == {"accepted": False, "reason": "order_intent.missing"}

    result = resolve_ai_approve_order_intent(
        trade_plan(
            {
                "execution_type": None,
                "requested_order_type": "market",
                "entry_zone": {"min": 3335.5, "max": 3335.7},
            }
        ),
        3335.6,
        3335.6,
        2,
    )
    assert result == {"accepted": False, "reason": "order_intent.missing"}

    result = resolve_ai_approve_order_intent(
        trade_plan({"execution_type": "limit", "requested_order_type": None}),
        3335.6,
        3332.5,
        2,
    )
    assert result == {"accepted": False, "reason": "order_intent.missing"}

    result = resolve_ai_approve_order_intent(
        trade_plan({"execution_type": None, "requested_order_type": "BUY_LIMIT"}),
        3335.6,
        3332.5,
        2,
    )
    assert result == {"accepted": False, "reason": "order_intent.missing"}


def test_rejects_contradictory_explicit_order_intent_fields() -> None:
    result = resolve_ai_approve_order_intent(
        trade_plan({"execution_type": "market", "requested_order_type": "BUY_LIMIT"}),
        3335.6,
        3335.6,
        2,
    )
    assert result == {"accepted": False, "reason": "order_intent.mismatch"}

    result = resolve_ai_approve_order_intent(
        trade_plan({"side": "buy", "execution_type": "limit", "requested_order_type": "SELL_LIMIT"}),
        3335.6,
        3332.5,
        2,
    )
    assert result == {"accepted": False, "reason": "order_intent.mismatch"}

    result = resolve_ai_approve_order_intent(
        trade_plan({"side": "sell", "execution_type": "limit", "requested_order_type": "BUY_LIMIT"}),
        3335.6,
        3338.5,
        2,
    )
    assert result == {"accepted": False, "reason": "order_intent.mismatch"}


def test_rejects_invalid_explicit_order_intent_values() -> None:
    result = resolve_ai_approve_order_intent(
        trade_plan({"execution_type": "pending", "requested_order_type": "BUY_LIMIT"}),
        3335.6,
        3332.5,
        2,
    )
    assert result == {"accepted": False, "reason": "order_intent.mismatch"}

    result = resolve_ai_approve_order_intent(
        trade_plan(
            {
                "execution_type": "pending",
                "requested_order_type": "market",
                "entry_zone": {"min": 3335.5, "max": 3335.7},
            }
        ),
        3335.6,
        3335.6,
        2,
    )
    assert result == {"accepted": False, "reason": "order_intent.mismatch"}

    result = resolve_ai_approve_order_intent(
        trade_plan({"execution_type": "limit", "requested_order_type": "BOGUS"}),
        3335.6,
        3332.5,
        2,
    )
    assert result == {"accepted": False, "reason": "order_intent.mismatch"}

    result = resolve_ai_approve_order_intent(
        trade_plan(
            {
                "execution_type": "MARKET",
                "requested_order_type": "market",
                "entry_zone": {"min": 3335.5, "max": 3335.7},
            }
        ),
        3335.6,
        3335.6,
        2,
    )
    assert result == {"accepted": False, "reason": "order_intent.mismatch"}

    result = resolve_ai_approve_order_intent(
        trade_plan({"execution_type": "Limit", "requested_order_type": "BUY_LIMIT"}),
        3335.6,
        3332.5,
        2,
    )
    assert result == {"accepted": False, "reason": "order_intent.mismatch"}

    result = resolve_ai_approve_order_intent(
        trade_plan({"execution_type": "limit", "requested_order_type": "buy_limit"}),
        3335.6,
        3332.5,
        2,
    )
    assert result == {"accepted": False, "reason": "order_intent.mismatch"}


def test_validates_buy_and_sell_protection_direction() -> None:
    result = validate_ai_approve_protection_direction(
        trade_plan({"side": "buy", "stop_loss": 3330, "take_profit": [3345]}),
        3335.6,
    )
    assert result == {"accepted": True}

    result = validate_ai_approve_protection_direction(
        trade_plan({"side": "sell", "stop_loss": 3340, "take_profit": [3325]}),
        3335.6,
    )
    assert result == {"accepted": True}

    result = validate_ai_approve_protection_direction(
        trade_plan({"side": "buy", "stop_loss": 3336, "take_profit": [3345]}),
        3335.6,
    )
    assert result == {"accepted": False, "reason": "protection.invalid_direction"}

    result = validate_ai_approve_protection_direction(
        trade_plan({"side": "sell", "stop_loss": 3340, "take_profit": [3338]}),
        3335.6,
    )
    assert result == {"accepted": False, "reason": "protection.invalid_direction"}


def test_rejects_missing_or_zero_protection_values() -> None:
    result = validate_ai_approve_protection_direction(
        trade_plan({"stop_loss": None, "take_profit": [3345]}),
        3335.6,
    )
    assert result == {"accepted": False, "reason": "protection.invalid_direction"}

    result = validate_ai_approve_protection_direction(
        trade_plan({"stop_loss": 0, "take_profit": [3345]}),
        3335.6,
    )
    assert result == {"accepted": False, "reason": "protection.invalid_direction"}

    result = validate_ai_approve_protection_direction(
        trade_plan({"stop_loss": 3330, "take_profit": None}),
        3335.6,
    )
    assert result == {"accepted": False, "reason": "protection.invalid_direction"}

    result = validate_ai_approve_protection_direction(
        trade_plan({"stop_loss": 3330, "take_profit": [0]}),
        3335.6,
    )
    assert result == {"accepted": False, "reason": "protection.invalid_direction"}

    result = validate_ai_approve_protection_direction(
        trade_plan({"stop_loss": 3330, "take_profit": [-1, 0]}),
        3335.6,
    )
    assert result == {"accepted": False, "reason": "protection.invalid_direction"}


# ---------------------------------------------------------------------------
# describe('AI approve take profit normalization')
# ---------------------------------------------------------------------------


def test_sorts_executable_buy_targets_from_near_tp1_to_far_tp2_and_gates_rr_on_far_tp2() -> None:
    result = resolve_ai_approve_executable_take_profits(
        {
            "side": "buy",
            "entry": 3335.7,
            "stopLoss": 3330,
            "takeProfitValues": [3350, 3342.825],
        }
    )

    assert result.get("accepted") is True
    assert result.get("tp1") == 3342.825
    assert result.get("tp2") == 3350
    assert result.get("legacyTakeProfit") == 3350
    assert result.get("tpSplit") is True
    targets = result["targets"]
    assert len(targets) == 2
    assert targets[0]["label"] == "TP1" and targets[0]["value"] == 3342.825
    assert targets[1]["label"] == "TP2" and targets[1]["value"] == 3350
    assert_close(float(targets[0]["rr"]), 1.25)
    assert_close(float(targets[1]["rr"]), 2.508771929824561)


def test_accepts_staged_targets_when_near_tp1_is_below_the_floor_as_long_as_far_tp2_qualifies() -> None:
    result = resolve_ai_approve_executable_take_profits(
        {
            "side": "buy",
            "entry": 3335.7,
            "stopLoss": 3330,
            "takeProfitValues": [3340, 3342.825],
        }
    )

    assert result.get("accepted") is True
    assert result.get("tp1") == 3340
    assert result.get("tp2") == 3342.825
    assert result.get("legacyTakeProfit") == 3342.825
    assert result.get("tpSplit") is True


def test_rejects_staged_targets_when_even_the_far_tp2_is_below_the_rr_floor() -> None:
    result = resolve_ai_approve_executable_take_profits(
        {
            "side": "buy",
            "entry": 3335.7,
            "stopLoss": 3330,
            "takeProfitValues": [3340, 3341],
        }
    )
    assert result == {"accepted": False, "reason": "rr.below_minimum", "label": "TP2"}


def test_keeps_single_target_plans_executable_when_the_only_target_qualifies() -> None:
    result = resolve_ai_approve_executable_take_profits(
        {
            "side": "sell",
            "entry": 3335.5,
            "stopLoss": 3340,
            "takeProfitValues": [3329.875],
        }
    )
    assert result.get("accepted") is True
    assert result.get("tp1") == 3329.875
    assert result.get("tp2") == 0
    assert result.get("legacyTakeProfit") == 3329.875
    assert result.get("tpSplit") is False
