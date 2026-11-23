"""SOP 五级分层告警路由器与冷却去重单元测试。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from backend.services.sop.alerts import AlertRouter


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
