"""镜像 packages/trading-core/src/positionmgr/manager.spec.ts(每个 it() 对应一个测试函数)。

断言与 vitest 语义等价:
- toEqual / assert 精确 dict 相等;toMatchObject / objectContaining → assert_match 部分匹配。
- toBeCloseTo(x, d) → 容差 0.5 * 10^-d 的 isclose。
- 小数结果由同一套 IEEE double 运算产出,与 TS 期望值逐位一致。
"""

from __future__ import annotations

import math
import re
from typing import Any

from backend.trading_core.positionmgr import (
    evaluate_position_breakeven,
    evaluate_position_dynamic_trailing,
    evaluate_position_key_levels,
    evaluate_position_manager_commands,
    evaluate_position_momentum_scalp_exits,
    evaluate_position_time_stops,
    evaluate_position_tp1,
    evaluate_position_tp2,
    evaluate_position_trend_reversal,
    summarize_positions,
)

NOW = "2026-04-13T08:00:00.000Z"
EMPTY5 = [{}, {}, {}, {}, {}]

H1_BARS = [
    {"ema20": 3341, "ema50": 3337, "rsi": 65, "adx": 32, "macdHist": 0.6, "atr": 2},
    {"ema20": 3341.5, "ema50": 3337.5, "rsi": 63, "adx": 31, "macdHist": 0.5, "atr": 2},
    {"ema20": 3342, "ema50": 3338, "rsi": 60, "adx": 30, "macdHist": 0.4, "atr": 2},
    {"ema20": 3342.5, "ema50": 3338.5, "rsi": 58, "adx": 31, "macdHist": 0.3, "atr": 2},
    {"ema20": 3343, "ema50": 3339, "rsi": 56, "adx": 29, "macdHist": 0.2, "atr": 2},
]

BULLISH_M5_BARS = [
    {"close": 99.6},
    {"close": 99.8},
    {"close": 100.0},
    {"close": 100.1},
    {"close": 100.2},
    {"close": 100.3},
    {"close": 100.35},
    {"close": 100.4},
]


def assert_match(result: Any, want: Any) -> None:
    """vitest toMatchObject / expect.objectContaining :递归部分匹配。"""
    if isinstance(want, dict):
        assert isinstance(result, dict), f"expected dict, got {result!r}"
        for key, value in want.items():
            assert key in result, f"missing key {key!r} in {result!r}"
            assert_match(result[key], value)
        return
    assert result == want, f"{result!r} != {want!r}"


def assert_contains(seq: Any, want: Any) -> None:
    """vitest toContainEqual:列表中存在完全相等的元素。"""
    for item in seq:
        if item == want:
            return
    raise AssertionError(f"expected {want!r} to be contained in {seq!r}")


def assert_contains_match(seq: Any, want: dict[str, Any]) -> None:
    """vitest toContainEqual(expect.objectContaining(...))。"""
    for item in seq:
        try:
            assert_match(item, want)
            return
        except AssertionError:
            continue
    raise AssertionError(f"expected an element matching {want!r} in {seq!r}")


def assert_close(got: float, want: float, num_digits: float = 2) -> None:
    """vitest toBeCloseTo:容差 0.5 * 10^-numDigits。"""
    tolerance = 0.5 * 10 ** (-num_digits)
    assert math.isclose(got, want, rel_tol=0, abs_tol=tolerance), f"{got!r} !~ {want!r}"


def assert_close_contained(seq: Any, want: dict[str, Any], num_digits: float) -> None:
    """toContainEqual(objectContaining({ newSL: expect.closeTo(...) }))。"""
    tolerance = 0.5 * 10 ** (-num_digits)
    for item in seq:
        if item["action"] != want["action"] or item["ticket"] != want["ticket"] or item["reason"] != want["reason"]:
            continue
        if math.isclose(float(item["newSL"]), want["newSL"], rel_tol=0, abs_tol=tolerance):
            return
    raise AssertionError(f"expected closeTo advisory {want!r} in {seq!r}")


def assert_reason_matching(seq: Any, ticket: int, lots: float, pattern: str) -> None:
    """toBeContainEqual(objectContaining({ action, ticket, lots, reason: stringMatching }))。"""
    for item in seq:
        if (
            item.get("action") == "CLOSE"
            and item.get("ticket") == ticket
            and item.get("lots") == lots
            and re.search(pattern, str(item.get("reason")))
        ):
            return
    raise AssertionError(f"expected CLOSE ticket={ticket} lots={lots} matching {pattern!r} in {seq!r}")


# ---------------------------------------------------------------------------
# position manager summary parity slice
# ---------------------------------------------------------------------------


class TestSummaryParity:
    def test_normalizes_symbols_and_summarizes_open_exposure(self) -> None:
        summary = summarize_positions(
            {
                "accountId": "90011087",
                "symbol": "GOLDm#",
                "positions": [
                    {
                        "ticket": 101, "symbol": "GOLDm#", "type": "BUY", "lots": 0.2, "openPrice": 3330,
                        "profit": 12.5, "strategy": "pullback",
                    },
                    {
                        "ticket": 102, "symbol": "XAUUSD", "type": "BUY", "lots": 0.1, "openPrice": 3340,
                        "profit": -1.25, "strategy": "pullback",
                    },
                    {
                        "ticket": 103, "symbol": "XAUUSD", "type": "SELL", "lots": 0.05, "openPrice": 3350,
                        "profit": 2.75, "strategy": "ai_signal",
                    },
                    {
                        "ticket": 104, "symbol": "GBPJPY", "type": "BUY", "lots": 0.4, "openPrice": 190.12,
                        "profit": 4.5, "strategy": "range",
                    },
                    {"ticket": 0, "symbol": "XAUUSD", "type": "BUY", "lots": 1, "openPrice": 3330, "profit": 100},
                    {"ticket": 105, "symbol": "XAUUSD", "type": "SELL", "lots": 0, "openPrice": 3350, "profit": 100},
                ],
            }
        )

        assert_match(
            summary,
            {
                "accountId": "90011087",
                "symbol": "XAUUSD",
                "totalOpenPositions": 3,
                "buyLots": 0.3,
                "sellLots": 0.05,
                "netLots": 0.25,
                "netSide": "BUY",
                "floatingProfit": 14,
                "canProduceLiveCommands": False,
            },
        )
        assert_close(summary["weightedAverageEntry"], 3333.333333, 6)
        assert summary["byStrategy"] == [
            {
                "strategy": "ai_signal", "positions": 1, "buyLots": 0, "sellLots": 0.05,
                "netLots": -0.05, "floatingProfit": 2.75,
            },
            {
                "strategy": "pullback", "positions": 2, "buyLots": 0.3, "sellLots": 0,
                "netLots": 0.3, "floatingProfit": 11.25,
            },
        ]

    def test_reports_flat_exposure_when_buy_and_sell_lots_offset(self) -> None:
        summary = summarize_positions(
            {
                "symbol": "GBPJPYm#",
                "positions": [
                    {"ticket": 201, "symbol": "GBPJPY", "type": "BUY", "lots": 0.1, "openPrice": 190.1},
                    {"ticket": 202, "symbol": "GBPJPY", "type": "SELL", "lots": 0.1, "openPrice": 190.3},
                ],
            }
        )

        assert summary["netSide"] == "FLAT"
        assert summary["netLots"] == 0


# ---------------------------------------------------------------------------
# position manager time-stop advisory parity slice
# ---------------------------------------------------------------------------


