"""EA 控制台轮询路由(镜像 apps/app-server/src/routes/visual.ts)。"""

from __future__ import annotations

from typing import Any

from backend.api.http.json import parse_json_object
from backend.api.http.response import JsonResponse, error
from backend.api.middleware.auth import (
    authorize_api_account,
    extract_auth_token,
)
from backend.persistence.store import EaStore

__all__ = ["handle_visual_route", "visual_tick", "visual_ai"]


async def handle_visual_route(
    request: dict,
    deps: dict,
) -> JsonResponse:
    if request["path"] != "/visual/poll":
        return error(404, "not found")
    token = extract_auth_token(request["headers"], request["url"])
    if token is None or deps["valid_tokens"] is None or token not in deps["valid_tokens"]:
        return error(401, "invalid token")
    if request["method"] != "POST":
        return error(405, "method not allowed")

    parsed_ok, parsed_body = parse_json_object(request["rawBody"])
    if not parsed_ok:
        return error(400, "invalid json")
    account_id = string_field(parsed_body, "account_id").strip()
    symbol = string_field(parsed_body, "symbol").strip()
    timeframe = string_field(parsed_body, "timeframe").strip()
    if len(account_id) == 0 or len(symbol) == 0:
        return error(400, "account_id and symbol are required")
    if not authorize_api_account(deps["token_accounts"], token, account_id, deps["admin_tokens"]):
        return error(403, "forbidden")

    alerts = deps["alerts"]["recent"]()
    alerts = [alert for alert in alerts if _alert_matches_visual_poll(alert, symbol, timeframe)]
    store: EaStore = deps["store"]
    tick = await store.get_latest_tick(account_id, symbol)
    ai_results = await store.get_ai_results(account_id)
    return {
        "statusCode": 200,
        "body": {
            "status": "ok",
            "account_id": account_id,
            "symbol": symbol,
            "timeframe": timeframe,
            "server_time": deps["now_iso"](),
            "tick": visual_tick(tick, symbol),
            "ai": visual_ai(ai_results, symbol),
            "alerts": alerts,
            "count": len(alerts),
        },
    }


def visual_tick(tick: dict | None, symbol: str) -> dict:
    if tick is None:
        return {"symbol": symbol, "bid": 0, "ask": 0, "spread": 0, "time": ""}
    return {
        "symbol": string_field(tick, "symbol") or symbol,
        "bid": number_field(tick, "bid"),
        "ask": number_field(tick, "ask"),
        "spread": number_field(tick, "spread"),
        "time": string_field(tick, "time"),
    }


def visual_ai(results: list[dict], symbol: str) -> dict:
    result = None
    for entry in reversed(results):
        if string_field(entry, "symbol").lower() == symbol.lower():
            result = entry
            break
    if result is None:
        return {
            "has_result": False,
            "bias": "",
            "confidence": 0,
            "exit_suggestion": "",
            "risk_alert": False,
            "alert_reason": "",
            "decision_id": "",
            "trade_plan_mode": "",
            "side": "",
            "entry_min": 0,
            "entry_max": 0,
            "stop_loss": 0,
            "take_profit": 0,
            "risk_gate_status": "",
            "narrative": "",
        }
    trade_plan = record_field(result, "trade_plan") or {}
    entry_zone = record_field(trade_plan, "entry_zone") or {}
    risk_gate = record_field(result, "risk_gate") or record_field(trade_plan, "risk_gate") or {}
    summary = {
        "has_result": False,
        "bias": string_field(result, "bias"),
        "confidence": number_field(result, "confidence"),
        "exit_suggestion": string_field(result, "exit_suggestion"),
        "risk_alert": result.get("risk_alert") is True,
        "alert_reason": string_field(result, "alert_reason"),
        "decision_id": string_field(result, "decision_id") or string_field(trade_plan, "decision_id"),
        "trade_plan_mode": string_field(result, "trade_plan_mode") or string_field(trade_plan, "mode"),
        "side": string_field(result, "side") or string_field(trade_plan, "side"),
        "entry_min": number_field(entry_zone, "min"),
        "entry_max": number_field(entry_zone, "max"),
        "stop_loss": number_field(result, "stop_loss") or number_field(trade_plan, "stop_loss"),
        "take_profit": number_field(result, "take_profit") or first_positive_number(trade_plan.get("take_profit")),
        "risk_gate_status": string_field(result, "risk_gate_status") or string_field(risk_gate, "status"),
        "narrative": string_field(result, "narrative") or string_field(trade_plan, "narrative"),
    }
    summary["has_result"] = visual_summary_has_result(summary)
    return summary


def visual_summary_has_result(summary: dict) -> bool:
    return (
        len(string_field(summary, "bias")) > 0
        or number_field(summary, "confidence") > 0
        or len(string_field(summary, "exit_suggestion")) > 0
        or summary.get("risk_alert") is True
        or len(string_field(summary, "alert_reason")) > 0
        or len(string_field(summary, "decision_id")) > 0
        or len(string_field(summary, "trade_plan_mode")) > 0
        or len(string_field(summary, "side")) > 0
        or number_field(summary, "entry_min") > 0
        or number_field(summary, "entry_max") > 0
        or number_field(summary, "stop_loss") > 0
        or number_field(summary, "take_profit") > 0
        or len(string_field(summary, "risk_gate_status")) > 0
        or len(string_field(summary, "narrative")) > 0
    )


def _alert_matches_visual_poll(alert: dict, symbol: str, timeframe: str) -> bool:
    alert_symbol = string_field(alert, "symbol").strip()
    alert_timeframe = string_field(alert, "timeframe").strip()
    return (len(alert_symbol) == 0 or alert_symbol.lower() == symbol.lower()) and (
        len(alert_timeframe) == 0 or alert_timeframe.lower() == timeframe.lower()
    )


def record_field(record: dict, field: str) -> dict | None:
    value = record.get(field)
    return value if value is not None and isinstance(value, dict) else None


def number_field(record: dict, field: str) -> float | int:
    value = record.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return value


def first_positive_number(value: Any) -> float | int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        if not isinstance(value, list):
            return 0
        for entry in value:
            if isinstance(entry, bool) or not isinstance(entry, (int, float)):
                continue
            if entry > 0:
                return entry
        return 0
    return value if value > 0 else 0


def string_field(record: dict, field: str) -> str:
    value = record.get(field)
    return value if isinstance(value, str) else ""
