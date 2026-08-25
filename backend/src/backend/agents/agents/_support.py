"""分析器 agents 共享支撑层(gold-bot apps/app-agent 移植的轻量本地实现)。

本模块是 M7(分析器 agents)分组的共用依赖,提供以下 1:1 移植的纯函数/类型:

- utils/logger.ts                -> get_logger()(pino 语义:log(obj, msg))
- utils/stable-stringify.ts      -> stable_stringify()
- utils/parse.ts                 -> extract_json() / safe_parse_response()
- utils/markdown-parser.ts       -> split_sections/extract_fields/.../detect_format
- utils/goldbot-indicators.ts    -> select_indicator()
- tools/sr-calculator.ts         -> find_psychological_levels()
- utils/price-validator.ts       -> validate_price_range()/filter_valid_prices()
- config/symbol-profile.ts       -> SymbolProfile/get_symbol_profile()/detect_cross_instrument_price()
- config/bar-source.service.ts   -> atr_of()
- types/schemas.ts               -> clean_sr_levels() + schemas 校验器(轻量 Zod 等价)
- tools/llm-client.ts            -> SystemBlock/UserLayer/ToolUse/CacheStats/
                                   StreamLayeredResult/LlmClient(Protocol)/LlmClientService
- tools/chanlun-core.ts          -> analyze_chanlun()(最小确定性实现,待后续 worker 对齐)
- tools/elliott-wave.ts          -> analyze_elliott_wave()(最小确定性实现,待后续 worker 对齐)

依赖说明:utils/parse.ts、utils/stable-stringify.ts、utils/logger.ts、
config/symbol-profile.ts、tools/llm-client.ts、types/schemas.ts 由其他 M7 worker
移植中,若其产出可用,本模块应切换到那些实现(本模块为最小本地实现)。
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Awaitable, Callable
from typing import Any, Protocol, TypeGuard

__all__ = [
    "CacheStats",
    "DEFAULT_MAX_LOTS",
    "DEFAULT_MIN_LOTS",
    "LlmClient",
    "LlmClientService",
    "OnLLM",
    "StreamLayeredResult",
    "StreamResult",
    "SystemBlock",
    "ToolUse",
    "TradeValidation",
    "UserLayer",
    "atr_of",
    "validate_arbitration_business",
    "validate_trade_recommendation_business",
    "clean_sr_levels",
    "compute_cache_hit_rate",
    "detect_cross_instrument_price",
    "detect_format",
    "extract_fields",
    "extract_json",
    "extract_list_items",
    "extract_warnings",
    "filter_valid_prices",
    "find_psychological_levels",
    "get_boolean_field",
    "get_enum_field",
    "get_logger",
    "get_number_field",
    "get_string_field",
    "get_symbol_profile",
    "parse_sr_level_line",
    "parse_sr_levels",
    "parse_warnings_line",
    "safe_parse_response",
    "select_indicator",
    "split_sections",
    "stable_stringify",
    "validate_arbitration_result",
    "validate_chanlun_analyst_result",
    "validate_comprehensive_data",
    "validate_harmonic_analysis_result",
    "validate_risk_assessment",
    "validate_sr_level",
    "validate_sr_levels",
    "validate_technical_analysis",
    "validate_wave_analyst_result",
    "validate_price_range",
]

JSONDict = dict[str, Any]

# ─── Logger(镜像 utils/logger.ts 的 getLogger,方法签名为 log(obj, msg)) ─────────


class Logger:
    """pino 风格的最小 logger:log(context, msg),默认静默(测试可替换)。"""

    def debug(self, obj: Any, msg: str = "") -> None:
        pass

    def info(self, obj: Any, msg: str = "") -> None:
        pass

    def warn(self, obj: Any, msg: str = "") -> None:
        pass

    def error(self, obj: Any, msg: str = "") -> None:
        pass


_LOGGER: Logger | None = None


def get_logger() -> Logger:
    global _LOGGER
    if _LOGGER is None:
        _LOGGER = Logger()
    return _LOGGER


# ─── stable-stringify(镜像 utils/stable-stringify.ts) ─────────────────────────


def _sort_object(obj: Any) -> Any:
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, list):
        return [_sort_object(item) for item in obj]
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for key in sorted(str(k) for k in obj.keys()):
            out[key] = _sort_object(obj[key])
        return out
    return str(obj)


def stable_stringify(obj: Any) -> str:
    """确定性 JSON 序列化:键按字母序排序、无空白(供 prompt 缓存前缀使用)。"""
    return json.dumps(_sort_object(obj), ensure_ascii=False, separators=(",", ":"))


def stable_stringify_pretty(obj: Any, indent: int = 2) -> str:
    return json.dumps(_sort_object(obj), ensure_ascii=False, indent=indent)


# ─── parse.ts(镜像 utils/parse.ts) ────────────────────────────────────────────


def extract_json(raw: str) -> str | None:
    match = re.search(r"\{[\s\S]*\}", raw)
    return match.group(0) if match else None


def safe_parse_response(
    raw: str,
    validator: Callable[[Any], Any],
    context: dict[str, Any] | None = None,
) -> Any:
    """安全解析单条 LLM 响应:无 JSON / 非法 JSON / schema 校验失败 -> None。"""
    logger = get_logger()
    text = extract_json(raw)
    if text is None:
        logger.warn({"raw": raw[:200], **(context or {})}, "safeParse: no JSON found")
        return None
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError) as err:
        logger.warn(
            {"raw": raw[:200], "err": str(err), **(context or {})},
            "safeParse: JSON.parse failed",
        )
        return None
    result = validator(parsed)
    if result is None:
        logger.warn(
            {"issues": _schema_issues(parsed), **(context or {})},
            "safeParse: Zod validation failed",
        )
        return None
    return result


def _schema_issues(data: Any) -> list[str]:
    """把校验失败描述为简短 issue 列表(仅用于日志,不参与语义)。"""
    if not isinstance(data, dict):
        return ["not-an-object"]
    return [f"field missing/invalid: {key}" for key in data.keys()][:5]


# ─── markdown-parser.ts(镜像 utils/markdown-parser.ts) ────────────────────────


def normalize_key(key: str) -> str:
    """Lowercase + 空格/连字符 -> 下划线。"""
    return re.sub(r"[\s-]+", "_", key.strip().lower())


def split_sections(raw: str) -> dict[str, str]:
    sections: dict[str, str] = {}
    lines = raw.split("\n")
    current_section = ""
    current_content: list[str] = []

    for line in lines:
        header_match = re.match(r"^##\s+(.+)", line)
        if header_match:
            if current_section:
                sections[current_section] = "\n".join(current_content)
            current_section = normalize_key(header_match.group(1))
            current_content = []
        else:
            current_content.append(line)
    if current_section:
        sections[current_section] = "\n".join(current_content)

    if not sections and raw.strip():
        sections["root"] = raw
    return sections


def extract_fields(section: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in section.split("\n"):
        kv_match = re.match(r"^-\s+(.+?):\s+(.*)", line)
        if kv_match:
            key = normalize_key(kv_match.group(1))
            value = kv_match.group(2).strip()
            if key not in fields:
                fields[key] = value
    return fields


def extract_list_items(section: str) -> list[str]:
    items: list[str] = []
    for line in section.split("\n"):
        list_match = re.match(r"^\s{2,}-\s+(.+)", line)
        if list_match:
            content = list_match.group(1).strip()
            if not re.match(r"^[^|]+:", content) or "|" in content:
                items.append(content)
    return items


def get_enum_field(fields: dict[str, str], key: str, allowed: tuple[str, ...], default: str) -> str:
    raw = fields.get(normalize_key(key))
    if not raw:
        return default
    normalized = raw.strip().lower()
    for allowed_val in allowed:
        if allowed_val.lower() == normalized:
            return allowed_val
    cleaned = re.sub(r"[_\s-]", "", normalized)
    for allowed_val in allowed:
        if re.sub(r"[_\s-]", "", allowed_val).lower() == cleaned:
            return allowed_val
    return default


def get_number_field(
    fields: dict[str, str],
    key: str,
    default: float,
    opts: dict[str, float] | None = None,
) -> float:
    raw = fields.get(normalize_key(key))
    if not raw:
        return default
    num_match = re.search(r"-?\d+\.?\d*", raw)
    if not num_match:
        return default
    try:
        num = float(num_match.group(0))
    except ValueError:
        return default
    if not math.isfinite(num):
        return default
    if opts is not None and "min" in opts and num < opts["min"]:
        return default
    if opts is not None and "max" in opts and num > opts["max"]:
        return default
    return num


def get_boolean_field(fields: dict[str, str], key: str, default: bool) -> bool:
    raw = fields.get(normalize_key(key))
    if not raw:
        return default
    normalized = raw.strip().lower()
    if normalized in ("true", "1", "yes"):
        return True
    if normalized in ("false", "0", "no"):
        return False
    return default


def get_string_field(
    fields: dict[str, str],
    key: str,
    default: str = "",
    max_length: int = 2000,
) -> str:
    raw = fields.get(normalize_key(key))
    if not raw or raw.strip() == "":
        return default
    sanitized = re.sub(r"<[^>]*>", "", raw).strip()
    if len(sanitized) == 0:
        return default
    return sanitized[:max_length] if len(sanitized) > max_length else sanitized


SRLevelDict = dict[str, Any]


def parse_sr_level_line(line: str, expected_type: str) -> SRLevelDict | None:
    parts = [part.strip() for part in line.split("|")]
    if len(parts) < 2:
        return None
    try:
        price = float(parts[0])
    except ValueError:
        return None
    if not math.isfinite(price) or price <= 0:
        return None
    strength_raw = (parts[2] if len(parts) > 2 else "").lower()
    strength = (
        strength_raw
        if strength_raw in ("strong", "moderate", "weak")
        else "moderate"
    )
    timeframe = parts[3] if len(parts) > 3 and parts[3] else "H1"
    try:
        touches_raw = float(parts[4]) if len(parts) > 4 else math.nan
    except ValueError:
        touches_raw = math.nan
    touches = (
        max(0, min(20, round(touches_raw)))
        if math.isfinite(touches_raw)
        else 1
    )
    return {"price": price, "type": expected_type, "strength": strength, "timeframe": timeframe, "touches": touches}


def parse_sr_levels(lines: list[str], expected_type: str) -> list[SRLevelDict]:
    results: list[SRLevelDict] = []
    for line in lines:
        parsed = parse_sr_level_line(line, expected_type)
        if parsed is not None:
            results.append(parsed)
    return results[:6]


def parse_warnings_line(line: str) -> list[str]:
    if not line:
        return []
    return [
        item
        for item in (
            re.sub(r"<[^>]*>", "", part).strip()
            for part in line.split(";")
        )
        if 0 < len(item) <= 500
    ][:10]


def extract_warnings(fields: dict[str, str], list_items: list[str]) -> list[str]:
    warnings_field = fields.get("warnings")
    if warnings_field:
        return parse_warnings_line(warnings_field)
    if len(list_items) > 0:
        return [
            item
            for item in (re.sub(r"<[^>]*>", "", item).strip() for item in list_items)
            if 0 < len(item) <= 500
        ][:10]
    return []


def detect_format(raw: str) -> str:
    if re.search(r"^##\s+", raw, re.MULTILINE):
        return "markdown"
    if re.search(r"\{[\s\S]*\}", raw):
        return "json"
    return "unknown"


# ─── goldbot-indicators.ts(镜像 utils/goldbot-indicators.ts) ──────────────────


def select_indicator(indicators: dict[str, Any], *timeframes: str) -> dict[str, Any]:
    """按优先时间框架选择指标包;找不到返回空 dict。"""
    for timeframe in timeframes:
        indicator = indicators.get(timeframe)
        if indicator:
            return indicator
    return {}


# ─── sr-calculator.ts(镜像 tools/sr-calculator.ts 的 findPsychologicalLevels) ──


def find_psychological_levels(
    price: float,
    range_: float = 100,
    max_distance: float | None = None,
) -> list[dict[str, Any]]:
    levels: list[dict[str, Any]] = []
    if price >= 1000:
        step = 50
    elif price >= 100:
        step = 10
    elif price >= 10:
        step = 5
    else:
        step = 1

    effective_max_distance = step * 3 if max_distance is None else max_distance
    lower_bound = max(price - range_, price - effective_max_distance)
    upper_bound = min(price + range_, price + effective_max_distance)

    start = math.ceil(lower_bound / step) * step
    level = start
    while level <= upper_bound:
        rounded_level = round(level * 100) / 100
        distance = abs(rounded_level - price)
        if distance <= effective_max_distance:
            is_round = rounded_level % (step * 10) == 0
            if is_round:
                label = f"Major Round {rounded_level}"
            elif rounded_level % (step * 2) == 0:
                label = f"Round {rounded_level}"
            else:
                label = f"Half {rounded_level}"
            levels.append({"price": rounded_level, "label": label})
        level += step
    return levels


# ─── price-validator.ts(镜像 utils/price-validator.ts 的核心价格过滤) ─────────

DEFAULT_TOLERANCE = 0.5


def validate_price_range(
    price: float,
    current_price: float,
    profile: dict[str, Any],
    label: str,
    tolerance: float = DEFAULT_TOLERANCE,
) -> bool:
    if not math.isfinite(price) or price <= 0:
        return False
    if not math.isfinite(current_price) or current_price <= 0:
        return True
    min_price = current_price * (1 - tolerance)
    max_price = current_price * (1 + tolerance)
    if price < min_price or price > max_price:
        return False
    return True


def filter_valid_prices(
    levels: list[SRLevelDict],
    current_price: float,
    profile: dict[str, Any],
    label: str,
) -> list[SRLevelDict]:
    return [
        level
        for level in levels
        if validate_price_range(level["price"], current_price, profile, label)
    ]


# ─── symbol-profile.ts(镜像 config/symbol-profile.ts) ─────────────────────────

DEFAULT_MIN_LOTS = 0.01
DEFAULT_MAX_LOTS = 0.5
_MICRO_CONTRACT_MIN_LOTS = 0.1

_PROFILES: dict[str, dict[str, Any]] = {
    "XAUUSD": {
        "symbol": "XAUUSD",
        "name": "黄金/美元 (Gold/USD)",
        "price_precision": 2,
        "pip_value": 0.1,
        "typical_atr_range": {
            "M15": {"min": 1.5, "max": 8},
            "M30": {"min": 2, "max": 12},
            "H1": {"min": 4, "max": 25},
            "H4": {"min": 10, "max": 60},
        },
        "sl_atr_multiplier": 1.5,
        "tp_atr_multiplier": 3.0,
        "volatility_level": "medium",
        "price_range_hint": "typically 1800–4500 USD/oz",
        "price_range": [1800, 4500],
        "asset_class": "metal",
        "volume_reliable": True,
        "min_lots": DEFAULT_MIN_LOTS,
        "max_lots": DEFAULT_MAX_LOTS,
    },
    "GOLD": {
        "symbol": "GOLD",
        "name": "黄金 (Gold)",
        "price_precision": 2,
        "pip_value": 0.1,
        "typical_atr_range": {
            "M15": {"min": 1.5, "max": 8},
            "M30": {"min": 2, "max": 12},
            "H1": {"min": 4, "max": 25},
            "H4": {"min": 10, "max": 60},
        },
        "sl_atr_multiplier": 1.5,
        "tp_atr_multiplier": 3.0,
        "volatility_level": "medium",
        "price_range_hint": "typically 1800–4500",
        "price_range": [1800, 4500],
        "asset_class": "metal",
        "volume_reliable": True,
        "min_lots": DEFAULT_MIN_LOTS,
        "max_lots": DEFAULT_MAX_LOTS,
    },
    "GBPJPY": {
        "symbol": "GBPJPY",
        "name": "英镑/日元 (British Pound/Japanese Yen)",
        "price_precision": 3,
        "pip_value": 0.01,
        "typical_atr_range": {
            "M15": {"min": 0.05, "max": 0.30},
            "M30": {"min": 0.08, "max": 0.45},
            "H1": {"min": 0.15, "max": 0.80},
            "H4": {"min": 0.30, "max": 1.50},
        },
        "sl_atr_multiplier": 1.8,
        "tp_atr_multiplier": 3.5,
        "volatility_level": "high",
        "price_range_hint": "typically 150–250 JPY per GBP",
        "price_range": [150, 250],
        "asset_class": "forex",
        "volume_reliable": False,
        "min_lots": DEFAULT_MIN_LOTS,
        "max_lots": DEFAULT_MAX_LOTS,
    },
    "EURJPY": {
        "symbol": "EURJPY",
        "name": "欧元/日元 (Euro/Japanese Yen)",
        "price_precision": 3,
        "pip_value": 0.01,
        "typical_atr_range": {
            "M15": {"min": 0.04, "max": 0.25},
            "M30": {"min": 0.06, "max": 0.40},
            "H1": {"min": 0.12, "max": 0.70},
            "H4": {"min": 0.25, "max": 1.30},
        },
        "sl_atr_multiplier": 1.8,
        "tp_atr_multiplier": 3.5,
        "volatility_level": "high",
        "price_range_hint": "typically 130–200 JPY per EUR",
        "price_range": [130, 200],
        "asset_class": "forex",
        "volume_reliable": False,
        "min_lots": DEFAULT_MIN_LOTS,
        "max_lots": DEFAULT_MAX_LOTS,
    },
    "USDJPY": {
        "symbol": "USDJPY",
        "name": "美元/日元 (US Dollar/Japanese Yen)",
        "price_precision": 3,
        "pip_value": 0.01,
        "typical_atr_range": {
            "M15": {"min": 0.03, "max": 0.20},
            "M30": {"min": 0.05, "max": 0.35},
            "H1": {"min": 0.10, "max": 0.60},
            "H4": {"min": 0.20, "max": 1.10},
        },
        "sl_atr_multiplier": 1.5,
        "tp_atr_multiplier": 3.0,
        "volatility_level": "medium",
        "price_range_hint": "typically 120–180 JPY per USD",
        "price_range": [120, 180],
        "asset_class": "forex",
        "volume_reliable": False,
        "min_lots": DEFAULT_MIN_LOTS,
        "max_lots": DEFAULT_MAX_LOTS,
    },
    "XAGUSD": {
        "symbol": "XAGUSD",
        "name": "白银/美元 (Silver/USD)",
        "price_precision": 3,
        "pip_value": 0.01,
        "typical_atr_range": {
            "M15": {"min": 0.03, "max": 0.20},
            "M30": {"min": 0.05, "max": 0.30},
            "H1": {"min": 0.10, "max": 0.60},
            "H4": {"min": 0.20, "max": 1.20},
        },
        "sl_atr_multiplier": 1.5,
        "tp_atr_multiplier": 3.0,
        "volatility_level": "high",
        "price_range_hint": "typically 20–40 USD/oz",
        "price_range": [15, 50],
        "asset_class": "metal",
        "volume_reliable": True,
        "min_lots": DEFAULT_MIN_LOTS,
        "max_lots": DEFAULT_MAX_LOTS,
    },
    "US100CASH": {
        "symbol": "US100CASH",
        "name": "纳斯达克100指数 (US100 Cash CFD)",
        "price_precision": 2,
        "pip_value": 1.0,
        "typical_atr_range": {
            "M15": {"min": 30, "max": 200},
            "M30": {"min": 50, "max": 400},
            "H1": {"min": 100, "max": 800},
            "H4": {"min": 300, "max": 2000},
        },
        "sl_atr_multiplier": 0.8,
        "tp_atr_multiplier": 2.5,
        "volatility_level": "high",
        "price_range_hint": "typically 15000–35000 USD",
        "price_range": [15000, 35000],
        "asset_class": "index",
        "volume_reliable": True,
        "min_lots": DEFAULT_MIN_LOTS,
        "max_lots": DEFAULT_MAX_LOTS,
    },
    "USOILCASH": {
        "symbol": "USOILCASH",
        "name": "WTI原油 (US Oil Cash CFD)",
        "price_precision": 2,
        "pip_value": 0.01,
        "typical_atr_range": {
            "M15": {"min": 0.2, "max": 1.5},
            "M30": {"min": 0.3, "max": 2.5},
            "H1": {"min": 0.5, "max": 4.0},
            "H4": {"min": 1.0, "max": 8.0},
        },
        "sl_atr_multiplier": 2.0,
        "tp_atr_multiplier": 3.5,
        "volatility_level": "medium",
        "price_range_hint": "typically 60–100 USD/barrel",
        "price_range": [40, 120],
        "asset_class": "commodity",
        "volume_reliable": True,
        "min_lots": DEFAULT_MIN_LOTS,
        "max_lots": DEFAULT_MAX_LOTS,
    },
    "UKOILCASH": {
        "symbol": "UKOILCASH",
        "name": "布伦特原油 (UK Oil Cash CFD)",
        "price_precision": 2,
        "pip_value": 0.01,
        "typical_atr_range": {
            "M15": {"min": 0.2, "max": 1.5},
            "M30": {"min": 0.3, "max": 2.5},
            "H1": {"min": 0.6, "max": 4.5},
            "H4": {"min": 1.2, "max": 9.0},
        },
        "sl_atr_multiplier": 2.0,
        "tp_atr_multiplier": 3.5,
        "volatility_level": "medium",
        "price_range_hint": "typically 65–105 USD/barrel",
        "price_range": [40, 120],
        "asset_class": "commodity",
        "volume_reliable": True,
        "min_lots": DEFAULT_MIN_LOTS,
        "max_lots": DEFAULT_MAX_LOTS,
    },
}

_MICRO_BASE_ALIASES: dict[str, str] = {
    "SILVERM": "XAGUSD",
}


def _has_micro_contract_suffix(raw_symbol: str) -> bool:
    normalized = raw_symbol.strip()
    if "#" in normalized:
        return True
    return bool(re.match(r"m$", re.sub(r"[^A-Z0-9]", "", normalized), re.IGNORECASE))


def _with_lot_bounds(profile: dict[str, Any], raw_symbol: str) -> dict[str, Any]:
    min_lots = (
        _MICRO_CONTRACT_MIN_LOTS
        if _has_micro_contract_suffix(raw_symbol)
        else (profile.get("min_lots") if profile.get("min_lots") is not None else DEFAULT_MIN_LOTS)
    )
    copy = dict(profile)
    copy["min_lots"] = min_lots
    copy["max_lots"] = profile.get("max_lots") if profile.get("max_lots") is not None else DEFAULT_MAX_LOTS
    return copy


def get_symbol_profile(raw_symbol: str) -> dict[str, Any]:
    """按 symbol 字符串解析 SymbolProfile;处理 base-symbol 剥离(GOLDm# -> GOLD)。"""
    if raw_symbol in _PROFILES:
        return _with_lot_bounds(_PROFILES[raw_symbol], raw_symbol)

    base = re.sub(r"[^A-Z0-9]", "", raw_symbol).upper()
    alias = _MICRO_BASE_ALIASES.get(base)
    if alias and alias in _PROFILES:
        return _with_lot_bounds(_PROFILES[alias], raw_symbol)

    if base in _PROFILES:
        return _with_lot_bounds(_PROFILES[base], raw_symbol)

    for key, profile in _PROFILES.items():
        if base.startswith(key):
            return _with_lot_bounds(profile, raw_symbol)

    return _with_lot_bounds(
        {
            "symbol": raw_symbol,
            "name": raw_symbol,
            "price_precision": 3,
            "pip_value": 0.01,
            "typical_atr_range": {
                "M15": {"min": 0.01, "max": 1},
                "M30": {"min": 0.02, "max": 2},
                "H1": {"min": 0.05, "max": 5},
                "H4": {"min": 0.1, "max": 10},
            },
            "sl_atr_multiplier": 1.5,
            "tp_atr_multiplier": 3.0,
            "volatility_level": "medium",
            "price_range_hint": "unknown — validate prices against current market data",
            "asset_class": "forex",
            "volume_reliable": False,
        },
        raw_symbol,
    )


def detect_cross_instrument_price(
    target_symbol: str,
    price: float,
    current_price: float,
    all_current_prices: dict[str, float],
) -> str | None:
    """跨品种价格碰撞检测:价格疑似属于另一品种时返回其 symbol,否则 None。"""
    if not math.isfinite(price) or price <= 0 or not math.isfinite(current_price) or current_price <= 0:
        return None
    target_profile = get_symbol_profile(target_symbol)
    for other_symbol, other_price in all_current_prices.items():
        if other_symbol == target_symbol:
            continue
        if not math.isfinite(other_price) or other_price <= 0:
            continue
        other_profile = get_symbol_profile(other_symbol)
        if target_profile["asset_class"] == other_profile["asset_class"] and target_profile["asset_class"] != "forex":
            continue
        dist_to_other = abs(price - other_price)
        dist_to_target = abs(price - current_price)
        ratio = dist_to_other / dist_to_target if dist_to_target > 0 else math.inf
        near_other = dist_to_other / other_price < 0.08
        closer_to_other = ratio < 0.33
        if near_other and closer_to_other:
            return other_symbol
    return None


# ─── bar-source.service.ts(镜像 atrOf) ────────────────────────────────────────

_PREFERRED_ATR_TIMEFRAMES = ("H1", "M30", "M15", "H4")
_DEFAULT_ATR_PERIOD = 14


def _finite_number(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value) if math.isfinite(float(value)) else None
    return None


def _bars_for(payload: dict[str, Any], timeframe: str) -> list[dict[str, Any]]:
    bars = payload.get("bars")
    if not isinstance(bars, dict):
        return []
    exact = bars.get(timeframe)
    if isinstance(exact, list):
        return exact
    for key, value in bars.items():
        if str(key).upper() == timeframe and isinstance(value, list):
            return value
    return []


def _true_range(bar: dict[str, Any], previous_close: float | None) -> float | None:
    high = _finite_number(bar.get("high"))
    low = _finite_number(bar.get("low"))
    if high is None or low is None:
        return None
    if previous_close is None:
        return high - low
    return max(high - low, abs(high - previous_close), abs(low - previous_close))


def atr_of(payload: dict[str, Any], period: int = _DEFAULT_ATR_PERIOD) -> float:
    for timeframe in _PREFERRED_ATR_TIMEFRAMES:
        bars = _bars_for(payload, timeframe)
        latest_bar_atr: float | None = None
        for bar in reversed(bars):
            value = _finite_number(bar.get("atr"))
            if value is not None and value > 0:
                latest_bar_atr = value
                break
        if latest_bar_atr is not None:
            return latest_bar_atr
        if len(bars) < 2:
            continue
        ranges: list[float] = []
        for index in range(len(bars)):
            previous_close = _finite_number(bars[index - 1].get("close")) if index > 0 else None
            range_value = _true_range(bars[index], previous_close)
            if range_value is not None and range_value > 0:
                ranges.append(range_value)
        sample = ranges[-period:]
        if sample:
            return sum(sample) / len(sample)

    indicators = payload.get("indicators") or {}
    for timeframe in _PREFERRED_ATR_TIMEFRAMES:
        indicator = indicators.get(timeframe) or indicators.get(timeframe.lower())
        if isinstance(indicator, dict):
            atr = _finite_number(indicator.get("atr"))
            if atr is not None and atr > 0:
                return atr
    return 0.0


# ─── llm-client.ts(镜像 tools/llm-client.ts 的类型与分层调用契约) ─────────────


class SystemBlock:
    def __init__(self, text: str, cacheable: bool = True) -> None:
        self.text = text
        self.cacheable = cacheable

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SystemBlock):
            return NotImplemented
        return self.text == other.text and self.cacheable == other.cacheable

    def __repr__(self) -> str:
        return f"SystemBlock(text={self.text!r}, cacheable={self.cacheable})"


