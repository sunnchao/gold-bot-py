"""风控门槛与市况过滤(镜像 packages/trading-core/src/riskgate/riskgate.ts)。

全部按 TS 语义逐字移植:`??`/`||` 空值合并、Math.floor 下取整、UTC 时间窗口、
字符串 reason code 与 dict camelCase 键均与源文件一致;纯标准库,
输入输出统一使用 dict[str, Any]。
"""

from __future__ import annotations

import math
import re
from datetime import UTC, datetime
from typing import Any

__all__ = [
    "evaluate_market_filters",
    "evaluate_risk_gate",
]

# 风控常量(镜像 TS 模块级字面值)
default_max_tick_age_ms = 2 * 60 * 1000
default_max_risk_pct = 0.02
default_margin_use_pct = 0.5
market_filter_max_spread = 5.0
atr_expansion_ratio = 2.0
min_atr_history_for_filter = 10

# 可执行 / 仅审计模式(镜像 TS isExecutableMode / isAuditOnlyMode)
executable_modes = ("approve", "modify", "reduce", "close")
audit_only_modes = ("observe", "veto")

# 符号元数据(镜像 TS metadataFor 的 switch 字面值;未收录符号回退 XAUUSD)
_symbol_meta: dict[str, dict[str, Any]] = {
    "GBPUSD": {
        "symbol": "GBPUSD",
        "contractSize": 100000,
        "minLot": 0.01,
        "maxLot": 30,
        "lotStep": 0.01,
        "maxSpread": 80,
        "minSLDistance": 0.0005,
        "maxSLDistance": 0.05,
    },
    "USDCAD": {
        "symbol": "USDCAD",
        "contractSize": 100000,
        "minLot": 0.01,
        "maxLot": 30,
        "lotStep": 0.01,
        "maxSpread": 80,
        "minSLDistance": 0.0005,
        "maxSLDistance": 0.05,
    },
    "GBPJPY": {
        "symbol": "GBPJPY",
        "contractSize": 100000,
        "minLot": 0.01,
        "maxLot": 20,
        "lotStep": 0.01,
        "maxSpread": 80,
        "minSLDistance": 0.03,
        "maxSLDistance": 8,
    },
    "EURJPY": {
        "symbol": "EURJPY",
        "contractSize": 100000,
        "minLot": 0.01,
        "maxLot": 20,
        "lotStep": 0.01,
        "maxSpread": 80,
        "minSLDistance": 0.03,
        "maxSLDistance": 7,
    },
    "USDJPY": {
        "symbol": "USDJPY",
        "contractSize": 100000,
        "minLot": 0.01,
        "maxLot": 30,
        "lotStep": 0.01,
        "maxSpread": 80,
        "minSLDistance": 0.02,
        "maxSLDistance": 6,
    },
    "US100CASH": {
        "symbol": "US100CASH",
        "contractSize": 1,
        "minLot": 0.01,
        "maxLot": 20,
        "lotStep": 0.01,
        "maxSpread": 80,
        "minSLDistance": 10,
        "maxSLDistance": 3000,
    },
    "USOILCASH": {
        "symbol": "USOILCASH",
        "contractSize": 100,
        "minLot": 0.01,
        "maxLot": 30,
        "lotStep": 0.01,
        "maxSpread": 80,
        "minSLDistance": 0.05,
        "maxSLDistance": 10,
    },
    "UKOILCASH": {
        "symbol": "UKOILCASH",
        "contractSize": 100,
        "minLot": 0.01,
        "maxLot": 30,
        "lotStep": 0.01,
        "maxSpread": 80,
        "minSLDistance": 0.05,
        "maxSLDistance": 10,
    },
    "XAUUSD": {
        "symbol": "XAUUSD",
        "contractSize": 100,
        "minLot": 0.01,
        "maxLot": 50,
        "lotStep": 0.01,
        "maxSpread": 80,
        "minSLDistance": 0.5,
        "maxSLDistance": 100,
    },
}

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _get(mapping: Any, key: str, default: Any = None) -> Any:
    """镜像 TS `??`:字段缺失或为 None 时返回 default。"""
    if not isinstance(mapping, dict):
        return default
    value = mapping.get(key)
    return default if value is None else value


def _nullish_or(value: Any, default: Any) -> Any:
    return default if value is None else value


