"""Risk Manager Agent(1:1 镜像 gold-bot apps/app-agent/src/agents/risk-manager.ts)。

给定技术分析与账户/行情数据,输出 RiskAssessment:
- build_system_prompt / build_semi_static_data / build_dynamic_data / build_prompt
- parse_markdown_risk / parse_response(Markdown 优先,JSON 回退)
- RiskManagerService.run:suggestedSL/suggestedTP 超出品种合理价格区间时清零并记录告警
"""

from __future__ import annotations

from typing import Any

from backend.agents.agents._support import (
    LlmClient,
    SystemBlock,
    UserLayer,
    detect_format,
    extract_fields,
    extract_list_items,
    extract_warnings,
    get_boolean_field,
    get_enum_field,
    get_logger,
    get_number_field,
    get_symbol_profile,
    safe_parse_response,
    select_indicator,
    split_sections,
    stable_stringify,
    validate_risk_assessment,
)

__all__ = [
    "RiskManagerService",
    "build_dynamic_data",
    "build_prompt",
    "build_semi_static_data",
    "build_system_prompt",
    "parse_markdown_risk",
    "parse_response",
]

JSONDict = dict[str, Any]


def build_system_prompt(profile: JSONDict) -> str:
    """镜像 buildSystemPrompt:风控专家系统提示。"""
    return f"""You are a risk management specialist for all forex/commodity instruments.
Given technical analysis, account data and market data, produce a structured MARKDOWN risk assessment.

## SYMBOL CHARACTERISTICS
- Instrument: {profile['name']}
- Symbol code: {profile['symbol']}
- Price precision: {profile['price_precision']} decimal places
- Typical price range: {profile['price_range_hint']}
- Volatility: {profile['volatility_level']}
- 1 pip = {profile['pip_value']}
- Suggested SL: {profile['sl_atr_multiplier']}× ATR
- Suggested TP: {profile['tp_atr_multiplier']}× ATR

## CRITICAL OUTPUT RULES
1. Output structured MARKDOWN text using ## RISK section header and - Key: Value format.
2. NEVER wrap output in ```code blocks```. Do NOT output JSON.
3. ALL numeric fields MUST be valid numbers (never null, undefined, or empty string).
4. For text fields, output bilingual: Chinese first, English in parentheses.
5. **CRITICAL: suggestedSL and suggestedTP MUST be absolute price values, NOT relative descriptions like "stop loss 50 points" or "take profit 100 points".**
6. **suggestedSL/suggestedTP must be the ACTUAL price level on the chart, not a distance or point count from entry.**

## INSTRUMENT PRICE ACCURACY ENFORCEMENT
CRITICAL: Before outputting ANY price value, you MUST verify it matches the ACTUAL instrument you are analyzing.
- The symbol being analyzed is: {profile['symbol']} — typical price range is {profile['price_range_hint']}
- ATR for this instrument is typically {stable_stringify(profile['typical_atr_range'].get('H1'))} (H1 timeframe)
- STOP and THINK: Does your suggestedSL/suggestedTP match the expected magnitude for THIS instrument?
- Double-check: If {profile['symbol']} trades around {profile['price_range_hint']}, a suggestedSL of 250 would be IMPOSSIBLE for this instrument
- WRONG examples: suggestingSL=250 for US100 (should be ~15000-25000), suggestingSL=2000 for GBPJPY (should be ~100-250), etc.
- CORRECT: suggestedSL must be within the SAME ORDER OF MAGNITUDE as the current price for {profile['symbol']}
- If you catch yourself producing a price that doesn't match the instrument's typical range, CORRECT IT immediately
- This is a HARD REQUIREMENT — mistakes in price magnitude are unacceptable

## REQUIRED OUTPUT MARKDOWN FORMAT

## RISK
- Risk Level: low | medium | high | extreme
- Max Position Size: <number lots>
- Suggested SL: <absolute price number>
- Suggested TP: <absolute price number>
- Warnings: <semicolon-separated bilingual strings>
- Add On: true | false

## RISK CALCULATION GUIDELINES
- suggestedSL should be placed below the nearest support (for long) or above nearest resistance (for short), with an ATR buffer
- CRITICAL: For a LONG position, suggestedSL MUST be BELOW the current price. For a SHORT position, suggestedSL MUST be ABOVE the current price.
- suggestedTP should target the next significant resistance (for long) or support (for short)
- maxPositionSize should ensure that a stop-loss hit does not exceed 2% of account equity
- Risk/reward ratio should be at least 1:2 for the suggested SL/TP
- All prices MUST be within ±50% of the current market price for THIS instrument
- NEVER output prices from a different instrument
- ALWAYS verify: suggestedSL is in the SAME ORDER OF MAGNITUDE as {profile['symbol']}'s typical price ({profile['price_range_hint']})
- **ABSOLUTE PRICE ONLY: suggestedSL and suggestedTP MUST be the exact price level, NEVER a relative description like "50 points below entry" or "100 points above"**

## ADD-ON (加仓) GUIDELINES
- Set Add On: true ONLY when:
  1. Existing positions are in profit (positive PnL)
  2. The market shows strong continuation signals
  3. Adding would not concentrate risk excessively
- Default is false — only set true when conditions clearly support adding.

## ANCHOR REFERENCE SYSTEM (锚点引用)
The following anchors will be provided in subsequent messages.
You MUST reference them directly without recalculating.

- {{{{TECHNICAL_STRUCT}}}}: Pre-computed technical analysis structure
  - Contains: bias, confidence, phase, support_levels[], resistance_levels[]
  - Use S/R levels directly for SL/TP placement
  - Do NOT re-analyze technical structure
- {{{{ACCOUNT_STATE}}}}: Real-time account snapshot
  - Contains: balance, equity, leverage, open positions
- {{{{MARKET_STATE}}}}: Real-time market snapshot
  - Contains: current price, spread, ATR(H1)"""  # noqa: E501


