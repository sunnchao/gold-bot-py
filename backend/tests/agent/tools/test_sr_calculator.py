"""镜像 gold-bot `apps/app-agent/src/tools/sr-calculator.test.ts`。"""
import pytest

from backend.agents.tools.sr_calculator import calculate_fibonacci_extensions


@pytest.mark.parametrize(
    ("direction", "expected"),
    [
        (
            "bullish",
            {
                "level_1_272": 193.6,
                "level_1_618": 210.9,
                "level_2_0": 230.0,
                "level_2_618": 260.9,
            },
        ),
        (
            "bearish",
            {
                "level_1_272": 56.4,
                "level_1_618": 39.1,
                "level_2_0": 20.0,
                "level_2_618": -10.9,
            },
        ),
    ],
)
def test_calculates_extension_levels_above_or_below_retracement_end(direction, expected):
    # TS: 'calculates bullish extension levels above retracement end'
    # TS: 'calculates bullish extension levels above retracement end'
    if direction == "bullish":
        levels = calculate_fibonacci_extensions(100, 150, 130, "bullish")
    else:
        levels = calculate_fibonacci_extensions(150, 100, 120, "bearish")

    assert levels.level_1_272 == pytest.approx(expected["level_1_272"], abs=1e-6)
    assert levels.level_1_618 == pytest.approx(expected["level_1_618"], abs=1e-6)
    assert levels.level_2_0 == pytest.approx(expected["level_2_0"], abs=1e-6)
    assert levels.level_2_618 == pytest.approx(expected["level_2_618"], abs=1e-6)
