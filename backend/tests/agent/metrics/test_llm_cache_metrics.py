"""LLM cache 指标契约(镜像 apps/app-agent/src/metrics/llm-cache-metrics.test.ts)。"""

from __future__ import annotations

import pytest
from prometheus_client import generate_latest

from backend.agents.metrics.llm_cache_metrics import (
    llm_cache_registry,
    record_llm_cache_usage,
    reset_llm_cache_metrics,
)


def _metrics_text() -> str:
    return generate_latest(llm_cache_registry()).decode("utf-8")


@pytest.fixture(autouse=True)
def _reset_registry():
    reset_llm_cache_metrics()
    yield
    reset_llm_cache_metrics()


def test_computes_hit_rate_as_max_read_hit_over_input() -> None:
    record_llm_cache_usage(
        {"readTokens": 800, "hitTokens": 0, "creationTokens": 200, "inputTokens": 1000},
        "claude-opus-4-8",
    )
    record_llm_cache_usage(
        {"readTokens": 0, "hitTokens": 300, "creationTokens": 0, "inputTokens": 1000},
        "deepseek-v4-pro",
    )

    text = _metrics_text()
    assert 'goldbot_llm_cache_hit_rate{model="claude-opus-4-8"} 0.8' in text
    assert 'goldbot_llm_cache_hit_rate{model="deepseek-v4-pro"} 0.3' in text


def test_does_not_emit_hit_rate_when_input_tokens_zero() -> None:
    record_llm_cache_usage(
        {"readTokens": 0, "hitTokens": 0, "creationTokens": 0, "inputTokens": 0},
        "gpt-4o",
    )

    text = _metrics_text()
    assert 'goldbot_llm_cache_hit_rate{model="gpt-4o"}' not in text


def test_accumulates_token_counters_across_requests() -> None:
    record_llm_cache_usage(
        {"readTokens": 100, "hitTokens": 0, "creationTokens": 50, "inputTokens": 500},
        "gpt-4o",
    )
    record_llm_cache_usage(
        {"readTokens": 0, "hitTokens": 200, "creationTokens": 0, "inputTokens": 400},
        "gpt-4o",
    )

    text = _metrics_text()
    assert 'goldbot_llm_cache_input_tokens_total{model="gpt-4o"} 900.0' in text
    assert 'goldbot_llm_cache_hit_tokens_total{model="gpt-4o"} 200.0' in text
