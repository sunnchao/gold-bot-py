"""持仓管理(镜像 packages/trading-core/src/positionmgr/manager.ts)。

TP/SL 管理、BE 移动、加仓、追踪止损、汇总等语义逐字移植:
- dict 键保持 TS camelCase(ticket、tp1_hit、be_moved、max_profit_atr 等双键共存)。
- ?? / ||、Math.*、NaN 处理、字段缺失默认值、边界条件与 TS 一致。
- mutations:TS 直接改 dict 的地方照做(状态 dict 以副本方式传递,与 TS spread 一致)。
"""

from __future__ import annotations

import math
import re
from datetime import UTC, datetime
from typing import Any

from backend.trading_core.indicators import ema

PRICE_EPSILON = 1e-6
LOCK_L1_PROFIT_ATR = 2.0
LOCK_L1_OFFSET_ATR = 0.3
LOCK_L2_PROFIT_ATR = 2.5
LOCK_L2_OFFSET_ATR = 0.6

__all__ = [
    "evaluate_position_breakeven",
    "evaluate_position_dynamic_trailing",
    "evaluate_position_key_levels",
    "evaluate_position_manager_commands",
    "evaluate_position_momentum_scalp_exits",
    "evaluate_position_tp1",
    "evaluate_position_tp2",
    "evaluate_position_trend_reversal",
    "evaluate_position_time_stops",
    "resolve_order_class",
    "summarize_positions",
]


# ---------------------------------------------------------------------------
# 基础工具(语义与 TS 对应)
# ---------------------------------------------------------------------------


def _coalesce(mapping: dict[str, Any] | None, *keys: str, default: Any = None) -> Any:
    """镜像 TS 的 ?? :依次取首个非 None 键值,全部缺失/为 None 时返回 default。"""
    if mapping is not None:
        for key in keys:
            if key in mapping and mapping[key] is not None:
                return mapping[key]
    return default


def _js_round(value: float) -> int:
    """镜像 JS Math.round(0.5 向 +inf 取整,负数同样)。"""
    return math.floor(value + 0.5)


