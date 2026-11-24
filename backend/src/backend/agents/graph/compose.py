"""Signal composition logic (mirror of apps/app-agent/src/graph/compose.ts).

All branches of decisionIdFor / modeFromState / sideFromArbitration /
takeProfitFromState / reasonCodesFor / buildTradePlan* / composeFinalSignal are
ported 1:1, including the dynamic confidence threshold gating and the
market-first veto rules.

State values may be dicts or the shared agents.types dataclasses; the ``_get``
accessor normalizes both so the composed output matches the TS contract.
"""

from __future__ import annotations

import hashlib
from dataclasses import is_dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast

from backend.agents.graph.state import AnalysisGraphState
from backend.agents.tools.breakout_confirmation import UNCONFIRMED_BREAKOUT, breakout_entry_rejection
from backend.agents.types.agent import (
    AISignalResult,
    DualTradePlan,
    TradePlan,
    TradePlanEntryZone,
)
from backend.agents.types.analysis import ArbitrationResult
from backend.agents.types.trade_action import MarketOrderAction, PendingOrderAction, TradeAction
from backend.trading_core.riskgate.economic_calendar import EVENT_BLACKOUT

TRADE_PLAN_SCHEMA_VERSION: Literal["trade_plan.v1"] = "trade_plan.v1"
TRADE_PLAN_EXPIRY_MS = 15 * 60 * 1000

CRITICAL_BLOCKING = ["market.closed", "market.trade_not_allowed", "tick.missing", "tick.stale", EVENT_BLACKOUT]

TradePlanMode = Literal["observe", "veto", "approve", "modify", "reduce", "close"]
TradePlanSide = Literal["buy", "sell", "dual", "none"]

OrderPlacingAction = PendingOrderAction | MarketOrderAction


# ─── value access helpers ──────────────────────────────────────────────────


def _get(obj: Any, name: str, default: Any = None) -> Any:
    """Read a TS-named field from a dataclass or dict (None-safe)."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _coalesce(value: Any, default: Any) -> Any:
    """Mirror JS ``?? default``: only None falls back."""
    return default if value is None else value


# ─── datetime helpers ──────────────────────────────────────────────────────


def _iso_z(value: datetime) -> str:
    """Format a datetime exactly like JS Date.toISOString()."""
    return value.strftime("%Y-%m-%dT%H:%M:%S") + f".{value.microsecond // 1000:03d}Z"


def _parse_ts(timestamp: str) -> datetime:
    normalized = timestamp[:-1] + "+00:00" if timestamp.endswith("Z") else timestamp
    return datetime.fromisoformat(normalized)


def _now_iso() -> str:
    return _iso_z(datetime.now(UTC))


def _js_number_str(value: Any) -> str:
    """Stringify a number like JS ``String(n)`` (82.0 -> '82', 80.5 -> '80.5')."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


# ─── decision id / mode / side ─────────────────────────────────────────────


def decision_id_for(
    account_id: str,
    symbol: str,
    timestamp: str,
    arbitration: ArbitrationResult,
) -> str:
    """Derive a deterministic decision id (mirror of decisionIdFor)."""
    parts = [
        TRADE_PLAN_SCHEMA_VERSION,
        account_id,
        symbol,
        timestamp,
        _get(arbitration, "final_direction", ""),
        _get(arbitration, "action", ""),
        _js_number_str(_get(arbitration, "confidence", 0)),
    ]
    raw = "|".join(parts)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"tpv1_{digest}"


def market_filter_codes(state: AnalysisGraphState) -> dict[str, list[str]]:
    """Extract blocking / warning filter codes from the primary payload."""
    payload = state.get("payload")
    filters = _get(payload, "market_filters")
    if filters is None:
        return {"blocking": [], "warnings": []}
    blocking = _get(filters, "blocking") or []
    warnings = _get(filters, "warnings") or []
    return {
        "blocking": [code for f in blocking if (code := _get(f, "code"))],
        "warnings": [code for f in warnings if (code := _get(f, "code"))],
    }


def has_blocking_market_filters(state: AnalysisGraphState) -> bool:
    return len(market_filter_codes(state)["blocking"]) > 0


