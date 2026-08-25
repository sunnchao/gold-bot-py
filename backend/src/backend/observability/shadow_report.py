"""Cutover 资格审查报告(镜像 packages/observability/src/shadow-report.ts)。"""

from __future__ import annotations

from typing import Any, TypedDict

__all__ = ["build_shadow_report"]


class CutoverCheck(TypedDict):
    label: str
    value: str
    detail: str
    tone: str


class ReplayCoverageSummary(TypedDict):
    total: int
    validated: int


class CutoverReport(TypedDict):
    ready: bool
    protocol_error_rate: float
    signal_drift_rate: float
    command_drift_rate: float
    replay_coverage: float
    last_shadow_event_at: str
    missing_capabilities: list[str]
    checks: list[CutoverCheck]


def build_shadow_report(
    comparisons: list[dict[str, Any]],
    replay_coverage: ReplayCoverageSummary | None = None,
) -> CutoverReport:
    """镜像 buildShadowReport:按影子流量 / oracle 参考 / 漂移率阈值输出 cutover 报告。"""
    last_shadow_event_at = comparisons[-1].get("created_at", "") if comparisons else ""
    replay_coverage_rate = (
        0
        if replay_coverage is None or replay_coverage.get("total", 0) == 0
        else replay_coverage["validated"] / replay_coverage["total"]
    )

    if len(comparisons) == 0:
        return {
            "ready": False,
            "protocol_error_rate": 0,
            "signal_drift_rate": 0,
            "command_drift_rate": 0,
            "replay_coverage": replay_coverage_rate,
            "last_shadow_event_at": last_shadow_event_at,
            "missing_capabilities": (
                ["shadow_traffic"] if replay_coverage is None else ["shadow_traffic", "replay_coverage"]
            ),
            "checks": _build_cutover_checks(
                {
                    "has_shadow_traffic": False,
                    "has_oracle_reference": False,
                    "protocol_error_rate": 0,
                    "signal_drift_rate": 0,
                    "command_drift_rate": 0,
                    "replay_coverage": replay_coverage,
                }
            ),
        }

    compared = [comparison for comparison in comparisons if comparison.get("oracle_compared") is True]
    if len(compared) == 0:
        return {
            "ready": False,
            "protocol_error_rate": 0,
            "signal_drift_rate": 0,
            "command_drift_rate": 0,
            "replay_coverage": replay_coverage_rate,
            "last_shadow_event_at": last_shadow_event_at,
            "missing_capabilities": (
                ["go_oracle_reference"] if replay_coverage is None else ["go_oracle_reference", "replay_coverage"]
            ),
            "checks": _build_cutover_checks(
                {
                    "has_shadow_traffic": True,
                    "has_oracle_reference": False,
                    "protocol_error_rate": 0,
                    "signal_drift_rate": 0,
                    "command_drift_rate": 0,
                    "replay_coverage": replay_coverage,
                }
            ),
        }

    protocol_errors = len([c for c in compared if c.get("protocol_ok") is not True])
    signal_drifts = len([c for c in compared if c.get("signal_drift") is True])
    command_drifts = len([c for c in compared if c.get("command_drift") is True])
    total = len(compared)
    protocol_error_rate = protocol_errors / total
    signal_drift_rate = signal_drifts / total
    command_drift_rate = command_drifts / total

    replay_coverage_missing = replay_coverage is None or replay_coverage_rate < 1
    return {
        "ready": (
            protocol_error_rate == 0
            and signal_drift_rate <= 0.02
            and command_drift_rate <= 0.02
            and not replay_coverage_missing
        ),
        "protocol_error_rate": protocol_error_rate,
        "signal_drift_rate": signal_drift_rate,
        "command_drift_rate": command_drift_rate,
        "replay_coverage": replay_coverage_rate,
        "last_shadow_event_at": last_shadow_event_at,
        "missing_capabilities": ["replay_coverage"] if replay_coverage_missing else [],
        "checks": _build_cutover_checks(
            {
                "has_shadow_traffic": True,
                "has_oracle_reference": True,
                "protocol_error_rate": protocol_error_rate,
                "signal_drift_rate": signal_drift_rate,
                "command_drift_rate": command_drift_rate,
                "replay_coverage": replay_coverage,
            }
        ),
    }