class UserLayer:
    def __init__(self, text: str, cacheable: bool = False) -> None:
        self.text = text
        self.cacheable = cacheable

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, UserLayer):
            return NotImplemented
        return self.text == other.text and self.cacheable == other.cacheable

    def __repr__(self) -> str:
        return f"UserLayer(text={self.text!r}, cacheable={self.cacheable})"


class ToolUse:
    def __init__(self, id: str, name: str, input: dict[str, Any]) -> None:
        self.id = id
        self.name = name
        self.input = input

    def __repr__(self) -> str:
        return f"ToolUse(id={self.id!r}, name={self.name!r})"


class CacheStats:
    def __init__(
        self,
        read_tokens: int = 0,
        creation_tokens: int = 0,
        hit_tokens: int = 0,
        miss_tokens: int = 0,
        input_tokens: int = 0,
    ) -> None:
        self.read_tokens = read_tokens
        self.creation_tokens = creation_tokens
        self.hit_tokens = hit_tokens
        self.miss_tokens = miss_tokens
        self.input_tokens = input_tokens


class StreamResult:
    """streamLayered 返回值(content + cacheStats + 可选 toolUse)。"""

    def __init__(
        self,
        content: str,
        cache_stats: CacheStats | None = None,
        tool_use: ToolUse | None = None,
    ) -> None:
        self.content = content
        self.cache_stats = cache_stats if cache_stats is not None else CacheStats()
        self.tool_use = tool_use


