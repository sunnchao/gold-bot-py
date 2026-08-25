"""指标预警契约(镜像 apps/app-server/src/routes/indicator-alert.spec.ts)。"""

from __future__ import annotations

import copy
import json

import pytest

from backend.api.http.response import JsonResponse
from backend.api.routes.indicator_alert import (
    create_indicator_alert_cache,
    handle_indicator_alert_route,
)

pytestmark = pytest.mark.contract

ROUTE_TOKEN = "test-token"


def make_deps() -> tuple[dict, dict]:
    alerts = create_indicator_alert_cache(lambda: 1772342400000)
    return {"valid_tokens": {ROUTE_TOKEN}, "alerts": alerts}, alerts


def store_request(alert: dict) -> dict:
    return {
        "method": "POST",
        "path": "/indicator_alert/store",
        "headers": {"X-API-Token": ROUTE_TOKEN},
        "url": "/indicator_alert/store",
        "rawBody": json.dumps(alert),
    }


def poll_request() -> dict:
    return {
        "method": "POST",
        "path": "/indicator_alert/poll",
        "headers": {"X-API-Token": ROUTE_TOKEN},
        "url": "/indicator_alert/poll",
        "rawBody": json.dumps({"account_id": "ignored-by-go"}),
    }


def raw_request(path: str, raw_body: str) -> dict:
    return {
        "method": "POST",
        "path": path,
        "headers": {"X-API-Token": ROUTE_TOKEN},
        "url": path,
        "rawBody": raw_body,
    }


def test_keeps_original_alert_payload_when_duplicate_suppressed_within_ttl() -> None:
    deps, alerts = make_deps()
    original = {
        "id": "alert_1",
        "type": "divergence",
        "indicator": "RSI",
        "direction": "bullish",
        "symbol": "XAUUSD",
        "timeframe": "H1",
        "time": "2026-04-13T08:00:00.000Z",
        "price": 3335.75,
        "strength": "strong",
        "confidence": 0.82,
        "description": "RSI bullish divergence A",
        "rsi_divergence": "bullish",
    }
    suppressed_duplicate = {
        **original,
        "id": "alert_2",
        "time": "2026-04-13T08:05:00.000Z",
        "price": 3340.25,
        "confidence": 0.44,
        "description": "RSI bullish divergence B",
    }

    first = handle_indicator_alert_route(store_request(original), deps)
    second = handle_indicator_alert_route(store_request(suppressed_duplicate), deps)
    recent = alerts["recent"]()
    poll = handle_indicator_alert_route(poll_request(), deps)

    assert first["body"] == {"status": "ok", "should_send": True}
    assert second["body"] == {"status": "ok", "should_send": False}
    assert recent == [original]
    assert poll["body"] == {"status": "ok", "count": 1, "alerts": [original]}


def test_rejects_go_decoder_incompatible_payloads_without_caching() -> None:
    deps, alerts = make_deps()
    invalid_store = handle_indicator_alert_route(raw_request("/indicator_alert/store", "[]"), deps)
    invalid_poll = handle_indicator_alert_route(
        raw_request("/indicator_alert/poll", json.dumps({"account_id": 123})), deps
    )
    poll = handle_indicator_alert_route(poll_request(), deps)

    assert invalid_store["statusCode"] == 400
    assert invalid_store["body"] == {"status": "ERROR", "message": "invalid json"}
    assert invalid_poll["statusCode"] == 400
    assert invalid_poll["body"] == {"status": "ERROR", "message": "invalid json"}
    assert poll["body"] == {"status": "ok", "count": 0, "alerts": []}


def test_ttl_refresh_allows_resend_after_four_hours() -> None:
    now = {"ms": 1772342400000}
    alerts = create_indicator_alert_cache(lambda: now["ms"])
    deps = {"valid_tokens": {ROUTE_TOKEN}, "alerts": alerts}
    alert = {"symbol": "XAUUSD", "indicator": "RSI", "direction": "bullish", "price": 3335.75}

    first = handle_indicator_alert_route(store_request(alert), deps)
    assert first["body"] == {"status": "ok", "should_send": True}

    now["ms"] += 4 * 60 * 60 * 1000 - 1
    again = handle_indicator_alert_route(store_request(alert), deps)
    assert again["body"] == {"status": "ok", "should_send": False}

    now["ms"] += 1
    after_ttl = handle_indicator_alert_route(store_request(alert), deps)
    assert after_ttl["body"] == {"status": "ok", "should_send": True}
    assert len(alerts["recent"]()) == 1