def mode_from_state(state: AnalysisGraphState) -> TradePlanMode:
    """Mirror of modeFromState — veto only for truly market-blocking filters."""
    blocking = market_filter_codes(state)["blocking"]
    if any(code in CRITICAL_BLOCKING for code in blocking):
        return "veto"

    technical = state.get("technicalAnalysis")
    action = _get(state.get("arbitration"), "action")
    exit_suggestion = _get(technical, "recommendation")

    if exit_suggestion == "partial_close":
        return "reduce"
    if exit_suggestion == "close":
        return "close"
    if action == "close":
        return "close"
    if action == "modify":
        return "modify"
    if action == "open":
        return "approve"
    if _get(state.get("arbitration"), "final_direction") == "dual":
        return "approve"
    if _get(state.get("arbitration"), "final_direction") == "hold":
        return "observe"
    return "observe"


def side_from_arbitration(arbitration: ArbitrationResult) -> TradePlanSide:
    """Mirror of sideFromArbitration."""
    direction = _get(arbitration, "final_direction")
    if direction == "buy":
        return "buy"
    if direction == "sell":
        return "sell"
    if direction == "dual":
        return "dual"
    return "none"


def take_profit_from_state(state: AnalysisGraphState, side: TradePlanSide) -> list[float]:
    """Mirror of takeProfitFromState: top-3 support/resistance prices above zero."""
    technical = state.get("technicalAnalysis")
    if technical is None:
        return []
    levels = _get(technical, "support_levels") if side == "sell" else _get(technical, "resistance_levels")
    prices: list[float] = []
    for level in levels or []:
        price = _get(level, "price")
        if isinstance(price, (int, float)) and price > 0:
            prices.append(float(price))
    return prices[:3]


def is_active_mode(mode: TradePlanMode) -> bool:
    return mode not in ("observe", "veto")


def reason_codes_for(
    mode: TradePlanMode,
    side: TradePlanSide,
    state: AnalysisGraphState,
    extra_codes: list[str] | None = None,
) -> list[str]:
    """Mirror of reasonCodesFor."""
    codes = [f"mode.{mode}", f"side.{side}"]
    filters = market_filter_codes(state)
    codes.extend(filters["blocking"])
    codes.extend(filters["warnings"])

    risk = state.get("riskAssessment")
    if _get(risk, "riskLevel"):
        codes.append(f"risk.{_get(risk, 'riskLevel')}")

    wave = state.get("waveAnalysis")
    if _get(wave, "wave_confirmation"):
        codes.append(f"wave.{_get(wave, 'wave_confirmation')}")

    chanlun = state.get("chanlunAnalysis")
    if _get(chanlun, "latest_signal"):
        codes.append(f"chanlun.{_get(chanlun, 'latest_signal')}")

    codes.extend(extra_codes or [])
    return codes


def same_symbol(left: str | None, right: str) -> bool:
    """Mirror of sameSymbol."""
    return left is not None and left.strip().upper() == right.strip().upper()


def is_market_first_state(state: AnalysisGraphState) -> bool:
    return state.get("accountActions") is not None or state.get("marketInsights") is not None


def is_account_aware_open_action(
    state: AnalysisGraphState,
    action: TradeAction | None,
) -> bool:
    """Mirror of isAccountAwareOpenAction."""
    if action is None:
        return False
    return (
        _get(action, "type") in ("place_market_order", "place_pending_order")
        and _get(action, "account_id") == state.get("accountId")
        and same_symbol(_get(action, "symbol"), state.get("symbol", ""))
    )


def _entry_bid_ask(state: AnalysisGraphState) -> tuple[float, float]:
    market = _get(state.get("payload"), "market")
    bid = _coalesce(_get(market, "bid"), 0.0)
    ask = _coalesce(_get(market, "ask"), bid)
    return float(bid), float(ask)


def _analysis_timing_meta(state: AnalysisGraphState) -> tuple[str | None, float | None, int | None]:
    raw_meta = _get(state.get("comprehensiveAnalysis"), "_meta")
    if not isinstance(raw_meta, dict):
        return None, None, None
    started_at = raw_meta.get("analysis_started_at")
    start_price = raw_meta.get("analysis_start_price")
    latency = raw_meta.get("inference_latency_ms")
    return (
        str(started_at) if isinstance(started_at, str) and started_at else None,
        float(start_price) if isinstance(start_price, (int, float)) and not isinstance(start_price, bool) else None,
        int(latency) if isinstance(latency, (int, float)) and not isinstance(latency, bool) else None,
    )


