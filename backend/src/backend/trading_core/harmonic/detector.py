"""谐波形态检测器(镜像 packages/trading-core/src/harmonic/detector.ts)。

按 TS oracle 语义 1:1 移植:Gartley/Bat/Butterfly/Crab/Deep Crab/ABCD
形态检测、5 摆动窗口与 4 摆动回溯推断 C 点、PRZ/SL/TP 构建、评分,
以及多周期谐波上下文构建。
"""

from __future__ import annotations

import math
from fractions import Fraction
from typing import Any

# ---------------------------------------------------------------- 类型别名

HarmonicBar = dict[str, Any]
"""镜像 HarmonicBar:high/low/close/open。"""

HarmonicPattern = dict[str, Any]
"""镜像 HarmonicPattern:type/direction/timeframe/status + XABCD 点位、比率、PRZ、SL/TP、评分。"""

HarmonicContext = dict[str, Any]
"""镜像 HarmonicContext:h4/h1/m30 形态 + activePattern/directionBias/score/summary。"""

SwingPoint = dict[str, Any]
"""镜像 TS 内部类型 SwingPoint:{index, price, kind:'high'|'low'}。"""

RatioTarget = dict[str, Any]
"""镜像 TS 内部类型 RatioTarget:{value, tolerance}。"""

PatternSpec = dict[str, Any]
"""镜像 TS 内部类型 PatternSpec:{patternType, abTargets, xdTargets, cdTargets, abcdTargets}。"""

PatternCandidate = dict[str, Any]
"""镜像 TS 内部类型 PatternCandidate:候选形态的中间状态。"""

# ---------------------------------------------------------------- 常量

DIRECTION_BULLISH = "bullish"
DIRECTION_BEARISH = "bearish"
STATUS_COMPLETED = "completed"
STATUS_INVALID = "invalidated"
STATUS_NEUTRAL = "neutral"

PATTERN_GARTLEY = "gartley"
PATTERN_BAT = "bat"
PATTERN_BUTTERFLY = "butterfly"
PATTERN_CRAB = "crab"
PATTERN_ABCD = "abcd"
PATTERN_DEEP_CRAB = "deep_crab"

_TOLERANCE_BY_RATIO: dict[float, float] = {
    0.382: 0.04,
    0.500: 0.05,
    0.618: 0.05,
    0.786: 0.04,
    0.886: 0.04,
    1.000: 0.06,
    1.128: 0.05,
    1.272: 0.08,
    1.618: 0.10,
    2.240: 0.12,
    2.618: 0.15,
    3.140: 0.15,
    3.618: 0.18,
}


def target(value: float) -> RatioTarget:
    """镜像 target:构造比率目标,容差查表(缺失默认 0.05)。"""
    return {"value": value, "tolerance": _TOLERANCE_BY_RATIO.get(value, 0.05)}


_PATTERN_SPECS: list[PatternSpec] = [
    {
        "patternType": PATTERN_GARTLEY,
        "abTargets": [target(0.618)],
        "xdTargets": [target(0.786)],
        "cdTargets": [target(1.272), target(1.618)],
        "abcdTargets": [target(1.0)],
    },
    {
        "patternType": PATTERN_BAT,
        "abTargets": [target(0.382), target(0.500)],
        "xdTargets": [target(0.886)],
        "cdTargets": [target(1.618), target(2.618)],
        "abcdTargets": [target(1.0)],
    },
    {
        "patternType": PATTERN_BUTTERFLY,
        "abTargets": [target(0.786)],
        "xdTargets": [target(1.272), target(1.618)],
        "cdTargets": [target(1.618), target(2.618)],
        "abcdTargets": [target(1.0)],
    },
    {
        "patternType": PATTERN_CRAB,
        "abTargets": [target(0.382), target(0.618)],
        "xdTargets": [target(1.618)],
        "cdTargets": [target(2.618), target(3.140), target(3.618)],
        "abcdTargets": [target(1.0)],
    },
    {
        "patternType": PATTERN_DEEP_CRAB,
        "abTargets": [target(0.886)],
        "xdTargets": [target(1.128)],
        "cdTargets": [target(2.240), target(2.618), target(3.618)],
        "abcdTargets": [target(1.0)],
    },
    {
        "patternType": PATTERN_ABCD,
        "abTargets": [],
        "xdTargets": [],
        "cdTargets": [],
        "abcdTargets": [target(1.0)],
    },
]