def _as_utc_dt(value: str | None) -> datetime:
    """解析 ISO 时间(镜像 new Date(...));缺省用 UTC now,naive 时间按 UTC。"""
    if value is None:
        return datetime.now(UTC)
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _parse_millis(value: str) -> float:
    """镜像 new Date(x).getTime():解析失败返回 NaN(比较恒为 false)。"""
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return float("nan")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return (dt - _EPOCH).total_seconds() * 1000.0


def _ms(dt: datetime) -> float:
    return (dt - _EPOCH).total_seconds() * 1000.0


def _utc_day(dt: datetime) -> int:
    """镜像 JS getUTCDay():0=Sunday .. 6=Saturday。"""
    return (dt.weekday() + 1) % 7


def base_symbol(symbol: str) -> str:
    """镜像 baseSymbol():规整 + 别名归一(去掉尾部 M#/# 后缀)。"""
    normalized = re.sub(r"M#$", "", symbol.strip().upper())
    normalized = re.sub(r"#$", "", normalized)
    if normalized in ("GOLD", "XAUUSD"):
        return "XAUUSD"
    if normalized in ("US100", "NAS100", "US100CASH"):
        return "US100CASH"
    if normalized in ("USOIL", "WTI", "USOILCASH"):
        return "USOILCASH"
    if normalized in ("UKOIL", "BRENT", "UKOILCASH"):
        return "UKOILCASH"
    return normalized


def metadata_for(symbol: str) -> dict[str, Any]:
    """镜像 metadataFor():按归一符号取元数据,未收录回退 XAUUSD。"""
    return _symbol_meta.get(base_symbol(symbol), _symbol_meta["XAUUSD"])


def max_spread_for_market_filter(symbol: str) -> float:
    """镜像 maxSpreadForMarketFilter():市况过滤器专用 spread 上限。"""
    base = base_symbol(symbol)
    if base == "GBPJPY":
        return 6.0
    if base in ("EURJPY", "USDJPY"):
        return 5.0
    if base in ("GBPUSD", "USDCAD"):
        return 4.0
    return market_filter_max_spread


def max_spread_limit(configured: Any, fallback: float) -> float:
    """镜像 maxSpreadLimit():EA 配置的有限正数优先,否则回退静态元数据。"""
    if isinstance(configured, (int, float)) and not isinstance(configured, bool):
        value = float(configured)
        if math.isfinite(value) and value > 0:
            return value
    return fallback


def is_symbol_close_window(now: datetime, symbol: str) -> bool:
    """镜像 isSymbolCloseWindow():周五收盘窗口(US100CASH 另按 UTC 4 点)。"""
    if base_symbol(symbol) == "US100CASH":
        day = _utc_day(now)
        if day == 0 or day == 6:
            return True
        return now.hour >= 4
    return _utc_day(now) == 5 and now.hour >= 20


def is_symbol_rollover_window(now: datetime, symbol: str) -> bool:
    """镜像 isSymbolRolloverWindow():每日 21:55-22:10 UTC 展期窗口。"""
    if base_symbol(symbol) == "US100CASH":
        return False
    minute_of_day = now.hour * 60 + now.minute
    return 21 * 60 + 55 <= minute_of_day <= 22 * 60 + 10


def is_symbol_low_liquidity_session(now: datetime, symbol: str) -> bool:
    """镜像 isSymbolLowLiquiditySession():低流动性时段。"""
    minute_of_day = now.hour * 60 + now.minute
    if base_symbol(symbol) == "US100CASH":
        open_min = 21 * 60 + 30
        close_min = 4 * 60
        if open_min - 30 <= minute_of_day < open_min:
            return True
        if open_min <= minute_of_day < open_min + 20:
            return True
        if close_min - 15 <= minute_of_day < close_min:
            return True
        return minute_of_day < open_min - 30 or minute_of_day >= close_min
    return minute_of_day > 22 * 60 + 10 or minute_of_day < 60


def atr_value(bar: dict[str, Any]) -> float:
    """镜像 atrValue():`bar.atr ?? bar.ATR ?? 0`。"""
    atr = bar.get("atr")
    if atr is not None:
        return float(atr)
    atr_upper = bar.get("ATR")
    if atr_upper is not None:
        return float(atr_upper)
    return 0.0


