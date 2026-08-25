"""SMC(Smart Money Concepts)检测器(镜像 packages/trading-core/src/smc/detector.ts)。

按 TS oracle 语义 1:1 移植:swing 高点/低点、趋势方向推断、结构突破
(BOS/CHoCH)、FVG、流动性扫荡、订单块检测,以及多周期 SMC 上下文构建。
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------- 类型别名

SmcBar = dict[str, Any]
"""镜像 SmcBar:high/low/close/open。"""

SwingPoint = dict[str, Any]
"""镜像 SwingPoint:{index, price, type:'HIGH'|'LOW'}。"""

StructureBreak = dict[str, Any]
"""镜像 StructureBreak:{index, direction:'UP'|'DOWN', level, type:'BOS'|'CHoCH'}。"""

FVG = dict[str, Any]
"""镜像 FVG:{startIndex, endIndex, side:'BULL'|'BEAR', upperBound, lowerBound, filled, fillIndex}。"""

OrderBlock = dict[str, Any]
"""镜像 OrderBlock:{index, side:'BUY'|'SELL', high, low, valid, mitigated, ageBars}。"""

LiquiditySweep = dict[str, Any]
"""镜像 LiquiditySweep:{index, level, side:'BULL'|'BEAR', reversed}。"""

SMCContext = dict[str, Any]
"""镜像 SMCContext:多周期 OB/FVG/Breaks/Sweeps 与趋势方向。"""

# 镜像 Number.MAX_SAFE_INTEGER(JS 可安全表示的最大整数,用于初始最低价)
_MAX_SAFE_INTEGER = 2**53 - 1


# ---------------------------------------------------------------- Swing 点检测


def find_swing_points(bars: list[SmcBar], left: int, right: int) -> dict[str, list[SwingPoint]]:
    """镜像 findSwingPoints:N 柱 pivot 逻辑检测局部 swing 高/低点。

    某柱为 swing high 当且仅当其 High 大于前后 `left`/`right` 根柱;
    swing low 类似。
    """
    if left < 1:
        left = 1
    if right < 1:
        right = 1
    if len(bars) < left + right + 1:
        return {"swingHighs": [], "swingLows": []}

    swing_highs: list[SwingPoint] = []
    swing_lows: list[SwingPoint] = []

    for i in range(left, len(bars) - right):
        high = bars[i]["high"]
        low = bars[i]["low"]
        is_swing_high = True
        is_swing_low = True

        for j in range(i - left, i + right + 1):
            if j == i:
                continue
            if bars[j]["high"] >= high:
                is_swing_high = False
            if bars[j]["low"] <= low:
                is_swing_low = False
            if not is_swing_high and not is_swing_low:
                break

        if is_swing_high:
            swing_highs.append({"index": i, "price": high, "type": "HIGH"})
        if is_swing_low:
            swing_lows.append({"index": i, "price": low, "type": "LOW"})

    return {"swingHighs": swing_highs, "swingLows": swing_lows}


# ---------------------------------------------------------------- 趋势方向


def determine_trend_direction(swing_highs: list[SwingPoint], swing_lows: list[SwingPoint]) -> str:
    """镜像 determineTrendDirection:由近期 swing 序列推断趋势,返回 BULL/BEAR/NEUTRAL。"""
    events: list[dict[str, Any]] = []
    for sh in swing_highs:
        events.append({"index": sh["index"], "price": sh["price"], "isHigh": True})
    for sl in swing_lows:
        events.append({"index": sl["index"], "price": sl["price"], "isHigh": False})

    if len(events) < 4:
        return "NEUTRAL"

    # 按 index 升序排序(与 TS Array.prototype.sort 一致,稳定排序)
    events.sort(key=lambda e: e["index"])

    recent_highs: list[SwingPoint] = []
    recent_lows: list[SwingPoint] = []
    for e in events:
        if e["isHigh"]:
            recent_highs.append({"index": e["index"], "price": e["price"], "type": "HIGH"})
        else:
            recent_lows.append({"index": e["index"], "price": e["price"], "type": "LOW"})

    bullish = (
        len(recent_highs) >= 2
        and len(recent_lows) >= 2
        and recent_highs[-1]["price"] > recent_highs[-2]["price"]
        and recent_lows[-1]["price"] > recent_lows[-2]["price"]
    )

    bearish = (
        len(recent_highs) >= 2
        and len(recent_lows) >= 2
        and recent_highs[-1]["price"] < recent_highs[-2]["price"]
        and recent_lows[-1]["price"] < recent_lows[-2]["price"]
    )

    if bullish:
        return "BULL"
    if bearish:
        return "BEAR"
    return "NEUTRAL"


# ---------------------------------------------------------------- 结构突破检测(BOS + CHoCH)


def detect_structure_breaks(bars: list[SmcBar], lookback: int, trend_direction: str) -> list[StructureBreak]:
    """镜像 detectStructureBreaks:对照主流趋势识别 BOS(延续)与 CHoCH(反转)。

    trend_direction 为 "BULL"/"BEAR"/"NEUTRAL";为空时由 swing 点自动推断。
    """
    if len(bars) < 3:
        return []
    if lookback <= 0 or lookback > len(bars):
        lookback = len(bars)

    start = len(bars) - lookback
    window = bars[start:]

    # 先尝试基于 swing 点的检测
    found = find_swing_points(window, 3, 3)
    swing_highs = found["swingHighs"]
    swing_lows = found["swingLows"]

    # 把 index 平移回完整 bar 序列
    swing_highs = [{**sp, "index": sp["index"] + start} for sp in swing_highs]
    swing_lows = [{**sp, "index": sp["index"] + start} for sp in swing_lows]

    # 未提供趋势时自动推断
    if not trend_direction:
        trend_direction = determine_trend_direction(swing_highs, swing_lows)

    # 有 swing 点时用其做精确检测
    if len(swing_highs) > 0 or len(swing_lows) > 0:
        swing_breaks = detect_breaks_from_swings(bars, start, swing_highs, swing_lows, trend_direction)

        # 只有一种 swing 点时,用 fallback 补充
        if len(swing_highs) == 0 or len(swing_lows) == 0:
            fallback_breaks = detect_breaks_from_recent_extremes(bars, start, trend_direction)
            seen: set[str] = set()
            for b in swing_breaks:
                seen.add(f"{b['index']}-{b['direction']}")
            for b in fallback_breaks:
                key = f"{b['index']}-{b['direction']}"
                if key not in seen:
                    swing_breaks.append(b)

        return swing_breaks

    # fallback:简单的近期高低点突破检测
    return detect_breaks_from_recent_extremes(bars, start, trend_direction)


def detect_breaks_from_swings(
    bars: list[SmcBar],
    start: int,
    swing_highs: list[SwingPoint],
    swing_lows: list[SwingPoint],
    trend_direction: str,
) -> list[StructureBreak]:
    """镜像 detectBreaksFromSwings:基于已确认 swing 点的精细突破检测。"""
    events: list[StructureBreak] = []
    high_cursor = 0
    low_cursor = 0

    for i in range(start, len(bars)):
        while high_cursor < len(swing_highs) and swing_highs[high_cursor]["index"] < i:
            high_cursor += 1
        while low_cursor < len(swing_lows) and swing_lows[low_cursor]["index"] < i:
            low_cursor += 1

        if high_cursor > 0:
            level = swing_highs[high_cursor - 1]["price"]
            if bars[i]["close"] > level and (i == 0 or bars[i - 1]["close"] <= level):
                events.append(
                    {
                        "index": i,
                        "direction": "UP",
                        "level": level,
                        "type": classify_break("UP", trend_direction),
                    }
                )

        if low_cursor > 0:
            level = swing_lows[low_cursor - 1]["price"]
            if bars[i]["close"] < level and (i == 0 or bars[i - 1]["close"] >= level):
                events.append(
                    {
                        "index": i,
                        "direction": "DOWN",
                        "level": level,
                        "type": classify_break("DOWN", trend_direction),
                    }
                )

    return events


def detect_breaks_from_recent_extremes(
    bars: list[SmcBar], start: int, trend_direction: str
) -> list[StructureBreak]:
    """镜像 detectBreaksFromRecentExtremes:用最近 5 根柱的高低点做突破检测。"""
    events: list[StructureBreak] = []
    window_size = 5

    for i in range(start + window_size, len(bars)):
        recent_high = 0
        recent_low = _MAX_SAFE_INTEGER
        recent_high_idx = -1
        recent_low_idx = -1

        for j in range(i - window_size, i):
            if j < start:
                continue
            if bars[j]["high"] > recent_high:
                recent_high = bars[j]["high"]
                recent_high_idx = j
            if bars[j]["low"] < recent_low:
                recent_low = bars[j]["low"]
                recent_low_idx = j

        if recent_high_idx < 0 and recent_low_idx < 0:
            continue

        if recent_high_idx >= 0 and bars[i]["close"] > recent_high and (i == 0 or bars[i - 1]["close"] <= recent_high):
            events.append(
                {
                    "index": i,
                    "direction": "UP",
                    "level": recent_high,
                    "type": classify_break("UP", trend_direction),
                }
            )

        if recent_low_idx >= 0 and bars[i]["close"] < recent_low and (i == 0 or bars[i - 1]["close"] >= recent_low):
            events.append(
                {
                    "index": i,
                    "direction": "DOWN",
                    "level": recent_low,
                    "type": classify_break("DOWN", trend_direction),
                }
            )

    return events


def classify_break(break_dir: str, trend_direction: str) -> str:
    """镜像 classifyBreak:判定结构突破是 BOS(延续)还是 CHoCH(反转)。"""
    if trend_direction == "BULL":
        return "BOS" if break_dir == "UP" else "CHoCH"
    if trend_direction == "BEAR":
        return "BOS" if break_dir == "DOWN" else "CHoCH"
    return "BOS"  # 未知趋势——全部标为 BOS(保守)


# ---------------------------------------------------------------- FVG 检测


def detect_fvgs(bars: list[SmcBar], lookback: int) -> list[FVG]:
    """镜像 detectFVGs:检测 Fair Value Gap。

    Bullish FVG:bars[i+2].Low > bars[i].High
    Bearish FVG:bars[i+2].High < bars[i].Low
    """
    if len(bars) < 3:
        return []
    if lookback <= 0 or lookback > len(bars):
        lookback = len(bars)

    start = len(bars) - lookback
    gaps: list[FVG] = []

    for i in range(start, len(bars) - 2):
        first = bars[i]
        third = bars[i + 2]

        # Bullish FVG:第三根 Low > 第一根 High
        if third["low"] > first["high"]:
            fvg: FVG = {
                "startIndex": i,
                "endIndex": i + 2,
                "side": "BULL",
                "upperBound": third["low"],
                "lowerBound": first["high"],
                "filled": False,
                "fillIndex": 0,
            }
            fvg = check_fvg_fill(fvg, bars, i + 3)
            gaps.append(fvg)

        # Bearish FVG:第三根 High < 第一根 Low
        if third["high"] < first["low"]:
            fvg = {
                "startIndex": i,
                "endIndex": i + 2,
                "side": "BEAR",
                "upperBound": first["low"],
                "lowerBound": third["high"],
                "filled": False,
                "fillIndex": 0,
            }
            fvg = check_fvg_fill(fvg, bars, i + 3)
            gaps.append(fvg)

    return gaps


def check_fvg_fill(fvg: FVG, bars: list[SmcBar], from_index: int) -> FVG:
    """镜像 checkFVGFill:从 fromIndex 起检查价格是否回撤填充 gap。"""
    for j in range(from_index, len(bars)):
        if fvg["side"] == "BULL" and bars[j]["low"] <= fvg["lowerBound"]:
            return {**fvg, "filled": True, "fillIndex": j}
        if fvg["side"] == "BEAR" and bars[j]["high"] >= fvg["upperBound"]:
            return {**fvg, "filled": True, "fillIndex": j}
    return fvg


# ---------------------------------------------------------------- 流动性扫荡检测


def detect_liquidity_sweeps(
    bars: list[SmcBar],
    swing_highs: list[SwingPoint],
    swing_lows: list[SwingPoint],
    max_reversal_bars: int,
) -> list[LiquiditySweep]:
    """镜像 detectLiquiditySweeps:识别流动性扫荡(假突破)。

    价格短暂越过 swing 点后反转、在数根柱内收回结构区间之内。
    """
    if len(bars) == 0 or (len(swing_highs) == 0 and len(swing_lows) == 0):
        return []
    if max_reversal_bars <= 0:
        max_reversal_bars = 3

    sweeps: list[LiquiditySweep] = []

    # swing high 扫荡(价格上冲后反转——看跌语境)
    for sh in swing_highs:
        for i in range(sh["index"] + 1, min(len(bars), sh["index"] + max_reversal_bars + 1)):
            if bars[i]["high"] > sh["price"] and bars[i]["close"] < sh["price"]:
                sweeps.append({"index": i, "level": sh["price"], "side": "BEAR", "reversed": True})
                break  # 每个 swing 点只记录第一次扫荡

    # swing low 扫荡(价格下探后反转——看涨语境)
    for sl in swing_lows:
        for i in range(sl["index"] + 1, min(len(bars), sl["index"] + max_reversal_bars + 1)):
            if bars[i]["low"] < sl["price"] and bars[i]["close"] > sl["price"]:
                sweeps.append({"index": i, "level": sl["price"], "side": "BULL", "reversed": True})
                break

    return sweeps


# ---------------------------------------------------------------- 订单块检测


def detect_order_blocks(
    bars: list[SmcBar], side: str, lookback: int, trend_direction: str
) -> list[OrderBlock]:
    """镜像 detectOrderBlocks:基于结构突破寻找订单块。

    BUY OB:向上突破(BOS UP / CHoCH UP)前最后一根看跌 K 线;
    SELL OB:向下突破前最后一根看涨 K 线。
    """
    if len(bars) == 0:
        return []

    bos_events = detect_structure_breaks(bars, lookback, trend_direction)
    if len(bos_events) == 0:
        return []

    seen: set[int] = set()
    blocks: list[OrderBlock] = []

    for i in range(len(bos_events) - 1, -1, -1):
        brk = bos_events[i]
        ob_index: int

        if side == "BUY" and brk["direction"] == "UP":
            # 看涨 OB:向上突破前的最后一根看跌 K 线
            ob_index = find_last_order_block_candle(bars, brk["index"], 0, False)
        elif side == "SELL" and brk["direction"] == "DOWN":
            # 看跌 OB:向下突破前的最后一根看涨 K 线
            ob_index = find_last_order_block_candle(bars, brk["index"], 0, True)
        else:
            continue

        if ob_index < 0 or ob_index in seen:
            continue
        seen.add(ob_index)

        block: OrderBlock = {
            "index": ob_index,
            "side": side,
            "high": bars[ob_index]["high"],
            "low": bars[ob_index]["low"],
            "valid": True,
            "mitigated": False,
            "ageBars": len(bars) - 1 - ob_index,
        }
        block = check_order_block_validity(block, bars)
        blocks.append(block)

    return blocks


def find_last_order_block_candle(bars: list[SmcBar], before_index: int, start: int, bullish: bool) -> int:
    """镜像 findLastOrderBlockCandle:从 beforeIndex 前向后找方向匹配的 K 线。

    优先找实体/振幅 > 30% 的强实体 K 线;找不到则回退接受任意方向性 K 线。
    """
    if before_index > len(bars):
        before_index = len(bars)
    if start < 0:
        start = 0

    # 第一遍:找强实体 K 线(实体/振幅 > 30%)
    for i in range(before_index - 1, start - 1, -1):
        bar_range = bars[i]["high"] - bars[i]["low"]
        if bar_range <= 0:
            continue
        body_size = abs(bars[i]["close"] - bars[i]["open"])
        if body_size <= bar_range * 0.30:
            continue
        if bullish and bars[i]["close"] > bars[i]["open"]:
            return i
        if not bullish and bars[i]["close"] < bars[i]["open"]:
            return i

    # 第二遍:接受任意方向性 K 线(回退)
    for i in range(before_index - 1, start - 1, -1):
        if bullish and bars[i]["close"] > bars[i]["open"]:
            return i
        if not bullish and bars[i]["close"] < bars[i]["open"]:
            return i

    return -1


def check_order_block_validity(ob: OrderBlock, bars: list[SmcBar]) -> OrderBlock:
    """镜像 checkOrderBlockValidity:根据后续价格行为更新 Valid/Mitigated。

    BUY OB:某根收盘价低于 OB.Low 则失效;SELL OB:某根收盘价高于 OB.High 则失效。
    """
    for i in range(ob["index"] + 1, len(bars)):
        if ob["side"] == "BUY":
            if bars[i]["close"] < ob["low"]:
                return {**ob, "valid": False, "mitigated": True}
        elif ob["side"] == "SELL":
            if bars[i]["close"] > ob["high"]:
                return {**ob, "valid": False, "mitigated": True}
    return ob


# ---------------------------------------------------------------- SMC 上下文构建


def build_smc_context(
    h4: list[SmcBar], h1: list[SmcBar], m30: list[SmcBar], m15: list[SmcBar] | None = None
) -> SMCContext:
    """镜像 buildSMCContext:由 H4/H1/M30/M15 构造多周期 SMC 上下文。"""
    if m15 is None:
        m15 = []
    ctx: SMCContext = {
        "h4OBs": [],
        "h1OBs": [],
        "h1ShortOBs": [],
        "m30OBs": [],
        "m15OBs": [],
        "h4FVGs": [],
        "h1FVGs": [],
        "m30FVGs": [],
        "m15FVGs": [],
        "h4Breaks": [],
        "h1Breaks": [],
        "m30Breaks": [],
        "m15Breaks": [],
        "h4Sweeps": [],
        "h1Sweeps": [],
        "m30Sweeps": [],
        "m15Sweeps": [],
        "h4TrendDirection": "NEUTRAL",
        "h1TrendDirection": "NEUTRAL",
        "m30TrendDirection": "NEUTRAL",
        "m15TrendDirection": "NEUTRAL",
    }

    if len(h4) >= 20:
        h4_found = find_swing_points(h4, 3, 3)
        h4_highs = h4_found["swingHighs"]
        h4_lows = h4_found["swingLows"]
        ctx["h4TrendDirection"] = determine_trend_direction(h4_highs, h4_lows)
        ctx["h4Breaks"] = detect_structure_breaks(h4, 50, ctx["h4TrendDirection"])
        ctx["h4OBs"] = [
            *detect_order_blocks(h4, "BUY", 50, ctx["h4TrendDirection"]),
            *detect_order_blocks(h4, "SELL", 50, ctx["h4TrendDirection"]),
        ]
        ctx["h4FVGs"] = detect_fvgs(h4, 50)
        ctx["h4Sweeps"] = detect_liquidity_sweeps(h4, h4_highs, h4_lows, 10)

    if len(h1) >= 20:
        h1_found = find_swing_points(h1, 3, 3)
        h1_highs = h1_found["swingHighs"]
        h1_lows = h1_found["swingLows"]
        ctx["h1TrendDirection"] = determine_trend_direction(h1_highs, h1_lows)
        ctx["h1Breaks"] = detect_structure_breaks(h1, 50, ctx["h1TrendDirection"])
        ctx["h1OBs"] = [
            *detect_order_blocks(h1, "BUY", 50, ctx["h1TrendDirection"]),
            *detect_order_blocks(h1, "SELL", 50, ctx["h1TrendDirection"]),
        ]
        ctx["h1FVGs"] = detect_fvgs(h1, 50)
        ctx["h1Sweeps"] = detect_liquidity_sweeps(h1, h1_highs, h1_lows, 10)

        # breakout_pyramid 使用的短回看订单块(lookback=20)
        ctx["h1ShortOBs"] = [
            *detect_order_blocks(h1, "BUY", 20, ctx["h1TrendDirection"]),
            *detect_order_blocks(h1, "SELL", 20, ctx["h1TrendDirection"]),
        ]

    if len(m30) >= 20:
        m30_found = find_swing_points(m30, 3, 3)
        m30_highs = m30_found["swingHighs"]
        m30_lows = m30_found["swingLows"]
        ctx["m30TrendDirection"] = determine_trend_direction(m30_highs, m30_lows)
        ctx["m30Breaks"] = detect_structure_breaks(m30, 50, ctx["m30TrendDirection"])
        ctx["m30OBs"] = [
            *detect_order_blocks(m30, "BUY", 50, ctx["m30TrendDirection"]),
            *detect_order_blocks(m30, "SELL", 50, ctx["m30TrendDirection"]),
        ]
        ctx["m30FVGs"] = detect_fvgs(m30, 50)
        ctx["m30Sweeps"] = detect_liquidity_sweeps(m30, m30_highs, m30_lows, 10)

    if len(m15) >= 20:
        m15_found = find_swing_points(m15, 3, 3)
        m15_highs = m15_found["swingHighs"]
        m15_lows = m15_found["swingLows"]
        ctx["m15TrendDirection"] = determine_trend_direction(m15_highs, m15_lows)
        ctx["m15Breaks"] = detect_structure_breaks(m15, 30, ctx["m15TrendDirection"])
        ctx["m15OBs"] = [
            *detect_order_blocks(m15, "BUY", 30, ctx["m15TrendDirection"]),
            *detect_order_blocks(m15, "SELL", 30, ctx["m15TrendDirection"]),
        ]
        ctx["m15FVGs"] = detect_fvgs(m15, 30)
        ctx["m15Sweeps"] = detect_liquidity_sweeps(m15, m15_highs, m15_lows, 10)

    return ctx


# ---------------------------------------------------------------- 辅助函数


def filter_obs_by_side(obs: list[OrderBlock], side: str) -> list[OrderBlock]:
    """镜像 filterOBsBySide:返回指定方向的订单块。"""
    return [ob for ob in obs if ob["side"] == side]


def unfilled_fvgs_near_price(fvgs: list[FVG], price: float, threshold: float) -> list[FVG]:
    """镜像 unfilledFVGsNearPrice:返回未填充且区间与 price ± threshold 重叠的 FVG。"""
    return [
        fvg
        for fvg in fvgs
        if not fvg["filled"] and fvg["upperBound"] >= price - threshold and fvg["lowerBound"] <= price + threshold
    ]


def valid_obs_near_price(obs: list[OrderBlock], price: float, threshold: float) -> list[OrderBlock]:
    """镜像 validOBsNearPrice:返回仍有效且区间与 price ± threshold 重叠的订单块。"""
    return [
        ob
        for ob in obs
        if ob["valid"] and ob["high"] >= price - threshold and ob["low"] <= price + threshold
    ]


def has_cho_ch_in_direction(breaks: list[StructureBreak], direction: str) -> bool:
    """镜像 hasCHOCHInDirection:检查是否存在指定反转方向的 CHoCH 事件。

    direction="BULL" → 找 Direction="UP" 的 CHoCH;direction="BEAR" → 找 Direction="DOWN"。
    """
    return any(
        brk["type"] == "CHoCH"
        and (
            (direction == "BULL" and brk["direction"] == "UP")
            or (direction == "BEAR" and brk["direction"] == "DOWN")
        )
        for brk in breaks
    )


def recent_sweep_in_direction(
    sweeps: list[LiquiditySweep], direction: str, last_bar_index: int, max_bars_ago: int
) -> bool:
    """镜像 recentSweepInDirection:检查近期是否有确认指定方向的流动性扫荡。

    direction="BULL" → 扫掉低点后反转向上;direction="BEAR" → 扫掉高点后反转向下。
    max_bars_ago:只考虑距数据末端该柱数以内的扫荡。
    """
    return any(
        sweep["reversed"]
        and sweep["side"] == direction
        and (max_bars_ago <= 0 or last_bar_index - sweep["index"] <= max_bars_ago)
        for sweep in sweeps
    )