# ---------------------------------------------------------------- 主检测


def detect_patterns(bars: list[HarmonicBar], timeframe: str) -> list[HarmonicPattern]:
    """镜像 detectPatterns:检测谐波形态并排序(DIndex 降序 → score 降序)。"""
    patterns: list[HarmonicPattern] = []
    swings = extract_swings(bars)
    if len(swings) < 4:
        return patterns

    start = len(swings) - 12
    if start < 0:
        start = 0

    # 5 摆动窗口:X, A, B, C, D
    for i in range(start, len(swings) - 4):
        x = swings[i]
        a = swings[i + 1]
        b = swings[i + 2]
        c = swings[i + 3]
        d = swings[i + 4]
        direction, ok = xabcd_direction(x, a, b, c, d)
        if not ok:
            continue

        for spec in _PATTERN_SPECS:
            candidate, valid = validate_candidate(spec, x, a, b, c, d, direction)
            if not valid:
                continue
            patterns.append(build_pattern(candidate, timeframe))

    # 4 摆动窗口:X, A, B, D——由比率反推 C
    for i in range(start, len(swings) - 3):
        x = swings[i]
        a = swings[i + 1]
        b = swings[i + 2]
        d = swings[i + 3]

        xab_ok_standard = (x["price"] > a["price"] and b["price"] > a["price"] and b["price"] < x["price"]) or (
            x["price"] < a["price"] and b["price"] < a["price"] and b["price"] > x["price"]
        )
        xab_ok_extension = (x["price"] >= a["price"] and b["price"] >= a["price"] and b["price"] <= x["price"]) or (
            x["price"] <= a["price"] and b["price"] <= a["price"] and b["price"] >= x["price"]
        )
        if not xab_ok_standard and not xab_ok_extension:
            continue

        direction = ""
        if x["price"] > a["price"] and d["price"] < b["price"]:
            direction = DIRECTION_BULLISH
        elif x["price"] < a["price"] and d["price"] > b["price"]:
            direction = DIRECTION_BEARISH
        if not direction:
            continue

        for spec in _PATTERN_SPECS:
            # 策略 1:CD/BC 反推
            for t in spec["cdTargets"]:
                cd_target_ratio = t["value"]
                c_price = (d["price"] + cd_target_ratio * b["price"]) / (1 + cd_target_ratio)

                if direction == DIRECTION_BULLISH:
                    if c_price >= b["price"] or c_price <= d["price"]:
                        continue
                else:
                    if c_price <= b["price"] or c_price >= d["price"]:
                        continue

                c = {
                    "index": b["index"] + 1,
                    "price": c_price,
                    "kind": "high" if direction == DIRECTION_BEARISH else "low",
                }

                candidate, ok = validate_candidate(spec, x, a, b, c, d, direction)
                if not ok:
                    continue

                dup = any(
                    p["dIndex"] == d["index"] and p["type"] == spec["patternType"] and p["direction"] == direction
                    for p in patterns
                )
                if dup:
                    continue
                patterns.append(build_pattern(candidate, timeframe))

            # 策略 2:CD/AB 反推
            for t in spec["abcdTargets"]:
                cd_ab_ratio = t["value"]
                ab = abs(b["price"] - a["price"])
                if direction == DIRECTION_BULLISH:
                    c_price = d["price"] + cd_ab_ratio * ab
                    if c_price >= b["price"] or c_price <= d["price"]:
                        continue
                else:
                    c_price = d["price"] - cd_ab_ratio * ab
                    if c_price <= b["price"] or c_price >= d["price"]:
                        continue

                c = {
                    "index": b["index"] + 1,
                    "price": c_price,
                    "kind": "high" if direction == DIRECTION_BEARISH else "low",
                }

                candidate, ok = validate_candidate(spec, x, a, b, c, d, direction)
                if not ok:
                    continue

                dup = any(
                    p["dIndex"] == d["index"] and p["type"] == spec["patternType"] and p["direction"] == direction
                    for p in patterns
                )
                if dup:
                    continue
                patterns.append(build_pattern(candidate, timeframe))

    # 按 DIndex 降序,再按 score 降序
    patterns.sort(key=lambda p: (-p["dIndex"], -p["score"]))

    return patterns


