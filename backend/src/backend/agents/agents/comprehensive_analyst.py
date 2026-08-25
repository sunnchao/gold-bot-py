"""Comprehensive Analyst Agent(1:1 镜像 gold-bot apps/app-agent/src/agents/comprehensive-analyst.ts)。

最大分析器:协调 TECHNICAL/WAVE/CHANLUN/RISK/ARBITRATION 五节:
- StructureCache:按 timeframe 分层(慢->快)的稳定结构缓存(hash 驱动的 cacheable 前缀)
- build_common_system_prompt / build_symbol_system_prompt / 静态/实时分层 prompt
- parse_markdown_response(Markdown 优先)+ JSON 回退 + Phase 4.2 强制 tool_use 结构化重试
- normalize_comprehensive:LLM 常见 enum 混淆归一化
- 程序化谐波注入(替代 LLM 自我判断)
- 动态价格窗 + 跨品种碰撞检测 + 交易业务校验
- decide_trade_action(legacy 工具)与 decide_account_actions(账户感知 + rebuild)

依赖说明:tools/elliott-wave.ts、tools/chanlun-core.ts 由最小确定性本地实现支撑
(_support.analyze_elliott_wave / analyze_chanlun),待后续 M7 worker 移植后对齐。
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from dataclasses import dataclass
from typing import Any, TypeGuard

from backend.agents.agents._support import (
    DEFAULT_MAX_LOTS,
    DEFAULT_MIN_LOTS,
    LlmClient,
    SystemBlock,
    UserLayer,
    analyze_chanlun,
    analyze_elliott_wave,
    atr_of,
    build_unavailable_chanlun_structure,
    build_unavailable_wave_structure,
    detect_cross_instrument_price,
    detect_format,
    extract_fields,
    extract_list_items,
    extract_warnings,
    get_boolean_field,
    get_enum_field,
    get_logger,
    get_number_field,
    get_string_field,
    get_symbol_profile,
    parse_sr_levels,
    safe_parse_response,
    select_indicator,
    split_sections,
    stable_stringify,
    validate_arbitration_business,
    validate_comprehensive_data,
    validate_trade_recommendation_business,
)
from backend.agents.agents.account_action_guard import is_symbol_loaded, validate_trade_action_for_account
from backend.agents.agents.trade_action_converter import (
    TRADE_ACTION_TOOLS,
    TRADE_ACTION_TOOLS_LEGACY,
    tool_use_to_trade_action,
    tool_use_to_trade_action_legacy,
)

__all__ = [
    "ComprehensiveAnalystService",
    "StructureCache",
    "build_common_system_prompt",
    "build_fallback",
    "build_harmonic_from_context",
    "build_realtime_data_prompt",
    "build_symbol_system_prompt",
    "current_price_from_payload",
    "execution_price_from_payload",
    "normalize_comprehensive",
    "parse_markdown_response",
    "to_market_insight",
]

JSONDict = dict[str, Any]
PriceLike = float | None
PREFERRED_BAR_TIMEFRAMES = ("H1", "M30", "M15", "H4")


# ─── 基础提取工具(镜像 TS 模块级工具函数) ──────────────────────────────────────


def to_finite_number(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value) if float(value) == float(value) else None
    return None


def _is_record(value: object) -> TypeGuard[JSONDict]:
    return isinstance(value, dict)


def average_bid_ask(entry: JSONDict) -> float | None:
    bid = to_finite_number(entry.get("bid"))
    ask = to_finite_number(entry.get("ask"))
    if bid is not None and ask is not None:
        return (bid + ask) / 2
    return bid if bid is not None else ask


def get_payload_bars(payload: JSONDict, timeframe: str) -> list[JSONDict]:
    bars = payload.get("bars")
    if not isinstance(bars, dict):
        return []
    exact = bars.get(timeframe)
    if isinstance(exact, list):
        return exact
    matching_key = next((key for key in bars.keys() if str(key).upper() == timeframe), None)
    matched = bars.get(matching_key) if matching_key else None
    return matched if isinstance(matched, list) else []


def extract_preferred_bar_closes(
    payload: JSONDict,
    min_count: int,
    closed_only: bool = False,
) -> list[float]:
    for timeframe in PREFERRED_BAR_TIMEFRAMES:
        bars = get_payload_bars(payload, timeframe)
        source_bars = bars[:-1] if closed_only else bars
        closes = [
            value
            for value in (to_finite_number(bar.get("close")) for bar in source_bars)
            if value is not None
        ]
        if len(closes) >= min_count:
            return closes
    return []


def extract_preferred_chanlun_bars(
    payload: JSONDict,
    min_count: int,
    closed_only: bool = False,
) -> list[JSONDict]:
    for timeframe in PREFERRED_BAR_TIMEFRAMES:
        source_bars = (
            get_payload_bars(payload, timeframe)[:-1]
            if closed_only
            else get_payload_bars(payload, timeframe)
        )
        bars: list[JSONDict] = []
        for index, bar in enumerate(source_bars):
            open_ = to_finite_number(bar.get("open"))
            high = to_finite_number(bar.get("high"))
            low = to_finite_number(bar.get("low"))
            close = to_finite_number(bar.get("close"))
            if open_ is None or high is None or low is None or close is None:
                continue
            bars.append({"index": index, "open": open_, "high": high, "low": low, "close": close})
        if len(bars) >= min_count:
            return bars
    return []


def extract_runtime_candles(payload: JSONDict) -> list[JSONDict]:
    direct = payload.get("candles")
    if isinstance(direct, list):
        return [item for item in direct if _is_record(item)]
    market_data = payload.get("market_data")
    nested = market_data.get("candles") if isinstance(market_data, dict) else None
    if isinstance(nested, list):
        return [item for item in nested if _is_record(item)]
    return []


def extract_runtime_prices(payload: JSONDict) -> list[float]:
    raw_prices: Any = payload.get("prices")
    market_data = payload.get("market_data")
    if not isinstance(raw_prices, list) and isinstance(market_data, dict):
        raw_prices = market_data.get("prices")
    if not isinstance(raw_prices, list):
        return []

    prices: list[float] = []
    for entry in raw_prices:
        if isinstance(entry, (int, float)) and not isinstance(entry, bool):
            prices.append(float(entry))
            continue
        if not _is_record(entry):
            continue
        extracted = (
            to_finite_number(entry.get("close"))
            or to_finite_number(entry.get("price"))
            or average_bid_ask(entry)
        )
        if extracted is not None:
            prices.append(extracted)
    return prices


def extract_wave_prices(payload: JSONDict) -> list[float]:
    payload_bar_prices = extract_preferred_bar_closes(payload, 2)
    if payload_bar_prices:
        return payload_bar_prices
    candle_prices = [
        value
        for value in (to_finite_number(c.get("close")) for c in extract_runtime_candles(payload))
        if value is not None
    ]
    if candle_prices:
        return candle_prices
    prices = extract_runtime_prices(payload)
    if prices:
        return prices
    market = payload.get("market") or {}
    fallback_price = market.get("bid") or market.get("ask")
    return [fallback_price] if isinstance(fallback_price, (int, float)) and not isinstance(fallback_price, bool) else []


def extract_wave_closed_bar_prices(payload: JSONDict) -> list[float]:
    return extract_preferred_bar_closes(payload, 2, True)


def extract_chanlun_bars(payload: JSONDict) -> list[JSONDict]:
    payload_bars = extract_preferred_chanlun_bars(payload, 3)
    if payload_bars:
        return payload_bars
    bars: list[JSONDict] = []
    for index, candle in enumerate(extract_runtime_candles(payload)):
        open_ = to_finite_number(candle.get("open"))
        high = to_finite_number(candle.get("high"))
        low = to_finite_number(candle.get("low"))
        close = to_finite_number(candle.get("close"))
        if open_ is None or high is None or low is None or close is None:
            continue
        bars.append({"index": index, "open": open_, "high": high, "low": low, "close": close})
    return bars


def extract_closed_chanlun_bars(payload: JSONDict) -> list[JSONDict]:
    return extract_preferred_chanlun_bars(payload, 3, True)


def summarize_candlestick_patterns(payload: JSONDict) -> dict[str, list[str]]:
    summary: dict[str, list[str]] = {}
    for timeframe in PREFERRED_BAR_TIMEFRAMES:
        recent = get_payload_bars(payload, timeframe)[:-1][-20:]
        patterns: list[str] = []
        for bar in recent:
            for pattern in bar.get("candlestick_patterns") or []:
                if isinstance(pattern, str) and pattern:
                    patterns.append(pattern)
        if recent and patterns:
            summary[timeframe] = list(dict.fromkeys(patterns))
    return summary


def stable_hash(parts: list[Any]) -> str:
    hash_obj = hashlib.sha256()
    for part in parts:
        hash_obj.update(stable_stringify(part).encode("utf-8"))
        hash_obj.update(b"\n")
    return hash_obj.hexdigest()


# ─── 谐波上下文清理(HARMONIC_CTX 分层) ────────────────────────────────────────

HARMONIC_VOLATILE_KEYS = {"score", "completion_pct", "reason"}


def sanitize_harmonic_pattern_stable(pattern: Any) -> JSONDict | None:
    if not _is_record(pattern):
        return None
    stable_keys = [
        "type",
        "direction",
        "timeframe",
        "x_price",
        "a_price",
        "b_price",
        "c_price",
        "d_price",
        "ab_ratio",
        "bc_ratio",
        "cd_ratio",
        "xd_ratio",
        "prz_low",
        "prz_high",
        "stop_loss",
        "target_1",
        "target_2",
        "confidence",
        "invalidated",
        "status",
    ]
    sanitized: JSONDict = {}
    for key in stable_keys:
        if pattern.get(key) is not None:
            sanitized[key] = pattern[key]
    return sanitized if len(sanitized) > 0 else None


def sanitize_harmonic_pattern_volatile(pattern: Any) -> JSONDict | None:
    if not _is_record(pattern):
        return None
    sanitized: JSONDict = {}
    for key in HARMONIC_VOLATILE_KEYS:
        if pattern.get(key) is not None:
            sanitized[key] = pattern[key]
    return sanitized if len(sanitized) > 0 else None


def sanitize_harmonic_context_stable(payload: JSONDict) -> JSONDict | None:
    harmonic_context = payload.get("harmonic_context")
    if not _is_record(harmonic_context):
        return None
    h4_patterns = harmonic_context.get("h4_patterns") or []
    h1_patterns = harmonic_context.get("h1_patterns") or []
    m30_patterns = harmonic_context.get("m30_patterns") or []
    return {
        "h4_patterns": [
            p for p in (sanitize_harmonic_pattern_stable(item) for item in h4_patterns) if p
        ],
        "h1_patterns": [
            p for p in (sanitize_harmonic_pattern_stable(item) for item in h1_patterns) if p
        ],
        "m30_patterns": [
            p for p in (sanitize_harmonic_pattern_stable(item) for item in m30_patterns) if p
        ],
        "active_pattern": sanitize_harmonic_pattern_stable(harmonic_context.get("active_pattern")) or None,
        "direction_bias": harmonic_context.get("direction_bias"),
    }


def sanitize_harmonic_context_volatile(payload: JSONDict) -> JSONDict | None:
    harmonic_context = payload.get("harmonic_context")
    if not _is_record(harmonic_context):
        return None
    h4_patterns = harmonic_context.get("h4_patterns") or []
    h1_patterns = harmonic_context.get("h1_patterns") or []
    m30_patterns = harmonic_context.get("m30_patterns") or []
    return {
        "h4_patterns": [
            p for p in (sanitize_harmonic_pattern_volatile(item) for item in h4_patterns) if p
        ],
        "h1_patterns": [
            p for p in (sanitize_harmonic_pattern_volatile(item) for item in h1_patterns) if p
        ],
        "m30_patterns": [
            p for p in (sanitize_harmonic_pattern_volatile(item) for item in m30_patterns) if p
        ],
        "active_pattern": sanitize_harmonic_pattern_volatile(harmonic_context.get("active_pattern")) or None,
        "score": harmonic_context.get("score"),
        "summary": harmonic_context.get("summary"),
    }


@dataclass
class StructureTierEntry:
    hash_value: str
    text: str


class StructureCache:
    """镜像 StructureCache:每个 (symbol, timeframe) tier 独立哈希缓存。"""

    def __init__(self) -> None:
        self._cache: dict[str, StructureTierEntry] = {}

    def _tier_key(self, symbol: str, tier: str) -> str:
        return f"{symbol}:{tier}"

    def get_or_build(self, symbol: str, payload: JSONDict) -> dict[str, Any]:
        wave_prices = extract_wave_closed_bar_prices(payload)
        chanlun_bars = extract_closed_chanlun_bars(payload)
        wave_structure = (
            analyze_elliott_wave(wave_prices)
            if len(wave_prices) >= 2
            else build_unavailable_wave_structure()
        )
        chanlun_structure = (
            analyze_chanlun(chanlun_bars)
            if len(chanlun_bars) >= 3
            else build_unavailable_chanlun_structure()
        )
        candlestick_patterns = summarize_candlestick_patterns(payload)
        harmonic_ctx_stable = sanitize_harmonic_context_stable(payload)
        harmonic_ctx_volatile = sanitize_harmonic_context_volatile(payload)
        static_pivots = extract_static_pivots(payload)

        inputs = StructureTierInputs(
            wave_structure=wave_structure,
            chanlun_structure=chanlun_structure,
            candlestick_patterns=candlestick_patterns,
            harmonic_ctx_stable=harmonic_ctx_stable,
            static_pivots=static_pivots,
        )

        block_texts: list[str] = []
        for tier in STATIC_TIMEFRAME_ORDER:
            hash_value = stable_hash(structure_tier_hash_parts(tier, inputs))
            key = self._tier_key(symbol, tier)
            cached = self._cache.get(key)
            if cached is not None and cached.hash_value == hash_value:
                block_texts.append(cached.text)
            else:
                text = render_structure_tier_block(tier, inputs)
                self._cache[key] = StructureTierEntry(hash_value=hash_value, text=text)
                block_texts.append(text)

        return {"blockTexts": block_texts, "harmonicVolatile": harmonic_ctx_volatile}


def normalize_comprehensive(result: JSONDict) -> JSONDict:
    """镜像 normalizeComprehensive:Chanlun 顶级枚举归一化(在 schema 校验之后)。"""
    chanlun = result.get("chanlun")
    if isinstance(chanlun, dict):
        if chanlun.get("hub_state") in ("breaking_up", "breaking_down"):
            chanlun["hub_state"] = "active"
        latest = chanlun.get("latest_signal")
        if latest in ("buy_1", "buy_2", "buy_3"):
            chanlun["latest_signal"] = "buy"
        elif latest in ("sell_1", "sell_2", "sell_3"):
            chanlun["latest_signal"] = "sell"
        elif latest == "close":
            chanlun["latest_signal"] = "sell"
    return result


def build_common_system_prompt() -> str:
    """镜像 buildCommonSystemPrompt:五节 Markdown 输出规则 + 双向下单 + 锚点引用。"""
    return """You are a comprehensive market analysis orchestrator.