def _build_cutover_checks(inputs: dict[str, Any]) -> list[CutoverCheck]:
    return [
        _build_oracle_replay_check(inputs["has_oracle_reference"], inputs["has_shadow_traffic"]),
        _build_shadow_drift_check(
            inputs["has_shadow_traffic"], inputs["signal_drift_rate"], inputs["command_drift_rate"]
        ),
        _build_protocol_check(inputs["has_shadow_traffic"], inputs["protocol_error_rate"]),
        _build_replay_coverage_check(inputs["replay_coverage"]),
    ]


def _build_replay_coverage_check(coverage: ReplayCoverageSummary | None) -> CutoverCheck:
    if coverage is None:
        return {
            "label": "Replay Coverage",
            "value": "pending",
            "detail": "Replay fixture set has not been scanned yet",
            "tone": "amber",
        }

    if coverage.get("total", 0) == 0:
        return {
            "label": "Replay Coverage",
            "value": "pending",
            "detail": "No replay fixture pairs have been recorded yet",
            "tone": "amber",
        }

    rate = coverage["validated"] / coverage["total"]
    if rate >= 1:
        return {
            "label": "Replay Coverage",
            "value": "100.00%",
            "detail": f"{coverage['validated']}/{coverage['total']} Go fixtures reproduced by Node replay",
            "tone": "green",
        }

    if rate > 0:
        return {
            "label": "Replay Coverage",
            "value": _format_rate(rate),
            "detail": f"{coverage['validated']}/{coverage['total']} Go fixtures reproduced (raw pass requires 100%)",
            "tone": "amber",
        }

    return {
        "label": "Replay Coverage",
        "value": "0.00%",
        "detail": f"{coverage['total']} fixture(s) not yet reproduced by Node replay",
        "tone": "red",
    }


def _build_oracle_replay_check(has_oracle_reference: bool, has_shadow_traffic: bool) -> CutoverCheck:
    if has_oracle_reference:
        return {
            "label": "Oracle Replay",
            "value": "validated",
            "detail": "Go oracle comparisons are flowing into the shadow stream",
            "tone": "green",
        }

    return {
        "label": "Oracle Replay",
        "value": "pending",
        "detail": (
            "Go oracle comparisons have not been approved yet"
            if has_shadow_traffic
            else "No Go oracle comparisons have been recorded yet"
        ),
        "tone": "orange",
    }


def _build_shadow_drift_check(
    has_shadow_traffic: bool, signal_drift_rate: float, command_drift_rate: float
) -> CutoverCheck:
    if not has_shadow_traffic:
        return {
            "label": "Shadow Drift",
            "value": "pending",
            "detail": "Waiting for mirrored production traffic",
            "tone": "orange",
        }

    if signal_drift_rate <= 0.02 and command_drift_rate <= 0.02:
        return {
            "label": "Shadow Drift",
            "value": "within threshold",
            "detail": f"Signal {_format_rate(signal_drift_rate)}, command {_format_rate(command_drift_rate)}",
            "tone": "green",
        }

    return {
        "label": "Shadow Drift",
        "value": "review required",
        "detail": (
            f"Signal {_format_rate(signal_drift_rate)}, command {_format_rate(command_drift_rate)} "
            "(limit 2.00%)"
        ),
        "tone": "red",
    }


def _build_protocol_check(has_shadow_traffic: bool, protocol_error_rate: float) -> CutoverCheck:
    if not has_shadow_traffic:
        return {
            "label": "Protocol Errors",
            "value": _format_rate(protocol_error_rate),
            "detail": "Live shadow traffic has not started yet",
            "tone": "amber",
        }

    if protocol_error_rate > 0:
        return {
            "label": "Protocol Errors",
            "value": _format_rate(protocol_error_rate),
            "detail": "Legacy contract mismatches detected in mirrored traffic",
            "tone": "red",
        }

    return {
        "label": "Protocol Errors",
        "value": _format_rate(protocol_error_rate),
        "detail": "No contract mismatches observed in mirrored traffic",
        "tone": "green",
    }


def _format_rate(rate: float) -> str:
    return f"{rate * 100:.2f}%"