class TestTimeStopParity:
    def test_returns_no_advisories_for_invalid_snapshots(self) -> None:
        result = evaluate_position_time_stops(
            {
                "now": NOW,
                "currentPrice": 3340.8,
                "currentAtr": 0,
                "h1Bars": EMPTY5,
                "positions": [{"ticket": 101, "type": "BUY", "lots": 0.5, "openPrice": 3340}],
                "states": [{"ticket": 101, "openTime": "2026-04-11T07:00:00.000Z"}],
            }
        )

        assert result == {"advisories": [], "canProduceLiveCommands": False}

    def test_mirrors_go_48h_full_close_time_stop(self) -> None:
        result = evaluate_position_time_stops(
            {
                "now": NOW,
                "currentPrice": 3340.8,
                "currentAtr": 2,
                "avgAtr": 2,
                "h1Bars": EMPTY5,
                "positions": [{"ticket": 101, "type": "BUY", "lots": 0.5, "openPrice": 3340}],
                "states": [{"ticket": 101, "openTime": "2026-04-11T07:00:00.000Z"}],
            }
        )

        assert result == {
            "advisories": [{"action": "CLOSE", "ticket": 101, "lots": 0.5, "reason": "time_48h_0.4ATR"}],
            "canProduceLiveCommands": False,
        }

    def test_mirrors_go_72h_partial_close_and_tiny_lot_fallback(self) -> None:
        result = evaluate_position_time_stops(
            {
                "now": NOW,
                "currentPrice": 3342,
                "currentAtr": 2,
                "avgAtr": 2,
                "h1Bars": EMPTY5,
                "positions": [
                    {"ticket": 201, "type": "BUY", "lots": 0.5, "openPrice": 3340},
                    {"ticket": 202, "type": "BUY", "lots": 0.05, "openPrice": 3340},
                ],
                "states": [
                    {"ticket": 201, "openTime": "2026-04-10T07:00:00.000Z"},
                    {"ticket": 202, "openTime": "2026-04-10T07:00:00.000Z"},
                ],
            }
        )

        assert result["advisories"] == [
            {"action": "CLOSE", "ticket": 201, "lots": 0.25, "reason": "time_72h_1.0ATR"},
            {"action": "CLOSE", "ticket": 202, "lots": 0.05, "reason": "time_72h_1.0ATR"},
        ]

    def test_mirrors_go_24h_low_volatility_time_stop(self) -> None:
        result = evaluate_position_time_stops(
            {
                "now": NOW,
                "currentPrice": 3340.05,
                "currentAtr": 1,
                "avgAtr": 2,
                "h1Bars": EMPTY5,
                "positions": [{"ticket": 301, "type": "BUY", "lots": 0.4, "openPrice": 3340}],
                "states": [{"ticket": 301, "openTime": "2026-04-12T07:00:00.000Z"}],
            }
        )

        assert result["advisories"] == [
            {"action": "CLOSE", "ticket": 301, "lots": 0.4, "reason": "time_24h_0.1ATR_lowvol"}
        ]

    def test_does_not_emit_72h_advisory_when_tp2_already_hit(self) -> None:
        result = evaluate_position_time_stops(
            {
                "now": NOW,
                "currentPrice": 3342,
                "currentAtr": 2,
                "avgAtr": 2,
                "h1Bars": EMPTY5,
                "positions": [{"ticket": 401, "type": "BUY", "lots": 0.5, "openPrice": 3340}],
                "states": [{"ticket": 401, "openTime": "2026-04-10T07:00:00.000Z", "tp2Hit": True}],
            }
        )

        assert result["advisories"] == []


# ---------------------------------------------------------------------------
# position manager breakeven advisory parity slice
# ---------------------------------------------------------------------------


class TestBreakevenParity:
    def test_mirrors_go_breakeven_modify_advisory_and_state_update(self) -> None:
        result = evaluate_position_breakeven(
            {
                "currentPrice": 3343.2,
                "currentAtr": 2,
                "positions": [{"ticket": 703, "type": "BUY", "lots": 0.5, "openPrice": 3340, "sl": 0}],
                "states": [{"ticket": 703, "beTriggerAtr": 1.5, "bestSl": 0}],
            }
        )

        assert result["advisories"] == [
            {"action": "MODIFY", "ticket": 703, "newSL": 3340, "reason": "breakeven_1.6ATR"}
        ]
        assert_match(
            result["nextStates"][0],
            {"ticket": 703, "beTriggerAtr": 1.5, "beMoved": True, "bestSl": 3340},
        )
        assert result["canProduceLiveCommands"] is False

    def test_allows_buy_breakeven_when_tracked_bestsl_polluted_but_current_sl_worse(self) -> None:
        result = evaluate_position_breakeven(
            {
                "currentPrice": 3344,
                "currentAtr": 2,
                "positions": [{"ticket": 705, "type": "BUY", "lots": 0.5, "openPrice": 3340, "sl": 3338}],
                "states": [{"ticket": 705, "beTriggerAtr": 1.5, "bestSl": 3342}],
            }
        )

        assert result["advisories"] == [
            {"action": "MODIFY", "ticket": 705, "newSL": 3340, "reason": "breakeven_2.0ATR"}
        ]
        assert_match(result["nextStates"][0], {"ticket": 705, "beMoved": True, "bestSl": 3340})
        assert result["canProduceLiveCommands"] is False

    def test_no_buy_breakeven_when_current_sl_already_better(self) -> None:
        result = evaluate_position_breakeven(
            {
                "currentPrice": 3344,
                "currentAtr": 2,
                "positions": [{"ticket": 701, "type": "BUY", "lots": 0.5, "openPrice": 3340, "sl": 3342}],
                "states": [{"ticket": 701, "beTriggerAtr": 1.5, "bestSl": 3342}],
            }
        )

        assert result["advisories"] == []
        assert_match(result["nextStates"][0], {"ticket": 701, "beMoved": False, "bestSl": 3342})
        assert result["canProduceLiveCommands"] is False

    def test_no_sell_breakeven_when_current_sl_already_better(self) -> None:
        result = evaluate_position_breakeven(
            {
                "currentPrice": 3336,
                "currentAtr": 2,
                "positions": [{"ticket": 702, "type": "SELL", "lots": 0.5, "openPrice": 3340, "sl": 3338}],
                "states": [{"ticket": 702, "beTriggerAtr": 1.5, "bestSl": 3338}],
            }
        )

        assert result["advisories"] == []
        assert_match(result["nextStates"][0], {"ticket": 702, "beMoved": False, "bestSl": 3338})
        assert result["canProduceLiveCommands"] is False

    def test_zero_be_trigger_uses_go_default_instead_of_moving_immediately(self) -> None:
        result = evaluate_position_breakeven(
            {
                "currentPrice": 3341,
                "currentAtr": 2,
                "positions": [{"ticket": 704, "type": "BUY", "lots": 0.5, "openPrice": 3340, "sl": 0}],
                "states": [{"ticket": 704, "beTriggerAtr": 0, "bestSl": 0}],
            }
        )

        assert result["advisories"] == []
        assert_match(result["nextStates"][0], {"ticket": 704, "beTriggerAtr": 1.5, "beMoved": False, "bestSl": 0})
        assert result["canProduceLiveCommands"] is False


# ---------------------------------------------------------------------------
# position manager TP1 advisory parity slice
# ---------------------------------------------------------------------------