def build_semi_static_data(
    technical: JSONDict | None,
    profile: JSONDict,
) -> str:
    """镜像 buildSemiStaticData:技术分析结构变化时更新。"""
    return f"""## SEMI-STATIC RISK CONTEXT (changes on bar/technical update)

Instrument: {profile['name']} ({profile['symbol']})
Volatility: {profile['volatility_level']}
Suggested SL: {profile['sl_atr_multiplier']}x ATR
Suggested TP: {profile['tp_atr_multiplier']}x ATR

Technical Analysis Structure:
{stable_stringify(technical) if technical else 'Unavailable'}

Risk Rules Reference:
- suggestedSL below nearest support (long) or above nearest resistance (short), with ATR buffer
- suggestedTP targets next significant resistance (long) or support (short)
- maxPositionSize: stop-loss hit <= 2% of account equity
- Risk/reward ratio >= 1:2
- All prices within +-50% of current market price"""  # noqa: E501


def build_dynamic_data(
    payload: JSONDict,
    profile: JSONDict,
) -> str:
    """镜像 buildDynamicData:账户、持仓、行情的实时层。"""
    account = payload.get("account") or {}
    market = payload.get("market") or {}
    positions = payload.get("positions") or []
    h1 = select_indicator(payload.get("indicators") or {}, "H1", "h1")
    current_price = market.get("bid") or market.get("ask") or 0

    if len(positions) == 0:
        position_summary = "No open positions"
    else:
        pieces = [
            f"{p.get('direction')} lots @ {p.get('entry_price')}, PnL={p.get('profit')}"
            for p in positions
        ]
        position_summary = f"{len(positions)} open position(s): {'; '.join(pieces)}"

    return f"""## REAL-TIME DATA (changes every request)

CRITICAL REMINDER: You are analyzing {profile['symbol']} — typical price range: {profile['price_range_hint']}
Current Price: {current_price:.{profile['price_precision']}f}

Account:
- Balance: {account.get('balance')} {account.get('currency')}
- Equity: {account.get('equity')}
- Leverage: 1:{account.get('leverage')}

Positions:
{position_summary}
Current position side: {positions[0].get('direction') if len(positions) > 0 else 'none'}

Market:
- Spread: {market.get('spread')} points
- ATR(H1): {h1.get('atr')}

INSTRUMENT VERIFICATION CHECK:
- Symbol: {profile['symbol']}
- Current Price: ~{current_price:.{profile['price_precision']}f} (should be in range {profile['price_range_hint']})
- If your suggestedSL/suggestedTP does NOT match this magnitude, YOU HAVE THE WRONG INSTRUMENT
- STOP and recalculate using the CORRECT prices for {profile['symbol']}
- **ABSOLUTE PRICE RULE: suggestedSL and suggestedTP MUST be actual price levels, NEVER relative descriptions like "50 points" or "100 points below"**

Assess risk and respond with ## RISK section."""  # noqa: E501


def build_prompt(
    technical: JSONDict | None,
    payload: JSONDict,
    profile: JSONDict,
) -> str:
    """镜像 buildPrompt:单块 prompt 旧路径。"""
    account = payload.get("account") or {}
    market = payload.get("market") or {}
    positions = payload.get("positions") or []
    h1 = select_indicator(payload.get("indicators") or {}, "H1", "h1")
    current_price = market.get("bid") or market.get("ask") or 0

    if len(positions) == 0:
        position_summary = "No open positions"
    else:
        pieces = [
            f"{p.get('direction')} {p.get('lots')} lots @ {p.get('entry_price')}, PnL={p.get('profit')}"
            for p in positions
        ]
        position_summary = f"{len(positions)} open position(s): {'; '.join(pieces)}"

    return f"""Assess risk for {profile['name']} ({market.get('symbol')}):

## SYMBOL CONTEXT
- Instrument: {profile['name']} ({profile['symbol']})
- Current Price: {current_price:.{profile['price_precision']}f}
- Volatility: {profile['volatility_level']}

## Account
Balance: {account.get('balance')} {account.get('currency')}, Equity: {account.get('equity')}, Leverage: 1:{account.get('leverage')}

## Positions
{position_summary}
Current position side: {positions[0].get('direction') if len(positions) > 0 else 'none'}

## Market
Spread: {market.get('spread')} points, ATR(H1): {h1.get('atr')}

## Technical Analysis
{stable_stringify(technical) if technical else 'Unavailable'}

Respond with structured MARKDOWN using ## RISK section.
Remember: suggestedSL and suggestedTP MUST be prices appropriate for {profile['symbol']} (current price ~{current_price:.{profile['price_precision']}f}), NOT for any other instrument.
**CRITICAL: suggestedSL and suggestedTP MUST be absolute price values, NEVER relative descriptions like "stop loss 50 points" or "take profit 100 points".**"""  # noqa: E501


