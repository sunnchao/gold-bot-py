"""Admin 路由(1:1 镜像 gold-bot apps/app-server/src/routes/admin.ts + app.ts 的 admin helpers)。

routeRequest 落到 routeAdmin 的所有 /api 路径都由此处理:
- /api/tokens 管理(GET/POST/DELETE,prefix 截断删除,lexicographic 排序)
- /api/arbitration/:id / expire(人工仲裁与过期清理)
- /api/trigger_ai(弃用端点)
- /api/symbols / api/ai_symbols / api/pending_signal(账户绑定读)
- /api/v1/accounts / overview / audit / events/stream(admin 只读)
- /api/v1/analysis/:account/:symbol/trading-core(route token + API account)

数字语义与 TS/Go 一致:parseDecisionLimit / parsePathInteger 用
`^[0-9]+$` / `^[+-]?[0-9]+$` + Number.isSafeInteger(拒绝 '1.0'、'1.5');
token 由 randomBytes(24).toString('base64url') 生成(32 字符无 padding)。
"""

from __future__ import annotations

import base64
import math
import os
import re
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from backend.api.http.json import parse_json_object
from backend.api.http.response import JsonResponse, error
from backend.api.middleware.auth import authorize_api_account, extract_auth_token
from backend.observability.sse import event_stream_headers
from backend.persistence.store import EaStore

__all__ = [
    "account_detail",
    "account_summaries",
    "build_audit_body",
    "event_stream_snapshot",
    "handle_admin_route",
    "overview_cards",
    "trading_core_analysis",
]

HeaderMap = dict[str, str]

# parseDecisionLimit:空串 → undefined(省略 limit);非法 → null(400)。
_SAFE_INTEGER_MAX = (2**53) - 1  # JS Number.MAX_SAFE_INTEGER


# ------------------------------------------------ 基础字段助手(admin.ts / app.ts)


def _string_field(record: dict, field: str) -> str:
    value = record.get(field)
    return value if isinstance(value, str) else ""


def _number_field(record: dict, field: str) -> float:
    value = record.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return float(value)


def _boolean_field(record: dict, field: str) -> bool:
    return record.get(field) is True


def _string_array_field(record: dict, field: str) -> list[str]:
    value = record.get(field)
    if not isinstance(value, list):
        return []
    return [entry for entry in value if isinstance(entry, str)]


def _is_finite(value: float) -> bool:
    return math.isfinite(value)


def _is_safe_integer(value: float) -> bool:
    # JS Number.isSafeInteger:整数且 |n| <= 2^53 - 1
    return _is_finite(value) and value.is_integer() and abs(value) <= _SAFE_INTEGER_MAX


def _decode_path_segment(value: str) -> str:
    try:
        return unquote(value)
    except (ValueError, UnicodeError):
        return value


# ------------------------------------------------ token 纯函数(admin.ts)


def list_token_records(records: dict[str, dict]) -> dict:
    """等价 listTokenRecords:只列出非 admin 记录,key 为掩码后的 token。"""
    out: dict[str, dict] = {}
    for record in records.values():
        if record.get("is_admin") is not True:
            out[mask_token(str(record["token"]))] = {
                "accounts": list(record.get("accounts") or []),
                "name": record.get("name", "") or "",
                "full_token": record["token"],
            }
    return out


def find_token_by_prefix(records: dict[str, dict], prefix: str) -> str | None:
    """等价 findTokenByPrefix:精确匹配或前缀匹配,取 lexicographically 最小者。"""
    matches = [token for token in records if token == prefix or token.startswith(prefix)]
    if len(matches) == 0:
        return None
    return sorted(matches)[0]


def mask_token(token: str) -> str:
    return token if len(token) <= 8 else f"{token[:4]}...{token[-4:]}"


# ------------------------------------------------ 数字解析(admin.ts)


def parse_decision_limit(raw: str) -> tuple[bool, int | None]:
    """等价 parseDecisionLimit。

    返回 (unset, limit):unset=True 表示 undefined(省略 limit 参数);
    limit=None 表示非法(调用方返回 400)。
    """
    if len(raw) == 0:
        return True, None
    if re.fullmatch(r"[0-9]+", raw) is None:
        return False, None
    value = float(raw)
    if not _is_safe_integer(value) or value < 1:
        return False, None
    return False, int(value)


def parse_path_integer(raw: str) -> int | None:
    """等价 parsePathInteger:`^[+-]?[0-9]+$` + Number.isSafeInteger。"""
    if re.fullmatch(r"[+-]?[0-9]+", raw) is None:
        return None
    value = float(raw)
    if not _is_safe_integer(value):
        return None
    return int(value)


