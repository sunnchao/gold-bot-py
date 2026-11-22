from __future__ import annotations

from typing import Any

import httpx
import pytest

import backend.agents.runtime as runtime
from backend.agents.tools.pi_client import PiClient


def test_injects_gb_feishu_configuration_into_publisher(monkeypatch) -> None:
    captured: dict[str, str | None] = {}

    class RecordingPublisher:
        def __init__(self, _goldbot_api, *, webhook_url=None, secret=None) -> None:
            captured["webhook_url"] = webhook_url
            captured["secret"] = secret

    monkeypatch.setattr(runtime, "PublisherService", RecordingPublisher)

    runtime.create_agent_workflow(
        {
            "GB_FEISHU_WEBHOOK_URL": "https://feishu.example/gb-webhook",
            "GB_FEISHU_SECRET": "gb-secret",
        }
    )

    assert captured == {
        "webhook_url": "https://feishu.example/gb-webhook",
        "secret": "gb-secret",
    }


def test_create_agent_workflow_uses_independent_trade_llm_client_with_shared_pi_client(monkeypatch) -> None:
    constructed_clients: list[RecordingLlmClient] = []

    class RecordingLlmClient:
        def __init__(self, config, *, complete_once: Any = None) -> None:
            self.llm = dict(config.llm)
            self.complete_once = complete_once
            constructed_clients.append(self)

        def get_model(self) -> str:
            return self.llm["model"]

    class RecordingAnalyst:
        def __init__(self, client, *, trade_client=None, trade_model=None) -> None:
            self.client = client
            self.trade_client = trade_client
            self.trade_model = trade_model

    monkeypatch.setattr(runtime, "LlmClientService", RecordingLlmClient)
    monkeypatch.setattr(runtime, "ComprehensiveAnalystService", RecordingAnalyst)

    workflow = runtime.create_agent_workflow(
        {
            "LLM_MODEL": "deepseek-v4-pro-0813",
            "LLM_TRADE_MODEL": "glm-5.3-flash",
        }
    )

    analyst = workflow._nodes.comprehensive_analyst
    assert len(constructed_clients) == 2
    assert analyst.client is constructed_clients[0]
    assert analyst.trade_client is constructed_clients[1]
    assert analyst.client.get_model() == "deepseek-v4-pro-0813"
    assert analyst.trade_client.get_model() == "glm-5.3-flash"
    assert analyst.trade_model == "glm-5.3-flash"
    assert {
        **analyst.trade_client.llm,
        "model": analyst.client.llm["model"],
    } == analyst.client.llm
    assert isinstance(constructed_clients[0].complete_once, PiClient)
    assert constructed_clients[0].complete_once is constructed_clients[1].complete_once
    assert constructed_clients[0].complete_once._endpoint == "http://127.0.0.1:3000/v1/complete"


async def test_pi_runtime_routes_both_model_clients_through_custom_url(monkeypatch) -> None:
    registrations: list[tuple[str, PiClient]] = []

    class RecordingLlmClient:
        def __init__(self, config, *, complete_once: PiClient) -> None:
            registrations.append((config.llm["model"], complete_once))

    monkeypatch.setattr(runtime, "LlmClientService", RecordingLlmClient)

    async with httpx.AsyncClient() as http:
        runtime.create_agent_workflow(
            {
                "AI_RUNTIME": "pi",
                "PI_RUNTIME_URL": "http://pi-runtime:3000",
                "LLM_MODEL": "analysis-model",
                "LLM_TRADE_MODEL": "trade-model",
            },
            pi_http_client=http,
        )

    assert [model for model, _ in registrations] == ["analysis-model", "trade-model"]
    assert isinstance(registrations[0][1], PiClient)
    assert registrations[0][1] is registrations[1][1]
    assert registrations[0][1]._endpoint == "http://pi-runtime:3000/v1/complete"


@pytest.mark.parametrize(
    ("env", "message"),
    [
        ({"AI_RUNTIME": "langchain"}, "AI_RUNTIME"),
        ({"AI_RUNTIME": "typo"}, "AI_RUNTIME"),
        ({"PI_RUNTIME_URL": ""}, "PI_RUNTIME_URL"),
        ({"PI_RUNTIME_URL": "ftp://pi-runtime:3000"}, "PI_RUNTIME_URL"),
    ],
)
def test_rejects_invalid_ai_runtime_or_pi_url_configuration(env, message) -> None:
    with pytest.raises(ValueError, match=message):
        runtime.create_agent_workflow(env)


def test_blank_ai_runtime_defaults_to_pi_client(monkeypatch) -> None:
    captured: list[Any] = []

    class RecordingLlmClient:
        def __init__(self, config, *, complete_once: Any = None) -> None:
            captured.append(complete_once)

    monkeypatch.setattr(runtime, "LlmClientService", RecordingLlmClient)

    runtime.create_agent_workflow({"AI_RUNTIME": ""})
    assert len(captured) == 2
    assert isinstance(captured[0], PiClient)
    assert captured[0] is captured[1]
