"""镜像 apps/app-agent/src/types/goldbot.ts(Go API payload 契约类型)。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

__all__ = [
    "AccountInfo",
    "BarData",
    "GoldbotBar",
    "GoldbotPayload",
    "HarmonicContextPayload",
    "HarmonicPattern",
    "IndicatorPack",
    "MarketData",
    "MarketFilter",
    "MarketFilters",
    "MarketStatus",
    "PendingSignal",
    "PositionInfo",
    "StrategyMapping",
    "TrendContextPayload",
]


# --- Bar Data (legacy, kept for compatibility) ---


@dataclass
class BarData:
    time: str
    open: float
    high: float
    low: float
    close: float
    volume: float


# --- Indicator Pack (matches gold-bot v2 payload) ---


@dataclass
class IndicatorPack:
    close: float
    open: float
    high: float
    low: float
    ema20: float
    ema50: float
    ema200: float | None = None
    rsi: float = 0.0
    adx: float = 0.0
    atr: float = 0.0
    macd: float = 0.0
    macd_signal: float = 0.0
    macd_hist: float = 0.0
    bb_upper: float = 0.0
    bb_middle: float = 0.0
    bb_lower: float = 0.0
    stoch_k: float = 0.0
    stoch_d: float = 0.0
    vol_sma: float | None = None
    fib_236: float | None = None
    fib_382: float | None = None
    fib_500: float | None = None
    fib_618: float | None = None
    fib_786: float | None = None
    pp: float | None = None
    r1: float | None = None
    s1: float | None = None
    bars_count: float | None = None
    # Divergence indicators (from gold-bot indicator engine)
    macd_divergence: Literal["bullish", "bearish"] | None = None
    rsi_divergence: Literal["bullish", "bearish"] | None = None


# --- Market Data ---


@dataclass
class MarketData:
    symbol: str
    bid: float
    ask: float
    spread: float
    time: str | None = None


# --- Account Info (matches Go API aurex.AccountSummary, snake_case) ---


@dataclass
class AccountInfo:
    account_id: str
    equity: float
    balance: float
    margin: float
    free_margin: float
    currency: str
    leverage: float
    broker: str | None = None
    server_name: str | None = None
    connected: bool | None = None


# --- Position Info (matches Go API aurex.PositionSummary) ---


@dataclass
class PositionInfo:
    ticket: int
    strategy: str
    direction: Literal["buy", "sell", "BUY", "SELL"]
    entry_price: float
    current_price: float
    lots: float
    profit: float
    sl: float
    tp: float
    symbol: str | None = None
    magic: int | None = None
    pnl_percent: float | None = None
    hold_seconds: int | None = None
    hold_hours: float | None = None
    comment: str | None = None


# --- Market Status (matches Go API aurex.MarketStatus) ---


@dataclass
class MarketStatus:
    market_open: bool
    is_trade_allowed: bool
    tradeable: bool
    mt4_server_time: str | None = None


@dataclass
class MarketFilter:
    code: str
    severity: Literal["blocking", "warning"]
    message: str | None = None


@dataclass
class MarketFilters:
    blocked: bool = False
    blocking: list[MarketFilter] | None = field(default_factory=list)
    warnings: list[MarketFilter] | None = field(default_factory=list)
    reason_codes: list[str] | None = field(default_factory=list)


# --- Strategy Mapping (Go API returns simple map[string]string) ---

StrategyMapping = dict[str, str]


# --- Pending Signal ---


@dataclass
class PendingSignal:
    id: int
    account_id: str
    symbol: str
    side: Literal["buy", "sell", "close"]
    score: int
    strategy: str
    indicators: str
    status: str
    created_at: str
    expires_at: str
    arbitration_result: str
    arbitration_reason: str


# --- Rich Bar Data (matches Go domain.Bar fields exposed in v2 payload) ---


@dataclass
class GoldbotBar:
    time: str
    open: float
    high: float
    low: float
    close: float
    volume: float | None = None
    ema20: float | None = None
    ema50: float | None = None
    ema200: float | None = None
    atr: float | None = None
    rsi: float | None = None
    macd: float | None = None
    macd_signal: float | None = None
    macd_hist: float | None = None
    adx: float | None = None
    bb_upper: float | None = None
    bb_lower: float | None = None
    bb_mid: float | None = None
    stoch_k: float | None = None
    stoch_d: float | None = None
    vol_sma: float | None = None
    fib_236: float | None = None
    fib_382: float | None = None
    fib_500: float | None = None
    fib_618: float | None = None
    fib_786: float | None = None
    pp: float | None = None
    r1: float | None = None
    r2: float | None = None
    s1: float | None = None
    s2: float | None = None
    candlestick_patterns: list[str] | None = None


# --- Harmonic Pattern (matches Go harmonic.HarmonicPattern) ---


@dataclass
class HarmonicPattern:
    type: str  # "gartley"|"bat"|"butterfly"|"crab"|"abcd"|"cypher"|"shark"|"deep_crab"
    direction: str  # "bullish"|"bearish"
    timeframe: str  # "H4"|"H1"|"M30"
    score: float  # 0-100
    x_price: float
    a_price: float
    b_price: float
    c_price: float
    d_price: float
    ab_ratio: float
    bc_ratio: float
    cd_ratio: float
    xd_ratio: float
    reason: str
    completion_pct: float | None = None
    is_active: bool | None = None
    # Trading-core detector output fields (added for programmatic harmonic detection)
    prz_low: float | None = None
    prz_high: float | None = None
    stop_loss: float | None = None
    target_1: float | None = None
    target_2: float | None = None
    confidence: float | None = None
    invalidated: bool | None = None
    status: str | None = None


@dataclass
class HarmonicContextPayload:
    h4_patterns: list[HarmonicPattern]
    h1_patterns: list[HarmonicPattern]
    m30_patterns: list[HarmonicPattern]
    direction_bias: str  # "bullish"|"bearish"|"neutral"
    score: float
    summary: str
    active_pattern: HarmonicPattern | None = None


# --- Trend Context (matches Go aurex.TrendContextPayload) ---


@dataclass
class TrendContextPayload:
    d1_direction: str
    h4_direction: str
    h1_direction: str
    m30_direction: str
    consensus_direction: str
    consensus_strength: float


# --- Goldbot Payload ---


@dataclass
class GoldbotPayload:
    account: AccountInfo
    market: MarketData
    positions: list[PositionInfo]
    market_status: MarketStatus
    strategy_mapping: StrategyMapping
    indicators: dict[str, IndicatorPack | None] = field(default_factory=dict)
    status: str | None = None
    timestamp: str | None = None
    market_filters: MarketFilters | None = None
    bars: dict[str, list[GoldbotBar]] | None = None
    trend_context: TrendContextPayload | None = None
    harmonic_context: HarmonicContextPayload | None = None
