"""镜像 packages/observability/src/metrics.spec.ts 的语义。"""

from __future__ import annotations

from typing import Any

import pytest
from prometheus_client import generate_latest

from backend.observability.metrics import (
    create_metrics_registry,
    http_status_class,
)
from backend.observability.metrics_collector import create_store_metrics_collector
from backend.observability.metrics_middleware import (
    create_http_metrics_middleware,
    normalize_http_path,
)
from backend.persistence import create_in_memory_store


def parse_samples(text: str) -> dict[tuple[str, tuple[tuple[str, str], ...]], float]:
    """把 Prometheus 文本转成 {(name, ((label, value), ...)): value} 的规范化映射。"""
    samples: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
    for line in text.splitlines():
        line = line.strip()
        if line == "" or line.startswith("#"):
            continue
        if "{" not in line:
            name, _, value = line.partition(" ")
            samples[(name, ())] = float(value)
            continue
        name, rest = line.split("{", 1)
        labels_text, _, value = rest.rpartition("} ")
        labels = tuple(
            (part.split("=", 1)[0], part.split("=", 1)[1].strip('"'))
            for part in labels_text.split(",")
        )
        samples[(name, labels)] = float(value)
    return samples


ALL_METRIC_NAMES = [
    "goldbot_signals_total",
    "goldbot_signal_score",
    "goldbot_orders_total",
    "goldbot_order_latency_seconds",
    "goldbot_order_profit_usd",
    "goldbot_account_equity_usd",
    "goldbot_account_balance_usd",
    "goldbot_account_positions",
    "goldbot_account_floating_pl_usd",
    "goldbot_account_daily_pl_usd",
    "goldbot_ea_last_heartbeat_timestamp",
    "goldbot_ea_heartbeats_total",
    "goldbot_ea_ticks_total",
    "goldbot_http_requests_total",
    "goldbot_http_request_duration_seconds",
    "goldbot_db_query_duration_seconds",
    "goldbot_db_queries_total",
    "goldbot_db_connections_open",
    "goldbot_db_connections_in_use",
    "goldbot_strategy_execution_seconds",
    "goldbot_strategy_win_rate",
    "goldbot_risk_gate_rejections_total",
    "goldbot_command_results_total",
    "goldbot_spread_points",
]


def text_of(metrics: Any) -> str:
    return generate_latest(metrics.registry).decode()


