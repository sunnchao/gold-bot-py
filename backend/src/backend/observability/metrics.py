"""Prometheus 指标注册表(镜像 packages/observability/src/metrics.ts)。

24 个 goldbot_* 指标的名字 / help / labels / buckets 与 gold-bot 基线逐字一致,
见 docs/porting/snapshots/metrics.md。
"""

from __future__ import annotations

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    gc_collector,
    platform_collector,
    process_collector,
)

from backend.observability._types import CounterMetric, GaugeMetric, HistogramMetric, MetricsRegistry

__all__ = [
    "MetricsRegistry",
    "create_metrics_registry",
    "default_metrics_registry",
    "http_status_class",
    "metrics_text",
]


def create_metrics_registry(enable_default: bool = True) -> MetricsRegistry:
    """镜像 createMetricsRegistry:enableDefault 时收集进程级默认指标。"""
    registry = CollectorRegistry()
    if enable_default:
        process_collector.ProcessCollector(registry=registry)
        platform_collector.PlatformCollector(registry=registry)
        gc_collector.GCCollector(registry=registry)

    signals_total: CounterMetric = Counter(
        "goldbot_signals_total",
        "Total number of trading signals generated",
        ("account_id", "symbol", "strategy", "side"),
        registry=registry,
    )

    signal_score: HistogramMetric = Histogram(
        "goldbot_signal_score",
        "Distribution of signal scores",
        ("account_id", "strategy"),
        buckets=(0, 2, 4, 6, 8, 10),
        registry=registry,
    )

    orders_total: CounterMetric = Counter(
        "goldbot_orders_total",
        "Total number of orders executed",
        ("account_id", "symbol", "side", "result", "strategy"),
        registry=registry,
    )

    order_latency: HistogramMetric = Histogram(
        "goldbot_order_latency_seconds",
        "Order execution latency from signal to EA execution",
        ("account_id", "order_type"),
        buckets=(0.1, 0.5, 1, 2, 5, 10, 30),
        registry=registry,
    )

    order_profit: HistogramMetric = Histogram(
        "goldbot_order_profit_usd",
        "Order profit/loss distribution in USD",
        ("account_id", "symbol", "strategy"),
        buckets=(-1000, -500, -100, -50, 0, 50, 100, 500, 1000),
        registry=registry,
    )

    account_equity: GaugeMetric = Gauge(
        "goldbot_account_equity_usd", "Account equity in USD", ("account_id",), registry=registry
    )

    account_balance: GaugeMetric = Gauge(
        "goldbot_account_balance_usd", "Account balance in USD", ("account_id",), registry=registry
    )

    account_positions: GaugeMetric = Gauge(
        "goldbot_account_positions", "Number of open positions", ("account_id", "symbol"), registry=registry
    )

    account_floating_pl: GaugeMetric = Gauge(
        "goldbot_account_floating_pl_usd", "Floating profit/loss in USD", ("account_id",), registry=registry
    )

    account_daily_pl: GaugeMetric = Gauge(
        "goldbot_account_daily_pl_usd",
        "Daily profit/loss in USD (resets at midnight)",
        ("account_id",),
        registry=registry,
    )

    ea_heartbeat_timestamp: GaugeMetric = Gauge(
        "goldbot_ea_last_heartbeat_timestamp",
        "Unix timestamp of last EA heartbeat",
        ("account_id",),
        registry=registry,
    )

    ea_heartbeats_total: CounterMetric = Counter(
        "goldbot_ea_heartbeats_total", "Total number of EA heartbeats received", ("account_id",), registry=registry
    )

    ea_ticks_total: CounterMetric = Counter(
        "goldbot_ea_ticks_total", "Total number of ticks received from EA", ("account_id", "symbol"), registry=registry
    )

    http_requests_total: CounterMetric = Counter(
        "goldbot_http_requests_total",
        "Total number of HTTP requests",
        ("method", "path", "status"),
        registry=registry,
    )

    http_request_duration: HistogramMetric = Histogram(
        "goldbot_http_request_duration_seconds",
        "HTTP request duration in seconds",
        ("method", "path"),
        buckets=(0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1, 2, 5),
        registry=registry,
    )

    db_query_duration: HistogramMetric = Histogram(
        "goldbot_db_query_duration_seconds",
        "Database query duration in seconds",
        ("operation",),
        buckets=(0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1),
        registry=registry,
    )

    db_queries_total: CounterMetric = Counter(
        "goldbot_db_queries_total", "Total number of database queries", ("operation", "status"), registry=registry
    )

    db_connections_open: GaugeMetric = Gauge(
        "goldbot_db_connections_open", "Number of open database connections", registry=registry
    )

    db_connections_in_use: GaugeMetric = Gauge(
        "goldbot_db_connections_in_use",
        "Number of database connections currently in use",
        registry=registry,
    )

    strategy_execution_duration: HistogramMetric = Histogram(
        "goldbot_strategy_execution_seconds",
        "Strategy execution duration in seconds",
        ("account_id", "strategy"),
        buckets=(0.01, 0.05, 0.1, 0.5, 1, 2, 5),
        registry=registry,
    )

    strategy_win_rate: GaugeMetric = Gauge(
        "goldbot_strategy_win_rate",
        "Strategy win rate (0-1)",
        ("account_id", "strategy"),
        registry=registry,
    )

    risk_gate_rejections: CounterMetric = Counter(
        "goldbot_risk_gate_rejections_total",
        "Total number of signals rejected by risk gate",
        ("account_id", "reason"),
        registry=registry,
    )

    # EA /order_result 回报计数(Phase 2.4 补充):error_code 维度让 4108/130
    # 等券商错误码可在 Grafana 里做趋势面板;成功回报 error_code='none'。
    command_results_total: CounterMetric = Counter(
        "goldbot_command_results_total",
        "Total number of EA command results received, by result and broker error code",
        ("account_id", "result", "error_code"),
        registry=registry,
    )

    spread_points: GaugeMetric = Gauge(
        "goldbot_spread_points",
        "Current spread in points",
        ("account_id", "symbol"),
        registry=registry,
    )

    return MetricsRegistry(
        registry=registry,
        signals_total=signals_total,
        signal_score=signal_score,
        orders_total=orders_total,
        order_latency=order_latency,
        order_profit=order_profit,
        account_equity=account_equity,
        account_balance=account_balance,
        account_positions=account_positions,
        account_floating_pl=account_floating_pl,
        account_daily_pl=account_daily_pl,
        ea_heartbeat_timestamp=ea_heartbeat_timestamp,
        ea_heartbeats_total=ea_heartbeats_total,
        ea_ticks_total=ea_ticks_total,
        http_requests_total=http_requests_total,
        http_request_duration=http_request_duration,
        db_query_duration=db_query_duration,
        db_queries_total=db_queries_total,
        db_connections_open=db_connections_open,
        db_connections_in_use=db_connections_in_use,
        strategy_execution_duration=strategy_execution_duration,
        strategy_win_rate=strategy_win_rate,
        risk_gate_rejections=risk_gate_rejections,
        command_results_total=command_results_total,
        spread_points=spread_points,
    )


def http_status_class(code: int) -> str:
    if 200 <= code < 300:
        return "2xx"
    if 300 <= code < 400:
        return "3xx"
    if 400 <= code < 500:
        return "4xx"
    if code >= 500:
        return "5xx"
    return "unknown"


default_metrics_registry: MetricsRegistry = create_metrics_registry(enable_default=False)

async def metrics_text() -> str:
    from prometheus_client import generate_latest
    return generate_latest(default_metrics_registry.registry).decode()
