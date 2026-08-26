"""EA 生命周期规范化契约(镜像 ea-lifecycle-normalization.spec.ts + EA 端点基础契约)。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.api.app import create_api_app
from backend.persistence.store import EaStore, create_in_memory_store

pytestmark = pytest.mark.contract


def make_app(**options) -> tuple[TestClient, EaStore]:
    store = options.pop("store", None) or create_in_memory_store()
    app = create_api_app({"store": store, **options})
    return TestClient(app), store


async def test_rejects_register_scalar_type_mismatches_like_go_decoder() -> None:
    client, store = make_app()
    for body in [
        {"account_id": "90011087", "broker": 123},
        {"account_id": "90011087", "leverage": "500"},
        {"account_id": "90011087", "leverage": 500.5},
    ]:
        response = client.post("/register", json=body)
        assert response.status_code == 400
        assert response.json() == {"status": "ERROR", "message": "invalid JSON"}
    assert await store.get_registration("90011087") is None


async def test_stores_go_heartbeat_runtime_defaults_and_rejects_boolean_mismatches() -> None:
    client, store = make_app(
        now_unix=lambda: 1772342400,
        now_iso=lambda: "2026-03-01T00:00:00.000Z",
    )
    accepted = client.post(
        "/heartbeat",
        json={
            "account_id": "90011087",
            "balance": 1000.5,
            "equity": 1100.25,
            "max_spread": 25,
            "max_daily_loss": 5.0,
            "server_time": "2026.03.01 08:00:00",
        },
    )
    rejected = client.post("/heartbeat", json={"account_id": "90022000", "market_open": "true"})

    assert accepted.status_code == 200
    assert accepted.json() == {"status": "OK", "server_time": 1772342400}
    heartbeat = await store.get_heartbeat("90011087")
    assert heartbeat is not None
    assert {
        "connected": heartbeat["connected"],
        "market_open": heartbeat["market_open"],
        "is_trade_allowed": heartbeat["is_trade_allowed"],
        "balance": heartbeat["balance"],
        "equity": heartbeat["equity"],
        "max_spread": heartbeat["max_spread"],
        "max_daily_loss": heartbeat["max_daily_loss"],
        "mt4_server_time": heartbeat["mt4_server_time"],
        "last_heartbeat_at": heartbeat["last_heartbeat_at"],
        "updated_at": heartbeat["updated_at"],
    } == {
        "connected": True,
        "market_open": False,
        "is_trade_allowed": False,
        "balance": 1000.5,
        "equity": 1100.25,
        "max_spread": 25,
        "max_daily_loss": 5.0,
        "mt4_server_time": "2026.03.01 08:00:00",
        "last_heartbeat_at": "2026-03-01T00:00:00.000Z",
        "updated_at": "2026-03-01T00:00:00.000Z",
    }
    assert rejected.status_code == 400
    assert rejected.json() == {"status": "ERROR", "message": "invalid JSON"}
    assert await store.get_heartbeat("90022000") is None


async def test_writes_default_tick_symbol_and_rejects_numeric_mismatches() -> None:
    client, store = make_app()
    accepted = client.post(
        "/tick",
        json={
            "account_id": "90011087",
            "bid": 3335.55,
            "ask": 3335.75,
            "spread": 21,
            "max_spread": 25,
        },
    )
    rejected = client.post("/tick", json={"account_id": "90022000", "bid": "3335.55"})

    assert accepted.status_code == 200
    assert accepted.json() == {"status": "OK"}
    tick = await store.get_latest_tick("90011087", "XAUUSD")
    assert tick is not None
    assert {
        "symbol": tick["symbol"],
        "bid": tick["bid"],
        "ask": tick["ask"],
        "spread": tick["spread"],
        "max_spread": tick["max_spread"],
    } == {"symbol": "XAUUSD", "bid": 3335.55, "ask": 3335.75, "spread": 21, "max_spread": 25}
    assert rejected.status_code == 400
    assert rejected.json() == {"status": "ERROR", "message": "invalid JSON"}
    assert await store.get_latest_tick("90022000", "XAUUSD") is None


async def test_ea_endpoints_require_valid_token() -> None:
    client, _store = make_app(valid_tokens={"route-token"}, token_accounts={}, admin_tokens=set())
    for path in ["/register", "/heartbeat", "/tick", "/bars", "/positions", "/poll", "/order_result"]:
        response = client.post(path, json={"account_id": "90011087"})
        assert response.status_code == 401, path
        assert response.json() == {"status": "ERROR", "message": "invalid token"}, path


async def test_ea_endpoint_rejects_missing_account_id_and_invalid_json() -> None:
    client, _store = make_app()
    missing = client.post("/register", json={})
    assert missing.status_code == 400
    assert missing.json() == {"status": "ERROR", "message": "missing account_id"}
    invalid = client.post("/register", data="not-json{", headers={"Content-Type": "text/plain"})
    assert invalid.status_code == 400
    assert invalid.json() == {"status": "ERROR", "message": "invalid JSON"}


async def test_unknown_post_path_returns_not_found() -> None:
    client, _store = make_app()
    response = client.post("/unknown_route", json={"account_id": "x"})
    assert response.status_code == 404
    assert response.json() == {"status": "ERROR", "message": "not found"}


async def test_route_token_binds_first_account_and_rejects_others() -> None:
    token_accounts: dict[str, set[str]] = {}
    client, _store = make_app(
        valid_tokens={"route-token"},
        token_accounts=token_accounts,
        admin_tokens=set(),
    )
    first = client.post("/register", json={"account_id": "90011087"}, headers={"X-API-Token": "route-token"})
    assert first.status_code == 200
    second = client.post("/register", json={"account_id": "90022000"}, headers={"X-API-Token": "route-token"})
    assert second.status_code == 403
    assert second.json() == {"status": "ERROR", "message": "token not authorized for account"}


async def test_order_result_fires_callback_like_ts_void() -> None:
    calls: list[tuple] = []

    def record(
        account_id: str, command_id: str, result: str, ticket: int | None, error_text: str, created_at: str
    ) -> None:
        calls.append((account_id, command_id, result, ticket, error_text, created_at))

    client, store = make_app(on_order_result=record, now_iso=lambda: "2026-03-01T00:00:00.000Z")
    response = client.post(
        "/order_result",
        json={"account_id": "90011087", "command_id": "cmd-1", "result": "filled", "ticket": 91001},
    )
    assert response.status_code == 200
    assert response.json() == {"status": "OK"}
    assert calls == [("90011087", "cmd-1", "filled", 91001, "", "2026-03-01T00:00:00.000Z")]
    # 回调存在时不入库(镜像 TS:onOrderResult == null 才 saveOrderResult)
    assert await store.get_order_results("90011087") == []


async def test_order_result_saves_to_store_when_no_callback() -> None:
    client, store = make_app(on_order_result=None)
    response = client.post(
        "/order_result",
        json={"account_id": "90011087", "command_id": "cmd-1", "result": "filled", "ticket": 91001},
    )
    assert response.status_code == 200
    assert response.json() == {"status": "OK"}
    assert (await store.get_order_results("90011087"))[0]["result"] == "filled"


async def test_order_result_validation() -> None:
    client, _store = make_app(on_order_result=None)
    missing = client.post("/order_result", json={"account_id": "90011087", "result": "filled"})
    assert missing.status_code == 400
    assert missing.json() == {"status": "ERROR", "message": "missing command_id"}
    no_result = client.post("/order_result", json={"account_id": "90011087", "command_id": "cmd-2"})
    assert no_result.status_code == 400
    assert no_result.json() == {"status": "ERROR", "message": "missing result"}


async def test_bars_and_positions_persist_with_defaults() -> None:
    client, store = make_app()
    bars = client.post(
        "/bars",
        json={
            "account_id": "90011087",
            "symbol": "",
            "timeframe": "H1",
            "bars": [{"time": 1735689600, "open": 1.0, "high": 1.1, "low": 0.9, "close": 1.05, "volume": 100}],
        },
    )
    assert bars.status_code == 200
    assert bars.json() == {"status": "OK", "received": 1}
    stored_bars = await store.get_bars("90011087", "XAUUSD", "H1")
    assert len(stored_bars) == 1
    assert stored_bars[0]["time"] == "1735689600"

    positions = client.post(
        "/positions",
        json={
            "account_id": "90011087",
            "symbol": "XAUUSD",
            "positions": [
                {
                    "ticket": 91001,
                    "lots": 0.1,
                    "open_price": 3335.5,
                    "open_time": 1735689600,
                    "type": "BUY",
                    "comment": "GB_breakout_retest_45029911",
                }
            ],
        },
    )
    assert positions.status_code == 200
    assert positions.json() == {"status": "OK", "count": 1}
    stored = await store.get_positions("90011087", "XAUUSD")
    assert stored[0]["strategy"] == "breakout_retest"
    assert stored[0]["order_class"] == "market"


async def test_routes_m15_to_llm_and_ignores_m30_once_per_bar() -> None:
    llm_calls: list[tuple[str, str, str, str]] = []
    technical_calls: list[tuple[str, str, str, str]] = []
    client, _store = make_app(
        llm_analysis_trigger=lambda *args: llm_calls.append(args),
        technical_analysis_trigger=lambda *args: technical_calls.append(args),
    )

    def upload(timeframe: str, bar_time: int) -> None:
        response = client.post(
            "/bars",
            json={
                "account_id": "90011087",
                "symbol": "XAUUSD",
                "timeframe": timeframe,
                "bars": [{"time": bar_time, "open": 1.0, "high": 1.1, "low": 0.9, "close": 1.05}],
            },
        )
        assert response.status_code == 200

    upload("M30", 1735689600)
    upload("m30", 1735689600)
    upload("M15", 1735689600)
    upload("M15", 1735689600)
    upload("H1", 1735689600)
    upload("M30", 1735691400)
    upload("M15", 1735690500)

    assert llm_calls == [
        ("90011087", "XAUUSD", "M15", "1735689600"),
        ("90011087", "XAUUSD", "M15", "1735690500"),
    ]
    assert technical_calls == []


async def test_normalizes_m15_bar_before_triggering_llm_analysis() -> None:
    llm_calls: list[tuple[str, str, str, str]] = []
    client, store = make_app(llm_analysis_trigger=lambda *args: llm_calls.append(args))

    response = client.post(
        "/bars",
        json={
            "account_id": "90011087",
            "symbol": " xauusd ",
            "timeframe": " m15 ",
            "bars": [{"time": 1735689600, "open": 1.0, "high": 1.1, "low": 0.9, "close": 1.05}],
        },
    )

    assert response.status_code == 200
    assert llm_calls == [("90011087", "XAUUSD", "M15", "1735689600")]
    assert (await store.get_bars("90011087", "XAUUSD", "M15"))[0]["close"] == 1.05


async def test_does_not_dispatch_bar_close_analysis_without_a_bar_time() -> None:
    calls: list[tuple[str, str, str, str]] = []
    client, _store = make_app(
        llm_analysis_trigger=lambda *args: calls.append(args),
        technical_analysis_trigger=lambda *args: calls.append(args),
    )

    response = client.post(
        "/bars",
        json={"account_id": "90011087", "symbol": "XAUUSD", "timeframe": "M30", "bars": [{"close": 1.0}]},
    )

    assert response.status_code == 200
    assert calls == []


async def test_poll_returns_queued_commands() -> None:
    client, store = make_app()
    await store.enqueue_command("90011087", {"command_id": "cmd-a", "action": "close"})

    response = client.post("/poll", json={"account_id": "90011087"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "OK"
    assert body["count"] == 1
    assert body["commands"][0]["action"] == "close"


async def test_api_prefixed_ea_endpoints_are_supported() -> None:
    """/api 前缀兼容(旧栈 EA/网关指向 /api/tick 等):行为与根路径一致并正常落库。"""
    client, store = make_app(
        now_unix=lambda: 1772342400,
        now_iso=lambda: "2026-03-01T00:00:00.000Z",
    )
    accepted = client.post(
        "/api/tick",
        json={"account_id": "90011087", "bid": 3335.55, "ask": 3335.75, "spread": 21, "max_spread": 25},
    )
    assert accepted.status_code == 200
    assert accepted.json() == {"status": "OK"}
    tick = await store.get_latest_tick("90011087", "XAUUSD")
    assert tick is not None and tick["bid"] == 3335.55

    for path, status in {
        "/register": 200,
        "/heartbeat": 200,
        "/bars": 200,
        "/positions": 200,
        "/poll": 200,
        "/order_result": 400,  # 缺 command_id/result
    }.items():
        response = client.post("/api" + path, json={"account_id": "90011087"})
        assert response.status_code == status, path

    # 非 POST 方法同样支持(GET /api/poll 镜像 GET /poll)
    poll = client.request("GET", "/api/poll", json={"account_id": "90011087"})
    assert poll.status_code == 200
    assert poll.json() == {"status": "OK", "commands": [], "count": 0}


async def test_api_prefixed_ea_endpoints_require_valid_token() -> None:
    client, _store = make_app(valid_tokens={"route-token"}, token_accounts={}, admin_tokens=set())
    for path in ["/register", "/heartbeat", "/tick", "/bars", "/positions", "/poll", "/order_result"]:
        response = client.post("/api" + path, json={"account_id": "90011087"})
        assert response.status_code == 401, path
        assert response.json() == {"status": "ERROR", "message": "invalid token"}, path
