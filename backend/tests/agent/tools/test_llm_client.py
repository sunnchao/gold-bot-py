"""LLM 客户端单元测试(镜像 gold-bot llm-client.test.ts,基于 langchain-openai)。

请求层托管于 langchain-openai:通过注入 httpx.MockTransport 完全离线,
验证请求体 / 流式汇聚 / 工具调用 / 缓存统计 / 错误语义。
"""

import json
import logging

import httpx
import pytest

from backend.agents.tools.llm_client import (
    CacheStats,
    LLMClient,
    LLMClientConfig,
    LlmClientService,
)
from backend.agents.types.trade_action import TRADE_ACTION_TOOLS


def default_config(**overrides) -> LLMClientConfig:
    values = {
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


def sse(data) -> str:
    return f"data: {json.dumps(data)}\n\n"


def stream_response(parts: list[str]) -> httpx.Response:
    return httpx.Response(
        200,
        content="".join(parts).encode("utf-8"),
        headers={"content-type": "text/event-stream"},
    )


def json_response(body: dict) -> httpx.Response:
    # langchain-openai 1.x 要求 assistant message 带 role(真实 API 总会返回)
    if isinstance(body.get("choices"), list) and body["choices"]:
        message = body["choices"][0].get("message")
        if isinstance(message, dict) and "role" not in message:
            body["choices"][0]["message"] = {**message, "role": "assistant"}
    return httpx.Response(200, json=body)


class _CaptureHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


async def test_uses_the_openai_chat_completions_endpoint_headers_and_messages():
    # TS: 'uses the OpenAI Chat Completions endpoint, headers, and messages'
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return json_response({"choices": [{"message": {"content": "Hello world"}}]})

    client = LLMClient(default_config(), transport=httpx.MockTransport(handler))
    result = await client.invoke("Say hello", "You are terse")

    assert result == "Hello world"
    request = captured[0]
    assert str(request.url) == "https://gateway.example/v1/chat/completions"
    assert dict(request.headers)["authorization"] == "Bearer sk-test-key"
    body = json.loads(request.content)
    assert body["model"] == "gpt-4o"
    assert body["temperature"] == 0.1
    assert body["messages"] == [
        {"role": "system", "content": "You are terse"},
        {"role": "user", "content": "Say hello"},
    ]
    # langchain-openai 1.x 使用 max_completion_tokens(替代废弃的 max_tokens)
    assert body.get("max_tokens") == 16384 or body.get("max_completion_tokens") == 16384


async def test_omits_an_empty_system_prompt_and_returns_empty_text_for_null_or_missing_content():
    # TS: 'omits an empty system prompt and returns empty text for null or missing content'
    bodies: list[dict] = []
    responses = iter(
        [
            {"choices": [{"message": {"content": None}}]},
            {"choices": [{"message": {}}]},
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return json_response(next(responses))

    client = LLMClient(default_config(), transport=httpx.MockTransport(handler))

    assert await client.invoke("first", "") == ""
    assert bodies[0]["messages"] == [{"role": "user", "content": "first"}]

    assert await client.invoke("second") == ""


def test_detects_cache_strategy_from_model_name():
    # TS: 'detects cache strategy from model name'
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

    # 兼容 dict 风格访问(调用方 comprehensive_analyst 用 .get("type"))
    assert claude_via_gateway.get_cache_strategy().get("type") == "auto_prefix"


async def test_accepts_layer_objects_from_support_module_when_building_layered_messages():
    """Runtime agents pass _support.SystemBlock/UserLayer objects, not llm_client-local classes."""
    from backend.agents.agents._support import SystemBlock as SupportSystemBlock
    from backend.agents.agents._support import UserLayer as SupportUserLayer

    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return json_response({"choices": [{"message": {"content": "ok"}}]})

    client = LLMClient(default_config(), transport=httpx.MockTransport(handler))
    await client.invoke_layered(
        [SupportSystemBlock("system prompt", cacheable=True)],
        [SupportUserLayer("live prompt", cacheable=False)],
    )

    assert bodies[0]["messages"] == [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "live prompt"},
    ]


async def test_builds_openai_layered_messages_and_reads_streaming_cache_usage():
    # TS: 'builds OpenAI layered messages and reads streaming cache usage'
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return stream_response(
            [
                sse({"choices": [{"delta": {"content": "ok"}, "finish_reason": None}]}),
                sse(
                    {
                        "choices": [],
                        "usage": {
                            "prompt_tokens": 300,
                            "completion_tokens": 10,
                            "total_tokens": 310,
                            "prompt_tokens_details": {"cached_tokens": 120},
                        },
                    }
                ),
                "data: [DONE]\n\n",
            ]
        )

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

    # dict 接口(旧测试)与属性接口(调用方)均可用
    assert result["content"] == "ok"
    assert result.content == "ok"
    assert result["cacheStats"].readTokens == 120
    assert result["cacheStats"].creationTokens == 0
    assert result["cacheStats"].hitTokens == 120
    assert result["cacheStats"].missTokens == 0
    assert result["cacheStats"].inputTokens == 300
    assert result.cache_stats.readTokens == 120
    body = bodies[0]
    assert body["messages"] == [
        {"role": "system", "content": "common rules\n\nsymbol rules"},
        {"role": "user", "content": "computed structures"},
        {"role": "user", "content": "live market data"},
    ]
    assert body["stream"] is True
    assert body["stream_options"] == {"include_usage": True}
    assert "cache_control" not in json.dumps(body)
    assert "prompt_cache_key" not in body


async def test_keeps_automatic_prefix_providers_free_of_explicit_cache_fields():
    # TS: 'keeps automatic-prefix providers free of explicit cache fields'
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return json_response({"choices": [{"message": {"content": "ok"}}]})

    client = LLMClient(default_config(model="deepseek-v4-pro"), transport=httpx.MockTransport(handler))
    await client.invoke_layered(
        [{"text": "system", "cacheable": True}],
        [{"text": "user", "cacheable": True}],
    )

    body = bodies[0]
    assert "cache_control" not in json.dumps(body)
    assert "prompt_cache_key" not in body


async def test_adds_prompt_cache_key_only_for_kimi_moonshot_strategy():
    # TS: 'adds prompt_cache_key only for Kimi/Moonshot strategy'
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return json_response({"choices": [{"message": {"content": "ok"}}]})

    client = LLMClient(default_config(model="kimi-k2"), transport=httpx.MockTransport(handler))
    await client.invoke_layered(
        [{"text": "system", "cacheable": True}],
        [{"text": "user", "cacheable": True}],
    )

    body = bodies[0]
    assert body["prompt_cache_key"] == "gold-analysis"
    assert "cache_control" not in json.dumps(body)


async def test_deepseek_requests_set_reasoning_effort_high_and_disable_thinking():
    # 老板要求:DeepSeek 模型 reasoning_effort=high,并显式关闭 thinking 输出。
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return json_response({"choices": [{"message": {"content": "ok"}}]})

    client = LLMClient(default_config(model="deepseek-v4-flash-0731"), transport=httpx.MockTransport(handler))
    await client.invoke_layered(
        [{"text": "system", "cacheable": True}],
        [{"text": "user", "cacheable": False}],
    )

    body = bodies[0]
    assert body["reasoning_effort"] == "high"
    assert body["thinking"] == {"type": "disabled"}


async def test_non_deepseek_requests_do_not_carry_deepseek_reasoning_fields():
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return json_response({"choices": [{"message": {"content": "ok"}}]})

    client = LLMClient(default_config(model="gpt-4o"), transport=httpx.MockTransport(handler))
    await client.invoke_layered(
        [{"text": "system", "cacheable": True}],
        [{"text": "user", "cacheable": False}],
    )

    body = bodies[0]
    assert "reasoning_effort" not in body
    assert "thinking" not in body


@pytest.mark.parametrize(
    ("label", "tool_choice", "expected_choice"),
    [
        ("missing choice", None, "auto"),
        ("auto choice", {"type": "auto"}, "auto"),
        ("any choice", {"type": "any"}, "required"),
        (
            "named choice",
            {"type": "tool", "name": "place_pending_order"},
            {"type": "function", "function": {"name": "place_pending_order"}},
        ),
    ],
)
async def test_maps_tools_and_choice_to_openai_function_calling(label, tool_choice, expected_choice):
    # TS: it.each(['missing choice', ...]) 'maps tools and %s to OpenAI function calling'
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return json_response({"choices": [{"message": {"content": "ok"}}]})

    client = LLMClient(default_config(), transport=httpx.MockTransport(handler))
    opts = {"tools": TRADE_ACTION_TOOLS}
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
    assert body["tool_choice"] == expected_choice


async def test_accumulates_indexed_streaming_tool_call_arguments_and_returns_the_public_tool_use_shape():
    # TS: 'accumulates indexed streaming tool call arguments and returns the public toolUse shape'
    parts = [
        sse(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_01",
                                    "type": "function",
                                    "function": {"name": "place_pending_order", "arguments": '{"side":"buy",'},
                                }
                            ]
                        },
                        "finish_reason": None,
                    }
                ]
            }
        ),
        sse(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "function": {"arguments": '"entry_price":4145,"stop_loss":4125}'},
                                }
                            ]
                        },
                        "finish_reason": None,
                    }
                ]
            }
        ),
        sse({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}),
        "data: [DONE]\n\n",
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return stream_response(parts)

    client = LLMClient(default_config(), transport=httpx.MockTransport(handler))
    result = await client.stream_layered(
        [{"text": "sys", "cacheable": True}],
        [{"text": "user", "cacheable": True}],
        {"tools": TRADE_ACTION_TOOLS, "toolChoice": {"type": "any"}},
    )

    assert result["toolUse"].id == "call_01"
    assert result["toolUse"].name == "place_pending_order"
    assert result["toolUse"].input == {"side": "buy", "entry_price": 4145, "stop_loss": 4125}
    assert result.tool_use.id == "call_01"


