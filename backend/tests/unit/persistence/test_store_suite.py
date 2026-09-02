"""EaStore 共享测试套件:内存版与 SQLite 版同一语义(镜像 index.spec.ts 的存储相关用例)。

参数化 store_factory:内存版 / 临时 SQLite 文件版,逐项断言两者行为一致。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable

import pytest

from backend.persistence.records import EaRecord
from backend.persistence.store import EaStore, create_in_memory_store, create_sqlite_store

STORE_FACTORIES: list[Callable[[str], EaStore]] = [
    lambda _path: create_in_memory_store(),
    lambda path: create_sqlite_store(path),
]


@pytest.fixture(params=STORE_FACTORIES, ids=["in-memory", "sqlite"])
async def store(request: pytest.FixtureRequest, tmp_path) -> AsyncIterator[EaStore]:
    instance = request.param(str(tmp_path / "store.db"))
    yield instance
    await instance.close()


def registration(account: str = "acc-1", **extra: object) -> EaRecord:
    return {"account_id": account, "broker": "Demo", "leverage": 500, **extra}


def command(account: str = "acc-1", **extra: object) -> EaRecord:
    return {
        "command_id": f"cmd_{account}_{len(extra)}",
        "account_id": account,
        "action": "SIGNAL",
        "source": "ea_analysis",
        "symbol": "XAUUSD",
        "type": "BUY",
        "entry": 100.0,
        **extra,
    }


def decision_input(account: str, decision_id: str = "dec-1", **extra: object) -> EaRecord:
    return {
        "decision_id": decision_id,
        "account_id": account,
        "symbol": "XAUUSD",
        "stage": "ai_result",
        "status": "accepted",
        "reason_codes": ["mode.approve"],
        "summary": {"decision_id": decision_id},
        "related_command_id": "",
        "created_at": "2026-08-22T08:00:00Z",
        **extra,
    }


# ---------------------------------------------------------------- 生命周期快照
async def test_stores_ea_lifecycle_snapshots_by_account(store: EaStore) -> None:
    await store.save_registration(registration("acc-1"))
    await store.save_registration(registration("acc-2"))
    await store.save_heartbeat({"account_id": "acc-1", "balance": 1000})
    await store.save_tick({"account_id": "acc-1", "symbol": "XAUUSD", "bid": 3335.0})
    await store.save_bars(
        {"account_id": "acc-1", "symbol": "XAUUSD", "timeframe": "H1", "bars": [{"time": "1", "close": 1.0}]}
    )
    await store.save_positions({"account_id": "acc-1", "symbol": "XAUUSD", "positions": [{"ticket": 1}]})

    reg = await store.get_registration("acc-1")
    assert reg == registration("acc-1")
    assert (await store.get_registration("missing")) is None
    assert (await store.get_heartbeat("acc-1")) == {"account_id": "acc-1", "balance": 1000}
    assert (await store.get_latest_tick("acc-1", "XAUUSD")) == {
        "account_id": "acc-1",
        "symbol": "XAUUSD",
        "bid": 3335.0,
    }
    assert (await store.get_latest_tick("acc-1", "XAGUSD")) is None
    assert (await store.get_bars("acc-1", "XAUUSD", "H1")) == [{"time": "1", "close": 1.0}]
    assert (await store.get_bars("acc-1", "XAUUSD", "H4")) == []
    assert (await store.get_positions("acc-1", "XAUUSD")) == [{"ticket": 1}]
    assert await store.list_account_ids() == ["acc-1", "acc-2"]


async def test_isolates_position_snapshots_by_account_and_symbol(store: EaStore) -> None:
    await store.save_positions({"account_id": "acc-1", "symbol": "XAUUSD", "positions": [{"ticket": 1}]})
    await store.save_positions({"account_id": "acc-1", "symbol": "XAGUSD", "positions": [{"ticket": 2}]})
    await store.save_positions({"account_id": "acc-2", "symbol": "XAUUSD", "positions": [{"ticket": 3}]})

    assert (await store.get_positions("acc-1", "XAUUSD")) == [{"ticket": 1}]
    assert (await store.get_positions("acc-1", "XAGUSD")) == [{"ticket": 2}]
    assert all(p["ticket"] in (1, 2) for p in await store.get_positions("acc-1"))
    assert (await store.get_positions("acc-2")) == [{"ticket": 3}]


async def test_symbol_lookup_is_case_insensitive_across_kinds(store: EaStore) -> None:
    """回归:/bars 入库 upper() 而 /tick /positions 保留 EA 原样(如 GOLDm#),
    查询侧必须大小写不敏感,否则 AI payload 读不到 tick → market_open=false → 静默跳过分析。"""
    await store.save_bars(
        {"account_id": "acc-1", "symbol": "GOLDM#", "timeframe": "H1", "bars": [{"time": "1", "close": 4325.0}]}
    )
    await store.save_tick({"account_id": "acc-1", "symbol": "GOLDm#", "bid": 4325.92, "ask": 4326.42})
    await store.save_positions({"account_id": "acc-1", "symbol": "GOLDm#", "positions": [{"ticket": 9}]})

    # 大写查 tick/positions(analysis_payload 触发链的实际路径)
    tick_upper = await store.get_latest_tick("acc-1", "GOLDM#")
    assert tick_upper is not None and tick_upper["bid"] == 4325.92
    assert (await store.get_positions("acc-1", "GOLDM#")) == [{"ticket": 9}]
    # 原样小写查 bars
    assert (await store.get_bars("acc-1", "GOLDm#", "H1")) == [{"time": "1", "close": 4325.0}]

    # 持仓状态同样大小写不敏感
    await store.save_position_state("acc-1", "GOLDM#", {"ticket": 9, "tp1_hit": False})
    states = await store.load_position_states("acc-1", "goldm#")
    assert [s["ticket"] for s in states] == [9]

    # list_symbols 去重大小写变体(返回首个出现的 EA 原样写法)
    symbols = await store.list_symbols("acc-1")
    assert len([s for s in symbols if s.upper() == "GOLDM#"]) == 1

    # 不同品种仍隔离
    await store.save_tick({"account_id": "acc-1", "symbol": "SILVERm#", "bid": 68.0})
    assert (await store.get_latest_tick("acc-1", "SILVERM#")) is not None
    assert (await store.get_latest_tick("acc-1", "XAUUSD")) is None


async def test_variant_snapshots_coexist_read_deterministic(store: EaStore) -> None:
    """变体并存(Codex 复核项):GOLDM# 与 GOLDm# 各存一份快照时,
    折叠查询必须确定地取最早写入的一份,不得合并(重复持仓)或随机选行。"""
    # positions:先 GOLDm#(EA 原样),后 GOLDM#
    await store.save_positions({"account_id": "acc-1", "symbol": "GOLDm#", "positions": [{"ticket": 1}]})
    await store.save_positions({"account_id": "acc-1", "symbol": "GOLDM#", "positions": [{"ticket": 2}]})
    assert (await store.get_positions("acc-1", "GOLDM#")) == [{"ticket": 1}]
    assert (await store.get_positions("acc-1", "goldm#")) == [{"ticket": 1}]
    # 无 symbol 列出全部(不折叠去重)
    all_pos = await store.get_positions("acc-1")
    assert sorted(p["ticket"] for p in all_pos) == [1, 2]

    # tick:变体并存取最早
    await store.save_tick({"account_id": "acc-1", "symbol": "GOLDm#", "bid": 111.0})
    await store.save_tick({"account_id": "acc-1", "symbol": "GOLDM#", "bid": 222.0})
    tick = await store.get_latest_tick("acc-1", "GOLDM#")
    assert tick is not None and tick["bid"] == 111.0

    # list_symbols 只返回一个代表(EA 原样)
    symbols = await store.list_symbols("acc-1")
    gold_variants = [s for s in symbols if s.upper() == "GOLDM#"]
    assert len(gold_variants) == 1
    assert gold_variants[0] == "GOLDm#"


async def test_claims_each_bar_close_event_once(store: EaStore) -> None:
    assert await store.claim_bar_close_event("acc-1", "XAUUSD", "M30", "2026-08-25T08:00:00Z") is True
    assert await store.claim_bar_close_event("acc-1", "XAUUSD", "M30", "2026-08-25T08:00:00Z") is False
    assert await store.claim_bar_close_event("acc-1", "XAUUSD", "M15", "2026-08-25T08:00:00Z") is True
    assert await store.claim_bar_close_event("acc-1", "XAUUSD", "M30", "2026-08-25T08:30:00Z") is True


# ---------------------------------------------------------------- 持仓状态
async def test_stores_symbol_scoped_position_manager_states(store: EaStore) -> None:
    await store.save_position_state(
        "acc-1",
        "XAUUSD",
        {
            "ticket": 101,
            "tp1_hit": True,
            "max_profit_atr": 2.5,
            "be_trigger_atr": 1.5,
            "open_time": "",
            "last_modify_time": "",
            "trailing_closed": True,
        },
    )
    await store.save_position_state("acc-1", "XAUUSD", {"ticket": 102, "tp1_hit": False, "add_on_count": 1})
    states = await store.load_position_states("acc-1", "XAUUSD")
    assert [s["ticket"] for s in states] == [101, 102]
    assert states[0]["tp1_hit"] is True
    assert states[0]["trailing_closed"] is True
    assert states[1]["add_on_count"] == 1

    await store.delete_stale_position_states("acc-1", "XAUUSD", [101])
    states = await store.load_position_states("acc-1", "XAUUSD")
    assert [s["ticket"] for s in states] == [101]

    await store.delete_stale_position_states("acc-1", "XAUUSD", [])
    assert await store.load_position_states("acc-1", "XAUUSD") == []


# ---------------------------------------------------------------- AI 结果
async def test_keeps_only_latest_ai_result_per_symbol(store: EaStore) -> None:
    await store.save_ai_result("acc-1", "XAUUSD", {"bias": "bullish"})
    await store.save_ai_result("acc-1", "XAUUSD", {"bias": "bearish"})
    await store.save_ai_result("acc-1", "XAGUSD", {"bias": "neutral"})
    await store.save_ai_result("acc-2", "XAUUSD", {"bias": "bullish"})

    results = await store.get_ai_results("acc-1")
    assert {r["symbol"]: r["bias"] for r in results} == {"XAUUSD": "bearish", "XAGUSD": "neutral"}


# ---------------------------------------------------------------- 命令生命周期
async def test_delivers_explicitly_queued_commands_once(store: EaStore) -> None:
    await store.enqueue_command("acc-1", command("acc-1"))
    assert (await store.get_command("cmd_acc-1_0"))["status"] == "queued"

    delivered = await store.poll_commands("acc-1")
    assert len(delivered) == 1
    assert "account_id" not in delivered[0]
    assert "status" not in delivered[0]
    assert "created_at" not in delivered[0]

    assert (await store.poll_commands("acc-1")) == []


async def test_defaults_unseen_account_to_oracle_mode_and_persists_modes(store: EaStore) -> None:
    assert await store.get_runtime_mode("acc-1") == "oracle"
    await store.set_runtime_mode("acc-1", "cutover")
    assert await store.get_runtime_mode("acc-1") == "cutover"


async def test_stores_command_candidates_and_transitions_them(store: EaStore) -> None:
    candidate = command("acc-1", command_id="cand-1", confidence=80, decision_id="dec-1")
    saved = await store.save_command_candidate("acc-1", candidate)
    assert saved["status"] == "draft"
    assert saved["command_id"] == "cand-1"

    await store.promote_command("cand-1")
    assert (await store.get_command("cand-1"))["status"] == "queued"

    # promote 幂等:不重复产生 decision 事件
    events_before = await store.list_decision_events({"account_id": "acc-1"})
    await store.promote_command("cand-1")
    events_after = await store.list_decision_events({"account_id": "acc-1"})
    assert len(events_after) == len(events_before)

    delivered = await store.poll_commands("acc-1")
    assert len(delivered) == 1
    assert delivered[0]["source"] == "ea_analysis"  # source 显式存在 → poll 可见

    reconciled = await store.reconcile_command_result("acc-1", "cand-1", "OK", ticket=777)
    assert reconciled is True
    got = await store.get_command("cand-1")
    assert got["status"] == "acked"
    assert got["ticket"] == 777
    assert got["acked_at"]

    order_results = await store.get_order_results("acc-1")
    assert order_results[-1]["ticket"] == 777


async def test_does_not_deliver_expired_queued_commands(store: EaStore) -> None:
    await store.enqueue_command("acc-1", command("acc-1", command_id="expired-1", expiration=1000))
    delivered = await store.poll_commands("acc-1")
    assert delivered == []
    got = await store.get_command("expired-1")
    assert got["status"] == "failed"
    assert got["result"] == "expired"
    assert got["error_text"] == "command expired before delivery"


def test_ai_approve_commands_expire_after_four_hours() -> None:
    """ai_approve 命令无显式 expiration 时,created_at 起 4 小时后过期(helpers 层)。"""
    from backend.persistence.helpers import is_runtime_command_expired

    now = "2026-08-22T04:30:00Z"
    fresh: EaRecord = {"source": "ai_approve", "created_at": "2026-08-22T01:00:00Z"}
    stale: EaRecord = {"source": "ai_approve", "created_at": "2026-08-22T00:00:00Z"}  # 4.5h 前
    assert is_runtime_command_expired(fresh, now) is False
    assert is_runtime_command_expired(stale, now) is True
    # 带显式 expiration 的任意来源也过期
    numeric: EaRecord = {"source": "ea_analysis", "expiration": 1000}
    assert is_runtime_command_expired(numeric, "2099-01-01T00:00:00Z") is True


async def test_applies_order_results_only_to_delivered_commands(store: EaStore) -> None:
    await store.enqueue_command("acc-1", command("acc-1"))
    assert await store.reconcile_command_result("acc-1", "cmd_acc-1_0", "OK") is False  # 未 delivered

    await store.poll_commands("acc-1")
    assert await store.reconcile_command_result("acc-1", "cmd_acc-1_0", "OK") is True
    assert await store.reconcile_command_result("acc-1", "cmd_acc-1_0", "OK") is False  # 已 acked


async def test_records_failed_delivered_order_results_with_error_text(store: EaStore) -> None:
    await store.enqueue_command("acc-1", command("acc-1", command_id="fail-1", decision_id="dec-1"))
    await store.poll_commands("acc-1")
    assert await store.reconcile_command_result("acc-1", "fail-1", "4108", error_text="invalid ticket") is True

    got = await store.get_command("fail-1")
    assert got["status"] == "failed"
    assert got["result"] == "4108"
    assert got["error_text"] == "invalid ticket"
    assert got["failed_at"]

    events = await store.list_decision_events({"account_id": "acc-1"})
    order_result_events = [e for e in events if e["stage"] == "order_result"]
    assert len(order_result_events) == 1
    assert order_result_events[0]["status"] == "failed"
    assert order_result_events[0]["summary"]["ticket"] == 0


async def test_records_command_enqueue_and_delivery_decision_events(store: EaStore) -> None:
    await store.enqueue_command("acc-1", command("acc-1", decision_id="dec-1", source="ai_result"))
    await store.poll_commands("acc-1")

    events = await store.list_decision_events({"account_id": "acc-1"})
    stages = [e["stage"] for e in events]
    assert "command_enqueued" in stages
    assert "command_delivered" in stages
    enqueued = next(e for e in events if e["stage"] == "command_enqueued")
    assert enqueued["decision_id"] == "dec-1"
    assert "source.ai_result" in enqueued["reason_codes"]


async def test_detects_active_ai_approve_pending_commands(store: EaStore) -> None:
    await store.enqueue_command(
        "acc-1", command("acc-1", source="ai_approve", symbol="XAUUSD", type="BUY", decision_id="dec-1")
    )
    now = "2099-01-01T00:00:00Z"
    assert await store.has_active_ai_approve_pending("acc-1", "XAUUSD", "buy", now) is True
    assert await store.has_active_ai_approve_pending("acc-1", "XAUUSD", "sell", now) is False
    assert await store.has_active_ai_approve_pending("acc-1", "XAGUSD", "buy", now) is False
    assert await store.has_active_ai_approve_pending("acc-2", "XAUUSD", "buy", now) is False

    await store.enqueue_command(
        "acc-1",
        command("acc-1", source="ai_approve", symbol="XAUUSD", type="BUY", decision_id="dec-2", expiration=1000),
    )
    assert await store.has_active_ai_approve_pending("acc-1", "XAUUSD", "buy", now) is True  # 第一条仍有效


# ---------------------------------------------------------------- 影子校验
async def test_stores_and_reloads_latest_shadow_snapshot(store: EaStore) -> None:
    await store.save_shadow_snapshot(
        {
            "account_id": "acc-1",
            "symbol": "XAUUSD",
            "source": "ea_analysis",
            "signal": {"side": "buy"},
            "command": None,
            "created_at": "2026-08-22T00:00:00Z",
        }
    )
    snapshot = await store.get_latest_shadow_snapshot("acc-1", "XAUUSD", "ea_analysis")
    assert snapshot is not None
    assert snapshot["signal"] == {"side": "buy"}
    assert await store.get_latest_shadow_snapshot("acc-1", "XAUUSD", "ai_result") is None


async def test_filters_and_summarizes_shadow_comparisons(store: EaStore) -> None:
    await store.record_shadow_comparison(
        {
            "account_id": "acc-1",
            "symbol": "XAUUSD",
            "protocol_ok": True,
            "signal_drift": False,
            "command_drift": False,
            "oracle_compared": True,
            "source": "ea_analysis",
            "created_at": "2026-08-22T00:00:00Z",
        }
    )
    await store.record_shadow_comparison(
        {
            "account_id": "acc-1",
            "symbol": "XAUUSD",
            "protocol_ok": False,
            "signal_drift": True,
            "command_drift": True,
            "oracle_compared": True,
            "source": "ea_analysis",
            "created_at": "2026-08-22T00:01:00Z",
        }
    )

    all_comparisons = await store.list_shadow_comparisons()
    assert len(all_comparisons) == 2

    filtered = await store.list_shadow_comparisons({"protocol_ok": True})
    assert len(filtered) == 1

    summary = await store.summarize_shadow_comparisons()
    assert summary["comparisons"] == 2
    assert summary["protocol_errors"] == 1
    assert summary["signal_drifts"] == 1
    assert summary["command_drifts"] == 1
    assert summary["first_created_at"] == "2026-08-22T00:00:00Z"
    assert summary["last_created_at"] == "2026-08-22T00:01:00Z"


# ---------------------------------------------------------------- 决策时间线
async def test_stores_decision_events_newest_first_with_filters(store: EaStore) -> None:
    for i in range(8):
        await store.record_decision_event(decision_input("acc-1", f"dec-{i}", created_at=f"2026-08-22T00:0{i}:00Z"))

    events = await store.list_decision_events({"account_id": "acc-1"})
    assert len(events) == 8
    assert events[0]["decision_id"] == "dec-7"  # newest first

    limited = await store.list_decision_events({"account_id": "acc-1", "limit": 3})
    assert len(limited) == 3

    by_status = await store.list_decision_events({"account_id": "acc-1", "status": "accepted"})
    assert len(by_status) == 8

    other = await store.list_decision_events({"account_id": "acc-2"})
    assert other == []


# ---------------------------------------------------------------- 候选信号
async def test_lists_account_symbols_and_pending_signals(store: EaStore) -> None:
    await store.save_ai_result("acc-1", "XAUUSD", {"bias": "bullish"})  # ai_result 不产生 symbol
    await store.save_tick({"account_id": "acc-1", "symbol": "XAUUSD", "bid": 1.0})
    await store.save_bars({"account_id": "acc-1", "symbol": "XAGUSD", "timeframe": "H1", "bars": []})

    assert await store.list_symbols("acc-1") == ["XAUUSD", "XAGUSD"]
    assert await store.list_ai_symbols("acc-1") == ["XAGUSD", "XAUUSD"]  # sorted fallback

    await store.save_registration(registration("acc-1", ai_symbols=["XAUUSD"]))
    assert await store.list_ai_symbols("acc-1") == ["XAUUSD"]


async def test_symbol_scoped_ai_results_do_not_create_symbols(store: EaStore) -> None:
    """AI 结果只产生账户 ID,不进入 list_symbols(镜像 TS 同名用例)。"""
    await store.save_ai_result("acc-1", "XAUUSD", {"bias": "bullish"})
    assert await store.list_account_ids() == ["acc-1"]
    assert await store.list_symbols("acc-1") == []


async def test_records_candidate_signal_decision_events(store: EaStore) -> None:
    await store.save_pending_signal(
        {
            "account_id": "acc-1",
            "symbol": "XAUUSD",
            "side": "buy",
            "score": 8,
            "strategy": "pullback",
            "expires_at": "2099-01-01T00:00:00Z",
        }
    )

    signals = await store.get_pending_signals("acc-1", "XAUUSD")
    assert len(signals) == 1
    signal_id = signals[0]["id"]

    events = await store.list_decision_events({"account_id": "acc-1"})
    candidate = [e for e in events if e["stage"] == "candidate_signal"]
    assert len(candidate) == 1
    assert candidate[0]["decision_id"] == f"candidate_acc-1_XAUUSD_{signal_id}"
    assert candidate[0]["reason_codes"] == ["candidate.pullback"]
    assert candidate[0]["summary"]["score"] == 8


async def test_updates_explicit_pending_signal_ids_without_duplicating_decisions(store: EaStore) -> None:
    await store.save_pending_signal(
        {
            "account_id": "acc-1",
            "symbol": "XAUUSD",
            "side": "buy",
            "score": 87,
            "strategy": "momentum_scalp",
            "status": "pending",
            "created_at": "2026-04-13T08:00:00.000Z",
            "expires_at": "2026-04-13T08:01:00.000Z",
        }
    )
    await store.save_pending_signal(
        {
            "id": 1,
            "account_id": "acc-1",
            "symbol": "XAUUSD",
            "side": "buy",
            "score": 91,
            "strategy": "momentum_scalp",
            "status": "pending",
            "created_at": "2026-04-13T08:00:15.000Z",
            "expires_at": "2026-04-13T08:02:00.000Z",
        }
    )
    # 显式 id 但不存在 → 不插入(镜像 TS)
    await store.save_pending_signal(
        {
            "id": 99,
            "account_id": "acc-1",
            "symbol": "GBPJPY",
            "side": "sell",
            "score": 70,
            "strategy": "range",
            "status": "pending",
            "created_at": "2026-04-13T08:00:30.000Z",
            "expires_at": "2026-04-13T08:02:00.000Z",
        }
    )

    signals = await store.get_pending_signals("acc-1", "XAUUSD")
    assert len(signals) == 1
    assert signals[0]["id"] == 1
    assert signals[0]["score"] == 91
    assert signals[0]["expires_at"] == "2026-04-13T08:02:00.000Z"
    assert await store.get_pending_signals("acc-1", "GBPJPY") == []
    events = await store.list_decision_events({"account_id": "acc-1", "symbol": "XAUUSD"})
    assert len(events) == 1  # 替换不重复记录候选决策


async def test_allocates_pending_signal_ids_before_recording_candidate_decisions(store: EaStore) -> None:
    await store.save_pending_signal(
        {
            "account_id": "acc-1",
            "symbol": "XAUUSD",
            "side": "buy",
            "score": 87,
            "strategy": "momentum_scalp",
            "status": "pending",
            "created_at": "2026-04-13T08:00:00.000Z",
            "expires_at": "2026-04-13T08:01:00.000Z",
        }
    )
    signals = await store.get_pending_signals("acc-1", "XAUUSD")
    assert signals[0]["id"] == 1
    events = await store.list_decision_events({"account_id": "acc-1", "symbol": "XAUUSD"})
    candidate = [e for e in events if e["stage"] == "candidate_signal"]
    assert len(candidate) == 1
    assert candidate[0]["summary"]["signal_id"] == 1


async def test_expires_pending_signals_using_utc_normalized_timestamps(store: EaStore) -> None:
    # 镜像 TS:expires_at 用 +08:00 时区,expire 判断按 UTC 归一
    await store.save_pending_signal(
        {
            "account_id": "acc-1",
            "symbol": "XAUUSD",
            "side": "buy",
            "score": 87,
            "strategy": "momentum_scalp",
            "status": "pending",
            "created_at": "2026-04-13T07:59:00.000Z",
            "expires_at": "2026-04-13T16:00:00+08:00",
        }
    )
    await store.save_pending_signal(
        {
            "account_id": "acc-1",
            "symbol": "XAUUSD",
            "side": "sell",
            "score": 74,
            "strategy": "range",
            "status": "pending",
            "created_at": "2026-04-13T08:00:00.000Z",
            "expires_at": "2026-04-13T16:02:00+08:00",
        }
    )

    assert await store.expire_pending_signals("2026-04-13T08:00:01.000Z") == 1
    signals = await store.get_pending_signals("acc-1", "XAUUSD")
    assert [s["id"] for s in signals] == [2]
    assert signals[0]["status"] == "pending"


async def test_updates_and_expires_pending_signal_arbitration_state(store: EaStore) -> None:
    await store.save_pending_signal(
        {
            "account_id": "acc-1",
            "symbol": "XAUUSD",
            "side": "buy",
            "score": 9,
            "strategy": "pullback",
            "status": "pending",
            "created_at": "2026-04-13T08:00:00.000Z",
            "expires_at": "2026-04-13T08:10:00.000Z",
        }
    )
    await store.save_pending_signal(
        {
            "account_id": "acc-1",
            "symbol": "XAUUSD",
            "side": "sell",
            "score": 7,
            "strategy": "range",
            "status": "pending",
            "created_at": "2026-04-13T08:01:00.000Z",
            "expires_at": "2026-04-13T08:02:00.000Z",
        }
    )

    assert await store.update_pending_signal_arbitration(1, "approved", "manual ok") is True
    signals = await store.get_pending_signals("acc-1", "XAUUSD")
    assert [s["id"] for s in signals] == [2]  # id=1 已仲裁 → 不在 pending 列表

    assert await store.expire_pending_signals("2026-04-13T08:03:00.000Z") == 1
    assert await store.get_pending_signals("acc-1", "XAUUSD") == []
    assert await store.update_pending_signal_arbitration(999, "approved", "missing") is False


# ---------------------------------------------------------------- Token
async def test_stores_and_deletes_api_token_records(store: EaStore) -> None:
    await store.save_api_token({"token": "tok-1", "name": "alice", "is_admin": False, "accounts": ["acc-1", "acc-2"]})
    await store.save_api_token({"token": "tok-admin", "name": "admin", "is_admin": True, "accounts": []})

    tokens = await store.list_api_tokens()
    assert {t["token"] for t in tokens} == {"tok-1", "tok-admin"}
    alice = next(t for t in tokens if t["token"] == "tok-1")
    assert alice["accounts"] == ["acc-1", "acc-2"]
    assert alice["is_admin"] is False

    # 更新:替换 accounts
    await store.save_api_token({"token": "tok-1", "name": "alice", "is_admin": False, "accounts": ["acc-3"]})
    tokens = await store.list_api_tokens()
    alice = next(t for t in tokens if t["token"] == "tok-1")
    assert alice["accounts"] == ["acc-3"]

    assert await store.delete_api_token("tok-1") is True
    assert await store.delete_api_token("tok-1") is False
    assert {t["token"] for t in await store.list_api_tokens()} == {"tok-admin"}


# ---------------------------------------------------------------- 已平仓/日权益
def _closed_trade(ticket: int, strategy: str, side: str, profit: float) -> EaRecord:
    return {
        "account_id": "acc-1",
        "ticket": ticket,
        "magic": 20250231 if strategy == "pullback" else 20250238,
        "symbol": "XAUUSD",
        "strategy": strategy,
        "side": side,
        "open_price": 100,
        "close_price": 90,
        "lots": 0.1,
        "profit": profit,
        "open_time": "2026-08-01T00:00:00Z",
        "close_time": "2026-08-01T01:00:00Z",
        "duration_min": 60,
    }


async def test_closed_trade_stats(store: EaStore) -> None:
    await store.save_closed_trade(_closed_trade(1, "pullback", "BUY", 100))
    await store.save_closed_trade(_closed_trade(2, "pullback", "BUY", -50))
    await store.save_closed_trade(_closed_trade(3, "ai_signal", "SELL", -200))

    stats = await store.get_closed_trade_stats("acc-1")
    assert [s["strategy"] for s in stats] == ["pullback", "ai_signal"]  # total desc
    pullback = stats[0]
    assert pullback["total"] == 2
    assert pullback["wins"] == 1
    assert pullback["losses"] == 1
    assert pullback["win_rate"] == 0.5
    assert pullback["total_profit"] == 50
    assert pullback["avg_profit"] == 25
    assert pullback["expectancy"] == 0.5 * 100 + 0.5 * (-50)
    assert pullback["avg_duration_min"] == 60

    # 同 ticket 更新(upsert)
    await store.save_closed_trade(_closed_trade(1, "pullback", "BUY", 50))
    stats = await store.get_closed_trade_stats("acc-1")
    pullback = next(s for s in stats if s["strategy"] == "pullback")
    assert pullback["total"] == 2


async def test_daily_start_equity_first_write_wins(store: EaStore) -> None:
    assert await store.get_daily_start_equity("acc-1", "2026-08-22") is None
    await store.save_daily_start_equity("acc-1", "2026-08-22", 1000.0)
    await store.save_daily_start_equity("acc-1", "2026-08-22", 999.0)
    assert await store.get_daily_start_equity("acc-1", "2026-08-22") == 1000.0
    assert await store.get_daily_start_equity("acc-1", "2026-08-23") is None


async def test_list_commands_orders_by_created_at(store: EaStore) -> None:
    await store.enqueue_command("acc-1", command("acc-1", command_id="list-1"))
    await store.enqueue_command("acc-1", command("acc-1", command_id="list-2"))
    commands = await store.list_commands("acc-1")
    assert len(commands) == 2
    assert [c["command_id"] for c in commands] == ["list-1", "list-2"]
