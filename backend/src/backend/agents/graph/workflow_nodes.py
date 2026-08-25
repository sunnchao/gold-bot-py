"""Workflow node implementations (mirror of apps/app-agent/src/graph/workflow-nodes.service.ts).

External collaborators (goldbot API, comprehensive analyst, publisher, bar source,
market-insight cache, logger) are injected via Protocols so every path runs
offline in tests with fakes.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Literal, Protocol

from backend.agents.graph.access import as_dict_shallow, field_of
from backend.agents.graph.compose import compose_final_signal
from backend.agents.graph.market_insight_cache import MarketInsightCache, MarketInsightCacheValue
from backend.agents.graph.state import AnalysisGraphState
from backend.agents.types.agent import AISignalResult, AnalysisLog
from backend.agents.types.comprehensive import AccountView, BarView, ComprehensiveAnalysisResult, MarketInsight
from backend.agents.types.goldbot import GoldbotPayload, PendingSignal
from backend.agents.types.trade_action import TradeAction


def _monotonic_ms() -> int:
    return int(time.monotonic() * 1000)


def _now_iso() -> str:
    from datetime import UTC, datetime

    return (
        datetime.now(UTC)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def create_log(
    node: str,
    message: str,
    level: Literal["debug", "info", "warn", "error"] = "info",
) -> AnalysisLog:
    return AnalysisLog(timestamp=_now_iso(), node=node, message=message, level=level)


def current_price(payload: GoldbotPayload | None) -> float:
    """Mirror of the module-level currentPrice() helper."""
    market = field_of(payload, "market")
    bid = field_of(market, "bid")
    ask = field_of(market, "ask")
    bid_f = float(bid) if isinstance(bid, (int, float)) else float("nan")
    ask_f = float(ask) if isinstance(ask, (int, float)) else float("nan")
    if _finite(bid_f) and _finite(ask_f):
        return (bid_f + ask_f) / 2
    if _finite(bid_f):
        return bid_f
    if _finite(ask_f):
        return ask_f
    return 0.0


def _finite(value: float) -> bool:
    return value == value and value not in (float("inf"), float("-inf"))


_PREFERRED_ATR_TIMEFRAMES = ("H1", "M30", "M15", "H4")
_DEFAULT_ATR_PERIOD = 14


def _finite_number(value: Any) -> float | None:
    if not isinstance(value, (int, float)):
        return None
    return value if value == value and value not in (float("inf"), float("-inf")) else None


def _bars_for(payload: GoldbotPayload | None, timeframe: str) -> list[dict[str, Any]]:
    bars = field_of(payload, "bars")
    if not isinstance(bars, dict):
        return []
    exact = bars.get(timeframe)
    if isinstance(exact, list):
        return [bar for bar in exact if isinstance(bar, dict)]
    for candidate, matched in bars.items():
        if str(candidate).upper() == timeframe and isinstance(matched, list):
            return [bar for bar in matched if isinstance(bar, dict)]
    return []


def _true_range(bar: dict[str, Any], previous_close: float | None) -> float | None:
    high = _finite_number(bar.get("high"))
    low = _finite_number(bar.get("low"))
    if high is None or low is None:
        return None
    if previous_close is None:
        return high - low
    return max(high - low, abs(high - previous_close), abs(low - previous_close))


def atr_of(payload: GoldbotPayload | None, period: int = _DEFAULT_ATR_PERIOD) -> float:
    """Mirror of BarSourceService.atrOf (1:1)."""
    for timeframe in _PREFERRED_ATR_TIMEFRAMES:
        bars = _bars_for(payload, timeframe)
        for bar in reversed(bars):
            atr = _finite_number(bar.get("atr"))
            if atr is not None and atr > 0:
                return atr

        if len(bars) < 2:
            continue
        ranges: list[float] = []
        for index, bar in enumerate(bars):
            previous_close = _finite_number(bars[index - 1].get("close")) if index > 0 else None
            range_ = _true_range(bar, previous_close)
            if range_ is not None and range_ > 0:
                ranges.append(range_)
        sample = ranges[-period:]
        if len(sample) > 0:
            return sum(sample) / len(sample)

    indicators = field_of(payload, "indicators")
    if isinstance(indicators, dict):
        for timeframe in _PREFERRED_ATR_TIMEFRAMES:
            indicator = indicators.get(timeframe) or indicators.get(timeframe.lower())
            atr = _finite_number(field_of(indicator, "atr"))
            if atr is not None and atr > 0:
                return atr
    return 0.0


class LoggerLike(Protocol):
    def info(self, message: str, **context: Any) -> None: ...
    def warn(self, message: str, **context: Any) -> None: ...
    def error(self, message: str, **context: Any) -> None: ...


class GoldbotApiLike(Protocol):
    async def fetch_analysis_payload(self, account_id: str, symbol: str) -> GoldbotPayload: ...
    async def fetch_pending_signal(self, account_id: str, symbol: str) -> PendingSignal | None: ...


class BarSourceLike(Protocol):
    def bar_source_for(self, account_id: str, symbol: str) -> dict[str, Any]: ...
    async def account_symbols(self, account_id: str) -> list[str]: ...


class ComprehensiveAnalystLike(Protocol):
    async def run(
        self,
        payload: GoldbotPayload,
        symbol: str,
        pending_signal: PendingSignal | None,
        all_current_prices: dict[str, float],
    ) -> ComprehensiveAnalysisResult: ...
    async def run_market_insight(
        self,
        bar_view: BarView,
        source_symbol: str,
        all_current_prices: dict[str, float],
    ) -> MarketInsight: ...
    async def decide_account_actions(
        self,
        insight: MarketInsight,
        account_views: list[AccountView],
        benchmark_price: float,
        atr: float,
        deviation_tolerance_atr: float,
    ) -> dict[str, TradeAction]: ...


class PublisherLike(Protocol):
    async def publish(
        self,
        account_id: str,
        symbol: str,
        result: AISignalResult,
        skip_feishu: bool | None = False,
    ) -> None: ...


class WorkflowNodesConfigLike(Protocol):
    market_first_enabled: bool | None
    market_insight_ttl_ms: int | None
    price_deviation_tolerance_atr: float | None


class WorkflowNodes:
    """Implements the analysis workflow nodes (fetchData/dispatch/compose/etc.)."""

    def __init__(
        self,
        goldbot_api: GoldbotApiLike,
        comprehensive_analyst: ComprehensiveAnalystLike,
        publisher: PublisherLike,
        logger: LoggerLike | None = None,
        config: WorkflowNodesConfigLike | None = None,
        bar_source: BarSourceLike | None = None,
        market_insight_cache: MarketInsightCache[MarketInsight] | None = None,
    ) -> None:
        self.goldbot_api = goldbot_api
        self.comprehensive_analyst = comprehensive_analyst
        self.publisher = publisher
        self.logger = logger
        self.config = config
        self.bar_source = bar_source
        self.market_insight_cache = market_insight_cache

    def _get_symbols(self, state: AnalysisGraphState) -> list[str]:
        if state.get("symbols") is not None:
            return state["symbols"]
        symbol = state.get("symbol")
        return [symbol] if symbol else []

    def _get_primary_symbol(self, state: AnalysisGraphState) -> str:
        symbols = self._get_symbols(state)
        return symbols[0] if symbols else state.get("symbol", "")

    def _select_primary(self, values: dict[str, Any] | None, state: AnalysisGraphState) -> Any:
        if values is None:
            return None
        return values.get(self._get_primary_symbol(state))

    # ─── fetchData ──────────────────────────────────────────────────────────

    async def fetch_data(self, state: AnalysisGraphState) -> dict[str, Any]:
        account_id = state.get("accountId", "")
        symbols = self._get_symbols(state)
        logger = self.logger
        if logger is not None:
            logger.info("fetchData: fetching payloads + pending signals", accountId=account_id, symbols=symbols)

        try:
            entries: list[tuple[str, dict[str, Any]]] = []
            for symbol in symbols:
                payload, pending_signal = await asyncio.gather(
                    self.goldbot_api.fetch_analysis_payload(account_id, symbol),
                    self.goldbot_api.fetch_pending_signal(account_id, symbol),
                )
                entries.append((symbol, {"payload": payload, "pendingSignal": pending_signal or None}))

            payloads: dict[str, GoldbotPayload] = {symbol: value["payload"] for symbol, value in entries}
            pending_signals: dict[str, PendingSignal | None] = {
                symbol: value["pendingSignal"] for symbol, value in entries
            }
            primary_symbol = symbols[0] if symbols else ""

            if self.config is not None and self.config.market_first_enabled is True and self.bar_source is not None:
                view_entries: list[tuple[str, dict[str, Any]]] = []
                for symbol in symbols:
                    account_payload = payloads.get(symbol)
                    resolution = self.bar_source.bar_source_for(account_id, symbol)
                    bar_payload = account_payload
                    source_account = str(field_of(resolution, "sourceAccount"))
                    source_symbol = str(field_of(resolution, "sourceSymbol"))
                    use_shared = bool(field_of(resolution, "useShared"))

                    if use_shared:
                        try:
                            bar_payload = await self.goldbot_api.fetch_analysis_payload(
                                source_account,
                                source_symbol,
                            )
                        except Exception as err:  # noqa: BLE001
                            if logger is not None:
                                logger.warn(
                                    "fetchData: shared BAR payload failed, falling back to account payload",
                                    accountId=account_id,
                                    symbol=symbol,
                                    sourceAccount=source_account,
                                    sourceSymbol=source_symbol,
                                    err=str(err),
                                )
                            source_account = account_id
                            source_symbol = symbol
                            use_shared = False
                            bar_payload = account_payload

                    account_symbols = await self.bar_source.account_symbols(account_id)
                    view_entries.append(
                        (
                            symbol,
                            {
                                "barView": {
                                    "canonicalSymbol": field_of(resolution, "canonicalSymbol"),
                                    "sourceAccount": source_account,
                                    "sourceSymbol": source_symbol,
                                    "useShared": use_shared,
                                    "payload": bar_payload,
                                    "benchmarkPrice": current_price(bar_payload),
                                    "atr": atr_of(bar_payload),
                                },
                                "accountView": {
                                    "accountId": account_id,
                                    "symbol": symbol,
                                    "payload": account_payload,
                                    "pendingSignal": pending_signals.get(symbol),
                                    "aiSymbols": account_symbols,
                                    "realtimePrice": current_price(account_payload),
                                    "atr": atr_of(account_payload),
                                },
                            },
                        ),
                    )

                return {
                    "payload": payloads.get(primary_symbol),
                    "payloads": payloads,
                    "barViews": {symbol: value["barView"] for symbol, value in view_entries},
                    "accountViews": {symbol: value["accountView"] for symbol, value in view_entries},
                    "pendingSignal": pending_signals.get(primary_symbol),
                    "pendingSignals": pending_signals,
                    "logs": [
                        create_log("fetchData", f"Fetched market/account views for {', '.join(symbols)}"),
                    ],
                }

            return {
                "payload": payloads.get(primary_symbol),
                "payloads": payloads,
                "pendingSignal": pending_signals.get(primary_symbol),
                "pendingSignals": pending_signals,
                "logs": [create_log("fetchData", f"Fetched payloads for {', '.join(symbols)}")],
            }
        except Exception as err:  # noqa: BLE001
            msg = str(err)
            if logger is not None:
                logger.error("fetchData failed", err=msg, accountId=account_id, symbols=symbols)
            return {
                "errors": [f"fetchData: {msg}"],
                "logs": [create_log("fetchData", f"Error: {msg}", "error")],
            }

    # ─── dispatchAnalysis ───────────────────────────────────────────────────

    async def dispatch_analysis(self, state: AnalysisGraphState) -> dict[str, Any]:
        symbols = self._get_symbols(state)
        logger = self.logger

        if state.get("forceAnalyze"):
            if logger is not None:
                logger.info(
                    "dispatchAnalysis: force mode — analyzing all symbols despite market closed",
                    symbols=symbols,
                )
            return {
                "symbols": symbols,
                "logs": [
                    create_log(
                        "dispatchAnalysis",
                        f"Force mode: dispatched analysis for {', '.join(symbols)} (market closed override)",
                    ),
                ],
            }

        payloads = state.get("payloads") or {}
        open_symbols = [
            s
            for s in symbols
            if self._market_open_of(payloads.get(s)) is not False
        ]
        closed_symbols = [
            s
            for s in symbols
            if self._market_open_of(payloads.get(s)) is False
        ]

        if len(closed_symbols) > 0 and logger is not None:
            logger.info(
                "dispatchAnalysis: skipping closed-market symbols",
                closedSymbols=closed_symbols,
                openSymbols=open_symbols,
            )

        if len(open_symbols) == 0:
            return {
                "skipReason": "All markets closed",
                "logs": [
                    create_log(
                        "dispatchAnalysis",
                        f"All markets closed — skipping analysis for {', '.join(symbols)}",
                        "warn",
                    ),
                ],
            }

        return {
            "symbols": open_symbols,
            "logs": [create_log("dispatchAnalysis", f"Dispatched analysis for {', '.join(open_symbols)}")],
        }

    @staticmethod
    def _market_open_of(payload: Any) -> bool | None:
        if payload is None:
            return None
        market_status = field_of(payload, "market_status")
        return field_of(market_status, "market_open")

    # ─── comprehensiveAnalysis ──────────────────────────────────────────────

    async def comprehensive_analysis(self, state: AnalysisGraphState) -> dict[str, Any]:
        payloads = state.get("payloads") or {}
        if len(payloads) == 0:
            return {
                "errors": ["comprehensiveAnalysis: No payload available"],
                "logs": [create_log("comprehensiveAnalysis", "No payload - skipping", "warn")],
            }

        logger = self.logger
        try:
            all_current_prices: dict[str, float] = {}
            for sym, pl in payloads.items():
                if pl is None:
                    continue
                market = field_of(pl, "market")
                bid = field_of(market, "bid")
                ask = field_of(market, "ask")
                price = bid if bid else (ask if ask else 0)
                if price > 0:
                    all_current_prices[sym] = price

            if (
                self.config is not None
                and self.config.market_first_enabled is True
                and self.market_insight_cache is not None
            ):
                return await self._comprehensive_market_first(state, payloads, all_current_prices)

            entries: list[tuple[str, dict[str, Any]]] = []
            for symbol in self._get_symbols(state):
                payload = payloads.get(symbol)
                if payload is None:
                    continue
                result = await self.comprehensive_analyst.run(
                    payload,
                    symbol,
                    (state.get("pendingSignals") or {}).get(symbol),
                    all_current_prices,
                )
                entries.append((symbol, {"result": as_dict_shallow(result)}))

            return self._analysis_result_payload(state, entries, market_first=False)
        except Exception as err:  # noqa: BLE001
            msg = str(err)
            if logger is not None:
                logger.error("comprehensiveAnalysis failed", err=msg)
            return {
                "errors": [f"comprehensiveAnalysis: {msg}"],
                "logs": [create_log("comprehensiveAnalysis", f"Error: {msg}", "error")],
            }

    async def _comprehensive_market_first(
        self,
        state: AnalysisGraphState,
        payloads: dict[str, GoldbotPayload],
        all_current_prices: dict[str, float],
    ) -> dict[str, Any]:
        cache = self.market_insight_cache
        analyst = self.comprehensive_analyst
        assert cache is not None
        assert analyst is not None
        assert self.config is not None

        bar_views = state.get("barViews") or {}
        account_views = state.get("accountViews") or {}

        entries: list[tuple[str, dict[str, Any]]] = []
        for symbol in self._get_symbols(state):
            if payloads.get(symbol) is None or bar_views.get(symbol) is None or account_views.get(symbol) is None:
                continue
            bar_view = bar_views[symbol]
            account_view = account_views[symbol]

            async def build_insight(bar_view: BarView = bar_view) -> MarketInsightCacheValue[MarketInsight]:
                insight = await analyst.run_market_insight(
                    bar_view,
                    bar_view.sourceSymbol,
                    all_current_prices,
                )
                return MarketInsightCacheValue(
                    insight=insight,
                    benchmark_price=bar_view.benchmarkPrice,
                    computed_at=_monotonic_ms(),
                    source_account=bar_view.sourceAccount,
                )

            if bar_view.useShared:
                cached = await cache.get_or_build(bar_view.canonicalSymbol, build_insight)
            else:
                cached = await build_insight()

            actions = await analyst.decide_account_actions(
                cached.insight,
                [account_view],
                cached.benchmark_price,
                bar_view.atr,
                self.config.price_deviation_tolerance_atr or 0.25,
            )
            action = actions.get(symbol)
            result = as_dict_shallow(cached.insight)
            result["tradeAction"] = action
            entries.append((symbol, {"result": result, "insight": cached.insight, "action": action}))

        return self._analysis_result_payload(state, entries, market_first=True)

    def _analysis_result_payload(
        self,
        state: AnalysisGraphState,
        entries: list[tuple[str, dict[str, Any]]],
        market_first: bool,
    ) -> dict[str, Any]:
        results = {symbol: value["result"] for symbol, value in entries}
        payload: dict[str, Any] = {
            "comprehensiveAnalysis": self._select_primary(results, state),
            "comprehensiveAnalyses": results,
            "technicalAnalysis": self._select_primary(
                {symbol: value["result"].get("technical") for symbol, value in entries},
                state,
            ),
            "technicalAnalyses": {symbol: value["result"]["technical"] for symbol, value in entries},
            "waveAnalysis": self._select_primary(
                {symbol: value["result"].get("wave") for symbol, value in entries},
                state,
            ),
            "waveAnalyses": {symbol: value["result"]["wave"] for symbol, value in entries},
            "chanlunAnalysis": self._select_primary(
                {symbol: value["result"].get("chanlun") for symbol, value in entries},
                state,
            ),
            "chanlunAnalyses": {symbol: value["result"]["chanlun"] for symbol, value in entries},
            "riskAssessment": self._select_primary(
                {symbol: value["result"].get("risk") for symbol, value in entries},
                state,
            ),
            "riskAssessments": {symbol: value["result"]["risk"] for symbol, value in entries},
            "arbitration": self._select_primary(
                {symbol: value["result"].get("arbitration") for symbol, value in entries},
                state,
            ),
            "arbitrations": {symbol: value["result"]["arbitration"] for symbol, value in entries},
        }
        if market_first:
            payload["marketInsights"] = {symbol: value["insight"] for symbol, value in entries}
            payload["accountActions"] = {symbol: value["action"] for symbol, value in entries}
            payload["tradeAction"] = self._select_primary(
                {symbol: value["action"] for symbol, value in entries},
                state,
            )
            payload["tradeActions"] = {
                symbol: value["action"] for symbol, value in entries if value["action"] is not None
            }
            message = f"Completed market-first analysis for {', '.join(results)}"
        else:
            payload["tradeAction"] = self._select_primary(
                {symbol: value["result"].get("tradeAction") for symbol, value in entries},
                state,
            )
            payload["tradeActions"] = {
                symbol: value["result"]["tradeAction"]
                for symbol, value in entries
                if value["result"].get("tradeAction") is not None
            }
            message = f"Completed comprehensive analysis for {', '.join(results)}"
        payload["logs"] = [create_log("comprehensiveAnalysis", message)]
        return payload

    # ─── composeSignal ──────────────────────────────────────────────────────

    async def compose_signal(self, state: AnalysisGraphState) -> dict[str, Any]:
        logger = self.logger
        try:
            entries: list[tuple[str, AISignalResult]] = []
            payloads = state.get("payloads") or {}
            for symbol in self._get_symbols(state):
                if payloads.get(symbol) is None:
                    continue
                per_symbol_state: AnalysisGraphState = {
                    **state,
                    "symbol": symbol,
                    "payload": payloads.get(symbol),
                    "pendingSignal": (state.get("pendingSignals") or {}).get(symbol),
                    "comprehensiveAnalysis": (state.get("comprehensiveAnalyses") or {}).get(symbol),
                    "technicalAnalysis": (state.get("technicalAnalyses") or {}).get(symbol),
                    "waveAnalysis": (state.get("waveAnalyses") or {}).get(symbol),
                    "chanlunAnalysis": (state.get("chanlunAnalyses") or {}).get(symbol),
                    "riskAssessment": (state.get("riskAssessments") or {}).get(symbol),
                    "arbitration": (state.get("arbitrations") or {}).get(symbol),
                    "tradeAction": (state.get("tradeActions") or {}).get(symbol),
                }
                signal = compose_final_signal(per_symbol_state)
                if signal is not None:
                    entries.append((symbol, signal))

            final_signals = dict(entries)
            primary_symbol = self._get_primary_symbol(state)
            message = f"Composed signals for {', '.join(final_signals)}"
            if len(entries) == 0:
                message += " — all dropped (no arbitration)"
            return {
                "finalSignal": final_signals.get(primary_symbol),
                "finalSignals": final_signals,
                "logs": [create_log("composeSignal", message)],
            }
        except Exception as err:  # noqa: BLE001
            msg = str(err)
            if logger is not None:
                logger.error("composeSignal failed", err=msg)
            return {
                "errors": [f"composeSignal: {msg}"],
                "logs": [create_log("composeSignal", f"Error: {msg}", "error")],
            }

    # ─── publishResult ──────────────────────────────────────────────────────

    async def publish_result(self, state: AnalysisGraphState) -> dict[str, Any]:
        account_id = state.get("accountId", "")
        symbols = self._get_symbols(state)
        final_signals = state.get("finalSignals") or {}
        logger = self.logger

        if len(final_signals) == 0 and state.get("finalSignal") is None:
            return {"logs": [create_log("publishResult", "No final signal to publish", "warn")]}

        try:
            durations: dict[str, int] = {
                symbol: (state.get("duration") or 0) if len(symbols) == 1 else 0 for symbol in symbols
            }

            async def publish_one(symbol: str) -> None:
                final_signal = final_signals.get(symbol)
                if final_signal is None and len(symbols) == 1:
                    final_signal = state.get("finalSignal")
                if final_signal is None:
                    return
                started_at = _monotonic_ms()
                await self.publisher.publish(account_id, symbol, final_signal, state.get("skipFeishu"))
                durations[symbol] = _monotonic_ms() - started_at

            await asyncio.gather(*(publish_one(symbol) for symbol in symbols))
            return {
                "durations": durations,
                "logs": [create_log("publishResult", f"Published results for {', '.join(symbols)}")],
            }
        except Exception as err:  # noqa: BLE001
            msg = str(err)
            if logger is not None:
                logger.error("publishResult failed", err=msg)
            return {
                "errors": [f"publishResult: {msg}"],
                "logs": [create_log("publishResult", f"Error: {msg}", "error")],
            }

    # ─── skipNode / errorNode ───────────────────────────────────────────────

    async def skip_node(self, state: AnalysisGraphState) -> dict[str, Any]:
        reason = state.get("skipReason") or "unknown"
        logger = self.logger
        if logger is not None:
            logger.info("Workflow skipped", reason=reason, symbols=self._get_symbols(state))
        return {"logs": [create_log("skipNode", f"Skipped: {reason}")]}

    async def error_node(self, state: AnalysisGraphState) -> dict[str, Any]:
        errors = list(state.get("errors") or [])
        logger = self.logger
        if logger is not None:
            logger.error("Workflow ended in error state", errors=errors)
        message = "Error state: " + ("; ".join(errors) if errors else "unknown error")
        return {"logs": [create_log("errorNode", message, "error")]}
