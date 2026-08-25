"""MAO(Market Analysis Orchestrator)Arbitrator Agent
(1:1 镜像 gold-bot apps/app-agent/src/agents/mao-arbitrator.ts)。

协调技术/风险/挂单信号并输出最终仲裁结果:
- build_system_prompt / build_prompt(多时间框架指标摘要)
- parse_markdown_arbitration / parse_response(Markdown 优先,JSON 回退)
- MaoArbitratorService.run:解析失败返回 hold 兜底;对 trade_recommendation 做业务校验降级
"""

from __future__ import annotations

import json
from typing import Any

from backend.agents.agents._support import (
    LlmClient,
    detect_format,
    extract_fields,
    get_boolean_field,
    get_enum_field,
    get_logger,
    get_number_field,
    get_string_field,
    get_symbol_profile,
    safe_parse_response,
    split_sections,
    validate_arbitration_business,
    validate_arbitration_result,
)

__all__ = [
    "MaoArbitratorService",
    "build_prompt",
    "build_system_prompt",
    "parse_markdown_arbitration",
    "parse_response",
]

JSONDict = dict[str, Any]


def build_system_prompt(profile: JSONDict) -> str:
    """镜像 buildSystemPrompt:MAO 仲裁系统提示(五大节 + 挂单管线 + 置信度校准)。"""
    return f"""You are the Market Analysis Orchestrator (MAO) arbitrator for {profile['name']} ({profile['symbol']}).
Your job is to perform comprehensive multi-theory analysis, reconcile conflicting signals,
and produce a final arbitration result with specific trade recommendations.

Produce a structured MARKDOWN analysis with exactly these sections:
- ## ARBITRATION
- ## DOW THEORY
- ## WAVE THEORY
- ## CHANLUN THEORY
- ## TRADE RECOMMENDATION

ALL 5 sections are REQUIRED on every response.

## SYMBOL CHARACTERISTICS
- Instrument: {profile['name']}
- Price precision: {profile['price_precision']} decimal places
- Typical price range: {profile['price_range_hint']}
- Volatility: {profile['volatility_level']}
- 1 pip = {profile['pip_value']}
- Suggested SL: {profile['sl_atr_multiplier']}× ATR
- Suggested TP: {profile['tp_atr_multiplier']}× ATR

## CRITICAL OUTPUT RULES
1. Output structured MARKDOWN text using ## SECTION headers and - Key: Value format.
2. NEVER wrap output in ```code blocks```. Do NOT output JSON.
3. ALL numeric fields MUST be valid finite numbers.
4. For text fields, output bilingual: Chinese first, English in parentheses.
5. Trade prices MUST be precise to {profile['price_precision']} decimal places.
6. All prices MUST be within ±50% of the current market price for THIS instrument.
7. NEVER output prices from a different instrument.

## PENDING ORDER PIPELINE (Critical Context)
When you output Action: open, the system will:
- Create a PENDING order (BUY_LIMIT/BUY_STOP/SELL_LIMIT/SELL_STOP) with 4-hour expiry
- The pending order is placed at the entry_price from your trade_recommendation
- It only triggers when price REACHES that level — it does NOT execute at current price
- Spread at signal time is IRRELEVANT for pending orders
- You can set entry_price FARTHER from current price for a better risk/reward setup

## WHAT MAKES A GOOD PENDING ORDER SIGNAL
- Clear trend direction on H4/H1 (ADX > 25, EMA aligned)
- Specific entry zone with favorable risk/reward (SL/TP ratio ≥ 1:2)
- The market does NOT need to be at the entry price right now
- Spread, consolidation, and low short-term ADX are NOT blockers for pending orders

## CONFIDENCE CALIBRATION RULES (Critical)
Base confidence calculation guidelines:
1. H1/M30 ADX > 40 + 多周期 RSI 同向 → Base confidence 75%
2. 波浪理论确认 (confirmed) + 谐波形态完成 → +10%
3. 缠论中枢突破 + 买卖点确认 → +10%
4. 多时间框架矛盾 (H1 看空 vs M15 超卖) → -15%
5. 单周期强信号但无共振 → -10%

**Time-frame signal weighting for direction decision:**
- H1 (35%) + M30 (35%) = 70% weight → PRIMARY trend direction
- H4 (15%) provides medium-term trend validation
- M15 (15%) provides entry timing, NOT trend reversal signal
- When H1 ADX > 40, M15 oversold/overbought is a TIMING consideration, NOT a direction override

**CRITICAL:**
- If H1 shows strong bearish trend (ADX > 40, MACD negative) and M15 is oversold (RSI < 30):
  - Direction: STILL BEARISH (hold or wait for confirmation, do NOT reverse to buy)
  - M15 oversold only means "caution for short-term bounce risk," not "trend reversal"
- Final confidence must reflect weighted consensus, not equal-vote averaging

## ANALYSIS FRAMEWORK — THREE THEORIES + TRADE RECOMMENDATION
You MUST analyze the market through THREE theoretical frameworks and produce a trade recommendation.

### 1. DOW THEORY (道氏理论)
- Primary Trend (D1/H4): accumulation, markup, distribution, markdown
- Secondary Trend (H1): Counter-trend corrections
- Short-term (M30/M15): Minor fluctuations
- Multi-TF Confirmation: All timeframes must agree for strong signals

### 2. ELLIOTT WAVE THEORY (波浪理论)
- Current Wave: Which wave is price in?
- Wave Direction: impulse_up, impulse_down, corrective, or unclear
- Wave Count: Describe the wave count
- Next Target: Where is the next wave likely to take price?

### 3. CHANLUN THEORY (缠论)
- Bi Direction: Current stroke direction
- Duan Direction: Current segment direction
- Zhongshu State: forming, active, breaking_up, breaking_down, or none
- Buy/Sell Point: buy_1, buy_2, buy_3, sell_1, sell_2, sell_3, or none

### 4. TRADE RECOMMENDATION (交易建议)
Based on the three theories, provide a SPECIFIC trade recommendation.

## REASONING REQUIREMENTS
The "Reasoning" field MUST be a detailed analysis (at least 6-8 sentences) covering all three theories, multi-timeframe alignment, risk state, and key levels.

## REQUIRED OUTPUT MARKDOWN FORMAT

## ARBITRATION
- Final Direction: buy | sell | hold | close
- Confidence: <0-100>
- Action: open | close | modify | hold
- Primary Contradiction: <string or empty>
- Phase: <string>
- United Front Analysis: <bilingual string>
- Reasoning: <bilingual string, at least 6-8 sentences covering all 3 theories>

## DOW THEORY
- Primary Trend: bullish | bearish | neutral
- Primary Phase: accumulation | markup | distribution | markdown
- Secondary Trend: bullish | bearish | neutral
- Short Term Trend: bullish | bearish | neutral
- Multi TF Confirm: true | false
- Rationale: <string>

## WAVE THEORY
- Current Wave: <string>
- Wave Direction: impulse_up | impulse_down | corrective | unclear
- Wave Count: <string>
- Next Target: <string>
- Confidence: <0-100>
- Rationale: <string>

## CHANLUN THEORY
- Trend: up | down | range
- Bi Direction: up | down | none
- Duan Direction: up | down | none
- Zhongshu State: forming | active | breaking_up | breaking_down | none
- Buy Sell Point: buy_1 | buy_2 | buy_3 | sell_1 | sell_2 | sell_3 | none
- Confidence: <0-100>
- Rationale: <string>

## TRADE RECOMMENDATION
- Direction: buy | sell | hold
- Entry Price: <number>
- Stop Loss: <number>
- Take Profit 1: <number>
- Take Profit 2: <number>
- Risk Reward Ratio: <number>
- Position Size Lots: <string e.g. 0.05-0.1>
- Rationale: <string>"""  # noqa: E501


