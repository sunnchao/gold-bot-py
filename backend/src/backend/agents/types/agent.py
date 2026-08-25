"""镜像 apps/app-agent/src/types/agent.ts(LangGraph AnalysisState 与 AI 信号结果类型)。

TS 字段命名保持原样(camelCase:accountId/symbol/timestamp/logs/errors 等)。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from backend.agents.types.analysis import (
    ArbitrationResult,
    ChanlunAnalystResult,
    ChanlunTheoryAnalysis,
    DowTheoryAnalysis,
    HarmonicTheoryAnalysis,
    RiskAssessment,
    SRLevels,
    TechnicalAnalysis,
    TradeRecommendation,
    WaveAnalystResult,
    WaveTheoryAnalysis,
)
from backend.agents.types.goldbot import GoldbotPayload, PendingSignal

__all__ = [
    "AISignalResult",
    "AnalysisLog",
    "AnalysisState",
    "DualTradePlan",
    "TradePlan",
    "TradePlanEntryZone",
    "TradePlanExecutionType",
    "TradePlanMode",
    "TradePlanRequestedOrderType",
    "TradePlanSide",
    "create_initial_state",
]

# --- Analysis Log ---

LogLevel = Literal["debug", "info", "warn", "error"]


@dataclass
class AnalysisLog:
    timestamp: str
    node: str
    message: str
    level: LogLevel


# --- AI Signal Result ---

TradePlanMode = Literal["observe", "veto", "approve", "modify", "reduce", "close"]
TradePlanSide = Literal["buy", "sell", "dual", "none"]
TradePlanExecutionType = Literal["market", "limit"]
TradePlanRequestedOrderType = Literal["market", "BUY_LIMIT", "SELL_LIMIT"]


@dataclass
class TradePlanEntryZone:
    min: float
    max: float


@dataclass
class TradePlan:
    schema_version: Literal["trade_plan.v1"]
    decision_id: str
    account_id: str
    symbol: str
    mode: TradePlanMode
    side: TradePlanSide
    confidence: float
    entry_zone: TradePlanEntryZone
    stop_loss: float
    take_profit: list[float]
    max_lots: float
    expires_at: str
    reason_codes: list[str]
    conflicts: list[str]
    narrative: str
    execution_type: TradePlanExecutionType | None = None
    requested_order_type: TradePlanRequestedOrderType | None = None
    add_on: bool = False
    add_on_type: Literal["favorable", "adverse"] | None = None
    add_on_level: int | None = None
    max_add_count: int | None = None
    max_total_lots: float | None = None


@dataclass
class DualTradePlan:
    buy: TradePlan
    sell: TradePlan
    is_dual_direction: bool


@dataclass
class AISignalResult:
    bias: Literal["bullish", "bearish", "neutral"]
    confidence: float
    exit_suggestion: Literal["hold", "close", "partial_close", "trail_stop", "none"]
    risk_alert: bool
    risk_level: Literal["low", "medium", "high", "extreme"] | None = None
    alert_reason: str | None = None
    suggested_sl: float | None = None  # AI 建议止损价格(基于支撑阻力位分析)
    suggested_tp: float | None = None  # AI 建议止盈价格(可选)
    max_position_size: float | None = None  # AI 建议最大仓位
    indicators_summary: str | None = None  # 技术指标摘要
    sr_levels: dict[str, list[float]] | None = None  # {"support": [...], "resistance": [...]}
    arbitration: dict[str, str | None] | None = None
    wave_analysis: dict[str, str | int | None] | None = None
    chanlun_analysis: dict[str, str] | None = None
    dow_theory: DowTheoryAnalysis | None = None
    wave_theory: WaveTheoryAnalysis | None = None
    chanlun_theory: ChanlunTheoryAnalysis | None = None
    harmonic_theory: HarmonicTheoryAnalysis | None = None
    trade_recommendation: TradeRecommendation | None = None
    trade_plan: TradePlan | None = None
    dual_trade_plan: DualTradePlan | None = None  # 双向下单支持


# --- Analysis State ---


@dataclass
class AnalysisState:
    accountId: str
    symbol: str
    timestamp: str
    logs: list[AnalysisLog] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    payload: GoldbotPayload | None = None
    pendingSignal: PendingSignal | None = None
    technicalAnalysis: TechnicalAnalysis | None = None
    waveAnalysis: WaveAnalystResult | None = None
    chanlunAnalysis: ChanlunAnalystResult | None = None
    srLevels: SRLevels | None = None
    arbitration: ArbitrationResult | None = None
    riskAssessment: RiskAssessment | None = None
    finalSignal: AISignalResult | None = None
    duration: int | None = None


def _now_iso() -> str:
    # 镜像 JS new Date().toISOString():UTC、毫秒精度、Z 后缀。
    return (
        datetime.now(UTC)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


# --- Factory ---


def create_initial_state(account_id: str, symbol: str) -> AnalysisState:
    """镜像 createInitialState(accountId, symbol)。"""
    return AnalysisState(
        accountId=account_id,
        symbol=symbol,
        timestamp=_now_iso(),
        logs=[],
        errors=[],
    )
