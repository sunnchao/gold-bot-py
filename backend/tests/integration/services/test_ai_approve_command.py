"""镜像 apps/app-server/src/services/ai-approve/command.spec.ts(vitest 逐用例)。

每个 describe/it 映射为一个 pytest 用例;toEqual 用断言相等,toMatchObject 用逐键断言。
"""

from __future__ import annotations

from typing import Any

from backend.services.ai_approve import build_ai_approve_command_candidate


def build(
    *,
    account_id: str = "90011087",
    symbol: str = "XAUUSD",
    now_iso: str = "2026-04-13T08:00:00Z",
    order_type: str = "market",
    risk_gate: dict[str, Any] | None = None,
    trade_plan_: dict[str, Any] | None = None,
    positions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    command_input: dict[str, Any] = {
        "accountId": account_id,
        "symbol": symbol,
        "nowIso": now_iso,
        "orderType": order_type,
        "riskGate": risk_gate if risk_gate is not None else {},
        "tradePlan": trade_plan_ if trade_plan_ is not None else {},
    }
    if positions is not None:
        command_input["positions"] = positions
    return build_ai_approve_command_candidate(command_input)


def assert_match(result: dict[str, Any], expected: dict[str, Any]) -> None:
    for key, value in expected.items():
        if isinstance(value, dict):
            nested = result.get(key)
            assert isinstance(nested, dict), f"key {key}: {nested!r} is not a dict"
            assert_match(nested, value)
        else:
            assert result.get(key) == value, f"key {key}: {result.get(key)!r} != {value!r}"


# ---------------------------------------------------------------------------
# describe('AI approve command builder')
# ---------------------------------------------------------------------------


def test_builds_market_signal_payloads_from_accepted_market_intent() -> None:
    command = build(
        now_iso="2026-04-13T16:00:00+08:00",
        order_type="market",
        risk_gate={
            "decision_id": "tpv1_market",
            "mode": "approve",
            "symbol": "XAUUSD",
            "status": "accepted",
            "allowed_lots": 0.03,
        },
        trade_plan_={
            "schema_version": "trade_plan.v1",
            "decision_id": "tpv1_market",
            "account_id": "90011087",
            "symbol": "XAUUSD",
            "mode": "approve",
            "side": "buy",
            "entry_zone": {"min": 3335.5, "max": 3335.7},
            "execution_type": "market",
            "requested_order_type": "market",
            "stop_loss": 3330.456,
            "take_profit": [3344.876],
            "max_lots": 0.2,
            "confidence": 80,
            "narrative": "current price entry",
        },
    )

    assert command == {
        "command_id": "ai_pending_90011087_XAUUSD_1776067200000000000",
        "action": "SIGNAL",
        "symbol": "XAUUSD",
        "type": "BUY",
        "entry": 3335.6,
        "entry_min": 3335.5,
        "entry_max": 3335.7,
        "sl": 3330.456,
        "tp": 3344.876,
        "tp1": 3344.876,
        "tp2": 0,
        "tp_split": False,
        "lots": 0,
        "order_type": "market",
        "expiration": 1776081600,
        "score": 80,
        "strategy": "ai_signal",
        "source": "ai_approve",
        "confidence": 80,
        "decision_id": "tpv1_market",
        "reason": "current price entry",
        "trade_plan_mode": "approve",
        "risk_gate": {
            "decision_id": "tpv1_market",
            "mode": "approve",
            "symbol": "XAUUSD",
            "status": "accepted",
            "allowed_lots": 0.03,
        },
    }


def test_builds_buy_limit_and_sell_limit_payloads_without_deriving_stop_orders() -> None:
    buy_limit = build(
        order_type="BUY_LIMIT",
        risk_gate={"decision_id": "tpv1_buy_limit", "mode": "approve", "symbol": "XAUUSD", "allowed_lots": 0.02},
        trade_plan_={
            "decision_id": "tpv1_buy_limit",
            "mode": "approve",
            "side": "buy",
            "entry_zone": {"min": 3332, "max": 3333},
            "stop_loss": 3328,
            "take_profit": [3345],
            "max_lots": 0.01,
            "confidence": 76,
            "narrative": "buy pullback",
        },
    )

    assert_match(
        buy_limit,
        {
            "type": "BUY",
            "entry": 3332.5,
            "lots": 0,
            "order_type": "BUY_LIMIT",
            "expiration": 1776081600,
            "strategy": "ai_signal",
        },
    )

    sell_limit = build(
        order_type="SELL_LIMIT",
        risk_gate={"decision_id": "tpv1_sell_limit", "mode": "approve", "symbol": "XAUUSD", "allowed_lots": 0.03},
        trade_plan_={
            "decision_id": "tpv1_sell_limit",
            "mode": "approve",
            "side": "sell",
            "entry_zone": {"min": 3338, "max": 3339},
            "stop_loss": 3344,
            "take_profit": [3320],
            "max_lots": 0.01,
            "confidence": 76,
            "narrative": "sell rebound",
        },
    )

    assert_match(
        sell_limit,
        {
            "type": "SELL",
            "entry": 3338.5,
            "lots": 0,
            "order_type": "SELL_LIMIT",
            "expiration": 1776081600,
            "strategy": "ai_signal",
        },
    )

    assert sell_limit["order_type"] not in ("BUY_STOP", "SELL_STOP")
    assert buy_limit["order_type"] not in ("BUY_STOP", "SELL_STOP")


def test_builds_staged_take_profit_payloads_from_validated_executable_targets() -> None:
    command = build(
        order_type="market",
        risk_gate={"decision_id": "tpv1_tp2", "mode": "approve", "symbol": "XAUUSD", "allowed_lots": 0.01},
        trade_plan_={
            "decision_id": "tpv1_tp2",
            "mode": "approve",
            "side": "buy",
            "entry_zone": {"min": 3335.5, "max": 3335.7},
            "stop_loss": 3330,
            "take_profit": [3340, 3345],
            "max_lots": 0.01,
            "confidence": 76,
            "narrative": "staged target",
        },
    )

    assert_match(command, {"tp": 3345, "tp1": 3340, "tp2": 3345, "tp_split": True})


def test_normalizes_staged_take_profits_by_distance_instead_of_trusting_array_order() -> None:
    command = build(
        order_type="market",
        risk_gate={"decision_id": "tpv1_tp_order", "mode": "approve", "symbol": "XAUUSD", "allowed_lots": 0.01},
        trade_plan_={
            "decision_id": "tpv1_tp_order",
            "mode": "approve",
            "side": "buy",
            "entry_zone": {"min": 3335.5, "max": 3335.7},
            "stop_loss": 3330,
            "take_profit": [3345, 3340],
            "max_lots": 0.01,
            "confidence": 76,
            "narrative": "targets arrived far-to-near",
        },
    )

    assert_match(command, {"tp": 3345, "tp1": 3340, "tp2": 3345, "tp_split": True})


def test_preserves_eurusd_input_precision_in_the_ea_payload() -> None:
    command = build(
        symbol="EURUSD",
        order_type="market",
        risk_gate={"decision_id": "tpv1_eurusd_precision", "mode": "approve", "symbol": "EURUSD", "allowed_lots": 0.01},
        trade_plan_={
            "decision_id": "tpv1_eurusd_precision",
            "mode": "approve",
            "side": "buy",
            "entry_zone": {"min": 1.09500, "max": 1.09500},
            "stop_loss": 1.09420,
            "take_profit": [1.09650],
            "max_lots": 0.01,
            "confidence": 76,
            "narrative": "preserve five digit forex prices",
        },
    )

    assert_match(
        command,
        {"entry": 1.095, "entry_min": 1.095, "entry_max": 1.095, "sl": 1.0942, "tp": 1.0965, "tp1": 1.0965, "tp2": 0},
    )
    assert command["entry"] != 1.1
    assert command["sl"] != 1.09
    assert command["tp"] != 1.1


def test_builds_adverse_signal_with_scale_in_add_on_type_level_and_unified_sl_min_open_price_buy() -> None:
    command = build(
        order_type="BUY_LIMIT",
        risk_gate={"decision_id": "tpv1_adverse", "mode": "approve", "symbol": "XAUUSD", "allowed_lots": 0.04},
        trade_plan_={
            "decision_id": "tpv1_adverse",
            "mode": "approve",
            "side": "buy",
            "entry_zone": {"min": 3335.5, "max": 3335.7},
            "stop_loss": 3330,
            "take_profit": [3345],
            "max_lots": 0.05,
            "confidence": 76,
            "narrative": "adverse add-on L1",
            "add_on": True,
            "add_on_type": "adverse",
            "add_on_level": 1,
        },
        positions=[
            {
                "ticket": 1001,
                "symbol": "XAUUSD",
                "type": "BUY",
                "lots": 0.10,
                "open_price": 3337.6,
                "strategy": "ai_signal",
            }
        ],
    )

    assert_match(
        command,
        {
            "type": "BUY",
            "order_type": "BUY_LIMIT",
            "strategy": "ai_signal",
            "scale_in_parent_ticket": 1001,
            "weighted_avg_entry": 3337.6,
            "unified_sl": 3337.6,
            "scale_in_count": 1,
            "scale_in_add_on_type": "adverse",
            "scale_in_add_on_level": 1,
        },
    )


def test_builds_adverse_signal_with_unified_sl_min_open_price_across_multiple_positions_buy() -> None:
    command = build(
        order_type="BUY_LIMIT",
        risk_gate={"decision_id": "tpv1_adverse", "mode": "approve", "symbol": "XAUUSD", "allowed_lots": 0.04},
        trade_plan_={
            "decision_id": "tpv1_adverse",
            "mode": "approve",
            "side": "buy",
            "entry_zone": {"min": 3335.5, "max": 3335.7},
            "stop_loss": 3330,
            "take_profit": [3345],
            "max_lots": 0.05,
            "confidence": 76,
            "narrative": "adverse add-on L2",
            "add_on": True,
            "add_on_type": "adverse",
            "add_on_level": 2,
        },
        positions=[
            {
                "ticket": 1001,
                "symbol": "XAUUSD",
                "type": "BUY",
                "lots": 0.10,
                "open_price": 3340.0,
                "strategy": "ai_signal",
            },
            {
                "ticket": 1002,
                "symbol": "XAUUSD",
                "type": "BUY",
                "lots": 0.06,
                "open_price": 3338.0,
                "strategy": "ai_signal",
            },
        ],
    )

    assert_match(
        command,
        {
            "scale_in_parent_ticket": 1002,
            "weighted_avg_entry": 3339.25,
            "unified_sl": 3338,
            "scale_in_count": 2,
            "scale_in_add_on_type": "adverse",
            "scale_in_add_on_level": 2,
        },
    )


def test_leaves_command_lots_at_zero_while_preserving_risk_gate_allowed_lots_as_audit_metadata() -> None:
    command = build(
        order_type="market",
        risk_gate={"decision_id": "tpv1_lots", "mode": "approve", "symbol": "XAUUSD", "allowed_lots": 0.07},
        trade_plan_={
            "decision_id": "tpv1_lots",
            "mode": "approve",
            "side": "buy",
            "entry_zone": {"min": 3335.5, "max": 3335.7},
            "stop_loss": 3330,
            "take_profit": [3345],
            "max_lots": 0.01,
            "confidence": 76,
            "narrative": "gate sized trade",
        },
    )

    assert command["lots"] == 0
    assert_match(command["risk_gate"], {"allowed_lots": 0.07})


def test_builds_commands_when_risk_gate_allowed_lots_is_zero_or_missing() -> None:
    base_input: dict[str, Any] = {
        "now_iso": "2026-04-13T08:00:00Z",
        "order_type": "market",
        "trade_plan_": {
            "decision_id": "tpv1_invalid_lots",
            "mode": "approve",
            "side": "buy",
            "entry_zone": {"min": 3335.5, "max": 3335.7},
            "stop_loss": 3330,
            "take_profit": [3345],
            "max_lots": 0.05,
            "confidence": 76,
            "narrative": "invalid gate size",
        },
    }

    zero_lots = build(
        risk_gate={"decision_id": "tpv1_zero_lots", "mode": "approve", "symbol": "XAUUSD", "allowed_lots": 0},
        **base_input,
    )
    assert_match(zero_lots, {"lots": 0, "risk_gate": {"allowed_lots": 0}})

    missing_lots = build(
        risk_gate={"decision_id": "tpv1_missing_lots", "mode": "approve", "symbol": "XAUUSD"},
        **base_input,
    )
    assert_match(missing_lots, {"lots": 0, "risk_gate": {"decision_id": "tpv1_missing_lots"}})
