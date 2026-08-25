"""SchedulerService(镜像 gold-bot apps/app-server/src/services/scheduler/service.ts)。

按 TS 语义逐字移植:enqueueAnalysis / enqueuePositionReview 及全部内部链路
(市况过滤、日亏保护 Phase 5.1、riskgate allowedLots Phase 5.2、per-symbol
STOPLEVEL Phase 5.3、AI 止损 5 分钟冷却)。console.log/console.warn 镜像为
stdout/stderr 的 print,JSON 序列化保持 JSON.stringify 紧凑格式。

start()/stop() 为本移植新增的调度入口(APScheduler + taskiq 内存 broker):
测试不启动持久调度器,直接调用 job 本体(enqueue_analysis / enqueue_position_review)。
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol

from backend.persistence.records import EaRecord, StoredCommand
from backend.persistence.store import EaStore
from backend.trading_core.indicators import atr
from backend.trading_core.riskgate import evaluate_market_filters, evaluate_risk_gate

__all__ = [
    "SchedulerService",
    "create_scheduler_service",
]

AI_STOP_LOSS_MODIFY_COOLDOWN_MS = 5 * 60 * 1000
AI_STOP_LOSS_PROFIT_ATR_GATE = 1.5
MODIFY_DISTANCE_EPSILON = 1e-9
# MT4 STOPLEVEL 最小距离比例默认值(0.05%),SL/TP 离当前价比此更近会触发 error 130
STOPLEVEL_MIN_RATIO_DEFAULT = 0.0005
# per-symbol STOPLEVEL 最小距离比例(Phase 5.3);key 为归一化 symbol(去 broker 后缀、大写)
STOPLEVEL_MIN_RATIO_BY_SYMBOL: dict[str, float] = {
    "GBPJPY": 0.0012,
}
# 仓位数据超过此时间未更新视为过期(可能已平仓),跳过避免 4108
STALE_POSITION_MS = 5 * 60 * 1000

AI_STOP_LOSS_SKIP_REASONS = (
    "suggested_sl_le_zero",
    "ai_result_missing",
    "atr_le_zero",
    "price_le_zero",
    "not_be_or_profit_ready",
    "price_magnitude",
    "candidate_null",
    "command_exists",
    "cooldown_active",
)
AIStopLossSkipReason = str


class AnalysisServiceLike(Protocol):
    async def analyze_account_symbol(self, account_id: str, symbol: str) -> dict[str, Any]: ...

    async def persist_position_states(
        self,
        account_id: str,
        symbol: str,
        states: list[dict[str, Any]] | None,
    ) -> None: ...


class CommandLifecycleServiceProtocol(Protocol):
    """TS 端 services/command-lifecycle/service.ts 的 Python 模拟接口。"""

    # TODO M5-B fills this: real port of services/command-lifecycle/service.ts
    async def accept_candidate(self, account_id: str, candidate: dict[str, Any]) -> dict[str, Any]: ...

    async def reconcile(
        self,
        account_id: str,
        command_id: str,
        result: str,
        ticket: int | None = None,
        error_text: str = "",
        created_at: str | None = None,
    ) -> bool: ...


class ShadowServiceProtocol(Protocol):
    """TS 端 services/shadow/service.ts 的 Python 模拟接口。"""

    # TODO M5-B fills this: real port of services/shadow/service.ts
    async def record_runtime_snapshot(self, payload: dict[str, Any]) -> None: ...


def _default_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class SchedulerService:
    """调度与信号入队服务(镜像 TS SchedulerService)。"""

    def __init__(
        self,
        analysis: AnalysisServiceLike,
        command_lifecycle: CommandLifecycleServiceProtocol,
        shadow: ShadowServiceProtocol | None = None,
        store: EaStore | None = None,
        now_iso: Callable[[], str] | None = None,
    ) -> None:
        self._analysis = analysis
        self._command_lifecycle = command_lifecycle
        self._shadow = shadow
        self._store = store
        self._now_iso = now_iso if now_iso is not None else _default_now_iso
        self._ai_stop_loss_queued_at_ms: dict[str, float] = {}
        self._started = False
        self._apscheduler: Any = None
        self._broker: Any = None

    # ------------------------------------------------------------------ 公共入口
    async def enqueue_analysis(self, account_id: str, symbol: str, timeframe: str = "") -> None:
        if not _is_live_strategy_timeframe(timeframe) or not await self._can_run_live_analysis(account_id):
            return
        await self._publish_replay_signal(account_id, symbol)

    async def enqueue_position_review(self, account_id: str, symbol: str) -> None:
        if not await self._can_run_live_analysis(account_id):
            return
        result = await self._analysis.analyze_account_symbol(account_id, symbol)
        replay = result["replay"]
        if self._shadow is not None:
            await self._shadow.record_runtime_snapshot(
                {
                    "account_id": account_id,
                    "symbol": symbol,
                    "source": "position_review",
                    "signal": replay.get("signal"),
                    "command": replay.get("position_commands"),
                }
            )
        await self._queue_replay_signal(account_id, symbol, replay.get("signal"), "positions")
        if replay.get("signal") is None:
            await self._queue_ai_stop_loss_adjust(account_id, symbol)
        await self._queue_position_manager_commands(account_id, symbol, replay.get("position_commands"))
        position_states = replay.get("position_states")
        if position_states is not None and len(position_states) > 0:
            await self._analysis.persist_position_states(account_id, symbol, position_states)

    # ------------------------------------------------------------------ 调度生命周期
    async def start(self) -> None:
        """启动 APScheduler 周期任务 + taskiq 内存 broker(调度入口)。

        任务本体就是 enqueue_analysis / enqueue_position_review(与 TS 的 job 语义一致);
        测试不调用 start,直接触发 job 本体。coordinator 可在此处按账户/品种配置周期。
        """
        if self._started:
            return
        from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore[import-untyped]
        from taskiq import InMemoryBroker

        broker = InMemoryBroker()
        self._broker = broker

        @broker.task
        async def enqueue_analysis_task(payload: dict[str, Any]) -> None:
            await self.enqueue_analysis(
                str(payload["account_id"]),
                str(payload["symbol"]),
                str(payload.get("timeframe") or ""),
            )

        @broker.task
        async def enqueue_position_review_task(payload: dict[str, Any]) -> None:
            await self.enqueue_position_review(str(payload["account_id"]), str(payload["symbol"]))

        scheduler = AsyncIOScheduler(timezone=UTC)
        scheduler.add_job(self._position_review_cycle, "interval", minutes=1, id="scheduler:position_review")
        self._apscheduler = scheduler
        scheduler.start()
        self._started = True

    async def stop(self) -> None:
        if self._apscheduler is not None:
            self._apscheduler.shutdown(wait=False)
            self._apscheduler = None
        self._broker = None
        self._started = False

    async def _analysis_cycle(self) -> None:
        store = self._store
        if store is None:
            return
        for account_id in await store.list_account_ids():
            for symbol in await store.list_symbols(account_id):
                await self.enqueue_analysis(account_id, symbol, "H1")

    async def _position_review_cycle(self) -> None:
        store = self._store
        if store is None:
            return
        for account_id in await store.list_account_ids():
            for symbol in await store.list_symbols(account_id):
                await self.enqueue_position_review(account_id, symbol)

    # ------------------------------------------------------------------ 内部链路
    async def _can_run_live_analysis(self, account_id: str) -> bool:
        heartbeat = await self._get_heartbeat(account_id)
        if _explicit_boolean(heartbeat, "market_open") is not True or _explicit_boolean(
            heartbeat, "is_trade_allowed"
        ) is not True:
            return False
        return await self._passes_daily_loss_guard(account_id, heartbeat)

    async def _passes_daily_loss_guard(self, account_id: str, heartbeat: EaRecord) -> bool:
        """服务端日亏保护(Phase 5.1):按 UTC 日持久化当日起始权益基线。"""
        equity = _number_field(heartbeat, "equity")
        if (
            equity <= 0
            or self._store is None
            or not hasattr(self._store, "get_daily_start_equity")
            or not hasattr(self._store, "save_daily_start_equity")
        ):
            return True
        utc_date = _utc_date_key(self._now_iso())
        start_equity = await self._store.get_daily_start_equity(account_id, utc_date)
        if start_equity is None or start_equity <= 0:
            await self._store.save_daily_start_equity(account_id, utc_date, equity)
            return True
        drawdown_pct = (start_equity - equity) / start_equity
        registration = await self._get_registration(account_id)
        threshold_pct = _max_daily_loss_pct(heartbeat, registration)
        if drawdown_pct >= threshold_pct:
            print(
                f"[SCHED] daily_loss_guard_blocked {_json({
                    'account_id': account_id,
                    'utc_date': utc_date,
                    'start_equity': start_equity,
                    'equity': equity,
                    'drawdown_pct': _to_fixed(drawdown_pct, 4),
                    'threshold_pct': threshold_pct,
                })}",
                file=sys.stderr,
            )
            return False
        return True

    async def _allowed_lots_for_signal(
        self,
        account_id: str,
        symbol: str,
        signal: EaRecord,
        heartbeat: EaRecord,
        latest_tick: EaRecord,
    ) -> float | None:
        """riskgate allowedLots(Phase 5.2):2% 权益风险 + 保证金约束下的手数上限。"""
        registration = await self._get_registration(account_id)
        positions = await self._get_positions(account_id, symbol)
        last_tick_at = (
            _string_field(latest_tick, "received_at")
            or _string_field(latest_tick, "updated_at")
            or _string_field(latest_tick, "time")
            or self._now_iso()
        )
        result = evaluate_risk_gate(
            {
                "now": self._now_iso(),
                "account": {
                    "accountId": account_id,
                    "leverage": _number_field(registration, "leverage"),
                },
                "runtime": {
                    "equity": _number_field(heartbeat, "equity"),
                    "freeMargin": _number_field(heartbeat, "free_margin"),
                    "marketOpen": _explicit_boolean(heartbeat, "market_open") is not False,
                    "isTradeAllowed": _explicit_boolean(heartbeat, "is_trade_allowed") is not False,
                    "lastTickAt": last_tick_at,
                },
                "state": {
                    "tick": {
                        "symbol": symbol,
                        "bid": _number_field(latest_tick, "bid"),
                        "ask": _number_field(latest_tick, "ask"),
                        "spread": _number_field(latest_tick, "spread"),
                        "maxSpread": _positive_number_field(latest_tick, "max_spread")
                        or _positive_number_field(heartbeat, "max_spread"),
                    },
                    "positions": [
                        {
                            "ticket": _number_field(position, "ticket"),
                            "symbol": _string_field(position, "symbol"),
                            "type": _string_field(position, "type"),
                            "lots": _number_field(position, "lots"),
                            "strategy": _string_field(position, "strategy"),
                        }
                        for position in positions
                    ],
                },
                "plan": {
                    "accountId": account_id,
                    "symbol": symbol,
                    "mode": "approve",
                    "side": _string_field(signal, "side"),
                    "stopLoss": _number_field(signal, "stop_loss"),
                    "maxLots": _number_field(signal, "lots"),
                },
                "allowAdd": True,
                "allowHedge": True,
                "sourceStrategy": _string_field(signal, "strategy"),
            }
        )
        allowed_lots = result.get("allowedLots")
        if (
            isinstance(allowed_lots, (int, float))
            and not isinstance(allowed_lots, bool)
            and math.isfinite(float(allowed_lots))
            and allowed_lots > 0
        ):
            return float(allowed_lots)
        return None

    async def _publish_replay_signal(self, account_id: str, symbol: str) -> None:
        result = await self._analysis.analyze_account_symbol(account_id, symbol)
        replay = result["replay"]
        if self._shadow is not None:
            await self._shadow.record_runtime_snapshot(
                {
                    "account_id": account_id,
                    "symbol": symbol,
                    "source": "ea_analysis",
                    "signal": replay.get("signal"),
                    "command": None,
                }
            )
        await self._queue_replay_signal(account_id, symbol, replay.get("signal"), "bars")

    async def _queue_replay_signal(
        self,
        account_id: str,
        symbol: str,
        signal: Any,
        analysis_mode: str,
    ) -> None:
        if signal is None:
            return
        signal_record = dict(signal)
        strategy = _string_field(signal_record, "strategy")
        heartbeat = await self._get_heartbeat(account_id)
        if _heartbeat_strategy_disabled(heartbeat, strategy):
            print(
                f"[SCHED] signal_skipped_strategy_disabled {_json({
                    'account_id': account_id,
                    'symbol': symbol,
                    'strategy': strategy,
                })}"
            )
            return
        bars = await self._bars_by_timeframe(account_id, symbol)
        latest_tick = await self._get_latest_tick(account_id, symbol)
        market_filters = await self._evaluate_signal_market_filters(account_id, symbol, latest_tick, bars, heartbeat)
        if market_filters.get("blocked") is True:
            return
        trigger_key = _live_decision_key(strategy, bars)
        command_id = _live_command_id(account_id, symbol, signal_record, trigger_key)
        if await self._get_command(command_id) is not None:
            return
        current_price = _live_current_price(latest_tick, bars)
        atr_value = _latest_atr(bars.get("H1") or []) or _number_field(signal_record, "atr")
        order_type = _order_type_for_signal(
            current_price, _number_field(signal_record, "entry"), atr_value, _string_field(signal_record, "side")
        )
        tp1_value = _number_field(signal_record, "tp1")
        tp2_value = _number_field(signal_record, "tp2")
        should_split_tp = tp2_value > 0 and abs(tp2_value - tp1_value) > 0
        signal_lots = _number_field(signal_record, "lots")
        command_lots = signal_lots
        if signal_lots > 0:
            allowed_lots = await self._allowed_lots_for_signal(
                account_id, symbol, signal_record, heartbeat, latest_tick
            )
            if allowed_lots is not None and signal_lots > allowed_lots:
                command_lots = allowed_lots
                print(
                    f"[SCHED] signal_lots_clamped {_json({
                        'account_id': account_id,
                        'symbol': symbol,
                        'strategy': strategy,
                        'lots': signal_lots,
                        'allowed_lots': allowed_lots,
                        'clamped_lots': command_lots,
                    })}"
                )
        candidate: dict[str, Any] = {
            "command_id": command_id,
            "decision_id": command_id,
            "action": "SIGNAL",
            "source": "live_strategy",
            "strategy": _string_field(signal_record, "strategy"),
            "symbol": symbol,
            "type": _string_field(signal_record, "side"),
            "entry": _number_field(signal_record, "entry"),
            "sl": _number_field(signal_record, "stop_loss"),
            "tp1": tp1_value,
            "tp2": tp2_value,
            "score": _number_field(signal_record, "score"),
            "atr": _number_field(signal_record, "atr"),
            "scale_in_parent_ticket": _number_field(signal_record, "scale_in_parent_ticket"),
            "weighted_avg_entry": _number_field(signal_record, "weighted_avg_entry"),
            "unified_sl": _number_field(signal_record, "unified_sl"),
            "scale_in_count": _number_field(signal_record, "scale_in_count"),
            "trigger_key": trigger_key,
            "analysis_mode": analysis_mode,
            "order_type": order_type,
            "tp_split": should_split_tp,
        }
        if command_lots > 0:
            candidate["lots"] = command_lots
        if _boolean_field(signal_record, "fib_enhanced"):
            candidate["fib_enhanced"] = True
        if order_type != "market":
            candidate["expiration"] = _unix_seconds(self._now_iso()) + 24 * 60 * 60
        await self._command_lifecycle.accept_candidate(account_id, candidate)

    async def _queue_ai_stop_loss_adjust(self, account_id: str, symbol: str) -> None:
        # 对 EA 上报持仓的全部品种生效(不再有 GB_AI_TRAIL_SYMBOLS 白名单):
        # 分析按 EA 端 /positions、/tick、/bars 载荷里的 symbol 字段执行。
        ai_result = await self._latest_ai_result(account_id, symbol)
        if ai_result is None:
            self._log_ai_stop_loss_skip(account_id, symbol, "ai_result_missing")
            return
        ai_sl = _number_field(ai_result, "suggested_sl") or _number_field(ai_result, "suggestedSL")
        if ai_sl <= 0:
            self._log_ai_stop_loss_skip(account_id, symbol, "suggested_sl_le_zero", {"suggested_sl": ai_sl})
            return
        bars = await self._bars_by_timeframe(account_id, symbol)
        atr_value = _latest_atr(bars.get("H1") or [])
        if atr_value <= 0:
            self._log_ai_stop_loss_skip(account_id, symbol, "atr_le_zero", {"atr": atr_value})
            return
        current_price = _live_current_price(await self._get_latest_tick(account_id, symbol), bars)
        if current_price <= 0:
            self._log_ai_stop_loss_skip(account_id, symbol, "price_le_zero", {"price": current_price})
            return
        if ai_sl < current_price * 0.3 or ai_sl > current_price * 2.0:
            self._log_ai_stop_loss_skip(
                account_id,
                symbol,
                "price_magnitude",
                {"suggested_sl": ai_sl, "price": current_price},
            )
            return
        decision_id = _ai_decision_id(ai_result)
        position_states = await self._load_position_states(account_id, symbol)
        states_by_ticket = {state.get("ticket"): state for state in position_states}
        for position in await self._get_positions(account_id, symbol):
            ticket = _number_field(position, "ticket")
            state = states_by_ticket.get(ticket)
            be_moved = state is not None and state.get("be_moved") is True
            profit_atr = _ai_stop_loss_profit_atr(position, atr_value, current_price)
            if not be_moved and profit_atr is not None and profit_atr < AI_STOP_LOSS_PROFIT_ATR_GATE:
                self._log_ai_stop_loss_skip(
                    account_id,
                    symbol,
                    "not_be_or_profit_ready",
                    {"ticket": ticket, "profit_atr": profit_atr, "be_moved": be_moved},
                )
                continue
            candidate = self._ai_stop_loss_command_candidate(
                account_id, symbol, position, ai_sl, atr_value, current_price, decision_id
            )
            if candidate is None:
                self._log_ai_stop_loss_skip(
                    account_id,
                    symbol,
                    "candidate_null",
                    {"ticket": _number_field(position, "ticket"), "decision_id": decision_id},
                )
                continue
            if await self._get_command(candidate["command_id"]) is not None:
                self._log_ai_stop_loss_skip(
                    account_id,
                    symbol,
                    "command_exists",
                    {"ticket": _number_field(candidate, "ticket"), "command_id": candidate["command_id"]},
                )
                continue
            if self._is_ai_stop_loss_cooldown_active(account_id, symbol, candidate):
                self._log_ai_stop_loss_skip(
                    account_id,
                    symbol,
                    "cooldown_active",
                    {"ticket": _number_field(candidate, "ticket"), "command_id": candidate["command_id"]},
                )
                continue
            await self._command_lifecycle.accept_candidate(account_id, candidate)
            self._remember_ai_stop_loss_queued(account_id, symbol, candidate)

    async def _queue_position_manager_commands(
        self,
        account_id: str,
        symbol: str,
        commands: list[dict[str, Any]] | None,
    ) -> None:
        if commands is None or len(commands) == 0:
            return
        now_iso = self._now_iso()
        positions = await self._get_positions(account_id, symbol)
        bars = await self._bars_by_timeframe(account_id, symbol)
        current_price = _live_current_price(await self._get_latest_tick(account_id, symbol), bars)
        for command in commands:
            candidate = self._position_manager_command_candidate(
                account_id, symbol, command, positions, now_iso, current_price
            )
            if candidate is None or await self._get_command(candidate["command_id"]) is not None:
                continue
            await self._command_lifecycle.accept_candidate(account_id, candidate)

    def _position_manager_command_candidate(
        self,
        account_id: str,
        symbol: str,
        command: dict[str, Any],
        positions: list[EaRecord],
        now_iso: str,
        current_price: float,
    ) -> dict[str, Any] | None:
        ticket = command.get("ticket")
        if (
            isinstance(ticket, bool)
            or not isinstance(ticket, (int, float))
            or not math.isfinite(float(ticket))
            or ticket <= 0
        ):
            return None
        command_id = _position_manager_command_id(account_id, symbol, command, now_iso)
        if command.get("action") == "MODIFY":
            new_sl_value = command.get("new_sl")
            new_sl = 0 if new_sl_value is None else new_sl_value
            if (
                isinstance(new_sl, bool)
                or not isinstance(new_sl, (int, float))
                or not math.isfinite(float(new_sl))
                or new_sl <= 0
            ):
                return None
            position = _find_position(positions, ticket)
            if position is None:
                return None
            if _is_stale_position(position, now_iso):
                return None
            old_sl = _number_field(position, "sl")
            if old_sl <= 0:
                return None
            if abs(new_sl - old_sl) < MODIFY_DISTANCE_EPSILON:
                return None
            side = _string_field(position, "type").upper()
            if current_price > 0 and len(side) > 0:
                min_distance = current_price * _stoplevel_min_ratio(symbol)
                if side == "BUY" or side.startswith("BUY"):
                    if new_sl >= current_price - min_distance:
                        return None
                elif side == "SELL" or side.startswith("SELL"):
                    if new_sl <= current_price + min_distance:
                        return None
            elif current_price > 0 and abs(new_sl - current_price) < current_price * _stoplevel_min_ratio(symbol):
                return None
            return {
                "command_id": command_id,
                "action": "MODIFY",
                "source": "position_manager",
                "symbol": symbol,
                "ticket": ticket,
                "new_sl": new_sl,
                "sl": new_sl,
                "old_sl": old_sl,
                "tp": _number_field(position, "tp"),
                "open_price": _number_field(position, "open_price") or _number_field(position, "openPrice"),
                "distance": abs(new_sl - old_sl),
                "reason": command.get("reason"),
                "trigger_time": now_iso,
                "analysis_mode": "positions",
            }
        if command.get("action") == "CLOSE":
            position = _find_position(positions, ticket)
            if position is None:
                return None
            if _is_pending_position_record(position):
                return None
            if _is_stale_position(position, now_iso):
                return None
            return {
                "command_id": command_id,
                "action": "CLOSE",
                "source": "position_manager",
                "symbol": symbol,
                "ticket": ticket,
                "lots": command.get("lots"),
                "reason": command.get("reason"),
                "trigger_time": now_iso,
                "analysis_mode": "positions",
            }
        if command.get("action") == "CANCEL_PENDING":
            return {
                "command_id": command_id,
                "action": "CANCEL_PENDING",
                "source": "position_manager",
                "symbol": symbol,
                "ticket": ticket,
                "reason": command.get("reason"),
                "trigger_time": now_iso,
                "analysis_mode": "positions",
            }
        return None

    def _is_ai_stop_loss_cooldown_active(self, account_id: str, symbol: str, candidate: dict[str, Any]) -> bool:
        if not _is_ai_stop_loss_modify_candidate(candidate):
            return False
        ticket = _number_field(candidate, "ticket")
        if ticket <= 0:
            return False
        queued_at_ms = _candidate_timestamp_ms(candidate, self._now_iso)
        last_queued_at_ms = self._ai_stop_loss_queued_at_ms.get(_ai_stop_loss_cooldown_key(account_id, symbol, ticket))
        return last_queued_at_ms is not None and queued_at_ms - last_queued_at_ms < AI_STOP_LOSS_MODIFY_COOLDOWN_MS

    def _remember_ai_stop_loss_queued(self, account_id: str, symbol: str, candidate: dict[str, Any]) -> None:
        if not _is_ai_stop_loss_modify_candidate(candidate):
            return
        ticket = _number_field(candidate, "ticket")
        if ticket <= 0:
            return
        self._ai_stop_loss_queued_at_ms[
            _ai_stop_loss_cooldown_key(account_id, symbol, ticket)
        ] = _candidate_timestamp_ms(candidate, self._now_iso)

    async def _latest_ai_result(self, account_id: str, symbol: str) -> EaRecord | None:
        for result in await self._get_ai_results(account_id):
            if _string_field(result, "symbol") == symbol:
                return result
        return None

    def _ai_stop_loss_command_candidate(
        self,
        account_id: str,
        symbol: str,
        position: EaRecord,
        new_sl: float,
        atr_value: float,
        current_price: float,
        decision_id: str,
    ) -> dict[str, Any] | None:
        ticket = _number_field(position, "ticket")
        old_sl = _number_field(position, "sl")
        tp = _number_field(position, "tp")
        open_price = _number_field(position, "open_price") or _number_field(position, "openPrice")
        side = _string_field(position, "type").upper()
        if ticket <= 0 or old_sl <= 0 or tp <= 0 or open_price <= 0:
            return None
        if side == "BUY":
            if new_sl < old_sl:
                return None
            if new_sl >= current_price:
                return None
        elif side == "SELL":
            if new_sl > old_sl:
                return None
            if new_sl <= current_price:
                return None
        else:
            return None
        distance = abs(new_sl - old_sl)
        if distance < atr_value * 0.3:
            return None
        trigger_time = self._now_iso()
        candidate: dict[str, Any] = {
            "command_id": _ai_stop_loss_command_id(account_id, symbol, ticket, trigger_time),
            "action": "MODIFY",
            "source": "ai_stop_loss",
            "symbol": symbol,
            "ticket": ticket,
            "new_sl": new_sl,
            "sl": new_sl,
            "tp": tp,
            "old_sl": old_sl,
            "distance": distance,
            "atr": atr_value,
            "trigger_time": trigger_time,
            "analysis_mode": "positions",
        }
        if len(decision_id) > 0:
            candidate["decision_id"] = decision_id
        return candidate

    def _log_ai_stop_loss_skip(
        self,
        account_id: str,
        symbol: str,
        reason: AIStopLossSkipReason,
        details: dict[str, Any] | None = None,
    ) -> None:
        payload: dict[str, Any] = {"account_id": account_id, "symbol": symbol, "reason": reason}
        if details:
            payload.update(details)
        print(f"[AI] stop_loss_skip {_json(payload)}")

    async def _bars_by_timeframe(self, account_id: str, symbol: str) -> dict[str, list[EaRecord]]:
        store = self._store
        if store is None:
            return {}
        return {
            "H1": await store.get_bars(account_id, symbol, "H1"),
            "H4": await store.get_bars(account_id, symbol, "H4"),
            "M30": await store.get_bars(account_id, symbol, "M30"),
            "M15": await store.get_bars(account_id, symbol, "M15"),
            "M5": await store.get_bars(account_id, symbol, "M5"),
            "M1": await store.get_bars(account_id, symbol, "M1"),
        }

    async def _evaluate_signal_market_filters(
        self,
        account_id: str,
        symbol: str,
        latest_tick: EaRecord,
        bars: dict[str, list[EaRecord]],
        heartbeat: EaRecord,
    ) -> dict[str, Any]:
        last_tick_at = (
            _string_field(latest_tick, "received_at")
            or _string_field(latest_tick, "updated_at")
            or _string_field(latest_tick, "time")
            or self._now_iso()
        )
        result = evaluate_market_filters(
            {
                "now": self._now_iso(),
                "symbol": symbol,
                "runtime": {
                    "marketOpen": _explicit_boolean(heartbeat, "market_open") is not False,
                    "isTradeAllowed": _explicit_boolean(heartbeat, "is_trade_allowed") is not False,
                    "lastTickAt": last_tick_at,
                },
                "state": {
                    "tick": {
                        "symbol": symbol,
                        "spread": _number_field(latest_tick, "spread"),
                        "maxSpread": _positive_number_field(latest_tick, "max_spread")
                        or _positive_number_field(heartbeat, "max_spread"),
                    },
                    "bars": {
                        "M30": [
                            {"atr": _number_field(bar, "atr") or _number_field(bar, "ATR")}
                            for bar in (bars.get("M30") or [])
                        ]
                    },
                },
            }
        )
        if result.get("blocked") is True:
            print(
                f"[SCHED] signal_blocked_by_market_filter {_json({
                    'account_id': account_id,
                    'symbol': symbol,
                    'reason_codes': result.get('reason_codes'),
                })}"
            )
        return result

    # ------------------------------------------------------------------ store 辅助
    async def _get_heartbeat(self, account_id: str) -> EaRecord:
        store = self._store
        if store is None:
            return {}
        return (await store.get_heartbeat(account_id)) or {}

    async def _get_latest_tick(self, account_id: str, symbol: str) -> EaRecord:
        store = self._store
        if store is None:
            return {}
        return (await store.get_latest_tick(account_id, symbol)) or {}

    async def _get_command(self, command_id: str) -> StoredCommand | None:
        store = self._store
        if store is None:
            return None
        return await store.get_command(command_id)

    async def _get_positions(self, account_id: str, symbol: str) -> list[EaRecord]:
        store = self._store
        if store is None:
            return []
        return await store.get_positions(account_id, symbol)

    async def _get_ai_results(self, account_id: str) -> list[EaRecord]:
        store = self._store
        if store is None:
            return []
        return await store.get_ai_results(account_id)

    async def _load_position_states(self, account_id: str, symbol: str) -> list[EaRecord]:
        store = self._store
        if store is None:
            return []
        return await store.load_position_states(account_id, symbol)

    async def _get_registration(self, account_id: str) -> EaRecord:
        store = self._store
        if store is None:
            return {}
        return (await store.get_registration(account_id)) or {}


def create_scheduler_service(options: dict[str, Any]) -> SchedulerService:
    """工厂:coordinator 在 main.py 组装依赖时通过 options 注入。"""
    return SchedulerService(
        analysis=options["analysis"],
        command_lifecycle=options["command_lifecycle"],
        shadow=options.get("shadow"),
        store=options.get("store"),
        now_iso=options.get("now_iso"),
    )


# ------------------------------------------------------------------ 模块级辅助(镜像 TS 文件级函数)


def _is_live_strategy_timeframe(timeframe: str) -> bool:
    return timeframe.strip().upper() in ("H4", "H1", "M30", "M15", "M5", "M1")


def _explicit_boolean(record: EaRecord, field: str) -> bool | None:
    value = record.get(field)
    return value if isinstance(value, bool) else None


def _heartbeat_strategy_disabled(heartbeat: EaRecord, strategy: str) -> bool:
    if len(strategy) == 0:
        return False
    strategies = heartbeat.get("strategies")
    if strategies is None or not isinstance(strategies, dict):
        return False
    entry = strategies.get(strategy)
    if entry is None or not isinstance(entry, dict):
        return False
    return entry.get("enabled") is False


def _is_ai_stop_loss_modify_candidate(candidate: dict[str, Any]) -> bool:
    return candidate.get("source") == "ai_stop_loss" and candidate.get("action") == "MODIFY"


def _ai_stop_loss_cooldown_key(account_id: str, symbol: str, ticket: float) -> str:
    return "|".join([account_id, symbol.upper(), str(ticket)])


def _candidate_timestamp_ms(candidate: dict[str, Any], fallback_now_iso: Callable[[], str]) -> float:
    trigger_time_ms = _parse_iso_ms(_string_field(candidate, "trigger_time"))
    if trigger_time_ms is not None:
        return trigger_time_ms
    fallback_ms = _parse_iso_ms(fallback_now_iso())
    return fallback_ms if fallback_ms is not None else time.time() * 1000


def _live_decision_key(strategy: str, bars: dict[str, list[EaRecord]]) -> str:
    # NOTE: momentum_scalp disabled, all strategies use full bar set(strategy 不参与)
    _ = strategy
    return _last_live_bar_ref(bars, "H1", "M15", "M5", "M30", "H4", "M1")


def _last_live_bar_ref(bars: dict[str, list[EaRecord]], *order: str) -> str:
    for timeframe in order:
        timeframe_bars = bars.get(timeframe)
        last = timeframe_bars[-1] if isinstance(timeframe_bars, list) and len(timeframe_bars) > 0 else None
        time_value = "" if last is None else _string_field(last, "time").strip()
        if len(time_value) > 0:
            return f"{timeframe}:{time_value}"
    return "no-bars"


def _live_command_id(account_id: str, symbol: str, signal: EaRecord, decision_key: str) -> str:
    seed = "|".join(
        [
            account_id,
            symbol.upper(),
            _string_field(signal, "strategy"),
            _string_field(signal, "side"),
            decision_key,
        ]
    )
    return f"live_{hashlib.sha1(seed.encode('utf-8')).hexdigest()[:16]}"


def _ai_stop_loss_command_id(account_id: str, symbol: str, ticket: float, now_iso: str) -> str:
    seed = "|".join([account_id, symbol.upper(), str(ticket), _utc_minute_key(now_iso)])
    return f"mod_{hashlib.sha1(seed.encode('utf-8')).hexdigest()[:16]}"


def _position_manager_command_id(account_id: str, symbol: str, command: dict[str, Any], now_iso: str) -> str:
    timestamp_key = _utc_minute_key(now_iso)
    # 归一化 reason:移除动态 dd 值(如 trail_tp1_dd1.2 → trail_tp1)
    # 防止同一仓位因 drawdown 数值变化生成不同 command_id,导致去重失效
    normalized_reason = re.sub(r"_dd[\d.]+", "", command.get("reason") or "")
    return "_".join(
        part
        for part in [
            "pm",
            _command_id_part(account_id),
            _command_id_part(symbol.upper()),
            str(command.get("ticket")),
            str(command.get("action") or "").lower(),
            _command_id_part(normalized_reason),
            timestamp_key,
        ]
        if len(part) > 0
    )


def _command_id_part(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_")[:48]


def _is_pending_position_record(position: EaRecord) -> bool:
    order_class = _string_field(position, "order_class") or _string_field(position, "orderClass")
    if order_class.lower() == "pending":
        return True
    if order_class.lower() == "market":
        return False
    type_value = _string_field(position, "type").upper()
    return "LIMIT" in type_value or "STOP" in type_value


def _is_stale_position(position: EaRecord, now_iso: str) -> bool:
    time_str = (
        _string_field(position, "time")
        or _string_field(position, "updated_at")
        or _string_field(position, "updatedAt")
    )
    if not time_str:
        return False
    ms = _parse_iso_ms(time_str)
    if ms is None:
        return False
    now_ms = _parse_iso_ms(now_iso)
    ref = now_ms if now_ms is not None else time.time() * 1000
    return ref - ms > STALE_POSITION_MS


def _ai_decision_id(ai_result: EaRecord) -> str:
    trade_plan = _record_field(ai_result, "trade_plan")
    if trade_plan is None:
        trade_plan = _record_field(ai_result, "tradePlan")
    trade_plan = trade_plan if trade_plan is not None else {}
    return _string_field(trade_plan, "decision_id") or _string_field(ai_result, "decision_id")


def _record_field(record: EaRecord, field: str) -> EaRecord | None:
    value = record.get(field)
    return value if value is not None and isinstance(value, dict) else None


def _utc_minute_key(value: str) -> str:
    millis = _parse_iso_ms(value)
    if millis is None:
        millis = time.time() * 1000
    dt = datetime.fromtimestamp(millis / 1000, tz=UTC)
    return f"{dt.year:04d}{dt.month:02d}{dt.day:02d}{dt.hour:02d}{dt.minute:02d}"


def _utc_date_key(value: str) -> str:
    millis = _parse_iso_ms(value)
    if millis is None:
        millis = time.time() * 1000
    dt = datetime.fromtimestamp(millis / 1000, tz=UTC)
    return f"{dt.year:04d}-{dt.month:02d}-{dt.day:02d}"


def _max_daily_loss_pct(*records: EaRecord | None) -> float:
    """日亏保护阈值来自 EA MaxDailyLoss(百分比),默认 5% → 0.05。"""
    for record in records:
        if record is None:
            continue
        raw = _number_field(record, "max_daily_loss")
        if raw > 0:
            return raw / 100.0 if raw > 1 else raw
    return 0.05


def _live_current_price(tick: EaRecord, bars: dict[str, list[EaRecord]]) -> float:
    bid = _number_field(tick, "bid")
    ask = _number_field(tick, "ask")
    if bid > 0 and ask > 0:
        return (bid + ask) / 2
    if ask > 0:
        return ask
    if bid > 0:
        return bid
    for timeframe in ("H1", "M15", "M5", "M1", "M30", "H4"):
        timeframe_bars = bars.get(timeframe)
        last = timeframe_bars[-1] if isinstance(timeframe_bars, list) and len(timeframe_bars) > 0 else {}
        close = _number_field(last, "close")
        if close > 0:
            return close
    return 0


def _latest_atr(bars: list[EaRecord]) -> float:
    last = bars[-1] if len(bars) > 0 else None
    if last is not None:
        lower_atr = _number_field(last, "atr")
        if lower_atr > 0:
            return lower_atr
        upper_atr = _number_field(last, "ATR")
        if upper_atr > 0:
            return upper_atr

    ohlc_bars = [
        bar
        for bar in bars
        if _number_field(bar, "open") > 0
        and _number_field(bar, "high") > 0
        and _number_field(bar, "low") > 0
        and _number_field(bar, "close") > 0
    ]
    if len(ohlc_bars) < 14:
        return 0
    atr_values = atr(
        [_number_field(bar, "high") for bar in ohlc_bars],
        [_number_field(bar, "low") for bar in ohlc_bars],
        [_number_field(bar, "close") for bar in ohlc_bars],
        14,
    )
    for value in reversed(atr_values):
        if _is_finite_number(value) and value > 0:
            return float(value)
    return 0


def _stoplevel_min_ratio(symbol: str) -> float:
    normalized = symbol.strip().upper()
    # 归一化对齐 riskgate/analysis 的 baseSymbol 写法:去 broker 后缀(如 GBPJPYm#、GBPJPY#)
    if normalized.endswith("M#"):
        normalized = normalized[:-2]
    if normalized.endswith("#"):
        normalized = normalized[:-1]
    return STOPLEVEL_MIN_RATIO_BY_SYMBOL.get(normalized, STOPLEVEL_MIN_RATIO_DEFAULT)


def _ai_stop_loss_profit_atr(position: EaRecord, atr_value: float, current_price: float) -> float | None:
    open_price = _number_field(position, "open_price") or _number_field(position, "openPrice")
    if atr_value <= 0 or current_price <= 0 or open_price <= 0:
        return None
    side = _string_field(position, "type").upper()
    if side == "BUY":
        return (current_price - open_price) / atr_value
    if side == "SELL":
        return (open_price - current_price) / atr_value
    return None


def _order_type_for_signal(price: float, entry: float, atr_value: float, side: str) -> str:
    if atr_value <= 0:
        return "market"
    if abs(price - entry) <= atr_value * 0.3:
        return "market"
    if side == "BUY":
        return "BUY_LIMIT" if entry <= price else "BUY_STOP"
    return "SELL_LIMIT" if entry >= price else "SELL_STOP"


def _unix_seconds(value: str) -> int:
    millis = _parse_iso_ms(value)
    if millis is None:
        millis = time.time() * 1000
    return math.floor(millis / 1000)


def _parse_iso_ms(value: str) -> float | None:
    try:
        text = value[:-1] + "+00:00" if value.endswith("Z") else value
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        millis = dt.timestamp() * 1000
    except (TypeError, ValueError):
        return None
    return millis if math.isfinite(millis) else None


def _to_fixed(value: float, digits: int) -> float:
    """镜像 JS Number.prototype.toFixed(半向 +inf 舍入),返回数值。"""
    factor = 10**digits
    return math.floor(value * factor + 0.5) / factor


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _number_field(record: EaRecord, field: str) -> float:
    value = record.get(field)
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value)
    return 0.0


def _positive_number_field(record: EaRecord, field: str) -> float | None:
    value = record.get(field)
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and math.isfinite(float(value)) and value > 0:
        return float(value)
    return None


def _string_field(record: EaRecord, field: str) -> str:
    value = record.get(field)
    return value if isinstance(value, str) else ""


def _boolean_field(record: EaRecord, field: str) -> bool:
    return record.get(field) is True


def _is_finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _find_position(positions: list[EaRecord], ticket: Any) -> EaRecord | None:
    for position in positions:
        if _number_field(position, "ticket") == ticket:
            return position
    return None
