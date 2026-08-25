"""影子校验服务集成测试(1:1 镜像 apps/app-server/src/services/shadow/service.spec.ts)。"""

from __future__ import annotations

from backend.persistence.store import create_in_memory_store
from backend.services.shadow.index import ShadowService


async def test_returns_placeholder_metrics_when_no_shadow_comparisons_exist() -> None:
    service = ShadowService(create_in_memory_store(), lambda: "2026-07-03T00:00:00.000Z")

    assert await service.metrics() == {
        "status": "OK",
        "generated_at": "2026-07-03T00:00:00.000Z",
        "report": {
            "ready": False,
            "protocol_error_rate": 0,
            "signal_drift_rate": 0,
            "command_drift_rate": 0,
            "replay_coverage": 0,
            "last_shadow_event_at": "",
            "missing_capabilities": ["shadow_traffic"],
            "checks": [
                {
                    "label": "Oracle Replay",
                    "value": "pending",
                    "detail": "No Go oracle comparisons have been recorded yet",
                    "tone": "orange",
                },
                {
                    "label": "Shadow Drift",
                    "value": "pending",
                    "detail": "Waiting for mirrored production traffic",
                    "tone": "orange",
                },
                {
                    "label": "Protocol Errors",
                    "value": "0.00%",
                    "detail": "Live shadow traffic has not started yet",
                    "tone": "amber",
                },
                {
                    "label": "Replay Coverage",
                    "value": "pending",
                    "detail": "Replay fixture set has not been scanned yet",
                    "tone": "amber",
                },
            ],
        },
        "totals": {
            "comparisons": 0,
            "protocol_errors": 0,
            "signal_drifts": 0,
            "command_drifts": 0,
        },
    }


async def test_aggregates_persisted_shadow_comparison_metrics() -> None:
    store = create_in_memory_store()
    await store.record_shadow_comparison(
        {
            "account_id": "90011087",
            "symbol": "XAUUSD",
            "protocol_ok": True,
            "signal_drift": False,
            "command_drift": True,
            "oracle_compared": True,
            "source": "ai_result",
            "created_at": "2026-07-03T00:00:00.000Z",
        }
    )
    await store.record_shadow_comparison(
        {
            "account_id": "90011087",
            "symbol": "XAUUSD",
            "protocol_ok": False,
            "signal_drift": True,
            "command_drift": False,
            "oracle_compared": True,
            "source": "ea_analysis",
            "created_at": "2026-07-03T00:05:00.000Z",
        }
    )

    service = ShadowService(store, lambda: "2026-07-03T00:10:00.000Z")

    assert await service.metrics() == {
        "status": "OK",
        "generated_at": "2026-07-03T00:10:00.000Z",
        "report": {
            "ready": False,
            "protocol_error_rate": 0.5,
            "signal_drift_rate": 0.5,
            "command_drift_rate": 0.5,
            "replay_coverage": 0,
            "last_shadow_event_at": "2026-07-03T00:05:00.000Z",
            "missing_capabilities": ["replay_coverage"],
            "checks": [
                {
                    "label": "Oracle Replay",
                    "value": "validated",
                    "detail": "Go oracle comparisons are flowing into the shadow stream",
                    "tone": "green",
                },
                {
                    "label": "Shadow Drift",
                    "value": "review required",
                    "detail": "Signal 50.00%, command 50.00% (limit 2.00%)",
                    "tone": "red",
                },
                {
                    "label": "Protocol Errors",
                    "value": "50.00%",
                    "detail": "Legacy contract mismatches detected in mirrored traffic",
                    "tone": "red",
                },
                {
                    "label": "Replay Coverage",
                    "value": "pending",
                    "detail": "Replay fixture set has not been scanned yet",
                    "tone": "amber",
                },
            ],
        },
        "totals": {
            "comparisons": 2,
            "protocol_errors": 1,
            "signal_drifts": 1,
            "command_drifts": 1,
        },
    }


async def test_records_oracle_backed_comparison_rows_and_computes_drift_flags() -> None:
    store = create_in_memory_store()
    service = ShadowService(store, lambda: "2026-07-03T00:10:00.000Z")

    comparison = await service.record_oracle_comparison(
        {
            "account_id": "90011087",
            "symbol": "XAUUSD",
            "source": "ea_analysis",
            "node": {
                "signal": {"strategy": "pullback", "side": "BUY", "entry": 3335.7},
                "command": {"action": "SIGNAL", "strategy": "pullback", "tp1": 3345},
            },
            "oracle": {
                "signal": {"strategy": "pullback", "side": "BUY", "entry": 3335.7},
                "command": {"action": "SIGNAL", "strategy": "pullback", "tp1": 3350},
            },
        }
    )

    assert comparison == {
        "account_id": "90011087",
        "symbol": "XAUUSD",
        "protocol_ok": True,
        "signal_drift": False,
        "command_drift": True,
        "oracle_compared": True,
        "source": "ea_analysis",
        "created_at": "2026-07-03T00:10:00.000Z",
    }
    assert await store.list_shadow_comparisons() == [comparison]


async def test_records_runtime_snapshots_and_can_compare_against_oracle_payloads_later() -> None:
    store = create_in_memory_store()
    service = ShadowService(store, lambda: "2026-07-03T00:10:00.000Z")

    await service.record_runtime_snapshot(
        {
            "account_id": "90011087",
            "symbol": "XAUUSD",
            "source": "ea_analysis",
            "signal": {"strategy": "pullback", "side": "BUY", "entry": 3335.7},
            "command": {"action": "SIGNAL", "strategy": "pullback", "tp1": 3345},
        }
    )

    comparison = await service.record_oracle_comparison(
        {
            "account_id": "90011087",
            "symbol": "XAUUSD",
            "source": "ea_analysis",
            "oracle": {
                "signal": {"strategy": "pullback", "side": "BUY", "entry": 3335.7},
                "command": {"action": "SIGNAL", "strategy": "pullback", "tp1": 3350},
            },
        }
    )

    assert comparison == {
        "account_id": "90011087",
        "symbol": "XAUUSD",
        "protocol_ok": True,
        "signal_drift": False,
        "command_drift": True,
        "oracle_compared": True,
        "source": "ea_analysis",
        "created_at": "2026-07-03T00:10:00.000Z",
    }


async def test_builds_cutover_style_qualification_payload_from_current_metrics() -> None:
    store = create_in_memory_store()
    await store.record_shadow_comparison(
        {
            "account_id": "90011087",
            "symbol": "XAUUSD",
            "protocol_ok": True,
            "signal_drift": False,
            "command_drift": False,
            "oracle_compared": True,
            "source": "ea_analysis",
            "created_at": "2026-07-03T00:00:00.000Z",
        }
    )

    service = ShadowService(store, lambda: "2026-07-03T00:10:00.000Z")
    qualification = await service.qualification()

    assert qualification["status"] == "OK"
    assert qualification["report"]["ready"] is False
    assert len(qualification["summary"]) == 4
    assert qualification["summary"][0]["label"] == "Oracle Replay"
    assert qualification["summary"][0]["value"] == "validated"
    assert qualification["totals"] == {
        "comparisons": 1,
        "protocol_errors": 0,
        "signal_drifts": 0,
        "command_drifts": 0,
    }
