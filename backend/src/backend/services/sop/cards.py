"""SOP 自动巡检与多周期(1H/4H、M15、D1 日/周收盘)复盘飞书交互卡片构建器。"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import UTC, datetime
from math import isfinite
from typing import Any
from zoneinfo import ZoneInfo

_BEIJING_TZ = ZoneInfo("Asia/Shanghai")
_HTF_ABBR_RE = re.compile(r"\bhtf\b", re.IGNORECASE)

_TRIGGER_REASON_LABELS: dict[str, str] = {
    "htf_bootstrap": "高周期(1H/4H)首次锚定",
    "htf_schedule_due": "高周期(1H/4H)定时复盘",
    "volatility_move": "价格异动",
    "key_level_breakout": "阻力突破",
    "key_level_breakdown": "支撑跌破",
    "false_breakout_reclaim": "假突破收回",
    "false_breakdown_reclaim": "假跌破收回",
    "veto_boundary_breached": "否决边界突破",
    "playbook_invalidated": "剧本失效",
    "logic_exhausted": "目标兑现重建",
    "no_active_playbook": "无活跃剧本重建",
    "manual_htf": "手动高周期(1H/4H)复盘",
    "manual_m15": "手动 M15 复盘",
    "m15_close": "M15 收盘",
    "d1_close": "D1 日线收盘",
    "weekly_close": "每周收盘",
    "daily_close_review": "每日收盘复盘",
    "weekly_close_review": "每周收盘复盘",
    "manual_daily_review": "手动每日复盘",
    "manual_weekly_review": "手动每周复盘",
    "patrol": "例行巡检",
}

_DIRECTION_LABELS: dict[str, str] = {
    "long": "🟢 看多 (long)",
    "short": "🔴 看空 (short)",
    "wait": "⚪ 观望 (wait)",
}

_ACTION_LABELS: dict[str, str] = {
    "no_op": "稳态观察",
    "htf_reviewed": "高周期(1H/4H)复盘",
    "m15_wait": "M15 观察",
    "m15_entry_signal": "🎯 M15 入场信号",
    "daily_close_reviewed": "📅 每日收盘复盘",
    "weekly_close_reviewed": "📊 每周收盘复盘",
    "state_transition_only": "⚡ 状态迁徙",
    "data_failure_silent_retry": "⚠️ 数据异常重试",
    "data_failure_alert": "🚨 连续数据异常",
}

_BREAK_STATE_LABELS: dict[str, str] = {
    "unbroken": "未破",
    "broken_up": "上破",
    "broken_down": "下破",
    "reclaimed": "假突破收回",
    "probing": "测试中",
}

_CHANGE_REASON_LABELS: dict[str, str] = {
    "intraday_direction": "日内方向",
    "short_term_targets": "短期目标",
    "veto_boundary": "否决边界",
    "active_playbook": "主剧本",
}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _fmt_beijing_time(now: datetime | None) -> str:
    dt = now or datetime.now(UTC)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(_BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")


def _to_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    return num if isfinite(num) else None


def _fmt_price(value: Any) -> str:
    num = _to_float(value)
    if num is None or num <= 0:
        return "-"
    text = f"{num:.6f}".rstrip("0").rstrip(".")
    return text or "0"


def _fmt_positive_list(values: Any, *, limit: int = 3) -> str:
    if not isinstance(values, (list, tuple)):
        return "-"
    formatted = [_fmt_price(v) for v in values if (_p := _to_float(v)) is not None and _p > 0]
    return ", ".join(formatted[:limit]) if formatted else "-"


def _sanitize_display_text(value: Any) -> str:
    """将展示文本中的缩写统一展开为完整名称并压缩空白，避免用户卡片出现 HTF 等缩写。"""
    if not isinstance(value, str):
        return ""
    collapsed = " ".join(value.split())
    return _HTF_ABBR_RE.sub("高周期(1H/4H)", collapsed)


def _clip_text(value: Any, max_len: int) -> str:
    cleaned = _sanitize_display_text(value)
    if not cleaned:
        return ""
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[: max(1, max_len - 1)].rstrip() + "…"


def _format_reasons(reasons: Sequence[str] | None, *, fallback: str = "例行触发") -> str:
    items = [
        _TRIGGER_REASON_LABELS.get(str(r), _sanitize_display_text(str(r)))
        for r in (reasons or [])
        if str(r).strip()
    ]
    return "、".join(dict.fromkeys(items)) if items else fallback


def _format_veto(anchor: dict[str, Any]) -> str:
    veto = anchor.get("veto_boundary")
    if not isinstance(veto, dict):
        return "-"
    direction = str(anchor.get("intraday_direction") or "").lower()
    price = (
        veto.get("long_invalid_below")
        if direction == "long"
        else veto.get("short_invalid_above")
        if direction == "short"
        else (veto.get("long_invalid_below") or veto.get("short_invalid_above"))
    )
    price_str = _fmt_price(price)
    reason = _clip_text(veto.get("reason"), 80)
    if price_str != "-" and reason:
        side_txt = "下破" if direction == "long" else "上破" if direction == "short" else "边界"
        return f"{side_txt} `{price_str}` 失效 ({reason})"
    if price_str != "-":
        return f"`{price_str}`"
    return reason or "-"


def _format_zone(zone: Any) -> str:
    if not isinstance(zone, dict):
        return "-"
    low = _fmt_price(zone.get("low"))
    high = _fmt_price(zone.get("high"))
    if low != "-" and high != "-":
        return f"{low}~{high}"
    lvl = _fmt_price(zone.get("level"))
    op = str(zone.get("op") or "").strip()
    if lvl != "-":
        return f"{op} {lvl}".strip()
    return "-"


def _format_playbooks(playbooks: Any, *, max_items: int = 2) -> list[str]:
    if not isinstance(playbooks, list):
        return []
    active = [
        pb
        for pb in playbooks
        if isinstance(pb, dict) and str(pb.get("state") or "").lower() not in ("invalidated", "exhausted")
    ]
    rows: list[str] = []
    for pb in active[:max_items]:
        role = str(pb.get("role") or "primary")
        direction = str(pb.get("direction") or "-")
        state = str(pb.get("state") or "armed")
        zone_txt = _format_zone(pb.get("trigger_zone"))
        sl_txt = _fmt_price(pb.get("stop_loss"))
        tgt_txt = _fmt_positive_list(pb.get("targets"), limit=2)
        rows.append(
            f"- `{pb.get('id', role)}` ({direction}/{state}): 触发 `{zone_txt}` · 止损 `{sl_txt}` · 目标 `{tgt_txt}`"
        )
    return rows


def _make_card(title: str, template: str, elements: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "template": template,
        },
        "elements": elements,
    }


def _md_div(content: str) -> dict[str, Any]:
    return {"tag": "div", "text": {"tag": "lark_md", "content": content}}


def _fields_div(*pairs: tuple[str, str]) -> dict[str, Any]:
    return {
        "tag": "div",
        "fields": [
            {"is_short": True, "text": {"tag": "lark_md", "content": f"**{label}:**\n{val}"}}
            for label, val in pairs
        ],
    }


def _note_block(text: str) -> dict[str, Any]:
    return {"tag": "note", "elements": [{"tag": "plain_text", "content": text}]}


def build_htf_review_card(
    *,
    account_id: str,
    symbol: str,
    current_price: float,
    ledger: dict[str, Any],
    ai_result: dict[str, Any],
    trigger_reasons: Sequence[str],
    changed: bool,
    change_reasons: Sequence[str],
    execution_mode: str,
    now: datetime | None = None,
) -> tuple[str, str, dict[str, Any]]:
    """构建高周期(1H/4H)日内方向更新/例行复盘飞书交互卡片，返回 (title, fallback_md, card)。"""
    anchor = _as_dict(ledger.get("htf_anchor"))
    intraday_dir = str(anchor.get("intraday_direction") or "wait").lower()
    macro_bias = str(anchor.get("macro_bias") or ai_result.get("bias") or "neutral").lower()
    conf = _to_float(anchor.get("confidence") or ai_result.get("confidence")) or 0.0
    title = (
        f"🔄 高周期(1H/4H)日内方向更新 · {symbol}"
        if changed
        else f"🧭 高周期(1H/4H)例行复盘 · {symbol}"
    )
    template = "green" if intraday_dir == "long" else ("red" if intraday_dir == "short" else "blue")

    dir_text = _DIRECTION_LABELS.get(intraday_dir, intraday_dir)
    reason_text = _format_reasons(trigger_reasons, fallback="定时复盘")
    short_targets = _fmt_positive_list(anchor.get("short_term_targets"), limit=3)
    long_targets = _fmt_positive_list(anchor.get("long_term_targets"), limit=3)
    veto_text = _format_veto(anchor)

    elements: list[dict[str, Any]] = [
        _fields_div(("账户", account_id), ("品种", symbol)),
        _fields_div(("当前价格", _fmt_price(current_price)), ("触发原因", reason_text)),
        _fields_div(("日内整体方向 (1H/4H)", dir_text), ("高周期偏向 / 置信度", f"{macro_bias} ({conf:.0f}%)")),
        _fields_div(
            ("日内短期目标", short_targets),
            ("延伸参考目标", long_targets),
        ),
        _md_div(f"**否决边界:** {veto_text}"),
    ]

    if changed and change_reasons:
        mapped = [_CHANGE_REASON_LABELS.get(str(r), str(r)) for r in change_reasons]
        elements.append(_md_div(f"**核心变更:** {'、'.join(mapped)}"))

    key_levels = [lv for lv in (ledger.get("key_levels") or []) if isinstance(lv, dict)][:4]
    if key_levels:
        kl_lines = [
            (
                f"- {'支撑' if lv.get('role') == 'support' else '阻力' if lv.get('role') == 'resistance' else '枢轴'} "
                f"`{_fmt_price(lv.get('price'))}` "
                f"({_BREAK_STATE_LABELS.get(str(lv.get('break_state') or 'unbroken'), str(lv.get('break_state')))})"
            )
            for lv in key_levels
        ]
        elements.append(_md_div("**核心关键位:**\n" + "\n".join(kl_lines)))

    pb_rows = _format_playbooks(ledger.get("playbooks"), max_items=2)
    if pb_rows:
        elements.append(_md_div("**活跃剧本:**\n" + "\n".join(pb_rows)))

    arb = _as_dict(ai_result.get("arbitration"))
    rationale = _clip_text(anchor.get("rationale") or arb.get("reasoning") or "", 320)
    if rationale:
        elements.append(_md_div(f"**复盘要点:**\n{rationale}"))

    contradiction = _clip_text(arb.get("contradiction") or ai_result.get("alert_reason") or "", 200)
    if contradiction:
        elements.append(_md_div(f"**分歧 / 风险提示:**\n{contradiction}"))

    version = ledger.get("version", "-")
    elements.append({"tag": "hr"})
    elements.append(
        _note_block(f"北京时间 {_fmt_beijing_time(now)} · 账本 v{version} · 模式 {execution_mode}")
    )

    fallback_md = (
        f"{symbol} 高周期(1H/4H)复盘 ({reason_text})\n"
        f"方向: {intraday_dir} ({macro_bias}, {conf:.0f}%) | 现价: {_fmt_price(current_price)}\n"
        f"短期目标: {short_targets} | 否决边界: {veto_text}"
    )
    return title, fallback_md, _make_card(title, template, elements)


build_intraday_direction_review_card = build_htf_review_card


def build_m15_review_card(
    *,
    account_id: str,
    symbol: str,
    current_price: float,
    ledger: dict[str, Any],
    ai_result: dict[str, Any],
    trigger_reasons: Sequence[str],
    is_actionable: bool,
    side_label: str,
    execution_mode: str,
    now: datetime | None = None,
) -> tuple[str, str, dict[str, Any]]:
    """构建 M15 入场信号或切入观察复盘飞书交互卡片，返回 (title, fallback_md, card)。"""
    anchor = _as_dict(ledger.get("htf_anchor"))
    intraday_dir = str(anchor.get("intraday_direction") or "wait").lower()
    sop_ctx = _as_dict(ai_result.get("sop_context"))
    sop_blocks = [str(b) for b in (sop_ctx.get("sop_blocks") or []) if str(b).strip()]
    counter_blocked = "sop.m15_counter_htf_blocked" in sop_blocks

    if is_actionable:
        title = f"🎯 M15 入场信号 · {symbol} [{side_label}]"
        template = "green" if side_label == "BUY" else "red"
    else:
        title = f"⛔ M15 逆势拦截 · {symbol}" if counter_blocked else f"👀 M15 切入复盘 · {symbol}"
        template = "orange" if counter_blocked else "blue"

    plan = _as_dict(ai_result.get("trade_plan"))
    arb = _as_dict(ai_result.get("arbitration"))
    conf = _to_float(plan.get("confidence") or ai_result.get("confidence")) or 0.0
    reason_text = _format_reasons(trigger_reasons, fallback="M15 收盘")

    elements: list[dict[str, Any]] = [
        _fields_div(("账户", account_id), ("品种", symbol)),
        _fields_div(("当前价格", _fmt_price(current_price)), ("触发原因", reason_text)),
        _fields_div(
            ("高周期(1H/4H)前提方向", _DIRECTION_LABELS.get(intraday_dir, intraday_dir)),
            ("否决边界", _format_veto(anchor)),
        ),
    ]

    if is_actionable:
        entry_val = plan.get("entry_price")
        raw_ez = plan.get("entry_zone")
        if entry_val is None and isinstance(raw_ez, dict):
            entry_val = raw_ez.get("min")
        tp_list = _fmt_positive_list(plan.get("take_profits") or plan.get("take_profit"), limit=3)
        elements.append(
            _fields_div(
                ("信号方向 / 置信度", f"{side_label} ({conf:.0f}%)"),
                ("参考入场", _fmt_price(entry_val if entry_val is not None else current_price)),
            )
        )
        elements.append(
            _fields_div(
                ("止损 SL", _fmt_price(plan.get("stop_loss"))),
                ("止盈 TP", tp_list),
            )
        )
    else:
        status_note = (
            "逆高周期(1H/4H)日内方向开仓已拦截"
            if counter_blocked
            else f"保持观察 · 置信度 {conf:.0f}%"
        )
        elements.append(_md_div(f"**M15 结论:** {status_note}"))
        pb_rows = _format_playbooks(ledger.get("playbooks"), max_items=1)
        if pb_rows:
            elements.append(_md_div("**当前观察剧本:**\n" + "\n".join(pb_rows)))

    summary_txt = _clip_text(
        arb.get("reasoning") or plan.get("narrative") or ai_result.get("alert_reason") or "", 280
    )
    if summary_txt:
        elements.append(_md_div(f"**分析摘要:**\n{summary_txt}"))

    elements.append({"tag": "hr"})
    elements.append(
        _note_block(f"北京时间 {_fmt_beijing_time(now)} · 账本 v{ledger.get('version', '-')} · 模式 {execution_mode}")
    )

    fallback_md = (
        f"{title} | 现价 {_fmt_price(current_price)} | 高周期(1H/4H) {intraday_dir} | 触发 {reason_text}"
    )
    return title, fallback_md, _make_card(title, template, elements)


def _nearest_levels(ledger: dict[str, Any], current_price: float | None) -> tuple[str, str]:
    levels = [lv for lv in (ledger.get("key_levels") or []) if isinstance(lv, dict)]
    supports: list[float] = []
    resistances: list[float] = []
    for lv in levels:
        price = _to_float(lv.get("price"))
        if price is None or price <= 0:
            continue
        role = str(lv.get("role") or "").lower()
        if current_price is not None and current_price > 0:
            if price <= current_price:
                supports.append(price)
            else:
                resistances.append(price)
        elif role == "support":
            supports.append(price)
        elif role == "resistance":
            resistances.append(price)
    sup_txt = _fmt_price(max(supports)) if supports else "-"
    res_txt = _fmt_price(min(resistances)) if resistances else "-"
    return sup_txt, res_txt


def _brief_playbook(ledger: dict[str, Any]) -> str:
    for pb in ledger.get("playbooks") or []:
        if not isinstance(pb, dict):
            continue
        state = str(pb.get("state") or "").lower()
        if state in ("invalidated", "exhausted"):
            continue
        return f"{pb.get('direction', '-')} / {state} ({_format_zone(pb.get('trigger_zone'))})"
    return "-"


def build_patrol_cycle_card(
    *,
    account_id: str,
    results: Sequence[dict[str, Any]],
    interval_seconds: int,
    execution_mode: str,
    now: datetime | None = None,
) -> tuple[str, str, dict[str, Any]]:
    """构建单账户自动冷/热巡检汇总飞书卡片，返回 (title, fallback_md, card)。"""
    has_failure = any(
        r.get("status") == "failure" or r.get("alert_level") == "urgent" for r in results
    )
    has_event = any(
        r.get("htf_ran")
        or r.get("m15_ran")
        or r.get("action_taken") in ("state_transition_only", "m15_entry_signal")
        for r in results
    )
    reviewed_cnt = sum(1 for r in results if r.get("htf_ran") or r.get("m15_ran"))
    transition_cnt = sum(1 for r in results if r.get("action_taken") == "state_transition_only")
    noop_cnt = sum(1 for r in results if r.get("action_taken") == "no_op")

    if has_failure:
        title = f"🚨 自动巡检异常 · {account_id}"
        template = "red"
    elif has_event:
        title = f"🛰️ 自动巡检报告 · {account_id}"
        template = "orange"
    else:
        title = f"🛰️ 自动巡检简报 · {account_id}"
        template = "wathet"

    elements: list[dict[str, Any]] = [
        _fields_div(
            ("巡检账户", account_id),
            (
                "活跃品种 / 本轮统计",
                f"{len(results)} 个 (复盘 {reviewed_cnt} · 迁徙 {transition_cnt} · 观察 {noop_cnt})",
            ),
        )
    ]

    fallback_lines: list[str] = [title]
    for item in results:
        elements.append({"tag": "hr"})
        sym = str(item.get("symbol") or "-")
        price_num = _to_float(item.get("current_price"))
        price_txt = _fmt_price(price_num)
        action = str(item.get("action_taken") or item.get("status") or "-")
        badge = _ACTION_LABELS.get(action, action)
        ledger = _as_dict(item.get("ledger"))
        anchor = _as_dict(ledger.get("htf_anchor"))
        intraday_dir = str(anchor.get("intraday_direction") or "wait").lower()
        macro_bias = str(anchor.get("macro_bias") or "neutral").lower()
        dir_txt = _DIRECTION_LABELS.get(intraday_dir, intraday_dir)
        veto_txt = _format_veto(anchor)
        targets_txt = _fmt_positive_list(anchor.get("short_term_targets"), limit=2)
        sup_txt, res_txt = _nearest_levels(ledger, price_num)
        pb_txt = _brief_playbook(ledger)

        lines = [
            f"**{sym}** | 现价 `{price_txt}` | {badge}",
            f"方向: {dir_txt} ({macro_bias}) · 短期目标: `{targets_txt}`",
            f"否决边界: {veto_txt}",
            f"活跃剧本: {pb_txt} · 关键位: 支撑 `{sup_txt}` / 阻力 `{res_txt}`",
        ]
        reasons = item.get("trigger_reasons")
        if isinstance(reasons, list) and reasons:
            lines.append(f"触发因素: {_format_reasons(reasons)}")
        elif item.get("status") == "failure" and item.get("summary"):
            lines.append(f"异常说明: {_clip_text(item.get('summary'), 140)}")

        elements.append(_md_div("\n".join(lines)))
        fallback_lines.append(f"- {sym}: 现价 {price_txt} | {badge} | 方向 {intraday_dir} | 目标 {targets_txt}")

    elements.append({"tag": "hr"})
    interval_min = max(1, int(interval_seconds) // 60)
    elements.append(
        _note_block(
            f"北京时间 {_fmt_beijing_time(now)} · 周期每 {interval_min} 分钟 · 模式 {execution_mode} · 仅巡检活跃账户"
        )
    )
    return title, "\n".join(fallback_lines), _make_card(title, template, elements)


def _fmt_signed(value: Any, *, suffix: str = "") -> str:
    num = _to_float(value)
    if num is None:
        return "-"
    sign = "+" if num > 0 else ""
    text = f"{num:.4f}".rstrip("0").rstrip(".") if abs(num) < 10 else f"{num:.2f}"
    return f"{sign}{text}{suffix}"


def build_daily_close_review_card(
    *,
    account_id: str,
    symbol: str,
    daily_recap: dict[str, Any],
    daily_structure: dict[str, Any] | None,
    ai_result: dict[str, Any],
    execution_mode: str,
    now: datetime | None = None,
) -> tuple[str, str, dict[str, Any]]:
    """构建每日收盘后的日内走势复盘飞书交互卡片（使用日线 D1 收盘与 1H/4H 结构数据）。"""
    trade_date = str(daily_recap.get("trade_date") or "-")
    net_change = _to_float(daily_recap.get("net_change")) or 0.0
    candle_type = str(daily_recap.get("candle_type") or "-")
    title = f"📅 每日收盘日内走势复盘 · {symbol} ({trade_date})"
    template = "green" if net_change > 0 else ("red" if net_change < 0 else "blue")

    open_px = _fmt_price(daily_recap.get("open"))
    close_px = _fmt_price(daily_recap.get("close"))
    high_px = _fmt_price(daily_recap.get("high"))
    low_px = _fmt_price(daily_recap.get("low"))
    amp_px = _fmt_price(daily_recap.get("amplitude"))
    chg_txt = f"{_fmt_signed(net_change)} ({_fmt_signed(daily_recap.get('change_pct'), suffix='%')})"
    vs_prev = str(daily_recap.get("vs_prev_day") or "-")

    daily_struct = _as_dict(daily_structure)
    macro_bias = str(daily_struct.get("macro_bias") or ai_result.get("bias") or "neutral")
    prior_hi = _fmt_price(daily_struct.get("prior_major_high"))
    prior_lo = _fmt_price(daily_struct.get("prior_major_low"))

    prev_range_txt = f"{_fmt_price(daily_recap.get('prev_high'))} / {_fmt_price(daily_recap.get('prev_low'))}"
    elements: list[dict[str, Any]] = [
        _fields_div(("账户", account_id), ("品种", symbol)),
        _fields_div(("复盘交易日", trade_date), ("日线收线 / 涨跌", f"{candle_type} · {chg_txt}")),
        _fields_div(
            ("开盘 / 收盘", f"{open_px} / {close_px}"),
            ("最高 / 最低 (振幅)", f"{high_px} / {low_px} ({amp_px})"),
        ),
        _fields_div(("相对前日结构", vs_prev), ("日线结构偏向", macro_bias)),
        _fields_div(("前日高 / 低", prev_range_txt), ("日线重要参考位", f"前高 {prior_hi} / 前低 {prior_lo}")),
    ]

    arb = _as_dict(ai_result.get("arbitration"))
    tech = _as_dict(ai_result.get("technical"))
    review_note = _clip_text(
        arb.get("reasoning") or tech.get("rationale") or ai_result.get("indicators_summary") or "",
        360,
    )
    if review_note:
        elements.append(_md_div(f"**每日走势复盘总结:**\n{review_note}"))

    elements.append({"tag": "hr"})
    elements.append(
        _note_block(
            f"北京时间 {_fmt_beijing_time(now)} · 数据周期：日线(D1) + 高周期(1H/4H) "
            f"· 模式 {execution_mode} · 仅复盘不开仓"
        )
    )

    fallback_md = (
        f"{title}\n"
        f"收线: {candle_type} {chg_txt} | 开 {open_px} 高 {high_px} 低 {low_px} 收 {close_px}\n"
        f"相对前日: {vs_prev} | 日线偏向: {macro_bias}"
    )
    return title, fallback_md, _make_card(title, template, elements)


def build_weekly_close_review_card(
    *,
    account_id: str,
    symbol: str,
    weekly_recap: dict[str, Any],
    daily_structure: dict[str, Any] | None,
    ai_result: dict[str, Any],
    execution_mode: str,
    now: datetime | None = None,
) -> tuple[str, str, dict[str, Any]]:
    """构建每周收盘后的周内走势复盘飞书交互卡片（使用日线 D1 周度汇总数据）。"""
    iso_week = str(weekly_recap.get("iso_week") or "-")
    start_d = str(weekly_recap.get("week_start_date") or "-")
    end_d = str(weekly_recap.get("week_end_date") or "-")
    net_change = _to_float(weekly_recap.get("net_change")) or 0.0
    wk_type = str(weekly_recap.get("weekly_candle_type") or "-")
    title = f"📊 每周收盘周内走势复盘 · {symbol} ({iso_week})"
    template = "green" if net_change > 0 else ("red" if net_change < 0 else "blue")

    wk_open = _fmt_price(weekly_recap.get("week_open"))
    wk_close = _fmt_price(weekly_recap.get("week_close"))
    wk_high = _fmt_price(weekly_recap.get("week_high"))
    wk_low = _fmt_price(weekly_recap.get("week_low"))
    wk_range = _fmt_price(weekly_recap.get("week_range"))
    chg_txt = f"{_fmt_signed(net_change)} ({_fmt_signed(weekly_recap.get('change_pct'), suffix='%')})"
    up_days = weekly_recap.get("up_days", 0)
    down_days = weekly_recap.get("down_days", 0)
    days_cnt = weekly_recap.get("trading_days_count", 0)
    vs_prev_wk = str(weekly_recap.get("vs_prev_week") or "-")

    daily_struct = _as_dict(daily_structure)
    macro_bias = str(daily_struct.get("macro_bias") or ai_result.get("bias") or "neutral")
    targets = [
        _fmt_price(t.get("price"))
        for t in (daily_struct.get("measured_targets") or [])[:3]
        if isinstance(t, dict) and t.get("price") is not None
    ]
    targets_txt = ", ".join(t for t in targets if t != "-") or "-"

    elements: list[dict[str, Any]] = [
        _fields_div(("账户", account_id), ("品种", symbol)),
        _fields_div(
            ("复盘周期", f"{iso_week} ({start_d} ~ {end_d})"),
            ("周线收线 / 涨跌", f"{wk_type} · {chg_txt}"),
        ),
        _fields_div(
            ("周开 / 周收", f"{wk_open} / {wk_close}"),
            ("周高 / 周低 (振幅)", f"{wk_high} / {wk_low} ({wk_range})"),
        ),
        _fields_div(
            ("日线节奏", f"共 {days_cnt} 交易日 · 涨 {up_days} / 跌 {down_days}"),
            ("相对上周结构", vs_prev_wk),
        ),
        _fields_div(("周后日线结构偏向", macro_bias), ("下周延伸参考位", targets_txt)),
    ]

    breakdown = [d for d in (weekly_recap.get("daily_breakdown") or []) if isinstance(d, dict)]
    if breakdown:
        day_lines = [
            f"- {d.get('date')}: 开 `{_fmt_price(d.get('open'))}` 收 `{_fmt_price(d.get('close'))}` "
            f"({_fmt_signed(d.get('change_pct'), suffix='%')})"
            for d in breakdown
        ]
        elements.append(_md_div("**周内逐日走势明细:**\n" + "\n".join(day_lines)))

    arb = _as_dict(ai_result.get("arbitration"))
    tech = _as_dict(ai_result.get("technical"))
    review_note = _clip_text(
        arb.get("reasoning") or tech.get("rationale") or ai_result.get("indicators_summary") or "",
        360,
    )
    if review_note:
        elements.append(_md_div(f"**每周走势复盘与下周展望:**\n{review_note}"))

    elements.append({"tag": "hr"})
    elements.append(
        _note_block(
            f"北京时间 {_fmt_beijing_time(now)} · 数据周期：日线(D1)周度汇总 · 模式 {execution_mode} · 仅复盘不开仓"
        )
    )

    fallback_md = (
        f"{title}\n"
        f"收线: {wk_type} {chg_txt} | 周开 {wk_open} 高 {wk_high} 低 {wk_low} 收 {wk_close}\n"
        f"相对上周: {vs_prev_wk} | 下周参考位: {targets_txt}"
    )
    return title, fallback_md, _make_card(title, template, elements)


__all__ = [
    "build_daily_close_review_card",
    "build_htf_review_card",
    "build_intraday_direction_review_card",
    "build_m15_review_card",
    "build_patrol_cycle_card",
    "build_weekly_close_review_card",
]