# 兼容别名:TS 里这层叫 ChatCompletionsStreamResult,测试/调用方按内容使用
StreamLayeredResult = StreamResult


def compute_cache_hit_rate(stats: PickLike) -> float:
    if not stats.input_tokens or stats.input_tokens <= 0:
        return 0
    return stats.read_tokens / stats.input_tokens


class _PickCacheStats(Protocol):
    read_tokens: int
    input_tokens: int


PickLike = _PickCacheStats
"""computeCacheHitRate 的入参(Pick<CacheStats,'readTokens'|'inputTokens'>)。"""


OnLLM = Callable[[list[SystemBlock], list[UserLayer], dict[str, Any] | None], Awaitable[str]]


class LlmClient(Protocol):
    """分析器 agents 依赖的 LLM 客户端契约(stub 友好)。

    镜像 TS LlmClientService 暴露给 agent 的方法子集。
    """

    async def stream_layered(
        self,
        system_blocks: list[SystemBlock],
        user_layers: list[UserLayer],
        opts: dict[str, Any] | None = None,
    ) -> StreamResult: ...

    async def invoke_layered(
        self,
        system_blocks: list[SystemBlock],
        user_layers: list[UserLayer],
        opts: dict[str, Any] | None = None,
    ) -> str: ...

    async def stream_invoke(self, prompt: str, system_message: str | None = None) -> str: ...

    def get_model(self) -> str: ...

    def get_cache_strategy(self) -> dict[str, str]: ...


