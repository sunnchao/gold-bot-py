"""SOP 双层盯盘巡检与日/周收盘复盘服务: 冷巡检过滤 + 高周期(1H/4H)方向锚定 + M15 入场切入 + D1 日/周复盘。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime, timedelta
from math import isfinite
from typing import Any, Literal

from backend.agents.tools.market_structure_sop import (
    analyze_daily_vacuum,
    daily_bars_through_close,
    is_weekly_close_bar,
    summarize_daily_close_recap,
    summarize_weekly_close_recap,
)
from backend.persistence.helpers import DEFAULT_ACTIVE_ACCOUNT_TTL_SECONDS, is_heartbeat_active
from backend.persistence.store import EaStore
from backend.services.sop.alerts import AlertRouter
from backend.services.sop.cards import (
    build_daily_close_review_card,
    build_htf_review_card,
    build_m15_review_card,
    build_patrol_cycle_card,
    build_weekly_close_review_card,
)
from backend.services.sop.ledger import (
    empty_watch_ledger,
    evaluate_ledger_patrol,
    is_failed_review_result,
    is_material_change,
    normalize_watch_ledger,
    reconcile_ledger_after_review,
)
from backend.trading_core.riskgate.riskgate import (
    evaluate_market_filters,
    friday_close_reminder_stage,
    is_monday_open_observation_window,
)


def _default_now() -> datetime:
    return datetime.now(UTC)


def _norm_symbol(symbol: str) -> str:
    return symbol.strip().upper()


def _to_plain_dict(obj: Any) -> dict[str, Any] | None:
    if isinstance(obj, dict):
        return obj
    if is_dataclass(obj) and not isinstance(obj, type):
        return asdict(obj)
    return None


def _extract_ai_result(workflow_out: dict[str, Any] | None, symbol: str) -> dict[str, Any] | None:
    if not isinstance(workflow_out, dict):
        return None
    direct = _to_plain_dict(workflow_out.get("ai_result"))
    if direct is not None:
        return direct
    final_signals = workflow_out.get("finalSignals")
    if isinstance(final_signals, dict):
        for sym_key in (symbol, _norm_symbol(symbol)):
            found = _to_plain_dict(final_signals.get(sym_key))
            if found is not None:
                return found
    final_single = _to_plain_dict(workflow_out.get("finalSignal"))
    if final_single is not None:
        return final_single
    results = workflow_out.get("results")
    if isinstance(results, dict):
        for sym_key in (symbol, _norm_symbol(symbol)):
            sym_res = results.get(sym_key)
            if isinstance(sym_res, dict):
                nested = _to_plain_dict(sym_res.get("ai_result"))
                if nested is not None:
                    return nested
    return None


def _extract_snapshot_context(
    workflow_out: dict[str, Any] | None,
    symbol: str,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]] | None]:
    if not isinstance(workflow_out, dict):
        return None, None
    payload = _to_plain_dict(workflow_out.get("payload"))
    if payload is None and isinstance(workflow_out.get("payloads"), dict):
        payload = _to_plain_dict(
            workflow_out["payloads"].get(symbol) or workflow_out["payloads"].get(_norm_symbol(symbol))
        )
    if payload is None and isinstance(workflow_out.get("results"), dict):
        sym_res = workflow_out["results"].get(symbol) or workflow_out["results"].get(_norm_symbol(symbol))
        if isinstance(sym_res, dict):
            payload = _to_plain_dict(sym_res.get("payload"))
    if not isinstance(payload, dict):
        return None, None
    daily = payload.get("daily_structure") if isinstance(payload.get("daily_structure"), dict) else None
    conf = payload.get("confluence_levels") if isinstance(payload.get("confluence_levels"), list) else None
    return daily, conf


class PatrolService:
    """管理按 (account_id, symbol) 加锁的冷/热分离 SOP 巡检流程。"""

    def __init__(
        self,
        *,
        store: EaStore,
        workflow_service: Any,
        alert_router: AlertRouter | None = None,
        execution_mode: Literal["advisory_only", "integrated"] = "advisory_only",
        htf_review_minutes: int = 60,
        move_trigger_pct: float = 0.5,
        m15_always_run_entry: bool = False,
        active_account_ttl_seconds: int = DEFAULT_ACTIVE_ACCOUNT_TTL_SECONDS,
        patrol_interval_seconds: int = 300,
        notify_on_noop: bool = True,
        patrol_report_cooldown_seconds: int = 0,
        queue_pending_fn: Callable[..., Awaitable[Any]] | None = None,
        snapshot_fn: Callable[[str, str], Awaitable[dict[str, Any]]] | None = None,
        now_fn: Callable[[], datetime] = _default_now,
    ) -> None:
        self._store = store
        self._workflow = workflow_service
        self._alerts = alert_router or AlertRouter(now_fn=now_fn)
        self.execution_mode: Literal["advisory_only", "integrated"] = (
            "integrated" if execution_mode == "integrated" else "advisory_only"
        )
        self.htf_review_minutes = max(5, int(htf_review_minutes))
        self.move_trigger_pct = max(0.05, float(move_trigger_pct))
        self.m15_always_run_entry = bool(m15_always_run_entry)
        self.active_account_ttl_seconds = max(60, int(active_account_ttl_seconds))
        self.patrol_interval_seconds = max(15, int(patrol_interval_seconds))
        self.notify_on_noop = bool(notify_on_noop)
        self.patrol_report_cooldown_seconds = max(0, int(patrol_report_cooldown_seconds))
        self._queue_pending_fn = queue_pending_fn
        self._snapshot_fn = snapshot_fn
        self._now_fn = now_fn
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._consecutive_failures: dict[tuple[str, str], int] = {}
        self._last_cycle_report_at: dict[str, datetime] = {}

    def _lock_for(self, account_id: str, symbol: str) -> asyncio.Lock:
        key = (account_id, _norm_symbol(symbol))
        if key not in self._locks:
            self._locks[key] = asyncio.Lock()
        return self._locks[key]

    async def _default_snapshot(self, account_id: str, symbol: str) -> dict[str, Any]:
        norm_sym = _norm_symbol(symbol)
        tick = await self._store.get_latest_tick(account_id, norm_sym)
        heartbeat = await self._store.get_heartbeat(account_id)
        bars_by_tf: dict[str, Sequence[dict[str, Any]]] = {}
        for tf in ("M15", "M30", "H1", "H4", "D1"):
            bars_by_tf[tf] = await self._store.get_bars(account_id, norm_sym, tf, limit=200)

        price: float | None = None
        if isinstance(tick, dict):
            for k in ("bid", "ask", "last"):
                val = tick.get(k)
                if isinstance(val, (int, float)) and not isinstance(val, bool) and isfinite(val):
                    price = float(val)
                    break
        if price is None and bars_by_tf.get("M15"):
            raw_c = bars_by_tf["M15"][-1].get("close")
            if isinstance(raw_c, (int, float)) and not isinstance(raw_c, bool) and isfinite(raw_c):
                price = float(raw_c)

        now_dt = self._now_fn()
        if now_dt.tzinfo is None:
            now_dt = now_dt.replace(tzinfo=UTC)
        now_iso = now_dt.isoformat().replace("+00:00", "Z")

        market_open = (heartbeat.get("market_open") is not False) if isinstance(heartbeat, dict) else True
        trade_allowed = (heartbeat.get("is_trade_allowed") is not False) if isinstance(heartbeat, dict) else True
        stale = tick is None and not bars_by_tf.get("M15")

        last_tick_at = ""
        if isinstance(tick, dict):
            for ts_key in ("received_at", "updated_at"):
                raw_ts = tick.get(ts_key)
                if isinstance(raw_ts, str) and raw_ts.strip():
                    last_tick_at = raw_ts.strip()
                    break
            if not last_tick_at and tick:
                last_tick_at = now_iso

        raw_spread = tick.get("spread") if isinstance(tick, dict) else None
        spread = (
            float(raw_spread)
            if isinstance(raw_spread, (int, float)) and not isinstance(raw_spread, bool) and isfinite(raw_spread)
            else 0.0
        )

        configured_max_spread: float | None = None
        for source in (tick, heartbeat):
            if not isinstance(source, dict):
                continue
            cand = source.get("max_spread")
            if isinstance(cand, (int, float)) and not isinstance(cand, bool) and isfinite(cand) and cand > 0:
                configured_max_spread = float(cand)
                break

        tick_state: dict[str, Any] = dict(tick) if isinstance(tick, dict) else {}
        tick_state["symbol"] = norm_sym
        tick_state["spread"] = spread
        if configured_max_spread is not None:
            tick_state["maxSpread"] = configured_max_spread

        filters = evaluate_market_filters(
            {
                "symbol": norm_sym,
                "now": now_dt,
                "includeSopSessionWarnings": True,
                "runtime": {
                    "marketOpen": market_open,
                    "isTradeAllowed": trade_allowed,
                    "lastTickAt": last_tick_at,
                },
                "state": {
                    "heartbeat": heartbeat or {},
                    "tick": tick_state,
                    "bars": bars_by_tf,
                },
            }
        )
        return {
            "price": price,
            "bars_by_tf": bars_by_tf,
            "market_open": market_open,
            "data_stale": stale,
            "market_filters": filters,
        }

    async def _record_failure(
        self,
        *,
        account_id: str,
        symbol: str,
        trigger_type: str,
        reason: str,
    ) -> dict[str, Any]:
        key = (account_id, _norm_symbol(symbol))
        count = self._consecutive_failures.get(key, 0) + 1
        self._consecutive_failures[key] = count

        existing = await self._store.get_watch_ledger(account_id, key[1])
        if existing is not None:
            updated = normalize_watch_ledger(existing, account_id, key[1])
            updated["consecutive_failures"] = count
            await self._store.save_watch_ledger(account_id, key[1], updated)

        # 单次失败静默重试; 连续 >=2 次才发 urgent 告警
        if count >= 2:
            await self._alerts.route(
                account_id=account_id,
                symbol=key[1],
                level="urgent",
                category="data_failure",
                title=f"GOLD-BOT 异常告警 · {key[1]}",
                content=f"连续 {count} 次数据/巡检异常: {reason}",
                dedup_key=f"fail:{reason[:40]}",
            )
            log_level = "urgent"
            action = "data_failure_alert"
        else:
            log_level = "silent"
            action = "data_failure_silent_retry"

        await self._store.append_patrol_log(
            {
                "account_id": account_id,
                "symbol": key[1],
                "trigger_type": trigger_type,
                "tier": "patrol",
                "alert_level": log_level,
                "action_taken": action,
                "summary": reason,
                "details": {"consecutive_failures": count},
            }
        )
        return {
            "status": "failure",
            "symbol": key[1],
            "current_price": None,
            "consecutive_failures": count,
            "alert_level": log_level,
            "action_taken": action,
            "summary": reason,
            "reason": reason,
        }

    async def run_patrol(
        self,
        account_id: str,
        symbol: str,
        *,
        trigger_type: str = "patrol",
        snapshot: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        norm_sym = _norm_symbol(symbol)
        lock = self._lock_for(account_id, norm_sym)
        async with lock:
            return await self._run_locked(
                account_id=account_id,
                symbol=norm_sym,
                trigger_type=trigger_type,
                snapshot=snapshot,
            )

    async def _run_locked(
        self,
        *,
        account_id: str,
        symbol: str,
        trigger_type: str,
        snapshot: dict[str, Any] | None,
    ) -> dict[str, Any]:
        now_dt = self._now_fn()
        if now_dt.tzinfo is None:
            now_dt = now_dt.replace(tzinfo=UTC)

        if snapshot is None and self._snapshot_fn is None and trigger_type not in {"manual_htf", "manual_m15"}:
            heartbeat = await self._store.get_heartbeat(account_id)
            if not is_heartbeat_active(
                heartbeat,
                now=now_dt,
                ttl_seconds=self.active_account_ttl_seconds,
                require_timestamp=False,
            ):
                return {
                    "status": "skipped",
                    "reason": "inactive_account",
                    "htf_ran": False,
                    "m15_ran": False,
                    "alert_level": "silent",
                    "action_taken": "skipped_inactive_account",
                    "cancelled_pending": False,
                    "trigger_reasons": [],
                    "ledger": None,
                    "log": None,
                }

        try:
            snap = snapshot if snapshot is not None else (
                await self._snapshot_fn(account_id, symbol)
                if self._snapshot_fn is not None
                else await self._default_snapshot(account_id, symbol)
            )
        except Exception as exc:
            return await self._record_failure(
                account_id=account_id,
                symbol=symbol,
                trigger_type=trigger_type,
                reason=f"行情读取异常: {exc}",
            )

        current_price = snap.get("price")
        data_stale = bool(snap.get("data_stale"))
        market_open = bool(snap.get("market_open", True))
        if data_stale or not isinstance(current_price, (int, float)) or not isfinite(current_price):
            return await self._record_failure(
                account_id=account_id,
                symbol=symbol,
                trigger_type=trigger_type,
                reason="最新价格或 K 线数据缺失/过期",
            )

        # 数据恢复后清零连续失败计数
        self._consecutive_failures[(account_id, symbol)] = 0

        raw_mf = snap.get("market_filters")
        market_filters: dict[str, Any] = raw_mf if isinstance(raw_mf, dict) else {}
        market_blocked = bool(market_filters.get("blocked"))
        blocking_codes = [
            str(b.get("code")) if isinstance(b, dict) else str(b)
            for b in (market_filters.get("blocking") or [])
        ]
        if any(code.startswith("event.") for code in blocking_codes):
            await self._alerts.route(
                account_id=account_id,
                symbol=symbol,
                level="notify",
                category="session.event_blackout",
                title=f"GOLD-BOT 财经事件管制 · {symbol}",
                content=f"当前处于事件管制窗口 ({', '.join(blocking_codes)}), M15 入场触发已抑制。",
                dedup_key=f"ev:{now_dt.date().isoformat()}:{','.join(blocking_codes)}",
            )

        # 时段级提示 (周一早盘观察 / 周五收盘前提醒)
        if is_monday_open_observation_window(now_dt, symbol):
            await self._alerts.route(
                account_id=account_id,
                symbol=symbol,
                level="notify",
                category="session.monday_open",
                title=f"GOLD-BOT 周一开盘观察 · {symbol}",
                content="当前处于周一早盘开盘观察窗口, 优先等待流动性和缺口稳定后再执行 M15 入场。",
                dedup_key=f"mon:{now_dt.date().isoformat()}",
            )
        friday_stage = friday_close_reminder_stage(now_dt, symbol)
        if friday_stage is not None:
            await self._alerts.route(
                account_id=account_id,
                symbol=symbol,
                level="urgent" if friday_stage == "flatten_urgent" else "digest",
                category=f"session.{friday_stage}",
                title=f"GOLD-BOT 周五收盘提醒 · {symbol}",
                content=(
                    "临近周末休市, 请尽快核查持仓与未成交挂单!"
                    if friday_stage == "flatten_urgent"
                    else "周五收盘前平仓窗口临近, 建议逐步减仓或收紧保护。"
                ),
                dedup_key=f"fri:{friday_stage}:{now_dt.date().isoformat()}",
            )

        existing = await self._store.get_watch_ledger(account_id, symbol)
        bars_by_tf = snap.get("bars_by_tf") if isinstance(snap.get("bars_by_tf"), dict) else {}

        patrol_eval = evaluate_ledger_patrol(
            existing,
            account_id=account_id,
            symbol=symbol,
            current_price=float(current_price),
            bars_by_tf=bars_by_tf,
            htf_review_minutes=self.htf_review_minutes,
            move_trigger_pct=self.move_trigger_pct,
            now=now_dt,
            market_open=market_open,
            data_stale=False,
        )
        working_ledger = patrol_eval["updated_ledger"]
        working_ledger["consecutive_failures"] = 0

        # 触发即先写入状态迁移, 并路由冷路径识别出的告警候选
        for cand in patrol_eval["alert_candidates"]:
            await self._alerts.route(
                account_id=account_id,
                symbol=symbol,
                level=str(cand.get("level") or "notify"),
                category=str(cand.get("category") or "patrol_event"),
                title=f"GOLD-BOT 巡检提醒 · {symbol}",
                content=str(cand.get("summary") or ""),
                dedup_key=cand.get("dedup_key"),
            )

        if patrol_eval["state_transitions"]:
            working_ledger = await self._store.save_watch_ledger(account_id, symbol, working_ledger)

        # integrated 模式下若否决线破位/假突破收回/逻辑穷尽, 建议撤除挂单
        cancelled_pending = False
        if self.execution_mode == "integrated" and patrol_eval["cancel_pending_recommended"]:
            cancelled_pending = await self._enqueue_cancel_pending(account_id, symbol, patrol_eval["trigger_reasons"])

        htf_ran = False
        htf_updated = False
        m15_ran = False
        highest_alert = "silent"
        tier = "patrol"
        action_taken = "no_op"
        summary = f"{symbol} 冷巡检无实质触发 (price={current_price})"

        # Tier 1: HTF 宏观/日内方向锚定
        if patrol_eval["should_trigger_htf"] or trigger_type == "manual_htf":
            htf_ran = True
            tier = "htf_anchor"
            wf_out = await self._workflow.run(
                account_id,
                symbol,
                {
                    "analysisRole": "htf_anchor",
                    "previousLedger": working_ledger,
                    "patrolContext": {
                        "trigger_type": trigger_type,
                        "trigger_reasons": patrol_eval["trigger_reasons"],
                        "current_price": float(current_price),
                    },
                    "skipFeishu": True,
                    "skipPublish": True,
                },
            )
            ai_res = _extract_ai_result(wf_out, symbol) or {}
            daily_struct, conf_levels = _extract_snapshot_context(wf_out, symbol)
            if existing is not None and is_failed_review_result(ai_res):
                action_taken = "htf_review_failed"
                highest_alert = "notify"
                summary = f"{symbol} 高周期(1H/4H)复盘失败(保留既有锚定)"
            else:
                htf_updated = True
                prev_snapshot = deepcopy_dict(working_ledger) if existing is not None else None
                working_ledger = reconcile_ledger_after_review(
                    working_ledger,
                    account_id=account_id,
                    symbol=symbol,
                    role="htf_anchor",
                    review_result=ai_res,
                    daily_structure=daily_struct,
                    confluence_levels=conf_levels,
                    current_price=float(current_price),
                    now=now_dt,
                )
                working_ledger = await self._store.save_watch_ledger(account_id, symbol, working_ledger)
                changed, change_reasons = is_material_change(prev_snapshot, working_ledger)
                action_taken = "htf_reviewed"
                highest_alert = "notify"
                anchor = working_ledger["htf_anchor"]
                if changed:
                    summary = (
                        f"{symbol} 高周期(1H/4H)方向更新: 方向={anchor.get('intraday_direction')} "
                        f"短期目标={anchor.get('short_term_targets')} 变动={','.join(change_reasons)}"
                    )
                else:
                    summary = f"{symbol} 高周期(1H/4H)例行复盘完成(核心结论无实质变更)"
                htf_title, htf_content, htf_card = build_htf_review_card(
                    account_id=account_id,
                    symbol=symbol,
                    current_price=float(current_price),
                    ledger=working_ledger,
                    ai_result=ai_res,
                    trigger_reasons=patrol_eval["trigger_reasons"],
                    changed=changed,
                    change_reasons=change_reasons,
                    execution_mode=self.execution_mode,
                    now=now_dt,
                )
                await self._alerts.route(
                    account_id=account_id,
                    symbol=symbol,
                    level="notify",
                    category="htf_anchor_update" if changed else "htf_routine_review",
                    title=htf_title,
                    content=htf_content,
                    card=htf_card,
                    dedup_key=f"htf:{working_ledger.get('version')}:{working_ledger.get('htf_reviewed_at')}",
                    bypass_cooldown=True,
                )

        # Tier 2: M15 入场时机切入 (严格以 persisted WatchLedger HTF 锚定为前提)
        run_m15 = (
            (
                trigger_type == "m15_close"
                and (patrol_eval["should_trigger_m15"] or htf_updated or self.m15_always_run_entry)
            )
            or trigger_type == "manual_m15"
        ) and not market_blocked
        if run_m15:
            m15_ran = True
            tier = "m15_entry"
            wf_out = await self._workflow.run(
                account_id,
                symbol,
                {
                    "analysisRole": "m15_entry",
                    "previousLedger": working_ledger,
                    "patrolContext": {
                        "trigger_type": trigger_type,
                        "trigger_reasons": patrol_eval["trigger_reasons"],
                        "current_price": float(current_price),
                    },
                    "skipFeishu": True,
                },
            )
            ai_res = _extract_ai_result(wf_out, symbol) or {}
            if is_failed_review_result(ai_res):
                action_taken = "m15_review_failed"
                if highest_alert == "silent":
                    highest_alert = "notify"
                summary = f"{symbol} M15 切入巡检失败(保留既有剧本与锚定)"
            else:
                prev_snapshot = deepcopy_dict(working_ledger)
                working_ledger = reconcile_ledger_after_review(
                    working_ledger,
                    account_id=account_id,
                    symbol=symbol,
                    role="m15_entry",
                    review_result=ai_res,
                    current_price=float(current_price),
                    now=now_dt,
                )
                working_ledger = await self._store.save_watch_ledger(account_id, symbol, working_ledger)

                trade_action = str(ai_res.get("trade_action") or "").upper()
                raw_rec = ai_res.get("trade_recommendation")
                rec: dict[str, Any] = raw_rec if isinstance(raw_rec, dict) else {}
                rec_action = str(rec.get("action") or rec.get("direction") or "").upper()
                raw_arb = ai_res.get("arbitration")
                arb: dict[str, Any] = raw_arb if isinstance(raw_arb, dict) else {}
                raw_plan = ai_res.get("trade_plan")
                plan: dict[str, Any] = raw_plan if isinstance(raw_plan, dict) else {}
                plan_side = str(plan.get("side") or "").upper()
                plan_mode = str(plan.get("mode") or "").lower()
                arb_action = str(arb.get("action") or "").lower()
                entry_val = plan.get("entry_price")
                raw_ez = plan.get("entry_zone")
                if entry_val is None and isinstance(raw_ez, dict):
                    entry_val = raw_ez.get("min")
                if entry_val is None:
                    entry_val = rec.get("entry_price")
                sl_val = plan.get("stop_loss")
                if sl_val is None:
                    sl_val = rec.get("stop_loss")
                entry_num = float(entry_val) if isinstance(entry_val, (int, float)) else 0.0
                sl_num = float(sl_val) if isinstance(sl_val, (int, float)) else 0.0
                has_valid_prices = entry_num > 0 and sl_num > 0
                plan_executable = not plan_mode or plan_mode == "approve"
                is_actionable = plan_executable and has_valid_prices and (
                    trade_action in {"BUY", "SELL"}
                    or (rec_action in {"BUY", "SELL"} and arb_action in {"", "open"})
                    or (arb_action == "open" and plan_side in {"BUY", "SELL"})
                )

                side_label = (
                    trade_action
                    if trade_action in {"BUY", "SELL"}
                    else (rec_action if rec_action in {"BUY", "SELL"} else plan_side)
                )
                m15_title, m15_content, m15_card = build_m15_review_card(
                    account_id=account_id,
                    symbol=symbol,
                    current_price=float(current_price),
                    ledger=working_ledger,
                    ai_result=ai_res,
                    trigger_reasons=patrol_eval["trigger_reasons"],
                    is_actionable=is_actionable,
                    side_label=side_label,
                    execution_mode=self.execution_mode,
                    now=now_dt,
                )

                if is_actionable:
                    highest_alert = "actionable"
                    action_taken = "m15_entry_signal"
                    plan_tp = plan.get("take_profits") or plan.get("take_profit")
                    summary = (
                        f"{symbol} M15 入场触发 [{side_label}] "
                        f"入场={entry_num} "
                        f"SL={sl_num} TP={plan_tp}"
                    )
                    await self._alerts.route(
                        account_id=account_id,
                        symbol=symbol,
                        level="actionable",
                        category="m15_entry_signal",
                        title=m15_title,
                        content=m15_content,
                        card=m15_card,
                        dedup_key=f"m15:{side_label}:{entry_num}:{working_ledger.get('m15_reviewed_at')}",
                        bypass_cooldown=True,
                    )
                    if self.execution_mode == "integrated" and self._queue_pending_fn is not None:
                        await self._queue_pending_fn(account_id, symbol, ai_res)
                else:
                    _changed_m15, _ = is_material_change(prev_snapshot, working_ledger)
                    action_taken = "m15_wait"
                    if highest_alert == "silent":
                        highest_alert = "notify"
                    summary = f"{symbol} M15 切入巡检: 保持观察 (未触发下单条件)"
                    await self._alerts.route(
                        account_id=account_id,
                        symbol=symbol,
                        level="notify",
                        category="m15_entry_review",
                        title=m15_title,
                        content=m15_content,
                        card=m15_card,
                        dedup_key=f"m15_wait:{working_ledger.get('version')}:{working_ledger.get('m15_reviewed_at')}",
                        bypass_cooldown=True,
                    )

        # 若未触发任何 LLM 调用但也发生了状态迁徙
        if not htf_ran and not m15_ran and patrol_eval["state_transitions"]:
            action_taken = "state_transition_only"
            highest_alert = max(
                (str(c.get("level") or "notify") for c in patrol_eval["alert_candidates"]),
                default="notify",
                key=lambda lv: {"silent": 0, "digest": 1, "notify": 2, "actionable": 3, "urgent": 4}.get(lv, 0),
            )
            summary = f"{symbol} 冷巡检状态迁徙: {len(patrol_eval['state_transitions'])} 项"

        # 保底初始化持久化账本(若尚无任何持久化记录)
        if existing is None and not htf_ran and not m15_ran:
            working_ledger = await self._store.save_watch_ledger(
                account_id, symbol, empty_watch_ledger(account_id, symbol)
            )

        log_entry = await self._store.append_patrol_log(
            {
                "account_id": account_id,
                "symbol": symbol,
                "trigger_type": trigger_type,
                "tier": tier,
                "alert_level": highest_alert,
                "action_taken": action_taken,
                "summary": summary,
                "details": {
                    "trigger_reasons": patrol_eval["trigger_reasons"],
                    "state_transitions": patrol_eval["state_transitions"],
                    "htf_ran": htf_ran,
                    "m15_ran": m15_ran,
                    "cancelled_pending": cancelled_pending,
                    "execution_mode": self.execution_mode,
                },
            }
        )
        return {
            "status": "ok",
            "symbol": symbol,
            "current_price": float(current_price),
            "summary": summary,
            "htf_ran": htf_ran,
            "m15_ran": m15_ran,
            "alert_level": highest_alert,
            "action_taken": action_taken,
            "cancelled_pending": cancelled_pending,
            "trigger_reasons": patrol_eval["trigger_reasons"],
            "ledger": working_ledger,
            "log": log_entry,
        }

    async def notify_patrol_cycle(
        self,
        account_id: str,
        results: Sequence[dict[str, Any]],
    ) -> dict[str, Any] | None:
        valid = [r for r in results if isinstance(r, dict) and r.get("status") in {"ok", "failure"}]
        if not valid:
            return None
        all_noop = all(r.get("status") == "ok" and r.get("action_taken") == "no_op" for r in valid)
        if all_noop and not self.notify_on_noop:
            return {"sent": False, "reason": "noop_disabled"}

        now_dt = self._now_fn()
        if now_dt.tzinfo is None:
            now_dt = now_dt.replace(tzinfo=UTC)

        if self.patrol_report_cooldown_seconds > 0 and all_noop:
            prev_dt = self._last_cycle_report_at.get(account_id)
            if prev_dt is not None:
                elapsed = (now_dt - prev_dt).total_seconds()
                if elapsed < self.patrol_report_cooldown_seconds:
                    return {
                        "sent": False,
                        "suppressed": True,
                        "reason": "report_cooldown",
                        "elapsed_seconds": elapsed,
                    }

        title, content, card = build_patrol_cycle_card(
            account_id=account_id,
            results=valid,
            interval_seconds=self.patrol_interval_seconds,
            execution_mode=self.execution_mode,
            now=now_dt,
        )
        res = await self._alerts.route(
            account_id=account_id,
            symbol="ALL",
            level="digest",
            category="patrol_cycle_report",
            title=title,
            content=content,
            card=card,
            dedup_key=f"patrol:{account_id}:{now_dt.isoformat()}",
            bypass_cooldown=True,
        )
        if res.get("sent"):
            self._last_cycle_report_at[account_id] = now_dt
        return res

    async def _is_post_close_account_active(self, account_id: str, now_dt: datetime) -> bool:
        heartbeat = await self._store.get_heartbeat(account_id)
        return is_heartbeat_active(
            heartbeat,
            now=now_dt,
            ttl_seconds=max(self.active_account_ttl_seconds, 48 * 3600),
            require_timestamp=True,
        )

    async def _load_review_bars(
        self,
        account_id: str,
        symbol: str,
        snapshot: dict[str, Any] | None,
    ) -> dict[str, Sequence[dict[str, Any]]]:
        if isinstance(snapshot, dict) and isinstance(snapshot.get("bars_by_tf"), dict):
            return dict(snapshot["bars_by_tf"])
        out: dict[str, Sequence[dict[str, Any]]] = {}
        for tf in ("D1", "H4", "H1"):
            out[tf] = await self._store.get_bars(account_id, symbol, tf, limit=200)
        return out

    async def run_daily_close_review(
        self,
        account_id: str,
        symbol: str,
        *,
        bar_time: str | None = None,
        manual: bool = False,
        snapshot: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """D1 收盘后的日内走势复盘分析（不修改日内锚定账本，不触发开仓）。"""
        norm_sym = _norm_symbol(symbol)
        lock = self._lock_for(account_id, norm_sym)
        async with lock:
            now_dt = self._now_fn()
            if now_dt.tzinfo is None:
                now_dt = now_dt.replace(tzinfo=UTC)

            if not manual and snapshot is None:
                if not await self._is_post_close_account_active(account_id, now_dt):
                    return {"status": "skipped", "reason": "inactive_account", "symbol": norm_sym}

            bars_by_tf = await self._load_review_bars(account_id, norm_sym, snapshot)
            d1_bars = list(bars_by_tf.get("D1") or [])
            h4_bars = list(bars_by_tf.get("H4") or [])
            h1_bars = list(bars_by_tf.get("H1") or [])
            daily_recap = summarize_daily_close_recap(
                d1_bars,
                h4_bars=h4_bars,
                h1_bars=h1_bars,
                target_bar_time=bar_time,
            )
            if daily_recap.get("status") != "ok":
                return {"status": "skipped", "reason": "insufficient_d1_data", "symbol": norm_sym}

            trade_date = str(daily_recap.get("trade_date") or "")
            if not manual and trade_date:
                claimed = await self._store.claim_bar_close_event(
                    account_id, norm_sym, "D1_DAILY_REVIEW", trade_date
                )
                if not claimed:
                    return {
                        "status": "skipped",
                        "reason": "already_reviewed",
                        "symbol": norm_sym,
                        "trade_date": trade_date,
                    }

            close_px = float(daily_recap["close"])
            daily_structure = analyze_daily_vacuum(
                daily_bars_through_close(d1_bars, daily_recap["bar_time"]), current_price=close_px,
            )
            existing_ledger = await self._store.get_watch_ledger(account_id, norm_sym)

            wf_out = await self._workflow.run(
                account_id,
                norm_sym,
                {
                    "analysisRole": "daily_close_review",
                    "previousLedger": existing_ledger,
                    "patrolContext": {
                        "trigger_type": "manual_daily_review" if manual else "d1_close",
                        "bar_time": bar_time or daily_recap.get("bar_time"),
                        "daily_recap": daily_recap,
                        "daily_structure": daily_structure,
                    },
                    "skipFeishu": True,
                    "skipPublish": True,
                    "forceAnalyze": True,
                },
            )
            ai_res = _extract_ai_result(wf_out, norm_sym) or {}
            title, content, card = build_daily_close_review_card(
                account_id=account_id,
                symbol=norm_sym,
                daily_recap=daily_recap,
                daily_structure=daily_structure,
                ai_result=ai_res,
                execution_mode=self.execution_mode,
                now=now_dt,
            )
            await self._alerts.route(
                account_id=account_id,
                symbol=norm_sym,
                level="notify",
                category="daily_close_review",
                title=title,
                content=content,
                card=card,
                dedup_key=f"daily:{account_id}:{norm_sym}:{trade_date}",
                bypass_cooldown=True,
            )
            summary = (
                f"{norm_sym} 每日收盘复盘 ({trade_date}): "
                f"{daily_recap.get('candle_type')} {daily_recap.get('change_pct')}%, "
                f"结构={daily_structure.get('macro_bias')}"
            )
            log_entry = await self._store.append_patrol_log(
                {
                    "account_id": account_id,
                    "symbol": norm_sym,
                    "trigger_type": "manual_daily_review" if manual else "d1_close",
                    "tier": "daily_close_review",
                    "alert_level": "notify",
                    "action_taken": "daily_close_reviewed",
                    "summary": summary,
                    "details": {
                        "trade_date": trade_date,
                        "daily_recap": daily_recap,
                        "daily_structure_bias": daily_structure.get("macro_bias"),
                    },
                }
            )
            return {
                "status": "ok",
                "symbol": norm_sym,
                "trade_date": trade_date,
                "summary": summary,
                "daily_recap": daily_recap,
                "daily_structure": daily_structure,
                "log": log_entry,
            }

    async def run_weekly_close_review(
        self,
        account_id: str,
        symbol: str,
        *,
        bar_time: str | None = None,
        manual: bool = False,
        snapshot: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """每周收盘后的周内走势复盘分析（使用 D1 周度汇总，不修改日内账本，不触发开仓）。"""
        norm_sym = _norm_symbol(symbol)
        lock = self._lock_for(account_id, norm_sym)
        async with lock:
            now_dt = self._now_fn()
            if now_dt.tzinfo is None:
                now_dt = now_dt.replace(tzinfo=UTC)

            if not manual and snapshot is None:
                if not await self._is_post_close_account_active(account_id, now_dt):
                    return {"status": "skipped", "reason": "inactive_account", "symbol": norm_sym}

            bars_by_tf = await self._load_review_bars(account_id, norm_sym, snapshot)
            d1_bars = list(bars_by_tf.get("D1") or [])
            weekly_recap = summarize_weekly_close_recap(d1_bars, reference_bar_time=bar_time)
            if weekly_recap.get("status") != "ok":
                return {"status": "skipped", "reason": "insufficient_d1_data", "symbol": norm_sym}

            iso_week = str(weekly_recap.get("iso_week") or "")
            if not manual and iso_week:
                claimed = await self._store.claim_bar_close_event(
                    account_id, norm_sym, "W1_WEEKLY_REVIEW", iso_week
                )
                if not claimed:
                    return {
                        "status": "skipped",
                        "reason": "already_reviewed",
                        "symbol": norm_sym,
                        "iso_week": iso_week,
                    }

            wk_close = float(weekly_recap["week_close"])
            daily_structure = analyze_daily_vacuum(
                daily_bars_through_close(d1_bars, weekly_recap["week_end_date"]), current_price=wk_close,
            )
            existing_ledger = await self._store.get_watch_ledger(account_id, norm_sym)

            wf_out = await self._workflow.run(
                account_id,
                norm_sym,
                {
                    "analysisRole": "weekly_close_review",
                    "previousLedger": existing_ledger,
                    "patrolContext": {
                        "trigger_type": "manual_weekly_review" if manual else "weekly_close",
                        "bar_time": bar_time or weekly_recap.get("week_end_date"),
                        "weekly_recap": weekly_recap,
                        "daily_structure": daily_structure,
                    },
                    "skipFeishu": True,
                    "skipPublish": True,
                    "forceAnalyze": True,
                },
            )
            ai_res = _extract_ai_result(wf_out, norm_sym) or {}
            title, content, card = build_weekly_close_review_card(
                account_id=account_id,
                symbol=norm_sym,
                weekly_recap=weekly_recap,
                daily_structure=daily_structure,
                ai_result=ai_res,
                execution_mode=self.execution_mode,
                now=now_dt,
            )
            await self._alerts.route(
                account_id=account_id,
                symbol=norm_sym,
                level="digest",
                category="weekly_close_review",
                title=title,
                content=content,
                card=card,
                dedup_key=f"weekly:{account_id}:{norm_sym}:{iso_week}",
                bypass_cooldown=True,
            )
            summary = (
                f"{norm_sym} 每周收盘复盘 ({iso_week}): "
                f"{weekly_recap.get('weekly_candle_type')} {weekly_recap.get('change_pct')}%, "
                f"结构={daily_structure.get('macro_bias')}"
            )
            log_entry = await self._store.append_patrol_log(
                {
                    "account_id": account_id,
                    "symbol": norm_sym,
                    "trigger_type": "manual_weekly_review" if manual else "weekly_close",
                    "tier": "weekly_close_review",
                    "alert_level": "digest",
                    "action_taken": "weekly_close_reviewed",
                    "summary": summary,
                    "details": {
                        "iso_week": iso_week,
                        "weekly_recap": weekly_recap,
                        "daily_structure_bias": daily_structure.get("macro_bias"),
                    },
                }
            )
            return {
                "status": "ok",
                "symbol": norm_sym,
                "iso_week": iso_week,
                "summary": summary,
                "weekly_recap": weekly_recap,
                "daily_structure": daily_structure,
                "log": log_entry,
            }

    async def handle_d1_bar_close(
        self,
        account_id: str,
        symbol: str,
        *,
        bar_time: str,
        snapshot: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """响应 D1 收盘事件: 执行每日收盘复盘；若为本周最后一个交易日则顺带执行每周收盘复盘。"""
        norm_sym = _norm_symbol(symbol)
        daily_out = await self.run_daily_close_review(
            account_id,
            norm_sym,
            bar_time=bar_time,
            snapshot=snapshot,
        )
        bars_by_tf = await self._load_review_bars(account_id, norm_sym, snapshot)
        d1_bars = list(bars_by_tf.get("D1") or [])
        weekly_out: dict[str, Any] | None = None
        if is_weekly_close_bar(d1_bars, target_bar_time=bar_time):
            weekly_out = await self.run_weekly_close_review(
                account_id,
                norm_sym,
                bar_time=bar_time,
                snapshot=snapshot,
            )
        return {
            "status": "ok",
            "symbol": norm_sym,
            "daily_review": daily_out,
            "weekly_review": weekly_out,
        }

    async def check_post_close_reviews(
        self,
        account_id: str,
        symbol: str,
        *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """周五收盘后定时兜底检查: 无需等周一开盘新 K 线，即可完成周五每日复盘与每周复盘。"""
        now_dt = now or self._now_fn()
        if now_dt.tzinfo is None:
            now_dt = now_dt.replace(tzinfo=UTC)
        utc_now = now_dt.astimezone(UTC)
        in_friday_post_close = utc_now.weekday() == 4 and (utc_now.hour * 60 + utc_now.minute) >= (22 * 60 + 5)
        in_saturday_post_close = utc_now.weekday() == 5 and utc_now.hour < 12
        if not (in_friday_post_close or in_saturday_post_close):
            return {"status": "skipped", "reason": "outside_weekly_post_close_window"}

        norm_sym = _norm_symbol(symbol)
        d1_bars = await self._store.get_bars(account_id, norm_sym, "D1", limit=30)
        friday = utc_now.date() - timedelta(days=utc_now.weekday() - 4)
        target = friday.isoformat()
        if not is_weekly_close_bar(d1_bars, target_bar_time=target):
            return {"status": "skipped", "reason": "awaiting_friday_d1"}
        daily_out = await self.run_daily_close_review(account_id, norm_sym, bar_time=target)
        weekly_out = await self.run_weekly_close_review(account_id, norm_sym, bar_time=target)
        return {
            "status": "ok",
            "symbol": norm_sym,
            "daily_review": daily_out,
            "weekly_review": weekly_out,
        }

    async def _enqueue_cancel_pending(self, account_id: str, symbol: str, reasons: list[str]) -> bool:
        from uuid import uuid4

        cmd = {
            "command_id": f"sop-cancel-{uuid4()}",
            "account_id": account_id,
            "symbol": symbol,
            "type": "CANCEL_PENDING",
            "command_type": "CANCEL_PENDING",
            "source": "sop_patrol",
            "payload": {"reason": ",".join(reasons) or "sop_invalidation"},
        }
        try:
            await self._store.enqueue_command(account_id, cmd)
            return True
        except Exception:
            return False


def deepcopy_dict(data: dict[str, Any]) -> dict[str, Any]:
    from copy import deepcopy

    return deepcopy(data)


__all__ = ["PatrolService"]