# ------------------------------------------------ 鉴权流程(镜像 requireAdminRoute 等)


def _require_route_token(
    valid_tokens: set[str] | None, headers: HeaderMap, url: str
) -> tuple[str | None, JsonResponse | None]:
    token = extract_auth_token(headers, url)
    if token is None or valid_tokens is None or token not in valid_tokens:
        return None, error(401, "invalid token")
    return token, None


def _require_admin_route(
    valid_tokens: set[str] | None,
    admin_tokens: set[str],
    headers: HeaderMap,
    url: str,
) -> tuple[str | None, JsonResponse | None]:
    token, response = _require_route_token(valid_tokens, headers, url)
    if response is not None:
        return None, response
    if token is None or token not in admin_tokens:
        return None, error(403, "admin only")
    return token, None


# ------------------------------------------------ admin helpers(app.ts)


def account_connected(heartbeat: dict) -> bool:
    if isinstance(heartbeat.get("connected"), bool):
        return heartbeat["connected"]
    return len(heartbeat) > 0


async def trading_core_analysis(store: EaStore, account_id: str, symbol: str, timestamp: str) -> dict:
    """镜像 app.ts tradingCoreAnalysis:runReplay + summarizePositions,附 status/generated_at。"""
    from backend.trading_core.positionmgr import summarize_positions
    from backend.trading_core.replay import run_replay

    latest_tick = (await store.get_latest_tick(account_id, symbol)) or {}
    positions = _filter_positions_for_symbol(symbol, await store.get_positions(account_id, symbol))
    replay_bars = {
        "H1": await store.get_bars(account_id, symbol, "H1"),
        "H4": await store.get_bars(account_id, symbol, "H4"),
        "M30": await store.get_bars(account_id, symbol, "M30"),
        "M15": await store.get_bars(account_id, symbol, "M15"),
        "M5": await store.get_bars(account_id, symbol, "M5"),
        "M1": await store.get_bars(account_id, symbol, "M1"),
        "D1": await store.get_bars(account_id, symbol, "D1"),
    }
    return {
        "status": "OK",
        "generated_at": timestamp,
        "replay": run_replay(
            {
                "account_id": account_id,
                "symbol": symbol,
                "analysis_time": timestamp,
                "current_price": _current_price_for_replay(_current_price_from_tick(latest_tick), replay_bars["H1"]),
                "bars": replay_bars,
                "positions": positions,
            }
        ),
        "position_summary": summarize_positions(
            {
                "accountId": account_id,
                "symbol": symbol,
                "positions": [_to_position_manager_position(position) for position in positions],
            }
        ),
    }


async def account_summaries(store: EaStore) -> list[dict]:
    """镜像 app.ts accountSummaries:每个账户一条连接状态摘要。"""
    account_ids = await store.list_account_ids()
    summaries: list[dict] = []
    for account_id in account_ids:
        registration = (await store.get_registration(account_id)) or {}
        heartbeat = (await store.get_heartbeat(account_id)) or {}
        positions = await store.get_positions(account_id)
        summaries.append(
            {
                "account_id": account_id,
                "balance": _number_field(heartbeat, "balance"),
                "broker": _string_field(registration, "broker"),
                "connected": account_connected(heartbeat),
                "equity": _number_field(heartbeat, "equity"),
                "is_trade_allowed": _boolean_field(heartbeat, "is_trade_allowed"),
                "market_open": _boolean_field(heartbeat, "market_open"),
                "positions": len(positions),
                "server_name": _string_field(registration, "server_name"),
            }
        )
    return summaries


async def account_detail(store: EaStore, account_id: str, timestamp: str) -> dict:
    """镜像 app.ts accountDetail:XAUUSD analysis payload + 最新 AI 结果 + decision_events。"""
    from backend.api.routes.ai.index import analysis_payload

    payload = await analysis_payload(store, account_id, "XAUUSD", timestamp)
    ai_results = await store.get_ai_results(account_id)
    default_symbol_ai_result = next(
        (record for record in ai_results if _string_field(record, "symbol").upper() == "XAUUSD"),
        None,
    )
    latest_ai_result = (
        {} if default_symbol_ai_result is None else _strip_node_ai_result_envelope(default_symbol_ai_result)
    )
    return {
        "status": "OK",
        "account": payload["account"],
        "market": payload["market"],
        "positions": payload["positions"],
        "indicators": payload["indicators"],
        "ai_result": latest_ai_result,
        "decision_events": await store.list_decision_events({"account_id": account_id, "limit": 10}),
    }