Produce a structured MARKDOWN analysis with exactly these 5 sections:
- ## TECHNICAL
- ## WAVE
- ## CHANLUN
- ## RISK
- ## ARBITRATION

ALL 5 sections are REQUIRED on every response. Do not omit any section.

## CRITICAL RULES
1. Output structured MARKDOWN text using ## SECTION headers and - Key: Value format.
2. NEVER wrap output in ```code blocks```. Do NOT output JSON.
3. The output MUST include ALL 5 sections: TECHNICAL, WAVE, CHANLUN, RISK, ARBITRATION.
4. All enum values must be EXACTLY as specified (lowercase).
5. All numeric fields must be valid finite numbers.
6. **ABSOLUTE PRICE RULE: All SL/TP fields (Suggested SL, Suggested TP, Stop Loss, Take Profit, Trade Stop Loss, Trade Take Profit) MUST be absolute price levels visible on the chart — NOT relative offsets, NOT point distances, NOT ATR values, NOT pip counts.** A stop loss for a buy should be BELOW the current price; a take profit should be ABOVE. These numbers should be in the same order of magnitude as the current price shown above.
7. Support/Resistance levels use pipe-delimited format: price | type | strength | timeframe | touches
8. All prices must fit the instrument's range described in SYMBOL CHARACTERISTICS.
9. For bilingual text fields: Chinese first, English in parentheses.

## DUAL-DIRECTION TRADING (双向下单)

When the market is in a clear ranging/consolidation phase (technical.phase is "ranging" or "consolidation"), AND both BUY and SELL directions have valid setups with confidence ≥ 60, you MAY output a dual-direction recommendation.

Dual-direction conditions:
- technical.phase is "ranging" or "consolidation"
- support_levels and resistance_levels both have strong levels
- wave.wave_confirmation is "rejected" or "partial"
- chanlun.hub_state is "forming"
- risk.riskLevel is NOT "high" or "extreme"
- No critical blocking market filters

When dual-direction is triggered:
- arbitration.action = "open"
- arbitration.final_direction = "dual"
- trade_recommendation.direction = "dual"

## IMPORTANT: Two Different "Chanlun" Sections
- "CHANLUN" (top-level section) = SIMPLE Chanlun analysis. Use SIMPLE enums:
  - hub_state: ONLY "forming" | "active" | "none" (no breaking_up/breaking_down)
  - latest_signal: ONLY "buy" | "sell" | "hold" (no buy_1/sell_1/close)
- "Chanlun Theory" (inside ARBITRATION section) = DETAILED Chanlun theory. Uses RICH enums:
  - zhongshu_state: "forming" | "active" | "breaking_up" | "breaking_down" | "none"
  - buy_sell_point: "buy_1" | "buy_2" | "buy_3" | "sell_1" | "sell_2" | "sell_3" | "none"
Do NOT mix these up. Keep them separate.

## REQUIRED OUTPUT MARKDOWN FORMAT

## TECHNICAL
- Bias: bullish | bearish | neutral
- Confidence: <0-100>
- Phase: trending | ranging | breakout | reversal | consolidation
- Indicators Summary: <bilingual string>
- Support Levels:
  - <price> | support | strong|moderate|weak | <timeframe e.g. H1> | <touches 1-10>
- Resistance Levels:
  - <price> | resistance | strong|moderate|weak | <timeframe e.g. H4> | <touches 1-10>
- Recommendation: hold | close | partial_close | trail_stop | none
- Rationale: <bilingual string>

## WAVE
- Confirmation: confirmed | partial | rejected
- Extension Wave: 1 | 3 | 5
- Corrective Type: zigzag | flat | triangle
- Trend Strength: strong | moderate | weak
- Target Level 1.618: <number>
- Target Level 2.0: <number>
- Confidence: <0-100>
- Rationale: <bilingual string>

## CHANLUN
- Trend: up | down | range
- Strength: strong | moderate | weak
- Latest Signal: buy | sell | hold
- Hub State: forming | active | none
- Confidence: <0-100>
- Rationale: <bilingual string>

## RISK
- Risk Level: low | medium | high | extreme
- Max Position Size: <number lots>
- Suggested SL: <absolute stop loss price level>
- Suggested TP: <absolute take profit price level>
- Warnings: <semicolon-separated bilingual strings>
- Add On: true | false

## ARBITRATION
- Final Direction: buy | sell | hold | close | dual
- Confidence: <0-100>
- Action: open | close | modify | hold
- Primary Contradiction: <string or empty>
- Phase: <string>
- United Front Analysis: <bilingual string>
- Reasoning: <bilingual string, at least 3 sentences covering all 3 theories>
- Dow Primary Trend: bullish | bearish | neutral
- Dow Primary Phase: accumulation | markup | distribution | markdown
- Dow Secondary Trend: bullish | bearish | neutral
- Dow Short Term Trend: bullish | bearish | neutral
- Dow Multi TF Confirm: true | false
- Dow Rationale: <string>
- Wave Current Wave: <string>
- Wave Direction: impulse_up | impulse_down | corrective | unclear
- Wave Count: <string>
- Wave Next Target: <string>
- Wave Confidence: <0-100>
- Wave Rationale: <string>
- Chanlun Trend: up | down | range
- Chanlun Bi Direction: up | down | none
- Chanlun Duan Direction: up | down | none
- Chanlun Zhongshu State: forming | active | breaking_up | breaking_down | none
- Chanlun Buy Sell Point: buy_1 | buy_2 | buy_3 | sell_1 | sell_2 | sell_3 | none
- Chanlun Confidence: <0-100>
- Chanlun Rationale: <string>
- Harmonic Pattern: gartley | bat | butterfly | crab | abcd | cypher | shark | none
- Harmonic Direction: bullish | bearish | neutral
- Harmonic Confidence: <0-100>
- Harmonic Rationale: <string>
- Trade Direction: buy | sell | hold | dual
- Trade Entry Price: <number>
- Trade Stop Loss: <absolute stop loss price level>
- Trade Take Profit 1: <absolute take profit price level>
- Trade Take Profit 2: <absolute take profit price level>
- Trade Risk Reward Ratio: <number>
- Trade Position Size Lots: <string e.g. 0.05-0.1>
- Trade Rationale: <string>

