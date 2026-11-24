"""镜像 apps/app-server/src/services/ai-approve/gate.spec.ts(vitest 逐用例)。

每个 describe/it 映射为一个 pytest 用例;内存 store 经 create_in_memory_store 创建,
toEqual 用断言相等,toMatchObject 用逐键断言。
"""

from __future__ import annotations

import time
from typing import Any

from backend.persistence.helpers import current_timestamp
from backend.persistence.records import EaRecord
from backend.persistence.store import EaStore, create_in_memory_store
from backend.services.ai_approve import (
    create_ai_approve_cooldown,
    evaluate_ai_approve_pending_gate,
)

ACCOUNT_ID = "90011087"
SYMBOL = "XAUUSD"
NOW_ISO = "2026-04-13T08:00:00.000Z"


def trade_plan(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    plan: dict[str, Any] = {
        "schema_version": "trade_plan.v1",
        "decision_id": "tpv1_buy",
        "account_id": ACCOUNT_ID,
        "symbol": SYMBOL,
        "mode": "approve",
        "side": "buy",
        "confidence": 80,
        "entry_zone": {"min": 3335.5, "max": 3335.7},
        "execution_type": "market",
        "requested_order_type": "market",
        "stop_loss": 3330,
        "take_profit": [3345],
        "max_lots": 0.2,
        "expires_at": "2099-06-06T09:15:00Z",
        "reason_codes": ["mode.approve", "side.buy"],
        "narrative": "approved by AI",
    }
    if overrides:
        plan.update(overrides)
    return plan


async def seed_strong_trend_state(
    store: EaStore,
    trend: str = "bull",
    symbol: str = SYMBOL,
) -> None:
    await store.save_registration({"account_id": ACCOUNT_ID, "ai_symbols": [symbol]})
    await store.save_tick(
        {
            "account_id": ACCOUNT_ID,
            "symbol": symbol,
            "bid": 3335.5,
            "ask": 3335.7,
            "spread": 0.2,
            "time": "2026-04-13T07:59:30.000Z",
        }
    )
    if trend == "bull":
        bar: EaRecord = {"close": 3336, "ema20": 3335, "ema50": 3330, "adx": 35, "atr": 2, "rsi": 60}
    elif trend == "bear":
        bar = {"close": 3334, "ema20": 3335, "ema50": 3340, "adx": 35, "atr": 2, "rsi": 40}
    elif trend == "missing-indicators":
        bar = {"open": 3335, "high": 3340, "low": 3330, "close": 3336, "volume": 100, "atr": 2}
    else:
        bar = {"close": 3335, "ema20": 3335, "ema50": 3335, "adx": 10, "atr": 2, "rsi": 50}
    for timeframe in ("D1", "H4", "H1", "M30", "M15"):
        await store.save_bars(
            {"account_id": ACCOUNT_ID, "symbol": symbol, "timeframe": timeframe, "bars": [bar]}
        )


async def evaluate(
    store: EaStore,
    *,
    symbol: str = SYMBOL,
    trade_plan_: dict[str, Any] | None = None,
    now_iso: str = NOW_ISO,
    cooldown: Any = None,
    position_states: list[EaRecord] | None = None,
) -> dict[str, Any]:
    gate_input: dict[str, Any] = {
        "store": store,
        "accountId": ACCOUNT_ID,
        "symbol": symbol,
        "tradePlan": trade_plan_ if trade_plan_ is not None else trade_plan(),
        "nowIso": now_iso,
    }
    if cooldown is not None:
        gate_input["cooldown"] = cooldown
    if position_states is not None:
        gate_input["positionStates"] = position_states
    return await evaluate_ai_approve_pending_gate(gate_input)


def assert_match(result: dict[str, Any], expected: dict[str, Any]) -> None:
    for key, value in expected.items():
        if isinstance(value, dict):
            nested = result.get(key)
            assert isinstance(nested, dict), f"key {key}: {nested!r} is not a dict"
            assert_match(nested, value)
        else:
            assert result.get(key) == value, f"key {key}: {result.get(key)!r} != {value!r}"


# ---------------------------------------------------------------------------
# describe('AI approve pending gate')
# ---------------------------------------------------------------------------


async def test_rejects_an_account_symbol_that_is_not_present_in_ai_symbols() -> None:
    store = create_in_memory_store()
    await store.save_registration({"account_id": ACCOUNT_ID, "ai_symbols": ["XAUUSD"]})

    result = await evaluate(store, symbol="US100Cash", trade_plan_=trade_plan({"symbol": "US100Cash"}))
    assert result == {"accepted": False, "reason": "account.symbol_not_loaded"}


async def test_fails_closed_when_registration_ai_symbols_are_missing() -> None:
    store = create_in_memory_store()

    result = await evaluate(store)
    assert result == {"accepted": False, "reason": "account.symbol_not_loaded"}


async def test_accepts_valid_approve_plans_when_market_context_is_go_compatible() -> None:
    store = create_in_memory_store()
    await seed_strong_trend_state(store)

    result = await evaluate(store)
    assert_match(result, {"accepted": True, "currentPrice": 3335.6, "entry": 3335.6, "lots": 0, "h1Atr": 2})


async def test_matches_ai_symbols_case_insensitively_after_trimming_and_uses_registered_contract_symbol() -> None:
    store = create_in_memory_store()
    await seed_strong_trend_state(store, symbol="GOLDm#")

    result = await evaluate(store, symbol=" goldm# ", trade_plan_=trade_plan({"symbol": "GOLDm#"}))
    assert_match(result, {"accepted": True, "currentPrice": 3335.6, "entry": 3335.6, "h1Atr": 2})


async def test_rejects_otherwise_valid_approve_plans_when_execution_rr_is_1_249() -> None:
    store = create_in_memory_store()
    await seed_strong_trend_state(store)

    result = await evaluate(store, trade_plan_=trade_plan({"take_profit": [3342.8193]}))
    assert result == {"accepted": False, "reason": "rr.below_minimum"}


async def test_accepts_otherwise_valid_approve_plans_when_execution_rr_is_exactly_1_25() -> None:
    store = create_in_memory_store()
    await seed_strong_trend_state(store)

    result = await evaluate(store, trade_plan_=trade_plan({"take_profit": [3342.825]}))
    assert_match(result, {"accepted": True, "currentPrice": 3335.6, "entry": 3335.6})


async def test_accepts_staged_approve_plans_when_tp1_rr_is_below_1_25_as_long_as_far_tp2_qualifies() -> None:
    store = create_in_memory_store()
    await seed_strong_trend_state(store)

    result = await evaluate(store, trade_plan_=trade_plan({"take_profit": [3340, 3342.825]}))
    assert_match(result, {"accepted": True, "currentPrice": 3335.6, "entry": 3335.6})


async def test_rejects_staged_approve_plans_when_tp1_is_invalid_even_if_tp2_qualifies() -> None:
    store = create_in_memory_store()
    await seed_strong_trend_state(store)

    result = await evaluate(store, trade_plan_=trade_plan({"take_profit": [3335.65, 3342.825]}))
    assert result == {"accepted": False, "reason": "rr.invalid"}


async def test_normalizes_staged_approve_targets_by_distance_before_validating_each_child_target() -> None:
    store = create_in_memory_store()
    await seed_strong_trend_state(store)

    result = await evaluate(store, trade_plan_=trade_plan({"take_profit": [3350, 3342.825]}))
    assert_match(result, {"accepted": True, "currentPrice": 3335.6, "entry": 3335.6})


async def test_rejects_market_approve_plans_whose_final_execution_rr_geometry_is_invalid() -> None:
    store = create_in_memory_store()
    await seed_strong_trend_state(store)

    result = await evaluate(store, trade_plan_=trade_plan({"take_profit": [3335.65]}))
    assert result == {"accepted": False, "reason": "rr.invalid"}


async def test_rejects_active_duplicate_ai_approve_pending_commands() -> None:
    store = create_in_memory_store()
    await seed_strong_trend_state(store)
    pending = await store.save_command_candidate(
        ACCOUNT_ID,
        {
            "command_id": "ai_pending_90011087_XAUUSD_active",
            "source": "ai_approve",
            "symbol": SYMBOL,
            "type": "BUY",
            "action": "SIGNAL",
            "expiration": 1776081600,
        },
    )
    await store.promote_command(pending["command_id"])

    result = await evaluate(store)
    assert result == {"accepted": False, "reason": "pending.duplicate"}


async def test_no_longer_rejects_when_per_symbol_daily_ai_signal_limit_was_removed() -> None:
    store = create_in_memory_store()
    await seed_strong_trend_state(store)
    # 每日配额(原 Phase 4.1, 上限 2)已移除(2026-09-07 老板拍板)。这里写入远超旧上限
    # (5 条 SELL)的已下发命令,断言不再被 daily_limit.symbol 拦截。
    # 用 SELL 方向铺历史命令;待测 plan 为 BUY,避免撞 pending.duplicate(BUY 侧无重复)。
    real_now_iso = current_timestamp()
    expiration = int(time.time()) + 4 * 60 * 60
    for suffix in ("a", "b", "c", "d", "e"):
        command = await store.save_command_candidate(
            ACCOUNT_ID,
            {
                "command_id": f"ai_pending_90011087_XAUUSD_daily_{suffix}",
                "source": "ai_approve",
                "symbol": SYMBOL,
                "type": "SELL",
                "action": "SIGNAL",
                "expiration": expiration,
            },
        )
        await store.promote_command(command["command_id"])

    result = await evaluate(store, now_iso=real_now_iso)
    # 不再因当日累计条数拒绝 —— 只受其余关卡(pending.duplicate/cooldown 等)约束。
    assert result.get("reason") != "daily_limit.symbol"


async def test_accepts_weak_trend_consensus_because_ea_owns_position_size() -> None:
    # 老板决策:仓位由 EA 参数决定,服务端不再因弱趋势拒绝或减半。
    store = create_in_memory_store()
    await seed_strong_trend_state(store, trend="neutral")

    result = await evaluate(store)
    assert result["accepted"] is True


async def test_mirrors_go_same_side_and_add_on_distance_gates() -> None:
    store = create_in_memory_store()
    await seed_strong_trend_state(store)
    await store.save_positions(
        {
            "account_id": ACCOUNT_ID,
            "symbol": SYMBOL,
            "positions": [
                {
                    "ticket": 1001,
                    "symbol": SYMBOL,
                    "type": "BUY",
                    "lots": 0.1,
                    "open_price": 3335,
                    "strategy": "ai_signal",
                }
            ],
        }
    )

    result = await evaluate(store)
    assert result == {"accepted": False, "reason": "position.same_side"}

    result = await evaluate(store, trade_plan_=trade_plan({"add_on": True}))
    assert result == {"accepted": False, "reason": "position.add_on_distance"}

    result = await evaluate(
        store,
        trade_plan_=trade_plan(
            {
                "execution_type": "limit",
                "requested_order_type": "BUY_LIMIT",
                "entry_zone": {"min": 3332.8, "max": 3332.8},
                "add_on": True,
            }
        ),
    )
    assert_match(result, {"accepted": True, "entry": 3332.8})


async def test_rejects_cooldown_and_far_h1_atr_entry_distance() -> None:
    store = create_in_memory_store()
    await seed_strong_trend_state(store)
    cooldown = create_ai_approve_cooldown()
    cooldown.mark(SYMBOL, "2026-04-13T07:45:00.000Z")

    result = await evaluate(store, cooldown=cooldown)
    assert result == {"accepted": False, "reason": "cooldown.active"}

    result = await evaluate(
        store,
        trade_plan_=trade_plan(
            {
                "execution_type": "limit",
                "requested_order_type": "BUY_LIMIT",
                "entry_zone": {"min": 3328, "max": 3328},
                "stop_loss": 3320,
            }
        ),
    )
    assert result == {"accepted": False, "reason": "entry.too_far_from_market"}


async def test_returns_accepted_order_type_for_explicit_market_buy_limit_and_sell_limit_plans() -> None:
    store = create_in_memory_store()
    await seed_strong_trend_state(store)

    result = await evaluate(
        store,
        trade_plan_=trade_plan(
            {
                "execution_type": "market",
                "requested_order_type": "market",
                "entry_zone": {"min": 3335.5, "max": 3335.7},
            }
        ),
    )
    assert_match(result, {"accepted": True, "orderType": "market"})

    result = await evaluate(
        store,
        trade_plan_=trade_plan(
            {
                "execution_type": "limit",
                "requested_order_type": "BUY_LIMIT",
                "entry_zone": {"min": 3332.5, "max": 3332.5},
            }
        ),
    )
    assert_match(result, {"accepted": True, "orderType": "BUY_LIMIT"})

    await seed_strong_trend_state(store, trend="bear")
    result = await evaluate(
        store,
        trade_plan_=trade_plan(
            {
                "side": "sell",
                "execution_type": "limit",
                "requested_order_type": "SELL_LIMIT",
                "entry_zone": {"min": 3338.5, "max": 3338.5},
                "stop_loss": 3344,
                "take_profit": [3325],
                "reason_codes": ["mode.approve", "side.sell"],
            }
        ),
    )
    assert_match(result, {"accepted": True, "orderType": "SELL_LIMIT"})


async def test_accepts_lower_confidence_sell_limit_plans_when_trend_context_is_bearish() -> None:
    store = create_in_memory_store()
    await seed_strong_trend_state(store, trend="bear")

    result = await evaluate(
        store,
        trade_plan_=trade_plan(
            {
                "side": "sell",
                "confidence": 70,
                "execution_type": "limit",
                "requested_order_type": "SELL_LIMIT",
                "entry_zone": {"min": 3338.5, "max": 3338.5},
                "stop_loss": 3344,
                "take_profit": [3325],
                "reason_codes": ["mode.approve", "side.sell"],
            }
        ),
    )
    assert_match(result, {"accepted": True, "orderType": "SELL_LIMIT"})


async def test_does_not_reject_approved_plans_just_because_ema_adx_trend_indicators_are_absent() -> None:
    store = create_in_memory_store()
    await seed_strong_trend_state(store, trend="missing-indicators")

    result = await evaluate(
        store,
        trade_plan_=trade_plan(
            {
                "side": "sell",
                "confidence": 68,
                "execution_type": "limit",
                "requested_order_type": "SELL_LIMIT",
                "entry_zone": {"min": 3338.5, "max": 3338.5},
                "stop_loss": 3344,
                "take_profit": [3325],
                "max_lots": 0.08,
                "reason_codes": ["mode.approve", "side.sell"],
            }
        ),
    )
    assert_match(result, {"accepted": True, "orderType": "SELL_LIMIT"})


async def test_rejects_disabled_stops_and_mismatched_market_entry() -> None:
    store = create_in_memory_store()
    await seed_strong_trend_state(store)

    result = await evaluate(
        store, trade_plan_=trade_plan({"execution_type": "stop", "requested_order_type": "BUY_STOP"})
    )
    assert result == {"accepted": False, "reason": "stop_order.disabled"}

    result = await evaluate(
        store,
        trade_plan_=trade_plan(
            {
                "execution_type": "market",
                "requested_order_type": "market",
                "entry_zone": {"min": 3332, "max": 3332},
            }
        ),
    )
    assert result == {"accepted": False, "reason": "market_entry_mismatch"}

    result = await evaluate(
        store,
        trade_plan_=trade_plan(
            {
                "execution_type": "limit",
                "requested_order_type": "BUY_LIMIT",
                "entry_zone": {"min": 3338, "max": 3338},
            }
        ),
    )
    assert_match(result, {"accepted": True, "orderType": "market"})

    result = await evaluate(
        store,
        trade_plan_=trade_plan(
            {
                "execution_type": "limit",
                "requested_order_type": "BUY_LIMIT",
                "entry_zone": {"min": 3332.5, "max": 3332.5},
                "stop_loss": 3338,
            }
        ),
    )
    assert result == {"accepted": False, "reason": "protection.invalid_direction"}


async def test_rejects_otherwise_valid_plans_that_omit_explicit_order_intent() -> None:
    store = create_in_memory_store()
    await seed_strong_trend_state(store)

    result = await evaluate(
        store, trade_plan_=trade_plan({"execution_type": None, "requested_order_type": None})
    )
    assert result == {"accepted": False, "reason": "order_intent.missing"}


async def test_accepts_favorable_add_on_when_profit_gte_1_0_atr_and_new_lots_lte_existing_times_0_5() -> None:
    store = create_in_memory_store()
    await seed_strong_trend_state(store)
    await store.save_positions(
        {
            "account_id": ACCOUNT_ID,
            "symbol": SYMBOL,
            "positions": [
                {
                    "ticket": 2001,
                    "symbol": SYMBOL,
                    "type": "BUY",
                    "lots": 0.2,
                    "open_price": 3333.6,
                    "strategy": "ai_signal",
                }
            ],
        }
    )

    result = await evaluate(
        store,
        trade_plan_=trade_plan(
            {
                "add_on": True,
                "add_on_type": "favorable",
                "entry_zone": {"min": 3335.5, "max": 3335.7},
                "max_lots": 0.1,
            }
        ),
    )
    assert_match(result, {"accepted": True})


async def test_rejects_favorable_add_on_when_profit_lt_1_0_atr() -> None:
    store = create_in_memory_store()
    await seed_strong_trend_state(store)
    await store.save_positions(
        {
            "account_id": ACCOUNT_ID,
            "symbol": SYMBOL,
            "positions": [
                {
                    "ticket": 2001,
                    "symbol": SYMBOL,
                    "type": "BUY",
                    "lots": 0.2,
                    "open_price": 3335.0,
                    "strategy": "ai_signal",
                }
            ],
        }
    )

    result = await evaluate(
        store,
        trade_plan_=trade_plan(
            {
                "add_on": True,
                "add_on_type": "favorable",
                "entry_zone": {"min": 3337.6, "max": 3337.8},
                "execution_type": "limit",
                "requested_order_type": "BUY_LIMIT",
                "max_lots": 0.1,
            }
        ),
    )
    assert result == {"accepted": False, "reason": "position.favorable_add_profit_not_enough"}


async def test_rejects_favorable_add_on_when_new_lots_gt_existing_times_0_5() -> None:
    store = create_in_memory_store()
    await seed_strong_trend_state(store)
    await store.save_positions(
        {
            "account_id": ACCOUNT_ID,
            "symbol": SYMBOL,
            "positions": [
                {
                    "ticket": 2001,
                    "symbol": SYMBOL,
                    "type": "BUY",
                    "lots": 0.01,
                    "open_price": 3333.6,
                    "strategy": "ai_signal",
                }
            ],
        }
    )

    result = await evaluate(
        store,
        trade_plan_=trade_plan(
            {
                "add_on": True,
                "add_on_type": "favorable",
                "entry_zone": {"min": 3335.5, "max": 3335.7},
                "max_lots": 0.15,
            }
        ),
    )
    assert result == {"accepted": False, "reason": "position.favorable_add_lots_too_large"}


# ---------------------------------------------------------------------------
# describe('AI approve favorable add-on')
# ---------------------------------------------------------------------------


async def test_favorable_addon_accepts_when_profit_gte_1_0_atr_and_lots_lte_existing_times_0_5() -> None:
    store = create_in_memory_store()
    await seed_strong_trend_state(store)
    await store.save_positions(
        {
            "account_id": ACCOUNT_ID,
            "symbol": SYMBOL,
            "positions": [
                {
                    "ticket": 1001,
                    "symbol": SYMBOL,
                    "type": "BUY",
                    "lots": 0.10,
                    "open_price": 3333.0,
                    "sl": 3330.0,
                    "tp": 3340.0,
                    "profit": 260,
                    "strategy": "ai_signal",
                }
            ],
        }
    )

    result = await evaluate(
        store,
        trade_plan_=trade_plan(
            {
                "add_on": True,
                "add_on_type": "favorable",
                "max_lots": 0.05,
                "entry_zone": {"min": 3336.0, "max": 3336.2},
                "execution_type": "limit",
                "requested_order_type": "BUY_LIMIT",
            }
        ),
    )
    assert_match(result, {"accepted": True, "lots": 0})


async def test_favorable_addon_rejects_when_profit_lt_1_0_atr() -> None:
    store = create_in_memory_store()
    await seed_strong_trend_state(store)
    await store.save_positions(
        {
            "account_id": ACCOUNT_ID,
            "symbol": SYMBOL,
            "positions": [
                {
                    "ticket": 1001,
                    "symbol": SYMBOL,
                    "type": "BUY",
                    "lots": 0.10,
                    "open_price": 3334.5,
                    "sl": 3330.0,
                    "tp": 3340.0,
                    "profit": 110,
                    "strategy": "ai_signal",
                }
            ],
        }
    )

    result = await evaluate(
        store,
        trade_plan_=trade_plan(
            {
                "add_on": True,
                "add_on_type": "favorable",
                "max_lots": 0.05,
                "entry_zone": {"min": 3336.6, "max": 3336.8},
                "execution_type": "limit",
                "requested_order_type": "BUY_LIMIT",
            }
        ),
    )
    assert result == {"accepted": False, "reason": "position.favorable_add_profit_not_enough"}


async def test_favorable_addon_rejects_when_new_lots_gt_existing_times_0_5() -> None:
    store = create_in_memory_store()
    await seed_strong_trend_state(store)
    await store.save_positions(
        {
            "account_id": ACCOUNT_ID,
            "symbol": SYMBOL,
            "positions": [
                {
                    "ticket": 1001,
                    "symbol": SYMBOL,
                    "type": "BUY",
                    "lots": 0.01,
                    "open_price": 3333.0,
                    "sl": 3330.0,
                    "tp": 3340.0,
                    "profit": 104,
                    "strategy": "ai_signal",
                }
            ],
        }
    )

    result = await evaluate(
        store,
        trade_plan_=trade_plan(
            {
                "add_on": True,
                "add_on_type": "favorable",
                "max_lots": 0.10,
                "entry_zone": {"min": 3336.0, "max": 3336.2},
                "execution_type": "limit",
                "requested_order_type": "BUY_LIMIT",
            }
        ),
    )
    assert result == {"accepted": False, "reason": "position.favorable_add_lots_too_large"}


# ---------------------------------------------------------------------------
# describe('AI approve adverse add-on')
# ---------------------------------------------------------------------------


async def test_adverse_addon_accepts_l1_when_loss_gte_1_0_atr_spacing_gte_1_0_atr_lots_lte_net_times_0_6() -> None:
    store = create_in_memory_store()
    await seed_strong_trend_state(store)
    await store.save_positions(
        {
            "account_id": ACCOUNT_ID,
            "symbol": SYMBOL,
            "positions": [
                {
                    "ticket": 1001,
                    "symbol": SYMBOL,
                    "type": "BUY",
                    "lots": 0.10,
                    "open_price": 3337.6,
                    "sl": 3330.0,
                    "tp": 3340.0,
                    "strategy": "ai_signal",
                }
            ],
        }
    )

    result = await evaluate(
        store,
        trade_plan_=trade_plan(
            {
                "add_on": True,
                "add_on_type": "adverse",
                "add_on_level": 1,
                "max_lots": 0.05,
                "entry_zone": {"min": 3335.5, "max": 3335.7},
                "execution_type": "limit",
                "requested_order_type": "BUY_LIMIT",
            }
        ),
    )
    assert_match(result, {"accepted": True, "lots": 0})


async def test_adverse_addon_rejects_when_loss_lt_1_0_atr_l1() -> None:
    store = create_in_memory_store()
    await seed_strong_trend_state(store)
    await store.save_positions(
        {
            "account_id": ACCOUNT_ID,
            "symbol": SYMBOL,
            "positions": [
                {
                    "ticket": 1001,
                    "symbol": SYMBOL,
                    "type": "BUY",
                    "lots": 0.10,
                    "open_price": 3336.0,
                    "sl": 3330.0,
                    "tp": 3340.0,
                    "strategy": "ai_signal",
                }
            ],
        }
    )

    result = await evaluate(
        store,
        trade_plan_=trade_plan(
            {
                "add_on": True,
                "add_on_type": "adverse",
                "add_on_level": 1,
                "max_lots": 0.05,
                "entry_zone": {"min": 3333.4, "max": 3333.6},
                "execution_type": "limit",
                "requested_order_type": "BUY_LIMIT",
            }
        ),
    )
    assert result == {"accepted": False, "reason": "position.adverse_add_loss_not_enough"}


async def test_adverse_addon_rejects_l2_when_spacing_lt_1_5_atr() -> None:
    store = create_in_memory_store()
    await seed_strong_trend_state(store)
    await store.save_positions(
        {
            "account_id": ACCOUNT_ID,
            "symbol": SYMBOL,
            "positions": [
                {
                    "ticket": 1001,
                    "symbol": SYMBOL,
                    "type": "BUY",
                    "lots": 0.10,
                    "open_price": 3340.0,
                    "sl": 3330.0,
                    "tp": 3340.0,
                    "strategy": "ai_signal",
                }
            ],
        }
    )

    result = await evaluate(
        store,
        trade_plan_=trade_plan(
            {
                "add_on": True,
                "add_on_type": "adverse",
                "add_on_level": 2,
                "max_lots": 0.05,
                "entry_zone": {"min": 3337.0, "max": 3337.2},
                "execution_type": "limit",
                "requested_order_type": "BUY_LIMIT",
            }
        ),
    )
    assert result == {"accepted": False, "reason": "position.add_on_distance"}


async def test_adverse_addon_rejects_l2_when_time_interval_not_elapsed_45min() -> None:
    store = create_in_memory_store()
    await seed_strong_trend_state(store)
    await store.save_positions(
        {
            "account_id": ACCOUNT_ID,
            "symbol": SYMBOL,
            "positions": [
                {
                    "ticket": 1001,
                    "symbol": SYMBOL,
                    "type": "BUY",
                    "lots": 0.10,
                    "open_price": 3340.0,
                    "sl": 3330.0,
                    "tp": 3340.0,
                    "strategy": "ai_signal",
                }
            ],
        }
    )
    await store.save_position_state(
        ACCOUNT_ID,
        SYMBOL,
        {
            "ticket": 1001,
            "tp1_hit": False,
            "tp2_hit": False,
            "max_profit_atr": 0,
            "be_moved": False,
            "be_trigger_atr": 1.5,
            "best_sl": 0,
            "open_time": "2026-04-13T06:00:00.000Z",
            "last_modify_time": "2026-04-13T07:10:00.000Z",
            "add_on_count": 1,
            "last_add_on_time": "2026-04-13T07:30:00.000Z",
            "last_add_on_price": 3338.0,
            "group_id": "",
            "group_avg_entry": 0,
            "group_best_sl": 0,
        },
    )

    result = await evaluate(
        store,
        trade_plan_=trade_plan(
            {
                "add_on": True,
                "add_on_type": "adverse",
                "add_on_level": 2,
                "max_lots": 0.05,
                "entry_zone": {"min": 3335.5, "max": 3335.7},
                "execution_type": "limit",
                "requested_order_type": "BUY_LIMIT",
            }
        ),
        now_iso="2026-04-13T07:50:00.000Z",
    )
    assert result == {"accepted": False, "reason": "position.adverse_add_interval_active"}


async def test_adverse_addon_rejects_when_count_exceeded_max_add_count_2() -> None:
    store = create_in_memory_store()
    await seed_strong_trend_state(store)
    await store.save_positions(
        {
            "account_id": ACCOUNT_ID,
            "symbol": SYMBOL,
            "positions": [
                {
                    "ticket": 1001,
                    "symbol": SYMBOL,
                    "type": "BUY",
                    "lots": 0.10,
                    "open_price": 3343.0,
                    "sl": 3330.0,
                    "tp": 3340.0,
                    "strategy": "ai_signal",
                }
            ],
        }
    )
    await store.save_position_state(
        ACCOUNT_ID,
        SYMBOL,
        {
            "ticket": 1001,
            "tp1_hit": False,
            "tp2_hit": False,
            "max_profit_atr": 0,
            "be_moved": False,
            "be_trigger_atr": 1.5,
            "best_sl": 0,
            "open_time": "2026-04-13T05:00:00.000Z",
            "last_modify_time": "2026-04-13T07:00:00.000Z",
            "add_on_count": 2,
            "last_add_on_time": "2026-04-13T05:30:00.000Z",
            "last_add_on_price": 3339.0,
            "group_id": "",
            "group_avg_entry": 0,
            "group_best_sl": 0,
        },
    )

    result = await evaluate(
        store,
        trade_plan_=trade_plan(
            {
                "add_on": True,
                "add_on_type": "adverse",
                "add_on_level": 3,
                "max_add_count": 2,
                "max_lots": 0.05,
                "entry_zone": {"min": 3335.5, "max": 3335.7},
                "execution_type": "limit",
                "requested_order_type": "BUY_LIMIT",
            }
        ),
    )
    assert result == {"accepted": False, "reason": "position.adverse_add_count_exceeded"}


async def test_adverse_addon_rejects_when_single_lots_gt_net_times_0_6() -> None:
    store = create_in_memory_store()
    await seed_strong_trend_state(store)
    await store.save_positions(
        {
            "account_id": ACCOUNT_ID,
            "symbol": SYMBOL,
            "positions": [
                {
                    "ticket": 1001,
                    "symbol": SYMBOL,
                    "type": "BUY",
                    "lots": 0.01,
                    "open_price": 3338.0,
                    "sl": 3330.0,
                    "tp": 3340.0,
                    "strategy": "ai_signal",
                }
            ],
        }
    )

    result = await evaluate(
        store,
        trade_plan_=trade_plan(
            {
                "add_on": True,
                "add_on_type": "adverse",
                "add_on_level": 1,
                "max_lots": 0.10,
                "entry_zone": {"min": 3335.5, "max": 3335.7},
                "execution_type": "limit",
                "requested_order_type": "BUY_LIMIT",
            }
        ),
    )
    assert result == {"accepted": False, "reason": "position.adverse_add_single_lots_too_large"}


async def test_adverse_addon_rejects_when_total_lots_gt_max_total_lots() -> None:
    store = create_in_memory_store()
    await seed_strong_trend_state(store)
    await store.save_positions(
        {
            "account_id": ACCOUNT_ID,
            "symbol": SYMBOL,
            "positions": [
                {
                    "ticket": 1001,
                    "symbol": SYMBOL,
                    "type": "BUY",
                    "lots": 0.10,
                    "open_price": 3338.0,
                    "sl": 3330.0,
                    "tp": 3340.0,
                    "strategy": "ai_signal",
                }
            ],
        }
    )

    result = await evaluate(
        store,
        trade_plan_=trade_plan(
            {
                "add_on": True,
                "add_on_type": "adverse",
                "add_on_level": 1,
                "max_lots": 0.05,
                "max_total_lots": 0.10,
                "entry_zone": {"min": 3335.5, "max": 3335.7},
                "execution_type": "limit",
                "requested_order_type": "BUY_LIMIT",
            }
        ),
    )
    assert result == {"accepted": False, "reason": "position.adverse_add_total_lots_exceeded"}


async def test_adverse_addon_rejects_when_account_drawdown_gte_5_percent() -> None:
    store = create_in_memory_store()
    await seed_strong_trend_state(store)
    await store.save_positions(
        {
            "account_id": ACCOUNT_ID,
            "symbol": SYMBOL,
            "positions": [
                {
                    "ticket": 1001,
                    "symbol": SYMBOL,
                    "type": "BUY",
                    "lots": 0.10,
                    "open_price": 3338.0,
                    "sl": 3330.0,
                    "tp": 3340.0,
                    "strategy": "ai_signal",
                }
            ],
        }
    )
    await store.save_heartbeat(
        {"account_id": ACCOUNT_ID, "balance": 10000, "equity": 9400, "time": "2026-04-13T07:59:00.000Z"}
    )

    result = await evaluate(
        store,
        trade_plan_=trade_plan(
            {
                "add_on": True,
                "add_on_type": "adverse",
                "add_on_level": 1,
                "max_lots": 0.05,
                "entry_zone": {"min": 3335.5, "max": 3335.7},
                "execution_type": "limit",
                "requested_order_type": "BUY_LIMIT",
            }
        ),
    )
    assert result == {"accepted": False, "reason": "position.adverse_add_account_drawdown_exceeded"}


async def test_d1_bear_trend_does_not_reject_intraday_buy_when_intraday_neutral_or_split() -> None:
    store = create_in_memory_store()
    await seed_strong_trend_state(store, trend="neutral")
    bear_bar: EaRecord = {"close": 3334, "ema20": 3335, "ema50": 3340, "adx": 35, "atr": 2, "rsi": 40}
    await store.save_bars({"account_id": ACCOUNT_ID, "symbol": SYMBOL, "timeframe": "D1", "bars": [bear_bar]})

    neutral_result = await evaluate(store, trade_plan_=trade_plan({"confidence": 65}))
    assert_match(neutral_result, {"accepted": True, "orderType": "market"})

    bull_bar: EaRecord = {"close": 3336, "ema20": 3335, "ema50": 3330, "adx": 35, "atr": 2, "rsi": 60}
    await store.save_bars({"account_id": ACCOUNT_ID, "symbol": SYMBOL, "timeframe": "H1", "bars": [bull_bar]})
    await store.save_bars({"account_id": ACCOUNT_ID, "symbol": SYMBOL, "timeframe": "M30", "bars": [bear_bar]})

    split_result = await evaluate(store, trade_plan_=trade_plan({"confidence": 65}))
    assert_match(split_result, {"accepted": True, "orderType": "market"})


async def test_d1_swing_level_does_not_reject_market_buy_as_unconfirmed_breakout() -> None:
    store = create_in_memory_store()
    await seed_strong_trend_state(store, trend="neutral")
    d1_bars = [
        {"open": 3328, "high": 3330, "low": 3326, "close": 3329, "atr": 20},
        {"open": 3329, "high": 3332, "low": 3327, "close": 3331, "atr": 20},
        {"open": 3331, "high": 3335.0, "low": 3329, "close": 3332, "atr": 20},
        {"open": 3332, "high": 3333, "low": 3328, "close": 3330, "atr": 20},
        {"open": 3330, "high": 3334, "low": 3329, "close": 3333, "atr": 20},
        {"open": 3333, "high": 3336.5, "low": 3332, "close": 3335.5, "atr": 20},
    ]
    await store.save_bars({"account_id": ACCOUNT_ID, "symbol": SYMBOL, "timeframe": "D1", "bars": d1_bars})

    result = await evaluate(store, trade_plan_=trade_plan({"confidence": 80}))
    assert_match(result, {"accepted": True, "orderType": "market"})


def _make_raw_ohlcv_bars(count: int, *, start_price: float, step: float, spread_hl: float = 2.0) -> list[EaRecord]:
    bars: list[EaRecord] = []
    for idx in range(count):
        open_p = start_price + idx * step
        close_p = open_p + step * 0.8
        high_p = max(open_p, close_p) + spread_hl
        low_p = min(open_p, close_p) - spread_hl
        bars.append(
            {
                "time": f"2026-04-{(idx // 24) + 1:02d}T{idx % 24:02d}:00:00Z",
                "open": open_p,
                "high": high_p,
                "low": low_p,
                "close": close_p,
                "volume": 1000,
            }
        )
    return bars


async def test_raw_ohlcv_bars_trigger_market_entry_mismatch() -> None:
    store = create_in_memory_store()
    await seed_strong_trend_state(store, trend="neutral")
    raw_bars = _make_raw_ohlcv_bars(60, start_price=3330.0, step=0.1, spread_hl=1.0)
    await store.save_bars({"account_id": ACCOUNT_ID, "symbol": SYMBOL, "timeframe": "H1", "bars": raw_bars})

    result = await evaluate(
        store,
        trade_plan_=trade_plan(
            {
                "confidence": 80,
                "entry_zone": {"min": 3320.0, "max": 3320.2},
                "stop_loss": 3310.0,
                "take_profit": [3360.0],
            }
        ),
    )
    assert result == {"accepted": False, "reason": "market_entry_mismatch"}


async def test_raw_ohlcv_bars_trigger_inverse_trend_confidence() -> None:
    store = create_in_memory_store()
    await seed_strong_trend_state(store, trend="neutral")
    bear_bars = _make_raw_ohlcv_bars(60, start_price=3420.0, step=-1.4, spread_hl=1.0)
    await store.save_bars({"account_id": ACCOUNT_ID, "symbol": SYMBOL, "timeframe": "H1", "bars": bear_bars})
    await store.save_bars({"account_id": ACCOUNT_ID, "symbol": SYMBOL, "timeframe": "M30", "bars": bear_bars})

    result = await evaluate(
        store,
        trade_plan_=trade_plan(
            {
                "confidence": 66,
                "entry_zone": {"min": 3335.6, "max": 3335.8},
                "stop_loss": 3328.0,
                "take_profit": [3350.0],
            }
        ),
    )
    assert result == {"accepted": False, "reason": "trend.inverse_confidence"}


async def test_market_order_validates_protection_against_live_execution_price() -> None:
    store = create_in_memory_store()
    await seed_strong_trend_state(store, trend="neutral")
    wide_atr_bar: EaRecord = {"close": 3340, "ema20": 3340, "ema50": 3340, "adx": 10, "atr": 20.0, "rsi": 50}
    await store.save_bars({"account_id": ACCOUNT_ID, "symbol": SYMBOL, "timeframe": "H1", "bars": [wide_atr_bar]})
    await store.save_tick(
        {
            "account_id": ACCOUNT_ID,
            "symbol": SYMBOL,
            "bid": 3346.0,
            "ask": 3346.2,
            "spread": 0.2,
            "time": "2026-04-13T07:59:30.000Z",
        }
    )

    result = await evaluate(
        store,
        trade_plan_=trade_plan(
            {
                "side": "sell",
                "confidence": 80,
                "entry_zone": {"min": 3340.0, "max": 3340.2},
                "stop_loss": 3345.0,
                "take_profit": [3310.0],
            }
        ),
    )
    assert result == {"accepted": False, "reason": "protection.invalid_direction"}


async def test_rejects_limit_order_when_market_has_already_reached_tp1() -> None:
    store = create_in_memory_store()
    await seed_strong_trend_state(store, trend="neutral")
    wide_atr_bar: EaRecord = {"close": 3335, "ema20": 3335, "ema50": 3335, "adx": 10, "atr": 20.0, "rsi": 50}
    await store.save_bars({"account_id": ACCOUNT_ID, "symbol": SYMBOL, "timeframe": "H1", "bars": [wide_atr_bar]})

    sell_res = await evaluate(
        store,
        trade_plan_=trade_plan(
            {
                "side": "sell",
                "confidence": 80,
                "execution_type": "limit",
                "requested_order_type": "SELL_LIMIT",
                "entry_zone": {"min": 3345.0, "max": 3346.0},
                "stop_loss": 3355.0,
                "take_profit": [3336.0, 3325.0],
            }
        ),
    )
    assert sell_res == {"accepted": False, "reason": "limit.tp_already_reached"}

    buy_res = await evaluate(
        store,
        trade_plan_=trade_plan(
            {
                "side": "buy",
                "confidence": 80,
                "execution_type": "limit",
                "requested_order_type": "BUY_LIMIT",
                "entry_zone": {"min": 3325.0, "max": 3326.0},
                "stop_loss": 3315.0,
                "take_profit": [3335.0, 3350.0],
            }
        ),
    )
    assert buy_res == {"accepted": False, "reason": "limit.tp_already_reached"}


async def test_rejects_limit_order_when_move_is_mostly_completed() -> None:
    store = create_in_memory_store()
    await seed_strong_trend_state(store, trend="neutral")
    wide_atr: EaRecord = {"close": 3335, "ema20": 3335, "ema50": 3335, "adx": 10, "atr": 20.0}
    await store.save_bars({"account_id": ACCOUNT_ID, "symbol": SYMBOL, "timeframe": "H1", "bars": [wide_atr]})

    # BUY_LIMIT entry=3330, TP1=3339 (distance=9), live ask=3335.7 -> (3335.7-3330)/9 = 63.3% >= 60%
    buy_res = await evaluate(
        store,
        trade_plan_=trade_plan(
            {
                "side": "buy",
                "confidence": 80,
                "execution_type": "limit",
                "requested_order_type": "BUY_LIMIT",
                "entry_zone": {"min": 3330.0, "max": 3330.0},
                "stop_loss": 3320.0,
                "take_profit": [3339.0, 3350.0],
            }
        ),
    )
    assert buy_res == {"accepted": False, "reason": "limit.move_mostly_completed"}

    # SELL_LIMIT entry=3340, TP1=3333 (risk=8 -> rr=0.875 >= 0.75), live bid=3335.5 -> 64.3% >= 60%
    sell_res = await evaluate(
        store,
        trade_plan_=trade_plan(
            {
                "side": "sell",
                "confidence": 80,
                "execution_type": "limit",
                "requested_order_type": "SELL_LIMIT",
                "entry_zone": {"min": 3340.0, "max": 3340.0},
                "stop_loss": 3348.0,
                "take_profit": [3333.0, 3320.0],
            }
        ),
    )
    assert sell_res == {"accepted": False, "reason": "limit.move_mostly_completed"}


async def test_rejects_far_entry_at_2x_h1_atr_and_sl_bandwidth_limits() -> None:
    store = create_in_memory_store()
    await seed_strong_trend_state(store, trend="neutral")  # h1_atr = 2.0

    # Distance = 5.0 > 2.0 * atr (4.0)
    far_res = await evaluate(
        store,
        trade_plan_=trade_plan(
            {
                "execution_type": "limit",
                "requested_order_type": "BUY_LIMIT",
                "entry_zone": {"min": 3330.6, "max": 3330.6},
                "stop_loss": 3327.0,
                "take_profit": [3340.0],
            }
        ),
    )
    assert far_res == {"accepted": False, "reason": "entry.too_far_from_market"}

    # Tight SL = 0.5 < 0.4 * atr (0.8)
    tight_res = await evaluate(
        store,
        trade_plan_=trade_plan(
            {
                "stop_loss": 3335.2,
                "take_profit": [3337.0],
            }
        ),
    )
    assert tight_res == {"accepted": False, "reason": "protection.sl_too_tight"}

    # Wide SL = 8.7 > 3.5 * atr (7.0)
    wide_res = await evaluate(
        store,
        trade_plan_=trade_plan(
            {
                "stop_loss": 3327.0,
                "take_profit": [3350.0],
            }
        ),
    )
    assert wide_res == {"accepted": False, "reason": "protection.sl_too_wide"}


async def test_rejects_stale_or_drifted_market_signal() -> None:
    store = create_in_memory_store()
    await seed_strong_trend_state(store, trend="neutral")

    stale_res = await evaluate(
        store,
        trade_plan_=trade_plan(
            {
                "analysis_started_at": "2026-04-13T07:58:50.000Z",  # 70s before NOW_ISO
                "analysis_start_price": 3335.6,
            }
        ),
    )
    assert stale_res == {"accepted": False, "reason": "signal.stale_latency"}

    drifted_res = await evaluate(
        store,
        trade_plan_=trade_plan(
            {
                "analysis_started_at": "2026-04-13T07:59:45.000Z",  # 15s fresh
                "analysis_start_price": 3334.9,  # |3335.7 - 3334.9| = 0.8 > 0.3 * 2.0 = 0.6
            }
        ),
    )
    assert drifted_res == {"accepted": False, "reason": "signal.price_drifted"}


async def test_rejects_strong_h4_h1_aligned_inverse_trade_regardless_of_confidence() -> None:
    store = create_in_memory_store()
    await seed_strong_trend_state(store, trend="bull")  # H4 & H1 both BULL, adx=35

    result = await evaluate(
        store,
        trade_plan_=trade_plan(
            {
                "side": "sell",
                "confidence": 92,
                "entry_zone": {"min": 3335.4, "max": 3335.6},
                "stop_loss": 3339.0,
                "take_profit": [3328.0],
            }
        ),
    )
    assert result == {"accepted": False, "reason": "trend.h4_h1_strong_inverse"}


async def test_sop_wait_regime_requires_higher_rr() -> None:
    store = create_in_memory_store()
    await seed_strong_trend_state(store, trend="neutral")
    await store.save_watch_ledger(
        ACCOUNT_ID,
        SYMBOL,
        {"htf_anchor": {"intraday_direction": "wait", "macro_bias": "range"}},
    )

    # TP1 R:R = 0.85 < 1.0, TP2 R:R = 1.35 < 1.5 -> rejected under wait regime
    gate_input = {
        "store": store,
        "accountId": ACCOUNT_ID,
        "symbol": SYMBOL,
        "tradePlan": trade_plan(
            {
                "stop_loss": 3333.7,  # risk vs ask 3335.7 = 2.0
                "take_profit": [3337.4, 3338.4],  # rewards = 1.7 (0.85R), 2.7 (1.35R)
            }
        ),
        "nowIso": NOW_ISO,
        "enforceSopLedger": True,
    }
    rejected = await evaluate_ai_approve_pending_gate(gate_input)
    assert rejected == {"accepted": False, "reason": "rr.below_minimum"}

    telemetry: dict[str, Any] = {}
    accepted = await evaluate_ai_approve_pending_gate(
        {
            **gate_input,
            "tradePlan": trade_plan(
                {
                    "stop_loss": 3333.7,
                    "take_profit": [3337.9, 3339.0],  # 1.1R & 1.65R
                }
            ),
            "telemetryOut": telemetry,
        }
    )
    assert accepted["accepted"] is True
    assert telemetry["tp1_rr"] == 1.1
    assert telemetry["tp2_rr"] == 1.65
    assert telemetry["h1_atr"] == 2.0


async def test_fails_closed_when_50_plus_bars_lack_indicator_context() -> None:
    store = create_in_memory_store()
    await seed_strong_trend_state(store, trend="neutral")
    degenerate_bars = [{"open": 0, "high": 0, "low": 0, "close": 0, "volume": 0} for _ in range(55)]
    await store.save_bars({"account_id": ACCOUNT_ID, "symbol": SYMBOL, "timeframe": "H1", "bars": degenerate_bars})

    result = await evaluate(store)
    assert result == {"accepted": False, "reason": "indicator.context_unavailable"}
