"""Strategy 引擎对拍用例(镜像 packages/trading-core/src/engine/engine.spec.ts)。

逐个 it() 镜像为 pytest:analyze 的 no_signal / signal 两条路径、EA 策略名校验、
AI SL/TP 透传、Go 符号精度、H4 硬过滤、SMC 上下文加分等。
ts `toMatchObject` → 断言键子集;`toContainEqual` → `in` 列表断言;
`it.skip` 的两个动量剥头皮用例 → pytest.mark.skip。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from backend.shared_contracts import EA_STRATEGY_NAMES
from backend.trading_core.engine.engine import analyze, create_strategy_engine

FIXTURE_ACCOUNT_90011087 = (
    Path(__file__).resolve().parents[2] / "fixtures" / "replay" / "account_90011087" / "input.json"
)

# ------------------------------------------------------------------ helpers


def _to_match_object(actual: dict[str, Any], expected: dict[str, Any]) -> None:
    """镜像 jest toMatchObject:actual 需包含 expected 的全部键且值相等。"""
    for key, value in expected.items():
        assert key in actual, f"missing key {key!r} in {actual}"
        assert actual[key] == value, f"key {key!r}: got {actual[key]!r}, want {value!r}"


def _epoch_iso(index: int) -> str:
    """镜像 new Date((index + 1) * 1000).toISOString()。"""
    return f"1970-01-01T00:00:{index + 1:02d}.000Z"


# ------------------------------------------------------------------ fixture builders(逐字镜像 spec 下方 helper)


def _momentum_scalp_buy_bars(options: dict[str, Any] | None = None) -> dict[str, list[dict[str, Any]]]:
    opts = options or {}
    m15_adx = opts.get("m15Adx", 33)
    previous_macd_hist = opts.get("previousMacdHist", 0.73)
    macd_hist = opts.get("macdHist", 0.81)
    rsi = opts.get("rsi", 49)
    volume = opts.get("volume", 130)
    return {
        "M15": [
            {"open": 96, "high": 96, "low": 96, "close": 96, "ema20": 96, "ema50": 94, "adx": 28},
            {"open": 97, "high": 97, "low": 97, "close": 97, "ema20": 97, "ema50": 95, "adx": m15_adx},
        ],
        "M5": [
            {"open": 98, "high": 98, "low": 98, "close": 98, "macd_hist": 0.1},
            {"open": 98.4, "high": 98.4, "low": 98.4, "close": 98.4, "macd_hist": 0.15},
            {"open": 98.8, "high": 98.8, "low": 98.8, "close": 98.8, "macd_hist": 0.21},
            {"open": 99, "high": 99, "low": 99, "close": 99, "macd_hist": 0.27},
            {"open": 99.2, "high": 99.2, "low": 99.2, "close": 99.2, "macd_hist": 0.34},
            {"open": 99.4, "high": 99.4, "low": 99.4, "close": 99.4, "macd_hist": 0.4},
            {"open": 99.5, "high": 99.5, "low": 99.5, "close": 99.5, "macd_hist": 0.47},
            {"open": 99.6, "high": 99.6, "low": 99.6, "close": 99.6, "macd_hist": 0.54},
            {"open": 99.7, "high": 99.7, "low": 99.7, "close": 99.7, "macd_hist": 0.6},
            {"open": 99.8, "high": 99.8, "low": 99.8, "close": 99.8, "macd_hist": 0.66},
            {"open": 99.9, "high": 99.9, "low": 99.9, "close": 99.9, "macd_hist": previous_macd_hist},
            {"open": 100, "high": 100, "low": 100, "close": 100, "macd_hist": macd_hist},
        ],
        "M1": [
            {
                "open": 99 + index * 0.02,
                "high": 99 + index * 0.02,
                "low": 99 + index * 0.02,
                "close": 99 + index * 0.02,
                "atr": 1.5,
                "rsi": 38 if index == 12 else (rsi if index == 13 else 44),
                "volume": volume if index == 13 else 90,
                "vol_sma": 80,
            }
            for index in range(14)
        ],
    }


def _breakout_retest_buy_bars() -> dict[str, list[dict[str, Any]]]:
    bars: list[dict[str, Any]] = [
        {
            "time": f"2026-04-13T{index:02d}:00:00.000Z",
            "open": 100,
            "high": 101,
            "low": 99,
            "close": 100,
            "atr": 2,
            "adx": 18,
            "rsi": 50,
            "ema20": 120,
            "ema50": 119,
            "macd_hist": 0,
        }
        for index in range(55)
    ]
    for index in range(50):
        bars[index]["high"] = 102
    last_five = [
        (103.2, 101.7, 103.0),
        (102.8, 101.8, 102.5),
        (102.6, 101.9, 102.3),
        (102.5, 101.95, 102.25),
        (102.4, 102.0, 102.2),
    ]
    for offset, (high, low, close) in enumerate(last_five):
        bar = bars[50 + offset]
        bar.update(
            {
                "high": high,
                "low": low,
                "close": close,
                "open": close,
                "atr": 2,
                "adx": 26 if offset == 4 else 18,
                "rsi": 58 if offset == 4 else 50,
                "ema20": 120,
                "ema50": 119,
                "macd_hist": 0.3 if offset == 4 else 0,
                "volume": 160 if offset == 4 else 100,
                "vol_sma": 100,
            }
        )
        if offset == 4:
            bar["bb_upper"] = 106
        else:
            bar.pop("bb_upper", None)
    return {"H1": bars}


def _breakout_retest_sell_bars() -> dict[str, list[dict[str, Any]]]:
    bars: list[dict[str, Any]] = [
        {
            "time": f"2026-04-13T{index:02d}:00:00.000Z",
            "open": 100,
            "high": 101,
            "low": 99,
            "close": 100,
            "atr": 2,
            "adx": 18,
            "rsi": 50,
            "ema20": 119,
            "ema50": 120,
            "macd_hist": 0,
        }
        for index in range(55)
    ]
    for index in range(50):
        bars[index]["low"] = 98
    last_five = [
        (98.3, 96.8, 97.0),
        (98.2, 97.2, 97.5),
        (98.1, 97.4, 97.7),
        (98.05, 97.55, 97.75),
        (98.0, 97.6, 97.8),
    ]
    for offset, (high, low, close) in enumerate(last_five):
        bar = bars[50 + offset]
        bar.update(
            {
                "high": high,
                "low": low,
                "close": close,
                "open": close,
                "atr": 2,
                "adx": 26 if offset == 4 else 18,
                "rsi": 42 if offset == 4 else 50,
                "ema20": 119,
                "ema50": 120,
                "macd_hist": -0.3 if offset == 4 else 0,
                "volume": 160 if offset == 4 else 100,
                "vol_sma": 100,
            }
        )
        if offset == 4:
            bar["bb_lower"] = 94
        else:
            bar.pop("bb_lower", None)
    return {"H1": bars}


def _pullback_fib_buy_bars() -> list[dict[str, Any]]:
    bars: list[dict[str, Any]] = [
        {
            "time": f"2026-04-15T{index:02d}:00:00.000Z",
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
        }
        for index in range(50)
    ]
    bars[48].update({"close": 95.2, "open": 95.2})
    bars[49].update({"close": 95, "open": 95, "fib_382": 96, "fib_618": 92, "fib_786": 89})
    return bars


def _h4_range_bars() -> list[dict[str, Any]]:
    return [
        {
            "time": f"2026-04-16T{index:02d}:00:00.000Z",
            "open": 95,
            "high": 96,
            "low": 94,
            "close": 95,
            "adx": 10,
            "ema20": 100,
            "ema50": 99,
        }
        for index in range(50)
    ]


def _h4_strong_bear_bars() -> list[dict[str, Any]]:
    return [
        {
            "time": f"2026-04-16T{index:02d}:00:00.000Z",
            "open": 95,
            "high": 96,
            "low": 94,
            "close": 95,
            "adx": 35,
            "ema20": 99,
            "ema50": 100,
        }
        for index in range(50)
    ]


def _pullback_fib_h4_bars_up() -> list[dict[str, Any]]:
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


def _pullback_buy_bars() -> list[dict[str, Any]]:
    bars: list[dict[str, Any]] = [
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
        for index in range(50)
    ]
    bars[48].update({"close": 95.2, "open": 95.2})
    bars[49].update({"close": 95, "open": 95})
    return bars


def _eurusd_pullback_buy_bars() -> list[dict[str, Any]]:
    bars: list[dict[str, Any]] = [
        {
            "time": f"2026-04-16T{index:02d}:00:00.000Z",
            "open": 1.09567,
            "high": 1.09592,
            "low": 1.09542,
            "close": 1.09567,
            "atr": 0.00036,
            "adx": 35,
            "rsi": 45,
            "ema20": 1.0957,
            "ema50": 1.09,
            "macd_hist": 0.0001,
            "bb_upper": 1.09608,
        }
        for index in range(50)
    ]
    bars[48].update({"close": 1.09569, "open": 1.09569})
    return bars


def _pullback_m15_confirm_bars() -> list[dict[str, Any]]:
    return [
        {
            "time": f"2026-04-16T{index:02d}:00:00.000Z",
            "open": 95,
            "high": 95.5,
            "low": 94.5,
            "close": 95,
            "atr": 2,
            "rsi": 35 if index == 13 else 45,
        }
        for index in range(14)
    ]


def _divergence_buy_bars() -> list[dict[str, Any]]:
    bars: list[dict[str, Any]] = [
        {
            "time": _epoch_iso(index),
            "open": 101,
            "high": 102,
            "low": 100,
            "close": 101 if index < 15 else 100,
            "atr": 2,
            "adx": 10,
            "rsi": 55 if index < 15 else 50,
            "ema20": 120,
            "ema50": 119,
            "bb_upper": 130,
            "bb_lower": 80,
            "macd_hist": 0.1 if index < 15 else 0.2,
            "stoch_k": 0,
        }
        for index in range(30)
    ]
    bars[5].update({"close": 95, "rsi": 30, "macd_hist": -0.5})
    bars[25].update({"close": 93, "rsi": 35, "macd_hist": -0.2})
    bars[28]["macd_hist"] = 0.1
    bars[29].update(
        {"open": 94, "high": 95, "low": 93.5, "close": 94, "rsi": 38, "macd_hist": 0.2, "volume": 60, "vol_sma": 100}
    )
    return bars


def _divergence_sell_bars() -> list[dict[str, Any]]:
    bars: list[dict[str, Any]] = [
        {
            "time": _epoch_iso(index),
            "open": 101,
            "high": 102,
            "low": 100,
            "close": 101 if index < 15 else 100,
            "atr": 2,
            "adx": 10,
            "rsi": 45 if index < 15 else 40,
            "ema20": 120,
            "ema50": 119,
            "bb_upper": 130,
            "bb_lower": 80,
            "macd_hist": 0.2 if index < 15 else 0.1,
            "stoch_k": 50,
        }
        for index in range(30)
    ]
    bars[5].update({"close": 105, "rsi": 70, "macd_hist": 0.5})
    bars[25].update({"close": 107, "rsi": 65, "macd_hist": 0.2})
    bars[28]["macd_hist"] = 0.2
    bars[29].update(
        {
            "open": 106,
            "high": 107,
            "low": 105,
            "close": 106,
            "rsi": 62,
            "macd_hist": 0.1,
            "volume": 60,
            "vol_sma": 100,
            "stoch_k": 90,
        }
    )
    return bars


def _breakout_pyramid_buy_signal_bars() -> list[dict[str, Any]]:
    bars: list[dict[str, Any]] = [
        {
            "time": _epoch_iso(index),
            "open": 100,
            "high": 100.5,
            "low": 99.5,
            "close": 100,
            "atr": 2,
            "adx": 35,
            "rsi": 60,
            "ema20": 101,
            "ema50": 99,
            "bb_upper": 101,
            "bb_lower": 99,
            "macd_hist": 0.2,
        }
        for index in range(30)
    ]
    bars[29].update({"open": 102, "high": 102.4, "low": 101.2, "close": 102})
    return bars


def _breakout_pyramid_sell_signal_bars() -> list[dict[str, Any]]:
    bars = _breakout_pyramid_buy_signal_bars()
    bars[29].update(
        {"open": 98, "high": 98.8, "low": 97.6, "close": 98, "rsi": 40, "ema20": 99, "ema50": 101, "macd_hist": -0.2}
    )
    return bars


def _counter_pullback_buy_bars() -> list[dict[str, Any]]:
    return [
        {
            "time": _epoch_iso(index),
            "open": 100,
            "high": 101,
            "low": 99,
            "close": 100,
            "atr": 2,
            "adx": 10,
            "rsi": 44 if index == 19 else 50,
            "ema20": 120,
            "ema50": 119,
            "bb_upper": 130,
            "bb_lower": 80,
            "macd_hist": 0.1 if index == 19 else 0,
        }
        for index in range(20)
    ]


def _counter_pullback_sell_bars() -> list[dict[str, Any]]:
    return [
        {
            "time": _epoch_iso(index),
            "open": 100,
            "high": 101,
            "low": 99,
            "close": 100,
            "atr": 2,
            "adx": 10,
            "rsi": 56 if index == 19 else 50,
            "ema20": 119,
            "ema50": 120,
            "bb_upper": 130,
            "bb_lower": 80,
            "macd_hist": -0.1 if index == 19 else 0,
        }
        for index in range(20)
    ]


# ------------------------------------------------------------------ it() 用例


def test_returns_a_no_signal_decision_without_producing_live_commands() -> None:
    result = analyze({"accountId": "90011087", "symbol": "XAUUSD", "price": 3335.75, "bars": {}})

    assert result == {
        "decision": "no_signal",
        "signal": None,
        "logs": [],
        "canProduceLiveCommands": False,
    }


def test_validates_strategy_outputs_against_the_ea_approved_strategy_names() -> None:
    engine = create_strategy_engine()

    for strategy in EA_STRATEGY_NAMES:
        engine["validateStrategyName"](strategy)  # type: ignore[index]

    with pytest.raises(ValueError, match="not an EA strategy name"):
        engine["validateStrategyName"]("smc")  # type: ignore[index]


def test_returns_the_replay_backed_pullback_signal_for_the_frozen_go_oracle_fixture() -> None:
    with open(FIXTURE_ACCOUNT_90011087, encoding="utf-8") as fh:
        snapshot = json.load(fh)
    last_h1_bar = snapshot["bars"]["H1"][-1]
    # Gold Fib gates pullback entries; pin the oracle fixture's Fib pocket to the Go-expected entry.
    last_h1_bar.update({"fib_382": 3350, "fib_618": 3320, "fib_786": 3334.93})

    result = analyze(
        {
            "accountId": snapshot["account_id"],
            "symbol": "XAUUSD",
            "price": snapshot["current_price"],
            "bars": snapshot["bars"],
        }
    )

    assert result["decision"] == "no_signal"
    assert result["signal"] is None
    assert {"level": "warn", "strategy": "R:R过滤", "message": "⚠️ 信号 R:R=0.875 < 1.25 拒绝 ⏭"} in result["logs"]
    assert result["canProduceLiveCommands"] is False


@pytest.mark.skip(reason="镜像 engine.spec.ts 的 it.skip:动量剥头皮当前禁用")
def test_maps_the_replay_backed_momentum_scalp_signal_without_producing_live_commands() -> None:
    result = analyze(
        {
            "accountId": "90011087",
            "symbol": "",
            "price": 100,
            "bars": _momentum_scalp_buy_bars(),
        }
    )

    assert result["decision"] == "signal"
    assert result["signal"] == {
        "strategy": "momentum_scalp",
        "side": "BUY",
        "entry": 100,
        "stopLoss": 99.4,
        "tp1": 100.75,
        "tp2": 101.2,
        "score": 10,
    }
    assert {
        "level": "signal",
        "strategy": "动量剥头皮",
        "message": "🟢 BUY 评分=10 | M15 ADX=33.0 | M5 MACDHist=0.81 | M1 RSI=49.0 | 成交量=1.62x | M15 ADX=33.0",
    } in result["logs"]
    assert result["canProduceLiveCommands"] is False


@pytest.mark.skip(reason="镜像 engine.spec.ts 的 it.skip:动量剥头皮当前禁用")
def test_maps_xauusd_momentum_scalp_signals_using_go_gold_thresholds() -> None:
    result = analyze(
        {
            "accountId": "90011087",
            "symbol": "XAUUSD",
            "price": 100,
            "bars": _momentum_scalp_buy_bars(
                {"m15Adx": 18.5, "previousMacdHist": -0.2, "macdHist": -0.1, "rsi": 52, "volume": 90}
            ),
        }
    )

    assert result["decision"] == "signal"
    _to_match_object(
        result["signal"],
        {
            "strategy": "momentum_scalp",
            "side": "BUY",
            "entry": 100,
            "stopLoss": 99.4,
            "tp1": 100.75,
            "tp2": 101.2,
            "score": 6,
        },
    )
    assert result["canProduceLiveCommands"] is False


def test_boosts_a_pullback_buy_signal_when_m15_confirms_the_entry() -> None:
    result = analyze(
        {
            "accountId": "90011087",
            "symbol": "",
            "price": 95,
            "bars": {"H1": _pullback_buy_bars(), "M15": _pullback_m15_confirm_bars()},
        }
    )

    assert result["decision"] == "signal"
    assert result["signal"] == {
        "strategy": "pullback",
        "side": "BUY",
        "entry": 95,
        "stopLoss": 93.13,
        "tp1": 95.8,
        "tp2": 97.5,
        "score": 10,
    }
    assert {
        "level": "signal",
        "strategy": "趋势回调",
        "message": "🟢 BUY 评分=9 | EMA20回调 dist=0.80 | MACD柱>0 | RSI=45.0<50 | ADX=35.0>30 | 连续2根回调到位",
    } in result["logs"]
    assert {
        "level": "info",
        "strategy": "M15确认",
        "message": "✅ pullback | M15确认: RSI=35.0<40(多头) | 近Fib382=95.12 | 评分+1→10",
    } in result["logs"]
    assert {
        "level": "signal",
        "strategy": "汇总",
        "message": "✅ 发出信号: BUY @ 95.00 | SL=93.13 | 策略=pullback | 评分=10",
    } in result["logs"]
    assert result["canProduceLiveCommands"] is False


def test_passes_ai_stop_loss_suggestions_through_to_the_replay_engine() -> None:
    result = analyze(
        {
            "accountId": "90011087",
            "symbol": "XAUUSD",
            "price": 95,
            "bars": {"H1": _pullback_buy_bars(), "H4": _pullback_fib_h4_bars_up()},
            "aiResult": {"suggested_sl": 93},
        }
    )

    assert result["decision"] == "signal"
    _to_match_object(
        result["signal"],
        {
            "strategy": "pullback",
            "side": "BUY",
            "entry": 95,
            "stopLoss": 93,
            "tp1": 95.8,
            "tp2": 97.5,
            # 10 → 9:1ffbea7 起趋势评级门槛从 D1 改为 H4,多周期共识偏弱触发 -1 扣分
            "score": 9,
        },
    )
    assert {
        "level": "info",
        "strategy": "AI止损",
        "message": "🤖 AI止损覆盖: 93.13 → 93.00 (基于支撑阻力位)",
    } in result["logs"]


def test_rounds_forex_signal_prices_with_go_symbol_precision() -> None:
    result = analyze(
        {
            "accountId": "90011087",
            "symbol": "EURUSD",
            "price": 1.09567,
            "bars": {"H1": _eurusd_pullback_buy_bars()},
            "aiResult": {"suggested_tp": 1.09619},
        }
    )

    assert result["decision"] == "signal"
    _to_match_object(
        result["signal"],
        {
            "strategy": "pullback",
            "side": "BUY",
            "entry": 1.09567,
            "stopLoss": 1.09535,
            "tp1": 1.09619,
            "tp2": 1.09619,
            "score": 9,
        },
    )


def test_filters_a_pullback_buy_signal_when_h4_is_range_bound() -> None:
    result = analyze(
        {
            "accountId": "90011087",
            "symbol": "",
            "price": 95,
            "bars": {"H1": _pullback_buy_bars(), "H4": _h4_range_bars()},
        }
    )

    assert result["decision"] == "no_signal"
    assert result["signal"] is None
    assert {
        "level": "warn",
        "strategy": "H4过滤",
        "message": "H4=震荡(ADX=10.0<30), 过滤掉 1 个信号（震荡市禁入）",
    } in result["logs"]
    assert {"level": "info", "strategy": "H4过滤", "message": "H4趋势过滤后无信号"} in result["logs"]
    assert result["canProduceLiveCommands"] is False


def test_filters_a_pullback_buy_signal_when_h4_strong_trend_is_opposite() -> None:
    result = analyze(
        {
            "accountId": "90011087",
            "symbol": "",
            "price": 95,
            "bars": {"H1": _pullback_buy_bars(), "H4": _h4_strong_bear_bars()},
        }
    )

    assert result["decision"] == "no_signal"
    assert result["signal"] is None
    assert {"level": "warn", "strategy": "H4过滤", "message": "H4=强空头,过滤掉 1 个逆势信号,保留 0 个"} in result[
        "logs"
    ]
    assert {"level": "info", "strategy": "H4过滤", "message": "H4趋势过滤后无信号"} in result["logs"]
    assert result["canProduceLiveCommands"] is False


def test_maps_the_replay_backed_breakout_retest_signal_without_producing_live_commands() -> None:
    result = analyze({"accountId": "90011087", "symbol": "", "price": 102.2, "bars": _breakout_retest_buy_bars()})

    assert result["decision"] == "signal"
    assert result["signal"] == {
        "strategy": "breakout_retest",
        "side": "BUY",
        "entry": 102.2,
        "stopLoss": 99.6,
        "tp1": 106,
        "tp2": 106,
        "score": 10,
    }
    assert {
        "level": "signal",
        "strategy": "突破回踩",
        "message": (
            "🟢 BUY 评分=10 | 阻力位=102.00 突破后回踩 dist=0.20 | 成交量确认 | "
            "MACD柱>0 | ADX=26.0 | RSI=58.0 | 回踩确认3根"
        ),
    } in result["logs"]
    assert result["canProduceLiveCommands"] is False


def test_maps_the_replay_backed_breakout_retest_sell_signal_without_producing_live_commands() -> None:
    result = analyze({"accountId": "90011087", "symbol": "", "price": 97.8, "bars": _breakout_retest_sell_bars()})

    assert result["decision"] == "signal"
    assert result["signal"] == {
        "strategy": "breakout_retest",
        "side": "SELL",
        "entry": 97.8,
        "stopLoss": 100.4,
        "tp1": 94,
        "tp2": 94,
        "score": 9,
    }
    assert {
        "level": "signal",
        "strategy": "突破回踩",
        "message": "🔴 SELL 评分=9 | 支撑位=98.00 突破后回踩 dist=0.20 | 成交量确认 | MACD柱<0 | ADX=26.0 | RSI=42.0",
    } in result["logs"]
    assert result["canProduceLiveCommands"] is False


def test_maps_a_fib_enhanced_pullback_buy_slice_with_fib786_stop_loss() -> None:
    result = analyze(
        {
            "accountId": "90011087",
            "symbol": "XAUUSD",
            "price": 95,
            "bars": {"H1": _pullback_fib_buy_bars(), "H4": _pullback_fib_h4_bars_up()},
        }
    )

    assert result["decision"] == "no_signal"
    assert result["signal"] is None
    assert {
        "level": "info",
        "strategy": "pullback",
        "message": "🌀 pullback+FIB: fib786 止损距离超限 (3.50 ATR > 1.5) 回退 ⏭",
    } in result["logs"]
    assert {"level": "warn", "strategy": "R:R过滤", "message": "⚠️ 信号 R:R=0.535 < 1.25 拒绝 ⏭"} in result["logs"]
    assert result["canProduceLiveCommands"] is False


def test_maps_the_replay_backed_divergence_signal_without_producing_live_commands() -> None:
    result = analyze({"accountId": "90011087", "symbol": "", "price": 94, "bars": {"H1": _divergence_buy_bars()}})

    assert result["decision"] == "signal"
    assert result["signal"] == {
        "strategy": "divergence",
        "side": "BUY",
        "entry": 94,
        "stopLoss": 91,
        "tp1": 98,
        "tp2": 102,
        "score": 9,
    }
    assert {
        "level": "signal",
        "strategy": "RSI背离",
        "message": (
            "🟢 BUY 评分=9 | 看涨背离: 价格新低93.00<95.00 RSI抬高35.0>30.0 | "
            "MACD背离确认 | 成交量萎缩 | StochK=0"
        ),
    } in result["logs"]
    assert result["canProduceLiveCommands"] is False


def test_maps_the_replay_backed_divergence_sell_signal_without_producing_live_commands() -> None:
    result = analyze({"accountId": "90011087", "symbol": "", "price": 106, "bars": {"H1": _divergence_sell_bars()}})

    assert result["decision"] == "signal"
    assert result["signal"] == {
        "strategy": "divergence",
        "side": "SELL",
        "entry": 106,
        "stopLoss": 109,
        "tp1": 102,
        "tp2": 98,
        "score": 9,
    }
    assert {
        "level": "signal",
        "strategy": "RSI背离",
        "message": (
            "🔴 SELL 评分=9 | 看跌背离: 价格新高107.00>105.00 RSI降低65.0<70.0 | "
            "MACD背离确认 | 成交量萎缩 | StochK=90"
        ),
    } in result["logs"]
    assert result["canProduceLiveCommands"] is False


def test_maps_the_replay_backed_breakout_pyramid_signal_without_producing_live_commands() -> None:
    result = analyze(
        {"accountId": "90011087", "symbol": "", "price": 102, "bars": {"H1": _breakout_pyramid_buy_signal_bars()}}
    )

    assert result["decision"] == "signal"
    assert result["signal"] == {
        "strategy": "breakout_pyramid",
        "side": "BUY",
        "entry": 102,
        "stopLoss": 98,
        "tp1": 106,
        "tp2": 112,
        "score": 9,
    }
    assert {
        "level": "signal",
        "strategy": "突破加仓",
        "message": "🟢 BUY 评分=9 | 收盘价突破布林上轨=101.00 | ADX=35.0>30 | RSI=60.0 | MACD柱>0",
    } in result["logs"]
    assert result["canProduceLiveCommands"] is False


def test_maps_the_replay_backed_breakout_pyramid_sell_signal_without_producing_live_commands() -> None:
    result = analyze(
        {"accountId": "90011087", "symbol": "", "price": 98, "bars": {"H1": _breakout_pyramid_sell_signal_bars()}}
    )

    assert result["decision"] == "signal"
    assert result["signal"] == {
        "strategy": "breakout_pyramid",
        "side": "SELL",
        "entry": 98,
        "stopLoss": 102,
        "tp1": 94,
        "tp2": 88,
        "score": 9,
    }
    assert {
        "level": "signal",
        "strategy": "突破加仓",
        "message": "🔴 SELL 评分=9 | 收盘价突破布林下轨=99.00 | ADX=35.0>30 | RSI=40.0 | MACD柱<0",
    } in result["logs"]
    assert result["canProduceLiveCommands"] is False


def test_maps_the_replay_backed_counter_pullback_buy_signal_without_producing_live_commands() -> None:
    result = analyze(
        {
            "accountId": "90011087",
            "symbol": "",
            "price": 100.4,
            "bars": {"H1": _counter_pullback_buy_bars(), "M30": _counter_pullback_buy_bars()},
            "smc": {
                "m30_breaks": [{"index": 18, "direction": "UP", "level": 101, "type": "CHoCH"}],
                "m30_sweeps": [{"index": 17, "level": 100, "side": "BULL", "reversed": True}],
            },
        }
    )

    assert result["decision"] == "signal"
    assert result["signal"] == {
        "strategy": "counter_pullback",
        "side": "BUY",
        "entry": 100.4,
        "stopLoss": 99,
        "tp1": 104.4,
        "tp2": 108.4,
        "score": 6,
    }
    assert {
        "level": "signal",
        "strategy": "反转回调",
        "message": (
            "🟢 BUY 评分=7 | M30 | 看涨反转回调: CHoCH↑+Sweep@100.00 | CHoCH@18 | "
            "Sweep@100.00 | RSI=44.0 | MACD>0"
        ),
    } in result["logs"]
    assert result["canProduceLiveCommands"] is False


def test_maps_the_replay_backed_counter_pullback_sell_signal_without_producing_live_commands() -> None:
    result = analyze(
        {
            "accountId": "90011087",
            "symbol": "",
            "price": 99.6,
            "bars": {"H1": _counter_pullback_sell_bars(), "M30": _counter_pullback_sell_bars()},
            "smc": {
                "m30_breaks": [{"index": 18, "direction": "DOWN", "level": 99, "type": "CHoCH"}],
                "m30_sweeps": [{"index": 17, "level": 100, "side": "BEAR", "reversed": True}],
            },
        }
    )

    assert result["decision"] == "signal"
    assert result["signal"] == {
        "strategy": "counter_pullback",
        "side": "SELL",
        "entry": 99.6,
        "stopLoss": 101,
        "tp1": 95.6,
        "tp2": 91.6,
        "score": 6,
    }
    assert {
        "level": "signal",
        "strategy": "反转回调",
        "message": (
            "🔴 SELL 评分=7 | M30 | 看跌反转回调: CHoCH↓+Sweep@100.00 | CHoCH@18 | "
            "Sweep@100.00 | RSI=56.0 | MACD<0"
        ),
    } in result["logs"]
    assert result["canProduceLiveCommands"] is False