async def test_preserves_the_warning_behavior_for_invalid_streaming_tool_json():
    # TS: 'preserves the warning behavior for invalid streaming tool JSON'
    logger = logging.getLogger("goldbot.app_agent")
    capture = _CaptureHandler()
    logger.addHandler(capture)
    try:
        parts = [
            sse(
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call_bad",
                                        "function": {"name": "place_pending_order", "arguments": "{bad"},
                                    }
                                ]
                            },
                            "finish_reason": None,
                        }
                    ]
                }
            ),
            sse({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}),
            "data: [DONE]\n\n",
        ]

        def handler(request: httpx.Request) -> httpx.Response:
            return stream_response(parts)

        client = LLMClient(default_config(), transport=httpx.MockTransport(handler))
        result = await client.stream_layered(
            [{"text": "sys", "cacheable": True}],
            [{"text": "user", "cacheable": False}],
        )

        assert result.get("toolUse") is None

        messages = [record.getMessage() for record in capture.records]
        assert any("tool_use input parse failed" in message and "{bad" in message for message in messages)
    finally:
        logger.removeHandler(capture)


def test_reads_non_streaming_usage_field_precedence_and_input_tokens_over_prompt_tokens():
    # 非流式响应保留原始 usage(DeepSeek native / billing_usage 嵌套 / Kimi 优先级)
    client = LLMClient(default_config(), transport=httpx.MockTransport(lambda request: httpx.Response(200)))
    usage = {
        "cache_read_input_tokens": 200,
        "cache_creation_input_tokens": 100,
        "input_tokens": 400,
        "prompt_tokens": 999,
        "prompt_cache_hit_tokens": 300,
        "prompt_cache_miss_tokens": 50,
        "prompt_tokens_details": {"cached_tokens": 120},
        "billing_usage": {"openai_usage": {"prompt_tokens_details": {"cached_tokens": 110}}},
        "cached_tokens": 90,
    }
    stats = client._read_cache_usage(usage, CacheStats())

    assert stats.readTokens == 200
    assert stats.creationTokens == 100
    assert stats.hitTokens == 300
    assert stats.missTokens == 50
    assert stats.inputTokens == 400


