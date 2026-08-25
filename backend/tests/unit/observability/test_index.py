"""镜像 packages/observability/src/index.spec.ts 的语义(health/SSE/shadow-report)。"""

from __future__ import annotations

from backend.observability import (
    build_shadow_report,
    create_sse_hub,
    format_sse_frame,
    health_payload,
)


class TestHealthPayload:
    def test_returns_a_stable_health_payload_shape(self) -> None:
        assert health_payload("ok") == {"status": "ok"}


class TestFormatSseFrame:
    def test_formats_sse_frames_with_json_payloads(self) -> None:
        assert format_sse_frame({"status": "OK"}) == 'data: {"status": "OK"}\n\n'


class TestSseHub:
    def test_publishes_events_to_current_subscribers_only(self) -> None:
        hub = create_sse_hub()
        received: list[str] = []
        unsubscribe = hub.subscribe(lambda event: received.append(event["event_type"]))

        hub.publish({"event_type": "ai_result"})
        unsubscribe()
        hub.publish({"event_type": "ai_analysis_failed"})

        assert received == ["ai_result"]
        assert hub.subscriber_count() == 0

    def test_multiple_subscribers_and_isolated_unsubscribe(self) -> None:
        hub = create_sse_hub()
        first: list[str] = []
        second: list[str] = []
        hub.subscribe(lambda event: first.append(event["event_type"]))
        unsub_second = hub.subscribe(lambda event: second.append(event["event_type"]))
        assert hub.subscriber_count() == 2

        hub.publish({"event_type": "ai_result"})
        unsub_second()
        hub.publish({"event_type": "ai_result"})

        assert first == ["ai_result", "ai_result"]
        assert second == ["ai_result"]


class TestBuildShadowReport:
    def test_builds_a_placeholder_report_when_no_shadow_comparisons_exist(self) -> None:
        assert build_shadow_report([]) == {
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
        }

    def test_builds_a_drift_report_from_shadow_comparisons(self) -> None:
        assert build_shadow_report(
            [
                {
                    "account_id": "90011087",
                    "symbol": "XAUUSD",
                    "protocol_ok": True,
                    "signal_drift": False,
                    "command_drift": True,
                    "oracle_compared": True,
                    "source": "ai_result",
                    "created_at": "2026-07-02T12:00:00.000Z",
                }
            ]
        ) == {
            "ready": False,
            "protocol_error_rate": 0,
            "signal_drift_rate": 0,
            "command_drift_rate": 1,
            "replay_coverage": 0,
            "last_shadow_event_at": "2026-07-02T12:00:00.000Z",
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
                    "detail": "Signal 0.00%, command 100.00% (limit 2.00%)",
                    "tone": "red",
                },
                {
                    "label": "Protocol Errors",
                    "value": "0.00%",
                    "detail": "No contract mismatches observed in mirrored traffic",
                    "tone": "green",
                },
                {
                    "label": "Replay Coverage",
                    "value": "pending",
                    "detail": "Replay fixture set has not been scanned yet",
                    "tone": "amber",
                },
            ],
        }

    def test_keeps_shadow_metrics_non_ready_until_oracle_comparisons_exist(self) -> None:
        assert build_shadow_report(
            [
                {
                    "account_id": "90011087",
                    "symbol": "XAUUSD",
                    "protocol_ok": True,
                    "signal_drift": False,
                    "command_drift": False,
                    "oracle_compared": False,
                    "source": "ea_analysis",
                    "created_at": "2026-07-03T00:00:00.000Z",
                }
            ]
        ) == {
            "ready": False,
            "protocol_error_rate": 0,
            "signal_drift_rate": 0,
            "command_drift_rate": 0,
            "replay_coverage": 0,
            "last_shadow_event_at": "2026-07-03T00:00:00.000Z",
            "missing_capabilities": ["go_oracle_reference"],
            "checks": [
                {
                    "label": "Oracle Replay",
                    "value": "pending",
                    "detail": "Go oracle comparisons have not been approved yet",
                    "tone": "orange",
                },
                {
                    "label": "Shadow Drift",
                    "value": "within threshold",
                    "detail": "Signal 0.00%, command 0.00%",
                    "tone": "green",
                },
                {
                    "label": "Protocol Errors",
                    "value": "0.00%",
                    "detail": "No contract mismatches observed in mirrored traffic",
                    "tone": "green",
                },
                {
                    "label": "Replay Coverage",
                    "value": "pending",
                    "detail": "Replay fixture set has not been scanned yet",
                    "tone": "amber",
                },
            ],
        }

    def test_marks_cutover_ready_when_compared_traffic_stays_within_thresholds(self) -> None:
        assert build_shadow_report(
            [
                {
                    "account_id": "90011087",
                    "symbol": "XAUUSD",
                    "protocol_ok": True,
                    "signal_drift": False,
                    "command_drift": False,
                    "oracle_compared": True,
                    "source": "ai_result",
                    "created_at": "2026-07-03T01:00:00.000Z",
                },
                {
                    "account_id": "90011087",
                    "symbol": "XAUUSD",
                    "protocol_ok": True,
                    "signal_drift": False,
                    "command_drift": False,
                    "oracle_compared": True,
                    "source": "ai_result",
                    "created_at": "2026-07-03T01:05:00.000Z",
                },
            ],
            {"total": 1, "validated": 1},
        ) == {
            "ready": True,
            "protocol_error_rate": 0,
            "signal_drift_rate": 0,
            "command_drift_rate": 0,
            "replay_coverage": 1,
            "last_shadow_event_at": "2026-07-03T01:05:00.000Z",
            "missing_capabilities": [],
            "checks": [
                {
                    "label": "Oracle Replay",
                    "value": "validated",
                    "detail": "Go oracle comparisons are flowing into the shadow stream",
                    "tone": "green",
                },
                {
                    "label": "Shadow Drift",
                    "value": "within threshold",
                    "detail": "Signal 0.00%, command 0.00%",
                    "tone": "green",
                },
                {
                    "label": "Protocol Errors",
                    "value": "0.00%",
                    "detail": "No contract mismatches observed in mirrored traffic",
                    "tone": "green",
                },
                {
                    "label": "Replay Coverage",
                    "value": "100.00%",
                    "detail": "1/1 Go fixtures reproduced by Node replay",
                    "tone": "green",
                },
            ],
        }

    def test_partial_replay_coverage_and_zero_total_variants(self) -> None:
        # 部分覆盖:amber,且 ready=False
        report = build_shadow_report(
            [
                {
                    "account_id": "90011087",
                    "symbol": "XAUUSD",
                    "protocol_ok": True,
                    "signal_drift": False,
                    "command_drift": False,
                    "oracle_compared": True,
                    "source": "position_review",
                    "created_at": "2026-07-03T01:00:00.000Z",
                }
            ],
            {"total": 2, "validated": 1},
        )
        assert report["ready"] is False
        assert report["replay_coverage"] == 0.5
        assert report["missing_capabilities"] == ["replay_coverage"]
        coverage_check = report["checks"][3]
        assert coverage_check == {
            "label": "Replay Coverage",
            "value": "50.00%",
            "detail": "1/2 Go fixtures reproduced (raw pass requires 100%)",
            "tone": "amber",
        }

        # total=0 的覆盖率对象:保持 pending
        report = build_shadow_report([], {"total": 0, "validated": 0})
        assert report["replay_coverage"] == 0
        assert report["checks"][3] == {
            "label": "Replay Coverage",
            "value": "pending",
            "detail": "No replay fixture pairs have been recorded yet",
            "tone": "amber",
        }
