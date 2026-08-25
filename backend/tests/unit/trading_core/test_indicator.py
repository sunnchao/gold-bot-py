"""镜像 packages/trading-core/src/indicators/indicator.spec.ts(与 Go oracle 对拍)。"""

from __future__ import annotations

import math

from backend.trading_core.indicators import (
    adx,
    atr,
    bollinger,
    calculate_fib_extension,
    detect_macd_divergence,
    detect_rsi_divergence,
    ema,
    fibonacci,
    is_price_in_fib_zone,
    macd,
    pivot_points,
    rsi,
    stoch,
)

CLOSES = [
    4430, 4433, 4438, 4435, 4437, 4442, 4440, 4444, 4441, 4448, 4450, 4446, 4452, 4455, 4451, 4458, 4460,
    4457, 4462, 4465,
]


def assert_tail_close(label: str, got: list[float], want_tail: list[float], tolerance: float = 1e-9) -> None:
    start = len(got) - len(want_tail)
    for i, want in enumerate(want_tail):
        got_value = got[start + i]
        if math.isnan(want):
            assert math.isnan(got_value), f"{label} tail[{i}]: want NaN, got {got_value}"
            continue
        assert math.isclose(got_value, want, rel_tol=0, abs_tol=tolerance), (
            f"{label} tail[{i}]: {got_value} != {want}"
        )


class TestIndicatorParityWithGoOracle:
    def test_matches_go_ema20_fixture_tail(self) -> None:
        assert_tail_close(
            "EMA20",
            ema(CLOSES, 20),
            [
                4442.15241473154,
                4443.661708566632,
                4445.217736322191,
                4446.339856672458,
                4447.831298894129,
                4449.466413285164,
            ],
        )

    def test_matches_go_atr14_fixture_tail(self) -> None:
        highs = [c + 2.5 for c in CLOSES]
        lows = [c - 2.0 for c in CLOSES]
        assert_tail_close(
            "ATR14",
            atr(highs, lows, CLOSES, 14),
            [6.033163265306, 6.280794460641, 6.15359485631, 6.071195223716, 6.173252707737, 6.125163228613],
        )

    def test_matches_go_rsi14_fixture_tail(self) -> None:
        assert_tail_close(
            "RSI14",
            rsi(CLOSES, 14),
            [69.408369408369, 73.451497928909, 74.488931294992, 70.066064531286, 72.948963703115, 74.533735777533],
        )

    def test_matches_go_macd_output_shape_and_baseline_values(self) -> None:
        result = macd(CLOSES)

        assert len(result["macd"]) == len(CLOSES)
        assert len(result["signal"]) == len(CLOSES)
        assert len(result["histogram"]) == len(CLOSES)
        assert_tail_close(
            "MACD",
            result["macd"],
            [5.542436875906, 6.102989394834, 6.632163141631, 6.731861511441, 7.132116798198, 7.603745677586],
        )
        assert_tail_close(
            "MACD signal",
            result["signal"],
            [4.095213190849, 4.496768431646, 4.923847373643, 5.285450201203, 5.654783520602, 6.044575951999],
        )
        assert_tail_close(
            "MACD histogram",
            result["histogram"],
            [1.447223685057, 1.606220963188, 1.708315767988, 1.446411310238, 1.477333277596, 1.559169725587],
        )

    def test_matches_go_fibonacci_helpers(self) -> None:
        assert fibonacci([100, 103, 108], [90, 94, 95], 3) == {
            "fib236": 103.752,
            "fib382": 101.124,
            "fib500": 99,
            "fib618": 96.876,
            "fib786": 93.852,
        }
        assert calculate_fib_extension(100, 80, "UP") == {
            "level1272": 125.44,
            "level1618": 132.36,
            "level2618": 152.36,
        }
        assert calculate_fib_extension(80, 100, "DOWN") == {
            "level1272": 74.56,
            "level1618": 67.64,
            "level2618": 47.64,
        }
        assert is_price_in_fib_zone(92, 92.36, 87.64, 2, 0.1) is True
        assert is_price_in_fib_zone(96, 92.36, 87.64, 2, 0.1) is False

    def test_matches_go_classic_pivot_points(self) -> None:
        assert pivot_points(108, 94, 107) == {
            "pp": 103,
            "r1": 112,
            "r2": 117,
            "r3": 126,
            "s1": 98,
            "s2": 89,
            "s3": 84,
        }

    def test_matches_go_adx_simple_moving_average_implementation(self) -> None:
        highs = [10, 12, 13, 15, 16, 18]
        lows = [8, 9, 10, 11, 12, 13]
        close = [9, 11, 12, 14, 15, 17]

        result = adx(highs, lows, close, 3)

        assert_tail_close("ADX3", result, [100, 100], 1e-9)
        assert all(math.isnan(v) for v in result[:4])

    def test_matches_go_bollinger_bands_with_sample_standard_deviation(self) -> None:
        result = bollinger([1, 2, 3, 4, 5], 3, 2)

        assert_tail_close("Bollinger upper", result["upper"], [4, 5, 6])
        assert_tail_close("Bollinger mid", result["mid"], [2, 3, 4])
        assert_tail_close("Bollinger lower", result["lower"], [0, 1, 2])
        assert all(math.isnan(v) for v in result["upper"][:2])

    def test_matches_go_stochastic_k_and_d_smoothing(self) -> None:
        result = stoch([10, 11, 12, 13, 14], [5, 6, 7, 8, 9], [7, 10, 11, 12, 13], 3, 2)

        assert_tail_close("Stoch K", result["k"], [85.714285714286, 85.714285714286, 85.714285714286])
        assert_tail_close("Stoch D", result["d"], [math.nan, 85.714285714286, 85.714285714286])

    def test_detects_the_same_bearish_rsi_divergence_shape_as_go_oracle_test(self) -> None:
        bars = [
            {
                "time": "test",
                "open": 100 + i * 0.1,
                "high": 101 + i * 0.1,
                "low": 99 + i * 0.1,
                "close": 100.5 + i * 0.1,
                "rsi": 50 + i * 0.5,
            }
            for i in range(20)
        ]
        bars[5]["high"] = 110
        bars[5]["rsi"] = 75
        bars[15]["high"] = 112
        bars[15]["rsi"] = 73

        assert detect_rsi_divergence(bars) == {
            "type": "bearish_rsi",
            "strength": "weak",
            "confidence": 0.6,
            "priceLevel": 112,
            "time": "test",
        }

    def test_matches_go_macd_divergence_no_signal_behavior_for_monotonic_sample(self) -> None:
        bars = [
            {
                "time": "test",
                "open": 100 + i * 0.1,
                "high": 101 + i * 0.1,
                "low": 99 + i * 0.1,
                "close": 100.5 + i * 0.1,
                "macdHist": i * 0.1,
            }
            for i in range(20)
        ]

        assert detect_macd_divergence(bars) is None