## HARMONIC PATTERN GUIDE (Static Reference)
- Gartley/Bat: M-type (bearish) or W-type (bullish) retracement patterns — high-probability reversal zones
- Butterfly/Crab: Extension patterns — extreme reversal zones, higher risk/reward
- Cypher: C exceeds X (signature move), D retrace is based on XC (not XA) at 0.786
- Shark: B exceeds X (AB extension 1.13-1.618), D retrace at 0.886 of XA
- AB=CD: Simple geometric equivalence — CD leg mirrors AB leg
- Pattern direction + confidence determine entry weight in ARBITRATION

## PATTERN INTERPRETATION GUIDE (Static Reference)
- Bullish reversal: hammer, bullish_engulfing, piercing_line, morning_star
- Bearish reversal: shooting_star, bearish_engulfing, dark_cloud_cover, evening_star
- Continuation: three_white_soldiers, three_black_crows
- Priority: H1/M30 patterns are primary confirmation, M15 is timing signal


## ANCHOR REFERENCE SYSTEM (锚点引用)
The following anchors will be provided in subsequent messages.
You MUST reference them by their anchor ID without recalculating.

- {{WAVE_STRUCT}}: Pre-computed Elliott Wave structure
  - wave_count, wave_confirmation, extension_wave, corrective_type
  - Use exactly as provided; do NOT re-analyze wave structure
- {{CHANLUN_STRUCT}}: Pre-computed Chanlun (缠论) structure
  - bi_direction, duan_direction, zhongshu_state, buy_sell_point
  - Use exactly as provided; do NOT re-analyze chanlun structure
- {{CANDLESTICK_PATTERNS}}: Detected candlestick patterns by timeframe
  - Array of pattern strings per timeframe (e.g., {"H1": ["bullish_engulfing"]})
- {{HARMONIC_CTX}}: Pre-computed harmonic pattern detection
  - Programmatic detector output: detected_pattern, direction, confidence, PRZ, stop_loss, target levels
  - Use exactly as provided without modification; harmonic analysis is injected by system, not LLM-generated

## INTEGRATION INSTRUCTIONS
- The WAVE section MUST use {{WAVE_STRUCT}} data without modification
- The CHANLUN section MUST use {{CHANLUN_STRUCT}} data without modification
- Harmonic pattern analysis is injected from {{HARMONIC_CTX}} programmatically — do NOT output a HARMONIC section
- Mention aligned patterns from {{CANDLESTICK_PATTERNS}} in technical.indicators_summary"""  # noqa: E501


def build_symbol_system_prompt(profile: JSONDict) -> str:
    """镜像 buildSymbolSystemPrompt:品种特征 + 分析任务。"""
    return f"""## SYMBOL CHARACTERISTICS
- Instrument: {profile['name']} ({profile['symbol']})
- Price precision: {profile['price_precision']} decimal places
- Typical price range: {profile['price_range_hint']}
- Volatility: {profile['volatility_level']}
- 1 pip = {profile['pip_value']}
- Suggested SL: {profile['sl_atr_multiplier']}x ATR from current price (MUST output as absolute price level)
- Suggested TP: {profile['tp_atr_multiplier']}x ATR from current price (MUST output as absolute price level)
- All prices must fit this instrument's range (~{profile['price_range_hint']}).

## ANALYSIS TASK
Analyze {profile['name']} ({profile['symbol']}) and return structured MARKDOWN
with ALL 5 sections: TECHNICAL, WAVE, CHANLUN, RISK, ARBITRATION."""


MAX_RECENT_SWING_POINTS = 12
MAX_RECENT_FRACTALS = 12
MAX_RECENT_STROKES = 12
MAX_RECENT_HUBS = 6


def summarize_wave_structure(wave: JSONDict) -> JSONDict:
    return {
        "direction": wave.get("direction"),
        "validation": wave.get("validation"),
        "confidence": wave.get("confidence"),
        "impulseWaves": wave.get("impulseWaves"),
        "correctiveWaves": wave.get("correctiveWaves"),
        "swingPoints": (wave.get("swingPoints") or [])[-MAX_RECENT_SWING_POINTS:],
    }


def summarize_chanlun_structure(chanlun: JSONDict) -> JSONDict:
    return {
        "fractals": (chanlun.get("fractals") or [])[-MAX_RECENT_FRACTALS:],
        "strokes": (chanlun.get("strokes") or [])[-MAX_RECENT_STROKES:],
        "hubs": (chanlun.get("hubs") or [])[-MAX_RECENT_HUBS:],
    }


PIVOT_FIELDS = ("pp", "r1", "s1")
STATIC_TIMEFRAME_ORDER = ("H4", "H1", "M30", "M15")


@dataclass
class StructureTierInputs:
    wave_structure: JSONDict
    chanlun_structure: JSONDict
    candlestick_patterns: dict[str, list[str]]
    harmonic_ctx_stable: JSONDict | None
    static_pivots: JSONDict


def extract_static_pivots(payload: JSONDict) -> JSONDict:
    out: JSONDict = {}
    for timeframe in STATIC_TIMEFRAME_ORDER:
        indicator = select_indicator(payload.get("indicators") or {}, timeframe, timeframe.lower())
        pivot: JSONDict = {}
        for field in PIVOT_FIELDS:
            value = indicator.get(field)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                pivot[field] = value
        if pivot:
            out[timeframe] = pivot
    return out


def structure_tier_hash_parts(tier: str, inputs: StructureTierInputs) -> list[Any]:
    parts: list[Any] = []
    if tier == "H1":
        parts.extend(
            [
                summarize_wave_structure(inputs.wave_structure),
                summarize_chanlun_structure(inputs.chanlun_structure),
                inputs.harmonic_ctx_stable if inputs.harmonic_ctx_stable is not None else "none",
            ]
        )
    parts.append(inputs.static_pivots.get(tier, "none"))
    parts.append(inputs.candlestick_patterns.get(tier, "none"))
    return parts


def render_static_pivot_for(static_pivots: JSONDict, timeframe: str) -> str:
    pivot = static_pivots.get(timeframe)
    if isinstance(pivot, dict) and len(pivot) > 0:
        return f"- {timeframe}: {stable_stringify(pivot)}"
    return ""


def render_candlestick_patterns_for(candlestick_patterns: dict[str, list[str]], timeframe: str) -> str:
    patterns = candlestick_patterns.get(timeframe)
    if isinstance(patterns, list) and len(patterns) > 0:
        return f"- {timeframe}: {stable_stringify(patterns)}"
    return ""


def render_structure_tier_block(tier: str, inputs: StructureTierInputs) -> str:
    sections: list[str] = []
    if tier == "H1":
        sections.append(f"### WAVE_STRUCT\n{stable_stringify(summarize_wave_structure(inputs.wave_structure))}")
        sections.append(
            "### CHANLUN_STRUCT\n" + stable_stringify(summarize_chanlun_structure(inputs.chanlun_structure))
        )
        sections.append(
            "### HARMONIC_CTX\n"
            + (stable_stringify(inputs.harmonic_ctx_stable) if inputs.harmonic_ctx_stable else "none")
        )

    pivot_line = render_static_pivot_for(inputs.static_pivots, tier)
    sections.append(f"### PIVOT_LEVELS ({tier})\n{pivot_line or 'none'}")

    pattern_line = render_candlestick_patterns_for(inputs.candlestick_patterns, tier)
    sections.append(f"### CANDLESTICK_PATTERNS ({tier})\n{pattern_line or 'none'}")

    return f"## COMPUTED STRUCTURES — {tier} (caching eligible)\n\n{'\n\n'.join(sections)}"


def build_static_context_prompt(
    payload: JSONDict,
    symbol: str,
    structure_cache: StructureCache,
) -> dict[str, Any]:
    return structure_cache.get_or_build(symbol, payload)


STATIC_ACCOUNT_KEYS = ("account_id", "currency", "leverage", "broker", "server_name")


def partition_account(account: JSONDict) -> dict[str, JSONDict]:
    static_fields: JSONDict = {}
    dynamic_fields: JSONDict = {}
    for key, value in account.items():
        if key in STATIC_ACCOUNT_KEYS:
            static_fields[key] = value
        else:
            dynamic_fields[key] = value
    return {"staticFields": static_fields, "dynamicFields": dynamic_fields}


def build_static_account_and_strategy_text(payload: JSONDict, market_only: bool) -> str:
    sections = [
        f"### STRATEGY_MAPPING (EA-side contract)\n{stable_stringify(payload.get('strategy_mapping') or {})}"
    ]
    if not market_only:
        static_action_fields = partition_account(payload.get("account") or {}).get("staticFields", {})
        sections.append("### ACCOUNT_STATIC\n" + stable_stringify(static_action_fields))
    return f"## STATIC ACCOUNT & STRATEGY CONTEXT (caching eligible)\n\n{'\n\n'.join(sections)}"


def strip_hoisted_pivot_fields(indicator: JSONDict) -> JSONDict:
    out = dict(indicator)
    for field in PIVOT_FIELDS:
        out.pop(field, None)
    return out


def build_realtime_data_prompt(
    payload: JSONDict,
    pending_signal: JSONDict | None,
    symbol: str,
    profile: JSONDict,
    harmonic_volatile: JSONDict | None,
    market_only: bool = False,
) -> str:
    """镜像 buildRealtimeDataPrompt:每请求变化的实时层(不缓存)。"""
    market = payload.get("market") or {}
    indicators = payload.get("indicators") or {}
    current_price = market.get("bid") or market.get("ask") or 0
    m15 = strip_hoisted_pivot_fields(select_indicator(indicators, "M15", "m15"))
    m30 = strip_hoisted_pivot_fields(select_indicator(indicators, "M30", "m30"))
    h1 = strip_hoisted_pivot_fields(select_indicator(indicators, "H1", "h1"))
    h4 = strip_hoisted_pivot_fields(select_indicator(indicators, "H4", "h4"))

    market_status = payload.get("market_status") or {}
    stable_market_status = {k: v for k, v in market_status.items() if k != "mt4_server_time"}

    account_positions = (
        ""
        if market_only
        else f"""### Account State
{stable_stringify(partition_account(payload.get('account') or {}).get('dynamicFields', {}))}

