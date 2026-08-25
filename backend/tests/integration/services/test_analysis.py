"""AnalysisService 集成测试(镜像 apps/app-server/src/services/analysis/service.spec.ts)。

逐条映射:
- passes the latest stored AI result into replay analysis
- uses the latest H1 close for replay analysis when no current tick exists
- passes D1 bars into replay trend scoring
- filters unrelated position symbols before replay conflict checks
"""

from __future__ import annotations

from backend.persistence.store import create_in_memory_store
from backend.services.analysis import AnalysisService

ACCOUNT = "90011087"
SYMBOL = "XAUUSD"
NOW = "2026-04-16T12:00:00.000Z"


def _assert_matches(subset: dict, record: dict) -> None:
    for key, value in subset.items():
        assert record[key] == value


def _pullback_buy_bars() -> list[dict]:
    bars = []
    for index in range(50):
        bars.append(
            {
                "time": f"2026-04-16T{index:02d}:00:00.000Z",
                "open": 95,
                "high": 96,
                "low": 94,
                "close": 95,
                "atr": 2,
                "adx": 35,
                "rsi": 45,
                "ema20": 95.8,
                "ema50": 90,
                "macd_hist": 1,
                "r1": 97.5,
            }
        )
    bars[48]["close"] = 95.2
    bars[48]["open"] = 95.2
    bars[49]["close"] = 95
    bars[49]["open"] = 95
    return bars


def _pullback_fib_h4_bars_up() -> list[dict]:
    return [
        {
            "time": f"2026-04-15T{index:02d}:00:00.000Z",
            "open": 90 + index,
            "high": 100 + index,
            "low": 88 + index,
            "close": 95 + index,
            "adx": 30,
            "ema20": 110,
            "ema50": 100,
        }
        for index in range(5)
    ]


def _d1_trend_bars() -> list[dict]:
    return [
        {
            "time": f"2026-04-{index + 1:02d}T00:00:00.000Z",
            "open": 100 + index,
            "high": 101 + index,
            "low": 99 + index,
            "close": 100 + index,
            "adx": 35,
            "ema20": 120,
            "ema50": 100,
        }
        for index in range(40)
    ]


async def _seed_pullback_bars(store) -> None:
    await store.save_bars({"account_id": ACCOUNT, "symbol": SYMBOL, "timeframe": "H1", "bars": _pullback_buy_bars()})
    await store.save_bars(
        {"account_id": ACCOUNT, "symbol": SYMBOL, "timeframe": "H4", "bars": _pullback_fib_h4_bars_up()}
    )


async def test_passes_the_latest_stored_ai_result_into_replay_analysis() -> None:
    store = create_in_memory_store()
    await store.save_tick({"account_id": ACCOUNT, "symbol": SYMBOL, "bid": 95, "ask": 95})
    await _seed_pullback_bars(store)
    await store.save_ai_result(ACCOUNT, SYMBOL, {"suggested_sl": 93})

    result = await AnalysisService(store, lambda: NOW).analyze_account_symbol(ACCOUNT, SYMBOL)

    signal = result["replay"]["signal"]
    assert isinstance(signal, dict)
    _assert_matches({"strategy": "pullback", "side": "BUY", "entry": 95, "stop_loss": 93}, signal)


async def test_uses_the_latest_h1_close_for_replay_analysis_when_no_current_tick_exists() -> None:
    store = create_in_memory_store()
    await _seed_pullback_bars(store)

    result = await AnalysisService(store, lambda: NOW).analyze_account_symbol(ACCOUNT, SYMBOL)

    signal = result["replay"]["signal"]
    assert isinstance(signal, dict)
    _assert_matches({"strategy": "pullback", "side": "BUY", "entry": 95}, signal)


async def test_passes_d1_bars_into_replay_trend_scoring() -> None:
    store = create_in_memory_store()
    await store.save_tick({"account_id": ACCOUNT, "symbol": SYMBOL, "bid": 95, "ask": 95})
    await _seed_pullback_bars(store)
    await store.save_bars({"account_id": ACCOUNT, "symbol": SYMBOL, "timeframe": "D1", "bars": _d1_trend_bars()})

    result = await AnalysisService(store, lambda: NOW).analyze_account_symbol(ACCOUNT, SYMBOL)

    signal = result["replay"]["signal"]
    assert isinstance(signal, dict)
    _assert_matches({"strategy": "pullback", "side": "BUY", "score": 9}, signal)


async def test_filters_unrelated_position_symbols_before_replay_conflict_checks() -> None:
    store = create_in_memory_store()
    await store.save_tick({"account_id": ACCOUNT, "symbol": SYMBOL, "bid": 95, "ask": 95})
    await _seed_pullback_bars(store)
    await store.save_positions(
        {
            "account_id": ACCOUNT,
            "symbol": SYMBOL,
            "positions": [
                {"ticket": 2002, "symbol": "GBPJPY", "type": "BUY", "lots": 0.2, "open_price": 95.1, "profit": 1.5}
            ],
        }
    )

    result = await AnalysisService(store, lambda: NOW).analyze_account_symbol(ACCOUNT, SYMBOL)

    signal = result["replay"]["signal"]
    assert isinstance(signal, dict)
    _assert_matches({"strategy": "pullback", "side": "BUY", "entry": 95}, signal)