# ---------------------------------------------------------------- 上下文构建


def build_context(h4: list[HarmonicBar], h1: list[HarmonicBar], m30: list[HarmonicBar]) -> HarmonicContext:
    """镜像 buildContext:构建多周期谐波上下文,选出最高分活跃形态。"""
    context: HarmonicContext = {
        "h4Patterns": detect_patterns(h4, "H4"),
        "h1Patterns": detect_patterns(h1, "H1"),
        "m30Patterns": detect_patterns(m30, "M30"),
        "activePattern": None,
        "directionBias": STATUS_NEUTRAL,
        "score": 0,
        "summary": "No completed harmonic pattern detected.",
    }

    all_patterns = context["h4Patterns"] + context["h1Patterns"] + context["m30Patterns"]

    for pattern in all_patterns:
        if pattern["invalidated"] or pattern["status"] == STATUS_INVALID:
            continue
        if context["activePattern"] is None or pattern["score"] > context["activePattern"]["score"]:
            context["activePattern"] = pattern

    if context["activePattern"] is not None:
        active = context["activePattern"]
        context["directionBias"] = active["direction"]
        context["score"] = active["score"]
        context["summary"] = (
            f"{active['timeframe']} {active['direction']} {active['type']} completed "
            f"score={active['score']} PRZ={_to_fixed(active['przLow'], 2)}-{_to_fixed(active['przHigh'], 2)}"
        )

    return context


# ---------------------------------------------------------------- 内部函数


def extract_swings(bars: list[HarmonicBar]) -> list[SwingPoint]:
    """镜像 extractSwings:基于 (high+low)/2 的价格序列提取摆动点。"""
    swings: list[SwingPoint] = []
    if len(bars) < 2:
        return swings

    prices: list[float] = [(b["high"] + b["low"]) / 2 for b in bars]

    direction = 0  # 1=up, -1=down
    for i in range(1, len(prices)):
        if prices[i] > prices[i - 1]:
            direction = 1
            break
        elif prices[i] < prices[i - 1]:
            direction = -1
            break
    if direction == 0:
        return swings

    extremum_idx = 0
    extremum_price = prices[0]

    for i in range(1, len(prices)):
        if direction == 1:
            if prices[i] > extremum_price:
                extremum_price = prices[i]
                extremum_idx = i
            if prices[i] < prices[i - 1]:
                swings.append({"index": extremum_idx, "price": bars[extremum_idx]["high"], "kind": "high"})
                direction = -1
                extremum_price = prices[i]
                extremum_idx = i
        else:
            if prices[i] < extremum_price:
                extremum_price = prices[i]
                extremum_idx = i
            if prices[i] > prices[i - 1]:
                swings.append({"index": extremum_idx, "price": bars[extremum_idx]["low"], "kind": "low"})
                direction = 1
                extremum_price = prices[i]
                extremum_idx = i

    # 最后的待定极值
    if direction == 1:
        swings.append({"index": extremum_idx, "price": bars[extremum_idx]["high"], "kind": "high"})
    else:
        swings.append({"index": extremum_idx, "price": bars[extremum_idx]["low"], "kind": "low"})

    # 只保留最近 20 个摆动点
    if len(swings) > 20:
        return swings[-20:]
    return swings


def xabcd_direction(x: SwingPoint, a: SwingPoint, b: SwingPoint, c: SwingPoint, d: SwingPoint) -> tuple[str, bool]:
    """镜像 xabcdDirection:判定 XABCD 五点的形态方向。"""
    # 标准谐波形态
    if (
        x["price"] > a["price"]
        and b["price"] > a["price"]
        and b["price"] < x["price"]
        and c["price"] < b["price"]
        and d["price"] < b["price"]
    ):
        return DIRECTION_BULLISH, True
    if (
        x["price"] < a["price"]
        and b["price"] < a["price"]
        and b["price"] > x["price"]
        and c["price"] > b["price"]
        and d["price"] > b["price"]
    ):
        return DIRECTION_BEARISH, True

    # ABCD / 扩展形态
    if (
        x["price"] >= a["price"]
        and b["price"] >= a["price"]
        and c["price"] < b["price"]
        and d["price"] < c["price"]
    ):
        return DIRECTION_BULLISH, True
    if (
        x["price"] <= a["price"]
        and b["price"] <= a["price"]
        and c["price"] > b["price"]
        and d["price"] > c["price"]
    ):
        return DIRECTION_BEARISH, True

    return "", False