def _expires_at_from(timestamp: str) -> str:
    return _iso_z(_parse_ts(timestamp) + timedelta(milliseconds=TRADE_PLAN_EXPIRY_MS))


# ─── trade plan builders ───────────────────────────────────────────────────


def build_trade_plan_from_trade_action(
    state: AnalysisGraphState,
    action: OrderPlacingAction,
) -> TradePlan | None:
    """Mirror of buildTradePlanFromTradeAction — plan directly from tool call output."""
    arbitration = state.get("arbitration")
    if arbitration is None:
        return None
    if _get(action, "account_id") and _get(action, "account_id") != state.get("accountId"):
        return None
    if _get(action, "symbol") and not same_symbol(_get(action, "symbol"), state.get("symbol", "")):
        return None

    is_market = _get(action, "type") == "place_market_order"
    if not is_market and _get(action, "order_type") == "stop":
        return None

    side: Literal["buy", "sell"] = _get(action, "side")
    execution_type: Literal["market", "limit"] = "market" if is_market else "limit"
    requested_order_type: Literal["market", "BUY_LIMIT", "SELL_LIMIT"] = (
        "market"
        if is_market
        else "BUY_LIMIT"
        if side == "buy"
        else "SELL_LIMIT"
    )

    bid, ask = _entry_bid_ask(state)
    if is_market:
        entry_zone = TradePlanEntryZone(min=bid, max=ask)
    else:
        entry_price = float(_coalesce(_get(action, "entry_price"), 0.0))
        entry_zone = TradePlanEntryZone(min=entry_price, max=entry_price)

    take_profit = [float(_get(action, "take_profit_1", 0.0))]
    if _get(action, "take_profit_2") is not None:
        take_profit.append(float(_get(action, "take_profit_2")))

    if is_market:
        expiry_ms = 15 * 60 * 1000
    else:
        expiry_hours = _get(action, "expiry_hours")
        expiry_ms = int(_coalesce(expiry_hours, 4)) * 3600 * 1000

    timestamp = state.get("timestamp") or _now_iso()
    started_at, start_price, latency_ms = _analysis_timing_meta(state)
    return TradePlan(
        schema_version=TRADE_PLAN_SCHEMA_VERSION,
        decision_id=decision_id_for(state.get("accountId", ""), state.get("symbol", ""), timestamp, arbitration),
        account_id=state.get("accountId", ""),
        symbol=state.get("symbol", ""),
        mode="approve",
        side=side,
        confidence=_coalesce(_get(arbitration, "confidence"), 70),
        entry_zone=entry_zone,
        execution_type=execution_type,
        requested_order_type=requested_order_type,
        stop_loss=float(_coalesce(_get(action, "stop_loss"), 0.0)),
        take_profit=take_profit,
        max_lots=float(_coalesce(_get(action, "lots"), 0.0)),
        expires_at=_iso_z(datetime.now(UTC) + timedelta(milliseconds=expiry_ms)),
        reason_codes=[f"fc.{_get(action, 'type')}", f"side.{side}", f"order.{requested_order_type}"],
        conflicts=[],
        narrative=_get(action, "reason", ""),
        add_on=bool(_get(state.get("riskAssessment"), "addOn", False)),
        analysis_started_at=started_at,
        analysis_start_price=start_price,
        inference_latency_ms=latency_ms,
    )


def _execution_fields(state: AnalysisGraphState, side: TradePlanSide) -> tuple[float, float, float, list[float], float]:
    bid, ask = _entry_bid_ask(state)
    entry_min = min(bid, ask)
    entry_max = max(bid, ask)
    take_profit = take_profit_from_state(state, side)
    risk = state.get("riskAssessment")
    stop_loss = float(_coalesce(_get(risk, "suggestedSL"), 0.0))
    max_lots = float(_coalesce(_get(risk, "maxPositionSize"), 0.0))
    return entry_min, entry_max, stop_loss, take_profit, max_lots


