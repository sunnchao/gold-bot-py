from __future__ import annotations

import backend.agents.runtime as runtime


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


def test_create_agent_workflow_uses_independent_trade_llm_client(monkeypatch) -> None:
    constructed_clients: list[RecordingLlmClient] = []

    class RecordingLlmClient:
        def __init__(self, config) -> None:
            self.llm = dict(config.llm)
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
