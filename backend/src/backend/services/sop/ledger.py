"""SOP WatchLedger 增量记忆结构、冷路径巡检状态机与双层和解逻辑。"""

from __future__ import annotations

from collections.abc import Sequence
from copy import deepcopy
from datetime import UTC, datetime
from math import isfinite
from typing import Any, Literal

PLAYBOOK_STATES: tuple[str, ...] = (
    "armed",
    "watching",
    "triggered",
    "partial_taken",
    "exhausted",
    "invalidated",
    "rearmed",
)
ACTIVE_PLAYBOOK_STATES: frozenset[str] = frozenset({"armed", "watching", "triggered", "partial_taken", "rearmed"})


def _utc_iso(now: datetime | None = None) -> str:
    dt = now or datetime.now(UTC)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_iso(ts: Any) -> datetime | None:
    if not isinstance(ts, str) or not ts.strip():
        return None
    raw = ts.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _to_float(val: Any) -> float | None:
    try:
        num = float(val)
    except (TypeError, ValueError):
        return None
    return num if isfinite(num) else None


def _norm_symbol(symbol: str) -> str:
    return symbol.strip().upper()


def empty_watch_ledger(account_id: str, symbol: str) -> dict[str, Any]:
    return {
        "account_id": account_id,
        "symbol": _norm_symbol(symbol),
        "version": 0,
        "htf_anchor": {
            "macro_bias": "neutral",
            "intraday_direction": "wait",
            "short_term_targets": [],
            "long_term_targets": [],
            "veto_boundary": None,
            "vacuum_zone": None,
            "rationale": "",
            "confidence": 0.0,
            "reviewed_at": None,
            "anchor_bar_time": None,
            "anchor_price": None,
        },
        "key_levels": [],
        "playbooks": [],
        "recent_events": [],
        "htf_reviewed_at": None,
        "m15_reviewed_at": None,
        "last_patrol_at": None,
        "last_patrol_price": None,
        "last_alert_hash": None,
        "last_alert_at": None,
        "consecutive_failures": 0,
        "updated_at": None,
    }


def normalize_watch_ledger(
    ledger: dict[str, Any] | None,
    account_id: str,
    symbol: str,
) -> dict[str, Any]:
    base = empty_watch_ledger(account_id, symbol)
    if not isinstance(ledger, dict):
        return base
    out = deepcopy(base)
    if ledger.get("version") is not None:
        try:
            out["version"] = int(ledger["version"])
        except (TypeError, ValueError):
            out["version"] = 0
    if isinstance(ledger.get("htf_anchor"), dict):
        anchor = dict(out["htf_anchor"])
        anchor.update(deepcopy(ledger["htf_anchor"]))
        out["htf_anchor"] = anchor
    if isinstance(ledger.get("key_levels"), list):
        out["key_levels"] = [deepcopy(item) for item in ledger["key_levels"] if isinstance(item, dict)]
    if isinstance(ledger.get("playbooks"), list):
        out["playbooks"] = [deepcopy(item) for item in ledger["playbooks"] if isinstance(item, dict)]
    if isinstance(ledger.get("recent_events"), list):
        out["recent_events"] = [deepcopy(item) for item in ledger["recent_events"][-20:] if isinstance(item, dict)]
    for field in (
        "htf_reviewed_at",
        "m15_reviewed_at",
        "last_patrol_at",
        "last_alert_hash",
        "last_alert_at",
        "updated_at",
    ):
        if ledger.get(field) is not None:
            out[field] = ledger[field]
    out["last_patrol_price"] = _to_float(ledger.get("last_patrol_price"))
    try:
        out["consecutive_failures"] = int(ledger.get("consecutive_failures") or 0)
    except (TypeError, ValueError):
        out["consecutive_failures"] = 0
    return out


def _append_event(ledger: dict[str, Any], event_type: str, detail: dict[str, Any], ts_iso: str) -> None:
    events = ledger.setdefault("recent_events", [])
    events.append({"ts": ts_iso, "type": event_type, **detail})
    if len(events) > 20:
        del events[:-20]


def _latest_bar_close(bars_by_tf: dict[str, Sequence[dict[str, Any]]], tf: str) -> tuple[float | None, str | None]:
    bars = bars_by_tf.get(tf) or []
    if not bars:
        return None, None
    last = bars[-1]
    return _to_float(last.get("close")), str(last.get("time")) if last.get("time") is not None else None