class TestTP1Parity:
    def test_mirrors_go_tp1_close_advisory_and_state_update(self) -> None:
        result = evaluate_position_tp1(
            {
                "currentPrice": 3343.2,
                "currentAtr": 2,
                "h1Bars": EMPTY5,
                "positions": [{"ticket": 803, "type": "BUY", "lots": 0.5, "openPrice": 3340}],
                "states": [{"ticket": 803, "beMoved": True, "tp1Hit": False}],
            }
        )

        assert result["advisories"] == [{"action": "CLOSE", "ticket": 803, "lots": 0.2, "reason": "TP1_1.6ATR"}]
        assert_match(result["nextStates"][0], {"ticket": 803, "beMoved": True, "tp1Hit": True})
        assert result["canProduceLiveCommands"] is False

    def test_does_not_tp1_before_breakeven_has_moved(self) -> None:
        result = evaluate_position_tp1(
            {
                "currentPrice": 3345,
                "currentAtr": 2,
                "h1Bars": EMPTY5,
                "positions": [{"ticket": 804, "type": "BUY", "lots": 0.5, "openPrice": 3340}],
                "states": [{"ticket": 804, "beMoved": False, "tp1Hit": False}],
            }
        )

        assert result["advisories"] == []
        assert_match(result["nextStates"][0], {"ticket": 804, "beMoved": False, "tp1Hit": False})
        assert result["canProduceLiveCommands"] is False

    def test_does_not_tp1_twice_for_already_hit_state(self) -> None:
        result = evaluate_position_tp1(
            {
                "currentPrice": 3345,
                "currentAtr": 2,
                "h1Bars": EMPTY5,
                "positions": [{"ticket": 805, "type": "BUY", "lots": 0.5, "openPrice": 3340}],
                "states": [{"ticket": 805, "beMoved": True, "tp1Hit": True}],
            }
        )

        assert result["advisories"] == []
        assert_match(result["nextStates"][0], {"ticket": 805, "beMoved": True, "tp1Hit": True})
        assert result["canProduceLiveCommands"] is False

    def test_mirrors_go_early_buy_tp1_rsi_reversal_trigger(self) -> None:
        result = evaluate_position_tp1(
            {
                "currentPrice": 3341.8,
                "currentAtr": 2,
                "h1Bars": [{}, {}, {"rsi": 61}, {"rsi": 70}, {"rsi": 54}],
                "positions": [{"ticket": 806, "type": "BUY", "lots": 0.3, "openPrice": 3340}],
                "states": [{"ticket": 806, "beMoved": True, "tp1Hit": False}],
            }
        )

        assert result["advisories"] == [{"action": "CLOSE", "ticket": 806, "lots": 0.12, "reason": "TP1_0.9ATR"}]
        assert_match(result["nextStates"][0], {"ticket": 806, "beMoved": True, "tp1Hit": True})

    def test_mirrors_go_early_sell_tp1_rsi_reversal_trigger(self) -> None:
        result = evaluate_position_tp1(
            {
                "currentPrice": 3338.2,
                "currentAtr": 2,
                "h1Bars": [{}, {}, {"rsi": 39}, {"rsi": 30}, {"rsi": 46}],
                "positions": [{"ticket": 807, "type": "SELL", "lots": 0.3, "openPrice": 3340}],
                "states": [{"ticket": 807, "beMoved": True, "tp1Hit": False}],
            }
        )

        assert result["advisories"] == [{"action": "CLOSE", "ticket": 807, "lots": 0.12, "reason": "TP1_0.9ATR"}]
        assert_match(result["nextStates"][0], {"ticket": 807, "beMoved": True, "tp1Hit": True})
        assert result["canProduceLiveCommands"] is False

    def test_mirrors_go_tp1_tiny_lot_full_close_fallback(self) -> None:
        result = evaluate_position_tp1(
            {
                "currentPrice": 3345,
                "currentAtr": 2,
                "h1Bars": EMPTY5,
                "positions": [{"ticket": 808, "type": "BUY", "lots": 0.01, "openPrice": 3340}],
                "states": [{"ticket": 808, "beMoved": True, "tp1Hit": False}],
            }
        )

        assert result["advisories"] == [{"action": "CLOSE", "ticket": 808, "lots": 0.01, "reason": "TP1_2.5ATR"}]
        assert_match(result["nextStates"][0], {"ticket": 808, "beMoved": True, "tp1Hit": True})
        assert result["canProduceLiveCommands"] is False

    def test_mirrors_go_same_side_tp1_group_coordination(self) -> None:
        result = evaluate_position_tp1(
            {
                "currentPrice": 3343,
                "currentAtr": 2,
                "h1Bars": EMPTY5,
                "positions": [
                    {"ticket": 809, "type": "BUY", "lots": 0.5, "openPrice": 3340},
                    {"ticket": 810, "type": "BUY", "lots": 0.3, "openPrice": 3342.4},
                ],
                "states": [
                    {"ticket": 809, "beMoved": True, "tp1Hit": False},
                    {"ticket": 810, "beMoved": True, "tp1Hit": False},
                ],
            }
        )

        assert result["advisories"] == [
            {"action": "CLOSE", "ticket": 809, "lots": 0.2, "reason": "TP1_1.5ATR"},
            {"action": "CLOSE", "ticket": 810, "lots": 0.12, "reason": "group_tp1_BUY"},
        ]
        assert_match(result["nextStates"][0], {"ticket": 809, "beMoved": True, "tp1Hit": True})
        assert_match(result["nextStates"][1], {"ticket": 810, "beMoved": True, "tp1Hit": True})
        assert result["canProduceLiveCommands"] is False


# ---------------------------------------------------------------------------
# position manager TP2 advisory parity slice
# ---------------------------------------------------------------------------


class TestTP2Parity:
    def test_mirrors_go_tp2_close_advisory_and_state_update(self) -> None:
        result = evaluate_position_tp2(
            {
                "currentPrice": 3346.4,
                "currentAtr": 2,
                "h1Bars": EMPTY5,
                "positions": [{"ticket": 903, "type": "BUY", "lots": 0.5, "openPrice": 3340}],
                "states": [{"ticket": 903, "tp1Hit": True, "tp2Hit": False}],
            }
        )

        assert result["advisories"] == [{"action": "CLOSE", "ticket": 903, "lots": 0.2, "reason": "TP2_3.2ATR"}]
        assert_match(result["nextStates"][0], {"ticket": 903, "tp1Hit": True, "tp2Hit": True})
        assert result["canProduceLiveCommands"] is False

    def test_does_not_tp2_before_tp1_has_hit(self) -> None:
        result = evaluate_position_tp2(
            {
                "currentPrice": 3348,
                "currentAtr": 2,
                "h1Bars": EMPTY5,
                "positions": [{"ticket": 904, "type": "BUY", "lots": 0.5, "openPrice": 3340}],
                "states": [{"ticket": 904, "tp1Hit": False, "tp2Hit": False}],
            }
        )

        assert result["advisories"] == []
        assert_match(result["nextStates"][0], {"ticket": 904, "tp1Hit": False, "tp2Hit": False})
        assert result["canProduceLiveCommands"] is False

    def test_does_not_tp2_twice_for_already_hit_state(self) -> None:
        result = evaluate_position_tp2(
            {
                "currentPrice": 3348,
                "currentAtr": 2,
                "h1Bars": EMPTY5,
                "positions": [{"ticket": 905, "type": "BUY", "lots": 0.5, "openPrice": 3340}],
                "states": [{"ticket": 905, "tp1Hit": True, "tp2Hit": True}],
            }
        )

        assert result["advisories"] == []
        assert_match(result["nextStates"][0], {"ticket": 905, "tp1Hit": True, "tp2Hit": True})
        assert result["canProduceLiveCommands"] is False

    def test_mirrors_go_early_buy_tp2_weakness_trigger(self) -> None:
        result = evaluate_position_tp2(
            {
                "currentPrice": 3344.4,
                "currentAtr": 2,
                "h1Bars": [
                    {},
                    {},
                    {},
                    {"macdHist": 0.9, "rsi": 64, "adx": 34},
                    {"macdHist": 0.4, "rsi": 58, "adx": 30},
                ],
                "positions": [{"ticket": 906, "type": "BUY", "lots": 0.3, "openPrice": 3340}],
                "states": [{"ticket": 906, "tp1Hit": True, "tp2Hit": False}],
            }
        )

        assert result["advisories"] == [{"action": "CLOSE", "ticket": 906, "lots": 0.12, "reason": "TP2_2.2ATR"}]
        assert_match(result["nextStates"][0], {"ticket": 906, "tp1Hit": True, "tp2Hit": True})
        assert result["canProduceLiveCommands"] is False

    def test_accepts_go_json_macd_hist_bars_for_early_buy_tp2_weakness(self) -> None:
        result = evaluate_position_tp2(
            {
                "currentPrice": 3344.4,
                "currentAtr": 2,
                "h1Bars": [
                    {},
                    {},
                    {},
                    {"macd_hist": 0.9, "rsi": 64, "adx": 34},
                    {"macd_hist": 0.4, "rsi": 62, "adx": 30},
                ],
                "positions": [{"ticket": 909, "type": "BUY", "lots": 0.3, "openPrice": 3340}],
                "states": [{"ticket": 909, "tp1Hit": True, "tp2Hit": False}],
            }
        )

        assert result["advisories"] == [{"action": "CLOSE", "ticket": 909, "lots": 0.12, "reason": "TP2_2.2ATR"}]
        assert_match(result["nextStates"][0], {"ticket": 909, "tp1Hit": True, "tp2Hit": True})
        assert result["canProduceLiveCommands"] is False

    def test_mirrors_go_early_sell_tp2_weakness_trigger(self) -> None:
        result = evaluate_position_tp2(
            {
                "currentPrice": 3335.6,
                "currentAtr": 2,
                "h1Bars": [
                    {},
                    {},
                    {},
                    {"macdHist": -0.9, "rsi": 36, "adx": 34},
                    {"macdHist": -0.4, "rsi": 42, "adx": 30},
                ],
                "positions": [{"ticket": 907, "type": "SELL", "lots": 0.3, "openPrice": 3340}],
                "states": [{"ticket": 907, "tp1Hit": True, "tp2Hit": False}],
            }
        )

        assert result["advisories"] == [{"action": "CLOSE", "ticket": 907, "lots": 0.12, "reason": "TP2_2.2ATR"}]
        assert_match(result["nextStates"][0], {"ticket": 907, "tp1Hit": True, "tp2Hit": True})
        assert result["canProduceLiveCommands"] is False

    def test_mirrors_go_tp2_tiny_lot_full_close_fallback(self) -> None:
        result = evaluate_position_tp2(
            {
                "currentPrice": 3348,
                "currentAtr": 2,
                "h1Bars": EMPTY5,
                "positions": [{"ticket": 908, "type": "BUY", "lots": 0.01, "openPrice": 3340}],
                "states": [{"ticket": 908, "tp1Hit": True, "tp2Hit": False}],
            }
        )

        assert result["advisories"] == [{"action": "CLOSE", "ticket": 908, "lots": 0.01, "reason": "TP2_4.0ATR"}]
        assert_match(result["nextStates"][0], {"ticket": 908, "tp1Hit": True, "tp2Hit": True})
        assert result["canProduceLiveCommands"] is False

    def test_mirrors_go_same_side_tp2_group_coordination(self) -> None:
        result = evaluate_position_tp2(
            {
                "currentPrice": 3346,
                "currentAtr": 2,
                "h1Bars": EMPTY5,
                "positions": [
                    {"ticket": 909, "type": "BUY", "lots": 0.5, "openPrice": 3340},
                    {"ticket": 910, "type": "BUY", "lots": 0.3, "openPrice": 3342.4},
                ],
                "states": [
                    {"ticket": 909, "tp1Hit": True, "tp2Hit": False},
                    {"ticket": 910, "tp1Hit": True, "tp2Hit": False},
                ],
            }
        )

        assert result["advisories"] == [
            {"action": "CLOSE", "ticket": 909, "lots": 0.2, "reason": "TP2_3.0ATR"},
            {"action": "CLOSE", "ticket": 910, "lots": 0.12, "reason": "group_tp2_BUY"},
        ]
        assert_match(result["nextStates"][0], {"ticket": 909, "tp1Hit": True, "tp2Hit": True})
        assert_match(result["nextStates"][1], {"ticket": 910, "tp1Hit": True, "tp2Hit": True})
        assert result["canProduceLiveCommands"] is False