def validate_candidate(
    spec: PatternSpec,
    x: SwingPoint,
    a: SwingPoint,
    b: SwingPoint,
    c: SwingPoint,
    d: SwingPoint,
    direction: str,
) -> tuple[PatternCandidate, bool]:
    """镜像 validateCandidate:校验比率目标、PRZ 投影与 D 点是否落入 PRZ。"""
    xa = abs(a["price"] - x["price"])
    ab = abs(b["price"] - a["price"])
    bc = abs(c["price"] - b["price"])
    cd = abs(d["price"] - c["price"])
    if xa == 0 or ab == 0 or bc == 0 or cd == 0:
        return {}, False

    candidate: PatternCandidate = {
        "spec": spec,
        "x": x,
        "a": a,
        "b": b,
        "c": c,
        "d": d,
        "direction": direction,
        "abRatio": ab / xa,
        "bcRatio": bc / ab,
        "cdRatio": cd / bc,
        "xdRatio": abs(d["price"] - x["price"]) / xa,
        "ratioQuality": 0,
        "przTargets": [],
        "expectedDLow": 0,
        "expectedDHigh": 0,
    }

    qualities: list[float] = []

    if len(spec["abTargets"]) > 0:
        quality, ok = best_ratio_quality(candidate["abRatio"], spec["abTargets"])
        if not ok:
            return candidate, False
        qualities.append(quality)
    if len(spec["xdTargets"]) > 0:
        quality, ok = best_ratio_quality(candidate["xdRatio"], spec["xdTargets"])
        if not ok:
            return candidate, False
        qualities.append(quality)
    if len(spec["cdTargets"]) > 0:
        quality, ok = best_ratio_quality(candidate["cdRatio"], spec["cdTargets"])
        if not ok:
            return candidate, False
        qualities.append(quality)
    if len(spec["abcdTargets"]) > 0:
        abcd_ratio = cd / ab
        quality, ok = best_ratio_quality(abcd_ratio, spec["abcdTargets"])
        if spec["patternType"] == PATTERN_ABCD and not ok:
            return candidate, False
        if ok:
            qualities.append(quality)

    if len(qualities) == 0:
        return candidate, False
    candidate["ratioQuality"] = average(qualities)
    candidate["przTargets"] = projected_d_targets(candidate)
    if len(candidate["przTargets"]) == 0:
        return candidate, False

    low, high = min_max(candidate["przTargets"])
    candidate["expectedDLow"] = low
    candidate["expectedDHigh"] = high
    if not price_in_range(candidate["d"]["price"], candidate["expectedDLow"], candidate["expectedDHigh"]):
        return candidate, False

    return candidate, True


def build_pattern(candidate: PatternCandidate, timeframe: str) -> HarmonicPattern:
    """镜像 buildPattern:由候选构建最终 HarmonicPattern。"""
    prz_low, prz_high = build_prz(candidate)
    invalidated = is_invalidated(candidate, prz_low, prz_high)
    status = STATUS_INVALID if invalidated else STATUS_COMPLETED
    score = score_candidate(candidate, timeframe, prz_low, prz_high, invalidated)

    stop_loss, target1, target2 = trade_levels(candidate, prz_low, prz_high)

    return {
        "type": candidate["spec"]["patternType"],
        "direction": candidate["direction"],
        "timeframe": timeframe,
        "status": status,
        "xIndex": candidate["x"]["index"],
        "aIndex": candidate["a"]["index"],
        "bIndex": candidate["b"]["index"],
        "cIndex": candidate["c"]["index"],
        "dIndex": candidate["d"]["index"],
        "xPrice": _round2(candidate["x"]["price"]),
        "aPrice": _round2(candidate["a"]["price"]),
        "bPrice": _round2(candidate["b"]["price"]),
        "cPrice": _round2(candidate["c"]["price"]),
        "dPrice": _round2(candidate["d"]["price"]),
        "abRatio": _round3(candidate["abRatio"]),
        "bcRatio": _round3(candidate["bcRatio"]),
        "cdRatio": _round3(candidate["cdRatio"]),
        "xdRatio": _round3(candidate["xdRatio"]),
        "przLow": _round2(prz_low),
        "przHigh": _round2(prz_high),
        "stopLoss": stop_loss,
        "target1": target1,
        "target2": target2,
        "invalidated": invalidated,
        "score": score,
        "confidence": _round3(score / 100),
        "reason": (
            f"AB/XA={_to_fixed(candidate['abRatio'], 3)}, BC/AB={_to_fixed(candidate['bcRatio'], 3)}, "
            f"CD/BC={_to_fixed(candidate['cdRatio'], 3)}, XD/XA={_to_fixed(candidate['xdRatio'], 3)}"
        ),
    }