def _state_map(states: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    for state in states:
        ticket = state.get("ticket")
        if ticket is not None:
            result[ticket] = state
    return result


def _state_bool(state: dict[str, Any] | None, *keys: str) -> bool:
    """镜像 state?.a ?? state?.b ?? false === true 的判断。"""
    if state is None:
        return False
    for key in keys:
        if state.get(key) is True:
            return True
    return False


def _parse_date(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _date_ms(dt: datetime) -> float:
    return (dt - datetime(1970, 1, 1, tzinfo=UTC)).total_seconds() * 1000.0


def _iso_z(dt: datetime) -> str:
    """镜像 Date.toISOString():毫秒精度 + Z。"""
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _to_snake_case(value: str) -> str:
    return re.sub(r"[A-Z]", lambda m: "_" + m.group(0).lower(), value)


# ---------------------------------------------------------------------------
# 公开导出函数(镜像 manager.ts 的 export function)
# ---------------------------------------------------------------------------


def summarize_positions(input_data: dict[str, Any]) -> dict[str, Any]:
    """汇总市价敞口(镜像 summarizePositions)。"""
    input_symbol = _coalesce(input_data, "symbol", default=None) or ""
    symbol = base_symbol(input_symbol)
    open_positions: list[dict[str, Any]] = []
    for position in input_data.get("positions") or []:
        pos_symbol = _coalesce(position, "symbol", default=None)
        if pos_symbol is None:
            pos_symbol = input_symbol
        if symbol != "" and base_symbol(pos_symbol) != symbol:
            continue
        if resolve_order_class(position) != "market":
            continue
        open_pos = to_open_position(position)
        if open_pos is not None:
            open_positions.append(open_pos)

    buy_lots = 0.0
    sell_lots = 0.0
    buy_weighted_entry_sum = 0.0
    sell_weighted_entry_sum = 0.0
    floating_profit = 0.0
    by_strategy: dict[str, dict[str, Any]] = {}

    for position in open_positions:
        if position["side"] == "BUY":
            buy_lots += position["lots"]
            buy_weighted_entry_sum += position["openPrice"] * position["lots"]
        else:
            sell_lots += position["lots"]
            sell_weighted_entry_sum += position["openPrice"] * position["lots"]
        floating_profit += position["profit"]

        strategy_summary = get_strategy_summary(by_strategy, position["strategy"])
        strategy_summary["positions"] += 1
        if position["side"] == "BUY":
            strategy_summary["buyLots"] += position["lots"]
        else:
            strategy_summary["sellLots"] += position["lots"]
        strategy_summary["floatingProfit"] += position["profit"]

    rounded_buy_lots = round_lots(buy_lots)
    rounded_sell_lots = round_lots(sell_lots)
    net_lots = round_lots(abs(rounded_buy_lots - rounded_sell_lots))
    summary_net_side = net_side(rounded_buy_lots, rounded_sell_lots)

    return {
        "accountId": input_data.get("accountId"),
        "symbol": symbol,
        "totalOpenPositions": len(open_positions),
        "buyLots": rounded_buy_lots,
        "sellLots": rounded_sell_lots,
        "netLots": net_lots,
        "netSide": summary_net_side,
        "weightedAverageEntry": weighted_average_entry(
            summary_net_side, buy_weighted_entry_sum, buy_lots, sell_weighted_entry_sum, sell_lots
        ),
        "floatingProfit": round_money(floating_profit),
        "byStrategy": [
            {
                **summary,
                "buyLots": round_lots(summary["buyLots"]),
                "sellLots": round_lots(summary["sellLots"]),
                "netLots": round_lots(summary["buyLots"] - summary["sellLots"]),
                "floatingProfit": round_money(summary["floatingProfit"]),
            }
            for summary in sorted(by_strategy.values(), key=lambda s: s["strategy"])
        ],
        "canProduceLiveCommands": False,
    }


def evaluate_position_time_stops(input_data: dict[str, Any]) -> dict[str, Any]:
    """持仓时间止损(镜像 evaluatePositionTimeStops)。"""
    result: dict[str, Any] = {"advisories": [], "canProduceLiveCommands": False}
    positions = input_data.get("positions") or []
    h1_bars = input_data.get("h1Bars") or []
    current_atr = input_data.get("currentAtr", 0)
    current_price = input_data.get("currentPrice", 0)
    if len(positions) == 0 or len(h1_bars) < 5 or current_atr <= 0 or current_price <= 0:
        return result

    now_raw = input_data.get("now")
    now = _parse_date(now_raw) if now_raw is not None else datetime.now(UTC)
    states_map = _state_map(input_data.get("states") or [])

    for raw_position in positions:
        position = to_open_position(raw_position)
        if position is None:
            continue
        state = states_map.get(position["ticket"])
        open_time_raw = _coalesce(state, "openTime", "open_time", default=None)
        if open_time_raw is None:
            open_time_raw = now_raw
        open_time = _parse_date(open_time_raw) if open_time_raw is not None else now
        hours = (_date_ms(now) - _date_ms(open_time)) / (60 * 60 * 1000.0)
        profit_atr = profit_in_atr(position, current_price, current_atr)

        advisory = time_stop_advisory(
            position, state, hours, profit_atr, current_atr, _coalesce(input_data, "avgAtr", default=0)
        )
        if advisory is not None:
            result["advisories"].append(advisory)

    return result


def evaluate_position_breakeven(input_data: dict[str, Any]) -> dict[str, Any]:
    """持仓 BE 移动(镜像 evaluatePositionBreakeven)。"""
    result: dict[str, Any] = {"advisories": [], "nextStates": [], "canProduceLiveCommands": False}
    positions = input_data.get("positions") or []
    current_atr = input_data.get("currentAtr", 0)
    current_price = input_data.get("currentPrice", 0)
    if len(positions) == 0 or current_atr <= 0 or current_price <= 0:
        return result

    states_map = _state_map(input_data.get("states") or [])
    for raw_position in positions:
        position = to_open_position(raw_position)
        if position is None:
            continue

        state = breakeven_state(position, states_map.get(position["ticket"]))
        profit_atr = profit_in_atr(position, current_price, current_atr)
        if (
            state.get("beMoved") is not True
            and profit_atr >= _coalesce(state, "beTriggerAtr", default=1.5)
            and validate_new_sl(position["side"], position["openPrice"], position["sl"])
        ):
            state["beMoved"] = True
            state["be_moved"] = True
            state["bestSl"] = position["openPrice"]
            result["advisories"].append(
                {
                    "action": "MODIFY",
                    "ticket": position["ticket"],
                    "newSL": position["openPrice"],
                    "reason": f"breakeven_{format_atr(profit_atr)}ATR",
                }
            )
        result["nextStates"].append(state)

    return result


def evaluate_position_tp1(input_data: dict[str, Any]) -> dict[str, Any]:
    """TP1 部分止盈(镜像 evaluatePositionTP1)。"""
    result: dict[str, Any] = {"advisories": [], "nextStates": [], "canProduceLiveCommands": False}
    positions = input_data.get("positions") or []
    h1_bars = input_data.get("h1Bars") or []
    current_atr = input_data.get("currentAtr", 0)
    current_price = input_data.get("currentPrice", 0)
    if len(positions) == 0 or len(h1_bars) < 5 or current_atr <= 0 or current_price <= 0:
        return result

    states_map = _state_map(input_data.get("states") or [])
    tp1_multi = adaptive_tp1_multi(h1_bars)
    open_positions: list[dict[str, Any]] = []
    pre_tp1_hit: dict[int, bool] = {}
    for raw_position in positions:
        position = to_open_position(raw_position)
        if position is None:
            continue
        open_positions.append(position)

        state = tp1_state(position, states_map.get(position["ticket"]))
        pre_tp1_hit[position["ticket"]] = state.get("tp1Hit") is True
        profit_atr = profit_in_atr(position, current_price, current_atr)
        if (
            state.get("tp1Hit") is not True
            and state.get("beMoved") is True
            and should_take_tp1(position["side"], profit_atr, tp1_multi, h1_bars)
        ):
            close_lots = round_lots(position["lots"] * 0.4)
            if close_lots < 0.01:
                close_lots = position["lots"]
            state["tp1Hit"] = True
            result["advisories"].append(
                {
                    "action": "CLOSE",
                    "ticket": position["ticket"],
                    "lots": close_lots,
                    "reason": f"TP1_{format_atr(profit_atr)}ATR",
                }
            )
        result["nextStates"].append(state)

    apply_same_side_group_close(
        result["advisories"], result["nextStates"], open_positions, pre_tp1_hit, "tp1Hit", "group_tp1"
    )

    return result


def evaluate_position_tp2(input_data: dict[str, Any]) -> dict[str, Any]:
    """TP2 部分止盈(镜像 evaluatePositionTP2)。"""
    result: dict[str, Any] = {"advisories": [], "nextStates": [], "canProduceLiveCommands": False}
    positions = input_data.get("positions") or []
    h1_bars = input_data.get("h1Bars") or []
    current_atr = input_data.get("currentAtr", 0)
    current_price = input_data.get("currentPrice", 0)
    if len(positions) == 0 or len(h1_bars) < 5 or current_atr <= 0 or current_price <= 0:
        return result

    states_map = _state_map(input_data.get("states") or [])
    tp2_multi = adaptive_tp2_multi(h1_bars)
    open_positions: list[dict[str, Any]] = []
    pre_tp2_hit: dict[int, bool] = {}
    for raw_position in positions:
        position = to_open_position(raw_position)
        if position is None:
            continue
        open_positions.append(position)

        state = tp2_state(position, states_map.get(position["ticket"]))
        pre_tp2_hit[position["ticket"]] = state.get("tp2Hit") is True
        profit_atr = profit_in_atr(position, current_price, current_atr)
        if (
            state.get("tp1Hit") is True
            and state.get("tp2Hit") is not True
            and should_take_tp2(position["side"], profit_atr, tp2_multi, h1_bars)
        ):
            close_lots = round_lots(position["lots"] * 0.4)
            if close_lots < 0.01:
                close_lots = position["lots"]
            state["tp2Hit"] = True
            result["advisories"].append(
                {
                    "action": "CLOSE",
                    "ticket": position["ticket"],
                    "lots": close_lots,
                    "reason": f"TP2_{format_atr(profit_atr)}ATR",
                }
            )
        result["nextStates"].append(state)

    apply_same_side_group_close(
        result["advisories"], result["nextStates"], open_positions, pre_tp2_hit, "tp2Hit", "group_tp2"
    )

    return result


def evaluate_position_key_levels(input_data: dict[str, Any]) -> dict[str, Any]:
    """关键价位分批止盈(镜像 evaluatePositionKeyLevels)。"""
    result: dict[str, Any] = {"advisories": [], "nextStates": [], "canProduceLiveCommands": False}
    positions = input_data.get("positions") or []
    h1_bars = input_data.get("h1Bars") or []
    current_atr = input_data.get("currentAtr", 0)
    current_price = input_data.get("currentPrice", 0)
    if len(positions) == 0 or len(h1_bars) < 5 or current_atr <= 0 or current_price <= 0:
        return result

    states_map = _state_map(input_data.get("states") or [])
    open_positions: list[dict[str, Any]] = []
    pre_tp1_hit: dict[int, bool] = {}
    pre_tp2_hit: dict[int, bool] = {}
    for raw_position in positions:
        position = to_open_position(raw_position)
        if position is None:
            continue
        open_positions.append(position)

        state = key_level_state(position, states_map.get(position["ticket"]))
        pre_tp1_hit[position["ticket"]] = state.get("tp1Hit") is True
        pre_tp2_hit[position["ticket"]] = state.get("tp2Hit") is True
        profit_atr = profit_in_atr(position, current_price, current_atr)
        advisory = key_level_advisory(position, state, current_price, current_atr, profit_atr, h1_bars)
        if advisory is not None:
            result["advisories"].append(advisory)
        result["nextStates"].append(state)

    apply_same_side_group_close(
        result["advisories"], result["nextStates"], open_positions, pre_tp1_hit, "tp1Hit", "group_tp1"
    )
    apply_same_side_group_close(
        result["advisories"], result["nextStates"], open_positions, pre_tp2_hit, "tp2Hit", "group_tp2"
    )

    return result


def evaluate_position_trend_reversal(input_data: dict[str, Any]) -> dict[str, Any]:
    """趋势反转离场(镜像 evaluatePositionTrendReversal)。"""
    result: dict[str, Any] = {"advisories": [], "canProduceLiveCommands": False}
    positions = input_data.get("positions") or []
    h1_bars = input_data.get("h1Bars") or []
    current_atr = input_data.get("currentAtr", 0)
    current_price = input_data.get("currentPrice", 0)
    if len(positions) == 0 or len(h1_bars) < 4 or current_atr <= 0 or current_price <= 0:
        return result

    states_map = _state_map(input_data.get("states") or [])
    for raw_position in positions:
        position = to_open_position(raw_position)
        if position is None:
            continue

        profit_atr = profit_in_atr(position, current_price, current_atr)
        advisory = trend_reversal_advisory(
            position, states_map.get(position["ticket"]), current_price, profit_atr, h1_bars
        )
        if advisory is not None:
            result["advisories"].append(advisory)

    return result


def evaluate_position_dynamic_trailing(input_data: dict[str, Any]) -> dict[str, Any]:
    """动态追踪止损(镜像 evaluatePositionDynamicTrailing)。"""
    result: dict[str, Any] = {"advisories": [], "nextStates": [], "canProduceLiveCommands": False}
    positions = input_data.get("positions") or []
    current_atr = input_data.get("currentAtr", 0)
    current_price = input_data.get("currentPrice", 0)
    if len(positions) == 0 or current_atr <= 0 or current_price <= 0:
        return result

    states_map = _state_map(input_data.get("states") or [])
    for raw_position in positions:
        position = to_open_position(raw_position)
        if position is None:
            continue

        profit_atr = profit_in_atr(position, current_price, current_atr)
        state = dynamic_trailing_state(position, states_map.get(position["ticket"]), profit_atr)
        advisory = dynamic_trailing_advisory(position, state, profit_atr)
        if advisory is not None:
            result["advisories"].append(advisory)
        result["nextStates"].append(state)

    return result


def evaluate_position_momentum_scalp_exits(input_data: dict[str, Any]) -> dict[str, Any]:
    """momentum_scalp 离场(已禁用,返回空,镜像 evaluatePositionMomentumScalpExits)。"""
    return {"advisories": [], "nextStates": [], "canProduceLiveCommands": False}


def evaluate_position_manager_commands(input_data: dict[str, Any]) -> dict[str, Any]:
    """统一编排:挂单取消、TP/BE/锁盈/关键位/趋势/追踪、同向分组协调(镜像 evaluatePositionManagerCommands)。"""
    result: dict[str, Any] = {"advisories": [], "nextStates": [], "canProduceLiveCommands": False}
    positions = input_data.get("positions") or []
    h1_bars = input_data.get("h1Bars") or []
    current_atr = input_data.get("currentAtr", 0)
    current_price = input_data.get("currentPrice", 0)
    if len(positions) == 0 or len(h1_bars) < 5 or current_atr <= 0 or current_price <= 0:
        return result

    # 挂单:不参与 TP/trail/BE;现价已到未成交挂单 TP → CANCEL_PENDING
    for raw in positions:
        if resolve_order_class(raw) != "pending":
            continue
        cancel = pending_tp_cancel_advisory(raw, current_price)
        if cancel is not None:
            result["advisories"].append(cancel)

    now_raw = input_data.get("now")
    now = _parse_date(now_raw) if now_raw is not None else datetime.now(UTC)
    input_states = _state_map(input_data.get("states") or [])
    open_positions: list[dict[str, Any]] = []
    for raw in positions:
        if resolve_order_class(raw) != "market":
            continue
        position = to_open_position(raw)
        if position is not None:
            open_positions.append(position)
    state_by_ticket: dict[int, dict[str, Any]] = {}
    pre_tp1_hit: dict[int, bool] = {}
    pre_tp2_hit: dict[int, bool] = {}
    pre_be: dict[int, bool] = {}

    for position in open_positions:
        existing = input_states.get(position["ticket"])
        pre_tp1_hit[position["ticket"]] = _coalesce(existing, "tp1Hit", "tp1_hit", default=False) is True
        pre_tp2_hit[position["ticket"]] = _coalesce(existing, "tp2Hit", "tp2_hit", default=False) is True
        pre_be[position["ticket"]] = _coalesce(existing, "beMoved", "be_moved", default=False) is True

    tp1_multi = adaptive_tp1_multi(h1_bars)
    tp2_multi = adaptive_tp2_multi(h1_bars)

    for position in open_positions:
        state = position_analyze_state(position, input_states.get(position["ticket"]), now)
        update_best_sl_from_position(position, state)

        profit_atr = profit_in_atr(position, current_price, current_atr)
        if profit_atr > _coalesce(state, "maxProfitAtr", default=0):
            state["maxProfitAtr"] = profit_atr

        open_time_raw = _coalesce(state, "openTime", "open_time", default=None)
        open_time = _parse_date(open_time_raw) if isinstance(open_time_raw, str) else now
        hours = (_date_ms(now) - _date_ms(open_time)) / (60 * 60 * 1000.0)
        time_stop = time_stop_advisory(
            position, state, hours, profit_atr, current_atr, _coalesce(input_data, "avgAtr", default=0)
        )
        if time_stop is not None:
            result["advisories"].append(time_stop)
            state_by_ticket[position["ticket"]] = state
            continue

        reset_stale_breakeven(position, state)
        lock_target = profit_lock_target(
            position["side"],
            position["openPrice"],
            current_atr,
            profit_atr,
            _coalesce(state, "beTriggerAtr", default=1.5),
        )
        if (
            lock_target is not None
            and validate_new_sl(position["side"], lock_target["newSL"], position["sl"])
            and is_stop_better_than_current(position["side"], lock_target["newSL"], position["sl"])
        ):
            if is_breakeven_or_better(position["side"], lock_target["newSL"], position["openPrice"]):
                state["beMoved"] = True
                state["be_moved"] = True
            state["bestSl"] = lock_target["newSL"]
            result["advisories"].append(
                {
                    "action": "MODIFY",
                    "ticket": position["ticket"],
                    "newSL": lock_target["newSL"],
                    "reason": lock_target["reason"],
                }
            )

        if (
            state.get("tp1Hit") is not True
            and state.get("beMoved") is True
            and should_take_tp1(position["side"], profit_atr, tp1_multi, h1_bars)
        ):
            close_lots = round_lots(position["lots"] * 0.4)
            if close_lots < 0.01:
                close_lots = position["lots"]
            state["tp1Hit"] = True
            result["advisories"].append(
                {
                    "action": "CLOSE",
                    "ticket": position["ticket"],
                    "lots": close_lots,
                    "reason": f"TP1_{format_atr(profit_atr)}ATR",
                }
            )
            state_by_ticket[position["ticket"]] = state
            continue

        key_level = key_level_advisory(position, state, current_price, current_atr, profit_atr, h1_bars)
        if key_level is not None:
            result["advisories"].append(key_level)
            state_by_ticket[position["ticket"]] = state
            continue

        if (
            state.get("tp1Hit") is True
            and state.get("tp2Hit") is not True
            and should_take_tp2(position["side"], profit_atr, tp2_multi, h1_bars)
        ):
            close_lots = round_lots(position["lots"] * 0.4)
            if close_lots < 0.01:
                close_lots = position["lots"]
            state["tp2Hit"] = True
            result["advisories"].append(
                {
                    "action": "CLOSE",
                    "ticket": position["ticket"],
                    "lots": close_lots,
                    "reason": f"TP2_{format_atr(profit_atr)}ATR",
                }
            )
            state_by_ticket[position["ticket"]] = state
            continue

        trend_reversal = trend_reversal_advisory(position, state, current_price, profit_atr, h1_bars)
        if trend_reversal is not None:
            result["advisories"].append(trend_reversal)
            state_by_ticket[position["ticket"]] = state
            continue

        dynamic_trailing = dynamic_trailing_advisory(position, state, profit_atr)
        if dynamic_trailing is not None:
            result["advisories"].append(dynamic_trailing)

        state_by_ticket[position["ticket"]] = state

    result["nextStates"] = [
        state_by_ticket[position["ticket"]] for position in open_positions if position["ticket"] in state_by_ticket
    ]
    apply_same_side_group_close(
        result["advisories"], result["nextStates"], open_positions, pre_tp1_hit, "tp1Hit", "group_tp1"
    )
    apply_same_side_group_close(
        result["advisories"], result["nextStates"], open_positions, pre_tp2_hit, "tp2Hit", "group_tp2"
    )
    previous_tickets = set(input_states.keys())
    apply_same_side_breakeven(
        result["advisories"], result["nextStates"], open_positions, pre_be, previous_tickets, current_price
    )
    apply_same_side_group_stop_reanchor(
        result["advisories"], result["nextStates"], open_positions, previous_tickets, now, current_price
    )
    apply_adverse_group_drawdown_exit(
        result["advisories"], result["nextStates"], open_positions, _coalesce(input_data, "equity", default=0)
    )

    return result


def resolve_order_class(position: dict[str, Any]) -> str:
    """解析订单类别(镜像 resolveOrderClass)。"""
    explicit = str(_coalesce(position, "order_class", "orderClass", default="") or "").strip().lower()
    if explicit == "pending":
        return "pending"
    if explicit == "market":
        return "market"
    type_value = str(_coalesce(position, "type", default="") or "").strip().upper()
    if type_value in ("BUY", "SELL"):
        return "market"
    if type_value in (
        "BUY_LIMIT",
        "BUY_STOP",
        "SELL_LIMIT",
        "SELL_STOP",
        "BUYLIMIT",
        "BUYSTOP",
        "SELLLIMIT",
        "SELLSTOP",
    ):
        return "pending"
    return "pending"


# ---------------------------------------------------------------------------
# 内部 advisory/state 构造(镜像 manager.ts 内部函数)
# ---------------------------------------------------------------------------


def time_stop_advisory(
    position: dict[str, Any],
    state: dict[str, Any] | None,
    hours: float,
    profit_atr: float,
    current_atr: float,
    avg_atr: float,
) -> dict[str, Any] | None:
    if hours > 72 and not _state_bool(state, "tp2Hit", "tp2_hit"):
        close_lots = round_lots(position["lots"] * 0.5)
        if close_lots <= 0.02:
            close_lots = position["lots"]
        return {
            "action": "CLOSE",
            "ticket": position["ticket"],
            "lots": close_lots,
            "reason": f"time_72h_{format_atr(profit_atr)}ATR",
        }
    if hours > 48 and profit_atr < 0.5:
        return {
            "action": "CLOSE",
            "ticket": position["ticket"],
            "lots": position["lots"],
            "reason": f"time_48h_{format_atr(profit_atr)}ATR",
        }
    if hours > 24 and profit_atr < 0.1 and avg_atr > 0 and current_atr < avg_atr * 0.7:
        return {
            "action": "CLOSE",
            "ticket": position["ticket"],
            "lots": position["lots"],
            "reason": f"time_24h_{format_atr(profit_atr)}ATR_lowvol",
        }
    return None


def trend_reversal_advisory(
    position: dict[str, Any],
    state: dict[str, Any] | None,
    current_price: float,
    profit_atr: float,
    h1_bars: list[Any],
) -> dict[str, Any] | None:
    if not _state_bool(state, "beMoved", "be_moved") or profit_atr < 0.3 or len(h1_bars) < 4:
        return None

    last = h1_bars[-1]
    previous = h1_bars[-2]
    last_macd_hist = numeric_bar_field(last, "macdHist", "MACDHist")
    if last_macd_hist is None:
        last_macd_hist = 0
    previous_macd_hist = numeric_bar_field(previous, "macdHist", "MACDHist")
    if previous_macd_hist is None:
        previous_macd_hist = 0
    last_rsi = numeric_bar_field(last, "rsi", "RSI")
    if last_rsi is None:
        last_rsi = 0
    last_adx = numeric_bar_field(last, "adx", "ADX")
    if last_adx is None:
        last_adx = 0
    last_ema20 = numeric_bar_field(last, "ema20", "EMA20")
    if last_ema20 is None:
        last_ema20 = 0
    previous_ema20 = numeric_bar_field(previous, "ema20", "EMA20")
    if previous_ema20 is None:
        previous_ema20 = 0
    last_ema50 = numeric_bar_field(last, "ema50", "EMA50")
    if last_ema50 is None:
        last_ema50 = 0
    previous_ema50 = numeric_bar_field(previous, "ema50", "EMA50")
    if previous_ema50 is None:
        previous_ema50 = 0
    ema20 = current_price if last_ema20 == 0 else last_ema20

    score = 0
    reasons: list[str] = []

    if position["side"] == "BUY":
        if last_macd_hist < -0.5 and current_price < ema20:
            score += 3
            reasons.append(f"MACD={format_macd(last_macd_hist)}<-0.5且价格<EMA20")
        if last_rsi < 40:
            score += 2
            reasons.append(f"RSI={format_whole(last_rsi)}<40")
        if last_macd_hist < 0 and previous_macd_hist > 0:
            score += 1
            reasons.append("MACD翻负")
        if last_adx < 20:
            score += 1
            reasons.append(f"ADX={format_whole(last_adx)}<20")
        if last_ema20 < last_ema50 and previous_ema20 < previous_ema50:
            score += 2
            reasons.append("EMA死叉确认(2根)")
    else:
        if last_macd_hist > 0.5 and current_price > ema20:
            score += 3
            reasons.append(f"MACD={format_macd(last_macd_hist)}>0.5且价格>EMA20")
        if last_rsi > 60:
            score += 2
            reasons.append(f"RSI={format_whole(last_rsi)}>60")
        if last_macd_hist > 0 and previous_macd_hist < 0:
            score += 1
            reasons.append("MACD翻正")
        if last_adx < 20:
            score += 1
            reasons.append(f"ADX={format_whole(last_adx)}<20")
        if last_ema20 > last_ema50 and previous_ema20 > previous_ema50:
            score += 2
            reasons.append("EMA金叉确认(2根)")

    if score < 4:
        return None

    return {
        "action": "CLOSE",
        "ticket": position["ticket"],
        "lots": position["lots"],
        "reason": f"reversal_s{score}_{' '.join(reasons)}",
    }


def dynamic_trailing_state(
    position: dict[str, Any], existing: dict[str, Any] | None, profit_atr: float
) -> dict[str, Any]:
    state = dict(existing) if existing is not None else {"ticket": position["ticket"]}
    state["ticket"] = position["ticket"]
    state["tp1Hit"] = _coalesce(existing, "tp1Hit", "tp1_hit", default=False)
    state["tp2Hit"] = _coalesce(existing, "tp2Hit", "tp2_hit", default=False)
    state["maxProfitAtr"] = _coalesce(existing, "maxProfitAtr", "max_profit_atr", default=0)
    trailing_closed = _coalesce(existing, "trailingClosed", "trailing_closed", default=False)
    state["trailingClosed"] = trailing_closed
    state["trailing_closed"] = trailing_closed

    if profit_atr > _coalesce(state, "maxProfitAtr", default=0):
        state["maxProfitAtr"] = profit_atr

    return state


def dynamic_trailing_advisory(
    position: dict[str, Any], state: dict[str, Any], profit_atr: float
) -> dict[str, Any] | None:
    max_profit_atr = _coalesce(state, "maxProfitAtr", "max_profit_atr", default=0)
    if state.get("tp1Hit") is not True or max_profit_atr <= 0:
        return None

    # 幂等检查:trail_tp CLOSE 已执行过,不再重复生成
    if state.get("trailingClosed") is True or state.get("trailing_closed") is True:
        return None

    drawdown = max_profit_atr - profit_atr
    if state.get("tp2Hit") is True:
        if drawdown > max_profit_atr * 0.55:
            state["trailingClosed"] = True
            state["trailing_closed"] = True
            return {
                "action": "CLOSE",
                "ticket": position["ticket"],
                "lots": position["lots"],
                "reason": f"trail_tp2_dd{format_atr(drawdown)}",
            }
        return None

    if drawdown > max_profit_atr * 0.6 and profit_atr < max_profit_atr - 0.8:
        state["trailingClosed"] = True
        state["trailing_closed"] = True
        return {
            "action": "CLOSE",
            "ticket": position["ticket"],
            "lots": position["lots"],
            "reason": f"trail_tp1_dd{format_atr(drawdown)}",
        }
    return None


def momentum_scalp_state(position: dict[str, Any], existing: dict[str, Any] | None, now: datetime) -> dict[str, Any]:
    state = dict(existing) if existing is not None else {"ticket": position["ticket"]}
    state["ticket"] = position["ticket"]
    state["openTime"] = _coalesce(existing, "openTime", "open_time", default=_iso_z(now))
    state["rsiTp75Triggered"] = _coalesce(existing, "rsiTp75Triggered", "rsi_tp75_triggered", default=False)
    return state


def momentum_scalp_exit_advisory(
    position: dict[str, Any],
    state: dict[str, Any],
    now: datetime,
    profit_atr: float,
    m5_bars: list[Any],
    m1_bars: list[Any],
) -> dict[str, Any] | None:
    open_time_raw = _coalesce(state, "openTime", "open_time", default=None)
    open_time = _parse_date(open_time_raw) if isinstance(open_time_raw, str) else now
    holding_minutes = (_date_ms(now) - _date_ms(open_time)) / (60 * 1000.0)
    max_holding_minutes = 20
    if holding_minutes > max_holding_minutes and profit_atr < 0.2:
        return {
            "action": "CLOSE",
            "ticket": position["ticket"],
            "lots": position["lots"],
            "reason": "momentum_scalp_time_stop_0.2ATR",
        }

    if len(m5_bars) > 0:
        closes: list[float] = [
            value if value is not None else 0.0
            for value in (numeric_bar_field(bar, "close", "Close") for bar in m5_bars)
        ]
        ema5 = ema(closes, 5)
        ema8 = ema(closes, 8)
        last_index = len(closes) - 1
        if (position["side"] == "BUY" and ema5[last_index] < ema8[last_index]) or (
            position["side"] == "SELL" and ema5[last_index] > ema8[last_index]
        ):
            return {
                "action": "CLOSE",
                "ticket": position["ticket"],
                "lots": position["lots"],
                "reason": "momentum_scalp_m5_structure_break",
            }

    if len(m1_bars) > 0:
        latest_rsi = numeric_bar_field(m1_bars[-1], "rsi", "RSI")
        if latest_rsi is None:
            latest_rsi = 0
        if (position["side"] == "BUY" and latest_rsi > 80) or (position["side"] == "SELL" and latest_rsi < 20):
            return {
                "action": "CLOSE",
                "ticket": position["ticket"],
                "lots": position["lots"],
                "reason": "momentum_scalp_rsi_extreme",
            }
        if state.get("rsiTp75Triggered") is not True and (
            (position["side"] == "BUY" and latest_rsi > 75) or (position["side"] == "SELL" and latest_rsi < 25)
        ):
            close_lots = round_lots(position["lots"] * 0.5)
            if close_lots < 0.01:
                close_lots = position["lots"]
            state["rsiTp75Triggered"] = True
            return {
                "action": "CLOSE",
                "ticket": position["ticket"],
                "lots": close_lots,
                "reason": "momentum_scalp_rsi_tp75",
            }

    return None


def tp1_state(position: dict[str, Any], existing: dict[str, Any] | None) -> dict[str, Any]:
    state = dict(existing) if existing is not None else {"ticket": position["ticket"]}
    state["ticket"] = position["ticket"]
    state["beMoved"] = _coalesce(existing, "beMoved", "be_moved", default=False)
    state["tp1Hit"] = _coalesce(existing, "tp1Hit", "tp1_hit", default=False)
    return state


def tp2_state(position: dict[str, Any], existing: dict[str, Any] | None) -> dict[str, Any]:
    state = dict(existing) if existing is not None else {"ticket": position["ticket"]}
    state["ticket"] = position["ticket"]
    state["tp1Hit"] = _coalesce(existing, "tp1Hit", "tp1_hit", default=False)
    state["tp2Hit"] = _coalesce(existing, "tp2Hit", "tp2_hit", default=False)
    return state


def key_level_state(position: dict[str, Any], existing: dict[str, Any] | None) -> dict[str, Any]:
    state = dict(existing) if existing is not None else {"ticket": position["ticket"]}
    state["ticket"] = position["ticket"]
    state["tp1Hit"] = _coalesce(existing, "tp1Hit", "tp1_hit", default=False)
    state["tp2Hit"] = _coalesce(existing, "tp2Hit", "tp2_hit", default=False)
    return state


def key_level_advisory(
    position: dict[str, Any],
    state: dict[str, Any],
    current_price: float,
    current_atr: float,
    profit_atr: float,
    h1_bars: list[Any],
) -> dict[str, Any] | None:
    if profit_atr < 1.0:
        return None

    key_level = nearest_key_level(current_price, position["side"], h1_bars)
    if abs(current_price - key_level) >= current_atr * 0.2:
        return None

    close_lots = round_lots(position["lots"] * 0.4)
    if close_lots < 0.01:
        close_lots = position["lots"]

    if state.get("tp1Hit") is not True:
        state["tp1Hit"] = True
        return {
            "action": "CLOSE",
            "ticket": position["ticket"],
            "lots": close_lots,
            "reason": f"key_level_{format_level(key_level)}",
        }
    if state.get("tp1Hit") is True and state.get("tp2Hit") is not True and profit_atr > 2.0:
        state["tp2Hit"] = True
        return {
            "action": "CLOSE",
            "ticket": position["ticket"],
            "lots": close_lots,
            "reason": f"key_level2_{format_level(key_level)}",
        }
    return None


def apply_same_side_group_close(
    advisories: list[dict[str, Any]],
    next_states: list[dict[str, Any]],
    positions: list[dict[str, Any]],
    pre_hit: dict[int, bool],
    hit_field: str,
    reason_prefix: str,
) -> None:
    states_by_ticket = _state_map(next_states)
    groups: dict[str, list[dict[str, Any]]] = {}
    for position in positions:
        groups.setdefault(position["side"], []).append(position)

    for side, group in groups.items():
        if len(group) <= 1:
            continue

        any_new_hit = any(
            position["ticket"] in states_by_ticket
            and states_by_ticket[position["ticket"]].get(hit_field) is True
            and pre_hit.get(position["ticket"]) is not True
            for position in group
        )
        if not any_new_hit:
            continue

        for position in group:
            state = states_by_ticket.get(position["ticket"])
            if state is None or state.get(hit_field) is True:
                continue
            close_lots = round_lots(position["lots"] * 0.4)
            if close_lots < 0.01:
                close_lots = position["lots"]
            state[hit_field] = True
            advisories.append(
                {
                    "action": "CLOSE",
                    "ticket": position["ticket"],
                    "lots": close_lots,
                    "reason": f"{reason_prefix}_{side}",
                }
            )


def apply_same_side_breakeven(
    advisories: list[dict[str, Any]],
    next_states: list[dict[str, Any]],
    positions: list[dict[str, Any]],
    pre_be: dict[int, bool],
    previous_tickets: set[int],
    current_price: float,
) -> None:
    states_by_ticket = _state_map(next_states)
    groups: dict[str, list[dict[str, Any]]] = {}
    for position in positions:
        groups.setdefault(position["side"], []).append(position)

    for side, group in groups.items():
        if len(group) <= 1:
            continue

        any_new_be = any(
            position["ticket"] in states_by_ticket
            and states_by_ticket[position["ticket"]].get("beMoved") is True
            and pre_be.get(position["ticket"]) is not True
            for position in group
        )

        new_positions = [p for p in group if p["ticket"] not in previous_tickets]
        old_positions = [p for p in group if p["ticket"] in previous_tickets]
        has_favorable_add_on = False
        if len(new_positions) > 0 and len(old_positions) > 0:
            weighted_sum = sum(p["openPrice"] * p["lots"] for p in old_positions)
            total_lots = sum(p["lots"] for p in old_positions)
            average_price = weighted_sum / total_lots if total_lots > 0 else 0
            if average_price > 0:
                has_favorable_add_on = any(
                    (side == "BUY" and p["openPrice"] > average_price)
                    or (side == "SELL" and p["openPrice"] < average_price)
                    for p in new_positions
                )

        if not any_new_be and not has_favorable_add_on:
            continue

        best_sl = 0
        for position in group:
            if side == "BUY" and position["openPrice"] > best_sl:
                best_sl = position["openPrice"]
            elif side == "SELL" and (best_sl == 0 or position["openPrice"] < best_sl):
                best_sl = position["openPrice"]

        for position in group:
            state = states_by_ticket.get(position["ticket"])
            if state is None:
                continue
            current_best_sl = _coalesce(state, "bestSl", default=0)
            # 同 reanchor:group_be / favorable_addon 的 SL 也必须在市价正确一侧
            stop_valid_vs_price = (
                (side == "BUY" and best_sl < current_price - PRICE_EPSILON)
                or (side == "SELL" and best_sl > current_price + PRICE_EPSILON)
            ) if current_price > 0 else True
            if stop_valid_vs_price and validate_new_sl(side, best_sl, current_best_sl) and best_sl != current_best_sl:
                state["bestSl"] = best_sl
                reason = f"group_favorable_addon_{side}" if has_favorable_add_on else f"group_be_{side}"
                advisories.append(
                    {"action": "MODIFY", "ticket": position["ticket"], "newSL": best_sl, "reason": reason}
                )


def apply_same_side_group_stop_reanchor(
    advisories: list[dict[str, Any]],
    next_states: list[dict[str, Any]],
    positions: list[dict[str, Any]],
    previous_tickets: set[int],
    now: datetime,
    current_price: float,
) -> None:
    states_by_ticket = _state_map(next_states)
    groups: dict[str, list[dict[str, Any]]] = {}
    for position in positions:
        groups.setdefault(position["side"], []).append(position)

    for side, group in groups.items():
        if len(group) <= 1:
            continue

        new_positions = [p for p in group if p["ticket"] not in previous_tickets]
        old_positions = [p for p in group if p["ticket"] in previous_tickets]

        if len(new_positions) == 0 or len(old_positions) == 0:
            continue

        old_weighted_sum = sum(p["openPrice"] * p["lots"] for p in old_positions)
        old_total_lots = sum(p["lots"] for p in old_positions)
        old_average_price = old_weighted_sum / old_total_lots if old_total_lots > 0 else 0

        if old_average_price == 0:
            continue

        has_adverse_add_on = any(
            (side == "BUY" and p["openPrice"] < old_average_price)
            or (side == "SELL" and p["openPrice"] > old_average_price)
            for p in new_positions
        )

        if not has_adverse_add_on:
            continue

        all_weighted_sum = sum(p["openPrice"] * p["lots"] for p in group)
        all_total_lots = sum(p["lots"] for p in group)
        group_avg_entry = all_weighted_sum / all_total_lots if all_total_lots > 0 else 0

        if group_avg_entry == 0:
            continue

        now_iso = _iso_z(now)
        for position in group:
            state = states_by_ticket.get(position["ticket"])
            if state is None:
                continue

            if any(p["ticket"] == position["ticket"] for p in new_positions):
                current_count = _coalesce(state, "addOnCount", "add_on_count", default=0)
                state["addOnCount"] = current_count + 1
                state["lastAddOnTime"] = now_iso
                state["lastAddOnPrice"] = position["openPrice"]
            else:
                if state.get("addOnCount") is None and state.get("add_on_count") is None:
                    state["addOnCount"] = 0

            if _coalesce(state, "groupId", default=None) in (None, ""):
                state["groupId"] = f"{side}_{group[0]['ticket']}"
            state["groupAvgEntry"] = group_avg_entry
            state["groupBestSl"] = group_avg_entry

            current_best_sl = _coalesce(state, "bestSl", default=0)
            # 仅在 groupAvgEntry 相对当前价合法时才发 MODIFY;否则 MT4 会回 130
            # BUY: SL 必须 < 现价;SELL: SL 必须 > 现价。状态仍记录 group 信息供后续使用。
            stop_valid_vs_price = (
                (side == "BUY" and group_avg_entry < current_price - PRICE_EPSILON)
                or (side == "SELL" and group_avg_entry > current_price + PRICE_EPSILON)
            ) if current_price > 0 else True
            if (
                stop_valid_vs_price
                and validate_new_sl(side, group_avg_entry, current_best_sl)
                and group_avg_entry != current_best_sl
            ):
                state["bestSl"] = group_avg_entry
                advisories.append(
                    {
                        "action": "MODIFY",
                        "ticket": position["ticket"],
                        "newSL": group_avg_entry,
                        "reason": f"group_adverse_reanchor_{side}",
                    }
                )


def apply_adverse_group_drawdown_exit(
    advisories: list[dict[str, Any]],
    next_states: list[dict[str, Any]],
    positions: list[dict[str, Any]],
    equity: float,
) -> None:
    if equity <= 0:
        return

    states_by_ticket = _state_map(next_states)
    groups: dict[str, list[dict[str, Any]]] = {}
    for position in positions:
        groups.setdefault(position["side"], []).append(position)

    for group in groups.values():
        if len(group) <= 1:
            continue

        has_adverse_group = any(
            position["ticket"] in states_by_ticket
            and _coalesce(states_by_ticket[position["ticket"]], "addOnCount", "add_on_count", default=0) > 0
            for position in group
        )

        if not has_adverse_group:
            continue

        net_profit = sum(p["profit"] for p in group)
        if net_profit >= 0:
            continue

        net_loss = abs(net_profit)
        drawdown_pct = (net_loss / equity) * 100

        if drawdown_pct >= 6.0:
            for position in group:
                advisories.append(
                    {
                        "action": "CLOSE",
                        "ticket": position["ticket"],
                        "lots": position["lots"],
                        "reason": f"adverse_group_exit_{drawdown_pct:.1f}pct",
                    }
                )


def should_take_tp1(side: str, profit_atr: float, tp1_multi: float, h1_bars: list[Any]) -> bool:
    if profit_atr >= tp1_multi:
        return True
    if profit_atr < tp1_multi * 0.6 or len(h1_bars) < 3:
        return False

    latest_rsi = numeric_bar_field(h1_bars[-1], "rsi", "RSI")
    previous_rsi = numeric_bar_field(h1_bars[-2], "rsi", "RSI")
    if latest_rsi is None or previous_rsi is None:
        return False

    reversal_count = 0
    if side == "BUY":
        if previous_rsi > 65 and latest_rsi < 55:
            reversal_count += 1
        if latest_rsi < previous_rsi:
            reversal_count += 1
    else:
        if previous_rsi < 35 and latest_rsi > 45:
            reversal_count += 1
        if latest_rsi > previous_rsi:
            reversal_count += 1
    return reversal_count >= 2


def should_take_tp2(side: str, profit_atr: float, tp2_multi: float, h1_bars: list[Any]) -> bool:
    if profit_atr >= tp2_multi:
        return True
    if profit_atr < tp2_multi * 0.7 or len(h1_bars) < 3:
        return False

    latest_macd_hist = numeric_bar_field(h1_bars[-1], "macdHist", "MACDHist")
    previous_macd_hist = numeric_bar_field(h1_bars[-2], "macdHist", "MACDHist")
    latest_rsi = numeric_bar_field(h1_bars[-1], "rsi", "RSI")
    previous_rsi = numeric_bar_field(h1_bars[-2], "rsi", "RSI")
    latest_adx = numeric_bar_field(h1_bars[-1], "adx", "ADX")
    previous_adx = numeric_bar_field(h1_bars[-2], "adx", "ADX")

    weakness = 0
    if side == "BUY":
        if latest_macd_hist is not None and previous_macd_hist is not None and latest_macd_hist < previous_macd_hist:
            weakness += 1
        if latest_rsi is not None and previous_rsi is not None and latest_rsi < previous_rsi and latest_rsi < 60:
            weakness += 1
        if latest_adx is not None and previous_adx is not None and latest_adx < previous_adx:
            weakness += 1
    else:
        if latest_macd_hist is not None and previous_macd_hist is not None and latest_macd_hist > previous_macd_hist:
            weakness += 1
        if latest_rsi is not None and previous_rsi is not None and latest_rsi > previous_rsi and latest_rsi > 40:
            weakness += 1
        if latest_adx is not None and previous_adx is not None and latest_adx < previous_adx:
            weakness += 1
    return weakness >= 2


def adaptive_tp1_multi(h1_bars: list[Any]) -> float:
    return adaptive_atr_multis(h1_bars)["tp1Multi"]


def adaptive_tp2_multi(h1_bars: list[Any]) -> float:
    return adaptive_atr_multis(h1_bars)["tp2Multi"]


def adaptive_atr_multis(h1_bars: list[Any]) -> dict[str, float]:
    if len(h1_bars) < 25:
        return {"tp1Multi": 1.5, "tp2Multi": 3.0}

    current_atr = numeric_bar_field(h1_bars[-1], "atr", "ATR")
    if current_atr is None or current_atr <= 0:
        return {"tp1Multi": 1.5, "tp2Multi": 3.0}

    recent_atr_values = [
        value
        for bar in h1_bars[-20:]
        for value in [numeric_bar_field(bar, "atr", "ATR")]
        if value is not None and value > 0
    ]
    if len(recent_atr_values) == 0:
        return {"tp1Multi": 1.5, "tp2Multi": 3.0}

    avg_atr = sum(recent_atr_values) / len(recent_atr_values)
    if avg_atr <= 0:
        return {"tp1Multi": 1.5, "tp2Multi": 3.0}

    ratio = current_atr / avg_atr
    if ratio > 1.3:
        return {"tp1Multi": 2.0, "tp2Multi": 4.0}
    if ratio < 0.7:
        return {"tp1Multi": 1.0, "tp2Multi": 2.0}
    return {"tp1Multi": 1.5, "tp2Multi": 3.0}


def numeric_bar_field(bar: Any, camel_name: str, go_name: str) -> float | None:
    if bar is None or not isinstance(bar, dict):
        return None
    value = bar.get(camel_name)
    if value is None:
        value = bar.get(go_name)
    if value is None:
        value = bar.get(_to_snake_case(camel_name))
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)):
        return value
    return None


