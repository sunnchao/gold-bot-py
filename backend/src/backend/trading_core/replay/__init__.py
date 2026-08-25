"""replay 支持模块(镜像 packages/trading-core/src/replay;引擎 replay.py 另行移植)。"""

from __future__ import annotations

from backend.trading_core.replay.breakout_cache import (
    BreakoutCache,
    BreakoutCacheEntry,
    BreakoutConfirmResult,
    confirm_breakout_pyramid,
    get_breakout_cache,
)
from backend.trading_core.replay.coverage import (
    ReplayCoverageSummary,
    ReplayFixturePair,
    compute_replay_coverage,
    list_replay_fixture_pairs,
)
from backend.trading_core.replay.fib_extension import (
    FibExtension,
    apply_fib_extension_tp,
    calculate_fib_extension,
)
from backend.trading_core.replay.replay import run_replay
from backend.trading_core.replay.scale_in import (
    ScaleInResult,
    ScaleInSignal,
    check_scale_in,
)
from backend.trading_core.replay.smc_scoring import (
    SMCContext,
    calculate_smc_bonus,
    has_confirming_sweep,
    has_recent_choch,
    has_valid_ob_near_price,
)
from backend.trading_core.replay.sr_sltp import (
    AIResult,
    SRSLTPResult,
    pick_sltp,
)

__all__ = [
    "AIResult",
    "BreakoutCache",
    "BreakoutCacheEntry",
    "BreakoutConfirmResult",
    "FibExtension",
    "ReplayCoverageSummary",
    "ReplayFixturePair",
    "SMCContext",
    "SRSLTPResult",
    "ScaleInResult",
    "ScaleInSignal",
    "apply_fib_extension_tp",
    "calculate_fib_extension",
    "calculate_smc_bonus",
    "check_scale_in",
    "compute_replay_coverage",
    "confirm_breakout_pyramid",
    "get_breakout_cache",
    "has_confirming_sweep",
    "has_recent_choch",
    "has_valid_ob_near_price",
    "list_replay_fixture_pairs",
    "pick_sltp",
    "run_replay",
]
