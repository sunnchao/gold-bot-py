"""Replay 引擎(镜像 packages/trading-core/src/replay/replay.ts,runReplay → run_replay)。

1:1 移植:函数 snake_case 镜像 TS 导出,dict 键保持 TS camelCase / snake_case 原样,
`??`/`||`/Math.round(=floor(x+0.5))/toFixed(ECMA 精确算法)/NaN/边界/mutation 顺序逐字镜像。
信号链路:collectCandidates(H1/H4/M30/M15/M5/M1)→ H4 过滤 → 趋势扣分 → M15 确认加分 →
上下文加分(harmonic + SMC bonus)→ minScore → 持仓冲突 → AI SL/TP 覆盖 → 最小盈亏比 →
持仓命令(evaluate_position_manager_commands)。
"""

from __future__ import annotations

import math
import os
import re
from fractions import Fraction
from typing import Any

from backend.trading_core.engine.config import StrategyConfig, get_strategy_config_by_symbol
from backend.trading_core.harmonic import build_context as _build_harmonic_context
from backend.trading_core.indicators import (
    adx as _adx,
)
from backend.trading_core.indicators import (
    atr as _atr,
)
from backend.trading_core.indicators import (
    bollinger as _bollinger,
)
from backend.trading_core.indicators import (
    ema as _ema,
)
from backend.trading_core.indicators import (
    fibonacci as _fibonacci,
)
from backend.trading_core.indicators import (
    is_price_in_fib_zone as _is_price_in_fib_zone,
)
from backend.trading_core.indicators import (
    macd as _macd,
)
from backend.trading_core.indicators import (
    pivot_points as _pivot_points,
)
from backend.trading_core.indicators import (
    rsi as _rsi,
)
from backend.trading_core.indicators import (
    stoch as _stoch,
)
from backend.trading_core.positionmgr.manager import (
    evaluate_position_manager_commands as _evaluate_position_manager_commands,
)
from backend.trading_core.replay.breakout_cache import confirm_breakout_pyramid
from backend.trading_core.replay.smc_scoring import calculate_smc_bonus
from backend.trading_core.replay.sr_sltp import pick_sltp
from backend.trading_core.smc import build_smc_context as _build_smc_context

__all__ = [
    "run_replay",
]

# ------------------------------------------------------------------ 类型别名

ReplayRawBar = dict[str, Any]
"""镜像 ReplayRawBar:time/open/high/low/close/volume + 可选指标字段。"""

ReplaySnapshot = dict[str, Any]
"""镜像 ReplaySnapshot:account_id/symbol/analysis_time/current_price/bars/smc/harmonic/ai_result/positions。"""

ReplaySmcContext = dict[str, Any]
"""镜像 ReplaySmcContext:h4/h1/m30/m15 的 breaks / sweeps / obs / fvgs(键为 snake_case)。"""

ReplayHarmonicContext = dict[str, Any]
"""镜像 ReplayHarmonicContext:active_pattern。"""

ReplaySignal = dict[str, Any]
"""镜像 ReplaySignal:side/entry/stop_loss/tp1/tp2/score/strategy/atr/all_strategies。"""

ReplayLog = dict[str, Any]
"""镜像 ReplayLog:level/strategy/msg。"""

ReplayResult = dict[str, Any]
"""镜像 ReplayResult:signal/logs/position_commands/position_states/canProduceLiveCommands。"""

EnrichedReplayBar = dict[str, Any]
"""镜像 EnrichedReplayBar:ReplayRawBar + 计算指标(ema20/atr/rsi/macd_hist/bb/fib/pivot...)。"""

ReplayPositionCommand = dict[str, Any]
"""镜像 ReplayPositionCommand:action/ticket/lots?/new_sl?/reason。"""

ReplayTraditionalConfig = dict[str, Any]
"""镜像 ReplayTraditionalConfig:pullback/breakoutRetest/divergence/breakoutPyramid 等子对象。"""

MomentumScalpConfig = dict[str, Any]
"""镜像 MomentumScalpConfig:momentum_scalp(已禁用)配置。"""

_DEFAULT_MOMENTUM_SCALP_CONFIG: MomentumScalpConfig = {
    "minAdx": 20,
    "emaPeriod1": 5,
    "emaPeriod2": 8,
    "emaPeriod3": 12,
    "rsiBullThresh": 45,
    "rsiBearThresh": 55,
    "rsiCrossoverBull": 48,
    "rsiCrossoverBear": 52,
    "slAtr": 0.4,
    "tp1Atr": 0.5,
    "tp2Atr": 0.8,
    "volConfirm": 1.05,
    "minScore": 7,
}


# ------------------------------------------------------------------ 数值/字符串 helper


def _math_round(value: float) -> int:
    """镜像 JS Math.round:等价于 floor(x + 0.5)(半值朝 +∞,含负数)。"""
    return math.floor(value + 0.5)


def _round_to_precision(value: float, precision: int) -> float:
    factor = 10**precision
    return _math_round(value * factor) / factor


def _round_to_significant_digits(value: float, digits: int) -> float:
    """镜像 Number(value.toPrecision(digits)):先短格式再解析回 float。"""
    return float(f"{value:.{digits}g}")


def _to_fixed(value: float, precision: int) -> str:
    """镜像 JS Number.prototype.toFixed(ECMA-262 21.1.3.3)。

    在精确 double 有理展开(Fraction)上取 n = 距离 x·10^f 最近的整数,tie 取更大的 n
    (朝 +∞),与 Node 在同一个 double 上的输出一致;非 Python round-half-even。
    """
    if math.isnan(value):
        return "NaN"
    if math.isinf(value):
        return "Infinity" if value > 0 else "-Infinity"
    if precision < 0 or precision > 100:
        raise ValueError(f"toFixed() digits must be in [0, 100], got {precision}")
    if abs(value) >= 10**21:
        # JS Number::toString 短表示(极少触达;repr 与 V8 基本一致)
        return repr(value)
    sign = "-" if value < 0 else ""
    scaled = Fraction(value) * (10**precision)
    n = math.floor(scaled + Fraction(1, 2))
    digits = str(abs(n))
    if precision == 0:
        return f"{sign}{digits}"
    if len(digits) <= precision:
        digits = digits.zfill(precision + 1)
    return f"{sign}{digits[:-precision]}.{digits[-precision:]}"


def _format_fixed(value: float, precision: int) -> str:
    return _to_fixed(value, precision)


def _format_risk_reward(value: float) -> str:
    r"""镜像 toFixed(3).replace(/0+$/,'').replace(/\.$/,'')。"""
    return str(_to_fixed(value, 3)).rstrip("0").rstrip(".")


def _js_number_string(value: float) -> str:
    """镜像 JS Number 字符串化(模板插值):整数型 double 不打印小数点。"""
    if math.isnan(value):
        return "NaN"
    if math.isinf(value):
        return "Infinity" if value > 0 else "-Infinity"
    if isinstance(value, float) and math.isfinite(value) and value == int(value) and abs(value) < 1e21:
        return str(int(value))
    return repr(value)


def _format_fixed_half_even(value: float, precision: int) -> str:
    """镜像 formatFixedHalfEven(仅 momentum_scalp 禁用代码路径使用)。"""
    factor = 10**precision
    scaled = value * factor
    floor = math.floor(scaled)
    fraction = scaled - floor
    if abs(fraction - 0.5) < 2.220446049250313e-16:
        rounded = floor if floor % 2 == 0 else floor + 1
    else:
        rounded = _math_round(scaled)
    return _to_fixed(rounded / factor, precision)


def _as_record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _string_field(record: dict[str, Any], key: str) -> str:
    value = record.get(key)
    return value if isinstance(value, str) else ""


def _optional_string_field(record: dict[str, Any], key: str) -> str | None:
    value = record.get(key)
    return value if isinstance(value, str) else None


def _number_field(record: dict[str, Any], key: str) -> float:
    value = record.get(key)
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)) and math.isfinite(value):
        return float(value)
    return 0


def _optional_number_field(record: dict[str, Any], key: str) -> float | None:
    value = record.get(key)
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and math.isfinite(value):
        return float(value)
    return None


def _optional_boolean_field(record: dict[str, Any], key: str) -> bool | None:
    value = record.get(key)
    return value if isinstance(value, bool) else None


def _coalesce(record: dict[str, Any], *keys: str) -> Any:
    """镜像 TS `a ?? b ?? c`:取第一个非 None 键值。"""
    for key in keys:
        if key in record and record[key] is not None:
            return record[key]
    return None


def _js_or_number(a: float, b: float) -> float:
    """镜像 TS `a || b`(数值):0 / NaN / -0 视为 falsy。"""
    return a if (a == a and a != 0) else b


def _finite_or_neutral_stoch(value: float) -> float:
    """镜像 finiteOrNeutralStoch:窗口不足返回中性 50,避免 NaN 当 0 触发超卖加分。"""
    return value if math.isfinite(value) else 50.0


# ------------------------------------------------------------------ 配置


def _replay_traditional_config(cfg: StrategyConfig) -> ReplayTraditionalConfig:
    return {
        "pullback": {
            "minAdx": cfg["pullbackMinADX"],
            "rsiOverbought": cfg["pullbackRSIOverbought"],
            "rsiOversold": cfg["pullbackRSIOversold"],
            "distAtr": cfg["pullbackDistATR"],
            "adxBonus": cfg["pullbackADXBonus"],
            "slAtr": cfg["pullbackSLATR"],
            "tp1Atr": cfg["pullbackTP1ATR"],
            "tp2Atr": cfg["pullbackTP2ATR"],
        },
        "breakoutRetest": {
            "lookback": cfg["breakoutRetestLookback"],
            "confirmWindow": cfg["breakoutRetestConfirmWindow"],
            "distAtr": cfg["breakoutRetestDistATR"],
            "slAtr": cfg["breakoutRetestSLATR"],
            "tp1Atr": cfg["breakoutRetestTP1ATR"],
            "tp2Atr": cfg["breakoutRetestTP2ATR"],
        },
        "divergence": {
            "windowRecent": cfg["divergenceWindowRecent"],
            "windowPrev": cfg["divergenceWindowPrev"],
            "rsiBullThresh": cfg["divergenceRSIBullThresh"],
            "rsiBearThresh": cfg["divergenceRSIBearThresh"],
            "slAtr": cfg["divergenceSLATR"],
            "tp1Atr": cfg["divergenceTP1ATR"],
            "tp2Atr": cfg["divergenceTP2ATR"],
        },
        "breakoutPyramid": {
            "minAdx": cfg["breakoutPyramidMinADX"],
            "slAtr": cfg["breakoutPyramidSLATR"],
        },
        "srMinDistATR": cfg["srMinDistATR"],
        "srMaxDistATR": cfg["srMaxDistATR"],
        "srBufferATR": cfg["srBufferATR"],
        "pullbackFibEnabled": cfg["pullbackFib"]["retracementEnabled"],
        "pullbackFibMaxSLDistATR": cfg["pullbackFib"]["maxFibSLDistATR"],
        "pullbackFibMinRR": cfg["pullbackFib"]["fibMinRR"],
        "minRR": cfg["minRR"],
        "minScore": cfg["minScore"],
        "h4ADXThreshold": cfg["h4ADXThreshold"],
        "h4RequireConsecutive": cfg["h4RequireConsecutive"],
    }


def _momentum_scalp_config_for_symbol(symbol: str | None) -> MomentumScalpConfig:
    config = dict(_DEFAULT_MOMENTUM_SCALP_CONFIG)
    base = _base_symbol(symbol)
    if base == "XAUUSD":
        config["minAdx"] = 18
        config["volConfirm"] = 1.05
        config["minScore"] = 6
    elif base == "XAGUSD":
        config["minAdx"] = 15
        config["slAtr"] = 0.6
        config["tp1Atr"] = 0.8
        config["tp2Atr"] = 1.2
        config["minScore"] = 7
    elif base in ("GBPJPY", "EURJPY", "USDJPY"):
        config["minAdx"] = 18
        config["slAtr"] = 0.8
        config["tp1Atr"] = 1
        config["tp2Atr"] = 1.5
        config["rsiBullThresh"] = 42
        config["rsiBearThresh"] = 58
        config["rsiCrossoverBull"] = 46
        config["rsiCrossoverBear"] = 54
        config["volConfirm"] = 1.02
        config["minScore"] = 7
    elif base == "EURUSD":
        config["minAdx"] = 15
        config["slAtr"] = 0.3
        config["tp1Atr"] = 0.5
        config["tp2Atr"] = 0.8
        config["minScore"] = 6
    elif base == "GBPUSD":
        config["minAdx"] = 16
        config["slAtr"] = 0.5
        config["tp1Atr"] = 0.7
        config["tp2Atr"] = 1
        config["minScore"] = 6
    elif base == "USDCAD":
        config["minAdx"] = 16
        config["slAtr"] = 0.4
        config["tp1Atr"] = 0.6
        config["tp2Atr"] = 0.9
        config["minScore"] = 6
    elif base == "US100CASH":
        config["minAdx"] = 16
        config["slAtr"] = 0.5
        config["tp1Atr"] = 0.8
        config["tp2Atr"] = 1.2
        config["minScore"] = 6
    elif base in ("USOILCASH", "UKOILCASH"):
        config["minAdx"] = 15
        config["slAtr"] = 0.6
        config["tp1Atr"] = 0.8
        config["tp2Atr"] = 1.2
        config["minScore"] = 7
    return config


def _base_symbol(symbol: str | None) -> str:
    normalized = re.sub(r"M#$", "", re.sub(r"#$", "", (symbol or "").strip().upper()))
    if normalized in ("GOLD", "XAUUSD"):
        return "XAUUSD"
    if normalized in ("SILVER", "XAGUSD"):
        return "XAGUSD"
    if normalized in ("US100", "NAS100", "US100CASH"):
        return "US100CASH"
    if normalized in ("USOIL", "WTI", "USOILCASH"):
        return "USOILCASH"
    if normalized in ("UKOIL", "BRENT", "UKOILCASH"):
        return "UKOILCASH"
    return normalized


def _rounding_precision_for_symbol(symbol: str | None) -> int:
    base = _base_symbol(symbol)
    if base in ("EURUSD", "GBPUSD", "USDCAD", "AUDUSD", "NZDUSD"):
        return 5
    if base in ("GBPJPY", "EURJPY", "USDJPY"):
        return 3
    return 2


# ------------------------------------------------------------------ 主入口