# ---------------------------------------------------------------------------
# position manager key-level advisory parity slice
# ---------------------------------------------------------------------------


class TestKeyLevelParity:
    def test_mirrors_go_first_key_level_partial_close_and_tp1_state_update(self) -> None:
        result = evaluate_position_key_levels(
            {
                "currentPrice": 3349.8,
                "currentAtr": 2,
                "h1Bars": EMPTY5,
                "positions": [{"ticket": 1003, "type": "BUY", "lots": 0.5, "openPrice": 3347}],
                "states": [{"ticket": 1003, "tp1Hit": False, "tp2Hit": False}],
            }
        )

        assert result["advisories"] == [{"action": "CLOSE", "ticket": 1003, "lots": 0.2, "reason": "key_level_3350"}]
        assert_match(result["nextStates"][0], {"ticket": 1003, "tp1Hit": True, "tp2Hit": False})
        assert result["canProduceLiveCommands"] is False

    def test_mirrors_go_second_key_level_partial_close_and_tp2_state_update(self) -> None:
        result = evaluate_position_key_levels(
            {
                "currentPrice": 3349.8,
                "currentAtr": 2,
                "h1Bars": EMPTY5,
                "positions": [{"ticket": 1004, "type": "BUY", "lots": 0.5, "openPrice": 3345.6}],
                "states": [{"ticket": 1004, "tp1Hit": True, "tp2Hit": False}],
            }
        )

        assert result["advisories"] == [{"action": "CLOSE", "ticket": 1004, "lots": 0.2, "reason": "key_level2_3350"}]
        assert_match(result["nextStates"][0], {"ticket": 1004, "tp1Hit": True, "tp2Hit": True})
        assert result["canProduceLiveCommands"] is False

    def test_does_not_emit_key_level_advisory_when_price_not_near_level(self) -> None:
        result = evaluate_position_key_levels(
            {
                "currentPrice": 3349.5,
                "currentAtr": 2,
                "h1Bars": EMPTY5,
                "positions": [{"ticket": 1005, "type": "BUY", "lots": 0.5, "openPrice": 3347}],
                "states": [{"ticket": 1005, "tp1Hit": False, "tp2Hit": False}],
            }
        )

        assert result["advisories"] == []
        assert_match(result["nextStates"][0], {"ticket": 1005, "tp1Hit": False, "tp2Hit": False})
        assert result["canProduceLiveCommands"] is False

    def test_mirrors_go_sell_key_level_selection_below_price(self) -> None:
        result = evaluate_position_key_levels(
            {
                "currentPrice": 3300.2,
                "currentAtr": 2,
                "h1Bars": EMPTY5,
                "positions": [{"ticket": 1006, "type": "SELL", "lots": 0.3, "openPrice": 3302.4}],
                "states": [{"ticket": 1006, "tp1Hit": False, "tp2Hit": False}],
            }
        )

        assert result["advisories"] == [{"action": "CLOSE", "ticket": 1006, "lots": 0.12, "reason": "key_level_3300"}]
        assert_match(result["nextStates"][0], {"ticket": 1006, "tp1Hit": True, "tp2Hit": False})
        assert result["canProduceLiveCommands"] is False


# ---------------------------------------------------------------------------
# position manager trend-reversal advisory parity slice
# ---------------------------------------------------------------------------


class TestTrendReversalParity:
    def test_mirrors_go_buy_trend_reversal_full_close(self) -> None:
        result = evaluate_position_trend_reversal(
            {
                "currentPrice": 3338.8,
                "currentAtr": 2,
                "h1Bars": [
                    {},
                    {},
                    {"macdHist": -0.2, "rsi": 44, "adx": 24, "ema20": 3339.6, "ema50": 3340.2},
                    {"macdHist": -0.62, "rsi": 38, "adx": 18, "ema20": 3339.4, "ema50": 3340.4},
                ],
                "positions": [{"ticket": 1103, "type": "BUY", "lots": 0.5, "openPrice": 3338}],
                "states": [{"ticket": 1103, "beMoved": True}],
            }
        )

        assert result == {
            "advisories": [
                {
                    "action": "CLOSE",
                    "ticket": 1103,
                    "lots": 0.5,
                    "reason": "reversal_s8_MACD=-0.62<-0.5且价格<EMA20 RSI=38<40 ADX=18<20 EMA死叉确认(2根)",
                }
            ],
            "canProduceLiveCommands": False,
        }

    def test_mirrors_go_sell_trend_reversal_full_close(self) -> None:
        result = evaluate_position_trend_reversal(
            {
                "currentPrice": 3341.2,
                "currentAtr": 2,
                "h1Bars": [
                    {},
                    {},
                    {"macdHist": 0.2, "rsi": 56, "adx": 23, "ema20": 3340.6, "ema50": 3339.8},
                    {"macdHist": 0.67, "rsi": 63, "adx": 17, "ema20": 3340.8, "ema50": 3339.7},
                ],
                "positions": [{"ticket": 1104, "type": "SELL", "lots": 0.3, "openPrice": 3342}],
                "states": [{"ticket": 1104, "beMoved": True}],
            }
        )

        assert result["advisories"] == [
            {
                "action": "CLOSE",
                "ticket": 1104,
                "lots": 0.3,
                "reason": "reversal_s8_MACD=0.67>0.5且价格>EMA20 RSI=63>60 ADX=17<20 EMA金叉确认(2根)",
            }
        ]
        assert result["canProduceLiveCommands"] is False

    def test_does_not_emit_trend_reversal_before_breakeven_has_moved(self) -> None:
        result = evaluate_position_trend_reversal(
            {
                "currentPrice": 3338.8,
                "currentAtr": 2,
                "h1Bars": [
                    {},
                    {},
                    {"macdHist": 0.2, "rsi": 44, "adx": 24, "ema20": 3339.6, "ema50": 3340.2},
                    {"macdHist": -0.62, "rsi": 38, "adx": 18, "ema20": 3339.4, "ema50": 3340.4},
                ],
                "positions": [{"ticket": 1105, "type": "BUY", "lots": 0.5, "openPrice": 3338}],
                "states": [{"ticket": 1105, "beMoved": False}],
            }
        )

        assert result == {"advisories": [], "canProduceLiveCommands": False}

    def test_does_not_emit_trend_reversal_below_go_score_threshold(self) -> None:
        result = evaluate_position_trend_reversal(
            {
                "currentPrice": 3338.8,
                "currentAtr": 2,
                "h1Bars": [
                    {},
                    {},
                    {"macdHist": -0.2, "rsi": 44, "adx": 24, "ema20": 3340.4, "ema50": 3339.6},
                    {"macdHist": -0.62, "rsi": 45, "adx": 24, "ema20": 3339.4, "ema50": 3339},
                ],
                "positions": [{"ticket": 1106, "type": "BUY", "lots": 0.5, "openPrice": 3338}],
                "states": [{"ticket": 1106, "beMoved": True}],
            }
        )

        assert result == {"advisories": [], "canProduceLiveCommands": False}


