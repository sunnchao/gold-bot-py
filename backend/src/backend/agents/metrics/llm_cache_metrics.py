"""LLM prompt-cache 指标(镜像 apps/app-agent/src/metrics/llm-cache-metrics.ts)。

prometheus-client 提供与 prom-client 同等的 Counter/Gauge/Registry 语义。
reset_llm_cache_metrics() 镜像 prom-client registry.resetMetrics():卸载并重建计数器。
"""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge

_registry = CollectorRegistry()

_cache_read_tokens = Counter(
    "goldbot_llm_cache_read_tokens_total",
    "Cumulative cache-read input tokens (OpenAI-compatible gateway cache_read_input_tokens).",
    labelnames=("model",),
    registry=_registry,
)
_cache_hit_tokens = Counter(
    "goldbot_llm_cache_hit_tokens_total",
    "Cumulative cached input tokens reported by DeepSeek/OpenAI/Kimi gateways.",
    labelnames=("model",),
    registry=_registry,
)
_cache_creation_tokens = Counter(
    "goldbot_llm_cache_creation_tokens_total",
    "Cumulative cache-creation input tokens.",
    labelnames=("model",),
    registry=_registry,
)
_cache_input_tokens = Counter(
    "goldbot_llm_cache_input_tokens_total",
    "Cumulative total input tokens across LLM requests.",
    labelnames=("model",),
    registry=_registry,
)
_cache_hit_rate = Gauge(
    "goldbot_llm_cache_hit_rate",
    "Cache hit rate (cached / total input tokens) of the most recent request.",
    labelnames=("model",),
    registry=_registry,
)


def _rebuild(metric_type, name: str, help_: str, labels: tuple[str, ...]):
    return metric_type(name, help_, labelnames=labels, registry=_registry)


def reset_llm_cache_metrics() -> None:
    """镜像 prom-client registry.resetMetrics():清空值,保留注册。"""
    global _cache_read_tokens, _cache_hit_tokens, _cache_creation_tokens
    global _cache_input_tokens, _cache_hit_rate
    for collector in (
        _cache_read_tokens,
        _cache_hit_tokens,
        _cache_creation_tokens,
        _cache_input_tokens,
        _cache_hit_rate,
    ):
        _registry.unregister(collector)
    _cache_read_tokens = _rebuild(Counter, "goldbot_llm_cache_read_tokens_total",
                                  "Cumulative cache-read input tokens.", ("model",))
    _cache_hit_tokens = _rebuild(Counter, "goldbot_llm_cache_hit_tokens_total",
                                 "Cumulative cached input tokens.", ("model",))
    _cache_creation_tokens = _rebuild(Counter, "goldbot_llm_cache_creation_tokens_total",
                                      "Cumulative cache-creation input tokens.", ("model",))
    _cache_input_tokens = _rebuild(Counter, "goldbot_llm_cache_input_tokens_total",
                                   "Cumulative total input tokens.", ("model",))
    _cache_hit_rate = _rebuild(Gauge, "goldbot_llm_cache_hit_rate",
                               "Cache hit rate of the most recent request.", ("model",))


def record_llm_cache_usage(usage: dict[str, float], model: str) -> None:
    labels = {"model": model}
    read_tokens = float(usage.get("readTokens") or 0)
    hit_tokens = float(usage.get("hitTokens") or 0)
    creation_tokens = float(usage.get("creationTokens") or 0)
    input_tokens = float(usage.get("inputTokens") or 0)
    _cache_read_tokens.labels(**labels).inc(read_tokens)
    _cache_hit_tokens.labels(**labels).inc(hit_tokens)
    _cache_creation_tokens.labels(**labels).inc(creation_tokens)
    _cache_input_tokens.labels(**labels).inc(input_tokens)

    if input_tokens > 0:
        cached = max(read_tokens, hit_tokens)
        _cache_hit_rate.labels(**labels).set(cached / input_tokens)


def llm_cache_registry() -> CollectorRegistry:
    return _registry