def _strip_node_ai_result_envelope(record: dict) -> dict:
    out = {**record}
    out.pop("account_id", None)
    out.pop("symbol", None)
    return out


def overview_cards(accounts: list[dict]) -> list[dict]:
    """镜像 app.ts overviewCards:固定 4 张卡片。"""
    connected = len([account for account in accounts if account.get("connected") is True])
    tradeable = len(
        [
            account
            for account in accounts
            if account.get("market_open") is True and account.get("is_trade_allowed") is True
        ]
    )
    return [
        {
            "detail": "SQLite + Go API online",
            "title": "System Health",
            "tone": "green",
            "value": "Healthy",
        },
        {
            "detail": "active terminals reporting",
            "title": "Connected Accounts",
            "tone": "amber",
            "value": str(connected),
        },
        {
            "detail": "market open and trading allowed",
            "title": "Tradeable Accounts",
            "tone": "blue",
            "value": str(tradeable),
        },
        {
            "detail": "Replay validated, shadow diff pending",
            "title": "Cutover Health",
            "tone": "orange",
            "value": "Baseline Only",
        },
    ]


def audit_checks(report: Any) -> list[dict]:
    """镜像 app.ts auditChecks:基于 shadow report 的三项旧式检查(summary 用)。"""
    last_shadow_event_at = _string_field(report, "last_shadow_event_at")
    if len(last_shadow_event_at) == 0:
        return [
            {
                "detail": "Replay fixture has not been approved yet",
                "label": "Replay Parity",
                "tone": "orange",
                "value": "pending",
            },
            {
                "detail": "Waiting for mirrored production traffic",
                "label": "Shadow Drift",
                "tone": "orange",
                "value": "pending",
            },
            {
                "detail": "Live shadow traffic has not started yet",
                "label": "Protocol Errors",
                "tone": "amber",
                "value": "0.00%",
            },
        ]
    return [
        {
            "detail": (
                "Replay fixture matched baseline or drift is within threshold"
                if report.get("signal_drift_rate", 0) <= 0.02
                else "Replay fixture drift is above threshold"
            ),
            "label": "Replay Parity",
            "tone": "green" if report.get("signal_drift_rate", 0) <= 0.02 else "orange",
            "value": "validated" if report.get("signal_drift_rate", 0) <= 0.02 else "pending",
        },
        {
            "detail": f"Last shadow event at {last_shadow_event_at}",
            "label": "Shadow Drift",
            "tone": "blue",
            "value": "active",
        },
        {
            "detail": (
                "No contract mismatches observed in replay or shadow mode"
                if report.get("protocol_error_rate", 0) == 0
                else "Protocol mismatches detected in shadow mode"
            ),
            "label": "Protocol Errors",
            "tone": "green" if report.get("protocol_error_rate", 0) == 0 else "amber",
            "value": f"{report.get('protocol_error_rate', 0) * 100:.2f}%",
        },
    ]


async def build_audit_body(store: EaStore, timestamp: str) -> dict:
    """镜像 app.ts buildAuditBody:无 shadow 流量时输出占位 report,否则输出完整 cutover 报告。"""
    from backend.observability.shadow_report import build_shadow_report

    comparisons = await store.list_shadow_comparisons()
    report = build_shadow_report(comparisons)
    summary = audit_checks(report) if isinstance(report, dict) else []
    if len(comparisons) == 0:
        return {
            "status": "OK",
            "generated_at": timestamp,
            "summary": summary,
            "report": {
                "ready": False,
                "protocol_error_rate": 0,
                "signal_drift_rate": 0,
                "command_drift_rate": 0,
                "last_shadow_event_at": "0001-01-01T00:00:00Z",
                "missing_capabilities": ["shadow_traffic"],
                "checks": summary,
            },
            "events": [],
        }
    return {
        "status": "OK",
        "generated_at": timestamp,
        "summary": summary,
        "report": report,
        "events": [],
    }


def event_stream_snapshot(_store: EaStore, _timestamp: str) -> str:
    """镜像 app.ts eventStreamSnapshot:当前恒为空串(SSE 由 events hub 实时推送)。"""
    return ""


# ------------------------------------------------ 小助手(analysis/service.ts 同款,app.ts 内联副本)


