"""镜像 apps/app-agent/src/types/schemas.ts(zod schema → pydantic model)。

1:1 语义:
- strict 模式(pydantic ConfigDict(strict=True))镜像 zod 默认拒绝字符串强转:
  z.number() 拒绝 '5' / true;z.string() 拒绝数字;z.boolean() 只接受 true/false;
  z.literal()/z.enum() 拒绝集合外值(Note: z.number() 接受 int 与 float,
  因此数值字段标注 Union[int, float],与 zod 的 JS number 语义一致)。
- z.number().finite() → finite 校验(拒绝 NaN/Infinity);z.number().int() → 整数校验;
  min/max/positive → 范围校验;z.array(...).max(n) / z.string().min(n) 同理。
- 约束用 Annotated AfterValidator 表达(而非 Field ge/le),这样 safeParseLLM 的
  partial 回退(全部字段 optional)能保留内层约束——镜像 zod .partial() 语义。
- 未知字段忽略(extra='ignore' 为 pydantic 默认,镜像 zod object 的 strip 行为)。
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    ValidationError,
    create_model,
    field_validator,
    model_validator,
)

__all__ = [
    "AccountInfoSchema",
    "ArbitrationResultSchema",
    "ChanlunAnalystResultSchema",
    "ChanlunTheorySchema",
    "ComprehensiveAnalysisDataSchema",
    "DowTheorySchema",
    "GoldbotBarSchema",
    "GoldbotPayloadSchema",
    "HarmonicAnalysisResultSchema",
    "HarmonicTheorySchema",
    "IndicatorPackSchema",
    "MarketDataSchema",
    "MarketFilterSchema",
    "MarketFiltersSchema",
    "MarketStatusSchema",
    "PendingSignalSchema",
    "PositionInfoSchema",
    "RiskAssessmentSchema",
    "SRLevelSchema",
    "SRLevelsSchema",
    "StrategyMappingSchema",
    "TechnicalAnalysisSchema",
    "TradePlanEntryZoneSchema",
    "TradePlanExecutionTypeSchema",
    "TradePlanModeSchema",
    "TradePlanRequestedOrderTypeSchema",
    "TradePlanSchema",
    "TradePlanSideSchema",
    "TradeRecommendationSchema",
    "WaveAnalystResultSchema",
    "WaveTargetLevelsSchema",
    "WaveTheorySchema",
    "clean_sr_levels",
    "safe_parse_llm",
]

# ---------------------------------------------------------------- 数值基础

Number = int | float
"""z.number():JS number 接受 int 与 float(含 NaN/Infinity,除非 .finite())。"""


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _finite(value: Any) -> Any:
    # z.number().finite()
    if not _is_number(value) or not math.isfinite(float(value)):
        raise ValueError("expected a finite number")
    return value


def _integer(value: Any) -> Any:
    # z.number().int()
    _finite(value)
    if not float(value).is_integer():
        raise ValueError("expected an integer")
    return value


def _between(ge: float, le: float) -> Any:
    # z.number().min(ge).max(le)
    def check(value: Any) -> Any:
        if not _is_number(value) or not math.isfinite(float(value)):
            raise ValueError("expected a finite number")
        num = float(value)
        if num < ge or num > le:
            raise ValueError(f"expected a number between {ge} and {le}")
        return value

    return check


def _min(value: float) -> Any:
    # z.number().min(value)
    def check(inner: Any) -> Any:
        if not _is_number(inner) or not math.isfinite(float(inner)):
            raise ValueError("expected a finite number")
        if float(inner) < value:
            raise ValueError(f"expected a number >= {value}")
        return inner

    return check


def _positive(value: Any) -> Any:
    # z.number().positive()
    if not _is_number(value) or not math.isfinite(float(value)) or float(value) <= 0:
        raise ValueError("expected a positive number")
    return value


def _str_min_len(n: int) -> Any:
    # z.string().min(n)
    def check(value: Any) -> Any:
        if not isinstance(value, str):
            raise ValueError("expected a string")
        if len(value) < n:
            raise ValueError(f"expected a string with length >= {n}")
        return value

    return check


def _list_max_len(n: int) -> Any:
    # z.array(...).max(n)
    def check(value: Any) -> Any:
        if not isinstance(value, list):
            raise ValueError("expected an array")
        if len(value) > n:
            raise ValueError(f"expected an array with length <= {n}")
        return value

    return check


def _list_min_len(n: int) -> Any:
    # z.array(...).min(n)
    def check(value: Any) -> Any:
        if not isinstance(value, list):
            raise ValueError("expected an array")
        if len(value) < n:
            raise ValueError(f"expected an array with length >= {n}")
        return value

    return check


def _datetime_string(value: Any) -> Any:
    # z.string().datetime()
    if not isinstance(value, str):
        raise ValueError("expected a datetime string")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("expected an ISO 8601 datetime string") from None
    return value


Finite = Annotated[Number, AfterValidator(_finite)]
Integer = Annotated[Number, AfterValidator(_integer)]
NonNegative = Annotated[Number, AfterValidator(_min(0))]
Positive = Annotated[Number, AfterValidator(_positive)]
MinOneString = Annotated[str, AfterValidator(_str_min_len(1))]


class _StrictModel(BaseModel):
    model_config = ConfigDict(strict=True)


# ---------------------------------------------------------------- LLM 输出 schema

# SRLevel schema - price must be valid number
class SRLevelSchema(_StrictModel):
    price: Positive
    type: Literal["support", "resistance"]
    strength: Literal["strong", "moderate", "weak"]
    timeframe: MinOneString
    touches: Annotated[Number, AfterValidator(_integer), AfterValidator(_between(0, 20))]


# TechnicalAnalysis schema
class TechnicalAnalysisSchema(_StrictModel):
    bias: Literal["bullish", "bearish", "neutral"]
    confidence: Annotated[Number, AfterValidator(_between(0, 100))]
    phase: Literal["trending", "ranging", "breakout", "reversal", "consolidation"]
    indicators_summary: MinOneString
    support_levels: Annotated[list[SRLevelSchema], AfterValidator(_list_max_len(6))]
    resistance_levels: Annotated[list[SRLevelSchema], AfterValidator(_list_max_len(6))]
    recommendation: Literal["hold", "close", "partial_close", "trail_stop", "none"]
    rationale: MinOneString


# SRLevels schema
class SRLevelsSchema(_StrictModel):
    support_levels: Annotated[list[SRLevelSchema], AfterValidator(_list_max_len(6))]
    resistance_levels: Annotated[list[SRLevelSchema], AfterValidator(_list_max_len(6))]
    recommendation: str
    rationale: str


# Dow Theory analysis schema
class DowTheorySchema(_StrictModel):
    primary_trend: Literal["bullish", "bearish", "neutral"]
    primary_phase: Literal["accumulation", "markup", "distribution", "markdown"]
    secondary_trend: Literal["bullish", "bearish", "neutral"]
    short_term_trend: Literal["bullish", "bearish", "neutral"]
    multi_tf_confirm: bool
    rationale: MinOneString


# Wave Theory analysis schema
class WaveTheorySchema(_StrictModel):
    current_wave: MinOneString
    wave_direction: Literal["impulse_up", "impulse_down", "corrective", "unclear"]
    wave_count: MinOneString
    next_target: MinOneString
    confidence: Annotated[Number, AfterValidator(_between(0, 100))]
    rationale: MinOneString


# Chanlun Theory analysis schema
class ChanlunTheorySchema(_StrictModel):
    trend: Literal["up", "down", "range"]
    bi_direction: Literal["up", "down", "none"]
    duan_direction: Literal["up", "down", "none"]
    zhongshu_state: Literal["forming", "active", "breaking_up", "breaking_down", "none"]
    buy_sell_point: Literal["buy_1", "buy_2", "buy_3", "sell_1", "sell_2", "sell_3", "none"]
    confidence: Annotated[Number, AfterValidator(_between(0, 100))]
    rationale: MinOneString


# Harmonic Theory analysis schema
class HarmonicTheorySchema(_StrictModel):
    pattern: Literal["gartley", "bat", "butterfly", "crab", "abcd", "cypher", "shark", "none"]
    direction: Literal["bullish", "bearish", "neutral"]
    confidence: Annotated[Number, AfterValidator(_between(0, 100))]
    rationale: MinOneString


# Trade recommendation schema
class TradeRecommendationSchema(_StrictModel):
    direction: Literal["buy", "sell", "hold"]
    entry_price: Finite
    stop_loss: Finite
    take_profit_1: Finite
    take_profit_2: Finite | None = None
    risk_reward_ratio: Annotated[Finite, AfterValidator(_min(0))]
    position_size_lots: MinOneString
    rationale: MinOneString


# ArbitrationResult schema
class ArbitrationResultSchema(_StrictModel):
    final_direction: Literal["buy", "sell", "hold", "close", "dual"]
    confidence: Annotated[Number, AfterValidator(_between(0, 100))]
    primary_contradiction: str
    phase: str
    reasoning: MinOneString
    action: Literal["open", "close", "modify", "hold"]
    united_front_analysis: str
    dow_theory: DowTheorySchema | None = None
    wave_theory: WaveTheorySchema | None = None
    chanlun_theory: ChanlunTheorySchema | None = None
    harmonic_theory: HarmonicTheorySchema | None = None
    trade_recommendation: TradeRecommendationSchema | None = None


# RiskAssessment schema
class RiskAssessmentSchema(_StrictModel):
    riskLevel: Literal["low", "medium", "high", "extreme"]
    maxPositionSize: Annotated[Finite, AfterValidator(_min(0))]
    suggestedSL: Positive  # 止损价格(支撑位 - ATR缓冲)
    suggestedTP: Positive | None = None  # 可选止盈目标
    warnings: list[str]
    addOn: bool = False


class WaveTargetLevelsSchema(_StrictModel):
    level_1_618: Finite
    level_2_0: Finite


class WaveAnalystResultSchema(_StrictModel):
    wave_confirmation: Literal["confirmed", "partial", "rejected"]
    extension_wave: Literal[1] | Literal[3] | Literal[5] | None
    corrective_type: Literal["zigzag", "flat", "triangle"] | None
    trend_strength: Literal["strong", "moderate", "weak"]
    target_levels: WaveTargetLevelsSchema
    confidence: Annotated[Number, AfterValidator(_between(0, 100))]
    rationale: MinOneString


class ChanlunAnalystResultSchema(_StrictModel):
    trend: Literal["up", "down", "range"]
    strength: Literal["strong", "moderate", "weak"]
    latest_signal: Literal["buy", "sell", "hold"]
    hub_state: Literal["forming", "active", "none"]
    confidence: Annotated[Number, AfterValidator(_between(0, 100))]
    rationale: MinOneString


class HarmonicAnalysisResultSchema(_StrictModel):
    detected_pattern: Literal[
        "gartley", "bat", "butterfly", "crab", "abcd", "cypher", "shark", "deep_crab", "none"
    ]
    direction: Literal["bullish", "bearish", "neutral"]
    timeframe: str
    completion_pct: Annotated[Number, AfterValidator(_between(0, 100))] | None = None
    is_active: bool | None = None
    confidence: Annotated[Number, AfterValidator(_between(0, 100))]
    d_zone_price: Number
    entry_zone: str
    stop_loss: Number
    take_profit_1: Number
    take_profit_2: Number
    rationale: MinOneString


class ComprehensiveAnalysisDataSchema(_StrictModel):
    technical: TechnicalAnalysisSchema
    wave: WaveAnalystResultSchema
    chanlun: ChanlunAnalystResultSchema
    harmonic: HarmonicAnalysisResultSchema | None = None
    risk: RiskAssessmentSchema
    arbitration: ArbitrationResultSchema


# ---------------------------------------------------------------- TradePlan schemas

TradePlanModeSchema = Literal["observe", "veto", "approve", "modify", "reduce", "close"]
TradePlanSideSchema = Literal["buy", "sell", "none"]
TradePlanExecutionTypeSchema = Literal["market", "limit"]
TradePlanRequestedOrderTypeSchema = Literal["market", "BUY_LIMIT", "SELL_LIMIT"]


class TradePlanEntryZoneSchema(_StrictModel):
    min: Annotated[Finite, AfterValidator(_min(0))]
    max: Annotated[Finite, AfterValidator(_min(0))]


class TradePlanSchema(_StrictModel):
    schema_version: Literal["trade_plan.v1"]
    decision_id: MinOneString
    account_id: MinOneString
    symbol: MinOneString
    mode: TradePlanModeSchema
    side: TradePlanSideSchema
    confidence: Annotated[Number, AfterValidator(_integer), AfterValidator(_between(0, 100))]
    entry_zone: TradePlanEntryZoneSchema
    execution_type: TradePlanExecutionTypeSchema | None = None
    requested_order_type: TradePlanRequestedOrderTypeSchema | None = None
    stop_loss: Annotated[Finite, AfterValidator(_min(0))]
    take_profit: list[Annotated[Finite, AfterValidator(_min(0))]]
    max_lots: Annotated[Finite, AfterValidator(_min(0))]
    expires_at: Annotated[str, AfterValidator(_datetime_string)]
    reason_codes: Annotated[list[MinOneString], AfterValidator(_list_min_len(1))]
    conflicts: list[str]
    narrative: MinOneString
    add_on: bool = False
    add_on_type: Literal["favorable", "adverse"] | None = None
    add_on_level: Annotated[Number, AfterValidator(_integer), AfterValidator(_between(1, 3))] | None = None
    max_add_count: Annotated[Number, AfterValidator(_integer), AfterValidator(_between(1, 3))] | None = None
    max_total_lots: Annotated[Finite, AfterValidator(_min(0))] | None = None

    @model_validator(mode="after")
    def _super_refine(self) -> TradePlanSchema:
        """镜像 superRefine:observe/veto 模式放行,其余模式强制 buy/sell、正
        entry_zone、正 stop_loss、正 take_profit、正 max_lots。"""
        if self.mode in ("observe", "veto"):
            return self

        if self.side == "none":
            raise ValueError("active trade plan mode requires buy or sell side")
        if self.entry_zone.min <= 0 or self.entry_zone.max <= 0:
            raise ValueError("active trade plan mode requires a positive entry zone")
        if self.entry_zone.min > self.entry_zone.max:
            raise ValueError("entry_zone.min must be <= entry_zone.max")
        if self.stop_loss <= 0:
            raise ValueError("active trade plan mode requires a positive stop_loss")
        if len(self.take_profit) == 0 or any(price <= 0 for price in self.take_profit):
            raise ValueError("active trade plan mode requires positive take_profit levels")
        if self.max_lots <= 0:
            raise ValueError("active trade plan mode requires positive max_lots")
        return self


# ---------------------------------------------------------------- Goldbot payload schemas


class IndicatorPackSchema(_StrictModel):
    close: Number
    open: Number
    high: Number
    low: Number
    ema20: Number
    ema50: Number
    ema200: Number | None = None
    rsi: Number
    adx: Number
    atr: Number
    macd: Number
    macd_signal: Number
    macd_hist: Number
    bb_upper: Number
    bb_middle: Number
    bb_lower: Number
    stoch_k: Number
    stoch_d: Number
    vol_sma: Number | None = None
    fib_236: Number | None = None
    fib_382: Number | None = None
    fib_500: Number | None = None
    fib_618: Number | None = None
    fib_786: Number | None = None
    pp: Number | None = None
    r1: Number | None = None
    s1: Number | None = None
    bars_count: Number | None = None
    macd_divergence: Literal["bullish", "bearish"] | None = None
    rsi_divergence: Literal["bullish", "bearish"] | None = None


class MarketDataSchema(_StrictModel):
    symbol: MinOneString
    bid: Number
    ask: Number
    spread: Number
    time: str | None = None


# AccountInfoSchema - matches Go API aurex.AccountSummary (snake_case)
class AccountInfoSchema(_StrictModel):
    account_id: MinOneString
    equity: Finite
    balance: Finite
    margin: Finite
    free_margin: Finite
    currency: MinOneString
    leverage: Annotated[Number, AfterValidator(_integer), AfterValidator(_min(0))]  # 允许 0(API 可能返回未设置值)
    broker: str | None = None
    server_name: str | None = None
    connected: bool | None = None


# PositionInfoSchema - matches Go API aurex.PositionSummary
class PositionInfoSchema(_StrictModel):
    ticket: Annotated[Number, AfterValidator(_integer), AfterValidator(_positive)]
    symbol: MinOneString | None = None
    strategy: MinOneString
    magic: Annotated[Number, AfterValidator(_integer)] | None = None
    direction: Literal["buy", "sell", "BUY", "SELL"]
    entry_price: Positive
    current_price: Positive
    lots: Positive
    profit: Finite
    pnl_percent: Finite | None = None
    sl: Finite
    tp: Finite
    hold_seconds: Annotated[Number, AfterValidator(_integer)] | None = None
    hold_hours: Finite | None = None
    comment: str | None = None


# MarketStatusSchema - matches Go API aurex.MarketStatus
class MarketStatusSchema(_StrictModel):
    market_open: bool
    is_trade_allowed: bool
    mt4_server_time: str | None = None
    tradeable: bool


class MarketFilterSchema(_StrictModel):
    code: MinOneString
    severity: Literal["blocking", "warning"]
    message: str | None = None


class MarketFiltersSchema(_StrictModel):
    blocked: bool = False
    blocking: list[MarketFilterSchema] | None = []
    warnings: list[MarketFilterSchema] | None = []
    reason_codes: list[MinOneString] | None = []


# StrategyMappingSchema - Go API returns simple map[string]string
StrategyMappingSchema = dict[str, str]


# PendingSignalSchema - matches Go API pending signal format
class PendingSignalSchema(_StrictModel):
    id: Annotated[Number, AfterValidator(_integer), AfterValidator(_positive)]
    account_id: MinOneString
    symbol: MinOneString
    side: Literal["buy", "sell", "close"]
    score: Annotated[Number, AfterValidator(_integer), AfterValidator(_min(0))]
    strategy: str
    indicators: str
    status: str
    created_at: MinOneString
    expires_at: MinOneString
    arbitration_result: str
    arbitration_reason: str

    @field_validator("side", mode="before")
    @classmethod
    def _lower_side(cls, value: Any) -> Any:
        # z.preprocess((value) => typeof value === 'string' ? value.toLowerCase() : value, z.enum([...]))
        if isinstance(value, str):
            return value.lower()
        return value


class GoldbotBarSchema(_StrictModel):
    time: MinOneString
    open: Finite
    high: Finite
    low: Finite
    close: Finite
    volume: Finite | None = None
    ema20: Finite | None = None
    ema50: Finite | None = None
    ema200: Finite | None = None
    atr: Finite | None = None
    rsi: Finite | None = None
    macd: Finite | None = None
    macd_signal: Finite | None = None
    macd_hist: Finite | None = None
    adx: Finite | None = None
    bb_upper: Finite | None = None
    bb_lower: Finite | None = None
    bb_mid: Finite | None = None
    stoch_k: Finite | None = None
    stoch_d: Finite | None = None
    vol_sma: Finite | None = None
    fib_236: Finite | None = None
    fib_382: Finite | None = None
    fib_500: Finite | None = None
    fib_618: Finite | None = None
    fib_786: Finite | None = None
    pp: Finite | None = None
    r1: Finite | None = None
    r2: Finite | None = None
    s1: Finite | None = None
    s2: Finite | None = None
    candlestick_patterns: list[MinOneString] | None = None


# HarmonicPatternSchema - matches Go harmonic.HarmonicPattern
class HarmonicPatternSchema(_StrictModel):
    type: str
    direction: str
    timeframe: str
    score: Number
    x_price: Number
    a_price: Number
    b_price: Number
    c_price: Number
    d_price: Number
    ab_ratio: Number
    bc_ratio: Number
    cd_ratio: Number
    xd_ratio: Number
    completion_pct: Number | None = None
    is_active: bool | None = None
    reason: str
    # Trading-core detector output fields
    prz_low: Number | None = None
    prz_high: Number | None = None
    stop_loss: Number | None = None
    target_1: Number | None = None
    target_2: Number | None = None
    confidence: Number | None = None
    invalidated: bool | None = None
    status: str | None = None


class HarmonicContextSchema(_StrictModel):
    h4_patterns: list[HarmonicPatternSchema]
    h1_patterns: list[HarmonicPatternSchema]
    m30_patterns: list[HarmonicPatternSchema]
    active_pattern: HarmonicPatternSchema | None = None
    direction_bias: str
    score: Number
    summary: str


class TrendContextSchema(_StrictModel):
    d1_direction: str
    h4_direction: str
    h1_direction: str
    m30_direction: str
    consensus_direction: str
    consensus_strength: Number


# GoldbotPayloadSchema - matches Go API aurex.AnalysisPayload
class GoldbotPayloadSchema(_StrictModel):
    status: str | None = None
    timestamp: str | None = None
    account: AccountInfoSchema
    market: MarketDataSchema
    positions: list[PositionInfoSchema]
    indicators: dict[str, IndicatorPackSchema | None]
    market_status: MarketStatusSchema
    market_filters: MarketFiltersSchema | None = None
    strategy_mapping: dict[str, str]
    bars: dict[str, list[GoldbotBarSchema]] | None = None
    trend_context: TrendContextSchema | None = None
    harmonic_context: HarmonicContextSchema | None = None


# ---------------------------------------------------------------- safeParseLLM + helpers


def _with_all_optional(model: type[BaseModel]) -> type[BaseModel]:
    """镜像 zod ZodObject.partial():顶层字段全部变为可选(缺失→None),
    已提供字段仍按原 schema(含约束)校验。"""
    fields: dict[str, Any] = {}
    for name, field_info in model.model_fields.items():
        fields[name] = (field_info.annotation or Any, None)
    return create_model(
        f"Partial{model.__name__}",
        __config__=ConfigDict(strict=True),
        **fields,
    )


def safe_parse_llm(schema: type[BaseModel], raw: object) -> BaseModel | None:
    """镜像 safeParseLLM(schema, raw):先 strict 校验;失败则 partial 回退
    (缺失字段填默认值/None),仍失败返回 None。"""
    try:
        return schema.model_validate(raw)
    except ValidationError:
        pass

    partial = _with_all_optional(schema)
    try:
        return partial.model_validate(raw)
    except ValidationError:
        return None


def clean_sr_levels(data: Any) -> Any:
    """镜像 cleanSRLevels:过滤 support_levels / resistance_levels 中
    price 为 null / 非 number(含 bool)的条目。"""
    if not isinstance(data, dict):
        return data
    result = dict(data)

    def clean(key: str) -> None:
        value = result.get(key)
        if isinstance(value, list):
            result[key] = [
                level
                for level in value
                if isinstance(level, dict)
                and "price" in level
                and level["price"] is not None
                and isinstance(level["price"], (int, float))
                and not isinstance(level["price"], bool)
            ]

    clean("support_levels")
    clean("resistance_levels")
    return result