async def _default_on_llm(
    system_blocks: list[SystemBlock],
    user_layers: list[UserLayer],
    opts: dict[str, Any] | None,
) -> str:
    raise NotImplementedError(
        "LlmClientService 未配置 on_llm 注入点;测试请传入 FakeLlmClient 或 on_llm 回调"
    )


class LlmClientService:
    """默认 LLM 客户端:通过 on_llm(layer 列表) 注入点 stub 化。

    未移植 LLM 网关客户端前的轻量实现;on_llm 为  (systemBlocks, userLayers, opts) -> str
    的异步回调,stream 与 invoke 都走同一注入点。
    """

    def __init__(
        self,
        *,
        on_llm: OnLLM | None = None,
        model: str = "default",
        cache_strategy: str = "auto_prefix",
    ) -> None:
        self._on_llm = on_llm if on_llm is not None else _default_on_llm
        self._model = model
        self._cache_strategy = cache_strategy

    async def stream_layered(
        self,
        system_blocks: list[SystemBlock],
        user_layers: list[UserLayer],
        opts: dict[str, Any] | None = None,
    ) -> StreamResult:
        text = await self._on_llm(system_blocks, user_layers, opts)
        return StreamResult(content=text, cache_stats=CacheStats())

    async def invoke_layered(
        self,
        system_blocks: list[SystemBlock],
        user_layers: list[UserLayer],
        opts: dict[str, Any] | None = None,
    ) -> str:
        return await self._on_llm(system_blocks, user_layers, opts)

    async def stream_invoke(self, prompt: str, system_message: str | None = None) -> str:
        blocks = [SystemBlock(system_message)] if system_message else []
        layers = [UserLayer(prompt, cacheable=False)]
        return await self._on_llm(blocks, layers, None)

    def get_model(self) -> str:
        return self._model

    def get_cache_strategy(self) -> dict[str, str]:
        return {"type": self._cache_strategy}