def nearest_key_level(price: float, side: str, h1_bars: list[Any]) -> float:
    level_below = float(math.floor(price / 50) * 50)
    level_above = float((math.floor(price / 50) + 1) * 50)

    if len(h1_bars) >= 20:
        recent_high = 0.0
        recent_low = float("inf")
        for bar in h1_bars[-20:]:
            high = numeric_bar_field(bar, "high", "High")
            low = numeric_bar_field(bar, "low", "Low")
            if high is not None and high > recent_high:
                recent_high = high
            if low is not None and low < recent_low:
                recent_low = low

        rounded_high = round_to_nearest(recent_high, 50)
        rounded_low = round_to_nearest(recent_low, 50)
        if side == "BUY" and rounded_high > level_above and abs(price - rounded_high) < abs(price - level_above):
            level_above = rounded_high
        if side == "SELL" and rounded_low < level_below and abs(price - rounded_low) < abs(price - level_below):
            level_below = rounded_low

    return level_above if side == "BUY" else level_below


def format_level(value: float) -> str:
    return f"{value:.0f}"


def breakeven_state(position: dict[str, Any], existing: dict[str, Any] | None) -> dict[str, Any]:
    be_trigger_atr = normalize_be_trigger_atr(_coalesce(existing, "beTriggerAtr", "be_trigger_atr", default=None))
    state = dict(existing) if existing is not None else {"ticket": position["ticket"]}
    state["ticket"] = position["ticket"]
    state["beMoved"] = _coalesce(existing, "beMoved", "be_moved", default=False)
    state["beTriggerAtr"] = be_trigger_atr
    state["bestSl"] = _coalesce(existing, "bestSl", "best_sl", default=0)

    if position["sl"] != 0:
        best_sl = _coalesce(state, "bestSl", default=0)
        if best_sl == 0:
            state["bestSl"] = position["sl"]
        elif position["side"] == "BUY" and position["sl"] > best_sl:
            state["bestSl"] = position["sl"]
        elif position["side"] == "SELL" and position["sl"] < best_sl:
            state["bestSl"] = position["sl"]

    return state


