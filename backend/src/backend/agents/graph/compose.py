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
from backend.agents.types.agent import (
    AISignalResult,
    DualTradePlan,
    TradePlan,
    TradePlanEntryZone,
)
from backend.agents.types.analysis import ArbitrationResult
from backend.agents.types.trade_action import MarketOrderAction, PendingOrderAction, TradeAction

TRADE_PLAN_SCHEMA_VERSION: Literal["trade_plan.v1"] = "trade_plan.v1"
TRADE_PLAN_EXPIRY_MS = 15 * 60 * 1000

CRITICAL_BLOCKING = ["market.closed", "market.trade_not_allowed", "tick.missing", "tick.stale"]

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
    missing_execution_fields = is_active_mode(intended_mode) and not has_complete_execution_fields
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
    missing_execution_fields = is_active_mode(intended_mode) and not has_complete_execution_fields
    mode: TradePlanMode = "observe" if missing_execution_fields else intended_mode
    if missing_execution_fields:
        extra_reason_codes = ["execution.incomplete_fields"]
    elif mode == "approve":
        extra_reason_codes = ["order.market"]
    else:
        extra_reason_codes = []

    isolated = mode in ("observe", "veto")
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

    if resolved_trade_action is not None:
        trade_plan = build_trade_plan_from_trade_action(
            effective_state,
            cast(OrderPlacingAction, resolved_trade_action),
        )
    elif market_first:
        trade_plan = None
    else:
        trade_plan = build_trade_plan(effective_state, confidence)

    if market_first and _get(arbitration, "action") == "open" and trade_plan is None:
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

    dual_trade_plan = (
        build_dual_trade_plan(effective_state, confidence)
        if (not market_first and _get(arbitration, "action") == "open")
        else None
    )

    risk_level = _get(risk_assessment, "riskLevel")
    trade_action_type = _get(trade_action, "type")
    if trade_action_type == "place_pending_order":
        risk_alert = (
            any(code in CRITICAL_BLOCKING for code in filters["blocking"])
            or risk_level in ("high", "extreme")
        )
    else:
        risk_alert = market_blocked or risk_level in ("high", "extreme")

    alert_reason_parts: list[str] = []
    if market_blocked:
        alert_reason_parts.append("; ".join(filters["blocking"]))
    warnings = _get(risk_assessment, "warnings")
    if warnings:
        alert_reason_parts.append("; ".join(warnings))
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
