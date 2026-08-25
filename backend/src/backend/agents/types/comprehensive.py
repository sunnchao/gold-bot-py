"""镜像 apps/app-agent/src/types/comprehensive.ts。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from backend.agents.types.analysis import (
    ArbitrationResult,
    ChanlunAnalystResult,
    HarmonicAnalysisResult,
    RiskAssessment,
    TechnicalAnalysis,
    WaveAnalystResult,
)
from backend.agents.types.goldbot import GoldbotPayload, PendingSignal
from backend.agents.types.trade_action import TradeAction

__all__ = [
    "AccountView",
    "BarView",
    "ComprehensiveAnalysisResult",
    "MarketInsight",
    "TradeIntent",
]


@dataclass
class ComprehensiveAnalysisResult:
    technical: TechnicalAnalysis
    wave: WaveAnalystResult
    chanlun: ChanlunAnalystResult
    harmonic: HarmonicAnalysisResult
    risk: RiskAssessment
    arbitration: ArbitrationResult
    tradeAction: TradeAction | None = None  # function calling 下单动作(第二阶段产生)


@dataclass
class TradeIntent:
    direction: Literal["buy", "sell", "hold"]
    entry_trigger: Literal["market", "pullback", "breakout", "none"]
    entry_offset_atr: float
    stop_loss_atr: float
    take_profit_1_atr: float
    take_profit_2_atr: float | None = None
    rationale: str = ""


@dataclass
class MarketInsight:
    technical: TechnicalAnalysis
    wave: WaveAnalystResult
    chanlun: ChanlunAnalystResult
    harmonic: HarmonicAnalysisResult
    risk: RiskAssessment
    arbitration: ArbitrationResult
    sr_levels: dict[str, list[float]]  # {"support": [...], "resistance": [...]}
    trend_bias: Literal["bullish", "bearish", "neutral"]
    confidence: float
    trade_intent: TradeIntent


@dataclass
class BarView:
    canonicalSymbol: str
    sourceAccount: str
    sourceSymbol: str
    useShared: bool
    payload: GoldbotPayload
    benchmarkPrice: float
    atr: float


@dataclass
class AccountView:
    accountId: str
    symbol: str
    payload: GoldbotPayload
    aiSymbols: list[str]
    realtimePrice: float
    atr: float
    pendingSignal: PendingSignal | None = None
