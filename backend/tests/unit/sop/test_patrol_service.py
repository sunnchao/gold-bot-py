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
