"""基于 langchain-openai 的 OpenAI Chat Completions 客户端。

镜像 apps/app-agent/src/tools/llm-client.ts 的业务语义,但请求层改用成熟的
langchain-openai(ChatOpenAI),替代自研 httpx + SSE 解析:

- 请求组装 / 流式解析 / 工具调用 / 重试 / 超时交给 langchain-openai
- 分层请求(system 首条 / user 分层 / tools + tool_choice 映射)语义保留
- 缓存策略探测保留(deepseek/gpt→auto_prefix;kimi/moonshot→prompt_cache_key
  (通过 extra_body 下发 prompt_cache_key);glm→auto_prefix_unstable;
  minimax/abab→none;其余→auto_prefix)
- cache usage 读取:流式取 langchain 的标准 usage_metadata(input_tokens +
  input_token_details.cache_read / cache_creation,对应 OpenAI 标准
  prompt_tokens_details.cached_tokens);非流式响应中原始 usage 完整保留在
  message.response_metadata["token_usage"],可经 _read_cache_usage 解析
  DeepSeek native / billing_usage.openai_usage 嵌套 / Kimi cached_tokens

HTTP 层托管于 langchain-openai;测试通过注入 httpx.MockTransport 完全离线。
"""

from __future__ import annotations

import importlib
import json
from dataclasses import asdict, dataclass, field, replace
from typing import Any

import httpx
from langchain_openai import ChatOpenAI
from openai import APIStatusError

from backend.agents.utils.logger import get_logger

