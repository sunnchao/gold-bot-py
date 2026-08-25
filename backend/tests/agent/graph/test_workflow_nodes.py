"""Mirror of apps/app-agent/src/graph/workflow-nodes.service.test.ts (market-first mode)."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest

from backend.agents.graph.market_insight_cache import MarketInsightCache
from backend.agents.graph.state import AnalysisGraphState
from backend.agents.graph.workflow_nodes import WorkflowNodes
from backend.agents.types.analysis import (
    ArbitrationResult,
    ChanlunAnalystResult,
    HarmonicAnalysisResult,
    RiskAssessment,
    TechnicalAnalysis,
    WaveAnalystResult,
    WaveTargetLevels,
)
from backend.agents.types.comprehensive import AccountView, BarView, MarketInsight, TradeIntent
from backend.agents.types.goldbot import (
    AccountInfo,
    GoldbotBar,
    GoldbotPayload,
    MarketData,
    MarketStatus,
)
from backend.agents.types.trade_action import DoNothingAction


def payload(symbol: str, bid: float = 3335, ask: float = 3335.2) -> GoldbotPayload:
    return GoldbotPayload(
        account=AccountInfo(
            account_id="81124211",
            equity=10000,
            balance=10000,
            margin=0,
            free_margin=10000,
            currency="USD",
            leverage=500,
        ),
        market=MarketData(symbol=symbol, bid=bid, ask=ask, spread=ask - bid),
        indicators={},
        positions=[],
        market_status=MarketStatus(market_open=True, is_trade_allowed=True, tradeable=True),
        strategy_mapping={},
        bars={
            "H1": [
                GoldbotBar(
                    time="1",
                    open=bid - 1,
                    high=bid + 2,
                    low=bid - 2,
                    close=bid,
                    atr=4,
                )
            ]
        },
    )


def build_insight() -> MarketInsight:
    return MarketInsight(
        technical=TechnicalAnalysis(
            bias="bullish",
            confidence=80,
            phase="trending",
            indicators_summary="trend",
            support_levels=[],
            resistance_levels=[],
            recommendation="none",
            rationale="trend",
        ),
        wave=WaveAnalystResult(
            wave_confirmation="partial",
            extension_wave=None,
            corrective_type=None,
            trend_strength="moderate",
            target_levels=WaveTargetLevels(level_1_618=3340, level_2_0=3350),
            confidence=60,
            rationale="wave",
        ),
        chanlun=ChanlunAnalystResult(
            trend="up",
            strength="moderate",
            latest_signal="buy",
            hub_state="active",
            confidence=60,
            rationale="chanlun",
        ),
        harmonic=HarmonicAnalysisResult(
            detected_pattern="none",
            direction="neutral",
            timeframe="N/A",
            confidence=0,
            d_zone_price=0,
            entry_zone="N/A",
            stop_loss=0,
            take_profit_1=0,
            take_profit_2=0,
            rationale="none",
        ),
        risk=RiskAssessment(
            riskLevel="low",
            maxPositionSize=0.1,
            suggestedSL=3328,
            suggestedTP=3345,
            warnings=[],
            addOn=False,
        ),
        arbitration=ArbitrationResult(
            final_direction="buy",
            confidence=80,
            primary_contradiction="none",
            phase="trend",
            reasoning="buy",
            action="open",
            united_front_analysis="aligned",
        ),
        sr_levels={"support": [], "resistance": []},
        trend_bias="bullish",
        confidence=80,
        trade_intent=TradeIntent(
            direction="buy",
            entry_trigger="market",
            entry_offset_atr=0,
            stop_loss_atr=1.5,
            take_profit_1_atr=3,
            rationale="buy",
        ),
    )


def make_config() -> SimpleNamespace:
    return SimpleNamespace(
        market_first_enabled=True,
        market_insight_ttl_ms=600000,
        price_deviation_tolerance_atr=0.25,
    )


def make_service(
    insight: MarketInsight, fallback_insight: MarketInsight
) -> tuple[WorkflowNodes, MarketInsightCache, Any]:
    cache: MarketInsightCache[MarketInsight] = MarketInsightCache(ttl_ms=600000)
    calls = {"run_market_insight": 0, "decide_account_actions": 0}

    class FakeAnalyst:
        async def run_market_insight(
            self, bar_view: BarView, source_symbol: str, all_current_prices: dict[str, float]
        ) -> MarketInsight:
            calls["run_market_insight"] += 1
            return insight if bar_view.useShared else fallback_insight

        async def decide_account_actions(
            self,
            insight_value: MarketInsight,
            views: list[AccountView],
            benchmark_price: float,
            atr: float,
            deviation_tolerance_atr: float,
        ) -> dict[str, DoNothingAction]:
            calls["decide_account_actions"] += 1
            return {
                views[0].symbol: DoNothingAction(
                    type="do_nothing", account_id=views[0].accountId, reasoning="test"
                )
            }

    service = WorkflowNodes(
        goldbot_api=SimpleNamespace(),  # unused in this path
        comprehensive_analyst=FakeAnalyst(),
        publisher=SimpleNamespace(),
        logger=SimpleNamespace(
            info=lambda *a, **k: None,
            warn=lambda *a, **k: None,
            error=lambda *a, **k: None,
        ),
        config=make_config(),
        bar_source=None,
        market_insight_cache=cache,
    )
    return service, cache, calls


def market_first_state_for_two_symbols() -> AnalysisGraphState:
    return {
        "accountId": "81124211",
        "symbol": "GOLDm#",
        "symbols": ["GOLDm#", "XAUUSD"],
        "timestamp": "2026-08-12T00:00:00.000Z",
        "payloads": {"GOLDm#": payload("GOLDm#"), "XAUUSD": payload("XAUUSD")},
        "barViews": {
            "GOLDm#": BarView(
                canonicalSymbol="XAUUSD",
                sourceAccount="90011087",
                sourceSymbol="XAUUSD",
                useShared=True,
                payload=payload("XAUUSD"),
                benchmarkPrice=3335.1,
                atr=4,
            ),
            "XAUUSD": BarView(
                canonicalSymbol="XAUUSD",
                sourceAccount="90011087",
                sourceSymbol="XAUUSD",
                useShared=False,
                payload=payload("XAUUSD"),
                benchmarkPrice=3335.1,
                atr=4,
            ),
        },
        "accountViews": {
            "GOLDm#": AccountView(
                accountId="81124211",
                symbol="GOLDm#",
                payload=payload("GOLDm#"),
                aiSymbols=["GOLDm#", "XAUUSD"],
                realtimePrice=3335.1,
                atr=4,
            ),
            "XAUUSD": AccountView(
                accountId="81124211",
                symbol="XAUUSD",
                payload=payload("XAUUSD"),
                aiSymbols=["GOLDm#", "XAUUSD"],
                realtimePrice=3335.1,
                atr=4,
            ),
        },
        "logs": [],
        "errors": [],
    }


@pytest.mark.asyncio
async def test_uses_the_shared_cache_only_for_shared_bar_views() -> None:
    insight = build_insight()
    fallback_insight = replace(
        insight,
        confidence=55,
        trend_bias="neutral",
        arbitration=replace(
            insight.arbitration,
            final_direction="hold",
            action="hold",
            reasoning="fallback account-local bars",
        ),
    )
    service, cache, calls = make_service(insight, fallback_insight)

    result = await service.comprehensive_analysis(market_first_state_for_two_symbols())

    assert calls["run_market_insight"] == 2
    assert calls["decide_account_actions"] == 2
    assert result["marketInsights"] == {"GOLDm#": insight, "XAUUSD": fallback_insight}
    cached = cache.get("XAUUSD")
    assert cached is not None
    assert cached.insight is insight


@pytest.mark.asyncio
async def test_does_not_write_fallback_bar_insights_into_the_shared_market_cache() -> None:
    insight = build_insight()
    fallback_insight = replace(
        insight,
        confidence=45,
        arbitration=replace(
            insight.arbitration,
            final_direction="hold",
            action="hold",
            reasoning="account fallback",
        ),
    )
    service, cache, calls = make_service(insight, fallback_insight)

    state: AnalysisGraphState = {
        "accountId": "81124211",
        "symbol": "GOLDm#",
        "symbols": ["GOLDm#"],
        "timestamp": "2026-08-12T00:00:00.000Z",
        "payloads": {"GOLDm#": payload("GOLDm#")},
        "barViews": {
            "GOLDm#": BarView(
                canonicalSymbol="XAUUSD",
                sourceAccount="81124211",
                sourceSymbol="GOLDm#",
                useShared=False,
                payload=payload("GOLDm#"),
                benchmarkPrice=3335.1,
                atr=4,
            ),
        },
        "accountViews": {
            "GOLDm#": AccountView(
                accountId="81124211",
                symbol="GOLDm#",
                payload=payload("GOLDm#"),
                aiSymbols=["GOLDm#"],
                realtimePrice=3335.1,
                atr=4,
            ),
        },
        "logs": [],
        "errors": [],
    }

    await service.comprehensive_analysis(state)

    assert calls["run_market_insight"] == 1
    assert cache.get("XAUUSD") is None


@pytest.mark.asyncio
async def test_non_market_first_comprehensive_analysis_wraps_analyst_result_for_publish_payload() -> None:
    insight = build_insight()
    calls = {"run": 0}

    class FakeAnalyst:
        async def run(self, payload_value, symbol, pending_signal, all_current_prices):
            calls["run"] += 1
            return insight

    service = WorkflowNodes(
        goldbot_api=SimpleNamespace(),
        comprehensive_analyst=FakeAnalyst(),
        publisher=SimpleNamespace(),
        logger=SimpleNamespace(
            info=lambda *a, **k: None,
            warn=lambda *a, **k: None,
            error=lambda *a, **k: None,
        ),
        config=SimpleNamespace(market_first_enabled=False),
        bar_source=None,
        market_insight_cache=None,
    )

    result = await service.comprehensive_analysis(
        {
            "accountId": "81124211",
            "symbol": "XAUUSD",
            "symbols": ["XAUUSD"],
            "timestamp": "2026-08-12T00:00:00.000Z",
            "payloads": {"XAUUSD": payload("XAUUSD")},
            "pendingSignals": {},
            "logs": [],
            "errors": [],
        }
    )

    assert calls["run"] == 1
    assert "errors" not in result
    assert result["comprehensiveAnalyses"]["XAUUSD"]["arbitration"].final_direction == "buy"
    assert result["arbitrations"]["XAUUSD"].final_direction == "buy"