### Current Positions
{stable_stringify(payload.get('positions') or [])}"""
    )

    pending_signal_text = (
        ""
        if market_only
        else f"""### Pending Signal (from previous analysis cycle)
{stable_stringify(pending_signal) if pending_signal else 'none'}"""
    )

    divergence_m30 = m30.get("macd_divergence")
    rsi_m30 = m30.get("rsi_divergence")

    reminder = (
        "Risk and arbitration must describe market structure only; do not infer account-specific actions."
        if market_only
        else "Risk and arbitration sections must reflect account, positions, and pending signal"
    )

    return f"""## REAL-TIME MARKET DATA (Dynamic — no caching)

### Current Price & Market Context
- **{symbol}: {current_price:.{profile['price_precision']}f}**
- Market status: {stable_stringify(stable_market_status)}

### Market Data
{stable_stringify(market)}

{account_positions}

### Multi-Timeframe Indicators (Live)
- **M15:** {stable_stringify(m15)}
- **M30:** {stable_stringify(m30)}
- **H1:** {stable_stringify(h1)}
- **H4:** {stable_stringify(h4)}

### Divergence Signals (Technical Indicator Engine)
- **MACD Divergence:** H1={h1.get('macd_divergence') or 'none'}, M30={divergence_m30 or 'none'}
- **RSI Divergence:** H1={h1.get('rsi_divergence') or 'none'}, M30={rsi_m30 or 'none'}
- **Impact:** Bullish divergence → increase BUY confidence, bearish divergence → increase SELL confidence
- Strong divergence (price extreme + contra-trend RSI/MACD) must be mentioned in technical.rationale

### Harmonic Realtime (volatile)
{stable_stringify(harmonic_volatile) if harmonic_volatile else 'none'}

{pending_signal_text}