# ---------------------------------------------------------------------------
# position manager dynamic-trailing advisory parity slice
# ---------------------------------------------------------------------------


class TestDynamicTrailingParity:
    def test_mirrors_go_tp1_dynamic_trailing_full_close(self) -> None:
        result = evaluate_position_dynamic_trailing(
            {
                "currentPrice": 3343,
                "currentAtr": 2,
                "positions": [{"ticket": 1203, "type": "BUY", "lots": 0.5, "openPrice": 3340}],
                "states": [{"ticket": 1203, "tp1Hit": True, "tp2Hit": False, "maxProfitAtr": 4}],
            }
        )

        assert result["advisories"] == [{"action": "CLOSE", "ticket": 1203, "lots": 0.5, "reason": "trail_tp1_dd2.5"}]
        assert_match(result["nextStates"][0], {"ticket": 1203, "tp1Hit": True, "tp2Hit": False, "maxProfitAtr": 4})
        assert result["canProduceLiveCommands"] is False

    def test_mirrors_go_tp2_dynamic_trailing_full_close_with_snake_case_state(self) -> None:
        result = evaluate_position_dynamic_trailing(
            {
                "currentPrice": 3336.6,
                "currentAtr": 2,
                "positions": [{"ticket": 1204, "type": "SELL", "lots": 0.3, "openPrice": 3340}],
                "states": [{"ticket": 1204, "tp1_hit": True, "tp2_hit": True, "max_profit_atr": 4}],
            }
        )

        assert result["advisories"] == [{"action": "CLOSE", "ticket": 1204, "lots": 0.3, "reason": "trail_tp2_dd2.3"}]
        assert_match(result["nextStates"][0], {"ticket": 1204, "tp1Hit": True, "tp2Hit": True, "maxProfitAtr": 4})
        assert result["canProduceLiveCommands"] is False

    def test_does_not_emit_dynamic_trailing_before_tp1_has_hit(self) -> None:
        result = evaluate_position_dynamic_trailing(
            {
                "currentPrice": 3343,
                "currentAtr": 2,
                "positions": [{"ticket": 1205, "type": "BUY", "lots": 0.5, "openPrice": 3340}],
                "states": [{"ticket": 1205, "tp1Hit": False, "tp2Hit": False, "maxProfitAtr": 4}],
            }
        )

        assert result["advisories"] == []
        assert_match(result["nextStates"][0], {"ticket": 1205, "tp1Hit": False, "tp2Hit": False, "maxProfitAtr": 4})
        assert result["canProduceLiveCommands"] is False

    def test_refreshes_max_profit_atr_before_evaluating_drawdown(self) -> None:
        result = evaluate_position_dynamic_trailing(
            {
                "currentPrice": 3346,
                "currentAtr": 2,
                "positions": [{"ticket": 1206, "type": "BUY", "lots": 0.5, "openPrice": 3340}],
                "states": [{"ticket": 1206, "tp1Hit": True, "tp2Hit": False, "maxProfitAtr": 2}],
            }
        )

        assert result["advisories"] == []
        assert_match(result["nextStates"][0], {"ticket": 1206, "tp1Hit": True, "tp2Hit": False, "maxProfitAtr": 3})
        assert result["canProduceLiveCommands"] is False

    def test_sets_trailing_closed_after_first_trail_tp_close_and_stays_idempotent(self) -> None:
        first = evaluate_position_dynamic_trailing(
            {
                "currentPrice": 3343,
                "currentAtr": 2,
                "positions": [{"ticket": 1207, "type": "BUY", "lots": 0.5, "openPrice": 3340}],
                "states": [{"ticket": 1207, "tp1Hit": True, "tp2Hit": False, "maxProfitAtr": 4}],
            }
        )

        assert first["advisories"] == [{"action": "CLOSE", "ticket": 1207, "lots": 0.5, "reason": "trail_tp1_dd2.5"}]
        assert_match(first["nextStates"][0], {"ticket": 1207, "trailingClosed": True, "trailing_closed": True})

        second = evaluate_position_dynamic_trailing(
            {
                "currentPrice": 3342.8,
                "currentAtr": 2,
                "positions": [{"ticket": 1207, "type": "BUY", "lots": 0.5, "openPrice": 3340}],
                "states": first["nextStates"],
            }
        )

        assert second["advisories"] == []
        assert_match(second["nextStates"][0], {"ticket": 1207, "trailingClosed": True, "trailing_closed": True})


# ---------------------------------------------------------------------------
# position manager momentum-scalp exit advisory parity slice(策略已禁用)
# ---------------------------------------------------------------------------


class TestMomentumScalpParity:
    EMPTY_RESULT = {"advisories": [], "nextStates": [], "canProduceLiveCommands": False}

    def test_mirrors_go_momentum_scalp_time_stop_before_indicator_exits(self) -> None:
        result = evaluate_position_momentum_scalp_exits(
            {
                "now": NOW,
                "currentPrice": 100.15,
                "currentAtr": 1,
                "m5Bars": BULLISH_M5_BARS,
                "m1Bars": [{"rsi": 82}],
                "positions": [
                    {
                        "ticket": 1303, "type": "BUY", "lots": 0.5, "openPrice": 100,
                        "comment": "bot momentum_scalp entry",
                    }
                ],
                "states": [{"ticket": 1303, "openTime": "2026-04-13T07:39:00.000Z"}],
            }
        )

        assert result == self.EMPTY_RESULT

    def test_mirrors_go_one_time_rsi_75_partial_close_and_state_update(self) -> None:
        result = evaluate_position_momentum_scalp_exits(
            {
                "now": NOW,
                "currentPrice": 101,
                "currentAtr": 1,
                "m5Bars": BULLISH_M5_BARS,
                "m1Bars": [{"rsi": 76}],
                "positions": [
                    {"ticket": 1304, "type": "BUY", "lots": 0.5, "openPrice": 100, "comment": "momentum_scalp"}
                ],
                "states": [{"ticket": 1304, "openTime": "2026-04-13T07:55:00.000Z", "rsiTp75Triggered": False}],
            }
        )

        assert result == self.EMPTY_RESULT

    def test_does_not_repeat_rsi_75_partial_close_once_state_triggered(self) -> None:
        result = evaluate_position_momentum_scalp_exits(
            {
                "now": NOW,
                "currentPrice": 101,
                "currentAtr": 1,
                "m5Bars": BULLISH_M5_BARS,
                "m1Bars": [{"rsi": 76}],
                "positions": [
                    {"ticket": 1305, "type": "BUY", "lots": 0.5, "openPrice": 100, "comment": "momentum_scalp"}
                ],
                "states": [{"ticket": 1305, "openTime": "2026-04-13T07:55:00.000Z", "rsiTp75Triggered": True}],
            }
        )

        assert result == self.EMPTY_RESULT

    def test_mirrors_go_rsi_extreme_full_close(self) -> None:
        result = evaluate_position_momentum_scalp_exits(
            {
                "now": NOW,
                "currentPrice": 101.2,
                "currentAtr": 1,
                "m5Bars": BULLISH_M5_BARS,
                "m1Bars": [{"rsi": 82}],
                "positions": [
                    {"ticket": 1306, "type": "BUY", "lots": 0.5, "openPrice": 100, "comment": "momentum_scalp"}
                ],
                "states": [{"ticket": 1306, "openTime": "2026-04-13T07:55:00.000Z", "rsiTp75Triggered": True}],
            }
        )

        assert result == self.EMPTY_RESULT

    def test_mirrors_go_m5_ema_structure_break_full_close(self) -> None:
        result = evaluate_position_momentum_scalp_exits(
            {
                "now": NOW,
                "currentPrice": 100.9,
                "currentAtr": 1,
                "m5Bars": [
                    {"close": 100.8},
                    {"close": 100.7},
                    {"close": 100.6},
                    {"close": 100.5},
                    {"close": 100.4},
                    {"close": 100.3},
                    {"close": 100.2},
                    {"close": 100.1},
                ],
                "m1Bars": [{"rsi": 60}],
                "positions": [
                    {"ticket": 1307, "type": "BUY", "lots": 0.5, "openPrice": 100, "comment": "momentum_scalp"}
                ],
                "states": [{"ticket": 1307, "openTime": "2026-04-13T07:55:00.000Z"}],
            }
        )

        assert result == self.EMPTY_RESULT

    def test_does_not_run_momentum_scalp_exits_for_non_scalp_comments(self) -> None:
        result = evaluate_position_momentum_scalp_exits(
            {
                "now": NOW,
                "currentPrice": 100.15,
                "currentAtr": 1,
                "m5Bars": BULLISH_M5_BARS,
                "m1Bars": [{"rsi": 82}],
                "positions": [
                    {
                        "ticket": 1308, "type": "BUY", "lots": 0.5, "openPrice": 100, "comment": "GB_pullback_S7",
                        "strategy": "momentum_scalp",
                    }
                ],
                "states": [{"ticket": 1308, "openTime": "2026-04-13T07:39:00.000Z"}],
            }
        )

        assert result == self.EMPTY_RESULT