def labels_of(samples: dict[tuple[str, tuple[tuple[str, str], ...]], float], name: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for (sample_name, sample_labels), value in samples.items():
        if sample_name == name:
            out.append({key: value_ for key, value_ in sample_labels} | {"value": value})
    return out


# ---------------------------------------------------------------- createMetricsRegistry
class TestCreateMetricsRegistry:
    def test_exposes_all_goldbot_metrics_before_any_observation(self) -> None:
        metrics = create_metrics_registry(enable_default=False)
        text = text_of(metrics)
        for name in ALL_METRIC_NAMES:
            assert name in text

    def test_registers_exact_help_and_label_sets(self) -> None:
        import math

        metrics = create_metrics_registry(enable_default=False)
        # 信号计数:account_id/symbol/strategy/side
        assert metrics.signals_total._labelnames == ("account_id", "symbol", "strategy", "side")
        assert metrics.signals_total._documentation == "Total number of trading signals generated"
        # 订单延迟直方图桶序列(排除自动追加的 +Inf)
        bounds = [b for b in metrics.order_latency._upper_bounds if b != math.inf]
        assert bounds == [0.1, 0.5, 1, 2, 5, 10, 30]
        # 盈亏直方图桶序列
        profit_bounds = [b for b in metrics.order_profit._upper_bounds if b != math.inf]
        assert profit_bounds == [-1000, -500, -100, -50, 0, 50, 100, 500, 1000]
        # http 请求标签
        assert metrics.http_requests_total._labelnames == ("method", "path", "status")
        assert metrics.http_request_duration._labelnames == ("method", "path")

    def test_increments_counters_and_observes_histograms_with_labels(self) -> None:
        metrics = create_metrics_registry(enable_default=False)
        metrics.signals_total.labels("90011087", "XAUUSD", "pullback", "buy").inc()
        metrics.signal_score.labels("90011087", "pullback").observe(7)
        metrics.orders_total.labels("90011087", "XAUUSD", "buy", "success", "pullback").inc()
        metrics.http_requests_total.labels("GET", "/metrics", "2xx").inc()

        samples = parse_samples(text_of(metrics))
        assert labels_of(samples, "goldbot_signals_total") == [
            {"account_id": "90011087", "symbol": "XAUUSD", "strategy": "pullback", "side": "buy", "value": 1.0}
        ]
        assert labels_of(samples, "goldbot_http_requests_total") == [
            {"method": "GET", "path": "/metrics", "status": "2xx", "value": 1.0}
        ]
        # 直方图:分数 7 应计入 le=8.0 及以上桶
        score_buckets = [
            {"labels": dict(sample_labels), "value": value}
            for (sample_name, sample_labels), value in samples.items()
            if sample_name == "goldbot_signal_score_bucket" and dict(sample_labels).get("le") == "8.0"
        ]
        assert len(score_buckets) == 1
        assert score_buckets[0]["value"] >= 1

    @pytest.mark.asyncio
    async def test_sets_gauge_values_from_store_state(self) -> None:
        store = create_in_memory_store()
        await store.save_heartbeat(
            {
                "account_id": "90011087",
                "equity": 10500.25,
                "balance": 10000,
                "floating_pl": 500.25,
                "timestamp": 1751750000,
            }
        )
        await store.save_tick(
            {"account_id": "90011087", "symbol": "XAUUSD", "bid": 3335.9, "ask": 3336.1, "spread": 2}
        )

        metrics = create_metrics_registry(enable_default=False)
        collector = create_store_metrics_collector(
            {"metrics": metrics, "store": store, "now": lambda: 1751760000000}
        )
        snapshot = await collector.collect()

        assert snapshot.accounts == 1
        assert snapshot.heartbeats == 1
        samples = parse_samples(text_of(metrics))
        assert labels_of(samples, "goldbot_account_equity_usd") == [
            {"account_id": "90011087", "value": 10500.25}
        ]
        assert labels_of(samples, "goldbot_account_balance_usd") == [
            {"account_id": "90011087", "value": 10000.0}
        ]
        assert labels_of(samples, "goldbot_account_floating_pl_usd") == [
            {"account_id": "90011087", "value": 500.25}
        ]
        assert labels_of(samples, "goldbot_ea_last_heartbeat_timestamp") == [
            {"account_id": "90011087", "value": 1751750000.0}
        ]
        assert labels_of(samples, "goldbot_spread_points") == [
            {"account_id": "90011087", "symbol": "XAUUSD", "value": 2.0}
        ]

    @pytest.mark.asyncio
    async def test_falls_back_to_wall_clock_when_heartbeat_has_no_timestamp(self) -> None:
        store = create_in_memory_store()
        await store.save_heartbeat({"account_id": "acc-1", "equity": 100})
        metrics = create_metrics_registry(enable_default=False)
        collector = create_store_metrics_collector(
            {"metrics": metrics, "store": store, "now": lambda: 1_751_760_000_000}
        )
        await collector.collect()
        samples = parse_samples(text_of(metrics))
        assert labels_of(samples, "goldbot_ea_last_heartbeat_timestamp") == [
            {"account_id": "acc-1", "value": 1_751_760_000.0}
        ]

    @pytest.mark.asyncio
    async def test_counts_positions_per_symbol_from_store_state(self) -> None:
        store = create_in_memory_store()
        await store.save_positions(
            {
                "account_id": "90011087",
                "symbol": "XAUUSD",
                "positions": [
                    {"ticket": 1, "symbol": "XAUUSD", "profit": 10},
                    {"ticket": 2, "symbol": "XAUUSD", "profit": -5},
                ],
            }
        )

        metrics = create_metrics_registry(enable_default=False)
        collector = create_store_metrics_collector({"metrics": metrics, "store": store})
        snapshot = await collector.collect()

        assert snapshot.positions == 2
        samples = parse_samples(text_of(metrics))
        assert labels_of(samples, "goldbot_account_positions") == [
            {"account_id": "90011087", "symbol": "XAUUSD", "value": 2.0}
        ]


# ---------------------------------------------------------------- httpStatusClass
class TestHttpStatusClass:
    def test_maps_status_codes_to_class_buckets(self) -> None:
        assert http_status_class(200) == "2xx"
        assert http_status_class(204) == "2xx"
        assert http_status_class(301) == "3xx"
        assert http_status_class(404) == "4xx"
        assert http_status_class(500) == "5xx"
        assert http_status_class(0) == "unknown"


# ---------------------------------------------------------------- normalizeHttpPath
class TestNormalizeHttpPath:
    def test_collapses_numeric_segments_to_id(self) -> None:
        assert normalize_http_path("GET", "/api/accounts/90011087/symbols") == "/api/accounts/:id/symbols"
        assert normalize_http_path("GET", "/") == "/"
        assert normalize_http_path("GET", "/metrics") == "/metrics"


# ---------------------------------------------------------------- createHttpMetricsMiddleware
class TestCreateHttpMetricsMiddleware:
    def test_records_http_request_counters_and_durations(self) -> None:
        metrics = create_metrics_registry(enable_default=False)
        middleware = create_http_metrics_middleware({"metrics": metrics, "now": lambda: 1000})

        middleware.record({"method": "GET", "url": "/metrics", "status_code": 200, "duration_ms": 5})
        middleware.record({"method": "POST", "url": "/api/ea/heartbeat", "status_code": 401, "duration_ms": 12})

        samples = parse_samples(text_of(metrics))
        assert labels_of(samples, "goldbot_http_requests_total") == [
            {"method": "GET", "path": "/metrics", "status": "2xx", "value": 1.0},
            {"method": "POST", "path": "/api/ea/heartbeat", "status": "4xx", "value": 1.0},
        ]
        # 直方图:GET /metrics 5ms 落在 le=0.005 桶
        duration_buckets = [
            {"labels": dict(sample_labels), "value": value}
            for (sample_name, sample_labels), value in samples.items()
            if sample_name == "goldbot_http_request_duration_seconds_bucket"
            and dict(sample_labels).get("le") == "0.005"
            and dict(sample_labels).get("method") == "GET"
            and dict(sample_labels).get("path") == "/metrics"
        ]
        assert len(duration_buckets) == 1
        assert duration_buckets[0]["value"] >= 1