### Final Reminders
- Output MUST include all 5 top-level sections: TECHNICAL, WAVE, CHANLUN, RISK, ARBITRATION
- {reminder}
- All prices must fit instrument range (~{profile['price_range_hint']})
- **PRICE ANCHOR: Current {symbol} price is {current_price:.{profile['price_precision']}f}. All SL/TP values MUST be absolute price levels in this same order of magnitude.** Do NOT output ATR values, point distances, or pip offsets as SL/TP."""  # noqa: E501


def build_fallback(current_price: float) -> JSONDict:
    """镜像 buildFallback:全路径失败时的中性兜底。"""
    return {
        "technical": {
            "bias": "neutral",
            "confidence": 0,
            "phase": "consolidation",
            "indicators_summary": "中性观望 (Neutral hold)",
            "support_levels": [],
            "resistance_levels": [],
            "recommendation": "none",
            "rationale": "综合分析失败，返回中性结果 (Comprehensive analysis failed, returning neutral result)",
        },
        "wave": {
            "wave_confirmation": "rejected",
            "extension_wave": None,
            "corrective_type": None,
            "trend_strength": "weak",
            "target_levels": {
                "level_1_618": current_price,
                "level_2_0": current_price,
            },
            "confidence": 0,
            "rationale": "波浪结构不可用 (Wave structure unavailable)",
        },
        "chanlun": {
            "trend": "range",
            "strength": "weak",
            "latest_signal": "hold",
            "hub_state": "none",
            "confidence": 0,
            "rationale": "缠论结构不可用 (Chanlun structure unavailable)",
        },
        "harmonic": {
            "detected_pattern": "none",
            "direction": "neutral",
            "timeframe": "N/A",
            "completion_pct": 0,
            "confidence": 0,
            "d_zone_price": 0,
            "entry_zone": "N/A",
            "stop_loss": 0,
            "take_profit_1": 0,
            "take_profit_2": 0,
            "rationale": "谐波形态不可用 (Harmonic pattern unavailable)",
        },
        "risk": {
            "riskLevel": "high",
            "maxPositionSize": 0,
            "suggestedSL": 0,
            "suggestedTP": 0,
            "warnings": ["综合分析失败，建议观望 (Comprehensive analysis failed, stay flat)"],
            "addOn": False,
        },
        "arbitration": {
            "final_direction": "hold",
            "confidence": 0,
            "primary_contradiction": "analysis_unavailable",
            "phase": "unknown",
            "reasoning": "综合分析失败，维持观望 (Comprehensive analysis failed, hold)",
            "action": "hold",
            "united_front_analysis": "无一致性信号 (No aligned signal)",
            "dow_theory": {
                "primary_trend": "neutral",
                "primary_phase": "accumulation",
                "secondary_trend": "neutral",
                "short_term_trend": "neutral",
                "multi_tf_confirm": False,
                "rationale": "道氏理论不可用，无法确认趋势或阶段，建议观望 (Dow theory unavailable; observe)",
            },
            "wave_theory": {
                "current_wave": "unknown",
                "wave_direction": "unclear",
                "wave_count": "unavailable",
                "next_target": "N/A",
                "confidence": 0,
                "rationale": "波浪理论不可用，无法确认浪型或目标，建议观望 (Wave theory unavailable; observe)",
            },
            "chanlun_theory": {
                "trend": "range",
                "bi_direction": "none",
                "duan_direction": "none",
                "zhongshu_state": "none",
                "buy_sell_point": "none",
                "confidence": 0,
                "rationale": "缠论结构不可用，无法确认买卖点，建议观望 (Chanlun theory unavailable; observe)",
            },
            "harmonic_theory": {
                "pattern": "none",
                "direction": "neutral",
                "confidence": 0,
                "rationale": "谐波理论不可用，无法确认形态与方向，建议观望 (Harmonic theory unavailable; observe)",
            },
            "trade_recommendation": {
                "direction": "hold",
                "entry_price": 0,
                "stop_loss": 0,
                "take_profit_1": 0,
                "risk_reward_ratio": 0,
                "position_size_lots": "0",
                "rationale": "分析结果不可用，暂无可靠交易结论，建议观望 (Analysis unavailable; hold)",
            },
        },
    }


def build_harmonic_from_context(ctx: Any) -> JSONDict:
    """镜像 buildHarmonicFromContext:程序化解码器输出替代 LLM 谐波判断。"""
    if not _is_record(ctx) or not _is_record(ctx.get("active_pattern")):
        return {
            "detected_pattern": "none",
            "direction": "neutral",
            "timeframe": "N/A",
            "completion_pct": 0,
            "confidence": 0,
            "d_zone_price": 0,
            "entry_zone": "N/A",
            "stop_loss": 0,
            "take_profit_1": 0,
            "take_profit_2": 0,
            "rationale": "无程序化谐波形态检测到 (No programmatic harmonic pattern detected)",
        }

    ap = ctx["active_pattern"]
    prz_low = ap.get("prz_low") or 0
    prz_high = ap.get("prz_high") or 0
    d_zone_price = (prz_low + prz_high) / 2 if prz_low > 0 and prz_high > 0 else 0
    entry_zone = f"{prz_low:.2f}-{prz_high:.2f}" if prz_low > 0 and prz_high > 0 else "N/A"

    confidence = ap.get("confidence")
    if confidence is None:
        confidence = ap.get("score")
    pattern = ap.get("type") if ap.get("type") in (
        "gartley", "bat", "butterfly", "crab", "abcd", "cypher", "shark", "deep_crab", "none"
    ) else "none"

    rationale = ap.get("reason") or (
        f"程序化检测到 {ap.get('timeframe')} {ap.get('direction')} {ap.get('type')} 形态，PRZ={entry_zone}"
    )

    return {
        "detected_pattern": pattern,
        "direction": ap.get("direction")
        if ap.get("direction") in ("bullish", "bearish", "neutral")
        else "neutral",
        "timeframe": ap.get("timeframe"),
        "completion_pct": confidence,
        "confidence": confidence,
        "d_zone_price": d_zone_price,
        "entry_zone": entry_zone,
        "stop_loss": ap.get("stop_loss") or 0,
        "take_profit_1": ap.get("target_1") or 0,
        "take_profit_2": ap.get("target_2") or 0,
        "rationale": rationale,
    }


def build_harmonic_theory_from_context(ctx: Any) -> JSONDict:
    """镜像 buildHarmonicTheoryFromContext:仲裁节的谐波理论。"""
    if not _is_record(ctx) or not _is_record(ctx.get("active_pattern")):
        return {
            "pattern": "none",
            "direction": "neutral",
            "confidence": 0,
            "rationale": "无程序化谐波形态 (No programmatic harmonic pattern)",
        }

    ap = ctx["active_pattern"]
    confidence = ap.get("confidence")
    if confidence is None:
        confidence = ap.get("score")
    pattern = ap.get("type") if ap.get("type") in (
        "gartley", "bat", "butterfly", "crab", "abcd", "cypher", "shark", "deep_crab", "none"
    ) else "none"
    return {
        "pattern": pattern,
        "direction": ap.get("direction")
        if ap.get("direction") in ("bullish", "bearish", "neutral")
        else "neutral",
        "confidence": confidence,
        "rationale": ap.get("reason") or f"程序化检测到 {ap.get('type')} 形态",
    }


def parse_markdown_response(
    raw: str,
    current_price: float,
    profile: JSONDict,
) -> JSONDict | None:
    """镜像 parseMarkdownResponse:五节 Markdown 解析(基础失败返回 None)。"""
    sections = split_sections(raw)
    if len(sections) < 3:
        return None

    # ── TECHNICAL ──
    tech_section = sections.get("technical", "")
    tech_fields = extract_fields(tech_section)
    tech_lines = tech_section.split("\n")
    in_support_section = False
    in_resistance_section = False
    support_lines: list[str] = []
    resistance_lines: list[str] = []
    for line in tech_lines:
        if re.match(r"^-\s+support\s+levels", line, re.IGNORECASE):
            in_support_section, in_resistance_section = True, False
        elif re.match(r"^-\s+resistance\s+levels", line, re.IGNORECASE):
            in_resistance_section, in_support_section = True, False
        elif re.match(r"^-\s+\w", line) and not re.match(r"^\s", line):
            in_support_section, in_resistance_section = False, False
        list_match = re.match(r"^\s{2,}-\s+(.+)", line)
        if list_match:
            content = list_match.group(1).strip()
            if "|" in content:
                if in_support_section:
                    support_lines.append(content)
                elif in_resistance_section:
                    resistance_lines.append(content)

    technical = {
        "bias": get_enum_field(tech_fields, "bias", ("bullish", "bearish", "neutral"), "neutral"),
        "confidence": get_number_field(tech_fields, "confidence", 0, {"min": 0, "max": 100}),
        "phase": get_enum_field(
            tech_fields, "phase", ("trending", "ranging", "breakout", "reversal", "consolidation"), "consolidation"
        ),
        "indicators_summary": get_string_field(tech_fields, "indicators_summary", "中性观望 (Neutral hold)"),
        "support_levels": parse_sr_levels(support_lines, "support"),
        "resistance_levels": parse_sr_levels(resistance_lines, "resistance"),
        "recommendation": get_enum_field(
            tech_fields, "recommendation", ("hold", "close", "partial_close", "trail_stop", "none"), "none"
        ),
        "rationale": get_string_field(tech_fields, "rationale", "无分析 (No analysis)"),
    }

    # ── WAVE ──
    wave_section = sections.get("wave", "")
    wave_fields = extract_fields(wave_section)
    ext_wave_raw = get_number_field(wave_fields, "extension_wave", 0, {"min": 1, "max": 5})
    extension_wave = ext_wave_raw if ext_wave_raw in (1, 3, 5) else None
    corrective_raw = get_string_field(wave_fields, "corrective_type", "")
    corrective_type = corrective_raw if corrective_raw in ("zigzag", "flat", "triangle") else None

    wave = {
        "wave_confirmation": get_enum_field(
            wave_fields, "confirmation", ("confirmed", "partial", "rejected"), "rejected"
        ),
        "extension_wave": extension_wave,
        "corrective_type": corrective_type,
        "trend_strength": get_enum_field(wave_fields, "trend_strength", ("strong", "moderate", "weak"), "weak"),
        "target_levels": {
            "level_1_618": get_number_field(wave_fields, "target_level_1.618", current_price),
            "level_2_0": get_number_field(wave_fields, "target_level_2.0", current_price),
        },
        "confidence": get_number_field(wave_fields, "confidence", 0, {"min": 0, "max": 100}),
        "rationale": get_string_field(wave_fields, "rationale", "波浪结构不可用 (Wave structure unavailable)"),
    }

    # ── CHANLUN ──
    chanlun_section = sections.get("chanlun", "")
    chanlun_fields = extract_fields(chanlun_section)

    chanlun = {
        "trend": get_enum_field(chanlun_fields, "trend", ("up", "down", "range"), "range"),
        "strength": get_enum_field(chanlun_fields, "strength", ("strong", "moderate", "weak"), "weak"),
        "latest_signal": get_enum_field(chanlun_fields, "latest_signal", ("buy", "sell", "hold"), "hold"),
        "hub_state": get_enum_field(chanlun_fields, "hub_state", ("forming", "active", "none"), "none"),
        "confidence": get_number_field(chanlun_fields, "confidence", 0, {"min": 0, "max": 100}),
        "rationale": get_string_field(chanlun_fields, "rationale", "缠论结构不可用 (Chanlun structure unavailable)"),
    }

    # ── HARMONIC ──
    harmonic_section = sections.get("harmonic", "")
    harmonic_fields = extract_fields(harmonic_section)

    harmonic = {
        "detected_pattern": get_enum_field(
            harmonic_fields,
            "detected_pattern",
            ("gartley", "bat", "butterfly", "crab", "abcd", "cypher", "shark", "deep_crab", "none"),
            "none",
        ),
        "direction": get_enum_field(harmonic_fields, "direction", ("bullish", "bearish", "neutral"), "neutral"),
        "timeframe": get_string_field(harmonic_fields, "timeframe", "N/A"),
        "completion_pct": get_number_field(harmonic_fields, "completion", 0, {"min": 0, "max": 100}),
        "confidence": get_number_field(harmonic_fields, "confidence", 0, {"min": 0, "max": 100}),
        "d_zone_price": get_number_field(harmonic_fields, "d_zone_price", 0),
        "entry_zone": get_string_field(harmonic_fields, "entry_zone", "N/A"),
        "stop_loss": get_number_field(harmonic_fields, "stop_loss", 0),
        "take_profit_1": get_number_field(harmonic_fields, "take_profit_1", 0),
        "take_profit_2": get_number_field(harmonic_fields, "take_profit_2", 0),
        "rationale": get_string_field(harmonic_fields, "rationale", "谐波形态未检测到 (No harmonic pattern detected)"),
    }

    # ── RISK ──
    risk_section = sections.get("risk", "")
    risk_fields = extract_fields(risk_section)
    risk_list_items = extract_list_items(risk_section)

    risk = {
        "riskLevel": get_enum_field(risk_fields, "risk_level", ("low", "medium", "high", "extreme"), "high"),
        "maxPositionSize": get_number_field(risk_fields, "max_position_size", 0),
        "suggestedSL": get_number_field(risk_fields, "suggested_sl", 0),
        "suggestedTP": get_number_field(risk_fields, "suggested_tp", 0),
        "warnings": extract_warnings(risk_fields, risk_list_items),
        "addOn": get_boolean_field(risk_fields, "add_on", False),
    }

    # ── ARBITRATION ──
    arb_section = sections.get("arbitration", "")
    arb_fields = extract_fields(arb_section)

    required_trade_fields = [
        "trade_direction",
        "trade_entry_price",
        "trade_stop_loss",
        "trade_take_profit_1",
        "trade_risk_reward_ratio",
        "trade_position_size_lots",
    ]
    missing_trade_fields = [field for field in required_trade_fields if field not in arb_fields]

    if len(missing_trade_fields) > 0:
        get_logger().warn(
            {
                "symbol": profile["symbol"],
                "rawLength": len(raw),
                "sectionCount": len(sections),
                "arbitrationLength": len(arb_section),
                "missingTradeFields": missing_trade_fields,
                "availableFields": [key for key in arb_fields.keys() if key.startswith("trade_")],
            },
            "Incomplete ARBITRATION section detected, rejecting to trigger retry",
        )
        return None

    dow_theory = {
        "primary_trend": get_enum_field(arb_fields, "dow_primary_trend", ("bullish", "bearish", "neutral"), "neutral"),
        "primary_phase": get_enum_field(
            arb_fields, "dow_primary_phase", ("accumulation", "markup", "distribution", "markdown"), "accumulation"
        ),
        "secondary_trend": get_enum_field(
            arb_fields, "dow_secondary_trend", ("bullish", "bearish", "neutral"), "neutral"
        ),
        "short_term_trend": get_enum_field(
            arb_fields, "dow_short_term_trend", ("bullish", "bearish", "neutral"), "neutral"
        ),
        "multi_tf_confirm": get_boolean_field(arb_fields, "dow_multi_tf_confirm", False),
        "rationale": get_string_field(arb_fields, "dow_rationale", ""),
    }

    wave_theory = {
        "current_wave": get_string_field(arb_fields, "wave_current_wave", "Unknown"),
        "wave_direction": get_enum_field(
            arb_fields, "wave_direction", ("impulse_up", "impulse_down", "corrective", "unclear"), "unclear"
        ),
        "wave_count": get_string_field(arb_fields, "wave_count", "Unknown"),
        "next_target": get_string_field(arb_fields, "wave_next_target", "N/A"),
        "confidence": get_number_field(arb_fields, "wave_confidence", 0, {"min": 0, "max": 100}),
        "rationale": get_string_field(arb_fields, "wave_rationale", ""),
    }

    chanlun_theory = {
        "trend": get_enum_field(arb_fields, "chanlun_trend", ("up", "down", "range"), "range"),
        "bi_direction": get_enum_field(arb_fields, "chanlun_bi_direction", ("up", "down", "none"), "none"),
        "duan_direction": get_enum_field(arb_fields, "chanlun_duan_direction", ("up", "down", "none"), "none"),
        "zhongshu_state": get_enum_field(
            arb_fields,
            "chanlun_zhongshu_state",
            ("forming", "active", "breaking_up", "breaking_down", "none"),
            "none",
        ),
        "buy_sell_point": get_enum_field(
            arb_fields,
            "chanlun_buy_sell_point",
            ("buy_1", "buy_2", "buy_3", "sell_1", "sell_2", "sell_3", "none"),
            "none",
        ),
        "confidence": get_number_field(arb_fields, "chanlun_confidence", 0, {"min": 0, "max": 100}),
        "rationale": get_string_field(arb_fields, "chanlun_rationale", ""),
    }

    harmonic_theory = {
        "pattern": get_enum_field(
            arb_fields,
            "harmonic_pattern",
            ("gartley", "bat", "butterfly", "crab", "abcd", "cypher", "shark", "deep_crab", "none"),
            "none",
        ),
        "direction": get_enum_field(arb_fields, "harmonic_direction", ("bullish", "bearish", "neutral"), "neutral"),
        "confidence": get_number_field(arb_fields, "harmonic_confidence", 0, {"min": 0, "max": 100}),
        "rationale": get_string_field(arb_fields, "harmonic_rationale", ""),
    }

    trade_direction = get_enum_field(arb_fields, "trade_direction", ("buy", "sell", "hold", "dual"), "hold")
    trade_entry_price = get_number_field(arb_fields, "trade_entry_price", 0)
    trade_stop_loss = get_number_field(arb_fields, "trade_stop_loss", 0)
    trade_take_profit_1 = get_number_field(arb_fields, "trade_take_profit_1", 0)
    trade_take_profit_2 = get_number_field(arb_fields, "trade_take_profit_2", 0)

    trade_recommendation: JSONDict | None = None
    if trade_entry_price > 0 and trade_stop_loss > 0 and trade_take_profit_1 > 0:
        trade_recommendation = {
            "direction": "buy" if trade_direction == "dual" else trade_direction,
            "entry_price": trade_entry_price,
            "stop_loss": trade_stop_loss,
            "take_profit_1": trade_take_profit_1,
            "take_profit_2": trade_take_profit_2 if trade_take_profit_2 > 0 else None,
            "risk_reward_ratio": get_number_field(arb_fields, "trade_risk_reward_ratio", 0),
            "position_size_lots": get_string_field(arb_fields, "trade_position_size_lots", "0.01"),
            "rationale": get_string_field(arb_fields, "trade_rationale", ""),
        }

    arbitration = {
        "final_direction": get_enum_field(
            arb_fields, "final_direction", ("buy", "sell", "hold", "close", "dual"), "hold"
        ),
        "confidence": get_number_field(arb_fields, "confidence", 0, {"min": 0, "max": 100}),
        "primary_contradiction": get_string_field(arb_fields, "primary_contradiction", ""),
        "phase": get_string_field(arb_fields, "phase", "unknown"),
        "reasoning": get_string_field(arb_fields, "reasoning", "无分析推理 (No analysis reasoning)"),
        "action": get_enum_field(arb_fields, "action", ("open", "close", "modify", "hold"), "hold"),
        "united_front_analysis": get_string_field(
            arb_fields, "united_front_analysis", "无一致性信号 (No aligned signal)"
        ),
        "dow_theory": dow_theory,
        "wave_theory": wave_theory,
        "chanlun_theory": chanlun_theory,
        "harmonic_theory": harmonic_theory,
        "trade_recommendation": trade_recommendation,
    }

    return {
        "technical": technical,
        "wave": wave,
        "chanlun": chanlun,
        "harmonic": harmonic,
        "risk": risk,
        "arbitration": arbitration,
    }


COMPREHENSIVE_ANALYSIS_TOOLS = [
    {
        "name": "submit_comprehensive_analysis",
        "description": (
            "Submit the full comprehensive market analysis result as structured data. "
            "Every section (technical / wave / chanlun / risk / arbitration) is required."
        ),
        "input_schema": {
            "type": "object",
            "required": ["technical", "wave", "chanlun", "risk", "arbitration"],
            "properties": {
                "technical": {"type": "object"},
                "wave": {"type": "object"},
                "chanlun": {"type": "object"},
                "risk": {"type": "object"},
                "arbitration": {"type": "object"},
                "harmonic": {"type": "object"},
            },
        },
    }
]


def format_lots(lots: float) -> str:
    return f"{lots:.4f}".rstrip("0").rstrip(".") if lots == lots else str(lots)


def build_trade_action_decision_prompt(profile: JSONDict) -> str:
    """镜像 buildTradeActionDecisionPrompt:第二阶段下单决策提示。"""
    min_lots = float(to_finite_number(profile.get("min_lots")) or DEFAULT_MIN_LOTS)
    max_lots = float(to_finite_number(profile.get("max_lots")) or DEFAULT_MAX_LOTS)
    typical_max_lots = max(min_lots, max_lots / 10)

    return f"""You are the final trade execution decision agent.

