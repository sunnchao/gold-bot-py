from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import threading
import time
from collections.abc import AsyncIterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from backend.agents.agents.comprehensive_analyst import ComprehensiveAnalystService
from backend.agents.metrics.llm_cache_metrics import llm_cache_registry, reset_llm_cache_metrics
from backend.agents.tools.llm_client import LLMClient, LLMClientConfig, SystemBlock, UserLayer
from backend.agents.tools.pi_client import PiClient
from backend.api.app import create_api_app
from backend.observability.metrics import create_metrics_registry, metrics_text
from backend.persistence import create_in_memory_store

REPO_ROOT = Path(__file__).resolve().parents[4]
PI_RUNTIME_DIR = REPO_ROOT / "pi-runtime"
_PI_AI_INSTALLED = (PI_RUNTIME_DIR / "node_modules" / "@earendil-works" / "pi-ai").is_dir()


def _resolve_supported_node() -> str | None:
    node_bin = shutil.which("node")
    if not node_bin:
        return None
    try:
        proc = subprocess.run(
            [node_bin, "-p", "process.versions.node"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    major_str = proc.stdout.strip().split(".", 1)[0]
    return node_bin if major_str.isdigit() and int(major_str) >= 22 else None


_SUPPORTED_NODE = _resolve_supported_node()


@pytest.fixture(autouse=True)
def _reset_cache_metrics():
    reset_llm_cache_metrics()
    yield
    reset_llm_cache_metrics()


def config(model: str = "kimi-test", fallback: str = "deepseek-test") -> LLMClientConfig:
    return LLMClientConfig("openai", "https://gateway.invalid/v1", "offline-key", model, fallback, 1200, 3, True)


def reply(
    content: str = "",
    tool_use=None,
    cache_stats=None,
    stop_reason: str = "stop",
    thinking: str = "",
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


class _ChunkedSseStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[tuple[float, bytes]]) -> None:
        self._chunks = chunks

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for delay, data in self._chunks:
            if delay > 0:
                await asyncio.sleep(delay)
            yield data


def sse_reply(chunks: list[tuple[float, str]]) -> httpx.Response:
    return httpx.Response(
        200,
        headers={"Content-Type": "text/event-stream; charset=utf-8"},
        stream=_ChunkedSseStream([(delay, text.encode("utf-8")) for delay, text in chunks]),
    )


async def test_maps_existing_contract_and_falls_back_only_on_empty_result() -> None:
    requests: list[dict] = []
    usage = {"inputTokens": 100, "readTokens": 30, "creationTokens": 5, "hitTokens": 30, "missTokens": 65}
    tool_use = {"id": "call-2", "name": "submit_analysis", "input": {"confidence": 80}}
    schema = {"type": "object", "properties": {"confidence": {"type": "integer"}}, "required": ["confidence"]}

    def handle(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "http://pi-runtime:3000/v1/complete"
        assert "authorization" not in request.headers
        body = json.loads(request.content)
        assert "offline-key" not in request.content.decode()
        requests.append(body)
        if body["model"] == "kimi-test":
            return reply(" \n")
        return reply(tool_use=tool_use, cache_stats=usage, stop_reason="toolUse")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        adapter = PiClient("http://pi-runtime:3000", client=http)
        client = LLMClient(config(), complete_once=adapter)
        result = await client.stream_layered(
            [SystemBlock("first", True), SystemBlock("second", True)],
            [UserLayer("stable", True), UserLayer("live", False)],
            {
                "tools": [{"name": "submit_analysis", "input_schema": schema}],
                "toolChoice": {"type": "tool", "name": "submit_analysis"},
            },
        )

    assert result.content == ""
    assert result.tool_use is not None and result.tool_use.input == {"confidence": 80}
    assert [request["model"] for request in requests] == ["kimi-test", "deepseek-test"]
    assert requests[0]["stream"] is True
    assert requests[0]["parameters"] == {
        "prompt_cache_key": "gold-analysis",
        "reasoning_effort": "max",
        "thinking": {"type": "enabled"},
    }
    assert requests[1]["parameters"] == {"reasoning_effort": "max", "thinking": {"type": "disabled"}}
    assert requests[0]["maxTokens"] == 32768
    assert requests[0]["maxRetries"] == 3
    assert requests[0]["timeoutMs"] == 1200
    assert requests[0]["messages"] == [
        {"role": "system", "content": "first\n\nsecond"},
        {"role": "user", "content": "stable"},
        {"role": "user", "content": "live"},
    ]
    assert requests[0]["toolChoice"] == {"type": "function", "function": {"name": "submit_analysis"}}
    assert requests[0]["tools"][0]["function"]["parameters"] == schema
    assert result.cache_stats.inputTokens == 100
    registry = llm_cache_registry()
    assert registry.get_sample_value("goldbot_llm_cache_input_tokens_total", {"model": "deepseek-test"}) == 100
    assert registry.get_sample_value("goldbot_llm_cache_input_tokens_total", {"model": "kimi-test"}) is None


async def test_sends_nonstreaming_flag_on_invoke_and_invoke_layered() -> None:
    bodies: list[dict[str, Any]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return reply("recovered")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        adapter = PiClient("http://pi-runtime:3000", client=http)
        client = LLMClient(config(fallback="kimi-test"), complete_once=adapter)
        assert await client.invoke_layered([SystemBlock("test", True)], [UserLayer("data", False)]) == "recovered"

    assert len(bodies) == 1
    assert bodies[0]["stream"] is False


@pytest.mark.parametrize("remote_abort", [False, True])
async def test_cancellation_does_not_call_the_fallback_model(remote_abort: bool) -> None:
    started = asyncio.Event()

    async def handle(_request: httpx.Request) -> httpx.Response:
        started.set()
        if remote_abort:
            return reply(stop_reason="aborted")
        await asyncio.Event().wait()
        pytest.fail("request must be cancelled")

    mock = AsyncMock(side_effect=handle)
    async with httpx.AsyncClient(transport=httpx.MockTransport(mock)) as http:
        adapter = PiClient("http://pi-runtime:3000", client=http)
        client = LLMClient(config(), complete_once=adapter)
        task = asyncio.create_task(client.stream_invoke("test"))
        await started.wait()
        if not remote_abort:
            task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert mock.await_count == 1


@pytest.mark.parametrize(
    "bad_url",
    [
        "",
        "not a url",
        "ftp://pi-runtime:3000",
        "http://user:pass@pi-runtime:3000",
        "http://pi-runtime:3000?a=1",
        "http://pi-runtime:3000#frag",
        "http://:3000",
    ],
)
def test_rejects_invalid_pi_runtime_urls(bad_url: str) -> None:
    with pytest.raises(ValueError, match="PI_RUNTIME_URL"):
        PiClient(bad_url)


async def test_rejects_non_200_and_invalid_json_responses_without_leaking_body() -> None:
    responses = iter(
        [
            httpx.Response(502, text="secret-upstream-detail"),
            httpx.Response(503, json={"error": "busy"}),
            httpx.Response(200, text="not-json"),
            httpx.Response(200, json={"content": "ok", "toolUse": None, "cacheStats": None, "stopReason": "length"}),
        ]
    )

    def handle(_request: httpx.Request) -> httpx.Response:
        return next(responses)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        adapter = PiClient("http://pi-runtime:3000", client=http)
        client = LLMClient(config(fallback="kimi-test"), complete_once=adapter)
        with pytest.raises(RuntimeError, match="HTTP 502") as exc_502:
            await client.stream_invoke("test")
        assert "secret-upstream-detail" not in str(exc_502.value)

        with pytest.raises(RuntimeError, match="HTTP 503"):
            await client.stream_invoke("test")

        for _ in range(2):
            with pytest.raises(RuntimeError, match="invalid completion"):
                await client.stream_invoke("test")


async def test_metrics_endpoint_exposes_llm_cache_metrics() -> None:
    usage = {"inputTokens": 200, "readTokens": 50, "creationTokens": 10, "hitTokens": 50, "missTokens": 140}
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _r: reply("ok", cache_stats=usage))) as http:
        adapter = PiClient("http://pi-runtime:3000", client=http)
        client = LLMClient(config(), complete_once=adapter)
        result = await client.stream_layered([SystemBlock("sys", True)], [UserLayer("usr", False)])

    assert result.content == "ok"
    app = create_api_app(
        {"store": create_in_memory_store(), "metrics": create_metrics_registry(enable_default=False)}
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as api:
        response = await api.get("/metrics")

    assert response.status_code == 200
    assert 'goldbot_llm_cache_input_tokens_total{model="kimi-test"} 200.0' in response.text
    assert 'goldbot_llm_cache_hit_tokens_total{model="kimi-test"} 50.0' in response.text
    assert 'goldbot_llm_cache_hit_rate{model="kimi-test"} 0.25' in response.text


def _sample_analysis_payload(last_close: float = 4174.0) -> dict[str, Any]:
    def indicator(close: float) -> dict[str, Any]:
        return {
            "close": close,
            "open": close - 1,
            "high": close + 2,
            "low": close - 2,
            "ema20": close - 1,
            "ema50": close - 2,
            "ema200": close - 3,
            "rsi": 55,
            "adx": 24,
            "atr": 5,
            "macd": 1,
            "macd_signal": 0.5,
            "macd_hist": 0.5,
            "bb_upper": close + 10,
            "bb_middle": close,
            "bb_lower": close - 10,
            "stoch_k": 55,
            "stoch_d": 50,
        }

    return {
        "account": {
            "account_id": "acc-001",
            "equity": 10000,
            "balance": 10000,
            "margin": 100,
            "free_margin": 9900,
            "currency": "USD",
            "leverage": 100,
        },
        "market": {"symbol": "XAUUSD", "bid": last_close, "ask": last_close + 0.2, "spread": 0.2},
        "indicators": {
            "M15": indicator(last_close),
            "M30": indicator(last_close),
            "H1": indicator(last_close),
            "H4": indicator(last_close),
        },
        "positions": [],
        "market_status": {"market_open": True, "is_trade_allowed": True, "tradeable": True},
        "strategy_mapping": {"trend": "10001"},
        "bars": {
            "H1": [
                {"time": "2026-06-30T00:00:00Z", "open": 4140, "high": 4150, "low": 4135, "close": 4145},
                {"time": "2026-06-30T01:00:00Z", "open": 4145, "high": 4160, "low": 4140, "close": 4155},
                {"time": "2026-06-30T02:00:00Z", "open": 4155, "high": 4170, "low": 4150, "close": 4165},
                {"time": "2026-06-30T03:00:00Z", "open": 4165, "high": 4180, "low": 4160, "close": last_close},
            ]
        },
        "harmonic_context": {
            "h4_patterns": [],
            "h1_patterns": [],
            "m30_patterns": [],
            "active_pattern": None,
            "direction_bias": "neutral",
            "score": 0,
            "summary": "none",
        },
    }


_BUY_SETUP_MARKDOWN = """## TECHNICAL
- Bias: bullish
- Confidence: 75
- Phase: trending
- Indicators Summary: 多头趋势 (Bullish trend)
- Support Levels:
  - 4140 | support | strong | H1 | 3
- Resistance Levels:
  - 4200 | resistance | strong | H1 | 3
- Recommendation: none
- Rationale: 看涨 (Bullish)

## WAVE
- Confirmation: partial
- Extension Wave: 3
- Corrective Type: zigzag
- Trend Strength: moderate
- Target Level 1.618: 4188
- Target Level 2.0: 4205
- Confidence: 75
- Rationale: 部分确认 (Partial)

## CHANLUN
- Trend: up
- Strength: moderate
- Latest Signal: buy
- Hub State: active
- Confidence: 75
- Rationale: 向上突破 (Upward)

## HARMONIC
- Detected Pattern: none
- Direction: neutral
- Timeframe: N/A
- Completion: 0
- Confidence: 0
- D Zone Price: 0
- Entry Zone: N/A
- Stop Loss: 0
- Take Profit 1: 0
- Take Profit 2: 0
- Rationale: 无形态 (None)

## RISK
- Risk Level: low
- Max Position Size: 0.05
- Suggested SL: 4125
- Suggested TP: 4188
- Warnings: 无 (None)
- Add On: false

## ARBITRATION
- Final Direction: buy
- Confidence: 75
- Action: open
- Primary Contradiction:
- Phase: trending
- United Front Analysis: 看涨 (Bullish)
- Reasoning: 多头趋势仍在，等待回调提供更好盈亏比。
- Dow Primary Trend: bullish
- Dow Primary Phase: markup
- Dow Secondary Trend: bullish
- Dow Short Term Trend: bullish
- Dow Multi TF Confirm: true
- Dow Rationale: bullish
- Wave Current Wave: 3
- Wave Direction: impulse_up
- Wave Count: partial
- Wave Next Target: 4188
- Wave Confidence: 75
- Wave Rationale: partial
- Chanlun Trend: up
- Chanlun Bi Direction: up
- Chanlun Duan Direction: up
- Chanlun Zhongshu State: active
- Chanlun Buy Sell Point: buy_2
- Chanlun Confidence: 75
- Chanlun Rationale: up
- Harmonic Pattern: none
- Harmonic Direction: neutral
- Harmonic Confidence: 0
- Harmonic Rationale: none
- Trade Direction: buy
- Trade Entry Price: 4145
- Trade Stop Loss: 4125
- Trade Take Profit 1: 4188
- Trade Take Profit 2: 4205
- Trade Risk Reward Ratio: 2.15
- Trade Position Size Lots: 0.05
- Trade Rationale: 等待回调至 4145 入场 (wait for pullback to 4145)"""


@pytest.mark.skipif(
    _SUPPORTED_NODE is None or not _PI_AI_INSTALLED,
    reason="Node >= 22 and pi-runtime/node_modules required",
)
async def test_end_to_end_comprehensive_analyst_streaming_and_nonstreaming_with_real_pi_runtime() -> None:
    assert _SUPPORTED_NODE is not None

    captured: list[dict[str, Any]] = []
    fail_phase1_stream = False
    tool_args = json.dumps(
        {
            "side": "buy",
            "entry_price": 4145,
            "stop_loss": 4125,
            "take_profit_1": 4188,
            "take_profit_2": 4205,
            "lots": 0.05,
            "order_type": "limit",
            "reason": "等待回调至 4145 入场 (wait for pullback to 4145)",
        },
        ensure_ascii=False,
    )
    split_at = len(_BUY_SETUP_MARKDOWN) // 2
    tool_split = len(tool_args) // 2

    def sse_bytes(frames: list[dict[str, Any]]) -> bytes:
        chunks = [f"data: {json.dumps({'id': 'fake-chatcmpl', **frame})}\n\n" for frame in frames]
        chunks.append("data: [DONE]\n\n")
        return "".join(chunks).encode("utf-8")

    class GatewayHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            return

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            captured.append({"path": self.path, "auth": self.headers.get("Authorization"), "body": body})

            if body.get("stream") is False:
                payload = json.dumps(
                    {
                        "id": "fake-sync",
                        "choices": [
                            {
                                "index": 0,
                                "message": {"role": "assistant", "content": _BUY_SETUP_MARKDOWN},
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": {
                            "prompt_tokens": 1200,
                            "completion_tokens": 300,
                            "prompt_tokens_details": {"cached_tokens": 600},
                        },
                    }
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return

            if not body.get("tools") and fail_phase1_stream:
                err_body = b'{"error":{"message":"forced-stream-failure"}}'
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(err_body)))
                self.end_headers()
                self.wfile.write(err_body)
                return

            if body.get("tools"):
                payload = sse_bytes(
                    [
                        {
                            "choices": [
                                {
                                    "index": 0,
                                    "delta": {
                                        "tool_calls": [
                                            {
                                                "index": 0,
                                                "id": "call-trade-1",
                                                "type": "function",
                                                "function": {
                                                    "name": "place_pending_order",
                                                    "arguments": tool_args[:tool_split],
                                                },
                                            }
                                        ]
                                    },
                                    "finish_reason": None,
                                }
                            ]
                        },
                        {
                            "choices": [
                                {
                                    "index": 0,
                                    "delta": {
                                        "tool_calls": [
                                            {
                                                "index": 0,
                                                "function": {"arguments": tool_args[tool_split:]},
                                            }
                                        ]
                                    },
                                    "finish_reason": "tool_calls",
                                }
                            ]
                        },
                        {
                            "choices": [],
                            "usage": {
                                "prompt_tokens": 800,
                                "completion_tokens": 40,
                                "prompt_tokens_details": {"cached_tokens": 200},
                            },
                        },
                    ]
                )
            else:
                payload = sse_bytes(
                    [
                        {
                            "choices": [
                                {
                                    "index": 0,
                                    "delta": {"content": _BUY_SETUP_MARKDOWN[:split_at]},
                                    "finish_reason": None,
                                }
                            ]
                        },
                        {
                            "choices": [
                                {
                                    "index": 0,
                                    "delta": {"content": _BUY_SETUP_MARKDOWN[split_at:]},
                                    "finish_reason": "stop",
                                }
                            ]
                        },
                        {
                            "choices": [],
                            "usage": {
                                "prompt_tokens": 1200,
                                "completion_tokens": 300,
                                "prompt_tokens_details": {"cached_tokens": 600, "cache_write_tokens": 100},
                            },
                        },
                    ]
                )
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    gateway = ThreadingHTTPServer(("127.0.0.1", 0), GatewayHandler)
    gateway_thread = threading.Thread(target=gateway.serve_forever, daemon=True)
    gateway_thread.start()
    gateway_url = f"http://127.0.0.1:{gateway.server_address[1]}/v1"

    pi_bootstrap = (
        "import { createPiServer } from './src/server.mjs';"
        "const s = createPiServer({"
        "  baseUrl: process.env.LLM_BASE_URL,"
        "  apiKey: process.env.LLM_API_KEY,"
        "  modelIds: [process.env.LLM_MODEL, process.env.LLM_TRADE_MODEL, process.env.LLM_FALLBACK_MODEL],"
        "  maxTimeoutMs: 5000, maxRetries: 0"
        "});"
        "s.listen(0, '127.0.0.1', () => {"
        "  console.log(JSON.stringify({ port: s.address().port }));"
        "});"
        "process.on('SIGTERM', () => s.close(() => process.exit(0)));"
    )
    node_env = {
        "PATH": os.environ.get("PATH", ""),
        "LLM_BASE_URL": gateway_url,
        "LLM_API_KEY": "gateway-secret-key",
        "LLM_MODEL": "kimi-test",
        "LLM_TRADE_MODEL": "deepseek-trade-test",
        "LLM_FALLBACK_MODEL": "kimi-test",
    }
    pi_proc = subprocess.Popen(
        [_SUPPORTED_NODE, "--input-type=module", "-e", pi_bootstrap],
        cwd=str(PI_RUNTIME_DIR),
        env=node_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert pi_proc.stdout is not None
        ready_line = await asyncio.to_thread(pi_proc.stdout.readline)
        assert ready_line, f"pi-runtime failed to start: {pi_proc.stderr.read() if pi_proc.stderr else ''}"
        pi_port = json.loads(ready_line)["port"]
        pi_base_url = f"http://127.0.0.1:{pi_port}"

        payload = _sample_analysis_payload()
        analysis_cfg = LLMClientConfig("openai", gateway_url, "unused", "kimi-test", "kimi-test", 5000, 0, True)
        trade_cfg = LLMClientConfig("openai", gateway_url, "unused", "deepseek-trade-test", "kimi-test", 5000, 0, True)

        async with httpx.AsyncClient(trust_env=False) as pi_http:
            assert (await pi_http.get(f"{pi_base_url}/healthz")).status_code == 200
            adapter = PiClient(pi_base_url, client=pi_http)
            pi_service = ComprehensiveAnalystService(
                LLMClient(analysis_cfg, complete_once=adapter),
                trade_client=LLMClient(trade_cfg, complete_once=adapter),
                trade_model="deepseek-trade-test",
            )
            stream_result = await pi_service.run(payload, "XAUUSD")

            fail_phase1_stream = True
            fallback_result = await pi_service.run(payload, "XAUUSD")

        expected_action = {
            "type": "place_pending_order",
            "side": "buy",
            "entry_price": 4145,
            "stop_loss": 4125,
            "take_profit_1": 4188,
            "take_profit_2": 4205,
            "lots": 0.05,
            "order_type": "limit",
            "expiry_hours": 4,
            "reason": "等待回调至 4145 入场 (wait for pullback to 4145)",
        }
        assert stream_result == fallback_result
        assert stream_result["tradeAction"] == expected_action

        assert len(captured) == 5
        assert all(item["path"] == "/v1/chat/completions" for item in captured)
        assert all(item["auth"] == "Bearer gateway-secret-key" for item in captured)
        stream_phase1, stream_phase2, failed_stream, sync_fallback, sync_phase2 = [c["body"] for c in captured]
        assert stream_phase1["stream"] is True
        assert stream_phase1["model"] == "kimi-test"
        assert stream_phase1["prompt_cache_key"] == "gold-analysis"
        assert stream_phase1["reasoning_effort"] == "max"
        assert stream_phase1["thinking"] == {"type": "enabled"}
        assert stream_phase1["max_completion_tokens"] == 32768
        assert stream_phase2["model"] == "deepseek-trade-test"
        assert stream_phase2["reasoning_effort"] == "max"
        assert stream_phase2["thinking"] == {"type": "disabled"}
        assert stream_phase2["tool_choice"] == "auto"
        assert failed_stream["stream"] is True
        assert sync_fallback["stream"] is False
        assert "stream_options" not in sync_fallback
        assert sync_fallback["model"] == "kimi-test"
        assert sync_phase2["stream"] is True

        exported = await metrics_text()
        assert 'goldbot_llm_cache_input_tokens_total{model="kimi-test"} 2400.0' in exported
        assert 'goldbot_llm_cache_hit_rate{model="kimi-test"} 0.5' in exported
        assert 'goldbot_llm_cache_input_tokens_total{model="deepseek-trade-test"} 1600.0' in exported
        assert 'goldbot_llm_cache_hit_rate{model="deepseek-trade-test"} 0.25' in exported
    finally:
        pi_proc.terminate()
        try:
            pi_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pi_proc.kill()
            pi_proc.wait(timeout=5)
        gateway.shutdown()
        gateway.server_close()


async def test_streams_sse_events_resets_idle_timeout_and_captures_thinking() -> None:
    def sse_frame(event: str, data: dict[str, Any]) -> str:
        return f"event: {event}\ndata: {json.dumps(data)}\n\n"

    chunks = [
        (0.0, sse_frame("thinking", {"delta": "step 1 "})),
        (0.45, sse_frame("ping", {})),
        (0.45, sse_frame("thinking", {"delta": "step 2"})),
        (0.45, sse_frame("tool_call", {"active": True})),
        (0.0, sse_frame("text", {"delta": "final "})),
        (0.0, sse_frame("text", {"delta": "answer"})),
        (
            0.0,
            sse_frame(
                "done",
                {
                    "content": "final answer",
                    "thinking": "step 1 step 2",
                    "toolUse": None,
                    "cacheStats": {
                        "inputTokens": 120,
                        "readTokens": 30,
                        "creationTokens": 0,
                        "hitTokens": 30,
                        "missTokens": 90,
                    },
                    "stopReason": "stop",
                },
            ),
        ),
    ]

    def handle(_request: httpx.Request) -> httpx.Response:
        return sse_reply(chunks)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        adapter = PiClient("http://pi-runtime:3000", client=http)
        cfg = LLMClientConfig(
            "openai", "https://gateway.invalid/v1", "offline-key", "kimi-test", "deepseek-test", 50, 0, True
        )
        client = LLMClient(cfg, complete_once=adapter)
        result = await client.stream_layered([SystemBlock("sys", True)], [UserLayer("usr", False)])

    assert result.content == "final answer"
    assert result.thinking == "step 1 step 2"
    assert result.cache_stats.hitTokens == 30


async def test_sse_stalled_stream_hits_idle_timeout() -> None:
    class _StalledStream(httpx.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            yield b'event: thinking\ndata: {"delta": "start"}\n\n'
            await asyncio.Event().wait()

    def handle(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, stream=_StalledStream())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        adapter = PiClient("http://pi-runtime:3000", client=http)
        cfg = LLMClientConfig(
            "openai", "https://gateway.invalid/v1", "offline-key", "kimi-test", "kimi-test", 1, 0, True
        )
        client = LLMClient(cfg, complete_once=adapter)
        with pytest.raises(RuntimeError, match="TimeoutError"):
            await client.stream_invoke("test")


async def test_sse_midstream_error_and_premature_eof_drop_partial_chunks() -> None:
    def sse_frame(event: str, data: dict[str, Any]) -> str:
        return f"event: {event}\ndata: {json.dumps(data)}\n\n"

    outcomes = iter(
        [
            sse_reply(
                [
                    (0.0, sse_frame("text", {"delta": "partial"})),
                    (0.0, sse_frame("error", {"error": "upstream_completion_failed"})),
                ]
            ),
            sse_reply(
                [
                    (0.0, sse_frame("text", {"delta": "partial"})),
                    (0.0, sse_frame("error", {"error": "deadline_exceeded"})),
                ]
            ),
            sse_reply([(0.0, sse_frame("text", {"delta": "partial"}))]),
            sse_reply(
                [(0.0, sse_frame("error", {"error": "upstream_completion_failed", "stopReason": "aborted"}))]
            ),
        ]
    )
    calls = 0

    def handle(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return next(outcomes)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        adapter = PiClient("http://pi-runtime:3000", client=http)
        client = LLMClient(config(fallback="kimi-test"), complete_once=adapter)

        with pytest.raises(RuntimeError, match="completion failed"):
            await client.stream_invoke("test")
        with pytest.raises(RuntimeError, match="TimeoutError"):
            await client.stream_invoke("test")
        with pytest.raises(RuntimeError, match="invalid completion"):
            await client.stream_invoke("test")

        client_with_fallback = LLMClient(config(fallback="deepseek-test"), complete_once=adapter)
        calls = 0
        with pytest.raises(asyncio.CancelledError):
            await client_with_fallback.stream_invoke("test")
        assert calls == 1


@pytest.mark.skipif(
    _SUPPORTED_NODE is None or not _PI_AI_INSTALLED,
    reason="requires Node >= 22 and pi-runtime dependencies installed",
)
async def test_real_pi_runtime_slow_thinking_longer_than_idle_timeout() -> None:
    stall_mode = False
    calls = 0

    class SlowThinkingGateway(BaseHTTPRequestHandler):
        def log_message(self, _format: str, *_args: object) -> None:
            return

        def do_POST(self) -> None:
            nonlocal calls
            calls += 1
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()

            try:
                if stall_mode:
                    first = {
                        "id": "fake",
                        "choices": [{"index": 0, "delta": {"reasoning_content": "hang"}, "finish_reason": None}],
                    }
                    self.wfile.write(f"data: {json.dumps(first)}\n\n".encode())
                    self.wfile.flush()
                    time.sleep(0.8)
                    return

                for i in range(6):
                    frame = {
                        "id": "fake",
                        "choices": [{"index": 0, "delta": {"reasoning_content": f"t{i} "}, "finish_reason": None}],
                    }
                    self.wfile.write(f"data: {json.dumps(frame)}\n\n".encode())
                    self.wfile.flush()
                    time.sleep(0.09)
                final = {
                    "id": "fake",
                    "choices": [{"index": 0, "delta": {"content": "slow success"}, "finish_reason": "stop"}],
                }
                self.wfile.write(f"data: {json.dumps(final)}\n\ndata: [DONE]\n\n".encode())
                self.wfile.flush()
            except BrokenPipeError:
                return

    gateway = ThreadingHTTPServer(("127.0.0.1", 0), SlowThinkingGateway)
    gateway_thread = threading.Thread(target=gateway.serve_forever, daemon=True)
    gateway_thread.start()
    gateway_url = f"http://127.0.0.1:{gateway.server_address[1]}/v1"

    pi_bootstrap = (
        "import { createPiServer } from './src/server.mjs';"
        "const s = createPiServer({"
        "  baseUrl: process.env.LLM_BASE_URL,"
        "  apiKey: process.env.LLM_API_KEY,"
        "  modelIds: [process.env.LLM_MODEL, process.env.LLM_FALLBACK_MODEL],"
        "  maxTimeoutMs: 1000, maxRetries: 0, keepaliveMs: 40, maxStreamDurationMs: 10000"
        "});"
        "s.listen(0, '127.0.0.1', () => {"
        "  console.log(JSON.stringify({ port: s.address().port }));"
        "});"
        "process.on('SIGTERM', () => s.close(() => process.exit(0)));"
    )
    pi_proc = subprocess.Popen(
        [_SUPPORTED_NODE, "--input-type=module", "-e", pi_bootstrap],
        cwd=str(PI_RUNTIME_DIR),
        env={
            "PATH": os.environ.get("PATH", ""),
            "LLM_BASE_URL": gateway_url,
            "LLM_API_KEY": "gateway-secret",
            "LLM_MODEL": "kimi-test",
            "LLM_FALLBACK_MODEL": "deepseek-test",
        },
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert pi_proc.stdout is not None
        ready_line = await asyncio.to_thread(pi_proc.stdout.readline)
        assert ready_line, f"pi-runtime failed to start: {pi_proc.stderr.read() if pi_proc.stderr else ''}"
        pi_port = json.loads(ready_line)["port"]
        pi_base_url = f"http://127.0.0.1:{pi_port}"

        async with httpx.AsyncClient(trust_env=False) as pi_http:
            adapter = PiClient(pi_base_url, client=pi_http)
            cfg = LLMClientConfig("openai", gateway_url, "unused", "kimi-test", "deepseek-test", 250, 0, True)
            client = LLMClient(cfg, complete_once=adapter)
            res = await client.stream_layered([SystemBlock("sys", True)], [UserLayer("usr", False)])
            assert res.content == "slow success"
            assert res.thinking == "t0 t1 t2 t3 t4 t5 "
            assert calls == 1

            stall_mode = True
            cfg_no_fb = LLMClientConfig("openai", gateway_url, "unused", "kimi-test", "kimi-test", 250, 0, True)
            client_no_fb = LLMClient(cfg_no_fb, complete_once=adapter)
            with pytest.raises(RuntimeError, match="TimeoutError"):
                await client_no_fb.stream_invoke("test")
    finally:
        pi_proc.terminate()
        try:
            pi_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pi_proc.kill()
            pi_proc.wait(timeout=5)
        gateway.shutdown()
        gateway.server_close()
