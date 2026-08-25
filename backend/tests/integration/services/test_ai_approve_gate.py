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


async def test_rejects_plans_once_the_per_symbol_daily_ai_signal_limit_is_reached() -> None:
    store = create_in_memory_store()
    await seed_strong_trend_state(store)
    # 用 SELL 方向占满当日限额,避免先触发 pending.duplicate(BUY 侧无重复)。
    # save_command_candidate 用真实时钟盖 created_at,所以 now_iso 也用真实时钟对齐 UTC 日期。
    real_now_iso = current_timestamp()
    expiration = int(time.time()) + 4 * 60 * 60
    for suffix in ("a", "b"):
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
    assert result == {"accepted": False, "reason": "daily_limit.symbol"}


async def test_rejects_weak_trend_consensus_after_the_go_lots_halving_rule() -> None:
    store = create_in_memory_store()
    await seed_strong_trend_state(store, trend="neutral")

    result = await evaluate(store)
    assert result == {"accepted": False, "reason": "trend.weak_lots_below_min"}


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
