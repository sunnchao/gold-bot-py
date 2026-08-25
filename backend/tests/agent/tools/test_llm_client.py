"""镜像 gold-bot `apps/app-agent/src/tools/llm-client.test.ts`(离线可测部分)。

HTTP 层注入 httpx.MockTransport,完全离线。
"""

import json
import logging

import httpx
import pytest

from backend.agents.tools.llm_client import (
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
    assert json.loads(request.content) == {
        "model": "gpt-4o",
        "max_tokens": 16384,
        "messages": [
            {"role": "system", "content": "You are terse"},
            {"role": "user", "content": "Say hello"},
        ],
        "temperature": 0.1,
    }


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
                            "input_tokens": 300,
                            "cache_read_input_tokens": 200,
                            "cache_creation_input_tokens": 100,
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

    assert result["content"] == "ok"
    assert result["cacheStats"].readTokens == 200
    assert result["cacheStats"].creationTokens == 100
    assert result["cacheStats"].hitTokens == 0
    assert result["cacheStats"].missTokens == 0
    assert result["cacheStats"].inputTokens == 300
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


async def test_preserves_usage_field_precedence_and_input_tokens_over_prompt_tokens():
    # TS: 'preserves usage field precedence and input_tokens over prompt_tokens'
    parts = [
        sse({"choices": [{"delta": {"content": "cached"}, "finish_reason": None}]}),
        sse(
            {
                "choices": [],
                "usage": {
                    "cache_read_input_tokens": 200,
                    "cache_creation_input_tokens": 100,
                    "input_tokens": 400,
                    "prompt_tokens": 999,
                    "prompt_cache_hit_tokens": 300,
                    "prompt_cache_miss_tokens": 50,
                    "prompt_tokens_details": {"cached_tokens": 120},
                    "billing_usage": {"openai_usage": {"prompt_tokens_details": {"cached_tokens": 110}}},
                    "cached_tokens": 90,
                },
            }
        ),
        "data: [DONE]\n\n",
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return stream_response(parts)

    client = LLMClient(default_config(), transport=httpx.MockTransport(handler))
    result = await client.stream_layered(
        [{"text": "system", "cacheable": True}],
        [{"text": "user", "cacheable": True}],
    )

    assert result["content"] == "cached"
    assert result["cacheStats"].readTokens == 200
    assert result["cacheStats"].creationTokens == 100
    assert result["cacheStats"].hitTokens == 300
    assert result["cacheStats"].missTokens == 50
    assert result["cacheStats"].inputTokens == 400


async def test_falls_back_from_input_tokens_to_openai_prompt_tokens():
    # TS: 'falls back from input_tokens to OpenAI prompt_tokens'
    parts = [
        sse({"choices": [], "usage": {"prompt_tokens": 321}}),
        "data: [DONE]\n\n",
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return stream_response(parts)

    client = LLMClient(default_config(), transport=httpx.MockTransport(handler))
    result = await client.stream_layered([], [])

    assert result["cacheStats"].inputTokens == 321


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
async def test_falls_back_to_cached_token_sources(label, usage, expected):
    # TS: it.each(['OpenAI cached tokens', 'nested gateway cached tokens', 'Kimi cached tokens'])
    #     'falls back to %s'
    parts = [
        sse({"choices": [], "usage": usage}),
        "data: [DONE]\n\n",
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return stream_response(parts)

    client = LLMClient(default_config(), transport=httpx.MockTransport(handler))
    result = await client.stream_layered([], [])

    assert result["cacheStats"].hitTokens == expected


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

    with pytest.raises(RuntimeError, match="OpenAI Chat Completions API 401: Unauthorized"):
        await client.invoke("test")
