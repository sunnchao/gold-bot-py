"""SOP WatchLedger 状态机、双层权限隔离与无孤儿剧本保证单元测试。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from backend.services.sop.ledger import (
    empty_watch_ledger,
    evaluate_ledger_patrol,
    format_ledger_prompt_block,
    is_material_change,
    reconcile_ledger_after_review,
)


def _sample_htf_ledger(now: datetime) -> dict:
    return {
        "account_id": "acc-1",
        "symbol": "XAUUSD",
        "version": 1,
        "htf_reviewed_at": now.isoformat().replace("+00:00", "Z"),
        "m15_reviewed_at": None,
        "last_patrol_at": None,
        "last_patrol_price": 4050.0,
        "htf_anchor": {
            "macro_bias": "bullish",
            "intraday_direction": "long",
            "short_term_targets": [4080.0, 4100.0],
            "long_term_targets": [4150.0],
            "veto_boundary": {"long_invalid_below": 4020.0, "short_invalid_above": None, "reason": "H4 结构低点"},
            "vacuum_zone": {"low": 4040.0, "high": 4150.0},
            "rationale": "H4 维持多头结构",
            "confidence": 82.0,
            "reviewed_at": now.isoformat().replace("+00:00", "Z"),
            "anchor_bar_time": None,
            "anchor_price": 4050.0,
        },
        "key_levels": [
            {
                "id": "kl-res",
                "price": 4060.0,
                "role": "resistance",
                "tier": "core",
                "sources": ["D1_PDH_PDL", "H4_SWING"],
                "break_state": "unbroken",
            }
        ],
        "playbooks": [
            {
                "id": "pb-long-1",
                "name": "回踩/突破做多剧本",
                "role": "primary",
                "direction": "long",
                "state": "armed",
                "trigger_zone": {"low": 4055.0, "high": 4065.0},
                "entry_price": 4058.0,
                "stop_loss": 4035.0,
                "targets": [4080.0, 4100.0],
                "targets_hit": [],
                "invalid_if": {"price_below": 4020.0},
            }
        ],
        "recent_events": [],
    }


def test_playbook_lifecycle_transitions_through_exhausted_and_false_breakout_reclaim() -> None:
    t0 = datetime(2026, 11, 19, 8, 0, tzinfo=UTC)
    ledger = _sample_htf_ledger(t0)

    # 1. 进入观察区 (4052 在 4055 +/- 0.15% = 4048.9 附近) -> watching
    r1 = evaluate_ledger_patrol(
        ledger,
        current_price=4052.0,
        bars_by_tf={"M15": [{"close": 4052.0, "time": "2026-11-19T08:15:00Z"}], "H1": [{"close": 4052.0}]},
        now=t0 + timedelta(minutes=15),
    )
    assert r1["updated_ledger"]["playbooks"][0]["state"] == "watching"
    assert "entered_watch_zone" in r1["trigger_reasons"]

    # 2. M15 收盘突破阻力位 4060 且命中 trigger_zone -> triggered + key_level_breakout
    r2 = evaluate_ledger_patrol(
        r1["updated_ledger"],
        current_price=4063.0,
        bars_by_tf={"M15": [{"close": 4063.0, "time": "2026-11-19T08:30:00Z"}], "H1": [{"close": 4063.0}]},
        now=t0 + timedelta(minutes=30),
    )
    assert r2["updated_ledger"]["playbooks"][0]["state"] == "triggered"
    assert r2["updated_ledger"]["key_levels"][0]["break_state"] == "broken_up"
    assert "key_level_breakout" in r2["trigger_reasons"]

    # 3. 到达第一目标 4080 -> partial_taken
    r3 = evaluate_ledger_patrol(
        r2["updated_ledger"],
        current_price=4082.0,
        bars_by_tf={"M15": [{"close": 4082.0, "time": "2026-11-19T08:45:00Z"}], "H1": [{"close": 4082.0}]},
        now=t0 + timedelta(minutes=45),
    )
    assert r3["updated_ledger"]["playbooks"][0]["state"] == "partial_taken"

    # 4. 全部目标 4100 到位 -> exhausted + cancel_pending_recommended + forces HTF rebuild
    r4 = evaluate_ledger_patrol(
        r3["updated_ledger"],
        current_price=4102.0,
        bars_by_tf={"M15": [{"close": 4102.0, "time": "2026-11-19T08:50:00Z"}], "H1": [{"close": 4102.0}]},
        now=t0 + timedelta(minutes=50),
    )
    assert r4["updated_ledger"]["playbooks"][0]["state"] == "exhausted"
    assert "logic_exhausted" in r4["trigger_reasons"]
    assert "no_active_playbook" in r4["trigger_reasons"]
    assert r4["should_trigger_htf"] is True
    assert r4["cancel_pending_recommended"] is True

    # 5. 从第2步验证假突破收回 -> reclaimed + urgent
    reclaim_eval = evaluate_ledger_patrol(
        r2["updated_ledger"],
        current_price=4056.0,
        bars_by_tf={"M15": [{"close": 4056.0, "time": "2026-11-19T08:45:00Z"}], "H1": [{"close": 4056.0}]},
        now=t0 + timedelta(minutes=45),
    )
    assert reclaim_eval["updated_ledger"]["key_levels"][0]["break_state"] == "reclaimed"
    assert "false_breakout_reclaim" in reclaim_eval["trigger_reasons"]
    assert any(c["level"] == "urgent" for c in reclaim_eval["alert_candidates"])


def test_reconcile_m15_strictly_preserves_htf_anchor_and_never_leaves_orphan_playbook() -> None:
    t0 = datetime(2026, 11, 19, 8, 0, tzinfo=UTC)
    htf_ledger = _sample_htf_ledger(t0)

    # M15 审查即使返回相反 bias/target, 也不得篡改 htf_anchor!
    updated_after_m15 = reconcile_ledger_after_review(
        htf_ledger,
        account_id="acc-1",
        symbol="XAUUSD",
        role="m15_entry",
        review_result={
            "bias": "bearish",
            "confidence": 75,
            "trade_plan": {"entry_price": 4057.0, "stop_loss": 4042.0, "take_profits": [4080.0]},
            "sop_context": {
                "macro_bias": "bearish",
                "intraday_direction": "short",
                "short_term_targets": [3990.0],
            },
        },
        current_price=4057.0,
        now=t0 + timedelta(minutes=15),
    )
    assert updated_after_m15["htf_anchor"] == htf_ledger["htf_anchor"]
    assert updated_after_m15["m15_reviewed_at"] is not None

    # 失效后再经 HTF 重建 -> rearmed, 且保证始终存在活跃剧本(无孤儿)
    inv_eval = evaluate_ledger_patrol(
        htf_ledger,
        current_price=4010.0,
        bars_by_tf={"M15": [{"close": 4010.0}], "H1": [{"close": 4010.0}]},
        now=t0 + timedelta(minutes=20),
    )
    assert inv_eval["updated_ledger"]["playbooks"][0]["state"] == "invalidated"

    rebuilt = reconcile_ledger_after_review(
        inv_eval["updated_ledger"],
        account_id="acc-1",
        symbol="XAUUSD",
        role="htf_anchor",
        review_result={
            "bias": "bearish",
            "confidence": 78,
            "trade_plan": {"entry_price": 4012.0, "stop_loss": 4035.0, "take_profits": [3960.0]},
        },
        current_price=4010.0,
        now=t0 + timedelta(minutes=25),
    )
    active = [pb for pb in rebuilt["playbooks"] if pb["state"] in ("armed", "rearmed")]
    assert len(active) >= 1
    assert active[-1]["state"] == "rearmed"
    changed, reasons = is_material_change(htf_ledger, rebuilt)
    assert changed is True
    assert "intraday_direction_changed" in reasons

    prompt_txt = format_ledger_prompt_block(htf_ledger, role="m15_entry")
    assert "HARD PREMISE FOR M15 ENTRY" in prompt_txt
    assert "4080.0" in prompt_txt
    assert empty_watch_ledger("acc-1", "xauusd")["symbol"] == "XAUUSD"


def test_htf_hold_with_bearish_bias_preserves_short_direction_compact_veto_and_armed_playbook() -> None:
    from backend.agents.agents.comprehensive_analyst import _build_sop_context

    long_reasoning = (
        "技术面显示 H4 宏观偏空但短线反弹，需等待阻力带收盘拒绝确认，鉴于市场未开最终观望。" * 8
    )
    payload = {
        "market": {"bid": 3610.0, "ask": 3610.5},
        "daily_structure": {
            "status": "ok",
            "macro_bias": "bearish",
            "prior_major_high": 3650.0,
            "measured_targets": [{"ratio": 1.618, "price": 3440.0}],
        },
        "confluence_levels": [
            {"price": 3630.2, "role": "resistance", "tier": "core", "sources": ["H1_SWING", "PSYCH_ROUND"]},
            {"price": 3596.08, "role": "support", "tier": "core", "sources": ["H4_SWING", "D1_PDH_PDL"]},
            {"price": 3585.76, "role": "support", "tier": "secondary", "sources": ["H1_SWING"]},
        ],
    }
    analyst_result = {
        "technical": {
            "bias": "bearish",
            "confidence": 75,
            "support_levels": [{"price": 3596.08}],
            "resistance_levels": [{"price": 3630.2}, {"price": 3634.94}],
            "rationale": long_reasoning,
        },
        "risk": {"suggestedSL": 3637.0},
        "arbitration": {
            "final_direction": "hold",
            "action": "hold",
            "confidence": 65,
            "reasoning": long_reasoning,
            "trade_recommendation": None,
        },
    }

    sop_ctx = _build_sop_context(
        result=analyst_result,
        payload=payload,
        options={"analysisRole": "htf_anchor"},
    )
    assert sop_ctx["intraday_direction"] == "short"
    assert sop_ctx["short_term_targets"] == [3596.08, 3585.76]
    assert sop_ctx["veto_boundary"] is not None
    assert sop_ctx["veto_boundary"]["short_invalid_above"] == 3637.0
    assert sop_ctx["veto_boundary"]["long_invalid_below"] is None
    assert len(sop_ctx["veto_boundary"]["reason"]) <= 160
    assert len(sop_ctx["htf_rationale"]) <= 240

    t0 = datetime(2026, 11, 23, 9, 36, tzinfo=UTC)
    reconciled = reconcile_ledger_after_review(
        None,
        account_id="81124211",
        symbol="GOLDM#",
        role="htf_anchor",
        review_result={**analyst_result, "bias": "bearish", "sop_context": sop_ctx},
        daily_structure=payload["daily_structure"],
        confluence_levels=payload["confluence_levels"],
        current_price=3610.0,
        now=t0,
    )
    anchor = reconciled["htf_anchor"]
    assert anchor["intraday_direction"] == "short"
    assert anchor["veto_boundary"]["short_invalid_above"] == 3637.0
    assert len(anchor["rationale"]) <= 240
    assert reconciled["playbooks"][0]["direction"] == "short"
    assert reconciled["playbooks"][0]["state"] == "armed"
    assert reconciled["playbooks"][0]["invalid_if"] == {"price_above": 3637.0}

    # 下一次平盘/微反弹巡检进入 watching 观察态(而非误触发 invalidated)
    next_patrol = evaluate_ledger_patrol(
        reconciled,
        current_price=3612.0,
        bars_by_tf={"M15": [{"close": 3612.0}], "H1": [{"close": 3612.0}]},
        now=t0 + timedelta(minutes=15),
    )
    assert next_patrol["updated_ledger"]["playbooks"][0]["state"] in ("armed", "watching")
