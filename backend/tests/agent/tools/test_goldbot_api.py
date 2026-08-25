"""镜像 apps/app-agent/src/tools/goldbot-api.test.ts。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from backend.agents.tools.goldbot_api import GoldbotAPI, GoldbotApiService
from backend.agents.types.schemas import GoldbotPayloadSchema, HarmonicAnalysisResultSchema, PendingSignalSchema


def create_pending_signal(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    body = {
        "id": 1,
        "account_id": "90011087",
        "symbol": "XAUUSD",
        "side": "buy",
        "score": 8,
        "strategy": "pullback",
        "indicators": '{"rsi":55}',
        "status": "pending",
        "created_at": "2026-06-06T00:00:00Z",
        "expires_at": "2026-06-06T00:05:00Z",
        "arbitration_result": "",
        "arbitration_reason": "",
    }
    if overrides:
        body.update(overrides)
    return body


def mock_json_transport(body: Any, captured: list[httpx.Request] | None = None) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if captured is not None:
            captured.append(request)
        return httpx.Response(200, json=body)

    return httpx.MockTransport(handler)


def test_constructs_with_base_url_and_token() -> None:
    api = GoldbotAPI("http://localhost:8880", "test-token")
    assert api is not None


def test_strips_trailing_slash_from_base_url() -> None:
    api = GoldbotAPI("http://localhost:8880/", "test-token")
    assert api.base_url == "http://localhost:8880"


def test_has_fetch_analysis_payload_method() -> None:
    api = GoldbotAPI("http://localhost:8880", "test-token")
    assert callable(api.fetch_analysis_payload)


def test_has_fetch_pending_signal_method() -> None:
    api = GoldbotAPI("http://localhost:8880", "test-token")
    assert callable(api.fetch_pending_signal)


def test_has_fetch_account_symbols_method() -> None:
    api = GoldbotAPI("http://localhost:8880", "test-token")
    assert callable(api.fetch_account_symbols)


def test_has_fetch_accounts_method() -> None:
    api = GoldbotAPI("http://localhost:8880", "test-token")
    assert callable(api.fetch_accounts)


def test_has_post_ai_result_method() -> None:
    api = GoldbotAPI("http://localhost:8880", "test-token")
    assert callable(api.post_ai_result)


def test_constructs_service_from_app_config() -> None:
    service = GoldbotApiService(SimpleNamespace(goldbot={"apiUrl": "http://localhost:8880/", "apiToken": "test-token"}))
    assert callable(service.fetch_analysis_payload)
    assert callable(service.fetch_pending_signal)
    assert callable(service.fetch_account_symbols)
    assert callable(service.fetch_accounts)
    assert callable(service.post_ai_result)
    assert service.base_url == "http://localhost:8880"


@pytest.mark.asyncio
async def test_fetches_account_symbols_from_goldbot() -> None:
    captured: list[httpx.Request] = []
    api = GoldbotAPI(
        "http://localhost:8880",
        "test-token",
        transport=mock_json_transport(["XAUUSD", "XAGUSD", "GBPJPY", "US100Cash"], captured),
    )
    assert await api.fetch_account_symbols("90011087") == {"symbols": ["XAUUSD", "XAGUSD", "GBPJPY", "US100Cash"]}
    assert str(captured[0].url) == "http://localhost:8880/api/ai_symbols/90011087"
    assert captured[0].headers["x-api-token"] == "test-token"


@pytest.mark.asyncio
async def test_fetches_registered_accounts_from_goldbot() -> None:
    captured: list[httpx.Request] = []
    api = GoldbotAPI(
        "http://localhost:8880",
        "test-token",
        transport=mock_json_transport(
            {
                "status": "OK",
                "accounts": [
                    {"account_id": "90011087", "equity": 1100.25},
                    {"account_id": "90022000", "equity": 500},
                ],
            },
            captured,
        ),
    )
    assert await api.fetch_accounts() == [{"account_id": "90011087"}, {"account_id": "90022000"}]
    assert str(captured[0].url) == "http://localhost:8880/api/v1/accounts"
    assert captured[0].headers["x-api-token"] == "test-token"


def test_parses_the_real_go_pending_signal_shape() -> None:
    signal = PendingSignalSchema.model_validate(
        {
            "id": 1,
            "account_id": "90011087",
            "symbol": "XAUUSD",
            "side": "buy",
            "score": 8,
            "strategy": "pullback",
            "indicators": '{"rsi":55}',
            "status": "pending",
            "created_at": "2026-06-06T00:00:00Z",
            "expires_at": "2026-06-06T00:05:00Z",
            "arbitration_result": "",
            "arbitration_reason": "",
        }
    )
    assert signal.id == 1
    assert signal.account_id == "90011087"
    assert signal.side == "buy"
    assert signal.score == 8
    assert signal.strategy == "pullback"
    assert signal.status == "pending"


@pytest.mark.asyncio
async def test_fetches_a_single_go_pending_signal_object() -> None:
    body = create_pending_signal({"id": 7, "side": "SELL"})
    captured: list[httpx.Request] = []
    api = GoldbotAPI("http://localhost:8880", "test-token", transport=mock_json_transport(body, captured))
    signal = await api.fetch_pending_signal("90011087", "XAUUSD")
    assert signal == {**body, "side": "sell"}
    assert str(captured[0].url) == "http://localhost:8880/api/pending_signal/90011087/XAUUSD"
    assert captured[0].headers["x-api-token"] == "test-token"


@pytest.mark.asyncio
async def test_fetches_the_first_pending_signal_from_legacy_array() -> None:
    first = create_pending_signal({"id": 8, "side": "BUY"})
    second = create_pending_signal({"id": 9, "side": "CLOSE"})
    api = GoldbotAPI("http://localhost:8880", "test-token", transport=mock_json_transport([first, second]))
    assert await api.fetch_pending_signal("90011087", "XAUUSD") == {**first, "side": "buy"}


@pytest.mark.asyncio
async def test_returns_null_for_empty_legacy_pending_signal_array() -> None:
    api = GoldbotAPI("http://localhost:8880", "test-token", transport=mock_json_transport([]))
    assert await api.fetch_pending_signal("90011087", "XAUUSD") is None


def test_parses_analysis_payload_bars_with_optional_indicator_fields() -> None:
    payload = GoldbotPayloadSchema.model_validate(
        {
            "account": {
                "account_id": "90011087",
                "equity": 10000,
                "balance": 10000,
                "margin": 0,
                "free_margin": 10000,
                "currency": "USD",
                "leverage": 100,
            },
            "market": {"symbol": "XAUUSD", "bid": 2350.3, "ask": 2350.7, "spread": 0.4},
            "indicators": {
                "H1": {
                    "close": 2350,
                    "open": 2348,
                    "high": 2352,
                    "low": 2347,
                    "ema20": 2348,
                    "ema50": 2340,
                    "rsi": 55,
                    "adx": 25,
                    "atr": 15,
                    "macd": 1.2,
                    "macd_signal": 0.8,
                    "macd_hist": 0.4,
                    "bb_upper": 2360,
                    "bb_middle": 2350,
                    "bb_lower": 2340,
                    "stoch_k": 65,
                    "stoch_d": 60,
                }
            },
            "positions": [],
            "market_status": {"market_open": True, "is_trade_allowed": True, "tradeable": True},
            "strategy_mapping": {},
            "bars": {
                "H1": [
                    {
                        "time": "2026-06-06T00:00:00Z",
                        "open": 2348,
                        "high": 2352,
                        "low": 2347,
                        "close": 2351,
                        "volume": 1000,
                        "ema20": 2348,
                        "ema50": 2340,
                        "ema200": 2310,
                        "atr": 15,
                        "rsi": 55,
                        "macd": 1.2,
                        "macd_signal": 0.8,
                        "macd_hist": 0.4,
                        "adx": 25,
                        "bb_upper": 2360,
                        "bb_lower": 2340,
                        "bb_mid": 2350,
                        "stoch_k": 65,
                        "stoch_d": 60,
                        "vol_sma": 1200,
                        "fib_236": 2335,
                        "fib_382": 2330,
                        "fib_500": 2325,
                        "fib_618": 2320,
                        "fib_786": 2315,
                        "pp": 2349,
                        "r1": 2355,
                        "r2": 2362,
                        "s1": 2342,
                        "s2": 2335,
                    }
                ]
            },
        }
    )
    assert payload.bars is not None
    assert len(payload.bars["H1"]) == 1
    assert payload.bars["H1"][0].bb_mid == 2350
    assert payload.bars["H1"][0].r2 == 2362


def test_parses_analysis_payload_null_indicators_for_sparse_timeframes() -> None:
    payload = GoldbotPayloadSchema.model_validate(
        {
            "account": {
                "account_id": "90011087",
                "equity": 10000,
                "balance": 10000,
                "margin": 0,
                "free_margin": 10000,
                "currency": "USD",
                "leverage": 100,
            },
            "market": {"symbol": "XAUUSD", "bid": 2350.3, "ask": 2350.7, "spread": 0.4},
            "indicators": {
                "M15": None,
                "H1": {
                    "close": 2350,
                    "open": 2348,
                    "high": 2352,
                    "low": 2347,
                    "ema20": 2348,
                    "ema50": 2340,
                    "rsi": 55,
                    "adx": 25,
                    "atr": 15,
                    "macd": 1.2,
                    "macd_signal": 0.8,
                    "macd_hist": 0.4,
                    "bb_upper": 2360,
                    "bb_middle": 2350,
                    "bb_lower": 2340,
                    "stoch_k": 65,
                    "stoch_d": 60,
                },
            },
            "positions": [],
            "market_status": {"market_open": True, "is_trade_allowed": True, "tradeable": True},
            "strategy_mapping": {},
        }
    )
    assert payload.indicators["M15"] is None
    assert payload.indicators["H1"] is not None
    assert payload.indicators["H1"].atr == 15


def test_parses_harmonic_context_patterns_without_completion_and_active_fields() -> None:
    payload = GoldbotPayloadSchema.model_validate(
        {
            "account": {
                "account_id": "90011087",
                "equity": 10000,
                "balance": 10000,
                "margin": 0,
                "free_margin": 10000,
                "currency": "USD",
                "leverage": 100,
            },
            "market": {"symbol": "XAUUSD", "bid": 2350.3, "ask": 2350.7, "spread": 0.4},
            "indicators": {},
            "positions": [],
            "market_status": {"market_open": True, "is_trade_allowed": True, "tradeable": True},
            "strategy_mapping": {},
            "harmonic_context": {
                "h4_patterns": [
                    {
                        "type": "gartley",
                        "direction": "bullish",
                        "timeframe": "H4",
                        "score": 78,
                        "x_price": 2300,
                        "a_price": 2360,
                        "b_price": 2322.9,
                        "c_price": 2345,
                        "d_price": 2310,
                        "ab_ratio": 0.618,
                        "bc_ratio": 0.382,
                        "cd_ratio": 1.272,
                        "xd_ratio": 0.786,
                        "reason": "valid ratios",
                    }
                ],
                "h1_patterns": [],
                "m30_patterns": [],
                "active_pattern": None,
                "direction_bias": "bullish",
                "score": 78,
                "summary": "H4 bullish Gartley candidate",
            },
        }
    )
    assert payload.harmonic_context is not None
    assert payload.harmonic_context.h4_patterns[0].completion_pct is None
    assert payload.harmonic_context.h4_patterns[0].is_active is None


def test_parses_harmonic_analysis_results_without_completion_and_active_fields() -> None:
    result = HarmonicAnalysisResultSchema.model_validate(
        {
            "detected_pattern": "gartley",
            "direction": "bullish",
            "timeframe": "H4",
            "confidence": 78,
            "d_zone_price": 2310,
            "entry_zone": "2308-2312",
            "stop_loss": 2298,
            "take_profit_1": 2330,
            "take_profit_2": 2350,
            "rationale": "valid ratios",
        }
    )
    assert result.completion_pct is None
    assert result.is_active is None