def run_replay(raw: Any) -> ReplayResult:
    """镜像 runReplay:完整信号链路 + 持仓命令审计(只读,canProduceLiveCommands=False)。"""
    snapshot = _normalize_replay_snapshot(raw)
    strategy_config = get_strategy_config_by_symbol(_base_symbol(snapshot.get("symbol")))
    traditional_config = _replay_traditional_config(strategy_config)
    # D1 富化(镜像 TS 的 enrichedD1;当前管线未消费该变量)
    _enrich_bars(snapshot["bars"].get("D1") or [])
    enriched_h1 = _enrich_bars(snapshot["bars"].get("H1") or [])
    enriched_h4 = _enrich_bars(snapshot["bars"].get("H4") or [])
    enriched_m30 = _enrich_bars(snapshot["bars"].get("M30") or [])
    enriched_m15 = _enrich_bars(snapshot["bars"].get("M15") or [])
    enriched_m5 = _enrich_bars(snapshot["bars"].get("M5") or [])
    enriched_m1 = _enrich_bars(snapshot["bars"].get("M1") or [])
    smc_context = snapshot.get("smc")
    if smc_context is None:
        smc_context = _build_replay_smc_context(enriched_h4, enriched_h1, enriched_m30, enriched_m15)
    harmonic_context = snapshot.get("harmonic")
    if harmonic_context is None:
        harmonic_context = _replay_harmonic_from_core_context(
            _build_harmonic_context(enriched_h4, enriched_h1, enriched_m30)
        )
    h1_last = enriched_h1[-1] if enriched_h1 else {}
    current_price = snapshot.get("current_price")
    if current_price is None:
        current_price = h1_last.get("close") or 0
    momentum_config = _momentum_scalp_config_for_symbol(snapshot.get("symbol"))
    price_precision = _rounding_precision_for_symbol(snapshot.get("symbol"))
    positions = _normalize_position_manager_positions(snapshot.get("positions") or [])
    candidates = _collect_replay_candidates(
        enriched_h1,
        enriched_h4,
        enriched_m30,
        enriched_m15,
        enriched_m5,
        enriched_m1,
        current_price,
        smc_context,
        traditional_config,
        momentum_config,
        price_precision,
        snapshot.get("symbol") or "",
        positions,
        snapshot.get("ai_result"),
    )
    h4_filter_result = _apply_h4_filter_to_candidates(candidates, enriched_h4, traditional_config)
    trend_rated_candidates: list[ReplaySignal] = []
    for candidate in h4_filter_result["candidates"]:
        rated = _apply_trend_rating_penalty(candidate, enriched_h4, enriched_h1, enriched_m30)
        if rated is not None:
            trend_rated_candidates.append(rated)
    m15_boosted_candidates: list[ReplaySignal] = []
    for candidate in trend_rated_candidates:
        boosted = _apply_m15_confirmation_boost(candidate, enriched_m15, current_price)
        if boosted is not None:
            m15_boosted_candidates.append(boosted)
    boosted_candidates = [
        _apply_context_scoring_bonuses(candidate, harmonic_context, smc_context, current_price, len(enriched_h1) - 1)
        for candidate in m15_boosted_candidates
    ]
    boosted_signal = _select_highest_score(boosted_candidates)
    raw_signal = _select_raw_candidate_for(boosted_signal, candidates)
    selected_signal = None if boosted_signal is None else _with_all_strategies(boosted_signal, boosted_candidates)
    min_score_result = _apply_min_score_filter(selected_signal, traditional_config["minScore"])
    position_filter_result = _apply_position_conflict_filter(min_score_result["signal"], positions)
    ai_stop_loss_result = _apply_ai_stop_loss_override(position_filter_result["signal"], snapshot.get("ai_result"))
    ai_take_profit_result = _apply_ai_take_profit_override(ai_stop_loss_result["signal"], snapshot.get("ai_result"))
    rr_filter_result = _apply_min_rr_filter(ai_take_profit_result["signal"], traditional_config["minRR"])
    position_review = _evaluate_replay_position_commands(snapshot, enriched_h1, current_price)

    return {
        "signal": rr_filter_result["signal"],
        "logs": _build_replay_logs(
            snapshot,
            enriched_h1,
            enriched_h4,
            enriched_m30,
            enriched_m15,
            enriched_m5,
            enriched_m1,
            current_price,
            raw_signal,
            boosted_signal,
            rr_filter_result["signal"],
            h4_filter_result["logs"],
            min_score_result["logs"],
            position_filter_result["logs"],
            ai_stop_loss_result["logs"] + ai_take_profit_result["logs"],
            rr_filter_result["logs"],
            momentum_config,
            traditional_config,
            smc_context,
            smc_context,
        ),
        "position_commands": None if len(position_review["commands"]) == 0 else position_review["commands"],
        "position_states": position_review["states"],
        "canProduceLiveCommands": False,
    }


# ------------------------------------------------------------------ 归一化


def _normalize_replay_snapshot(raw: Any) -> ReplaySnapshot:
    record = _as_record(raw)
    bars = _as_record(record.get("bars"))
    return {
        "account_id": _string_field(record, "account_id"),
        "symbol": _optional_string_field(record, "symbol"),
        "analysis_time": _optional_string_field(record, "analysis_time"),
        "current_price": _optional_number_field(record, "current_price"),
        "bars": {timeframe: _normalize_bars(value) for timeframe, value in bars.items()},
        "smc": _normalize_replay_smc(_coalesce(record, "smc")),
        "harmonic": _normalize_replay_harmonic(
            _coalesce(record, "harmonic", "harmonic_context", "harmonicContext")
        ),
        "ai_result": _normalize_replay_ai_result(_coalesce(record, "ai_result", "aiResult")),
        "positions": record.get("positions") if isinstance(record.get("positions"), list) else [],
        "position_states": record.get("position_states")
        if isinstance(record.get("position_states"), list)
        else [],
        "account": record.get("account") if isinstance(record.get("account"), dict) else None,
    }


def _normalize_bars(value: Any) -> list[ReplayRawBar]:
    if not isinstance(value, list):
        return []
    out: list[ReplayRawBar] = []
    for entry in value:
        record = _as_record(entry)
        out.append(
            {
                "time": _optional_string_field(record, "time"),
                "open": _number_field(record, "open"),
                "high": _number_field(record, "high"),
                "low": _number_field(record, "low"),
                "close": _number_field(record, "close"),
                "volume": _optional_number_field(record, "volume"),
                "ema20": _optional_number_field(record, "ema20"),
                "ema50": _optional_number_field(record, "ema50"),
                "atr": _optional_number_field(record, "atr"),
                "rsi": _optional_number_field(record, "rsi"),
                "macd_hist": _optional_number_field(record, "macd_hist"),
                "macdHist": _optional_number_field(record, "macdHist"),
                "adx": _optional_number_field(record, "adx"),
                "ADX": _optional_number_field(record, "ADX"),
                "bb_upper": _optional_number_field(record, "bb_upper"),
                "bb_lower": _optional_number_field(record, "bb_lower"),
                "bbUpper": _optional_number_field(record, "bbUpper"),
                "bbLower": _optional_number_field(record, "bbLower"),
                "BBUpper": _optional_number_field(record, "BBUpper"),
                "BBLower": _optional_number_field(record, "BBLower"),
                "stoch_k": _optional_number_field(record, "stoch_k"),
                "stochK": _optional_number_field(record, "stochK"),
                "StochK": _optional_number_field(record, "StochK"),
                "vol_sma": _optional_number_field(record, "vol_sma"),
                "volSMA": _optional_number_field(record, "volSMA"),
                "VolSMA": _optional_number_field(record, "VolSMA"),
                "fib_382": _optional_number_field(record, "fib_382"),
                "fib382": _optional_number_field(record, "fib382"),
                "Fib382": _optional_number_field(record, "Fib382"),
                "fib_618": _optional_number_field(record, "fib_618"),
                "fib618": _optional_number_field(record, "fib618"),
                "Fib618": _optional_number_field(record, "Fib618"),
                "fib_786": _optional_number_field(record, "fib_786"),
                "fib786": _optional_number_field(record, "fib786"),
                "Fib786": _optional_number_field(record, "Fib786"),
                "pp": _coalesce_optional_number(record, "pp", "PP"),
                "r1": _coalesce_optional_number(record, "r1", "R1"),
                "r2": _coalesce_optional_number(record, "r2", "R2"),
                "s1": _coalesce_optional_number(record, "s1", "S1"),
                "s2": _coalesce_optional_number(record, "s2", "S2"),
            }
        )
    return out