def position_analyze_state(position: dict[str, Any], existing: dict[str, Any] | None, now: datetime) -> dict[str, Any]:
    be_trigger_atr = normalize_be_trigger_atr(_coalesce(existing, "beTriggerAtr", "be_trigger_atr", default=None))
    state = dict(existing) if existing is not None else {"ticket": position["ticket"]}
    state["ticket"] = position["ticket"]
    state["openTime"] = _coalesce(existing, "openTime", "open_time", default=_iso_z(now))
    state["tp1Hit"] = _coalesce(existing, "tp1Hit", "tp1_hit", default=False)
    state["tp2Hit"] = _coalesce(existing, "tp2Hit", "tp2_hit", default=False)
    state["rsiTp75Triggered"] = _coalesce(existing, "rsiTp75Triggered", "rsi_tp75_triggered", default=False)
    state["beMoved"] = _coalesce(existing, "beMoved", "be_moved", default=False)
    state["maxProfitAtr"] = _coalesce(existing, "maxProfitAtr", "max_profit_atr", default=0)
    state["beTriggerAtr"] = be_trigger_atr
    state["bestSl"] = _coalesce(existing, "bestSl", "best_sl", default=position["sl"])
    trailing_closed = _coalesce(existing, "trailingClosed", "trailing_closed", default=False)
    state["trailingClosed"] = trailing_closed
    state["trailing_closed"] = trailing_closed
    return state