# ─── schemas.ts(镜像 types/schemas.ts 的校验器:轻量 Zod 语义) ─────────────────

_BIAS_VALUES = ("bullish", "bearish", "neutral")
_PHASE_VALUES = ("trending", "ranging", "breakout", "reversal", "consolidation")
_RECOMMENDATION_VALUES = ("hold", "close", "partial_close", "trail_stop", "none")
_STRENGTH_VALUES = ("strong", "moderate", "weak")
_SR_TYPE_VALUES = ("support", "resistance")


def _is_number(value: object) -> TypeGuard[float]:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(float(value))
    except OverflowError:
        return False


def _is_int(value: object) -> TypeGuard[int]:
    return _is_number(value) and float(value) == int(float(value))


def _is_non_empty_string(value: object) -> TypeGuard[str]:
    return isinstance(value, str) and len(value) >= 1


def validate_sr_level(data: Any) -> SRLevelDict | None:
    if not isinstance(data, dict):
        return None
    price = data.get("price")
    if not _is_number(price) or price <= 0:
        return None
    sr_type = data.get("type")
    if sr_type not in _SR_TYPE_VALUES:
        return None
    strength = data.get("strength")
    if strength not in _STRENGTH_VALUES:
        return None
    timeframe = data.get("timeframe")
    if not _is_non_empty_string(timeframe):
        return None
    touches = data.get("touches")
    if not _is_int(touches) or touches < 0 or touches > 20:
        return None
    return {
        "price": price,
        "type": sr_type,
        "strength": strength,
        "timeframe": timeframe,
        "touches": int(touches),
    }


def validate_technical_analysis(data: Any) -> dict[str, Any] | None:
    if not isinstance(data, dict):
        return None
    bias = data.get("bias")
    confidence = data.get("confidence")
    phase = data.get("phase")
    indicators_summary = data.get("indicators_summary")
    recommendation = data.get("recommendation")
    rationale = data.get("rationale")
    if bias not in _BIAS_VALUES:
        return None
    if not _is_number(confidence) or confidence < 0 or confidence > 100:
        return None
    if phase not in _PHASE_VALUES:
        return None
    if not _is_non_empty_string(indicators_summary):
        return None
    support_raw = data.get("support_levels")
    resistance_raw = data.get("resistance_levels")
    if not isinstance(support_raw, list) or len(support_raw) > 6:
        return None
    if not isinstance(resistance_raw, list) or len(resistance_raw) > 6:
        return None
    support = [validate_sr_level(level) for level in support_raw]
    resistance = [validate_sr_level(level) for level in resistance_raw]
    if any(level is None for level in support) or any(level is None for level in resistance):
        return None
    if recommendation not in _RECOMMENDATION_VALUES:
        return None
    if not _is_non_empty_string(rationale):
        return None
    return {
        "bias": bias,
        "confidence": confidence,
        "phase": phase,
        "indicators_summary": indicators_summary,
        "support_levels": [level for level in support if level is not None],
        "resistance_levels": [level for level in resistance if level is not None],
        "recommendation": recommendation,
        "rationale": rationale,
    }


def validate_sr_levels(data: Any) -> dict[str, Any] | None:
    if not isinstance(data, dict):
        return None
    support_raw = data.get("support_levels")
    resistance_raw = data.get("resistance_levels")
    recommendation = data.get("recommendation")
    rationale = data.get("rationale")
    if not isinstance(support_raw, list) or len(support_raw) > 6:
        return None
    if not isinstance(resistance_raw, list) or len(resistance_raw) > 6:
        return None
    support = [validate_sr_level(level) for level in support_raw]
    resistance = [validate_sr_level(level) for level in resistance_raw]
    if any(level is None for level in support) or any(level is None for level in resistance):
        return None
    if not isinstance(recommendation, str) or not isinstance(rationale, str):
        return None
    return {
        "support_levels": [level for level in support if level is not None],
        "resistance_levels": [level for level in resistance if level is not None],
        "recommendation": recommendation,
        "rationale": rationale,
    }


_RISK_LEVEL_VALUES = ("low", "medium", "high", "extreme")


def validate_risk_assessment(data: Any) -> dict[str, Any] | None:
    if not isinstance(data, dict):
        return None
    risk_level = data.get("riskLevel")
    max_position_size = data.get("maxPositionSize")
    suggested_sl = data.get("suggestedSL")
    warnings = data.get("warnings")
    if risk_level not in _RISK_LEVEL_VALUES:
        return None
    if not _is_number(max_position_size) or max_position_size < 0:
        return None
    if not _is_number(suggested_sl) or suggested_sl <= 0:
        return None
    suggested_tp = data.get("suggestedTP")
    if suggested_tp is not None and (not _is_number(suggested_tp) or suggested_tp <= 0):
        return None
    if not isinstance(warnings, list) or not all(isinstance(item, str) for item in warnings):
        return None
    add_on = data.get("addOn", False)
    if not isinstance(add_on, bool):
        return None
    result: dict[str, Any] = {
        "riskLevel": risk_level,
        "maxPositionSize": max_position_size,
        "suggestedSL": suggested_sl,
        "warnings": warnings,
        "addOn": add_on,
    }
    if suggested_tp is not None:
        result["suggestedTP"] = suggested_tp
    return result


_WAVE_CONFIRMATION_VALUES = ("confirmed", "partial", "rejected")
_CORRECTIVE_TYPE_VALUES = ("zigzag", "flat", "triangle")