def has_atr_expansion(bars: list[dict[str, Any]]) -> bool:
    """镜像 hasAtrExpansion():最新 ATR >= 历史均值 * 2(需 >= 10 根历史)。"""
    if len(bars) < min_atr_history_for_filter + 1:
        return False
    latest = atr_value(bars[-1])
    if latest <= 0:
        return False
    total = 0.0
    count = 0
    for bar in bars[:-1]:
        value = atr_value(bar)
        if value <= 0:
            continue
        total += value
        count += 1
    if count < min_atr_history_for_filter:
        return False
    average = total / count
    return average > 0 and latest >= average * atr_expansion_ratio


def min_positive(*values: float) -> float:
    """镜像 minPositive():取所有正数中的最小值,无正数返回 0。"""
    result = 0.0
    for value in values:
        if value <= 0:
            continue
        if result == 0 or value < result:
            result = value
    return result


def round_down_lot(value: float, step: float) -> float:
    """镜像 roundDownLot():按 step 向下取整(带 1e-9 容差)。"""
    if value <= 0 or step <= 0:
        return 0.0
    return math.floor(value / step + 1e-9) * step


def position_side(value: str) -> str:
    """镜像 positionSide():BUY/SELL 归一,其余返回空串。"""
    upper = value.strip().upper()
    if upper == "BUY":
        return "buy"
    if upper == "SELL":
        return "sell"
    return ""


def execution_price(tick: dict[str, Any], side: str) -> float:
    """镜像 executionPrice():buy 用 ask、sell 用 bid,其它取 mid。"""
    lowered = side.lower()
    if lowered == "buy":
        return float(_nullish_or(tick.get("ask"), 0))
    if lowered == "sell":
        return float(_nullish_or(tick.get("bid"), 0))
    bid = float(_nullish_or(tick.get("bid"), 0))
    ask = float(_nullish_or(tick.get("ask"), 0))
    return (bid + ask) / 2 if bid > 0 and ask > 0 else 0.0


def is_executable_mode(mode: str) -> bool:
    return mode in executable_modes


def is_audit_only_mode(mode: str) -> bool:
    return mode in audit_only_modes


def position_conflict_rejects(input_: dict[str, Any]) -> list[str]:
    """镜像 positionConflictRejects():同向加仓 / 反向对冲冲突检测。"""
    plan = _get(input_, "plan", {})
    state = _get(input_, "state", {})
    reasons: list[str] = []
    plan_side = str(plan.get("side") or "").lower()
    plan_symbol = base_symbol(_get(plan, "symbol", "") or "")
    add_rejected = False
    hedge_rejected = False

    for position in _get(state, "positions", []):
        if _nullish_or(position.get("ticket"), 0) <= 0 or _nullish_or(position.get("lots"), 0) <= 0:
            continue
        pos_symbol = position.get("symbol")
        if pos_symbol is not None and len(str(pos_symbol)) > 0 and base_symbol(str(pos_symbol)) != plan_symbol:
            continue
        side = position_side(str(_nullish_or(position.get("type"), "")))
        if side == "" or plan_side == "none":
            continue
        source_strategy = _get(input_, "sourceStrategy", "")
        pos_strategy = position.get("strategy")
        if source_strategy != "" and str(pos_strategy or "") != "" and pos_strategy != source_strategy:
            continue
        if side == plan_side and input_.get("allowAdd") is not True and not add_rejected:
            reasons.append("position.add_not_allowed")
            add_rejected = True
        if side != plan_side and input_.get("allowHedge") is not True and not hedge_rejected:
            reasons.append("position.hedge_not_allowed")
            hedge_rejected = True

    return reasons


def collect_tradeability_rejects(input_: dict[str, Any], now: datetime, meta: dict[str, Any]) -> list[str]:
    """镜像 collectTradeabilityRejects():市场/市价可交易性否决原因。"""
    runtime = _get(input_, "runtime", {})
    state = _get(input_, "state", {})
    tick = _get(state, "tick", {})
    plan = _get(input_, "plan", {})
    reasons: list[str] = []

    if not runtime.get("marketOpen"):
        reasons.append("market.closed")
    if not runtime.get("isTradeAllowed"):
        reasons.append("market.trade_not_allowed")
    last_tick_at = runtime.get("lastTickAt")
    if last_tick_at is None or len(str(last_tick_at)) == 0:
        reasons.append("tick.missing")
    elif _ms(now) - _parse_millis(str(last_tick_at)) > default_max_tick_age_ms:
        reasons.append("tick.stale")
    if _nullish_or(tick.get("bid"), 0) <= 0 or _nullish_or(tick.get("ask"), 0) <= 0:
        reasons.append("tick.missing_price")
    if _nullish_or(tick.get("spread"), 0) > max_spread_limit(_get(tick, "maxSpread", None), meta.get("maxSpread", 0)):
        reasons.append("spread.too_wide")
    expires_at = plan.get("expiresAt")
    if expires_at is not None and len(str(expires_at)) > 0 and _ms(now) > _parse_millis(str(expires_at)):
        reasons.append("plan.expired")
    return reasons