def _is_in_zone(price: float, trigger_zone: dict[str, Any] | None, *, tol_pct: float = 0.0015) -> tuple[bool, bool]:
    """返回 (entered_watch_zone, triggered_exact)."""
    if not isinstance(trigger_zone, dict):
        return False, False
    low = _to_float(trigger_zone.get("low"))
    high = _to_float(trigger_zone.get("high"))
    level = _to_float(trigger_zone.get("level"))
    op = str(trigger_zone.get("op") or "").strip()

    if low is not None and high is not None:
        lo, hi = min(low, high), max(low, high)
        margin = max(price * tol_pct, 1e-6)
        in_exact = lo <= price <= hi
        near_watch = (lo - margin) <= price <= (hi + margin)
        return near_watch, in_exact

    if level is not None:
        margin = max(level * tol_pct, 1e-6)
        near_watch = abs(price - level) <= margin
        if op in (">=", ">", "above"):
            return near_watch, price >= level
        if op in ("<=", "<", "below"):
            return near_watch, price <= level
        return near_watch, abs(price - level) <= margin * 0.5
    return False, False


def _evaluate_veto_breach(
    htf_anchor: dict[str, Any],
    confirm_price: float,
) -> tuple[bool, str | None]:
    direction = str(htf_anchor.get("intraday_direction") or "wait").lower()
    veto = htf_anchor.get("veto_boundary")
    if not isinstance(veto, dict):
        return False, None
    long_invalid = _to_float(veto.get("long_invalid_below"))
    short_invalid = _to_float(veto.get("short_invalid_above"))
    if direction == "long" and long_invalid is not None and confirm_price < long_invalid:
        return True, f"多头否决边界跌破 ({confirm_price} < {long_invalid})"
    if direction == "short" and short_invalid is not None and confirm_price > short_invalid:
        return True, f"空头否决边界突破 ({confirm_price} > {short_invalid})"
    return False, None