def _current_price_from_tick(tick: dict) -> float:
    ask = tick.get("ask")
    bid = tick.get("bid")
    ask_value = ask if isinstance(ask, (int, float)) and not isinstance(ask, bool) else None
    bid_value = bid if isinstance(bid, (int, float)) and not isinstance(bid, bool) else None
    if ask_value is not None:
        return float(ask_value)
    if bid_value is not None:
        return float(bid_value)
    return 0.0


def _current_price_for_replay(current_price: float, h1_bars: list[dict]) -> float:
    if current_price != 0:
        return current_price
    latest_h1_close = h1_bars[-1].get("close") if len(h1_bars) > 0 else None
    if isinstance(latest_h1_close, (int, float)) and not isinstance(latest_h1_close, bool):
        return float(latest_h1_close)
    return current_price


def _filter_positions_for_symbol(symbol: str, positions: list[dict]) -> list[dict]:
    from backend.api.routes.ea.index import base_symbol

    base = base_symbol(symbol)
    return [
        position
        for position in positions
        if _string_field(position, "symbol") == "" or base_symbol(_string_field(position, "symbol")) == base
    ]


def _to_position_manager_position(position: dict) -> dict:
    def _num(field: str) -> float | None:
        value = position.get(field)
        return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None

    def _text(field: str) -> str:
        value = position.get(field)
        return value if isinstance(value, str) else ""

    return {
        "ticket": _num("ticket"),
        "symbol": _text("symbol"),
        "type": _text("type"),
        "lots": _num("lots"),
        "openPrice": _num("openPrice"),
        "open_price": _num("open_price"),
        "profit": _num("profit"),
        "comment": _text("comment"),
        "strategy": _text("strategy"),
        "magic": _num("magic"),
    }


# ------------------------------------------------ 主分发(admin.ts handleAdminRoute)


