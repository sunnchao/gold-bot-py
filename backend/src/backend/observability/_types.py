"""可观测性内部类型(镜像 packages/observability/src/metrics.ts 的 MetricsRegistry)。"""

from __future__ import annotations

from dataclasses import dataclass

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

# prometheus-client 的类型别名:Counter/Gauge/Histogram 实例
CounterMetric = Counter
GaugeMetric = Gauge
HistogramMetric = Histogram


@dataclass
class MetricsRegistry:
    """24 个 goldbot_* 指标 + 独立 CollectorRegistry。"""

    registry: CollectorRegistry
    signals_total: CounterMetric
    signal_score: HistogramMetric
    orders_total: CounterMetric
    order_latency: HistogramMetric
    order_profit: HistogramMetric
    account_equity: GaugeMetric
    account_balance: GaugeMetric
    account_positions: GaugeMetric
    account_floating_pl: GaugeMetric
    account_daily_pl: GaugeMetric
    ea_heartbeat_timestamp: GaugeMetric
    ea_heartbeats_total: CounterMetric
    ea_ticks_total: CounterMetric
    http_requests_total: CounterMetric
    http_request_duration: HistogramMetric
    db_query_duration: HistogramMetric
    db_queries_total: CounterMetric
    db_connections_open: GaugeMetric
    db_connections_in_use: GaugeMetric
    strategy_execution_duration: HistogramMetric
    strategy_win_rate: GaugeMetric
    risk_gate_rejections: CounterMetric
    command_results_total: CounterMetric
    spread_points: GaugeMetric
