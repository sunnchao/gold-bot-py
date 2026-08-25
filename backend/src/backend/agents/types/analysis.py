"""镜像 apps/app-agent/src/types/analysis.ts。

TS interface → dataclass,字段名与 TS 完全一致(camelCase / snake_case 均按源文件)。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

__all__ = [
    "ArbitrationResult",
    "ChanlunAnalysis",
    "ChanlunAnalystResult",
    "ChanlunBar",
    "ChanlunFractal",
    "ChanlunHub",
    "ChanlunStroke",
    "ChanlunTheoryAnalysis",
    "DowTheoryAnalysis",
    "ElliottWaveAnalysis",
    "ElliottWaveLabel",
    "ElliottWaveSegment",
    "ElliottWaveSwingPoint",
    "ElliottWaveValidation",
    "HarmonicAnalysisResult",
    "HarmonicTheoryAnalysis",
    "RiskAssessment",
    "SRLevel",
    "SRLevels",
    "TechnicalAnalysis",
    "TimeframeAnalysis",
    "TradeRecommendation",
    "TradeSuggestion",
    "WaveAnalystResult",
    "WaveTargetLevels",
    "WaveTheoryAnalysis",
]

# --- Timeframe Analysis ---

Direction3 = Literal["bullish", "bearish", "neutral"]
TradeSuggestion = Literal["hold", "close", "partial_close", "trail_stop", "none"]


@dataclass
class TimeframeAnalysis:
    trend: Direction3
    strength: float  # 0-100
    key_level: float
    notes: str


# --- Technical Analysis ---


@dataclass
class TechnicalAnalysis:
    bias: Direction3
    confidence: float  # 0-100
    phase: Literal["trending", "ranging", "breakout", "reversal", "consolidation"]
    indicators_summary: str
    support_levels: list[SRLevel]
    resistance_levels: list[SRLevel]
    recommendation: TradeSuggestion
    rationale: str


# --- Support/Resistance Levels ---


@dataclass
class SRLevel:
    price: float
    type: Literal["support", "resistance"]
    strength: Literal["strong", "moderate", "weak"]
    timeframe: str
    touches: int


@dataclass
class SRLevels:
    support_levels: list[SRLevel]
    resistance_levels: list[SRLevel]
    recommendation: str
    rationale: str


# --- Arbitration Result ---


@dataclass
class ArbitrationResult:
    final_direction: Literal["buy", "sell", "hold", "close", "dual"]
    confidence: float  # 0-100
    primary_contradiction: str
    phase: str
    reasoning: str
    action: Literal["open", "close", "modify", "hold"]
    united_front_analysis: str
    dow_theory: DowTheoryAnalysis | None = None
    wave_theory: WaveTheoryAnalysis | None = None
    chanlun_theory: ChanlunTheoryAnalysis | None = None
    harmonic_theory: HarmonicTheoryAnalysis | None = None
    trade_recommendation: TradeRecommendation | None = None


# --- Harmonic Theory Analysis (arbitration sub-theory) ---


@dataclass
class HarmonicTheoryAnalysis:
    pattern: Literal[
        "gartley", "bat", "butterfly", "crab", "abcd", "cypher", "shark", "deep_crab", "none"
    ]
    direction: Direction3
    confidence: float  # 0-100
    rationale: str


# --- Dow Theory Analysis ---


@dataclass
class DowTheoryAnalysis:
    primary_trend: Direction3
    primary_phase: Literal["accumulation", "markup", "distribution", "markdown"]
    secondary_trend: Direction3
    short_term_trend: Direction3
    multi_tf_confirm: bool
    rationale: str


# --- Wave Theory Analysis ---


@dataclass
class WaveTheoryAnalysis:
    current_wave: str
    wave_direction: Literal["impulse_up", "impulse_down", "corrective", "unclear"]
    wave_count: str
    next_target: str
    confidence: float
    rationale: str


# --- Chanlun Theory Analysis ---


@dataclass
class ChanlunTheoryAnalysis:
    trend: Literal["up", "down", "range"]
    bi_direction: Literal["up", "down", "none"]
    duan_direction: Literal["up", "down", "none"]
    zhongshu_state: Literal["forming", "active", "breaking_up", "breaking_down", "none"]
    buy_sell_point: Literal["buy_1", "buy_2", "buy_3", "sell_1", "sell_2", "sell_3", "none"]
    confidence: float
    rationale: str


# --- Trade Recommendation ---


@dataclass
class TradeRecommendation:
    direction: Literal["buy", "sell", "hold"]
    entry_price: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: float | None = None
    risk_reward_ratio: float = 0.0
    position_size_lots: str = ""
    rationale: str = ""


# --- Risk Assessment ---


@dataclass
class RiskAssessment:
    riskLevel: Literal["low", "medium", "high", "extreme"]
    maxPositionSize: float
    suggestedSL: float  # 止损价格(支撑位 - ATR缓冲)
    suggestedTP: float | None = None  # 止盈价格(阻力位或 Fib extension 目标)
    warnings: list[str] = field(default_factory=list)
    addOn: bool | None = None  # NEW: whether to add to existing same-side position


# --- Elliott Wave Analysis ---


@dataclass
class ElliottWaveSwingPoint:
    index: int
    price: float
    type: Literal["high", "low"]


ElliottWaveLabel = Literal[1, 2, 3, 4, 5] | Literal["A", "B", "C"]


@dataclass
class ElliottWaveSegment:
    wave: ElliottWaveLabel
    startIndex: int
    endIndex: int
    startPrice: float
    endPrice: float
    direction: Literal["up", "down"]
    length: float


@dataclass
class ElliottWaveValidation:
    isValid: bool
    violations: list[str] = field(default_factory=list)


@dataclass
class ElliottWaveAnalysis:
    direction: Literal["bullish", "bearish"]
    swingPoints: list[ElliottWaveSwingPoint]
    impulseWaves: list[ElliottWaveSegment]
    correctiveWaves: list[ElliottWaveSegment]
    validation: ElliottWaveValidation
    confidence: float  # 0-100


@dataclass
class WaveTargetLevels:
    level_1_618: float
    level_2_0: float


@dataclass
class WaveAnalystResult:
    wave_confirmation: Literal["confirmed", "partial", "rejected"]
    extension_wave: Literal[1, 3, 5] | None
    corrective_type: Literal["zigzag", "flat", "triangle"] | None
    trend_strength: Literal["strong", "moderate", "weak"]
    target_levels: WaveTargetLevels
    confidence: float  # 0-100
    rationale: str


# --- Chanlun Analysis ---


@dataclass
class ChanlunBar:
    index: int
    open: float
    high: float
    low: float
    close: float


@dataclass
class ChanlunFractal:
    type: Literal["top", "bottom"]
    index: int
    price: float
    confirmed: Literal[True] = True


@dataclass
class ChanlunStroke:
    startIndex: int
    endIndex: int
    startPrice: float
    endPrice: float
    direction: Literal["up", "down"]
    high: float
    low: float


@dataclass
class ChanlunHub:
    startIndex: int
    endIndex: int
    high: float
    low: float
    strokeIndices: tuple[int, int, int]


@dataclass
class ChanlunAnalysis:
    processedBars: list[ChanlunBar]
    fractals: list[ChanlunFractal]
    strokes: list[ChanlunStroke]
    hubs: list[ChanlunHub]


@dataclass
class ChanlunAnalystResult:
    trend: Literal["up", "down", "range"]
    strength: Literal["strong", "moderate", "weak"]
    latest_signal: Literal["buy", "sell", "hold"]
    hub_state: Literal["forming", "active", "none"]
    confidence: float  # 0-100
    rationale: str


# --- Harmonic Pattern Analysis ---


@dataclass
class HarmonicAnalysisResult:
    detected_pattern: Literal[
        "gartley", "bat", "butterfly", "crab", "abcd", "cypher", "shark", "deep_crab", "none"
    ]
    direction: Direction3
    timeframe: str  # e.g. "H4", "H1", "M30"
    confidence: float  # 0-100
    d_zone_price: float  # PRZ D-point reference price from detector
    entry_zone: str  # e.g. "3265.50-3270.00"
    stop_loss: float  # suggested SL below/above D
    take_profit_1: float  # first TP target (38.2% or 61.8% retrace of CD)
    take_profit_2: float  # second TP target (full CD extension)
    rationale: str
    completion_pct: float | None = None  # 0-100% — how close price is to the PRZ (D point)
    is_active: bool | None = None
