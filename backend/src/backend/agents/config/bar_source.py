"""镜像 apps/app-agent/src/config/bar-source.service.ts。

BarSourceService — 决定"某个 account+symbol 的 K 线从哪个账户取",
以及 atrOf 镜像(优先 bar.atr → true range 均值 → indicators 回退)。
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

__all__ = [
    "DEFAULT_ATR_PERIOD",
    "BarSourceResolution",
    "BarSourceService",
    "atr_of",
    "canonical_symbol",
]

PREFERRED_ATR_TIMEFRAMES: tuple[str, ...] = ("H1", "M30", "M15", "H4")
DEFAULT_ATR_PERIOD = 14


@dataclass
class BarSourceResolution:
    canonicalSymbol: str
    sourceAccount: str
    sourceSymbol: str
    useShared: bool


class ConfigLike(Protocol):
    market_bar_account: str


class GoldbotApiLike(Protocol):
    async def fetch_account_symbols(self, account_id: str) -> dict[str, Any]: ...


def _clean_symbol(symbol: str) -> str:
    """镜像 cleanSymbol:去除非字母数字后大写。"""
    return "".join(ch for ch in symbol.strip() if ch.isalnum()).upper()


def canonical_symbol(symbol: str) -> str:
    """镜像 canonicalSymbol:清理后按规则映射到标准品种。"""
    cleaned = _clean_symbol(symbol)
    if cleaned == "GOLD" or cleaned == "GOLDM" or cleaned.startswith("XAUUSD"):
        return "XAUUSD"
    if cleaned == "SILVER" or cleaned == "SILVERM" or cleaned.startswith("XAGUSD"):
        return "XAGUSD"
    if cleaned == "US100CASH":
        return "US100CASH"
    return cleaned


def _finite_number(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)):
        return float(value)
    return None


def _bars_for(payload: Mapping[str, Any], timeframe: str) -> list[dict[str, Any]]:
    bars = payload.get("bars")
    if not isinstance(bars, dict):
        return []
    exact = bars.get(timeframe)
    if isinstance(exact, list):
        return exact
    for candidate, matched in bars.items():
        if isinstance(candidate, str) and candidate.upper() == timeframe and isinstance(matched, list):
            return matched
    return []


def _true_range(bar: Mapping[str, Any], previous_close: float | None) -> float | None:
    high = _finite_number(bar.get("high"))
    low = _finite_number(bar.get("low"))
    if high is None or low is None:
        return None
    if previous_close is None:
        return high - low
    return max(high - low, abs(high - previous_close), abs(low - previous_close))


def atr_of(payload: Mapping[str, Any], period: int = DEFAULT_ATR_PERIOD) -> float:
    """镜像 atrOf:按 H1→M30→M15→H4 优先级取 bar.atr > 0;否则用最近 period 根
    true range 均值;再回退到 indicators[timeframe].atr;全无返回 0。"""
    for timeframe in PREFERRED_ATR_TIMEFRAMES:
        bars = _bars_for(payload, timeframe)
        latest_bar_atr: float | None = None
        for bar in reversed(bars):
            atr = _finite_number(bar.get("atr"))
            if atr is not None and atr > 0:
                latest_bar_atr = atr
                break
        if latest_bar_atr is not None:
            return latest_bar_atr

        if len(bars) < 2:
            continue

        ranges: list[float] = []
        for index in range(len(bars)):
            previous_close = _finite_number(bars[index - 1].get("close")) if index > 0 else None
            tr = _true_range(bars[index], previous_close)
            if tr is not None and tr > 0:
                ranges.append(tr)
        sample = ranges[-period:]
        if len(sample) > 0:
            return sum(sample) / len(sample)

    for timeframe in PREFERRED_ATR_TIMEFRAMES:
        indicators = payload.get("indicators")
        if not isinstance(indicators, dict):
            continue
        indicator = indicators.get(timeframe)
        if indicator is None:
            indicator = indicators.get(timeframe.lower())
        if isinstance(indicator, dict):
            atr = _finite_number(indicator.get("atr"))
            if atr is not None and atr > 0:
                return atr

    return 0


class BarSourceService:
    """镜像 BarSourceService:账户符号缓存 + 主账户(market bar)取数判定。"""

    def __init__(self, config: ConfigLike, goldbot_api: GoldbotApiLike) -> None:
        self._config = config
        self._goldbot_api = goldbot_api
        self._symbols_cache: dict[str, list[str]] = {}

    async def bar_source_for(self, account_id: str, symbol: str) -> BarSourceResolution:
        canonical = canonical_symbol(symbol)
        market_account = self._config.market_bar_account

        if market_account == account_id:
            return BarSourceResolution(
                canonicalSymbol=canonical,
                sourceAccount=account_id,
                sourceSymbol=symbol,
                useShared=False,
            )

        market_symbols = await self._fetch_account_symbols(market_account)
        source_symbol = next(
            (candidate for candidate in market_symbols if canonical_symbol(candidate) == canonical),
            None,
        )
        if source_symbol is None:
            return BarSourceResolution(
                canonicalSymbol=canonical,
                sourceAccount=account_id,
                sourceSymbol=symbol,
                useShared=False,
            )

        return BarSourceResolution(
            canonicalSymbol=canonical,
            sourceAccount=market_account,
            sourceSymbol=source_symbol,
            useShared=True,
        )

    async def account_symbols(self, account_id: str) -> list[str]:
        return await self._fetch_account_symbols(account_id)

    async def _fetch_account_symbols(self, account_id: str) -> list[str]:
        cached = self._symbols_cache.get(account_id)
        if cached is not None:
            return list(cached)

        try:
            result = await self._goldbot_api.fetch_account_symbols(account_id)
            raw_symbols = result.get("symbols", [])
            symbols: list[str] = []
            seen: set[str] = set()
            for item in raw_symbols:
                if not isinstance(item, str):
                    continue
                trimmed = item.strip()
                if trimmed and trimmed not in seen:
                    seen.add(trimmed)
                    symbols.append(trimmed)
            self._symbols_cache[account_id] = symbols
            return list(symbols)
        except Exception as err:  # noqa: BLE001 — 镜像 TS catch-all
            from backend.agents.utils.logger import get_logger

            get_logger().warn(
                {"accountId": account_id, "err": str(err)},
                "barSource: failed to load ai_symbols",
            )
            return []