def build_trade_plan(state: AnalysisGraphState, confidence: float) -> TradePlan | None:
    """Mirror of buildTradePlan — legacy single trade plan from state signals."""
    arbitration = state.get("arbitration")
    if arbitration is None:
        return None

    intended_mode = mode_from_state(state)
    intended_side = "none" if intended_mode == "veto" else side_from_arbitration(arbitration)
    filters = market_filter_codes(state)
    timestamp = state.get("timestamp") or _now_iso()
    entry_min, entry_max, stop_loss, take_profit, max_lots = _execution_fields(state, intended_side)

    has_complete_execution_fields = (
        intended_side != "none"
        and entry_min > 0
        and entry_max > 0
        and stop_loss > 0
        and len(take_profit) > 0
        and max_lots > 0
    )
    requires_entry_fields = intended_mode in ("approve", "modify")
    missing_execution_fields = requires_entry_fields and not has_complete_execution_fields
    mode: TradePlanMode = "observe" if missing_execution_fields else intended_mode
    side: TradePlanSide = "none" if missing_execution_fields or mode == "veto" else intended_side
    if missing_execution_fields:
        extra_reason_codes = ["execution.incomplete_fields"]
    elif mode == "approve":
        extra_reason_codes = ["order.market"]
    else:
        extra_reason_codes = []

    if intended_side == "dual":
        return None

    isolated = mode in ("observe", "veto")
    started_at, start_price, latency_ms = _analysis_timing_meta(state)
    return TradePlan(
        schema_version=TRADE_PLAN_SCHEMA_VERSION,
        decision_id=decision_id_for(state.get("accountId", ""), state.get("symbol", ""), timestamp, arbitration),
        account_id=state.get("accountId", ""),
        symbol=state.get("symbol", ""),
        mode=mode,
        side=side,
        confidence=confidence,
        entry_zone=TradePlanEntryZone(min=0.0 if isolated else entry_min, max=0.0 if isolated else entry_max),
        execution_type="market" if mode == "approve" else None,
        requested_order_type="market" if mode == "approve" else None,
        stop_loss=0.0 if isolated else stop_loss,
        take_profit=[] if isolated else take_profit,
        max_lots=0.0 if isolated else max_lots,
        expires_at=_expires_at_from(timestamp),
        reason_codes=reason_codes_for(mode, side, state, extra_reason_codes),
        conflicts=_conflicts_for(missing_execution_fields, mode, filters, arbitration),
        narrative=_get(arbitration, "reasoning", ""),
        add_on=bool(_get(state.get("riskAssessment"), "addOn", False)),
        analysis_started_at=started_at,
        analysis_start_price=start_price,
        inference_latency_ms=latency_ms,
    )


def build_single_trade_plan(
    state: AnalysisGraphState,
    side: Literal["buy", "sell"],
    confidence: float,
) -> TradePlan | None:
    """Mirror of buildSingleTradePlan — per-side plan for dual signals."""
    arbitration = state.get("arbitration")
    if arbitration is None:
        return None

    intended_mode = mode_from_state(state)
    filters = market_filter_codes(state)
    timestamp = state.get("timestamp") or _now_iso()
    entry_min, entry_max, stop_loss, take_profit, max_lots = _execution_fields(state, side)

    has_complete_execution_fields = (
        entry_min > 0
        and entry_max > 0
        and stop_loss > 0
        and len(take_profit) > 0
        and max_lots > 0
    )
    requires_entry_fields = intended_mode in ("approve", "modify")
    missing_execution_fields = requires_entry_fields and not has_complete_execution_fields
    mode: TradePlanMode = "observe" if missing_execution_fields else intended_mode
    if missing_execution_fields:
        extra_reason_codes = ["execution.incomplete_fields"]
    elif mode == "approve":
        extra_reason_codes = ["order.market"]
    else:
        extra_reason_codes = []

    isolated = mode in ("observe", "veto")
    started_at, start_price, latency_ms = _analysis_timing_meta(state)
    return TradePlan(
        schema_version=TRADE_PLAN_SCHEMA_VERSION,
        decision_id=decision_id_for(state.get("accountId", ""), state.get("symbol", ""), timestamp, arbitration),
        account_id=state.get("accountId", ""),
        symbol=state.get("symbol", ""),
        mode=mode,
        side=side,
        confidence=confidence,
        entry_zone=TradePlanEntryZone(min=0.0 if isolated else entry_min, max=0.0 if isolated else entry_max),
        execution_type="market" if mode == "approve" else None,
        requested_order_type="market" if mode == "approve" else None,
        stop_loss=0.0 if isolated else stop_loss,
        take_profit=[] if isolated else take_profit,
        max_lots=0.0 if isolated else max_lots,
        expires_at=_expires_at_from(timestamp),
        reason_codes=reason_codes_for(mode, side, state, extra_reason_codes),
        conflicts=_conflicts_for(missing_execution_fields, mode, filters, arbitration),
        narrative=_get(arbitration, "reasoning", ""),
        add_on=bool(_get(state.get("riskAssessment"), "addOn", False)),
        analysis_started_at=started_at,
        analysis_start_price=start_price,
        inference_latency_ms=latency_ms,
    )


