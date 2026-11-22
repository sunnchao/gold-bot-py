"""HTTP adapter for the Pi AI runtime service."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from dataclasses import asdict, dataclass
from typing import Any, Literal, NotRequired, TypedDict

import httpx
from pydantic import BaseModel, ValidationError

from backend.agents.metrics.llm_cache_metrics import record_llm_cache_usage
from backend.agents.utils.logger import get_logger

DEFAULT_PI_RUNTIME_URL = "http://127.0.0.1:3000"


@dataclass(frozen=True)
class CacheStats:
    readTokens: int = 0
    creationTokens: int = 0
    hitTokens: int = 0
    missTokens: int = 0
    inputTokens: int = 0


@dataclass(frozen=True)
class ToolUse:
    id: str
    name: str
    input: dict[str, Any]


class StreamResultDict(dict[str, Any]):
    """completion 返回值:dict 接口(兼容旧测试) + 属性访问(兼容调用方)。"""

    def __init__(
        self,
        content: str,
        cache_stats: CacheStats,
        tool_use: ToolUse | None = None,
        thinking: str = "",
    ) -> None:
        super().__init__(content=content, cacheStats=cache_stats, toolUse=tool_use, thinking=thinking)

    @property
    def content(self) -> str:
        return self["content"]

    @property
    def cache_stats(self) -> CacheStats:
        return self["cacheStats"]

    @property
    def tool_use(self) -> ToolUse | None:
        return self["toolUse"]

    @property
    def thinking(self) -> str:
        return self["thinking"]


StreamLayeredResult = StreamResultDict


class CompletionRequest(TypedDict):
    model: str
    messages: list[dict[str, str]]
    stream: bool
    temperature: float
    maxTokens: int
    timeoutMs: int
    maxRetries: int
    parameters: dict[str, Any]
    tools: NotRequired[list[dict[str, Any]]]
    toolChoice: NotRequired[str | dict[str, Any]]


StreamRequest = CompletionRequest
CompleteOnce = Callable[[CompletionRequest], Awaitable[StreamResultDict]]
StreamOnce = CompleteOnce


class _PiResponse(BaseModel):
    content: str
    thinking: str = ""
    toolUse: ToolUse | None
    cacheStats: CacheStats | None
    stopReason: Literal["stop", "toolUse", "aborted"]


class _PiDeltaEvent(BaseModel):
    delta: str


class _PiErrorEvent(BaseModel):
    error: str
    stopReason: str | None = None


class PiClient:
    """Calls the internal Pi AI runtime (/v1/complete) for streaming and non-streaming inference."""

    def __init__(
        self,
        url: str = DEFAULT_PI_RUNTIME_URL,
        *,
        client: httpx.AsyncClient | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        try:
            parsed = httpx.URL(url)
        except httpx.InvalidURL as err:
            raise ValueError(
                "PI_RUNTIME_URL must be an HTTP(S) URL without credentials, query or fragment"
            ) from err
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.host
            or parsed.userinfo
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("PI_RUNTIME_URL must be an HTTP(S) URL without credentials, query or fragment")
        self._endpoint = url.rstrip("/") + "/v1/complete"
        self._owns_client = client is None
        self._client = (
            client
            if client is not None
            else httpx.AsyncClient(transport=transport, trust_env=False)
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    @staticmethod
    def _parse_json_response(raw_body: bytes) -> _PiResponse:
        try:
            return _PiResponse.model_validate_json(raw_body, strict=True)
        except ValidationError:
            raise RuntimeError("Pi runtime returned an invalid completion") from None

    @staticmethod
    def _finalize_result(
        result: _PiResponse,
        model: str,
        *,
        content_override: str | None = None,
        thinking_override: str | None = None,
        thinking_chunks: int = 0,
    ) -> StreamResultDict:
        if result.stopReason == "aborted":
            raise asyncio.CancelledError("Pi completion cancelled")
        final_content = result.content if content_override is None else content_override
        final_thinking = result.thinking if thinking_override is None else thinking_override
        if final_thinking:
            get_logger().debug(
                {"model": model, "thinkingChars": len(final_thinking), "thinkingChunks": thinking_chunks},
                "Pi completion thinking received",
            )
        if result.cacheStats is not None:
            record_llm_cache_usage(asdict(result.cacheStats), model)
        return StreamResultDict(
            final_content,
            result.cacheStats or CacheStats(),
            result.toolUse,
            final_thinking,
        )

    @staticmethod
    def _parse_delta(payload_raw: str) -> str:
        try:
            return _PiDeltaEvent.model_validate_json(payload_raw, strict=True).delta
        except ValidationError:
            raise RuntimeError("Pi runtime returned an invalid completion") from None

    @staticmethod
    def _raise_sse_error(payload_raw: str) -> None:
        try:
            err = _PiErrorEvent.model_validate_json(payload_raw, strict=True)
        except ValidationError:
            raise RuntimeError("Pi runtime returned an invalid completion") from None
        if err.error == "deadline_exceeded":
            raise RuntimeError("Pi runtime request failed: TimeoutError")
        if err.stopReason == "aborted":
            raise asyncio.CancelledError("Pi completion cancelled")
        raise RuntimeError("Pi runtime completion failed")

    async def _complete_json(self, request: CompletionRequest, timeout_seconds: float) -> StreamResultDict:
        try:
            async with asyncio.timeout(timeout_seconds):
                timeout = httpx.Timeout(timeout_seconds, connect=min(timeout_seconds, 5.0))
                response = await self._client.post(self._endpoint, json=request, timeout=timeout)
        except (httpx.RequestError, TimeoutError) as error:
            raise RuntimeError(f"Pi runtime request failed: {type(error).__name__}") from None

        if response.status_code != 200:
            raise RuntimeError(f"Pi runtime returned HTTP {response.status_code}")

        result = self._parse_json_response(response.content)
        return self._finalize_result(result, request["model"])

    async def _complete_stream(self, request: CompletionRequest, idle_seconds: float) -> StreamResultDict:
        timeout = httpx.Timeout(
            connect=min(idle_seconds, 5.0),
            write=idle_seconds,
            read=idle_seconds,
            pool=min(idle_seconds, 5.0),
        )
        async with AsyncExitStack() as stack:
            try:
                async with asyncio.timeout(idle_seconds):
                    response = await stack.enter_async_context(
                        self._client.stream("POST", self._endpoint, json=request, timeout=timeout)
                    )
            except (httpx.RequestError, TimeoutError) as error:
                raise RuntimeError(f"Pi runtime request failed: {type(error).__name__}") from None

            if response.status_code != 200:
                raise RuntimeError(f"Pi runtime returned HTTP {response.status_code}")

            content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if content_type != "text/event-stream":
                try:
                    async with asyncio.timeout(idle_seconds):
                        raw_body = await response.aread()
                except (httpx.RequestError, TimeoutError) as error:
                    raise RuntimeError(f"Pi runtime request failed: {type(error).__name__}") from None
                result = self._parse_json_response(raw_body)
                return self._finalize_result(result, request["model"])

            lines_iter = response.aiter_lines()
            event_name: str | None = None
            data_lines: list[str] = []
            thinking_parts: list[str] = []
            text_parts: list[str] = []
            thinking_chunks = 0
            done_result: _PiResponse | None = None

            while True:
                try:
                    async with asyncio.timeout(idle_seconds):
                        line = await anext(lines_iter)
                except StopAsyncIteration:
                    break
                except (httpx.RequestError, TimeoutError) as error:
                    raise RuntimeError(f"Pi runtime request failed: {type(error).__name__}") from None

                if line == "":
                    if not data_lines:
                        event_name = None
                        continue
                    event = event_name or "message"
                    payload_raw = "\n".join(data_lines)
                    event_name = None
                    data_lines.clear()

                    if event == "ping" or event == "tool_call":
                        continue
                    if event == "thinking":
                        delta = self._parse_delta(payload_raw)
                        thinking_chunks += 1
                        if delta:
                            thinking_parts.append(delta)
                    elif event == "text":
                        text_parts.append(self._parse_delta(payload_raw))
                    elif event == "error":
                        self._raise_sse_error(payload_raw)
                    elif event == "done":
                        done_result = self._parse_json_response(payload_raw.encode("utf-8"))
                        break
                    continue

                if line.startswith(":"):
                    continue
                field, sep, val = line.partition(":")
                if not sep:
                    continue
                if val.startswith(" "):
                    val = val[1:]
                if field == "event":
                    event_name = val
                elif field == "data":
                    data_lines.append(val)

            if done_result is None:
                raise RuntimeError("Pi runtime returned an invalid completion")

            final_content = done_result.content or "".join(text_parts)
            final_thinking = done_result.thinking or "".join(thinking_parts)
            return self._finalize_result(
                done_result,
                request["model"],
                content_override=final_content,
                thinking_override=final_thinking,
                thinking_chunks=thinking_chunks,
            )

    async def __call__(self, request: CompletionRequest) -> StreamResultDict:
        idle_seconds = request["timeoutMs"] / 1000 + 1
        if request.get("stream") is False:
            return await self._complete_json(request, idle_seconds)
        return await self._complete_stream(request, idle_seconds)

    async def complete(self, request: CompletionRequest) -> StreamResultDict:
        return await self(request)


PiStreamClient = PiClient
