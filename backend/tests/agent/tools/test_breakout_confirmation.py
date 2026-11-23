from datetime import UTC, datetime, timedelta

import pytest

from backend.agents.agents._support import get_symbol_profile, validate_arbitration_business
from backend.agents.tools.breakout_confirmation import UNCONFIRMED_BREAKOUT, build_breakout_levels

LEVEL = 2340.0
BOUNDARY = datetime(2026, 11, 20, 12, tzinfo=UTC)


def bar(time, opening, high, low, close, atr=5):
    return {"time": time.isoformat(), "open": opening, "high": high, "low": low, "close": close, "atr": atr}


@pytest.mark.parametrize(
    ("h1_close", "first_close", "second_close", "forming_high", "gap", "expected"),
    [
        (2339, 2339, 2339, 2342, False, "intrabar_broken"),
        (2339, 2341, 2339, 2339.8, False, "intrabar_broken"),
        (2339, 2340.5, 2340.5, 2342, False, "closed_confirmed"),
        (2339, 2340.5, 2340.5, 2342, True, "intrabar_broken"),
        (2340.5, 2339, 2339, 2342, False, "closed_confirmed"),
        (2340.49, 2339, 2339, 2342, False, "intrabar_broken"),
        (2339, 2339, 2339, 2339.8, False, "touched"),
    ],
)
def test_h1_level_uses_closed_bodies_not_wicks(h1_close, first_close, second_close, forming_high, gap, expected):
    h1 = [
        bar(BOUNDARY - timedelta(hours=6 - index), 2330, high, 2328, 2330)
        for index, high in enumerate((2334, 2336, LEVEL, 2335, 2336))
    ] + [
        bar(BOUNDARY - timedelta(hours=1), 2338, max(h1_close, 2339.5), 2337, h1_close),
        bar(BOUNDARY, h1_close, max(forming_high, h1_close), 2337, 2339),
    ]
    m15 = [
        bar(BOUNDARY - timedelta(minutes=45 if gap else 30), 2338, max(2339.5, first_close), 2337, first_close),
        bar(BOUNDARY - timedelta(minutes=15), first_close, max(2339.5, first_close, second_close), 2337, second_close),
        bar(BOUNDARY, second_close, max(forming_high, second_close), 2337, 2339),
    ]
    levels = build_breakout_levels({"H1": h1, "M15": m15}, 2339)
    assert {"price": LEVEL, "side": "resistance", "timeframe": "H1", "state": expected} in levels


def test_liquidity_sweep_marks_support_as_intrabar_even_after_recovery():
    h1 = [bar(BOUNDARY, 2342, 2343, 2341, 2342)]
    sweeps = {"h1_sweeps": [{"index": 0, "level": LEVEL, "reversed": True, "side": "BULL"}]}

    assert build_breakout_levels({"H1": h1}, 2342, sweeps) == [
        {"price": LEVEL, "side": "support", "timeframe": "H1", "state": "intrabar_broken"}
    ]


@pytest.mark.parametrize(
    ("state", "entry", "basis", "allowed"),
    [
        ("intrabar_broken", 2341, "intrabar_breakout", False),
        ("intrabar_broken", 2341, "closed_breakout", False),
        ("intrabar_broken", 2341, "", False),
        ("intrabar_broken", 2335, "pullback_limit", True),
        ("closed_confirmed", 2341, "closed_breakout", True),
    ],
)
def test_arbitration_cannot_override_programmatic_break_state(state, entry, basis, allowed):
    arbitration = {
        "final_direction": "buy", "action": "open", "confidence": 85, "trigger_basis": basis,
        "trade_recommendation": {"direction": "buy", "entry_price": entry, "stop_loss": 2325, "take_profit_1": 2370},
    }
    result = validate_arbitration_business(
        arbitration, 2341, get_symbol_profile("XAUUSD"), technical_phase="breakout",
        breakout_levels=[{"price": LEVEL, "side": "resistance", "timeframe": "H1", "state": state}],
    )
    assert result.valid is allowed
    if not allowed:
        assert result.fixed_arbitration["action"] == "hold"
        assert result.fixed_arbitration["confidence"] == 20
        assert result.fixed_trade["direction"] == "hold"
        assert result.warnings == [UNCONFIRMED_BREAKOUT]
