"""Support/Resistance Analyst Agent(1:1 镜像 gold-bot apps/app-agent/src/agents/sr-analyst.ts)。

识别关键价位:
- build_system_prompt / build_prompt / extract_strategy
- parse_markdown_sr / parse_response(Markdown 优先,JSON 提取回退)
- SrAnalystService.run:解析失败返回空 S/R 兜底,并按当前价过滤非法价位
"""

from __future__ import annotations

import json
import re
from typing import Any

from backend.agents.agents._support import (
    LlmClient,
    clean_sr_levels,
    detect_format,
    extract_fields,
    extract_list_items,
    filter_valid_prices,
    find_psychological_levels,
    get_logger,
    get_string_field,
    get_symbol_profile,
    parse_sr_levels,
    safe_parse_response,
    select_indicator,
    split_sections,
    validate_sr_levels,
)

__all__ = [
    "SrAnalystService",
    "build_prompt",
    "build_system_prompt",
    "extract_strategy",
    "parse_markdown_sr",
    "parse_response",
]

JSONDict = dict[str, Any]


def build_system_prompt(profile: JSONDict) -> str:
    """镜像 buildSystemPrompt:支撑/阻力分析专家系统提示。"""
    return f"""You are a support/resistance analysis specialist for {profile['name']} ({profile['symbol']}).
Given market data and pre-computed S/R levels, produce a structured MARKDOWN analysis.

## SYMBOL CHARACTERISTICS
- Instrument: {profile['name']}
- Price precision: {profile['price_precision']} decimal places
- Typical price range: {profile['price_range_hint']}
- Current asset class: {profile['asset_class']}

## CRITICAL OUTPUT RULES
1. Output structured MARKDOWN text using ## SECTION headers and - Key: Value format.
2. NEVER wrap output in ```code blocks```. Do NOT output JSON.
3. ALL numeric fields MUST be valid numbers (never null, undefined, or empty string).
4. For text fields, output bilingual: Chinese first, English in parentheses.
5. Support/Resistance levels use pipe-delimited format: price | type | strength | timeframe | touches

## PRICE VALIDATION
- price MUST be extracted from provided Fibonacci/Pivot/Psychological levels.
- If a level is not available, omit it entirely.
- NEVER guess or hallucinate a price value.
- All prices MUST be within ±50% of the current market price for THIS instrument.
- NEVER output prices from a different instrument (e.g. do NOT output gold prices for GBPJPY).

## REQUIRED OUTPUT MARKDOWN FORMAT

## SUPPORT LEVELS
- <price> | support | strong|moderate|weak | <timeframe e.g. H1> | <touches 1-10>
- <price> | support | strong|moderate|weak | <timeframe e.g. H4> | <touches 1-10>

## RESISTANCE LEVELS
- <price> | resistance | strong|moderate|weak | <timeframe e.g. H1> | <touches 1-10>
- <price> | resistance | strong|moderate|weak | <timeframe e.g. H4> | <touches 1-10>

## SUMMARY
- Recommendation: <bilingual string>
- Rationale: <bilingual string>"""


def build_prompt(payload: JSONDict, profile: JSONDict, strategy: str | None = None) -> str:
    """镜像 buildPrompt:统一使用 H1/H4 分析周期。"""
    market = payload.get("market") or {}
    indicators = payload.get("indicators") or {}

    tf1 = select_indicator(indicators, "H1", "h1")
    tf2 = select_indicator(indicators, "H4", "h4")
    tf1_label = "H1"
    tf2_label = "H4"

    fib_levels = {
        "fib_236": tf1.get("fib_236"),
        "fib_382": tf1.get("fib_382"),
        "fib_500": tf1.get("fib_500"),
        "fib_618": tf1.get("fib_618"),
        "fib_786": tf1.get("fib_786"),
    }
    pivot_levels = {
        "pp": tf1.get("pp"),
        "r1": tf1.get("r1"),
        "s1": tf1.get("s1"),
    }

    current_price = market.get("bid") or market.get("ask") or 0
    psych_levels = find_psychological_levels(current_price, 100)

    return f"""Analyze {profile['name']} ({market.get('symbol')}) support/resistance (strategy: {strategy or 'default'}):

## SYMBOL CONTEXT
- Instrument: {profile['name']} ({profile['symbol']})
- Current Price: {current_price:.{profile['price_precision']}f}
- Price Range: {current_price * 0.5:.{profile['price_precision']}f} - {current_price * 1.5:.{profile['price_precision']}f}

{tf1_label} Levels:
  High={tf1.get('high')}, Low={tf1.get('low')}, Open={tf1.get('open')}, Close={tf1.get('close')}
  EMA20={tf1.get('ema20')}, EMA50={tf1.get('ema50')}, EMA200={tf1.get('ema200')}
  BB Upper={tf1.get('bb_upper')} Lower={tf1.get('bb_lower')}

{tf2_label} Levels:
  High={tf2.get('high')}, Low={tf2.get('low')}, Open={tf2.get('open')}, Close={tf2.get('close')}
  EMA20={tf2.get('ema20')}, EMA50={tf2.get('ema50')}
  BB Upper={tf2.get('bb_upper')} Lower={tf2.get('bb_lower')}

Fibonacci Levels: {json.dumps(fib_levels, ensure_ascii=False)}

Pivot Points: {json.dumps(pivot_levels, ensure_ascii=False)}

Psychological Levels (nearby): {json.dumps(psych_levels[:10], ensure_ascii=False)}

Respond with structured MARKDOWN using ## SUPPORT LEVELS, ## RESISTANCE LEVELS, and ## SUMMARY sections.
Use pipe-delimited format for levels: price | type | strength | timeframe | touches"""  # noqa: E501


