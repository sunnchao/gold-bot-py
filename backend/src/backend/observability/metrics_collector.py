"""Store 状态 → 指标收集器(镜像 packages/observability/src/metrics-collector.ts)。"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from backend.observability._types import MetricsRegistry
from backend.persistence.store import EaStore

__all__ = ["create_store_metrics_collector"]


@dataclass
class StoreMetricsSnapshot:
    accounts: int = 0
    heartbeats: int = 0
    positions: int = 0


def as_number(value: Any) -> float | None:
    """等价 TS asNumber:数值 / 可解析字符串 → float,否则 None。"""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        parsed = float(value)
        if math.isfinite(parsed):
            return parsed
        return None
    if isinstance(value, str) and value.strip() != "":
        try:
            parsed = float(value)
        except ValueError:
            return None
        if math.isfinite(parsed):
            return parsed
    return None


def account_id_of(record: dict | None) -> str | None:
    if not record:
        return None
    value = record.get("account_id")
    if isinstance(value, str):
        return value
    value = record.get("accountId")
    return value if isinstance(value, str) else None


@dataclass
class StoreMetricsCollector:
    metrics: MetricsRegistry
    store: EaStore
    now: Callable[[], float] | None = None

    async def collect(self) -> StoreMetricsSnapshot:
        account_ids = await self.store.list_account_ids()
        positions_seen = 0
        heartbeats_seen = 0

        for account_id in account_ids:
            heartbeat = await self.store.get_heartbeat(account_id)
            if heartbeat:
                heartbeats_seen += 1
                ts = as_number(heartbeat.get("timestamp"))
                if ts is None:
                    ts = as_number(heartbeat.get("ts"))
                if ts is None:
                    ts = as_number(heartbeat.get("time"))
                if ts is not None:
                    self.metrics.ea_heartbeat_timestamp.labels(account_id).set(ts)
                else:
                    now_ms = (self.now() if self.now is not None else time.time() * 1000)
                    self.metrics.ea_heartbeat_timestamp.labels(account_id).set(math.floor(now_ms / 1000))
                equity = as_number(heartbeat.get("equity"))
                if equity is not None:
                    self.metrics.account_equity.labels(account_id).set(equity)
                balance = as_number(heartbeat.get("balance"))
                if balance is not None:
                    self.metrics.account_balance.labels(account_id).set(balance)
                floating_pl = as_number(heartbeat.get("floating_pl"))
                if floating_pl is None:
                    floating_pl = as_number(heartbeat.get("floatingPL"))
                if floating_pl is None:
                    floating_pl = as_number(heartbeat.get("profit"))
                if floating_pl is not None:
                    self.metrics.account_floating_pl.labels(account_id).set(floating_pl)
                daily_pl = as_number(heartbeat.get("daily_pl"))
                if daily_pl is None:
                    daily_pl = as_number(heartbeat.get("dailyPL"))
                if daily_pl is not None:
                    self.metrics.account_daily_pl.labels(account_id).set(daily_pl)

            symbols = await self.store.list_symbols(account_id)
            for symbol in symbols:
                tick = await self.store.get_latest_tick(account_id, symbol)
                if tick:
                    spread = as_number(tick.get("spread"))
                    if spread is not None:
                        self.metrics.spread_points.labels(account_id, symbol).set(spread)

                positions = await self.store.get_positions(account_id, symbol)
                positions_seen += len(positions)
                self.metrics.account_positions.labels(account_id, symbol).set(len(positions))

        return StoreMetricsSnapshot(
            accounts=len(account_ids), heartbeats=heartbeats_seen, positions=positions_seen
        )


def create_store_metrics_collector(options: dict) -> StoreMetricsCollector:
    """等价 TS createStoreMetricsCollector({metrics, store, now?})。"""
    return StoreMetricsCollector(
        metrics=options["metrics"],
        store=options["store"],
        now=options.get("now"),
    )