async def handle_admin_route(request: dict, deps: dict, helpers: dict) -> JsonResponse:
    parts = [part for part in request["path"].split("/") if len(part) > 0]

    is_account_bound_read = (
        (len(parts) == 3 and parts[0] == "api" and parts[1] == "symbols")
        or (len(parts) == 3 and parts[0] == "api" and parts[1] == "ai_symbols")
        or (len(parts) == 4 and parts[0] == "api" and parts[1] == "pending_signal")
    )
    is_admin_read = (
        (len(parts) >= 3 and parts[0] == "api" and parts[1] == "v1" and parts[2] == "accounts")
        or (len(parts) >= 3 and parts[0] == "api" and parts[1] == "v1" and parts[2] == "overview")
        or (len(parts) >= 3 and parts[0] == "api" and parts[1] == "v1" and parts[2] == "audit")
        or (
            len(parts) >= 4
            and parts[0] == "api"
            and parts[1] == "v1"
            and parts[2] == "events"
            and parts[3] == "stream"
        )
    )
    is_go_method_agnostic_admin_read = (
        (len(parts) == 3 and parts[0] == "api" and parts[1] == "v1" and parts[2] == "accounts")
        or (len(parts) == 4 and parts[0] == "api" and parts[1] == "v1" and parts[2] == "accounts")
        or (len(parts) == 3 and parts[0] == "api" and parts[1] == "v1" and parts[2] == "overview")
        or (len(parts) == 3 and parts[0] == "api" and parts[1] == "v1" and parts[2] == "audit")
    )

    store: EaStore = deps["store"]
    valid_tokens: set[str] | None = deps["valid_tokens"]
    token_accounts: dict[str, set[str]] | None = deps["token_accounts"]
    admin_tokens: set[str] = deps["admin_tokens"]
    token_records: dict[str, dict] = deps["token_records"]

    def require_route_token() -> tuple[str | None, JsonResponse | None]:
        return _require_route_token(valid_tokens, request["headers"], request["url"])

    def require_admin_route() -> tuple[str | None, JsonResponse | None]:
        return _require_admin_route(valid_tokens, admin_tokens, request["headers"], request["url"])

    # --- /api/trigger_ai(弃用;route token 即可) ---
    if len(parts) == 2 and parts[0] == "api" and parts[1] == "trigger_ai":
        token, response = require_route_token()
        if response is not None:
            return response
        return {
            "statusCode": 200,
            "body": {
                "status": "OK",
                "message": "AI analysis is now handled by Gateway Cron tasks. This endpoint is deprecated.",
                "deprecated": True,
            },
        }

    # --- /api/v1/analysis/:account/:symbol/trading-core(route token + API account) ---
    if (
        len(parts) == 6
        and parts[0] == "api"
        and parts[1] == "v1"
        and parts[2] == "analysis"
        and parts[5] == "trading-core"
    ):
        token, response = require_route_token()
        if response is not None:
            return response
        account_id = _decode_path_segment(parts[3])
        symbol = _decode_path_segment(parts[4])
        if not authorize_api_account(token_accounts, token, account_id, admin_tokens):
            return error(403, "forbidden")
        return {
            "statusCode": 200,
            "body": await helpers["trading_core_analysis"](store, account_id, symbol, deps["now_iso"]()),
        }

    # --- /api/v1/accounts/:account/decisions(admin;GET only + Allow header) ---
    if (
        len(parts) == 5
        and parts[0] == "api"
        and parts[1] == "v1"
        and parts[2] == "accounts"
        and parts[4] == "decisions"
    ):
        token, response = require_admin_route()
        if response is not None:
            return response
        if request["method"] != "GET":
            return {**error(405, "method not allowed"), "headers": {"Allow": "GET"}}
        query_params = parse_qs(urlsplit(request["url"]).query)
        raw_limit = (query_params.get("limit") or [""])[0].strip()
        unset, limit = parse_decision_limit(raw_limit)
        if not unset and limit is None:
            return error(400, "limit must be a positive integer")
        decision_filter: dict[str, Any] = {
            "account_id": parts[3],
            "symbol": ((query_params.get("symbol") or [""])[0]).strip(),
            "status": ((query_params.get("status") or [""])[0]).strip(),
        }
        if not unset:
            decision_filter["limit"] = limit
        return {
            "statusCode": 200,
            "body": {
                "status": "OK",
                "account_id": parts[3],
                "decision_events": await store.list_decision_events(decision_filter),
            },
        }

    # --- /api/arbitration/expire(admin;POST only) ---
    if len(parts) == 3 and parts[0] == "api" and parts[1] == "arbitration" and parts[2] == "expire":
        token, response = require_admin_route()
        if response is not None:
            return response
        if request["method"] != "POST":
            return error(405, "method not allowed")
        return {
            "statusCode": 200,
            "body": {
                "status": "OK",
                "expired": await store.expire_pending_signals(deps["now_iso"]()),
            },
        }

    # --- /api/arbitration/:signal_id(admin;POST only) ---
    if len(parts) == 3 and parts[0] == "api" and parts[1] == "arbitration":
        token, response = require_admin_route()
        if response is not None:
            return response
        if request["method"] != "POST":
            return error(405, "method not allowed")
        signal_id = parse_path_integer(parts[2])
        if signal_id is None:
            return error(400, "invalid signal_id")
        parsed_ok, parsed_body = parse_json_object(request["rawBody"])
        if not parsed_ok:
            return error(400, "invalid JSON")
        result = _string_field(parsed_body, "result")
        reason = _string_field(parsed_body, "reason")
        if result != "approved" and result != "rejected":
            return error(400, "result must be 'approved' or 'rejected'")
        if not (await store.update_pending_signal_arbitration(signal_id, result, reason)):
            return error(500, f"update arbitration for signal {signal_id}: not found")
        return {
            "statusCode": 200,
            "body": {
                "status": "OK",
                "signal_id": signal_id,
                "result": result,
            },
        }

    # --- /api/tokens(admin;GET/POST) ---
    if len(parts) == 2 and parts[0] == "api" and parts[1] == "tokens":
        token, response = require_admin_route()
        if response is not None:
            return response
        if request["method"] == "GET":
            return {
                "statusCode": 200,
                "body": {
                    "status": "OK",
                    "tokens": list_token_records(token_records),
                },
            }
        if request["method"] == "POST":
            parsed_ok, parsed_body = parse_json_object(request["rawBody"])
            if not parsed_ok:
                return error(400, "invalid JSON")
            token_value = base64.urlsafe_b64encode(os.urandom(24)).rstrip(b"=").decode("ascii")
            name = _string_field(parsed_body, "name")
            accounts = _string_array_field(parsed_body, "accounts")
            if valid_tokens is not None:
                valid_tokens.add(token_value)
            if token_accounts is not None:
                token_accounts[token_value] = set(accounts)
            token_records[token_value] = {
                "token": token_value,
                "name": name,
                "accounts": accounts,
                "is_admin": False,
            }
            await store.save_api_token(
                {"token": token_value, "name": name, "accounts": accounts, "is_admin": False}
            )
            return {
                "statusCode": 200,
                "body": {
                    "status": "OK",
                    "token": token_value,
                    "name": name,
                    "accounts": accounts,
                },
            }
        return error(405, "method not allowed")

    # --- /api/tokens/:prefix(admin;DELETE only;prefix 截断删除) ---
    if len(parts) == 3 and parts[0] == "api" and parts[1] == "tokens":
        token, response = require_admin_route()
        if response is not None:
            return response
        if request["method"] != "DELETE":
            return error(405, "method not allowed")
        revoked_token = find_token_by_prefix(token_records, parts[2])
        if revoked_token is None:
            return error(404, "token not found")
        del token_records[revoked_token]
        if valid_tokens is not None:
            valid_tokens.discard(revoked_token)
        if token_accounts is not None:
            token_accounts.pop(revoked_token, None)
        admin_tokens.discard(revoked_token)
        await store.delete_api_token(revoked_token)
        return {
            "statusCode": 200,
            "body": {
                "status": "OK",
                "revoked": mask_token(revoked_token),
            },
        }

    # --- 非 GET 且非 Go 方法无关 admin 读 → 405(在鉴权之前判定,与 TS 一致) ---
    if request["method"] != "GET" and not is_go_method_agnostic_admin_read:
        return error(405, "method not allowed")

    if is_account_bound_read:
        token, response = require_route_token()
        if response is not None:
            return response
        account_id = _decode_path_segment(parts[2])
        if not authorize_api_account(token_accounts, token, account_id, admin_tokens):
            return error(403, "forbidden")

    if is_admin_read:
        token, response = require_admin_route()
        if response is not None:
            return response

    # --- /api/symbols/:account(route token + API account) ---
    if len(parts) == 3 and parts[0] == "api" and parts[1] == "symbols":
        return {
            "statusCode": 200,
            "body": await store.list_symbols(_decode_path_segment(parts[2])),
        }
    # --- /api/ai_symbols/:account ---
    if len(parts) == 3 and parts[0] == "api" and parts[1] == "ai_symbols":
        return {
            "statusCode": 200,
            "body": await store.list_ai_symbols(_decode_path_segment(parts[2])),
        }
    # --- /api/pending_signal/:account/:symbol ---
    if len(parts) == 4 and parts[0] == "api" and parts[1] == "pending_signal":
        account_id = _decode_path_segment(parts[2])
        symbol = _decode_path_segment(parts[3])
        return {
            "statusCode": 200,
            "body": await store.get_pending_signals(account_id, symbol),
        }
    # --- /api/v1/accounts(admin 只读) ---
    if len(parts) == 3 and parts[0] == "api" and parts[1] == "v1" and parts[2] == "accounts":
        return {
            "statusCode": 200,
            "body": {
                "status": "OK",
                "accounts": await helpers["account_summaries"](store),
            },
        }
    # --- /api/v1/accounts/:account(admin 只读;缺失 → 500) ---
    if len(parts) == 4 and parts[0] == "api" and parts[1] == "v1" and parts[2] == "accounts":
        if not (await has_account_snapshot(store, parts[3])):
            return error(500, f"account {parts[3]} not found")
        return {
            "statusCode": 200,
            "body": await helpers["account_detail"](store, parts[3], deps["now_iso"]()),
        }
    # --- /api/v1/overview(admin 只读) ---
    if len(parts) == 3 and parts[0] == "api" and parts[1] == "v1" and parts[2] == "overview":
        accounts = await helpers["account_summaries"](store)
        return {
            "statusCode": 200,
            "body": {
                "status": "OK",
                "generated_at": deps["now_iso"](),
                "cards": helpers["overview_cards"](accounts),
                "accounts": accounts,
            },
        }
    # --- /api/v1/audit(admin 只读) ---
    if len(parts) == 3 and parts[0] == "api" and parts[1] == "v1" and parts[2] == "audit":
        return {
            "statusCode": 200,
            "body": await helpers["build_audit_body"](store, deps["now_iso"]()),
        }
    # --- /api/v1/events/stream(admin 只读;快照占位,实时 SSE 由 /api/v1/events/stream GET 单独处理) ---
    if len(parts) == 4 and parts[0] == "api" and parts[1] == "v1" and parts[2] == "events" and parts[3] == "stream":
        return {
            "statusCode": 200,
            "headers": event_stream_headers(),
            "body": None,
            "rawBody": helpers["event_stream_snapshot"](store, deps["now_iso"]()),
        }

    return error(404, "not found")


async def has_account_snapshot(store: EaStore, account_id: str) -> bool:
    return account_id in (await store.list_account_ids())