def validate_wave_analyst_result(data: Any) -> dict[str, Any] | None:
    if not isinstance(data, dict):
        return None
    wave_confirmation = data.get("wave_confirmation")
    extension_wave = data.get("extension_wave")
    corrective_type = data.get("corrective_type")
    trend_strength = data.get("trend_strength")
    target_levels = data.get("target_levels")
    confidence = data.get("confidence")
    rationale = data.get("rationale")
    if wave_confirmation not in _WAVE_CONFIRMATION_VALUES:
        return None
    if extension_wave not in (1, 3, 5, None):
        return None
    if corrective_type not in (*_CORRECTIVE_TYPE_VALUES, None):
        return None
    if trend_strength not in _STRENGTH_VALUES:
        return None
    if not isinstance(target_levels, dict):
        return None
    level_1_618 = target_levels.get("level_1_618")
    level_2_0 = target_levels.get("level_2_0")
    if not _is_number(level_1_618) or not _is_number(level_2_0):
        return None
    if not _is_number(confidence) or confidence < 0 or confidence > 100:
        return None
    if not _is_non_empty_string(rationale):
        return None
    return {
        "wave_confirmation": wave_confirmation,
        "extension_wave": extension_wave,
        "corrective_type": corrective_type,
        "trend_strength": trend_strength,
        "target_levels": {"level_1_618": level_1_618, "level_2_0": level_2_0},
        "confidence": confidence,
        "rationale": rationale,
    }


_CHANLUN_TREND_VALUES = ("up", "down", "range")
_HUB_STATE_VALUES = ("forming", "active", "none")


def validate_chanlun_analyst_result(data: Any) -> dict[str, Any] | None:
    if not isinstance(data, dict):
        return None
    trend = data.get("trend")
    strength = data.get("strength")
    latest_signal = data.get("latest_signal")
    hub_state = data.get("hub_state")
    confidence = data.get("confidence")
    rationale = data.get("rationale")
    if trend not in _CHANLUN_TREND_VALUES:
        return None
    if strength not in _STRENGTH_VALUES:
        return None
    if latest_signal not in ("buy", "sell", "hold"):
        return None
    if hub_state not in _HUB_STATE_VALUES:
        return None
    if not _is_number(confidence) or confidence < 0 or confidence > 100:
        return None
    if not _is_non_empty_string(rationale):
        return None
    return {
        "trend": trend,
        "strength": strength,
        "latest_signal": latest_signal,
        "hub_state": hub_state,
        "confidence": confidence,
        "rationale": rationale,
    }


_HARMONIC_PATTERN_VALUES = (
    "gartley",
    "bat",
    "butterfly",
    "crab",
    "abcd",
    "cypher",
    "shark",
    "deep_crab",
    "none",
)


def validate_harmonic_analysis_result(data: Any) -> dict[str, Any] | None:
    if not isinstance(data, dict):
        return None
    detected_pattern = data.get("detected_pattern")
    direction = data.get("direction")
    timeframe = data.get("timeframe")
    confidence = data.get("confidence")
    d_zone_price = data.get("d_zone_price")
    entry_zone = data.get("entry_zone")
    stop_loss = data.get("stop_loss")
    take_profit_1 = data.get("take_profit_1")
    take_profit_2 = data.get("take_profit_2")
    rationale = data.get("rationale")
    if detected_pattern not in _HARMONIC_PATTERN_VALUES:
        return None
    if direction not in _BIAS_VALUES:
        return None
    if not isinstance(timeframe, str):
        return None
    completion_pct = data.get("completion_pct")
    if completion_pct is not None and (not _is_number(completion_pct) or completion_pct < 0 or completion_pct > 100):
        return None
    is_active = data.get("is_active")
    if is_active is not None and not isinstance(is_active, bool):
        return None
    if not _is_number(confidence) or confidence < 0 or confidence > 100:
        return None
    if (
        not _is_number(d_zone_price)
        or not _is_number(stop_loss)
        or not _is_number(take_profit_1)
        or not _is_number(take_profit_2)
    ):
        return None
    if not isinstance(entry_zone, str):
        return None
    if not _is_non_empty_string(rationale):
        return None
    result: dict[str, Any] = {
        "detected_pattern": detected_pattern,
        "direction": direction,
        "timeframe": timeframe,
        "confidence": confidence,
        "d_zone_price": d_zone_price,
        "entry_zone": entry_zone,
        "stop_loss": stop_loss,
        "take_profit_1": take_profit_1,
        "take_profit_2": take_profit_2,
        "rationale": rationale,
    }
    if completion_pct is not None:
        result["completion_pct"] = completion_pct
    if is_active is not None:
        result["is_active"] = is_active
    return result


_ARBITRATION_DIRECTION_VALUES = ("buy", "sell", "hold", "close", "dual")
_ACTION_VALUES = ("open", "close", "modify", "hold")


def validate_dow_theory(data: Any) -> dict[str, Any] | None:
    if not isinstance(data, dict):
        return None
    primary_trend = data.get("primary_trend")
    primary_phase = data.get("primary_phase")
    secondary_trend = data.get("secondary_trend")
    short_term_trend = data.get("short_term_trend")
    multi_tf_confirm = data.get("multi_tf_confirm")
    rationale = data.get("rationale")
    if primary_trend not in _BIAS_VALUES:
        return None
    if primary_phase not in ("accumulation", "markup", "distribution", "markdown"):
        return None
    if secondary_trend not in _BIAS_VALUES:
        return None
    if short_term_trend not in _BIAS_VALUES:
        return None
    if not isinstance(multi_tf_confirm, bool):
        return None
    if not isinstance(rationale, str):
        return None
    return {
        "primary_trend": primary_trend,
        "primary_phase": primary_phase,
        "secondary_trend": secondary_trend,
        "short_term_trend": short_term_trend,
        "multi_tf_confirm": multi_tf_confirm,
        "rationale": rationale,
    }


_WAVE_DIRECTION_VALUES = ("impulse_up", "impulse_down", "corrective", "unclear")


def validate_wave_theory(data: Any) -> dict[str, Any] | None:
    if not isinstance(data, dict):
        return None
    current_wave = data.get("current_wave")
    wave_direction = data.get("wave_direction")
    wave_count = data.get("wave_count")
    next_target = data.get("next_target")
    confidence = data.get("confidence")
    rationale = data.get("rationale")
    if not isinstance(current_wave, str) or not isinstance(wave_count, str) or not isinstance(next_target, str):
        return None
    if wave_direction not in _WAVE_DIRECTION_VALUES:
        return None
    if not _is_number(confidence) or confidence < 0 or confidence > 100:
        return None
    if not isinstance(rationale, str):
        return None
    return {
        "current_wave": current_wave,
        "wave_direction": wave_direction,
        "wave_count": wave_count,
        "next_target": next_target,
        "confidence": confidence,
        "rationale": rationale,
    }


_ZHONGSHU_STATE_VALUES = ("forming", "active", "breaking_up", "breaking_down", "none")
_BUY_SELL_POINT_VALUES = ("buy_1", "buy_2", "buy_3", "sell_1", "sell_2", "sell_3", "none")


def validate_chanlun_theory(data: Any) -> dict[str, Any] | None:
    if not isinstance(data, dict):
        return None
    trend = data.get("trend")
    bi_direction = data.get("bi_direction")
    duan_direction = data.get("duan_direction")
    zhongshu_state = data.get("zhongshu_state")
    buy_sell_point = data.get("buy_sell_point")
    confidence = data.get("confidence")
    rationale = data.get("rationale")
    if trend not in _CHANLUN_TREND_VALUES:
        return None
    if bi_direction not in ("up", "down", "none"):
        return None
    if duan_direction not in ("up", "down", "none"):
        return None
    if zhongshu_state not in _ZHONGSHU_STATE_VALUES:
        return None
    if buy_sell_point not in _BUY_SELL_POINT_VALUES:
        return None
    if not _is_number(confidence) or confidence < 0 or confidence > 100:
        return None
    if not isinstance(rationale, str):
        return None
    return {
        "trend": trend,
        "bi_direction": bi_direction,
        "duan_direction": duan_direction,
        "zhongshu_state": zhongshu_state,
        "buy_sell_point": buy_sell_point,
        "confidence": confidence,
        "rationale": rationale,
    }