def test_recent_excludes_expired_alerts_after_ttl() -> None:
    now = {"ms": 1772342400000}
    alerts = create_indicator_alert_cache(lambda: now["ms"])
    deps = {"valid_tokens": {ROUTE_TOKEN}, "alerts": alerts}
    handle_indicator_alert_route(store_request({"symbol": "XAUUSD", "indicator": "RSI", "direction": "bullish"}), deps)
    assert len(alerts["recent"]()) == 1

    # 恰好 TTL 边界:lastSentAtMs > now - TTL 仍保留(严格大于)
    now["ms"] += 4 * 60 * 60 * 1000
    assert len(alerts["recent"]()) == 0


def test_sanitize_drops_non_decodable_fields_and_deep_copies() -> None:
    alerts = create_indicator_alert_cache(lambda: 1772342400000)
    deps = {"valid_tokens": {ROUTE_TOKEN}, "alerts": alerts}
    alert = {
        "symbol": "XAUUSD",
        "indicator": "MACD",
        "direction": "bearish",
        "price": 3340.0,
        "extra_field": "dropped",
        "nested": {"a": 1},
    }
    response = handle_indicator_alert_route(store_request(alert), deps)
    assert response["body"] == {"status": "ok", "should_send": True}

    recent = alerts["recent"]()
    assert recent == [{"symbol": "XAUUSD", "indicator": "MACD", "direction": "bearish", "price": 3340.0}]
    # structuredClone 语义:返回副本,修改不影响缓存
    recent[0]["price"] = 1.0
    assert alerts["recent"]()[0]["price"] == 3340.0
    assert copy.deepcopy(alert)


def test_handler_requires_token_and_post_method() -> None:
    alerts = create_indicator_alert_cache(lambda: 1772342400000)
    no_token = handle_indicator_alert_route(
        {
            "method": "POST",
            "path": "/indicator_alert/store",
            "headers": {},
            "url": "/indicator_alert/store",
            "rawBody": "{}",
        },
        {"valid_tokens": {ROUTE_TOKEN}, "alerts": alerts},
    )
    assert no_token["statusCode"] == 401
    assert no_token["body"] == {"status": "ERROR", "message": "invalid token"}

    wrong_method = handle_indicator_alert_route(
        {
            "method": "GET",
            "path": "/indicator_alert/poll",
            "headers": {"X-API-Token": ROUTE_TOKEN},
            "url": "/indicator_alert/poll",
            "rawBody": "{}",
        },
        {"valid_tokens": {ROUTE_TOKEN}, "alerts": alerts},
    )
    assert wrong_method["statusCode"] == 405
    assert wrong_method["body"] == {"status": "ERROR", "message": "method not allowed"}

    unknown = handle_indicator_alert_route(
        {
            "method": "POST",
            "path": "/indicator_alert/other",
            "headers": {"X-API-Token": ROUTE_TOKEN},
            "url": "/indicator_alert/other",
            "rawBody": "{}",
        },
        {"valid_tokens": {ROUTE_TOKEN}, "alerts": alerts},
    )
    assert unknown["statusCode"] == 404
    assert unknown["body"] == {"status": "ERROR", "message": "not found"}


def test_poll_ignores_account_id_and_works_without_it() -> None:
    alerts = create_indicator_alert_cache(lambda: 1772342400000)
    deps = {"valid_tokens": {ROUTE_TOKEN}, "alerts": alerts}
    handle_indicator_alert_route(
        store_request({"symbol": "XAUUSD", "indicator": "MACD", "direction": "bullish"}), deps
    )
    without_account = handle_indicator_alert_route(
        {
            "method": "POST",
            "path": "/indicator_alert/poll",
            "headers": {"X-API-Token": ROUTE_TOKEN},
            "url": "/indicator_alert/poll",
            "rawBody": "{}",
        },
        deps,
    )
    assert without_account["statusCode"] == 200
    assert without_account["body"]["count"] == 1
    assert isinstance(without_account["body"], dict)
    response: JsonResponse = without_account
    assert response["body"]["status"] == "ok"
