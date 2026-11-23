"""RiskGate SOP 时段提醒窗口与白银独立元数据单元测试。"""

from __future__ import annotations

from datetime import UTC, datetime

from backend.trading_core.riskgate.riskgate import (
    evaluate_market_filters,
    friday_close_reminder_stage,
    is_monday_open_observation_window,
    metadata_for,
)


def test_silver_aliases_resolve_independent_xagusd_metadata() -> None:
    gold = metadata_for("XAUUSD")
    xag = metadata_for("XAGUSD")
    silver = metadata_for("SILVER")
    silverm = metadata_for("SILVERm#")

    assert xag["symbol"] == "XAGUSD"
    assert xag["contractSize"] == 5000
    assert silver == xag
    assert silverm == xag
    assert xag is not gold


def test_monday_open_observation_and_friday_flatten_stages() -> None:
    # Sun 22:15 UTC == Mon 06:15 BJT -> 周一早盘开盘观察窗
    assert is_monday_open_observation_window(datetime(2026, 11, 22, 22, 15, tzinfo=UTC)) is True
    assert is_monday_open_observation_window(datetime(2026, 11, 22, 21, 15, tzinfo=UTC)) is False
    assert is_monday_open_observation_window(datetime(2026, 11, 23, 10, 0, tzinfo=UTC)) is False

    # 周五 18:30 UTC -> flatten_reminder; 周五 19:30 UTC -> flatten_urgent; >=20:00 UTC 已入收盘窗 -> None
    assert friday_close_reminder_stage(datetime(2026, 11, 20, 18, 30, tzinfo=UTC)) == "flatten_reminder"
    assert friday_close_reminder_stage(datetime(2026, 11, 20, 19, 30, tzinfo=UTC)) == "flatten_urgent"
    assert friday_close_reminder_stage(datetime(2026, 11, 20, 20, 45, tzinfo=UTC)) is None
    assert friday_close_reminder_stage(datetime(2026, 11, 18, 19, 30, tzinfo=UTC)) is None


def test_evaluate_market_filters_gates_sop_session_warnings_behind_opt_in_flag() -> None:
    fri_1930 = datetime(2026, 11, 20, 19, 30, tzinfo=UTC)
    base_input = {
        "symbol": "XAUUSD",
        "now": fri_1930,
        "spread": 1.0,
        "maxSpread": 5.0,
        "state": {"heartbeat": {}, "tick": {}, "bars": {}},
    }

    without_sop = evaluate_market_filters(base_input)
    without_codes = [w["code"] for w in without_sop["warnings"]]
    assert "session.friday_flatten_urgent" not in without_codes

    with_sop = evaluate_market_filters({**base_input, "includeSopSessionWarnings": True})
    with_codes = [w["code"] for w in with_sop["warnings"]]
    assert "session.friday_flatten_urgent" in with_codes

    mon_open = evaluate_market_filters(
        {
            **base_input,
            "now": datetime(2026, 11, 22, 22, 15, tzinfo=UTC),
            "includeSopSessionWarnings": True,
        }
    )
    mon_codes = [w["code"] for w in mon_open["warnings"]]
    assert "session.monday_open_observation" in mon_codes
