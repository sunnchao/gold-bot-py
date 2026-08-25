"""observability 包(镜像 gold-bot packages/observability/src)。"""

from __future__ import annotations

from backend.observability.metrics import (
    create_metrics_registry,
    default_metrics_registry,
    http_status_class,
    metrics_text,
)
from backend.observability.metrics_collector import (
    StoreMetricsCollector,
    StoreMetricsSnapshot,
    create_store_metrics_collector,
)
from backend.observability.metrics_middleware import (
    HttpMetricsMiddleware,
    create_http_metrics_middleware,
    normalize_http_path,
)
from backend.observability.shadow_report import (
    CutoverCheck,
    CutoverReport,
    ReplayCoverageSummary,
    build_shadow_report,
)
from backend.observability.sse import (
    SseEvent,
    SseHub,
    create_sse_hub,
    event_stream_headers,
    format_sse_frame,
)

__all__ = [
    "CutoverCheck",
    "CutoverReport",
    "HttpMetricsMiddleware",
    "MetricsRegistry",
    "ReplayCoverageSummary",
    "SseEvent",
    "SseHub",
    "StoreMetricsCollector",
    "StoreMetricsSnapshot",
    "build_shadow_report",
    "create_http_metrics_middleware",
    "create_metrics_registry",
    "create_sse_hub",
    "create_store_metrics_collector",
    "default_metrics_registry",
    "event_stream_headers",
    "format_sse_frame",
    "http_status_class",
    "metrics_text",
    "normalize_http_path",
]


def health_payload(status: str) -> dict[str, str]:
    """镜像 healthPayload(status):返回稳定的健康检查载荷。"""
    return {"status": status}