def build_prompt(input_: JSONDict, profile: JSONDict) -> str:
    """镜像 buildPrompt:技术/风险/挂单信号 + 多时间框架指标摘要。"""
    technical = input_.get("technical")
    risk = input_.get("risk")
    payload = input_.get("payload") or {}
    pending_signal = input_.get("pendingSignal")
    market = payload.get("market") or {}
    price = market.get("bid") or market.get("ask") or 0

    indicators = payload.get("indicators") or {}
    tf_lines: list[str] = []
    for tf in ("M15", "M30", "H1", "H4"):
        ind = indicators.get(tf) or indicators.get(tf.lower())
        if not ind:
            tf_lines.append(f"{tf}: no data")
            continue
        ema200 = f" EMA200={ind.get('ema200')}" if ind.get("ema200") else ""
        fib = (
            f" | Fib: 23.6%={ind.get('fib_236')} 38.2%={ind.get('fib_382')} "
            f"50%={ind.get('fib_500')} 61.8%={ind.get('fib_618')}"
            if ind.get("fib_236")
            else ""
        )
        pivot = (
            f" | Pivot: PP={ind.get('pp')} R1={ind.get('r1')} S1={ind.get('s1')}"
            if ind.get("pp")
            else ""
        )
        tf_lines.append(
            f"{tf}: close={ind.get('close')} open={ind.get('open')} high={ind.get('high')} "
            f"low={ind.get('low')} | EMA20={ind.get('ema20')} EMA50={ind.get('ema50')}"
            f"{ema200} | RSI={ind.get('rsi')} ADX={ind.get('adx')} ATR={ind.get('atr')} "
            f"| MACD={ind.get('macd')} signal={ind.get('macd_signal')} hist={ind.get('macd_hist')} "
            f"| BB: upper={ind.get('bb_upper')} mid={ind.get('bb_middle')} lower={ind.get('bb_lower')} "
            f"| Stoch: K={ind.get('stoch_k')} D={ind.get('stoch_d')}{fib}{pivot}"
        )
    tf_summary = "\n".join(tf_lines)

    account = payload.get("account") or {}
    positions = payload.get("positions") or []

    return f"""Arbitrate {profile['name']} ({market.get('symbol')}) analysis:

## SYMBOL CONTEXT
- Instrument: {profile['name']} ({profile['symbol']})
- Current Price: {price:.{profile['price_precision']}f}
- Price Range: {price * 0.5:.{profile['price_precision']}f} - {price * 1.5:.{profile['price_precision']}f}

Technical Analysis: {json.dumps(technical, ensure_ascii=False) if technical else "unavailable"}
Risk Assessment: {json.dumps(risk, ensure_ascii=False) if risk else "unavailable"}
Pending Signal: {json.dumps(pending_signal, ensure_ascii=False) if pending_signal else "none"}

Account: Balance={account.get('balance')}, Equity={account.get('equity')}, Positions={len(positions)}
Market: Price={price:.{profile['price_precision']}f}, Spread={market.get('spread')}
Market Status: {json.dumps(payload.get('market_status'), ensure_ascii=False)}
Strategy: {json.dumps(payload.get('strategy_mapping'), ensure_ascii=False)}
Positions: {json.dumps(positions, ensure_ascii=False)}

## Multi-Timeframe Indicators (for Dow/Wave/Chanlun analysis)
{tf_summary}

Analyze alignment and conflicts between:
1. Technical bias vs pending signal direction
2. Risk level vs suggested position size
3. Timeframe agreement across M15, M30, H1, H4
4. Dow Theory trend alignment across timeframes
5. Elliott Wave structure from indicator patterns
6. Chanlun Bi/Duan/Zhongshu from price structure

Perform ALL THREE theories (Dow Theory, Elliott Wave, Chanlun) analysis and provide a specific trade recommendation with exact entry/SL/TP prices.

IMPORTANT: All prices in your response MUST be appropriate for {profile['symbol']} (current price ~{price:.{profile['price_precision']}f}). Do NOT output prices from a different instrument.

Respond with structured MARKDOWN using ## ARBITRATION, ## DOW THEORY, ## WAVE THEORY, ## CHANLUN THEORY, and ## TRADE RECOMMENDATION sections.
IMPORTANT: All prices in your response MUST be appropriate for {profile['symbol']} (current price ~{price:.{profile['price_precision']}f}). Do NOT output prices from a different instrument."""  # noqa: E501


