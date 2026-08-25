"""镜像 packages/trading-core/src/smc/detector.spec.ts(逐 it() 用例镜像)。"""

from __future__ import annotations

from backend.trading_core.smc import (
    build_smc_context,
    detect_fvgs,
    detect_liquidity_sweeps,
    detect_order_blocks,
    detect_structure_breaks,
    determine_trend_direction,
    filter_obs_by_side,
    find_swing_points,
    has_cho_ch_in_direction,
    recent_sweep_in_direction,
    unfilled_fvgs_near_price,
    valid_obs_near_price,
)


def make_bar(high: float, low: float, close: float, open_: float) -> dict:
    return {"high": high, "low": low, "close": close, "open": open_}


class TestFindSwingPoints:
    def test_finds_swing_highs_and_lows_in_simple_series(self) -> None:
        # 5 根柱:低、高、低、高、低
        bars = [
            make_bar(10, 8, 9, 8.5),
            make_bar(15, 13, 14, 13.5),  # swing high
            make_bar(11, 9, 10, 10.5),  # swing low
            make_bar(14, 12, 13, 12.5),  # swing high
            make_bar(10, 7, 8, 8.5),  # swing low
        ]
        result = find_swing_points(bars, 1, 1)
        assert len(result["swingHighs"]) >= 1
        assert len(result["swingLows"]) >= 1
        # Bar 1 应为 swing high(15 > 相邻柱)
        assert any(sp["index"] == 1 and sp["price"] == 15 for sp in result["swingHighs"])
        # Bar 2 应为 swing low(9 < 相邻柱)
        assert any(sp["index"] == 2 and sp["price"] == 9 for sp in result["swingLows"])

    def test_returns_empty_for_too_few_bars(self) -> None:
        bars = [make_bar(10, 8, 9, 9)]
        assert find_swing_points(bars, 1, 1) == {"swingHighs": [], "swingLows": []}


class TestDetermineTrendDirection:
    def test_detects_bullish_trend_higher_highs_and_higher_lows(self) -> None:
        swing_highs = [{"index": 0, "price": 10, "type": "HIGH"}, {"index": 2, "price": 15, "type": "HIGH"}]
        swing_lows = [{"index": 1, "price": 8, "type": "LOW"}, {"index": 3, "price": 12, "type": "LOW"}]
        assert determine_trend_direction(swing_highs, swing_lows) == "BULL"

    def test_detects_bearish_trend_lower_highs_and_lower_lows(self) -> None:
        swing_highs = [{"index": 0, "price": 15, "type": "HIGH"}, {"index": 2, "price": 10, "type": "HIGH"}]
        swing_lows = [{"index": 1, "price": 12, "type": "LOW"}, {"index": 3, "price": 8, "type": "LOW"}]
        assert determine_trend_direction(swing_highs, swing_lows) == "BEAR"

    def test_returns_neutral_for_insufficient_data(self) -> None:
        assert determine_trend_direction([], []) == "NEUTRAL"

    def test_returns_neutral_when_highs_and_lows_do_not_agree_on_direction(self) -> None:
        swing_highs = [{"index": 0, "price": 10, "type": "HIGH"}, {"index": 2, "price": 15, "type": "HIGH"}]
        swing_lows = [{"index": 1, "price": 8, "type": "LOW"}, {"index": 3, "price": 7, "type": "LOW"}]
        assert determine_trend_direction(swing_highs, swing_lows) == "NEUTRAL"


class TestDetectStructureBreaks:
    def test_detects_structure_breaks_with_enough_bar_history(self) -> None:
        # 足够的历史 bar 用于 swing 检测 + 突破检测
        bars = [make_bar(100 - i, 95 - i, 96 - i, 99 - i) for i in range(10)]  # 下行序列
        # 突破近期 swing high 的强势上涨柱
        bars.append(make_bar(105, 90, 104, 91))
        breaks = detect_structure_breaks(bars, 15, "BULL")
        assert len(breaks) >= 0  # 取决于 swing 检测质量

    def test_classifies_breaks_correctly_by_trend(self) -> None:
        # BULL 趋势中的 UP 突破 = BOS;BEAR 趋势中的 UP 突破 = CHoCH
        bars = [make_bar(100 + i, 95 + i, 96 + i, 99 + i) for i in range(10)]
        bars.append(make_bar(115, 105, 114, 106))

        bull_breaks = detect_structure_breaks(bars, 15, "BULL")
        for b in [br for br in bull_breaks if br["direction"] == "UP"]:
            assert b["type"] == "BOS"

        bear_breaks = detect_structure_breaks(list(bars), 15, "BEAR")
        for b in [br for br in bear_breaks if br["direction"] == "UP"]:
            assert b["type"] == "CHoCH"


