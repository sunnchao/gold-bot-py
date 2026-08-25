"""镜像 gold-bot `apps/app-agent/src/tools/pattern-detector.test.ts`。"""
import pytest

from backend.agents.tools.pattern_detector import (
    RegressionPoint,
    detect_channel,
    detect_triangle,
    detect_wedge,
    linear_regression,
)


def build_series(
    length: int,
    high_at,
    low_at,
    close_at=None,
    volume_at=None,
):
    highs: list[float] = []
    lows: list[float] = []
    closes: list[float] = []
    volumes: list[float] = []

    for index in range(length):
        high = high_at(index)
        low = low_at(index)
        highs.append(high)
        lows.append(low)
        closes.append(close_at(index, high, low) if close_at is not None else (high + low) / 2)
        volumes.append(volume_at(index) if volume_at is not None else 1000)

    return {"highs": highs, "lows": lows, "closes": closes, "volumes": volumes}


def test_fits_a_simple_upward_sloping_line():
    # TS: linearRegression 'fits a simple upward sloping line'
    result = linear_regression(
        [RegressionPoint(x=0, y=10), RegressionPoint(x=1, y=12), RegressionPoint(x=2, y=14), RegressionPoint(x=3, y=16)]
    )

    assert result.slope == pytest.approx(2)
    assert result.intercept == pytest.approx(10)


def test_detects_a_rising_wedge_with_bearish_breakout_confirmation():
    # TS: detectWedge 'detects a rising wedge with bearish breakout confirmation'
    series = build_series(
        20,
        lambda index: 110 + index * 0.6,
        lambda index: 100 + index * 0.9,
        lambda index, high, low: low - 0.6 if index == 19 else low + (high - low) * 0.45,
        lambda index: 2000 - index * 45,
    )

    patterns = detect_wedge(series["highs"], series["lows"], series["closes"], series["volumes"], 20)

    assert len(patterns) == 1
    assert patterns[0].type == "rising_wedge"
    assert patterns[0].direction == "bearish"
    assert patterns[0].breakoutPrice is not None
    assert patterns[0].lowerLine.slope > patterns[0].upperLine.slope


def test_detects_a_falling_wedge_with_bullish_breakout_confirmation():
    # TS: detectWedge 'detects a falling wedge with bullish breakout confirmation'
    series = build_series(
        20,
        lambda index: 120 - index * 0.9,
        lambda index: 108 - index * 0.6,
        lambda index, high, low: high + 0.6 if index == 19 else low + (high - low) * 0.55,
        lambda index: 1800 - index * 35,
    )

    patterns = detect_wedge(series["highs"], series["lows"], series["closes"], series["volumes"], 20)

    assert len(patterns) == 1
    assert patterns[0].type == "falling_wedge"
    assert patterns[0].direction == "bullish"
    assert patterns[0].breakoutPrice is not None
    assert abs(patterns[0].upperLine.slope) > abs(patterns[0].lowerLine.slope)


def test_detects_an_ascending_channel_when_trend_lines_are_parallel():
    # TS: detectChannel 'detects an ascending channel when trend lines are parallel'
    series = build_series(
        24,
        lambda index: 130 + index * 0.7,
        lambda index: 120 + index * 0.68,
        lambda index, high, low: low + (high - low) * 0.6,
    )

    patterns = detect_channel(series["highs"], series["lows"], 24)

    assert len(patterns) == 1
    assert patterns[0].type == "ascending_channel"
    assert patterns[0].direction == "bullish"
    assert abs(patterns[0].upperLine.slope - patterns[0].lowerLine.slope) < 0.1


def test_detects_a_symmetrical_triangle():
    # TS: detectTriangle 'detects a symmetrical triangle'
    series = build_series(
        24,
        lambda index: 145 - index * 0.5,
        lambda index: 120 + index * 0.5,
        lambda index, high, low: low + (high - low) * 0.5,
    )

    patterns = detect_triangle(series["highs"], series["lows"], series["closes"], 24)

    assert len(patterns) == 1
    assert patterns[0].type == "symmetrical"
    assert patterns[0].direction == "continuation"
    assert patterns[0].apexPrice is not None
