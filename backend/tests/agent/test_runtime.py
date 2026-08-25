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
