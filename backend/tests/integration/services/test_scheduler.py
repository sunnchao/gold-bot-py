"""SchedulerService 集成测试(镜像 apps/app-server/src/services/scheduler/service.spec.ts)。

28 个用例逐条 1:1 映射,见每处 docstring。console.log/console.warn 断言经
pytest capsys 的 stdout/stderr 捕获镜像 vi.spyOn(console, ...)。测试直接触发
job 本体(enqueue_analysis / enqueue_position_review),不启动持久调度器。
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

from backend.persistence.store import create_in_memory_store
from backend.services.analysis import AnalysisService
from backend.services.scheduler import SchedulerService

ACCOUNT = "90011087"


class FakeAnalysis:
    """镜像 spec 中 `{ analyzeAccountSymbol() {...} } as never` 的内联 mock。"""

    def __init__(self, handler=None):
        self._handler = handler
        self.calls = 0
        self.persist_calls: list[tuple[str, str, list[dict[str, Any]] | None]] = []

    async def analyze_account_symbol(self, account_id: str, symbol: str) -> dict[str, Any]:
        self.calls += 1
        if self._handler is not None:
            return self._handler()
        return {"replay": {"signal": None, "position_commands": None}}

    async def persist_position_states(
        self,
        account_id: str,
        symbol: str,
        states: list[dict[str, Any]] | None,
    ) -> None:
        self.persist_calls.append((account_id, symbol, states))


class CommandLifecycleService:
    """镜像 TS command-lifecycle/service.ts 的 acceptCandidate/reconcile。

    M5-B 将提供正式移植;此处为跑通 scheduler 用例的忠实临时镜像。
    """

    def __init__(self, store, default_runtime_mode: str = "oracle", shadow=None):
        self.store = store
        self.default_runtime_mode = default_runtime_mode
        self.shadow = shadow

    async def accept_candidate(self, account_id: str, candidate: dict[str, Any]) -> dict[str, Any]:
        stored = await self.store.save_command_candidate(account_id, candidate)
        mode = self._resolve_runtime_mode(await self.store.get_runtime_mode(account_id))
        if mode == "cutover":
            await self.store.promote_command(stored["command_id"])
        else:
            await self.store.demote_command_to_shadow_only(stored["command_id"])
        resolved = (await self.store.get_command(stored["command_id"])) or stored
        symbol = (
            resolved.get("symbol")
            if isinstance(resolved.get("symbol"), str) and len(resolved.get("symbol")) > 0
            else "XAUUSD"
        )
        source = self._shadow_source_for_command(resolved.get("source"))
        if self.shadow is not None:
            await self.shadow.record_runtime_snapshot(
                {
                    "account_id": account_id,
                    "symbol": symbol,
                    "source": source,
                    "command": resolved,
                    "created_at": resolved.get("created_at"),
                }
            )
        await self.store.record_shadow_comparison(
            {
                "account_id": account_id,
                "symbol": symbol,
                "protocol_ok": True,
                "signal_drift": False,
                "command_drift": False,
                "oracle_compared": False,
                "source": source,
                "created_at": resolved.get("created_at"),
            }
        )
        return resolved

    async def reconcile(
        self,
        account_id: str,
        command_id: str,
        result: str,
        ticket: int | None = None,
        error_text: str = "",
        created_at: str | None = None,
    ) -> bool:
        ok = await self.store.reconcile_command_result(account_id, command_id, result, ticket, error_text, created_at)
        if (error_text or "").strip() == "4108":
            target_ticket = self._extract_ticket_from_command_id(command_id)
            symbol = self._extract_symbol_from_command_id(command_id)
            if target_ticket > 0 and len(symbol) > 0:
                try:
                    states = await self.store.load_position_states(account_id, symbol)
                    keep_tickets = [
                        int(state.get("ticket", 0))
                        for state in states
                        if int(state.get("ticket", 0)) > 0 and int(state.get("ticket", 0)) != target_ticket
                    ]
                    await self.store.delete_stale_position_states(account_id, symbol, keep_tickets)
                except Exception:
                    pass
        return ok

    def _resolve_runtime_mode(self, stored_mode: str) -> str:
        if stored_mode == "oracle" and self.default_runtime_mode in ("shadow", "cutover"):
            return self.default_runtime_mode
        return stored_mode

    @staticmethod
    def _shadow_source_for_command(source: Any) -> str:
        if source == "live_strategy":
            return "ea_analysis"
        if source in ("ai_stop_loss", "position_manager"):
            return "position_review"
        return "ai_result" if source in ("ai_risk_alert", "ai_approve") else str(source or "")

    @staticmethod
    def _extract_ticket_from_command_id(command_id: str) -> int:
        parts = command_id.split("_")
        if len(parts) >= 4 and parts[0] == "pm":
            try:
                value = int(parts[3])
            except ValueError:
                value = 0
            if value > 0:
                return value
        return 0

    @staticmethod
    def _extract_symbol_from_command_id(command_id: str) -> str:
        parts = command_id.split("_")
        if len(parts) >= 3 and parts[0] == "pm":
            return parts[2]
        return ""


# --------------------------------------------------------------------------- 工具


async def save_tradeable_heartbeat(store) -> None:
    await store.save_heartbeat({"account_id": ACCOUNT, "market_open": True, "is_trade_allowed": True})


def flat_h1_bars(count: int) -> list[dict]:
    return [
        {
            "time": f"2026-04-13T{index:02d}:00:00.000Z",
            "open": 3340,
            "high": 3341,
            "low": 3339,
            "close": 3340,
        }
        for index in range(count)
    ]


def gbp_jpy_h1_bars_without_atr(count: int) -> list[dict]:
    return [
        {
            "time": f"2026-04-13T{index:02d}:00:00.000Z",
            "open": close - 0.01,
            "high": close + 0.25,
            "low": close - 0.25,
            "close": close,
            "volume": 1000 + index,
        }
        for index in range(count)
        for close in [219 + index * 0.02]
    ]


def assert_matches(subset: dict, record: dict) -> None:
    for key, value in subset.items():
        assert record[key] == value


# --------------------------------------------------------------------------- 主链路


async def test_publishes_replay_signals_through_the_command_lifecycle_for_cutover_accounts() -> None:
    store = create_in_memory_store()
    await store.set_runtime_mode(ACCOUNT, "cutover")
    await save_tradeable_heartbeat(store)
    await store.save_tick({"account_id": ACCOUNT, "symbol": "XAUUSD", "bid": 3335.9, "ask": 3336.1})
    await store.save_bars(
        {
            "account_id": ACCOUNT,
            "symbol": "XAUUSD",
            "timeframe": "H1",
            "bars": [
                {"time": "2026-04-13T07:00:00.000Z", "open": 3335, "high": 3337, "low": 3333, "close": 3336, "atr": 2}
            ],
        }
    )
    command_lifecycle = CommandLifecycleService(store)
    scheduler = SchedulerService(
        FakeAnalysis(
            lambda: {
                "replay": {
                    "signal": {
                        "strategy": "pullback",
                        "side": "BUY",
                        "entry": 3335,
                        "stop_loss": 3330,
                        "tp1": 3345,
                        "tp2": 3355,
                        "score": 8,
                        "atr": 2,
                    },
                    "position_commands": None,
                }
            }
        ),
        command_lifecycle,
        None,
        store,
        lambda: "2026-04-13T08:00:00.000Z",
    )

    await scheduler.enqueue_analysis(ACCOUNT, "XAUUSD", "H1")
    await scheduler.enqueue_analysis(ACCOUNT, "XAUUSD", "H1")

    commands = await store.list_commands(ACCOUNT)
    assert len(commands) == 1
    expected_expiration = int(dt.datetime(2026, 4, 13, 8, 0, tzinfo=dt.UTC).timestamp()) + 24 * 60 * 60
    assert_matches(
        {
            "source": "live_strategy",
            "status": "queued",
            "strategy": "pullback",
            "trigger_key": "H1:2026-04-13T07:00:00.000Z",
            "analysis_mode": "bars",
            "order_type": "BUY_LIMIT",
            "expiration": expected_expiration,
        },
        commands[0],
    )
    assert re.fullmatch(r"live_[0-9a-f]{16}", commands[0]["command_id"])
    assert commands[0]["decision_id"] == commands[0]["command_id"]


async def test_skips_replay_analysis_for_non_live_strategy_timeframes() -> None:
    store = create_in_memory_store()
    await store.set_runtime_mode(ACCOUNT, "cutover")
    analysis = FakeAnalysis(
        lambda: {
            "replay": {
                "signal": {
                    "strategy": "pullback",
                    "side": "BUY",
                    "entry": 3335.7,
                    "stop_loss": 3330,
                    "tp1": 3345,
                    "tp2": 3355,
                    "score": 8,
                },
                "position_commands": None,
            }
        }
    )
    scheduler = SchedulerService(analysis, CommandLifecycleService(store), None, store)

    await scheduler.enqueue_analysis(ACCOUNT, "XAUUSD", "D1")

    assert analysis.calls == 0
    assert await store.list_commands(ACCOUNT) == []


async def test_skips_replay_analysis_when_ea_runtime_is_not_tradeable() -> None:
    store = create_in_memory_store()
    await store.set_runtime_mode(ACCOUNT, "cutover")
    await store.save_heartbeat({"account_id": ACCOUNT, "market_open": False, "is_trade_allowed": True})
    analysis = FakeAnalysis(
        lambda: {
            "replay": {
                "signal": {
                    "strategy": "pullback",
                    "side": "BUY",
                    "entry": 3335.7,
                    "stop_loss": 3330,
                    "tp1": 3345,
                    "tp2": 3355,
                    "score": 8,
                },
                "position_commands": None,
            }
        }
    )
    scheduler = SchedulerService(analysis, CommandLifecycleService(store), None, store)

    await scheduler.enqueue_analysis(ACCOUNT, "XAUUSD", "H1")

    assert analysis.calls == 0
    assert await store.list_commands(ACCOUNT) == []


async def test_skips_replay_analysis_when_heartbeat_is_missing() -> None:
    store = create_in_memory_store()
    await store.set_runtime_mode(ACCOUNT, "cutover")
    analysis = FakeAnalysis(
        lambda: {
            "replay": {
                "signal": {
                    "strategy": "pullback",
                    "side": "BUY",
                    "entry": 3335.7,
                    "stop_loss": 3330,
                    "tp1": 3345,
                    "tp2": 3355,
                    "score": 8,
                },
                "position_commands": None,
            }
        }
    )
    scheduler = SchedulerService(analysis, CommandLifecycleService(store), None, store)

    await scheduler.enqueue_analysis(ACCOUNT, "XAUUSD", "H1")

    assert analysis.calls == 0
    assert await store.list_commands(ACCOUNT) == []


async def test_skips_position_review_when_heartbeat_is_missing() -> None:
    store = create_in_memory_store()
    await store.set_runtime_mode(ACCOUNT, "cutover")
    analysis = FakeAnalysis(
        lambda: {
            "replay": {
                "signal": None,
                "position_commands": [{"action": "CLOSE", "ticket": 777, "lots": 0.04, "reason": "TP1_2.2ATR"}],
            }
        }
    )
    scheduler = SchedulerService(analysis, CommandLifecycleService(store), None, store)

    await scheduler.enqueue_position_review(ACCOUNT, "XAUUSD")

    assert analysis.calls == 0
    assert await store.list_commands(ACCOUNT) == []


# --------------------------------------------------------------------------- 持仓管理命令


async def test_queues_replay_position_manager_commands_during_position_review() -> None:
    store = create_in_memory_store()
    await store.set_runtime_mode(ACCOUNT, "cutover")
    await save_tradeable_heartbeat(store)
    await store.save_positions(
        {
            "account_id": ACCOUNT,
            "symbol": "XAUUSD",
            "positions": [
                {
                    "ticket": 777,
                    "symbol": "XAUUSD",
                    "type": "BUY",
                    "open_price": 3320,
                    "lots": 0.1,
                    "sl": 3325,
                    "tp": 3345,
                }
            ],
        }
    )
    command_lifecycle = CommandLifecycleService(store)
    now = {"value": "2026-04-13T08:00:00.000Z"}
    scheduler = SchedulerService(
        FakeAnalysis(
            lambda: {
                "replay": {
                    "signal": None,
                    "position_commands": [
                        {"action": "MODIFY", "ticket": 777, "new_sl": 3330, "reason": "breakeven_2.2ATR"},
                        {"action": "CLOSE", "ticket": 777, "lots": 0.04, "reason": "TP1_2.2ATR"},
                    ],
                }
            }
        ),
        command_lifecycle,
        None,
        store,
        lambda: now["value"],
    )

    await scheduler.enqueue_position_review(ACCOUNT, "XAUUSD")
    now["value"] = "2026-04-13T08:00:30.000Z"
    await scheduler.enqueue_position_review(ACCOUNT, "XAUUSD")

    commands = await store.list_commands(ACCOUNT)
    assert len(commands) == 2
    assert_matches(
        {
            "command_id": "pm_90011087_XAUUSD_777_modify_breakeven_2_2ATR_202604130800",
            "action": "MODIFY",
            "source": "position_manager",
            "status": "queued",
            "symbol": "XAUUSD",
            "ticket": 777,
            "new_sl": 3330,
            "sl": 3330,
            "old_sl": 3325,
            "tp": 3345,
            "open_price": 3320,
            "distance": 5,
            "reason": "breakeven_2.2ATR",
            "trigger_time": "2026-04-13T08:00:00.000Z",
            "analysis_mode": "positions",
        },
        commands[0],
    )
    assert re.fullmatch(r"pm_90011087_XAUUSD_777_modify_breakeven_2_2ATR_202604130800", commands[0]["command_id"])
    assert_matches(
        {
            "command_id": "pm_90011087_XAUUSD_777_close_TP1_2_2ATR_202604130800",
            "action": "CLOSE",
            "source": "position_manager",
            "status": "queued",
            "symbol": "XAUUSD",
            "ticket": 777,
            "lots": 0.04,
            "reason": "TP1_2.2ATR",
            "trigger_time": "2026-04-13T08:00:00.000Z",
            "analysis_mode": "positions",
        },
        commands[1],
    )
    assert re.fullmatch(r"pm_90011087_XAUUSD_777_close_TP1_2_2ATR_202604130800", commands[1]["command_id"])


async def test_skips_position_manager_modify_commands_when_the_new_stop_equals_the_current_stop() -> None:
    store = create_in_memory_store()
    await store.set_runtime_mode(ACCOUNT, "cutover")
    await save_tradeable_heartbeat(store)
    await store.save_positions(
        {
            "account_id": ACCOUNT,
            "symbol": "XAUUSD",
            "positions": [
                {
                    "ticket": 778,
                    "symbol": "XAUUSD",
                    "type": "BUY",
                    "open_price": 3320,
                    "lots": 0.1,
                    "sl": 3330,
                    "tp": 3345,
                }
            ],
        }
    )
    scheduler = SchedulerService(
        FakeAnalysis(
            lambda: {
                "replay": {
                    "signal": None,
                    "position_commands": [
                        {"action": "MODIFY", "ticket": 778, "new_sl": 3330, "reason": "breakeven_2.2ATR"}
                    ],
                }
            }
        ),
        CommandLifecycleService(store),
        None,
        store,
        lambda: "2026-04-13T08:00:00.000Z",
    )

    await scheduler.enqueue_position_review(ACCOUNT, "XAUUSD")

    assert await store.list_commands(ACCOUNT) == []


async def test_skips_position_manager_modify_when_stop_is_on_the_wrong_side_of_market_error_130() -> None:
    store = create_in_memory_store()
    await store.set_runtime_mode(ACCOUNT, "cutover")
    await save_tradeable_heartbeat(store)
    # BUY open 218.707, group reanchor wants 218.735, but market already below both → invalid SL
    await store.save_tick({"account_id": ACCOUNT, "symbol": "GBPJPY", "bid": 218.30, "ask": 218.32})
    await store.save_positions(
        {
            "account_id": ACCOUNT,
            "symbol": "GBPJPY",
            "positions": [
                {
                    "ticket": 42370061,
                    "symbol": "GBPJPY",
                    "type": "BUY",
                    "open_price": 218.707,
                    "lots": 0.02,
                    "sl": 217.7,
                    "tp": 219.18,
                }
            ],
        }
    )
    scheduler = SchedulerService(
        FakeAnalysis(
            lambda: {
                "replay": {
                    "signal": None,
                    "position_commands": [
                        {
                            "action": "MODIFY",
                            "ticket": 42370061,
                            "new_sl": 218.73475,
                            "reason": "group_adverse_reanchor_BUY",
                        }
                    ],
                }
            }
        ),
        CommandLifecycleService(store),
        None,
        store,
        lambda: "2026-07-17T06:13:55.000Z",
    )

    await scheduler.enqueue_position_review(ACCOUNT, "GBPJPY")
    assert await store.list_commands(ACCOUNT) == []


async def test_still_queues_position_manager_modify_when_stop_stays_valid_vs_market() -> None:
    store = create_in_memory_store()
    await store.set_runtime_mode(ACCOUNT, "cutover")
    await save_tradeable_heartbeat(store)
    # GBPJPY STOPLEVEL 比例为 0.0012(Phase 5.3),SL 距市价需 > ~0.26
    await store.save_tick({"account_id": ACCOUNT, "symbol": "GBPJPY", "bid": 219.10, "ask": 219.12})
    await store.save_positions(
        {
            "account_id": ACCOUNT,
            "symbol": "GBPJPY",
            "positions": [
                {
                    "ticket": 42370061,
                    "symbol": "GBPJPY",
                    "type": "BUY",
                    "open_price": 218.707,
                    "lots": 0.02,
                    "sl": 217.7,
                    "tp": 219.18,
                }
            ],
        }
    )
    scheduler = SchedulerService(
        FakeAnalysis(
            lambda: {
                "replay": {
                    "signal": None,
                    "position_commands": [
                        {
                            "action": "MODIFY",
                            "ticket": 42370061,
                            "new_sl": 218.73475,
                            "reason": "group_adverse_reanchor_BUY",
                        }
                    ],
                }
            }
        ),
        CommandLifecycleService(store),
        None,
        store,
        lambda: "2026-07-17T06:13:55.000Z",
    )

    await scheduler.enqueue_position_review(ACCOUNT, "GBPJPY")
    commands = await store.list_commands(ACCOUNT)
    assert len(commands) == 1
    assert_matches(
        {
            "action": "MODIFY",
            "source": "position_manager",
            "ticket": 42370061,
            "new_sl": 218.73475,
            "reason": "group_adverse_reanchor_BUY",
        },
        commands[0],
    )


async def test_does_not_queue_position_manager_close_commands_for_missing_tickets() -> None:
    store = create_in_memory_store()
    await store.set_runtime_mode(ACCOUNT, "cutover")
    await save_tradeable_heartbeat(store)
    await store.save_positions({"account_id": ACCOUNT, "symbol": "XAUUSD", "positions": []})
    scheduler = SchedulerService(
        FakeAnalysis(
            lambda: {
                "replay": {
                    "signal": None,
                    "position_commands": [{"action": "CLOSE", "ticket": 779, "lots": 0.04, "reason": "TP1_2.2ATR"}],
                }
            }
        ),
        CommandLifecycleService(store),
        None,
        store,
        lambda: "2026-04-13T08:00:00.000Z",
    )

    await scheduler.enqueue_position_review(ACCOUNT, "XAUUSD")

    assert await store.list_commands(ACCOUNT) == []


async def test_queues_cancel_pending_and_skips_close_for_pending_orders() -> None:
    store = create_in_memory_store()
    await store.set_runtime_mode(ACCOUNT, "cutover")
    await save_tradeable_heartbeat(store)
    await store.save_positions(
        {
            "account_id": ACCOUNT,
            "symbol": "XAGUSD",
            "positions": [
                {
                    "ticket": 42275433,
                    "symbol": "XAGUSD",
                    "type": "SELL_LIMIT",
                    "order_class": "pending",
                    "open_price": 59.5,
                    "lots": 0.05,
                    "sl": 59.5,
                    "tp": 58.36,
                }
            ],
        }
    )
    command_lifecycle = CommandLifecycleService(store)
    scheduler = SchedulerService(
        FakeAnalysis(
            lambda: {
                "replay": {
                    "signal": None,
                    "position_commands": [
                        {"action": "CLOSE", "ticket": 42275433, "lots": 0.05, "reason": "trail_tp2_dd2.1"},
                        {"action": "CANCEL_PENDING", "ticket": 42275433, "reason": "pending_tp_reached_58.36"},
                    ],
                }
            }
        ),
        command_lifecycle,
        None,
        store,
        lambda: "2026-04-13T08:00:00.000Z",
    )

    await scheduler.enqueue_position_review(ACCOUNT, "XAGUSD")

    commands = await store.list_commands(ACCOUNT)
    assert len(commands) == 1
    assert_matches(
        {
            "action": "CANCEL_PENDING",
            "source": "position_manager",
            "status": "queued",
            "symbol": "XAGUSD",
            "ticket": 42275433,
            "reason": "pending_tp_reached_58.36",
        },
        commands[0],
    )


async def test_publishes_position_triggered_replay_signals_with_positions_analysis_mode() -> None:
    store = create_in_memory_store()
    await store.set_runtime_mode(ACCOUNT, "cutover")
    await save_tradeable_heartbeat(store)
    await store.save_tick({"account_id": ACCOUNT, "symbol": "XAUUSD", "bid": 3335.9, "ask": 3336.1})
    await store.save_bars(
        {
            "account_id": ACCOUNT,
            "symbol": "XAUUSD",
            "timeframe": "H1",
            "bars": [
                {"time": "2026-04-13T07:00:00.000Z", "open": 3335, "high": 3337, "low": 3333, "close": 3336, "atr": 2}
            ],
        }
    )
    scheduler = SchedulerService(
        FakeAnalysis(
            lambda: {
                "replay": {
                    "signal": {
                        "strategy": "pullback",
                        "side": "BUY",
                        "entry": 3335,
                        "stop_loss": 3330,
                        "tp1": 3345,
                        "tp2": 3355,
                        "score": 8,
                        "atr": 2,
                    },
                    "position_commands": None,
                }
            }
        ),
        CommandLifecycleService(store),
        None,
        store,
        lambda: "2026-04-13T08:00:00.000Z",
    )

    await scheduler.enqueue_position_review(ACCOUNT, "XAUUSD")
    await scheduler.enqueue_position_review(ACCOUNT, "XAUUSD")

    commands = await store.list_commands(ACCOUNT)
    assert len(commands) == 1
    assert_matches(
        {
            "source": "live_strategy",
            "action": "SIGNAL",
            "analysis_mode": "positions",
            "trigger_key": "H1:2026-04-13T07:00:00.000Z",
        },
        commands[0],
    )


# --------------------------------------------------------------------------- AI 止损


async def test_queues_gbpjpy_ai_stop_loss_lock_profit_commands_with_atr_derived_from_h1_ohlc() -> None:
    store = create_in_memory_store()
    await store.set_runtime_mode(ACCOUNT, "cutover")
    await save_tradeable_heartbeat(store)
    await store.save_tick({"account_id": ACCOUNT, "symbol": "GBPJPY", "bid": 219.99, "ask": 220.01})
    await store.save_bars(
        {"account_id": ACCOUNT, "symbol": "GBPJPY", "timeframe": "H1", "bars": gbp_jpy_h1_bars_without_atr(20)}
    )
    await store.save_positions(
        {
            "account_id": ACCOUNT,
            "symbol": "GBPJPY",
            "positions": [
                {
                    "ticket": 123456,
                    "symbol": "GBPJPY",
                    "type": "BUY",
                    "open_price": 218.4,
                    "lots": 0.2,
                    "sl": 218.7,
                    "tp": 222,
                }
            ],
        }
    )
    await store.save_ai_result(
        ACCOUNT, "GBPJPY", {"suggested_sl": 219.77, "trade_plan": {"decision_id": "tpv1_modify_sl"}}
    )
    scheduler = SchedulerService(
        FakeAnalysis(lambda: {"replay": {"signal": None, "position_commands": None}}),
        CommandLifecycleService(store),
        None,
        store,
        lambda: "2026-04-13T08:02:00.000Z",
    )

    await scheduler.enqueue_position_review(ACCOUNT, "GBPJPY")
    await scheduler.enqueue_position_review(ACCOUNT, "GBPJPY")

    commands = await store.list_commands(ACCOUNT)
    assert len(commands) == 1
    assert re.fullmatch(r"mod_[0-9a-f]{16}", commands[0]["command_id"])
    assert_matches(
        {
            "action": "MODIFY",
            "source": "ai_stop_loss",
            "status": "queued",
            "symbol": "GBPJPY",
            "ticket": 123456,
            "new_sl": 219.77,
            "sl": 219.77,
            "tp": 222,
            "old_sl": 218.7,
            "decision_id": "tpv1_modify_sl",
            "trigger_time": "2026-04-13T08:02:00.000Z",
            "analysis_mode": "positions",
        },
        commands[0],
    )
    assert abs(commands[0]["distance"] - 1.07) < 5e-11
    assert abs(commands[0]["atr"] - 0.5) < 5e-11


async def test_queues_ai_stop_loss_commands_for_any_symbol_reported_by_the_ea() -> None:
    """白名单已移除:AI 止损跟踪对 EA 上报品种(XAUUSD 等非金丝雀品种)一律生效。"""
    store = create_in_memory_store()
    await store.set_runtime_mode(ACCOUNT, "cutover")
    await save_tradeable_heartbeat(store)
    await store.save_tick({"account_id": ACCOUNT, "symbol": "XAUUSD", "bid": 3339.9, "ask": 3340.1})
    await store.save_bars(
        {
            "account_id": ACCOUNT,
            "symbol": "XAUUSD",
            "timeframe": "H1",
            "bars": [
                {"time": "2026-04-13T07:00:00.000Z", "open": 3338, "high": 3341, "low": 3337, "close": 3340, "atr": 2}
            ],
        }
    )
    await store.save_positions(
        {
            "account_id": ACCOUNT,
            "symbol": "XAUUSD",
            "positions": [
                {
                    "ticket": 123457,
                    "symbol": "XAUUSD",
                    "type": "BUY",
                    "open_price": 3335,
                    "lots": 0.2,
                    "sl": 3336,
                    "tp": 3355,
                }
            ],
        }
    )
    await store.save_ai_result(
        ACCOUNT, "XAUUSD", {"suggested_sl": 3338.8, "trade_plan": {"decision_id": "tpv1_modify_xau"}}
    )
    scheduler = SchedulerService(
        FakeAnalysis(lambda: {"replay": {"signal": None, "position_commands": None}}),
        CommandLifecycleService(store),
        None,
        store,
        lambda: "2026-04-13T08:02:00.000Z",
    )

    await scheduler.enqueue_position_review(ACCOUNT, "XAUUSD")
    commands = await store.list_commands(ACCOUNT)
    assert len(commands) == 1
    assert_matches(
        {
            "action": "MODIFY",
            "source": "ai_stop_loss",
            "status": "queued",
            "symbol": "XAUUSD",
            "ticket": 123457,
            "new_sl": 3338.8,
            "sl": 3338.8,
            "tp": 3355,
            "old_sl": 3336,
            "decision_id": "tpv1_modify_xau",
            "trigger_time": "2026-04-13T08:02:00.000Z",
            "analysis_mode": "positions",
        },
        commands[0],
    )
    assert abs(commands[0]["distance"] - 2.8) < 5e-11
    assert abs(commands[0]["atr"] - 2) < 5e-11


async def test_does_not_queue_buy_ai_stop_loss_commands_that_loosen_below_the_current_stop(capsys) -> None:
    store = create_in_memory_store()
    await store.set_runtime_mode(ACCOUNT, "cutover")
    await save_tradeable_heartbeat(store)
    await store.save_tick({"account_id": ACCOUNT, "symbol": "GBPJPY", "bid": 219.99, "ask": 220.01})
    await store.save_bars(
        {
            "account_id": ACCOUNT,
            "symbol": "GBPJPY",
            "timeframe": "H1",
            "bars": [
                {
                    "time": "2026-04-13T07:00:00.000Z",
                    "open": 219.8,
                    "high": 220.2,
                    "low": 219.7,
                    "close": 220,
                    "atr": 0.5,
                }
            ],
        }
    )
    await store.save_positions(
        {
            "account_id": ACCOUNT,
            "symbol": "GBPJPY",
            "positions": [
                {
                    "ticket": 123458,
                    "symbol": "GBPJPY",
                    "type": "BUY",
                    "open_price": 218.4,
                    "lots": 0.2,
                    "sl": 219.2,
                    "tp": 222,
                }
            ],
        }
    )
    await store.save_ai_result(
        ACCOUNT, "GBPJPY", {"suggested_sl": 219.0, "trade_plan": {"decision_id": "loosen_rejected"}}
    )
    scheduler = SchedulerService(
        FakeAnalysis(lambda: {"replay": {"signal": None, "position_commands": None}}),
        CommandLifecycleService(store),
        None,
        store,
        lambda: "2026-04-13T08:02:00.000Z",
    )
    await scheduler.enqueue_position_review(ACCOUNT, "GBPJPY")
    capsys.readouterr()

    assert await store.list_commands(ACCOUNT) == []


async def test_suppresses_ai_stop_loss_modify_commands_for_the_same_ticket_inside_the_five_minute_cooldown() -> None:
    store = create_in_memory_store()
    await store.set_runtime_mode(ACCOUNT, "cutover")
    await save_tradeable_heartbeat(store)
    await store.save_tick({"account_id": ACCOUNT, "symbol": "GBPJPY", "bid": 219.99, "ask": 220.01})
    await store.save_bars(
        {
            "account_id": ACCOUNT,
            "symbol": "GBPJPY",
            "timeframe": "H1",
            "bars": [
                {
                    "time": "2026-04-13T07:00:00.000Z",
                    "open": 219.8,
                    "high": 220.2,
                    "low": 219.7,
                    "close": 220,
                    "atr": 0.5,
                }
            ],
        }
    )
    await store.save_positions(
        {
            "account_id": ACCOUNT,
            "symbol": "GBPJPY",
            "positions": [
                {
                    "ticket": 123456,
                    "symbol": "GBPJPY",
                    "type": "BUY",
                    "open_price": 218.4,
                    "lots": 0.2,
                    "sl": 218.7,
                    "tp": 222,
                }
            ],
        }
    )
    await store.save_ai_result(
        ACCOUNT, "GBPJPY", {"suggested_sl": 219.77, "trade_plan": {"decision_id": "tpv1_modify_sl"}}
    )
    now = {"value": "2026-04-13T08:02:00.000Z"}
    scheduler = SchedulerService(
        FakeAnalysis(lambda: {"replay": {"signal": None, "position_commands": None}}),
        CommandLifecycleService(store),
        None,
        store,
        lambda: now["value"],
    )
    await scheduler.enqueue_position_review(ACCOUNT, "GBPJPY")
    now["value"] = "2026-04-13T08:04:00.000Z"
    await scheduler.enqueue_position_review(ACCOUNT, "GBPJPY")
    now["value"] = "2026-04-13T08:07:00.000Z"
    await scheduler.enqueue_position_review(ACCOUNT, "GBPJPY")

    commands = [command for command in await store.list_commands(ACCOUNT) if command["source"] == "ai_stop_loss"]
    assert len(commands) == 2
    assert [command["trigger_time"] for command in commands] == [
        "2026-04-13T08:02:00.000Z",
        "2026-04-13T08:07:00.000Z",
    ]


async def test_hydrates_existing_position_manager_state_without_persisting_replay_only_state() -> None:
    store = create_in_memory_store()
    await store.set_runtime_mode(ACCOUNT, "cutover")
    await save_tradeable_heartbeat(store)
    await store.save_tick({"account_id": ACCOUNT, "symbol": "XAUUSD", "bid": 3343.1, "ask": 3343.2})
    await store.save_bars({"account_id": ACCOUNT, "symbol": "XAUUSD", "timeframe": "H1", "bars": flat_h1_bars(15)})
    await store.save_positions(
        {
            "account_id": ACCOUNT,
            "symbol": "XAUUSD",
            "positions": [
                {"ticket": 202, "symbol": "XAUUSD", "type": "BUY", "open_price": 3340, "lots": 0.5, "sl": 3340}
            ],
        }
    )
    await store.save_position_state(
        ACCOUNT,
        "XAUUSD",
        {
            "ticket": 202,
            "tp1_hit": True,
            "tp2_hit": False,
            "max_profit_atr": 1.6,
            "be_moved": True,
            "be_trigger_atr": 1.5,
            "best_sl": 0,
            "open_time": "2026-04-13T06:00:00.000Z",
            "last_modify_time": "2026-04-13T07:00:00.000Z",
            "add_on_count": 0,
            "last_add_on_time": "",
            "last_add_on_price": 0,
            "group_id": "",
            "group_avg_entry": 0,
            "group_best_sl": 0,
        },
    )
    analysis = AnalysisService(store, lambda: "2026-04-13T08:00:00.000Z")
    scheduler = SchedulerService(analysis, CommandLifecycleService(store), None, store)

    await scheduler.enqueue_position_review(ACCOUNT, "XAUUSD")

    assert await store.list_commands(ACCOUNT) == []
    states = await store.load_position_states(ACCOUNT, "XAUUSD")
    assert_matches(
        {
            "ticket": 202,
            "tp1_hit": True,
            "be_moved": True,
            "open_time": "2026-04-13T06:00:00.000Z",
        },
        states[0],
    )


# --------------------------------------------------------------------------- 市况过滤


async def test_drops_replay_signals_when_spread_exceeds_the_market_filter_limit(capsys) -> None:
    store = create_in_memory_store()
    await store.set_runtime_mode(ACCOUNT, "cutover")
    await save_tradeable_heartbeat(store)
    # XAUUSD 默认点差上限 5.0,spread=8 触发 spread.too_wide blocking
    await store.save_tick({"account_id": ACCOUNT, "symbol": "XAUUSD", "bid": 3335.9, "ask": 3336.1, "spread": 8})
    await store.save_bars(
        {
            "account_id": ACCOUNT,
            "symbol": "XAUUSD",
            "timeframe": "H1",
            "bars": [
                {"time": "2026-04-13T07:00:00.000Z", "open": 3335, "high": 3337, "low": 3333, "close": 3336, "atr": 2}
            ],
        }
    )
    scheduler = SchedulerService(
        FakeAnalysis(
            lambda: {
                "replay": {
                    "signal": {
                        "strategy": "pullback",
                        "side": "BUY",
                        "entry": 3335,
                        "stop_loss": 3330,
                        "tp1": 3345,
                        "tp2": 3355,
                        "score": 8,
                        "atr": 2,
                    },
                    "position_commands": None,
                }
            }
        ),
        CommandLifecycleService(store),
        None,
        store,
        lambda: "2026-04-13T08:00:00.000Z",
    )

    out = capsys.readouterr().out
    await scheduler.enqueue_analysis(ACCOUNT, "XAUUSD", "H1")
    out += capsys.readouterr().out
    assert "spread.too_wide" in out

    assert await store.list_commands(ACCOUNT) == []


async def test_drops_replay_signals_during_the_friday_close_window(capsys) -> None:
    store = create_in_memory_store()
    await store.set_runtime_mode(ACCOUNT, "cutover")
    await save_tradeable_heartbeat(store)
    await store.save_tick({"account_id": ACCOUNT, "symbol": "XAUUSD", "bid": 3335.9, "ask": 3336.1})
    await store.save_bars(
        {
            "account_id": ACCOUNT,
            "symbol": "XAUUSD",
            "timeframe": "H1",
            "bars": [
                {"time": "2026-04-17T20:00:00.000Z", "open": 3335, "high": 3337, "low": 3333, "close": 3336, "atr": 2}
            ],
        }
    )
    scheduler = SchedulerService(
        FakeAnalysis(
            lambda: {
                "replay": {
                    "signal": {
                        "strategy": "pullback",
                        "side": "BUY",
                        "entry": 3335,
                        "stop_loss": 3330,
                        "tp1": 3345,
                        "tp2": 3355,
                        "score": 8,
                        "atr": 2,
                    },
                    "position_commands": None,
                }
            }
        ),
        CommandLifecycleService(store),
        None,
        store,
        # 2026-04-17 是周五,20:00 UTC 之后进入 friday_close_window
        lambda: "2026-04-17T21:00:00.000Z",
    )

    out = capsys.readouterr().out
    await scheduler.enqueue_analysis(ACCOUNT, "XAUUSD", "H1")
    out += capsys.readouterr().out
    assert "session.friday_close_window" in out

    assert await store.list_commands(ACCOUNT) == []


async def test_drops_replay_signals_when_the_ea_heartbeat_reports_the_strategy_disabled(capsys) -> None:
    store = create_in_memory_store()
    await store.set_runtime_mode(ACCOUNT, "cutover")
    await store.save_heartbeat(
        {
            "account_id": ACCOUNT,
            "market_open": True,
            "is_trade_allowed": True,
            "strategies": {"breakout_retest": {"enabled": False, "magic": 20250232, "positions": 0}},
        }
    )
    await store.save_tick({"account_id": ACCOUNT, "symbol": "US100Cash", "bid": 23150.0, "ask": 23151.0})
    await store.save_bars(
        {
            "account_id": ACCOUNT,
            "symbol": "US100Cash",
            "timeframe": "H1",
            "bars": [
                {
                    "time": "2026-04-13T07:00:00.000Z",
                    "open": 23100,
                    "high": 23180,
                    "low": 23080,
                    "close": 23150,
                    "atr": 40,
                }
            ],
        }
    )
    scheduler = SchedulerService(
        FakeAnalysis(
            lambda: {
                "replay": {
                    "signal": {
                        "strategy": "breakout_retest",
                        "side": "BUY",
                        "entry": 23150,
                        "stop_loss": 23100,
                        "tp1": 23250,
                        "tp2": 23350,
                        "score": 8,
                        "atr": 40,
                    },
                    "position_commands": None,
                }
            }
        ),
        CommandLifecycleService(store),
        None,
        store,
        lambda: "2026-04-13T08:00:00.000Z",
    )

    out = capsys.readouterr().out
    await scheduler.enqueue_analysis(ACCOUNT, "US100Cash", "H1")
    out += capsys.readouterr().out
    assert "signal_skipped_strategy_disabled" in out

    assert await store.list_commands(ACCOUNT) == []


# --------------------------------------------------------------------------- Phase 5.1 服务端日亏保护


async def test_records_the_daily_start_equity_on_first_sight_and_keeps_signals_flowing() -> None:
    store = create_in_memory_store()
    await store.set_runtime_mode(ACCOUNT, "cutover")
    await store.save_heartbeat({"account_id": ACCOUNT, "market_open": True, "is_trade_allowed": True, "equity": 10000})
    await store.save_tick({"account_id": ACCOUNT, "symbol": "XAUUSD", "bid": 3335.9, "ask": 3336.1})
    await store.save_bars(
        {
            "account_id": ACCOUNT,
            "symbol": "XAUUSD",
            "timeframe": "H1",
            "bars": [
                {"time": "2026-04-13T07:00:00.000Z", "open": 3335, "high": 3337, "low": 3333, "close": 3336, "atr": 2}
            ],
        }
    )
    scheduler = SchedulerService(
        FakeAnalysis(
            lambda: {
                "replay": {
                    "signal": {
                        "strategy": "pullback",
                        "side": "BUY",
                        "entry": 3335,
                        "stop_loss": 3330,
                        "tp1": 3345,
                        "tp2": 3355,
                        "score": 8,
                        "atr": 2,
                    },
                    "position_commands": None,
                }
            }
        ),
        CommandLifecycleService(store),
        None,
        store,
        lambda: "2026-04-13T08:00:00.000Z",
    )

    await scheduler.enqueue_analysis(ACCOUNT, "XAUUSD", "H1")

    # 当日首见心跳权益 → 记录 UTC 日起始权益基线,同时正常放行信号
    assert await store.get_daily_start_equity(ACCOUNT, "2026-04-13") == 10000
    assert len(await store.list_commands(ACCOUNT)) == 1


async def test_blocks_live_analysis_when_the_daily_realized_drawdown_reaches_the_limit(capsys) -> None:
    store = create_in_memory_store()
    await store.set_runtime_mode(ACCOUNT, "cutover")
    # 当日起始权益 10000,当前权益 9400 → 回撤 6% ≥ 默认阈值 5%
    await store.save_daily_start_equity(ACCOUNT, "2026-04-13", 10000)
    await store.save_heartbeat({"account_id": ACCOUNT, "market_open": True, "is_trade_allowed": True, "equity": 9400})
    analysis = FakeAnalysis(
        lambda: {
            "replay": {
                "signal": None,
                "position_commands": None,
            }
        }
    )
    scheduler = SchedulerService(
        analysis,
        CommandLifecycleService(store),
        None,
        store,
        lambda: "2026-04-13T08:00:00.000Z",
    )

    err = capsys.readouterr().err
    await scheduler.enqueue_analysis(ACCOUNT, "XAUUSD", "H1")
    await scheduler.enqueue_position_review(ACCOUNT, "XAUUSD")
    err += capsys.readouterr().err
    assert "daily_loss_guard_blocked" in err

    assert analysis.calls == 0
    assert await store.list_commands(ACCOUNT) == []


async def test_uses_ea_max_daily_loss_instead_of_environment_variable(monkeypatch) -> None:
    monkeypatch.setenv("GB_MAX_DAILY_LOSS_PCT", "0.01")
    store = create_in_memory_store()
    await store.set_runtime_mode(ACCOUNT, "cutover")
    await store.save_daily_start_equity(ACCOUNT, "2026-04-13", 10000)
    await store.save_heartbeat(
        {
            "account_id": ACCOUNT,
            "market_open": True,
            "is_trade_allowed": True,
            "equity": 9400,
            "max_daily_loss": 10.0,
        }
    )
    await store.save_tick({"account_id": ACCOUNT, "symbol": "XAUUSD", "bid": 3335.9, "ask": 3336.1})
    await store.save_bars(
        {
            "account_id": ACCOUNT,
            "symbol": "XAUUSD",
            "timeframe": "H1",
            "bars": [
                {"time": "2026-04-13T07:00:00.000Z", "open": 3335, "high": 3337, "low": 3333, "close": 3336, "atr": 2}
            ],
        }
    )
    analysis = FakeAnalysis(
        lambda: {
            "replay": {
                "signal": {
                    "strategy": "pullback",
                    "side": "BUY",
                    "entry": 3335,
                    "stop_loss": 3330,
                    "tp1": 3345,
                    "tp2": 3355,
                    "score": 8,
                    "atr": 2,
                },
                "position_commands": None,
            }
        }
    )
    scheduler = SchedulerService(
        analysis,
        CommandLifecycleService(store),
        None,
        store,
        lambda: "2026-04-13T08:00:00.000Z",
    )

    await scheduler.enqueue_analysis(ACCOUNT, "XAUUSD", "H1")

    assert analysis.calls == 1
    assert len(await store.list_commands(ACCOUNT)) == 1


async def test_resets_the_daily_loss_guard_baseline_when_the_utc_date_rolls_over() -> None:
    store = create_in_memory_store()
    await store.set_runtime_mode(ACCOUNT, "cutover")
    # 前一 UTC 日亏损 6%(10000 → 9400),跨日后应以新基线放行
    await store.save_daily_start_equity(ACCOUNT, "2026-04-12", 10000)
    await store.save_heartbeat({"account_id": ACCOUNT, "market_open": True, "is_trade_allowed": True, "equity": 9400})
    await store.save_tick({"account_id": ACCOUNT, "symbol": "XAUUSD", "bid": 3335.9, "ask": 3336.1})
    await store.save_bars(
        {
            "account_id": ACCOUNT,
            "symbol": "XAUUSD",
            "timeframe": "H1",
            "bars": [
                {"time": "2026-04-13T07:00:00.000Z", "open": 3335, "high": 3337, "low": 3333, "close": 3336, "atr": 2}
            ],
        }
    )
    scheduler = SchedulerService(
        FakeAnalysis(
            lambda: {
                "replay": {
                    "signal": {
                        "strategy": "pullback",
                        "side": "BUY",
                        "entry": 3335,
                        "stop_loss": 3330,
                        "tp1": 3345,
                        "tp2": 3355,
                        "score": 8,
                        "atr": 2,
                    },
                    "position_commands": None,
                }
            }
        ),
        CommandLifecycleService(store),
        None,
        store,
        lambda: "2026-04-13T08:00:00.000Z",
    )

    await scheduler.enqueue_analysis(ACCOUNT, "XAUUSD", "H1")

    # 跨 UTC 日:旧基线不再生效,当日以 9400 建立新基线并正常下发信号
    assert await store.get_daily_start_equity(ACCOUNT, "2026-04-13") == 9400
    assert len(await store.list_commands(ACCOUNT)) == 1


# --------------------------------------------------------------------------- Phase 5.2 allowedLots 实际生效


async def test_clamps_signal_lots_to_the_riskgate_allowed_lots_ceiling(capsys) -> None:
    store = create_in_memory_store()
    await store.set_runtime_mode(ACCOUNT, "cutover")
    await store.save_registration({"account_id": ACCOUNT, "leverage": 100})
    await store.save_heartbeat(
        {"account_id": ACCOUNT, "market_open": True, "is_trade_allowed": True, "equity": 10000, "free_margin": 8000}
    )
    await store.save_tick({"account_id": ACCOUNT, "symbol": "XAUUSD", "bid": 3335.9, "ask": 3336.1})
    await store.save_bars(
        {
            "account_id": ACCOUNT,
            "symbol": "XAUUSD",
            "timeframe": "H1",
            "bars": [
                {"time": "2026-04-13T07:00:00.000Z", "open": 3335, "high": 3337, "low": 3333, "close": 3336, "atr": 2}
            ],
        }
    )
    scheduler = SchedulerService(
        FakeAnalysis(
            lambda: {
                "replay": {
                    "signal": {
                        "strategy": "pullback",
                        "side": "BUY",
                        "entry": 3335,
                        "stop_loss": 3330,
                        "tp1": 3345,
                        "tp2": 3355,
                        "score": 8,
                        "atr": 2,
                        # 2% 权益风险上限:10000 * 0.02 / (|3336.1 - 3330| * 100) ≈ 0.32 手
                        "lots": 1.0,
                    },
                    "position_commands": None,
                }
            }
        ),
        CommandLifecycleService(store),
        None,
        store,
        lambda: "2026-04-13T08:00:00.000Z",
    )

    out = capsys.readouterr().out
    await scheduler.enqueue_analysis(ACCOUNT, "XAUUSD", "H1")
    out += capsys.readouterr().out
    assert "signal_lots_clamped" in out

    commands = await store.list_commands(ACCOUNT)
    assert len(commands) == 1
    assert abs(float(commands[0]["lots"]) - 0.32) < 5e-11


async def test_keeps_signal_lots_unchanged_when_below_the_riskgate_allowed_lots_ceiling(capsys) -> None:
    store = create_in_memory_store()
    await store.set_runtime_mode(ACCOUNT, "cutover")
    await store.save_registration({"account_id": ACCOUNT, "leverage": 100})
    await store.save_heartbeat(
        {"account_id": ACCOUNT, "market_open": True, "is_trade_allowed": True, "equity": 10000, "free_margin": 8000}
    )
    await store.save_tick({"account_id": ACCOUNT, "symbol": "XAUUSD", "bid": 3335.9, "ask": 3336.1})
    await store.save_bars(
        {
            "account_id": ACCOUNT,
            "symbol": "XAUUSD",
            "timeframe": "H1",
            "bars": [
                {"time": "2026-04-13T07:00:00.000Z", "open": 3335, "high": 3337, "low": 3333, "close": 3336, "atr": 2}
            ],
        }
    )
    scheduler = SchedulerService(
        FakeAnalysis(
            lambda: {
                "replay": {
                    "signal": {
                        "strategy": "pullback",
                        "side": "BUY",
                        "entry": 3335,
                        "stop_loss": 3330,
                        "tp1": 3345,
                        "tp2": 3355,
                        "score": 8,
                        "atr": 2,
                        # 0.1 手低于 riskgate 上限(≈0.32),保持原手数
                        "lots": 0.1,
                    },
                    "position_commands": None,
                }
            }
        ),
        CommandLifecycleService(store),
        None,
        store,
        lambda: "2026-04-13T08:00:00.000Z",
    )

    out = capsys.readouterr().out
    await scheduler.enqueue_analysis(ACCOUNT, "XAUUSD", "H1")
    out += capsys.readouterr().out
    assert "signal_lots_clamped" not in out

    commands = await store.list_commands(ACCOUNT)
    assert len(commands) == 1
    assert commands[0]["lots"] == 0.1


# --------------------------------------------------------------------------- Phase 5.3 per-symbol STOPLEVEL_MIN_RATIO


async def test_skips_gbpjpy_modify_inside_the_raised_0_0012_stoplevel_ratio() -> None:
    store = create_in_memory_store()
    await store.set_runtime_mode(ACCOUNT, "cutover")
    await save_tradeable_heartbeat(store)
    # mid = 218.31,GBPJPY 比例 0.0012 → 最小距离 ~0.262;
    # new_sl 距市价 0.21,旧默认比例 0.0005(~0.109)会放行,新比例应拦截
    await store.save_tick({"account_id": ACCOUNT, "symbol": "GBPJPY", "bid": 218.30, "ask": 218.32})
    await store.save_positions(
        {
            "account_id": ACCOUNT,
            "symbol": "GBPJPY",
            "positions": [
                {
                    "ticket": 42370061,
                    "symbol": "GBPJPY",
                    "type": "BUY",
                    "open_price": 218.0,
                    "lots": 0.02,
                    "sl": 217.7,
                    "tp": 219.18,
                }
            ],
        }
    )
    scheduler = SchedulerService(
        FakeAnalysis(
            lambda: {
                "replay": {
                    "signal": None,
                    "position_commands": [
                        {
                            "action": "MODIFY",
                            "ticket": 42370061,
                            "new_sl": 218.10,
                            "reason": "group_favorable_addon_BUY",
                        }
                    ],
                }
            }
        ),
        CommandLifecycleService(store),
        None,
        store,
        lambda: "2026-07-17T06:13:55.000Z",
    )

    await scheduler.enqueue_position_review(ACCOUNT, "GBPJPY")
    assert await store.list_commands(ACCOUNT) == []


async def test_queues_modify_for_non_gbpjpy_symbols_at_distances_allowed_by_the_default_0_0005_ratio() -> None:
    store = create_in_memory_store()
    await store.set_runtime_mode(ACCOUNT, "cutover")
    await save_tradeable_heartbeat(store)
    # mid = 3336.0,XAUUSD 用默认比例 0.0005 → 最小距离 ~1.67;
    # new_sl 距市价 3.0 放行(若误用 GBPJPY 的 0.0012 → ~4.0 会被拦截)
    await store.save_tick({"account_id": ACCOUNT, "symbol": "XAUUSD", "bid": 3335.9, "ask": 3336.1})
    await store.save_positions(
        {
            "account_id": ACCOUNT,
            "symbol": "XAUUSD",
            "positions": [
                {
                    "ticket": 888,
                    "symbol": "XAUUSD",
                    "type": "BUY",
                    "open_price": 3330,
                    "lots": 0.1,
                    "sl": 3328,
                    "tp": 3350,
                }
            ],
        }
    )
    scheduler = SchedulerService(
        FakeAnalysis(
            lambda: {
                "replay": {
                    "signal": None,
                    "position_commands": [
                        {"action": "MODIFY", "ticket": 888, "new_sl": 3333.0, "reason": "breakeven_2.2ATR"}
                    ],
                }
            }
        ),
        CommandLifecycleService(store),
        None,
        store,
        lambda: "2026-07-17T06:13:55.000Z",
    )

    await scheduler.enqueue_position_review(ACCOUNT, "XAUUSD")
    commands = await store.list_commands(ACCOUNT)
    assert len(commands) == 1
    assert_matches(
        {
            "action": "MODIFY",
            "source": "position_manager",
            "ticket": 888,
            "new_sl": 3333.0,
        },
        commands[0],
    )


# --------------------------------------------------------------------------- position_states 持久化


async def test_persists_position_states_after_position_review() -> None:
    store = create_in_memory_store()
    mock_position_states = [
        {"ticket": 6001, "addOnCount": 1, "lastAddOnTime": "2026-04-13T08:00:00.000Z", "groupAvgEntry": 3328.5}
    ]

    class PersistentTrackingAnalysis:
        def __init__(self) -> None:
            self.persisted: tuple[str, str, list[dict[str, Any]]] | None = None

        async def analyze_account_symbol(self, account_id: str, symbol: str) -> dict[str, Any]:
            del account_id, symbol
            return {"replay": {"position_states": mock_position_states, "signal": None}}

        async def persist_position_states(
            self,
            account_id: str,
            symbol: str,
            states: list[dict[str, Any]] | None,
        ) -> None:
            self.persisted = (account_id, symbol, states)

    analysis = PersistentTrackingAnalysis()
    await save_tradeable_heartbeat(store)

    command_lifecycle = CommandLifecycleService(store)
    scheduler = SchedulerService(analysis, command_lifecycle, None, store)
    await scheduler.enqueue_position_review(ACCOUNT, "XAUUSD")

    assert analysis.persisted == (ACCOUNT, "XAUUSD", mock_position_states)
