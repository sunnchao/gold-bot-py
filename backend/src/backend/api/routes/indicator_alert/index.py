"""指标预警路由(镜像 apps/app-server/src/routes/indicator-alert.ts)。

缓存语义:key = symbol_indicator_direction;TTL 4 小时;期限内重复 → should_send=false 且计数+1;
过期后再次出现 → 重新发送(count 累计)。recent() 只返回 lastSentAtMs > now-TTL 的条目。
structuredClone 语义 → copy.deepcopy。
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

from backend.api.http.json import parse_strict_json_object
from backend.api.http.response import JsonResponse, error
from backend.api.middleware.auth import extract_auth_token

__all__ = ["ALERT_TTL_MS", "IndicatorAlert", "create_indicator_alert_cache", "handle_indicator_alert_route"]

ALERT_TTL_MS = 4 * 60 * 60 * 1000

IndicatorAlert = dict[str, Any]

GO_STRING_FIELDS = [
    "id", "type", "indicator", "direction", "symbol", "timeframe", "time",
    "strength", "description", "macd_divergence", "rsi_divergence",
]
GO_NUMBER_FIELDS = ["price", "confidence"]
GO_ALERT_FIELDS = [*GO_STRING_FIELDS, *GO_NUMBER_FIELDS]

CachedAlert = dict[str, Any]  # {alert, lastSentAtMs, count}


def create_indicator_alert_cache(now_ms: Callable[[], int]) -> dict[str, Any]:
    """返回 {add(alert), recent()} 的字典形态缓存(镜像 IndicatorAlertCache)。"""
    alerts: dict[str, CachedAlert] = {}

    def add(alert: IndicatorAlert) -> bool:
        key = alert_key(alert)
        now = now_ms()
        existing = alerts.get(key)
        if existing is None or now - existing["lastSentAtMs"] >= ALERT_TTL_MS:
            alerts[key] = {
                "alert": copy.deepcopy(alert),
                "lastSentAtMs": now,
                "count": (existing["count"] if existing is not None else 0) + 1,
            }
            return True
        existing["count"] += 1
        return False

    def recent() -> list[IndicatorAlert]:
        cutoff = now_ms() - ALERT_TTL_MS
        return [
            copy.deepcopy(entry["alert"])
            for entry in alerts.values()
            if entry["lastSentAtMs"] > cutoff
        ]

    return {"alerts": alerts, "add": add, "recent": recent}


def handle_indicator_alert_route(request: dict, deps: dict) -> JsonResponse:
    token = extract_auth_token(request["headers"], request["url"])
    if token is None or deps["valid_tokens"] is None or token not in deps["valid_tokens"]:
        return error(401, "invalid token")
    if request["method"] != "POST":
        return error(405, "method not allowed")

    parsed_ok, parsed_body = parse_strict_json_object(request["rawBody"])
    if not parsed_ok:
        return error(400, "invalid json")

    if request["path"] == "/indicator_alert/store":
        if not _is_go_decodable_indicator_alert(parsed_body):
            return error(400, "invalid json")
        alert = sanitize_indicator_alert(parsed_body)
        return {
            "statusCode": 200,
            "body": {"status": "ok", "should_send": deps["alerts"]["add"](alert)},
        }
    if request["path"] == "/indicator_alert/poll":
        account_id = parsed_body.get("account_id")
        if account_id is not None and not isinstance(account_id, str):
            return error(400, "invalid json")
        alerts = deps["alerts"]["recent"]()
        return {"statusCode": 200, "body": {"status": "ok", "count": len(alerts), "alerts": alerts}}
    return error(404, "not found")


def _is_go_decodable_indicator_alert(record: dict) -> bool:
    for field in GO_STRING_FIELDS:
        value = record.get(field)
        if value is not None and not isinstance(value, str):
            return False
    for field in GO_NUMBER_FIELDS:
        value = record.get(field)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
    return True


def sanitize_indicator_alert(record: dict) -> IndicatorAlert:
    alert: IndicatorAlert = {}
    for field in GO_ALERT_FIELDS:
        value = record.get(field)
        if value is not None:
            alert[field] = value
    return alert


def alert_key(alert: IndicatorAlert) -> str:
    return f"{string_field(alert, 'symbol')}_{string_field(alert, 'indicator')}_{string_field(alert, 'direction')}"


def string_field(record: dict, field: str) -> str:
    value = record.get(field)
    return value if isinstance(value, str) else ""
