"""Replay 引擎对拍与用例(镜像 packages/trading-core/src/replay/replay.spec.ts)。

- L1 金标准对拍:account_90011087(input.json → expected.json)深度对比,
  数字相对容差 1e-9;字符串/数组/键集严格一致;键序不一致仅告警不算失败。
- 代表性 it() 用例(entry/SLTP/scale-in 位置冲突/breakout-pyramid/SMC bonus/tp 推进等策略路径)。
- compute_replay_coverage 对夹具的端到端覆盖。
"""

from __future__ import annotations

import json
import math
import shutil
from pathlib import Path
from typing import Any

import pytest

from backend.trading_core.replay.coverage import compute_replay_coverage
from backend.trading_core.replay.replay import run_replay

FIXTURE_ROOT = Path(__file__).resolve().parents[2] / "fixtures" / "replay"
ACCOUNT_90011087 = FIXTURE_ROOT / "account_90011087"

REL_TOL = 1e-9


# ------------------------------------------------------------------ 递归对拍 helper


def _deep_compare(got: Any, want: Any, path: str = "root") -> tuple[bool, list[str]]:
    """递归深度对比。

    数字:相对容差 1e-9(math.isclose);字符串/布尔严格;数组长度与元素顺序严格;
    dict 键集严格,键序不一致仅追加 warning(不算失败)。
    """
    warnings: list[str] = []
    if isinstance(want, dict):
        if not isinstance(got, dict):
            return False, [f"{path}: 期望 dict,实际 {type(got).__name__}"]
        if set(got) != set(want):
            return False, [f"{path}: 键集不一致 got={sorted(got)} want={sorted(want)}"]
        if list(got.keys()) != list(want.keys()):
            warnings.append(f"{path}: 键序不一致(warning only) got={list(got)} want={list(want)}")
        for key in want:
            ok, ws = _deep_compare(got[key], want[key], f"{path}.{key}")
            warnings.extend(ws)
            if not ok:
                return False, warnings
        return True, warnings
    if isinstance(want, list):
        if not isinstance(got, list):
            return False, [f"{path}: 期望 list,实际 {type(got).__name__}"]
        if len(got) != len(want):
            return False, [f"{path}: 长度不一致 got={len(got)} want={len(want)}"]
        for index, (g, w) in enumerate(zip(got, want, strict=False)):
            ok, ws = _deep_compare(g, w, f"{path}[{index}]")
            warnings.extend(ws)
            if not ok:
                return False, warnings
        return True, warnings
    if isinstance(want, bool) or isinstance(got, bool):
        if got is want:
            return True, warnings
        return False, [f"{path}: {got!r} != {want!r}"]
    if isinstance(want, (int, float)):
        if not isinstance(got, (int, float)):
            return False, [f"{path}: 期望 number,实际 {type(got).__name__}"]
        if math.isnan(float(want)):
            return math.isnan(float(got)), warnings
        if not math.isclose(float(got), float(want), rel_tol=REL_TOL, abs_tol=0):
            return False, [f"{path}: {got} != {want} (相对容差 {REL_TOL})"]
        return True, warnings
    if got != want:
        return False, [f"{path}: {got!r} != {want!r}"]
    return True, warnings


def _assert_deep_equal(got: Any, want: Any, label: str) -> None:
    ok, warnings = _deep_compare(got, want, label)
    for warning in warnings:
        if warning.startswith(f"{label}.") and "键序不一致" in warning:
            print(f"[warn] {warning}")
    assert ok, f"{label} 对拍失败:\n" + "\n".join(warnings)


# ------------------------------------------------------------------ spec 用例 bar builders