def parse_markdown_arbitration(raw: str) -> JSONDict | None:
    """镜像 parseMarkdownArbitration:五个节的字段提取与默认值。"""
    sections = split_sections(raw)

    arb_section = sections.get("arbitration") or ""
    dow_section = sections.get("dow theory") or sections.get("dow_theory") or ""
    wave_section = sections.get("wave theory") or sections.get("wave_theory") or ""
    chanlun_section = sections.get("chanlun theory") or sections.get("chanlun_theory") or ""
    trade_section = sections.get("trade recommendation") or sections.get("trade_recommendation") or ""

    if not arb_section:
        return None

    arb_fields = extract_fields(arb_section)
    dow_fields = extract_fields(dow_section)
    wave_fields = extract_fields(wave_section)
    chanlun_fields = extract_fields(chanlun_section)
    trade_fields = extract_fields(trade_section)

    return {
        "final_direction": get_enum_field(arb_fields, "final_direction", ("buy", "sell", "hold", "close"), "hold"),
        "confidence": get_number_field(arb_fields, "confidence", 0, {"min": 0, "max": 100}),
        "primary_contradiction": get_string_field(arb_fields, "primary_contradiction", ""),
        "phase": get_string_field(arb_fields, "phase", "unknown"),
        "reasoning": get_string_field(arb_fields, "reasoning", ""),
        "action": get_enum_field(arb_fields, "action", ("open", "close", "modify", "hold"), "hold"),
        "united_front_analysis": get_string_field(arb_fields, "united_front_analysis", ""),
        "dow_theory": {
            "primary_trend": get_enum_field(dow_fields, "primary_trend", ("bullish", "bearish", "neutral"), "neutral"),
            "primary_phase": get_enum_field(
                dow_fields, "primary_phase", ("accumulation", "markup", "distribution", "markdown"), "accumulation"
            ),
            "secondary_trend": get_enum_field(
                dow_fields, "secondary_trend", ("bullish", "bearish", "neutral"), "neutral"
            ),
            "short_term_trend": get_enum_field(
                dow_fields, "short_term_trend", ("bullish", "bearish", "neutral"), "neutral"
            ),
            "multi_tf_confirm": get_boolean_field(dow_fields, "multi_tf_confirm", False),
            "rationale": get_string_field(dow_fields, "rationale", ""),
        },
        "wave_theory": {
            "current_wave": get_string_field(wave_fields, "current_wave", ""),
            "wave_direction": get_enum_field(
                wave_fields, "wave_direction", ("impulse_up", "impulse_down", "corrective", "unclear"), "unclear"
            ),
            "wave_count": get_string_field(wave_fields, "wave_count", ""),
            "next_target": get_string_field(wave_fields, "next_target", ""),
            "confidence": get_number_field(wave_fields, "confidence", 0, {"min": 0, "max": 100}),
            "rationale": get_string_field(wave_fields, "rationale", ""),
        },
        "chanlun_theory": {
            "trend": get_enum_field(chanlun_fields, "trend", ("up", "down", "range"), "range"),
            "bi_direction": get_enum_field(chanlun_fields, "bi_direction", ("up", "down", "none"), "none"),
            "duan_direction": get_enum_field(chanlun_fields, "duan_direction", ("up", "down", "none"), "none"),
            "zhongshu_state": get_enum_field(
                chanlun_fields,
                "zhongshu_state",
                ("forming", "active", "breaking_up", "breaking_down", "none"),
                "none",
            ),
            "buy_sell_point": get_enum_field(
                chanlun_fields,
                "buy_sell_point",
                ("buy_1", "buy_2", "buy_3", "sell_1", "sell_2", "sell_3", "none"),
                "none",
            ),
            "confidence": get_number_field(chanlun_fields, "confidence", 0, {"min": 0, "max": 100}),
            "rationale": get_string_field(chanlun_fields, "rationale", ""),
        },
        "trade_recommendation": {
            "direction": get_enum_field(trade_fields, "direction", ("buy", "sell", "hold"), "hold"),
            "entry_price": get_number_field(trade_fields, "entry_price", 0),
            "stop_loss": get_number_field(trade_fields, "stop_loss", 0),
            "take_profit_1": get_number_field(trade_fields, "take_profit_1", 0),
            "take_profit_2": get_number_field(trade_fields, "take_profit_2", 0),
            "risk_reward_ratio": get_number_field(trade_fields, "risk_reward_ratio", 0),
            "position_size_lots": get_string_field(trade_fields, "position_size_lots", "0.01"),
            "rationale": get_string_field(trade_fields, "rationale", ""),
        },
    }


