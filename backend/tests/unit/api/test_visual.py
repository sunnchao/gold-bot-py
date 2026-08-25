"""EA 控制台轮询契约(镜像 apps/app-server/src/routes/visual.ts 语义)。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.api.app import create_api_app
from backend.api.routes.visual import visual_ai, visual_tick
from backend.persistence.store import create_in_memory_store

pytestmark = pytest.mark.contract

ROUTE_TOKEN = "route-token"
ACCOUNT_ID = "90011087"
SYMBOL = "XAUUSD"


def make_app(**options) -> TestClient:
    store = options.pop("store", None) or create_in_memory_store()
    defaults = {
        "store": store,
        "now_iso": lambda: "2026-03-01T00:00:00.000Z",
    }
    if "valid_tokens" not in options:
        defaults["valid_tokens"] = {ROUTE_TOKEN}
    if "admin_tokens" not in options:
        defaults["admin_tokens"] = {ROUTE_TOKEN}
    return TestClient(create_api_app({**defaults, **options}))


def poll_body(**overrides) -> dict:
    body = {"account_id": ACCOUNT_ID, "symbol": SYMBOL, "timeframe": "H1"}
    body.update(overrides)
    return body


def headers(token: str = ROUTE_TOKEN) -> dict:
    return {"X-API-Token": token}


async def test_poll_requires_valid_token() -> None:
    client = make_app(valid_tokens={ROUTE_TOKEN}, admin_tokens=set())
    response = client.post("/visual/poll", json=poll_body())
    assert response.status_code == 401
    assert response.json() == {"status": "ERROR", "message": "invalid token"}
    bad = client.post("/visual/poll", json=poll_body(), headers={"X-API-Token": "wrong"})
    assert bad.status_code == 401


async def test_poll_requires_account_id_and_symbol() -> None:
    client = make_app(valid_tokens={ROUTE_TOKEN}, admin_tokens=set())
    response = client.post("/visual/poll", json=poll_body(account_id="", symbol=""), headers=headers())
    assert response.status_code == 400
    assert response.json() == {"status": "ERROR", "message": "account_id and symbol are required"}


async def test_poll_rejects_account_not_bound_to_api_token() -> None:
    client = make_app(
        valid_tokens={ROUTE_TOKEN},
        admin_tokens=set(),
        token_accounts={},
    )
    response = client.post("/visual/poll", json=poll_body(), headers=headers())
    assert response.status_code == 403
    assert response.json() == {"status": "ERROR", "message": "forbidden"}


async def test_poll_method_not_allowed() -> None:
    client = make_app(valid_tokens={ROUTE_TOKEN}, admin_tokens=set())
    response = client.get("/visual/poll", headers=headers())
    assert response.status_code == 405
    assert response.json() == {"status": "ERROR", "message": "method not allowed"}


async def test_poll_accepts_api_prefixed_alias() -> None:
    client = make_app()
    response = client.post("/api/visual/poll", json=poll_body(), headers=headers())
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["account_id"] == ACCOUNT_ID
    assert body["symbol"] == SYMBOL


async def test_poll_returns_default_tick_and_empty_ai_when_nothing_stored() -> None:
    client = make_app()
    response = client.post("/visual/poll", json=poll_body(), headers=headers())
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["account_id"] == ACCOUNT_ID
    assert body["symbol"] == SYMBOL
    assert body["timeframe"] == "H1"
    assert body["server_time"] == "2026-03-01T00:00:00.000Z"
    assert body["tick"] == {"symbol": SYMBOL, "bid": 0, "ask": 0, "spread": 0, "time": ""}
    assert body["ai"] == {
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
    assert body["alerts"] == []
    assert body["count"] == 0


async def test_poll_returns_latest_tick_values() -> None:
    client = make_app()
    client.post(
        "/tick",
        json={"account_id": ACCOUNT_ID, "symbol": "", "bid": 3335.55, "ask": 3335.75, "spread": 21},
        headers=headers(),
    )
    response = client.post("/visual/poll", json=poll_body(), headers=headers())
    assert response.status_code == 200
    tick = response.json()["tick"]
    assert tick["bid"] == 3335.55
    assert tick["ask"] == 3335.75
    assert tick["spread"] == 21


async def test_poll_ai_summary_falls_back_to_trade_plan() -> None:
    client, store = await make_app_with_store()
    await store.save_ai_result(
        ACCOUNT_ID,
        SYMBOL,
        {
            "symbol": "xauusd",
            "bias": "bullish",
            "confidence": 0.85,
            "trade_plan": {
                "mode": "reactive",
                "side": "buy",
                "decision_id": "d-42",
                "entry_zone": {"min": 3330.0, "max": 3338.0},
                "stop_loss": 3320.0,
                "take_profit": [3350.0, 3360.0],
                "narrative": "plan narrative",
            },
        },
    )
    response = client.post("/visual/poll", json=poll_body(), headers=headers())
    assert response.status_code == 200
    ai = response.json()["ai"]
    assert ai["has_result"] is True
    assert ai["bias"] == "bullish"
    assert ai["confidence"] == 0.85
    assert ai["decision_id"] == "d-42"
    assert ai["trade_plan_mode"] == "reactive"
    assert ai["side"] == "buy"
    assert ai["entry_min"] == 3330.0
    assert ai["entry_max"] == 3338.0
    assert ai["stop_loss"] == 3320.0
    # take_profit 数组 → 第一个正数
    assert ai["take_profit"] == 3350.0
    assert ai["narrative"] == "plan narrative"


async def make_app_with_store() -> tuple[TestClient, object]:
    store = create_in_memory_store()
    return make_app(store=store), store


async def test_poll_filters_alerts_by_symbol_and_timeframe() -> None:
    from backend.api.routes.indicator_alert import create_indicator_alert_cache

    alerts = create_indicator_alert_cache(lambda: 1772342400000)
    client = make_app(alerts=alerts)
    # alerts 添加不同 symbol/timeframe 的预警
    alerts["add"]({"symbol": "XAUUSD", "indicator": "RSI", "direction": "bullish", "timeframe": "H1"})
    alerts["add"]({"symbol": "EURUSD", "indicator": "MACD", "direction": "bearish", "timeframe": "H4"})
    alerts["add"]({"symbol": "", "indicator": "ADX", "direction": "neutral", "timeframe": ""})

    response = client.post("/visual/poll", json=poll_body(), headers=headers())
    body = response.json()
    assert body["count"] == 2
    assert [a["symbol"] for a in body["alerts"]] == ["XAUUSD", ""]


async def test_visual_tick_and_ai_pure_functions() -> None:
    assert visual_tick(None, "XAUUSD") == {"symbol": "XAUUSD", "bid": 0, "ask": 0, "spread": 0, "time": ""}
    assert visual_tick({"symbol": "GOLD", "bid": 1.5, "ask": 1.6, "spread": 10, "time": "t"}, "XAUUSD") == {
        "symbol": "GOLD",
        "bid": 1.5,
        "ask": 1.6,
        "spread": 10,
        "time": "t",
    }
    ai = visual_ai([], "XAUUSD")
    assert ai["has_result"] is False
    assert ai["bias"] == ""
    assert ai["confidence"] == 0
