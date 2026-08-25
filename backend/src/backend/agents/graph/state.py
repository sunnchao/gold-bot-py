"""LangGraph state definition (mirror of apps/app-agent/src/graph/state.ts).

Uses a TypedDict schema with Annotated list reducers for the accumulated
``logs`` / ``errors`` channels, matching the Annotation.Root reducer semantics.
Field values use the shared dataclass types from ``backend.agents.types``.
"""

from __future__ import annotations

from typing import Annotated, NotRequired, TypedDict

from backend.agents.types.agent import AISignalResult, AnalysisLog
from backend.agents.types.analysis import (
    ArbitrationResult,
    ChanlunAnalystResult,
    RiskAssessment,
    TechnicalAnalysis,
    WaveAnalystResult,
)
from backend.agents.types.comprehensive import (
    AccountView,
    BarView,
    ComprehensiveAnalysisResult,
    MarketInsight,
)
from backend.agents.types.goldbot import GoldbotPayload, PendingSignal
from backend.agents.types.trade_action import TradeAction


def _append_logs(existing: list[AnalysisLog], update: list[AnalysisLog]) -> list[AnalysisLog]:
    return [*existing, *update]


def _append_errors(existing: list[str], update: list[str]) -> list[str]:
    return [*existing, *update]


class AnalysisGraphState(TypedDict, total=False):
    """State schema for the gold-bot analysis workflow."""

    accountId: str
    symbol: str
    symbols: list[str]
    timestamp: str
    payload: NotRequired[GoldbotPayload | None]
    payloads: NotRequired[dict[str, GoldbotPayload] | None]
    barViews: NotRequired[dict[str, BarView] | None]
    accountViews: NotRequired[dict[str, AccountView] | None]
    pendingSignal: NotRequired[PendingSignal | None]
    pendingSignals: NotRequired[dict[str, PendingSignal | None] | None]
    comprehensiveAnalysis: NotRequired[ComprehensiveAnalysisResult | None]
    comprehensiveAnalyses: NotRequired[dict[str, ComprehensiveAnalysisResult] | None]
    marketInsights: NotRequired[dict[str, MarketInsight] | None]
    accountActions: NotRequired[dict[str, TradeAction] | None]
    technicalAnalysis: NotRequired[TechnicalAnalysis | None]
    technicalAnalyses: NotRequired[dict[str, TechnicalAnalysis] | None]
    waveAnalysis: NotRequired[WaveAnalystResult | None]
    waveAnalyses: NotRequired[dict[str, WaveAnalystResult] | None]
    chanlunAnalysis: NotRequired[ChanlunAnalystResult | None]
    chanlunAnalyses: NotRequired[dict[str, ChanlunAnalystResult] | None]
    riskAssessment: NotRequired[RiskAssessment | None]
    riskAssessments: NotRequired[dict[str, RiskAssessment] | None]
    arbitration: NotRequired[ArbitrationResult | None]
    arbitrations: NotRequired[dict[str, ArbitrationResult] | None]
    finalSignal: NotRequired[AISignalResult | None]
    finalSignals: NotRequired[dict[str, AISignalResult] | None]
    logs: Annotated[list[AnalysisLog], _append_logs]
    errors: Annotated[list[str], _append_errors]
    skipReason: NotRequired[str | None]
    tradeAction: NotRequired[TradeAction | None]
    tradeActions: NotRequired[dict[str, TradeAction] | None]
    duration: NotRequired[int | None]
    durations: NotRequired[dict[str, int] | None]
    skipFeishu: NotRequired[bool | None]
    forceAnalyze: NotRequired[bool | None]


AnalysisGraphStateType = AnalysisGraphState