def pullback_buy_bars() -> list[dict[str, Any]]:
    out = [
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
    out[48] = {**out[48], "close": 95.2, "open": 95.2}
    out[49] = {**out[49], "close": 95, "open": 95}
    return out


def pullback_weak_adx_buy_bars() -> list[dict[str, Any]]:
    bars = pullback_buy_bars()
    bars[48]["adx"] = 26
    bars[49]["adx"] = 26
    return bars


def pullback_m15_confirm_bars() -> list[dict[str, Any]]:
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


def h4_range_bars() -> list[dict[str, Any]]:
    return [
        {"time": f"t{index}", "open": 95, "high": 96, "low": 94, "close": 95, "adx": 10, "ema20": 100, "ema50": 99}
        for index in range(50)
    ]


def h4_strong_bear_bars() -> list[dict[str, Any]]:
    return [
        {"time": f"t{index}", "open": 95, "high": 96, "low": 94, "close": 95, "adx": 35, "ema20": 99, "ema50": 100}
        for index in range(50)
    ]


def m30_neutral_bars() -> list[dict[str, Any]]:
    return [{"time": "t0", "open": 95, "high": 96, "low": 94, "close": 95, "adx": 10, "ema20": 95, "ema50": 95}]


def breakout_retest_buy_bars() -> dict[str, Any]:
    out = [
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
        out[index]["high"] = 102
    last_five = [
        (103.2, 101.7, 103),
        (102.8, 101.8, 102.5),
        (102.6, 101.9, 102.3),
        (102.5, 101.95, 102.25),
        (102.4, 102.0, 102.2),
    ]
    for offset, (high, low, close) in enumerate(last_five):
        bar = out[50 + offset]
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
            bar["r1"] = 106
    return {"H1": out}


def divergence_buy_bars() -> list[dict[str, Any]]:
    out = [
        {
            "time": f"t{index}",
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
    out[5] = {**out[5], "close": 95, "rsi": 30, "macd_hist": -0.5}
    out[25] = {**out[25], "close": 93, "rsi": 35, "macd_hist": -0.2}
    out[28] = {**out[28], "macd_hist": 0.1}
    out[29] = {
        **out[29],
        "open": 94,
        "high": 95,
        "low": 93.5,
        "close": 94,
        "rsi": 38,
        "macd_hist": 0.2,
        "volume": 60,
        "vol_sma": 100,
    }
    return out


def breakout_pyramid_base_bars() -> list[dict[str, Any]]:
    return [
        {
            "time": f"t{index}",
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


def breakout_pyramid_buy_signal_bars() -> list[dict[str, Any]]:
    bars = breakout_pyramid_base_bars()
    bars[29] = {**bars[29], "open": 102, "high": 102.4, "low": 101.2, "close": 102}
    return bars


def breakout_pyramid_buy_order_block_bars() -> list[dict[str, Any]]:
    bars = breakout_pyramid_base_bars()
    bars[13]["high"] = 101.25
    bars[13]["close"] = 100.8
    bars[18] = {**bars[18], "open": 99.5, "high": 101.4, "low": 99.4, "close": 101.2}
    bars[19]["high"] = 101.45
    bars[19]["close"] = 101.3
    bars[29]["close"] = 101.2
    bars[29]["ema20"] = 101
    bars[29]["ema50"] = 99
    bars[29]["bb_upper"] = 101
    return bars


def counter_pullback_buy_bars() -> list[dict[str, Any]]:
    return [
        {
            "time": f"t{index}",
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


def pullback_fib_h4_bars_up() -> list[dict[str, Any]]:
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


def pullback_fib_buy_bars(fib: dict[str, float]) -> list[dict[str, Any]]:
    out = [
        {
            "time": f"t{index}",
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
    out[48] = {**out[48], "close": 95.2, "open": 95.2}
    out[49] = {**out[49], "close": 95, "open": 95, **fib}
    return out


def pullback_narrow_fib_buy_bars() -> list[dict[str, Any]]:
    """镜像 spec pullbackNarrowFibBuyBars:BB/fib 在棒内,ema20=94.8(用于 fib 门止损 93.6)。"""
    out = [
        {
            "time": f"2026-04-15T{index:02d}:00:00.000Z",
            "open": 95,
            "high": 96,
            "low": 94,
            "close": 95,
            "atr": 2,
            "adx": 35,
            "rsi": 45,
            "ema20": 94.8,
            "ema50": 93.8,
            "macd_hist": 1,
            "bb_upper": 100,
            "bb_lower": 92,
        }
        for index in range(50)
    ]
    out[48] = {**out[48], "close": 94.9, "open": 94.9, "high": 95, "low": 94.8}
    out[49] = {**out[49], "close": 95, "open": 95, "fib_382": 98.36, "fib_618": 96, "fib_786": 94.6}
    return out


def pullback_buy_bars_with_bb_support_resistance() -> list[dict[str, Any]]:
    out = [
        {
            "time": f"2026-04-16T{index:02d}:00:00.000Z",
            "open": 1.1,
            "high": 1.1004,
            "low": 1.1,
            "close": 1.1,
            "atr": 0.001,
            "adx": 35,
            "rsi": 45,
            "ema20": 1.09995,
            "ema50": 1.09994,
            "macd_hist": 0.0001,
            "bb_upper": 1.1017,
            "bb_lower": 1.0992,
            "fib_382": 0,
            "fib_618": 0,
            "fib_786": 0,
        }
        for index in range(50)
    ]
    out[48] = {**out[48], "close": 1.09996, "open": 1.09996, "high": 1.103, "low": 1.096}
    out[49] = {**out[49], "close": 1.1, "open": 1.1}
    return out


def _assert_log_present(logs: list[dict[str, Any]], want: dict[str, Any]) -> None:
    assert any(
        log["level"] == want["level"] and log["strategy"] == want["strategy"] and log["msg"] == want["msg"]
        for log in logs
    ), f"缺少日志 {want}"


def _any_log_contains(logs: list[dict[str, Any]], strategy: str, fragment: str) -> bool:
    return any(log["strategy"] == strategy and fragment in log["msg"] for log in logs)


# ------------------------------------------------------------------ L1 金标准对拍


def test_l1_golden_fixture_account_90011087() -> None:
    with open(ACCOUNT_90011087 / "input.json", encoding="utf-8") as fh:
        snapshot = json.load(fh)
    with open(ACCOUNT_90011087 / "expected.json", encoding="utf-8") as fh:
        expected: dict[str, Any] = json.load(fh)

    result = run_replay(snapshot)

    assert result["canProduceLiveCommands"] is False
    assert result["position_states"] == []
    for key in expected:
        _assert_deep_equal(result[key], expected[key], f"result.{key}")


# ------------------------------------------------------------------ 持仓冲突 / 策略隔离


def test_blocks_same_side_signal_within_1_atr() -> None:
    result = run_replay(
        {
            "account_id": "90011087",
            "current_price": 95,
            "bars": {"H1": pullback_buy_bars()},
            "positions": [{"ticket": 101, "symbol": "XAUUSD", "type": "BUY", "lots": 0.1, "open_price": 95.5}],
        }
    )
    assert result["signal"] is None
    assert _any_log_contains(result["logs"], "汇总", "防重复: 已有同向持仓")
    assert result["position_commands"] is None


def test_blocks_opposing_side_signal_within_2_atr() -> None:
    result = run_replay(
        {
            "account_id": "90011087",
            "current_price": 95,
            "bars": {"H1": pullback_buy_bars()},
            "positions": [{"ticket": 102, "symbol": "XAUUSD", "type": "SELL", "lots": 0.1, "open_price": 98}],
        }
    )
    assert result["signal"] is None
    assert _any_log_contains(result["logs"], "汇总", "防对冲: 已有反向持仓")


def test_strategy_isolation_allows_different_strategy_position() -> None:
    result = run_replay(
        {
            "account_id": "90011087",
            "current_price": 95,
            "bars": {"H1": pullback_buy_bars()},
            "positions": [
                {
                    "ticket": 101,
                    "symbol": "XAUUSD",
                    "type": "BUY",
                    "lots": 0.1,
                    "open_price": 95.5,
                    "strategy": "breakout_retest",
                }
            ],
        }
    )
    assert result["signal"] is not None
    assert result["signal"]["strategy"] == "pullback"
    assert result["signal"]["side"] == "BUY"


def test_strategy_isolation_blocks_same_strategy_position() -> None:
    result = run_replay(
        {
            "account_id": "90011087",
            "current_price": 95,
            "bars": {"H1": pullback_buy_bars()},
            "positions": [
                {
                    "ticket": 101,
                    "symbol": "XAUUSD",
                    "type": "BUY",
                    "lots": 0.1,
                    "open_price": 95.5,
                    "strategy": "pullback",
                }
            ],
        }
    )
    assert result["signal"] is None
    assert _any_log_contains(result["logs"], "汇总", "防重复: 已有同向持仓 [pullback]")


# ------------------------------------------------------------------ pullback + M15 确认


def test_m15_confirm_boosts_pullback_buy_signal() -> None:
    result = run_replay(
        {
            "account_id": "90011087",
            "current_price": 95,
            "bars": {"H1": pullback_buy_bars(), "M15": pullback_m15_confirm_bars()},
        }
    )
    _assert_deep_equal(
        result["signal"],
        {
            "side": "BUY",
            "entry": 95,
            "stop_loss": 93.13,
            "tp1": 95.8,
            "tp2": 97.5,
            "score": 10,
            "strategy": "pullback",
            "atr": 2,
            "all_strategies": [{"strategy": "pullback", "side": "BUY", "score": 10, "entry": 95, "stop_loss": 93.13}],
        },
        "signal",
    )
    _assert_log_present(
        result["logs"],
        {
            "level": "signal",
            "strategy": "趋势回调",
            "msg": "🟢 BUY 评分=9 | EMA20回调 dist=0.80 | MACD柱>0 | RSI=45.0<50 | ADX=35.0>30 | 连续2根回调到位",
        },
    )
    _assert_log_present(
        result["logs"],
        {
            "level": "info",
            "strategy": "M15确认",
            "msg": "✅ pullback | M15确认: RSI=35.0<40(多头) | 近Fib382=95.12 | 评分+1→10",
        },
    )
    _assert_log_present(
        result["logs"],
        {"level": "signal", "strategy": "汇总", "msg": "✅ 发出信号: BUY @ 95.00 | SL=93.13 | 策略=pullback | 评分=10"},
    )


# ------------------------------------------------------------------ H4 过滤


def test_h4_range_blocks_all_signals_hard_mode() -> None:
    result = run_replay(
        {
            "account_id": "90011087",
            "current_price": 95,
            "bars": {"H1": pullback_buy_bars(), "H4": h4_range_bars()},
        }
    )
    assert result["signal"] is None
    assert _any_log_contains(result["logs"], "H4过滤", "震荡市禁入")


def test_h4_range_keeps_candidates_soft_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GB_H4_ADX_FILTER_MODE", "soft")
    result = run_replay(
        {
            "account_id": "90011087",
            "current_price": 95,
            "bars": {"H1": pullback_buy_bars(), "H4": h4_range_bars()},
        }
    )
    assert _any_log_contains(result["logs"], "H4过滤", "不做方向偏置")


def test_h4_opposite_trend_filters_signal() -> None:
    result = run_replay(
        {
            "account_id": "90011087",
            "current_price": 95,
            "bars": {"H1": pullback_buy_bars(), "H4": h4_strong_bear_bars()},
        }
    )
    assert result["signal"] is None
    _assert_log_present(
        result["logs"], {"level": "warn", "strategy": "H4过滤", "msg": "H4=强空头,过滤掉 1 个逆势信号,保留 0 个"}
    )
    _assert_log_present(result["logs"], {"level": "info", "strategy": "H4过滤", "msg": "H4趋势过滤后无信号"})


# ------------------------------------------------------------------ 趋势评级 / 上下文加分


def test_soft_trend_rating_penalty_under_weak_consensus() -> None:
    result = run_replay(
        {
            "account_id": "90011087",
            "current_price": 95,
            "bars": {"H1": pullback_weak_adx_buy_bars(), "M30": m30_neutral_bars()},
        }
    )
    _assert_deep_equal(
        result["signal"],
        {
            "side": "BUY",
            "entry": 95,
            "stop_loss": 93.13,
            "tp1": 95.8,
            "tp2": 97.5,
            "score": 7,
            "strategy": "pullback",
            "atr": 2,
            "all_strategies": [{"strategy": "pullback", "side": "BUY", "score": 7, "entry": 95, "stop_loss": 93.13}],
        },
        "signal",
    )


def test_harmonic_bonus_grants_plus_one_only_for_quality_patterns() -> None:
    snapshot = {
        "account_id": "90011087",
        "current_price": 95,
        "bars": {"H1": pullback_weak_adx_buy_bars(), "M30": m30_neutral_bars()},
    }
    weak = run_replay(
        {**snapshot, "harmonic": {"active_pattern": {"type": "gartley", "direction": "BUY", "score": 20}}}
    )
    assert weak["signal"]["score"] == 7
    medium = run_replay(
        {**snapshot, "harmonic": {"active_pattern": {"type": "gartley", "direction": "BUY", "score": 45}}}
    )
    assert medium["signal"]["score"] == 8
    strong = run_replay(
        {**snapshot, "harmonic": {"active_pattern": {"type": "gartley", "direction": "BUY", "score": 85}}}
    )
    assert strong["signal"]["score"] == 9


def test_smc_choch_sweep_ob_bonus_from_replay_context() -> None:
    result = run_replay(
        {
            "account_id": "90011087",
            "current_price": 95,
            "bars": {"H1": pullback_weak_adx_buy_bars(), "M30": m30_neutral_bars()},
            "smc": {
                "h1_breaks": [{"index": 48, "direction": "UP", "level": 94, "type": "CHoCH"}],
                "h1_sweeps": [{"index": 48, "level": 95, "side": "BULL", "reversed": True}],
                "h1_obs": [{"index": 48, "side": "BUY", "high": 96, "low": 94, "valid": True}],
                "h1_fvgs": [{"index": 48, "upper_bound": 96, "lower_bound": 94, "filled": False}],
            },
        }
    )
    assert result["signal"] is not None
    assert result["signal"]["score"] == 10
    _assert_deep_equal(
        result["signal"]["all_strategies"],
        [{"strategy": "pullback", "side": "BUY", "score": 10, "entry": 95, "stop_loss": 93.13}],
        "all_strategies",
    )


# ------------------------------------------------------------------ 策略 oracle 切片


def test_breakout_retest_buy_oracle_slice() -> None:
    result = run_replay({"account_id": "90011087", "current_price": 102.2, "bars": breakout_retest_buy_bars()})
    _assert_deep_equal(
        result["signal"],
        {
            "side": "BUY",
            "entry": 102.2,
            "stop_loss": 99.6,
            "tp1": 106,
            "tp2": 106,
            "score": 10,
            "strategy": "breakout_retest",
            "atr": 2,
            "all_strategies": [
                {"strategy": "breakout_retest", "side": "BUY", "score": 10, "entry": 102.2, "stop_loss": 99.6}
            ],
        },
        "signal",
    )
    _assert_log_present(
        result["logs"],
        {
            "level": "signal",
            "strategy": "突破回踩",
            "msg": (
                "🟢 BUY 评分=10 | 阻力位=102.00 突破后回踩 dist=0.20 | "
                "成交量确认 | MACD柱>0 | ADX=26.0 | RSI=58.0 | 回踩确认3根"
            ),
        },
    )


def test_divergence_buy_oracle_slice() -> None:
    result = run_replay({"account_id": "90011087", "current_price": 94, "bars": {"H1": divergence_buy_bars()}})
    _assert_deep_equal(
        result["signal"],
        {
            "side": "BUY",
            "entry": 94,
            "stop_loss": 91,
            "tp1": 98,
            "tp2": 102,
            "score": 9,
            "strategy": "divergence",
            "atr": 2,
            "all_strategies": [{"strategy": "divergence", "side": "BUY", "score": 9, "entry": 94, "stop_loss": 91}],
        },
        "signal",
    )
    _assert_log_present(
        result["logs"],
        {
            "level": "signal",
            "strategy": "RSI背离",
            "msg": (
                "🟢 BUY 评分=9 | 看涨背离: 价格新低93.00<95.00 RSI抬高35.0>30.0 | "
                "MACD背离确认 | 成交量萎缩 | StochK=0"
            ),
        },
    )


def test_breakout_pyramid_buy_oracle_slice() -> None:
    result = run_replay(
        {"account_id": "90011087", "current_price": 102, "bars": {"H1": breakout_pyramid_buy_signal_bars()}}
    )
    _assert_deep_equal(
        result["signal"],
        {
            "side": "BUY",
            "entry": 102,
            "stop_loss": 98,
            "tp1": 106,
            "tp2": 112,
            "score": 9,
            "strategy": "breakout_pyramid",
            "atr": 2,
            "all_strategies": [
                {"strategy": "breakout_pyramid", "side": "BUY", "score": 9, "entry": 102, "stop_loss": 98}
            ],
        },
        "signal",
    )
    _assert_log_present(
        result["logs"],
        {
            "level": "signal",
            "strategy": "突破加仓",
            "msg": "🟢 BUY 评分=9 | 收盘价突破布林上轨=101.00 | ADX=35.0>30 | RSI=60.0 | MACD柱>0",
        },
    )


def test_breakout_pyramid_buy_guard_ahead_of_order_block() -> None:
    result = run_replay(
        {"account_id": "90011087", "current_price": 101.2, "bars": {"H1": breakout_pyramid_buy_order_block_bars()}}
    )
    assert result["signal"] is None
    _assert_log_present(
        result["logs"],
        {"level": "info", "strategy": "突破加仓", "msg": "前方有空头OB 101.40 (距离0.2点), 突破风险高 ⏭"},
    )


def test_counter_pullback_buy_oracle_slice() -> None:
    h1 = [{**bar, "atr": 2} for bar in counter_pullback_buy_bars()]
    result = run_replay(
        {
            "account_id": "90011087",
            "current_price": 100.4,
            "bars": {"H1": h1, "M30": counter_pullback_buy_bars()},
            "smc": {
                "m30_breaks": [{"index": 18, "direction": "UP", "level": 101, "type": "CHoCH"}],
                "m30_sweeps": [{"index": 17, "level": 100, "side": "BULL", "reversed": True}],
            },
        }
    )
    _assert_deep_equal(
        result["signal"],
        {
            "side": "BUY",
            "entry": 100.4,
            "stop_loss": 99,
            "tp1": 104.4,
            "tp2": 108.4,
            "score": 6,
            "strategy": "counter_pullback",
            "atr": 2,
            "all_strategies": [
                {"strategy": "counter_pullback", "side": "BUY", "score": 6, "entry": 100.4, "stop_loss": 99}
            ],
        },
        "signal",
    )
    _assert_log_present(
        result["logs"],
        {
            "level": "signal",
            "strategy": "反转回调",
            "msg": (
                "🟢 BUY 评分=7 | M30 | 看涨反转回调: CHoCH↑+Sweep@100.00 | CHoCH@18 | "
                "Sweep@100.00 | RSI=44.0 | MACD>0"
            ),
        },
    )


# ------------------------------------------------------------------ pullback + FIB gating / SR SLTP


def test_wide_fib_pullback_rejected_by_global_min_rr() -> None:
    result = run_replay(
        {
            "account_id": "90011087",
            "symbol": "XAUUSD",
            "current_price": 95,
            "bars": {
                "H1": pullback_fib_buy_bars({"fib_382": 96, "fib_618": 92, "fib_786": 89}),
                "H4": pullback_fib_h4_bars_up(),
            },
        }
    )
    assert result["signal"] is None
    _assert_log_present(
        result["logs"],
        {
            "level": "info",
            "strategy": "pullback",
            "msg": "🌀 pullback+FIB: fib786 止损距离超限 (3.50 ATR > 1.5) 回退 ⏭",
        },
    )
    _assert_log_present(
        result["logs"],
        {"level": "warn", "strategy": "R:R过滤", "msg": "⚠️ 信号 R:R=0.535 < 1.25 拒绝 ⏭"},
    )


def test_narrow_fib_pullback_kept_when_rr_preserved() -> None:
    result = run_replay(
        {
            "account_id": "90011087",
            "symbol": "XAUUSD",
            "current_price": 95,
            "bars": {"H1": pullback_narrow_fib_buy_bars(), "H4": pullback_fib_h4_bars_up()},
        }
    )
    assert result["signal"] is not None
    signal = result["signal"]
    _assert_deep_equal(
        {key: signal[key] for key in ("side", "entry", "stop_loss", "tp1", "tp2", "score", "strategy")},
        {
            "side": "BUY",
            "entry": 95,
            "stop_loss": 93.6,
            "tp1": 98.36,
            "tp2": 98.36,
            "score": 10,
            "strategy": "pullback",
        },
        "signal(subset)",
    )
    rr = (signal["tp1"] - signal["entry"]) / (signal["entry"] - signal["stop_loss"])
    assert rr >= 1.25
    assert not _any_log_contains(result["logs"], "R:R过滤", "拒绝")


def test_pick_sltp_buy_uses_bb_levels_from_enrichment() -> None:
    result = run_replay(
        {
            "account_id": "90011087",
            "symbol": "EURUSD",
            "current_price": 1.1,
            "bars": {"H1": pullback_buy_bars_with_bb_support_resistance()},
        }
    )
    assert result["signal"] is not None
    signal = result["signal"]
    _assert_deep_equal(
        {key: signal[key] for key in ("side", "strategy", "entry", "stop_loss", "tp1", "tp2")},
        {"side": "BUY", "strategy": "pullback", "entry": 1.1, "stop_loss": 1.0987, "tp1": 1.1017, "tp2": 1.1017},
        "signal(subset)",
    )


# ------------------------------------------------------------------ 持仓命令(仅审计)


def test_position_commands_audit_only_with_complete_state() -> None:
    h1_bars = [
        {
            "time": f"2026-04-13T{index:02d}:00:00.000Z",
            "open": 3340,
            "high": 3341,
            "low": 3339,
            "close": 3340,
        }
        for index in range(15)
    ]
    result = run_replay(
        {
            "account_id": "90011087",
            "symbol": "XAUUSD",
            "analysis_time": "2026-04-13T08:00:00.000Z",
            "current_price": 3343.2,
            "bars": {"H1": h1_bars},
            "positions": [{"ticket": 202, "symbol": "XAUUSD", "type": "BUY", "open_price": 3340, "lots": 0.5}],
            "position_states": [{"ticket": 202, "open_time": "2026-04-13T06:00:00.000Z", "be_trigger_atr": 1.5}],
        }
    )
    _assert_deep_equal(
        result["position_commands"],
        [
            {"action": "MODIFY", "ticket": 202, "new_sl": 3340, "reason": "breakeven_1.6ATR"},
            {"action": "CLOSE", "ticket": 202, "lots": 0.2, "reason": "TP1_1.6ATR"},
        ],
        "position_commands",
    )
    states = result["position_states"]
    assert states is not None
    state = next(s for s in states if s["ticket"] == 202)
    assert state["beMoved"] is True
    assert state["tp1Hit"] is True
    assert math.isclose(state["maxProfitAtr"], 1.6, rel_tol=0, abs_tol=5e-5)
    assert result["canProduceLiveCommands"] is False


def test_replay_only_positions_do_not_synthesize_commands() -> None:
    with open(ACCOUNT_90011087 / "input.json", encoding="utf-8") as fh:
        snapshot = json.load(fh)
    snapshot["positions"] = [{"ticket": 101, "symbol": "XAUUSD", "type": "BUY", "lots": 0.1}]
    result = run_replay(snapshot)
    assert result["position_commands"] is None
    assert result["canProduceLiveCommands"] is False


# ------------------------------------------------------------------ coverage


def test_compute_replay_coverage_on_fixture_pairs(tmp_path: Path) -> None:
    """将 L1 夹具复制为 coverage 模块约定的 *_snapshot.json / *_expected.json 配对并跑通覆盖。"""
    with open(ACCOUNT_90011087 / "input.json", encoding="utf-8") as fh:
        snapshot = json.load(fh)
    with open(ACCOUNT_90011087 / "expected.json", encoding="utf-8") as fh:
        expected = json.load(fh)
    case_dir = tmp_path / "replay"
    case_dir.mkdir()
    with open(case_dir / "account_90011087_snapshot.json", "w", encoding="utf-8") as fh:
        json.dump(snapshot, fh, ensure_ascii=False)
    with open(case_dir / "account_90011087_expected.json", "w", encoding="utf-8") as fh:
        json.dump(expected, fh, ensure_ascii=False)
    shutil.copy(ACCOUNT_90011087 / "input.json", case_dir / "unpaired.json")

    summary = compute_replay_coverage(str(case_dir))
    assert summary == {"total": 1, "validated": 1}


def test_compute_replay_coverage_sees_no_pairs_in_top_level_fixture_dir() -> None:
    """当前夹具根目录是 input.json/expected.json 命名,coverage(按 _snapshot.json 配对)应为 0 对。"""
    summary = compute_replay_coverage(str(FIXTURE_ROOT))
    assert summary == {"total": 0, "validated": 0}