def _coalesce_optional_number(record: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = _optional_number_field(record, key)
        if value is not None:
            return value
    return None


def _normalize_replay_ai_result(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    record = _as_record(value)
    suggested_sl = _optional_number_field(record, "suggested_sl")
    if suggested_sl is None:
        suggested_sl = _optional_number_field(record, "suggestedSL")
    suggested_tp = _optional_number_field(record, "suggested_tp")
    if suggested_tp is None:
        suggested_tp = _optional_number_field(record, "suggestedTP")
    return {"suggested_sl": suggested_sl, "suggested_tp": suggested_tp}


def _normalize_replay_harmonic(value: Any) -> ReplayHarmonicContext | None:
    if value is None:
        return None
    record = _as_record(value)
    active = _coalesce(record, "active_pattern", "activePattern", "ActivePattern")
    if active is None:
        return {"active_pattern": None}
    active_record = _as_record(active)
    direction = _optional_string_field(active_record, "direction")
    if direction is None:
        direction = _optional_string_field(active_record, "Direction")
    if direction is None:
        direction = ""
    type_ = _optional_string_field(active_record, "type")
    if type_ is None:
        type_ = _optional_string_field(active_record, "Type")
    if type_ is None:
        type_ = ""
    score = _optional_number_field(active_record, "score")
    if score is None:
        score = _optional_number_field(active_record, "Score")
    if score is None:
        score = 0
    return {"active_pattern": {"type": type_, "direction": direction, "score": score}}


def _normalize_replay_smc(value: Any) -> ReplaySmcContext | None:
    if value is None:
        return None
    record = _as_record(value)
    return {
        "h4_breaks": _normalize_structure_breaks(_coalesce(record, "h4_breaks", "h4Breaks", "H4Breaks")),
        "h4_sweeps": _normalize_liquidity_sweeps(_coalesce(record, "h4_sweeps", "h4Sweeps", "H4Sweeps")),
        "h4_obs": _normalize_order_blocks(_coalesce(record, "h4_obs", "h4OBs", "H4OBs")),
        "h1_breaks": _normalize_structure_breaks(_coalesce(record, "h1_breaks", "h1Breaks", "H1Breaks")),
        "h1_sweeps": _normalize_liquidity_sweeps(_coalesce(record, "h1_sweeps", "h1Sweeps", "H1Sweeps")),
        "h1_obs": _normalize_order_blocks(_coalesce(record, "h1_obs", "h1OBs", "H1OBs")),
        "h1_short_obs": _normalize_order_blocks(_coalesce(record, "h1_short_obs", "h1ShortOBs", "H1ShortOBs")),
        "h1_fvgs": _normalize_fvgs(_coalesce(record, "h1_fvgs", "h1FVGs", "H1FVGs")),
        "m30_breaks": _normalize_structure_breaks(_coalesce(record, "m30_breaks", "m30Breaks", "M30Breaks")),
        "m30_sweeps": _normalize_liquidity_sweeps(_coalesce(record, "m30_sweeps", "m30Sweeps", "M30Sweeps")),
        "m30_obs": _normalize_order_blocks(
            _coalesce(record, "m30_obs", "m30OBs", "M30OBs", "m30_order_blocks", "m30OrderBlocks", "M30OrderBlocks")
        ),
        "m30_fvgs": _normalize_fvgs(
            _coalesce(
                record, "m30_fvgs", "m30FVGs", "M30FVGs", "m30_fair_value_gaps", "m30FairValueGaps", "M30FairValueGaps"
            )
        ),
        "m15_breaks": _normalize_structure_breaks(_coalesce(record, "m15_breaks", "m15Breaks", "M15Breaks")),
        "m15_sweeps": _normalize_liquidity_sweeps(_coalesce(record, "m15_sweeps", "m15Sweeps", "M15Sweeps")),
        "m15_obs": _normalize_order_blocks(
            _coalesce(record, "m15_obs", "m15OBs", "M15OBs", "m15_order_blocks", "m15OrderBlocks", "M15OrderBlocks")
        ),
        "m15_fvgs": _normalize_fvgs(
            _coalesce(
                record, "m15_fvgs", "m15FVGs", "M15FVGs", "m15_fair_value_gaps", "m15FairValueGaps", "M15FairValueGaps"
            )
        ),
    }


def _normalize_structure_breaks(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    out: list[dict[str, Any]] = []
    for entry in value:
        record = _as_record(entry)
        direction = _optional_string_field(record, "direction")
        if direction is None:
            direction = _optional_string_field(record, "Direction")
        type_ = _optional_string_field(record, "type")
        if type_ is None:
            type_ = _optional_string_field(record, "Type")
        if (direction not in ("UP", "DOWN")) or (type_ not in ("BOS", "CHoCH")):
            continue
        level = _optional_number_field(record, "level")
        if level is None:
            level = _optional_number_field(record, "Level")
        if level is None:
            level = 0
        out.append(
            {
                "index": _js_or_number(_number_field(record, "index"), _number_field(record, "Index")),
                "direction": direction,
                "level": level,
                "type": type_,
            }
        )
    return out


def _normalize_liquidity_sweeps(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    out: list[dict[str, Any]] = []
    for entry in value:
        record = _as_record(entry)
        side = _optional_string_field(record, "side")
        if side is None:
            side = _optional_string_field(record, "Side")
        if side not in ("BULL", "BEAR"):
            continue
        level = _optional_number_field(record, "level")
        if level is None:
            level = _optional_number_field(record, "Level")
        if level is None:
            level = 0
        reversed_ = _optional_boolean_field(record, "reversed")
        if reversed_ is None:
            reversed_ = _optional_boolean_field(record, "Reversed")
        out.append(
            {
                "index": _js_or_number(_number_field(record, "index"), _number_field(record, "Index")),
                "level": level,
                "side": side,
                "reversed": reversed_,
            }
        )
    return out


def _normalize_order_blocks(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    out: list[dict[str, Any]] = []
    for entry in value:
        record = _as_record(entry)
        side = _optional_string_field(record, "side")
        if side is None:
            side = _optional_string_field(record, "Side")
        if side not in ("BUY", "SELL"):
            continue
        high = _optional_number_field(record, "high")
        if high is None:
            high = _optional_number_field(record, "High")
        if high is None:
            high = 0
        low = _optional_number_field(record, "low")
        if low is None:
            low = _optional_number_field(record, "Low")
        if low is None:
            low = 0
        valid = _optional_boolean_field(record, "valid")
        if valid is None:
            valid = _optional_boolean_field(record, "Valid")
        if valid is None:
            valid = False
        out.append(
            {
                "index": _js_or_number(_number_field(record, "index"), _number_field(record, "Index")),
                "side": side,
                "high": high,
                "low": low,
                "valid": valid,
            }
        )
    return out


def _normalize_fvgs(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    out: list[dict[str, Any]] = []
    for entry in value:
        record = _as_record(entry)
        upper_bound = _optional_number_field(record, "upper_bound")
        if upper_bound is None:
            upper_bound = _optional_number_field(record, "upperBound")
        if upper_bound is None:
            upper_bound = _optional_number_field(record, "UpperBound")
        if upper_bound is None:
            upper_bound = 0
        lower_bound = _optional_number_field(record, "lower_bound")
        if lower_bound is None:
            lower_bound = _optional_number_field(record, "lowerBound")
        if lower_bound is None:
            lower_bound = _optional_number_field(record, "LowerBound")
        if lower_bound is None:
            lower_bound = 0
        filled = _optional_boolean_field(record, "filled")
        if filled is None:
            filled = _optional_boolean_field(record, "Filled")
        if filled is None:
            filled = False
        out.append(
            {
                "index": _js_or_number(
                    _js_or_number(_number_field(record, "index"), _number_field(record, "Index")),
                    _js_or_number(_number_field(record, "start_index"), _number_field(record, "StartIndex")),
                ),
                "upper_bound": upper_bound,
                "lower_bound": lower_bound,
                "filled": filled,
            }
        )
    return out


def _normalize_position_manager_positions(values: Any) -> list[dict[str, Any]]:
    if not isinstance(values, list):
        return []
    out: list[dict[str, Any]] = []
    for value in values:
        record = _as_record(value)
        order_class = _optional_string_field(record, "order_class")
        if order_class is None:
            order_class = _optional_string_field(record, "orderClass")
        order_class2 = _optional_string_field(record, "orderClass")
        if order_class2 is None:
            order_class2 = _optional_string_field(record, "order_class")
        open_price = _optional_number_field(record, "openPrice")
        if open_price is None:
            open_price = _optional_number_field(record, "open_price")
        out.append(
            {
                "ticket": _optional_number_field(record, "ticket"),
                "symbol": _optional_string_field(record, "symbol"),
                "type": _optional_string_field(record, "type"),
                "order_class": order_class,
                "orderClass": order_class2,
                "lots": _optional_number_field(record, "lots"),
                "openPrice": open_price,
                "open_price": _optional_number_field(record, "open_price"),
                "sl": _optional_number_field(record, "sl"),
                "tp": _optional_number_field(record, "tp"),
                "profit": _optional_number_field(record, "profit"),
                "comment": _optional_string_field(record, "comment"),
                "strategy": _optional_string_field(record, "strategy"),
                "magic": _optional_number_field(record, "magic"),
            }
        )
    return out


def _normalize_position_manager_states(values: Any) -> list[dict[str, Any]]:
    if not isinstance(values, list):
        return []
    out: list[dict[str, Any]] = []
    for value in values:
        record = _as_record(value)
        out.append(
            {
                "ticket": _number_field(record, "ticket"),
                "openTime": _optional_string_field(record, "openTime"),
                "open_time": _optional_string_field(record, "open_time"),
                "tp1Hit": _coalesce_bool(record, "tp1Hit", "tp1_hit"),
                "tp2Hit": _coalesce_bool(record, "tp2Hit", "tp2_hit"),
                "rsiTp75Triggered": _coalesce_bool(record, "rsiTp75Triggered", "rsi_tp75_triggered"),
                "beMoved": _coalesce_bool(record, "beMoved", "be_moved"),
                "maxProfitAtr": _coalesce_num(record, "maxProfitAtr", "max_profit_atr"),
                "beTriggerAtr": _coalesce_num(record, "beTriggerAtr", "be_trigger_atr"),
                "bestSl": _coalesce_num(record, "bestSl", "best_sl"),
                "addOnCount": _coalesce_num(record, "addOnCount", "add_on_count"),
                "lastAddOnTime": _optional_string_field(record, "lastAddOnTime"),
                "last_add_on_time": _optional_string_field(record, "last_add_on_time"),
                "lastAddOnPrice": _coalesce_num(record, "lastAddOnPrice", "last_add_on_price"),
                "groupId": _optional_string_field(record, "groupId"),
                "group_id": _optional_string_field(record, "group_id"),
                "groupAvgEntry": _coalesce_num(record, "groupAvgEntry", "group_avg_entry"),
                "groupBestSl": _coalesce_num(record, "groupBestSl", "group_best_sl"),
                "trailingClosed": _coalesce_bool(record, "trailingClosed", "trailing_closed"),
            }
        )
    return out


def _coalesce_bool(record: dict[str, Any], *keys: str) -> bool | None:
    for key in keys:
        value = _optional_boolean_field(record, key)
        if value is not None:
            return value
    return None


def _coalesce_num(record: dict[str, Any], *keys: str) -> float | None:
    return _coalesce_optional_number(record, *keys)


# ------------------------------------------------------------------ SMC / harmonic 上下文


def _build_replay_smc_context(
    h4: list[EnrichedReplayBar],
    h1: list[EnrichedReplayBar],
    m30: list[EnrichedReplayBar],
    m15: list[EnrichedReplayBar],
) -> ReplaySmcContext:
    ctx = _build_smc_context(h4, h1, m30, m15)
    return _replay_smc_from_core_context(ctx)


def _replay_smc_from_core_context(ctx: dict[str, Any]) -> ReplaySmcContext:
    return {
        "h4_breaks": ctx.get("h4Breaks"),
        "h4_sweeps": ctx.get("h4Sweeps"),
        "h4_obs": [_replay_order_block_from_core(ob) for ob in ctx.get("h4OBs") or []],
        "h1_breaks": ctx.get("h1Breaks"),
        "h1_sweeps": ctx.get("h1Sweeps"),
        "h1_obs": [_replay_order_block_from_core(ob) for ob in ctx.get("h1OBs") or []],
        "h1_short_obs": [_replay_order_block_from_core(ob) for ob in ctx.get("h1ShortOBs") or []],
        "h1_fvgs": [_replay_fvg_from_core(fvg) for fvg in ctx.get("h1FVGs") or []],
        "m30_breaks": ctx.get("m30Breaks"),
        "m30_sweeps": ctx.get("m30Sweeps"),
        "m30_obs": [_replay_order_block_from_core(ob) for ob in ctx.get("m30OBs") or []],
        "m30_fvgs": [_replay_fvg_from_core(fvg) for fvg in ctx.get("m30FVGs") or []],
        "m15_breaks": ctx.get("m15Breaks"),
        "m15_sweeps": ctx.get("m15Sweeps"),
        "m15_obs": [_replay_order_block_from_core(ob) for ob in ctx.get("m15OBs") or []],
        "m15_fvgs": [_replay_fvg_from_core(fvg) for fvg in ctx.get("m15FVGs") or []],
    }


def _replay_harmonic_from_core_context(ctx: dict[str, Any]) -> ReplayHarmonicContext:
    active = ctx.get("activePattern")
    if active is None:
        return {"active_pattern": None}
    return {
        "active_pattern": {
            "type": active.get("type"),
            "direction": active.get("direction"),
            "score": active.get("score"),
        }
    }


def _replay_order_block_from_core(ob: Any) -> dict[str, Any]:
    return {
        "index": ob.get("index"),
        "side": ob.get("side"),
        "high": ob.get("high"),
        "low": ob.get("low"),
        "valid": ob.get("valid"),
    }


def _replay_fvg_from_core(fvg: Any) -> dict[str, Any]:
    return {
        "index": fvg.get("startIndex"),
        "upper_bound": fvg.get("upperBound"),
        "lower_bound": fvg.get("lowerBound"),
        "filled": fvg.get("filled"),
    }


# ------------------------------------------------------------------ 指标富化


def _rolling_volume_sma(volumes: list[float], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(volumes)
    total = 0.0
    for index, volume in enumerate(volumes):
        total += volume
        if index >= period:
            total -= volumes[index - period]
        if index >= period - 1:
            mean = total / period
            out[index] = mean if mean > 0 else None
    return out


def _enrich_bars(bars: list[ReplayRawBar]) -> list[EnrichedReplayBar]:
    closes = [bar["close"] for bar in bars]
    highs = [bar["high"] for bar in bars]
    lows = [bar["low"] for bar in bars]
    volumes = [bar.get("volume") or 0 for bar in bars]
    ema20 = _ema(closes, 20)
    ema50 = _ema(closes, 50)
    atr14 = _atr(highs, lows, closes, 14)
    rsi14 = _rsi(closes, 14)
    macd_result = _macd(closes)
    adx14 = _adx(highs, lows, closes, 14)
    bb20 = _bollinger(closes, 20, 2)
    stoch14 = _stoch(highs, lows, closes, 14, 3)
    vol_sma20 = _rolling_volume_sma(volumes, 20)

    out: list[EnrichedReplayBar] = []
    for index, bar in enumerate(bars):
        fib_window = min(50, len(bars))
        start = max(0, index - fib_window)
        window_highs = highs[start : index + 1]
        window_lows = lows[start : index + 1]
        fib = _fibonacci(window_highs, window_lows, len(window_highs))
        if index > 0:
            pivot = _pivot_points(bars[index - 1]["high"], bars[index - 1]["low"], bars[index - 1]["close"])
        else:
            pivot = None
        out.append(
            {
                **bar,
                "ema20": bar.get("ema20") if bar.get("ema20") is not None else ema20[index],
                "ema50": bar.get("ema50") if bar.get("ema50") is not None else ema50[index],
                "atr": bar.get("atr") if bar.get("atr") is not None else atr14[index],
                "rsi": bar.get("rsi") if bar.get("rsi") is not None else rsi14[index],
                "macd_hist": bar.get("macd_hist")
                if bar.get("macd_hist") is not None
                else (bar.get("macdHist") if bar.get("macdHist") is not None else macd_result["histogram"][index]),
                "macdHist": bar.get("macdHist")
                if bar.get("macdHist") is not None
                else (bar.get("macd_hist") if bar.get("macd_hist") is not None else macd_result["histogram"][index]),
                "adx": bar.get("adx")
                if bar.get("adx") is not None
                else (bar.get("ADX") if bar.get("ADX") is not None else adx14[index]),
                "bb_upper": bar.get("bb_upper")
                if bar.get("bb_upper") is not None
                else (
                    bar.get("bbUpper")
                    if bar.get("bbUpper") is not None
                    else (bar.get("BBUpper") if bar.get("BBUpper") is not None else bb20["upper"][index])
                ),
                "bb_lower": bar.get("bb_lower")
                if bar.get("bb_lower") is not None
                else (
                    bar.get("bbLower")
                    if bar.get("bbLower") is not None
                    else (bar.get("BBLower") if bar.get("BBLower") is not None else bb20["lower"][index])
                ),
                "stoch_k": bar.get("stoch_k")
                if bar.get("stoch_k") is not None
                else (
                    bar.get("stochK")
                    if bar.get("stochK") is not None
                    else (
                        bar.get("StochK")
                        if bar.get("StochK") is not None
                        else _finite_or_neutral_stoch(stoch14["k"][index])
                    )
                ),
                "stochK": bar.get("stochK")
                if bar.get("stochK") is not None
                else (
                    bar.get("stoch_k")
                    if bar.get("stoch_k") is not None
                    else (
                        bar.get("StochK")
                        if bar.get("StochK") is not None
                        else _finite_or_neutral_stoch(stoch14["k"][index])
                    )
                ),
                "vol_sma": bar.get("vol_sma")
                if bar.get("vol_sma") is not None
                else (
                    bar.get("volSMA")
                    if bar.get("volSMA") is not None
                    else (bar.get("VolSMA") if bar.get("VolSMA") is not None else vol_sma20[index])
                ),
                "volSMA": bar.get("volSMA")
                if bar.get("volSMA") is not None
                else (
                    bar.get("vol_sma")
                    if bar.get("vol_sma") is not None
                    else (bar.get("VolSMA") if bar.get("VolSMA") is not None else vol_sma20[index])
                ),
                "fib382": bar.get("fib382")
                if bar.get("fib382") is not None
                else (
                    bar.get("fib_382")
                    if bar.get("fib_382") is not None
                    else (bar.get("Fib382") if bar.get("Fib382") is not None else fib["fib382"])
                ),
                "fib618": bar.get("fib618")
                if bar.get("fib618") is not None
                else (
                    bar.get("fib_618")
                    if bar.get("fib_618") is not None
                    else (bar.get("Fib618") if bar.get("Fib618") is not None else fib["fib618"])
                ),
                "fib786": bar.get("fib786")
                if bar.get("fib786") is not None
                else (
                    bar.get("fib_786")
                    if bar.get("fib_786") is not None
                    else (bar.get("Fib786") if bar.get("Fib786") is not None else fib["fib786"])
                ),
                "pp": bar.get("pp") if bar.get("pp") is not None else (pivot.get("pp") if pivot is not None else None),
                "r1": bar.get("r1") if bar.get("r1") is not None else (pivot.get("r1") if pivot is not None else None),
                "r2": bar.get("r2") if bar.get("r2") is not None else (pivot.get("r2") if pivot is not None else None),
                "s1": bar.get("s1") if bar.get("s1") is not None else (pivot.get("s1") if pivot is not None else None),
                "s2": bar.get("s2") if bar.get("s2") is not None else (pivot.get("s2") if pivot is not None else None),
            }
        )
    return out


# ------------------------------------------------------------------ 候选收集


def _collect_replay_candidates(
    h1: list[EnrichedReplayBar],
    h4: list[EnrichedReplayBar],
    m30: list[EnrichedReplayBar],
    m15: list[EnrichedReplayBar],
    m5: list[EnrichedReplayBar],
    m1: list[EnrichedReplayBar],
    price: float,
    smc: ReplaySmcContext | None,
    traditional_config: ReplayTraditionalConfig,
    momentum_config: MomentumScalpConfig,
    price_precision: int,
    symbol: str,
    positions: list[dict[str, Any]],
    ai_result: dict[str, Any] | None,
) -> list[ReplaySignal]:
    # NOTE: scale_in / momentum_scalp 策略禁用(镜像 TS 注释)
    candidates = [
        _evaluate_pullback_signal(h1, h4, price, price_precision, traditional_config),
        _evaluate_breakout_retest_signal(h1, price, price_precision, traditional_config),
        _evaluate_divergence_signal(h1, price, price_precision, traditional_config),
        _evaluate_counter_pullback_signal(h1, m30, m15, price, smc, price_precision, traditional_config),
        _evaluate_breakout_pyramid_signal(h1, m30, price, smc, price_precision, traditional_config, symbol),
    ]
    del momentum_config, m5, m1, positions, ai_result  # 保留签名对齐 TS(禁用策略参数)
    return [candidate for candidate in candidates if candidate is not None]


def _select_highest_score(candidates: list[ReplaySignal]) -> ReplaySignal | None:
    if not candidates:
        return None
    best = candidates[0]
    for candidate in candidates[1:]:
        if candidate["score"] > best["score"]:
            best = candidate
    return best


def _select_raw_candidate_for(signal: ReplaySignal | None, candidates: list[ReplaySignal]) -> ReplaySignal | None:
    if signal is None:
        return None
    for candidate in candidates:
        if candidate["strategy"] == signal["strategy"] and candidate["side"] == signal["side"]:
            return candidate
    return signal


def _with_all_strategies(signal: ReplaySignal, candidates: list[ReplaySignal]) -> ReplaySignal:
    return {
        **signal,
        "all_strategies": [
            {
                "strategy": candidate["strategy"],
                "side": candidate["side"],
                "score": candidate["score"],
                "entry": candidate["entry"],
                "stop_loss": candidate["stop_loss"],
            }
            for candidate in candidates
        ],
    }


# ------------------------------------------------------------------ H4 过滤


def _h4_adx_filter_mode() -> str:
    """镜像 h4AdxFilterMode:读环境变量 GB_H4_ADX_FILTER_MODE,soft 之外默认 hard。"""
    raw = os.environ.get("GB_H4_ADX_FILTER_MODE")
    return "soft" if raw is not None and raw.strip().lower() == "soft" else "hard"


def _h4_filter_decision(
    h4: list[EnrichedReplayBar], config: ReplayTraditionalConfig
) -> dict[str, Any]:
    if len(h4) < 50:
        return {"trend": "未知", "direction": "", "adx": 0}
    last = h4[-1]
    adx_value = last["adx"]
    if adx_value < config["h4ADXThreshold"]:
        return {"trend": "震荡", "direction": "BLOCK", "adx": adx_value}
    direction = ""
    consecutive = 0
    bars_to_check = min(config["h4RequireConsecutive"], len(h4))
    for index in range(len(h4) - 1, len(h4) - bars_to_check - 1, -1):
        bar = h4[index]
        if bar["ema20"] > bar["ema50"] and bar["close"] > bar["ema20"]:
            if direction == "" or direction == "BUY":
                direction = "BUY"
                consecutive += 1
        elif bar["ema20"] < bar["ema50"] and bar["close"] < bar["ema20"]:
            if direction == "" or direction == "SELL":
                direction = "SELL"
                consecutive += 1
        else:
            break
    if consecutive >= config["h4RequireConsecutive"]:
        return {
            "trend": "强多头" if direction == "BUY" else "强空头",
            "direction": direction,
            "adx": adx_value,
        }
    return {"trend": "趋势不明", "direction": "", "adx": adx_value}


def _apply_h4_filter_to_candidates(
    candidates: list[ReplaySignal],
    h4: list[EnrichedReplayBar],
    config: ReplayTraditionalConfig,
) -> dict[str, Any]:
    if not candidates:
        return {"candidates": candidates, "logs": []}
    filter_result = _h4_filter_decision(h4, config)
    if filter_result["direction"] == "BLOCK":
        if _h4_adx_filter_mode() == "soft":
            logs: list[ReplayLog] = [
                {
                    "level": "warn",
                    "strategy": "H4过滤",
                    "msg": (
                        f"H4=震荡(ADX={_format_fixed(filter_result['adx'], 1)}"
                        f"<{_format_fixed(config['h4ADXThreshold'], 0)}), 不做方向偏置, 后续由多周期共识扣分决定"
                    ),
                }
            ]
            return {"candidates": candidates, "logs": logs}
        logs = [
            {
                "level": "warn",
                "strategy": "H4过滤",
                "msg": (
                    f"H4=震荡(ADX={_format_fixed(filter_result['adx'], 1)}"
                    f"<{_format_fixed(config['h4ADXThreshold'], 0)}), 过滤掉 {len(candidates)} 个信号（震荡市禁入）"
                ),
            },
            {
                "level": "info",
                "strategy": "H4过滤",
                "msg": "H4趋势过滤后无信号",
            },
        ]
        return {"candidates": [], "logs": logs}
    if filter_result["direction"] == "":
        return {"candidates": candidates, "logs": []}
    kept = [candidate for candidate in candidates if candidate["side"] == filter_result["direction"]]
    filtered = len(candidates) - len(kept)
    if filtered == 0:
        return {"candidates": candidates, "logs": []}
    logs = [
        {
            "level": "warn",
            "strategy": "H4过滤",
            "msg": f"H4={filter_result['trend']},过滤掉 {filtered} 个逆势信号,保留 {len(kept)} 个",
        }
    ]
    if len(kept) == 0:
        logs.append({"level": "info", "strategy": "H4过滤", "msg": "H4趋势过滤后无信号"})
    return {"candidates": kept, "logs": logs}


# ------------------------------------------------------------------ 过滤链


def _apply_min_score_filter(signal: ReplaySignal | None, min_score: float) -> dict[str, Any]:
    if signal is None:
        return {"signal": signal, "logs": []}
    if signal["score"] >= min_score:
        return {"signal": signal, "logs": []}
    return {
        "signal": None,
        "logs": [
            {
                "level": "info",
                "strategy": "汇总",
                "msg": f"最优信号评分 {signal['score']} < 最低要求 {min_score},过滤",
            }
        ],
    }


def _apply_min_rr_filter(signal: ReplaySignal | None, min_rr: float) -> dict[str, Any]:
    if signal is None:
        return {"signal": signal, "logs": []}
    take_profit = _primary_signal_take_profit(signal)
    rr = _signal_risk_reward_detail({**signal, "tp1": take_profit, "tp2": None})
    if not rr["valid"]:
        return {
            "signal": None,
            "logs": [
                {
                    "level": "warn",
                    "strategy": "R:R过滤",
                    "msg": f"⚠️ 信号 R:R无效 拒绝: {rr['reason']} ⏭",
                }
            ],
        }
    if rr["value"] + 1e-12 < min_rr:
        return {
            "signal": None,
            "logs": [
                {
                    "level": "warn",
                    "strategy": "R:R过滤",
                    "msg": f"⚠️ 信号 R:R={_format_risk_reward(rr['value'])} < {_format_risk_reward(min_rr)} 拒绝 ⏭",
                }
            ],
        }
    return {"signal": signal, "logs": []}


def _apply_position_conflict_filter(
    signal: ReplaySignal | None, positions: list[dict[str, Any]]
) -> dict[str, Any]:
    if signal is None or not positions:
        return {"signal": signal, "logs": []}
    signal_strategy = signal.get("strategy") or ""
    relevant_positions = []
    for position in positions:
        pos_strategy = position.get("strategy") or ""
        if signal_strategy and pos_strategy:
            if pos_strategy == signal_strategy:
                relevant_positions.append(position)
        else:
            relevant_positions.append(position)

    for position in relevant_positions:
        open_price = position.get("openPrice")
        if open_price is None:
            open_price = position.get("open_price")
        if open_price is None:
            open_price = 0
        position_side = (position.get("type") or "").upper()
        if open_price <= 0 or (position_side != "BUY" and position_side != "SELL"):
            continue
        dist = abs(signal["entry"] - open_price)
        if position_side == signal["side"]:
            if dist < signal["atr"]:
                return {
                    "signal": None,
                    "logs": [
                        {
                            "level": "warn",
                            "strategy": "汇总",
                            "msg": (
                                f"防重复: 已有同向持仓 [{position.get('strategy') or '未知'}] @ "
                                f"{_format_fixed(open_price, 2)},距离 < 1.0 ATR"
                            ),
                        }
                    ],
                }
            continue
        if dist < signal["atr"] * 2:
            return {
                "signal": None,
                "logs": [
                    {
                        "level": "warn",
                        "strategy": "汇总",
                        "msg": (
                            f"防对冲: 已有反向持仓 [{position.get('strategy') or '未知'}] @ "
                            f"{_format_fixed(open_price, 2)},距离 < 2.0 ATR"
                        ),
                    }
                ],
            }
    return {"signal": signal, "logs": []}


def _apply_ai_stop_loss_override(
    signal: ReplaySignal | None, ai_result: dict[str, Any] | None
) -> dict[str, Any]:
    if signal is None or ai_result is None or ai_result.get("suggested_sl") is None or ai_result["suggested_sl"] <= 0:
        return {"signal": signal, "logs": []}
    ai_sl = ai_result["suggested_sl"]
    dist = abs(signal["entry"] - ai_sl)
    side_valid = (signal["side"] == "BUY" and ai_sl < signal["entry"]) or (
        signal["side"] == "SELL" and ai_sl > signal["entry"]
    )
    if not side_valid or dist < signal["atr"] * 0.3 or dist > signal["atr"] * 3:
        return {"signal": signal, "logs": []}
    original_sl = signal["stop_loss"]
    return {
        "signal": {
            **signal,
            "stop_loss": ai_sl,
            "all_strategies": [{**entry, "stop_loss": ai_sl} for entry in signal["all_strategies"]],
        },
        "logs": [
            {
                "level": "info",
                "strategy": "AI止损",
                "msg": f"🤖 AI止损覆盖: {_format_fixed(original_sl, 2)} → {_format_fixed(ai_sl, 2)} (基于支撑阻力位)",
            }
        ],
    }


def _apply_ai_take_profit_override(
    signal: ReplaySignal | None, ai_result: dict[str, Any] | None
) -> dict[str, Any]:
    if signal is None or ai_result is None or ai_result.get("suggested_tp") is None or ai_result["suggested_tp"] <= 0:
        return {"signal": signal, "logs": []}
    ai_tp = ai_result["suggested_tp"]
    dist = abs(ai_tp - signal["entry"])
    side_valid = (signal["side"] == "BUY" and ai_tp > signal["entry"]) or (
        signal["side"] == "SELL" and ai_tp < signal["entry"]
    )
    if not side_valid or dist < signal["atr"] * 0.3 or dist > signal["atr"] * 5:
        return {"signal": signal, "logs": []}
    original_tp1 = signal["tp1"]
    original_tp2 = signal["tp2"]
    return {
        "signal": {**signal, "tp1": ai_tp, "tp2": ai_tp},
        "logs": [
            {
                "level": "info",
                "strategy": "AI止盈",
                "msg": (
                    f"🤖 AI止盈覆盖: TP1={_format_fixed(original_tp1, 2)}→{_format_fixed(ai_tp, 2)}, "
                    f"TP2={_format_fixed(original_tp2, 2)}→{_format_fixed(ai_tp, 2)}"
                ),
            }
        ],
    }


# ------------------------------------------------------------------ 持仓命令


def _evaluate_replay_position_commands(
    snapshot: ReplaySnapshot, enriched_h1: list[EnrichedReplayBar], current_price: float
) -> dict[str, Any]:
    if len(snapshot.get("positions") or []) == 0:
        return {"commands": [], "states": []}
    if len(enriched_h1) < 5 or current_price <= 0:
        return {"commands": [], "states": None}
    current_atr = enriched_h1[-1].get("atr") or 0
    if current_atr <= 0 or math.isnan(current_atr):
        return {"commands": [], "states": None}
    account = snapshot.get("account") or {}
    result = _evaluate_position_manager_commands(
        {
            "now": snapshot.get("analysis_time"),
            "currentPrice": current_price,
            "currentAtr": current_atr,
            "avgAtr": _average_atr(enriched_h1),
            "h1Bars": enriched_h1,
            "m5Bars": snapshot["bars"].get("M5") or [],
            "m1Bars": snapshot["bars"].get("M1") or [],
            "positions": _normalize_position_manager_positions(snapshot.get("positions") or []),
            "states": _normalize_position_manager_states(snapshot.get("position_states") or []),
            "equity": account.get("equity") or 0,
        }
    )
    return {
        "commands": [_to_replay_position_command(advisory) for advisory in result["advisories"]],
        "states": result["nextStates"],
    }


def _average_atr(h1_bars: list[EnrichedReplayBar]) -> float:
    atr_values: list[float] = []
    for bar in h1_bars[-20:]:
        value = bar.get("atr")
        if value is not None and math.isfinite(value) and value > 0:
            atr_values.append(value)
    if not atr_values:
        return 0
    return sum(atr_values) / len(atr_values)


def _to_replay_position_command(advisory: dict[str, Any]) -> ReplayPositionCommand:
    action = advisory["action"]
    if action == "MODIFY":
        return {
            "action": "MODIFY",
            "ticket": advisory["ticket"],
            "new_sl": advisory["newSL"],
            "reason": advisory["reason"],
        }
    if action == "CANCEL_PENDING":
        return {
            "action": "CANCEL_PENDING",
            "ticket": advisory["ticket"],
            "reason": advisory["reason"],
        }
    command: ReplayPositionCommand = {
        "action": "CLOSE",
        "ticket": advisory["ticket"],
        "reason": advisory["reason"],
    }
    if "lots" in advisory:
        command["lots"] = advisory["lots"]
    return command


# ------------------------------------------------------------------ 日志


def _build_replay_logs(
    snapshot: ReplaySnapshot,
    h1: list[EnrichedReplayBar],
    h4: list[EnrichedReplayBar],
    m30: list[EnrichedReplayBar],
    m15: list[EnrichedReplayBar],
    m5: list[EnrichedReplayBar],
    m1: list[EnrichedReplayBar],
    price: float,
    raw_signal: ReplaySignal | None,
    boosted_signal: ReplaySignal | None,
    final_signal: ReplaySignal | None,
    h4_filter_logs: list[ReplayLog],
    min_score_filter_logs: list[ReplayLog],
    position_filter_logs: list[ReplayLog],
    ai_override_logs: list[ReplayLog],
    rr_filter_logs: list[ReplayLog],
    momentum_config: MomentumScalpConfig,
    traditional_config: ReplayTraditionalConfig,
    smc: ReplaySmcContext | None,
    counter_log_smc: ReplaySmcContext | None,
) -> list[ReplayLog]:
    if price <= 0:
        return []
    if not h1:
        return []
    logs: list[ReplayLog] = []
    logs.append(_market_log(h1, h4, price, traditional_config))
    logs.append(_pullback_log(h1, h4, price, traditional_config))
    logs.append(_breakout_retest_log(h1, price, raw_signal, traditional_config))
    logs.append(_divergence_log(h1, raw_signal, traditional_config))
    logs.extend(_counter_pullback_logs(counter_log_smc, h1, m30, m15, price, raw_signal))
    logs.append(_breakout_pyramid_log(h1, price, raw_signal, smc, traditional_config))
    logs.append(_scale_in_log(snapshot.get("positions") or []))
    logs.append(_momentum_scalp_log(m15, m5, m1, price, raw_signal, momentum_config))
    logs.extend(h4_filter_logs)
    logs.extend(min_score_filter_logs)
    logs.extend(position_filter_logs)
    logs.extend(ai_override_logs)
    logs.extend(rr_filter_logs)
    if raw_signal is not None and boosted_signal is not None and len(m15) >= 14:
        logs.append(_m15_confirmation_log(raw_signal, boosted_signal, m15, price))
    if final_signal is not None:
        logs.append(
            {
                "level": "signal",
                "strategy": "汇总",
                "msg": (
                    f"✅ 发出信号: {final_signal['side']} @ {_format_fixed(final_signal['entry'], 2)} | "
                    f"SL={_format_fixed(final_signal['stop_loss'], 2)} | 策略={final_signal['strategy']} | "
                    f"评分={final_signal['score']}"
                ),
            }
        )
    return logs


def _h4_trend_label(h4: list[EnrichedReplayBar], config: ReplayTraditionalConfig) -> str:
    if len(h4) < 20:
        return "未知"
    last = h4[-1]
    if last["adx"] < config["h4ADXThreshold"]:
        return "震荡"
    recent = h4[-config["h4RequireConsecutive"] :]
    if all(bar["ema20"] > bar["ema50"] for bar in recent):
        return "强多头"
    if all(bar["ema20"] < bar["ema50"] for bar in recent):
        return "强空头"
    return "趋势不明"


def _market_log(
    h1: list[EnrichedReplayBar], h4: list[EnrichedReplayBar], price: float, config: ReplayTraditionalConfig
) -> ReplayLog:
    last = h1[-1]
    h4_last = h4[-1] if h4 else {}
    h4_adx = h4_last.get("adx") or 0
    return {
        "level": "info",
        "strategy": "市场",
        "msg": (
            f"Price={_format_fixed(price, 2)} | ATR={_format_fixed(last['atr'], 2)} | "
            f"RSI={_format_fixed(last['rsi'], 1)} | ADX={_format_fixed(last['adx'], 1)} | "
            f"EMA趋势(H1)={'多头' if last['ema20'] > last['ema50'] else '空头'} | "
            f"H4={_h4_trend_label(h4, config)}(ADX={_format_fixed(h4_adx, 1)}) | "
            f"MACD柱={_format_fixed(last['macd_hist'], 2)}"
        ),
    }


def _is_near_ema20(h1: list[EnrichedReplayBar], threshold: float) -> bool:
    if len(h1) < 2:
        return False
    previous = h1[-2]
    last = h1[-1]
    return abs(previous["close"] - previous["ema20"]) < threshold and abs(last["close"] - last["ema20"]) < threshold


def _pullback_score(side: str, last: EnrichedReplayBar, near_ema: bool, config: ReplayTraditionalConfig) -> int:
    score = 5
    if side == "BUY" and last["macd_hist"] > 0:
        score += 1
    if side == "SELL" and last["macd_hist"] < 0:
        score += 1
    if side == "BUY" and last["rsi"] < 50:
        score += 1
    if side == "SELL" and last["rsi"] > 50:
        score += 1
    if last["adx"] > config["pullback"]["adxBonus"]:
        score += 1
    if near_ema:
        score += 1
    return min(score, 10)


def _build_pullback_signal(
    side: str,
    entry: float,
    atr_value: float,
    score: int,
    price_precision: int,
    config: ReplayTraditionalConfig,
) -> ReplaySignal:
    direction = 1 if side == "BUY" else -1
    stop_loss = _round_to_precision(entry - direction * atr_value * config["pullback"]["slAtr"], price_precision)
    oracle_atr = _round_to_significant_digits(atr_value, 16)
    return {
        "side": side,
        "entry": entry,
        "stop_loss": stop_loss,
        "tp1": _round_to_precision(entry + direction * atr_value * config["pullback"]["tp1Atr"], price_precision),
        "tp2": _round_to_precision(entry + direction * atr_value * config["pullback"]["tp2Atr"], price_precision),
        "score": score,
        "strategy": "pullback",
        "atr": oracle_atr,
        "all_strategies": [
            {
                "strategy": "pullback",
                "side": side,
                "score": score,
                "entry": entry,
                "stop_loss": stop_loss,
            }
        ],
    }


def _explicit_pullback_fib(last: EnrichedReplayBar) -> dict[str, float] | None:
    fib382 = last.get("fib382")
    fib618 = last.get("fib618")
    fib786 = last.get("fib786")
    if (
        fib382 is not None
        and math.isfinite(fib382)
        and fib618 is not None
        and math.isfinite(fib618)
        and fib786 is not None
        and math.isfinite(fib786)
    ):
        return {"fib382": fib382, "fib618": fib618, "fib786": fib786}
    return None


def _evaluate_pullback_fib_gate(
    side: str,
    last: EnrichedReplayBar,
    h4: list[EnrichedReplayBar],
    price: float,
    price_precision: int,
    config: ReplayTraditionalConfig,
) -> dict[str, Any]:
    if not config["pullbackFibEnabled"]:
        return {"scoreBonus": 0}
    fib = _explicit_pullback_fib(last)
    if fib is None:
        return {"scoreBonus": 0}
    if not h4:
        return {
            "scoreBonus": 0,
            "rejectLog": {"level": "info", "strategy": "pullback", "msg": "🌀 pullback+FIB: H4数据不足 ⏭"},
        }
    h4_last = h4[-1]
    fib_trend = "DOWN" if h4_last["ema20"] < h4_last["ema50"] else "UP"
    if (side == "BUY" and fib_trend != "UP") or (side == "SELL" and fib_trend != "DOWN"):
        return {
            "scoreBonus": 0,
            "rejectLog": {
                "level": "info",
                "strategy": "pullback",
                "msg": "🌀 pullback+FIB: 信号方向与H4趋势不一致 ⏭",
            },
        }
    if not _is_price_in_fib_zone(price, fib["fib382"], fib["fib618"], last["atr"], 0.5):
        return {
            "scoreBonus": 0,
            "rejectLog": {
                "level": "info",
                "strategy": "pullback",
                "msg": (
                    f"🌀 pullback+FIB: 价格 {_format_fixed(price, 2)} 不在回撤区 "
                    f"[{_format_fixed(fib['fib382'], 2)}-{_format_fixed(fib['fib618'], 2)}] ⏭"
                ),
            },
        }
    stop_loss = _round_to_precision(
        fib["fib786"] - last["atr"] * 0.5 if side == "BUY" else fib["fib786"] + last["atr"] * 0.5,
        price_precision,
    )
    stop_loss_dist_atr = abs(price - stop_loss) / last["atr"]
    if abs(price - stop_loss) > last["atr"] * config["pullbackFibMaxSLDistATR"]:
        return {
            "scoreBonus": 1,
            "rejectLog": {
                "level": "info",
                "strategy": "pullback",
                "msg": (
                    f"🌀 pullback+FIB: fib786 止损距离超限 ({_format_fixed(stop_loss_dist_atr, 2)} ATR > "
                    f"{config['pullbackFibMaxSLDistATR']}) 回退 ⏭"
                ),
            },
        }
    return {"scoreBonus": 1, "stopLoss": stop_loss}


def _signal_risk(side: str, entry: float, stop_loss: float) -> float | None:
    if not (math.isfinite(entry) and math.isfinite(stop_loss) and entry > 0 and stop_loss > 0):
        return None
    risk = entry - stop_loss if side == "BUY" else stop_loss - entry
    return risk if math.isfinite(risk) and risk > 0 else None


def _primary_signal_take_profit(signal: dict[str, Any]) -> float:
    tp2 = signal.get("tp2")
    if tp2 is None:
        tp2 = 0
    else:
        tp2 = float(tp2)
    tp1 = float(signal["tp1"])
    side = signal["side"]
    entry = signal["entry"]
    if tp2 > 0 and side == "BUY" and tp2 > entry and tp2 > tp1:
        return tp2
    if tp2 > 0 and side == "SELL" and tp2 < entry and tp2 < tp1:
        return tp2
    return tp1


def _signal_risk_reward_detail(signal: dict[str, Any]) -> dict[str, Any]:
    entry = signal["entry"]
    stop_loss = signal["stop_loss"]
    take_profit = _primary_signal_take_profit(signal)
    if not (math.isfinite(entry) and math.isfinite(stop_loss) and math.isfinite(take_profit)):
        return {"valid": False, "reason": "non_finite"}
    if entry <= 0 or stop_loss <= 0 or take_profit <= 0:
        return {"valid": False, "reason": "non_positive"}
    risk = entry - stop_loss if signal["side"] == "BUY" else stop_loss - entry
    if not math.isfinite(risk) or risk <= 0:
        return {"valid": False, "reason": "invalid_risk"}
    reward = take_profit - entry if signal["side"] == "BUY" else entry - take_profit
    if not math.isfinite(reward) or reward <= 0:
        return {"valid": False, "reason": "invalid_reward"}
    value = reward / risk
    if not math.isfinite(value):
        return {"valid": False, "reason": "non_finite"}
    return {"valid": True, "value": value}


def _signal_risk_reward(side: str, entry: float, stop_loss: float, take_profit: float) -> float | None:
    detail = _signal_risk_reward_detail(
        {"side": side, "entry": entry, "stop_loss": stop_loss, "tp1": take_profit, "tp2": None}
    )
    return detail["value"] if detail["valid"] else None


def _pullback_fib_lifted_take_profit(
    side: str,
    price: float,
    stop_loss: float,
    current_tp: float,
    last: EnrichedReplayBar,
    price_precision: int,
    min_rr: float,
) -> float | None:
    risk = _signal_risk(side, price, stop_loss)
    next_level = _nearest_pullback_fib_take_profit_level(side, last, price)
    if risk is None or next_level is None:
        return None
    min_reward_tp = price + risk * min_rr if side == "BUY" else price - risk * min_rr
    candidate_tp = min(next_level, min_reward_tp) if side == "BUY" else max(next_level, min_reward_tp)
    rounded_tp = _round_to_precision(candidate_tp, price_precision)
    improves_tp = rounded_tp > current_tp if side == "BUY" else rounded_tp < current_tp
    side_valid = rounded_tp > price if side == "BUY" else rounded_tp < price
    lifted_rr = _signal_risk_reward(side, price, stop_loss, rounded_tp)
    if not improves_tp or not side_valid or lifted_rr is None or lifted_rr < min_rr:
        return None
    return rounded_tp


def _nearest_pullback_fib_take_profit_level(side: str, last: EnrichedReplayBar, price: float) -> float | None:
    if side == "BUY":
        levels = [last.get("ema20"), last.get("bb_upper"), last.get("fib382"), last.get("r1")]
    else:
        levels = [last.get("ema20"), last.get("bb_lower"), last.get("fib618"), last.get("fib786"), last.get("s1")]
    candidates: list[float] = []
    for level in levels:
        if level is None or not math.isfinite(level) or level <= 0:
            continue
        if side == "BUY" and level <= price:
            continue
        if side == "SELL" and level >= price:
            continue
        candidates.append(level)
    if not candidates:
        return None
    best = candidates[0]
    for level in candidates[1:]:
        if abs(level - price) < abs(best - price):
            best = level
    return best


def _apply_pullback_fib_gate_to_signal(
    signal: ReplaySignal,
    fib_gate: dict[str, Any],
    side: str,
    last: EnrichedReplayBar,
    price: float,
    price_precision: int,
    config: ReplayTraditionalConfig,
) -> ReplaySignal | None:
    if fib_gate.get("rejectLog") is not None and fib_gate.get("scoreBonus", 0) <= 0:
        return None
    if fib_gate.get("scoreBonus", 0) > 0:
        signal["score"] = min(signal["score"] + fib_gate["scoreBonus"], 10)
        signal["all_strategies"][0]["score"] = signal["score"]
    if fib_gate.get("stopLoss") is None:
        return signal
    skip_fib_stop_loss = False
    rr = _signal_risk_reward(side, price, fib_gate["stopLoss"], signal["tp1"])
    if rr is None or rr < config["pullbackFibMinRR"]:
        lifted_tp = _pullback_fib_lifted_take_profit(
            side, price, fib_gate["stopLoss"], signal["tp1"], last, price_precision, config["pullbackFibMinRR"]
        )
        if lifted_tp is None:
            skip_fib_stop_loss = True
        else:
            signal["tp1"] = lifted_tp
            if side == "BUY" and signal["tp2"] < signal["tp1"]:
                signal["tp2"] = signal["tp1"]
            if side == "SELL" and signal["tp2"] > signal["tp1"]:
                signal["tp2"] = signal["tp1"]
    if not skip_fib_stop_loss:
        signal["stop_loss"] = fib_gate["stopLoss"]
        signal["all_strategies"][0]["stop_loss"] = signal["stop_loss"]
    return signal


def _pullback_log(
    h1: list[EnrichedReplayBar], h4: list[EnrichedReplayBar], price: float, config: ReplayTraditionalConfig
) -> ReplayLog:
    last = h1[-1]
    dist = abs(price - last["ema20"])
    threshold = last["atr"] * config["pullback"]["distAtr"]
    near_ema = _is_near_ema20(h1, threshold)
    name = "趋势回调"

    if last["adx"] < config["pullback"]["minAdx"]:
        return {
            "level": "info",
            "strategy": name,
            "msg": (
                f"ADX={_format_fixed(last['adx'], 1)} < {_format_fixed(config['pullback']['minAdx'], 0)},"
                "趋势不明显 ⏭"
            ),
        }
    if last["ema20"] > last["ema50"] and price > last["ema50"]:
        if not near_ema and dist >= threshold:
            return {
                "level": "info",
                "strategy": name,
                "msg": f"多头趋势 | 价格距EMA20={_format_fixed(dist, 2)} > {_format_fixed(threshold, 2)},未回调到位 ⏭",
            }
        if last["rsi"] >= config["pullback"]["rsiOverbought"]:
            return {
                "level": "info",
                "strategy": name,
                "msg": (
                    f"多头趋势 | RSI={_format_fixed(last['rsi'], 1)} ≥ "
                    f"{_format_fixed(config['pullback']['rsiOverbought'], 0)},超买 ⏭"
                ),
            }
        fib_gate = _evaluate_pullback_fib_gate("BUY", last, h4, price, 2, config)
        if fib_gate.get("rejectLog") is not None:
            return fib_gate["rejectLog"]
        score = min(_pullback_score("BUY", last, near_ema, config) + fib_gate["scoreBonus"], 10)
        details: list[str] = []
        if last["macd_hist"] > 0:
            details.append("MACD柱>0")
        if last["rsi"] < 50:
            details.append(f"RSI={_format_fixed(last['rsi'], 1)}<50")
        if last["adx"] > config["pullback"]["adxBonus"]:
            details.append(f"ADX={_format_fixed(last['adx'], 1)}>{_format_fixed(config['pullback']['adxBonus'], 0)}")
        if near_ema:
            details.append("连续2根回调到位")
        return {
            "level": "signal",
            "strategy": name,
            "msg": f"🟢 BUY 评分={score} | EMA20回调 dist={_format_fixed(dist, 2)} | {' | '.join(details)}",
        }

    if last["ema20"] < last["ema50"] and price < last["ema50"]:
        if not near_ema and dist >= threshold:
            return {
                "level": "info",
                "strategy": name,
                "msg": f"空头趋势 | 价格距EMA20={_format_fixed(dist, 2)} > {_format_fixed(threshold, 2)},未回调到位 ⏭",
            }
        if last["rsi"] <= config["pullback"]["rsiOversold"]:
            return {
                "level": "info",
                "strategy": name,
                "msg": (
                    f"空头趋势 | RSI={_format_fixed(last['rsi'], 1)} ≤ "
                    f"{_format_fixed(config['pullback']['rsiOversold'], 0)},超卖 ⏭"
                ),
            }
        fib_gate = _evaluate_pullback_fib_gate("SELL", last, h4, price, 2, config)
        if fib_gate.get("rejectLog") is not None:
            return fib_gate["rejectLog"]
        score = min(_pullback_score("SELL", last, near_ema, config) + fib_gate["scoreBonus"], 10)
        details = []
        if last["macd_hist"] < 0:
            details.append("MACD柱<0")
        if last["rsi"] > 50:
            details.append(f"RSI={_format_fixed(last['rsi'], 1)}>50")
        if last["adx"] > config["pullback"]["adxBonus"]:
            details.append(f"ADX={_format_fixed(last['adx'], 1)}>{_format_fixed(config['pullback']['adxBonus'], 0)}")
        if near_ema:
            details.append("连续2根回调到位")
        return {
            "level": "signal",
            "strategy": name,
            "msg": f"🔴 SELL 评分={score} | EMA20回调 dist={_format_fixed(dist, 2)} | {' | '.join(details)}",
        }

    return {
        "level": "info",
        "strategy": name,
        "msg": (
            f"EMA20={_format_fixed(last['ema20'], 2)} vs EMA50={_format_fixed(last['ema50'], 2)} | "
            f"价格={_format_fixed(price, 2)} 不符合回调条件 ⏭"
        ),
    }


# ------------------------------------------------------------------ 突破回踩


def _breakout_retest_levels(
    h1: list[EnrichedReplayBar], config: ReplayTraditionalConfig
) -> dict[str, float]:
    recent = h1[len(h1) - config["breakoutRetest"]["lookback"] - 5 : len(h1) - 5]
    last5 = h1[-5:]
    return {
        "resistance": max(bar["high"] for bar in recent),
        "support": min(bar["low"] for bar in recent),
        "last5High": max(bar["high"] for bar in last5),
        "last5Low": min(bar["low"] for bar in last5),
    }


def _count_breakout_retest_touches(
    h1: list[EnrichedReplayBar],
    side: str,
    resistance: float,
    support: float,
    threshold: float,
    config: ReplayTraditionalConfig,
) -> int:
    count = 0
    for bar in h1[-config["breakoutRetest"]["confirmWindow"] :]:
        if side == "BUY" and abs(bar["low"] - resistance) < threshold:
            count += 1
        if side == "SELL" and abs(bar["high"] - support) < threshold:
            count += 1
    return count


def _breakout_retest_score(side: str, last: EnrichedReplayBar, touch_count: int) -> int:
    score = 5
    volume = last.get("volume") or 0
    vol_sma = last.get("vol_sma") or 0
    if vol_sma > 0 and volume > 1.5 * vol_sma:
        score += 1
    if (side == "BUY" and last["macd_hist"] > 0) or (side == "SELL" and last["macd_hist"] < 0):
        score += 1
    if last["adx"] > 20:
        score += 1
    if (side == "BUY" and last["rsi"] > 50) or (side == "SELL" and last["rsi"] < 50):
        score += 1
    if side == "BUY" and touch_count >= 2:
        score += 1
    return min(score, 10)


def _breakout_retest_details(side: str, last: EnrichedReplayBar, touch_count: int) -> list[str]:
    details: list[str] = []
    volume = last.get("volume") or 0
    vol_sma = last.get("vol_sma") or 0
    if vol_sma > 0 and volume > 1.5 * vol_sma:
        details.append("成交量确认")
    if side == "BUY" and last["macd_hist"] > 0:
        details.append("MACD柱>0")
    if side == "SELL" and last["macd_hist"] < 0:
        details.append("MACD柱<0")
    if last["adx"] > 20:
        details.append(f"ADX={_format_fixed(last['adx'], 1)}")
    if side == "BUY" and last["rsi"] > 50:
        details.append(f"RSI={_format_fixed(last['rsi'], 1)}")
    if side == "SELL" and last["rsi"] < 50:
        details.append(f"RSI={_format_fixed(last['rsi'], 1)}")
    if side == "BUY" and touch_count >= 2:
        details.append(f"回踩确认{touch_count}根")
    return details


def _build_breakout_retest_signal(
    side: str,
    entry: float,
    atr_value: float,
    broken_level: float,
    score: int,
    price_precision: int,
    config: ReplayTraditionalConfig,
) -> ReplaySignal:
    direction = 1 if side == "BUY" else -1
    stop_loss = _round_to_precision(
        broken_level - direction * atr_value * config["breakoutRetest"]["slAtr"], price_precision
    )
    return {
        "side": side,
        "entry": entry,
        "stop_loss": stop_loss,
        "tp1": _round_to_precision(
            entry + direction * atr_value * config["breakoutRetest"]["tp1Atr"], price_precision
        ),
        "tp2": _round_to_precision(
            entry + direction * atr_value * config["breakoutRetest"]["tp2Atr"], price_precision
        ),
        "score": score,
        "strategy": "breakout_retest",
        "atr": _round_to_significant_digits(atr_value, 16),
        "all_strategies": [
            {
                "strategy": "breakout_retest",
                "side": side,
                "score": score,
                "entry": entry,
                "stop_loss": stop_loss,
            }
        ],
    }


def _apply_pick_sltp(
    signal: ReplaySignal,
    last: EnrichedReplayBar,
    price_precision: int,
    config: ReplayTraditionalConfig,
) -> ReplaySignal:
    sr = pick_sltp(signal["side"], signal["entry"], last, signal["atr"], price_precision, config)
    if not sr["usedSR"]:
        return signal
    return {
        **signal,
        "stop_loss": sr["sl"],
        "tp1": sr["tp1"],
        "tp2": sr["tp2"],
        "all_strategies": [{**entry, "stop_loss": sr["sl"]} for entry in signal["all_strategies"]],
    }


def _evaluate_breakout_retest_signal(
    h1: list[EnrichedReplayBar],
    price: float,
    price_precision: int,
    config: ReplayTraditionalConfig,
) -> ReplaySignal | None:
    if len(h1) < config["breakoutRetest"]["lookback"] + 5 or price <= 0:
        return None
    last = h1[-1]
    atr_value = last["atr"]
    if atr_value <= 0 or math.isnan(atr_value):
        return None
    levels = _breakout_retest_levels(h1, config)
    resistance = levels["resistance"]
    support = levels["support"]
    last5_high = levels["last5High"]
    last5_low = levels["last5Low"]
    threshold = atr_value * config["breakoutRetest"]["distAtr"]
    broke_up = last5_high > resistance
    broke_down = last5_low < support
    touch_count = _count_breakout_retest_touches(
        h1, "BUY" if broke_up else "SELL", resistance, support, threshold, config
    )
    if broke_up:
        dist = abs(price - resistance)
        if dist < threshold and touch_count >= 1:
            return _apply_pick_sltp(
                _build_breakout_retest_signal(
                    "BUY",
                    price,
                    atr_value,
                    resistance,
                    _breakout_retest_score("BUY", last, touch_count),
                    price_precision,
                    config
                ),
                last,
                price_precision,
                config,
            )
    if broke_down:
        dist = abs(price - support)
        if dist < threshold and touch_count >= 1:
            return _apply_pick_sltp(
                _build_breakout_retest_signal(
                    "SELL",
                    price,
                    atr_value,
                    support,
                    _breakout_retest_score("SELL", last, touch_count),
                    price_precision,
                    config
                ),
                last,
                price_precision,
                config,
            )
    return None


def _breakout_retest_log(
    h1: list[EnrichedReplayBar],
    price: float,
    signal: ReplaySignal | None,
    config: ReplayTraditionalConfig,
) -> ReplayLog:
    name = "突破回踩"
    lookback = config["breakoutRetest"]["lookback"]
    if len(h1) < lookback + 5:
        return {"level": "info", "strategy": name, "msg": f"数据不足 {len(h1)}/{lookback + 5} ⏭"}
    recent = h1[len(h1) - lookback - 5 : len(h1) - 5]
    last5 = h1[-5:]
    resistance = max(bar["high"] for bar in recent)
    support = min(bar["low"] for bar in recent)
    broke_up = max(bar["high"] for bar in last5) > resistance
    broke_down = min(bar["low"] for bar in last5) < support
    threshold = h1[-1]["atr"] * config["breakoutRetest"]["distAtr"]
    dist_res = abs(price - resistance)
    dist_sup = abs(price - support)

    if signal is not None and signal["strategy"] == "breakout_retest":
        last = h1[-1]
        detail = _breakout_retest_details(
            signal["side"],
            last,
            _count_breakout_retest_touches(h1, signal["side"], resistance, support, threshold, config),
        )
        if signal["side"] == "BUY":
            return {
                "level": "signal",
                "strategy": name,
                "msg": (
                    f"🟢 BUY 评分={signal['score']} | 阻力位={_format_fixed(resistance, 2)} "
                    f"突破后回踩 dist={_format_fixed(dist_res, 2)} | {' | '.join(detail)}"
                ),
            }
        return {
            "level": "signal",
            "strategy": name,
            "msg": (
                f"🔴 SELL 评分={signal['score']} | 支撑位={_format_fixed(support, 2)} "
                f"突破后回踩 dist={_format_fixed(dist_sup, 2)} | {' | '.join(detail)}"
            ),
        }

    msg = f"阻力={_format_fixed(resistance, 2)} 支撑={_format_fixed(support, 2)}"
    if broke_up:
        msg += f" | 上破✓ 但回踩距离={_format_fixed(dist_res, 2)} > {_format_fixed(threshold, 2)}"
    elif broke_down:
        msg += f" | 下破✓ 但回踩距离={_format_fixed(dist_sup, 2)} > {_format_fixed(threshold, 2)}"
    else:
        msg += " | 未突破 ⏭"
    return {"level": "info", "strategy": name, "msg": msg}


# ------------------------------------------------------------------ RSI 背离


def _divergence_stats(h1: list[EnrichedReplayBar], config: ReplayTraditionalConfig) -> dict[str, float] | None:
    needed = config["divergence"]["windowRecent"] + config["divergence"]["windowPrev"]
    if len(h1) < needed:
        return None
    recent = h1[-config["divergence"]["windowRecent"] :]
    previous = h1[-needed : -config["divergence"]["windowRecent"]]
    return {
        "recentLow": min(bar["close"] for bar in recent),
        "previousLow": min(bar["close"] for bar in previous),
        "recentRsiLow": min(bar["rsi"] for bar in recent),
        "previousRsiLow": min(bar["rsi"] for bar in previous),
        "recentHigh": max(bar["close"] for bar in recent),
        "previousHigh": max(bar["close"] for bar in previous),
        "recentRsiHigh": max(bar["rsi"] for bar in recent),
        "previousRsiHigh": max(bar["rsi"] for bar in previous),
        "recentMacdLow": min(bar["macd_hist"] for bar in recent),
        "previousMacdLow": min(bar["macd_hist"] for bar in previous),
        "recentMacdHigh": max(bar["macd_hist"] for bar in recent),
        "previousMacdHigh": max(bar["macd_hist"] for bar in previous),
    }


def _divergence_buy_score(h1: list[EnrichedReplayBar], stats: dict[str, float]) -> int:
    score = 6
    last = h1[-1]
    previous = h1[-2]
    volume = last.get("volume") or 0
    vol_sma = last.get("vol_sma") or 0
    if stats["recentMacdLow"] > stats["previousMacdLow"]:
        score += 1
    elif last["macd_hist"] > previous["macd_hist"]:
        score += 1
    if vol_sma > 0 and volume > 0 and volume < 0.7 * vol_sma:
        score += 1
    if last["stoch_k"] < 20:
        score += 1
    return min(score, 10)


def _divergence_buy_details(h1: list[EnrichedReplayBar], stats: dict[str, float]) -> list[str]:
    details: list[str] = []
    last = h1[-1]
    previous = h1[-2]
    volume = last.get("volume") or 0
    vol_sma = last.get("vol_sma") or 0
    if stats["recentMacdLow"] > stats["previousMacdLow"]:
        details.append("MACD背离确认")
    elif last["macd_hist"] > previous["macd_hist"]:
        details.append("MACD改善")
    if vol_sma > 0 and volume > 0 and volume < 0.7 * vol_sma:
        details.append("成交量萎缩")
    if last["stoch_k"] < 20:
        details.append(f"StochK={_format_fixed(last['stoch_k'], 0)}")
    return details


def _divergence_sell_score(h1: list[EnrichedReplayBar], stats: dict[str, float]) -> int:
    score = 6
    last = h1[-1]
    previous = h1[-2]
    volume = last.get("volume") or 0
    vol_sma = last.get("vol_sma") or 0
    if stats["recentMacdHigh"] < stats["previousMacdHigh"]:
        score += 1
    elif last["macd_hist"] < previous["macd_hist"]:
        score += 1
    if vol_sma > 0 and volume > 0 and volume < 0.7 * vol_sma:
        score += 1
    if last["stoch_k"] > 80:
        score += 1
    return min(score, 10)


def _divergence_sell_details(h1: list[EnrichedReplayBar], stats: dict[str, float]) -> list[str]:
    details: list[str] = []
    last = h1[-1]
    previous = h1[-2]
    volume = last.get("volume") or 0
    vol_sma = last.get("vol_sma") or 0
    if stats["recentMacdHigh"] < stats["previousMacdHigh"]:
        details.append("MACD背离确认")
    elif last["macd_hist"] < previous["macd_hist"]:
        details.append("MACD恶化")
    if vol_sma > 0 and volume > 0 and volume < 0.7 * vol_sma:
        details.append("成交量萎缩")
    if last["stoch_k"] > 80:
        details.append(f"StochK={_format_fixed(last['stoch_k'], 0)}")
    return details


def _build_divergence_signal(
    side: str,
    entry: float,
    atr_value: float,
    pivot_close: float,
    score: int,
    price_precision: int,
    config: ReplayTraditionalConfig,
) -> ReplaySignal:
    direction = 1 if side == "BUY" else -1
    stop_loss = _round_to_precision(
        pivot_close - atr_value * config["divergence"]["slAtr"]
        if side == "BUY"
        else pivot_close + atr_value * config["divergence"]["slAtr"],
        price_precision,
    )
    return {
        "side": side,
        "entry": entry,
        "stop_loss": stop_loss,
        "tp1": _round_to_precision(
            entry + direction * atr_value * config["divergence"]["tp1Atr"], price_precision
        ),
        "tp2": _round_to_precision(
            entry + direction * atr_value * config["divergence"]["tp2Atr"], price_precision
        ),
        "score": score,
        "strategy": "divergence",
        "atr": _round_to_significant_digits(atr_value, 16),
        "all_strategies": [
            {
                "strategy": "divergence",
                "side": side,
                "score": score,
                "entry": entry,
                "stop_loss": stop_loss,
            }
        ],
    }


def _evaluate_divergence_signal(
    h1: list[EnrichedReplayBar],
    price: float,
    price_precision: int,
    config: ReplayTraditionalConfig,
) -> ReplaySignal | None:
    if len(h1) < 30 or price <= 0:
        return None
    last = h1[-1]
    atr_value = last["atr"]
    stats = _divergence_stats(h1, config)
    if stats is None or atr_value <= 0 or math.isnan(atr_value):
        return None
    bull_div = stats["recentLow"] < stats["previousLow"] and stats["recentRsiLow"] > stats["previousRsiLow"]
    if bull_div and last["rsi"] < config["divergence"]["rsiBullThresh"]:
        return _apply_pick_sltp(
            _build_divergence_signal(
                "BUY", price, atr_value, stats["recentLow"], _divergence_buy_score(h1, stats), price_precision, config
            ),
            last,
            price_precision,
            config,
        )
    bear_div = stats["recentHigh"] > stats["previousHigh"] and stats["recentRsiHigh"] < stats["previousRsiHigh"]
    if bear_div and last["rsi"] > config["divergence"]["rsiBearThresh"]:
        return _apply_pick_sltp(
            _build_divergence_signal(
                "SELL",
                price,
                atr_value,
                stats["recentHigh"],
                _divergence_sell_score(h1, stats),
                price_precision,
                config
            ),
            last,
            price_precision,
            config,
        )
    return None


def _divergence_log(
    h1: list[EnrichedReplayBar],
    signal: ReplaySignal | None,
    config: ReplayTraditionalConfig,
) -> ReplayLog:
    name = "RSI背离"
    if len(h1) < 30:
        return {"level": "info", "strategy": name, "msg": "数据不足 ⏭"}
    last = h1[-1]
    stats = _divergence_stats(h1, config)
    if stats is None:
        return {"level": "info", "strategy": name, "msg": "数据不足检测背离 ⏭"}
    bull_div = stats["recentLow"] < stats["previousLow"] and stats["recentRsiLow"] > stats["previousRsiLow"]
    bear_div = stats["recentHigh"] > stats["previousHigh"] and stats["recentRsiHigh"] < stats["previousRsiHigh"]
    if signal is not None and signal["strategy"] == "divergence" and signal["side"] == "BUY":
        return {
            "level": "signal",
            "strategy": name,
            "msg": (
                f"🟢 BUY 评分={signal['score']} | 看涨背离: 价格新低"
                f"{_format_fixed(stats['recentLow'], 2)}<{_format_fixed(stats['previousLow'], 2)} "
                f"RSI抬高{_format_fixed(stats['recentRsiLow'], 1)}>{_format_fixed(stats['previousRsiLow'], 1)} | "
                f"{' | '.join(_divergence_buy_details(h1, stats))}"
            ),
        }
    if signal is not None and signal["strategy"] == "divergence" and signal["side"] == "SELL":
        return {
            "level": "signal",
            "strategy": name,
            "msg": (
                f"🔴 SELL 评分={signal['score']} | 看跌背离: 价格新高"
                f"{_format_fixed(stats['recentHigh'], 2)}>{_format_fixed(stats['previousHigh'], 2)} "
                f"RSI降低{_format_fixed(stats['recentRsiHigh'], 1)}<{_format_fixed(stats['previousRsiHigh'], 1)} | "
                f"{' | '.join(_divergence_sell_details(h1, stats))}"
            ),
        }
    msg = f"RSI={_format_fixed(last['rsi'], 1)}"
    if bull_div:
        msg += (
            f" | 看涨背离检测到但RSI={_format_fixed(last['rsi'], 1)} ≥ "
            f"{_format_fixed(config['divergence']['rsiBullThresh'], 0)}"
        )
    elif bear_div:
        msg += (
            f" | 看跌背离检测到但RSI={_format_fixed(last['rsi'], 1)} ≤ "
            f"{_format_fixed(config['divergence']['rsiBearThresh'], 0)}"
        )
    else:
        msg += " | 无背离 ⏭"
    return {"level": "info", "strategy": name, "msg": msg}


# ------------------------------------------------------------------ 反转回调


def _select_counter_pullback_context(
    m30: list[EnrichedReplayBar], m15: list[EnrichedReplayBar]
) -> dict[str, Any] | None:
    if len(m30) >= 20:
        return {"bars": m30, "timeframe": "m30"}
    if len(m15) >= 20:
        return {"bars": m15, "timeframe": "m15"}
    return None


def _recent_counter_pullback_choch(
    smc: ReplaySmcContext | None, timeframe: str
) -> dict[str, Any] | None:
    if smc is None:
        return None
    breaks = smc.get(f"{timeframe}_breaks") or []
    for entry in reversed(breaks):
        if entry.get("type") == "CHoCH":
            return entry
    return None


def _recent_counter_pullback_sweep(
    smc: ReplaySmcContext | None, choch: dict[str, Any], timeframe: str
) -> dict[str, Any] | None:
    if smc is None:
        return None
    sweeps = smc.get(f"{timeframe}_sweeps") or []
    for entry in reversed(sweeps):
        if (choch["direction"] == "UP" and entry.get("side") == "BULL") or (
            choch["direction"] == "DOWN" and entry.get("side") == "BEAR"
        ):
            return entry
    return None


def _zone_near_price(low: float, high: float, price: float, threshold: float) -> bool:
    return high >= price - threshold and low <= price + threshold


def _has_counter_pullback_order_block(
    side: str,
    smc: ReplaySmcContext | None,
    timeframe: str | None,
    price: float,
    atr: float,
) -> bool:
    if price <= 0 or atr <= 0:
        return False
    order_blocks: list[Any] = []
    if smc is not None and timeframe == "m30":
        order_blocks = smc.get("m30_obs") or []
    elif smc is not None and timeframe == "m15":
        order_blocks = smc.get("m15_obs") or []
    return any(
        ob.get("side") == side and ob.get("valid") and _zone_near_price(ob.get("low", 0), ob.get("high", 0), price, atr)
        for ob in order_blocks
    )


def _has_counter_pullback_fvg(
    smc: ReplaySmcContext | None, timeframe: str | None, price: float, atr: float
) -> bool:
    if price <= 0 or atr <= 0:
        return False
    fvgs: list[Any] = []
    if smc is not None and timeframe == "m30":
        fvgs = smc.get("m30_fvgs") or []
    elif smc is not None and timeframe == "m15":
        fvgs = smc.get("m15_fvgs") or []
    return any(
        not fvg.get("filled")
        and _zone_near_price(fvg.get("lower_bound", 0), fvg.get("upper_bound", 0), price, atr)
        for fvg in fvgs
    )


def _counter_pullback_score(
    side: str,
    last: EnrichedReplayBar,
    smc: ReplaySmcContext | None,
    timeframe: str | None,
    price: float,
    atr: float,
) -> int:
    score = 5
    if side == "BUY" and last["rsi"] < 45:
        score += 1
    if side == "SELL" and last["rsi"] > 55:
        score += 1
    if _has_counter_pullback_order_block(side, smc, timeframe, price, atr):
        score += 1
    if side == "BUY" and last["macd_hist"] > 0:
        score += 1
    if side == "SELL" and last["macd_hist"] < 0:
        score += 1
    if _has_counter_pullback_fvg(smc, timeframe, price, atr):
        score += 1
    return min(score, 10)


def _counter_pullback_details(
    side: str,
    choch: dict[str, Any],
    sweep: dict[str, Any],
    last: EnrichedReplayBar,
    smc: ReplaySmcContext | None,
    timeframe: str | None,
    price: float,
    atr: float,
) -> list[str]:
    details = [f"CHoCH@{_js_number_string(choch['index'])}", f"Sweep@{_format_fixed(sweep['level'], 2)}"]
    if side == "BUY" and last["rsi"] < 45:
        details.append(f"RSI={_format_fixed(last['rsi'], 1)}")
    if side == "SELL" and last["rsi"] > 55:
        details.append(f"RSI={_format_fixed(last['rsi'], 1)}")
    if _has_counter_pullback_order_block(side, smc, timeframe, price, atr):
        details.append("OB确认")
    if side == "BUY" and last["macd_hist"] > 0:
        details.append("MACD>0")
    if side == "SELL" and last["macd_hist"] < 0:
        details.append("MACD<0")
    if _has_counter_pullback_fvg(smc, timeframe, price, atr):
        details.append("FVG确认")
    return details


def _build_counter_pullback_signal(
    side: str,
    entry: float,
    atr_value: float,
    sweep_level: float,
    score: int,
    price_precision: int,
) -> ReplaySignal:
    direction = 1 if side == "BUY" else -1
    stop_loss = _round_to_precision(sweep_level - direction * atr_value * 0.5, price_precision)
    if (side == "BUY" and stop_loss >= entry) or (side == "SELL" and stop_loss <= entry):
        stop_loss = _round_to_precision(entry - direction * atr_value * 1.5, price_precision)
    return {
        "side": side,
        "entry": entry,
        "stop_loss": stop_loss,
        "tp1": _round_to_precision(entry + direction * atr_value * 2, price_precision),
        "tp2": _round_to_precision(entry + direction * atr_value * 4, price_precision),
        "score": score,
        "strategy": "counter_pullback",
        "atr": _round_to_significant_digits(atr_value, 16),
        "all_strategies": [
            {
                "strategy": "counter_pullback",
                "side": side,
                "score": score,
                "entry": entry,
                "stop_loss": stop_loss,
            }
        ],
    }


def _evaluate_counter_pullback_signal(
    h1: list[EnrichedReplayBar],
    m30: list[EnrichedReplayBar],
    m15: list[EnrichedReplayBar],
    price: float,
    smc: ReplaySmcContext | None,
    price_precision: int,
    config: ReplayTraditionalConfig,
) -> ReplaySignal | None:
    context = _select_counter_pullback_context(m30, m15)
    if context is None or not h1 or price <= 0 or smc is None:
        return None
    signal_bars = context["bars"]
    timeframe = context["timeframe"]
    last = signal_bars[-1]
    atr_value = h1[-1]["atr"]
    if atr_value <= 0 or math.isnan(atr_value):
        return None
    recent_choch = _recent_counter_pullback_choch(smc, timeframe)
    if recent_choch is None:
        return None
    if len(signal_bars) - 1 - int(recent_choch["index"]) > 10:
        return None
    recent_sweep = _recent_counter_pullback_sweep(smc, recent_choch, timeframe)
    if recent_sweep is None:
        return None
    if recent_choch["direction"] == "UP" and recent_sweep.get("side") == "BULL":
        pullback_zone = recent_sweep["level"] + atr_value * 0.5
        if price > pullback_zone:
            return None
        return _apply_pick_sltp(
            _build_counter_pullback_signal(
                "BUY", price, atr_value, recent_sweep["level"],
                _counter_pullback_score("BUY", last, smc, timeframe, price, atr_value), price_precision,
            ),
            last,
            price_precision,
            config,
        )
    if recent_choch["direction"] == "DOWN" and recent_sweep.get("side") == "BEAR":
        pullback_zone = recent_sweep["level"] - atr_value * 0.5
        if price < pullback_zone:
            return None
        return _apply_pick_sltp(
            _build_counter_pullback_signal(
                "SELL", price, atr_value, recent_sweep["level"],
                _counter_pullback_score("SELL", last, smc, timeframe, price, atr_value), price_precision,
            ),
            last,
            price_precision,
            config,
        )
    return None


def _counter_pullback_logs(
    smc: ReplaySmcContext | None,
    h1: list[EnrichedReplayBar],
    m30: list[EnrichedReplayBar],
    m15: list[EnrichedReplayBar],
    price: float,
    signal: ReplaySignal | None,
) -> list[ReplayLog]:
    if smc is None and (signal is None or signal["strategy"] != "counter_pullback"):
        return []
    return [_counter_pullback_log(smc, h1, m30, m15, price, signal)]


def _counter_pullback_log(
    smc: ReplaySmcContext | None,
    h1: list[EnrichedReplayBar],
    m30: list[EnrichedReplayBar],
    m15: list[EnrichedReplayBar],
    price: float,
    signal: ReplaySignal | None,
) -> ReplayLog:
    name = "反转回调"
    context = _select_counter_pullback_context(m30, m15)
    if context is None or not h1:
        return {"level": "info", "strategy": name, "msg": "数据不足 ⏭"}
    signal_bars = context["bars"]
    timeframe = context["timeframe"]
    del signal  # 镜像 TS:该分支不使用 signal 参数
    recent_choch = _recent_counter_pullback_choch(smc, timeframe)
    if recent_choch is None:
        return {"level": "info", "strategy": name, "msg": "无CHoCH信号 ⏭"}
    last_bar_index = len(signal_bars) - 1
    if last_bar_index - int(recent_choch["index"]) > 10:
        return {
            "level": "info",
            "strategy": name,
            "msg": f"CHoCH在{last_bar_index - int(recent_choch['index'])}根前,太旧 ⏭",
        }
    recent_sweep = _recent_counter_pullback_sweep(smc, recent_choch, timeframe)
    if recent_sweep is None:
        return {"level": "info", "strategy": name, "msg": "CHoCH无对应Sweep确认 ⏭"}
    last = signal_bars[-1]
    atr_value = h1[-1]["atr"]
    timeframe_label = timeframe.upper()
    if recent_choch["direction"] == "UP" and recent_sweep.get("side") == "BULL":
        pullback_zone = recent_sweep["level"] + atr_value * 0.5
        if price > pullback_zone:
            return {
                "level": "info",
                "strategy": name,
                "msg": (
                    f"{timeframe_label} | 看涨CHoCH+Sweep | 价格{_format_fixed(price, 2)} > "
                    f"回调区{_format_fixed(pullback_zone, 2)},未到位 ⏭"
                ),
            }
        score = _counter_pullback_score("BUY", last, smc, timeframe, price, atr_value)
        cp_details = " | ".join(
            _counter_pullback_details("BUY", recent_choch, recent_sweep, last, smc, timeframe, price, atr_value)
        )
        return {
            "level": "signal",
            "strategy": name,
            "msg": (
                f"🟢 BUY 评分={score} | {timeframe_label} | 看涨反转回调: CHoCH↑+Sweep@"
                f"{_format_fixed(recent_sweep['level'], 2)} | {cp_details}"
            ),
        }
    if recent_choch["direction"] == "DOWN" and recent_sweep.get("side") == "BEAR":
        pullback_zone = recent_sweep["level"] - atr_value * 0.5
        if price < pullback_zone:
            return {
                "level": "info",
                "strategy": name,
                "msg": (
                    f"{timeframe_label} | 看跌CHoCH+Sweep | 价格{_format_fixed(price, 2)} < "
                    f"回调区{_format_fixed(pullback_zone, 2)},未到位 ⏭"
                ),
            }
        score = _counter_pullback_score("SELL", last, smc, timeframe, price, atr_value)
        cp_details = " | ".join(
            _counter_pullback_details("SELL", recent_choch, recent_sweep, last, smc, timeframe, price, atr_value)
        )
        return {
            "level": "signal",
            "strategy": name,
            "msg": (
                f"🔴 SELL 评分={score} | {timeframe_label} | 看跌反转回调: CHoCH↓+Sweep@"
                f"{_format_fixed(recent_sweep['level'], 2)} | {cp_details}"
            ),
        }
    return {"level": "info", "strategy": name, "msg": "无CHoCH+Sweep组合 ⏭"}


# ------------------------------------------------------------------ 突破加仓


def _breakout_pyramid_classify_break(direction: str, trend_direction: str) -> str:
    if trend_direction == "BULL":
        return "BOS" if direction == "UP" else "CHoCH"
    return "BOS" if direction == "DOWN" else "CHoCH"


def _breakout_pyramid_swing_points(
    bars: list[EnrichedReplayBar], left: int, right: int
) -> dict[str, list[dict[str, Any]]]:
    resolved_left = max(left, 1)
    resolved_right = max(right, 1)
    swing_highs: list[dict[str, Any]] = []
    swing_lows: list[dict[str, Any]] = []
    if len(bars) < resolved_left + resolved_right + 1:
        return {"swingHighs": swing_highs, "swingLows": swing_lows}
    for index in range(resolved_left, len(bars) - resolved_right):
        is_swing_high = True
        is_swing_low = True
        for other in range(index - resolved_left, index + resolved_right + 1):
            if other == index:
                continue
            if bars[other]["high"] >= bars[index]["high"]:
                is_swing_high = False
            if bars[other]["low"] <= bars[index]["low"]:
                is_swing_low = False
            if not is_swing_high and not is_swing_low:
                break
        if is_swing_high:
            swing_highs.append({"index": index, "price": bars[index]["high"]})
        if is_swing_low:
            swing_lows.append({"index": index, "price": bars[index]["low"]})
    return {"swingHighs": swing_highs, "swingLows": swing_lows}


def _breakout_pyramid_breaks_from_swings(
    bars: list[EnrichedReplayBar],
    start: int,
    swing_highs: list[dict[str, Any]],
    swing_lows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    high_cursor = 0
    low_cursor = 0
    for index in range(start, len(bars)):
        while high_cursor < len(swing_highs) and swing_highs[high_cursor]["index"] < index:
            high_cursor += 1
        while low_cursor < len(swing_lows) and swing_lows[low_cursor]["index"] < index:
            low_cursor += 1
        if high_cursor > 0:
            level = swing_highs[high_cursor - 1]["price"]
            if bars[index]["close"] > level and (index == 0 or bars[index - 1]["close"] <= level):
                events.append({"index": index, "direction": "UP"})
        if low_cursor > 0:
            level = swing_lows[low_cursor - 1]["price"]
            if bars[index]["close"] < level and (index == 0 or bars[index - 1]["close"] >= level):
                events.append({"index": index, "direction": "DOWN"})
    return events


def _breakout_pyramid_breaks_from_recent_extremes(
    bars: list[EnrichedReplayBar], start: int
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    window_size = 5
    for index in range(start + window_size, len(bars)):
        recent_high = 0
        recent_low = float("inf")
        recent_high_index = -1
        recent_low_index = -1
        for lookback_index in range(index - window_size, index):
            if lookback_index < start:
                continue
            if bars[lookback_index]["high"] > recent_high:
                recent_high = bars[lookback_index]["high"]
                recent_high_index = lookback_index
            if bars[lookback_index]["low"] < recent_low:
                recent_low = bars[lookback_index]["low"]
                recent_low_index = lookback_index
        if recent_high_index >= 0 and bars[index]["close"] > recent_high and (
            index == 0 or bars[index - 1]["close"] <= recent_high
        ):
            events.append({"index": index, "direction": "UP"})
        if recent_low_index >= 0 and bars[index]["close"] < recent_low and (
            index == 0 or bars[index - 1]["close"] >= recent_low
        ):
            events.append({"index": index, "direction": "DOWN"})
    return events


def _breakout_pyramid_structure_breaks(
    bars: list[EnrichedReplayBar], lookback: int, trend_direction: str
) -> list[dict[str, Any]]:
    if len(bars) < 3:
        return []
    resolved_lookback = lookback if 0 < lookback <= len(bars) else len(bars)
    start = len(bars) - resolved_lookback
    swings = _breakout_pyramid_swing_points(bars[start:], 3, 3)
    swing_highs = swings["swingHighs"]
    swing_lows = swings["swingLows"]
    for swing in swing_highs:
        swing["index"] += start
    for swing in swing_lows:
        swing["index"] += start
    if swing_highs or swing_lows:
        swing_breaks = _breakout_pyramid_breaks_from_swings(bars, start, swing_highs, swing_lows)
        if not swing_highs or not swing_lows:
            seen = {f"{event['index']}-{event['direction']}" for event in swing_breaks}
            for event in _breakout_pyramid_breaks_from_recent_extremes(bars, start):
                key = f"{event['index']}-{event['direction']}"
                if key not in seen:
                    swing_breaks.append(event)
        return [
            event
            for event in swing_breaks
            if _breakout_pyramid_classify_break(event["direction"], trend_direction) == "BOS"
        ]
    return [
        event
        for event in _breakout_pyramid_breaks_from_recent_extremes(bars, start)
        if _breakout_pyramid_classify_break(event["direction"], trend_direction) == "BOS"
    ]


def _breakout_pyramid_directional_candle(
    bars: list[EnrichedReplayBar], before_index: int, start: int, bullish: bool
) -> int:
    resolved_before_index = min(before_index, len(bars))
    resolved_start = max(start, 0)
    for index in range(resolved_before_index - 1, resolved_start - 1, -1):
        body = abs(bars[index]["close"] - bars[index]["open"])
        range_ = bars[index]["high"] - bars[index]["low"]
        if range_ <= 0 or body <= range_ * 0.6:
            continue
        if bullish and bars[index]["close"] > bars[index]["open"]:
            return index
        if not bullish and bars[index]["close"] < bars[index]["open"]:
            return index
    return -1


def _breakout_pyramid_order_block_still_valid(
    bars: list[EnrichedReplayBar], ob: dict[str, Any]
) -> bool:
    for index in range(int(ob["index"]) + 1, len(bars)):
        if ob["side"] == "BUY" and bars[index]["close"] < ob["low"]:
            return False
        if ob["side"] == "SELL" and bars[index]["close"] > ob["high"]:
            return False
    return True


def _breakout_pyramid_continuation_order_blocks(
    bars: list[EnrichedReplayBar], side: str, lookback: int, trend_direction: str
) -> list[dict[str, Any]]:
    if not bars:
        return []
    resolved_lookback = lookback if 0 < lookback <= len(bars) else len(bars)
    start = len(bars) - resolved_lookback
    breaks = _breakout_pyramid_structure_breaks(bars, resolved_lookback, trend_direction)
    seen: set[int] = set()
    blocks: list[dict[str, Any]] = []
    for brk in reversed(breaks):
        ob_index = -1
        if side == "SELL" and brk["direction"] == "UP":
            ob_index = _breakout_pyramid_directional_candle(bars, int(brk["index"]), start, True)
        elif side == "BUY" and brk["direction"] == "DOWN":
            ob_index = _breakout_pyramid_directional_candle(bars, int(brk["index"]), start, False)
        if ob_index < 0 or ob_index in seen:
            continue
        seen.add(ob_index)
        block: dict[str, Any] = {
            "index": ob_index,
            "side": side,
            "high": bars[ob_index]["high"],
            "low": bars[ob_index]["low"],
            "valid": True,
        }
        block["valid"] = _breakout_pyramid_order_block_still_valid(bars, block)
        blocks.append(block)
    return blocks


def _breakout_pyramid_short_order_blocks(
    h1: list[EnrichedReplayBar],
    smc: ReplaySmcContext | None,
    ob_side: str,
    trend_direction: str,
) -> list[dict[str, Any]]:
    if smc is None:
        smc_blocks: list[dict[str, Any]] = []
    else:
        smc_blocks = [ob for ob in (smc.get("h1_short_obs") or []) if ob.get("side") == ob_side]
    return smc_blocks + _breakout_pyramid_continuation_order_blocks(h1, ob_side, 20, trend_direction)


def _breakout_pyramid_blocking_order_block(
    h1: list[EnrichedReplayBar], side: str, smc: ReplaySmcContext | None
) -> dict[str, Any] | None:
    last = h1[-1]
    if side == "BUY":
        for ob in _breakout_pyramid_short_order_blocks(h1, smc, "SELL", "BULL"):
            if ob["valid"] and ob["high"] > last["bb_upper"] and ob["high"] < last["bb_upper"] + last["atr"] * 2:
                return ob
        return None
    for ob in _breakout_pyramid_short_order_blocks(h1, smc, "BUY", "BEAR"):
        if ob["valid"] and ob["low"] < last["bb_lower"] and ob["low"] > last["bb_lower"] - last["atr"] * 2:
            return ob
    return None


def _breakout_pyramid_score(side: str, last: EnrichedReplayBar) -> int:
    score = 6
    volume = last.get("volume") or 0
    vol_sma = last.get("vol_sma") or 0
    if vol_sma > 0 and volume > 0 and volume > 1.5 * vol_sma:
        score += 1
    if last["adx"] > 30:
        score += 1
    if (side == "BUY" and 55 < last["rsi"] < 80) or (side == "SELL" and 20 < last["rsi"] < 45):
        score += 1
    if (side == "BUY" and last["macd_hist"] > 0) or (side == "SELL" and last["macd_hist"] < 0):
        score += 1
    return min(score, 10)


def _breakout_pyramid_details(side: str, last: EnrichedReplayBar) -> list[str]:
    details: list[str] = []
    volume = last.get("volume") or 0
    vol_sma = last.get("vol_sma") or 0
    if vol_sma > 0 and volume > 0 and volume > 1.5 * vol_sma:
        details.append("成交量确认")
    if last["adx"] > 30:
        details.append(f"ADX={_format_fixed(last['adx'], 1)}>30")
    if side == "BUY" and 55 < last["rsi"] < 80:
        details.append(f"RSI={_format_fixed(last['rsi'], 1)}")
    if side == "SELL" and 20 < last["rsi"] < 45:
        details.append(f"RSI={_format_fixed(last['rsi'], 1)}")
    if side == "BUY" and last["macd_hist"] > 0:
        details.append("MACD柱>0")
    if side == "SELL" and last["macd_hist"] < 0:
        details.append("MACD柱<0")
    return details


def _build_breakout_pyramid_signal(
    side: str,
    entry: float,
    atr_value: float,
    last: EnrichedReplayBar,
    score: int,
    price_precision: int,
    config: ReplayTraditionalConfig,
) -> ReplaySignal:
    direction = 1 if side == "BUY" else -1
    stop_loss = _round_to_precision(
        last["ema20"] - direction * atr_value * config["breakoutPyramid"]["slAtr"], price_precision
    )
    return {
        "side": side,
        "entry": entry,
        "stop_loss": stop_loss,
        "tp1": _round_to_precision(entry + direction * atr_value * 2, price_precision),
        "tp2": _round_to_precision(entry + direction * atr_value * 5, price_precision),
        "score": score,
        "strategy": "breakout_pyramid",
        "atr": _round_to_significant_digits(atr_value, 16),
        "all_strategies": [
            {
                "strategy": "breakout_pyramid",
                "side": side,
                "score": score,
                "entry": entry,
                "stop_loss": stop_loss,
            }
        ],
    }


def _apply_breakout_pyramid_m30_confirmation(
    symbol: str,
    side: str,
    bb_level: float,
    m30: list[EnrichedReplayBar],
    signal: ReplaySignal | None,
) -> ReplaySignal | None:
    if signal is None or symbol.strip() == "" or not m30:
        return signal
    result = confirm_breakout_pyramid(symbol, side, bb_level, m30, signal, "")
    return signal if result["confirmed"] else None


def _evaluate_breakout_pyramid_signal(
    h1: list[EnrichedReplayBar],
    m30: list[EnrichedReplayBar],
    price: float,
    smc: ReplaySmcContext | None,
    price_precision: int,
    config: ReplayTraditionalConfig,
    symbol: str,
) -> ReplaySignal | None:
    if len(h1) < 30 or price <= 0:
        return None
    last = h1[-1]
    atr_value = last["atr"]
    if last["adx"] < config["breakoutPyramid"]["minAdx"] or atr_value <= 0 or math.isnan(atr_value):
        return None
    if last["close"] > last["bb_upper"] and last["ema20"] > last["ema50"]:
        if _breakout_pyramid_blocking_order_block(h1, "BUY", smc) is not None:
            return None
        signal = _build_breakout_pyramid_signal(
            "BUY", price, atr_value, last, _breakout_pyramid_score("BUY", last), price_precision, config
        )
        return _apply_breakout_pyramid_m30_confirmation(symbol, "BUY", last["bb_upper"], m30, signal)
    if last["close"] < last["bb_lower"] and last["ema20"] < last["ema50"]:
        if _breakout_pyramid_blocking_order_block(h1, "SELL", smc) is not None:
            return None
        signal = _build_breakout_pyramid_signal(
            "SELL", price, atr_value, last, _breakout_pyramid_score("SELL", last), price_precision, config
        )
        return _apply_breakout_pyramid_m30_confirmation(symbol, "SELL", last["bb_lower"], m30, signal)
    return None


def _breakout_pyramid_log(
    h1: list[EnrichedReplayBar],
    price: float,
    signal: ReplaySignal | None,
    smc: ReplaySmcContext | None,
    config: ReplayTraditionalConfig,
) -> ReplayLog:
    name = "突破加仓"
    if len(h1) < 30:
        return {"level": "info", "strategy": name, "msg": "数据不足 ⏭"}
    last = h1[-1]
    if last["adx"] < config["breakoutPyramid"]["minAdx"]:
        return {
            "level": "info",
            "strategy": name,
            "msg": (
                f"ADX={_format_fixed(last['adx'], 1)} < "
                f"{_format_fixed(config['breakoutPyramid']['minAdx'], 0)},趋势不够强 ⏭"
            ),
        }
    if signal is not None and signal["strategy"] == "breakout_pyramid":
        details = _breakout_pyramid_details(signal["side"], last)
        if signal["side"] == "BUY":
            return {
                "level": "signal",
                "strategy": name,
                "msg": (
                    f"🟢 BUY 评分={signal['score']} | 收盘价突破布林上轨="
                    f"{_format_fixed(last['bb_upper'], 2)} | {' | '.join(details)}"
                ),
            }
        return {
            "level": "signal",
            "strategy": name,
            "msg": (
                f"🔴 SELL 评分={signal['score']} | 收盘价突破布林下轨="
                f"{_format_fixed(last['bb_lower'], 2)} | {' | '.join(details)}"
            ),
        }
    if last["close"] > last["bb_upper"] and last["ema20"] > last["ema50"]:
        blocked_ob = _breakout_pyramid_blocking_order_block(h1, "BUY", smc)
        if blocked_ob is not None:
            return {
                "level": "info",
                "strategy": name,
                "msg": (
                    f"前方有空头OB {_format_fixed(blocked_ob['high'], 2)} "
                    f"(距离{_format_fixed(blocked_ob['high'] - last['close'], 1)}点), 突破风险高 ⏭"
                ),
            }
    if last["close"] < last["bb_lower"] and last["ema20"] < last["ema50"]:
        blocked_ob = _breakout_pyramid_blocking_order_block(h1, "SELL", smc)
        if blocked_ob is not None:
            return {
                "level": "info",
                "strategy": name,
                "msg": (
                    f"前方有多头OB {_format_fixed(blocked_ob['low'], 2)} "
                    f"(距离{_format_fixed(last['close'] - blocked_ob['low'], 1)}点), 突破风险高 ⏭"
                ),
            }
    msg = (
        f"BB=[{_format_fixed(last['bb_lower'], 2)}, {_format_fixed(last['bb_upper'], 2)}] "
        f"Price={_format_fixed(price, 2)}"
    )
    if price > last["bb_upper"]:
        msg += " | 突破上轨但EMA20<EMA50趋势不一致"
    elif price < last["bb_lower"]:
        msg += " | 突破下轨但EMA20>EMA50趋势不一致"
    else:
        msg += " | 在通道内 ⏭"
    return {"level": "info", "strategy": name, "msg": msg}


# ------------------------------------------------------------------ 其它策略日志(禁用路径)


def _scale_in_log(positions: list[Any]) -> ReplayLog:
    if len(positions) == 0:
        return {"level": "info", "strategy": "浮亏加仓", "msg": "➕ 无同向浮亏持仓 ⏭"}
    return {"level": "info", "strategy": "浮亏加仓", "msg": "➕ 浮亏加仓未触发 ⏭"}


def _momentum_scalp_log(
    m15: list[EnrichedReplayBar],
    m5: list[EnrichedReplayBar],
    m1: list[EnrichedReplayBar],
    price: float,
    signal: ReplaySignal | None,
    momentum_config: MomentumScalpConfig,
) -> ReplayLog:
    del m15, m1, price, signal, momentum_config  # momentum_scalp 已禁用(镜像 TS 注释)
    if len(m5) < 12:
        return {"level": "info", "strategy": "动量剥头皮", "msg": f"M5数据不足: {len(m5)}/12 ⏭"}
    return {"level": "info", "strategy": "动量剥头皮", "msg": "动量剥头皮策略已禁用 ⏭"}


# ------------------------------------------------------------------ M15 确认 / 上下文加分


def _m15_confirmation_outcome(
    signal: ReplaySignal, m15: list[EnrichedReplayBar], price: float
) -> dict[str, Any]:
    last = m15[-1]
    confirmed = False
    detail = ""
    if signal["side"] == "BUY":
        confirmed = last["rsi"] > 0 and last["rsi"] < 40
        detail = f"M15确认: RSI={_format_fixed(last['rsi'], 1)}<40(多头)" if confirmed else (
            f"M15未确认: RSI={_format_fixed(last['rsi'], 1)}≥40"
        )
    else:
        confirmed = last["rsi"] > 0 and last["rsi"] > 60
        detail = f"M15确认: RSI={_format_fixed(last['rsi'], 1)}>60(空头)" if confirmed else (
            f"M15未确认: RSI={_format_fixed(last['rsi'], 1)}≤60"
        )
    fib382 = _optional_number_field(last, "fib382")
    fib618 = _optional_number_field(last, "fib618")
    if confirmed and fib382 is not None and abs(price - fib382) < last["atr"] * 0.5:
        detail += f" | 近Fib382={_format_fixed(fib382, 2)}"
    elif confirmed and fib618 is not None and abs(price - fib618) < last["atr"] * 0.5:
        detail += f" | 近Fib618={_format_fixed(fib618, 2)}"
    return {"confirmed": confirmed, "detail": detail}


def _apply_m15_confirmation_boost(
    signal: ReplaySignal | None, m15: list[EnrichedReplayBar], price: float
) -> ReplaySignal | None:
    if signal is None or len(m15) < 14:
        return signal
    outcome = _m15_confirmation_outcome(signal, m15, price)
    if not outcome["confirmed"]:
        return signal
    boosted_score = min(signal["score"] + 1, 10)
    return {
        **signal,
        "score": boosted_score,
        "all_strategies": [{**entry, "score": boosted_score} for entry in signal["all_strategies"]],
    }


def _calculate_harmonic_bonus(harmonic: ReplayHarmonicContext | None, side: str) -> int:
    pattern = None
    if harmonic is not None:
        pattern = harmonic.get("active_pattern")
    if pattern is None or pattern.get("score") is None or pattern["score"] < 30:
        return 0
    if _harmonic_direction_to_side(pattern.get("direction")) != side:
        return 0
    return 2 if pattern["score"] >= 70 else 1


def _harmonic_direction_to_side(direction: Any) -> str | None:
    if not isinstance(direction, str):
        return None
    normalized = direction.upper()
    if normalized in ("BUY", "BULL", "BULLISH"):
        return "BUY"
    if normalized in ("SELL", "BEAR", "BEARISH"):
        return "SELL"
    return None


def _apply_context_scoring_bonuses(
    signal: ReplaySignal,
    harmonic: ReplayHarmonicContext | None,
    smc: ReplaySmcContext | None,
    price: float,
    last_h1_index: int,
) -> ReplaySignal:
    boosted_score = signal["score"]
    harmonic_boost = _calculate_harmonic_bonus(harmonic, signal["side"])
    if harmonic_boost > 0:
        boosted_score = min(boosted_score + harmonic_boost, 10)
    smc_boost = calculate_smc_bonus(smc, signal["side"], price, signal["atr"], "h1", last_h1_index)
    if smc_boost > 0:
        boosted_score = min(boosted_score + smc_boost, 10)
    if boosted_score == signal["score"]:
        return signal
    return {
        **signal,
        "score": boosted_score,
        "all_strategies": [{**entry, "score": boosted_score} for entry in signal["all_strategies"]],
    }


# ------------------------------------------------------------------ 趋势评级


def _signal_side_to_trend_direction(side: str) -> str:
    return "BEAR" if side == "SELL" else "BULL"


def _timeframe_direction(bars: list[EnrichedReplayBar]) -> str:
    if not bars:
        return "NEUTRAL"
    last = bars[-1]
    if last["ema20"] > last["ema50"] and last["close"] > last["ema20"]:
        return "BULL"
    if last["ema20"] < last["ema50"] and last["close"] < last["ema20"]:
        return "BEAR"
    return "NEUTRAL"


def _trend_confidence(bars: list[EnrichedReplayBar]) -> float:
    if not bars:
        return 0
    last = bars[-1]
    direction = _timeframe_direction(bars)
    if direction == "NEUTRAL":
        return 0
    if last["adx"] < 20:
        return 0.3
    if last["adx"] <= 30:
        return 0.6
    return 0.9


def _trend_consensus(
    h4: list[EnrichedReplayBar], h1: list[EnrichedReplayBar], m30: list[EnrichedReplayBar]
) -> dict[str, Any]:
    h4_direction = _timeframe_direction(h4)
    h1_direction = _timeframe_direction(h1)
    m30_direction = _timeframe_direction(m30)
    h4_confidence = _trend_confidence(h4)
    h1_confidence = _trend_confidence(h1)
    m30_confidence = _trend_confidence(m30)
    h4_weight = 0.15
    h1_weight = 0.35
    m30_weight = 0.50
    h4_strength = h4_weight * h4_confidence
    h1_strength = h1_weight * h1_confidence
    m30_strength = m30_weight * m30_confidence
    bull_weight = (
        (h4_weight if h4_direction == "BULL" else 0)
        + (h1_weight if h1_direction == "BULL" else 0)
        + (m30_weight if m30_direction == "BULL" else 0)
    )
    bear_weight = (
        (h4_weight if h4_direction == "BEAR" else 0)
        + (h1_weight if h1_direction == "BEAR" else 0)
        + (m30_weight if m30_direction == "BEAR" else 0)
    )
    strength = h4_strength + h1_strength + m30_strength
    direction = "NEUTRAL"
    if bull_weight > bear_weight:
        direction = "BULL"
    elif bear_weight > bull_weight:
        direction = "BEAR"
    return {"direction": direction, "strength": strength, "h4Direction": h4_direction}


def _trend_rating(
    signal: ReplaySignal,
    h4: list[EnrichedReplayBar],
    h1: list[EnrichedReplayBar],
    m30: list[EnrichedReplayBar],
) -> dict[str, int]:
    consensus = _trend_consensus(h4, h1, m30)
    signal_direction = _signal_side_to_trend_direction(signal["side"])
    if (
        consensus["strength"] >= 0.3
        and consensus["direction"] != "NEUTRAL"
        and consensus["direction"] != signal_direction
    ):
        return {"penalty": 2}
    if consensus["strength"] >= 0.3:
        return {"penalty": 0}
    if consensus["h4Direction"] != "NEUTRAL" and consensus["h4Direction"] != signal_direction:
        return {"penalty": 2}
    return {"penalty": 1}


def _apply_trend_rating_penalty(
    signal: ReplaySignal | None,
    h4: list[EnrichedReplayBar],
    h1: list[EnrichedReplayBar],
    m30: list[EnrichedReplayBar],
) -> ReplaySignal | None:
    if signal is None:
        return None
    if not h4 and not m30:
        return signal
    rating = _trend_rating(signal, h4, h1, m30)
    if rating["penalty"] == 0:
        return signal
    next_score = signal["score"] - rating["penalty"]
    return {
        **signal,
        "score": next_score,
        "all_strategies": [{**entry, "score": next_score} for entry in signal["all_strategies"]],
    }


# ------------------------------------------------------------------ pullback 信号(生成)


def _evaluate_pullback_signal(
    h1: list[EnrichedReplayBar],
    h4: list[EnrichedReplayBar],
    price: float,
    price_precision: int,
    config: ReplayTraditionalConfig,
) -> ReplaySignal | None:
    if len(h1) < 50 or price <= 0:
        return None
    last = h1[-1]
    atr_value = last["atr"]
    if atr_value <= 0 or math.isnan(atr_value) or math.isnan(last["adx"]):
        return None
    if last["adx"] < config["pullback"]["minAdx"]:
        return None
    threshold = atr_value * config["pullback"]["distAtr"]
    near_ema = _is_near_ema20(h1, threshold)
    if last["ema20"] > last["ema50"] and price > last["ema50"]:
        dist = abs(price - last["ema20"])
        if not near_ema and dist >= threshold:
            return None
        if last["rsi"] >= config["pullback"]["rsiOverbought"]:
            return None
        signal = _apply_pick_sltp(
            _build_pullback_signal(
                "BUY", price, atr_value, _pullback_score("BUY", last, near_ema, config), price_precision, config
            ),
            last,
            price_precision,
            config,
        )
        fib_gate = _evaluate_pullback_fib_gate("BUY", last, h4, price, price_precision, config)
        return _apply_pullback_fib_gate_to_signal(signal, fib_gate, "BUY", last, price, price_precision, config)
    if last["ema20"] < last["ema50"] and price < last["ema50"]:
        dist = abs(price - last["ema20"])
        if not near_ema and dist >= threshold:
            return None
        if last["rsi"] <= config["pullback"]["rsiOversold"]:
            return None
        signal = _apply_pick_sltp(
            _build_pullback_signal(
                "SELL", price, atr_value, _pullback_score("SELL", last, near_ema, config), price_precision, config
            ),
            last,
            price_precision,
            config,
        )
        fib_gate = _evaluate_pullback_fib_gate("SELL", last, h4, price, price_precision, config)
        return _apply_pullback_fib_gate_to_signal(signal, fib_gate, "SELL", last, price, price_precision, config)
    return None


def _m15_confirmation_log(
    raw_signal: ReplaySignal,
    final_signal: ReplaySignal,
    m15: list[EnrichedReplayBar],
    price: float,
) -> ReplayLog:
    outcome = _m15_confirmation_outcome(raw_signal, m15, price)
    if outcome["confirmed"]:
        return {
            "level": "info",
            "strategy": "M15确认",
            "msg": f"✅ {raw_signal['strategy']} | {outcome['detail']} | 评分+1→{final_signal['score']}",
        }
    return {
        "level": "info",
        "strategy": "M15确认",
        "msg": f"⏭ {raw_signal['strategy']} | {outcome['detail']}",
    }
