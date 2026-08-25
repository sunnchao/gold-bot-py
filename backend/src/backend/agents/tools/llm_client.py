"""镜像 apps/app-agent/src/tools/llm-client.ts(纯逻辑 + 离线可测会话管理)。

OpenAI Chat Completions 客户端:
- request 组装(system 首条 / user 分层 / tools + tool_choice 映射)
- 缓存策略探测(deepseek/gpt→auto_prefix;kimi→prompt_cache_key;glm→
  auto_prefix_unstable;minimax/abab→none;其余→auto_prefix)
- cache usage 读取(OpenAI 顶层字段 / DeepSeek native / prompt_tokens_details /
  billing_usage.openai_usage 嵌套 / Kimi cached_tokens 优先级)
- SSE 流解析(delta 累积、indexed tool_calls 累积、[DONE]、finish_reason tool_calls)

HTTP 层用 httpx(可注入 MockTransport,测试完全离线)。
"""

from __future__ import annotations

import importlib
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field, replace
from typing import Any

import httpx

from backend.agents.utils.logger import get_logger

__all__ = [
    "CacheStats",
    "CacheStrategy",
    "LLMClient",
    "LLMClientConfig",
    "LAYER_SEPARATOR",
    "LlmClientService",
    "SystemBlock",
    "ToolUse",
    "UserLayer",
    "compute_cache_hit_rate",
    "detect_cache_strategy",
]

DEFAULT_MAX_TOKENS = 16384
DEFAULT_TEMPERATURE = 0.1
LAYER_SEPARATOR = "\n\n----------------------------------------\n\n"

CacheStrategyType = str
# 'auto_prefix' | 'prompt_cache_key' | 'auto_prefix_unstable' | 'none'


@dataclass(frozen=True)
class CacheStrategy:
    type: str


@dataclass(frozen=True)
class SystemBlock:
    text: str
    cacheable: bool


@dataclass(frozen=True)
class UserLayer:
    text: str
    cacheable: bool


@dataclass(frozen=True)
class CacheStats:
    readTokens: int = 0
    creationTokens: int = 0
    hitTokens: int = 0
    missTokens: int = 0
    inputTokens: int = 0


def compute_cache_hit_rate(stats: Any) -> float:
    """缓存命中率 = cache_read_input_tokens / input_tokens。"""
    if not stats.inputTokens or stats.inputTokens <= 0:
        return 0
    return stats.readTokens / stats.inputTokens


_MODEL_CACHE_PATTERNS: list[tuple[list[str], CacheStrategy]] = [
    (["deepseek"], CacheStrategy(type="auto_prefix")),
    (["gpt-", "gpt4", "gpt-4", "-o1", "-o3", "-o4"], CacheStrategy(type="auto_prefix")),
    (["moonshot", "kimi"], CacheStrategy(type="prompt_cache_key")),
    (["glm", "chatglm"], CacheStrategy(type="auto_prefix_unstable")),
    (["minimax", "abab"], CacheStrategy(type="none")),
]


def detect_cache_strategy(model: str, enable_prompt_caching: bool) -> CacheStrategy:
    if not enable_prompt_caching:
        return CacheStrategy(type="none")

    model_lower = model.lower()
    for keywords, strategy in _MODEL_CACHE_PATTERNS:
        if any(keyword in model_lower for keyword in keywords):
            return strategy

    return CacheStrategy(type="auto_prefix")


@dataclass(frozen=True)
class LLMClientConfig:
    provider: str
    baseUrl: str
    apiKey: str
    model: str
    fallbackModel: str
    timeout: int
    maxRetries: int
    enablePromptCaching: bool


@dataclass(frozen=True)
class ToolUse:
    id: str
    name: str
    input: dict[str, Any]


@dataclass
class PendingToolUse:
    id: str
    name: str
    inputJson: str


@dataclass
class ChatCompletionsStreamResult:
    content: str
    chunks: int
    cacheStats: CacheStats
    toolUse: ToolUse | None = None