def test_reads_non_streaming_usage_falls_back_from_input_tokens_to_openai_prompt_tokens():
    client = LLMClient(default_config(), transport=httpx.MockTransport(lambda request: httpx.Response(200)))
    stats = client._read_cache_usage({"prompt_tokens": 321}, CacheStats())

    assert stats.inputTokens == 321


@pytest.mark.parametrize(
    ("label", "usage", "expected"),
    [
        ("OpenAI cached tokens", {"prompt_tokens_details": {"cached_tokens": 120}}, 120),
        (
            "nested gateway cached tokens",
            {"billing_usage": {"openai_usage": {"prompt_tokens_details": {"cached_tokens": 110}}}},
            110,
        ),
        ("Kimi cached tokens", {"cached_tokens": 90}, 90),
    ],
)
def test_reads_non_streaming_usage_falls_back_to_cached_token_sources(label, usage, expected):
    # TS: it.each(['OpenAI cached tokens', 'nested gateway cached tokens', 'Kimi cached tokens'])
    #     'falls back to %s'
    client = LLMClient(default_config(), transport=httpx.MockTransport(lambda request: httpx.Response(200)))
    stats = client._read_cache_usage(usage, CacheStats())

    assert stats.hitTokens == expected


async def test_streams_text_from_content_deltas_and_stops_at_done():
    # TS: 'streams text from content deltas and stops at DONE'
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return stream_response(
            [
                sse({"choices": [{"delta": {"content": "Hello"}, "finish_reason": None}]}),
                sse({"choices": [{"delta": {"content": " world"}, "finish_reason": "stop"}]}),
                "data: [DONE]\n\n",
                sse({"choices": [{"delta": {"content": " ignored"}, "finish_reason": None}]}),
            ]
        )

    client = LLMClient(default_config(), transport=httpx.MockTransport(handler))

    assert await client.stream_invoke("prompt", "system") == "Hello world"
    body = bodies[0]
    assert body["messages"] == [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "prompt"},
    ]
    assert body["stream"] is True
    assert body["stream_options"] == {"include_usage": True}


