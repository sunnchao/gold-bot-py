"""SOP PatrolService 单元测试: 冷/热分离、双层衔接、连续失败降级与执行模式隔离。"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from backend.persistence.inmemory import InMemoryEaStore
from backend.services.sop.alerts import AlertRouter
from backend.services.sop.patrol import PatrolService


class FakeFeishu:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    async def send(self, payload: dict[str, Any]) -> None:
        self.messages.append(payload)


class StubWorkflow:
    def __init__(self, handler: Any) -> None:
        self._handler = handler
        self.calls: list[dict[str, Any]] = []

    async def run(self, account_id: str, symbol: str, initial_state: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(
            {
                "account_id": account_id,
                "symbol": symbol,
                "role": initial_state.get("analysisRole"),
                "previousLedger": initial_state.get("previousLedger"),
                "skipFeishu": initial_state.get("skipFeishu"),
                "patrolContext": initial_state.get("patrolContext"),
            }
        )
        return await self._handler(account_id, symbol, initial_state)


async def test_cold_patrol_stays_silent_and_m15_consumes_htf_anchor() -> None:
    store = InMemoryEaStore()
    feishu = FakeFeishu()
    clock = [datetime(2026, 11, 19, 8, 0, tzinfo=UTC)]

    async def wf_handler(_acc: str, _sym: str, state: dict[str, Any]) -> dict[str, Any]:
        role = state.get("analysisRole")
        if role == "htf_anchor":
            return {
                "ai_result": {
                    "bias": "bullish",
                    "confidence": 82,
                    "suggested_sl": 4020.0,
                    "trade_plan": {"entry_price": 4050.0, "stop_loss": 4020.0, "take_profits": [4080.0]},
                    "sop_context": {
                        "macro_bias": "bullish_breakout",
                        "intraday_direction": "long",
                        "short_term_targets": [4080.0, 4100.0],
                        "long_term_targets": [4150.0],
                        "playbooks": [
                            {
                                "id": "pb-long",
                                "role": "primary",
                                "direction": "long",
                                "state": "armed",
                                "trigger_zone": {"low": 4060.0, "high": 4070.0},
                                "entry_price": 4062.0,
                                "stop_loss": 4035.0,
                                "targets": [4080.0, 4100.0],
                                "invalid_if": {"price_below": 4020.0},
                            }
                        ],
                    },
                }
            }
        # m15_entry
        return {
            "ai_result": {
                "bias": "bullish",
                "confidence": 86,
                "trade_action": "BUY",
                "trade_plan": {"entry_price": 4063.0, "stop_loss": 4042.0, "take_profits": [4080.0]},
            }
        }

    wf = StubWorkflow(wf_handler)
    patrol = PatrolService(
        store=store,
        workflow_service=wf,
        alert_router=AlertRouter(feishu=feishu, now_fn=lambda: clock[0]),
        execution_mode="advisory_only",
        htf_review_minutes=60,
        move_trigger_pct=0.5,
        now_fn=lambda: clock[0],
    )

    base_snap = {
        "price": 4050.0,
        "bars_by_tf": {"M15": [{"close": 4050.0, "time": "2026-11-19T08:00:00Z"}], "H1": [{"close": 4050.0}]},
        "market_open": True,
        "data_stale": False,
    }
    # 1) 首次冷启动 -> 触发 Tier 1 HTF 锚定 (且 skipFeishu=True 交给 AlertRouter)
    res1 = await patrol.run_patrol("acc-1", "XAUUSD", trigger_type="patrol", snapshot=base_snap)
    assert res1["htf_ran"] is True
    assert res1["m15_ran"] is False
    assert wf.calls[-1]["role"] == "htf_anchor"
    assert wf.calls[-1]["skipFeishu"] is True
    assert len(feishu.messages) == 1

    # 2) 15 分钟后价格仍在 4052 (未到观察区、未超波动阈值、HTF未过期) -> 零 LLM 静默
    clock[0] += timedelta(minutes=15)
    quiet_snap = {
        "price": 4052.0,
        "bars_by_tf": {"M15": [{"close": 4052.0, "time": "2026-11-19T08:15:00Z"}], "H1": [{"close": 4052.0}]},
        "market_open": True,
        "data_stale": False,
    }
    res2 = await patrol.run_patrol("acc-1", "XAUUSD", trigger_type="m15_close", snapshot=quiet_snap)
    assert res2["htf_ran"] is False
    assert res2["m15_ran"] is False
    assert res2["alert_level"] == "silent"
    assert len(wf.calls) == 1
    assert len(feishu.messages) == 1

    # 3) M15 收盘到达剧本触发区 4062 -> 触发 Tier 2 (m15_entry), 且携带 persisted HTF 前提
    clock[0] += timedelta(minutes=15)
    trig_snap = {
        "price": 4062.0,
        "bars_by_tf": {"M15": [{"close": 4062.0, "time": "2026-11-19T08:30:00Z"}], "H1": [{"close": 4062.0}]},
        "market_open": True,
        "data_stale": False,
    }
    res3 = await patrol.run_patrol("acc-1", "XAUUSD", trigger_type="m15_close", snapshot=trig_snap)
    assert res3["m15_ran"] is True
    assert wf.calls[-1]["role"] == "m15_entry"
    assert wf.calls[-1]["previousLedger"]["htf_anchor"]["intraday_direction"] == "long"
    assert res3["alert_level"] == "actionable"
    assert await store.list_commands("acc-1") == []  # advisory_only 绝不自动下发 EA 挂单


async def test_consecutive_data_failures_and_integrated_mode_cancel_pending() -> None:
    store = InMemoryEaStore()
    feishu = FakeFeishu()
    clock = [datetime(2026, 11, 19, 10, 0, tzinfo=UTC)]
    queued_calls: list[tuple[str, str]] = []

    async def queue_fn(acc: str, sym: str, _res: dict[str, Any]) -> None:
        queued_calls.append((acc, sym))

    wf = StubWorkflow(
        lambda _a, _s, _st: asyncio.sleep(
            0,
            result={
                "ai_result": {
                    "bias": "bearish",
                    "confidence": 80,
                    "trade_action": "SELL",
                    "trade_plan": {"entry_price": 4010.0, "stop_loss": 4030.0, "take_profits": [3980.0]},
                }
            },
        )
    )

    patrol = PatrolService(
        store=store,
        workflow_service=wf,
        alert_router=AlertRouter(feishu=feishu, now_fn=lambda: clock[0]),
        execution_mode="integrated",
        queue_pending_fn=queue_fn,
        now_fn=lambda: clock[0],
    )

    stale_snap = {"price": None, "bars_by_tf": {}, "market_open": True, "data_stale": True}
    # 单次失败: 静默重试, 0 条飞书告警
    f1 = await patrol.run_patrol("acc-1", "XAUUSD", snapshot=stale_snap)
    assert f1["alert_level"] == "silent"
    assert f1["consecutive_failures"] == 1
    assert len(feishu.messages) == 0

    # 连续第 2 次失败: 升级 urgent 告警
    f2 = await patrol.run_patrol("acc-1", "XAUUSD", snapshot=stale_snap)
    assert f2["alert_level"] == "urgent"
    assert f2["consecutive_failures"] == 2
    assert len(feishu.messages) == 1

    # 预置多头账本, 随后价格跌破否决边界 -> integrated 模式自动入队 CANCEL_PENDING + M15 入场回调
    await store.save_watch_ledger(
        "acc-1",
        "XAUUSD",
        {
            "htf_reviewed_at": clock[0].isoformat().replace("+00:00", "Z"),
            "htf_anchor": {
                "macro_bias": "bullish",
                "intraday_direction": "long",
                "short_term_targets": [4080.0],
                "long_term_targets": [],
                "veto_boundary": {"long_invalid_below": 4020.0},
                "anchor_price": 4050.0,
            },
            "playbooks": [
                {
                    "id": "pb-long",
                    "role": "primary",
                    "direction": "long",
                    "state": "armed",
                    "invalid_if": {"price_below": 4020.0},
                }
            ],
        },
    )
    breach_snap = {
        "price": 4010.0,
        "bars_by_tf": {"M15": [{"close": 4010.0}], "H1": [{"close": 4010.0}]},
        "market_open": True,
        "data_stale": False,
    }
    res_b = await patrol.run_patrol("acc-1", "XAUUSD", trigger_type="m15_close", snapshot=breach_snap)
    assert res_b["cancelled_pending"] is True
    assert queued_calls == [("acc-1", "XAUUSD")]
    cmds = await store.list_commands("acc-1")
    assert any(c.get("type") == "CANCEL_PENDING" for c in cmds)


async def test_concurrent_patrol_cycles_serialize_cleanly_under_symbol_lock() -> None:
    store = InMemoryEaStore()
    feishu = FakeFeishu()

    async def slow_wf(_acc: str, _sym: str, _state: dict[str, Any]) -> dict[str, Any]:
        await asyncio.sleep(0.02)
        return {"ai_result": {"bias": "bullish", "confidence": 80}}

    patrol = PatrolService(
        store=store,
        workflow_service=StubWorkflow(slow_wf),
        alert_router=AlertRouter(feishu=feishu),
    )
    snap = {
        "price": 4050.0,
        "bars_by_tf": {"M15": [{"close": 4050.0}], "H1": [{"close": 4050.0}]},
        "market_open": True,
        "data_stale": False,
    }
    r1, r2 = await asyncio.gather(
        patrol.run_patrol("acc-1", "XAUUSD", trigger_type="patrol", snapshot=snap),
        patrol.run_patrol("acc-1", "XAUUSD", trigger_type="m15_close", snapshot=snap),
    )
    assert r1["status"] == "ok" and r2["status"] == "ok"
    ledger = await store.get_watch_ledger("acc-1", "XAUUSD")
    assert ledger is not None
    assert ledger["version"] >= 2


async def test_patrol_service_skips_inactive_or_missing_heartbeat_account() -> None:
    store = InMemoryEaStore()
    feishu = FakeFeishu()
    clock = [datetime(2026, 11, 23, 10, 40, tzinfo=UTC)]

    calls: list[tuple[str, str, str | None]] = []

    async def wf_handler(acc: str, sym: str, state: dict[str, Any]) -> dict[str, Any]:
        calls.append((acc, sym, state.get("analysisRole")))
        return {
            "ai_result": {
                "bias": "bearish",
                "confidence": 80,
                "trade_action": "HOLD",
                "suggested_sl": 3650.0,
            }
        }

    patrol = PatrolService(
        store=store,
        workflow_service=StubWorkflow(wf_handler),
        alert_router=AlertRouter(feishu=feishu, now_fn=lambda: clock[0]),
        active_account_ttl_seconds=900,
        now_fn=lambda: clock[0],
    )

    # 1) Stale heartbeat account (90011087, 10 days old)
    await store.save_heartbeat(
        {
            "account_id": "90011087",
            "connected": True,
            "market_open": True,
            "is_trade_allowed": True,
            "last_heartbeat_at": "2026-11-13T03:38:20Z",
        }
    )
    await store.save_tick({"account_id": "90011087", "symbol": "XAUUSD", "bid": 4100.0, "ask": 4100.5})
    await store.save_bars(
        {
            "account_id": "90011087",
            "symbol": "XAUUSD",
            "timeframe": "M15",
            "bars": [{"time": "2026-11-23T10:30:00Z", "open": 4100, "high": 4101, "low": 4099, "close": 4100}],
        }
    )

    # 2) Missing heartbeat (spread-test) and disconnected heartbeat
    await store.save_tick({"account_id": "spread-test", "symbol": "XAUUSD", "bid": 4100.0, "ask": 4100.5})
    await store.save_heartbeat(
        {
            "account_id": "disconnected-acc",
            "connected": False,
            "market_open": True,
            "is_trade_allowed": True,
            "last_heartbeat_at": clock[0].isoformat().replace("+00:00", "Z"),
        }
    )

    # 3) Active account (81124211, 30 seconds old)
    await store.save_heartbeat(
        {
            "account_id": "81124211",
            "connected": True,
            "market_open": True,
            "is_trade_allowed": True,
            "last_heartbeat_at": (clock[0] - timedelta(seconds=30)).isoformat().replace("+00:00", "Z"),
        }
    )
    await store.save_tick({"account_id": "81124211", "symbol": "GOLDm#", "bid": 4100.0, "ask": 4100.2})
    await store.save_bars(
        {
            "account_id": "81124211",
            "symbol": "GOLDM#",
            "timeframe": "M15",
            "bars": [{"time": "2026-11-23T10:30:00Z", "open": 4100, "high": 4101, "low": 4099, "close": 4100}],
        }
    )

    for ttype in ("patrol", "m15_close"):
        res_stale = await patrol.run_patrol("90011087", "XAUUSD", trigger_type=ttype)
        assert res_stale["status"] == "skipped"
        assert res_stale["reason"] == "inactive_account"
        assert res_stale["htf_ran"] is False
        assert res_stale["m15_ran"] is False

    res_missing = await patrol.run_patrol("spread-test", "XAUUSD", trigger_type="patrol")
    assert res_missing["status"] == "skipped"
    res_disconn = await patrol.run_patrol("disconnected-acc", "XAUUSD", trigger_type="patrol")
    assert res_disconn["status"] == "skipped"

    assert await store.get_watch_ledger("90011087", "XAUUSD") is None
    assert await store.list_patrol_logs("90011087") == []
    assert await store.list_patrol_logs("spread-test") == []
    assert len(calls) == 0
    assert len(feishu.messages) == 0

    # Active account patrols and runs HTF anchor normally
    res_active = await patrol.run_patrol("81124211", "GOLDm#", trigger_type="patrol")
    assert res_active["status"] == "ok"
    assert res_active["htf_ran"] is True
    assert calls == [("81124211", "GOLDM#", "htf_anchor")]
    assert await store.get_watch_ledger("81124211", "GOLDM#") is not None

    # Explicit manual trigger still allowed on stale-heartbeat account
    res_manual = await patrol.run_patrol("90011087", "XAUUSD", trigger_type="manual_htf")
    assert res_manual["status"] == "ok"
    assert res_manual["htf_ran"] is True


async def test_routine_htf_m15_wait_and_patrol_cycle_all_deliver_feishu_cards() -> None:
    store = InMemoryEaStore()
    feishu = FakeFeishu()
    clock = [datetime(2026, 11, 23, 12, 0, tzinfo=UTC)]

    async def wf_handler(_acc: str, _sym: str, state: dict[str, Any]) -> dict[str, Any]:
        role = state.get("analysisRole")
        if role == "htf_anchor":
            return {
                "ai_result": {
                    "bias": "bearish",
                    "confidence": 80,
                    "suggested_sl": 3655.0,
                    "suggested_tp": 3590.0,
                    "trade_plan": {"entry_price": 3625.0, "stop_loss": 3655.0, "take_profits": [3590.0]},
                    "arbitration": {"reasoning": "HTF 空头结构保持"},
                    "sop_context": {
                        "macro_bias": "bearish",
                        "intraday_direction": "short",
                        "short_term_targets": [3590.0],
                        "long_term_targets": [3400.0, -50.0],
                        "veto_boundary": {"short_invalid_above": 3655.0, "reason": "HTF 计划止损边界 3655"},
                        "playbooks": [
                            {
                                "id": "pb-short-1",
                                "role": "primary",
                                "direction": "short",
                                "state": "armed",
                                "trigger_zone": {"low": 3600.0, "high": 3610.0},
                                "entry_price": 3605.0,
                                "stop_loss": 3655.0,
                                "targets": [3590.0],
                                "invalid_if": {"price_above": 3655.0},
                            }
                        ],
                    },
                }
            }
        return {
            "ai_result": {
                "bias": "bullish",
                "confidence": 60,
                "trade_action": "HOLD",
                "sop_context": {"sop_blocks": ["sop.m15_counter_htf_blocked"]},
            }
        }

    patrol = PatrolService(
        store=store,
        workflow_service=StubWorkflow(wf_handler),
        alert_router=AlertRouter(feishu=feishu, now_fn=lambda: clock[0]),
        htf_review_minutes=60,
        patrol_interval_seconds=300,
        notify_on_noop=True,
        patrol_report_cooldown_seconds=0,
        now_fn=lambda: clock[0],
    )

    snap = {
        "price": 3625.0,
        "bars_by_tf": {"M15": [{"close": 3625.0}], "H1": [{"close": 3625.0}]},
        "market_open": True,
        "data_stale": False,
    }
    # 1) 首次 HTF 锚定 -> 发卡片 (且负值长期目标被过滤)
    r1 = await patrol.run_patrol("81124211", "GOLDM#", trigger_type="patrol", snapshot=snap)
    assert r1["htf_ran"] is True
    assert len(feishu.messages) == 1
    assert feishu.messages[0]["card"]["header"]["template"] == "red"
    ledger1 = await store.get_watch_ledger("81124211", "GOLDM#")
    assert ledger1 is not None
    assert ledger1["htf_anchor"]["long_term_targets"] == [3400.0]

    # 2) 65 分钟后 HTF 定时例行复盘结论无实质变动 (changed=False) -> 依然发送 HTF 例行复盘卡片!
    clock[0] += timedelta(minutes=65)
    r2 = await patrol.run_patrol("81124211", "GOLDM#", trigger_type="patrol", snapshot=snap)
    assert r2["htf_ran"] is True
    assert len(feishu.messages) == 2
    assert "例行复盘" in feishu.messages[1]["title"]

    # 3) M15 切入复盘给出 HOLD / 逆势拦截 (m15_wait) -> 发送 M15 复盘卡片!
    clock[0] += timedelta(minutes=5)
    r3 = await patrol.run_patrol("81124211", "GOLDM#", trigger_type="manual_m15", snapshot=snap)
    assert r3["m15_ran"] is True
    assert r3["action_taken"] == "m15_wait"
    assert len(feishu.messages) == 3
    assert "逆势拦截" in feishu.messages[2]["title"]

    # 4) 自动冷巡检结束调用 notify_patrol_cycle -> 发送巡检简报卡片!
    clock[0] += timedelta(minutes=5)
    r_noop = await patrol.run_patrol("81124211", "GOLDM#", trigger_type="patrol", snapshot=snap)
    assert r_noop["action_taken"] == "no_op"
    rep = await patrol.notify_patrol_cycle("81124211", [r_noop])
    assert rep is not None and rep["sent"] is True
    assert len(feishu.messages) == 4
    assert "自动巡检简报" in feishu.messages[3]["title"]

    # 5) 配置 notify_on_noop=False 且全为 no_op -> 不重复发送巡检简报
    patrol_quiet = PatrolService(
        store=store,
        workflow_service=StubWorkflow(wf_handler),
        alert_router=AlertRouter(feishu=feishu, now_fn=lambda: clock[0]),
        notify_on_noop=False,
        now_fn=lambda: clock[0],
    )
    rep_quiet = await patrol_quiet.notify_patrol_cycle("81124211", [r_noop])
    assert rep_quiet is not None and rep_quiet["sent"] is False
    assert len(feishu.messages) == 4


async def test_default_snapshot_unblocks_live_tick_and_runs_m15_when_always_run_enabled() -> None:
    store = InMemoryEaStore()
    feishu = FakeFeishu()
    now = datetime(2026, 11, 23, 12, 45, tzinfo=UTC)
    now_iso = now.isoformat().replace("+00:00", "Z")

    await store.save_heartbeat(
        {
            "account_id": "81124211",
            "connected": True,
            "market_open": True,
            "is_trade_allowed": True,
            "max_spread": 99.0,
            "last_heartbeat_at": now_iso,
        }
    )
    await store.save_tick(
        {
            "account_id": "81124211",
            "symbol": "GOLDm#",
            "bid": 3621.5,
            "ask": 3621.8,
            "spread": 31.0,
            "max_spread": 99.0,
            "received_at": now_iso,
        }
    )
    await store.save_bars(
        {
            "account_id": "81124211",
            "symbol": "GOLDM#",
            "timeframe": "M15",
            "bars": [{"time": "2026-11-23T12:30:00Z", "open": 3620, "high": 3622, "low": 3619, "close": 3621.5}],
        }
    )
    await store.save_bars(
        {
            "account_id": "81124211",
            "symbol": "GOLDM#",
            "timeframe": "H1",
            "bars": [{"time": "2026-11-23T12:00:00Z", "open": 3620, "high": 3623, "low": 3618, "close": 3621.5}],
        }
    )
    await store.save_watch_ledger(
        "81124211",
        "GOLDM#",
        {
            "account_id": "81124211",
            "symbol": "GOLDM#",
            "version": 1,
            "htf_reviewed_at": now_iso,
            "htf_anchor": {
                "macro_bias": "bearish",
                "intraday_direction": "short",
                "short_term_targets": [3596.0],
                "veto_boundary": {"short_invalid_above": 3655.0},
                "anchor_price": 3621.5,
            },
            "playbooks": [
                {
                    "id": "pb-1",
                    "role": "primary",
                    "direction": "short",
                    "state": "armed",
                    "trigger_zone": {"low": 3600.0, "high": 3610.0},
                    "stop_loss": 3655.0,
                    "invalid_if": {"price_above": 3655.0},
                }
            ],
        },
    )

    calls: list[str | None] = []

    async def wf_handler(_acc: str, _sym: str, state: dict[str, Any]) -> dict[str, Any]:
        calls.append(state.get("analysisRole"))
        return {"ai_result": {"bias": "bearish", "confidence": 72, "trade_action": "HOLD"}}

    patrol = PatrolService(
        store=store,
        workflow_service=StubWorkflow(wf_handler),
        alert_router=AlertRouter(feishu=feishu, now_fn=lambda: now),
        m15_always_run_entry=True,
        now_fn=lambda: now,
    )

    snap = await patrol._default_snapshot("81124211", "GOLDM#")
    assert snap["market_filters"]["blocked"] is False
    assert snap["market_filters"]["blocking"] == []

    res = await patrol.run_patrol("81124211", "GOLDM#", trigger_type="m15_close")
    assert res["status"] == "ok"
    assert res["htf_ran"] is False
    assert res["m15_ran"] is True
    assert calls == ["m15_entry"]
    assert len(feishu.messages) == 1


async def test_daily_and_weekly_close_reviews_trigger_dedupe_and_skip_inactive() -> None:
    store = InMemoryEaStore()
    feishu = FakeFeishu()
    clock = [datetime(2026, 11, 20, 22, 10, tzinfo=UTC)]  # 周五收盘后 10 分钟

    await store.save_heartbeat(
        {
            "account_id": "81124211",
            "connected": True,
            "market_open": False,  # 周五收盘后市场关闭, 但仍在最近 48 小时活跃心跳内
            "is_trade_allowed": False,
            "last_heartbeat_at": (clock[0] - timedelta(minutes=20)).isoformat().replace("+00:00", "Z"),
        }
    )
    await store.save_heartbeat(
        {
            "account_id": "90011087",
            "connected": True,
            "market_open": False,
            "last_heartbeat_at": "2026-11-10T00:00:00Z",  # 过期非活跃账户
        }
    )

    d1_bars = [
        {"time": "2026-11-12", "open": 3590, "high": 3610, "low": 3580, "close": 3600},
        {"time": "2026-11-13", "open": 3600, "high": 3620, "low": 3585, "close": 3610},
        {"time": "2026-11-16", "open": 3610, "high": 3630, "low": 3600, "close": 3620},
        {"time": "2026-11-17", "open": 3620, "high": 3640, "low": 3610, "close": 3635},
        {"time": "2026-11-18", "open": 3635, "high": 3645, "low": 3605, "close": 3615},
        {"time": "2026-11-19", "open": 3615, "high": 3642, "low": 3608, "close": 3638},
        {"time": "2026-11-20", "open": 3638, "high": 3670, "low": 3632, "close": 3665},
    ]
    await store.save_bars({"account_id": "81124211", "symbol": "GOLDM#", "timeframe": "D1", "bars": d1_bars})
    await store.save_bars(
        {
            "account_id": "81124211",
            "symbol": "GOLDM#",
            "timeframe": "H4",
            "bars": [{"time": "2026-11-20T20:00:00Z", "open": 3655, "high": 3670, "low": 3650, "close": 3665}],
        }
    )
    await store.save_bars(
        {
            "account_id": "81124211",
            "symbol": "GOLDM#",
            "timeframe": "H1",
            "bars": [{"time": "2026-11-20T21:00:00Z", "open": 3660, "high": 3670, "low": 3658, "close": 3665}],
        }
    )
    await store.save_bars({"account_id": "90011087", "symbol": "GOLDM#", "timeframe": "D1", "bars": d1_bars})

    wf_calls: list[dict[str, Any]] = []

    async def wf_handler(_acc: str, _sym: str, state: dict[str, Any]) -> dict[str, Any]:
        wf_calls.append(
            {
                "role": state.get("analysisRole"),
                "skipPublish": state.get("skipPublish"),
                "forceAnalyze": state.get("forceAnalyze"),
                "patrolContext": state.get("patrolContext"),
            }
        )
        return {
            "ai_result": {
                "bias": "bullish",
                "confidence": 75,
                "arbitration": {"reasoning": "D1 收盘走强，复盘关注下周延续。"},
            }
        }

    patrol = PatrolService(
        store=store,
        workflow_service=StubWorkflow(wf_handler),
        alert_router=AlertRouter(feishu=feishu, now_fn=lambda: clock[0]),
        now_fn=lambda: clock[0],
    )

    # 1) 非活跃账户应直接跳过
    inactive_res = await patrol.run_daily_close_review("90011087", "GOLDM#", bar_time="2026-11-20")
    assert inactive_res["status"] == "skipped"
    assert inactive_res["reason"] == "inactive_account"

    # 2) 周四 D1 收盘 -> 仅触发每日收盘复盘，不触发每周收盘复盘
    thu_res = await patrol.handle_d1_bar_close("81124211", "GOLDM#", bar_time="2026-11-19")
    assert thu_res["daily_review"]["status"] == "ok"
    assert thu_res["weekly_review"] is None
    assert [c["role"] for c in wf_calls] == ["daily_close_review"]
    assert wf_calls[0]["skipPublish"] is True
    assert wf_calls[0]["forceAnalyze"] is True
    assert len(feishu.messages) == 1
    assert "每日收盘日内走势复盘" in feishu.messages[0]["title"]
    # 收盘复盘绝不创建或覆盖日内盯盘账本，也不下单
    assert await store.get_watch_ledger("81124211", "GOLDM#") is None
    assert await store.list_commands("81124211") == []

    # 3) 周五收盘后定时窗口 check_post_close_reviews -> 触发周五每日 + 每周复盘各 1 次
    post_res = await patrol.check_post_close_reviews("81124211", "GOLDM#", now=clock[0])
    assert post_res["daily_review"]["status"] == "ok"
    assert post_res["weekly_review"]["status"] == "ok"
    assert [c["role"] for c in wf_calls[-2:]] == ["daily_close_review", "weekly_close_review"]
    assert len(feishu.messages) == 3
    assert "每日收盘日内走势复盘" in feishu.messages[1]["title"]
    assert "每周收盘周内走势复盘" in feishu.messages[2]["title"]

    # 4) 周一新 D1 K线推入周五 bar_time 时不再重复推送
    await store.save_bars(
        {
            "account_id": "81124211",
            "symbol": "GOLDM#",
            "timeframe": "D1",
            "bars": [*d1_bars, {"time": "2026-11-23", "open": 3665, "high": 3680, "low": 3655, "close": 3672}],
        }
    )
    dup_res = await patrol.handle_d1_bar_close("81124211", "GOLDM#", bar_time="2026-11-20")
    assert dup_res["daily_review"]["reason"] == "already_reviewed"
    assert dup_res["weekly_review"]["reason"] == "already_reviewed"
    assert len(feishu.messages) == 3
