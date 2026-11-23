"""SOP 自动巡检与 HTF/M15 复盘飞书交互卡片构建器。"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from math import isfinite
from typing import Any
from zoneinfo import ZoneInfo

_BEIJING_TZ = ZoneInfo("Asia/Shanghai")

_TRIGGER_REASON_LABELS: dict[str, str] = {
    "htf_bootstrap": "首次锚定",
    "htf_schedule_due": "定时复盘",
    "volatility_move": "价格异动",
    "key_level_breakout": "阻力突破",
    "key_level_breakdown": "支撑跌破",
    "false_breakout_reclaim": "假突破收回",
    "false_breakdown_reclaim": "假跌破收回",
    "veto_boundary_breached": "否决边界突破",
    "playbook_invalidated": "剧本失效",
    "logic_exhausted": "目标兑现重建",
    "no_active_playbook": "无活跃剧本重建",
    "manual_htf": "手动 HTF 复盘",
    "manual_m15": "手动 M15 复盘",
    "m15_close": "M15 收盘",
    "patrol": "例行巡检",
}

_DIRECTION_LABELS: dict[str, str] = {
    "long": "🟢 看多 (long)",
    "short": "🔴 看空 (short)",
    "wait": "⚪ 观望 (wait)",
}

_ACTION_LABELS: dict[str, str] = {
    "no_op": "稳态观察",
    "htf_reviewed": "HTF 复盘",
    "m15_wait": "M15 观察",
    "m15_entry_signal": "🎯 M15 入场信号",
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


def _clip_text(value: Any, max_len: int) -> str:
    if not isinstance(value, str):
        return ""
    cleaned = " ".join(value.split())
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[: max(1, max_len - 1)].rstrip() + "…"


def _format_reasons(reasons: Sequence[str] | None, *, fallback: str = "例行触发") -> str:
    items = [_TRIGGER_REASON_LABELS.get(str(r), str(r)) for r in (reasons or []) if str(r).strip()]
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
    """构建 HTF 锚定更新/例行复盘飞书交互卡片，返回 (title, fallback_md, card)。"""
    anchor = _as_dict(ledger.get("htf_anchor"))
    intraday_dir = str(anchor.get("intraday_direction") or "wait").lower()
    macro_bias = str(anchor.get("macro_bias") or ai_result.get("bias") or "neutral").lower()
    conf = _to_float(anchor.get("confidence") or ai_result.get("confidence")) or 0.0
    title = f"🔄 HTF 方向更新 · {symbol}" if changed else f"🧭 HTF 例行复盘 · {symbol}"
    template = "green" if intraday_dir == "long" else ("red" if intraday_dir == "short" else "blue")

    dir_text = _DIRECTION_LABELS.get(intraday_dir, intraday_dir)
    reason_text = _format_reasons(trigger_reasons, fallback="定时复盘")
    short_targets = _fmt_positive_list(anchor.get("short_term_targets"), limit=3)
    long_targets = _fmt_positive_list(anchor.get("long_term_targets"), limit=3)
    veto_text = _format_veto(anchor)

    elements: list[dict[str, Any]] = [
        _fields_div(("账户", account_id), ("品种", symbol)),
        _fields_div(("当前价格", _fmt_price(current_price)), ("触发原因", reason_text)),
        _fields_div(("日内方向", dir_text), ("宏观 / 置信度", f"{macro_bias} ({conf:.0f}%)")),
        _fields_div(
            ("短期目标", short_targets),
            ("长期目标", long_targets),
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
        f"{symbol} HTF 复盘 ({reason_text})\n"
        f"方向: {intraday_dir} ({macro_bias}, {conf:.0f}%) | 现价: {_fmt_price(current_price)}\n"
        f"短期目标: {short_targets} | 否决边界: {veto_text}"
    )
    return title, fallback_md, _make_card(title, template, elements)


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
            ("HTF 前提方向", _DIRECTION_LABELS.get(intraday_dir, intraday_dir)),
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
        status_note = "逆 HTF 日内方向开仓已拦截" if counter_blocked else f"保持观察 · 置信度 {conf:.0f}%"
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
        f"{title} | 现价 {_fmt_price(current_price)} | HTF {intraday_dir} | 触发 {reason_text}"
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


__all__ = [
    "build_htf_review_card",
    "build_m15_review_card",
    "build_patrol_cycle_card",
]