def _conflicts_for(
    missing_execution_fields: bool,
    mode: TradePlanMode,
    filters: dict[str, list[str]],
    arbitration: ArbitrationResult,
) -> list[str]:
    if missing_execution_fields:
        return ["execution.incomplete_fields"]
    if mode == "veto":
        return list(filters["blocking"])
    contradiction = _get(arbitration, "primary_contradiction", "") or ""
    return [] if contradiction == "none" else [contradiction]


def build_dual_trade_plan(
    state: AnalysisGraphState,
    confidence: float,
) -> DualTradePlan | None:
    """Mirror of buildDualTradePlan — buy + sell plans for a dual direction."""
    arbitration = state.get("arbitration")
    if arbitration is None or _get(arbitration, "final_direction") != "dual":
        return None

    buy_plan = build_single_trade_plan(state, "buy", confidence)
    sell_plan = build_single_trade_plan(state, "sell", confidence)
    if buy_plan is None or sell_plan is None:
        return None

    return DualTradePlan(buy=buy_plan, sell=sell_plan, is_dual_direction=True)


# ─── confidence threshold ──────────────────────────────────────────────────


def get_dynamic_confidence_threshold(
    trend_strength: str | None,
    multi_tf_align: bool,
    current_position_pnl: float = 0,
) -> int:
    """Mirror of the inline getDynamicConfidenceThreshold in compose.ts."""
    base_threshold = 58
    if trend_strength == "strong":
        base_threshold -= 8
    elif trend_strength == "weak":
        base_threshold += 6
    if multi_tf_align:
        base_threshold -= 5
    return max(35, min(75, base_threshold))


# ─── composeFinalSignal ────────────────────────────────────────────────────