def validate_harmonic_theory(data: Any) -> dict[str, Any] | None:
    if not isinstance(data, dict):
        return None
    pattern = data.get("pattern")
    direction = data.get("direction")
    confidence = data.get("confidence")
    rationale = data.get("rationale")
    if pattern not in _HARMONIC_PATTERN_VALUES:
        return None
    if direction not in _BIAS_VALUES:
        return None
    if not _is_number(confidence) or confidence < 0 or confidence > 100:
        return None
    if not isinstance(rationale, str):
        return None
    return {
        "pattern": pattern,
        "direction": direction,
        "confidence": confidence,
        "rationale": rationale,
    }


_TRADE_DIRECTION_VALUES = ("buy", "sell", "hold")


def validate_trade_recommendation(data: Any) -> dict[str, Any] | None:
    if not isinstance(data, dict):
        return None
    direction = data.get("direction")
    entry_price = data.get("entry_price")
    stop_loss = data.get("stop_loss")
    take_profit_1 = data.get("take_profit_1")
    risk_reward_ratio = data.get("risk_reward_ratio")
    position_size_lots = data.get("position_size_lots")
    rationale = data.get("rationale")
    take_profit_2 = data.get("take_profit_2")
    if direction not in _TRADE_DIRECTION_VALUES:
        return None
    if not _is_number(entry_price) or not _is_number(stop_loss) or not _is_number(take_profit_1):
        return None
    if take_profit_2 is not None and not _is_number(take_profit_2):
        return None
    if not _is_number(risk_reward_ratio) or risk_reward_ratio < 0:
        return None
    if not _is_non_empty_string(position_size_lots):
        return None
    if not _is_non_empty_string(rationale):
        return None
    result: dict[str, Any] = {
        "direction": direction,
        "entry_price": entry_price,
        "stop_loss": stop_loss,
        "take_profit_1": take_profit_1,
        "risk_reward_ratio": risk_reward_ratio,
        "position_size_lots": position_size_lots,
        "rationale": rationale,
    }
    if take_profit_2 is not None:
        result["take_profit_2"] = take_profit_2
    return result


def validate_arbitration_result(data: Any) -> dict[str, Any] | None:
    if not isinstance(data, dict):
        return None
    final_direction = data.get("final_direction")
    confidence = data.get("confidence")
    primary_contradiction = data.get("primary_contradiction")
    phase = data.get("phase")
    reasoning = data.get("reasoning")
    action = data.get("action")
    united_front_analysis = data.get("united_front_analysis")
    if final_direction not in _ARBITRATION_DIRECTION_VALUES:
        return None
    if not _is_number(confidence) or confidence < 0 or confidence > 100:
        return None
    if not isinstance(primary_contradiction, str) or not isinstance(phase, str):
        return None
    if not _is_non_empty_string(reasoning):
        return None
    if action not in _ACTION_VALUES:
        return None
    if not isinstance(united_front_analysis, str):
        return None
    result: dict[str, Any] = {
        "final_direction": final_direction,
        "confidence": confidence,
        "primary_contradiction": primary_contradiction,
        "phase": phase,
        "reasoning": reasoning,
        "action": action,
        "united_front_analysis": united_front_analysis,
    }
    optional_fields: dict[str, Any] = {
        "dow_theory": validate_dow_theory,
        "wave_theory": validate_wave_theory,
        "chanlun_theory": validate_chanlun_theory,
        "harmonic_theory": validate_harmonic_theory,
        "trade_recommendation": validate_trade_recommendation,
    }
    for key, validator in optional_fields.items():
        value = data.get(key)
        if value is None:
            continue
        parsed = validator(value)
        if parsed is None:
            return None
        result[key] = parsed
    return result


def validate_comprehensive_data(data: Any) -> dict[str, Any] | None:
    if not isinstance(data, dict):
        return None
    technical = validate_technical_analysis(data.get("technical"))
    if technical is None:
        return None
    wave = validate_wave_analyst_result(data.get("wave"))
    if wave is None:
        return None
    chanlun = validate_chanlun_analyst_result(data.get("chanlun"))
    if chanlun is None:
        return None
    risk = validate_risk_assessment(data.get("risk"))
    if risk is None:
        return None
    arbitration = validate_arbitration_result(data.get("arbitration"))
    if arbitration is None:
        return None
    result: dict[str, Any] = {
        "technical": technical,
        "wave": wave,
        "chanlun": chanlun,
        "risk": risk,
        "arbitration": arbitration,
    }
    harmonic = data.get("harmonic")
    if harmonic is not None:
        parsed_harmonic = validate_harmonic_analysis_result(harmonic)
        if parsed_harmonic is None:
            return None
        result["harmonic"] = parsed_harmonic
    return result


def clean_sr_levels(data: Any) -> Any:
    """校验前清理 S/R 数组:过滤 price 非数字或为 null/undefined 的 level。"""
    if not isinstance(data, dict):
        return data
    obj = data
    for key in ("support_levels", "resistance_levels"):
        if isinstance(obj.get(key), list):
            obj[key] = [
                level
                for level in obj[key]
                if isinstance(level, dict)
                and level.get("price") is not None
                and isinstance(level.get("price"), (int, float))
                and not isinstance(level.get("price"), bool)
            ]
    return obj


# ─── 交易业务校验(镜像 utils/price-validator.ts 的 validateTradeRecommendation /
#      validateArbitrationResult,供 comprehensive-analyst / mao-arbitrator 共用) ──


class TradeValidation:
    """镜像 TradeValidationResult:valid / warnings / fixedTrade / fixedArbitration。"""

    def __init__(
        self,
        *,
        valid: bool,
        warnings: list[str],
        fixed_trade: dict[str, Any] | None = None,
        fixed_arbitration: dict[str, Any] | None = None,
    ) -> None:
        self.valid = valid
        self.warnings = warnings
        self.fixed_trade = fixed_trade
        self.fixed_arbitration = fixed_arbitration


