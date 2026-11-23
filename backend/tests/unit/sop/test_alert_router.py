"""SOP 五级分层告警路由器与冷却去重单元测试。"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from backend.services.sop.alerts import AlertRouter, SopFeishuSender
from backend.services.sop.cards import (
    build_daily_close_review_card,
    build_htf_review_card,
    build_m15_review_card,
    build_patrol_cycle_card,
    build_weekly_close_review_card,
)


class FakeFeishu:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send(self, payload: dict) -> None:
        self.messages.append(payload)


async def test_alert_router_handles_five_levels_and_urgent_cooldown_bypass() -> None:
    feishu = FakeFeishu()
    clock = [datetime(2026, 11, 19, 8, 0, tzinfo=UTC)]
    router = AlertRouter(feishu=feishu, cooldown_seconds=900, now_fn=lambda: clock[0])

    # 1. silent 永不推送
    res_silent = await router.route(
        account_id="acc-1",
        symbol="XAUUSD",
        level="silent",
        category="no_op",
        title="t",
        content="c",
    )
    assert res_silent["sent"] is False
    assert len(feishu.messages) == 0

    # 2. notify 首次推送 -> 同 category 冷却期内第二次抑制
    res_n1 = await router.route(
        account_id="acc-1",
        symbol="XAUUSD",
        level="notify",
        category="htf_update",
        title="HTF 更新",
        content="看多 4080",
        dedup_key="htf:1",
    )
    assert res_n1["sent"] is True
    clock[0] += timedelta(seconds=60)
    res_n2 = await router.route(
        account_id="acc-1",
        symbol="XAUUSD",
        level="notify",
        category="htf_update",
        title="HTF 更新",
        content="看多 4080",
        dedup_key="htf:1",
    )
    assert res_n2["sent"] is False
    assert res_n2["suppressed"] is True

    # 3. actionable 新指纹可发送; 同指纹短冷却内抑制
    res_a1 = await router.route(
        account_id="acc-1",
        symbol="XAUUSD",
        level="actionable",
        category="m15_entry",
        title="M15 入场",
        content="BUY @ 4055",
        dedup_key="entry:1",
    )
    assert res_a1["sent"] is True
    res_a2 = await router.route(
        account_id="acc-1",
        symbol="XAUUSD",
        level="actionable",
        category="m15_entry",
        title="M15 入场",
        content="BUY @ 4055",
        dedup_key="entry:1",
    )
    assert res_a2["suppressed"] is True

    # 4. urgent 永远穿透冷却时间
    res_u1 = await router.route(
        account_id="acc-1",
        symbol="XAUUSD",
        level="urgent",
        category="veto_breach",
        title="紧急风控",
        content="否决线跌破 1",
    )
    res_u2 = await router.route(
        account_id="acc-1",
        symbol="XAUUSD",
        level="urgent",
        category="veto_breach",
        title="紧急风控",
        content="否决线跌破 2",
    )
    assert res_u1["sent"] is True
    assert res_u2["sent"] is True

    # 5. digest 汇总发送
    res_d = await router.route(
        account_id="acc-1",
        symbol="XAUUSD",
        level="digest",
        category="weekend_digest",
        title="周末复盘",
        content="本周净收益 +3.2R",
    )
    assert res_d["sent"] is True
    assert len(feishu.messages) == 5

    # 6. bypass_cooldown=True 穿透冷却期
    res_bypass = await router.route(
        account_id="acc-1",
        symbol="XAUUSD",
        level="notify",
        category="htf_update",
        title="HTF 强发",
        content="绕过冷却",
        dedup_key="htf:1",
        bypass_cooldown=True,
    )
    assert res_bypass["sent"] is True
    assert len(feishu.messages) == 6


async def test_sop_feishu_sender_validates_code_retries_and_passes_card() -> None:
    class MockResp:
        def __init__(self, status: int, body: dict[str, Any]) -> None:
            self.status_code = status
            self._body = json.dumps(body)

        @property
        def text(self) -> str:
            return self._body

    calls: list[dict[str, Any]] = []
    responses: list[MockResp] = []

    async def fake_fetch(url: str, *, method: str, headers: dict[str, str], body: str) -> MockResp:
        calls.append({"url": url, "method": method, "headers": headers, "body": json.loads(body)})
        return responses.pop(0)

    logs: list[str] = []
    clock = [datetime(2026, 11, 23, 12, 0, tzinfo=UTC)]
    sender = SopFeishuSender(
        webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/test-hook",
        secret="test-secret",
        fetch_impl=fake_fetch,
        backoff_seconds=(0.0, 0.0),
        log=logs.append,
        now_fn=lambda: clock[0],
    )

    # 1) 11232 限流先重试一次，随后返回 code=0 -> 成功，且传递完整交互卡片
    responses.extend(
        [
            MockResp(200, {"code": 11232, "msg": "frequency limit"}),
            MockResp(200, {"code": 0, "msg": "ok"}),
        ]
    )
    title, md, card = build_htf_review_card(
        account_id="81124211",
        symbol="GOLDM#",
        current_price=3625.85,
        ledger={
            "version": 6,
            "htf_anchor": {
                "intraday_direction": "short",
                "macro_bias": "bearish",
                "short_term_targets": [3596.08, 3540.64],
                "long_term_targets": [355.09],
                "veto_boundary": {"short_invalid_above": 3654.76, "reason": "HTF 计划止损边界 3654.76"},
                "confidence": 80.0,
            },
            "key_levels": [{"price": 3594.93, "role": "support", "break_state": "unbroken"}],
            "playbooks": [{"id": "pb-1", "role": "primary", "direction": "short", "state": "armed"}],
        },
        ai_result={},
        trigger_reasons=["htf_schedule_due"],
        changed=False,
        change_reasons=[],
        execution_mode="advisory_only",
        now=clock[0],
    )
    router = AlertRouter(feishu=sender, now_fn=lambda: clock[0])
    out_ok = await router.route(
        account_id="81124211",
        symbol="GOLDM#",
        level="notify",
        category="htf_routine_review",
        title=title,
        content=md,
        card=card,
        bypass_cooldown=True,
    )
    assert out_ok["sent"] is True
    assert len(calls) == 2
    sent_payload = calls[1]["body"]
    assert isinstance(sent_payload["timestamp"], str)
    assert sent_payload["sign"]
    assert sent_payload["card"]["header"]["template"] == "red"
    assert any("[SOP-FEISHU] delivered" in line for line in logs)

    # 2) 返回非 0 业务码（例如 19021 验签失败） -> 返回 False 且 AlertRouter 标记 delivery_failed
    responses.append(MockResp(200, {"code": 19021, "msg": "sign match fail"}))
    p_title, p_md, p_card = build_patrol_cycle_card(
        account_id="81124211",
        results=[
            {
                "status": "ok",
                "symbol": "GOLDM#",
                "current_price": 3625.85,
                "action_taken": "no_op",
                "htf_ran": False,
                "m15_ran": False,
                "ledger": {"htf_anchor": {"intraday_direction": "short", "macro_bias": "bearish"}},
            }
        ],
        interval_seconds=300,
        execution_mode="advisory_only",
        now=clock[0],
    )
    assert "自动巡检简报" in p_title
    out_fail = await router.route(
        account_id="81124211",
        symbol="ALL",
        level="digest",
        category="patrol_cycle_report",
        title=p_title,
        content=p_md,
        card=p_card,
        bypass_cooldown=True,
    )
    assert out_fail["sent"] is False
    assert out_fail["reason"] == "delivery_failed"
    assert any("code=19021" in line for line in logs)

    # 3) M15 拦截卡片格式正确且全部卡片不含 HTF 缩写
    assert "htf" not in json.dumps(card, ensure_ascii=False).lower()
    assert "htf" not in md.lower()
    assert "htf" not in json.dumps(p_card, ensure_ascii=False).lower()

    m_title, m_md, m_card = build_m15_review_card(
        account_id="81124211",
        symbol="GOLDM#",
        current_price=3620.0,
        ledger={
            "version": 6,
            "htf_anchor": {
                "intraday_direction": "short",
                "veto_boundary": {"short_invalid_above": 3655.0, "reason": "HTF 计划止损边界 3655"},
            },
        },
        ai_result={
            "arbitration": {"reasoning": "HTF 结构维持空头"},
            "sop_context": {"sop_blocks": ["sop.m15_counter_htf_blocked"]},
            "confidence": 65,
        },
        trigger_reasons=["m15_close"],
        is_actionable=False,
        side_label="",
        execution_mode="advisory_only",
        now=clock[0],
    )
    assert "逆势拦截" in m_title
    assert m_card["header"]["template"] == "orange"
    assert "htf" not in json.dumps(m_card, ensure_ascii=False).lower()
    assert "htf" not in m_md.lower()

    # 4) 每日收盘 & 每周收盘复盘卡片格式正确且不含 HTF 缩写
    d_title, d_md, d_card = build_daily_close_review_card(
        account_id="81124211",
        symbol="GOLDM#",
        daily_recap={
            "status": "ok",
            "trade_date": "2026-11-23",
            "open": 3557.91,
            "high": 3689.61,
            "low": 3553.75,
            "close": 3635.63,
            "net_change": 77.72,
            "change_pct": 2.18,
            "amplitude": 135.86,
            "candle_type": "阳线收涨",
            "prev_high": 3636.52,
            "prev_low": 3502.76,
            "vs_prev_day": "突破前日高点",
        },
        daily_structure={
            "status": "ok",
            "macro_bias": "bullish",
            "prior_major_high": 3890.0,
            "prior_major_low": 3502.76,
        },
        ai_result={"arbitration": {"reasoning": "日线收复失地并突破前日高点，明日关注回踩支撑。"}},
        execution_mode="advisory_only",
        now=clock[0],
    )
    assert "每日收盘日内走势复盘" in d_title
    assert d_card["header"]["template"] == "green"
    assert "htf" not in json.dumps(d_card, ensure_ascii=False).lower()
    assert "htf" not in d_md.lower()

    w_title, w_md, w_card = build_weekly_close_review_card(
        account_id="81124211",
        symbol="GOLDM#",
        weekly_recap={
            "status": "ok",
            "iso_week": "2026-W47",
            "week_start_date": "2026-11-16",
            "week_end_date": "2026-11-20",
            "trading_days_count": 5,
            "week_open": 3600.0,
            "week_high": 3665.0,
            "week_low": 3550.0,
            "week_close": 3580.0,
            "net_change": -20.0,
            "change_pct": -0.56,
            "week_range": 115.0,
            "weekly_candle_type": "周线收阴",
            "up_days": 2,
            "down_days": 3,
            "vs_prev_week": "上周区间内震荡",
            "daily_breakdown": [{"date": "2026-11-16", "open": 3600.0, "close": 3620.0, "change_pct": 0.56}],
        },
        daily_structure={
            "status": "ok",
            "macro_bias": "bearish",
            "measured_targets": [{"ratio": 1.0, "price": 3480.0}],
        },
        ai_result={"arbitration": {"reasoning": "本周先扬后抑周线收阴，下周关注前低支撑表现。"}},
        execution_mode="advisory_only",
        now=clock[0],
    )
    assert "每周收盘周内走势复盘" in w_title
    assert w_card["header"]["template"] == "red"
    assert "htf" not in json.dumps(w_card, ensure_ascii=False).lower()
    assert "htf" not in w_md.lower()