__all__ = [
    "CacheStats",
    "CacheStrategy",
    "ChatCompletionsStreamResult",
    "LLMClient",
    "LLMClientConfig",
    "LAYER_SEPARATOR",
    "LlmClientService",
    "StreamLayeredResult",
    "StreamResultDict",
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

    def get(self, key: str, default: Any = None) -> Any:
        """兼容 dict 风格访问(_support stub 返回 dict)。"""
        return getattr(self, key, default)


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
    """兼容导出:streamLayered 的返回值形态(content + cacheStats + toolUse)。"""

    content: str
    chunks: int = 0
    cacheStats: CacheStats = field(default_factory=CacheStats)
    toolUse: ToolUse | None = None


class StreamResultDict(dict[str, Any]):
    """stream_layered 返回值:dict 接口(兼容旧测试) + 属性访问(兼容调用方)。

    - dict 键保持旧 camelCase:content / cacheStats / toolUse
    - 属性保持 _support.StreamResult 契约:content / cache_stats / tool_use
    """

    def __init__(self, content: str, cache_stats: CacheStats, tool_use: ToolUse | None = None) -> None:
        super().__init__(content=content, cacheStats=cache_stats, toolUse=tool_use)

    @property
    def content(self) -> str:
        return self["content"]

    @property
    def cache_stats(self) -> CacheStats:
        return self["cacheStats"]

    @property
    def tool_use(self) -> ToolUse | None:
        return self["toolUse"]


StreamLayeredResult = StreamResultDict


def _as_blocks(items: Any, block_type: Any) -> list[Any]:
    result: list[Any] = []
    for item in items or []:
        if isinstance(item, block_type):
            result.append(item)
        elif isinstance(item, dict):
            result.append(block_type(text=item["text"], cacheable=bool(item.get("cacheable", True))))
        elif hasattr(item, "text"):
            result.append(block_type(text=str(item.text), cacheable=bool(getattr(item, "cacheable", True))))
    return result


def _as_number(value: Any) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


class LLMClient:
    """基于 langchain-openai ChatOpenAI 的 OpenAI Chat Completions 客户端。"""

    def __init__(self, config: LLMClientConfig, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._config = config
        self._cache_strategy = detect_cache_strategy(config.model, config.enablePromptCaching)
        self._transport = transport
        self._timeout = config.timeout / 1000.0
        self._chat = self._build_chat()

    # ------------------------------------------------------------------ 纯逻辑

    def get_cache_strategy(self) -> CacheStrategy:
        return CacheStrategy(type=self._cache_strategy.type)

    def get_model(self) -> str:
        return self._config.model

    def _build_chat(self) -> ChatOpenAI:
        params: dict[str, Any] = {
            "model": self._config.model,
            "api_key": self._config.apiKey,
            "base_url": self._config.baseUrl,
            "temperature": DEFAULT_TEMPERATURE,
            "max_tokens": DEFAULT_MAX_TOKENS,
            "request_timeout": self._timeout,
            "max_retries": self._config.maxRetries,
        }
        if self._cache_strategy.type == "prompt_cache_key":
            params["extra_body"] = {"prompt_cache_key": "gold-analysis"}
        if self._transport is not None:
            # httpx.MockTransport 同时实现 BaseTransport 与 AsyncBaseTransport
            params["http_client"] = httpx.Client(transport=self._transport, timeout=self._timeout)  # type: ignore[arg-type]
            params["http_async_client"] = httpx.AsyncClient(transport=self._transport, timeout=self._timeout)
        return ChatOpenAI(**params)

    def _build_messages(self, prompt: str, system_message: str | None = None) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = []
        if system_message:
            messages.append({"role": "system", "content": system_message})
        messages.append({"role": "user", "content": prompt})
        return messages

    def _combine_user_messages(self, user_messages: list[str]) -> str:
        return LAYER_SEPARATOR.join(user_messages)

    def _build_layered_messages(
        self,
        system_blocks: list[SystemBlock],
        user_layers: list[UserLayer],
    ) -> list[dict[str, str]]:
        system_message = "\n\n".join(block.text for block in system_blocks)
        messages: list[dict[str, str]] = []
        if system_message:
            messages.append({"role": "system", "content": system_message})
        messages.extend({"role": "user", "content": layer.text} for layer in user_layers)
        return messages

    # ------------------------------------------------------------------ 工具映射

    @staticmethod
    def _to_openai_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": tool["input_schema"],
                },
            }
            for tool in tools
        ]

    @staticmethod
    def _resolve_tool_choice(opts: dict[str, Any]) -> str | dict[str, Any]:
        """镜像 TS toolChoice 映射;兼容 snake_case(tool_choice)与 camelCase(toolChoice)。"""
        raw = opts.get("tool_choice", opts.get("toolChoice"))
        if not isinstance(raw, dict):
            return "auto"
        choice_type = raw.get("type")
        if choice_type == "any":
            return "required"
        if choice_type == "tool":
            return {"type": "function", "function": {"name": raw.get("name")}}
        if choice_type in ("auto", "none"):
            return choice_type
        return "auto"

    def _with_tools(self, opts: dict[str, Any]) -> Any:
        """bind_tools 返回 Runnable,仍提供 astream/ainvoke,标注 Any 便于调用。"""
        tools = opts.get("tools")
        if not tools or len(tools) == 0:
            return self._chat
        openai_tools = self._to_openai_tools(tools)
        return self._chat.bind_tools(openai_tools, tool_choice=self._resolve_tool_choice(opts))

    # ------------------------------------------------------------------ usage

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
        # billing_usage.openai_usage.prompt_tokens_details(网关位置)
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

    def _cache_stats_from_usage_metadata(self, usage: Any) -> CacheStats:
        """流式路径:langchain usage_metadata 只含标准字段。

        input_tokens 对应 prompt_tokens;input_token_details.cache_read 对应
        OpenAI 标准 prompt_tokens_details.cached_tokens(DeepSeek native /
        网关嵌套缓存字段在流式下不可得,指标退化为标准字段,非流式完整)。
        """
        if usage is None or not isinstance(usage, dict):
            return CacheStats()
        input_tokens = int(usage.get("input_tokens") or 0)
        details = usage.get("input_token_details")
        cache_read = int(details.get("cache_read") or 0) if isinstance(details, dict) else 0
        cache_creation = int(details.get("cache_creation") or 0) if isinstance(details, dict) else 0
        return CacheStats(
            readTokens=cache_read,
            creationTokens=cache_creation,
            hitTokens=cache_read,
            inputTokens=input_tokens,
        )

    # ------------------------------------------------------------------ 流式聚合

    @staticmethod
    def _accumulate_tool_call_chunks(
        chunk: Any,
        pending: dict[int, PendingToolUse],
    ) -> None:
        for raw in chunk.tool_call_chunks or []:
            index = raw.get("index")
            if index is None:
                continue
            current = pending.get(index, PendingToolUse(id="", name="", inputJson=""))
            if isinstance(raw.get("id"), str):
                current = replace(current, id=raw["id"])
            if isinstance(raw.get("name"), str):
                current = replace(current, name=raw["name"])
            if isinstance(raw.get("args"), str):
                current = replace(current, inputJson=current.inputJson + raw["args"])
            pending[index] = current

    @staticmethod
    def _finalize_tool_use(pending: dict[int, PendingToolUse]) -> ToolUse | None:
        for index in sorted(pending.keys()):
            pending_tool_use = pending[index]
            try:
                parsed_input = json.loads(pending_tool_use.inputJson)
            except (ValueError, TypeError) as err:
                get_logger().warn(
                    {"err": str(err), "rawJson": pending_tool_use.inputJson},
                    "tool_use input parse failed",
                )
                continue
            if isinstance(parsed_input, dict):
                return ToolUse(id=pending_tool_use.id, name=pending_tool_use.name, input=parsed_input)
        return None

    @staticmethod
    def _rethrow(err: APIStatusError) -> RuntimeError:
        body = err.body
        text = body if isinstance(body, str) else (json.dumps(body) if body else "no body")
        return RuntimeError(f"OpenAI Chat Completions API {err.status_code}: {text}")

    # ------------------------------------------------------------------ 对外方法

    async def invoke(self, prompt: str, system_message: str | None = None) -> str:
        """非流式 invoke:发送单次 Chat Completions 请求返回完整文本。"""
        messages = self._build_messages(prompt, system_message)
        try:
            message = await self._chat.ainvoke(messages)
        except APIStatusError as err:
            raise self._rethrow(err) from err
        content = message.content
        return content if isinstance(content, str) else ""

    async def stream_invoke(self, prompt: str, system_message: str | None = None) -> str:
        """流式 invoke:收集 OpenAI Chat Completions SSE 文本块。"""
        messages = self._build_messages(prompt, system_message)
        return await self._stream_text(messages)

    async def _stream_text(self, messages: list[dict[str, str]]) -> str:
        parts: list[str] = []
        try:
            async for chunk in self._chat.astream(messages, stream_options={"include_usage": True}):
                if isinstance(chunk.content, str) and chunk.content:
                    parts.append(chunk.content)
        except APIStatusError as err:
            raise self._rethrow(err) from err
        return "".join(parts)

    async def stream_layered(
        self,
        system_blocks: list[SystemBlock],
        user_layers: list[UserLayer],
        opts: dict[str, Any] | None = None,
    ) -> StreamResultDict:
        """策略感知的分层流式调用:可缓存层作为独立 request messages。"""
        opts = opts or {}
        messages = self._build_layered_messages(
            _as_blocks(system_blocks, SystemBlock),
            _as_blocks(user_layers, UserLayer),
        )
        chat = self._with_tools(opts)

        content_parts: list[str] = []
        pending: dict[int, PendingToolUse] = {}
        usage: Any | None = None
        try:
            async for chunk in chat.astream(messages, stream_options={"include_usage": True}):
                if isinstance(chunk.content, str) and chunk.content:
                    content_parts.append(chunk.content)
                self._accumulate_tool_call_chunks(chunk, pending)
                if chunk.usage_metadata:
                    usage = chunk.usage_metadata
        except APIStatusError as err:
            raise self._rethrow(err) from err

        cache_stats = self._cache_stats_from_usage_metadata(usage)
        tool_use = self._finalize_tool_use(pending)

        _record_llm_cache_usage(cache_stats, self._config.model)

        return StreamResultDict(content="".join(content_parts), cache_stats=cache_stats, tool_use=tool_use)

    async def stream_invoke_layered(self, system_message: str, user_messages: list[str]) -> str:
        """@deprecated 兼容包装:保持旧的合并单 user 消息请求形态。"""
        return await self.stream_invoke(self._combine_user_messages(user_messages), system_message)

    async def invoke_layered(
        self,
        system_message: str | list[SystemBlock],
        user_messages: list[str] | list[UserLayer],
        opts: dict[str, Any] | None = None,
    ) -> str:
        """非流式分层调用;system_message 为 str 时走旧的合并消息路径。"""
        if isinstance(system_message, str):
            messages = self._build_messages(
                self._combine_user_messages([str(m) for m in user_messages]),
                system_message,
            )
            chat = self._chat
        else:
            messages = self._build_layered_messages(
                _as_blocks(system_message, SystemBlock),
                _as_blocks(user_messages, UserLayer),
            )
            chat = self._with_tools(opts or {})
        try:
            message = await chat.ainvoke(messages)
        except APIStatusError as err:
            raise self._rethrow(err) from err
        content = message.content
        return content if isinstance(content, str) else ""


def _record_llm_cache_usage(stats: CacheStats, model: str) -> None:
    """镜像 metrics.recordLlmCacheUsage;metrics 模块由其他 worker 移植,
    通过 importlib 动态接入,缺失时静默跳过。"""
    try:
        module = importlib.import_module("backend.agents.metrics.llm_cache_metrics")
        record = module.record_llm_cache_usage
        record(asdict(stats), model)
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