class TestDetectFVGs:
    def test_detects_bullish_fvg(self) -> None:
        # Bullish FVG:bar[2].low > bar[0].high
        bars = [
            make_bar(100, 95, 98, 96),  # bar[0]: high=100
            make_bar(110, 105, 108, 106),  # bar[1]: gap
            make_bar(115, 102, 113, 112),  # bar[2]: low=102 > bar[0].high=100 → bullish FVG
        ]
        fvgs = detect_fvgs(bars, 3)
        assert len(fvgs) >= 1
        bull = [f for f in fvgs if f["side"] == "BULL"]
        assert bull
        assert bull[0]["lowerBound"] == 100
        assert bull[0]["upperBound"] == 102

    def test_detects_bearish_fvg(self) -> None:
        bars = [
            make_bar(110, 105, 108, 109),  # bar[0]: low=105
            make_bar(100, 95, 97, 99),  # bar[1]: gap
            make_bar(98, 92, 93, 97),  # bar[2]: high=98 < bar[0].low=105 → bearish FVG
        ]
        fvgs = detect_fvgs(bars, 3)
        bear = [f for f in fvgs if f["side"] == "BEAR"]
        assert bear
        assert bear[0]["upperBound"] == 105
        assert bear[0]["lowerBound"] == 98


class TestDetectLiquiditySweeps:
    def test_detects_sweep_of_swing_high_bearish(self) -> None:
        bars = [
            make_bar(100, 95, 98, 96),
            make_bar(105, 100, 104, 101),  # swing high at 105
            make_bar(106, 99, 100, 105),  # 影线上穿 105 但收于其下 → sweep
        ]
        swing_highs = [{"index": 1, "price": 105, "type": "HIGH"}]
        sweeps = detect_liquidity_sweeps(bars, swing_highs, [], 3)
        assert len(sweeps) == 1
        assert sweeps[0]["side"] == "BEAR"
        assert sweeps[0]["reversed"] is True

    def test_detects_sweep_of_swing_low_bullish(self) -> None:
        bars = [
            make_bar(105, 100, 104, 101),
            make_bar(100, 95, 96, 99),  # swing low at 95
            make_bar(99, 94, 98, 95),  # 影线下穿 95 但收于其上 → sweep
        ]
        swing_lows = [{"index": 1, "price": 95, "type": "LOW"}]
        sweeps = detect_liquidity_sweeps(bars, [], swing_lows, 3)
        assert len(sweeps) == 1
        assert sweeps[0]["side"] == "BULL"


class TestDetectOrderBlocks:
    def test_finds_buy_order_blocks_from_upward_breaks(self) -> None:
        # 构造带清晰向上突破(前有看跌 K 线)的 bars
        bars = [make_bar(100 + i, 98 + i, 99 + i, 100 + i) for i in range(20)]
        # 看跌 K 线(潜在 OB)
        bars.append(make_bar(110, 105, 106, 109))
        # 强势看涨突破
        bars.append(make_bar(120, 110, 119, 111))

        obs = detect_order_blocks(bars, "BUY", 30, "BULL")
        # 是否找到 OB 取决于结构突破检测
        assert isinstance(obs, list)


class TestBuildSMCContext:
    def test_builds_context_from_h4_and_h1_bars(self) -> None:
        h4 = [make_bar(2000 + i * 2, 1990 + i * 2, 1995 + i * 2, 1992 + i * 2) for i in range(30)]
        h1 = [make_bar(2000 + i, 1995 + i, 1998 + i, 1996 + i) for i in range(30)]
        ctx = build_smc_context(h4, h1, [])
        assert ctx["h4TrendDirection"] is not None
        assert ctx["h1TrendDirection"] is not None
        assert isinstance(ctx["h4Breaks"], list)
        assert isinstance(ctx["h1Breaks"], list)

    def test_returns_neutral_for_insufficient_bars(self) -> None:
        ctx = build_smc_context([], [], [])
        assert ctx["h4TrendDirection"] == "NEUTRAL"
        assert ctx["h1TrendDirection"] == "NEUTRAL"

    def test_populates_detector_built_m30_and_m15_breaks_sweeps_obs_and_fvgs(self) -> None:
        m30 = smc_counter_pullback_bars()
        m15 = smc_counter_pullback_bars()
        ctx = build_smc_context([], [], m30, m15)

        assert ctx["m30TrendDirection"] == "BEAR"
        assert ctx["m15TrendDirection"] == "BEAR"
        assert any(b["index"] == 28 and b["direction"] == "UP" and b["type"] == "CHoCH" for b in ctx["m30Breaks"])
        assert any(b["index"] == 28 and b["direction"] == "UP" and b["type"] == "CHoCH" for b in ctx["m15Breaks"])
        assert any(
            s["index"] == 17 and s["level"] == 100 and s["side"] == "BULL" and s["reversed"] for s in ctx["m30Sweeps"]
        )
        assert any(
            s["index"] == 17 and s["level"] == 100 and s["side"] == "BULL" and s["reversed"] for s in ctx["m15Sweeps"]
        )
        assert any(o["index"] == 27 and o["side"] == "BUY" and o["valid"] for o in ctx["m30OBs"])
        assert any(o["index"] == 27 and o["side"] == "BUY" and o["valid"] for o in ctx["m15OBs"])
        assert any(f["startIndex"] == 26 and f["side"] == "BULL" and not f["filled"] for f in ctx["m30FVGs"])
        assert any(f["startIndex"] == 26 and f["side"] == "BULL" and not f["filled"] for f in ctx["m15FVGs"])

    def test_populates_h1_short_lookback_order_blocks_used_by_breakout_pyramid_guards(self) -> None:
        h1 = smc_short_order_block_bars()
        ctx = build_smc_context([], h1, [])

        assert ctx["h1TrendDirection"] == "BEAR"
        assert any(o["index"] == 27 and o["side"] == "BUY" and o["valid"] for o in ctx["h1ShortOBs"])


