"""HTTP 指标中间件(镜像 packages/observability/src/metrics-middleware.ts)。"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from backend.observability._types import MetricsRegistry
from backend.observability.metrics import http_status_class

__all__ = ["HttpMiddlewareContext", "create_http_metrics_middleware", "normalize_http_path"]

_NUMERIC_SEGMENT = re.compile(r"^\d+$")


def normalize_http_path(method: str, url: str) -> str:
    """把 URL 路径中的纯数字段折叠为 :id,避免 label 基数爆炸。"""
    path = urlsplit(url).path
    segments = [segment for segment in path.split("/") if len(segment) > 0]
    if len(segments) == 0:
        return "/"
    normalized: list[str] = []
    for segment in segments:
        if _NUMERIC_SEGMENT.match(segment):
            normalized.append(":id")
        else:
            normalized.append(segment)
    return "/" + "/".join(normalized)


@dataclass
class HttpMiddlewareContext:
    """record 的入参形状(等价 TS HttpMiddlewareContext)。"""

    method: str
    url: str
    status_code: int
    duration_ms: float


@dataclass
class HttpMetricsMiddleware:
    """record({method, url, statusCode, durationMs}) → 计数 + 耗时直方图。"""

    metrics: MetricsRegistry
    now: Callable[[], float] | None = None
    path_normalizer: Callable[[str, str], str] | None = None

    def record(self, context: HttpMiddlewareContext | Mapping[str, Any]) -> None:
        if isinstance(context, Mapping):
            method = str(context["method"])
            url = str(context["url"])
            status_code = int(context["status_code"])
            duration_ms = float(context["duration_ms"])
        else:
            method = context.method
            url = context.url
            status_code = context.status_code
            duration_ms = context.duration_ms
        path = (self.path_normalizer or normalize_http_path)(method, url)
        status = http_status_class(status_code)
        self.metrics.http_requests_total.labels(method, path, status).inc()
        self.metrics.http_request_duration.labels(method, path).observe(duration_ms / 1000)


def create_http_metrics_middleware(options: dict) -> HttpMetricsMiddleware:
    """等价 TS createHttpMetricsMiddleware({metrics, now?, pathNormalizer?})。"""
    return HttpMetricsMiddleware(
        metrics=options["metrics"],
        now=options.get("now"),
        path_normalizer=options.get("path_normalizer"),
    )
