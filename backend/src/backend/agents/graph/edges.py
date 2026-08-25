"""Conditional edge / routing functions (mirror of apps/app-agent/src/graph/edges.ts)."""

from __future__ import annotations

from typing import Any, Literal

from backend.agents.graph.state import AnalysisGraphState


def _field(obj: Any, name: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _primary_payload(state: AnalysisGraphState) -> Any:
    payload = state.get("payload")
    if payload is not None:
        return payload
    payloads = state.get("payloads")
    if payloads is not None:
        return payloads.get(state.get("symbol", ""))
    return None


def _market_open(payload: Any) -> bool | None:
    if payload is None:
        return None
    market_status = _field(payload, "market_status")
    return _field(market_status, "market_open")


def route_after_fetch(state: AnalysisGraphState) -> Literal["error", "skip", "analyze"]:
    """Route after the fetchData node: error / skip / analyze (mirror of routeAfterFetch)."""
    primary_payload = _primary_payload(state)
    payload_values = list((state.get("payloads") or {}).values())
    errors = list(state.get("errors") or [])

    # If fetching produced errors and no payload, route to error
    if len(errors) > 0 and primary_payload is None:
        return "error"

    # Force mode: skip market-closed check entirely
    if state.get("forceAnalyze"):
        if primary_payload is not None or len(payload_values) > 0:
            return "analyze"

    if len(payload_values) > 0:
        all_closed = all(_market_open(payload) is False for payload in payload_values)
        if all_closed:
            return "skip"
    elif _market_open(primary_payload) is False:
        return "skip"

    # Check for critical errors even with partial payload
    critical_errors = [e for e in errors if e.startswith("fetchData:") and primary_payload is None]
    if len(critical_errors) > 0:
        return "error"

    return "analyze"


def route_after_arbitration(state: AnalysisGraphState) -> Literal["publish", "skip_publish"]:
    """Route after the composeSignal node (mirror of routeAfterArbitration)."""
    final_signal = state.get("finalSignal")
    if final_signal is not None and _field(final_signal, "arbitration") is not None:
        return "publish"

    final_signals = state.get("finalSignals") or {}
    if len(final_signals) > 0:
        has_arbitration = any(
            signal is not None and _field(signal, "arbitration") is not None
            for signal in final_signals.values()
        )
        if has_arbitration:
            return "publish"

    return "skip_publish"