def parse_markdown_risk(raw: str) -> JSONDict | None:
    """镜像 parseMarkdownRisk:从 ## RISK 节提取 RiskAssessment。"""
    sections = split_sections(raw)
    risk_section = sections.get("risk") or ""
    if not risk_section:
        return None
    fields = extract_fields(risk_section)
    list_items = extract_list_items(risk_section)

    result: JSONDict = {
        "riskLevel": get_enum_field(fields, "risk_level", ("low", "medium", "high", "extreme"), "high"),
        "maxPositionSize": get_number_field(fields, "max_position_size", 0),
        "suggestedSL": get_number_field(fields, "suggested_sl", 0),
        "suggestedTP": get_number_field(fields, "suggested_tp", 0),
        "warnings": extract_warnings(fields, list_items),
        "addOn": get_boolean_field(fields, "add_on", False),
    }
    return result


def parse_response(raw: str) -> JSONDict | None:
    """镜像 parseResponse:Markdown 优先,JSON 回退。"""
    format_ = detect_format(raw)
    if format_ == "markdown":
        md_result = parse_markdown_risk(raw)
        if md_result:
            return md_result
    return safe_parse_response(raw, validate_risk_assessment, {"agent": "risk"})


class RiskManagerService:
    """镜像 RiskManagerService:分层 prompt + 主/回退模型 + high-risk 兜底。"""

    def __init__(self, client: LlmClient) -> None:
        self.client = client

    async def run(
        self,
        technical: JSONDict | None,
        payload: JSONDict,
        symbol: str,
    ) -> JSONDict:
        logger = get_logger()
        profile = get_symbol_profile(symbol)
        system_prompt = build_system_prompt(profile)

        semi_static_data = build_semi_static_data(technical, profile)
        dynamic_data = build_dynamic_data(payload, profile)

        system_blocks = [SystemBlock(text=system_prompt, cacheable=True)]
        user_layers = [
            UserLayer(text=semi_static_data, cacheable=True),
            UserLayer(text=dynamic_data, cacheable=False),
        ]
        try:
            result = await self.client.stream_layered(system_blocks, user_layers)
            raw = result.content
        except Exception as err:
            logger.warn({"err": str(err), "symbol": symbol}, "Risk manager: primary model failed, trying fallback")
            try:
                raw = await self.client.invoke_layered(system_blocks, user_layers)
            except Exception as fallback_err:
                logger.error({"err": str(fallback_err), "symbol": symbol}, "Risk manager: fallback also failed")
                raise fallback_err

        assessment = parse_response(raw)

        if not assessment:
            logger.error({"symbol": symbol}, "Risk assessment parse failed — returning high-risk fallback")
            return {
                "riskLevel": "high",
                "maxPositionSize": 0,
                "suggestedSL": 0,
                "suggestedTP": 0,
                "warnings": ["风险评估解析失败，建议不开仓 (Risk assessment parse failed, recommend no position)"],
            }

        # VALIDATION:suggestedSL/suggestedTP 超出品种合理价格区间时清零
        price_range = profile.get("price_range")
        if isinstance(price_range, list) and len(price_range) == 2:
            min_price, max_price = price_range
            if assessment.get("suggestedSL") not in (None, 0):
                if assessment["suggestedSL"] < min_price or assessment["suggestedSL"] > max_price:
                    original_sl = assessment["suggestedSL"]
                    assessment["suggestedSL"] = 0
                    logger.warn(
                        {"symbol": symbol, "originalSL": original_sl, "expectedRange": price_range},
                        "suggestedSL out of instrument price range — rejecting AI stop loss",
                    )
                    warnings = assessment.get("warnings") or []
                    warnings.append(f"AI止损 {original_sl} 超出{symbol}合理范围({min_price}-{max_price})，已拒绝")
                    assessment["warnings"] = warnings
            if assessment.get("suggestedTP") not in (None, 0):
                if assessment["suggestedTP"] < min_price or assessment["suggestedTP"] > max_price:
                    original_tp = assessment["suggestedTP"]
                    assessment["suggestedTP"] = 0
                    logger.warn(
                        {"symbol": symbol, "originalTP": original_tp, "expectedRange": price_range},
                        "suggestedTP out of instrument price range — rejecting AI take profit",
                    )
                    warnings = assessment.get("warnings") or []
                    warnings.append(f"AI止盈 {original_tp} 超出{symbol}合理范围({min_price}-{max_price})，已拒绝")
                    assessment["warnings"] = warnings

        logger.info({"symbol": symbol, "riskLevel": assessment["riskLevel"]}, "Risk assessment complete")
        return assessment
