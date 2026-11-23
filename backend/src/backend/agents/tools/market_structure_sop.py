"""SOP 市场结构工具: D1 真空带与等距延伸目标、多源共振关键位、当日波动边界。"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import UTC, datetime
from math import isfinite
from typing import Any, Literal

from backend.agents.tools.sr_calculator import (
    calculate_fibonacci_extensions,
    find_psychological_levels,
)
from backend.trading_core.smc.detector import find_swing_points


def _to_float(value: Any) -> float | None:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    return num if isfinite(num) else None


def _round_price(value: float, reference: float | None = None) -> float:
    ref = abs(reference) if reference is not None else abs(value)
    if ref >= 500:
        return round(value, 2)
    if ref >= 10:
        return round(value, 3)
    return round(value, 5)


def _compute_atr(bars: Sequence[dict[str, Any]], period: int = 14) -> float | None:
    if len(bars) < 2:
        if len(bars) == 1:
            h = _to_float(bars[0].get("high"))
            l_val = _to_float(bars[0].get("low"))
            if h is not None and l_val is not None and h >= l_val:
                return h - l_val
        return None
    trs: list[float] = []
    prev_close: float | None = _to_float(bars[0].get("close"))
    for bar in bars:
        high = _to_float(bar.get("high"))
        low = _to_float(bar.get("low"))
        close = _to_float(bar.get("close"))
        if high is None or low is None or close is None:
            continue
        if prev_close is None:
            tr = max(0.0, high - low)
        else:
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
        prev_close = close
    if not trs:
        return None
    window = trs[-period:]
    return sum(window) / len(window)


def _extract_swing_extremes(
    bars: Sequence[dict[str, Any]],
) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
    swings = find_swing_points(list(bars), 2, 2)
    highs: list[tuple[int, float]] = []
    lows: list[tuple[int, float]] = []
    for item in swings.get("swingHighs", []):
        idx = int(item.get("index", 0))
        price = _to_float(item.get("price"))
        if price is not None:
            highs.append((idx, price))
    for item in swings.get("swingLows", []):
        idx = int(item.get("index", 0))
        price = _to_float(item.get("price"))
        if price is not None:
            lows.append((idx, price))
    return highs, lows


def analyze_daily_vacuum(
    d1_bars: Sequence[dict[str, Any]],
    current_price: float | None = None,
    *,
    atr_d1: float | None = None,
) -> dict[str, Any]:
    """识别 D1 宏观结构方向、历史前高/前低突破真空带及斐波/等距延伸目标梯队。"""
    valid_bars = [
        b
        for b in d1_bars
        if _to_float(b.get("high")) is not None
        and _to_float(b.get("low")) is not None
        and _to_float(b.get("close")) is not None
    ]
    if len(valid_bars) < 5:
        return {
            "status": "insufficient_data",
            "macro_bias": "neutral",
            "in_vacuum": False,
            "vacuum_zone": None,
            "prior_major_high": None,
            "prior_major_low": None,
            "anchor_swing": None,
            "measured_targets": [],
            "atr_d1": _round_price(atr_d1, current_price) if atr_d1 else None,
            "bars_analyzed": len(valid_bars),
        }

    ref_price = (
        float(current_price)
        if current_price is not None and isfinite(current_price)
        else float(valid_bars[-1]["close"])
    )
    computed_atr = atr_d1 if atr_d1 is not None and atr_d1 > 0 else (_compute_atr(valid_bars) or 0.0)

    # 在排除最近 2 根 K 线的历史窗口中提取结构前高与前低
    lookback = valid_bars[:-2] if len(valid_bars) > 6 else valid_bars[:-1]
    prior_high = max(float(b["high"]) for b in lookback)
    prior_low = min(float(b["low"]) for b in lookback)
    midpoint = (prior_high + prior_low) / 2.0

    swing_highs, swing_lows = _extract_swing_extremes(lookback)
    significant_high = swing_highs[-1][1] if swing_highs else prior_high
    significant_low = swing_lows[-1][1] if swing_lows else prior_low

    # 近端斜率: 最近 5 日收盘均价 vs 前 10 日收盘均价
    recent_avg = sum(float(b["close"]) for b in valid_bars[-min(5, len(valid_bars)) :]) / min(
        5, len(valid_bars)
    )
    prev_slice = valid_bars[-min(15, len(valid_bars)) : -min(5, len(valid_bars))] or valid_bars[:1]
    prev_avg = sum(float(b["close"]) for b in prev_slice) / len(prev_slice)

    breakout_buffer = max(computed_atr * 0.05, ref_price * 0.0005)
    if ref_price > prior_high + breakout_buffer:
        macro_bias = "bullish_breakout"
    elif ref_price < prior_low - breakout_buffer:
        macro_bias = "bearish_breakout"
    elif recent_avg > prev_avg and ref_price >= midpoint:
        macro_bias = "bullish"
    elif recent_avg < prev_avg and ref_price <= midpoint:
        macro_bias = "bearish"
    else:
        macro_bias = "range"

    is_bullish = macro_bias in ("bullish_breakout", "bullish")
    direction_word: Literal["bullish", "bearish"] = "bullish" if is_bullish else "bearish"

    if is_bullish:
        wave_start = prior_low
        wave_end = max(prior_high, significant_high)
        # 回踩低点优先取冲破前的最近波段低点; 若未出现有效次级低点(如箱体直接突破),
        # 用箱体中枢作为延伸起点, 确保 1.0x 落入突破前高上方的真空区。
        retracement_candidates = [p for _, p in swing_lows if wave_start < p < wave_end]
        span = max(0.0, wave_end - wave_start)
        retracement_end = retracement_candidates[-1] if retracement_candidates else wave_start
        if (retracement_end + span) <= wave_end:
            retracement_end = wave_start + span * 0.5
        ext = calculate_fibonacci_extensions(wave_start, wave_end, retracement_end, "bullish")
        # 保底保证扩展方向与涨跌方向一致(若下游工具方向参数为 up/down 则显式计算兜底)
        target_1_0 = retracement_end + span
        ext_vals = {
            1.272: ext.level_1_272 if ext.level_1_272 >= retracement_end else retracement_end + span * 1.272,
            1.618: ext.level_1_618 if ext.level_1_618 >= retracement_end else retracement_end + span * 1.618,
            2.0: ext.level_2_0 if ext.level_2_0 >= retracement_end else retracement_end + span * 2.0,
            2.618: ext.level_2_618 if ext.level_2_618 >= retracement_end else retracement_end + span * 2.618,
        }
    else:
        wave_start = prior_high
        wave_end = min(prior_low, significant_low)
        retracement_candidates = [p for _, p in swing_highs if wave_end < p < wave_start]
        span = max(0.0, wave_start - wave_end)
        retracement_end = retracement_candidates[-1] if retracement_candidates else wave_start
        if (retracement_end - span) >= wave_end:
            retracement_end = wave_start - span * 0.5
        ext = calculate_fibonacci_extensions(wave_start, wave_end, retracement_end, "bearish")
        target_1_0 = retracement_end - span
        ext_vals = {
            1.272: ext.level_1_272 if ext.level_1_272 <= retracement_end else retracement_end - span * 1.272,
            1.618: ext.level_1_618 if ext.level_1_618 <= retracement_end else retracement_end - span * 1.618,
            2.0: ext.level_2_0 if ext.level_2_0 <= retracement_end else retracement_end - span * 2.0,
            2.618: ext.level_2_618 if ext.level_2_618 <= retracement_end else retracement_end - span * 2.618,
        }

    measured_targets: list[dict[str, Any]] = []
    if span > 0:
        for ratio, raw_target in [
            (1.0, target_1_0),
            (1.272, ext_vals[1.272]),
            (1.618, ext_vals[1.618]),
            (2.0, ext_vals[2.0]),
            (2.618, ext_vals[2.618]),
        ]:
            hit = ref_price >= raw_target if is_bullish else ref_price <= raw_target
            measured_targets.append(
                {
                    "ratio": ratio,
                    "price": _round_price(raw_target, ref_price),
                    "hit": hit,
                }
            )

    in_vacuum = macro_bias in ("bullish_breakout", "bearish_breakout")
    vacuum_zone: dict[str, float] | None = None
    if in_vacuum and measured_targets:
        anchor_target = measured_targets[min(2, len(measured_targets) - 1)]["price"]
        if macro_bias == "bullish_breakout":
            vacuum_zone = {
                "low": _round_price(prior_high, ref_price),
                "high": float(anchor_target),
            }
        else:
            vacuum_zone = {
                "low": float(anchor_target),
                "high": _round_price(prior_low, ref_price),
            }

    return {
        "status": "ok",
        "macro_bias": macro_bias,
        "in_vacuum": in_vacuum,
        "vacuum_zone": vacuum_zone,
        "prior_major_high": _round_price(prior_high, ref_price),
        "prior_major_low": _round_price(prior_low, ref_price),
        "anchor_swing": {
            "start": _round_price(wave_start, ref_price),
            "end": _round_price(wave_end, ref_price),
            "retracement": _round_price(retracement_end, ref_price),
            "direction": direction_word,
        },
        "measured_targets": measured_targets,
        "atr_d1": _round_price(computed_atr, ref_price) if computed_atr > 0 else None,
        "bars_analyzed": len(valid_bars),
    }


def compute_day_range(
    symbol: str,
    d1_bars: Sequence[dict[str, Any]] | None = None,
    h1_bars: Sequence[dict[str, Any]] | None = None,
    current_price: float | None = None,
    *,
    atr_d1: float | None = None,
    atr_h1: float | None = None,
) -> dict[str, Any]:
    """计算当日已走振幅、相对 D1 ATR 消耗比例及日内上下边界。"""
    del symbol
    d1_list = [
        b
        for b in (d1_bars or [])
        if _to_float(b.get("high")) is not None and _to_float(b.get("low")) is not None
    ]
    h1_list = [
        b
        for b in (h1_bars or [])
        if _to_float(b.get("high")) is not None and _to_float(b.get("low")) is not None
    ]

    if d1_list:
        today_bar = d1_list[-1]
        day_open = _to_float(today_bar.get("open")) or float(today_bar["low"])
        day_high = float(today_bar["high"])
        day_low = float(today_bar["low"])
        day_close = _to_float(today_bar.get("close")) or day_open
    elif h1_list:
        recent_h1 = h1_list[-24:]
        day_open = _to_float(recent_h1[0].get("open")) or float(recent_h1[0]["low"])
        day_high = max(float(b["high"]) for b in recent_h1)
        day_low = min(float(b["low"]) for b in recent_h1)
        day_close = _to_float(recent_h1[-1].get("close")) or day_open
    else:
        return {
            "status": "insufficient_data",
            "day_open": None,
            "day_high": None,
            "day_low": None,
            "realized_range": 0.0,
            "expected_atr": None,
            "atr_consumed_pct": 0.0,
            "expansion_stage": "unknown",
            "range_exhausted": False,
            "intraday_upper_bound": None,
            "intraday_lower_bound": None,
            "session_bias": "flat",
        }

    ref_price = float(current_price) if current_price is not None and isfinite(current_price) else day_close
    computed_d1_atr = (
        float(atr_d1)
        if atr_d1 is not None and atr_d1 > 0
        else (_compute_atr(d1_list) if d1_list else None)
    )
    if (computed_d1_atr is None or computed_d1_atr <= 0) and atr_h1 is not None and atr_h1 > 0:
        computed_d1_atr = float(atr_h1) * 4.5
    if (computed_d1_atr is None or computed_d1_atr <= 0) and h1_list:
        h1_computed = _compute_atr(h1_list)
        if h1_computed is not None and h1_computed > 0:
            computed_d1_atr = h1_computed * 4.5

    realized = max(0.0, day_high - day_low)
    expected_atr = computed_d1_atr if computed_d1_atr and computed_d1_atr > 0 else realized
    consumed_pct = (realized / expected_atr * 100.0) if expected_atr > 0 else 0.0

    if consumed_pct >= 100.0:
        stage = "extended"
    elif consumed_pct >= 80.0:
        stage = "late"
    elif consumed_pct >= 45.0:
        stage = "mid"
    else:
        stage = "early"

    upper_bound = day_low + expected_atr if expected_atr > 0 else day_high
    lower_bound = day_high - expected_atr if expected_atr > 0 else day_low
    if ref_price > day_open:
        session_bias = "bullish"
    elif ref_price < day_open:
        session_bias = "bearish"
    else:
        session_bias = "flat"

    return {
        "status": "ok",
        "day_open": _round_price(day_open, ref_price),
        "day_high": _round_price(day_high, ref_price),
        "day_low": _round_price(day_low, ref_price),
        "current_price": _round_price(ref_price, ref_price),
        "realized_range": _round_price(realized, ref_price),
        "expected_atr": _round_price(expected_atr, ref_price) if expected_atr > 0 else None,
        "atr_consumed_pct": round(consumed_pct, 1),
        "expansion_stage": stage,
        "range_exhausted": consumed_pct >= 95.0,
        "intraday_upper_bound": _round_price(upper_bound, ref_price),
        "intraday_lower_bound": _round_price(lower_bound, ref_price),
        "session_bias": session_bias,
    }


_SOURCE_WEIGHTS: dict[str, float] = {
    "D1_PREV_WEEK": 2.4,
    "D1_PDH_PDL": 2.2,
    "D1_SWING": 2.0,
    "D1_MEASURED": 1.9,
    "H4_SWING": 1.8,
    "H4_SMC": 1.7,
    "H1_SWING": 1.3,
    "H1_SMC": 1.3,
    "SR_KEY": 1.4,
    "PSYCH_ROUND": 1.0,
}


def build_confluence_levels(
    symbol: str,
    *,
    d1_bars: Sequence[dict[str, Any]] | None = None,
    h4_bars: Sequence[dict[str, Any]] | None = None,
    h1_bars: Sequence[dict[str, Any]] | None = None,
    m15_bars: Sequence[dict[str, Any]] | None = None,
    smc_data: dict[str, Any] | None = None,
    sr_data: dict[str, Any] | None = None,
    trend_context: dict[str, Any] | None = None,
    daily_structure: dict[str, Any] | None = None,
    current_price: float | None = None,
) -> list[dict[str, Any]]:
    """汇总 D1/H4/H1 摆动高低点、前日前周极值、D1延伸目标、SMC及整数关口做共振聚类。"""
    del m15_bars
    ref_price = _to_float(current_price)
    if ref_price is None:
        for seq in (h1_bars, h4_bars, d1_bars):
            if seq:
                ref_price = _to_float(seq[-1].get("close"))
                if ref_price is not None:
                    break
    if ref_price is None or ref_price <= 0:
        return []

    raw_candidates: list[tuple[float, str, str]] = []

    def add_candidate(price: Any, source: str, label: str) -> None:
        p = _to_float(price)
        if p is None or p <= 0:
            return
        raw_candidates.append((p, source, label))

    d1_list = list(d1_bars or [])
    if len(d1_list) >= 2:
        prev_day = d1_list[-2]
        add_candidate(prev_day.get("high"), "D1_PDH_PDL", "PDH")
        add_candidate(prev_day.get("low"), "D1_PDH_PDL", "PDL")
        week_slice = d1_list[-7:-1] if len(d1_list) >= 7 else d1_list[:-1]
        if week_slice:
            wk_highs = [float(b["high"]) for b in week_slice if _to_float(b.get("high")) is not None]
            wk_lows = [float(b["low"]) for b in week_slice if _to_float(b.get("low")) is not None]
            if wk_highs:
                add_candidate(max(wk_highs), "D1_PREV_WEEK", "PWH")
            if wk_lows:
                add_candidate(min(wk_lows), "D1_PREV_WEEK", "PWL")
    if len(d1_list) >= 5:
        d1_hi, d1_lo = _extract_swing_extremes(d1_list)
        for _, p in d1_hi[-3:]:
            add_candidate(p, "D1_SWING", "D1 SwingHigh")
        for _, p in d1_lo[-3:]:
            add_candidate(p, "D1_SWING", "D1 SwingLow")

    if isinstance(trend_context, dict):
        for k, tag in (("prev_day_high", "PDH"), ("prev_day_low", "PDL")):
            add_candidate(trend_context.get(k), "D1_PDH_PDL", tag)
        for k, tag in (("prev_week_high", "PWH"), ("prev_week_low", "PWL")):
            add_candidate(trend_context.get(k), "D1_PREV_WEEK", tag)

    if isinstance(daily_structure, dict):
        add_candidate(daily_structure.get("prior_major_high"), "D1_SWING", "D1 MajorHigh")
        add_candidate(daily_structure.get("prior_major_low"), "D1_SWING", "D1 MajorLow")
        for item in daily_structure.get("measured_targets") or []:
            if isinstance(item, dict):
                add_candidate(item.get("price"), "D1_MEASURED", f"Fib {item.get('ratio')}x")

    if h4_bars and len(h4_bars) >= 5:
        h4_hi, h4_lo = _extract_swing_extremes(h4_bars)
        for _, p in h4_hi[-4:]:
            add_candidate(p, "H4_SWING", "H4 SwingHigh")
        for _, p in h4_lo[-4:]:
            add_candidate(p, "H4_SWING", "H4 SwingLow")

    if h1_bars and len(h1_bars) >= 5:
        h1_hi, h1_lo = _extract_swing_extremes(h1_bars)
        for _, p in h1_hi[-4:]:
            add_candidate(p, "H1_SWING", "H1 SwingHigh")
        for _, p in h1_lo[-4:]:
            add_candidate(p, "H1_SWING", "H1 SwingLow")

    # 已有 SMC / SR 数据提取
    if isinstance(smc_data, dict):
        for tf, src in (("H4", "H4_SMC"), ("H1", "H1_SMC")):
            tf_smc = smc_data.get(tf)
            if not isinstance(tf_smc, dict):
                continue
            for ob in (tf_smc.get("orderBlocks") or [])[:4]:
                if isinstance(ob, dict):
                    add_candidate(ob.get("high") or ob.get("price"), src, f"{tf} OB")
                    add_candidate(ob.get("low"), src, f"{tf} OB")
            for liq in (tf_smc.get("liquidityPools") or tf_smc.get("liquidity") or [])[:4]:
                if isinstance(liq, dict):
                    add_candidate(liq.get("price") or liq.get("level"), src, f"{tf} Liq")

    if isinstance(sr_data, dict):
        for key in ("resistances", "supports", "levels"):
            for item in (sr_data.get(key) or [])[:6]:
                if isinstance(item, dict):
                    add_candidate(item.get("price"), "SR_KEY", str(item.get("label") or key))
                else:
                    add_candidate(item, "SR_KEY", key)

    # 心理整数关口: 针对白银/低价品种缩小搜索宽度, 同时复用 find_psychological_levels
    psych_range = 100.0 if ref_price >= 500 else (15.0 if ref_price >= 15 else 2.0)
    for lvl in find_psychological_levels(ref_price, range_=psych_range):
        add_candidate(lvl.price, "PSYCH_ROUND", lvl.label)
    if ref_price < 500 and ref_price >= 15:
        step = 5.0 if "XAG" in symbol.upper() or "SILVER" in symbol.upper() else 10.0
        base = round(ref_price / step) * step
        for mul in range(-3, 4):
            lvl_p = round(base + mul * step, 2)
            if lvl_p > 0:
                add_candidate(lvl_p, "PSYCH_ROUND", f"Round {lvl_p:g}")

    if not raw_candidates:
        return []

    tolerance = max(ref_price * 0.0025, 0.12 if ref_price < 200 else 2.5)
    raw_candidates.sort(key=lambda x: x[0])

    clusters: list[dict[str, Any]] = []
    for price, source, label in raw_candidates:
        if clusters and abs(price - clusters[-1]["center"]) <= tolerance:
            cl = clusters[-1]
            cl["items"].append((price, source, label))
            w_sum = sum(_SOURCE_WEIGHTS.get(s, 1.0) for _, s, _ in cl["items"])
            cl["center"] = sum(p * _SOURCE_WEIGHTS.get(s, 1.0) for p, s, _ in cl["items"]) / w_sum
        else:
            clusters.append({"center": price, "items": [(price, source, label)]})

    scored: list[dict[str, Any]] = []
    for cl in clusters:
        sources = sorted({s for _, s, _ in cl["items"]})
        labels = list(dict.fromkeys(lb for _, _, lb in cl["items"]))
        base_score = sum(_SOURCE_WEIGHTS.get(s, 1.0) for s in sources)
        confluence_bonus = max(0, len(sources) - 1) * 1.5
        total_score = round(base_score + confluence_bonus, 2)
        center = _round_price(cl["center"], ref_price)
        if abs(center - ref_price) <= tolerance:
            role = "pivot"
        elif center > ref_price:
            role = "resistance"
        else:
            role = "support"
        scored.append(
            {
                "price": center,
                "role": role,
                "tier": "core" if len(sources) >= 2 else "secondary",
                "confluence_count": len(sources),
                "score": total_score,
                "sources": sources,
                "labels": labels[:5],
                "distance_pct": round((center - ref_price) / ref_price * 100.0, 3),
            }
        )

    scored.sort(key=lambda item: (-item["confluence_count"], -item["score"], abs(item["distance_pct"])))
    return scored[:12]


def _parse_bar_timestamp(raw: Any) -> datetime | None:
    """将 Unix 秒/毫秒、ISO8601 或 MT4 点分日期字符串解析为 UTC datetime。"""
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        val = float(raw)
        if not isfinite(val):
            return None
        if abs(val) > 1e12:
            val /= 1000.0
        try:
            return datetime.fromtimestamp(val, tz=UTC)
        except (OSError, OverflowError, ValueError):
            return None

    if not isinstance(raw, str):
        return None
    s = raw.strip()
    if not s:
        return None
    if re.fullmatch(r"-?\d+(?:\.\d+)?", s):
        val = float(s)
        if abs(val) > 1e12:
            val /= 1000.0
        try:
            return datetime.fromtimestamp(val, tz=UTC)
        except (OSError, OverflowError, ValueError):
            return None

    mt_match = re.match(r"^(\d{4})\.(\d{2})\.(\d{2})(?:[ T](\d{2}):(\d{2})(?::(\d{2}))?)?$", s)
    if mt_match:
        yr, mo, dy, hr, mi, sc = (
            int(mt_match.group(1)),
            int(mt_match.group(2)),
            int(mt_match.group(3)),
            int(mt_match.group(4) or 0),
            int(mt_match.group(5) or 0),
            int(mt_match.group(6) or 0),
        )
        return datetime(yr, mo, dy, hr, mi, sc, tzinfo=UTC)

    iso = s[:-1] + "+00:00" if s.endswith("Z") else s
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _select_d1_bar_index(
    bars: Sequence[dict[str, Any]],
    target_bar_time: str | None = None,
    *,
    default_last: bool = False,
) -> int:
    if not bars:
        return -1
    if isinstance(target_bar_time, str) and target_bar_time.strip():
        needle = target_bar_time.strip()
        for idx in range(len(bars) - 1, -1, -1):
            if str(bars[idx].get("time") or "").strip() == needle:
                return idx
        needle_dt = _parse_bar_timestamp(needle)
        if needle_dt is not None:
            for idx in range(len(bars) - 1, -1, -1):
                bar_dt = _parse_bar_timestamp(bars[idx].get("time"))
                if bar_dt is not None and bar_dt.date() == needle_dt.date():
                    return idx
    if default_last:
        return len(bars) - 1
    return len(bars) - 2 if len(bars) >= 2 else 0


def is_weekly_close_bar(
    d1_bars: Sequence[dict[str, Any]],
    target_bar_time: str | None = None,
) -> bool:
    """判断目标已收盘 D1 K 线是否构成周度收盘（周五或节假日提前跨周的最后交易日）。"""
    valid = [
        b
        for b in d1_bars
        if _to_float(b.get("high")) is not None and _to_float(b.get("low")) is not None
    ]
    if not valid:
        return False
    idx = _select_d1_bar_index(valid, target_bar_time=target_bar_time, default_last=False)
    if idx < 0:
        return False
    target_dt = _parse_bar_timestamp(valid[idx].get("time"))
    if target_dt is None:
        return False
    if target_dt.weekday() == 4:
        return True
    if idx + 1 < len(valid):
        next_dt = _parse_bar_timestamp(valid[idx + 1].get("time"))
        if next_dt is not None and next_dt.isocalendar()[:2] != target_dt.isocalendar()[:2]:
            return True
    return False


def summarize_daily_close_recap(
    d1_bars: Sequence[dict[str, Any]],
    *,
    h4_bars: Sequence[dict[str, Any]] | None = None,
    h1_bars: Sequence[dict[str, Any]] | None = None,
    target_bar_time: str | None = None,
) -> dict[str, Any]:
    """汇总刚收盘 D1 K 线的日内走势回顾统计（仅做复盘统计，不施加开仓限制）。"""
    valid = [
        b
        for b in d1_bars
        if _to_float(b.get("open")) is not None
        and _to_float(b.get("high")) is not None
        and _to_float(b.get("low")) is not None
        and _to_float(b.get("close")) is not None
    ]
    if not valid:
        return {"status": "insufficient_data"}

    idx = _select_d1_bar_index(valid, target_bar_time=target_bar_time, default_last=False)
    bar = valid[idx]
    prev_bar = valid[idx - 1] if idx > 0 else None

    o = float(bar["open"])
    h = float(bar["high"])
    low = float(bar["low"])
    c = float(bar["close"])
    bar_dt = _parse_bar_timestamp(bar.get("time"))
    trade_date = bar_dt.date().isoformat() if bar_dt is not None else str(bar.get("time") or "-")

    net_change = c - o
    change_pct = (net_change / o * 100.0) if o > 0 else 0.0
    amplitude = max(0.0, h - low)
    body_ratio = (abs(net_change) / amplitude * 100.0) if amplitude > 0 else 0.0
    if body_ratio < 15.0:
        candle_type = "十字星震荡"
    elif net_change >= 0:
        candle_type = "阳线收涨"
    else:
        candle_type = "阴线收跌"

    prev_high = float(prev_bar["high"]) if prev_bar is not None else None
    prev_low = float(prev_bar["low"]) if prev_bar is not None else None
    prev_close = float(prev_bar["close"]) if prev_bar is not None else None
    if prev_high is not None and prev_low is not None:
        broke_up = h > prev_high
        broke_down = low < prev_low
        if broke_up and broke_down:
            vs_prev_day = "吞没前日区间 (高破低穿)"
        elif broke_up:
            vs_prev_day = "突破前日高点"
        elif broke_down:
            vs_prev_day = "跌破前日低点"
        else:
            vs_prev_day = "前日区间内波动"
    else:
        vs_prev_day = "无前日对照"

    h4_close = _to_float(h4_bars[-1].get("close")) if h4_bars else None
    h1_close = _to_float(h1_bars[-1].get("close")) if h1_bars else None

    return {
        "status": "ok",
        "bar_time": str(bar.get("time") or ""),
        "trade_date": trade_date,
        "open": _round_price(o, c),
        "high": _round_price(h, c),
        "low": _round_price(low, c),
        "close": _round_price(c, c),
        "net_change": round(net_change, 4 if c < 200 else 2),
        "change_pct": round(change_pct, 2),
        "amplitude": _round_price(amplitude, c),
        "body_ratio": round(body_ratio, 1),
        "candle_type": candle_type,
        "prev_high": _round_price(prev_high, c) if prev_high is not None else None,
        "prev_low": _round_price(prev_low, c) if prev_low is not None else None,
        "prev_close": _round_price(prev_close, c) if prev_close is not None else None,
        "vs_prev_day": vs_prev_day,
        "h4_close": _round_price(h4_close, c) if h4_close is not None else None,
        "h1_close": _round_price(h1_close, c) if h1_close is not None else None,
    }


def summarize_weekly_close_recap(
    d1_bars: Sequence[dict[str, Any]],
    *,
    reference_bar_time: str | None = None,
) -> dict[str, Any]:
    """按 ISO 自然周聚合 D1 K 线，生成每周收盘后的周内走势复盘统计。"""
    timed_bars: list[tuple[datetime, dict[str, Any]]] = []
    for bar in d1_bars:
        if (
            _to_float(bar.get("open")) is None
            or _to_float(bar.get("high")) is None
            or _to_float(bar.get("low")) is None
            or _to_float(bar.get("close")) is None
        ):
            continue
        dt = _parse_bar_timestamp(bar.get("time"))
        if dt is not None:
            timed_bars.append((dt, bar))

    if not timed_bars:
        return {"status": "insufficient_data"}

    timed_bars.sort(key=lambda item: item[0])
    target_idx = _select_d1_bar_index(
        [b for _, b in timed_bars],
        target_bar_time=reference_bar_time,
        default_last=True,
    )
    target_dt = timed_bars[target_idx][0]
    target_week = target_dt.isocalendar()[:2]

    week_items = [(dt, b) for dt, b in timed_bars if dt.isocalendar()[:2] == target_week]
    if not week_items:
        return {"status": "insufficient_data"}

    first_week_dt = week_items[0][0]
    prior_items = [(dt, b) for dt, b in timed_bars if dt < first_week_dt]
    prev_week_items: list[tuple[datetime, dict[str, Any]]] = []
    if prior_items:
        prev_week_key = prior_items[-1][0].isocalendar()[:2]
        prev_week_items = [(dt, b) for dt, b in prior_items if dt.isocalendar()[:2] == prev_week_key]

    week_open = float(week_items[0][1]["open"])
    week_high = max(float(b["high"]) for _, b in week_items)
    week_low = min(float(b["low"]) for _, b in week_items)
    week_close = float(week_items[-1][1]["close"])
    net_change = week_close - week_open
    change_pct = (net_change / week_open * 100.0) if week_open > 0 else 0.0
    week_range = max(0.0, week_high - week_low)
    body_ratio = (abs(net_change) / week_range * 100.0) if week_range > 0 else 0.0

    if body_ratio < 15.0:
        weekly_candle_type = "周线十字震荡"
    elif net_change >= 0:
        weekly_candle_type = "周线收阳"
    else:
        weekly_candle_type = "周线收阴"

    up_days = sum(1 for _, b in week_items if float(b["close"]) > float(b["open"]))
    down_days = sum(1 for _, b in week_items if float(b["close"]) < float(b["open"]))

    daily_breakdown: list[dict[str, Any]] = []
    for dt, b in week_items:
        bo = float(b["open"])
        bc = float(b["close"])
        bh = float(b["high"])
        bl = float(b["low"])
        pct = ((bc - bo) / bo * 100.0) if bo > 0 else 0.0
        daily_breakdown.append(
            {
                "date": dt.date().isoformat(),
                "open": _round_price(bo, week_close),
                "high": _round_price(bh, week_close),
                "low": _round_price(bl, week_close),
                "close": _round_price(bc, week_close),
                "change_pct": round(pct, 2),
            }
        )

    prev_week_high: float | None = None
    prev_week_low: float | None = None
    prev_week_close: float | None = None
    if prev_week_items:
        prev_week_high = max(float(b["high"]) for _, b in prev_week_items)
        prev_week_low = min(float(b["low"]) for _, b in prev_week_items)
        prev_week_close = float(prev_week_items[-1][1]["close"])
        broke_up = week_high > prev_week_high
        broke_down = week_low < prev_week_low
        if broke_up and broke_down:
            vs_prev_week = "吞没上周区间 (上破下穿)"
        elif broke_up:
            vs_prev_week = "突破上周高点"
        elif broke_down:
            vs_prev_week = "跌破上周低点"
        else:
            vs_prev_week = "上周区间内震荡"
    else:
        vs_prev_week = "无上周对照"

    iso_year, iso_wk = target_week
    return {
        "status": "ok",
        "iso_week": f"{iso_year}-W{iso_wk:02d}",
        "week_start_date": week_items[0][0].date().isoformat(),
        "week_end_date": week_items[-1][0].date().isoformat(),
        "trading_days_count": len(week_items),
        "week_open": _round_price(week_open, week_close),
        "week_high": _round_price(week_high, week_close),
        "week_low": _round_price(week_low, week_close),
        "week_close": _round_price(week_close, week_close),
        "net_change": round(net_change, 4 if week_close < 200 else 2),
        "change_pct": round(change_pct, 2),
        "week_range": _round_price(week_range, week_close),
        "weekly_candle_type": weekly_candle_type,
        "up_days": up_days,
        "down_days": down_days,
        "prev_week_high": _round_price(prev_week_high, week_close) if prev_week_high is not None else None,
        "prev_week_low": _round_price(prev_week_low, week_close) if prev_week_low is not None else None,
        "prev_week_close": _round_price(prev_week_close, week_close) if prev_week_close is not None else None,
        "vs_prev_week": vs_prev_week,
        "daily_breakdown": daily_breakdown,
    }


__all__ = [
    "analyze_daily_vacuum",
    "build_confluence_levels",
    "compute_day_range",
    "is_weekly_close_bar",
    "summarize_daily_close_recap",
    "summarize_weekly_close_recap",
]
