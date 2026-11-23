"""SOP 市场结构工具单元测试: D1 真空带延伸目标、当日振幅与多源共振关键位。"""

from __future__ import annotations

from backend.agents.tools.market_structure_sop import (
    analyze_daily_vacuum,
    build_confluence_levels,
    compute_day_range,
    is_weekly_close_bar,
    summarize_daily_close_recap,
    summarize_weekly_close_recap,
)


def _d1_breakout_bars(base: float = 50.0, peak: float = 61.44, final_close: float = 64.5) -> list[dict[str, float]]:
    bars: list[dict[str, float]] = []
    for i in range(18):
        lo = base + (i % 4) * 0.6
        hi = min(peak, base + 4.0 + i * 0.45)
        if i == 10:
            hi = peak
        if i == 14:
            lo = 54.0
        cl = (lo + hi) / 2.0
        bars.append({"open": lo + 0.5, "high": hi, "low": lo, "close": cl, "time": f"2026-11-{i + 1:02d}"})
    # 最近两日突破前高 61.44 进入真空区
    bars.append({"open": 60.8, "high": 63.5, "low": 60.5, "close": 63.1, "time": "2026-11-19"})
    bars.append({"open": 63.1, "high": final_close + 0.4, "low": 62.4, "close": final_close, "time": "2026-11-20"})
    return bars


def test_analyze_daily_vacuum_detects_bullish_breakout_and_measured_targets() -> None:
    bars = _d1_breakout_bars(base=50.0, peak=61.44, final_close=64.5)
    result = analyze_daily_vacuum(bars, current_price=64.5)

    assert result["status"] == "ok"
    assert result["macro_bias"] == "bullish_breakout"
    assert result["in_vacuum"] is True
    assert result["prior_major_high"] == 61.44
    assert result["vacuum_zone"] is not None
    assert result["vacuum_zone"]["low"] == 61.44
    ratios = [t["ratio"] for t in result["measured_targets"]]
    assert ratios == [1.0, 1.272, 1.618, 2.0, 2.618]
    prices = [t["price"] for t in result["measured_targets"]]
    assert prices == sorted(prices)
    assert all(p > 61.44 for p in prices)


def test_analyze_daily_vacuum_bearish_breakout_and_insufficient_data() -> None:
    insufficient = analyze_daily_vacuum([{"open": 10, "high": 11, "low": 9, "close": 10}])
    assert insufficient["status"] == "insufficient_data"

    bear_bars = [
        {"open": 4100.0, "high": 4120.0 - i * 3, "low": 4050.0 - (i % 3) * 2, "close": 4070.0}
        for i in range(18)
    ]
    bear_bars.append({"open": 4065.0, "high": 4070.0, "low": 4010.0, "close": 4015.0})
    bear_bars.append({"open": 4015.0, "high": 4020.0, "low": 3980.0, "close": 3985.0})
    bear_res = analyze_daily_vacuum(bear_bars, current_price=3985.0)

    assert bear_res["status"] == "ok"
    assert bear_res["macro_bias"] == "bearish_breakout"
    assert bear_res["in_vacuum"] is True
    prices = [t["price"] for t in bear_res["measured_targets"]]
    assert prices == sorted(prices, reverse=True)


def test_compute_day_range_tracks_atr_consumption_and_exhaustion() -> None:
    d1_bars = [
        {"open": 4000.0, "high": 4040.0, "low": 3990.0, "close": 4020.0}
        for _ in range(14)
    ]
    # ATR ~ 50; 当日实走 49 -> 消耗 98% >= 95% 标记耗尽
    d1_bars.append({"open": 4020.0, "high": 4065.0, "low": 4016.0, "close": 4055.0})
    dr = compute_day_range("XAUUSD", d1_bars, current_price=4055.0)

    assert dr["status"] == "ok"
    assert dr["realized_range"] == 49.0
    assert dr["atr_consumed_pct"] >= 95.0
    assert dr["range_exhausted"] is True
    assert dr["expansion_stage"] in ("late", "extended")
    assert dr["session_bias"] == "bullish"