def evaluate_ledger_patrol(
    ledger: dict[str, Any] | None,
    *,
    account_id: str = "",
    symbol: str = "",
    current_price: float,
    bars_by_tf: dict[str, Sequence[dict[str, Any]]] | None = None,
    htf_review_minutes: int = 60,
    move_trigger_pct: float = 0.5,
    now: datetime | None = None,
    market_open: bool = True,
    data_stale: bool = False,
) -> dict[str, Any]:
    """纯规则冷路径巡检: 检查 HTF 过期、位移阈值、关键位突破/假突破收回、否决线、剧本生命周期。"""
    now_dt = now or datetime.now(UTC)
    if now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=UTC)
    ts_iso = _utc_iso(now_dt)

    norm_acc = account_id or (str(ledger.get("account_id") or "") if isinstance(ledger, dict) else "")
    norm_sym = symbol or (str(ledger.get("symbol") or "") if isinstance(ledger, dict) else "")
    working = normalize_watch_ledger(ledger, norm_acc, norm_sym)

    bars_map = bars_by_tf or {}
    m15_close, m15_time = _latest_bar_close(bars_map, "M15")
    h1_close, _ = _latest_bar_close(bars_map, "H1")
    confirm_price = m15_close if m15_close is not None else current_price
    htf_confirm_price = h1_close if h1_close is not None else confirm_price

    trigger_reasons: list[str] = []
    state_transitions: list[dict[str, Any]] = []
    alert_candidates: list[dict[str, Any]] = []
    should_trigger_htf = False
    should_trigger_m15 = False
    cancel_pending_recommended = False

    def add_reason(reason: str) -> None:
        if reason not in trigger_reasons:
            trigger_reasons.append(reason)

    anchor = working["htf_anchor"]
    has_anchor = bool(anchor.get("intraday_direction") and anchor.get("intraday_direction") != "wait") or bool(
        working.get("htf_reviewed_at")
    )
    if not market_open or data_stale:
        working["last_patrol_at"] = ts_iso
        return {
            "should_trigger_htf": False,
            "should_trigger_m15": False,
            "trigger_reasons": trigger_reasons,
            "state_transitions": state_transitions,
            "alert_candidates": alert_candidates,
            "updated_ledger": working,
            "cancel_pending_recommended": False,
        }

    if not has_anchor:
        should_trigger_htf = True
        add_reason("htf_bootstrap")
    else:
        reviewed_dt = _parse_iso(working.get("htf_reviewed_at"))
        if reviewed_dt is None or (now_dt - reviewed_dt).total_seconds() >= htf_review_minutes * 60:
            should_trigger_htf = True
            add_reason("htf_schedule_due")

    # 相对锚定价波动超过阈值 -> 触发动态复盘
    anchor_price = _to_float(anchor.get("anchor_price")) or _to_float(working.get("last_patrol_price"))
    if anchor_price and anchor_price > 0 and move_trigger_pct > 0:
        move_pct = abs(current_price - anchor_price) / anchor_price * 100.0
        if move_pct >= move_trigger_pct:
            should_trigger_htf = True
            should_trigger_m15 = True
            add_reason("volatility_move")
            alert_candidates.append(
                {
                    "level": "notify",
                    "category": "volatility_move",
                    "summary": f"{norm_sym} 相对锚定基准波动 {move_pct:.2f}% (现价 {current_price})",
                    "dedup_key": f"vol:{round(move_pct, 1)}:{m15_time or ts_iso[:16]}",
                }
            )

    # 关键位突破 / 假突破收回识别
    for lvl in working["key_levels"]:
        lvl_price = _to_float(lvl.get("price"))
        if lvl_price is None or lvl_price <= 0:
            continue
        role = str(lvl.get("role") or "pivot").lower()
        tier = str(lvl.get("tier") or "secondary").lower()
        prev_state = str(lvl.get("break_state") or "unbroken").lower()
        new_state = prev_state
        tol = max(lvl_price * 0.0003, 1e-6)

        if role in ("resistance", "pivot"):
            if prev_state in ("unbroken", "probing", "reclaimed") and confirm_price > lvl_price + tol:
                new_state = "broken_up"
                add_reason("key_level_breakout")
                should_trigger_m15 = True
                if tier == "core":
                    should_trigger_htf = True
                alert_candidates.append(
                    {
                        "level": "actionable" if tier == "core" else "notify",
                        "category": "key_level_breakout",
                        "summary": f"{norm_sym} 收盘向上突破关键位 {lvl_price} (收盘 {confirm_price})",
                        "dedup_key": f"brk_up:{lvl.get('id', lvl_price)}",
                    }
                )
            elif prev_state == "broken_up" and confirm_price < lvl_price - tol:
                new_state = "reclaimed"
                add_reason("false_breakout_reclaim")
                should_trigger_m15 = True
                should_trigger_htf = True
                cancel_pending_recommended = True
                alert_candidates.append(
                    {
                        "level": "urgent",
                        "category": "false_breakout_reclaim",
                        "summary": f"{norm_sym} 关键位 {lvl_price} 向上假突破并收回 (收盘 {confirm_price})",
                        "dedup_key": f"reclaim_up:{lvl.get('id', lvl_price)}",
                    }
                )
        if role in ("support", "pivot") and new_state == prev_state:
            if prev_state in ("unbroken", "probing", "reclaimed") and confirm_price < lvl_price - tol:
                new_state = "broken_down"
                add_reason("key_level_breakdown")
                should_trigger_m15 = True
                if tier == "core":
                    should_trigger_htf = True
                alert_candidates.append(
                    {
                        "level": "actionable" if tier == "core" else "notify",
                        "category": "key_level_breakdown",
                        "summary": f"{norm_sym} 收盘向下跌破关键位 {lvl_price} (收盘 {confirm_price})",
                        "dedup_key": f"brk_dn:{lvl.get('id', lvl_price)}",
                    }
                )
            elif prev_state == "broken_down" and confirm_price > lvl_price + tol:
                new_state = "reclaimed"
                add_reason("false_breakdown_reclaim")
                should_trigger_m15 = True
                should_trigger_htf = True
                cancel_pending_recommended = True
                alert_candidates.append(
                    {
                        "level": "urgent",
                        "category": "false_breakdown_reclaim",
                        "summary": f"{norm_sym} 关键位 {lvl_price} 向下假跌破并收回 (收盘 {confirm_price})",
                        "dedup_key": f"reclaim_dn:{lvl.get('id', lvl_price)}",
                    }
                )

        if new_state != prev_state:
            lvl["break_state"] = new_state
            lvl["broken_at"] = ts_iso
            tr = {
                "target": "key_level",
                "id": lvl.get("id") or str(lvl_price),
                "from": prev_state,
                "to": new_state,
                "price": confirm_price,
            }
            state_transitions.append(tr)
            _append_event(working, "key_level_transition", tr, ts_iso)

    # HTF 否决边界检查
    veto_broken, veto_msg = _evaluate_veto_breach(anchor, htf_confirm_price)
    if veto_broken and veto_msg:
        should_trigger_htf = True
        cancel_pending_recommended = True
        add_reason("veto_boundary_breached")
        alert_candidates.append(
            {
                "level": "urgent",
                "category": "veto_boundary_breached",
                "summary": f"{norm_sym} {veto_msg}",
                "dedup_key": f"veto:{anchor.get('intraday_direction')}:{htf_confirm_price}",
            }
        )
        _append_event(working, "veto_boundary_breached", {"message": veto_msg}, ts_iso)

    # 剧本生命周期状态机
    for pb in working["playbooks"]:
        pb_id = str(pb.get("id") or pb.get("name") or "playbook")
        prev_state = str(pb.get("state") or "armed").lower()
        if prev_state in ("invalidated", "exhausted"):
            continue
        pb_dir = str(pb.get("direction") or "long").lower()
        new_state = prev_state

        # 1) 失效边界判断
        invalid_if = pb.get("invalid_if") if isinstance(pb.get("invalid_if"), dict) else {}
        inv_below = _to_float(invalid_if.get("price_below"))
        inv_above = _to_float(invalid_if.get("price_above"))
        if (inv_below is not None and confirm_price < inv_below) or (
            inv_above is not None and confirm_price > inv_above
        ):
            new_state = "invalidated"
            pb["invalidation_reason"] = f"价格 {confirm_price} 触发失效条件 ({invalid_if})"
            should_trigger_htf = True
            cancel_pending_recommended = True
            add_reason("playbook_invalidated")
            alert_candidates.append(
                {
                    "level": "urgent",
                    "category": "playbook_invalidated",
                    "summary": f"{norm_sym} 剧本 {pb_id} 已失效: {pb['invalidation_reason']}",
                    "dedup_key": f"pb_inv:{pb_id}",
                }
            )
        else:
            # 2) 目标达成与逻辑穷尽判断 (triggered/partial_taken/watching 均可检测)
            targets = [_to_float(t) for t in (pb.get("targets") or [])]
            valid_targets = [t for t in targets if t is not None]
            hit_set = {float(x) for x in (pb.get("targets_hit") or []) if _to_float(x) is not None}
            newly_hit: list[float] = []
            for tgt in valid_targets:
                is_hit = current_price >= tgt if pb_dir == "long" else current_price <= tgt
                if is_hit and tgt not in hit_set:
                    hit_set.add(tgt)
                    newly_hit.append(tgt)
            if newly_hit:
                pb["targets_hit"] = sorted(hit_set, reverse=(pb_dir == "short"))
                if valid_targets and len(hit_set) >= len(valid_targets):
                    new_state = "exhausted"
                    pb["exhausted_reason"] = "全部预定目标位已到达"
                    should_trigger_htf = True
                    cancel_pending_recommended = True
                    add_reason("logic_exhausted")
                    alert_candidates.append(
                        {
                            "level": "notify",
                            "category": "logic_exhausted",
                            "summary": f"{norm_sym} 剧本 {pb_id} 目标全部兑现, 进入逻辑穷尽并请求重建",
                            "dedup_key": f"pb_exh:{pb_id}",
                        }
                    )
                elif prev_state in ("triggered", "partial_taken"):
                    new_state = "partial_taken"
                    add_reason("target_partial_hit")
                    alert_candidates.append(
                        {
                            "level": "notify",
                            "category": "target_partial_hit",
                            "summary": f"{norm_sym} 剧本 {pb_id} 命中阶段目标 {newly_hit}",
                            "dedup_key": f"pb_part:{pb_id}:{len(hit_set)}",
                        }
                    )

            # 3) 触发区间检查
            if new_state in ("armed", "watching", "rearmed"):
                near_watch, exact_hit = _is_in_zone(confirm_price, pb.get("trigger_zone"))
                if exact_hit:
                    new_state = "triggered"
                    should_trigger_m15 = True
                    add_reason("playbook_triggered")
                    alert_candidates.append(
                        {
                            "level": "actionable",
                            "category": "playbook_triggered",
                            "summary": f"{norm_sym} 剧本 {pb_id} 触发入场条件 (价格 {confirm_price})",
                            "dedup_key": f"pb_trig:{pb_id}",
                        }
                    )
                elif near_watch and prev_state in ("armed", "rearmed"):
                    new_state = "watching"
                    should_trigger_m15 = True
                    add_reason("entered_watch_zone")
                    alert_candidates.append(
                        {
                            "level": "notify",
                            "category": "entered_watch_zone",
                            "summary": f"{norm_sym} 价格逼近剧本 {pb_id} 观察区 ({confirm_price})",
                            "dedup_key": f"pb_watch:{pb_id}",
                        }
                    )

        if new_state != prev_state:
            pb["state"] = new_state
            pb["updated_at"] = ts_iso
            tr = {
                "target": "playbook",
                "id": pb_id,
                "from": prev_state,
                "to": new_state,
                "price": confirm_price,
            }
            state_transitions.append(tr)
            _append_event(working, "playbook_transition", tr, ts_iso)

    # 无孤儿剧本保底: 若当前所有剧本均失效或穷尽, 强制触发 HTF 重建
    has_active_playbook = any(
        str(pb.get("state") or "").lower() in ACTIVE_PLAYBOOK_STATES for pb in working["playbooks"]
    )
    if not has_active_playbook and has_anchor:
        should_trigger_htf = True
        add_reason("no_active_playbook")

    working["last_patrol_at"] = ts_iso
    working["last_patrol_price"] = current_price

    return {
        "should_trigger_htf": should_trigger_htf,
        "should_trigger_m15": should_trigger_m15,
        "trigger_reasons": trigger_reasons,
        "state_transitions": state_transitions,
        "alert_candidates": alert_candidates,
        "updated_ledger": working,
        "cancel_pending_recommended": cancel_pending_recommended,
    }