def extract_strategy(payload: JSONDict) -> str | None:
    """镜像 extractStrategy:优先持仓策略,其次 strategy_mapping 首个值。"""
    positions = payload.get("positions") or []
    if len(positions) > 0:
        return positions[0].get("strategy")
    strategy_mapping = payload.get("strategy_mapping") or {}
    if isinstance(strategy_mapping, dict) and len(strategy_mapping) > 0:
        return next(iter(strategy_mapping.values()), None)
    return None


def parse_markdown_sr(raw: str) -> JSONDict | None:
    """镜像 parseMarkdownSR:提取 SUPPORT/RESISTANCE/SUMMARY 节。"""
    sections = split_sections(raw)
    if len(sections) < 1:
        return None

    support_section = sections.get("support levels") or sections.get("support_levels") or ""
    resistance_section = sections.get("resistance levels") or sections.get("resistance_levels") or ""
    summary_section = sections.get("summary") or ""

    support_lines = [line for line in extract_list_items(support_section) if "|" in line]
    resistance_lines = [line for line in extract_list_items(resistance_section) if "|" in line]

    summary_fields = extract_fields(summary_section)

    return {
        "support_levels": parse_sr_levels(support_lines, "support"),
        "resistance_levels": parse_sr_levels(resistance_lines, "resistance"),
        "recommendation": get_string_field(summary_fields, "recommendation", ""),
        "rationale": get_string_field(summary_fields, "rationale", ""),
    }


def parse_response(raw: str) -> JSONDict | None:
    """镜像 parseResponse:Markdown 优先,JSON 提取 + cleanSRLevels 回退。"""
    format_ = detect_format(raw)
    if format_ == "markdown":
        md_result = parse_markdown_sr(raw)
        if md_result and (len(md_result["support_levels"]) > 0 or len(md_result["resistance_levels"]) > 0):
            return md_result

    json_match = re.search(r"\{[\s\S]*\}", raw)
    if not json_match:
        get_logger().warn({"raw": raw[:200]}, "srAnalyst: no JSON found")
        return None
    try:
        parsed = json.loads(json_match.group(0))
    except (TypeError, ValueError) as err:
        get_logger().warn(
            {"raw": raw[:200], "err": str(err)},
            "srAnalyst: JSON.parse failed",
        )
        return None
    cleaned = clean_sr_levels(parsed)
    return safe_parse_response(json.dumps(cleaned), validate_sr_levels, {"agent": "sr"})


class SrAnalystService:
    """镜像 SrAnalystService:单次 streamInvoke + 空 S/R 兜底 + 价格过滤。"""

    def __init__(self, client: LlmClient) -> None:
        self.client = client

    async def run(self, payload: JSONDict, symbol: str) -> JSONDict:
        logger = get_logger()
        profile = get_symbol_profile(symbol)
        system_prompt = build_system_prompt(profile)
        strategy = extract_strategy(payload)
        prompt = build_prompt(payload, profile, strategy)
        raw = await self.client.stream_invoke(prompt, system_prompt)
        result = parse_response(raw)

        if not result:
            logger.error({"symbol": symbol}, "S/R analysis parse failed — returning empty fallback")
            return {
                "support_levels": [],
                "resistance_levels": [],
                "recommendation": "",
                "rationale": "S/R 解析失败 (S/R analysis parse failed)",
            }

        current_price = (payload.get("market") or {}).get("bid") or (payload.get("market") or {}).get("ask") or 0
        if current_price > 0:
            result["support_levels"] = filter_valid_prices(result["support_levels"], current_price, profile, "support")
            result["resistance_levels"] = filter_valid_prices(
                result["resistance_levels"], current_price, profile, "resistance"
            )

        logger.info(
            {
                "symbol": symbol,
                "support": len(result["support_levels"]),
                "resistance": len(result["resistance_levels"]),
                "strategy": strategy,
            },
            "S/R analysis complete",
        )
        return result
