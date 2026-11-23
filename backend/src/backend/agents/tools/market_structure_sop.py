"""SOP 市场结构工具: D1 真空带与等距延伸目标、多源共振关键位、当日波动边界。"""

from __future__ import annotations

from collections.abc import Sequence
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


__all__ = [
    "analyze_daily_vacuum",
    "build_confluence_levels",
    "compute_day_range",
]