def _extract_targets(review_result: dict[str, Any], tp_key: str, default_val: float | None = None) -> list[float]:
    out: list[float] = []
    raw_plan = review_result.get("trade_plan")
    plan: dict[str, Any] = raw_plan if isinstance(raw_plan, dict) else {}
    raw_targets = plan.get(tp_key)
    if isinstance(raw_targets, list):
        for item in raw_targets:
            num = _to_float(item)
            if num is not None:
                out.append(num)
    elif (single := _to_float(raw_targets)) is not None:
        out.append(single)
    if not out and default_val is not None:
        out.append(default_val)
    return out


def _compact_text(value: Any, *, max_len: int = 160) -> str:
    if not isinstance(value, str):
        return ""
    collapsed = " ".join(value.split())
    if len(collapsed) <= max_len:
        return collapsed
    return collapsed[: max(1, max_len - 3)].rstrip() + "..."


def _direction_from_bias(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    norm = value.strip().lower()
    if norm in ("long", "buy", "bullish", "bull", "bullish_breakout"):
        return "long"
    if norm in ("short", "sell", "bearish", "bear", "bearish_breakout"):
        return "short"
    return None


def _is_protective_level(price: float, direction: str, current_price: float | None) -> bool:
    if price <= 0 or not isfinite(price):
        return False
    if current_price is not None and current_price > 0:
        if direction == "long":
            return price < current_price
        if direction == "short":
            return price > current_price
    return True


def reconcile_ledger_after_review(
    existing_ledger: dict[str, Any] | None,
    *,
    account_id: str,
    symbol: str,
    role: Literal["htf_anchor", "m15_entry"],
    review_result: dict[str, Any],
    daily_structure: dict[str, Any] | None = None,
    confluence_levels: list[dict[str, Any]] | None = None,
    current_price: float | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """根据 LLM 分析结果更新 WatchLedger。m15_entry 严格保留既有 htf_anchor 不被篡改, 且无孤儿剧本。"""
    ts_iso = _utc_iso(now)
    working = normalize_watch_ledger(existing_ledger, account_id, symbol)

    raw_sop = review_result.get("sop_context")
    sop_ctx: dict[str, Any] = raw_sop if isinstance(raw_sop, dict) else {}
    bias = str(review_result.get("bias") or "neutral").lower()
    raw_plan = review_result.get("trade_plan")
    plan: dict[str, Any] = raw_plan if isinstance(raw_plan, dict) else {}

    entry_price = _to_float(plan.get("entry_price"))
    stop_loss = _to_float(plan.get("stop_loss")) or _to_float(review_result.get("suggested_sl"))
    suggested_tp = _to_float(review_result.get("suggested_tp"))
    tp_list = _extract_targets(review_result, "take_profits", suggested_tp)

    if role == "htf_anchor":
        # 先更新多源共振关键位, 并继承相同价位既有的破位状态以避免每 5 分钟冷巡检重复触发
        if isinstance(confluence_levels, list) and confluence_levels:
            prev_levels = [k for k in working["key_levels"] if isinstance(k, dict)]
            refreshed_levels: list[dict[str, Any]] = []
            for idx, item in enumerate(confluence_levels[:10]):
                if not isinstance(item, dict):
                    continue
                price_num = _to_float(item.get("price"))
                if price_num is None:
                    continue
                lvl_role = str(item.get("role") or "pivot")
                tol = max(price_num * 0.0005, 1e-6)
                matched = next(
                    (
                        old
                        for old in prev_levels
                        if (old_p := _to_float(old.get("price"))) is not None and abs(old_p - price_num) <= tol
                    ),
                    None,
                )
                if matched is not None:
                    break_state = str(matched.get("break_state") or "unbroken")
                    broken_at = matched.get("broken_at")
                else:
                    break_state = "unbroken"
                    broken_at = None
                    if current_price is not None and current_price > 0:
                        init_tol = max(price_num * 0.0003, 1e-6)
                        if lvl_role == "support" and current_price < price_num - init_tol:
                            break_state = "broken_down"
                        elif lvl_role == "resistance" and current_price > price_num + init_tol:
                            break_state = "broken_up"
                refreshed_levels.append(
                    {
                        "id": f"kl-{idx + 1}",
                        "price": price_num,
                        "role": lvl_role,
                        "tier": str(item.get("tier") or "secondary"),
                        "sources": list(item.get("sources") or []),
                        "break_state": break_state,
                        "broken_at": broken_at,
                    }
                )
            working["key_levels"] = refreshed_levels

        anchor = dict(working["htf_anchor"])
        macro_bias = str(
            sop_ctx.get("macro_bias")
            or (daily_structure or {}).get("macro_bias")
            or bias
        ).lower()
        intraday_dir = (
            _direction_from_bias(sop_ctx.get("intraday_direction"))
            or _direction_from_bias(bias)
            or _direction_from_bias(macro_bias)
            or "wait"
        )
        short_targets = [
            float(x) for x in (sop_ctx.get("short_term_targets") or tp_list) if _to_float(x) is not None
        ]
        if not short_targets and intraday_dir in ("long", "short"):
            tgt_pool: list[float] = []
            for lv in working["key_levels"]:
                lp = _to_float(lv.get("price"))
                if lp is None or lp <= 0:
                    continue
                if current_price is not None and current_price > 0:
                    if (intraday_dir == "long" and lp > current_price) or (
                        intraday_dir == "short" and lp < current_price
                    ):
                        tgt_pool.append(lp)
                else:
                    lv_role = str(lv.get("role") or "").lower()
                    if (intraday_dir == "long" and lv_role == "resistance") or (
                        intraday_dir == "short" and lv_role == "support"
                    ):
                        tgt_pool.append(lp)
            short_targets = sorted(set(tgt_pool), reverse=(intraday_dir == "short"))[:2]

        long_targets = [
            float(x)
            for x in (sop_ctx.get("long_term_targets") or [])
            if (lt_val := _to_float(x)) is not None and lt_val > 0
        ]
        if not long_targets and isinstance(daily_structure, dict):
            long_targets = [
                float(t["price"])
                for t in (daily_structure.get("measured_targets") or [])[:3]
                if isinstance(t, dict) and (lt_val := _to_float(t.get("price"))) is not None and lt_val > 0
            ]

        raw_veto = sop_ctx.get("veto_boundary")
        has_valid_veto = False
        if isinstance(raw_veto, dict):
            if intraday_dir == "long" and _to_float(raw_veto.get("long_invalid_below")) is not None:
                has_valid_veto = True
            elif intraday_dir == "short" and _to_float(raw_veto.get("short_invalid_above")) is not None:
                has_valid_veto = True

        veto: dict[str, Any]
        if has_valid_veto and isinstance(raw_veto, dict):
            veto = dict(raw_veto)
            veto["reason"] = _compact_text(veto.get("reason") or "HTF 结构边界", max_len=160)
        elif intraday_dir in ("long", "short"):
            chosen_veto: float | None = None
            veto_label = "HTF 结构失效止损边界"
            if stop_loss is not None and _is_protective_level(stop_loss, intraday_dir, current_price):
                chosen_veto = stop_loss
                veto_label = "HTF 止损边界"
            else:
                all_kl: list[float] = []
                core_kl: list[float] = []
                for lv in working["key_levels"]:
                    lp = _to_float(lv.get("price"))
                    if lp is None or not _is_protective_level(lp, intraday_dir, current_price):
                        continue
                    all_kl.append(lp)
                    if str(lv.get("tier") or "").lower() == "core":
                        core_kl.append(lp)
                pool = core_kl or all_kl
                if pool:
                    chosen_veto = max(pool) if intraday_dir == "long" else min(pool)
                    veto_label = "HTF 共振支撑否决位" if intraday_dir == "long" else "HTF 共振阻力否决位"
            veto = {
                "long_invalid_below": chosen_veto if intraday_dir == "long" else None,
                "short_invalid_above": chosen_veto if intraday_dir == "short" else None,
                "reason": _compact_text(
                    f"{veto_label} {chosen_veto}" if chosen_veto is not None else "HTF 结构失效止损边界",
                    max_len=160,
                ),
            }
        else:
            veto = {
                "long_invalid_below": None,
                "short_invalid_above": None,
                "reason": _compact_text(sop_ctx.get("veto_reason") or "HTF 结构失效止损边界", max_len=160),
            }

        anchor.update(
            {
                "macro_bias": macro_bias,
                "intraday_direction": intraday_dir,
                "short_term_targets": short_targets,
                "long_term_targets": long_targets,
                "veto_boundary": veto,
                "vacuum_zone": (daily_structure or {}).get("vacuum_zone") or anchor.get("vacuum_zone"),
                "rationale": _compact_text(
                    sop_ctx.get("htf_rationale") or review_result.get("alert_reason") or "",
                    max_len=240,
                ),
                "confidence": _to_float(review_result.get("confidence")) or 0.0,
                "reviewed_at": ts_iso,
                "anchor_price": current_price if current_price is not None else anchor.get("anchor_price"),
            }
        )
        working["htf_anchor"] = anchor
        working["htf_reviewed_at"] = ts_iso

        # 合并 LLM 给出的剧本(若有)
        incoming_playbooks = sop_ctx.get("playbooks")
        if isinstance(incoming_playbooks, list) and incoming_playbooks:
            had_inactive = any(
                str(p.get("state") or "").lower() in ("invalidated", "exhausted")
                for p in working["playbooks"]
            )
            rebuilt: list[dict[str, Any]] = []
            for idx, pb in enumerate(incoming_playbooks):
                if not isinstance(pb, dict):
                    continue
                pb_copy = deepcopy(pb)
                pb_copy.setdefault("id", f"pb-{idx + 1}")
                pb_copy.setdefault("role", "primary" if idx == 0 else "backup")
                pb_copy.setdefault("direction", intraday_dir if intraday_dir in ("long", "short") else "long")
                pb_copy["state"] = "rearmed" if had_inactive and idx == 0 else str(pb_copy.get("state") or "armed")
                pb_copy["updated_at"] = ts_iso
                rebuilt.append(pb_copy)
            if rebuilt:
                working["playbooks"] = rebuilt

        _append_event(
            working,
            "htf_anchor_updated",
            {"direction": anchor["intraday_direction"], "macro_bias": anchor["macro_bias"]},
            ts_iso,
        )
    else:
        # role == "m15_entry": 严格保持 working["htf_anchor"] 原样不动!
        working["m15_reviewed_at"] = ts_iso
        htf_dir = str(working["htf_anchor"].get("intraday_direction") or "wait").lower()
        if plan and working["playbooks"]:
            target_dir = "long" if bias == "bullish" else ("short" if bias == "bearish" else htf_dir)
            for pb in working["playbooks"]:
                if str(pb.get("state") or "").lower() in ACTIVE_PLAYBOOK_STATES and (
                    htf_dir == "wait" or str(pb.get("direction") or "").lower() == htf_dir == target_dir
                ):
                    if entry_price is not None:
                        pb["entry_price"] = entry_price
                        pb["trigger_zone"] = pb.get("trigger_zone") or {
                            "level": entry_price,
                            "op": ">=" if pb.get("direction") == "long" else "<=",
                        }
                    if stop_loss is not None:
                        pb["stop_loss"] = stop_loss
                    if tp_list:
                        pb["targets"] = tp_list
                    pb["updated_at"] = ts_iso
                    break
        _append_event(
            working,
            "m15_entry_reviewed",
            {"bias": bias, "entry_price": entry_price, "htf_direction": htf_dir},
            ts_iso,
        )

    # 无孤儿剧本保底: 若所有剧本均已失效/穷尽或列表为空, 创建兜底观测剧本
    active_pbs = [pb for pb in working["playbooks"] if str(pb.get("state") or "").lower() in ACTIVE_PLAYBOOK_STATES]
    if not active_pbs:
        fallback_dir = (
            _direction_from_bias(working["htf_anchor"].get("intraday_direction"))
            or _direction_from_bias(bias)
            or _direction_from_bias(working["htf_anchor"].get("macro_bias"))
            or "long"
        )
        raw_vb = working["htf_anchor"].get("veto_boundary")
        veto_b: dict[str, Any] = raw_vb if isinstance(raw_vb, dict) else {}
        veto_sl = (
            _to_float(veto_b.get("long_invalid_below"))
            if fallback_dir == "long"
            else _to_float(veto_b.get("short_invalid_above"))
        )
        safe_sl: float | None = veto_sl
        if safe_sl is None and stop_loss is not None and _is_protective_level(stop_loss, fallback_dir, current_price):
            safe_sl = stop_loss
        anchor_px = entry_price or current_price
        fallback_targets = tp_list or list(working["htf_anchor"].get("short_term_targets") or [])
        had_prior = bool(working["playbooks"])
        fallback_pb: dict[str, Any] = {
            "id": f"pb-rearmed-{len(working['playbooks']) + 1}" if had_prior else "pb-primary-1",
            "name": "HTF 锚定自动观察剧本",
            "role": "primary",
            "direction": fallback_dir,
            "state": "rearmed" if had_prior else "armed",
            "trigger_condition": f"围绕 HTF {fallback_dir} 方向等待 M15 结构确认入场",
            "trigger_zone": {"level": anchor_px, "op": ">=" if fallback_dir == "long" else "<="}
            if anchor_px is not None
            else None,
            "entry_price": anchor_px,
            "stop_loss": safe_sl,
            "targets": fallback_targets,
            "targets_hit": [],
            "invalid_if": (
                {"price_below": safe_sl}
                if (fallback_dir == "long" and safe_sl is not None)
                else ({"price_above": safe_sl} if (fallback_dir == "short" and safe_sl is not None) else None)
            ),
            "updated_at": ts_iso,
        }
        working["playbooks"].append(fallback_pb)

    if current_price is not None:
        working["last_patrol_price"] = current_price
    working["updated_at"] = ts_iso
    return working


def is_material_change(prev_ledger: dict[str, Any] | None, next_ledger: dict[str, Any]) -> tuple[bool, list[str]]:
    """判断两次复盘之间是否有实质变化, 用于抑制重复飞书打扰。"""
    if prev_ledger is None:
        return True, ["initial_anchor"]
    reasons: list[str] = []
    prev_raw = prev_ledger.get("htf_anchor")
    next_raw = next_ledger.get("htf_anchor")
    prev_a: dict[str, Any] = prev_raw if isinstance(prev_raw, dict) else {}
    next_a: dict[str, Any] = next_raw if isinstance(next_raw, dict) else {}

    if prev_a.get("intraday_direction") != next_a.get("intraday_direction"):
        reasons.append("intraday_direction_changed")
    if prev_a.get("macro_bias") != next_a.get("macro_bias"):
        reasons.append("macro_bias_changed")
    if prev_a.get("veto_boundary") != next_a.get("veto_boundary"):
        reasons.append("veto_boundary_changed")
    if prev_a.get("short_term_targets") != next_a.get("short_term_targets"):
        reasons.append("short_term_targets_changed")

    prev_pbs = {
        str(p.get("id")): (p.get("state"), p.get("entry_price"), p.get("stop_loss"))
        for p in (prev_ledger.get("playbooks") or [])
        if isinstance(p, dict)
    }
    next_pbs = {
        str(p.get("id")): (p.get("state"), p.get("entry_price"), p.get("stop_loss"))
        for p in (next_ledger.get("playbooks") or [])
        if isinstance(p, dict)
    }
    if prev_pbs != next_pbs:
        reasons.append("playbooks_changed")

    return bool(reasons), reasons


def format_ledger_prompt_block(ledger: dict[str, Any] | None, *, role: str) -> str:
    """将持久化 WatchLedger 格式化为注入 LLM 提示词的增量记忆与前提约束块。"""
    if not isinstance(ledger, dict):
        return (
            "## WATCH LEDGER MEMORY\n"
            f"- Analysis Role: {role}\n"
            "- Status: Initial baseline (no prior WatchLedger persisted yet)."
        )

    raw_anchor = ledger.get("htf_anchor")
    anchor: dict[str, Any] = raw_anchor if isinstance(raw_anchor, dict) else {}
    lines: list[str] = [
        "## WATCH LEDGER MEMORY (INCREMENTAL PREMISE)",
        f"- Analysis Role: `{role}` | Ledger Version: {ledger.get('version', 0)} | "
        f"HTF Reviewed: {ledger.get('htf_reviewed_at') or 'N/A'}",
        f"- HTF Macro Bias: {anchor.get('macro_bias', 'neutral')} | "
        f"Intraday Direction Premise: **{anchor.get('intraday_direction', 'wait')}**",
        f"- Short-Term Targets: {anchor.get('short_term_targets') or []} | "
        f"Long-Term Targets: {anchor.get('long_term_targets') or []}",
        f"- Veto Boundary: {anchor.get('veto_boundary')}",
    ]
    if anchor.get("vacuum_zone"):
        lines.append(f"- D1 Vacuum Zone: {anchor['vacuum_zone']}")

    key_levels = [k for k in (ledger.get("key_levels") or []) if isinstance(k, dict)]
    if key_levels:
        lvl_str = ", ".join(
            f"{k.get('price')}({k.get('role')}/{k.get('tier')}/{k.get('break_state', 'unbroken')})"
            for k in key_levels[:8]
        )
        lines.append(f"- Confluence Key Levels: {lvl_str}")

    playbooks = [p for p in (ledger.get("playbooks") or []) if isinstance(p, dict)]
    if playbooks:
        lines.append("- Active & Backup Playbooks:")
        for pb in playbooks[:4]:
            lines.append(
                f"  - [{pb.get('id')}] role={pb.get('role')} dir={pb.get('direction')} "
                f"state={pb.get('state')} entry={pb.get('entry_price')} sl={pb.get('stop_loss')} "
                f"targets={pb.get('targets')} invalid_if={pb.get('invalid_if')}"
            )

    events = [e for e in (ledger.get("recent_events") or [])[-5:] if isinstance(e, dict)]
    if events:
        ev_str = " | ".join(f"{e.get('ts', '')}:{e.get('type', '')}" for e in events)
        lines.append(f"- Recent Events: {ev_str}")

    if role == "m15_entry":
        lines.append(
            "- HARD PREMISE FOR M15 ENTRY: Treat HTF Intraday Direction Premise, Short/Long Targets, and "
            "Veto Boundary above as binding constraints. Focus on M15 entry timing/retest/breakout trigger "
            "and precise SL/TP; do NOT reverse HTF direction without double-evidence breach of the Veto Boundary."
        )
    elif role == "htf_anchor":
        lines.append(
            "- HTF ANCHOR INSTRUCTION: Review whether existing anchor thesis & playbooks remain valid or "
            "have become exhausted/invalidated; update macro/intraday direction, targets, veto boundary, "
            "and maintain primary + backup playbooks with explicit invalidation prices."
        )

    return "\n".join(lines)


__all__ = [
    "ACTIVE_PLAYBOOK_STATES",
    "PLAYBOOK_STATES",
    "empty_watch_ledger",
    "evaluate_ledger_patrol",
    "format_ledger_prompt_block",
    "is_material_change",
    "normalize_watch_ledger",
    "reconcile_ledger_after_review",
]
