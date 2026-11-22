"""LLM 客户端单元测试(基于 Pi AI runtime HTTP 契约)。

通过向 PiClient 注入 httpx.MockTransport 完全离线验证:
- 请求路径(/v1/complete)、无凭据泄露、流式/非流式标志(stream: true/false)
- 分层消息、模型专属参数(DeepSeek/Kimi/GPT)、tools 与 toolChoice 映射
- 主备模型回退策略与按模型上报缓存用量指标
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from backend.agents.metrics.llm_cache_metrics import llm_cache_registry, reset_llm_cache_metrics
from backend.agents.tools.llm_client import (
    CacheStats,
    LLMClient,
    LLMClientConfig,
    LlmClientService,
)
from backend.agents.types.trade_action import TRADE_ACTION_TOOLS


@pytest.fixture(autouse=True)
def _reset_cache_metrics():
    reset_llm_cache_metrics()
    yield
    reset_llm_cache_metrics()


def default_config(**overrides: Any) -> LLMClientConfig:
    values: dict[str, Any] = {
        "provider": "custom",
        "baseUrl": "https://gateway.example/v1/",
        "apiKey": "sk-test-key",
        "model": "gpt-4o",
        "fallbackModel": "gpt-4o-mini",
        "timeout": 120000,
        "maxRetries": 3,
        "enablePromptCaching": True,
    }
    values.update(overrides)
    return LLMClientConfig(**values)


def pi_response(
    content: str = "",
    *,
    thinking: str = "",
    tool_use: dict[str, Any] | None = None,
    cache_stats: dict[str, int] | None = None,
    stop_reason: str = "stop",
) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "content": content,
            "thinking": thinking,
            "toolUse": tool_use,
            "cacheStats": cache_stats,
            "stopReason": stop_reason,
        },
    )


async def test_sends_requests_to_pi_runtime_without_api_key_or_auth_headers() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return pi_response("Hello world")

    client = LLMClient(
        default_config(),
        pi_runtime_url="http://pi-runtime:3000",
        transport=httpx.MockTransport(handler),
    )
    result = await client.invoke("Say hello", "You are terse")

    assert result == "Hello world"
    request = captured[0]
    assert str(request.url) == "http://pi-runtime:3000/v1/complete"
    assert "authorization" not in request.headers
    assert "sk-test-key" not in request.content.decode()
    body = json.loads(request.content)
    assert body == {
        "model": "gpt-4o",
        "messages": [
            {"role": "system", "content": "You are terse"},
            {"role": "user", "content": "Say hello"},
        ],
        "stream": False,
        "temperature": 0.1,
        "maxTokens": 16384,
        "timeoutMs": 120000,
        "maxRetries": 3,
        "parameters": {},
    }


async def test_omits_empty_system_prompt_and_keeps_empty_nonstreaming_content_without_fallback() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return pi_response("")

    client = LLMClient(default_config(), transport=httpx.MockTransport(handler))

    assert await client.invoke("first", "") == ""
    assert bodies[0]["messages"] == [{"role": "user", "content": "first"}]
    assert await client.invoke("second") == ""
    assert len(bodies) == 2


def test_detects_cache_strategy_from_model_name() -> None:
    claude_via_gateway = LLMClient(default_config(provider="wochirou", model="claude-opus-4-8"))
    deepseek = LLMClient(default_config(model="deepseek-v4-pro"))
    kimi = LLMClient(default_config(model="moonshot-v1-128k"))
    glm = LLMClient(default_config(model="glm-4.5"))
    minimax = LLMClient(default_config(model="abab6.5s-chat"))
    disabled = LLMClient(default_config(model="claude-sonnet-4", enablePromptCaching=False))

    assert claude_via_gateway.get_cache_strategy().type == "auto_prefix"
    assert deepseek.get_cache_strategy().type == "auto_prefix"
    assert kimi.get_cache_strategy().type == "prompt_cache_key"
    assert glm.get_cache_strategy().type == "auto_prefix_unstable"
    assert minimax.get_cache_strategy().type == "none"
    assert disabled.get_cache_strategy().type == "none"
    assert claude_via_gateway.get_cache_strategy().get("type") == "auto_prefix"


async def test_accepts_layer_objects_from_support_module_when_building_layered_messages() -> None:
    from backend.agents.agents._support import SystemBlock as SupportSystemBlock
    from backend.agents.agents._support import UserLayer as SupportUserLayer

    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return pi_response("ok")

    client = LLMClient(default_config(), transport=httpx.MockTransport(handler))
    result = await client.invoke_layered(
        [SupportSystemBlock("system prompt", cacheable=True)],
        [SupportUserLayer("live prompt", cacheable=False)],
    )

    assert result == "ok"
    assert bodies[0]["stream"] is False
    assert bodies[0]["messages"] == [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "live prompt"},
    ]


async def test_builds_layered_messages_and_records_streaming_cache_usage() -> None:
    bodies: list[dict[str, Any]] = []
    usage = {"inputTokens": 300, "readTokens": 120, "creationTokens": 0, "hitTokens": 120, "missTokens": 180}

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return pi_response("ok", cache_stats=usage)

    client = LLMClient(default_config(model="deepseek-v4-pro"), transport=httpx.MockTransport(handler))
    result = await client.stream_layered(
        [
            {"text": "common rules", "cacheable": True},
            {"text": "symbol rules", "cacheable": True},
        ],
        [
            {"text": "computed structures", "cacheable": True},
            {"text": "live market data", "cacheable": False},
        ],
    )

    assert result["content"] == "ok"
    assert result.content == "ok"
    assert result["cacheStats"].readTokens == 120
    assert result["cacheStats"].creationTokens == 0
    assert result["cacheStats"].hitTokens == 120
    assert result["cacheStats"].missTokens == 180
    assert result["cacheStats"].inputTokens == 300
    assert result.cache_stats.readTokens == 120
    assert bodies[0]["stream"] is True
    assert bodies[0]["messages"] == [
        {"role": "system", "content": "common rules\n\nsymbol rules"},
        {"role": "user", "content": "computed structures"},
        {"role": "user", "content": "live market data"},
    ]
    assert "prompt_cache_key" not in bodies[0]["parameters"]
    assert llm_cache_registry().get_sample_value(
        "goldbot_llm_cache_input_tokens_total", {"model": "deepseek-v4-pro"}
    ) == 300


async def test_keeps_automatic_prefix_providers_free_of_explicit_cache_fields() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return pi_response("ok")

    client = LLMClient(default_config(model="deepseek-v4-pro"), transport=httpx.MockTransport(handler))
    await client.invoke_layered(
        [{"text": "system", "cacheable": True}],
        [{"text": "user", "cacheable": True}],
    )

    assert "prompt_cache_key" not in bodies[0]["parameters"]


async def test_adds_prompt_cache_key_only_for_kimi_moonshot_strategy() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return pi_response("ok")

    client = LLMClient(default_config(model="kimi-k2"), transport=httpx.MockTransport(handler))
    await client.invoke_layered(
        [{"text": "system", "cacheable": True}],
        [{"text": "user", "cacheable": True}],
    )

    assert bodies[0]["parameters"]["prompt_cache_key"] == "gold-analysis"


async def test_deepseek_requests_set_reasoning_effort_max_and_disable_thinking() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return pi_response("ok")

    client = LLMClient(default_config(model="deepseek-v4-flash-0731"), transport=httpx.MockTransport(handler))
    await client.invoke_layered(
        [{"text": "system", "cacheable": True}],
        [{"text": "user", "cacheable": False}],
    )

    assert bodies[0]["parameters"] == {"reasoning_effort": "max", "thinking": {"type": "disabled"}}


async def test_kimi_requests_enable_max_thinking_and_raise_max_tokens() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return pi_response('{"ok": true}')

    client = LLMClient(default_config(model="kimi-k3.1-pro"), transport=httpx.MockTransport(handler))
    await client.invoke_layered(
        [{"text": "system", "cacheable": True}],
        [{"text": "user", "cacheable": False}],
    )

    body = bodies[0]
    assert body["model"] == "kimi-k3.1-pro"
    assert body["stream"] is False
    assert body["maxTokens"] == 32768
    assert body["parameters"] == {
        "prompt_cache_key": "gold-analysis",
        "reasoning_effort": "max",
        "thinking": {"type": "enabled"},
    }


async def test_non_deepseek_requests_do_not_carry_deepseek_reasoning_fields() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return pi_response("ok")

    client = LLMClient(default_config(model="gpt-4o"), transport=httpx.MockTransport(handler))
    await client.invoke_layered(
        [{"text": "system", "cacheable": True}],
        [{"text": "user", "cacheable": False}],
    )

    assert bodies[0]["parameters"] == {}


@pytest.mark.parametrize(
    ("tool_choice", "expected_choice"),
    [
        (None, "auto"),
        ({"type": "auto"}, "auto"),
        ({"type": "any"}, "required"),
        (
            {"type": "tool", "name": "place_pending_order"},
            {"type": "function", "function": {"name": "place_pending_order"}},
        ),
    ],
)
async def test_maps_tools_and_choice_to_openai_function_calling(tool_choice: Any, expected_choice: Any) -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return pi_response("ok")

    client = LLMClient(default_config(), transport=httpx.MockTransport(handler))
    opts: dict[str, Any] = {"tools": TRADE_ACTION_TOOLS}
    if tool_choice is not None:
        opts["tool_choice"] = tool_choice
    await client.invoke_layered(
        [{"text": "system", "cacheable": True}],
        [{"text": "user", "cacheable": False}],
        opts,
    )

    body = bodies[0]
    assert body["tools"] == [
        {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool["description"],
                "parameters": tool["input_schema"],
            },
        }
        for tool in TRADE_ACTION_TOOLS
    ]
    assert body["toolChoice"] == expected_choice


async def test_returns_tool_use_shape_and_streams_text() -> None:
    bodies: list[dict[str, Any]] = []
    tool = {"id": "call_01", "name": "place_pending_order", "input": {"side": "buy", "entry_price": 4145}}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        if body.get("tools"):
            return pi_response("", tool_use=tool, stop_reason="toolUse")
        return pi_response("Hello world")

    client = LLMClient(default_config(), transport=httpx.MockTransport(handler))

    assert await client.stream_invoke("prompt", "system") == "Hello world"
    assert bodies[0]["stream"] is True
    assert bodies[0]["messages"] == [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "prompt"},
    ]

    result = await client.stream_layered(
        [{"text": "sys", "cacheable": True}],
        [{"text": "user", "cacheable": True}],
        {"tools": TRADE_ACTION_TOOLS, "toolChoice": {"type": "any"}},
    )
    assert result["toolUse"].id == "call_01"
    assert result["toolUse"].name == "place_pending_order"
    assert result["toolUse"].input == {"side": "buy", "entry_price": 4145}
    assert result.tool_use.id == "call_01"


async def test_keeps_deprecated_layered_invocation_methods_compatible() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        return pi_response("ok" if body["stream"] else "fallback")

    client = LLMClient(default_config(), transport=httpx.MockTransport(handler))

    assert await client.stream_invoke_layered("system", ["static", "dynamic"]) == "ok"
    assert bodies[0]["stream"] is True
    assert bodies[0]["messages"] == [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "static\n\n----------------------------------------\n\ndynamic"},
    ]

    assert await client.invoke_layered("system", ["static", "dynamic"]) == "fallback"
    assert bodies[1]["stream"] is False
    assert bodies[1]["messages"] == [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "static\n\n----------------------------------------\n\ndynamic"},
    ]


def test_constructs_llm_client_service_from_app_config_service() -> None:
    service = LlmClientService(
        SimpleNamespace(
            llm={
                "provider": "custom",
                "baseUrl": "https://gateway.example/v1/",
                "apiKey": "sk-test-key",
                "model": "gpt-4o",
                "fallbackModel": "gpt-4o-mini",
                "timeout": 120000,
                "maxRetries": 3,
                "enablePromptCaching": True,
            }
        )
    )

    assert service.get_model() == "gpt-4o"


async def test_reports_pi_runtime_errors_on_invoke_and_stream() -> None:
    client = LLMClient(default_config(maxRetries=0), transport=httpx.MockTransport(lambda _r: httpx.Response(502)))

    with pytest.raises(RuntimeError, match="Pi runtime returned HTTP 502"):
        await client.invoke("test")

    with pytest.raises(RuntimeError, match="Pi runtime returned HTTP 502"):
        await client.stream_invoke("test")


async def test_invoke_falls_back_to_fallback_model_when_primary_request_fails() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        if len(bodies) == 1:
            return httpx.Response(502)
        return pi_response("fallback ok")

    client = LLMClient(default_config(maxRetries=0), transport=httpx.MockTransport(handler))

    assert await client.invoke("test") == "fallback ok"
    assert [body["model"] for body in bodies] == ["gpt-4o", "gpt-4o-mini"]


async def test_invoke_raises_last_error_when_both_models_fail() -> None:
    client = LLMClient(default_config(maxRetries=0), transport=httpx.MockTransport(lambda _r: httpx.Response(502)))

    with pytest.raises(RuntimeError, match="Pi runtime returned HTTP 502"):
        await client.invoke("test")


async def test_no_fallback_request_when_fallback_model_equals_primary_model() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(502)

    client = LLMClient(
        default_config(model="gpt-4o", fallbackModel="gpt-4o", maxRetries=0),
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(RuntimeError, match="Pi runtime returned HTTP 502"):
        await client.invoke("test")
    assert len(bodies) == 1


async def test_stream_invoke_falls_back_on_error_and_empty_content() -> None:
    bodies: list[dict[str, Any]] = []
    outcomes = iter(
        [
            httpx.Response(503),
            pi_response("ok"),
            pi_response("   \n"),
            pi_response("recovered"),
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return next(outcomes)

    client = LLMClient(default_config(maxRetries=0), transport=httpx.MockTransport(handler))

    assert await client.stream_invoke("test") == "ok"
    assert await client.stream_invoke("test") == "recovered"
    assert [body["model"] for body in bodies] == ["gpt-4o", "gpt-4o-mini", "gpt-4o", "gpt-4o-mini"]


async def test_stream_layered_falls_back_on_empty_response_and_reports_per_model_metrics() -> None:
    bodies: list[dict[str, Any]] = []
    responses = iter(
        [
            pi_response("", cache_stats={"inputTokens": 50, "readTokens": 0, "creationTokens": 0, "hitTokens": 0}),
            pi_response("ok", cache_stats={"inputTokens": 100, "readTokens": 25, "creationTokens": 0, "hitTokens": 25}),
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return next(responses)

    client = LLMClient(default_config(), transport=httpx.MockTransport(handler))
    result = await client.stream_layered(
        [{"text": "system", "cacheable": True}],
        [{"text": "user", "cacheable": False}],
    )

    assert result.content == "ok"
    assert [body["model"] for body in bodies] == ["gpt-4o", "gpt-4o-mini"]

    registry = llm_cache_registry()
    assert registry.get_sample_value("goldbot_llm_cache_input_tokens_total", {"model": "gpt-4o"}) == 50
    assert registry.get_sample_value("goldbot_llm_cache_input_tokens_total", {"model": "gpt-4o-mini"}) == 100


async def test_stream_layered_keeps_tool_use_result_without_fallback() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return pi_response("", tool_use={"id": "call_01", "name": "place_pending_order", "input": {"amount": 1}})

    client = LLMClient(default_config(), transport=httpx.MockTransport(handler))
    result = await client.stream_layered(
        [{"text": "system", "cacheable": True}],
        [{"text": "user", "cacheable": False}],
        {"tools": TRADE_ACTION_TOOLS, "toolChoice": {"type": "tool", "name": "place_pending_order"}},
    )

    assert result.tool_use is not None
    assert result.tool_use.input == {"amount": 1}
    assert len(bodies) == 1


async def test_fallback_model_request_does_not_inherit_primary_model_params() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        if len(bodies) == 1:
            return httpx.Response(502)
        return pi_response("ok")

    client = LLMClient(
        default_config(model="deepseek-v4-flash-0731", fallbackModel="gpt-4o-mini", maxRetries=0),
        transport=httpx.MockTransport(handler),
    )

    assert await client.invoke("test") == "ok"
    assert bodies[0]["model"] == "deepseek-v4-flash-0731"
    assert bodies[0]["parameters"]["reasoning_effort"] == "max"
    assert bodies[1]["model"] == "gpt-4o-mini"
    assert bodies[1]["parameters"] == {}
    assert isinstance(CacheStats(), CacheStats)


async def test_stream_layered_treats_thinking_only_response_as_empty_and_falls_back() -> None:
    bodies: list[dict[str, Any]] = []
    outcomes = iter(
        [
            pi_response("", thinking="long internal reasoning only"),
            pi_response("recovered answer", thinking="brief"),
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return next(outcomes)

    client = LLMClient(default_config(), transport=httpx.MockTransport(handler))
    result = await client.stream_layered(
        [{"text": "system", "cacheable": True}],
        [{"text": "user", "cacheable": False}],
    )

    assert [body["model"] for body in bodies] == ["gpt-4o", "gpt-4o-mini"]
    assert result.content == "recovered answer"
    assert result.thinking == "brief"
