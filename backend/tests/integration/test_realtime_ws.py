"""实时通道集成测试:WebSocket 事件流 + EA 实时事件发布。

- /api/v1/ws/events:admin 鉴权(?token=)、hub 事件逐帧转发、鉴权失败关闭码
- EA /register /heartbeat /positions → strategy_update / account_update /
  positions_update 事件发布(与 /api/v1/events/stream SSE 共享同一 hub)
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend.api.app import create_api_app
from backend.persistence.store import EaStore, create_in_memory_store

ACCOUNT_ID = "90011087"
USER_TOKEN = "fixture-user-token"
ADMIN_TOKEN = "fixture-admin-token"


def make_client(**options) -> tuple[TestClient, EaStore]:
    store = options.pop("store", None) or create_in_memory_store()
    defaults = {
        "store": store,
        "valid_tokens": {USER_TOKEN, ADMIN_TOKEN},
        "token_accounts": {USER_TOKEN: {ACCOUNT_ID}},
        "admin_tokens": {ADMIN_TOKEN},
        "now_unix": lambda: 1772342400,
        "now_iso": lambda: "2026-04-13T16:00:00+08:00",
    }
    client = TestClient(create_api_app({**defaults, **options}))
    return client, store


def publish_event(client: TestClient, event: dict[str, Any]) -> None:
    client.app.state.events.publish(event)


# ---------------------------------------------------------------- WS 转发


def test_websocket_forwards_hub_events_to_admin_client() -> None:
    client, _store = make_client()
    event = {
        "event_id": "evt_test_1",
        "event_type": "ai_result",
        "account_id": ACCOUNT_ID,
        "source": "test",
        "timestamp": "2026-04-13T16:00:00+08:00",
        "payload": {"status": "OK"},
    }
    with client.websocket_connect(f"/api/v1/ws/events?token={ADMIN_TOKEN}") as websocket:
        publish_event(client, event)
        assert websocket.receive_json() == event


def test_websocket_pushes_ea_heartbeat_and_positions_events() -> None:
    client, _store = make_client()
    with client.websocket_connect(f"/api/v1/ws/events?token={ADMIN_TOKEN}") as websocket:
        heartbeat = client.post(
            "/heartbeat",
            headers={"X-API-Token": USER_TOKEN},
            json={
                "account_id": ACCOUNT_ID,
                "balance": 1000.5,
                "equity": 1100.25,
                "margin": 10.0,
                "free_margin": 990.25,
                "market_open": True,
                "is_trade_allowed": True,
                "server_time": "2026.04.13 16:00:00",
            },
        )
        assert heartbeat.status_code == 200

        account_event = websocket.receive_json()
        assert account_event["event_type"] == "account_update"
        assert account_event["account_id"] == ACCOUNT_ID
        payload = account_event["payload"]
        assert payload["balance"] == 1000.5
        assert payload["equity"] == 1100.25
        assert payload["connected"] is True
        assert payload["market_open"] is True

        positions = client.post(
            "/positions",
            headers={"X-API-Token": USER_TOKEN},
            json={
                "account_id": ACCOUNT_ID,
                "symbol": "XAUUSD",
                "positions": [
                    {
                        "ticket": 1001,
                        "type": "BUY",
                        "lots": 0.1,
                        "open_price": 4120.5,
                        "profit": 12.3,
                        "sl": 4100.0,
                        "tp": 4150.0,
                        "comment": "GB_pullback_act=open side=buy",
                    }
                ],
            },
        )
        assert positions.status_code == 200

        positions_event = websocket.receive_json()
        assert positions_event["event_type"] == "positions_update"
        assert positions_event["payload"]["count"] == 1
        compact = positions_event["payload"]["positions"][0]
        assert compact["ticket"] == 1001
        assert compact["side"] == "BUY"
        assert compact["lots"] == 0.1
        assert compact["profit"] == 12.3
        # 策略身份来自 comment,不回退 magic
        assert compact["strategy"] != "unknown"


def test_websocket_pushes_strategy_update_on_register() -> None:
    client, _store = make_client()
    with client.websocket_connect(f"/api/v1/ws/events?token={ADMIN_TOKEN}") as websocket:
        response = client.post(
            "/register",
            headers={"X-API-Token": USER_TOKEN},
            json={
                "account_id": ACCOUNT_ID,
                "broker": "Demo Broker",
                "server_name": "Demo-1",
                "strategy_mapping": {"20250231": "pullback", "20250232": "breakout_retest"},
            },
        )
        assert response.status_code == 200

        event = websocket.receive_json()
        assert event["event_type"] == "strategy_update"
        assert event["payload"]["strategy_mapping"] == {
            "20250231": "pullback",
            "20250232": "breakout_retest",
        }


# ---------------------------------------------------------------- WS 鉴权


def test_websocket_rejects_invalid_token_with_4401() -> None:
    client, _store = make_client()
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/api/v1/ws/events?token=unknown-token"):
            pass
    assert exc_info.value.code == 4401


def test_websocket_rejects_non_admin_token_with_4403() -> None:
    client, _store = make_client()
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(f"/api/v1/ws/events?token={USER_TOKEN}"):
            pass
    assert exc_info.value.code == 4403


# ---------------------------------------------------------------- 事件发布(未订阅时)


def test_ea_lifecycle_publishes_events_without_websocket_subscribers() -> None:
    """hub 发布不依赖 WS 订阅者;SSE 与 WS 共享同一 hub。"""
    client, _store = make_client()
    received: list[dict[str, Any]] = []
    unsubscribe = client.app.state.events.subscribe(lambda event: received.append(event))
    try:
        response = client.post(
            "/heartbeat", headers={"X-API-Token": USER_TOKEN}, json={"account_id": ACCOUNT_ID}
        )
        assert response.status_code == 200
        assert len(received) == 1
        assert received[0]["event_type"] == "account_update"
    finally:
        unsubscribe()