def normalize_be_trigger_atr(value: float | None) -> float:
    return 1.5 if value is None or value == 0 else value


def update_best_sl_from_position(position: dict[str, Any], state: dict[str, Any]) -> None:
    if position["sl"] == 0:
        return
    best_sl = _coalesce(state, "bestSl", default=0)
    if best_sl == 0:
        state["bestSl"] = position["sl"]
    elif position["side"] == "BUY" and position["sl"] > best_sl:
        state["bestSl"] = position["sl"]
    elif position["side"] == "SELL" and position["sl"] < best_sl:
        state["bestSl"] = position["sl"]


def reset_stale_breakeven(position: dict[str, Any], state: dict[str, Any]) -> None:
    if (
        (state.get("beMoved") is not True and state.get("be_moved") is not True)
        or position["sl"] <= 0
        or position["openPrice"] <= 0
    ):
        return
    is_stale = (
        position["sl"] < position["openPrice"] - PRICE_EPSILON
        if position["side"] == "BUY"
        else position["sl"] > position["openPrice"] + PRICE_EPSILON
    )
    if is_stale:
        state["beMoved"] = False
        state["be_moved"] = False


def validate_new_sl(side: str, new_sl: float, best_sl: float) -> bool:
    if best_sl == 0:
        return True
    if side == "BUY":
        return new_sl >= best_sl
    return new_sl <= best_sl