def validate_expandable_risk(input_: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
    """镜像 validateExpandableRisk():风险/保证金约束下的手数校验与收敛。"""
    plan = _get(input_, "plan", {})
    state = _get(input_, "state", {})
    tick = _get(state, "tick", {})
    runtime = _get(input_, "runtime", {})
    account = _get(input_, "account", {})

    requested_lots = _nullish_or(plan.get("maxLots"), 0)
    allowed_lots = 0.0
    max_risk_lots = 0.0
    max_margin_lots = 0.0
    clamped = False
    rejects: list[str] = []

    entry = execution_price(tick, str(plan.get("side") or ""))
    stop_loss = _nullish_or(plan.get("stopLoss"), 0)

    if entry <= 0:
        rejects.append("entry.missing")
    if stop_loss <= 0:
        rejects.append("sl.missing")
    if entry > 0 and stop_loss > 0:
        distance = abs(entry - stop_loss)
        if str(plan.get("side") or "").lower() == "buy" and stop_loss >= entry:
            rejects.append("sl.wrong_side")
        if str(plan.get("side") or "").lower() == "sell" and stop_loss <= entry:
            rejects.append("sl.wrong_side")
        if distance < meta.get("minSLDistance", 0):
            rejects.append("sl.too_close")
        if distance > meta.get("maxSLDistance", 0):
            rejects.append("sl.too_far")
        max_risk_lots = round_down_lot(
            (_nullish_or(runtime.get("equity"), 0) * default_max_risk_pct) / (distance * meta.get("contractSize", 0)),
            meta.get("lotStep", 0),
        )
    if requested_lots <= 0:
        rejects.append("lots.missing")
    free_margin = _nullish_or(runtime.get("freeMargin"), 0)
    if free_margin <= 0:
        rejects.append("margin.free_margin_missing")
    if entry > 0 and free_margin > 0:
        leverage_raw = _nullish_or(account.get("leverage"), 0)
        leverage = leverage_raw if leverage_raw > 0 else 1
        margin_per_lot = (entry * meta.get("contractSize", 0)) / leverage
        if margin_per_lot > 0:
            max_margin_lots = round_down_lot(
                (free_margin * default_margin_use_pct) / margin_per_lot,
                meta.get("lotStep", 0),
            )
    if rejects:
        return {
            "requestedLots": requested_lots,
            "allowedLots": allowed_lots,
            "maxRiskLots": max_risk_lots,
            "maxMarginLots": max_margin_lots,
            "clamped": clamped,
            "rejects": rejects,
        }

    rejects.extend(position_conflict_rejects(input_))
    if rejects:
        return {
            "requestedLots": requested_lots,
            "allowedLots": allowed_lots,
            "maxRiskLots": max_risk_lots,
            "maxMarginLots": max_margin_lots,
            "clamped": clamped,
            "rejects": rejects,
        }

    allowed_lots = round_down_lot(
        min_positive(requested_lots, meta.get("maxLot", 0), max_risk_lots, max_margin_lots),
        meta.get("lotStep", 0),
    )
    if allowed_lots < meta.get("minLot", 0):
        rejects.append("lots.below_min_after_clamp")
        return {
            "requestedLots": requested_lots,
            "allowedLots": allowed_lots,
            "maxRiskLots": max_risk_lots,
            "maxMarginLots": max_margin_lots,
            "clamped": clamped,
            "rejects": rejects,
        }

    clamped = allowed_lots < requested_lots
    return {
        "requestedLots": requested_lots,
        "allowedLots": allowed_lots,
        "maxRiskLots": max_risk_lots,
        "maxMarginLots": max_margin_lots,
        "clamped": clamped,
        "rejects": rejects,
    }


def evaluate_risk_gate(input_: dict[str, Any]) -> dict[str, Any]:
    """镜像 evaluateRiskGate():风控门槛总入口。

    plan 缺失直接放行(reasonCodes 含 plan.absent);按可执行模式、
    可交易性、close/reduce 审计安全、扩仓手数校验逐级裁决。
    """
    plan = input_.get("plan")
    if plan is None:
        return {
            "status": "accepted",
            "auditOnly": False,
            "reasonCodes": ["plan.absent"],
            "canProduceLiveCommands": False,
        }

    now = _as_utc_dt(_get(input_, "now", None))
    mode = str(plan.get("mode") or "").lower()
    symbol = base_symbol(_get(plan, "symbol", "") or "")
    result: dict[str, Any] = {
        "decisionId": plan.get("decisionId"),
        "mode": mode,
        "symbol": symbol,
        "status": "accepted",
        "auditOnly": is_audit_only_mode(mode),
        "reasonCodes": [],
        "canProduceLiveCommands": False,
    }

    if not is_executable_mode(mode):
        result["reasonCodes"].append("action.non_executable")
        return result

    meta = metadata_for(symbol)
    tradeability_rejects = collect_tradeability_rejects(input_, now, meta)
    if tradeability_rejects:
        result["status"] = "rejected"
        result["reasonCodes"].extend(tradeability_rejects)
        return result

    if mode in ("close", "reduce"):
        result["reasonCodes"].append("action.audit_safe")
        return result

    validation = validate_expandable_risk(input_, meta)
    result["requestedLots"] = validation["requestedLots"]
    result["allowedLots"] = validation["allowedLots"]
    result["maxRiskLots"] = validation["maxRiskLots"]
    result["maxMarginLots"] = validation["maxMarginLots"]

    if validation["rejects"]:
        result["status"] = "rejected"
        result["reasonCodes"].extend(validation["rejects"])
        return result
    if validation["clamped"]:
        result["status"] = "clamped"
        result["reasonCodes"].append("lots.clamped")
        return result

    result["reasonCodes"].append("lots.accepted")
    return result


def evaluate_market_filters(input_: dict[str, Any]) -> dict[str, Any]:
    """镜像 evaluateMarketFilters():市况过滤器总入口。

    按 blocking/warning 分级收集过滤项,输出 blocked/blocking/warnings/reason_codes。
    """
    now = _as_utc_dt(_get(input_, "now", None))
    state = _get(input_, "state", {})
    tick = _get(state, "tick", {})
    symbol_raw = _get(input_, "symbol", None)
    if symbol_raw is None:
        symbol_raw = tick.get("symbol")
    symbol = base_symbol(_nullish_or(symbol_raw, "") or "")

    result: dict[str, Any] = {
        "blocked": False,
        "blocking": [],
        "warnings": [],
        "reason_codes": [],
    }

    def add(code: str, severity: str) -> None:
        filter_item = {"code": code, "severity": severity}
        if severity == "blocking":
            result["blocked"] = True
            result["blocking"].append(filter_item)
        else:
            result["warnings"].append(filter_item)
        result["reason_codes"].append(code)

    runtime = _get(input_, "runtime", {})
    if not runtime.get("marketOpen"):
        add("market.closed", "blocking")
    if not runtime.get("isTradeAllowed"):
        add("market.trade_not_allowed", "blocking")
    last_tick_at = runtime.get("lastTickAt")
    if last_tick_at is None or len(str(last_tick_at)) == 0:
        add("tick.missing", "blocking")
    elif _ms(now) - _parse_millis(str(last_tick_at)) > default_max_tick_age_ms:
        add("tick.stale", "blocking")
    if _nullish_or(tick.get("spread"), 0) > max_spread_limit(
        _get(tick, "maxSpread", None), max_spread_for_market_filter(symbol)
    ):
        add("spread.too_wide", "blocking")
    if is_symbol_close_window(now, symbol):
        add("session.friday_close_window", "blocking")
    if is_symbol_rollover_window(now, symbol):
        add("session.rollover_window", "warning")
    if is_symbol_low_liquidity_session(now, symbol):
        add("session.low_liquidity", "warning")
    bars = _get(_get(state, "bars", {}), "M30", None)
    if has_atr_expansion(bars if isinstance(bars, list) else []):
        add("volatility.atr_expansion", "warning")

    return result
