"""Technical Analyst Agent(1:1 镜像 gold-bot apps/app-agent/src/agents/technical-analyst.ts)。

分析市场数据并返回结构化 TechnicalAnalysis:
- build_system_prompt / build_semi_static_data / build_dynamic_data / build_prompt(已废弃)
- normalize_enums:LLM 枚举值关键字归一化 + confidence/touches 字符串解析
- parse_response:JSON 提取 -> normalize -> cleanSRLevels -> schema 校验
- TechnicalAnalystService.run:分层 prompt + stream 主模型 + invoke 回退;
  全部解析失败返回 neutral 兜底(confidence=10)
"""

from __future__ import annotations

import json
import re
from typing import Any

from backend.agents.agents._support import (
    LlmClient,
    SystemBlock,
    UserLayer,
    clean_sr_levels,
    filter_valid_prices,
    find_psychological_levels,
    get_logger,
    get_symbol_profile,
    safe_parse_response,
    select_indicator,
    stable_stringify,
    validate_technical_analysis,
)

__all__ = [
    "NEUTRAL_FALLBACK",
    "TechnicalAnalystService",
    "build_dynamic_data",
    "build_prompt",
    "build_semi_static_data",
    "build_system_prompt",
    "normalize_enums",
    "parse_response",
]

JSONDict = dict[str, Any]


def build_system_prompt(profile: JSONDict) -> str:
    """镜像 buildSystemPrompt:系统提示(含品种特征与输出规则)。"""
    return f"""You are a technical analysis specialist for {profile['name']} ({profile['symbol']}).
Given raw market data and computed indicators, produce a JSON TechnicalAnalysis object.

## SYMBOL CHARACTERISTICS
- Instrument: {profile['name']}
- Price precision: {profile['price_precision']} decimal places
- Typical price range: {profile['price_range_hint']}
- Volatility: {profile['volatility_level']}
- 1 pip = {profile['pip_value']}
- Suggested SL: {profile['sl_atr_multiplier']}× ATR
- Suggested TP: {profile['tp_atr_multiplier']}× ATR
- Volume data reliable: {profile['volume_reliable']}

## CRITICAL: PRICE VALIDATION
All prices in your response MUST be within ±50% of the current market price.
If current price is ~214, your S/R levels MUST be between 107 and 321.
If current price is ~3300, your S/R levels MUST be between 1650 and 4950.
NEVER output prices from a different instrument. Check your numbers before responding.

## CRITICAL OUTPUT RULES
1. Output ONLY valid JSON. No markdown, no code blocks, no explanation outside JSON.
2. ALL numeric fields MUST be valid numbers (never null, undefined, or empty string).
3. For text fields, output bilingual: Chinese first, English in parentheses. Example: "下跌趋势 (Downtrend)".
4. ALL enum values MUST be EXACTLY lowercase: "bullish"/"bearish"/"neutral", "trending"/"ranging"/"breakout"/"reversal"/"consolidation", "hold"/"close"/"partial_close"/"trail_stop"/"none".
5. DO NOT invent new enum values. Use ONLY the exact values specified below.

## MULTI-TIMEFRAME WEIGHTING RULES
When multiple timeframes show conflicting signals, prioritize by weight:
- H1 (35%): Primary trend ADX, MACD, RSI alignment — DOMINANT timeframe
- M30 (35%): Primary trend confirmation, RSI divergence detection — DOMINANT timeframe
- H4 (15%): Medium-term trend validation (NOT primary direction source)
- M15 (15%): Entry timing signals (超卖/超买 NOT override H1/M30 trend)

**CRITICAL RULES:**
1. M15 RSI < 30 (oversold) does NOT reverse a H1 bearish trend (ADX > 35)
2. M15 超卖只是短期反弹风险提示，不能改变 H1/M30 主导的趋势方向
3. When H1 + M30 align (70% combined weight), they DOMINATE the direction
4. Confidence boost when H1 + M30 align: +10%
5. Confidence penalty when M15 contradicts H1: -5% (not direction reversal)

**Example scenarios:**
- H1 ADX 43 bearish + M30 RSI 51 neutral + M15 RSI 30 oversold → Direction: BEARISH (hold), not bullish
- H1 ADX 38 bullish + M30 RSI 55 bullish + M15 RSI 70 overbought → Direction: BULLISH, confidence -5%

## STRICT JSON SCHEMA (use EXACT lowercase enum values)
{{
  "bias": "bullish" | "bearish" | "neutral",
  "confidence": <number 0-100>,
  "phase": "trending" | "ranging" | "breakout" | "reversal" | "consolidation",
  "indicators_summary": "<string: bilingual summary>",
  "support_levels": [
    {{
      "price": <REQUIRED: valid number, NEVER null/undefined>,
      "type": "support",
      "strength": "strong" | "moderate" | "weak",
      "timeframe": "<string e.g. H1>",
      "touches": <number 1-10>
    }}
  ],
  "resistance_levels": [
    {{
      "price": <REQUIRED: valid number, NEVER null/undefined>,
      "type": "resistance",
      "strength": "strong" | "moderate" | "weak",
      "timeframe": "<string e.g. H4>",
      "touches": <number 1-10>
    }}
  ],
  "recommendation": "hold" | "close" | "partial_close" | "trail_stop" | "none",
  "rationale": "<string: bilingual reasoning>"
}}

## S/R LEVEL CONSTRAINTS
1. Output at most 3 support + 3 resistance (total ≤ 6).
2. Each level's price MUST be a concrete number from the provided data.
3. Levels must be at least 2× ATR apart.
4. Prioritize: Pivot Points > Fibonacci > Psychological levels.
5. If no valid level found, return empty array [] — NEVER include level with null price.
6. All prices MUST match the instrument being analyzed — do NOT output prices from a different instrument.

## ANCHOR REFERENCE SYSTEM
The following anchors will be provided in subsequent messages.
You MUST reference them directly without recalculating.

- {{{{FIB_LEVELS}}}}: Pre-computed Fibonacci retracement levels
  - Contains: fib_236, fib_382, fib_500, fib_618, fib_786
  - Use for support/resistance level identification
- {{{{PIVOT_LEVELS}}}}: Pre-computed pivot points
  - Contains: pp, r1, s1 (and optional r2, s2)
  - Use as primary S/R reference levels
- {{{{MTF_INDICATORS}}}}: Multi-timeframe raw indicator values
  - Contains raw OHLC, EMA, RSI, ADX, ATR, MACD, BB, Stoch per timeframe
  - Use for bias calculation, phase detection, and rationale"""  # noqa: E501


