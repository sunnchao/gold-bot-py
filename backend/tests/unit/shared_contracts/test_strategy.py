"""镜像 packages/shared-contracts/src/strategy.spec.ts。"""

from __future__ import annotations

from backend.shared_contracts import EA_STRATEGY_NAMES, is_ea_strategy_name


def test_freezes_the_ea_recognized_strategy_name_list() -> None:
    assert list(EA_STRATEGY_NAMES) == [
        "pullback",
        "breakout_retest",
        "divergence",
        "breakout_pyramid",
        "counter_pullback",
        "scale_in",
        "range",
        "momentum_scalp",
        "ai_signal",
    ]


def test_rejects_internal_or_invented_strategy_names() -> None:
    assert is_ea_strategy_name("pullback") is True
    assert is_ea_strategy_name("scale_in") is True
    assert is_ea_strategy_name("smc") is False
    assert is_ea_strategy_name("") is False