async def test_keeps_deprecated_layered_invocation_methods_compatible():
    # TS: 'keeps deprecated layered invocation methods compatible'
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        if body.get("stream"):
            return stream_response(
                [sse({"choices": [{"delta": {"content": "ok"}, "finish_reason": None}]}), "data: [DONE]\n\n"]
            )
        return json_response({"choices": [{"message": {"content": "fallback"}}]})

    client = LLMClient(default_config(), transport=httpx.MockTransport(handler))

    assert await client.stream_invoke_layered("system", ["static", "dynamic"]) == "ok"
    assert bodies[0]["messages"] == [
        {"role": "system", "content": "system"},
        {
            "role": "user",
            "content": "static\n\n----------------------------------------\n\ndynamic",
        },
    ]

    assert await client.invoke_layered("system", ["static", "dynamic"]) == "fallback"
    assert bodies[1]["messages"] == [
        {"role": "system", "content": "system"},
        {
            "role": "user",
            "content": "static\n\n----------------------------------------\n\ndynamic",
        },
    ]


def test_constructs_llm_client_service_from_app_config_service():
    # TS: 'constructs LlmClientService from AppConfigService'
    from types import SimpleNamespace

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

    assert service is not None


async def test_reports_openai_chat_completions_errors():
    # TS: 'reports OpenAI Chat Completions errors'
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="Unauthorized")

    client = LLMClient(default_config(), transport=httpx.MockTransport(handler))

    with pytest.raises(RuntimeError, match="OpenAI Chat Completions API 401"):
        await client.invoke("test")


async def test_reports_openai_chat_completions_errors_on_stream():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="Rate limited")

    client = LLMClient(default_config(maxRetries=0), transport=httpx.MockTransport(handler))

    with pytest.raises(RuntimeError, match="OpenAI Chat Completions API 429"):
        await client.stream_invoke("test")


async def test_invoke_falls_back_to_fallback_model_when_primary_request_fails():
    # 主模型 500 → 自动改用 fallback 模型重试并返回其结果
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        if len(bodies) == 1:
            return httpx.Response(500, text="boom")
        return json_response({"choices": [{"message": {"content": "fallback ok"}}]})

    client = LLMClient(default_config(maxRetries=0), transport=httpx.MockTransport(handler))

    assert await client.invoke("test") == "fallback ok"
    assert [body["model"] for body in bodies] == ["gpt-4o", "gpt-4o-mini"]


async def test_invoke_raises_last_error_when_both_models_fail():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    client = LLMClient(default_config(maxRetries=0), transport=httpx.MockTransport(handler))

    with pytest.raises(RuntimeError, match="OpenAI Chat Completions API 500"):
        await client.invoke("test")