# ---------------------------------------------------------------------------
# position manager Analyze orchestration parity slice
# ---------------------------------------------------------------------------


class TestAnalyzeOrchestrationParity:
    def test_lets_breakeven_and_tp1_fire_in_same_per_position_pass(self) -> None:
        result = evaluate_position_manager_commands(
            {
                "now": NOW,
                "currentPrice": 3343.2,
                "currentAtr": 2,
                "avgAtr": 2,
                "h1Bars": H1_BARS,
                "positions": [{"ticket": 202, "type": "BUY", "openPrice": 3340, "lots": 0.5}],
                "states": [{"ticket": 202, "openTime": "2026-04-13T06:00:00.000Z", "beTriggerAtr": 1.5}],
            }
        )

        assert result["advisories"] == [
            {"action": "MODIFY", "ticket": 202, "newSL": 3340, "reason": "breakeven_1.6ATR"},
            {"action": "CLOSE", "ticket": 202, "lots": 0.2, "reason": "TP1_1.6ATR"},
        ]
        assert len(result["nextStates"]) == 1
        assert_match(result["nextStates"][0], {"ticket": 202, "beMoved": True, "tp1Hit": True, "bestSl": 3340})
        assert_close(result["nextStates"][0]["maxProfitAtr"], 1.6)
        assert result["canProduceLiveCommands"] is False

    def test_upgrades_buy_stop_to_lock_l1_at_20_atr_profit(self) -> None:
        result = evaluate_position_manager_commands(
            {
                "now": NOW,
                "currentPrice": 3344,
                "currentAtr": 2,
                "avgAtr": 2,
                "h1Bars": H1_BARS,
                "positions": [{"ticket": 12001, "type": "BUY", "openPrice": 3340, "lots": 0.5, "sl": 3338}],
                "states": [{"ticket": 12001, "openTime": "2026-04-13T06:00:00.000Z", "beTriggerAtr": 1.5}],
            }
        )

        assert_contains(
            result["advisories"], {"action": "MODIFY", "ticket": 12001, "newSL": 3340.6, "reason": "lock_l1_2.0ATR"}
        )
        assert len(result["nextStates"]) == 1
        assert_match(result["nextStates"][0], {"ticket": 12001, "beMoved": True, "bestSl": 3340.6})

    def test_upgrades_buy_stop_to_lock_l2_at_25_atr_profit(self) -> None:
        result = evaluate_position_manager_commands(
            {
                "now": NOW,
                "currentPrice": 3345,
                "currentAtr": 2,
                "avgAtr": 2,
                "h1Bars": H1_BARS,
                "positions": [{"ticket": 12002, "type": "BUY", "openPrice": 3340, "lots": 0.5, "sl": 3338}],
                "states": [{"ticket": 12002, "openTime": "2026-04-13T06:00:00.000Z", "beTriggerAtr": 1.5}],
            }
        )

        assert_contains(
            result["advisories"], {"action": "MODIFY", "ticket": 12002, "newSL": 3341.2, "reason": "lock_l2_2.5ATR"}
        )
        assert len(result["nextStates"]) == 1
        assert_match(result["nextStates"][0], {"ticket": 12002, "beMoved": True, "bestSl": 3341.2})

    def test_mirrors_lock_l1_stop_upgrades_for_sell_positions(self) -> None:
        result = evaluate_position_manager_commands(
            {
                "now": NOW,
                "currentPrice": 3336,
                "currentAtr": 2,
                "avgAtr": 2,
                "h1Bars": H1_BARS,
                "positions": [{"ticket": 12003, "type": "SELL", "openPrice": 3340, "lots": 0.5, "sl": 3342}],
                "states": [{"ticket": 12003, "openTime": "2026-04-13T06:00:00.000Z", "beTriggerAtr": 1.5}],
            }
        )

        assert_contains(
            result["advisories"], {"action": "MODIFY", "ticket": 12003, "newSL": 3339.4, "reason": "lock_l1_2.0ATR"}
        )
        assert len(result["nextStates"]) == 1
        assert_match(result["nextStates"][0], {"ticket": 12003, "beMoved": True, "bestSl": 3339.4})

    def test_does_not_emit_lock_advisory_when_current_stop_already_better(self) -> None:
        result = evaluate_position_manager_commands(
            {
                "now": NOW,
                "currentPrice": 3345,
                "currentAtr": 2,
                "avgAtr": 2,
                "h1Bars": H1_BARS,
                "positions": [{"ticket": 12004, "type": "BUY", "openPrice": 3340, "lots": 0.5, "sl": 3341.2}],
                "states": [
                    {"ticket": 12004, "openTime": "2026-04-13T06:00:00.000Z", "beTriggerAtr": 1.5, "bestSl": 3341.2}
                ],
            }
        )

        assert [a for a in result["advisories"] if a["action"] == "MODIFY"] == []
        assert len(result["nextStates"]) == 1
        assert_match(result["nextStates"][0], {"ticket": 12004, "bestSl": 3341.2})

    def test_emits_direct_breakeven_against_own_sl_when_same_side_bestsl_polluted(self) -> None:
        result = evaluate_position_manager_commands(
            {
                "now": NOW,
                "currentPrice": 3343.2,
                "currentAtr": 2,
                "avgAtr": 2,
                "h1Bars": H1_BARS,
                "positions": [
                    {"ticket": 606, "type": "BUY", "openPrice": 3340, "lots": 0.5, "sl": 3338},
                    {"ticket": 607, "type": "BUY", "openPrice": 3342, "lots": 0.3, "sl": 3342},
                ],
                "states": [
                    {
                        "ticket": 606, "openTime": "2026-04-13T06:00:00.000Z", "beTriggerAtr": 1.5,
                        "beMoved": False, "bestSl": 3342,
                    },
                    {
                        "ticket": 607, "openTime": "2026-04-13T06:00:00.000Z", "beTriggerAtr": 1.5,
                        "beMoved": True, "bestSl": 3342,
                    },
                ],
            }
        )

        assert_contains(
            result["advisories"], {"action": "MODIFY", "ticket": 606, "newSL": 3340, "reason": "breakeven_1.6ATR"}
        )
        assert_contains_match(result["nextStates"], {"ticket": 606, "beMoved": True})

    def test_re_emits_breakeven_when_be_moved_stale_and_ea_sl_still_below_open(self) -> None:
        result = evaluate_position_manager_commands(
            {
                "now": NOW,
                "currentPrice": 3343.2,
                "currentAtr": 2,
                "avgAtr": 2,
                "h1Bars": H1_BARS,
                "positions": [{"ticket": 608, "type": "BUY", "openPrice": 3340, "lots": 0.5, "sl": 3338}],
                "states": [
                    {
                        "ticket": 608, "openTime": "2026-04-13T06:00:00.000Z", "beTriggerAtr": 1.5,
                        "be_moved": True, "bestSl": 3340,
                    }
                ],
            }
        )

        assert_contains(
            result["advisories"], {"action": "MODIFY", "ticket": 608, "newSL": 3340, "reason": "breakeven_1.6ATR"}
        )
        assert len(result["nextStates"]) == 1
        assert_match(result["nextStates"][0], {"ticket": 608, "beMoved": True, "be_moved": True, "bestSl": 3340})

    def test_does_not_run_disabled_momentum_scalp_exits_during_orchestration(self) -> None:
        result = evaluate_position_manager_commands(
            {
                "now": NOW,
                "currentPrice": 100.15,
                "currentAtr": 1,
                "avgAtr": 1,
                "h1Bars": H1_BARS,
                "m5Bars": BULLISH_M5_BARS,
                "m1Bars": [{"rsi": 82}],
                "positions": [
                    {"ticket": 404, "type": "BUY", "openPrice": 100, "lots": 0.5, "comment": "bot momentum_scalp entry"}
                ],
                "states": [{"ticket": 404, "openTime": "2026-04-13T07:39:00.000Z", "beTriggerAtr": 1.5}],
            }
        )

        assert result["advisories"] == []
        assert len(result["nextStates"]) == 1
        assert_match(result["nextStates"][0], {"ticket": 404, "maxProfitAtr": 0.15000000000000568})

    def test_runs_same_side_tp1_coordination_after_per_position_pass(self) -> None:
        result = evaluate_position_manager_commands(
            {
                "now": NOW,
                "currentPrice": 3343,
                "currentAtr": 2,
                "avgAtr": 2,
                "h1Bars": H1_BARS,
                "positions": [
                    {"ticket": 901, "type": "BUY", "openPrice": 3330, "lots": 0.5, "sl": 3330},
                    {"ticket": 902, "type": "BUY", "openPrice": 3342, "lots": 0.3, "sl": 3342},
                ],
                "states": [
                    {
                        "ticket": 901, "openTime": "2026-04-13T05:00:00.000Z", "beTriggerAtr": 1.5,
                        "beMoved": True, "bestSl": 3330,
                    },
                    {
                        "ticket": 902, "openTime": "2026-04-13T06:00:00.000Z", "beTriggerAtr": 1.5,
                        "beMoved": True, "bestSl": 3342,
                    },
                ],
            }
        )

        assert result["advisories"] == [
            {"action": "MODIFY", "ticket": 901, "newSL": 3331.2, "reason": "lock_l2_6.5ATR"},
            {"action": "CLOSE", "ticket": 901, "lots": 0.2, "reason": "TP1_6.5ATR"},
            {"action": "CLOSE", "ticket": 902, "lots": 0.12, "reason": "group_tp1_BUY"},
        ]
        assert len(result["nextStates"]) == 2
        assert_match(result["nextStates"][0], {"ticket": 901, "tp1Hit": True})
        assert_match(result["nextStates"][1], {"ticket": 902, "tp1Hit": True})

    def test_runs_same_side_breakeven_coordination_after_direct_breakeven_moves(self) -> None:
        result = evaluate_position_manager_commands(
            {
                "now": NOW,
                "currentPrice": 3343.2,
                "currentAtr": 2,
                "avgAtr": 2,
                "h1Bars": H1_BARS,
                "positions": [
                    {"ticket": 1001, "type": "BUY", "openPrice": 3330, "lots": 0.5},
                    {"ticket": 1002, "type": "BUY", "openPrice": 3340, "lots": 0.3},
                ],
                "states": [
                    {"ticket": 1001, "openTime": "2026-04-13T06:00:00.000Z", "beTriggerAtr": 1.5, "bestSl": 0},
                    {"ticket": 1002, "openTime": "2026-04-13T06:00:00.000Z", "beTriggerAtr": 1.5, "bestSl": 0},
                ],
            }
        )

        assert result["advisories"] == [
            {"action": "MODIFY", "ticket": 1001, "newSL": 3331.2, "reason": "lock_l2_6.6ATR"},
            {"action": "CLOSE", "ticket": 1001, "lots": 0.2, "reason": "TP1_6.6ATR"},
            {"action": "MODIFY", "ticket": 1002, "newSL": 3340, "reason": "breakeven_1.6ATR"},
            {"action": "CLOSE", "ticket": 1002, "lots": 0.12, "reason": "TP1_1.6ATR"},
            {"action": "MODIFY", "ticket": 1001, "newSL": 3340, "reason": "group_be_BUY"},
        ]
        assert len(result["nextStates"]) == 2
        assert_match(result["nextStates"][0], {"ticket": 1001, "beMoved": True, "tp1Hit": True, "bestSl": 3340})
        assert_match(result["nextStates"][1], {"ticket": 1002, "beMoved": True, "tp1Hit": True, "bestSl": 3340})

    def test_tightens_stop_loss_when_favorable_add_on_position_detected(self) -> None:
        result = evaluate_position_manager_commands(
            {
                "now": NOW,
                "currentPrice": 3345,
                "currentAtr": 2,
                "avgAtr": 2,
                "h1Bars": H1_BARS,
                "positions": [
                    {"ticket": 2001, "type": "BUY", "openPrice": 3330, "lots": 0.5, "sl": 3328},
                    {"ticket": 2002, "type": "BUY", "openPrice": 3340, "lots": 0.3, "sl": 3338},
                ],
                "states": [
                    {
                        "ticket": 2001, "openTime": "2026-04-13T06:00:00.000Z", "beTriggerAtr": 1.5,
                        "beMoved": False, "bestSl": 3328,
                    }
                ],
            }
        )

        assert_contains(
            result["advisories"],
            {"action": "MODIFY", "ticket": 2001, "newSL": 3340, "reason": "group_favorable_addon_BUY"},
        )
        assert len(result["nextStates"]) == 2
        assert_match(result["nextStates"][0], {"ticket": 2001, "bestSl": 3340})
        assert_match(result["nextStates"][1], {"ticket": 2002, "bestSl": 3341.2})

    def test_tightens_stop_loss_to_group_avg_entry_when_adverse_add_on_detected(self) -> None:
        result = evaluate_position_manager_commands(
            {
                "now": NOW,
                # 现价略高于 groupAvgEntry,BUY reanchor 合法;ATR 放大避免触发 BE/锁盈
                "currentPrice": 3329,
                "currentAtr": 10,
                "avgAtr": 10,
                "h1Bars": H1_BARS,
                "positions": [
                    {"ticket": 3001, "type": "BUY", "openPrice": 3330, "lots": 0.1, "sl": 3328, "profit": -10},
                    {"ticket": 3002, "type": "BUY", "openPrice": 3325, "lots": 0.06, "sl": 3323, "profit": -5},
                ],
                "states": [
                    {
                        "ticket": 3001, "openTime": "2026-04-13T06:00:00.000Z", "beTriggerAtr": 1.5,
                        "beMoved": False, "bestSl": 3328,
                    }
                ],
            }
        )

        group_avg_entry = (3330 * 0.1 + 3325 * 0.06) / 0.16
        assert_close_contained(
            result["advisories"],
            {"action": "MODIFY", "ticket": 3001, "newSL": group_avg_entry, "reason": "group_adverse_reanchor_BUY"},
            0.01,
        )
        assert_close_contained(
            result["advisories"],
            {"action": "MODIFY", "ticket": 3002, "newSL": group_avg_entry, "reason": "group_adverse_reanchor_BUY"},
            0.01,
        )
        assert_close(result["nextStates"][0]["groupAvgEntry"], group_avg_entry, 2)
        assert result["nextStates"][0]["addOnCount"] == 0
        assert result["nextStates"][1]["addOnCount"] == 1

    def test_does_not_emit_group_adverse_reanchor_when_group_avg_entry_wrong_side(self) -> None:
        result = evaluate_position_manager_commands(
            {
                "now": "2026-07-17T06:13:55.000Z",
                "currentPrice": 218.30,
                "currentAtr": 0.35,
                "avgAtr": 0.35,
                "equity": 10000,
                "h1Bars": [
                    {
                        "time": f"2026-07-17T0{i % 10}:00:00.000Z",
                        "open": 218.5, "high": 218.8, "low": 218.2, "close": 218.4, "volume": 1000,
                    }
                    for i in range(30)
                ],
                "positions": [
                    {
                        "ticket": 42360423, "type": "BUY", "open_price": 218.781, "lots": 0.02,
                        "sl": 218.39, "tp": 219.39, "profit": -5,
                    },
                    {
                        "ticket": 42370061, "type": "BUY", "open_price": 218.707, "lots": 0.02,
                        "sl": 217.7, "tp": 219.18, "profit": -5,
                    },
                ],
                "states": [
                    {"ticket": 42360423, "openTime": "2026-07-17T05:00:00.000Z", "bestSl": 218.39},
                    # previousTickets only has old ticket so second is treated as adverse add-on
                ],
            }
        )
        reanchors = [a for a in result["advisories"] if str(a.get("reason", "")).startswith("group_adverse_reanchor")]
        assert reanchors == []

    def test_does_not_trigger_adverse_group_exit_when_net_loss_below_6pct(self) -> None:
        result = evaluate_position_manager_commands(
            {
                "now": NOW,
                "currentPrice": 3300,
                "currentAtr": 2,
                "avgAtr": 2,
                "h1Bars": H1_BARS,
                "equity": 10000,
                "positions": [
                    {"ticket": 4001, "type": "BUY", "openPrice": 3330, "lots": 0.1, "sl": 3328, "profit": -300},
                    {"ticket": 4002, "type": "BUY", "openPrice": 3320, "lots": 0.06, "sl": 3318, "profit": -120},
                    {"ticket": 4003, "type": "BUY", "openPrice": 3310, "lots": 0.04, "sl": 3308, "profit": -40},
                ],
                "states": [
                    {"ticket": 4001, "openTime": "2026-04-13T06:00:00.000Z", "addOnCount": 0},
                    {"ticket": 4002, "openTime": "2026-04-13T06:30:00.000Z", "addOnCount": 1},
                    {"ticket": 4003, "openTime": "2026-04-13T07:00:00.000Z", "addOnCount": 2},
                ],
            }
        )

        close_advisories = [
            a for a in result["advisories"] if a["action"] == "CLOSE" and a["reason"].startswith("adverse_group_exit")
        ]
        assert len(close_advisories) == 0

    def test_closes_all_positions_when_net_loss_reaches_6pct_threshold(self) -> None:
        result = evaluate_position_manager_commands(
            {
                "now": NOW,
                "currentPrice": 3290,
                "currentAtr": 2,
                "avgAtr": 2,
                "h1Bars": H1_BARS,
                "equity": 10000,
                "positions": [
                    {"ticket": 5001, "type": "BUY", "openPrice": 3330, "lots": 0.1, "sl": 3328, "profit": -400},
                    {"ticket": 5002, "type": "BUY", "openPrice": 3320, "lots": 0.06, "sl": 3318, "profit": -180},
                    {"ticket": 5003, "type": "BUY", "openPrice": 3310, "lots": 0.04, "sl": 3308, "profit": -80},
                ],
                "states": [
                    {"ticket": 5001, "openTime": "2026-04-13T06:00:00.000Z", "addOnCount": 0},
                    {"ticket": 5002, "openTime": "2026-04-13T06:30:00.000Z", "addOnCount": 1},
                    {"ticket": 5003, "openTime": "2026-04-13T07:00:00.000Z", "addOnCount": 2},
                ],
            }
        )

        assert_reason_matching(result["advisories"], 5001, 0.1, r"adverse_group_exit_\d+\.\d+pct")
        assert_contains_match(result["advisories"], {"action": "CLOSE", "ticket": 5002, "lots": 0.06})
        assert_contains_match(result["advisories"], {"action": "CLOSE", "ticket": 5003, "lots": 0.04})