def build_semi_static_data(
    indicators: JSONDict,
    profile: JSONDict,
) -> str:
    """镜像 buildSemiStaticData:半静态技术结构(锚点映射,bar 更新时变化)。"""
    h1 = select_indicator(indicators, "H1", "h1")
    h4 = select_indicator(indicators, "H4", "h4")
    m15 = select_indicator(indicators, "M15", "m15")
    m30 = select_indicator(indicators, "M30", "m30")

    fib_levels = {
        "fib_236": h1.get("fib_236"),
        "fib_382": h1.get("fib_382"),
        "fib_500": h1.get("fib_500"),
        "fib_618": h1.get("fib_618"),
        "fib_786": h1.get("fib_786"),
    }
    pivot_levels = {
        "pp": h1.get("pp"),
        "r1": h1.get("r1"),
        "s1": h1.get("s1"),
    }

    return f"""## SEMI-STATIC TECHNICAL STRUCTURES (Anchor mapping — changes on bar update)

### FIB_LEVELS
{stable_stringify(fib_levels)}

### PIVOT_LEVELS
{stable_stringify(pivot_levels)}

### MTF_INDICATORS
H1: close={h1.get('close')}, high={h1.get('high')}, low={h1.get('low')}, EMA20={h1.get('ema20')}, EMA50={h1.get('ema50')}, EMA200={h1.get('ema200')}
H4: close={h4.get('close')}, high={h4.get('high')}, low={h4.get('low')}, EMA20={h4.get('ema20')}, EMA50={h4.get('ema50')}, EMA200={h4.get('ema200')}
M15: close={m15.get('close')}, EMA20={m15.get('ema20')}, EMA50={m15.get('ema50')}
M30: close={m30.get('close')}, EMA20={m30.get('ema20')}, EMA50={m30.get('ema50')}"""  # noqa: E501