async def test_no_fallback_request_when_fallback_model_equals_primary_model():
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(500, text="boom")

    client = LLMClient(
        default_config(model="gpt-4o", fallbackModel="gpt-4o", maxRetries=0),
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(RuntimeError, match="OpenAI Chat Completions API 500"):
        await client.invoke("test")
    assert len(bodies) == 1


async def test_stream_invoke_falls_back_when_primary_request_fails():
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        if len(bodies) == 1:
            return httpx.Response(503, text="unavailable")
        return stream_response(
            [sse({"choices": [{"delta": {"content": "ok"}, "finish_reason": None}]}), "data: [DONE]\n\n"]
        )

    client = LLMClient(default_config(maxRetries=0), transport=httpx.MockTransport(handler))

    assert await client.stream_invoke("test") == "ok"
    assert [body["model"] for body in bodies] == ["gpt-4o", "gpt-4o-mini"]


async def test_stream_invoke_falls_back_on_empty_content():
    # 流式空响应(无任何内容块)视为失败,触发 fallback 模型重试
    bodies: list[dict] = []
    responses = iter(
        [
            stream_response([sse({"choices": [{"delta": {}, "finish_reason": "stop"}]}), "data: [DONE]\n\n"]),
            stream_response(
                [sse({"choices": [{"delta": {"content": "recovered"}, "finish_reason": None}]}), "data: [DONE]\n\n"]
            ),
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return next(responses)

    client = LLMClient(default_config(), transport=httpx.MockTransport(handler))

    assert await client.stream_invoke("test") == "recovered"
    assert [body["model"] for body in bodies] == ["gpt-4o", "gpt-4o-mini"]


async def test_stream_layered_falls_back_on_empty_response_and_reports_per_model_metrics():
    from backend.agents.metrics.llm_cache_metrics import llm_cache_registry, reset_llm_cache_metrics

    reset_llm_cache_metrics()
    bodies: list[dict] = []
    responses = iter(
        [
            stream_response(
                [
                    sse({"choices": [{"delta": {}, "finish_reason": "stop"}]}),
                    sse(
                        {
                            "choices": [],
                            "usage": {"prompt_tokens": 50, "completion_tokens": 1, "total_tokens": 51},
                        }
                    ),
                    "data: [DONE]\n\n",
                ]
            ),
            stream_response(
                [
                    sse({"choices": [{"delta": {"content": "ok"}, "finish_reason": None}]}),
                    sse(
                        {
                            "choices": [],
                            "usage": {"prompt_tokens": 100, "completion_tokens": 1, "total_tokens": 101},
                        }
                    ),
                    "data: [DONE]\n\n",
                ]
            ),
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

    # 指标按实际使用的模型分别上报
    registry = llm_cache_registry()
    assert registry.get_sample_value("goldbot_llm_cache_input_tokens_total", {"model": "gpt-4o"}) == 50
    assert registry.get_sample_value("goldbot_llm_cache_input_tokens_total", {"model": "gpt-4o-mini"}) == 100


async def test_stream_layered_keeps_tool_use_result_without_fallback():
    # 空内容但携带合法 tool_use 属于成功结果,不触发 fallback
    parts = [
        sse(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_01",
                                    "function": {"name": "place_pending_order", "arguments": '{"amount":1}'},
                                }
                            ]
                        },
                        "finish_reason": None,
                    }
                ]
            }
        ),
        sse({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}),
        "data: [DONE]\n\n",
    ]
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return stream_response(parts)

    client = LLMClient(default_config(), transport=httpx.MockTransport(handler))
    result = await client.stream_layered(
        [{"text": "system", "cacheable": True}],
        [{"text": "user", "cacheable": False}],
        {"tools": TRADE_ACTION_TOOLS, "toolChoice": {"type": "tool", "name": "place_pending_order"}},
    )

    assert result.tool_use is not None
    assert result.tool_use.input == {"amount": 1}
    assert len(bodies) == 1


async def test_fallback_model_request_does_not_inherit_primary_model_params():
    # DeepSeek 主模型失败 → fallback 到 gpt 模型时不得携带 DeepSeek 专属参数
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        if len(bodies) == 1:
            return httpx.Response(500, text="boom")
        return json_response({"choices": [{"message": {"content": "ok"}}]})

    client = LLMClient(
        default_config(model="deepseek-v4-flash-0731", fallbackModel="gpt-4o-mini", maxRetries=0),
        transport=httpx.MockTransport(handler),
    )

    assert await client.invoke("test") == "ok"
    assert bodies[0]["model"] == "deepseek-v4-flash-0731"
    assert bodies[0]["reasoning_effort"] == "high"
    assert bodies[1]["model"] == "gpt-4o-mini"
    assert "reasoning_effort" not in bodies[1]
    assert "thinking" not in bodies[1]
