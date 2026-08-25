"""FastAPI 应用工厂(镜像 gold-bot apps/app-server/src/app.ts createAppServer)。

将 JsonResponse 形态的路由处理器翻译为 FastAPI Response:
- body 为 dict → JSONResponse
- rawBody 为 bytes/str(下载、metrics 文本)→ 原样文本/二进制
- headers 透传(Content-Disposition / Content-Type)
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from backend.api.dashboard import static_dashboard_response
from backend.api.http.json import parse_json_object
from backend.api.http.response import JsonResponse, error
from backend.api.middleware.auth import extract_auth_token
from backend.api.routes.ea.index import (
    ea_download_response,
    ea_version_check_response,
    ea_version_response,
    handle_ea_route,
    handle_trade_history,
)
from backend.api.routes.indicator_alert import (
    create_indicator_alert_cache,
    handle_indicator_alert_route,
)
from backend.api.routes.logs import router as logs_router
from backend.api.routes.users import router as users_router
from backend.api.routes.visual import handle_visual_route
from backend.observability.metrics import default_metrics_registry, metrics_text
from backend.observability.metrics_middleware import create_http_metrics_middleware
from backend.observability.sse import event_stream_headers, format_sse_frame
from backend.persistence.store import EaStore
from backend.services.bar_close import BarCloseEventService
from backend.shared_contracts import EA_COMPAT_ENDPOINTS, is_ea_compat_endpoint

__all__ = ["create_api_app", "to_fastapi_response"]


def _default_logger() -> Callable[[str], None]:
    logger = logging.getLogger("goldbot.ea")

    def emit(message: str) -> None:
        logger.info(message)

    return emit


def _now_unix_default() -> int:
    import time as _time

    return int(_time.time())


def _now_iso_default() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()


def _default_on_order_result(
    store: EaStore, metrics: Any, command_lifecycle: Any | None = None
) -> Callable[..., Any]:
    async def reconcile(
        account_id: str,
        command_id: str,
        result: str,
        ticket: int | None,
        error_text: str,
        created_at: str,
    ) -> None:
        # Phase 2.4 命令回报计数:error_code 只保留纯数字错误码,其余归 other
        if metrics is not None:
            trimmed = (error_text or "").strip()
            if trimmed == "":
                error_code = "none"
            elif re.fullmatch(r"\d{1,6}", trimmed) is not None:
                error_code = trimmed
            else:
                error_code = "other"
            result_key = result.strip().upper()
            metrics.command_results_total.labels(account_id, result_key or "UNKNOWN", error_code).inc()
        # 镜像 TS:void commandLifecycle.reconcile(...)
        if command_lifecycle is not None:
            await command_lifecycle.reconcile(account_id, command_id, result, ticket, error_text, created_at)
        else:
            await store.reconcile_command_result(account_id, command_id, result, ticket, error_text, created_at)

    return reconcile


def to_fastapi_response(json_response: JsonResponse) -> Response:
    """JsonResponse → FastAPI Response(statusCode/headers/body/rawBody)。"""
    status_code = int(json_response.get("statusCode", 200))
    headers = dict(json_response.get("headers") or {})
    raw_body = json_response.get("rawBody")
    body = json_response.get("body")
    if raw_body is not None:
        if isinstance(raw_body, bytes):
            return Response(content=raw_body, status_code=status_code, headers=headers)
        return Response(
            content=str(raw_body),
            status_code=status_code,
            headers=headers,
            media_type=headers.get("Content-Type", "text/plain"),
        )
    return JSONResponse(content=body, status_code=status_code, headers=headers)


def create_api_app(options: dict) -> FastAPI:
    """等价 TS createAppServer:装配 EA 兼容端点 + 管理 API + 指标/通知/SSE。

    options(与 createAppServer 对齐):
      store, now_unix, now_iso, valid_tokens, token_accounts, admin_tokens,
      release_root, log, metrics, http_metrics, alerts, discord, feishu,
      on_bars_saved, on_bar_closed, llm_analysis_trigger, technical_analysis_trigger,
      on_positions_saved, on_order_result, events, idle_timeout
    """
    store: EaStore = options.get("store") or _require_store(options)
    now_unix: Callable[[], int] = options.get("now_unix") or _now_unix_default
    now_iso: Callable[[], str] = options.get("now_iso") or _now_iso_default
    valid_tokens: set[str] | None = options.get("valid_tokens")
    token_accounts: dict[str, set[str]] | None = options.get("token_accounts")
    admin_tokens: set[str] = options.get("admin_tokens") or set()
    release_root: str | Path = options.get("release_root") or Path(__file__).resolve().parents[3]
    log: Callable[[str], None] | None = options.get("log")
    if log is None:
        log = _default_logger()
    metrics: Any = options.get("metrics") or default_metrics_registry
    http_metrics = options.get("http_metrics") or create_http_metrics_middleware({"metrics": metrics})
    alerts = options.get("alerts") or create_indicator_alert_cache(_now_ms_default)
    discord = options.get("discord")
    feishu = options.get("feishu")
    events = options.get("events") or _create_sse_hub_default()

    # M5 服务组合:command_lifecycle + shadow + scheduler(镜像 TS createAppServer 接线)
    from backend.services.analysis.index import AnalysisService
    from backend.services.command_lifecycle.index import (
        CommandLifecycleService,
    )
    from backend.services.scheduler.index import create_scheduler_service
    from backend.services.shadow.index import create_shadow_service

    default_runtime_mode: str = options.get("default_runtime_mode") or "oracle"
    supplied_lifecycle = options.get("command_lifecycle")
    if supplied_lifecycle is not None:
        command_lifecycle = supplied_lifecycle
    else:
        command_lifecycle = CommandLifecycleService(
            store=store,
            default_runtime_mode=default_runtime_mode,
            shadow=create_shadow_service({"store": store, "now_iso": now_iso}),
        )
    shadow = options.get("shadow") or getattr(command_lifecycle, "_shadow", None)
    if shadow is None:
        shadow = create_shadow_service({"store": store, "now_iso": now_iso})
    analysis_service = options.get("analysis_service") or AnalysisService(store=store, now_iso=now_iso)
    scheduler = options.get("scheduler") or create_scheduler_service(
        {
            "analysis": analysis_service,
            "command_lifecycle": command_lifecycle,
            "shadow": shadow,
            "store": store,
            "now_iso": now_iso,
        }
    )

    on_bars_saved = options.get("on_bars_saved")

    technical_analysis_trigger = options.get("technical_analysis_trigger")
    if technical_analysis_trigger is None:

        async def technical_analysis_trigger(
            account_id: str, symbol: str, timeframe: str, _bar_time: str
        ) -> None:
            await scheduler.enqueue_analysis(account_id, symbol, timeframe)

    bar_close_events = options.get("bar_close_events") or BarCloseEventService(
        store,
        llm_trigger=options.get("llm_analysis_trigger"),
        technical_trigger=technical_analysis_trigger,
    )
    on_bar_closed = options.get("on_bar_closed") or bar_close_events.dispatch

    on_positions_saved = options.get("on_positions_saved")

    def default_on_positions_saved(account_id: str, symbol: str) -> None:
        _fire_and_forget_async(scheduler.enqueue_position_review(account_id, symbol))

    if on_positions_saved is None:
        on_positions_saved = default_on_positions_saved

    if "on_order_result" in options and options["on_order_result"] is None:
        # 不注册回调 → EA /order_result 直接落库(镜像 TS onOrderResult == null)
        on_order_result_impl = None
    else:
        on_order_result_impl = options.get("on_order_result") or _default_on_order_result(
            store, metrics, command_lifecycle
        )

    app = FastAPI(title="Gold-Bot FastAPI Port", version="0.4.0")

    # 请求上下文挂在 app.state,供各端点处理器取用
    app.state.store = store
    app.state.now_unix = now_unix
    app.state.now_iso = now_iso
    app.state.valid_tokens = valid_tokens
    app.state.token_accounts = token_accounts
    app.state.admin_tokens = admin_tokens
    app.state.release_root = release_root
    app.state.metrics = metrics
    app.state.http_metrics = http_metrics
    app.state.alerts = alerts
    app.state.discord = discord
    app.state.feishu = feishu
    app.state.log = log
    app.state.events = events
    app.state.on_bars_saved = on_bars_saved
    app.state.on_bar_closed = on_bar_closed
    app.state.bar_close_events = bar_close_events
    app.state.on_positions_saved = on_positions_saved
    app.state.on_order_result = on_order_result_impl
    app.state.command_lifecycle = command_lifecycle
    app.state.shadow = shadow
    app.state.scheduler = scheduler
    app.state.analysis_service = analysis_service
    app.state.ai_approve_cooldown = options.get("ai_approve_cooldown") or _create_ai_approve_cooldown_default()
    # admin 路由的 token 记录表(镜像 TS tokenRecords):首次请求前由 _register_token_bootstrap 填充
    app.state.token_records = {}

    _register_token_bootstrap(app, store, options)
    _register_metrics_middleware(app)
    # 镜像 TS routeRequest 分支顺序:/version_check 等在 EA 兼容端点之前判断,
    # 因此先注册服务端点,再注册 EA 全方法路由与单段 POST 兜底。
    _register_service_endpoints(app)
    _register_ea_endpoints(app)
    _register_admin_api(app)
    _register_dashboard_and_not_found(app)
    return app


def _register_dashboard_and_not_found(app: FastAPI) -> None:
    from starlette.exceptions import HTTPException as StarletteHTTPException

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> Response:
        # POST /{ea_path} 会让 GET /audit、GET /not-found 变成 405。
        # 对仪表盘候选路径按 TS routeRequest 处理:先 SPA,否则 JSON 404。
        path = request.url.path
        method_blocked = exc.status_code == 405 and request.method in {"GET", "HEAD"}
        if exc.status_code == 404 or (method_blocked and _is_dashboard_candidate(path)):
            dashboard = static_dashboard_response(request.method, path, app.state.release_root)
            if dashboard is not None:
                return _translate(dashboard)
            return JSONResponse(content={"status": "ERROR", "message": "not found"}, status_code=404)
        return JSONResponse(content={"detail": exc.detail}, status_code=exc.status_code)


def _is_dashboard_candidate(path: str) -> bool:
    if path in {"/healthz", "/health", "/metrics", "/version_check", "/__contracts", "/api"}:
        return False
    if path.startswith(("/api/", "/shadow/", "/visual/", "/indicator_alert/")):
        return False
    return not is_ea_compat_endpoint(path)


def _register_metrics_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def record_http(request: Request, call_next: Callable[..., Any]) -> Response:
        start = time.monotonic()
        response = await call_next(request)
        duration_ms = (time.monotonic() - start) * 1000
        path = request.url.path
        if path in ("/docs", "/openapi.json", "/redoc"):
            return response
        try:
            app.state.http_metrics.record(
                {
                    "method": request.method,
                    "url": path,
                    "status_code": response.status_code,
                    "duration_ms": duration_ms,
                }
            )
        except Exception:  # 指标记录失败不影响业务
            pass
        return response


def _register_ea_endpoints(app: FastAPI) -> None:
    _EA_ROUTE_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"]

    async def _run_ea(request: Request) -> Response:
        # 镜像 TS handleEaRoute:EA 兼容端点接受任意方法(只要 JSON body 有效,如 GET /poll)
        return _translate(
            await handle_ea_route(
                await _request_dict(request),
                {
                    "valid_tokens": app.state.valid_tokens,
                    "token_accounts": app.state.token_accounts,
                    "admin_tokens": app.state.admin_tokens,
                    "store": app.state.store,
                    "now_unix": app.state.now_unix,
                    "now_iso": app.state.now_iso,
                    "log": _logger_of(app.state),
                    "on_bars_saved": app.state.on_bars_saved,
                    "on_bar_closed": app.state.on_bar_closed,
                    "on_positions_saved": app.state.on_positions_saved,
                    "on_order_result": app.state.on_order_result,
                },
                {
                    "string_field_or_empty": _string_field_or_empty,
                    "symbol_default": _symbol_default,
                    "validate_ea_payload": _validate_ea_payload,
                },
            )
        )

    # 先注册各 EA 兼容端点的全方法路由(GET /poll 等,镜像 TS 不限制 method)
    for _ea_path in EA_COMPAT_ENDPOINTS:
        app.add_api_route(_ea_path, _run_ea, methods=_EA_ROUTE_METHODS, include_in_schema=False)

    # POST 单段兜底(镜像 TS routeRequest):未知 POST 路径落 404 not found 信封。
    # 未知非 POST 单段路径(GET /not-found 等)不注册任何路由,由
    # _register_not_found_fallback 的 404 异常处理器兜底 — 这样 main.py 等外部
    # 在 create_api_app 之后追加的 /health 等单段 GET 路由不会被此兜底遮蔽。
    @app.api_route("/{ea_path}", methods=["POST"], include_in_schema=False)
    async def ea_dispatch(ea_path: str, request: Request) -> Response:
        # 只有 EA 兼容端点 + 其它已知 POST 路由走这里;未知路径 404(与 routeRequest 一致)
        if not is_ea_compat_endpoint("/" + ea_path):
            return JSONResponse(content={"status": "ERROR", "message": "not found"}, status_code=404)
        return await _run_ea(request)

    # 镜像 TS routeRequest:任何包含 /analysis_payload/ 或 /ai_result/ 的路径都先进 AI 分发,
    # 未知子路径由 handle_ai_route 返回 404(而不是落到 admin catch-all 的 405)。
    @app.api_route("/api/analysis_payload/{rest:path}", methods=["GET", "POST", "PUT", "PATCH"])
    @app.api_route("/api/v2/analysis_payload/{rest:path}", methods=["GET", "POST", "PUT", "PATCH"])
    @app.api_route("/api/ai_result/{rest:path}", methods=["GET", "POST", "PUT", "PATCH"])
    @app.api_route("/api/v2/ai_result/{rest:path}", methods=["GET", "POST", "PUT", "PATCH"])
    async def ai_dispatch(request: Request) -> Response:
        from backend.api.routes.ai import analysis_payload
        from backend.api.routes.ai.index import handle_ai_result_route, handle_ai_route

        return _translate(
            await handle_ai_route(
                await _request_dict(request),
                {
                    "store": app.state.store,
                    "now_iso": app.state.now_iso,
                    "valid_tokens": app.state.valid_tokens,
                    "token_accounts": app.state.token_accounts,
                    "admin_tokens": app.state.admin_tokens,
                    "events": app.state.events,
                    "command_lifecycle": app.state.command_lifecycle,
                    "shadow": app.state.shadow,
                    "ai_approve_cooldown": app.state.ai_approve_cooldown,
                    "discord": app.state.discord,
                    "feishu": app.state.feishu,
                },
                {
                    "analysis_payload": analysis_payload,
                    "handle_ai_result_route": handle_ai_result_route,
                },
            )
        )

    @app.api_route("/visual/poll", methods=["POST", "GET"])
    async def visual_poll(request: Request) -> Response:
        return _translate(
            await handle_visual_route(
                await _request_dict(request),
                {
                    "store": app.state.store,
                    "now_iso": app.state.now_iso,
                    "valid_tokens": app.state.valid_tokens,
                    "token_accounts": app.state.token_accounts,
                    "admin_tokens": app.state.admin_tokens,
                    "alerts": app.state.alerts,
                },
            )
        )

    @app.api_route("/indicator_alert/{indicator_path}", methods=["POST", "GET"])
    async def indicator_alert(indicator_path: str, request: Request) -> Response:
        response = handle_indicator_alert_route(
            await _request_dict(request),
            {"valid_tokens": app.state.valid_tokens, "alerts": app.state.alerts},
        )
        if (
            response.get("statusCode") == 200
            and request.url.path == "/indicator_alert/store"
            and isinstance(response.get("body"), dict)
            and response["body"].get("should_send") is True
        ):
            parsed_ok, parsed_body = parse_json_object(await _raw_body(request))
            if parsed_ok:
                _notify_indicator_alert(app.state, parsed_body)
        return _translate(response)


def _create_sse_hub_default() -> Any:
    from backend.observability.sse import create_sse_hub

    return create_sse_hub()


def _create_ai_approve_cooldown_default() -> Any:
    from backend.services.ai_approve.gate import create_ai_approve_cooldown

    return create_ai_approve_cooldown()


async def _sse_event_stream(hub: Any, queue: asyncio.Queue[Any]) -> Any:
    """订阅 hub → 逐帧转发为 SSE 文本;生成器关闭时退订(镜像 streamEvents cleanup)。"""
    unsubscribe = hub.subscribe(queue.put_nowait)
    try:
        while True:
            event = await queue.get()
            yield format_sse_frame(event)
    finally:
        unsubscribe()


def _register_service_endpoints(app: FastAPI) -> None:
    @app.get("/api/v1/events/stream")
    async def events_stream(request: Request) -> Response:
        # 镜像 streamEvents:admin 鉴权 + eventStreamHeaders + subscribe 推送
        guard = require_admin_route_js(app.state, request)
        if guard is not None:
            return _translate(guard)
        if getattr(app.state, "events", None) is None:
            return JSONResponse(
                content={"status": "ERROR", "message": "events hub not configured"},
                status_code=503,
            )
        queue: asyncio.Queue[Any] = asyncio.Queue()

        def stream() -> Any:
            return _sse_event_stream(app.state.events, queue)

        return StreamingResponse(stream(), headers=event_stream_headers())

    @app.get("/healthz")
    async def healthz() -> Response:
        return Response(content="ok", media_type="text/plain")

    @app.get("/metrics")
    async def metrics_endpoint() -> Response:
        from backend.observability.metrics_collector import StoreMetricsCollector

        await StoreMetricsCollector(metrics=app.state.metrics, store=app.state.store).collect()
        # 镜像 TS prometheusMetricsResponse:`/metrics` 自身请求在生成文本前先记录,
        # 使指标文本包含 {method=GET,path=/metrics,status=2xx}(测试断言依赖)。
        try:
            app.state.http_metrics.record(
                {"method": "GET", "url": "/metrics", "status_code": 200, "duration_ms": 0}
            )
        except Exception:  # 指标记录失败不影响响应
            pass
        body = await metrics_text()
        return Response(content=body, media_type="text/plain; version=0.0.4; charset=utf-8")

    @app.get("/api/ea/version")
    async def ea_version(request: Request) -> Response:
        platform = request.query_params.get("platform") or "mt4"
        return _translate(ea_version_response(app.state.release_root, platform))

    # 镜像 TS routeRequest:path === '/version_check' 分支不做 method 限制(任意方法均可,
    # 只要带 route token) → 与 GET 一样注册全方法。
    @app.api_route("/version_check", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def version_check(request: Request) -> Response:
        token_guard = require_route_token_js(app.state, request)
        if token_guard is not None:
            return _translate(token_guard)
        platform = request.query_params.get("platform") or "mt4"
        return _translate(ea_version_check_response(app.state.release_root, platform))

    @app.get("/api/ea/download")
    async def ea_download(request: Request) -> Response:
        token_guard = require_route_token_js(app.state, request)
        if token_guard is not None:
            return _translate(token_guard)
        platform = request.query_params.get("platform") or "mt4"
        return _translate(ea_download_response(app.state.release_root, platform))

    @app.get("/__contracts")
    async def contracts() -> Response:
        return JSONResponse(
            content={
                "status": "OK",
                "phase": 1,
                "ea_endpoints": list(EA_COMPAT_ENDPOINTS),
                "persistence": {"writesLiveCommands": False},
            }
        )

    @app.post("/api/trade_history")
    async def trade_history(request: Request) -> Response:
        token_guard = require_route_token_js(app.state, request)
        if token_guard is not None:
            return _translate(token_guard)
        try:
            body: Any = json.loads(await _raw_body(request) or "{}")
        except ValueError:
            return JSONResponse(content={"status": "ERROR", "message": "invalid JSON"}, status_code=400)
        return _translate(await handle_trade_history(body, app.state.store, app.state.metrics))

    @app.get("/shadow/metrics")
    @app.get("/shadow/qualification")
    async def shadow_service(request: Request) -> Response:
        # 镜像 TS:GET /shadow/metrics + /shadow/qualification → deps.shadow.metrics()/qualification()
        shadow: Any = app.state.shadow
        if shadow is None:
            return JSONResponse(
                content={"status": "ERROR", "message": "shadow service not activated"},
                status_code=501,
            )
        if request.url.path.endswith("/qualification"):
            body = await shadow.qualification()
        else:
            body = await shadow.metrics()
        return JSONResponse(content=body, status_code=200)

    @app.post("/shadow/comparisons")
    async def shadow_comparisons(request: Request) -> Response:
        # 镜像 TS:POST /shadow/comparisons → shadow.recordOracleComparison(node 优先,否则最新运行时快照)
        shadow: Any = app.state.shadow
        if shadow is None:
            return JSONResponse(
                content={"status": "ERROR", "message": "shadow service not activated"},
                status_code=501,
            )
        parsed_ok, parsed_body = parse_json_object(await _raw_body(request))
        if not parsed_ok:
            return JSONResponse(
                content={"status": "ERROR", "message": "invalid JSON"},
                status_code=400,
            )
        from backend.api.routes.ea.index import string_field_or_empty
        from backend.api.routes.visual.index import record_field

        account_id = string_field_or_empty(parsed_body, "account_id").strip()
        symbol = string_field_or_empty(parsed_body, "symbol").strip()
        source = string_field_or_empty(parsed_body, "source").strip()
        node = record_field(parsed_body, "node")
        oracle = record_field(parsed_body, "oracle")
        if len(account_id) == 0 or len(symbol) == 0 or oracle is None:
            return JSONResponse(
                content={"status": "ERROR", "message": "invalid shadow comparison payload"},
                status_code=400,
            )
        comparison_input: dict[str, Any] = {
            "account_id": account_id,
            "symbol": symbol,
            "source": source if source in ("position_review", "ai_result") else "ea_analysis",
            "oracle": oracle,
        }
        protocol_ok = parsed_body.get("protocol_ok")
        if isinstance(protocol_ok, bool):
            comparison_input["protocol_ok"] = protocol_ok
        created_at = string_field_or_empty(parsed_body, "created_at")
        if len(created_at) > 0:
            comparison_input["created_at"] = created_at
        if node is not None:
            comparison_input["node"] = {
                "signal": record_field(node, "signal"),
                "command": record_field(node, "command"),
            }
        try:
            comparison = await shadow.record_oracle_comparison(comparison_input)
        except RuntimeError as err:
            message = str(err)
            status_code = 404 if message == "shadow runtime snapshot not found" else 400
            return JSONResponse(
                content={"status": "ERROR", "message": message},
                status_code=status_code,
            )
        return JSONResponse(content={"status": "OK", "comparison": comparison}, status_code=200)


def _register_token_bootstrap(app: FastAPI, store: EaStore, options: dict) -> None:
    """首次请求前从 store 加载已持久化 API tokens(镜像 TS createAppServer 启动加载)。

    create_api_app 是同步工厂,而 store.list_api_tokens() 是异步的;把加载推迟到
    app 真正的事件循环首次请求时执行(sqlite 场景下也与请求处理器同循环)。
    """

    bootstrap_done = False

    @app.middleware("http")
    async def ensure_tokens_loaded(request: Request, call_next: Callable[..., Any]) -> Response:
        nonlocal bootstrap_done
        if not bootstrap_done:
            bootstrap_done = True
            await _apply_persisted_token_state(app.state, store, options)
        return await call_next(request)


async def _apply_persisted_token_state(state: Any, store: EaStore, options: dict) -> None:
    """镜像 createAppServer 的 storedTokens 加载:

    - options.valid_tokens == null 且 store 无 tokens → validTokens 保持 null(一律 401)
    - 否则 valid_tokens = options ∪ stored;admin_tokens ∪= stored 的 is_admin 项
    - token_accounts 默认每个 valid token 空集;stored tokens 覆盖其 accounts
    - token_records 由 bootstrapTokenRecords 语义构建,stored tokens 覆盖 name/accounts/is_admin
    """
    options_valid = options.get("valid_tokens")
    options_admin = options.get("admin_tokens") or set()
    options_token_accounts = options.get("token_accounts")
    stored = await store.list_api_tokens()

    if options_valid is None and len(stored) == 0:
        state.valid_tokens = None
        state.token_accounts = None
        state.admin_tokens = set(options_admin)
        state.token_records = {}
        return

    valid = set(options_valid or ())
    valid.update(str(record["token"]) for record in stored if record.get("token") is not None)
    admin = set(options_admin)
    admin.update(str(record["token"]) for record in stored if record.get("is_admin") is True)
    if options_token_accounts is None:
        token_accounts: dict[str, set[str]] = {token: set() for token in valid}
    else:
        token_accounts = {token: set(accounts) for token, accounts in options_token_accounts.items()}
    for record in stored:
        token = str(record["token"])
        accounts = record.get("accounts")
        token_accounts[token] = set(accounts) if isinstance(accounts, list) else set()

    token_records: dict[str, dict] = {}
    for token in sorted(valid):
        token_records[token] = {
            "token": token,
            "name": "admin" if token in admin else "",
            "accounts": sorted(token_accounts.get(token) or ()),
            "is_admin": token in admin,
        }
    for record in stored:
        token = str(record["token"])
        accounts = record.get("accounts")
        token_records[token] = {
            "token": token,
            "name": record.get("name") if isinstance(record.get("name"), str) else "",
            "accounts": (
                [entry for entry in (accounts or []) if isinstance(entry, str)] if isinstance(accounts, list) else []
            ),
            "is_admin": record.get("is_admin") is True,
        }

    state.valid_tokens = valid
    state.token_accounts = token_accounts
    state.admin_tokens = admin
    state.token_records = token_records


def _register_admin_api(app: FastAPI) -> None:
    app.include_router(users_router, prefix="/api", tags=["users"])
    app.include_router(logs_router, prefix="/api", tags=["logs"])

    @app.api_route(
        "/api/{admin_path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
        include_in_schema=False,
    )
    async def admin_dispatch(request: Request) -> Response:
        from backend.api.routes.admin import (
            account_detail,
            account_summaries,
            build_audit_body,
            event_stream_snapshot,
            handle_admin_route,
            overview_cards,
            trading_core_analysis,
        )

        return _translate(
            await handle_admin_route(
                await _request_dict(request),
                {
                    "store": app.state.store,
                    "now_iso": app.state.now_iso,
                    "valid_tokens": app.state.valid_tokens,
                    "token_accounts": app.state.token_accounts,
                    "admin_tokens": app.state.admin_tokens,
                    "token_records": app.state.token_records,
                },
                {
                    "trading_core_analysis": trading_core_analysis,
                    "account_detail": account_detail,
                    "account_summaries": account_summaries,
                    "overview_cards": overview_cards,
                    "build_audit_body": build_audit_body,
                    "event_stream_snapshot": event_stream_snapshot,
                },
            )
        )


# ---------------------------------------------------------------- 内部辅助


def _fire_and_forget_async(coro: Any) -> None:
    """镜像 TS void Promise/void deps.scheduler.enqueueX(...):协程 fire-and-forget。"""
    try:
        task = asyncio.create_task(coro)
        del task
    except RuntimeError:
        pass


def _require_store(options: dict) -> EaStore:
    store = options.get("store")
    if store is None:
        raise ValueError("create_api_app requires a store")
    return store


def _logger_of(state: Any) -> Callable[[str], None] | None:
    log = getattr(state, "log", None)
    if log is None:
        return _default_logger()
    return log


def _string_field_or_empty(record: dict, field: str) -> str:
    value = record.get(field)
    return value if isinstance(value, str) else ""


def _symbol_default(payload: dict) -> str:
    return payload["symbol"] if isinstance(payload.get("symbol"), str) and len(payload["symbol"]) > 0 else "XAUUSD"


async def _validate_ea_payload(path: str, body: dict, store: EaStore) -> str | None:
    from backend.api.routes.ea.index import validate_ea_payload

    return await validate_ea_payload(path, body, store)


def _notify_indicator_alert(state: Any, alert: dict) -> None:
    symbol_value = alert.get("symbol")
    symbol = symbol_value if isinstance(symbol_value, str) else ""
    symbol = symbol if len(symbol) > 0 else "XAUUSD"
    indicator_value = alert.get("indicator")
    indicator = indicator_value if isinstance(indicator_value, str) else ""
    direction_value = alert.get("direction")
    direction = direction_value if isinstance(direction_value, str) else ""
    summary = f"[GOLD-BOT] Alert: {symbol} {direction} {indicator}".strip()
    if len(summary) == 0:
        return
    _fire_notifications(state, summary, summary)


def _fire_notifications(state: Any, title: str, message: str) -> None:
    """镜像 fireNotifications:Discord/Feishu 各自 fire-and-forget。"""
    import asyncio

    async def send() -> None:
        if getattr(state, "discord", None) is not None:
            await state.discord.send({"content": message})
        if getattr(state, "feishu", None) is not None:
            await state.feishu.send({"title": title, "content": message})

    try:
        asyncio.get_running_loop().create_task(send())
    except RuntimeError:
        pass


async def _raw_body(request: Request) -> str:
    body = await request.body()
    return body.decode("utf-8", errors="replace")


async def _request_dict(request: Request) -> dict:
    return {
        "method": request.method,
        "path": request.url.path,
        "headers": {key: value for key, value in request.headers.items()},
        "url": str(request.url),
        "rawBody": await _raw_body(request),
    }


def require_route_token_js(state: Any, request: Request) -> JsonResponse | None:
    """等价 requireRouteToken:返回 JsonResponse 则直接返回该响应。"""
    token = extract_auth_token(
        {key: value for key, value in request.headers.items()},
        str(request.url),
    )
    valid_tokens = state.valid_tokens
    if token is None or valid_tokens is None or token not in valid_tokens:
        return error(401, "invalid token")
    return None


def require_admin_route_js(state: Any, request: Request) -> JsonResponse | None:
    """等价 requireAdminRoute:非 admin token → 403 'admin only'。"""
    guard = require_route_token_js(state, request)
    if guard is not None:
        return guard
    token = extract_auth_token(
        {key: value for key, value in request.headers.items()},
        str(request.url),
    )
    if token is None or token not in state.admin_tokens:
        return error(403, "admin only")
    return None


def _translate(json_response: JsonResponse) -> Response:
    return to_fastapi_response(json_response)


def _now_ms_default() -> int:
    return int(time.time() * 1000)