def validate_trade_recommendation_business(
    trade: dict[str, Any],
    current_price: float,
    profile: dict[str, Any],
) -> TradeValidation:
    """镜像 validateTradeRecommendation:SL/TP 方向、RR 比例、±50% 区间、entry>0。

    返回 warnings 与修复后的 trade(direction->hold 当 SL/TP 被清零)。
    """
    warnings: list[str] = []
    fixed = dict(trade)

    if not trade.get("entry_price") or trade["entry_price"] <= 0:
        warnings.append(f"entry_price {trade.get('entry_price')} is invalid — zeroing trade")
        fixed_trade = dict(trade)
        fixed_trade["direction"] = "hold"
        return TradeValidation(valid=False, warnings=warnings, fixed_trade=fixed_trade)

    price_checks: list[tuple[str, float]] = [
        ("entry_price", fixed["entry_price"]),
        ("stop_loss", fixed["stop_loss"]),
        ("take_profit_1", fixed["take_profit_1"]),
    ]
    if fixed.get("take_profit_2") is not None and fixed.get("take_profit_2", 0) > 0:
        price_checks.append(("take_profit_2", fixed["take_profit_2"]))
    for label, price in price_checks:
        if price > 0 and not validate_price_range(price, current_price, profile, label):
            warnings.append(f"{label} {price} outside valid range for current price {current_price}")

    if fixed.get("direction") == "buy":
        if fixed.get("stop_loss") is not None and fixed["stop_loss"] >= fixed["entry_price"] and fixed["stop_loss"] > 0:
            warnings.append(
                "BUY trade: stop_loss "
                f"{fixed['stop_loss']} >= entry {fixed['entry_price']} — wrong side, clamping below entry"
            )
            fixed["stop_loss"] = 0
        if (
            fixed.get("take_profit_1") is not None
            and fixed["take_profit_1"] <= fixed["entry_price"]
            and fixed["take_profit_1"] > 0
        ):
            warnings.append(
                "BUY trade: take_profit_1 "
                f"{fixed['take_profit_1']} <= entry {fixed['entry_price']} — wrong side, clamping above entry"
            )
            fixed["take_profit_1"] = 0
    elif fixed.get("direction") == "sell":
        if fixed.get("stop_loss") is not None and fixed["stop_loss"] <= fixed["entry_price"] and fixed["stop_loss"] > 0:
            warnings.append(
                "SELL trade: stop_loss "
                f"{fixed['stop_loss']} <= entry {fixed['entry_price']} — wrong side, clamping above entry"
            )
            fixed["stop_loss"] = 0
        if (
            fixed.get("take_profit_1") is not None
            and fixed["take_profit_1"] >= fixed["entry_price"]
            and fixed["take_profit_1"] > 0
        ):
            warnings.append(
                "SELL trade: take_profit_1 "
                f"{fixed['take_profit_1']} >= entry {fixed['entry_price']} — wrong side, clamping below entry"
            )
            fixed["take_profit_1"] = 0

    if (
        fixed.get("stop_loss") is not None
        and fixed["stop_loss"] > 0
        and fixed.get("take_profit_1") is not None
        and fixed["take_profit_1"] > 0
        and fixed.get("entry_price", 0) > 0
    ):
        sl_dist = abs(fixed["entry_price"] - fixed["stop_loss"])
        reward_target = (
            fixed["take_profit_2"]
            if fixed.get("take_profit_2") is not None and fixed.get("take_profit_2", 0) > 0
            else fixed["take_profit_1"]
        )
        tp_dist = abs(reward_target - fixed["entry_price"])
        if sl_dist > 0:
            rr = tp_dist / sl_dist
            if rr < 0.4:
                warnings.append(f"Risk/reward ratio {rr:.2f} < 0.4 — unfavorable trade")
            fixed["risk_reward_ratio"] = float(f"{rr:.2f}")

    is_valid = fixed.get("direction") == "hold" or (
        fixed.get("stop_loss", 0) > 0 and fixed.get("take_profit_1", 0) > 0
    )
    final_trade = fixed if is_valid else {**fixed, "direction": "hold"}
    return TradeValidation(
        valid=is_valid and len(warnings) == 0,
        warnings=warnings,
        fixed_trade=final_trade,
    )


def validate_arbitration_business(
    arb: dict[str, Any],
    current_price: float,
    profile: dict[str, Any],
) -> TradeValidation:
    """镜像 validateArbitrationResult:嵌入 trade_recommendation 的业务校验。"""
    trade = arb.get("trade_recommendation")
    if not trade:
        return TradeValidation(valid=True, warnings=[], fixed_arbitration=arb)

    if trade.get("direction") == "hold" and trade.get("entry_price", 0) <= 0:
        return TradeValidation(valid=True, warnings=[], fixed_arbitration=arb)

    trade_result = validate_trade_recommendation_business(trade, current_price, profile)

    if trade_result.fixed_trade is not None:
        fixed_arbitration: dict[str, Any] = {**arb, "trade_recommendation": trade_result.fixed_trade}
    else:
        fixed_arbitration = {**arb}

    if trade_result.valid:
        return TradeValidation(
            valid=True,
            warnings=trade_result.warnings,
            fixed_trade=trade_result.fixed_trade,
            fixed_arbitration=fixed_arbitration,
        )

    downgraded: dict[str, Any] = {
        **arb,
        "final_direction": "hold",
        "action": "hold",
        "confidence": min(arb.get("confidence", 0), 20),
        "trade_recommendation": trade_result.fixed_trade or arb.get("trade_recommendation"),
    }
    return TradeValidation(
        valid=False,
        warnings=[*trade_result.warnings, "Trade downgraded to hold due to invalid SL/TP"],
        fixed_trade=trade_result.fixed_trade,
        fixed_arbitration=downgraded,
    )


# ─── 最小结构化分析器(依赖 tools/elliott-wave.ts / tools/chanlun-core.ts,
#     待其他 M7 worker 移植后对齐;此处仅保证确定性输出,供 StructureCache 使用) ──


def build_unavailable_wave_structure() -> dict[str, Any]:
    return {
        "direction": "bullish",
        "swingPoints": [],
        "impulseWaves": [],
        "correctiveWaves": [],
        "validation": {
            "isValid": False,
            "violations": ["Insufficient closed bars for stable Elliott Wave analysis."],
        },
        "confidence": 0,
    }


def build_unavailable_chanlun_structure() -> dict[str, Any]:
    return {"processedBars": [], "fractals": [], "strokes": [], "hubs": []}


def analyze_elliott_wave(prices: list[float]) -> dict[str, Any]:
    """最小 Elliott Wave 结构分析(确定性实现,待 tools/elliott-wave.ts 移植后对齐)。

    输入为闭市 bar 的 close 序列;这里用局部高低点生成 swingPoints,
    并给出保守的 validation 结果,足以支撑 StructureCache 的稳定哈希。
    """
    if len(prices) < 2:
        return build_unavailable_wave_structure()
    swing_points: list[dict[str, Any]] = []
    lookback = 2
    for i in range(lookback, len(prices) - lookback):
        window = prices[i - lookback : i + lookback + 1]
        if prices[i] == max(window):
            swing_points.append({"index": i, "price": prices[i], "type": "high"})
        if prices[i] == min(window):
            swing_points.append({"index": i, "price": prices[i], "type": "low"})
    direction = "bearish" if prices[-1] < prices[0] else "bullish"
    return {
        "direction": direction,
        "swingPoints": swing_points,
        "impulseWaves": [],
        "correctiveWaves": [],
        "validation": {
            "isValid": len(swing_points) >= 3,
            "violations": (
                [] if len(swing_points) >= 3 else ["Insufficient swing points for Elliott Wave labeling."]
            ),
        },
        "confidence": len(swing_points) >= 3,
    }


def analyze_chanlun(bars: list[dict[str, Any]]) -> dict[str, Any]:
    """最小缠论结构分析(确定性实现,待 tools/chanlun-core.ts 移植后对齐)。

    bars 为 {index, open, high, low, close};生成分型(fractals),笔(strokes)
    与中枢(hubs)为空,足以支撑 StructureCache 的稳定哈希。
    """
    if len(bars) < 3:
        return build_unavailable_chanlun_structure()
    fractals: list[dict[str, Any]] = []
    for i in range(1, len(bars) - 1):
        prev_bar = bars[i - 1]
        bar = bars[i]
        next_bar = bars[i + 1]
        if bar["high"] > prev_bar["high"] and bar["high"] > next_bar["high"]:
            fractals.append({"type": "top", "index": bar["index"], "price": bar["high"]})
        if bar["low"] < prev_bar["low"] and bar["low"] < next_bar["low"]:
            fractals.append({"type": "bottom", "index": bar["index"], "price": bar["low"]})
    return {
        "processedBars": bars,
        "fractals": fractals,
        "strokes": [],
        "hubs": [],
    }
