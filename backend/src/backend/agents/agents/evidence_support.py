"""Deterministic, read-only evidence support ledger.

This module intentionally reports bounded support for auditable inputs.  The
score is not a probability, confidence, or trading recommendation.
"""
from __future__ import annotations

import math
from typing import Any

GROUP_CAPS = {
    "trend_structure": 0.30,
    "location": 0.20,
    "momentum": 0.20,
    "volatility_cost": 0.15,
    "execution_trigger": 0.15,
}
GROUP_ORDER = tuple(GROUP_CAPS)
VERSION = "evidence-support-v1"


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _indicators(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    raw = payload.get("indicators")
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for key, value in raw.items():
        if isinstance(value, dict):
            out[str(key).upper()] = value
    return out


def _bars(payload: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    raw = payload.get("bars")
    if not isinstance(raw, dict):
        return {}
    return {
        str(key).upper(): [item for item in value if isinstance(item, dict)]
        for key, value in raw.items()
        if isinstance(value, list)
    }


def _contribution(
    source_id: str,
    group: str,
    direction: str,
    support: float,
    timeframe: str | None,
    status: str,
    reason: str,
) -> dict[str, Any]:
    return {
        "sourceId": source_id,
        "group": group,
        "direction": direction,
        "support": round(max(0.0, min(float(GROUP_CAPS[group]), support)), 6)
        if status == "confirmed"
        else 0.0,
        "timeframe": timeframe,
        "status": status,
        "reason": reason,
    }


def _add(
    groups: dict[str, list[dict[str, Any]]], seen: set[str], contribution: dict[str, Any]
) -> None:
    source_id = contribution["sourceId"]
    if source_id in seen:
        return
    seen.add(source_id)
    groups[contribution["group"]].append(contribution)


def _direction(value: Any) -> str:
    if not isinstance(value, str):
        return "neutral"
    value = value.lower()
    if any(token in value for token in ("bull", "up", "buy", "positive")):
        return "bullish"
    if any(token in value for token in ("bear", "down", "sell", "negative")):
        return "bearish"
    return "neutral"


def compute_evidence_support(payload: dict[str, Any], symbol: str | None = None) -> dict[str, Any]:
    """Build a deterministic ledger from existing numeric payload evidence only."""
    del symbol  # Reserved for future source names; never affects the score.
    indicators = _indicators(payload)
    bars = _bars(payload)
    groups: dict[str, list[dict[str, Any]]] = {name: [] for name in GROUP_ORDER}
    seen: set[str] = set()
    warnings: list[str] = []

    # Structure: minimal wave/Chanlun are explicitly unavailable and never
    # contribute.  Numeric ADX/EMA relationships are safe observations.
    for timeframe in ("H1", "M30", "H4", "M15"):
        item = indicators.get(timeframe, {})
        adx_value = _finite(item.get("adx"))
        ema20 = _finite(item.get("ema20"))
        ema50 = _finite(item.get("ema50"))
        if adx_value is not None and ema20 is not None and ema50 is not None:
            direction = "bullish" if ema20 > ema50 else "bearish" if ema20 < ema50 else "neutral"
            support = min(0.12, max(0.0, adx_value / 100 * 0.12))
            _add(groups, seen, _contribution(
                f"{timeframe}:adx-ema", "trend_structure", direction, support,
                timeframe, "confirmed", "numeric ADX and EMA relationship present",
            ))

    # Location: actual pivot numbers only.  A pivot is neutral evidence here;
    # direction is not invented from proximity alone.
    market = payload.get("market")
    price = _finite(market.get("bid")) if isinstance(market, dict) else None
    if price is None and isinstance(market, dict):
        price = _finite(market.get("ask"))
    for timeframe, item in indicators.items():
        for field in ("pp", "r1", "s1"):
            level = _finite(item.get(field))
            if level is not None and price is not None:
                distance = abs(price - level)
                support = 0.06 if distance == 0 else 0.03 if distance <= max(abs(price) * 0.002, 1e-9) else 0.0
                if support:
                    _add(groups, seen, _contribution(
                        f"{timeframe}:pivot:{field}", "location", "neutral", support,
                        timeframe, "confirmed", "numeric pivot and current price present",
                    ))

    # Momentum: each actual indicator source can contribute once globally.
    for timeframe in ("H1", "M30", "M15", "H4"):
        item = indicators.get(timeframe, {})
        rsi_value = _finite(item.get("rsi"))
        if rsi_value is not None:
            direction = "bullish" if rsi_value >= 55 else "bearish" if rsi_value <= 45 else "neutral"
            _add(groups, seen, _contribution(
                f"{timeframe}:rsi", "momentum", direction, 0.05 if direction != "neutral" else 0.02,
                timeframe, "confirmed", "numeric RSI present",
            ))
        macd_hist = _finite(item.get("macd_hist"))
        if macd_hist is None:
            macd_hist = _finite(item.get("macd_histogram"))
        if macd_hist is not None:
            direction = "bullish" if macd_hist > 0 else "bearish" if macd_hist < 0 else "neutral"
            _add(groups, seen, _contribution(
                f"{timeframe}:macd_hist", "momentum", direction,
                0.05, timeframe, "confirmed", "numeric MACD histogram present",
            ))
        for key in ("rsi_divergence", "macd_divergence"):
            value = item.get(key)
            if isinstance(value, dict) and isinstance(value.get("type"), str):
                _add(groups, seen, _contribution(
                    f"{timeframe}:{key}", "momentum", _direction(value["type"]), 0.06,
                    timeframe, "confirmed", "structured divergence with actual timeframe",
                ))

    # Volatility/cost is deliberately neutral, not a directional signal.
    for timeframe in ("M30", "H1", "M15", "H4"):
        item = indicators.get(timeframe, {})
        atr_value = _finite(item.get("atr"))
        if atr_value is not None and atr_value >= 0:
            _add(groups, seen, _contribution(
                f"{timeframe}:atr", "volatility_cost", "neutral", 0.04,
                timeframe, "confirmed", "numeric ATR present",
            ))
    spread = _finite(market.get("spread")) if isinstance(market, dict) else None
    if spread is not None and spread >= 0:
        _add(groups, seen, _contribution(
            "market:spread", "volatility_cost", "neutral", 0.03,
            None, "confirmed", "numeric spread present",
        ))

    # Execution trigger: only factual closed-bar and market-state evidence.
    for timeframe in ("M30", "H1", "M15", "H4"):
        closed = bars.get(timeframe, [])[:-1]
        if closed and closed[-1].get("time") is not None:
            _add(groups, seen, _contribution(
                f"{timeframe}:closed-bar", "execution_trigger", "neutral", 0.04,
                timeframe, "confirmed", "closed bar has an actual timestamp",
            ))
            break
    market_status = payload.get("market_status")
    if isinstance(market_status, dict) and market_status.get("market_open") is True:
        _add(groups, seen, _contribution(
            "market:open", "execution_trigger", "neutral", 0.04,
            None, "confirmed", "market status explicitly open",
        ))
    if not any(groups.values()):
        warnings.append("no trustworthy computed evidence available")

    result_groups: dict[str, dict[str, Any]] = {}
    total = 0.0
    refs: list[str] = []
    for name in GROUP_ORDER:
        contributions = sorted(groups[name], key=lambda item: item["sourceId"])
        score = round(min(GROUP_CAPS[name], sum(item["support"] for item in contributions)), 6)
        total += score
        refs.extend(item["sourceId"] for item in contributions)
        result_groups[name] = {"score": score, "cap": GROUP_CAPS[name], "contributions": contributions}
    return {
        "version": VERSION,
        "scoreType": "bounded_support_score",
        "total": round(min(1.0, total), 6),
        "groups": result_groups,
        "evidenceRefs": sorted(refs),
        "warnings": warnings,
    }
