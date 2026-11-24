"""AI approve 前置门槛(镜像 gold-bot apps/app-server/src/services/ai-approve/gate.ts)。

逐字移植 TS 语义:ai_symbols 大小写/trim 匹配、tick bid/ask 取价、五点时间框架趋势
加权共识、同向仓位/加仓距离与手数上限校验、pending 去重、每品种每日限额、冷却窗口、
入市距离与最终执行 R:R。store 接口与 EaStore Protocol 完全一致。
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from backend.agents.tools.breakout_confirmation import breakout_entry_rejection, build_breakout_levels
from backend.persistence.records import EaRecord
from backend.persistence.store import EaStore
from backend.services.ai_approve.rules import (
    calc_ai_approve_lots,
    pick_ai_approve_entry_price,
    resolve_ai_approve_executable_take_profits,
    resolve_ai_approve_order_intent,
    validate_ai_approve_protection_direction,
)
from backend.trading_core.indicators import adx as calc_adx
from backend.trading_core.indicators import atr as calc_atr
from backend.trading_core.indicators import ema as calc_ema

__all__ = [
    "AI_APPROVE_COOLDOWN_MS",
    "AIApproveCooldown",
    "AiApproveGate",
    "create_ai_approve_cooldown",
    "create_ai_approve_gate",
    "evaluate_ai_approve_pending_gate",
]

AI_APPROVE_COOLDOWN_MS = 30 * 60 * 1000
AI_APPROVE_MAX_MARKET_SIGNAL_AGE_MS = 60 * 1000
AI_APPROVE_MAX_LIMIT_PROGRESS_RATIO = 0.6
AI_APPROVE_MAX_ENTRY_DISTANCE_ATR = 2.0
AI_APPROVE_MIN_SL_ATR = 0.4
AI_APPROVE_MAX_SL_ATR = 3.5

AIApprovePendingGateInput = dict[str, Any]
AIApprovePendingGateResult = EaRecord


class AIApproveCooldown:
    """镜像 createAIApproveCooldown 闭包:按归一化 symbol 记录最近一次标记时间。"""

    def __init__(self) -> None:
        self._last_by_symbol: dict[str, float] = {}

    def active(self, symbol: str, now_iso: str, ttl_ms: float = AI_APPROVE_COOLDOWN_MS) -> bool:
        previous = self._last_by_symbol.get(_cooldown_key(symbol))
        return previous is not None and _now_millis(now_iso) - previous < ttl_ms

    def mark(self, symbol: str, now_iso: str) -> None:
        self._last_by_symbol[_cooldown_key(symbol)] = _now_millis(now_iso)


def create_ai_approve_cooldown() -> AIApproveCooldown:
    return AIApproveCooldown()


async def evaluate_ai_approve_pending_gate(gate_input: AIApprovePendingGateInput) -> AIApprovePendingGateResult:
    """镜像 evaluateAIApprovePendingGate:完整验收流程,任一关卡失败即返回对应 reason。"""
    store: EaStore = gate_input["store"]
    account_id = str(gate_input["accountId"])
    trade_plan = gate_input["tradePlan"]
    now_iso = str(gate_input["nowIso"])

    registration = await store.get_registration(account_id)
    ai_symbols = _string_array_field(registration, "ai_symbols")
    normalized_symbol = _normalize_symbol_for_match(str(gate_input["symbol"]))
    tradable_symbol: str | None = None
    for symbol in ai_symbols:
        if _normalize_symbol_for_match(symbol) == normalized_symbol:
            tradable_symbol = symbol
            break
    if len(ai_symbols) == 0 or tradable_symbol is None:
        return _reject("account.symbol_not_loaded")

    tick = await store.get_latest_tick(account_id, tradable_symbol)
    tick = {} if tick is None else tick
    current_price = _current_price_from_tick(tick)
    execution_price = _execution_price_from_tick(tick, _string_field(trade_plan, "side"))
    if current_price <= 0:
        return _reject("current_price.missing")

    entry = pick_ai_approve_entry_price(_record_field(trade_plan, "entry_zone"))
    if entry <= 0:
        return _reject("entry_zone.invalid")

    # 手数由 EA 决定(FixedLots/SymbolLotsMap/StrategyLotsMap),服务端不再校验或收敛。
    max_lots = _number_field(trade_plan, "max_lots")
    lots: float = calc_ai_approve_lots(max_lots)

    telemetry_out = gate_input.get("telemetryOut")
    telemetry: dict[str, Any] | None = telemetry_out if isinstance(telemetry_out, dict) else None
    if telemetry is not None:
        telemetry["signal_entry"] = entry
        telemetry["execution_price"] = execution_price
        telemetry["slippage"] = round(abs(execution_price - entry), 6)
        if isinstance(trade_plan.get("inference_latency_ms"), (int, float)):
            telemetry["inference_latency_ms"] = int(trade_plan["inference_latency_ms"])

    h1_bars = await store.get_bars(account_id, tradable_symbol, "H1", limit=300)
    h1_atr = _latest_atr(h1_bars)
    if telemetry is not None:
        telemetry["h1_atr"] = round(h1_atr, 6)
    order_intent = resolve_ai_approve_order_intent(trade_plan, execution_price, entry, h1_atr)
    if not order_intent["accepted"]:
        return _reject(str(order_intent["reason"]))
    if telemetry is not None:
        telemetry["order_type"] = order_intent["orderType"]

    if order_intent["orderType"] == "market":
        started_at = _string_field(trade_plan, "analysis_started_at")
        if started_at:
            age_ms = _now_millis(now_iso) - _now_millis(started_at)
            if age_ms > AI_APPROVE_MAX_MARKET_SIGNAL_AGE_MS:
                return _reject("signal.stale_latency")
        start_price = _number_field(trade_plan, "analysis_start_price")
        if start_price > 0:
            drift = round(abs(execution_price - start_price), 6)
            if telemetry is not None:
                telemetry["analysis_drift"] = drift
            if h1_atr > 0 and drift > h1_atr * 0.3:
                return _reject("signal.price_drifted")

    protection = validate_ai_approve_protection_direction(trade_plan, entry)
    if not protection["accepted"]:
        return _reject(str(protection["reason"]))
    stop_loss_check = _number_field(trade_plan, "stop_loss")
    plan_side = _string_field(trade_plan, "side").strip().lower()
    if order_intent["orderType"] == "market" and stop_loss_check > 0 and (
        (plan_side == "buy" and stop_loss_check >= execution_price)
        or (plan_side == "sell" and stop_loss_check <= execution_price)
    ):
        return _reject("protection.invalid_direction")

    bars = {
        "H4": await store.get_bars(account_id, tradable_symbol, "H4", limit=300),
        "H1": h1_bars,
        "M30": await store.get_bars(account_id, tradable_symbol, "M30", limit=300),
        "M15": await store.get_bars(account_id, tradable_symbol, "M15", limit=300),
    }
    if order_intent["orderType"] == "market":
        reference_price = _number_field(tick, "bid") or current_price
        reason = breakout_entry_rejection(
            build_breakout_levels(bars, reference_price), _string_field(trade_plan, "side"),
            reference_price, entry, market=True,
        )
        if reason:
            return _reject(reason)

    trend = _build_ai_approve_trend_context(bars)
    if len(h1_bars) >= 50 and (h1_atr <= 0 or not bool(trend["hasIndicatorContext"])):
        return _reject("indicator.context_unavailable")

    signal_direction = "BEAR" if _string_field(trade_plan, "side").strip().lower() == "sell" else "BULL"
    if (
        bool(trend.get("h4HasContext"))
        and bool(trend.get("h1HasContext"))
        and trend.get("h4Direction") in ("BULL", "BEAR")
        and trend.get("h4Direction") == trend.get("h1Direction")
        and float(trend.get("h4Adx") or 0.0) >= 25.0
        and float(trend.get("h1Adx") or 0.0) >= 25.0
        and signal_direction != trend.get("h4Direction")
    ):
        return _reject("trend.h4_h1_strong_inverse")

    if bool(trend["hasIndicatorContext"]):
        if (
            trend["consensusDirection"] != "NEUTRAL"
            and trend["consensusDirection"] != signal_direction
            and _number_field(trade_plan, "confidence") < 75
        ):
            return _reject("trend.inverse_confidence")
        # 弱趋势不再按手数减半/拒绝:仓位由 EA 决定,服务端只做方向性拦截。

    sop_wait_regime = False
    if gate_input.get("enforceSopLedger") is True:
        watch_ledger = await store.get_watch_ledger(account_id, tradable_symbol)
        if isinstance(watch_ledger, dict):
            htf_anchor = watch_ledger.get("htf_anchor")
            if isinstance(htf_anchor, dict):
                htf_dir = str(htf_anchor.get("intraday_direction") or "wait").lower()
                if htf_dir == "wait":
                    sop_wait_regime = True
                side_up = _string_field(trade_plan, "side").strip().upper()
                if (htf_dir == "long" and side_up == "SELL") or (htf_dir == "short" and side_up == "BUY"):
                    return _reject("sop.m15_counter_htf_blocked")
                veto = htf_anchor.get("veto_boundary")
                if isinstance(veto, dict):
                    inv_low = veto.get("long_invalid_below")
                    inv_high = veto.get("short_invalid_above")
                    if htf_dir == "long" and isinstance(inv_low, (int, float)) and current_price < float(inv_low):
                        return _reject("sop.veto_boundary_breached")
                    if htf_dir == "short" and isinstance(inv_high, (int, float)) and current_price > float(inv_high):
                        return _reject("sop.veto_boundary_breached")

    positions = await store.get_positions(account_id, tradable_symbol)
    side = _string_field(trade_plan, "side")
    if _has_open_position_on_side(positions, tradable_symbol, side, "ai_signal"):
        if _boolean_field(trade_plan, "add_on") is not True:
            return _reject("position.same_side")
        average_price = _average_entry_price(positions, tradable_symbol, side)
        if average_price <= 0:
            return _reject("position.average_entry_missing")
        m30_atr = _latest_atr(await store.get_bars(account_id, tradable_symbol, "M30", limit=300))
        if m30_atr <= 0:
            return _reject("position.m30_atr_missing")
        add_on_type = _string_field(trade_plan, "add_on_type")
        add_on_level = _number_field(trade_plan, "add_on_level") or 1
        if add_on_type == "adverse":
            spacing_multiplier = 2.0 if add_on_level >= 3 else (1.5 if add_on_level == 2 else 1.0)
        else:
            spacing_multiplier = 1.0
        if abs(entry - average_price) < spacing_multiplier * m30_atr:
            return _reject("position.add_on_distance")

        # 实际下单手数由 EA 配置决定;加仓比例用 max_lots 作为意图上限做服务端校验
        size_for_add_on_limit = lots if lots > 0 else max_lots

        if add_on_type == "favorable":
            existing_lots = _total_lots_on_side(positions, tradable_symbol, side)
            if existing_lots <= 0:
                return _reject("position.favorable_add_no_existing_lots")
            profit_atr = _calculate_profit_atr(positions, tradable_symbol, side, current_price, m30_atr)
            if profit_atr < 1.0:
                return _reject("position.favorable_add_profit_not_enough")
            if size_for_add_on_limit > existing_lots * 0.5:
                return _reject("position.favorable_add_lots_too_large")

        if add_on_type == "adverse":
            existing_lots = _total_lots_on_side(positions, tradable_symbol, side)
            if existing_lots <= 0:
                return _reject("position.adverse_add_no_existing_lots")

            loss_atr = _calculate_loss_atr(positions, tradable_symbol, side, current_price, m30_atr)
            level = add_on_level if 1 <= add_on_level <= 3 else _infer_adverse_level(loss_atr)
            loss_threshold = 3.5 if level >= 3 else (2.0 if level == 2 else 1.0)
            if loss_atr < loss_threshold:
                return _reject("position.adverse_add_loss_not_enough")

            position_states = gate_input.get("positionStates")
            if position_states is None:
                position_states = await store.load_position_states(account_id, tradable_symbol)
            add_on_meta = _latest_adverse_add_on_state(position_states)
            interval_ms = 90 * 60 * 1000 if level >= 3 else (45 * 60 * 1000 if level == 2 else 0)
            if interval_ms > 0 and len(add_on_meta["lastAddOnTime"]) > 0:
                elapsed = _now_millis(now_iso) - _now_millis(add_on_meta["lastAddOnTime"])
                if 0 <= elapsed < interval_ms:
                    return _reject("position.adverse_add_interval_active")

            max_add_count = _number_field(trade_plan, "max_add_count") or 2
            if add_on_meta["addOnCount"] >= max_add_count:
                return _reject("position.adverse_add_count_exceeded")

            if size_for_add_on_limit > existing_lots * 0.6:
                return _reject("position.adverse_add_single_lots_too_large")

            initial_lots = _largest_lots_on_side(positions, tradable_symbol, side)
            if (
                initial_lots > 0
                and add_on_meta["addOnCount"] > 0
                and existing_lots - initial_lots + size_for_add_on_limit > initial_lots * 1.5
            ):
                return _reject("position.adverse_add_cumulative_lots_exceeded")

            max_total_lots = _number_field(trade_plan, "max_total_lots")
            if max_total_lots > 0 and existing_lots + size_for_add_on_limit > max_total_lots:
                return _reject("position.adverse_add_total_lots_exceeded")

            heartbeat = await store.get_heartbeat(account_id)
            heartbeat = {} if heartbeat is None else heartbeat
            balance = _number_field(heartbeat, "balance")
            equity = _number_field(heartbeat, "equity")
            if balance > 0 and equity > 0:
                drawdown_pct = ((balance - equity) / balance) * 100.0
                if drawdown_pct >= 5.0:
                    return _reject("position.adverse_add_account_drawdown_exceeded")

    if await store.has_active_ai_approve_pending(account_id, tradable_symbol, side, now_iso):
        return _reject("pending.duplicate")

    # 每品种每日限额(Phase 4.1)已移除(2026-09-07 老板拍板):配额由 cooldown + 其他关卡把控,
    # 不再按 UTC 日限制该品种 AI 信号条数。保留 pending.duplicate 防同向重复。

    cooldown = gate_input.get("cooldown")
    if cooldown is not None and cooldown.active(tradable_symbol, now_iso, AI_APPROVE_COOLDOWN_MS) is True:
        return _reject("cooldown.active")

    if h1_atr > 0 and abs(current_price - entry) > h1_atr * AI_APPROVE_MAX_ENTRY_DISTANCE_ATR:
        return _reject("entry.too_far_from_market")

    # R:R 下限过滤:市价单以当前执行价(bid/ask)计算,限价单以指定入场价计算,两者不同。
    stop_loss = _number_field(trade_plan, "stop_loss")
    take_profit_values = _array_number_field(trade_plan, "take_profit")
    rr_entry = execution_price if order_intent["orderType"] == "market" else entry
    if h1_atr > 0 and stop_loss > 0:
        sl_dist = abs(rr_entry - stop_loss)
        if sl_dist < h1_atr * AI_APPROVE_MIN_SL_ATR:
            return _reject("protection.sl_too_tight")
        if sl_dist > h1_atr * AI_APPROVE_MAX_SL_ATR:
            return _reject("protection.sl_too_wide")

    rr_input: dict[str, Any] = {
        "side": "buy" if signal_direction == "BULL" else "sell",
        "entry": rr_entry,
        "stopLoss": stop_loss,
        "takeProfitValues": take_profit_values,
    }
    if sop_wait_regime:
        rr_input["minRiskReward"] = 1.5
        rr_input["minTp1RiskReward"] = 1.0
    take_profits = resolve_ai_approve_executable_take_profits(rr_input)
    if not take_profits["accepted"]:
        return _reject(str(take_profits["reason"]))

    targets = take_profits.get("targets") or []
    if telemetry is not None and targets:
        telemetry["tp1_rr"] = round(float(targets[0]["rr"]), 4)
        if len(targets) > 1:
            telemetry["tp2_rr"] = round(float(targets[1]["rr"]), 4)
    if order_intent["orderType"] in ("BUY_LIMIT", "SELL_LIMIT") and targets:
        tp1 = float(targets[0]["value"])
        if (order_intent["orderType"] == "BUY_LIMIT" and execution_price >= tp1) or (
            order_intent["orderType"] == "SELL_LIMIT" and execution_price <= tp1
        ):
            return _reject("limit.tp_already_reached")
        if order_intent["orderType"] == "BUY_LIMIT":
            total_move = tp1 - entry
            if total_move > 0 and (execution_price - entry) / total_move >= AI_APPROVE_MAX_LIMIT_PROGRESS_RATIO:
                return _reject("limit.move_mostly_completed")
        else:
            total_move = entry - tp1
            if total_move > 0 and (entry - execution_price) / total_move >= AI_APPROVE_MAX_LIMIT_PROGRESS_RATIO:
                return _reject("limit.move_mostly_completed")

    return {
        "accepted": True,
        "tradableSymbol": tradable_symbol,
        "currentPrice": current_price,
        "entry": entry,
        "lots": lots,
        "h1Atr": h1_atr,
        "orderType": order_intent["orderType"],
    }


class AiApproveGate:
    """供协调器注入的组合门面;模块函数 evaluate_ai_approve_pending_gate 保持 1:1 语义。"""

    def __init__(
        self,
        store: EaStore,
        metrics: Any = None,
        now_iso: Callable[[], str] | None = None,
        now_unix: Callable[[], int] | None = None,
        now_ms: Callable[[], float] | None = None,
        log: Callable[[str], None] | None = None,
        cooldown: AIApproveCooldown | None = None,
    ) -> None:
        self._store = store
        self._metrics = metrics
        self._now_iso = now_iso or _default_now_iso
        self._now_unix = now_unix or _default_now_unix
        self._now_ms = now_ms or _default_now_ms
        self._log = log if log is not None else (lambda _message: None)
        self._cooldown = cooldown

    async def evaluate(
        self,
        account_id: str,
        symbol: str,
        trade_plan: EaRecord,
        *,
        now_iso: str | None = None,
        cooldown: AIApproveCooldown | None = None,
        position_states: list[EaRecord] | None = None,
    ) -> AIApprovePendingGateResult:
        gate_input: AIApprovePendingGateInput = {
            "store": self._store,
            "accountId": account_id,
            "symbol": symbol,
            "tradePlan": trade_plan,
            "nowIso": now_iso if now_iso is not None else self._now_iso(),
        }
        resolved_cooldown = cooldown if cooldown is not None else self._cooldown
        if resolved_cooldown is not None:
            gate_input["cooldown"] = resolved_cooldown
        if position_states is not None:
            gate_input["positionStates"] = position_states
        result = await evaluate_ai_approve_pending_gate(gate_input)
        if bool(result["accepted"]):
            self._log(f"[AI_APPROVE] gate accepted {account_id}/{symbol}")
        else:
            self._log(f"[AI_APPROVE] gate rejected {account_id}/{symbol} reason={result['reason']}")
        return result


def create_ai_approve_gate(store: EaStore, **options: Any) -> AiApproveGate:
    return AiApproveGate(store, **options)


def _reject(reason: str) -> AIApprovePendingGateResult:
    return {"accepted": False, "reason": reason}


def _current_price_from_tick(tick: EaRecord) -> float:
    bid = _number_field(tick, "bid")
    ask = _number_field(tick, "ask")
    if bid > 0 and ask > 0:
        return (bid + ask) / 2.0
    return ask or bid


def _execution_price_from_tick(tick: EaRecord, side: str) -> float:
    normalized_side = side.strip().lower()
    bid = _number_field(tick, "bid")
    ask = _number_field(tick, "ask")
    if normalized_side == "buy":
        return ask or bid
    if normalized_side == "sell":
        return bid or ask
    return _current_price_from_tick(tick)


def _build_ai_approve_trend_context(bars_by_timeframe: dict[str, list[EaRecord]]) -> dict[str, Any]:
    """日内 H4/H1/M30 加权共识（不含 D1；M15 仅用于突破确认，不参与趋势权重）。"""
    h4 = _bar_direction(bars_by_timeframe.get("H4") or [])
    h1 = _bar_direction(bars_by_timeframe.get("H1") or [])
    m30 = _bar_direction(bars_by_timeframe.get("M30") or [])
    weights: list[dict[str, Any]] = []
    for item, weight in ((h4, 0.25), (h1, 0.35), (m30, 0.35)):
        weighted = {**item, "weight": weight}
        if bool(weighted["hasIndicatorContext"]):
            weights.append(weighted)
    total_weight = sum(float(item["weight"]) for item in weights)
    if total_weight <= 0:
        return {"consensusDirection": "NEUTRAL", "consensusStrength": 0.0, "hasIndicatorContext": False}
    bull_weight = sum(float(item["weight"]) for item in weights if item["direction"] == "BULL")
    bear_weight = sum(float(item["weight"]) for item in weights if item["direction"] == "BEAR")
    if bull_weight > bear_weight:
        consensus_direction = "BULL"
    elif bear_weight > bull_weight:
        consensus_direction = "BEAR"
    else:
        consensus_direction = "NEUTRAL"
    consensus_strength = sum(
        float(item["weight"]) * _trend_confidence(str(item["direction"]), float(item["adx"]))
        for item in weights
    ) / total_weight
    return {
        "consensusDirection": consensus_direction,
        "consensusStrength": consensus_strength,
        "hasIndicatorContext": True,
        "h4Direction": h4["direction"],
        "h4Adx": float(h4["adx"]),
        "h4HasContext": bool(h4["hasIndicatorContext"]),
        "h1Direction": h1["direction"],
        "h1Adx": float(h1["adx"]),
        "h1HasContext": bool(h1["hasIndicatorContext"]),
    }


def _extract_ohlc_series(bars: list[EaRecord]) -> tuple[list[float], list[float], list[float]]:
    """Extract positive (high, low, close) series from bar records."""
    highs: list[float] = []
    lows: list[float] = []
    closes: list[float] = []
    for bar in bars:
        high = _number_field(bar, "high") or _number_field(bar, "High")
        low = _number_field(bar, "low") or _number_field(bar, "Low")
        close = _number_field(bar, "close") or _number_field(bar, "Close")
        if high > 0 and low > 0 and close > 0:
            highs.append(high)
            lows.append(low)
            closes.append(close)
    return highs, lows, closes


def _is_valid_positive(value: float) -> bool:
    return isinstance(value, (int, float)) and value == value and value != float("inf") and value > 0


def _bar_direction(bars: list[EaRecord]) -> dict[str, Any]:
    if len(bars) == 0:
        return {"direction": "NEUTRAL", "adx": 0.0, "hasIndicatorContext": False}
    last = bars[-1]
    ema20 = _number_field(last, "ema20") or _number_field(last, "EMA20")
    ema50 = _number_field(last, "ema50") or _number_field(last, "EMA50")
    close = _number_field(last, "close") or _number_field(last, "Close")
    adx = _number_field(last, "adx") or _number_field(last, "ADX")
    if not (ema20 > 0 and ema50 > 0 and close > 0 and adx > 0):
        highs, lows, closes = _extract_ohlc_series(bars)
        if len(closes) >= 50:
            if not (ema20 > 0):
                ema20_series = calc_ema(closes, 20)
                if ema20_series and _is_valid_positive(ema20_series[-1]):
                    ema20 = float(ema20_series[-1])
            if not (ema50 > 0):
                ema50_series = calc_ema(closes, 50)
                if ema50_series and _is_valid_positive(ema50_series[-1]):
                    ema50 = float(ema50_series[-1])
            if not (adx > 0):
                adx_series = calc_adx(highs, lows, closes, 14)
                if adx_series and _is_valid_positive(adx_series[-1]):
                    adx = float(adx_series[-1])
            if not (close > 0):
                close = closes[-1]
    has_indicator_context = ema20 > 0 and ema50 > 0 and close > 0 and adx > 0
    if not has_indicator_context:
        return {"direction": "NEUTRAL", "adx": adx, "hasIndicatorContext": False}
    if ema20 > ema50 and close > ema20:
        return {"direction": "BULL", "adx": adx, "hasIndicatorContext": True}
    if ema20 < ema50 and close < ema20:
        return {"direction": "BEAR", "adx": adx, "hasIndicatorContext": True}
    return {"direction": "NEUTRAL", "adx": adx, "hasIndicatorContext": True}


def _trend_confidence(direction: str, adx: float) -> float:
    if direction == "NEUTRAL":
        return 0.0
    if adx < 20:
        return 0.3
    if adx <= 30:
        return 0.6
    return 0.9


def _has_open_position_on_side(positions: list[EaRecord], symbol: str, side: str, skip_strategy: str) -> bool:
    want_symbol = symbol.strip().upper()
    want_side = side.strip().upper()
    for position in positions:
        position_symbol = _string_field(position, "symbol")
        if (
            len(want_symbol) > 0
            and len(position_symbol) > 0
            and position_symbol.strip().upper() != want_symbol
        ):
            continue
        strategy = _string_field(position, "strategy")
        if len(skip_strategy) > 0 and len(strategy) > 0 and strategy != skip_strategy:
            continue
        if _string_field(position, "type").strip().upper() == want_side:
            return True
    return False


def _average_entry_price(positions: list[EaRecord], symbol: str, side: str) -> float:
    want_symbol = symbol.strip().upper()
    want_side = side.strip().upper()
    total_lots = 0.0
    weighted_price = 0.0
    for position in positions:
        position_symbol = _string_field(position, "symbol")
        if (
            len(want_symbol) > 0
            and len(position_symbol) > 0
            and position_symbol.strip().upper() != want_symbol
        ):
            continue
        if _string_field(position, "type").strip().upper() != want_side:
            continue
        lots = _number_field(position, "lots")
        open_price = _number_field(position, "open_price") or _number_field(position, "openPrice")
        if lots <= 0 or open_price <= 0:
            continue
        total_lots += lots
        weighted_price += lots * open_price
    return 0.0 if total_lots <= 0 else weighted_price / total_lots


def _latest_atr(bars: list[EaRecord]) -> float:
    if len(bars) == 0:
        return 0.0
    last = bars[-1]
    explicit = _number_field(last, "atr") or _number_field(last, "ATR")
    if explicit > 0:
        return explicit
    highs, lows, closes = _extract_ohlc_series(bars)
    if len(closes) < 14:
        return 0.0
    atr_series = calc_atr(highs, lows, closes, 14)
    if not atr_series:
        return 0.0
    val = float(atr_series[-1])
    return val if _is_valid_positive(val) else 0.0


def _normalize_symbol_for_match(symbol: str) -> str:
    return symbol.strip().upper()


def _cooldown_key(symbol: str) -> str:
    return symbol.strip().upper()


def _now_millis(value: str) -> float:
    millis = _parse_millis(value)
    return millis if millis is not None else time.time() * 1000.0


def _parse_millis(value: str) -> float | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        millis = parsed.timestamp() * 1000.0
        return millis if math.isfinite(millis) else None
    except ValueError:
        return None


def _record_field(record: EaRecord, field: str) -> EaRecord | None:
    value = record.get(field)
    if isinstance(value, dict) and not isinstance(value, list):
        return value
    return None


def _number_field(record: EaRecord, field: str) -> float:
    value = record.get(field)
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return value
    return 0.0


def _string_field(record: EaRecord, field: str) -> str:
    value = record.get(field)
    return value if isinstance(value, str) else ""


def _string_array_field(record: EaRecord | None, field: str) -> list[str]:
    if record is None:
        return []
    value = record.get(field)
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and len(item.strip()) > 0]


def _boolean_field(record: EaRecord, field: str) -> bool:
    return record.get(field) is True


def _array_number_field(record: EaRecord, field: str) -> list[float]:
    value = record.get(field)
    if not isinstance(value, list):
        return []
    return [
        item
        for item in value
        if isinstance(item, (int, float)) and not isinstance(item, bool) and math.isfinite(item)
    ]


def _total_lots_on_side(positions: list[EaRecord], symbol: str, side: str) -> float:
    want_symbol = symbol.strip().upper()
    want_side = side.strip().upper()
    total = 0.0
    for position in positions:
        position_symbol = _string_field(position, "symbol")
        if (
            len(want_symbol) > 0
            and len(position_symbol) > 0
            and position_symbol.strip().upper() != want_symbol
        ):
            continue
        if _string_field(position, "type").strip().upper() != want_side:
            continue
        lots = _number_field(position, "lots")
        if lots > 0:
            total += lots
    return total


def _calculate_profit_atr(
    positions: list[EaRecord],
    symbol: str,
    side: str,
    current_price: float,
    atr: float,
) -> float:
    want_symbol = symbol.strip().upper()
    want_side = side.strip().upper()
    total_lots = 0.0
    weighted_profit = 0.0
    for position in positions:
        position_symbol = _string_field(position, "symbol")
        if (
            len(want_symbol) > 0
            and len(position_symbol) > 0
            and position_symbol.strip().upper() != want_symbol
        ):
            continue
        if _string_field(position, "type").strip().upper() != want_side:
            continue
        lots = _number_field(position, "lots")
        open_price = _number_field(position, "open_price") or _number_field(position, "openPrice")
        if lots <= 0 or open_price <= 0 or current_price <= 0 or atr <= 0:
            continue
        price_diff = current_price - open_price if want_side == "BUY" else open_price - current_price
        total_lots += lots
        weighted_profit += lots * price_diff
    if total_lots <= 0 or atr <= 0:
        return 0.0
    return (weighted_profit / total_lots) / atr


def _calculate_loss_atr(
    positions: list[EaRecord],
    symbol: str,
    side: str,
    current_price: float,
    atr: float,
) -> float:
    want_symbol = symbol.strip().upper()
    want_side = side.strip().upper()
    total_lots = 0.0
    weighted_loss = 0.0
    for position in positions:
        position_symbol = _string_field(position, "symbol")
        if (
            len(want_symbol) > 0
            and len(position_symbol) > 0
            and position_symbol.strip().upper() != want_symbol
        ):
            continue
        if _string_field(position, "type").strip().upper() != want_side:
            continue
        lots = _number_field(position, "lots")
        open_price = _number_field(position, "open_price") or _number_field(position, "openPrice")
        if lots <= 0 or open_price <= 0 or current_price <= 0 or atr <= 0:
            continue
        price_diff = open_price - current_price if want_side == "BUY" else current_price - open_price
        total_lots += lots
        weighted_loss += lots * price_diff
    if total_lots <= 0 or atr <= 0:
        return 0.0
    return (weighted_loss / total_lots) / atr


def _infer_adverse_level(loss_atr: float) -> float:
    if loss_atr >= 3.5:
        return 3.0
    if loss_atr >= 2.0:
        return 2.0
    return 1.0


def _largest_lots_on_side(positions: list[EaRecord], symbol: str, side: str) -> float:
    want_symbol = symbol.strip().upper()
    want_side = side.strip().upper()
    largest = 0.0
    for position in positions:
        position_symbol = _string_field(position, "symbol")
        if (
            len(want_symbol) > 0
            and len(position_symbol) > 0
            and position_symbol.strip().upper() != want_symbol
        ):
            continue
        if _string_field(position, "type").strip().upper() != want_side:
            continue
        lots = _number_field(position, "lots")
        if lots > largest:
            largest = lots
    return largest


def _latest_adverse_add_on_state(position_states: list[EaRecord]) -> dict[str, Any]:
    latest_time = ""
    latest_price = 0.0
    add_on_count = 0.0
    for state in position_states:
        count = _number_field(state, "add_on_count") or 0.0
        if count > add_on_count:
            add_on_count = count
        last_time = state.get("last_add_on_time")
        last_time = last_time if isinstance(last_time, str) else ""
        if len(last_time) > 0 and (len(latest_time) == 0 or last_time > latest_time):
            latest_time = last_time
            latest_price = _number_field(state, "last_add_on_price") or 0.0
    return {"lastAddOnTime": latest_time, "lastAddOnPrice": latest_price, "addOnCount": add_on_count}


def _default_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _default_now_unix() -> int:
    return int(datetime.now(UTC).timestamp())


def _default_now_ms() -> float:
    return time.time() * 1000.0