def profit_lock_target(
    side: str, open_price: float, current_atr: float, profit_atr: float, be_trigger_atr: float
) -> dict[str, Any] | None:
    if open_price <= 0 or current_atr <= 0 or profit_atr < be_trigger_atr:
        return None
    if profit_atr >= LOCK_L2_PROFIT_ATR:
        return {
            "newSL": (
                open_price + LOCK_L2_OFFSET_ATR * current_atr
                if side == "BUY"
                else open_price - LOCK_L2_OFFSET_ATR * current_atr
            ),
            "reason": f"lock_l2_{format_atr(profit_atr)}ATR",
        }
    if profit_atr >= LOCK_L1_PROFIT_ATR:
        return {
            "newSL": (
                open_price + LOCK_L1_OFFSET_ATR * current_atr
                if side == "BUY"
                else open_price - LOCK_L1_OFFSET_ATR * current_atr
            ),
            "reason": f"lock_l1_{format_atr(profit_atr)}ATR",
        }
    return {
        "newSL": open_price,
        "reason": f"breakeven_{format_atr(profit_atr)}ATR",
    }


def is_stop_better_than_current(side: str, new_sl: float, current_sl: float) -> bool:
    if current_sl == 0:
        return True
    return new_sl > current_sl + PRICE_EPSILON if side == "BUY" else new_sl < current_sl - PRICE_EPSILON