def build_dynamic_data(
    payload: JSONDict,
    profile: JSONDict,
) -> str:
    """镜像 buildDynamicData:实时数据层(每次请求变化,不缓存)。"""
    market = payload.get("market") or {}
    indicators = payload.get("indicators") or {}
    positions = payload.get("positions") or []
    h1 = select_indicator(indicators, "H1", "h1")
    h4 = select_indicator(indicators, "H4", "h4")
    m15 = select_indicator(indicators, "M15", "m15")
    m30 = select_indicator(indicators, "M30", "m30")
    current_price = market.get("bid") or market.get("ask") or 0
    psych_levels = find_psychological_levels(current_price, 100)

    return f"""## REAL-TIME DATA (changes every request — do not cache)

Symbol: {profile['name']} ({market.get('symbol')})
Current Price: {current_price:.{profile['price_precision']}f}
Price: bid={market.get('bid'):.{profile['price_precision']}f}, ask={market.get('ask'):.{profile['price_precision']}f}, spread={market.get('spread')}

Live Indicators:
H1 RSI={h1.get('rsi')}, ADX={h1.get('adx')}, ATR={h1.get('atr')}, MACD={h1.get('macd')}/Signal={h1.get('macd_signal')}/Hist={h1.get('macd_hist')}
H4 RSI={h4.get('rsi')}, ADX={h4.get('adx')}, ATR={h4.get('atr')}, MACD={h4.get('macd')}/Signal={h4.get('macd_signal')}/Hist={h4.get('macd_hist')}
M15 RSI={m15.get('rsi')}, ADX={m15.get('adx')}, ATR={m15.get('atr')}
M30 RSI={m30.get('rsi')}, ADX={m30.get('adx')}, ATR={m30.get('atr')}

Psychological Levels: {stable_stringify(psych_levels[:10])}
Open Positions: {stable_stringify(positions)}

Analyze and return a JSON TechnicalAnalysis."""  # noqa: E501