def compose_final_signal(state: AnalysisGraphState) -> AISignalResult | None:
    """Mirror of composeFinalSignal — builds the top-level AI signal result."""
    technical = state.get("technicalAnalysis")
    wave_analysis = state.get("waveAnalysis")
    chanlun_analysis = state.get("chanlunAnalysis")
    risk_assessment = state.get("riskAssessment")
    arbitration = state.get("arbitration")

    filters = market_filter_codes(state)
    market_blocked = len(filters["blocking"]) > 0

    if arbitration is None:
        return None

    if EVENT_BLACKOUT in filters["blocking"] and _get(arbitration, "action") in ("open", "modify"):
        arbitration = _with_arbitration(arbitration, {
            "action": "hold", "final_direction": "hold",
            "reasoning": f"{_get(arbitration, 'reasoning', '')} | {EVENT_BLACKOUT}",
            "trade_recommendation": None,
        })

    final_direction = _get(arbitration, "final_direction")
    if final_direction == "buy":
        bias: str = "bullish"
    elif final_direction == "sell":
        bias = "bearish"
    else:
        bias = _coalesce(_get(technical, "bias"), "neutral")

    if _get(chanlun_analysis, "latest_signal") == "buy":
        bias = "bullish"
    elif _get(chanlun_analysis, "latest_signal") == "sell":
        bias = "bearish"

    confidence: float = _coalesce(
        _get(arbitration, "confidence"),
        _coalesce(_get(technical, "confidence"), 50),
    )

    if (
        _get(wave_analysis, "trend_strength") == "strong"
        and _get(wave_analysis, "wave_confirmation") == "confirmed"
    ):
        confidence = min(100, confidence + 10)
    if UNCONFIRMED_BREAKOUT in _get(arbitration, "reasoning", "") and _get(arbitration, "action") == "hold":
        confidence = min(confidence, 20)

    dow_theory = _get(arbitration, "dow_theory")
    multi_tf_align = bool(_get(dow_theory, "multi_tf_confirm", False))
    dynamic_threshold = get_dynamic_confidence_threshold(
        _get(wave_analysis, "trend_strength"),
        multi_tf_align,
        0,
    )

    if confidence < dynamic_threshold and _get(arbitration, "action") == "open":
        gated_confidence = min(confidence, dynamic_threshold - 5)
        arbitration = _with_arbitration(
            arbitration,
            {"action": "hold", "confidence": gated_confidence},
        )
        confidence = gated_confidence

    market_first = is_market_first_state(state)
    trade_action = state.get("tradeAction")
    account_trade_action = (
        trade_action if is_account_aware_open_action(state, trade_action) else None
    )
    if market_first and _get(arbitration, "action") == "open" and account_trade_action is None:
        arbitration = _with_arbitration(
            arbitration,
            {
                "action": "hold",
                "final_direction": "hold",
                "reasoning": f"{_get(arbitration, 'reasoning', '')} | account_action_veto",
            },
        )

    if market_first:
        resolved_trade_action = account_trade_action if _get(arbitration, "action") == "open" else None
    else:
        resolved_trade_action = (
            trade_action
            if (
                _get(arbitration, "action") == "open"
                and trade_action is not None
                and _get(trade_action, "type") in ("place_market_order", "place_pending_order")
            )
            else None
        )

    effective_state: AnalysisGraphState = (
        state if arbitration is state.get("arbitration") else _with_state_arbitration(state, arbitration)
    )

    analysis_role = state.get("analysisRole")
    is_non_entry_role = analysis_role in ("htf_anchor", "daily_close_review", "weekly_close_review")

    trade_plan: TradePlan | None
    if is_non_entry_role:
        trade_plan = None
    elif resolved_trade_action is not None:
        trade_plan = build_trade_plan_from_trade_action(
            effective_state,
            cast(OrderPlacingAction, resolved_trade_action),
        )
    elif market_first:
        trade_plan = None
    else:
        trade_plan = build_trade_plan(effective_state, confidence)

    if not is_non_entry_role and market_first and _get(arbitration, "action") == "open" and trade_plan is None:
        reasoning = _get(arbitration, "reasoning", "")
        arbitration = _with_arbitration(
            arbitration,
            {
                "action": "hold",
                "final_direction": "hold",
                "reasoning": reasoning if "account_action_veto" in reasoning else f"{reasoning} | account_action_veto",
            },
        )
        effective_state = _with_state_arbitration(state, arbitration)

    dual_trade_plan: DualTradePlan | None = (
        build_dual_trade_plan(effective_state, confidence)
        if (not is_non_entry_role and not market_first and _get(arbitration, "action") == "open")
        else None
    )

    levels = [
        level
        for level in (_get(state.get("payload"), "breakout_levels") or [])
        if str(_get(level, "timeframe", "") or "").upper() != "D1"
    ]
    bid, ask = _entry_bid_ask(state)
    plans = (dual_trade_plan.buy, dual_trade_plan.sell) if dual_trade_plan is not None else (trade_plan,)
    if not is_non_entry_role and any(
        breakout_entry_rejection(
            levels, plan.side, bid or ask, (plan.entry_zone.min + plan.entry_zone.max) / 2,
            phase=_get(technical, "phase", ""), basis=_get(arbitration, "trigger_basis") or "",
            market=plan.execution_type == "market",
        )
        for plan in plans
        if plan is not None and plan.mode == "approve"
    ):
        confidence = min(confidence, 20)
        arbitration = _with_arbitration(arbitration, {
            "action": "hold", "final_direction": "hold", "confidence": confidence,
            "reasoning": f"{_get(arbitration, 'reasoning', '')} | {UNCONFIRMED_BREAKOUT}",
            "trade_recommendation": None,
        })
        trade_plan = dual_trade_plan = None

    sop_blocks: list[str] = []
    raw_prev_ledger = state.get("previousLedger")
    prev_ledger: dict[str, Any] | None = raw_prev_ledger if isinstance(raw_prev_ledger, dict) else None
    raw_patrol_ctx = state.get("patrolContext")
    patrol_ctx: dict[str, Any] = raw_patrol_ctx if isinstance(raw_patrol_ctx, dict) else {}
    raw_triggers = patrol_ctx.get("trigger_reasons")
    trigger_reasons = [str(r) for r in raw_triggers] if isinstance(raw_triggers, list) else []

    if is_non_entry_role:
        trade_plan = dual_trade_plan = None
    elif prev_ledger is not None:
        raw_anchor = prev_ledger.get("htf_anchor")
        htf_anchor: dict[str, Any] = raw_anchor if isinstance(raw_anchor, dict) else {}
        htf_dir = str(htf_anchor.get("intraday_direction") or "wait").lower()
        arb_dir = str(_get(arbitration, "final_direction") or "hold").lower()
        arb_action = str(_get(arbitration, "action") or "hold").lower()
        proposed_dir = (
            "long" if arb_dir in ("long", "buy", "bullish")
            else ("short" if arb_dir in ("short", "sell", "bearish") else None)
        )
        has_veto_breach = "veto_boundary_breached" in trigger_reasons
        has_reclaim_or_break = any(
            r in trigger_reasons
            for r in (
                "false_breakout_reclaim",
                "false_breakdown_reclaim",
                "key_level_breakout",
                "key_level_breakdown",
                "playbook_invalidated",
            )
        )
        double_evidence = bool(
            patrol_ctx.get("double_evidence")
            or patrol_ctx.get("veto_confirmed")
            or (has_veto_breach and has_reclaim_or_break)
        )

        if (
            htf_dir in ("long", "short")
            and proposed_dir in ("long", "short")
            and proposed_dir != htf_dir
            and arb_action == "open"
            and not double_evidence
        ):
            if analysis_role == "m15_entry":
                sop_blocks.append("sop.m15_counter_htf_blocked")
            sop_blocks.append("sop.reverse_missing_double_evidence")

        # 仅当剧本目标已全部兑现 ("logic_exhausted") 时拦截 M15 追单;
        # 已移除日 ATR 预算耗尽 (range_exhausted) 限制。
        if (
            analysis_role == "m15_entry"
            and arb_action == "open"
            and "logic_exhausted" in trigger_reasons
        ):
            sop_blocks.append("sop.logic_exhausted")

    if sop_blocks:
        confidence = min(confidence, 20)
        arbitration = _with_arbitration(
            arbitration,
            {
                "action": "hold",
                "final_direction": "hold",
                "confidence": confidence,
                "reasoning": f"{_get(arbitration, 'reasoning', '')} | {' | '.join(sop_blocks)}",
                "trade_recommendation": None,
            },
        )
        trade_plan = dual_trade_plan = None

    raw_positions = _get(state.get("payload"), "positions") or []
    has_open_positions = any(
        str(_get(pos, "order_class") or "").strip().lower() != "pending"
        and str(_get(pos, "type") or _get(pos, "direction") or "").strip().upper() in ("BUY", "SELL")
        for pos in raw_positions
    )
    exit_suggestion_val = _coalesce(_get(technical, "recommendation"), "none")
    arb_action_val = str(_get(arbitration, "action") or "").strip().lower()
    plan_mode_val = str(_get(trade_plan, "mode") or "").strip().lower()
    exit_requested = (
        exit_suggestion_val in ("close", "partial_close")
        or arb_action_val == "close"
        or plan_mode_val in ("close", "reduce")
    )
    auto_exit_alert = not is_non_entry_role and exit_requested and confidence >= 60 and has_open_positions

    risk_level = _get(risk_assessment, "riskLevel")
    trade_action_type = _get(trade_action, "type")
    risk_blocking = [code for code in filters["blocking"] if code != EVENT_BLACKOUT]
    if trade_action_type == "place_pending_order":
        risk_alert = (
            any(code in CRITICAL_BLOCKING for code in risk_blocking)
            or risk_level in ("high", "extreme")
            or auto_exit_alert
        )
    else:
        risk_alert = bool(risk_blocking) or risk_level in ("high", "extreme") or auto_exit_alert

    alert_reason_parts: list[str] = []
    if market_blocked:
        alert_reason_parts.append("; ".join(filters["blocking"]))
    warnings = _get(risk_assessment, "warnings")
    if warnings:
        alert_reason_parts.append("; ".join(warnings))
    if sop_blocks:
        alert_reason_parts.append("; ".join(sop_blocks))
    if auto_exit_alert and not alert_reason_parts:
        alert_reason_parts.append("ai_exit_signal")
    alert_reason = "; ".join(alert_reason_parts) or None

    if technical is not None:
        sr_levels: dict[str, list[float]] | None = {
            "support": [
                float(_get(level, "price"))
                for level in _get(technical, "support_levels") or []
                if _get(level, "price") is not None
            ],
            "resistance": [
                float(_get(level, "price"))
                for level in _get(technical, "resistance_levels") or []
                if _get(level, "price") is not None
            ],
        }
    else:
        sr_levels = None

    raw_sop_ctx = _get(state.get("comprehensiveAnalysis"), "sop_context")
    sop_ctx: dict[str, Any] | None = dict(raw_sop_ctx) if isinstance(raw_sop_ctx, dict) else None
    if sop_blocks:
        sop_ctx = {**(sop_ctx or {}), "sop_blocks": list(sop_blocks)}

    return AISignalResult(
        bias=bias,  # type: ignore[arg-type]
        confidence=confidence,
        exit_suggestion=_coalesce(_get(technical, "recommendation"), "none"),
        risk_alert=risk_alert,
        risk_level=risk_level,
        alert_reason=alert_reason,
        suggested_sl=_get(risk_assessment, "suggestedSL"),
        suggested_tp=_get(risk_assessment, "suggestedTP"),
        max_position_size=0 if market_blocked else _get(risk_assessment, "maxPositionSize"),
        indicators_summary=_get(technical, "indicators_summary"),
        sr_levels=sr_levels,
        arbitration={
            "direction": _get(arbitration, "final_direction", ""),
            "action": _get(arbitration, "action", ""),
            "reasoning": _get(arbitration, "reasoning", ""),
            "phase": _get(arbitration, "phase", ""),
            "contradiction": _get(arbitration, "primary_contradiction", ""),
            "united_front": _get(arbitration, "united_front_analysis", ""),
            **({"trigger_basis": basis} if (basis := _get(arbitration, "trigger_basis")) else {}),
        },
        wave_analysis=(
            {
                "confirmation": _get(wave_analysis, "wave_confirmation"),
                "extension_wave": _get(wave_analysis, "extension_wave"),
            }
            if wave_analysis is not None
            else None
        ),
        chanlun_analysis=(
            {
                "trend": _get(chanlun_analysis, "trend"),
                "signal": _get(chanlun_analysis, "latest_signal"),
            }
            if chanlun_analysis is not None
            else None
        ),
        dow_theory=_get(arbitration, "dow_theory"),
        wave_theory=_get(arbitration, "wave_theory"),
        chanlun_theory=_get(arbitration, "chanlun_theory"),
        harmonic_theory=_get(arbitration, "harmonic_theory"),
        trade_recommendation=_get(arbitration, "trade_recommendation"),
        trade_plan=trade_plan,
        dual_trade_plan=dual_trade_plan,
        sop_context=sop_ctx,
    )


def _with_arbitration(
    arbitration: ArbitrationResult,
    updates: dict[str, Any],
) -> ArbitrationResult:
    """Spread-new-dict semantics over a dataclass or dict arbitration."""
    if is_dataclass(arbitration):
        import dataclasses

        return dataclasses.replace(arbitration, **updates)
    return {**arbitration, **updates}


def _with_state_arbitration(
    state: AnalysisGraphState,
    arbitration: ArbitrationResult,
) -> AnalysisGraphState:
    return {**state, "arbitration": arbitration}