def is_breakeven_or_better(side: str, new_sl: float, open_price: float) -> bool:
    return new_sl >= open_price - PRICE_EPSILON if side == "BUY" else new_sl <= open_price + PRICE_EPSILON


def profit_in_atr(position: dict[str, Any], current_price: float, current_atr: float) -> float:
    profit = (
        current_price - position["openPrice"]
        if position["side"] == "BUY"
        else position["openPrice"] - current_price
    )
    return profit / current_atr


def format_atr(value: float) -> str:
    return f"{value:.1f}"


def format_macd(value: float) -> str:
    return f"{value:.2f}"


def format_whole(value: float) -> str:
    return f"{value:.0f}"


def weighted_average_entry(
    side: str, buy_weighted_entry_sum: float, buy_lots: float, sell_weighted_entry_sum: float, sell_lots: float
) -> float:
    if side == "BUY" and buy_lots > 0:
        return buy_weighted_entry_sum / buy_lots
    if side == "SELL" and sell_lots > 0:
        return sell_weighted_entry_sum / sell_lots
    return 0


def to_open_position(position: dict[str, Any]) -> dict[str, Any] | None:
    # 仅市价仓进入持仓管理;挂单在 evaluate_position_manager_commands 单独处理
    if resolve_order_class(position) != "market":
        return None
    side = position_side(_coalesce(position, "type", default="") or "")
    lots = _coalesce(position, "lots", default=0)
    open_price = _coalesce(position, "openPrice", "open_price", default=0)
    if _coalesce(position, "ticket", default=0) <= 0 or side is None or lots <= 0 or open_price <= 0:
        return None
    strategy = _coalesce(position, "strategy", default=None)
    if strategy is None or len(strategy) == 0:
        strategy = "unknown"
    return {
        "ticket": _coalesce(position, "ticket", default=0),
        "side": side,
        "lots": lots,
        "openPrice": open_price,
        "sl": _coalesce(position, "sl", default=0),
        "profit": _coalesce(position, "profit", default=0),
        "comment": _coalesce(position, "comment", default=""),
        "strategy": strategy,
    }


