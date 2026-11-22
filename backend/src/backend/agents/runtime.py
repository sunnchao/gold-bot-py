"""Production composition for the in-process analysis workflow backed by Pi AI runtime."""

from __future__ import annotations

import os
from collections.abc import Mapping
from types import SimpleNamespace
from typing import Any

import httpx

from backend.agents.agents.comprehensive_analyst import ComprehensiveAnalystService
from backend.agents.agents.publisher import PublisherService
from backend.agents.config.app_config import AppConfigService, validate_config
from backend.agents.config.bar_source import BarSourceService
from backend.agents.graph.market_insight_cache import MarketInsightCache
from backend.agents.graph.workflow import WorkflowService
from backend.agents.graph.workflow_nodes import WorkflowNodes
from backend.agents.tools.goldbot_api import GoldbotApiService
from backend.agents.tools.llm_client import LlmClientService
from backend.agents.tools.pi_client import DEFAULT_PI_RUNTIME_URL, PiClient

__all__ = ["create_agent_workflow"]


def create_agent_workflow(
    env: Mapping[str, str] | None = None, *, pi_http_client: httpx.AsyncClient | None = None
) -> WorkflowService:
    source = env if env is not None else os.environ
    raw_runtime = (source.get("AI_RUNTIME") or "").strip()
    if raw_runtime and raw_runtime != "pi":
        raise ValueError("AI_RUNTIME must be pi (langchain has been removed)")

    if "PI_RUNTIME_URL" in source:
        url = (source.get("PI_RUNTIME_URL") or "").strip()
        if not url:
            raise ValueError("PI_RUNTIME_URL cannot be empty")
    else:
        url = DEFAULT_PI_RUNTIME_URL

    pi_client = PiClient(url, client=pi_http_client)

    config: Any = AppConfigService(validate_config(source))
    goldbot_api: Any = GoldbotApiService(config)
    llm_client: Any = LlmClientService(config, complete_once=pi_client)
    trade_llm_config = SimpleNamespace(llm={**config.llm, "model": config.llm_trade_model})
    trade_client: Any = LlmClientService(trade_llm_config, complete_once=pi_client)
    analyst: Any = ComprehensiveAnalystService(
        llm_client,
        trade_client=trade_client,
        trade_model=config.llm_trade_model,
    )
    publisher: Any = PublisherService(
        goldbot_api,
        webhook_url=source.get("GB_FEISHU_WEBHOOK_URL"),
        secret=source.get("GB_FEISHU_SECRET"),
    )
    bar_source: Any = BarSourceService(config, goldbot_api)
    nodes = WorkflowNodes(
        goldbot_api,
        analyst,
        publisher,
        config=config,
        bar_source=bar_source,
        market_insight_cache=MarketInsightCache(config.market_insight_ttl_ms),
    )
    return WorkflowService(nodes)