def test_build_confluence_levels_promotes_multi_source_levels_for_gold_and_silver() -> None:
    d1_bars = _d1_breakout_bars(base=50.0, peak=61.44, final_close=64.5)
    # 人为构造 PDH=65.0 与整数关口 65.0 / H4 swing 重合
    d1_bars[-2]["high"] = 65.0
    h4_bars = [
        {"open": 62.0, "high": 63.0, "low": 61.5, "close": 62.5},
        {"open": 62.5, "high": 64.0, "low": 62.0, "close": 63.8},
        {"open": 63.8, "high": 65.02, "low": 63.2, "close": 64.1},
        {"open": 64.1, "high": 64.5, "low": 63.0, "close": 63.6},
        {"open": 63.6, "high": 64.2, "low": 63.1, "close": 64.0},
    ]
    daily = analyze_daily_vacuum(d1_bars, current_price=64.0)
    levels = build_confluence_levels(
        "XAGUSD",
        d1_bars=d1_bars,
        h4_bars=h4_bars,
        daily_structure=daily,
        current_price=64.0,
    )

    assert levels
    core_65 = [lv for lv in levels if abs(lv["price"] - 65.0) <= 0.2]
    assert core_65
    assert core_65[0]["tier"] == "core"
    assert core_65[0]["confluence_count"] >= 2
    assert "D1_PDH_PDL" in core_65[0]["sources"]
    assert "PSYCH_ROUND" in core_65[0]["sources"]


def test_daily_and_weekly_close_recap_helpers() -> None:
    # 2026-11-16 (Mon) ~ 2026-11-20 (Fri), plus 2026-11-23 (Mon, unix seconds) as forming bar
    d1_bars = [
        {"time": "2026-11-13", "open": 3590.0, "high": 3620.0, "low": 3580.0, "close": 3600.0},
        {"time": "2026-11-16", "open": 3600.0, "high": 3630.0, "low": 3595.0, "close": 3625.0},
        {"time": "2026-11-17", "open": 3625.0, "high": 3640.0, "low": 3610.0, "close": 3635.0},
        {"time": "2026-11-18", "open": 3635.0, "high": 3645.0, "low": 3605.0, "close": 3615.0},
        {"time": "2026-11-19", "open": 3615.0, "high": 3638.0, "low": 3608.0, "close": 3630.0},
        {"time": "2026-11-20", "open": 3630.0, "high": 3665.0, "low": 3628.0, "close": 3660.0},
        # Next Monday (Unix seconds 2026-11-23T00:00:00Z = 1795392000) forming bar
        {"time": "1795392000", "open": 3660.0, "high": 3670.0, "low": 3650.0, "close": 3662.0},
    ]

    assert is_weekly_close_bar(d1_bars, target_bar_time="2026-11-19") is False
    assert is_weekly_close_bar(d1_bars, target_bar_time="2026-11-20") is True

    daily_fri = summarize_daily_close_recap(d1_bars, target_bar_time="2026-11-20")
    assert daily_fri["status"] == "ok"
    assert daily_fri["trade_date"] == "2026-11-20"
    assert daily_fri["candle_type"] == "阳线收涨"
    assert daily_fri["vs_prev_day"] == "突破前日高点"
    assert daily_fri["net_change"] == 30.0

    weekly = summarize_weekly_close_recap(d1_bars, reference_bar_time="2026-11-20")
    assert weekly["status"] == "ok"
    assert weekly["iso_week"] == "2026-W47"
    assert weekly["week_start_date"] == "2026-11-16"
    assert weekly["week_end_date"] == "2026-11-20"
    assert weekly["trading_days_count"] == 5
    assert weekly["week_open"] == 3600.0
    assert weekly["week_close"] == 3660.0
    assert weekly["week_high"] == 3665.0
    assert weekly["week_low"] == 3595.0
    assert weekly["weekly_candle_type"] == "周线收阳"
    assert weekly["vs_prev_week"] == "突破上周高点"
    assert len(weekly["daily_breakdown"]) == 5

    # 节假日周五休市时, 周四同样被识别为本周最后一根收盘 D1
    holiday_bars = d1_bars[:-2] + [d1_bars[-1]]
    assert is_weekly_close_bar(holiday_bars, target_bar_time="2026-11-19") is True