def build_prompt(payload: JSONDict, profile: JSONDict) -> str:
    """镜像 buildPrompt(已废弃):单块 prompt,simulating 缓存友好拆分前的旧路径。"""
    market = payload.get("market") or {}
    indicators = payload.get("indicators") or {}
    positions = payload.get("positions") or []
    h1 = select_indicator(indicators, "H1", "h1")
    h4 = select_indicator(indicators, "H4", "h4")
    m15 = select_indicator(indicators, "M15", "m15")
    m30 = select_indicator(indicators, "M30", "m30")
    fib_levels = {
        "fib_236": h1.get("fib_236"),
        "fib_382": h1.get("fib_382"),
        "fib_500": h1.get("fib_500"),
        "fib_618": h1.get("fib_618"),
        "fib_786": h1.get("fib_786"),
    }
    pivot_levels = {
        "pp": h1.get("pp"),
        "r1": h1.get("r1"),
        "s1": h1.get("s1"),
    }
    psych_levels = find_psychological_levels(market.get("bid") or market.get("ask") or 0, 100)
    current_price = market.get("bid") or market.get("ask") or 0
    precision = profile["price_precision"]

    return f"""Analyze {profile['name']} ({market.get('symbol')}) technical data:

## SYMBOL CONTEXT
- Instrument: {profile['name']} ({profile['symbol']})
- Current Price: {current_price:.{precision}f}
- Price Range: {current_price * 0.5:.{precision}f} - {current_price * 1.5:.{precision}f}
- Volatility: {profile['volatility_level']}
- 1 pip = {profile['pip_value']}

## MARKET DATA
Price: bid={market.get('bid'):.{precision}f}, ask={market.get('ask'):.{precision}f}, spread={market.get('spread')}

H1 Indicators:
  close={h1.get('close')}, open={h1.get('open')}, high={h1.get('high')}, low={h1.get('low')}
  EMA20={h1.get('ema20')}, EMA50={h1.get('ema50')}, EMA200={h1.get('ema200')}
  RSI={h1.get('rsi')}, ADX={h1.get('adx')}, ATR={h1.get('atr')}
  MACD={h1.get('macd')} / Signal={h1.get('macd_signal')} / Hist={h1.get('macd_hist')}
  BB: Upper={h1.get('bb_upper')} Middle={h1.get('bb_middle')} Lower={h1.get('bb_lower')}
  Stoch: K={h1.get('stoch_k')} D={h1.get('stoch_d')}

H4 Indicators:
  close={h4.get('close')}, open={h4.get('open')}, high={h4.get('high')}, low={h4.get('low')}
  EMA20={h4.get('ema20')}, EMA50={h4.get('ema50')}, EMA200={h4.get('ema200')}
  RSI={h4.get('rsi')}, ADX={h4.get('adx')}, ATR={h4.get('atr')}
  MACD={h4.get('macd')} / Signal={h4.get('macd_signal')} / Hist={h4.get('macd_hist')}

M15 Indicators:
  close={m15.get('close')}, open={m15.get('open')}, high={m15.get('high')}, low={m15.get('low')}
  EMA20={m15.get('ema20')}, EMA50={m15.get('ema50')}
  RSI={m15.get('rsi')}, ADX={m15.get('adx')}, ATR={m15.get('atr')}

M30 Indicators:
  close={m30.get('close')}, open={m30.get('open')}, high={m30.get('high')}, low={m30.get('low')}
  EMA20={m30.get('ema20')}, EMA50={m30.get('ema50')}
  RSI={m30.get('rsi')}, ADX={m30.get('adx')}, ATR={m30.get('atr')}

Fibonacci Levels: {stable_stringify(fib_levels)}

Pivot Points: {stable_stringify(pivot_levels)}

Psychological Levels (nearby): {stable_stringify(psych_levels[:10])}

Open positions: {stable_stringify(positions)}

Respond with a JSON TechnicalAnalysis object with fields: bias, confidence, phase,
indicators_summary, support_levels, resistance_levels, recommendation, rationale."""


def _parse_int(raw: str) -> int | None:
    match = re.search(r"-?\d+", raw)
    if not match:
        return None
    try:
        return int(match.group(0))
    except ValueError:
        return None


def normalize_enums(data: Any) -> Any:
    """镜像 normalizeEnums:关键字匹配(glm-5 级模型的兜底保险)。"""
    if not isinstance(data, dict):
        return data
    obj = data

    if isinstance(obj.get("confidence"), str):
        parsed = _parse_int(obj["confidence"])
        obj["confidence"] = 50 if parsed is None else max(0, min(100, parsed))

    if isinstance(obj.get("bias"), str):
        lower_bias = obj["bias"].lower()
        if any(token in lower_bias for token in ("bear", "down", "sell")):
            obj["bias"] = "bearish"
        elif any(token in lower_bias for token in ("bull", "up", "buy")):
            obj["bias"] = "bullish"
        else:
            obj["bias"] = "neutral"

    if isinstance(obj.get("phase"), str):
        lower_phase = obj["phase"].lower()
        if any(token in lower_phase for token in ("breakout", "break")):
            obj["phase"] = "breakout"
        elif any(token in lower_phase for token in ("reversal", "turn", "change")):
            obj["phase"] = "reversal"
        elif any(token in lower_phase for token in ("consolidat", "range", "sideways", "flat")):
            obj["phase"] = "consolidation"
        elif any(token in lower_phase for token in ("trend", "uptrend", "downtrend")):
            obj["phase"] = "trending"
        else:
            obj["phase"] = "consolidation"

    if isinstance(obj.get("recommendation"), str):
        lower_rec = obj["recommendation"].lower()
        if any(token in lower_rec for token in ("close", "exit", "sell")):
            obj["recommendation"] = "close"
        elif any(token in lower_rec for token in ("partial", "half")):
            obj["recommendation"] = "partial_close"
        elif any(token in lower_rec for token in ("trail", "stop")):
            obj["recommendation"] = "trail_stop"
        elif any(token in lower_rec for token in ("hold", "wait", "buy", "keep")):
            obj["recommendation"] = "hold"
        else:
            obj["recommendation"] = "none"

    for key in ("support_levels", "resistance_levels"):
        if isinstance(obj.get(key), list):
            for level in obj[key]:
                if isinstance(level, dict) and isinstance(level.get("strength"), str):
                    lower_strength = level["strength"].lower()
                    if any(token in lower_strength for token in ("strong", "major")):
                        level["strength"] = "strong"
                    elif any(token in lower_strength for token in ("moderate", "medium")):
                        level["strength"] = "moderate"
                    else:
                        level["strength"] = "weak"
                if isinstance(level, dict) and isinstance(level.get("touches"), str):
                    parsed = _parse_int(level["touches"])
                    level["touches"] = 1 if parsed is None else max(1, min(10, parsed))

    return obj