def build_prz(candidate: PatternCandidate) -> tuple[float, float]:
    """镜像 buildPRZ:由 PRZ 目标与 D 点价格构建 PRZ 区间(带宽受限)。"""
    targets = list(candidate["przTargets"]) + [candidate["d"]["price"]]
    low, high = min_max(targets)

    price = abs(candidate["d"]["price"])
    max_width = max(abs(candidate["a"]["price"] - candidate["x"]["price"]) * 0.20, price * 0.0015)
    if max_width <= 0:
        return low, high

    mid = (low + high) / 2
    if high - low > max_width:
        low = mid - max_width / 2
        high = mid + max_width / 2
        if candidate["d"]["price"] < low:
            low = candidate["d"]["price"]
        if candidate["d"]["price"] > high:
            high = candidate["d"]["price"]
    return low, high


def trade_levels(candidate: PatternCandidate, prz_low: float, prz_high: float) -> tuple[float, float, float]:
    """镜像 tradeLevels:由 D 点与 XA 幅度计算 SL/T1/T2。"""
    range_size = abs(candidate["a"]["price"] - candidate["d"]["price"])
    if range_size == 0:
        range_size = abs(candidate["x"]["price"] - candidate["a"]["price"]) * 0.5

    if candidate["direction"] == DIRECTION_BULLISH:
        stop_loss = _round2(prz_low - abs(candidate["x"]["price"] - candidate["a"]["price"]) * 0.10)
        target1 = _round2(candidate["d"]["price"] + range_size * 0.382)
        target2 = _round2(candidate["d"]["price"] + range_size * 0.618)
        return stop_loss, target1, target2

    stop_loss = _round2(prz_high + abs(candidate["x"]["price"] - candidate["a"]["price"]) * 0.10)
    target1 = _round2(candidate["d"]["price"] - range_size * 0.382)
    target2 = _round2(candidate["d"]["price"] - range_size * 0.618)
    return stop_loss, target1, target2


def is_invalidated(candidate: PatternCandidate, prz_low: float, prz_high: float) -> bool:
    """镜像 isInvalidated:D 点超出 PRZ + buffer 则形态失效。"""
    buffer = abs(candidate["a"]["price"] - candidate["x"]["price"]) * 0.10
    if candidate["direction"] == DIRECTION_BULLISH:
        return candidate["d"]["price"] < prz_low - buffer
    return candidate["d"]["price"] > prz_high + buffer


def score_candidate(
    candidate: PatternCandidate, timeframe: str, prz_low: float, prz_high: float, invalidated: bool
) -> int:
    """镜像 scoreCandidate:比率质量 + PRZ 质量 + 完成 + 周期分,失效扣 30 并钳制到 [0,100]。"""
    ratio_score = candidate["ratioQuality"] * 40

    width = abs(prz_high - prz_low)
    xa = abs(candidate["a"]["price"] - candidate["x"]["price"])
    prz_score = 20.0
    if xa > 0:
        prz_score = clamp_float(20 - (width / xa) * 40, 0, 20)

    completion_score = 15
    timeframe_scores: dict[str, int] = {"H4": 8, "H1": 8, "M30": 10}
    timeframe_score = timeframe_scores.get(timeframe, 5)

    score = _math_round(ratio_score + prz_score + completion_score + timeframe_score)
    if invalidated:
        score -= 30
    return clamp_int(score, 0, 100)