Given the arbitration decision and trade recommendation from the first phase, you MUST call exactly ONE tool:

1. place_pending_order - when the recommended entry_price DIFFERS from the current market price
   (e.g., recommendation says "wait for pullback to 4145" and current price is 4174).
   The pending order will trigger when price reaches 4145.

2. place_market_order - when the recommendation says to open IMMEDIATELY at the current price
   (e.g., "买入现价" / "market buy now").

3. do_nothing - when confidence is too low (<50), direction is hold, or no clear edge.

CRITICAL RULES:
- If entry_price in the recommendation differs from current price by > 0.5%, USE place_pending_order
- Lots must be between {format_lots(min_lots)} and {format_lots(max_lots)} (typically {format_lots(min_lots)}-{format_lots(typical_max_lots)} for {profile['symbol']} intraday)
- expiry_hours defaults to 4 (intraday), set higher only if explicitly warranted
- reason MUST be bilingual (Chinese first, English in parentheses)
- For pending orders, verify entry_price is on the correct side of current:
  * buy limit: entry < current (waiting for dip)
  * sell limit: entry > current (waiting for rally)
  If wrong, call do_nothing instead."""  # noqa: E501


def current_price_from_payload(payload: JSONDict) -> float:
    bid = to_finite_number((payload.get("market") or {}).get("bid"))
    ask = to_finite_number((payload.get("market") or {}).get("ask"))
    if bid is not None and ask is not None:
        return (bid + ask) / 2
    return bid if bid is not None else (ask if ask is not None else 0)


def execution_price_from_payload(payload: JSONDict, side: str) -> float:
    bid = to_finite_number((payload.get("market") or {}).get("bid"))
    ask = to_finite_number((payload.get("market") or {}).get("ask"))
    if side == "buy":
        return ask if ask is not None else (bid if bid is not None else 0)
    return bid if bid is not None else (ask if ask is not None else 0)


def build_trade_intent(result: JSONDict, current_price: float, atr: float) -> JSONDict:
    """镜像 buildTradeIntent:把绝对价格转换为 ATR 化的交易意图。"""
    trade = (result.get("arbitration") or {}).get("trade_recommendation")
    if (
        not trade
        or trade.get("direction") == "hold"
        or atr <= 0
        or current_price <= 0
    ):
        return {
            "direction": "hold",
            "entry_trigger": "none",
            "entry_offset_atr": 0,
            "stop_loss_atr": 0,
            "take_profit_1_atr": 0,
            "rationale": (result.get("arbitration") or {}).get("reasoning"),
        }

    entry_offset_atr_value = abs(trade["entry_price"] - current_price) / atr
    if entry_offset_atr_value > 0.05:
        if trade["direction"] == "buy":
            entry_trigger = "pullback" if trade["entry_price"] < current_price else "breakout"
        else:
            entry_trigger = "pullback" if trade["entry_price"] > current_price else "breakout"
    else:
        entry_trigger = "market"

    intent: JSONDict = {
        "direction": trade["direction"],
        "entry_trigger": entry_trigger,
        "entry_offset_atr": entry_offset_atr_value,
        "stop_loss_atr": abs(trade["entry_price"] - trade["stop_loss"]) / atr,
        "take_profit_1_atr": abs(trade["take_profit_1"] - trade["entry_price"]) / atr,
        "rationale": trade.get("rationale") or (result.get("arbitration") or {}).get("reasoning"),
    }
    if trade.get("take_profit_2") is not None:
        intent["take_profit_2_atr"] = abs(trade["take_profit_2"] - trade["entry_price"]) / atr
    return intent


def to_market_insight(result: JSONDict, current_price: float, atr: float) -> JSONDict:
    """镜像 toMarketInsight:结果 -> MarketInsight(含 sr_levels/trade_intent)。"""
    technical = result["technical"]
    return {
        "technical": technical,
        "wave": result["wave"],
        "chanlun": result["chanlun"],
        "harmonic": result["harmonic"],
        "risk": result["risk"],
        "arbitration": result["arbitration"],
        "sr_levels": {
            "support": [level["price"] for level in technical["support_levels"]],
            "resistance": [level["price"] for level in technical["resistance_levels"]],
        },
        "trend_bias": technical["bias"],
        "confidence": (result["arbitration"].get("confidence") or 0) or technical["confidence"],
        "trade_intent": build_trade_intent(result, current_price, atr),
    }


def build_account_decision_system_prompt(profile: JSONDict) -> str:
    """镜像 buildAccountDecisionSystemPrompt:下单决策提示 + 账户安全规则。"""
    return f"""{build_trade_action_decision_prompt(profile)}

