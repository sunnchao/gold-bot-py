"""镜像 packages/shared-contracts/src/runtime.spec.ts。"""

from __future__ import annotations

from backend.persistence.records import (
    COMMAND_SOURCES,
    COMMAND_STATUSES,
    RUNTIME_MODES,
    is_command_source,
    is_command_status,
    is_runtime_mode,
)


def test_freezes_the_supported_runtime_modes() -> None:
    assert list(RUNTIME_MODES) == ["oracle", "shadow", "cutover", "rollback"]


def test_freezes_the_supported_command_statuses_and_sources() -> None:
    assert list(COMMAND_STATUSES) == [
        "draft",
        "shadow_only",
        "queued",
        "delivered",
        "acked",
        "rejected",
        "failed",
        "superseded",
    ]
    assert list(COMMAND_SOURCES) == [
        "ea_analysis",
        "live_strategy",
        "position_review",
        "ai_stop_loss",
        "ai_result",
        "ai_risk_alert",
        "ai_approve",
        "position_manager",
    ]


def test_recognizes_valid_runtime_types_and_rejects_unknown_ones() -> None:
    assert is_runtime_mode("oracle") is True
    assert is_runtime_mode("live") is False
    assert is_command_status("queued") is True
    assert is_command_status("pending") is False
    assert is_command_source("ai_result") is True
    assert is_command_source("ai_stop_loss") is True
    assert is_command_source("ai_risk_alert") is True
    assert is_command_source("ai_approve") is True
    assert is_command_source("live_strategy") is True
    assert is_command_source("position_manager") is True
    assert is_command_source("manual") is False