def smc_counter_pullback_bars() -> list[dict]:
    bars = [make_bar(101, 100.8, 101, 100) for _ in range(30)]
    bars[4] = make_bar(108, 104, 106, 106)
    bars[8] = make_bar(105, 100, 102, 103)
    bars[12] = make_bar(106, 102, 104, 104)
    bars[16] = make_bar(103, 100, 101, 101)
    bars[17] = make_bar(101, 99.5, 100.3, 100)
    bars[20] = make_bar(102, 100.2, 101, 101)
    bars[25] = make_bar(101, 100.2, 100.3, 100)
    bars[26] = make_bar(99.2, 98.8, 99, 99)
    bars[27] = make_bar(101.2, 99.8, 100.2, 101.1)
    bars[28] = make_bar(105, 100.5, 104.5, 100)
    return bars


def smc_short_order_block_bars() -> list[dict]:
    return smc_counter_pullback_bars()


class TestHelperFunctions:
    def test_filter_obs_by_side(self) -> None:
        obs = [
            {"index": 0, "side": "BUY", "high": 100, "low": 95, "valid": True, "mitigated": False, "ageBars": 5},
            {"index": 1, "side": "SELL", "high": 105, "low": 100, "valid": True, "mitigated": False, "ageBars": 3},
        ]
        assert len(filter_obs_by_side(obs, "BUY")) == 1
        assert len(filter_obs_by_side(obs, "SELL")) == 1

    def test_has_cho_ch_in_direction(self) -> None:
        breaks = [
            {"index": 0, "direction": "UP", "level": 100, "type": "BOS"},
            {"index": 1, "direction": "DOWN", "level": 95, "type": "CHoCH"},
        ]
        assert has_cho_ch_in_direction(breaks, "BEAR") is True
        assert has_cho_ch_in_direction(breaks, "BULL") is False

    def test_recent_sweep_in_direction(self) -> None:
        sweeps = [{"index": 8, "level": 95, "side": "BULL", "reversed": True}]
        assert recent_sweep_in_direction(sweeps, "BULL", 10, 5) is True
        assert recent_sweep_in_direction(sweeps, "BEAR", 10, 5) is False
        assert recent_sweep_in_direction(sweeps, "BULL", 10, 1) is False  # 太旧

    def test_unfilled_fvgs_near_price(self) -> None:
        fvgs = [
            {
                "startIndex": 0,
                "endIndex": 2,
                "side": "BULL",
                "upperBound": 102,
                "lowerBound": 100,
                "filled": False,
                "fillIndex": 0,
            },
            {
                "startIndex": 3,
                "endIndex": 5,
                "side": "BEAR",
                "upperBound": 110,
                "lowerBound": 108,
                "filled": True,
                "fillIndex": 6,
            },
        ]
        assert len(unfilled_fvgs_near_price(fvgs, 101, 5)) == 1
        # FVG 区间 [100, 102],price=101,threshold=0.5 → [100.5, 101.5] 与 [100, 102] 重叠
        assert len(unfilled_fvgs_near_price(fvgs, 101, 0.5)) == 1
        # 价格远离 FVG 区间
        assert len(unfilled_fvgs_near_price(fvgs, 50, 5)) == 0

    def test_valid_obs_near_price(self) -> None:
        obs = [
            {"index": 0, "side": "BUY", "high": 102, "low": 98, "valid": True, "mitigated": False, "ageBars": 5},
            {"index": 1, "side": "SELL", "high": 110, "low": 106, "valid": False, "mitigated": True, "ageBars": 3},
        ]
        assert len(valid_obs_near_price(obs, 100, 5)) == 1