def pending_tp_cancel_advisory(position: dict[str, Any], current_price: float) -> dict[str, Any] | None:
    ticket = _coalesce(position, "ticket", default=0)
    tp = _coalesce(position, "tp", default=0)
    if ticket <= 0 or tp <= 0 or current_price <= 0:
        return None
    side = pending_side(_coalesce(position, "type", default="") or "")
    if side is None:
        return None
    if side == "BUY" and current_price >= tp:
        return {"action": "CANCEL_PENDING", "ticket": ticket, "reason": f"pending_tp_reached_{tp}"}
    if side == "SELL" and current_price <= tp:
        return {"action": "CANCEL_PENDING", "ticket": ticket, "reason": f"pending_tp_reached_{tp}"}
    return None


def pending_side(type_value: str) -> str | None:
    t = type_value.strip().upper()
    if t in ("BUY", "BUY_LIMIT", "BUY_STOP", "BUYLIMIT", "BUYSTOP"):
        return "BUY"
    if t in ("SELL", "SELL_LIMIT", "SELL_STOP", "SELLLIMIT", "SELLSTOP"):
        return "SELL"
    return None


def is_momentum_scalp_position(position: dict[str, Any]) -> bool:
    # NOTE: momentum_scalp strategy disabled for intraday trading focus
    return False


def get_strategy_summary(summaries: dict[str, dict[str, Any]], strategy: str) -> dict[str, Any]:
    existing = summaries.get(strategy)
    if existing is not None:
        return existing
    created: dict[str, Any] = {
        "strategy": strategy,
        "positions": 0,
        "buyLots": 0,
        "sellLots": 0,
        "netLots": 0,
        "floatingProfit": 0,
    }
    summaries[strategy] = created
    return created


def position_side(value: str) -> str | None:
    t = value.strip().upper()
    if t == "BUY":
        return "BUY"
    if t == "SELL":
        return "SELL"
    return None


def net_side(buy_lots: float, sell_lots: float) -> str:
    if buy_lots > sell_lots:
        return "BUY"
    if sell_lots > buy_lots:
        return "SELL"
    return "FLAT"


def base_symbol(raw: str) -> str:
    symbol = re.sub(r"M#$", "", raw.strip().upper())
    symbol = re.sub(r"#$", "", symbol)
    if symbol in ("GOLD", "XAUUSD"):
        return "XAUUSD"
    return symbol


def round_lots(value: float) -> float:
    return round_to_even(value, 2)


def round_money(value: float) -> float:
    return _js_round((value + 2.220446049250313e-16) * 100) / 100


def round_to_nearest(value: float, nearest: float) -> float:
    return _js_round(value / nearest) * nearest


def round_to_even(value: float, precision: int) -> float:
    factor = 10**precision
    scaled = value * factor
    floor = math.floor(scaled)
    fraction = scaled - floor
    epsilon = 1e-9

    if abs(fraction - 0.5) <= epsilon:
        return (floor if floor % 2 == 0 else floor + 1) / factor
    return _js_round(scaled) / factor