def projected_d_targets(candidate: PatternCandidate) -> list[float]:
    """镜像 projectedDTargets:由 XD/CD/ABCD 各比率投影 D 点目标。"""
    targets: list[float] = []
    xa = abs(candidate["a"]["price"] - candidate["x"]["price"])
    bc = abs(candidate["c"]["price"] - candidate["b"]["price"])
    ab = abs(candidate["b"]["price"] - candidate["a"]["price"])

    for ratio in candidate["spec"]["xdTargets"]:
        if candidate["direction"] == DIRECTION_BULLISH:
            targets.append(candidate["x"]["price"] - xa * ratio["value"])
        else:
            targets.append(candidate["x"]["price"] + xa * ratio["value"])
    for ratio in candidate["spec"]["cdTargets"]:
        if candidate["direction"] == DIRECTION_BULLISH:
            targets.append(candidate["c"]["price"] - bc * ratio["value"])
        else:
            targets.append(candidate["c"]["price"] + bc * ratio["value"])
    for ratio in candidate["spec"]["abcdTargets"]:
        if candidate["direction"] == DIRECTION_BULLISH:
            targets.append(candidate["c"]["price"] - ab * ratio["value"])
        else:
            targets.append(candidate["c"]["price"] + ab * ratio["value"])
    return targets


def best_ratio_quality(value: float, targets: list[RatioTarget]) -> tuple[float, bool]:
    """镜像 bestRatioQuality:对目标比率取最佳质量分(容差内线性衰减)。"""
    best = 0.0
    for t in targets:
        delta = abs(value - t["value"])
        if delta > t["tolerance"]:
            continue
        quality = 1 - delta / t["tolerance"]
        if quality > best:
            best = quality
    return best, best > 0


def min_max(values: list[float]) -> tuple[float, float]:
    """镜像 minMax:返回 [最低, 最高]。"""
    low = values[0]
    high = values[0]
    for v in values[1:]:
        if v < low:
            low = v
        if v > high:
            high = v
    return low, high


def price_in_range(price: float, low: float, high: float) -> bool:
    """镜像 priceInRange:low/high 无序时先交换,再判断闭区间包含。"""
    if low > high:
        low, high = high, low
    return low <= price <= high


def average(values: list[float]) -> float:
    """镜像 average:算术平均。"""
    return sum(values) / len(values)


def clamp_float(value: float, low: float, high: float) -> float:
    """镜像 clampFloat:Math.max(low, Math.min(high, value))。"""
    return max(low, min(high, value))


def clamp_int(value: int, low: int, high: int) -> int:
    """镜像 clampInt:Math.max(low, Math.min(high, value))。"""
    return max(low, min(high, value))


# ---------------------------------------------------------------- 数值助手


def _math_round(value: float) -> int:
    """镜像 JS Math.round:等价于 floor(x + 0.5)(半值朝 +∞ 取整)。"""
    return math.floor(value + 0.5)


def _round2(value: float) -> float:
    """镜像 TS round:Math.round(value * 100) / 100。"""
    return _math_round(value * 100) / 100


def _round3(value: float) -> float:
    """镜像 TS roundRatio:Math.round(value * 1000) / 1000。"""
    return _math_round(value * 1000) / 1000


def _to_fixed(value: float, digits: int) -> str:
    """镜像 JS Number.prototype.toFixed:tie 时取更大的 n(朝 +∞)。

    基于二进制浮点值的精确有理展开(Fraction),与 JS 在精确 double 上取整的
    语义一致,而非 Python 的 round-half-even 字符串格式化。
    """
    if math.isnan(value):
        return "NaN"
    if math.isinf(value):
        return "Infinity" if value > 0 else "-Infinity"
    scaled = Fraction(value) * (10**digits)
    q, r = divmod(scaled.numerator, scaled.denominator)
    n = q + (1 if r * 2 >= scaled.denominator else 0)
    sign = "-" if n < 0 else ""
    digit_str = str(abs(n)).zfill(digits + 1)
    if digits == 0:
        return f"{sign}{digit_str}"
    return f"{sign}{digit_str[:-digits]}.{digit_str[-digits:]}"