# ---------------------------------------------------------------------------
# position manager pending vs market separation
# ---------------------------------------------------------------------------

PENDING_H1_BARS = [
    {
        "time": f"2026-04-13T0{index}:00:00.000Z",
        "open": 3340,
        "high": 3342,
        "low": 3338,
        "close": 3340,
        "atr": 2,
        "ema20": 3340,
        "ema50": 3335,
        "rsi": 50,
        "adx": 25,
        "macdHist": 0,
    }
    for index in range(6)
]


class TestPendingVsMarketSeparation:
    def test_excludes_pending_orders_from_open_position_summary(self) -> None:
        summary = summarize_positions(
            {
                "accountId": "90011087",
                "symbol": "XAGUSD",
                "positions": [
                    {
                        "ticket": 42275433,
                        "symbol": "XAGUSD",
                        "type": "SELL_LIMIT",
                        "order_class": "pending",
                        "lots": 0.05,
                        "openPrice": 59.5,
                        "profit": 0,
                        "strategy": "ai_signal",
                    },
                    {
                        "ticket": 99,
                        "symbol": "XAGUSD",
                        "type": "SELL",
                        "order_class": "market",
                        "lots": 0.02,
                        "openPrice": 59.1,
                        "profit": 1.2,
                        "strategy": "ai_signal",
                    },
                ],
            }
        )

        assert summary["totalOpenPositions"] == 1
        assert summary["sellLots"] == 0.02

    def test_does_not_run_trail_or_tp_close_on_pending_orders(self) -> None:
        result = evaluate_position_manager_commands(
            {
                "now": "2026-04-13T08:00:00.000Z",
                "currentPrice": 58.4,
                "currentAtr": 0.5,
                "avgAtr": 0.5,
                "h1Bars": PENDING_H1_BARS,
                "positions": [
                    {
                        "ticket": 42275433,
                        "type": "SELL_LIMIT",
                        "order_class": "pending",
                        "lots": 0.05,
                        "openPrice": 59.5,
                        "open_price": 59.5,
                        "sl": 59.5,
                        "tp": 57.0,
                        "profit": 0,
                        "strategy": "ai_signal",
                    }
                ],
                "states": [
                    {
                        "ticket": 42275433,
                        "tp1Hit": True,
                        "tp2Hit": True,
                        "maxProfitAtr": 3.6,
                        "beMoved": True,
                        "openTime": "2026-04-12T23:00:00.000Z",
                    }
                ],
            }
        )

        assert [a for a in result["advisories"] if a["action"] == "CLOSE"] == []
        assert [a for a in result["advisories"] if a["action"] == "MODIFY"] == []

    def test_cancels_pending_order_when_market_price_has_reached_its_tp(self) -> None:
        result = evaluate_position_manager_commands(
            {
                "now": "2026-04-13T08:00:00.000Z",
                "currentPrice": 58.36,
                "currentAtr": 0.5,
                "avgAtr": 0.5,
                "h1Bars": PENDING_H1_BARS,
                "positions": [
                    {
                        "ticket": 42275433,
                        "type": "SELL_LIMIT",
                        "order_class": "pending",
                        "lots": 0.05,
                        "openPrice": 59.5,
                        "open_price": 59.5,
                        "sl": 59.5,
                        "tp": 58.36,
                        "profit": 0,
                        "strategy": "ai_signal",
                    }
                ],
                "states": [],
            }
        )

        assert result["advisories"] == [
            {"action": "CANCEL_PENDING", "ticket": 42275433, "reason": "pending_tp_reached_58.36"}
        ]

    def test_infers_pending_from_type_when_order_class_missing(self) -> None:
        result = evaluate_position_manager_commands(
            {
                "now": "2026-04-13T08:00:00.000Z",
                "currentPrice": 3350,
                "currentAtr": 2,
                "avgAtr": 2,
                "h1Bars": PENDING_H1_BARS,
                "positions": [
                    {
                        "ticket": 88,
                        "type": "BUY_STOP",
                        "lots": 0.1,
                        "openPrice": 3340,
                        "tp": 3350,
                        "profit": 0,
                    }
                ],
            }
        )

        assert result["advisories"] == [{"action": "CANCEL_PENDING", "ticket": 88, "reason": "pending_tp_reached_3350"}]