@dataclass
class ChatCompletionsSseParseState:
    pendingToolUses: dict[int, PendingToolUse] = field(default_factory=dict)
    done: bool = False


def _as_blocks(items: Any, block_type: Any) -> list[Any]:
    result: list[Any] = []
    for item in items or []:
        if isinstance(item, block_type):
            result.append(item)
        elif isinstance(item, dict):
            result.append(block_type(text=item["text"], cacheable=bool(item.get("cacheable", True))))
    return result


def _as_number(value: Any) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


class LLMClient:
    """镜像 LLMClient:OpenAI Chat Completions API 客户端。"""

    def __init__(self, config: LLMClientConfig, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._config = config
        self._cache_strategy = detect_cache_strategy(config.model, config.enablePromptCaching)
        self._transport = transport
        self._timeout = config.timeout / 1000.0

    # ------------------------------------------------------------------ 纯逻辑

    def get_cache_strategy(self) -> CacheStrategy:
        return CacheStrategy(type=self._cache_strategy.type)

    def get_model(self) -> str:
        return self._config.model

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=self._transport, timeout=self._timeout)

    def _build_messages(self, prompt: str, system_message: str | None = None) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = []
        if system_message:
            messages.append({"role": "system", "content": system_message})
        messages.append({"role": "user", "content": prompt})
        return messages

    def _build_request(self, prompt: str, system_message: str | None = None, stream: bool = False) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self._config.model,
            "max_tokens": DEFAULT_MAX_TOKENS,
            "messages": self._build_messages(prompt, system_message),
            "temperature": DEFAULT_TEMPERATURE,
        }

        if stream:
            body["stream"] = True
            body["stream_options"] = {"include_usage": True}

        return body

    def _completions_url(self) -> str:
        return f"{self._config.baseUrl.rstrip('/')}/chat/completions"

    def _combine_user_messages(self, user_messages: list[str]) -> str:
        return LAYER_SEPARATOR.join(user_messages)

    def _build_layered_request_body(
        self,
        system_blocks: list[SystemBlock],
        user_layers: list[UserLayer],
        stream: bool,
        opts: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        system_message = "\n\n".join(block.text for block in system_blocks)
        messages: list[dict[str, str]] = []
        if system_message:
            messages.append({"role": "system", "content": system_message})
        messages.extend({"role": "user", "content": layer.text} for layer in user_layers)

        body: dict[str, Any] = {
            "model": self._config.model,
            "max_tokens": DEFAULT_MAX_TOKENS,
            "messages": messages,
            "temperature": DEFAULT_TEMPERATURE,
        }

        if stream:
            body["stream"] = True
            body["stream_options"] = {"include_usage": True}

        if self._cache_strategy.type == "prompt_cache_key":
            body["prompt_cache_key"] = "gold-analysis"

        opts = opts or {}
        tools = opts.get("tools")
        if tools and len(tools) > 0:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool["name"],
                        "description": tool["description"],
                        "parameters": tool["input_schema"],
                    },
                }
                for tool in tools
            ]
            choice = opts.get("tool_choice")
            choice_type = choice.get("type") if isinstance(choice, dict) else None
            if choice_type == "any":
                body["tool_choice"] = "required"
            elif choice_type == "tool":
                body["tool_choice"] = {
                    "type": "function",
                    "function": {"name": choice.get("name") if isinstance(choice, dict) else None},
                }
            else:
                # 'auto' 或未指定 → auto
                body["tool_choice"] = "auto"

        return body

    def _build_layered_request(
        self,
        system_blocks: list[SystemBlock],
        user_layers: list[UserLayer],
        stream: bool,
        opts: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "method": "POST",
            "body": self._build_layered_request_body(system_blocks, user_layers, stream, opts),
        }

    def _parse_response_text(self, raw: dict[str, Any]) -> str:
        if not isinstance(raw.get("choices"), list) or len(raw["choices"]) == 0:
            return ""
        choice = raw["choices"][0]
        if not isinstance(choice, dict):
            return ""
        message = choice.get("message")
        if not isinstance(message, dict):
            return ""
        content = message.get("content")
        return content if isinstance(content, str) else ""

    def _read_cache_usage(self, usage: Any, current: CacheStats) -> CacheStats:
        if usage is None or not isinstance(usage, dict):
            return current

        read_tokens = _as_number(usage.get("cache_read_input_tokens"))
        creation_tokens = _as_number(usage.get("cache_creation_input_tokens"))
        input_tokens = _as_number(usage.get("input_tokens"))
        prompt_tokens = _as_number(usage.get("prompt_tokens"))

        # DeepSeek-native cache fields
        deepseek_hit_tokens = _as_number(usage.get("prompt_cache_hit_tokens"))
        deepseek_miss_tokens = _as_number(usage.get("prompt_cache_miss_tokens"))

        # OpenAI-style cached tokens: top-level prompt_tokens_details,及嵌套
        # billing_usage.openai_usage.prompt_tokens_details(this gateway 实际位置)
        prompt_details = usage.get("prompt_tokens_details")
        openai_cached_tokens = (
            _as_number(prompt_details.get("cached_tokens")) if isinstance(prompt_details, dict) else None
        )
        billing_usage = usage.get("billing_usage")
        openai_usage = billing_usage.get("openai_usage") if isinstance(billing_usage, dict) else None
        nested_details = openai_usage.get("prompt_tokens_details") if isinstance(openai_usage, dict) else None
        nested_cached_tokens = (
            _as_number(nested_details.get("cached_tokens")) if isinstance(nested_details, dict) else None
        )

        kimi_cached_tokens = _as_number(usage.get("cached_tokens"))

        read = read_tokens if read_tokens is not None else current.readTokens
        created = creation_tokens if creation_tokens is not None else current.creationTokens
        fresh = (
            input_tokens
            if input_tokens is not None
            else (prompt_tokens if prompt_tokens is not None else current.inputTokens)
        )
        hit = (
            deepseek_hit_tokens
            if deepseek_hit_tokens is not None
            else (
                openai_cached_tokens
                if openai_cached_tokens is not None
                else (
                    nested_cached_tokens
                    if nested_cached_tokens is not None
                    else (kimi_cached_tokens if kimi_cached_tokens is not None else current.hitTokens)
                )
            )
        )
        miss = deepseek_miss_tokens if deepseek_miss_tokens is not None else current.missTokens

        return CacheStats(
            readTokens=int(read),
            creationTokens=int(created),
            hitTokens=int(hit),
            missTokens=int(miss),
            inputTokens=int(fresh),
        )

    # ------------------------------------------------------------------ SSE 解析

    def _process_chat_completions_sse_event(
        self,
        raw_event: str,
        result: ChatCompletionsStreamResult,
        parse_state: ChatCompletionsSseParseState,
    ) -> ChatCompletionsStreamResult:
        lines = raw_event.split("\n")
        data_lines: list[str] = []
        for raw_line in lines:
            line = raw_line.rstrip()
            if line.startswith("data:"):
                data_lines.append(line[len("data:") :].lstrip())

        if len(data_lines) == 0:
            return result

        data_str = "\n".join(data_lines).strip()
        if not data_str:
            return result
        if data_str == "[DONE]":
            parse_state.done = True
            return result

        try:
            data = json.loads(data_str)
            if not isinstance(data, dict):
                return result
            next_result = ChatCompletionsStreamResult(
                content=result.content,
                chunks=result.chunks,
                cacheStats=self._read_cache_usage(data.get("usage"), result.cacheStats),
                toolUse=result.toolUse,
            )
            choices = data.get("choices")
            choice = choices[0] if isinstance(choices, list) and choices else None
            if choice is None or not isinstance(choice, dict):
                return next_result

            delta = choice.get("delta")
            if isinstance(delta, dict):
                if isinstance(delta.get("content"), str):
                    next_result = replace(
                        next_result,
                        content=next_result.content + delta["content"],
                        chunks=next_result.chunks + 1,
                    )

                if isinstance(delta.get("tool_calls"), list):
                    for raw_tool_call in delta["tool_calls"]:
                        if not isinstance(raw_tool_call, dict):
                            continue
                        if not isinstance(raw_tool_call.get("index"), int):
                            continue
                        index = raw_tool_call["index"]
                        pending = parse_state.pendingToolUses.get(index, PendingToolUse(id="", name="", inputJson=""))
                        if isinstance(raw_tool_call.get("id"), str):
                            pending = replace(pending, id=raw_tool_call["id"])
                        function = raw_tool_call.get("function")
                        if isinstance(function, dict):
                            if isinstance(function.get("name"), str):
                                pending = replace(pending, name=function["name"])
                            if isinstance(function.get("arguments"), str):
                                pending = replace(pending, inputJson=pending.inputJson + function["arguments"])
                        parse_state.pendingToolUses[index] = pending

            if choice.get("finish_reason") == "tool_calls":
                for index in sorted(parse_state.pendingToolUses.keys()):
                    pending_tool_use = parse_state.pendingToolUses[index]
                    try:
                        parsed_input = json.loads(pending_tool_use.inputJson)
                    except (ValueError, TypeError) as err:
                        get_logger().warn(
                            {"err": str(err), "rawJson": pending_tool_use.inputJson},
                            "tool_use input parse failed",
                        )
                        continue
                    if isinstance(parsed_input, dict) and not isinstance(parsed_input, list):
                        next_result = replace(
                            next_result,
                            toolUse=ToolUse(id=pending_tool_use.id, name=pending_tool_use.name, input=parsed_input),
                        )
                        break
                parse_state.pendingToolUses.clear()

            return next_result
        except Exception:  # noqa: BLE001 — 镜像 TS catch{ return result }
            return result

    async def _read_chat_completions_stream(
        self,
        reader: AsyncIterator[bytes],
    ) -> ChatCompletionsStreamResult:
        import codecs
        import re

        sse_split = re.compile(r"\r?\n\r?\n")
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        buffer = ""
        result = ChatCompletionsStreamResult(
            content="",
            chunks=0,
            cacheStats=CacheStats(),
        )
        parse_state = ChatCompletionsSseParseState()

        async for value in reader:
            if parse_state.done:
                break
            buffer += decoder.decode(value)
            events = sse_split.split(buffer)
            buffer = events.pop() if events else ""
            for event in events:
                if event.strip():
                    result = self._process_chat_completions_sse_event(event, result, parse_state)
                    if parse_state.done:
                        break

        if not parse_state.done:
            buffer += decoder.decode(b"", final=True)
            if buffer.strip():
                result = self._process_chat_completions_sse_event(buffer, result, parse_state)

        return result

    # ------------------------------------------------------------------ HTTP

    @staticmethod
    def _is_ok(status_code: int) -> bool:
        return 200 <= status_code < 300

    async def _post_json(self, url: str, body: dict[str, Any]) -> dict[str, Any]:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._config.apiKey}",
        }
        async with self._client() as client:
            response = await client.post(url, headers=headers, json=body)
            if not self._is_ok(response.status_code):
                raise RuntimeError(f"OpenAI Chat Completions API {response.status_code}: {response.text}")
            try:
                data = response.json()
            except ValueError:
                data = {}
            return data if isinstance(data, dict) else {}

    async def _stream_bytes(self, url: str, body: dict[str, Any]) -> AsyncIterator[bytes]:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._config.apiKey}",
        }
        async with self._client() as client:
            async with client.stream("POST", url, headers=headers, json=body) as response:
                if not self._is_ok(response.status_code):
                    try:
                        text = (await response.aread()).decode("utf-8", errors="replace")
                    except Exception:  # noqa: BLE001
                        text = "no body"
                    raise RuntimeError(f"OpenAI Chat Completions API {response.status_code}: {text}")
                async for chunk in response.aiter_bytes():
                    yield chunk

    # ------------------------------------------------------------------ 对外方法

    async def invoke(self, prompt: str, system_message: str | None = None) -> str:
        """非流式 invoke:发送单次 Chat Completions 请求返回完整文本。"""
        url = self._completions_url()
        body = self._build_request(prompt, system_message, False)
        data = await self._post_json(url, body)
        return self._parse_response_text(data)

    async def stream_invoke(self, prompt: str, system_message: str | None = None) -> str:
        """流式 invoke:收集 OpenAI Chat Completions SSE 文本块。"""
        url = self._completions_url()
        body = self._build_request(prompt, system_message, True)
        result = await self._read_chat_completions_stream(self._stream_bytes(url, body))
        return result.content

    async def stream_layered(
        self,
        system_blocks: list[SystemBlock],
        user_layers: list[UserLayer],
        opts: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """策略感知的分层流式调用:可缓存层作为独立 request messages。"""
        url = self._completions_url()
        body = self._build_layered_request_body(
            _as_blocks(system_blocks, SystemBlock),
            _as_blocks(user_layers, UserLayer),
            True,
            opts,
        )
        result = await self._read_chat_completions_stream(self._stream_bytes(url, body))

        response_body: dict[str, Any] = {
            "content": result.content,
            "cacheStats": result.cacheStats,
        }
        if result.toolUse is not None:
            response_body["toolUse"] = result.toolUse

        _record_llm_cache_usage(result.cacheStats, self._config.model)

        return response_body

    async def stream_invoke_layered(self, system_message: str, user_messages: list[str]) -> str:
        """@deprecated 兼容包装:保持旧的合并单 user 消息请求形态。"""
        prompt = self._combine_user_messages(user_messages)
        url = self._completions_url()
        body = self._build_request(prompt, system_message, True)
        result = await self._read_chat_completions_stream(self._stream_bytes(url, body))
        return result.content

    async def invoke_layered(
        self,
        system_message: str | list[SystemBlock],
        user_messages: list[str] | list[UserLayer],
        opts: dict[str, Any] | None = None,
    ) -> str:
        """非流式分层调用;system_message 为 str 时走旧的合并消息路径。"""
        url = self._completions_url()
        if isinstance(system_message, str):
            body = self._build_request(
                self._combine_user_messages([str(m) for m in user_messages]),
                system_message,
                False,
            )
        else:
            body = self._build_layered_request_body(
                _as_blocks(system_message, SystemBlock),
                _as_blocks(user_messages, UserLayer),
                False,
                opts,
            )
        data = await self._post_json(url, body)
        return self._parse_response_text(data)


def _record_llm_cache_usage(stats: CacheStats, model: str) -> None:
    """镜像 metrics.recordLlmCacheUsage;metrics 模块由其他 worker 移植,
    通过 importlib 动态接入,缺失时静默跳过。"""
    try:
        module = importlib.import_module("backend.agents.metrics.llm_cache_metrics")
        record = module.record_llm_cache_usage
        record(stats, model)
    except (ImportError, AttributeError):
        pass


class LlmClientService(LLMClient):
    """镜像 LlmClientService:由 AppConfigService 构造。"""

    def __init__(self, config: Any) -> None:
        llm = config.llm
        super().__init__(
            LLMClientConfig(
                provider=llm["provider"],
                baseUrl=llm["baseUrl"],
                apiKey=llm["apiKey"],
                model=llm["model"],
                fallbackModel=llm["fallbackModel"],
                timeout=llm["timeout"],
                maxRetries=llm["maxRetries"],
                enablePromptCaching=llm["enablePromptCaching"],
            )
        )
