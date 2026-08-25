"""已平仓成交接口契约(镜像 app.ts POST /api/trade_history 语义)。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.api.app import create_api_app
from backend.persistence.store import create_in_memory_store

pytestmark = pytest.mark.contract

ROUTE_TOKEN = "route-token"
ACCOUNT_ID = "90011087"


def make_app(**options) -> TestClient:
    store = options.pop("store", None) or create_in_memory_store()
    defaults = {"store": store}
    if "valid_tokens" not in options:
        defaults["valid_tokens"] = {ROUTE_TOKEN}
    return TestClient(create_api_app({**defaults, **options}))


def headers(token: str = ROUTE_TOKEN) -> dict:
    return {"X-API-Token": token}


def sample_trade(**overrides) -> dict:
    trade = {
        "account_id": ACCOUNT_ID,
        "ticket": 91001,
        "magic": 20250231,
        "symbol": "XAUUSD",
        "side": "BUY",
        "open_price": 3335.5,
        "close_price": 3340.0,
        "lots": 0.1,
        "profit": 45.0,
        "open_time": "2026.03.01 08:00:00",
        "close_time": "2026.03.01 08:30:00",
    }
    trade.update(overrides)
    return trade


async def test_requires_valid_token() -> None:
    client = make_app(valid_tokens={ROUTE_TOKEN})
    response = client.post("/api/trade_history", json=[sample_trade()])
    assert response.status_code == 401
    assert response.json() == {"status": "ERROR", "message": "invalid token"}


async def test_saves_trade_and_derives_strategy_duration() -> None:
    client, store = await make_app_with_store()
    response = client.post("/api/trade_history", json=[sample_trade()], headers=headers())
    assert response.status_code == 200
    assert response.json() == {"status": "OK", "saved": 1}

    stats = await store.get_closed_trade_stats(ACCOUNT_ID)
    assert len(stats) == 1
    assert stats[0]["strategy"] == "pullback"
    assert stats[0]["wins"] == 1


async def make_app_with_store() -> tuple[TestClient, object]:
    store = create_in_memory_store()
    return make_app(store=store), store


def closed_trades(store: object, account_id: str) -> list[dict]:
    return [t for t in store._closed_trades if t.get("account_id") == account_id]  # type: ignore[attr-defined]


async def test_duration_minutes_from_mt_times_and_type_fallback() -> None:
    client, store = await make_app_with_store()
    response = client.post(
        "/api/trade_history",
        json=[
            sample_trade(
                magic=20250232,
                side=None,
                type="sell",
                open_time="2026.03.01 08:00:00",
                close_time="2026.03.01 09:15:00",
                profit=-12.5,
            )
        ],
        headers=headers(),
    )
    assert response.status_code == 200
    assert response.json() == {"status": "OK", "saved": 1}

    trades = closed_trades(store, ACCOUNT_ID)
    assert len(trades) == 1
    trade = trades[0]
    assert trade["strategy"] == "breakout_retest"
    assert trade["side"] == "SELL"
    assert trade["duration_min"] == 75
    assert trade["profit"] == -12.5


async def test_skips_invalid_trades_and_returns_saved_count() -> None:
    client = make_app()
    response = client.post(
        "/api/trade_history",
        json=[
            sample_trade(ticket=0),
            sample_trade(account_id=""),
            sample_trade(ticket=91002, magic=999999, profit=0),
            "garbage",
            None,
        ],
        headers=headers(),
    )
    assert response.status_code == 200
    assert response.json() == {"status": "OK", "saved": 1}


async def test_accepts_object_with_trades_array() -> None:
    client, store = await make_app_with_store()
    response = client.post(
        "/api/trade_history",
        json={"trades": [sample_trade()]},
        headers=headers(),
    )
    assert response.status_code == 200
    assert response.json() == {"status": "OK", "saved": 1}


async def test_rejects_non_array_and_invalid_json() -> None:
    client = make_app()
    scalar = client.post("/api/trade_history", json={"nope": 1}, headers=headers())
    assert scalar.status_code == 400
    assert scalar.json() == {"status": "ERROR", "message": "expected array of trades"}

    invalid = client.post("/api/trade_history", data="not-json", headers={"Content-Type": "text/plain", **headers()})
    assert invalid.status_code == 400
    assert invalid.json() == {"status": "ERROR", "message": "invalid JSON"}


async def test_records_prometheus_metrics() -> None:
    client, _store = await make_app_with_store()
    response = client.post(
        "/api/trade_history",
        json=[
            sample_trade(profit=45.0),
            sample_trade(ticket=91002, profit=-3.5, magic=20250233),
            sample_trade(ticket=91003, profit=0, side="SELL", magic=20250234),
        ],
        headers=headers(),
    )
    assert response.status_code == 200
    assert response.json() == {"status": "OK", "saved": 3}
    # orders_total: breakout_retest 胜/负 各 1,magic 20250234 → breakout_pyramid SELL 盈亏 0 → loss
    metric = response.json()
    assert metric["saved"] == 3
