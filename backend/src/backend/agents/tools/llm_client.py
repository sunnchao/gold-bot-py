"""基于 Pi AI runtime 的 OpenAI Chat Completions 业务客户端。

保留原业务契约:
- 分层请求(system 首条 / user 分层 / tools + tool_choice 映射)语义保留
- 缓存策略探测保留(deepseek/gpt→auto_prefix;kimi/moonshot→prompt_cache_key;
  glm→auto_prefix_unstable;minimax/abab→none;其余→auto_prefix)
- 模型专属参数保留(DeepSeek reasoning_effort=medium + thinking=disabled;
  Kimi/Moonshot reasoning_effort=medium + thinking=enabled + maxTokens=32768)
- fallback 模型兜底:请求抛错时自动改用 fallback 模型重试一次;流式路径下空响应
  (无内容且无 tool_use)同样触发回退,非流式路径仅在异常时回退;fallback 与
  主模型相同(或为空)时不启用。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, TypeVar

import httpx

from backend.agents.tools.pi_client import (
    DEFAULT_PI_RUNTIME_URL,
    CacheStats,
    CompleteOnce,
    CompletionRequest,
    PiClient,
    StreamLayeredResult,
    StreamOnce,
    StreamRequest,
    StreamResultDict,
    ToolUse,
)
from backend.agents.utils.logger import get_logger

__all__ = [
    "CacheStats",
    "CacheStrategy",
    "ChatCompletionsStreamResult",
    "CompleteOnce",
    "CompletionRequest",
    "LLMClient",
    "LLMClientConfig",
    "LAYER_SEPARATOR",
    "LlmClientService",
    "StreamLayeredResult",
    "StreamOnce",
    "StreamRequest",
    "StreamResultDict",
    "SystemBlock",
    "ToolUse",
    "UserLayer",
    "compute_cache_hit_rate",
    "detect_cache_strategy",
]

DEFAULT_MAX_TOKENS = 16384
DEFAULT_TEMPERATURE = 0.1
# Kimi 思考模型:思考 token 计入 completion,正文预算需相应放大。
KIMI_THINKING_MAX_TOKENS = 32768
# 思考强度:medium 在保留推理能力的同时显著压低思考 token 与耗时。
REASONING_EFFORT = "medium"
LAYER_SEPARATOR = "\n\n----------------------------------------\n\n"

ResponseT = TypeVar("ResponseT")

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


def _is_deepseek_model(model: str) -> bool:
    """DeepSeek 推理模型:reasoning_effort=max + 关闭 thinking 输出。"""
    return "deepseek" in model.lower()


def _is_kimi_thinking_model(model: str) -> bool:
    """Kimi/Moonshot 思考模型(kimi-k3.1-pro 等):开启 max 思考。"""
    model_lower = model.lower()
    return "kimi" in model_lower or "moonshot" in model_lower


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


@dataclass
class ChatCompletionsStreamResult:
    """兼容导出:streamLayered 的返回值形态(content + cacheStats + toolUse)。"""

    content: str
    chunks: int = 0
    cacheStats: CacheStats = CacheStats()
    toolUse: ToolUse | None = None


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


class LLMClient:
    """通过 Pi AI runtime 发起流式与非流式 Chat Completions 调用。"""

    def __init__(
        self,
        config: LLMClientConfig,
        *,
        pi_runtime_url: str = DEFAULT_PI_RUNTIME_URL,
        http_client: httpx.AsyncClient | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        complete_once: CompleteOnce | None = None,
        stream_once: CompleteOnce | None = None,
    ) -> None:
        self._config = config
        self._cache_strategy = detect_cache_strategy(config.model, config.enablePromptCaching)
        injected = complete_once or stream_once
        self._owned_pi_client: PiClient | None = None
        if injected is not None:
            self._complete_once = injected
        else:
            pi_client = PiClient(pi_runtime_url, client=http_client, transport=transport)
            self._owned_pi_client = pi_client
            self._complete_once = pi_client

    async def aclose(self) -> None:
        if self._owned_pi_client is not None:
            await self._owned_pi_client.aclose()

    # ------------------------------------------------------------------ 纯逻辑

    def get_cache_strategy(self) -> CacheStrategy:
        return CacheStrategy(type=self._cache_strategy.type)

    def get_model(self) -> str:
        return self._config.model

    def _model_parameters(self, model: str) -> dict[str, Any]:
        params: dict[str, Any] = {"temperature": DEFAULT_TEMPERATURE, "max_tokens": DEFAULT_MAX_TOKENS}
        if detect_cache_strategy(model, self._config.enablePromptCaching).type == "prompt_cache_key":
            params["extra_body"] = {"prompt_cache_key": "gold-analysis"}
        if _is_deepseek_model(model):
            extra_body = params.get("extra_body") or {}
            extra_body["reasoning_effort"] = REASONING_EFFORT
            extra_body["thinking"] = {"type": "disabled"}
            params["extra_body"] = extra_body
        if _is_kimi_thinking_model(model):
            extra_body = params.get("extra_body") or {}
            extra_body["reasoning_effort"] = REASONING_EFFORT
            extra_body["thinking"] = {"type": "enabled"}
            params["extra_body"] = extra_body
            params["max_tokens"] = max(int(params["max_tokens"]), KIMI_THINKING_MAX_TOKENS)
        return params

    def _model_chain(self) -> list[str]:
        """按优先级排列的模型名:主模型在前,fallback 模型在后。"""
        chain = [self._config.model]
        fallback_model = (self._config.fallbackModel or "").strip()
        if fallback_model and fallback_model != self._config.model:
            chain.append(fallback_model)
        return chain

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

    def _build_request(
        self,
        model: str,
        messages: list[dict[str, str]],
        opts: dict[str, Any],
        *,
        stream: bool,
    ) -> CompletionRequest:
        params = self._model_parameters(model)
        request: CompletionRequest = {
            "model": model,
            "messages": messages,
            "stream": stream,
            "temperature": params["temperature"],
            "maxTokens": params["max_tokens"],
            "timeoutMs": self._config.timeout,
            "maxRetries": self._config.maxRetries,
            "parameters": params.get("extra_body", {}),
        }
        if tools := opts.get("tools"):
            request["tools"] = self._to_openai_tools(tools)
            request["toolChoice"] = self._resolve_tool_choice(opts)
        return request

    # ------------------------------------------------------------------ 对外方法

    async def _run_with_fallback(
        self,
        label: str,
        run: Callable[[str], Awaitable[ResponseT]],
        *,
        is_empty: Callable[[Any], bool] | None = None,
    ) -> ResponseT:
        """按 主模型 → fallback 模型 顺序执行同一请求。

        - 请求抛错:记 warn 日志后尝试下一个模型;最后一个模型的异常原样抛出
        - 空结果(可选 is_empty 判定,仅流式路径传入):非最后一个模型时尝试下一个
        - 最后一个模型的结果总是采纳(含空结果,交由调用方兜底)
        """
        logger = get_logger()
        chain = self._model_chain()
        for index, model in enumerate(chain):
            is_last = index == len(chain) - 1
            try:
                result = await run(model)
            except Exception as err:
                logger.warn({"model": model, "err": str(err)}, f"{label}: request failed")
                if is_last:
                    raise
                continue
            if not is_last and result is not None and is_empty is not None and is_empty(result):
                logger.warn(
                    {"model": model, "fallbackModel": chain[index + 1]},
                    f"{label}: empty result, retrying with fallback model",
                )
                continue
            return result
        raise RuntimeError("unreachable: model chain is never empty")

    async def invoke(self, prompt: str, system_message: str | None = None) -> str:
        """非流式 invoke:发送 stream=false 请求返回文本;请求异常时回退 fallback 模型。"""
        messages = self._build_messages(prompt, system_message)
        return await self._run_with_fallback(
            "invoke",
            lambda model: self._invoke_once(model, messages, {}),
        )

    async def _invoke_once(self, model: str, messages: list[dict[str, str]], opts: dict[str, Any]) -> str:
        result = await self._complete_once(self._build_request(model, messages, opts, stream=False))
        return result.content

    async def stream_invoke(self, prompt: str, system_message: str | None = None) -> str:
        """流式 invoke:收集 SSE 文本;异常或空响应时回退 fallback 模型。"""
        messages = self._build_messages(prompt, system_message)
        return await self._run_with_fallback(
            "streamInvoke",
            lambda model: self._stream_text(model, messages),
            is_empty=lambda text: text.strip() == "",
        )

    async def _stream_text(self, model: str, messages: list[dict[str, str]]) -> str:
        return (await self._complete_once(self._build_request(model, messages, {}, stream=True))).content

    async def stream_layered(
        self,
        system_blocks: list[SystemBlock],
        user_layers: list[UserLayer],
        opts: dict[str, Any] | None = None,
    ) -> StreamResultDict:
        """策略感知的分层流式调用:异常或空响应(无内容且无 tool_use)时回退 fallback 模型。"""
        opts = opts or {}
        messages = self._build_layered_messages(
            _as_blocks(system_blocks, SystemBlock),
            _as_blocks(user_layers, UserLayer),
        )
        return await self._run_with_fallback(
            "streamLayered",
            lambda model: self._complete_once(self._build_request(model, messages, opts, stream=True)),
            is_empty=lambda result: result.content.strip() == "" and result.tool_use is None,
        )

    async def stream_invoke_layered(self, system_message: str, user_messages: list[str]) -> str:
        """@deprecated 兼容包装:保持旧的合并单 user 消息请求形态。"""
        return await self.stream_invoke(self._combine_user_messages(user_messages), system_message)

    async def invoke_layered(
        self,
        system_message: str | list[SystemBlock],
        user_messages: list[str] | list[UserLayer],
        opts: dict[str, Any] | None = None,
    ) -> str:
        """非流式分层调用;请求异常时回退 fallback 模型(空内容不触发,保持原语义)。

        system_message 为 str 时走旧的合并消息路径。
        """
        opts = opts or {}
        if isinstance(system_message, str):
            messages = self._build_messages(
                self._combine_user_messages([str(m) for m in user_messages]),
                system_message,
            )
            return await self._run_with_fallback(
                "invokeLayered",
                lambda model: self._invoke_once(model, messages, {}),
            )

        messages = self._build_layered_messages(
            _as_blocks(system_message, SystemBlock),
            _as_blocks(user_messages, UserLayer),
        )
        return await self._run_with_fallback(
            "invokeLayered",
            lambda model: self._invoke_once(model, messages, opts),
        )


class LlmClientService(LLMClient):
    """镜像 LlmClientService:由 AppConfigService 构造。"""

    def __init__(
        self,
        config: Any,
        *,
        pi_runtime_url: str | None = None,
        http_client: httpx.AsyncClient | None = None,
        complete_once: CompleteOnce | None = None,
        stream_once: CompleteOnce | None = None,
    ) -> None:
        llm = config.llm
        resolved_url = pi_runtime_url or getattr(config, "pi_runtime_url", None) or DEFAULT_PI_RUNTIME_URL
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
            ),
            pi_runtime_url=resolved_url,
            http_client=http_client,
            complete_once=complete_once,
            stream_once=stream_once,
        )