def parse_response(raw: str) -> JSONDict | None:
    """镜像 parseResponse:JSON 提取 -> normalizeEnums -> cleanSRLevels -> 校验。"""
    json_match = re.search(r"\{[\s\S]*\}", raw)
    if not json_match:
        get_logger().warn({"raw": raw[:200]}, "technicalAnalyst: no JSON found")
        return None
    try:
        parsed = json.loads(json_match.group(0))
    except (TypeError, ValueError) as err:
        get_logger().warn(
            {"raw": raw[:200], "err": str(err)},
            "technicalAnalyst: JSON.parse failed",
        )
        return None
    normalized = normalize_enums(parsed)
    cleaned = clean_sr_levels(normalized)
    return safe_parse_response(json.dumps(cleaned), validate_technical_analysis, {"agent": "technical"})


NEUTRAL_FALLBACK: JSONDict = {
    "bias": "neutral",
    "confidence": 10,
    "phase": "consolidation",
    "indicators_summary": "技术分析不可用 (Technical analysis unavailable)",
    "support_levels": [],
    "resistance_levels": [],
    "recommendation": "none",
    "rationale": "LLM API 超时或响应解析失败，无法进行有效分析 (LLM API timeout or parse failed)",
}


class TechnicalAnalystService:
    """镜像 TechnicalAnalystService:分层 prompt + 主模型/回退模型 + neutral 兜底。"""

    def __init__(self, client: LlmClient) -> None:
        self.client = client

    async def run(self, payload: JSONDict, symbol: str) -> JSONDict:
        logger = get_logger()
        profile = get_symbol_profile(symbol)
        system_prompt = build_system_prompt(profile)

        semi_static_prompt = build_semi_static_data(payload.get("indicators") or {}, profile)
        dynamic_prompt = build_dynamic_data(payload, profile)

        raw: str | None = None
        system_blocks = [SystemBlock(text=system_prompt, cacheable=True)]
        user_layers = [
            UserLayer(text=semi_static_prompt, cacheable=True),
            UserLayer(text=dynamic_prompt, cacheable=False),
        ]
        try:
            streamed = await self.client.stream_layered(system_blocks, user_layers)
            raw = streamed.content
        except Exception as err:
            logger.warn({"err": str(err), "symbol": symbol}, "Primary LLM model failed, trying fallback model")
            try:
                raw = await self.client.invoke_layered(system_blocks, user_layers)
                logger.info({"symbol": symbol}, "Fallback model succeeded")
            except Exception as fallback_err:
                logger.error({"err": str(fallback_err), "symbol": symbol}, "Fallback model also failed")
                raw = None

        result = parse_response(raw) if raw else None

        if not result:
            logger.error({"symbol": symbol}, "Technical analysis parse failed — returning neutral fallback")
            return NEUTRAL_FALLBACK

        current_price = (payload.get("market") or {}).get("bid") or (payload.get("market") or {}).get("ask") or 0
        if current_price > 0:
            result["support_levels"] = filter_valid_prices(result["support_levels"], current_price, profile, "support")
            result["resistance_levels"] = filter_valid_prices(
                result["resistance_levels"], current_price, profile, "resistance"
            )

        logger.info(
            {"symbol": symbol, "bias": result["bias"], "confidence": result["confidence"], "phase": result["phase"]},
            "Technical analysis complete",
        )
        return result