def parse_response(raw: str) -> JSONDict | None:
    """镜像 parseResponse:Markdown 优先,JSON 回退。"""
    format_ = detect_format(raw)
    if format_ == "markdown":
        md_result = parse_markdown_arbitration(raw)
        if md_result:
            return md_result
    return safe_parse_response(raw, validate_arbitration_result, {"agent": "mao"})


class MaoArbitratorService:
    """镜像 MaoArbitratorService:单次 streamInvoke + hold 兜底 + 交易校验降级。"""

    def __init__(self, client: LlmClient) -> None:
        self.client = client

    async def run(self, input_: JSONDict, symbol: str) -> JSONDict:
        logger = get_logger()
        profile = get_symbol_profile(symbol)
        system_prompt = build_system_prompt(profile)
        prompt = build_prompt(input_, profile)
        raw = await self.client.stream_invoke(prompt, system_prompt)
        result = parse_response(raw)

        if not result:
            logger.error({"symbol": symbol}, "MAO arbitration parse failed — returning hold fallback")
            return {
                "final_direction": "hold",
                "confidence": 0,
                "primary_contradiction": "",
                "phase": "unknown",
                "reasoning": "仲裁解析失败 (Arbitration parse failed)",
                "action": "hold",
                "united_front_analysis": "",
            }

        # Apply trade-level business validation
        payload = input_.get("payload") or {}
        market = payload.get("market") or {}
        current_price = market.get("bid") or market.get("ask") or 0
        trade = result.get("trade_recommendation")
        if current_price > 0 and trade and trade.get("direction") != "hold":
            validation = validate_arbitration_business(result, current_price, profile)
            if validation.warnings:
                logger.warn(
                    {"symbol": symbol, "warnings": validation.warnings},
                    "MAO arbitration: trade validation warnings",
                )
            if not validation.valid and validation.fixed_arbitration is not None:
                logger.warn({"symbol": symbol}, "MAO arbitration: trade downgraded to hold due to invalid SL/TP")
                result = validation.fixed_arbitration
            elif validation.fixed_arbitration is not None:
                result = validation.fixed_arbitration

        logger.info(
            {
                "symbol": symbol,
                "direction": result["final_direction"],
                "action": result["action"],
                "confidence": result["confidence"],
            },
            "MAO arbitration complete",
        )
        return result
