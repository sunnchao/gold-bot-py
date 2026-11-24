"""SOP WatchLedger 状态机、双层权限隔离与无孤儿剧本保证单元测试。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from backend.services.sop.ledger import (
    empty_watch_ledger,
    evaluate_ledger_patrol,
    format_ledger_prompt_block,
    is_failed_review_result,
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
    assert "htf" not in prompt_txt.lower()
    assert "4080.0" in prompt_txt
    assert empty_watch_ledger("acc-1", "xauusd")["symbol"] == "XAUUSD"


def test_intraday_uses_h1_h4_only_without_atr_budget_or_htf_abbr() -> None:
    from backend.agents.agents.comprehensive_analyst import _build_sop_context, _format_sop_realtime_section

    payload = {
        "market": {"bid": 3620.0, "ask": 3620.5},
        "trend_context": {
            "d1_direction": "BULL",
            "h4_direction": "BEAR",
            "h1_direction": "BEAR",
            "m30_direction": "BEAR",
            "consensus_direction": "BEAR",
            "consensus_strength": 0.7,
        },
        "daily_structure": {"status": "ok", "macro_bias": "bullish_breakout", "prior_major_high": 3800.0},
        "day_range": {
            "status": "ok",
            "realized_range": 95.0,
            "expected_atr": 70.0,
            "atr_consumed_pct": 135.7,
            "expansion_stage": "extended",
            "range_exhausted": True,
        },
        "confluence_levels": [
            {"price": 3800.0, "role": "resistance", "tier": "core", "sources": ["D1_PDH_PDL"]},
            {"price": 3645.0, "role": "resistance", "tier": "core", "sources": ["H4_SWING", "H1_SWING"]},
            {"price": 3598.0, "role": "support", "tier": "core", "sources": ["H1_SMC"]},
        ],
    }

    intraday_prompt = _format_sop_realtime_section(payload, {"analysisRole": "htf_anchor"})
    assert "ATR Budget" not in intraday_prompt
    assert "exhausted=" not in intraday_prompt
    assert "D1 Structure" not in intraday_prompt
    assert "1H/4H" in intraday_prompt
    assert "3645.0" in intraday_prompt
    assert "3800.0" not in intraday_prompt
    assert "htf" not in intraday_prompt.lower()

    # D1 方向是 BULL，但 1H/4H 是 BEAR，中性仲裁下日内方向应取 1H/4H (short)，不被 D1 覆盖
    sop_ctx = _build_sop_context(
        result={"technical": {"bias": "neutral"}, "risk": {}, "arbitration": {"final_direction": "hold"}},
        payload=payload,
        options={"analysisRole": "htf_anchor"},
    )
    assert sop_ctx["intraday_direction"] == "short"
    assert sop_ctx["macro_bias"] == "bearish"
    assert sop_ctx["daily_structure"] is None
    assert "day_range" not in sop_ctx
    assert sop_ctx["veto_boundary"] is not None
    assert "htf" not in sop_ctx["veto_boundary"]["reason"].lower()

    reconciled = reconcile_ledger_after_review(
        None,
        account_id="81124211",
        symbol="GOLDM#",
        role="htf_anchor",
        review_result={"bias": "neutral", "confidence": 70, "sop_context": sop_ctx},
        daily_structure=payload["daily_structure"],
        confluence_levels=payload["confluence_levels"],
        current_price=3620.0,
    )
    assert "htf" not in reconciled["htf_anchor"]["veto_boundary"]["reason"].lower()
    assert "htf" not in reconciled["playbooks"][0]["name"].lower()
    assert "htf" not in reconciled["playbooks"][0]["trigger_condition"].lower()

    # 收盘后日/周复盘角色才会注入 D1 Structure 与对应复盘统计（同样没有 ATR Budget）
    daily_prompt = _format_sop_realtime_section(
        payload,
        {
            "analysisRole": "daily_close_review",
            "patrolContext": {"daily_recap": {"status": "ok", "trade_date": "2026-11-23", "close": 3635.6}},
        },
    )
    assert "D1 Structure" in daily_prompt
    assert "Daily Close Recap" in daily_prompt
    assert "ATR Budget" not in daily_prompt

    weekly_prompt = _format_sop_realtime_section(
        payload,
        {
            "analysisRole": "weekly_close_review",
            "patrolContext": {"weekly_recap": {"status": "ok", "iso_week": "2026-W47", "week_close": 3660.0}},
        },
    )
    assert "D1 Structure" in weekly_prompt
    assert "Weekly Close Recap" in weekly_prompt
    assert "ATR Budget" not in weekly_prompt


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

    # 关键位破位经 HTF 复盘后不应被重置为 unbroken(防止同一价位 5 分钟死循环触发)
    broken_ledger = evaluate_ledger_patrol(
        reconciled,
        current_price=3590.0,
        bars_by_tf={"M15": [{"close": 3590.0}], "H1": [{"close": 3590.0}]},
        now=t0 + timedelta(minutes=20),
    )["updated_ledger"]
    after_rebuild = reconcile_ledger_after_review(
        broken_ledger,
        account_id="81124211",
        symbol="GOLDM#",
        role="htf_anchor",
        review_result={**analyst_result, "bias": "bearish", "sop_context": sop_ctx},
        confluence_levels=payload["confluence_levels"],
        current_price=3590.0,
        now=t0 + timedelta(minutes=21),
    )
    kl_3596 = next(lv for lv in after_rebuild["key_levels"] if lv["price"] == 3596.08)
    assert kl_3596["break_state"] == "broken_down"
    repeat_patrol = evaluate_ledger_patrol(
        after_rebuild,
        current_price=3590.0,
        bars_by_tf={"M15": [{"close": 3590.0}], "H1": [{"close": 3590.0}]},
        now=t0 + timedelta(minutes=26),
    )
    assert "key_level_breakdown" not in repeat_patrol["trigger_reasons"]
    assert repeat_patrol["should_trigger_htf"] is False


def test_intraday_direction_and_prompt_do_not_depend_on_d1() -> None:
    from backend.agents.agents.comprehensive_analyst import _build_sop_context, _format_sop_realtime_section

    neutral_result = {"technical": {"bias": "neutral"}, "risk": {}, "arbitration": {"final_direction": "hold"}}
    for d1_dir, consensus_dir in (("BULL", "BULL"), ("BEAR", "BEAR")):
        neutral_intraday_payload = {
            "market": {"bid": 3620.0, "ask": 3620.5},
            "trend_context": {
                "d1_direction": d1_dir,
                "h4_direction": "NEUTRAL",
                "h1_direction": "NEUTRAL",
                "m30_direction": d1_dir,
                "consensus_direction": consensus_dir,
                "consensus_strength": 0.4,
            },
        }
        sop_ctx = _build_sop_context(
            result=neutral_result,
            payload=neutral_intraday_payload,
            options={"analysisRole": "htf_anchor"},
        )
        assert sop_ctx["intraday_direction"] == "wait"
        assert sop_ctx["macro_bias"] == "neutral"

    split_payload = {
        "market": {"bid": 3620.0, "ask": 3620.5},
        "trend_context": {
            "d1_direction": "BEAR",
            "h4_direction": "BULL",
            "h1_direction": "BEAR",
            "m30_direction": "BEAR",
            "consensus_direction": "BEAR",
            "consensus_strength": 0.5,
        },
    }
    split_ctx = _build_sop_context(
        result=neutral_result,
        payload=split_payload,
        options={"analysisRole": "htf_anchor"},
    )
    assert split_ctx["intraday_direction"] == "wait"
    assert split_ctx["macro_bias"] == "neutral"

    d1_only_prompt = _format_sop_realtime_section(
        {
            "confluence_levels": [
                {"price": 3800.0, "role": "resistance", "tier": "core", "sources": ["D1_PDH_PDL"]},
            ]
        },
        {"analysisRole": "htf_anchor"},
    )
    assert "3800.0" not in d1_only_prompt
    assert "Confluence Key Levels" not in d1_only_prompt

    mixed_prompt = _format_sop_realtime_section(
        {
            "confluence_levels": [
                {"price": 3645.0, "role": "resistance", "tier": "core", "sources": ["D1_PDH_PDL", "H4_SWING"]},
            ]
        },
        {"analysisRole": "htf_anchor"},
    )
    assert "3645.0" in mixed_prompt
    assert "H4_SWING" in mixed_prompt
    assert "D1_PDH_PDL" not in mixed_prompt

    daily_prompt = _format_sop_realtime_section(
        {
            "confluence_levels": [
                {"price": 3800.0, "role": "resistance", "tier": "core", "sources": ["D1_PDH_PDL"]},
            ]
        },
        {
            "analysisRole": "daily_close_review",
            "patrolContext": {"daily_recap": {"status": "ok", "trade_date": "2026-11-23", "close": 3635.6}},
        },
    )
    assert "3800.0" in daily_prompt
    assert "D1_PDH_PDL" in daily_prompt


def test_reconcile_htf_anchor_extracts_reference_levels_when_trade_plan_is_none() -> None:
    t0 = datetime(2026, 11, 24, 1, 0, tzinfo=UTC)
    reconciled = reconcile_ledger_after_review(
        None,
        account_id="acc-1",
        symbol="GOLDM#",
        role="htf_anchor",
        review_result={
            "bias": "bearish",
            "confidence": 76,
            "suggested_sl": 3655.0,
            "suggested_tp": 0.0,
            "trade_plan": None,
            "trade_recommendation": {
                "direction": "sell",
                "entry_price": 3625.0,
                "stop_loss": 3655.0,
                "take_profit_1": 3590.0,
                "take_profit_2": 3570.0,
            },
            "sop_context": {
                "macro_bias": "bearish",
                "intraday_direction": "short",
                "short_term_targets": [3590.0, 3570.0],
            },
        },
        current_price=3620.0,
        now=t0,
    )
    anchor = reconciled["htf_anchor"]
    assert anchor["intraday_direction"] == "short"
    assert anchor["short_term_targets"] == [3590.0, 3570.0]
    assert anchor["veto_boundary"]["short_invalid_above"] == 3655.0
    pb = reconciled["playbooks"][0]
    assert pb["entry_price"] == 3625.0
    assert pb["stop_loss"] == 3655.0
    assert pb["targets"] == [3590.0, 3570.0]
    assert 0.0 not in pb["targets"]


def test_short_playbook_filters_above_market_targets_and_prevents_exhausted_loop() -> None:
    t0 = datetime(2026, 11, 24, 1, 0, tzinfo=UTC)
    reconciled = reconcile_ledger_after_review(
        None,
        account_id="acc-1",
        symbol="GOLDM#",
        role="htf_anchor",
        review_result={
            "bias": "bearish",
            "confidence": 72,
            "suggested_sl": 3660.0,
            "suggested_tp": 3620.0,
            "sop_context": {
                "macro_bias": "bearish",
                "intraday_direction": "short",
                "short_term_targets": [3653.13, 3620.0],
                "playbooks": [
                    {
                        "id": "pb-1",
                        "direction": "short",
                        "entry_price": 3640.0,
                        "stop_loss": 3660.0,
                        "targets": [3653.13, 3620.0],
                    }
                ],
            },
        },
        current_price=3634.4,
        now=t0,
    )
    assert reconciled["htf_anchor"]["short_term_targets"] == [3620.0]
    assert reconciled["playbooks"][0]["targets"] == [3620.0]

    patrol = evaluate_ledger_patrol(reconciled, current_price=3634.4, now=t0 + timedelta(minutes=5))
    assert patrol["should_trigger_htf"] is False
    assert "no_active_playbook" not in patrol["trigger_reasons"]
    assert patrol["updated_ledger"]["playbooks"][0]["state"] == "armed"


def test_wait_direction_does_not_spawn_long_fallback_playbook_or_loop() -> None:
    t0 = datetime(2026, 11, 24, 1, 0, tzinfo=UTC)
    reconciled = reconcile_ledger_after_review(
        None,
        account_id="acc-1",
        symbol="GOLDM#",
        role="htf_anchor",
        review_result={
            "bias": "neutral",
            "confidence": 40,
            "sop_context": {"macro_bias": "range", "intraday_direction": "wait"},
        },
        current_price=3634.4,
        now=t0,
    )
    assert reconciled["htf_anchor"]["intraday_direction"] == "wait"
    assert reconciled["playbooks"] == []

    patrol = evaluate_ledger_patrol(reconciled, current_price=3634.4, now=t0 + timedelta(minutes=5))
    assert patrol["should_trigger_htf"] is False
    assert "no_active_playbook" not in patrol["trigger_reasons"]


def test_is_failed_review_result_detects_zero_confidence_fallback() -> None:
    assert is_failed_review_result({"confidence": 0, "suggested_sl": 0, "suggested_tp": 0}) is True
    assert is_failed_review_result({"arbitration": {"contradiction": "analysis_unavailable"}}) is True
    assert is_failed_review_result({"bias": "bullish", "arbitration": {"action": "hold"}}) is False
