"""AI 路由契约(镜像 routes/ai.spec.ts + ai-result-method.spec.ts)。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.api.app import create_api_app
from backend.persistence.store import EaStore, create_in_memory_store

pytestmark = pytest.mark.contract

ACCOUNT_ID = "90011087"
TOKEN = "fixture-user-token"


def make_app(**options) -> TestClient:
    store = options.pop("store", None) or create_in_memory_store()
    defaults = {
        "store": store,
        "valid_tokens": {TOKEN},
        "token_accounts": {TOKEN: {ACCOUNT_ID}},
        "admin_tokens": set(),
        "now_iso": lambda: "2026-04-13T08:00:00.000Z",
    }
    return TestClient(create_api_app({**defaults, **options}))


def headers() -> dict:
    return {"X-API-Token": TOKEN}


# ---------------------------------------------------------------- analysis_payload 路由


async def test_allows_post_legacy_analysis_payload_like_go_handler() -> None:
    client = make_app()
    response = client.post(f"/api/analysis_payload/{ACCOUNT_ID}", json={}, headers=headers())
    assert response.status_code == 200
    body = response.json()
    assert body["account"]["account_id"] == ACCOUNT_ID
    assert body["market"]["symbol"] == "XAUUSD"
    assert body["status"] == "OK"


async def test_allows_put_v2_analysis_payload_like_go_handler() -> None:
    client = make_app()
    response = client.put(f"/api/v2/analysis_payload/{ACCOUNT_ID}/XAUUSD", json={}, headers=headers())
    assert response.status_code == 200
    body = response.json()
    assert body["account"]["account_id"] == ACCOUNT_ID
    assert body["market"]["symbol"] == "XAUUSD"
    assert body["status"] == "OK"


async def test_analysis_payload_requires_token_and_api_account() -> None:
    client = make_app()
    no_token = client.post(f"/api/analysis_payload/{ACCOUNT_ID}", json={})
    assert no_token.status_code == 401
    assert no_token.json() == {"status": "ERROR", "message": "invalid token"}

    other = client.post("/api/analysis_payload/99999999", json={}, headers=headers())
    assert other.status_code == 403
    assert other.json() == {"status": "ERROR", "message": "forbidden"}


async def test_unknown_ai_path_returns_404() -> None:
    client = make_app()
    response = client.post("/api/analysis_payload/90011087/extra", json={}, headers=headers())
    assert response.status_code == 404
    assert response.json() == {"status": "ERROR", "message": "not found"}


# ---------------------------------------------------------------- analysis_payload 结构


def ea_headers() -> dict:
    return {"X-API-Token": TOKEN, "Content-Type": "application/json"}


async def inject_heartbeat(client: TestClient, extra: dict | None = None) -> None:
    payload = {
        "account_id": ACCOUNT_ID,
        "balance": 10000,
        "equity": 10500,
        "margin": 500,
        "free_margin": 10000,
        "market_open": True,
        "is_trade_allowed": True,
        "server_time": "2026.04.13 08:00:00",
    }
    if extra:
        payload.update(extra)
    response = client.post("/heartbeat", json=payload, headers=ea_headers())
    assert response.status_code == 200


async def inject_tick(client: TestClient, extra: dict | None = None) -> None:
    payload = {"account_id": ACCOUNT_ID, "symbol": "XAUUSD", "bid": 3335.5, "ask": 3335.7, "spread": 21}
    if extra:
        payload.update(extra)
    response = client.post("/tick", json=payload, headers=ea_headers())
    assert response.status_code == 200


async def test_analysis_payload_assembles_full_snapshot() -> None:
    client, store = await make_app_with_store()
    await store.save_registration(
        {"account_id": ACCOUNT_ID, "broker": "ICMarkets", "currency": "USD", "leverage": 500}
    )
    await inject_heartbeat(client)
    await inject_tick(client)
    await store.save_bars(
        {
            "account_id": ACCOUNT_ID,
            "symbol": "XAUUSD",
            "timeframe": "H1",
            "bars": [
                {
                    "time": i,
                    "open": 3300.0 + i,
                    "high": 3301.0 + i,
                    "low": 3299.0 + i,
                    "close": 3300.5 + i,
                    "volume": 100,
                }
                for i in range(40)
            ],
        }
    )
    response = client.post(f"/api/analysis_payload/{ACCOUNT_ID}", json={}, headers=headers())
    assert response.status_code == 200
    body = response.json()
    assert body["account"]["account_id"] == ACCOUNT_ID
    assert body["account"]["broker"] == "ICMarkets"
    assert body["account"]["connected"] is True
    assert body["account"]["leverage"] == 500
    assert body["market"]["bid"] == 3335.5
    assert body["market"]["ask"] == 3335.7
    assert body["market"]["symbol"] == "XAUUSD"
    assert body["market_status"]["tradeable"] is True
    assert body["market_status"]["stale"] is False
    assert body["market_status"]["is_trade_allowed"] is True
    assert body["market_status"]["market_open"] is True
    assert set(body["market_filters"].keys()) == {"blocked", "blocking", "warnings", "reason_codes"}
    assert body["market_filters"]["blocked"] is True  # tick 无 time → tick.missing
    assert "tick.missing" in body["market_filters"]["reason_codes"]
    assert body["status"] == "OK"
    assert body["bars"]["H1"][-1]["close"] == 3339.5
    # 富化指标写入
    assert "ema20" in body["bars"]["H1"][-1]
    assert "atr" in body["bars"]["H1"][-1]
    assert "fib_618" in body["bars"]["H1"][-1]
    # indicator packs: <20 根 → None
    assert body["indicators"]["M15"] is None
    assert body["indicators"]["H1"]["bars_count"] == 40
    # trend_context 有默认值
    assert body["trend_context"]["consensus_direction"] in ("BULL", "BEAR", "NEUTRAL")
    # timestamp 转上海时区
    assert body["timestamp"].endswith("+08:00")
    assert body["positions"] == []
    assert body["harmonic_context"]["h4_patterns"] == []
    assert body["harmonic_context"]["active_pattern"] is None
    assert body["harmonic_context"]["direction_bias"] == "neutral"
    assert body["smc_context"]["h4_obs"] == []
    assert "h1_breaks" in body["smc_context"]
    assert body["candlestick_patterns"]["h4"] == []
    assert body["candlestick_patterns"]["m30"] == []
    assert body["candlestick_patterns"]["h1"] == ["three_white_soldiers"]


async def make_app_with_store() -> tuple[TestClient, EaStore]:
    store = create_in_memory_store()
    return make_app(store=store), store


async def test_analysis_payload_market_status_stale_when_tick_old() -> None:
    client, store = await make_app_with_store()
    await store.save_heartbeat(
        {
            "account_id": ACCOUNT_ID,
            "market_open": True,
            "is_trade_allowed": True,
            "last_heartbeat_at": "2026-04-13T07:45:00.000Z",
            "server_time": "2026.04.13 08:00:00",
        }
    )
    # tick 的 time-only 时间 + 服务端日期组合(rollover 语义)
    await store.save_tick(
        {
            "account_id": ACCOUNT_ID,
            "symbol": "XAUUSD",
            "bid": 3335.5,
            "ask": 3335.7,
            "time": "08:00:00",
            "received_at": "2026-04-13T07:30:00.000Z",
        }
    )
    response = client.post(f"/api/analysis_payload/{ACCOUNT_ID}", json={}, headers=headers())
    assert response.status_code == 200
    status = response.json()["market_status"]
    # tick 接收时间 7:30,now 8:00,超过 15min TTL → stale
    assert status["stale"] is True
    assert status["stale_reason"] == "tick_stale"
    assert status["tick_age_ms"] == 30 * 60 * 1000


async def test_strategy_mapping_filters_unknown_values() -> None:
    client, store = await make_app_with_store()
    await store.save_registration(
        {
            "account_id": ACCOUNT_ID,
            "strategy_mapping": {
                "20250231": "pullback",
                "20250232": "not-a-strategy",
                "99999999": "divergence",
            },
        }
    )
    response = client.post(f"/api/analysis_payload/{ACCOUNT_ID}", json={}, headers=headers())
    assert response.status_code == 200
    mapping = response.json()["strategy_mapping"]
    # TS: defaults 与注册合并,再按 allowed keys + 合法策略名过滤
    assert mapping == {
        "20250231": "pullback",
        "20250233": "divergence",
        "20250234": "breakout_pyramid",
        "20250235": "counter_pullback",
        "20250236": "range",
        "20250238": "ai_signal",
    }


# ---------------------------------------------------------------- ai_result 路由


async def test_accepts_non_post_ai_result_like_go_handler() -> None:
    client, store = await make_app_with_store()
    response = client.put(
        f"/api/ai_result/{ACCOUNT_ID}",
        json={"bias": "bullish", "confidence": 82},
        headers=headers(),
    )
    assert response.status_code == 200
    assert response.json() == {"status": "OK", "received": True}
    results = await store.get_ai_results(ACCOUNT_ID)
    assert len(results) == 1
    assert results[0]["symbol"] == "XAUUSD"
    assert results[0]["bias"] == "bullish"
    assert results[0]["confidence"] == 82


async def test_accepts_non_post_v2_ai_result_like_go_handler() -> None:
    client, store = await make_app_with_store()
    response = client.patch(
        f"/api/v2/ai_result/{ACCOUNT_ID}/GBPJPY",
        json={"bias": "bearish", "confidence": 64},
        headers=headers(),
    )
    assert response.status_code == 200
    assert response.json() == {"status": "OK", "received": True}
    results = await store.get_ai_results(ACCOUNT_ID)
    assert len(results) == 1
    assert results[0]["symbol"] == "GBPJPY"
    assert results[0]["bias"] == "bearish"
    assert results[0]["confidence"] == 64


async def test_ai_result_requires_valid_token() -> None:
    client = make_app()
    response = client.post(f"/api/ai_result/{ACCOUNT_ID}", json={})
    assert response.status_code == 401
    assert response.json() == {"status": "ERROR", "message": "invalid token"}


async def test_ai_result_rejects_invalid_json_strictly() -> None:
    client = make_app()
    response = client.post(
        f"/api/ai_result/{ACCOUNT_ID}",
        data="[]",
        headers={"Content-Type": "application/json", **headers()},
    )
    assert response.status_code == 400
    assert response.json() == {"status": "ERROR", "message": "invalid JSON"}


async def test_ai_result_with_trade_plan_returns_decision_and_risk_gate() -> None:
    client, store = await make_app_with_store()
    await store.save_registration(
        {"account_id": ACCOUNT_ID, "leverage": 500}
    )
    await store.save_heartbeat(
        {
            "account_id": ACCOUNT_ID,
            "equity": 10500,
            "free_margin": 10000,
            "market_open": True,
            "is_trade_allowed": True,
            "server_time": "2026.04.13 08:00:00",
        }
    )
    await store.save_tick(
        {"account_id": ACCOUNT_ID, "symbol": "XAUUSD", "bid": 3335.5, "ask": 3335.7, "spread": 21}
    )
    response = client.post(
        f"/api/ai_result/{ACCOUNT_ID}",
        json={
            "bias": "bullish",
            "suggested_sl": 3320,
            "suggested_tp": 3360,
            "trade_plan": {
                "schema_version": "trade_plan.v1",
                "decision_id": "d-100",
                "account_id": ACCOUNT_ID,
                "symbol": "XAUUSD",
                "mode": "approve",
                "side": "buy",
                "confidence": 82,
                "expires_at": "2026-04-13T10:00:00.000Z",
                "reason_codes": ["trend.bullish"],
                "narrative": "bullish momentum",
                "add_on": False,
                "entry_zone": {"min": 3330.0, "max": 3338.0},
                "stop_loss": 3320.0,
                "take_profit": [3360.0],
                "max_lots": 0.5,
            },
        },
        headers=headers(),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "OK"
    assert body["received"] is True
    assert body["decision"]["decision_id"] == "d-100"
    assert body["decision"]["symbol"] == "XAUUSD"
    assert body["decision"]["mode"] == "approve"
    assert "risk_gate" in body
    assert body["risk_gate"]["mode"] == "approve"
    assert body["trade_plan_validation"] == {"valid": True}
    # 决策时间线落库(镜像 TS:filter 必带 account_id)
    events = await store.list_decision_events({"account_id": ACCOUNT_ID})
    stages = {event["stage"] for event in events}
    assert {"ai_result", "risk_gate"} <= stages


async def test_ai_result_trade_plan_validation_error() -> None:
    client, store = await make_app_with_store()
    response = client.post(
        f"/api/ai_result/{ACCOUNT_ID}",
        json={
            "bias": "bullish",
            "trade_plan": {
                "schema_version": "trade_plan.v1",
                "decision_id": "d-bad",
                "account_id": ACCOUNT_ID,
                "symbol": "XAUUSD",
                "mode": "approve",
                "side": "buy",
                "confidence": 82,
                "expires_at": "2026-04-13T10:00:00.000Z",
                "reason_codes": ["x"],
                "narrative": "n",
                "entry_zone": {"min": 0, "max": 0},
                "stop_loss": 0,
                "take_profit": [],
                "max_lots": 0,
            },
        },
        headers=headers(),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "OK"
    assert body["received"] is True
    assert body["trade_plan_validation"]["valid"] is False
    # 无效 trade_plan → 不写决策时间线
    events = await store.list_decision_events({"account_id": ACCOUNT_ID})
    assert events == []


async def test_ai_result_triggers_sse_events() -> None:
    client, store = await make_app_with_store()
    published: list[dict] = []
    hub = client.app.state.events
    hub.subscribe(published.append)

    response = client.post(
        f"/api/ai_result/{ACCOUNT_ID}",
        json={"bias": "bullish", "confidence": 82, "suggested_sl": 0, "suggested_tp": 0},
        headers=headers(),
    )
    assert response.status_code == 200
    # suggested_sl/tp 全 0 → ai_analysis_failed + ai_result 事件
    types = [event["event_type"] for event in published]
    assert "ai_analysis_failed" in types
    assert "ai_result" in types
    failed = next(event for event in published if event["event_type"] == "ai_analysis_failed")
    assert failed["source"] == "api.ai_result"
    assert failed["account_id"] == ACCOUNT_ID


async def test_ai_result_risk_alert_queues_close_command() -> None:
    client, store = await make_app_with_store()
    await store.save_positions(
        {
            "account_id": ACCOUNT_ID,
            "symbol": "XAUUSD",
            "positions": [
                {
                    "ticket": 91001,
                    "symbol": "XAUUSD",
                    "type": "BUY",
                    "lots": 0.1,
                    "open_price": 3330.0,
                    "open_time": 1735689600,
                    "comment": "",
                }
            ],
        }
    )
    response = client.post(
        f"/api/ai_result/{ACCOUNT_ID}",
        json={"risk_alert": True, "exit_suggestion": "close_all", "alert_reason": "crash"},
        headers=headers(),
    )
    assert response.status_code == 200
    assert response.json() == {"status": "OK", "received": True}
    commands = await store.list_commands(ACCOUNT_ID)
    assert len(commands) == 1
    assert commands[0]["action"] == "CLOSE_ALL"
    assert commands[0]["source"] == "ai_risk_alert"
    assert commands[0]["reason"] == "AI风险警报(全平): crash"
