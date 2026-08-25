"""镜像 apps/app-agent/src/config/symbol-profile.ts。

SymbolProfile — 每个品种的特征,用于 AI 分析上下文注入。
getSymbolProfile / detectCrossInstrumentPrice 语义 1:1 对齐 TS。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, replace
from typing import Literal

__all__ = [
    "DEFAULT_MAX_LOTS",
    "DEFAULT_MIN_LOTS",
    "AtrRange",
    "AssetClass",
    "SymbolProfile",
    "detect_cross_instrument_price",
    "get_symbol_profile",
]

DEFAULT_MIN_LOTS = 0.01
DEFAULT_MAX_LOTS = 0.5
MICRO_CONTRACT_MIN_LOTS = 0.1

AssetClass = Literal["metal", "forex", "index", "energy", "commodity", "crypto"]
VolatilityLevel = Literal["high", "medium", "low"]


@dataclass
class AtrRange:
    min: float
    max: float


@dataclass
class SymbolProfile:
    symbol: str
    name: str
    pricePrecision: int
    pipValue: float
    typicalAtrRange: dict[str, AtrRange]
    slAtrMultiplier: float
    tpAtrMultiplier: float
    volatilityLevel: VolatilityLevel
    priceRangeHint: str
    assetClass: AssetClass
    volumeReliable: bool
    minLots: float = DEFAULT_MIN_LOTS
    maxLots: float = DEFAULT_MAX_LOTS
    priceRange: tuple[float, float] | None = None


def _build_profile(
    symbol: str,
    name: str,
    price_precision: int,
    pip_value: float,
    atr_ranges: dict[str, tuple[float, float]],
    sl_atr_multiplier: float,
    tp_atr_multiplier: float,
    volatility: VolatilityLevel,
    price_range_hint: str,
    asset_class: AssetClass,
    volume_reliable: bool,
    price_range: tuple[float, float] | None,
) -> SymbolProfile:
    return SymbolProfile(
        symbol=symbol,
        name=name,
        pricePrecision=price_precision,
        pipValue=pip_value,
        typicalAtrRange={tf: AtrRange(min=lo, max=hi) for tf, (lo, hi) in atr_ranges.items()},
        slAtrMultiplier=sl_atr_multiplier,
        tpAtrMultiplier=tp_atr_multiplier,
        volatilityLevel=volatility,
        priceRangeHint=price_range_hint,
        assetClass=asset_class,
        volumeReliable=volume_reliable,
        priceRange=price_range,
    )


PROFILES: dict[str, SymbolProfile] = {
    "XAUUSD": _build_profile(
        "XAUUSD", "黄金/美元 (Gold/USD)", 2, 0.1,
        {"M15": (1.5, 8), "M30": (2, 12), "H1": (4, 25), "H4": (10, 60)},
        1.5, 3.0, "medium", "typically 1800–4500 USD/oz", "metal", True, (1800, 4500),
    ),
    "GOLD": _build_profile(
        "GOLD", "黄金 (Gold)", 2, 0.1,
        {"M15": (1.5, 8), "M30": (2, 12), "H1": (4, 25), "H4": (10, 60)},
        1.5, 3.0, "medium", "typically 1800–4500", "metal", True, (1800, 4500),
    ),
    "GBPJPY": _build_profile(
        "GBPJPY", "英镑/日元 (British Pound/Japanese Yen)", 3, 0.01,
        {"M15": (0.05, 0.30), "M30": (0.08, 0.45), "H1": (0.15, 0.80), "H4": (0.30, 1.50)},
        1.8, 3.5, "high", "typically 150–250 JPY per GBP", "forex", False, (150, 250),
    ),
    "EURJPY": _build_profile(
        "EURJPY", "欧元/日元 (Euro/Japanese Yen)", 3, 0.01,
        {"M15": (0.04, 0.25), "M30": (0.06, 0.40), "H1": (0.12, 0.70), "H4": (0.25, 1.30)},
        1.8, 3.5, "high", "typically 130–200 JPY per EUR", "forex", False, (130, 200),
    ),
    "USDJPY": _build_profile(
        "USDJPY", "美元/日元 (US Dollar/Japanese Yen)", 3, 0.01,
        {"M15": (0.03, 0.20), "M30": (0.05, 0.35), "H1": (0.10, 0.60), "H4": (0.20, 1.10)},
        1.5, 3.0, "medium", "typically 120–180 JPY per USD", "forex", False, (120, 180),
    ),
    "XAGUSD": _build_profile(
        "XAGUSD", "白银/美元 (Silver/USD)", 3, 0.01,
        {"M15": (0.03, 0.20), "M30": (0.05, 0.30), "H1": (0.10, 0.60), "H4": (0.20, 1.20)},
        1.5, 3.0, "high", "typically 20–40 USD/oz", "metal", True, (15, 50),
    ),
    "US100CASH": _build_profile(
        "US100CASH", "纳斯达克100指数 (US100 Cash CFD)", 2, 1.0,
        {"M15": (30, 200), "M30": (50, 400), "H1": (100, 800), "H4": (300, 2000)},
        0.8, 2.5, "high", "typically 15000–35000 USD", "index", True, (15000, 35000),
    ),
    "USOILCASH": _build_profile(
        "USOILCASH", "WTI原油 (US Oil Cash CFD)", 2, 0.01,
        {"M15": (0.2, 1.5), "M30": (0.3, 2.5), "H1": (0.5, 4.0), "H4": (1.0, 8.0)},
        2.0, 3.5, "medium", "typically 60–100 USD/barrel", "commodity", True, (40, 120),
    ),
    "UKOILCASH": _build_profile(
        "UKOILCASH", "布伦特原油 (UK Oil Cash CFD)", 2, 0.01,
        {"M15": (0.2, 1.5), "M30": (0.3, 2.5), "H1": (0.6, 4.5), "H4": (1.2, 9.0)},
        2.0, 3.5, "medium", "typically 65–105 USD/barrel", "commodity", True, (40, 120),
    ),
}

MICRO_BASE_ALIASES: dict[str, str] = {
    "SILVERM": "XAGUSD",
}


def _has_micro_contract_suffix(raw_symbol: str) -> bool:
    normalized = raw_symbol.strip()
    if "#" in normalized:
        return True
    return bool(re.search(r"m$", re.sub(r"[^A-Za-z0-9]", "", normalized), re.IGNORECASE))


def _with_lot_bounds(profile: SymbolProfile, raw_symbol: str) -> SymbolProfile:
    min_lots = (
        MICRO_CONTRACT_MIN_LOTS
        if _has_micro_contract_suffix(raw_symbol)
        else (profile.minLots or DEFAULT_MIN_LOTS)
    )
    return replace(profile, minLots=min_lots, maxLots=profile.maxLots or DEFAULT_MAX_LOTS)


def get_symbol_profile(raw_symbol: str) -> SymbolProfile:
    """镜像 getSymbolProfile:先精确匹配,再剥离常见后缀(m/#/./等)找基符号,
    再做前缀匹配;都失败则构造通用 forex profile。"""
    # Try exact match first
    if raw_symbol in PROFILES:
        return _with_lot_bounds(PROFILES[raw_symbol], raw_symbol)

    # Strip common suffixes (m, #, ., etc.) to find base symbol
    base = re.sub(r"[^A-Za-z0-9]", "", raw_symbol).upper()
    alias = MICRO_BASE_ALIASES.get(base)
    if alias and alias in PROFILES:
        return _with_lot_bounds(PROFILES[alias], raw_symbol)

    if base in PROFILES:
        return _with_lot_bounds(PROFILES[base], raw_symbol)

    # Try prefix matching for known patterns (one-way only: input starts with known key)
    # e.g., "XAUUSDm" → matches "XAUUSD"; but "XAG" should NOT match "XAUUSD"
    for key, profile in PROFILES.items():
        if base.startswith(key):
            return _with_lot_bounds(profile, raw_symbol)

    # Fallback: construct a generic forex profile
    return _with_lot_bounds(
        SymbolProfile(
            symbol=raw_symbol,
            name=raw_symbol,
            pricePrecision=3,
            pipValue=0.01,
            typicalAtrRange={
                "M15": AtrRange(min=0.01, max=1),
                "M30": AtrRange(min=0.02, max=2),
                "H1": AtrRange(min=0.05, max=5),
                "H4": AtrRange(min=0.1, max=10),
            },
            slAtrMultiplier=1.5,
            tpAtrMultiplier=3.0,
            volatilityLevel="medium",
            priceRangeHint="unknown — validate prices against current market data",
            # No priceRange for generic fallback — validation will use current price heuristic
            assetClass="forex",
            volumeReliable=False,
        ),
        raw_symbol,
    )


def detect_cross_instrument_price(
    target_symbol: str,
    price: float,
    current_price: float,
    all_current_prices: dict[str, float],
) -> str | None:
    """交叉品种价格污染检测。

    当 LLM 输出价格更接近另一品种当前价时判为污染。返回可疑品种符号,
    合法则返回 None。
    """
    if (
        not _is_finite(price) or price <= 0 or not _is_finite(current_price) or current_price <= 0
    ):
        return None

    target_profile = get_symbol_profile(target_symbol)

    for other_symbol, other_price in all_current_prices.items():
        # Skip self
        if other_symbol == target_symbol:
            continue
        # Skip if other price is unavailable
        if not _is_finite(other_price) or other_price <= 0:
            continue

        other_profile = get_symbol_profile(other_symbol)

        # 仅检查不同资产类别或价格层级;同资产类别(如 USOILCASH vs UKOILCASH)
        # 价格相近属正常,跳过
        if target_profile.assetClass == other_profile.assetClass and target_profile.assetClass != "forex":
            continue

        # JPY crosses 共享 'forex' 资产类别,但基础货币不同,允许交叉污染检查

        dist_to_other = abs(price - other_price)
        dist_to_target = abs(price - current_price)

        # 若价格相较自身更接近另一品种当前价
        ratio = dist_to_other / dist_to_target if dist_to_target > 0 else math.inf

        # Heuristic: 价格在另一品种 ±8% 内,且距离是自身的 1/3 以下 → 交叉污染
        near_other = dist_to_other / other_price < 0.08
        closer_to_other = ratio < 0.33

        if near_other and closer_to_other:
            return other_symbol

    return None


def _is_finite(value: float) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))
