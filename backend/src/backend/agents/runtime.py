"""Production composition for the in-process LangGraph analysis workflow."""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

from backend.agents.agents.comprehensive_analyst import ComprehensiveAnalystService
from backend.agents.agents.publisher import PublisherService
from backend.agents.config.app_config import AppConfigService, validate_config
from backend.agents.config.bar_source import BarSourceService
from backend.agents.graph.market_insight_cache import MarketInsightCache
from backend.agents.graph.workflow import WorkflowService
from backend.agents.graph.workflow_nodes import WorkflowNodes
from backend.agents.tools.goldbot_api import GoldbotApiService
from backend.agents.tools.llm_client import LlmClientService

__all__ = ["create_agent_workflow"]


def create_agent_workflow(env: Mapping[str, str] | None = None) -> WorkflowService:
    config: Any = AppConfigService(validate_config(env if env is not None else os.environ))
    goldbot_api: Any = GoldbotApiService(config)
    llm_client: Any = LlmClientService(config)
    analyst: Any = ComprehensiveAnalystService(
        llm_client,
        trade_client=llm_client,
        trade_model=config.llm_trade_model,
    )
    publisher: Any = PublisherService(goldbot_api)
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