ACCOUNT-AWARE SAFETY RULES:
- You are deciding for exactly one account context.
- Every tool call MUST include the given account_id.
- The symbol in any action must be the account's own loaded contract symbol, not the shared market-analysis symbol.
- For modify_order/close_order, only use tickets visible in Current Positions for this account."""


def build_account_decision_prompt(
    insight: JSONDict,
    account_view: JSONDict,
    benchmark_price: float,
    atr: float,
    deviation_atr: float,
    profile: JSONDict,
) -> str:
    """镜像 buildAccountDecisionPrompt:市场洞察 + 账户视角上下文。"""
    payload = account_view.get("payload") or {}
    market = payload.get("market") or {}
    current_price = current_price_from_payload(payload)
    lines = [
        "## MARKET INSIGHT SUMMARY",
        f"- Trend Bias: {insight.get('trend_bias')}",
        f"- Confidence: {insight.get('confidence')}",
        f"- Technical Phase: {insight.get('technical', {}).get('phase')}",
        f"- Arbitration Direction: {insight.get('arbitration', {}).get('final_direction')}",
        f"- Arbitration Action: {insight.get('arbitration', {}).get('action')}",
        f"- Reasoning: {insight.get('arbitration', {}).get('reasoning')}",
        f"- Trade Intent: {stable_stringify(insight.get('trade_intent'))}",
        f"- Support Levels: {stable_stringify(insight.get('sr_levels', {}).get('support', [])[:5])}",
        f"- Resistance Levels: {stable_stringify(insight.get('sr_levels', {}).get('resistance', [])[:5])}",
        "",
        "## ACCOUNT CONTEXT",
        f"- Account ID: {account_view.get('accountId')}",
        f"- Tradable Symbol: {account_view.get('symbol')}",
        f"- Loaded ai_symbols: {stable_stringify(account_view.get('aiSymbols'))}",
        f"- Current {account_view.get('symbol')} mid price: {current_price:.{profile['price_precision']}f}",
        f"- Buy execution ask: {market.get('ask')}",
        f"- Sell execution bid: {market.get('bid')}",
        f"- Market benchmark price: {benchmark_price:.{profile['price_precision']}f}",
        f"- ATR: {atr}",
        f"- Quote Deviation ATR: {deviation_atr}",
        "",
        "## ACCOUNT STATE",
        stable_stringify(payload.get("account") or {}),
        "",
        "## CURRENT POSITIONS",
        stable_stringify(payload.get("positions") or []) if len(payload.get("positions") or []) > 0 else "none",
        "",
        "## PENDING SIGNAL",
        stable_stringify(account_view.get("pendingSignal")) if account_view.get("pendingSignal") else "none",
        "",
        "## EXECUTION PRICE RULE",
        f"Rebuild absolute entry/SL/TP from this account's bid/ask for {account_view.get('symbol')}. "
        "Do not copy absolute prices from the shared market account.",
    ]
    return "\n".join(lines)


def rebuild_account_order_from_insight(
    action: JSONDict,
    insight: JSONDict,
    payload: JSONDict,
    atr: float,
) -> JSONDict:
    """镜像 rebuildAccountOrderFromInsight:基于账户 bid/ask 重建绝对价位。"""
    current_price = current_price_from_payload(payload)
    raw_side = action.get("side")
    action_side = raw_side if isinstance(raw_side, str) else "buy"
    execution_price = (
        execution_price_from_payload(payload, action_side)
        if action.get("type") == "place_market_order"
        else current_price
    )
    if execution_price <= 0:
        return action

    intent = insight.get("trade_intent") or {}
    entry_offset = max(0.0, intent.get("entry_offset_atr") or 0) * atr if atr > 0 else 0
    if action.get("type") == "place_market_order":
        anchor = execution_price
    else:
        anchor = resolve_account_entry_price(
            action_side,
            intent.get("entry_trigger"),
            current_price,
            entry_offset,
            to_finite_number(action.get("entry_price")) or 0,
        )
    fallback_stop_distance = atr * 1.5 if atr > 0 else 0
    fallback_tp_distance = atr * 3 if atr > 0 else 0
    raw_sl = to_finite_number(action.get("stop_loss"))
    raw_tp1 = to_finite_number(action.get("take_profit_1"))
    sl_diff = abs(anchor - raw_sl) if raw_sl is not None else 0
    tp1_diff = abs(raw_tp1 - anchor) if raw_tp1 is not None else 0
    sl_distance = (
        (intent.get("stop_loss_atr") or 0) * atr
        if atr > 0 and (intent.get("stop_loss_atr") or 0) > 0
        else (sl_diff if sl_diff > 0 else fallback_stop_distance)
    )
    tp1_distance = (
        (intent.get("take_profit_1_atr") or 0) * atr
        if atr > 0 and (intent.get("take_profit_1_atr") or 0) > 0
        else (tp1_diff if tp1_diff > 0 else fallback_tp_distance)
    )
    tp2_distance: float | None = None
    tp2_atr = intent.get("take_profit_2_atr")
    raw_tp2 = to_finite_number(action.get("take_profit_2"))
    if atr > 0 and isinstance(tp2_atr, (int, float)) and not isinstance(tp2_atr, bool) and tp2_atr > 0:
        tp2_distance = float(tp2_atr) * atr
    elif raw_tp2 is not None:
        tp2_distance = abs(raw_tp2 - anchor)

    levels: JSONDict = {
        "stop_loss": anchor - sl_distance if action.get("side") == "buy" else anchor + sl_distance,
        "take_profit_1": anchor + tp1_distance if action.get("side") == "buy" else anchor - tp1_distance,
    }
    if tp2_distance is not None:
        levels["take_profit_2"] = (
            anchor + tp2_distance if action.get("side") == "buy" else anchor - tp2_distance
        )

    if action.get("type") == "place_market_order":
        return {**action, **levels}
    return {**action, "entry_price": anchor, **levels}


def resolve_account_entry_price(
    side: str,
    trigger: str | None,
    current_price: float,
    offset: float,
    fallback: float,
) -> float:
    """镜像 resolveAccountEntryPrice:按 trigger 方向推导挂单入场价。"""
    if offset <= 0 or trigger in ("market", "none", None):
        return current_price
    if side == "buy":
        return current_price - offset if trigger == "pullback" else current_price + offset
    return current_price + offset if trigger == "pullback" else current_price - offset


class ComprehensiveAnalystService:
    """镜像 ComprehensiveAnalystService:结构化重试 + 交易决策 + 账户动作。"""

    def __init__(
        self,
        client: LlmClient,
        app_config: dict[str, Any] | None = None,
        trade_client: LlmClient | None = None,
        trade_model: str = "deepseek-v4-flash-0731",
    ) -> None:
        self.client = client
        self.app_config = app_config or {}
        self.trade_model = trade_model
        self.structure_cache = StructureCache()
        if trade_client is not None:
            self.trade_client = trade_client
        else:
            from backend.agents.agents._support import LlmClientService

            self.trade_client = LlmClientService(model=trade_model)

    async def retry_with_structured_output(
        self,
        system_blocks: list[SystemBlock],
        user_layers: list[UserLayer],
        symbol: str,
    ) -> JSONDict | None:
        """镜像 retryWithStructuredOutput:强制 tool_use 结构化重试(Phase 4.2)。"""
        logger = get_logger()
        try:
            result = await self.client.stream_layered(
                system_blocks,
                [
                    *user_layers,
                    UserLayer(
                        "Your previous output could not be parsed. Re-submit the SAME analysis by calling the "
                        "`submit_comprehensive_analysis` tool with every field filled in. Do not change your "
                        "conclusions — only re-encode them as structured tool input.",
                        cacheable=False,
                    ),
                ],
                {
                    "tools": COMPREHENSIVE_ANALYSIS_TOOLS,
                    "toolChoice": {"type": "tool", "name": "submit_comprehensive_analysis"},
                },
            )
            tool_use = result.tool_use
            if tool_use is None:
                logger.warn({"symbol": symbol}, "comprehensiveAnalysis: structured retry returned no tool_use")
                return None
            parsed = validate_comprehensive_data(tool_use.input)
            if parsed is None:
                logger.warn(
                    {"symbol": symbol},
                    "comprehensiveAnalysis: structured retry tool input failed schema validation",
                )
                return None
            logger.info({"symbol": symbol}, "comprehensiveAnalysis: structured retry recovered a valid result")
            if not parsed.get("harmonic"):
                parsed["harmonic"] = build_harmonic_from_context(None)
            return normalize_comprehensive(parsed)
        except Exception as err:
            logger.warn(
                {"symbol": symbol, "err": str(err)},
                "comprehensiveAnalysis: structured retry call failed",
            )
            return None

    async def decide_trade_action(
        self,
        arbitration: JSONDict,
        payload: JSONDict,
        profile: JSONDict,
    ) -> JSONDict | None:
        """镜像 decideTradeAction:第二阶段 legacy 工具决定下单动作。"""
        logger = get_logger()
        market = payload.get("market") or {}
        current_price = market.get("bid") or market.get("ask") or 0
        trade = arbitration.get("trade_recommendation")
        if not trade:
            return None

        if trade.get("direction") == "hold" and arbitration.get("action") == "hold":
            return {"type": "do_nothing", "reasoning": "arbitration: hold"}

        lines = [
            "## ARBITRATION DECISION (from first phase)",
            f"- Final Direction: {arbitration.get('final_direction')}",
            f"- Action: {arbitration.get('action')}",
            f"- Confidence: {arbitration.get('confidence')}",
            f"- Current Price: {current_price:.{profile['price_precision']}f}",
            "",
            "## TRADE RECOMMENDATION (from first phase markdown)",
            f"- Direction: {trade.get('direction')}",
            f"- Entry Price: {trade.get('entry_price')}",
            f"- Stop Loss: {trade.get('stop_loss')}",
            f"- Take Profit 1: {trade.get('take_profit_1')}",
            f"- Take Profit 2: {trade.get('take_profit_2') if trade.get('take_profit_2') is not None else 'N/A'}",
            f"- Risk/Reward: {trade.get('risk_reward_ratio')}",
            f"- Position Size: {trade.get('position_size_lots')}",
            f"- Rationale: {trade.get('rationale')}",
        ]
        summary = "\n".join(lines)

        try:
            result = await self.trade_client.stream_layered(
                [
                    SystemBlock(build_trade_action_decision_prompt(profile), cacheable=True),
                    SystemBlock(f"Instrument: {profile['name']} ({profile['symbol']})", cacheable=True),
                ],
                [UserLayer(summary, cacheable=False)],
                {"tools": TRADE_ACTION_TOOLS_LEGACY, "toolChoice": {"type": "auto"}},
            )

            if result.tool_use is None:
                logger.warn({"symbol": profile["symbol"]}, "trade_action_decision: no tool_use returned")
                return None

            logger.info(
                {
                    "symbol": profile["symbol"],
                    "strategy": self.trade_client.get_cache_strategy().get("type"),
                    "model": self.trade_client.get_model(),
                },
                "Phase 2 prompt cache stats",
            )

            return tool_use_to_trade_action_legacy(
                {"name": result.tool_use.name, "input": result.tool_use.input},
                current_price,
                profile,
            )
        except Exception as err:
            logger.warn(
                {"symbol": profile["symbol"], "err": str(err)},
                "trade_action_decision: tool_use call failed, falling back to markdown path",
            )
            return None

    async def run(
        self,
        payload: JSONDict,
        symbol: str,
        pending_signal: JSONDict | None = None,
        all_current_prices: dict[str, float] | None = None,
        options: JSONDict | None = None,
    ) -> JSONDict:
        """镜像 run:分层 prompt -> Markdown/JSON 双格式解析 -> 重试 -> 校验 -> 决策。"""
        logger = get_logger()
        options = options or {}
        profile = get_symbol_profile(symbol)
        market = payload.get("market") or {}
        current_price = market.get("bid") or market.get("ask") or 0

        system_blocks = [
            SystemBlock(build_common_system_prompt(), cacheable=True),
            SystemBlock(build_symbol_system_prompt(profile), cacheable=True),
        ]

        structure_result = build_static_context_prompt(payload, symbol, self.structure_cache)
        market_only = options.get("marketOnly") is True

        static_account_strategy_text = build_static_account_and_strategy_text(payload, market_only)

        user_layers = [
            UserLayer(static_account_strategy_text, cacheable=True),
            *[UserLayer(text, cacheable=True) for text in structure_result["blockTexts"]],
            UserLayer(
                build_realtime_data_prompt(
                    payload,
                    pending_signal,
                    symbol,
                    profile,
                    structure_result["harmonicVolatile"],
                    market_only,
                ),
                cacheable=False,
            ),
        ]

        async def invoke_non_streaming_fallback() -> str | None:
            try:
                fallback_raw = await self.client.invoke_layered(system_blocks, user_layers)
                if fallback_raw.strip() == "":
                    logger.warn(
                        {"symbol": symbol, "model": self.client.get_model()},
                        "comprehensiveAnalysis: non-streaming fallback also returned empty content",
                    )
                return fallback_raw
            except Exception as err:
                logger.error(
                    {"symbol": symbol, "err": str(err)},
                    "comprehensiveAnalysis: invokeLayered failed",
                )
                return None

        try:
            streamed = await self.client.stream_layered(system_blocks, user_layers)
            if streamed.content.strip():
                raw = streamed.content
            else:
                logger.warn({}, "comprehensiveAnalysis: streaming returned empty content, retrying non-streaming")
                fallback_raw = await invoke_non_streaming_fallback()
                if fallback_raw is None:
                    return build_fallback(current_price)
                raw = fallback_raw
        except Exception as err:
            logger.warn(
                {"symbol": symbol, "err": str(err)},
                "comprehensiveAnalysis: streamInvoke failed, falling back to non-streaming",
            )
            fallback_raw = await invoke_non_streaming_fallback()
            if fallback_raw is None:
                return build_fallback(current_price)
            raw = fallback_raw

        format_ = detect_format(raw)

        if format_ == "markdown":
            md_result = parse_markdown_response(raw, current_price, profile)
            if md_result:
                result_data = normalize_comprehensive(md_result)
            else:
                logger.warn({"symbol": symbol}, "comprehensiveAnalysis: Markdown parse failed, trying JSON fallback")
                parsed = safe_parse_response(
                    raw, validate_comprehensive_data, {"agent": "comprehensive", "symbol": symbol}
                )
                if not parsed:
                    logger.error(
                        {"symbol": symbol, "rawPrefix": raw[:200]},
                        "comprehensiveAnalysis: both Markdown and JSON parse failed",
                    )
                    retried = await self.retry_with_structured_output(system_blocks, user_layers, symbol)
                    if not retried:
                        return build_fallback(current_price)
                    result_data = retried
                else:
                    if not parsed.get("harmonic"):
                        parsed["harmonic"] = build_harmonic_from_context(None)
                    result_data = normalize_comprehensive(parsed)
        else:
            parsed = safe_parse_response(
                raw, validate_comprehensive_data, {"agent": "comprehensive", "symbol": symbol}
            )
            if not parsed:
                logger.error(
                    {"symbol": symbol, "rawPrefix": raw[:200]},
                    "comprehensiveAnalysis: both Markdown and JSON parse failed",
                )
                retried = await self.retry_with_structured_output(system_blocks, user_layers, symbol)
                if not retried:
                    return build_fallback(current_price)
                result_data = retried
            else:
                if not parsed.get("harmonic"):
                    parsed["harmonic"] = build_harmonic_from_context(None)
                result_data = normalize_comprehensive(parsed)

        result: JSONDict = result_data

        # ── POST-PARSE PROGRAMMATIC OVERRIDE: Harmonic detection ──
        result["harmonic"] = build_harmonic_from_context(payload.get("harmonic_context"))
        result["arbitration"]["harmonic_theory"] = build_harmonic_theory_from_context(payload.get("harmonic_context"))

        # ── POST-PARSE VALIDATION: Dynamic price sanity check ──
        price_fields = [
            ("risk", "suggestedSL", "risk.suggestedSL"),
            ("risk", "suggestedTP", "risk.suggestedTP"),
            ("harmonic", "stop_loss", "harmonic.stop_loss"),
            ("harmonic", "take_profit_1", "harmonic.take_profit_1"),
            ("harmonic", "take_profit_2", "harmonic.take_profit_2"),
        ]

        for section, key, label in price_fields:
            obj = result.get(section)
            if not isinstance(obj, dict):
                continue
            value = obj.get(key)
            if value in (None, 0) or not _is_number_like(value):
                continue

            rejected = False
            reason = ""
            if current_price > 0:
                dynamic_lo = current_price * 0.3
                dynamic_hi = current_price * 2.0
                if value < dynamic_lo or value > dynamic_hi:
                    rejected = True
                    reason = f"偏离当前价{current_price}合理区间({dynamic_lo:.2f}-{dynamic_hi:.2f})"

            if not rejected and current_price <= 0 and value <= 0:
                rejected = True
                reason = "无效价格(当前价和输出值均为0)"

            if rejected:
                logger.warn(
                    {"symbol": symbol, "field": label, "value": value, "currentPrice": current_price},
                    f"{label} {reason} — zeroing",
                )
                obj[key] = 0
                risk = result.get("risk")
                if isinstance(risk, dict):
                    warnings = risk.get("warnings") or []
                    warnings.append(f"AI{label} {value} {reason}，已拒绝")
                    risk["warnings"] = warnings
                continue

            if all_current_prices and len(all_current_prices) > 1:
                suspect = detect_cross_instrument_price(symbol, value, current_price, all_current_prices)
                if suspect:
                    suspect_price = all_current_prices[suspect]
                    logger.warn(
                        {
                            "symbol": symbol,
                            "field": label,
                            "value": value,
                            "suspectInstrument": suspect,
                            "suspectPrice": suspect_price,
                        },
                        f"{label} {value} matches {suspect} price range ({suspect_price}) — "
                        "cross-instrument contamination, zeroing",
                    )
                    obj[key] = 0
                    risk = result.get("risk")
                    if isinstance(risk, dict):
                        warnings = risk.get("warnings") or []
                        warnings.append(
                            f"AI{label} {value} 与{suspect}价格({suspect_price})高度吻合，疑似品种混淆，已拒绝"
                        )
                        risk["warnings"] = warnings

        # ── ARBITRATION VALIDATION ──
        arbitration = result.get("arbitration")
        trade = arbitration.get("trade_recommendation") if isinstance(arbitration, dict) else None
        if isinstance(arbitration, dict) and trade:
            trade_val = validate_trade_recommendation_business(trade, current_price, profile)
            if trade_val.warnings:
                logger.warn({"symbol": symbol, "warnings": trade_val.warnings}, "Arbitration trade_validation warnings")
            if not trade_val.valid and trade_val.fixed_trade is not None:
                logger.warn(
                    {"symbol": symbol},
                    "Arbitration trade invalid — applying fix (direction→hold if SL/TP zeroed)",
                )
                arbitration["trade_recommendation"] = trade_val.fixed_trade
                if trade_val.fixed_trade.get("direction") == "hold":
                    arbitration["final_direction"] = "hold"
                    arbitration["action"] = "hold"
                    arbitration["confidence"] = min(arbitration.get("confidence", 0), 20)

            arb_val = validate_arbitration_business(arbitration, current_price, profile)
            if not arb_val.valid and arb_val.fixed_arbitration is not None:
                logger.warn({"symbol": symbol}, "Arbitration result invalid — applying downgrade")
                result["arbitration"] = arb_val.fixed_arbitration

        if (result.get("arbitration") is not None) and options.get("skipTradeAction") is not True:
            trade_action = await self.decide_trade_action(result["arbitration"], payload, profile)
            if trade_action:
                result["tradeAction"] = trade_action
                logger.info(
                    {"symbol": symbol, "type": trade_action.get("type"), "side": trade_action.get("side")},
                    "tradeAction decided",
                )

        return result

    async def run_market_insight(
        self,
        bar_view: JSONDict,
        symbol: str | None = None,
        all_current_prices: dict[str, float] | None = None,
    ) -> JSONDict:
        """镜像 runMarketInsight:marketOnly + skipTradeAction 的市场洞察。"""
        if symbol is None:
            raw_source = bar_view.get("sourceSymbol")
            symbol = raw_source if isinstance(raw_source, str) else ""
        result = await self.run(
            bar_view["payload"],
            symbol,
            None,
            all_current_prices,
            {"marketOnly": True, "skipTradeAction": True},
        )
        benchmark_price = bar_view.get("benchmarkPrice") or current_price_from_payload(bar_view["payload"])
        return to_market_insight(result, benchmark_price, bar_view.get("atr") or atr_of(bar_view["payload"]))

    async def decide_account_actions(
        self,
        insight: JSONDict,
        account_views: list[JSONDict],
        benchmark_price: float,
        benchmark_atr: float,
        tolerance_atr: float = 0.25,
    ) -> dict[str, JSONDict]:
        """镜像 decideAccountActions:并发生成各账户的 TradeAction。"""
        results = await asyncio.gather(
            *[
                self.decide_account_action(
                    insight,
                    account_view,
                    benchmark_price,
                    benchmark_atr,
                    tolerance_atr,
                )
                for account_view in account_views
            ]
        )
        return {account_views[i]["symbol"]: results[i] for i in range(len(account_views))}

    async def decide_account_action(
        self,
        insight: JSONDict,
        account_view: JSONDict,
        benchmark_price: float,
        benchmark_atr: float,
        tolerance_atr: float,
    ) -> JSONDict:
        """镜像 decideAccountAction:单个账户的下单决策(含守卫与价位重建)。"""
        logger = get_logger()
        account_id = account_view.get("accountId")
        raw_account_symbol = account_view.get("symbol")
        account_symbol = raw_account_symbol if isinstance(raw_account_symbol, str) else ""

        if not is_symbol_loaded(account_view):
            return {"type": "do_nothing", "account_id": account_id, "reasoning": "account.symbol_not_loaded"}

        atr = benchmark_atr if benchmark_atr > 0 else (account_view.get("atr") or 0)
        if atr <= 0:
            logger.warn(
                {
                    "accountId": account_id,
                    "symbol": account_symbol,
                    "benchmarkAtr": benchmark_atr,
                    "accountAtr": account_view.get("atr"),
                },
                "accountDecision: ATR unavailable",
            )
            return {"type": "do_nothing", "account_id": account_id, "reasoning": "price.atr_unavailable"}

        deviation = abs(benchmark_price - (account_view.get("realtimePrice") or 0))
        if deviation > tolerance_atr * atr:
            logger.warn(
                {
                    "accountId": account_id,
                    "symbol": account_symbol,
                    "benchmarkPrice": benchmark_price,
                    "realtimePrice": account_view.get("realtimePrice"),
                    "atr": atr,
                    "toleranceAtr": tolerance_atr,
                },
                "accountDecision: price deviation too large",
            )
            return {"type": "do_nothing", "account_id": account_id, "reasoning": "price.deviation_too_large"}

        profile = get_symbol_profile(account_symbol)
        payload = account_view.get("payload") or {}
        current_price = current_price_from_payload(payload)
        prompt = build_account_decision_prompt(
            insight,
            account_view,
            benchmark_price,
            atr,
            deviation / atr if atr > 0 else 0,
            profile,
        )

        try:
            result = await self.trade_client.stream_layered(
                [
                    SystemBlock(build_account_decision_system_prompt(profile), cacheable=True),
                    SystemBlock(f"Instrument: {profile['name']} ({profile['symbol']})", cacheable=True),
                ],
                [UserLayer(prompt, cacheable=False)],
                {"tools": TRADE_ACTION_TOOLS, "toolChoice": {"type": "auto"}},
            )

            if result.tool_use is None:
                return {"type": "do_nothing", "account_id": account_id, "reasoning": "tool_use.missing"}

            action = tool_use_to_trade_action(
                {"name": result.tool_use.name, "input": result.tool_use.input},
                current_price,
                profile,
                account_symbol,
            )
            if action is None:
                return {"type": "do_nothing", "account_id": account_id, "reasoning": "tool_use.account_id_missing"}

            guarded = validate_trade_action_for_account(action, account_view)
            if not guarded["ok"]:
                return {"type": "do_nothing", "account_id": account_id, "reasoning": guarded["reason"]}

            if action.get("type") in ("place_market_order", "place_pending_order"):
                rebuilt = rebuild_account_order_from_insight(
                    action,
                    insight,
                    account_view.get("payload") or {},
                    float(account_view.get("atr") or atr),
                )
                return {**rebuilt, "account_id": account_id, "symbol": account_symbol}

            return action
        except Exception as err:
            logger.warn(
                {
                    "accountId": account_id,
                    "symbol": account_symbol,
                    "err": str(err),
                },
                "accountDecision: tool_use call failed",
            )
            return {"type": "do_nothing", "account_id": account_id, "reasoning": "tool_use.failed"}


def _is_number_like(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)
