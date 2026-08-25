"""Admin 路由集成测试(1:1 镜像 apps/app-server/src/app.spec.ts 的 admin 相关 it())。

级别:L4 集成 —— 走完整 FastAPI 路由栈(create_api_app + TestClient + store)。
覆盖:
- token 管理(GET/POST/DELETE,prefix 截断删除,lexicographic 排序,sqlite 持久化重载)
- admin gates(401 invalid token / 403 admin only)
- trigger_ai 弃用端点
- symbols / ai_symbols / pending_signal 账户绑定读
- v1 accounts / overview / audit / account detail / decisions
- arbitration 更新 / ParseInt 拒绝 '1.0' / expire
- trading-core 分析(不产生 poll 命令)
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from fastapi.testclient import TestClient

from backend.api.app import create_api_app
from backend.persistence.store import EaStore, create_in_memory_store, create_sqlite_store

ACCOUNT_ID = "90011087"
OTHER_ACCOUNT_ID = "90022000"
USER_TOKEN = "fixture-user-token"
ADMIN_TOKEN = "fixture-admin-token"
SYMBOL = "XAUUSD"

USER_HEADERS = {"X-API-Token": USER_TOKEN}
ADMIN_HEADERS = {"X-API-Token": ADMIN_TOKEN}

REPLAY_FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "replay"
REPLAY_SNAPSHOT = json.loads(
    (REPLAY_FIXTURE_ROOT / "account_90011087" / "input.json").read_text(encoding="utf-8")
)

TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{32}$")


def make_client(**options) -> tuple[TestClient, EaStore]:
    """等价 app.spec.ts createApiServer 默认:user/admin 两个 fixture token。"""
    store = options.pop("store", None) or create_in_memory_store()
    defaults = {
        "store": store,
        "valid_tokens": {USER_TOKEN, ADMIN_TOKEN},
        "token_accounts": {USER_TOKEN: {ACCOUNT_ID}},
        "admin_tokens": {ADMIN_TOKEN},
        "now_iso": lambda: "2026-04-13T08:00:00Z",
    }
    client = TestClient(create_api_app({**defaults, **options}))
    return client, store


def mask_token(token: str) -> str:
    return token if len(token) <= 8 else f"{token[:4]}...{token[-4:]}"


# ---------------------------------------------------------------- token 管理


async def test_manages_api_tokens_behind_admin_auth() -> None:
    client, store = make_client()

    created = client.post(
        "/api/tokens", json={"name": "Desk", "accounts": ["90011087", "90022000"]}, headers=ADMIN_HEADERS
    )
    assert created.status_code == 200
    created_body = created.json()
    assert created_body["status"] == "OK"
    assert TOKEN_PATTERN.match(created_body["token"])
    assert created_body["name"] == "Desk"
    assert created_body["accounts"] == ["90011087", "90022000"]

    listed = client.get("/api/tokens", headers=ADMIN_HEADERS)
    assert listed.status_code == 200
    listed_body = listed.json()
    listed_token = next(
        (entry for entry in listed_body["tokens"].values() if entry["full_token"] == created_body["token"]),
        None,
    )
    assert listed_body["status"] == "OK"
    assert listed_token == {
        "name": "Desk",
        "accounts": ["90011087", "90022000"],
        "full_token": created_body["token"],
    }

    allowed = client.post(
        "/heartbeat", headers={"X-API-Token": created_body["token"]}, json={"account_id": "90022000", "equity": 2000}
    )
    assert allowed.status_code == 200
    heartbeat = await store.get_heartbeat("90022000")
    assert heartbeat is not None and heartbeat.get("equity") == 2000

    deleted = client.delete(f"/api/tokens/{created_body['token'][:8]}", headers=ADMIN_HEADERS)
    assert deleted.status_code == 200
    assert deleted.json() == {
        "status": "OK",
        "revoked": mask_token(created_body["token"]),
    }

    rejected = client.post(
        "/heartbeat", headers={"X-API-Token": created_body["token"]}, json={"account_id": "90022000", "equity": 3000}
    )
    assert rejected.status_code == 401


async def test_deletes_lexicographically_first_api_token_matching_prefix_like_go() -> None:
    client, _store = make_client(
        valid_tokens={ADMIN_TOKEN, "shared-bbb-token", "shared-aaa-token"},
        admin_tokens={ADMIN_TOKEN},
        token_accounts={"shared-bbb-token": {ACCOUNT_ID}, "shared-aaa-token": {ACCOUNT_ID}},
    )

    deleted = client.delete("/api/tokens/shared-", headers=ADMIN_HEADERS)
    listed = client.get("/api/tokens", headers=ADMIN_HEADERS)

    assert deleted.status_code == 200
    assert deleted.json() == {"status": "OK", "revoked": "shar...oken"}
    tokens = listed.json()["tokens"]
    full_tokens = [entry["full_token"] for entry in tokens.values()]
    assert "shared-aaa-token" not in full_tokens
    assert "shared-bbb-token" in full_tokens


async def test_loads_persisted_api_tokens_from_app_store_on_startup(tmp_path: Path) -> None:
    db_path = str(tmp_path / "ea.sqlite")
    first_store = create_sqlite_store(db_path)
    first_client, _ = make_client(store=first_store)
    created = first_client.post(
        "/api/tokens", json={"name": "Desk", "accounts": [ACCOUNT_ID]}, headers=ADMIN_HEADERS
    )
    token = created.json()["token"]
    assert created.status_code == 200
    await first_store.close()

    second_store = create_sqlite_store(db_path)
    second_client, _ = make_client(store=second_store)
    try:
        allowed = second_client.post(
            "/heartbeat", headers={"X-API-Token": token}, json={"account_id": ACCOUNT_ID, "equity": 2100}
        )
        listed = second_client.get("/api/tokens", headers=ADMIN_HEADERS)

        assert allowed.status_code == 200
        heartbeat = await second_store.get_heartbeat(ACCOUNT_ID)
        assert heartbeat is not None and heartbeat.get("equity") == 2100
        full_tokens = [entry["full_token"] for entry in listed.json()["tokens"].values()]
        assert token in full_tokens
    finally:
        await second_store.close()


# ---------------------------------------------------------------- token gates


async def test_rejects_api_routes_when_no_token_store_configured() -> None:
    client = TestClient(create_api_app({"store": create_in_memory_store()}))

    response = client.get("/api/analysis_payload/90011087", headers={"X-API-Token": "unknown-token"})

    assert response.status_code == 401
    assert response.json() == {"status": "ERROR", "message": "invalid token"}


async def test_rejects_unbound_valid_tokens_on_api_account_routes_without_auto_binding() -> None:
    requests = [
        {"method": "GET", "url": "/api/analysis_payload/90011087", "json": None},
        {"method": "POST", "url": "/api/ai_result/90011087", "json": {}},
        {"method": "GET", "url": "/api/pending_signal/90011087/XAUUSD", "json": None},
    ]

    for request in requests:
        client = TestClient(
            create_api_app(
                {
                    "store": create_in_memory_store(),
                    "valid_tokens": {"unbound-token"},
                    "token_accounts": {"unbound-token": set()},
                    "admin_tokens": set(),
                }
            )
        )
        kwargs = {"headers": {"X-API-Token": "unbound-token"}}
        if request["json"] is not None:
            kwargs["json"] = request["json"]
        response = client.request(request["method"], request["url"], **kwargs)

        assert response.status_code == 403
        assert response.json() == {"status": "ERROR", "message": "forbidden"}


async def test_enforces_go_compatible_api_admin_gates() -> None:
    client, _store = make_client()

    missing_token = client.get("/api/v1/overview")
    assert missing_token.status_code == 401
    assert missing_token.json() == {"status": "ERROR", "message": "invalid token"}

    user_token = client.get("/api/v1/overview", headers=USER_HEADERS)
    assert user_token.status_code == 403
    assert user_token.json() == {"status": "ERROR", "message": "admin only"}


async def test_serves_deprecated_trigger_ai_endpoint_behind_token_auth() -> None:
    client, _store = make_client()
    expected = {
        "status": "OK",
        "message": "AI analysis is now handled by Gateway Cron tasks. This endpoint is deprecated.",
        "deprecated": True,
    }

    missing_token = client.get("/api/trigger_ai")
    assert missing_token.status_code == 401
    assert missing_token.json() == {"status": "ERROR", "message": "invalid token"}

    for method in ("GET", "POST"):
        response = client.request(method, "/api/trigger_ai", headers=USER_HEADERS)
        assert response.status_code == 200
        assert response.json() == expected


# ---------------------------------------------------------------- 只读 admin 路由


async def test_serves_read_only_admin_symbol_and_dashboard_routes_from_node_snapshots() -> None:
    client, store = make_client()

    await store.save_registration(
        {
            "account_id": ACCOUNT_ID,
            "broker": "Demo Broker",
            "server_name": "Demo-1",
            "currency": "USD",
            "leverage": 500,
            "ai_symbols": ["XAUUSD", "GBPJPY"],
        }
    )
    await store.save_heartbeat(
        {
            "account_id": ACCOUNT_ID,
            "balance": 1000.5,
            "equity": 1100.25,
            "margin": 100,
            "free_margin": 1000.25,
            "market_open": True,
            "is_trade_allowed": True,
            "ai_symbols": ["XAUUSD", "GBPJPY"],
        }
    )
    await store.save_tick({"account_id": ACCOUNT_ID, "symbol": "GBPJPY", "bid": 191.25, "ask": 191.28})
    await store.save_positions(
        {"account_id": ACCOUNT_ID, "positions": [{"ticket": 123456, "symbol": "XAUUSD", "type": "BUY"}]}
    )
    await store.save_pending_signal(
        {
            "account_id": ACCOUNT_ID,
            "symbol": "XAUUSD",
            "side": "buy",
            "score": 9,
            "strategy": "pullback",
            "status": "pending",
            "created_at": "2026-04-13T08:00:00Z",
            "expires_at": "2026-04-13T08:00:30Z",
            "arbitration_result": "",
            "arbitration_reason": "",
            "indicators": '{"adx":31,"rsi":58}',
        }
    )

    expected_summary = {
        "account_id": ACCOUNT_ID,
        "balance": 1000.5,
        "broker": "Demo Broker",
        "connected": True,
        "equity": 1100.25,
        "is_trade_allowed": True,
        "market_open": True,
        "positions": 1,
        "server_name": "Demo-1",
    }

    symbols = client.get("/api/symbols/90011087", headers=USER_HEADERS)
    assert symbols.status_code == 200
    assert sorted(symbols.json()) == ["GBPJPY", "XAUUSD"]

    ai_symbols = client.get("/api/ai_symbols/90011087", headers=USER_HEADERS)
    assert ai_symbols.status_code == 200
    assert ai_symbols.json() == ["XAUUSD", "GBPJPY"]

    pending = client.get("/api/pending_signal/90011087/XAUUSD", headers=USER_HEADERS)
    assert pending.status_code == 200
    signals = pending.json()
    assert len(signals) == 1
    signal = signals[0]
    assert signal["id"] == 1
    assert signal["account_id"] == ACCOUNT_ID
    assert signal["symbol"] == "XAUUSD"
    assert signal["side"] == "buy"
    assert signal["score"] == 9
    assert signal["strategy"] == "pullback"
    assert signal["status"] == "pending"
    assert signal["indicators"] == '{"adx":31,"rsi":58}'
    assert signal["created_at"] == "2026-04-13T08:00:00Z"
    assert signal["expires_at"] == "2026-04-13T08:00:30Z"

    accounts = client.get("/api/v1/accounts", headers=ADMIN_HEADERS)
    assert accounts.status_code == 200
    assert accounts.json() == {"accounts": [expected_summary], "status": "OK"}

    overview = client.get("/api/v1/overview", headers=ADMIN_HEADERS)
    assert overview.status_code == 200
    def card(title: str, detail: str, tone: str, value: str) -> dict:
        return {"detail": detail, "title": title, "tone": tone, "value": value}

    def check(label: str, detail: str, tone: str, value: str) -> dict:
        return {"detail": detail, "label": label, "tone": tone, "value": value}

    expected_cards = [
        card("System Health", "SQLite + Go API online", "green", "Healthy"),
        card("Connected Accounts", "active terminals reporting", "amber", "1"),
        card("Tradeable Accounts", "market open and trading allowed", "blue", "1"),
        card("Cutover Health", "Replay validated, shadow diff pending", "orange", "Baseline Only"),
    ]
    pending_checks = [
        check("Replay Parity", "Replay fixture has not been approved yet", "orange", "pending"),
        check("Shadow Drift", "Waiting for mirrored production traffic", "orange", "pending"),
        check("Protocol Errors", "Live shadow traffic has not started yet", "amber", "0.00%"),
    ]
    assert overview.json() == {
        "accounts": [expected_summary],
        "cards": expected_cards,
        "generated_at": "2026-04-13T08:00:00Z",
        "status": "OK",
    }

    audit = client.get("/api/v1/audit", headers=ADMIN_HEADERS)
    assert audit.status_code == 200
    assert audit.json() == {
        "status": "OK",
        "generated_at": "2026-04-13T08:00:00Z",
        "summary": pending_checks,
        "report": {
            "ready": False,
            "protocol_error_rate": 0,
            "signal_drift_rate": 0,
            "command_drift_rate": 0,
            "last_shadow_event_at": "0001-01-01T00:00:00Z",
            "missing_capabilities": ["shadow_traffic"],
            "checks": pending_checks,
        },
        "events": [],
    }


async def test_uses_heartbeat_presence_for_admin_connected_state_and_overview_count() -> None:
    client, store = make_client()

    await store.save_registration({"account_id": ACCOUNT_ID, "broker": "Demo Broker", "server_name": "Demo-1"})
    await store.save_registration({"account_id": OTHER_ACCOUNT_ID, "broker": "Demo Broker", "server_name": "Demo-2"})
    await store.save_heartbeat(
        {
            "account_id": ACCOUNT_ID,
            "balance": 1000.5,
            "equity": 1100.25,
            "market_open": True,
            "is_trade_allowed": True,
        }
    )

    accounts = client.get("/api/v1/accounts", headers=ADMIN_HEADERS)
    overview = client.get("/api/v1/overview", headers=ADMIN_HEADERS)

    assert accounts.status_code == 200
    account_list = accounts.json()["accounts"]
    assert account_list[0]["account_id"] == ACCOUNT_ID
    assert account_list[0]["connected"] is True
    assert account_list[1]["account_id"] == OTHER_ACCOUNT_ID
    assert account_list[1]["connected"] is False

    assert overview.status_code == 200
    connected_card = next(card for card in overview.json()["cards"] if card["title"] == "Connected Accounts")
    assert connected_card["value"] == "1"


async def test_accepts_go_compatible_non_get_methods_for_admin_read_handlers() -> None:
    client, store = make_client()

    await store.save_registration({"account_id": ACCOUNT_ID, "broker": "Demo Broker", "server_name": "Demo-1"})
    await store.save_heartbeat(
        {
            "account_id": ACCOUNT_ID,
            "balance": 1000.5,
            "equity": 1100.25,
            "market_open": True,
            "is_trade_allowed": True,
        }
    )

    for url in ("/api/v1/accounts", "/api/v1/accounts/90011087", "/api/v1/overview", "/api/v1/audit"):
        response = client.post(url, headers=ADMIN_HEADERS)
        assert response.status_code == 200
        assert response.json().get("status") == "OK"


async def test_serves_go_compatible_account_detail_behind_admin_auth() -> None:
    client, store = make_client()

    await store.save_registration(
        {
            "account_id": ACCOUNT_ID,
            "broker": "Demo Broker",
            "server_name": "Demo-1",
            "currency": "USD",
            "leverage": 500,
        }
    )
    await store.save_heartbeat(
        {
            "account_id": ACCOUNT_ID,
            "balance": 1000.5,
            "equity": 1100.25,
            "margin": 100,
            "free_margin": 1000.25,
            "market_open": True,
            "is_trade_allowed": True,
        }
    )
    await store.save_tick(
        {
            "account_id": ACCOUNT_ID,
            "symbol": "XAUUSD",
            "bid": 3335.55,
            "ask": 3335.75,
            "spread": 0.2,
            "time": "2026-04-13T07:59:30Z",
        }
    )
    await store.save_positions(
        {
            "account_id": ACCOUNT_ID,
            "positions": [
                {"ticket": 123456, "symbol": "XAUUSD", "type": "BUY", "lots": 0.1, "open_price": 3330, "profit": 5.25}
            ],
        }
    )
    await store.save_ai_result(ACCOUNT_ID, "XAUUSD", {"bias": "bullish", "confidence": 82})
    await store.save_ai_result(ACCOUNT_ID, "GBPJPY", {"bias": "bearish", "confidence": 64})
    await store.record_decision_event(
        {
            "decision_id": "tpv1_old",
            "account_id": ACCOUNT_ID,
            "symbol": "XAUUSD",
            "stage": "candidate_signal",
            "status": "pending",
            "reason_codes": ["candidate.created"],
            "summary": {"score": 7},
            "related_command_id": "",
            "created_at": "2026-04-13T07:59:00.000Z",
        }
    )
    await store.record_decision_event(
        {
            "decision_id": "tpv1_new",
            "account_id": ACCOUNT_ID,
            "symbol": "XAUUSD",
            "stage": "risk_gate",
            "status": "rejected",
            "reason_codes": ["risk.spread.wide"],
            "summary": {"max_lots": 0},
            "related_command_id": "sig_new",
            "created_at": "2026-04-13T08:01:00.000Z",
        }
    )

    response = client.get("/api/v1/accounts/90011087", headers=ADMIN_HEADERS)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "OK"
    assert body["account"]["account_id"] == ACCOUNT_ID
    assert body["account"]["broker"] == "Demo Broker"
    assert body["account"]["balance"] == 1000.5
    assert body["account"]["equity"] == 1100.25
    assert body["market"]["symbol"] == "XAUUSD"
    assert body["market"]["bid"] == 3335.55
    assert body["market"]["ask"] == 3335.75
    assert body["positions"][0]["ticket"] == 123456
    assert body["positions"][0]["direction"] == "BUY"
    assert body["positions"][0]["lots"] == 0.1
    assert body["ai_result"] == {"bias": "bullish", "confidence": 82}
    assert [event["decision_id"] for event in body["decision_events"]] == ["tpv1_new", "tpv1_old"]


async def test_returns_error_for_missing_account_detail() -> None:
    client, _store = make_client()

    response = client.get("/api/v1/accounts/missing", headers=ADMIN_HEADERS)

    assert response.status_code != 200
    assert response.json()["status"] == "ERROR"


async def test_serves_go_compatible_account_decisions_behind_admin_auth() -> None:
    client, store = make_client()

    await store.record_decision_event(
        {
            "decision_id": "tpv1_old",
            "account_id": ACCOUNT_ID,
            "symbol": "XAUUSD",
            "stage": "candidate_signal",
            "status": "pending",
            "reason_codes": ["candidate.created"],
            "summary": {"score": 7},
            "related_command_id": "",
            "created_at": "2026-04-13T07:59:00.000Z",
        }
    )
    await store.record_decision_event(
        {
            "decision_id": "tpv1_rejected",
            "account_id": ACCOUNT_ID,
            "symbol": "XAUUSD",
            "stage": "risk_gate",
            "status": "rejected",
            "reason_codes": ["risk.spread.wide"],
            "summary": {"max_lots": 0},
            "related_command_id": "sig_rejected",
            "created_at": "2026-04-13T08:01:00.000Z",
        }
    )
    await store.record_decision_event(
        {
            "decision_id": "tpv1_other_symbol",
            "account_id": ACCOUNT_ID,
            "symbol": "GBPJPY",
            "stage": "risk_gate",
            "status": "rejected",
            "reason_codes": [],
            "summary": {},
            "related_command_id": "",
            "created_at": "2026-04-13T08:02:00.000Z",
        }
    )

    response = client.get(
        "/api/v1/accounts/90011087/decisions?symbol=XAUUSD&status=rejected&limit=1",
        headers=ADMIN_HEADERS,
    )
    assert response.status_code == 200
    assert response.json() == {
        "status": "OK",
        "account_id": ACCOUNT_ID,
        "decision_events": [
            {
                "id": 2,
                "decision_id": "tpv1_rejected",
                "account_id": ACCOUNT_ID,
                "symbol": "XAUUSD",
                "stage": "risk_gate",
                "status": "rejected",
                "reason_codes": ["risk.spread.wide"],
                "summary": {"max_lots": 0},
                "related_command_id": "sig_rejected",
                "created_at": "2026-04-13T08:01:00.000Z",
            }
        ],
    }

    bad_limit = client.get("/api/v1/accounts/90011087/decisions?limit=0", headers=ADMIN_HEADERS)
    assert bad_limit.status_code == 400
    assert bad_limit.json() == {"status": "ERROR", "message": "limit must be a positive integer"}


# ---------------------------------------------------------------- 仲裁


async def test_updates_pending_signal_arbitration_behind_admin_auth() -> None:
    client, store = make_client()

    await store.save_pending_signal(
        {
            "account_id": ACCOUNT_ID,
            "symbol": "XAUUSD",
            "side": "buy",
            "score": 9,
            "strategy": "pullback",
            "status": "pending",
            "created_at": "2026-04-13T08:00:00.000Z",
            "expires_at": "2026-04-13T08:10:00.000Z",
            "arbitration_result": "",
            "arbitration_reason": "",
        }
    )

    response = client.post(
        "/api/arbitration/1", headers=ADMIN_HEADERS, json={"result": "approved", "reason": "manual ok"}
    )
    assert response.status_code == 200
    assert response.json() == {"status": "OK", "signal_id": 1, "result": "approved"}
    assert await store.get_pending_signals(ACCOUNT_ID, "XAUUSD") == []

    invalid = client.post("/api/arbitration/1", headers=ADMIN_HEADERS, json={"result": "maybe", "reason": "bad"})
    assert invalid.status_code == 400
    assert invalid.json() == {"status": "ERROR", "message": "result must be 'approved' or 'rejected'"}


async def test_rejects_non_integer_arbitration_signal_id_like_go_parse_int() -> None:
    client, _store = make_client()

    for signal_id in ("1.0", "1.5"):
        response = client.post(
            f"/api/arbitration/{signal_id}", headers=ADMIN_HEADERS, json={"result": "approved", "reason": "manual ok"}
        )
        assert response.status_code == 400
        assert response.json() == {"status": "ERROR", "message": "invalid signal_id"}


async def test_expires_stale_pending_signals_behind_admin_auth() -> None:
    client, store = make_client(now_iso=lambda: "2026-04-13T08:03:00.000Z")

    await store.save_pending_signal(
        {
            "account_id": ACCOUNT_ID,
            "symbol": "XAUUSD",
            "side": "buy",
            "score": 7,
            "strategy": "pullback",
            "status": "pending",
            "created_at": "2026-04-13T08:00:00.000Z",
            "expires_at": "2026-04-13T08:02:00.000Z",
        }
    )
    await store.save_pending_signal(
        {
            "account_id": ACCOUNT_ID,
            "symbol": "XAUUSD",
            "side": "sell",
            "score": 8,
            "strategy": "range",
            "status": "pending",
            "created_at": "2026-04-13T08:01:00.000Z",
            "expires_at": "2026-04-13T08:10:00.000Z",
        }
    )

    response = client.post("/api/arbitration/expire", headers=ADMIN_HEADERS)
    assert response.status_code == 200
    assert response.json() == {"status": "OK", "expired": 1}
    remaining = await store.get_pending_signals(ACCOUNT_ID, "XAUUSD")
    assert [signal["id"] for signal in remaining] == [2]


# ---------------------------------------------------------------- audit


async def test_renders_api_v1_audit_from_persisted_shadow_state_instead_of_placeholders() -> None:
    client, store = make_client(now_iso=lambda: "2026-07-02T12:05:00.000Z")

    await store.record_shadow_comparison(
        {
            "account_id": ACCOUNT_ID,
            "symbol": "XAUUSD",
            "protocol_ok": True,
            "signal_drift": False,
            "command_drift": True,
            "oracle_compared": True,
            "source": "ai_result",
            "created_at": "2026-07-02T12:00:00.000Z",
        }
    )

    response = client.get("/api/v1/audit", headers=ADMIN_HEADERS)
    assert response.status_code == 200
    body = response.json()
    assert body["report"] == {
        "ready": False,
        "protocol_error_rate": 0,
        "signal_drift_rate": 0,
        "command_drift_rate": 1,
        "replay_coverage": 0,
        "last_shadow_event_at": "2026-07-02T12:00:00.000Z",
        "missing_capabilities": ["replay_coverage"],
        "checks": [
            {
                "label": "Oracle Replay",
                "value": "validated",
                "detail": "Go oracle comparisons are flowing into the shadow stream",
                "tone": "green",
            },
            {
                "label": "Shadow Drift",
                "value": "review required",
                "detail": "Signal 0.00%, command 100.00% (limit 2.00%)",
                "tone": "red",
            },
            {
                "label": "Protocol Errors",
                "value": "0.00%",
                "detail": "No contract mismatches observed in mirrored traffic",
                "tone": "green",
            },
            {
                "label": "Replay Coverage",
                "value": "pending",
                "detail": "Replay fixture set has not been scanned yet",
                "tone": "amber",
            },
        ],
    }
    assert body["summary"] == [
        {
            "label": "Replay Parity",
            "value": "validated",
            "detail": "Replay fixture matched baseline or drift is within threshold",
            "tone": "green",
        },
        {
            "label": "Shadow Drift",
            "value": "active",
            "detail": "Last shadow event at 2026-07-02T12:00:00.000Z",
            "tone": "blue",
        },
        {
            "label": "Protocol Errors",
            "value": "0.00%",
            "detail": "No contract mismatches observed in replay or shadow mode",
            "tone": "green",
        },
    ]


# ---------------------------------------------------------------- trading-core 分析


def pullback_buy_bars() -> list[dict]:
    """等价 app.spec.ts pullbackBuyBars(与 tests/unit/trading_core/test_replay.py 同源)。"""
    bars = [
        {
            "time": f"2026-04-16T{index:02d}:00:00.000Z",
            "open": 95,
            "high": 96,
            "low": 94,
            "close": 95,
            "atr": 2,
            "adx": 35,
            "rsi": 45,
            "ema20": 95.8,
            "ema50": 90,
            "macd_hist": 1,
            "r1": 97.5,
        }
        for index in range(50)
    ]
    bars[48] = {**bars[48], "close": 95.2, "open": 95.2}
    bars[49] = {**bars[49], "close": 95, "open": 95}
    return bars


def d1_trend_bars() -> list[dict]:
    return [
        {
            "time": f"2026-04-{index + 1:02d}T00:00:00.000Z",
            "open": 100 + index,
            "high": 101 + index,
            "low": 99 + index,
            "close": 100 + index,
            "adx": 35,
            "ema20": 120,
            "ema50": 100,
        }
        for index in range(40)
    ]


async def test_serves_trading_core_analysis_without_enqueueing_commands() -> None:
    client, store = make_client(now_iso=lambda: "2026-04-13T08:00:00Z")
    snapshot = REPLAY_SNAPSHOT

    # Gold Fib 门控 pullback 入场点:把 fixture 的 Fib 口袋钉在 Go 期望入场附近
    last_h1_bar = snapshot["bars"]["H1"][-1]
    if last_h1_bar:
        last_h1_bar.update({"fib_382": 3350, "fib_618": 3320, "fib_786": 3334.93})

    await store.save_registration(
        {
            "account_id": snapshot["account_id"],
            "broker": "Demo Broker",
            "server_name": "Demo-1",
            "account_name": "Primary",
            "account_type": "demo",
            "currency": "USD",
            "leverage": 500,
            "ai_symbols": ["XAUUSD", "GBPJPY"],
        }
    )
    await store.save_tick(
        {
            "account_id": snapshot["account_id"],
            "symbol": "XAUUSD",
            "bid": 3335.55,
            "ask": snapshot["current_price"],
            "spread": 0.2,
            "time": "08:00:00",
        }
    )
    for timeframe in ("H1", "H4", "M30", "M15"):
        await store.save_bars(
            {
                "account_id": snapshot["account_id"],
                "symbol": "XAUUSD",
                "timeframe": timeframe,
                "bars": snapshot["bars"][timeframe],
            }
        )
    await store.save_positions(
        {
            "account_id": snapshot["account_id"],
            "symbol": "XAUUSD",
            "positions": [
                {
                    "ticket": 777,
                    "symbol": "XAUUSDm#",
                    "type": "BUY",
                    "lots": 0.1,
                    "open_price": 3330,
                    "profit": 5.25,
                    "strategy": "pullback",
                }
            ],
        }
    )

    response = client.get("/api/v1/analysis/90011087/XAUUSD/trading-core", headers=USER_HEADERS)
    assert response.status_code == 200
    body = response.json()

    assert body["status"] == "OK"
    assert body["generated_at"] == "2026-04-13T08:00:00Z"
    assert body["replay"]["signal"] is None
    expected_logs = [
        {
            "level": "info",
            "strategy": "市场",
            "msg": (
                "Price=3335.75 | ATR=2.67 | RSI=64.7 | ADX=71.5 | "
                "EMA趋势(H1)=多头 | H4=强多头(ADX=100.0) | MACD柱=-0.82"
            ),
        },
        {
            "level": "info",
            "strategy": "M15确认",
            "msg": "⏭ pullback | M15未确认: RSI=77.2≥40",
        },
        {
            "level": "warn",
            "strategy": "R:R过滤",
            "msg": "⚠️ 信号 R:R=0.875 < 1.25 拒绝 ⏭",
        },
    ]
    assert body["replay"]["logs"] is not None
    for expected in expected_logs:
        assert (
            expected
            in [
                {"level": log["level"], "strategy": log["strategy"], "msg": log["msg"]}
                for log in body["replay"]["logs"]
            ]
        ), f"缺少日志 {expected}"
    assert body["replay"]["canProduceLiveCommands"] is False
    assert body["position_summary"]["accountId"] == "90011087"
    assert body["position_summary"]["symbol"] == "XAUUSD"
    assert body["position_summary"]["totalOpenPositions"] == 1
    assert body["position_summary"]["buyLots"] == 0.1
    assert body["position_summary"]["floatingProfit"] == 5.25
    assert body["position_summary"]["canProduceLiveCommands"] is False

    modify_command = next(
        (entry for entry in body["replay"]["position_commands"] if entry["action"] == "MODIFY"), None
    )
    close_command = next((entry for entry in body["replay"]["position_commands"] if entry["action"] == "CLOSE"), None)
    assert modify_command is not None
    assert modify_command["ticket"] == 777
    assert modify_command["reason"] == "lock_l1_2.2ATR"
    assert abs(modify_command["new_sl"] - 3330.8) <= 1
    assert close_command is not None
    assert close_command["ticket"] == 777
    assert close_command["lots"] == 0.04
    assert close_command["reason"] == "TP1_2.2ATR"

    poll = client.post("/poll", headers=USER_HEADERS, json={"account_id": "90011087"})
    assert poll.status_code == 200
    assert poll.json() == {"status": "OK", "commands": [], "count": 0}
    assert await store.poll_commands("90011087") == []


async def test_uses_latest_h1_close_for_trading_core_analysis_when_no_current_tick_exists() -> None:
    client, store = make_client(now_iso=lambda: "2026-04-16T12:00:00.000Z")

    await store.save_bars(
        {"account_id": ACCOUNT_ID, "symbol": "XAUUSD", "timeframe": "H1", "bars": pullback_buy_bars()}
    )
    # XAUUSD 走 gold 分品种配置,pullback 需要 H4 数据做 Fib 趋势确认
    await store.save_bars(
        {
            "account_id": ACCOUNT_ID,
            "symbol": "XAUUSD",
            "timeframe": "H4",
            "bars": [
                {
                    "time": f"2026-04-15T{index:02d}:00:00.000Z",
                    "open": 90 + index,
                    "high": 100 + index,
                    "low": 88 + index,
                    "close": 95 + index,
                    "adx": 30,
                    "ema20": 110,
                    "ema50": 100,
                }
                for index in range(5)
            ],
        }
    )
    await store.save_bars({"account_id": ACCOUNT_ID, "symbol": "XAUUSD", "timeframe": "D1", "bars": d1_trend_bars()})

    response = client.get("/api/v1/analysis/90011087/XAUUSD/trading-core", headers=USER_HEADERS)
    assert response.status_code == 200
    signal = response.json()["replay"]["signal"]
    assert signal["strategy"] == "pullback"
    assert signal["side"] == "BUY"
    assert signal["entry"] == 95
    assert signal["score"] == 9
