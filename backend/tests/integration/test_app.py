"""app-server scaffold 集成测试:1:1 镜像 apps/app-server/src/app.spec.ts(非 admin 部分)。

级别:L4 集成 —— 走完整 FastAPI 路由栈(create_api_app + TestClient + in-memory store)。

源 `apps/app-server/src/app.spec.ts` 的 it() → 本文件映射(admin 相关已由 test_admin.py 承担):

## Group A — scaffold(healthz/metrics/dashboard/version/download)
- "prefers X-API-Token over X-API-Key and query token"            → tests/unit/test_auth.py
- "binds the first account for a token with no stored account set" → tests/unit/test_auth.py
- "rejects account access when token binding does not match..."    → tests/unit/test_auth.py
- "returns Go-compatible health payload"                           → test_healthz
- "serves Go-compatible Prometheus metrics text"                   → test_metrics_text
- "serves dashboard static files with Go-compatible SPA fallbacks" → 未实现(报告:Python 无 dashboard 静态托管)⚠
- "serves public EA release version metadata"                      → tests/unit/api/test_version_download.py
- "rejects EA version_check without a route token"                 → test_version_check_post_without_token
- "serves token-protected EA version_check payload"                → test_version_check_post_with_token
- "rejects EA download without a route token"                      → tests/unit/api/test_version_download.py
- "serves token-protected EA download as an attachment"            → tests/unit/api/test_version_download.py
- "returns Go-shaped 404 when the EA download file is missing"     → tests/unit/api/test_version_download.py

## Group B — indicator alerts + visual poll(app 级组合)
- "stores and polls indicator alerts with duplicate suppression"
  → test_indicator_alerts_store_and_poll_with_duplicate_suppression
- "rejects invalid indicator alert requests"                       → tests/unit/api/test_indicator_alert.py
- "serves visual poll with tick, AI trade plan, and matching alerts" → test_visual_poll_full_tick_ai_and_matching_alerts
- "rejects invalid visual poll requests"                           → test_visual_poll_rejects_invalid_requests
- "serves visual poll with request symbol when no tick exists"     → tests/unit/api/test_visual.py
- "keeps visual AI summary empty for Go-compatible blank AI results" → tests/unit/api/test_visual.py

## Group C — EA lifecycle
- "accepts safe EA lifecycle routes with Go-shaped responses and stores payloads" → test_ea_fixture_loop_stores_payloads
- "logs accepted EA lifecycle details without token data"          → test_ea_lifecycle_logs_omit_tokens
- "does not emit EA lifecycle success logs for rejected payloads"  → test_ea_no_success_logs_on_rejected_payloads
- "polls only explicitly queued commands and marks them delivered" → test_ea_poll_only_queued_and_marks_delivered
- "rejects malformed EA requests with Go-compatible error envelopes" → test_ea_malformed_requests_error_envelopes
- "accepts Go-compatible sparse bars and positions payloads"       → test_ea_sparse_bars_and_positions
- "normalizes empty strategy from GB_ comment, never from magic"   → test_ea_strategy_from_comment_never_from_magic
- "analysis_payload backfills empty strategy from comment for agent schema"
                                                                    → test_analysis_strategy_backfill_from_comment
- "re-parses truncated strategy like ai from GB_ai_signal comment" → test_analysis_reparses_truncated_ai_strategy
- "rejects nested EA payload type mismatches like the Go decoder"  → test_ea_nested_type_mismatch_matrix
- "applies order_result only to delivered commands and preserves broker error text"
                                                                    → test_ea_order_result_reconcile_delivered_command
- "rejects order_result payloads missing required fields before persistence"
                                                                    → test_ea_order_result_required_fields(+unit)
- "accepts Go-compatible non-POST EA route methods when the JSON body is valid"
                                                                    → test_ea_get_poll_accepts_non_post_method
- "can enforce auth tokens using the Go-compatible extraction priority" → tests/unit/test_auth.py + test_ai.py
- "enforces Go-compatible account authorization for EA write routes" → tests/unit/api/test_ea_lifecycle.py
- token 管理 / trigger_ai / admin gates / symbol 路由           → tests/integration/test_admin.py
- "rejects API routes when no Node token store is configured"     → tests/integration/test_admin.py
- "rejects unbound valid tokens on API account routes without auto-binding" → tests/integration/test_admin.py

## Group D — shadow / SSE
- /shadow/metrics、/shadow/qualification、POST /shadow/comparisons(5 用例)
  → test_shadow_*(基础冒烟另见 test_version_download.py)
- SSE injectable + live streaming                                  → tests/unit/api/test_sse_events.py(生成器+鉴权)
                                                                    + test_ai.py(hub 发布)

## Group E — analysis_payload 基础(fixture 驱动)
- "serves analysis payloads and stores AI results with deterministic risk gates" → test_analysis_ai_result_fixture_flow
- "filters analysis payload positions to the requested symbol"     → test_analysis_positions_filtered_by_symbol
- "matches Go-compatible analysis payload position fields and timestamp formatting"
                                                                    → test_analysis_position_fields_and_timestamp
- "caps Go-compatible analysis payload bars without changing indicator history count"
                                                                    → test_analysis_bars_capped_indicators_full_count

## Group F — ai_result 进阶
- legacy close_all / close_short risk alerts                       → test_ai_result_legacy_close_all_risk_alert
  / test_ai_result_legacy_close_short_only_matching_sells
- "queues accepted V2 AI risk commands with trade-plan metadata"   → test_ai_result_v2_risk_command_metadata
- "does not queue V2 AI risk commands when the trade-plan risk gate rejects"
                                                                    → test_ai_result_v2_gate_reject_no_queue
- "does not queue live commands for accepted AI approve plans in shadow mode"
  → test_ai_result_shadow_mode_stores_but_does_not_queue
- "queues confidence 65 accepted AI approve plans for cutover accounts"
  → test_ai_result_confidence_65_cutover_queues
- "records a queue skip event for confidence 64 accepted AI approve plans" → test_ai_result_confidence_64_queue_skip
- stop intents(BUY_STOP/SELL_STOP)                                 → test_ai_result_stop_intents_cutover_no_queue
- "queues the first valid dual AI approve plan and keeps the Go symbol cooldown behavior"
                                                                    → test_ai_result_dual_plan_queues_first_and_cooldown
- "returns rejected AI approve risk gates without queueing poll commands"
                                                                    → test_ai_result_approve_reject_no_queue
- "returns Go-style invalid trade_plan validation without decision or risk gate"
                                                                    → test_ai_result_trade_plan_validation_error
- "rejects empty and array AI result bodies like the Go decoder"   → test_ai_result_rejects_empty_and_array_bodies
- "accepts empty AI result objects like the Go decoder"            → test_ai_result_accepts_empty_object
- trade_plan decode 类型矩阵(7 用例)                               → test_ai_result_decode_*

## Group G — analysis payload 进阶(indicators/trend/mapping/filters/market_status)
- indicators + trend context / D1 trend / strategy mapping 默认与合并 / market_filters /
  max_spread 回退 / market_status 全套(9 用例)                     → test_analysis_*

## Group H — trading-core / trade_history
- trading-core 2 用例                                              → tests/integration/test_admin.py
- POST /api/trade_history 3 用例                                   → tests/unit/api/test_trade_history.py
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from backend.api.app import create_api_app
from backend.persistence.store import EaStore, create_in_memory_store

ACCOUNT_ID = "90011087"
OTHER_ACCOUNT_ID = "90022000"
USER_TOKEN = "fixture-user-token"
ADMIN_TOKEN = "fixture-admin-token"
SYMBOL = "XAUUSD"

USER_HEADERS = {"X-API-Token": USER_TOKEN}
ADMIN_HEADERS = {"X-API-Token": ADMIN_TOKEN}

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures"

# 源 app.spec.ts 的 nowIso/nowUnix 常量(与 TS 断言逐字一致)
NOW_ISO_SHANGHAI = "2026-04-13T16:00:00+08:00"
NOW_UNIX = 1772342400


def read_fixture(name: str) -> dict:
    return json.loads((FIXTURE_ROOT / "earoutes" / f"{name}.json").read_text(encoding="utf-8"))


def read_admin_fixture(name: str) -> dict:
    return json.loads((FIXTURE_ROOT / "admin" / f"{name}.json").read_text(encoding="utf-8"))


def make_client(**options) -> tuple[TestClient, EaStore]:
    """等价 app.spec.ts createApiServer 默认:user/admin 两个 fixture token。"""
    store = options.pop("store", None) or create_in_memory_store()
    defaults = {
        "store": store,
        "valid_tokens": {USER_TOKEN, ADMIN_TOKEN},
        "token_accounts": {USER_TOKEN: {ACCOUNT_ID}},
        "admin_tokens": {ADMIN_TOKEN},
        "now_unix": lambda: NOW_UNIX,
        "now_iso": lambda: NOW_ISO_SHANGHAI,
    }
    client = TestClient(create_api_app({**defaults, **options}))
    return client, store


async def inject_ea_fixture(client: TestClient, name: str) -> None:
    """POST 一个 earoutes fixture(ea.ts 兼容端点)。"""
    fixture = read_fixture(name)
    method = (fixture.get("request") or {}).get("method", "POST")
    path = (fixture.get("request") or {}).get("path", f"/{name}")
    headers = {**(fixture.get("request") or {}).get("headers", {}), **USER_HEADERS}
    response = client.request(method, path, json=(fixture.get("request") or {}).get("body"), headers=headers)
    assert response.status_code == 200, f"{name}: {response.status_code} {response.text}"


async def seed_ea_fixtures(client: TestClient, names: list[str]) -> None:
    for name in names:
        await inject_ea_fixture(client, name)


def _trend_bars_payload(close: int = 3336, ema20: int = 3335, ema50: int = 3330) -> list[dict]:
    return [{"close": close, "ema20": ema20, "ema50": ema50, "adx": 35, "atr": 2, "rsi": 60}]


async def seed_ai_approve_trend_bars(store: EaStore) -> None:
    for timeframe in ["D1", "H4", "H1", "M30", "M15"]:
        await store.save_bars(
            {
                "account_id": ACCOUNT_ID,
                "symbol": "XAUUSD",
                "timeframe": timeframe,
                "bars": _trend_bars_payload(),
            }
        )


def _now_iso_from_ms(now_ms: int) -> str:
    return datetime.fromtimestamp(now_ms / 1000, tz=UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


# ---------------------------------------------------------------- Group A:scaffold


async def test_healthz() -> None:
    client, _store = make_client()
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.text == "ok"


async def test_metrics_text() -> None:
    client, _store = make_client()
    response = client.get("/healthz")
    assert response.status_code == 200
    # 镜像 TS:GET /not-found 仅用于产生指标记录,不断言其状态码
    client.get("/not-found")
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    body = response.text
    assert "# HELP goldbot_http_requests_total" in body
    assert "goldbot_http_requests_total" in body
    assert 'goldbot_http_requests_total{method="GET",path="/metrics",status="2xx"} 1' in body


async def test_version_check_post_without_token(tmp_path: Path) -> None:
    mt4_dir = tmp_path / "apps" / "app-mt" / "mt4_ea"
    mt4_dir.mkdir(parents=True)
    (mt4_dir / "version.json").write_text(
        json.dumps({"version": "2.9.5", "build": 15, "changelog": "x"}), encoding="utf-8"
    )
    client, _store = make_client(release_root=tmp_path)
    response = client.post("/version_check", json={})
    assert response.status_code == 401
    assert response.json() == {"status": "ERROR", "message": "invalid token"}


async def test_version_check_post_with_token(tmp_path: Path) -> None:
    mt4_dir = tmp_path / "apps" / "app-mt" / "mt4_ea"
    mt4_dir.mkdir(parents=True)
    (mt4_dir / "version.json").write_text(
        json.dumps({"version": "2.9.5", "build": 15, "changelog": "x"}), encoding="utf-8"
    )
    client, _store = make_client(release_root=tmp_path)
    response = client.post("/version_check", json={}, headers=USER_HEADERS)
    assert response.status_code == 200
    assert response.json() == {"latest_version": "2.9.5", "latest_build": 15, "force_update": False}


# ---------------------------------------------------------------- Group B:indicator alerts + visual poll


async def test_indicator_alerts_store_and_poll_with_duplicate_suppression() -> None:
    client, _store = make_client(now_unix=lambda: NOW_UNIX)
    alert = {
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
        "description": "RSI bullish divergence",
        "rsi_divergence": "bullish",
    }
    first = client.post("/indicator_alert/store", json=alert, headers=USER_HEADERS)
    duplicate = client.post("/indicator_alert/store", json=alert, headers=USER_HEADERS)
    poll = client.post("/indicator_alert/poll", json={"account_id": "ignored-by-go"}, headers=USER_HEADERS)

    assert first.status_code == 200
    assert first.json() == {"status": "ok", "should_send": True}
    assert duplicate.status_code == 200
    assert duplicate.json() == {"status": "ok", "should_send": False}
    assert poll.status_code == 200
    assert poll.json() == {"status": "ok", "count": 1, "alerts": [alert]}


async def test_visual_poll_full_tick_ai_and_matching_alerts() -> None:
    client, store = make_client()
    client.app.state.now_iso = lambda: "2026-04-13T08:05:00.000Z"
    client.app.state.now_unix = lambda: NOW_UNIX
    await store.save_tick(
        {
            "account_id": ACCOUNT_ID,
            "symbol": "XAUUSD",
            "bid": 3335.55,
            "ask": 3335.75,
            "spread": 20,
            "time": "08:00:00",
        }
    )
    await store.save_ai_result(
        ACCOUNT_ID,
        "XAUUSD",
        {
            "bias": "bullish",
            "confidence": 82,
            "exit_suggestion": "hold",
            "risk_alert": False,
            "alert_reason": "",
            "decision_id": "tpv1_abc123",
            "trade_plan": {
                "mode": "approve",
                "side": "buy",
                "entry_zone": {"min": 3330, "max": 3334},
                "stop_loss": 3320,
                "take_profit": [3360],
                "narrative": "trade plan narrative",
            },
            "risk_gate": {"status": "accepted"},
        },
    )

    client.post(
        "/indicator_alert/store",
        json={
            "id": "alert_xau_h1",
            "type": "divergence",
            "indicator": "RSI",
            "direction": "bullish",
            "symbol": "XAUUSD",
            "timeframe": "H1",
            "time": "2026-04-13T08:00:00.000Z",
            "price": 3335.75,
            "strength": "strong",
            "confidence": 0.82,
            "description": "RSI bullish divergence",
            "rsi_divergence": "bullish",
        },
        headers=USER_HEADERS,
    )
    client.post(
        "/indicator_alert/store",
        json={
            "id": "alert_gbp_m15",
            "type": "divergence",
            "indicator": "RSI",
            "direction": "bearish",
            "symbol": "GBPJPY",
            "timeframe": "M15",
            "time": "2026-04-13T08:00:00.000Z",
            "price": 190.1,
            "strength": "medium",
            "confidence": 0.6,
            "description": "GBPJPY alert",
        },
        headers=USER_HEADERS,
    )

    response = client.post(
        "/visual/poll",
        json={"account_id": ACCOUNT_ID, "symbol": "XAUUSD", "timeframe": "H1"},
        headers=USER_HEADERS,
    )
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "account_id": ACCOUNT_ID,
        "symbol": "XAUUSD",
        "timeframe": "H1",
        "server_time": "2026-04-13T08:05:00.000Z",
        "tick": {"symbol": "XAUUSD", "bid": 3335.55, "ask": 3335.75, "spread": 20, "time": "08:00:00"},
        "ai": {
            "has_result": True,
            "bias": "bullish",
            "confidence": 82,
            "exit_suggestion": "hold",
            "risk_alert": False,
            "alert_reason": "",
            "decision_id": "tpv1_abc123",
            "trade_plan_mode": "approve",
            "side": "buy",
            "entry_min": 3330,
            "entry_max": 3334,
            "stop_loss": 3320,
            "take_profit": 3360,
            "risk_gate_status": "accepted",
            "narrative": "trade plan narrative",
        },
        "alerts": [
            {
                "id": "alert_xau_h1",
                "type": "divergence",
                "indicator": "RSI",
                "direction": "bullish",
                "symbol": "XAUUSD",
                "timeframe": "H1",
                "time": "2026-04-13T08:00:00.000Z",
                "price": 3335.75,
                "strength": "strong",
                "confidence": 0.82,
                "description": "RSI bullish divergence",
                "rsi_divergence": "bullish",
            }
        ],
        "count": 1,
    }


async def test_visual_poll_rejects_invalid_requests() -> None:
    client, _store = make_client()
    invalid_json = client.post("/visual/poll", data="{", headers={**USER_HEADERS, "Content-Type": "application/json"})
    assert invalid_json.status_code == 400
    assert invalid_json.json() == {"status": "ERROR", "message": "invalid json"}
    missing = client.post("/visual/poll", json={"account_id": ACCOUNT_ID}, headers=USER_HEADERS)
    assert missing.status_code == 400
    assert missing.json() == {"status": "ERROR", "message": "account_id and symbol are required"}
    forbidden = client.post("/visual/poll", json={"account_id": "90022098", "symbol": "XAUUSD"}, headers=USER_HEADERS)
    assert forbidden.status_code == 403
    assert forbidden.json() == {"status": "ERROR", "message": "forbidden"}


# ---------------------------------------------------------------- Group C:EA lifecycle


async def test_ea_fixture_loop_stores_payloads() -> None:
    client, store = make_client()
    await store.enqueue_command(ACCOUNT_ID, {
        "command_id": "sig_2",
        "action": "SIGNAL",
        "strategy": "pullback",
        "symbol": "XAUUSD",
        "type": "BUY",
    })
    assert len(await store.poll_commands(ACCOUNT_ID)) == 1

    for name in ["register", "heartbeat", "tick", "bars", "positions", "order_result"]:
        fixture = read_fixture(name)
        method = (fixture.get("request") or {}).get("method", "POST")
        path = (fixture.get("request") or {}).get("path", f"/{name}")
        headers = {**(fixture.get("request") or {}).get("headers", {}), **USER_HEADERS}
        response = client.request(method, path, json=(fixture.get("request") or {}).get("body"), headers=headers)
        assert response.status_code == 200, f"{name}: {response.status_code} {response.text}"
        assert response.json() == (fixture.get("response") or {}).get("body"), name

    registration = await store.get_registration(ACCOUNT_ID)
    assert registration is not None and registration.get("broker") == "Demo Broker"
    heartbeat = await store.get_heartbeat(ACCOUNT_ID)
    assert heartbeat is not None and heartbeat.get("equity") == 1100.25
    tick = await store.get_latest_tick(ACCOUNT_ID, "XAUUSD")
    assert tick is not None and tick.get("ask") == 3335.75
    assert len(await store.get_bars(ACCOUNT_ID, "XAUUSD", "H1")) == 1
    assert len(await store.get_positions(ACCOUNT_ID)) == 1
    assert len(await store.get_order_results(ACCOUNT_ID)) == 1


async def test_ea_lifecycle_logs_omit_tokens() -> None:
    logs: list[str] = []

    def collect(message: str) -> None:
        logs.append(message)

    client, _store = make_client(
        valid_tokens={USER_TOKEN},
        token_accounts={USER_TOKEN: {ACCOUNT_ID}},
        admin_tokens=set(),
        log=collect,
        now_unix=lambda: NOW_UNIX,
        now_iso=lambda: "2026-03-01T00:00:00.000Z",
    )
    for name in ["register", "heartbeat", "tick"]:
        await inject_ea_fixture(client, name)

    assert len(logs) == 3
    assert "[EA-REGISTER] account_id=90011087" in logs[0]
    assert "broker=Demo Broker" in logs[0]
    assert "strategies=" in logs[0]
    assert "[EA-HEARTBEAT] account_id=90011087" in logs[1]
    assert "equity=1100.25" in logs[1]
    assert "market_open=true" in logs[1]
    assert "[EA-TICK] account_id=90011087" in logs[2]
    assert "symbol=XAUUSD" in logs[2]
    assert "ask=3335.75" in logs[2]
    joined = "\n".join(logs)
    assert "X-API-Token" not in joined
    assert USER_TOKEN not in joined


async def test_ea_no_success_logs_on_rejected_payloads() -> None:
    logs: list[str] = []

    def collect(message: str) -> None:
        logs.append(message)

    client, _store = make_client(valid_tokens=None, token_accounts=None, admin_tokens=set(), log=collect)
    response = client.post("/tick", json={"account_id": ACCOUNT_ID, "bid": "3335.55"})
    assert response.status_code == 400
    assert logs == []


async def test_ea_poll_only_queued_and_marks_delivered() -> None:
    client, store = make_client()
    poll_fixture = json.loads((FIXTURE_ROOT / "earoutes" / "poll.json").read_text(encoding="utf-8"))
    empty_case = poll_fixture["cases"][0]
    queued_case = poll_fixture["cases"][1]

    empty = client.request(
        empty_case["request"]["method"],
        empty_case["request"]["path"],
        json=empty_case["request"]["body"],
        headers=USER_HEADERS,
    )
    assert empty.status_code == 200
    assert empty.json() == empty_case["response"]["body"]

    command = {
        "command_id": "sig_1",
        "action": "SIGNAL",
        "strategy": "pullback",
        "symbol": "XAUUSD",
        "type": "BUY",
        "entry": 3345.5,
        "sl": 3338,
        "tp1": 3358,
        "score": 7,
    }
    await store.enqueue_command(ACCOUNT_ID, command)

    queued = client.request(
        queued_case["request"]["method"],
        queued_case["request"]["path"],
        json=queued_case["request"]["body"],
        headers=USER_HEADERS,
    )
    assert queued.status_code == 200
    assert queued.json() == queued_case["response"]["body"]

    delivered = client.request(
        empty_case["request"]["method"],
        empty_case["request"]["path"],
        json=empty_case["request"]["body"],
        headers=USER_HEADERS,
    )
    assert delivered.json() == empty_case["response"]["body"]


async def test_ea_malformed_requests_error_envelopes() -> None:
    # TS:createAppServer() 未配置 token store → 跳过 token 鉴权
    client, _store = make_client(valid_tokens=None, token_accounts=None, admin_tokens=set())
    invalid_json = client.post("/register", data="{", headers={"Content-Type": "application/json"})
    assert invalid_json.status_code == 400
    assert invalid_json.json() == {"status": "ERROR", "message": "invalid JSON"}
    missing_account = client.post("/register", json={})
    assert missing_account.status_code == 400
    assert missing_account.json() == {"status": "ERROR", "message": "missing account_id"}
    blank_account = client.post("/register", json={"account_id": "   "})
    assert blank_account.status_code == 400
    assert blank_account.json() == {"status": "ERROR", "message": "missing account_id"}


async def test_ea_sparse_bars_and_positions() -> None:
    client, store = make_client(valid_tokens=None, token_accounts=None, admin_tokens=set())
    no_bars = client.post("/bars", json={"account_id": ACCOUNT_ID})
    assert no_bars.status_code == 200
    assert no_bars.json() == {"status": "OK", "received": 0}

    no_bars_tf = client.post("/bars", json={"account_id": ACCOUNT_ID, "timeframe": "H1"})
    assert no_bars_tf.status_code == 200
    assert no_bars_tf.json() == {"status": "OK", "received": 0}
    assert await store.get_bars(ACCOUNT_ID, "XAUUSD", "H1") == []

    no_positions = client.post("/positions", json={"account_id": ACCOUNT_ID})
    assert no_positions.status_code == 200
    assert no_positions.json() == {"status": "OK", "count": 0}
    assert await store.get_positions(ACCOUNT_ID) == []


async def test_ea_strategy_from_comment_never_from_magic() -> None:
    client, store = make_client(valid_tokens=None, token_accounts=None, admin_tokens=set())
    bars = client.post(
        "/bars",
        json={
            "account_id": ACCOUNT_ID,
            "symbol": "XAUUSD",
            "timeframe": "H1",
            "bars": [{"time": 1712971200, "open": 3300, "high": 3301, "low": 3299, "close": 3300.5}],
        },
    )
    assert bars.status_code == 200
    stored_bars = await store.get_bars(ACCOUNT_ID, "XAUUSD", "H1")
    assert stored_bars[0].get("time") == "1712971200"

    positions = client.post(
        "/positions",
        json={
            "account_id": ACCOUNT_ID,
            "positions": [
                {
                    "ticket": 123,
                    "symbol": "XAUUSD",
                    "type": "BUY",
                    "lots": 0.1,
                    "open_price": 3300,
                    "magic": 20250238,
                    "strategy": "",
                    "comment": "GB_divergence_S8_A",
                }
            ],
        },
    )
    assert positions.status_code == 200
    stored = await store.get_positions(ACCOUNT_ID)
    assert stored[0]["strategy"] == "divergence"
    assert stored[0]["comment"] == "GB_divergence_S8_A"

    unknown_pos = client.post(
        "/positions",
        json={
            "account_id": ACCOUNT_ID,
            "positions": [
                {
                    "ticket": 456,
                    "symbol": "XAUUSD",
                    "type": "SELL",
                    "lots": 0.05,
                    "open_price": 3301,
                    "magic": 99999999,
                    "strategy": "",
                    "comment": "",
                }
            ],
        },
    )
    assert unknown_pos.status_code == 200
    stored = await store.get_positions(ACCOUNT_ID)
    ticket456 = next(p for p in stored if int(p["ticket"]) == 456)
    assert ticket456["strategy"] == "unknown"


async def test_analysis_strategy_backfill_from_comment() -> None:
    client, store = make_client(now_unix=lambda: 1713000000)
    await inject_ea_fixture(client, "register")
    client.post(
        "/heartbeat",
        json={
            "account_id": ACCOUNT_ID,
            "balance": 10000,
            "equity": 10000,
            "free_margin": 9000,
            "margin": 1000,
            "market_open": True,
            "is_trade_allowed": True,
            "server_time": "2026.04.13 08:00:00",
        },
        headers=USER_HEADERS,
    )
    client.post(
        "/tick",
        json={
            "account_id": ACCOUNT_ID,
            "symbol": "GBPJPY",
            "bid": 216.5,
            "ask": 216.55,
            "spread": 5,
            "time": "08:00:00",
        },
        headers=USER_HEADERS,
    )
    await store.save_positions(
        {
            "account_id": ACCOUNT_ID,
            "symbol": "GBPJPY",
            "positions": [
                {
                    "ticket": 42275446,
                    "symbol": "GBPJPY",
                    "type": "BUY",
                    "lots": 0.03,
                    "open_price": 216.4,
                    "magic": 202502333,
                    "strategy": "",
                    "comment": "GB_divergence_S8_A",
                    "profit": 1.2,
                }
            ],
        }
    )

    response = client.get(f"/api/v2/analysis_payload/{ACCOUNT_ID}/GBPJPY", headers=USER_HEADERS)
    assert response.status_code == 200
    positions = response.json()["positions"]
    assert any(
        p.get("ticket") == 42275446 and p.get("strategy") == "divergence" and p.get("comment") == "GB_divergence_S8_A"
        for p in positions
    )


async def test_analysis_payload_symbol_case_insensitive_tick_lookup() -> None:
    """回归(81124211 GOLDm#):/bars 入库大写 GOLDM# 而 /tick 保留 EA 原样 GOLDm#,
    workflow 用大写符号拉 payload 时必须能读到小写 tick,market_open 不得误判 closed。"""
    client, store = make_client(now_unix=lambda: 1713000000)
    await inject_ea_fixture(client, "register")
    client.post(
        "/heartbeat",
        json={
            "account_id": ACCOUNT_ID,
            "balance": 10000,
            "equity": 10000,
            "free_margin": 9000,
            "margin": 1000,
            "market_open": True,
            "is_trade_allowed": True,
            "server_time": "2026.04.13 08:00:00",
        },
        headers=USER_HEADERS,
    )
    client.post(
        "/tick",
        json={
            "account_id": ACCOUNT_ID,
            "symbol": "GOLDm#",
            "bid": 4325.92,
            "ask": 4326.42,
            "spread": 5,
            "time": "08:00:00",
        },
        headers=USER_HEADERS,
    )
    client.post(
        "/bars",
        json={
            "account_id": ACCOUNT_ID,
            "symbol": "GOLDM#",
            "timeframe": "H1",
            "bars": [
                {"time": "2026.04.13 07:00", "open": 4320, "high": 4330, "low": 4318, "close": 4325, "volume": 100}
            ],
        },
        headers=USER_HEADERS,
    )

    # workflow 触发链用大写符号(GOLDM#),EA 上报是小写 GOLDm#
    response = client.get(f"/api/v2/analysis_payload/{ACCOUNT_ID}/GOLDM%23", headers=USER_HEADERS)
    assert response.status_code == 200
    body = response.json()
    market = body.get("market") or {}
    assert market.get("bid") == 4325.92, f"tick not found via uppercase symbol: {market}"
    market_status = body.get("market_status") or {}
    assert market_status.get("market_open") is True, f"market_open wrongly false: {market_status}"
    assert market_status.get("stale") is not True
    # H1 bars(大写入库)也能被小写符号读到
    assert any(float(b.get("close", 0)) == 4325 for b in (body.get("bars") or {}).get("H1") or [])


async def test_analysis_reparses_truncated_ai_strategy() -> None:
    client, store = make_client(now_unix=lambda: 1713000000)
    await inject_ea_fixture(client, "register")
    client.post(
        "/heartbeat",
        json={
            "account_id": ACCOUNT_ID,
            "balance": 10000,
            "equity": 10000,
            "free_margin": 9000,
            "margin": 1000,
            "market_open": True,
            "is_trade_allowed": True,
            "server_time": "2026.04.13 08:00:00",
        },
        headers=USER_HEADERS,
    )
    client.post(
        "/tick",
        json={"account_id": ACCOUNT_ID, "symbol": "XAGUSD", "bid": 58.5, "ask": 58.55, "spread": 5, "time": "08:00:00"},
        headers=USER_HEADERS,
    )
    post = client.post(
        "/positions",
        json={
            "account_id": ACCOUNT_ID,
            "symbol": "XAGUSD",
            "positions": [
                {
                    "ticket": 42275433,
                    "symbol": "XAGUSD",
                    "type": "SELL_LIMIT",
                    "order_class": "pending",
                    "lots": 0.05,
                    "open_price": 59.5,
                    "strategy": "ai",
                    "comment": "GB_ai_signal_S78",
                    "profit": 0,
                    "tp": 58.36,
                    "sl": 59.5,
                }
            ],
        },
        headers=USER_HEADERS,
    )
    assert post.status_code == 200
    stored = await store.get_positions(ACCOUNT_ID)
    pending = next(p for p in stored if int(p["ticket"]) == 42275433)
    assert pending["strategy"] == "ai_signal"
    assert pending["order_class"] == "pending"
    assert pending["type"] == "SELL_LIMIT"

    response = client.get(f"/api/v2/analysis_payload/{ACCOUNT_ID}/XAGUSD", headers=USER_HEADERS)
    assert response.status_code == 200
    positions = response.json()["positions"]
    assert any(
        p.get("ticket") == 42275433
        and p.get("strategy") == "ai_signal"
        and p.get("order_class") == "pending"
        and p.get("direction") == "SELL_LIMIT"
        for p in positions
    )


async def test_ea_nested_type_mismatch_matrix() -> None:
    client, store = make_client(valid_tokens=None, token_accounts=None, admin_tokens=set())
    cases = [
        {
            "path": "/bars",
            "body": {"account_id": ACCOUNT_ID, "timeframe": "H1", "bars": [{"open": "3300"}]},
            "not_persisted": lambda: store.get_bars(ACCOUNT_ID, "XAUUSD", "H1"),
        },
        {
            "path": "/bars",
            "body": {"account_id": ACCOUNT_ID, "symbol": 123, "timeframe": "H1", "bars": []},
            "not_persisted": lambda: store.get_bars(ACCOUNT_ID, "XAUUSD", "H1"),
        },
        {
            "path": "/bars",
            "body": {"account_id": ACCOUNT_ID, "symbol": "XAUUSD", "timeframe": 60, "bars": []},
            "not_persisted": lambda: store.get_bars(ACCOUNT_ID, "XAUUSD", ""),
        },
        {
            "path": "/bars",
            "body": {"account_id": ACCOUNT_ID, "timeframe": "H1", "bars": [{"volume": 10.5}]},
            "not_persisted": lambda: store.get_bars(ACCOUNT_ID, "XAUUSD", "H1"),
        },
        {
            "path": "/bars",
            "body": {"account_id": ACCOUNT_ID, "timeframe": "H1", "bars": [{"macd_divergence": 123}]},
            "not_persisted": lambda: store.get_bars(ACCOUNT_ID, "XAUUSD", "H1"),
        },
        {
            "path": "/bars",
            "body": {"account_id": ACCOUNT_ID, "timeframe": "H1", "bars": [{"candlestick_patterns": ["hammer", 123]}]},
            "not_persisted": lambda: store.get_bars(ACCOUNT_ID, "XAUUSD", "H1"),
        },
        {
            "path": "/positions",
            "body": {"account_id": ACCOUNT_ID, "symbol": 123, "positions": []},
            "not_persisted": lambda: store.get_positions(ACCOUNT_ID),
        },
        {
            "path": "/positions",
            "body": {"account_id": ACCOUNT_ID, "positions": [{"ticket": "123"}]},
            "not_persisted": lambda: store.get_positions(ACCOUNT_ID),
        },
        {
            "path": "/positions",
            "body": {"account_id": ACCOUNT_ID, "positions": [{"ticket": 123.45}]},
            "not_persisted": lambda: store.get_positions(ACCOUNT_ID),
        },
        {
            "path": "/positions",
            "body": {"account_id": ACCOUNT_ID, "positions": [{"open_time": 1712971200.5}]},
            "not_persisted": lambda: store.get_positions(ACCOUNT_ID),
        },
        {
            "path": "/positions",
            "body": {"account_id": ACCOUNT_ID, "positions": [{"magic": 20250238.5}]},
            "not_persisted": lambda: store.get_positions(ACCOUNT_ID),
        },
        {
            "path": "/register",
            "body": {"account_id": ACCOUNT_ID, "strategy_mapping": {"20250231": 123}},
            "not_persisted": lambda: store.get_registration(ACCOUNT_ID),
        },
        {
            "path": "/order_result",
            "body": {"account_id": ACCOUNT_ID, "command_id": "cmd_1", "result": "filled", "ticket": "321"},
            "not_persisted": lambda: store.get_order_results(ACCOUNT_ID),
        },
        {
            "path": "/order_result",
            "body": {"account_id": ACCOUNT_ID, "command_id": "cmd_1", "result": "filled", "ticket": 321.5},
            "not_persisted": lambda: store.get_order_results(ACCOUNT_ID),
        },
        {
            "path": "/order_result",
            "body": {"account_id": ACCOUNT_ID, "command_id": "cmd_1", "result": "filled", "error": 500},
            "not_persisted": lambda: store.get_order_results(ACCOUNT_ID),
        },
    ]
    for case in cases:
        response = client.post(case["path"], json=case["body"])
        assert response.status_code == 400, case["path"]
        assert response.json() == {"status": "ERROR", "message": "invalid JSON"}, case["path"]
        assert await case["not_persisted"]() == [] or await case["not_persisted"]() is None, case["path"]


async def test_ea_order_result_reconcile_delivered_command() -> None:
    client, store = make_client(
        now_iso=lambda: "2026-04-13T08:01:00.000Z", valid_tokens=None, token_accounts=None, admin_tokens=set()
    )
    delivered = await store.save_command_candidate(
        ACCOUNT_ID,
        {
            "command_id": "sig_route_failed",
            "source": "ai_result",
            "symbol": "XAUUSD",
            "action": "SIGNAL",
            "strategy": "ai_signal",
            "decision_id": "tpv1_route_failed",
        },
    )
    queued = await store.save_command_candidate(
        ACCOUNT_ID,
        {
            "command_id": "sig_route_pending",
            "source": "ai_result",
            "symbol": "XAUUSD",
            "action": "SIGNAL",
            "strategy": "ai_signal",
            "decision_id": "tpv1_route_pending",
        },
    )
    await store.promote_command(delivered["command_id"])
    await store.promote_command(queued["command_id"])
    polled = await store.poll_commands(ACCOUNT_ID)
    assert delivered["command_id"] in [c["command_id"] for c in polled]

    response = client.post(
        "/order_result",
        json={
            "account_id": ACCOUNT_ID,
            "command_id": delivered["command_id"],
            "result": "ERROR",
            "ticket": 0,
            "error": "invalid stops",
        },
    )
    assert response.status_code == 200
    assert response.json() == {"status": "OK"}
    command = await store.get_command(delivered["command_id"])
    assert command is not None
    assert command["status"] == "failed"
    assert command["result"] == "ERROR"
    assert command["ticket"] == 0
    assert command["failed_at"] == "2026-04-13T08:01:00.000Z"
    assert command["error_text"] == "invalid stops"
    order_results = await store.get_order_results(ACCOUNT_ID)
    assert order_results == [
        {
            "account_id": ACCOUNT_ID,
            "command_id": delivered["command_id"],
            "result": "ERROR",
            "ticket": 0,
            "error_text": "invalid stops",
            "created_at": "2026-04-13T08:01:00.000Z",
        }
    ]
    events = await store.list_decision_events(
        {"account_id": ACCOUNT_ID, "symbol": "XAUUSD", "status": "failed"}
    )
    assert any(
        e.get("decision_id") == "tpv1_route_failed"
        and e.get("stage") == "order_result"
        and e.get("related_command_id") == delivered["command_id"]
        for e in events
    )

    duplicate = client.post(
        "/order_result",
        json={
            "account_id": ACCOUNT_ID,
            "command_id": delivered["command_id"],
            "result": "OK",
            "ticket": 123,
            "error": "",
        },
    )
    missing = client.post(
        "/order_result",
        json={"account_id": ACCOUNT_ID, "command_id": "sig_route_missing", "result": "OK", "ticket": 123, "error": ""},
    )
    assert duplicate.status_code == 200
    assert missing.status_code == 200
    assert len(await store.get_order_results(ACCOUNT_ID)) == 1
    pending_cmd = await store.get_command("sig_route_pending")
    assert pending_cmd is not None and pending_cmd["status"] == "delivered"


async def test_ea_order_result_required_fields() -> None:
    client, store = make_client(valid_tokens=None, token_accounts=None, admin_tokens=set())
    cases = [
        ({"account_id": ACCOUNT_ID, "result": "filled"}, "missing command_id"),
        ({"account_id": ACCOUNT_ID, "command_id": "cmd_1"}, "missing result"),
        ({"account_id": ACCOUNT_ID, "command_id": "   ", "result": "filled"}, "missing command_id"),
        ({"account_id": ACCOUNT_ID, "command_id": "cmd_1", "result": "   "}, "missing result"),
    ]
    for body, message in cases:
        response = client.post("/order_result", json=body)
        assert response.status_code == 400
        assert response.json() == {"status": "ERROR", "message": message}
    assert await store.get_order_results(ACCOUNT_ID) == []


async def test_ea_get_poll_accepts_non_post_method() -> None:
    client, _store = make_client(valid_tokens=None, token_accounts=None, admin_tokens=set())
    response = client.request("GET", "/poll", json={"account_id": ACCOUNT_ID})
    assert response.status_code == 200
    assert response.json() == {"status": "OK", "commands": [], "count": 0}


# ---------------------------------------------------------------- Group D:shadow


async def test_shadow_metrics_from_persisted_comparisons() -> None:
    client, store = make_client(now_iso=lambda: "2026-07-03T00:10:00.000Z")
    await store.record_shadow_comparison(
        {
            "account_id": ACCOUNT_ID,
            "symbol": "XAUUSD",
            "protocol_ok": True,
            "signal_drift": False,
            "command_drift": True,
            "oracle_compared": True,
            "source": "ai_result",
            "created_at": "2026-07-03T00:00:00.000Z",
        }
    )
    await store.record_shadow_comparison(
        {
            "account_id": ACCOUNT_ID,
            "symbol": "XAUUSD",
            "protocol_ok": False,
            "signal_drift": True,
            "command_drift": False,
            "oracle_compared": True,
            "source": "ea_analysis",
            "created_at": "2026-07-03T00:05:00.000Z",
        }
    )
    response = client.get("/shadow/metrics")
    assert response.status_code == 200
    assert response.json() == {
        "status": "OK",
        "generated_at": "2026-07-03T00:10:00.000Z",
        "report": {
            "ready": False,
            "protocol_error_rate": 0.5,
            "signal_drift_rate": 0.5,
            "command_drift_rate": 0.5,
            "replay_coverage": 0,
            "last_shadow_event_at": "2026-07-03T00:05:00.000Z",
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
                    "detail": "Signal 50.00%, command 50.00% (limit 2.00%)",
                    "tone": "red",
                },
                {
                    "label": "Protocol Errors",
                    "value": "50.00%",
                    "detail": "Legacy contract mismatches detected in mirrored traffic",
                    "tone": "red",
                },
                {
                    "label": "Replay Coverage",
                    "value": "pending",
                    "detail": "Replay fixture set has not been scanned yet",
                    "tone": "amber",
                },
            ],
        },
        "totals": {"comparisons": 2, "protocol_errors": 1, "signal_drifts": 1, "command_drifts": 1},
    }


async def test_shadow_qualification_cutover_style_checks() -> None:
    client, store = make_client(now_iso=lambda: "2026-07-03T00:10:00.000Z")
    await store.record_shadow_comparison(
        {
            "account_id": ACCOUNT_ID,
            "symbol": "XAUUSD",
            "protocol_ok": True,
            "signal_drift": False,
            "command_drift": False,
            "oracle_compared": True,
            "source": "ea_analysis",
            "created_at": "2026-07-03T00:00:00.000Z",
        }
    )
    response = client.get("/shadow/qualification")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "OK"
    assert body["generated_at"] == "2026-07-03T00:10:00.000Z"
    assert body["report"]["ready"] is False
    assert body["report"]["protocol_error_rate"] == 0
    assert body["report"]["signal_drift_rate"] == 0
    assert body["report"]["command_drift_rate"] == 0
    assert body["report"]["last_shadow_event_at"] == "2026-07-03T00:00:00.000Z"
    assert body["report"]["checks"][1] == {
        "label": "Shadow Drift",
        "value": "within threshold",
        "detail": "Signal 0.00%, command 0.00%",
        "tone": "green",
    }
    assert body["totals"] == {"comparisons": 1, "protocol_errors": 0, "signal_drifts": 0, "command_drifts": 0}
    assert body["summary"][1] == {
        "label": "Shadow Drift",
        "value": "within threshold",
        "detail": "Signal 0.00%, command 0.00%",
        "tone": "green",
    }


async def test_shadow_comparisons_post_with_node_and_oracle() -> None:
    client, store = make_client(now_iso=lambda: "2026-07-03T00:10:00.000Z")
    response = client.post(
        "/shadow/comparisons",
        json={
            "account_id": ACCOUNT_ID,
            "symbol": "XAUUSD",
            "source": "ea_analysis",
            "protocol_ok": True,
            "node": {
                "signal": {"strategy": "pullback", "side": "BUY", "entry": 3335.7},
                "command": {"action": "SIGNAL", "strategy": "pullback", "tp1": 3345},
            },
            "oracle": {
                "signal": {"strategy": "pullback", "side": "BUY", "entry": 3335.7},
                "command": {"action": "SIGNAL", "strategy": "pullback", "tp1": 3350},
            },
        },
    )
    assert response.status_code == 200
    assert response.json() == {
        "status": "OK",
        "comparison": {
            "account_id": ACCOUNT_ID,
            "symbol": "XAUUSD",
            "protocol_ok": True,
            "signal_drift": False,
            "command_drift": True,
            "oracle_compared": True,
            "source": "ea_analysis",
            "created_at": "2026-07-03T00:10:00.000Z",
        },
    }
    stored = await store.list_shadow_comparisons()
    assert stored == [
        {
            "account_id": ACCOUNT_ID,
            "symbol": "XAUUSD",
            "protocol_ok": True,
            "signal_drift": False,
            "command_drift": True,
            "oracle_compared": True,
            "source": "ea_analysis",
            "created_at": "2026-07-03T00:10:00.000Z",
        }
    ]


async def test_shadow_comparisons_uses_latest_snapshot_when_node_omitted() -> None:
    client, store = make_client(now_iso=lambda: "2026-07-03T00:10:00.000Z")
    await store.save_shadow_snapshot(
        {
            "account_id": ACCOUNT_ID,
            "symbol": "XAUUSD",
            "source": "ea_analysis",
            "signal": {"strategy": "pullback", "side": "BUY", "entry": 3335.7},
            "command": {"action": "SIGNAL", "strategy": "pullback", "tp1": 3345},
            "created_at": "2026-07-03T00:00:00.000Z",
        }
    )
    response = client.post(
        "/shadow/comparisons",
        json={
            "account_id": ACCOUNT_ID,
            "symbol": "XAUUSD",
            "source": "ea_analysis",
            "oracle": {
                "signal": {"strategy": "pullback", "side": "BUY", "entry": 3335.7},
                "command": {"action": "SIGNAL", "strategy": "pullback", "tp1": 3350},
            },
        },
    )
    assert response.status_code == 200
    assert response.json()["comparison"] == {
        "account_id": ACCOUNT_ID,
        "symbol": "XAUUSD",
        "protocol_ok": True,
        "signal_drift": False,
        "command_drift": True,
        "oracle_compared": True,
        "source": "ea_analysis",
        "created_at": "2026-07-03T00:10:00.000Z",
    }


async def test_shadow_comparisons_rejects_invalid_payload() -> None:
    client, _store = make_client()
    response = client.post(
        "/shadow/comparisons",
        json={"account_id": ACCOUNT_ID, "symbol": "", "node": {}, "oracle": {}},
    )
    assert response.status_code == 400
    assert response.json() == {"status": "ERROR", "message": "invalid shadow comparison payload"}


# ---------------------------------------------------------------- Group E:analysis_payload 基础(fixture)


async def test_analysis_ai_result_fixture_flow() -> None:
    client, store = make_client()
    for name in ["register", "heartbeat", "tick", "bars", "positions"]:
        await inject_ea_fixture(client, name)

    def assert_subset_contains(actual: dict, expected: dict) -> None:
        """镜像 jest toMatchObject:expected 的每个键/子键都必须出现在 actual 中且相等。"""
        for key, value in expected.items():
            assert key in actual, (key, expected, actual)
            if isinstance(value, dict) and isinstance(actual[key], dict):
                assert_subset_contains(actual[key], value)
            elif isinstance(value, list) and isinstance(actual[key], list):
                assert len(actual[key]) == len(value), (key, expected, actual)
                for index, item in enumerate(value):
                    if isinstance(item, dict) and isinstance(actual[key][index], dict):
                        assert_subset_contains(actual[key][index], item)
                    else:
                        assert actual[key][index] == item, (key, expected, actual)
            else:
                assert actual[key] == value, (key, expected, actual)

    analysis = read_admin_fixture("analysis-payload")
    analysis_request = analysis["request"]
    response = client.request(
        analysis_request["method"], analysis_request["path"], headers=analysis_request["headers"]
    )
    assert response.status_code == 200
    expected = analysis["response"]["body"]
    body = response.json()
    assert_subset_contains(body, {
        "account": expected["account"],
        "market": expected["market"],
        "positions": expected["positions"],
        "status": expected["status"],
        "timestamp": expected["timestamp"],
    })
    assert "H1" in body["indicators"]

    analysis_v2 = read_admin_fixture("analysis-payload-v2")
    v2_request = analysis_v2["request"]
    response2 = client.request(v2_request["method"], v2_request["path"], headers=v2_request["headers"])
    assert response2.status_code == 200
    body2 = response2.json()
    assert_subset_contains(body2, {
        "account": expected["account"],
        "market": expected["market"],
        "positions": expected["positions"],
        "status": expected["status"],
        "timestamp": expected["timestamp"],
    })

    for name in ["ai-result", "ai-result-v2-trade-plan"]:
        fixture = read_admin_fixture(name)
        req = fixture["request"]
        response3 = client.request(req["method"], req["path"], json=req.get("body"), headers=req["headers"])
        assert response3.status_code == 200, name
        if name == "ai-result-v2-trade-plan":
            body3 = response3.json()
            for key, value in fixture["response"]["body"].items():
                assert key in body3
                if isinstance(value, dict):
                    for k2, v2 in value.items():
                        assert body3[key].get(k2) == v2, (key, k2)
                else:
                    assert body3[key] == value, key
            assert "command_status" not in body3
            assert body3["risk_gate"]["audit_only"] is False
            assert body3["risk_gate"]["canProduceLiveCommands"] is False
        else:
            assert response3.json() == fixture["response"]["body"]

    assert len(await store.get_ai_results(ACCOUNT_ID)) == 1
    polled = await store.poll_commands(ACCOUNT_ID)
    assert any(
        c.get("action") == "CLOSE_ALL" and c.get("reason") == "AI风险警报(全平): volatility spike" for c in polled
    )


async def test_analysis_positions_filtered_by_symbol() -> None:
    client, store = make_client()
    await store.save_positions(
        {
            "account_id": ACCOUNT_ID,
            "symbol": "XAUUSD",
            "positions": [{"ticket": 1001, "symbol": "XAUUSD", "type": "BUY", "lots": 0.1}],
        }
    )
    await store.save_positions(
        {
            "account_id": ACCOUNT_ID,
            "symbol": "GBPJPY",
            "positions": [{"ticket": 2002, "symbol": "GBPJPY", "type": "SELL", "lots": 0.2}],
        }
    )
    response = client.get(f"/api/analysis_payload/{ACCOUNT_ID}", headers=USER_HEADERS)
    assert response.status_code == 200
    positions = response.json()["positions"]
    assert len(positions) == 1
    assert positions[0]["ticket"] == 1001


async def test_analysis_position_fields_and_timestamp() -> None:
    client, store = make_client(now_iso=lambda: "2026-04-13T08:00:00.000Z")
    # open_time(epoch 秒,与 now=2026-04-13T08:00:00Z=1776067200 对齐):
    # 07:00:00Z = 1776063600,07:58:30Z = 1776067110
    await store.save_tick(
        {"account_id": ACCOUNT_ID, "symbol": "XAUUSD", "bid": 109.8, "ask": 110, "time": "2026-04-13T08:00:00.000Z"}
    )
    await store.save_positions(
        {
            "account_id": ACCOUNT_ID,
            "symbol": "XAUUSD",
            "positions": [
                {
                    "ticket": 1001,
                    "symbol": "XAUUSD",
                    "type": "buy",
                    "lots": 1,
                    "open_price": 100,
                    "profit": 10,
                    "open_time": 1776063600,
                },
                {
                    "ticket": 1002,
                    "symbol": "XAUUSD",
                    "type": "sell",
                    "lots": 1,
                    "open_price": 100,
                    "profit": 0,
                    "open_time": 1776067110,
                },
            ],
        }
    )
    response = client.get(f"/api/analysis_payload/{ACCOUNT_ID}", headers=USER_HEADERS)
    assert response.status_code == 200
    body = response.json()
    assert body["timestamp"] == "2026-04-13T16:00:00+08:00"
    assert body["positions"][0]["direction"] == "BUY"
    assert body["positions"][0]["hold_hours"] == 1
    assert body["positions"][0]["hold_seconds"] == 3600
    assert body["positions"][0]["pnl_percent"] == 10
    assert body["positions"][1]["direction"] == "SELL"
    assert body["positions"][1]["hold_hours"] == 0.02
    assert body["positions"][1]["hold_seconds"] == 90


async def test_analysis_bars_capped_indicators_full_count() -> None:
    client, store = make_client(now_iso=lambda: "2026-04-13T08:00:00.000Z")
    bars = [
        {"time": f"bar-{index}", "open": 2000.0, "high": 2001.0, "low": 1999.0, "close": 2000.0, "volume": 1000}
        for index in range(1001)
    ]
    await store.save_bars({"account_id": ACCOUNT_ID, "symbol": "XAUUSD", "timeframe": "H1", "bars": bars})
    response = client.get(f"/api/analysis_payload/{ACCOUNT_ID}", headers=USER_HEADERS)
    assert response.status_code == 200
    body = response.json()
    assert len(body["bars"]["H1"]) == 1000
    assert body["bars"]["H1"][0]["time"] == "bar-1"
    assert body["bars"]["H1"][-1]["time"] == "bar-1000"
    assert body["indicators"]["H1"]["bars_count"] == 1001


# ---------------------------------------------------------------- Group F:ai_result 进阶


async def test_ai_result_legacy_close_all_risk_alert() -> None:
    client, store = make_client()
    response = client.post(
        f"/api/ai_result/{ACCOUNT_ID}",
        json={
            "combined_bias": "bearish",
            "confidence": 87,
            "reasoning": "risk regime changed",
            "exit_suggestion": "close_all",
            "risk_alert": True,
            "alert_reason": "volatility spike",
        },
        headers=USER_HEADERS,
    )
    assert response.status_code == 200
    assert response.json() == {"status": "OK", "received": True}

    poll = client.post("/poll", json={"account_id": ACCOUNT_ID}, headers=USER_HEADERS)
    body = poll.json()
    assert body["count"] == 1
    command = body["commands"][0]
    assert command["action"] == "CLOSE_ALL"
    assert command["reason"] == "AI风险警报(全平): volatility spike"
    assert command["confidence"] == 87
    assert command["source"] == "ai_risk_alert"
    assert "symbol" not in command


async def test_ai_result_legacy_close_short_only_matching_sells() -> None:
    # 对齐 TS nowIso:() => '2026-04-13T16:00:00+08:00' → 1776067200000ms
    client, store = make_client(now_iso=lambda: "2026-04-13T08:00:00.000Z")
    await store.save_positions(
        {
            "account_id": ACCOUNT_ID,
            "positions": [
                {"ticket": 111001, "symbol": "XAUUSD", "type": "BUY", "lots": 0.1},
                {"ticket": 222002, "symbol": "XAUUSD", "type": "SELL", "lots": 0.1},
                {"ticket": 333003, "symbol": "XAUUSD", "type": "SELL", "lots": 0.2},
                {"ticket": 444004, "symbol": "GBPJPY", "type": "SELL", "lots": 0.2},
            ],
        }
    )
    response = client.post(
        f"/api/ai_result/{ACCOUNT_ID}",
        json={
            "combined_bias": "bullish",
            "confidence": 84,
            "reasoning": "short exposure invalidated",
            "exit_suggestion": "close_short",
            "risk_alert": True,
            "alert_reason": "多周期强bullish共振",
        },
        headers=USER_HEADERS,
    )
    assert response.status_code == 200

    poll = client.post("/poll", json={"account_id": ACCOUNT_ID}, headers=USER_HEADERS)
    body = poll.json()
    assert body["count"] == 2
    assert sorted(c["ticket"] for c in body["commands"]) == [222002, 333003]
    for command in body["commands"]:
        assert command["action"] == "CLOSE"
        assert command["reason"] == "AI风险警报(平空): 多周期强bullish共振"
        assert command["source"] == "ai_risk_alert"
        assert re.match(r"^ai_close_1776067200000000000_\d+$", str(command["command_id"])) is not None


async def test_ai_result_v2_risk_command_metadata() -> None:
    from backend.observability.sse import create_sse_hub

    store = create_in_memory_store()
    events = create_sse_hub()
    published: list[dict] = []
    events.subscribe(published.append)
    client, _store = make_client(store=store, events=events)

    await store.save_registration({"account_id": ACCOUNT_ID, "leverage": 500, "ai_symbols": ["XAUUSD"]})
    await store.save_heartbeat(
        {
            "account_id": ACCOUNT_ID,
            "equity": 10000,
            "free_margin": 9000,
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
            "time": "2026-04-13T15:59:30+08:00",
        }
    )
    response = client.post(
        f"/api/v2/ai_result/{ACCOUNT_ID}/XAUUSD",
        json={
            "bias": "bearish",
            "confidence": 87,
            "reasoning": "risk regime changed",
            "exit_suggestion": "close_all",
            "risk_alert": True,
            "alert_reason": "volatility spike",
            "trade_plan": {
                "schema_version": "trade_plan.v1",
                "decision_id": "tpv1_close_all",
                "account_id": ACCOUNT_ID,
                "symbol": "XAUUSD",
                "mode": "close",
                "side": "buy",
                "confidence": 87,
                "entry_zone": {"min": 3335.55, "max": 3335.75},
                "stop_loss": 3328,
                "take_profit": [3350],
                "max_lots": 0.1,
                "expires_at": "2099-06-06T09:15:00Z",
                "reason_codes": ["mode.close", "risk.high"],
                "narrative": "AI requests full close after risk spike",
            },
        },
        headers=USER_HEADERS,
    )
    assert response.status_code == 200
    assert response.json()["risk_gate"]["status"] == "accepted"

    assert any(
        e.get("event_id") == "evt_ai_1776067200000000000"
        and e.get("event_type") == "ai_result"
        and e.get("account_id") == ACCOUNT_ID
        and e.get("source") == "api.ai_result"
        and e.get("timestamp") == "2026-04-13T08:00:00.000Z"
        and e["payload"].get("trade_plan_summary")
        == {"decision_id": "tpv1_close_all", "mode": "close", "symbol": "XAUUSD", "confidence": 87}
        and e["payload"].get("risk_gate", {}).get("status") == "accepted"
        for e in published
    )

    events = await store.list_decision_events({"account_id": ACCOUNT_ID, "symbol": "XAUUSD"})
    assert any(
        e.get("decision_id") == "tpv1_close_all"
        and e.get("stage") == "command_enqueued"
        and e.get("status") == "pending"
        and e.get("reason_codes") == ["command.CLOSE_ALL", "source.ai_risk_alert"]
        and e.get("summary", {}).get("action") == "CLOSE_ALL"
        for e in events
    )
    assert any(
        e.get("decision_id") == "tpv1_close_all"
        and e.get("stage") == "risk_gate"
        and e.get("status") == "accepted"
        and "action.audit_safe" in e.get("reason_codes", [])
        and e.get("summary", {}).get("status") == "accepted"
        and e.get("summary", {}).get("mode") == "close"
        and e.get("summary", {}).get("symbol") == "XAUUSD"
        for e in events
    )
    assert any(
        e.get("decision_id") == "tpv1_close_all"
        and e.get("stage") == "ai_result"
        and e.get("status") == "accepted"
        and e.get("reason_codes") == ["mode.close", "risk.high"]
        and e.get("summary", {}).get("mode") == "close"
        and e.get("summary", {}).get("symbol") == "XAUUSD"
        and e.get("summary", {}).get("confidence") == 87
        for e in events
    )

    poll = client.post("/poll", json={"account_id": ACCOUNT_ID}, headers=USER_HEADERS)
    body = poll.json()
    assert body["count"] == 1
    command = body["commands"][0]
    assert command["action"] == "CLOSE_ALL"
    assert command["source"] == "ai_risk_alert"
    assert command["decision_id"] == "tpv1_close_all"
    assert command["trade_plan_mode"] == "close"
    # 镜像 toMatchObject:risk_gate 是完整 gate 子集,status 必须为 accepted
    assert command["risk_gate"]["status"] == "accepted"
    assert "symbol" not in command


async def test_ai_result_v2_gate_reject_no_queue() -> None:
    client, store = make_client()
    await store.save_registration({"account_id": ACCOUNT_ID, "leverage": 500, "ai_symbols": ["XAUUSD"]})
    await store.save_heartbeat(
        {
            "account_id": ACCOUNT_ID,
            "equity": 10000,
            "free_margin": 9000,
            "market_open": False,
            "is_trade_allowed": True,
        }
    )
    await store.save_tick(
        {
            "account_id": ACCOUNT_ID,
            "symbol": "XAUUSD",
            "bid": 3335.55,
            "ask": 3335.75,
            "spread": 8.1,
            "time": "2026-04-13T15:59:30+08:00",
        }
    )
    response = client.post(
        f"/api/v2/ai_result/{ACCOUNT_ID}/XAUUSD",
        json={
            "bias": "bearish",
            "confidence": 87,
            "reasoning": "risk regime changed",
            "exit_suggestion": "close_all",
            "risk_alert": True,
            "alert_reason": "volatility spike",
            "trade_plan": {
                "schema_version": "trade_plan.v1",
                "decision_id": "tpv1_rejected_spread",
                "account_id": ACCOUNT_ID,
                "symbol": "XAUUSD",
                "mode": "close",
                "side": "buy",
                "confidence": 87,
                "entry_zone": {"min": 3335.55, "max": 3335.75},
                "stop_loss": 3328,
                "take_profit": [3350],
                "max_lots": 0.1,
                "expires_at": "2099-06-06T09:15:00Z",
                "reason_codes": ["mode.close", "risk.high"],
                "narrative": "AI requests full close after risk spike",
            },
        },
        headers=USER_HEADERS,
    )
    assert response.status_code == 200
    risk_gate = response.json()["risk_gate"]
    assert risk_gate["status"] == "rejected"
    assert "market.closed" in risk_gate["reason_codes"]

    poll = client.post("/poll", json={"account_id": ACCOUNT_ID}, headers=USER_HEADERS)
    assert poll.json()["count"] == 0
    assert poll.json()["commands"] == []


async def test_ai_result_shadow_mode_stores_but_does_not_queue() -> None:
    client, store = make_client()
    await store.set_runtime_mode(ACCOUNT_ID, "shadow")
    await store.save_registration({"account_id": ACCOUNT_ID, "leverage": 500, "ai_symbols": ["XAUUSD"]})
    await store.save_heartbeat(
        {
            "account_id": ACCOUNT_ID,
            "equity": 10000,
            "free_margin": 9000,
            "market_open": True,
            "is_trade_allowed": True,
        }
    )
    await store.save_tick(
        {
            "account_id": ACCOUNT_ID,
            "symbol": "XAUUSD",
            "bid": 3335.5,
            "ask": 3335.7,
            "spread": 0.2,
            "time": "2026-04-13T15:59:30+08:00",
        }
    )
    await seed_ai_approve_trend_bars(store)

    response = client.post(
        f"/api/v2/ai_result/{ACCOUNT_ID}/XAUUSD",
        json={
            "trade_plan": {
                "schema_version": "trade_plan.v1",
                "decision_id": "tpv1_shadow_mode",
                "account_id": ACCOUNT_ID,
                "symbol": "XAUUSD",
                "mode": "approve",
                "side": "buy",
                "entry_zone": {"min": 3335.5, "max": 3335.7},
                "execution_type": "market",
                "requested_order_type": "market",
                "stop_loss": 3330,
                "take_profit": [3345],
                "max_lots": 0.1,
                "confidence": 80,
                "expires_at": "2099-06-06T09:15:00Z",
                "reason_codes": ["mode.approve", "side.buy"],
                "narrative": "shadow mode should store but not queue",
            },
        },
        headers=USER_HEADERS,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["command_status"] == "shadow_only"
    assert body["risk_gate"]["status"] == "accepted"
    assert body["risk_gate"]["audit_only"] is False
    assert body["risk_gate"]["canProduceLiveCommands"] is False

    commands = await store.list_commands(ACCOUNT_ID)
    assert len(commands) == 1
    command = commands[0]
    assert command["action"] == "SIGNAL"
    assert command["source"] == "ai_approve"
    assert command["status"] == "shadow_only"
    assert command["decision_id"] == "tpv1_shadow_mode"
    assert command["type"] == "BUY"
    assert await store.poll_commands(ACCOUNT_ID) == []
    comparisons = await store.list_shadow_comparisons()
    assert any(
        c.get("account_id") == ACCOUNT_ID
        and c.get("symbol") == "XAUUSD"
        and c.get("source") == "ai_result"
        and c.get("command_drift") is False
        for c in comparisons
    )
    snapshot = await store.get_latest_shadow_snapshot(ACCOUNT_ID, "XAUUSD", "ai_result")
    assert snapshot is not None
    assert snapshot["account_id"] == ACCOUNT_ID
    assert snapshot["symbol"] == "XAUUSD"
    assert snapshot["source"] == "ai_result"
    assert snapshot["command"]["decision_id"] == "tpv1_shadow_mode"
    assert snapshot["command"]["status"] == "shadow_only"
    assert snapshot["command"]["risk_gate"]["audit_only"] is False
    assert snapshot["command"]["risk_gate"]["canProduceLiveCommands"] is False


async def test_ai_result_confidence_65_cutover_queues() -> None:
    client, store = make_client()
    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    now_iso = _now_iso_from_ms(now_ms)
    client.app.state.now_iso = lambda: now_iso

    await store.set_runtime_mode(ACCOUNT_ID, "cutover")
    await store.save_registration({"account_id": ACCOUNT_ID, "leverage": 500, "ai_symbols": ["XAUUSD"]})
    await store.save_heartbeat(
        {
            "account_id": ACCOUNT_ID,
            "equity": 10000,
            "free_margin": 9000,
            "market_open": True,
            "is_trade_allowed": True,
        }
    )
    await store.save_tick(
        {
            "account_id": ACCOUNT_ID,
            "symbol": "XAUUSD",
            "bid": 3335.5,
            "ask": 3335.7,
            "spread": 0.2,
            "time": _now_iso_from_ms(now_ms - 30_000),
        }
    )
    await seed_ai_approve_trend_bars(store)

    response = client.post(
        f"/api/v2/ai_result/{ACCOUNT_ID}/XAUUSD",
        json={
            "trade_plan": {
                "schema_version": "trade_plan.v1",
                "decision_id": "tpv1_cutover_mode",
                "account_id": ACCOUNT_ID,
                "symbol": "XAUUSD",
                "mode": "approve",
                "side": "buy",
                "entry_zone": {"min": 3335.5, "max": 3335.7},
                "execution_type": "market",
                "requested_order_type": "market",
                "stop_loss": 3330,
                "take_profit": [3345],
                "max_lots": 0.1,
                "confidence": 65,
                "expires_at": "2099-06-06T09:15:00Z",
                "reason_codes": ["mode.approve", "side.buy"],
                "narrative": "cutover mode may queue after deterministic and pending gates",
            },
        },
        headers=USER_HEADERS,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["command_status"] == "queued"
    assert body["risk_gate"]["status"] == "accepted"
    assert body["risk_gate"]["audit_only"] is False

    commands = await store.list_commands(ACCOUNT_ID)
    assert len(commands) == 1
    command = commands[0]
    assert command["action"] == "SIGNAL"
    assert command["source"] == "ai_approve"
    assert command["status"] == "queued"
    assert command["decision_id"] == "tpv1_cutover_mode"
    assert command["type"] == "BUY"
    assert command["confidence"] == 65
    assert command["score"] == 65

    polled = await store.poll_commands(ACCOUNT_ID)
    assert len(polled) == 1
    assert polled[0]["action"] == "SIGNAL"
    assert polled[0]["source"] == "ai_approve"
    assert polled[0]["decision_id"] == "tpv1_cutover_mode"
    assert polled[0]["type"] == "BUY"
    assert polled[0]["confidence"] == 65
    assert polled[0]["score"] == 65


async def test_ai_result_confidence_64_queue_skip() -> None:
    client, store = make_client()
    await store.set_runtime_mode(ACCOUNT_ID, "cutover")
    await store.save_registration({"account_id": ACCOUNT_ID, "leverage": 500})
    await store.save_heartbeat(
        {
            "account_id": ACCOUNT_ID,
            "equity": 10000,
            "free_margin": 9000,
            "market_open": True,
            "is_trade_allowed": True,
        }
    )
    await store.save_tick(
        {
            "account_id": ACCOUNT_ID,
            "symbol": "XAUUSD",
            "bid": 3335.5,
            "ask": 3335.7,
            "spread": 0.2,
            "time": "2026-04-13T15:59:30+08:00",
        }
    )
    await seed_ai_approve_trend_bars(store)

    response = client.post(
        f"/api/v2/ai_result/{ACCOUNT_ID}/XAUUSD",
        json={
            "trade_plan": {
                "schema_version": "trade_plan.v1",
                "decision_id": "tpv1_low_confidence",
                "account_id": ACCOUNT_ID,
                "symbol": "XAUUSD",
                "mode": "approve",
                "side": "buy",
                "entry_zone": {"min": 3335.5, "max": 3335.7},
                "execution_type": "market",
                "requested_order_type": "market",
                "stop_loss": 3330,
                "take_profit": [3345],
                "max_lots": 0.1,
                "confidence": 64,
                "expires_at": "2099-06-06T09:15:00Z",
                "reason_codes": ["mode.approve", "side.buy"],
                "narrative": "otherwise valid approve below live confidence threshold",
            },
        },
        headers=USER_HEADERS,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["risk_gate"]["status"] == "accepted"
    assert body["risk_gate"]["audit_only"] is False
    assert "command_status" not in body
    assert await store.list_commands(ACCOUNT_ID) == []
    assert await store.poll_commands(ACCOUNT_ID) == []
    events = await store.list_decision_events({"account_id": ACCOUNT_ID, "symbol": "XAUUSD"})
    assert any(
        e.get("decision_id") == "tpv1_low_confidence"
        and e.get("stage") == "risk_gate"
        and e.get("status") == "rejected"
        and "pending_gate.queue_skip.confidence_below_min" in e.get("reason_codes", [])
        and e.get("summary", {}).get("pending_gate_reason") == "queue_skip.confidence_below_min"
        and e.get("summary", {}).get("mode") == "approve"
        and e.get("summary", {}).get("symbol") == "XAUUSD"
        for e in events
    )


async def test_ai_result_stop_intents_cutover_no_queue() -> None:
    stop_intents = [
        {
            "side": "buy",
            "requested_order_type": "BUY_STOP",
            "entry": 3338,
            "stop_loss": 3332,
            "take_profit": 3350,
            "narrative": "breakout chase disabled",
        },
        {
            "side": "sell",
            "requested_order_type": "SELL_STOP",
            "entry": 3332,
            "stop_loss": 3338,
            "take_profit": 3320,
            "narrative": "breakdown chase disabled",
        },
    ]
    for intent in stop_intents:
        client, store = make_client()
        await store.set_runtime_mode(ACCOUNT_ID, "cutover")
        await store.save_registration({"account_id": ACCOUNT_ID, "leverage": 500})
        await store.save_heartbeat(
            {
                "account_id": ACCOUNT_ID,
                "equity": 10000,
                "free_margin": 9000,
                "market_open": True,
                "is_trade_allowed": True,
            }
        )
        await store.save_tick(
            {
                "account_id": ACCOUNT_ID,
                "symbol": "XAUUSD",
                "bid": 3335.5,
                "ask": 3335.7,
                "spread": 0.2,
                "time": "2026-04-13T15:59:30+08:00",
            }
        )
        await seed_ai_approve_trend_bars(store)

        response = client.post(
            f"/api/v2/ai_result/{ACCOUNT_ID}/XAUUSD",
            json={
                "trade_plan": {
                    "schema_version": "trade_plan.v1",
                    "decision_id": f"tpv1_{intent['requested_order_type'].lower()}_disabled",
                    "account_id": ACCOUNT_ID,
                    "symbol": "XAUUSD",
                    "mode": "approve",
                    "side": intent["side"],
                    "entry_zone": {"min": intent["entry"], "max": intent["entry"]},
                    "execution_type": "stop",
                    "requested_order_type": intent["requested_order_type"],
                    "stop_loss": intent["stop_loss"],
                    "take_profit": [intent["take_profit"]],
                    "max_lots": 0.1,
                    "confidence": 80,
                    "expires_at": "2099-06-06T09:15:00Z",
                    "reason_codes": [
                        "mode.approve",
                        f"side.{intent['side']}",
                        f"order.{intent['requested_order_type']}",
                    ],
                    "narrative": intent["narrative"],
                },
            },
            headers=USER_HEADERS,
        )
        body = response.json()
        assert response.status_code == 200
        assert body["status"] == "OK"
        assert body["received"] is True
        assert body["risk_gate"]["status"] == "accepted"
        assert "command_status" not in body
        assert await store.list_commands(ACCOUNT_ID) == []
        assert await store.poll_commands(ACCOUNT_ID) == []


async def test_ai_result_dual_plan_queues_first_and_cooldown() -> None:
    client, store = make_client()
    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    now_iso = _now_iso_from_ms(now_ms)
    client.app.state.now_iso = lambda: now_iso

    def dual_plan_side(decision_id: str, side: str, entry_min: float, entry_max: float) -> dict:
        return {
            "schema_version": "trade_plan.v1",
            "decision_id": decision_id,
            "account_id": ACCOUNT_ID,
            "symbol": "XAUUSD",
            "mode": "approve",
            "side": side,
            "confidence": 80,
            "entry_zone": {"min": entry_min, "max": entry_max},
            "execution_type": "market",
            "requested_order_type": "market",
            "stop_loss": 3330 if side == "buy" else 3340,
            "take_profit": [3345 if side == "buy" else 3325],
            "max_lots": 0.1,
            "expires_at": "2099-06-06T09:15:00Z",
            "reason_codes": ["mode.approve", f"side.{side}"],
            "narrative": f"dual {side} approve",
        }

    await store.set_runtime_mode(ACCOUNT_ID, "cutover")
    await store.save_registration({"account_id": ACCOUNT_ID, "leverage": 500, "ai_symbols": ["XAUUSD"]})
    await store.save_heartbeat(
        {
            "account_id": ACCOUNT_ID,
            "balance": 10000,
            "equity": 10000,
            "free_margin": 9000,
            "market_open": True,
            "is_trade_allowed": True,
        }
    )
    await store.save_tick(
        {
            "account_id": ACCOUNT_ID,
            "symbol": "XAUUSD",
            "bid": 3335.5,
            "ask": 3335.7,
            "spread": 0.2,
            "time": _now_iso_from_ms(now_ms - 30_000),
        }
    )
    await seed_ai_approve_trend_bars(store)

    buy_plan = dual_plan_side("tpv1_dual_buy", "buy", 3335.5, 3335.7)
    sell_plan = dual_plan_side("tpv1_dual_sell", "sell", 3335.5, 3335.7)
    response = client.post(
        f"/api/v2/ai_result/{ACCOUNT_ID}/XAUUSD",
        json={
            "trade_plan": buy_plan,
            "dual_trade_plan": {"is_dual_direction": True, "buy": buy_plan, "sell": sell_plan},
        },
        headers=USER_HEADERS,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "OK"
    assert body["received"] is True
    assert body["command_status"] == "queued"
    assert body["risk_gate"]["status"] == "accepted"
    assert body["risk_gate"]["allowed_lots"] > 0

    commands = await store.list_commands(ACCOUNT_ID)
    assert len(commands) == 1
    command = commands[0]
    assert command["action"] == "SIGNAL"
    assert command["source"] == "ai_approve"
    assert command["status"] == "queued"
    assert command["decision_id"] == "tpv1_dual_buy"
    assert command["type"] == "BUY"

    poll = client.post("/poll", json={"account_id": ACCOUNT_ID}, headers=USER_HEADERS)
    body = poll.json()
    assert body["count"] == 1
    assert body["commands"][0]["action"] == "SIGNAL"
    assert body["commands"][0]["source"] == "ai_approve"
    assert body["commands"][0]["strategy"] == "ai_signal"
    assert body["commands"][0]["decision_id"] == "tpv1_dual_buy"
    assert body["commands"][0]["type"] == "BUY"


async def test_ai_result_approve_reject_no_queue() -> None:
    client, store = make_client()
    await store.save_registration({"account_id": ACCOUNT_ID, "leverage": 500})
    await store.save_heartbeat(
        {
            "account_id": ACCOUNT_ID,
            "equity": 10000,
            "free_margin": 9000,
            "market_open": False,
            "is_trade_allowed": True,
        }
    )
    await store.save_tick(
        {
            "account_id": ACCOUNT_ID,
            "symbol": "XAUUSD",
            "bid": 3335.5,
            "ask": 3335.7,
            "spread": 0.2,
            "time": "2026-04-13T15:59:30+08:00",
        }
    )
    response = client.post(
        f"/api/v2/ai_result/{ACCOUNT_ID}/XAUUSD",
        json={
            "trade_plan": {
                "schema_version": "trade_plan.v1",
                "decision_id": "tpv1_closed",
                "account_id": ACCOUNT_ID,
                "symbol": "XAUUSD",
                "mode": "approve",
                "side": "buy",
                "entry_zone": {"min": 3335.5, "max": 3335.7},
                "stop_loss": 3330,
                "take_profit": [3345],
                "max_lots": 0.1,
                "confidence": 80,
                "expires_at": "2099-06-06T09:15:00Z",
                "reason_codes": ["mode.approve", "side.buy"],
                "narrative": "market closed reject should come from risk gate",
            },
        },
        headers=USER_HEADERS,
    )
    assert response.status_code == 200
    risk_gate = response.json()["risk_gate"]
    assert risk_gate["audit_only"] is False
    assert risk_gate["status"] == "rejected"
    assert risk_gate["reason_codes"] == ["market.closed"]
    assert risk_gate["canProduceLiveCommands"] is False
    assert await store.poll_commands(ACCOUNT_ID) == []


async def test_ai_result_trade_plan_validation_error() -> None:
    client, store = make_client()
    response = client.post(
        f"/api/v2/ai_result/{ACCOUNT_ID}/XAUUSD",
        json={
            "trade_plan": {
                "decision_id": "tpv1_invalid",
                "account_id": ACCOUNT_ID,
                "symbol": "XAUUSD",
                "mode": "approve",
                "side": "buy",
                "entry_zone": {"min": 3335.5, "max": 3335.7},
                "stop_loss": 3330,
                "take_profit": [3345],
                "max_lots": 0.1,
                "confidence": 80,
                "expires_at": "2099-06-06T09:15:00Z",
                "reason_codes": ["mode.approve"],
                "narrative": "missing schema version should fail validation",
            }
        },
        headers=USER_HEADERS,
    )
    assert response.status_code == 200
    assert response.json() == {
        "status": "OK",
        "received": True,
        "trade_plan_validation": {
            "valid": False,
            "error": 'trade_plan.schema_version = "", want "trade_plan.v1"',
        },
    }
    assert await store.poll_commands(ACCOUNT_ID) == []


async def test_ai_result_rejects_empty_and_array_bodies() -> None:
    client, store = make_client()
    for raw in ["", "[]"]:
        response = client.post(
            f"/api/ai_result/{ACCOUNT_ID}",
            content=raw,
            headers={"Content-Type": "application/json", **USER_HEADERS},
        )
        assert response.status_code == 400, raw
        assert response.json() == {"status": "ERROR", "message": "invalid JSON"}
    assert await store.get_ai_results(ACCOUNT_ID) == []


async def test_ai_result_accepts_empty_object() -> None:
    client, store = make_client()
    response = client.post(f"/api/ai_result/{ACCOUNT_ID}", json={}, headers=USER_HEADERS)
    assert response.status_code == 200
    results = await store.get_ai_results(ACCOUNT_ID)
    assert len(results) == 1
    assert results[0]["account_id"] == ACCOUNT_ID
    assert results[0]["symbol"] == "XAUUSD"


TRADE_PLAN_BASE = {
    "schema_version": "trade_plan.v1",
    "decision_id": "tpv1_decode_payload",
    "account_id": ACCOUNT_ID,
    "symbol": "XAUUSD",
    "mode": "approve",
    "side": "buy",
    "confidence": 80,
    "entry_zone": {"min": 3335.5, "max": 3335.7},
    "stop_loss": 3330,
    "take_profit": [3345],
    "max_lots": 0.1,
    "expires_at": "2099-06-06T09:15:00Z",
    "reason_codes": ["mode.approve"],
    "narrative": "decode parity",
}


async def _post_v2_ai_result(client: TestClient, trade_plan: dict) -> dict:
    response = client.post(
        f"/api/v2/ai_result/{ACCOUNT_ID}/XAUUSD", json={"trade_plan": trade_plan}, headers=USER_HEADERS
    )
    assert response.status_code == 200
    return response.json()


async def test_ai_result_decode_confidence_type() -> None:
    client, _store = make_client()
    plan = dict(TRADE_PLAN_BASE, confidence="80")
    body = await _post_v2_ai_result(client, plan)
    assert body["trade_plan_validation"] == {
        "valid": False,
        "error": (
            "decode trade_plan: json: cannot unmarshal string into Go struct field TradePlan.confidence of type int"
        ),
    }


async def test_ai_result_decode_stop_loss_type() -> None:
    client, _store = make_client()
    plan = dict(TRADE_PLAN_BASE, stop_loss="3330")
    body = await _post_v2_ai_result(client, plan)
    assert body["trade_plan_validation"] == {
        "valid": False,
        "error": (
            "decode trade_plan: json: cannot unmarshal string into Go struct field TradePlan.stop_loss of type float64"
        ),
    }


async def test_ai_result_decode_take_profit_element_type() -> None:
    client, _store = make_client()
    plan = dict(TRADE_PLAN_BASE, take_profit=["3345"])
    body = await _post_v2_ai_result(client, plan)
    assert body["trade_plan_validation"] == {
        "valid": False,
        "error": (
            "decode trade_plan: json: cannot unmarshal string into Go struct field "
            "TradePlan.take_profit of type float64"
        ),
    }


async def test_ai_result_decode_reason_codes_element_type() -> None:
    client, _store = make_client()
    plan = dict(TRADE_PLAN_BASE, reason_codes=[123])
    body = await _post_v2_ai_result(client, plan)
    assert body["trade_plan_validation"] == {
        "valid": False,
        "error": (
            "decode trade_plan: json: cannot unmarshal number into Go struct field "
            "TradePlan.reason_codes of type string"
        ),
    }


async def test_ai_result_decode_add_on_type() -> None:
    client, _store = make_client()
    plan = dict(TRADE_PLAN_BASE, add_on="true")
    body = await _post_v2_ai_result(client, plan)
    assert body["trade_plan_validation"] == {
        "valid": False,
        "error": "decode trade_plan: json: cannot unmarshal string into Go struct field TradePlan.add_on of type bool",
    }


async def test_ai_result_decode_entry_zone_type() -> None:
    client, _store = make_client()
    plan = dict(TRADE_PLAN_BASE, entry_zone={"min": "3335.5", "max": 3335.7})
    body = await _post_v2_ai_result(client, plan)
    assert body["trade_plan_validation"] == {
        "valid": False,
        "error": (
            "decode trade_plan: json: cannot unmarshal string into Go struct field "
            "TradePlan.entry_zone.min of type float64"
        ),
    }


async def test_ai_result_decode_expires_at_type() -> None:
    client, _store = make_client()
    plan = dict(TRADE_PLAN_BASE, expires_at=123)
    body = await _post_v2_ai_result(client, plan)
    assert body["trade_plan_validation"] == {
        "valid": False,
        "error": "decode trade_plan: Time.UnmarshalJSON: input is not a JSON string",
    }


# ---------------------------------------------------------------- Group G:analysis payload 进阶


def flat_bars(count: int, close: float) -> list[dict]:
    return [
        {
            "time": f"2026.04.13 {str(index).zfill(2)}:00",
            "open": close,
            "high": close + 1,
            "low": close - 1,
            "close": close,
            "volume": 1000 + index,
        }
        for index in range(count)
    ]


async def test_analysis_indicators_and_trend_context() -> None:
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
        {"account_id": ACCOUNT_ID, "symbol": "XAUUSD", "bid": 1999.8, "ask": 2000, "spread": 0.2, "time": "16:00:00"}
    )
    await store.save_bars(
        {"account_id": ACCOUNT_ID, "symbol": "XAUUSD", "timeframe": "H1", "bars": flat_bars(25, 2000)}
    )
    await store.save_bars(
        {"account_id": ACCOUNT_ID, "symbol": "XAUUSD", "timeframe": "M30", "bars": flat_bars(10, 1900)}
    )

    response = client.get(f"/api/analysis_payload/{ACCOUNT_ID}", headers=USER_HEADERS)
    assert response.status_code == 200
    body = response.json()
    assert len(body["bars"]["H1"]) == 25
    assert len(body["bars"]["M30"]) == 10
    assert body["indicators"]["H1"]["bars_count"] == 25
    assert body["indicators"]["H1"]["close"] == 2000
    assert body["indicators"]["H1"]["open"] == 2000
    assert body["indicators"]["H1"]["high"] == 2001
    assert body["indicators"]["H1"]["low"] == 1999
    assert body["indicators"]["H1"]["ema20"] == 2000
    assert body["indicators"]["H1"]["ema50"] == 2000
    assert body["indicators"]["H1"]["ema200"] == 0
    assert body["indicators"]["H1"]["atr"] == 2
    assert body["indicators"]["H1"]["macd"] == 0
    assert body["indicators"]["H1"]["macd_signal"] == 0
    assert body["indicators"]["H1"]["macd_hist"] == 0
    assert body["indicators"]["H1"]["fib_236"] == 2000.528
    assert body["indicators"]["H1"]["fib_382"] == 2000.236
    assert body["indicators"]["H1"]["fib_500"] == 2000
    assert body["indicators"]["H1"]["fib_618"] == 1999.764
    assert body["indicators"]["H1"]["fib_786"] == 1999.428
    assert body["indicators"]["M30"] is None
    assert body["trend_context"] == {
        "d1_direction": "NEUTRAL",
        "h4_direction": "NEUTRAL",
        "h1_direction": "NEUTRAL",
        "m30_direction": "NEUTRAL",
        "consensus_direction": "NEUTRAL",
        "consensus_strength": 0,
    }


async def test_analysis_d1_trend_context_not_exposed_in_bars() -> None:
    client, store = make_client()
    trending_bars = [
        {
            "time": f"D1-{index}",
            "open": close - 5,
            "high": close + 2,
            "low": close - 8,
            "close": close,
            "volume": 2000 + index,
        }
        for index, close in enumerate(1800 + index * 10 for index in range(40))
    ]
    await store.save_registration({"account_id": ACCOUNT_ID})
    await store.save_heartbeat({"account_id": ACCOUNT_ID, "market_open": True, "is_trade_allowed": True})
    await store.save_tick({"account_id": ACCOUNT_ID, "symbol": "XAUUSD", "bid": 2199.8, "ask": 2200, "spread": 0.2})
    await store.save_bars({"account_id": ACCOUNT_ID, "symbol": "XAUUSD", "timeframe": "D1", "bars": trending_bars})

    response = client.get(f"/api/analysis_payload/{ACCOUNT_ID}", headers=USER_HEADERS)
    assert response.status_code == 200
    body = response.json()
    assert "D1" not in body["bars"]
    assert "D1" not in body["indicators"]
    assert body["trend_context"]["d1_direction"] == "BULL"
    assert body["trend_context"]["consensus_direction"] == "BULL"
    assert body["trend_context"]["consensus_strength"] == 0.045


async def test_analysis_strategy_mapping_preserves_approved() -> None:
    client, store = make_client()
    strategy_mapping = {
        "20250231": "pullback",
        "20250232": "breakout_retest",
        "20250233": "divergence",
        "20250234": "breakout_pyramid",
        "20250235": "counter_pullback",
        "20250236": "range",
        "20250237": "momentum_scalp",
        "20250238": "ai_signal",
        "20259999": "experimental",
    }
    await store.save_registration({"account_id": ACCOUNT_ID, "strategy_mapping": strategy_mapping})
    response = client.get(f"/api/analysis_payload/{ACCOUNT_ID}", headers=USER_HEADERS)
    assert response.status_code == 200
    assert response.json()["strategy_mapping"] == {
        "20250231": "pullback",
        "20250232": "breakout_retest",
        "20250233": "divergence",
        "20250234": "breakout_pyramid",
        "20250235": "counter_pullback",
        "20250236": "range",
        "20250238": "ai_signal",
    }


async def test_analysis_strategy_mapping_defaults() -> None:
    client, store = make_client()
    await store.save_registration({"account_id": ACCOUNT_ID})
    response = client.get(f"/api/analysis_payload/{ACCOUNT_ID}", headers=USER_HEADERS)
    assert response.status_code == 200
    assert response.json()["strategy_mapping"] == {
        "20250231": "pullback",
        "20250232": "breakout_retest",
        "20250233": "divergence",
        "20250234": "breakout_pyramid",
        "20250235": "counter_pullback",
        "20250236": "range",
        "20250238": "ai_signal",
    }


async def test_analysis_market_filters_from_node_snapshots() -> None:
    client, store = make_client(now_iso=lambda: "2026-06-05T20:45:00.000Z")
    await store.save_registration({"account_id": ACCOUNT_ID})
    await store.save_heartbeat({"account_id": ACCOUNT_ID, "market_open": True, "is_trade_allowed": True})
    await store.save_tick(
        {
            "account_id": ACCOUNT_ID,
            "symbol": "XAUUSD",
            "bid": 3335.55,
            "ask": 3335.75,
            "spread": 8.2,
            "time": "2026-06-05T20:44:50.000Z",
        }
    )
    atr_bars = [
        *flat_bars(40, 3300),
        {"time": "2026.04.13 40:00", "open": 3340, "high": 3400, "low": 3300, "close": 3340},
    ]
    await store.save_bars({"account_id": ACCOUNT_ID, "symbol": "XAUUSD", "timeframe": "M30", "bars": atr_bars})

    response = client.get(f"/api/analysis_payload/{ACCOUNT_ID}", headers=USER_HEADERS)
    assert response.status_code == 200
    filters = response.json()["market_filters"]
    assert filters["blocked"] is True
    assert set(filters["reason_codes"]) == {
        "spread.too_wide",
        "session.friday_close_window",
        "volatility.atr_expansion",
    }
    blocking_codes = {item["code"] for item in filters["blocking"]}
    assert blocking_codes == {"spread.too_wide", "session.friday_close_window"}
    warning_codes = {item["code"] for item in filters["warnings"]}
    assert warning_codes == {"volatility.atr_expansion"}


async def test_analysis_market_max_spread_from_heartbeat() -> None:
    client, store = make_client(now_iso=lambda: "2026-06-04T13:00:00.000Z")
    await store.save_registration({"account_id": ACCOUNT_ID})
    await store.save_heartbeat(
        {"account_id": ACCOUNT_ID, "market_open": True, "is_trade_allowed": True, "max_spread": 25}
    )
    await store.save_tick(
        {
            "account_id": ACCOUNT_ID,
            "symbol": "XAUUSD",
            "bid": 3335.55,
            "ask": 3335.75,
            "spread": 21,
            "time": "2026-06-04T12:59:50.000Z",
        }
    )
    response = client.get(f"/api/analysis_payload/{ACCOUNT_ID}", headers=USER_HEADERS)
    assert response.status_code == 200
    body = response.json()
    assert body["market"]["max_spread"] == 25
    assert "spread.too_wide" not in body["market_filters"]["reason_codes"]
    assert body["market_filters"]["blocked"] is False


async def test_analysis_market_status_stale_tick() -> None:
    client, store = make_client(now_iso=lambda: "2026-06-04T13:00:00.000Z")
    await store.save_registration({"account_id": ACCOUNT_ID})
    await store.save_heartbeat(
        {
            "account_id": ACCOUNT_ID,
            "market_open": True,
            "is_trade_allowed": True,
            "server_time": "2026.06.04 13:00",
            "last_heartbeat_at": "2026-06-04T13:00:00.000Z",
            "updated_at": "2026-06-04T13:00:00.000Z",
        }
    )
    await store.save_tick(
        {
            "account_id": ACCOUNT_ID,
            "symbol": "XAUUSD",
            "bid": 3335.55,
            "ask": 3335.75,
            "spread": 0.2,
            "time": "12:44:30",
            "received_at": "2026-06-04T12:44:30.000Z",
            "updated_at": "2026-06-04T12:44:30.000Z",
        }
    )
    response = client.get(f"/api/analysis_payload/{ACCOUNT_ID}", headers=USER_HEADERS)
    assert response.status_code == 200
    status = response.json()["market_status"]
    assert status["market_open"] is False
    assert status["is_trade_allowed"] is False
    assert status["mt4_server_time"] == "2026.06.04 13:00"
    assert status["tradeable"] is False
    assert status["stale"] is True
    assert status["stale_reason"] == "tick_stale"
    assert status["tick_age_ms"] > 15 * 60 * 1000


async def test_analysis_market_status_fresh_receive_timestamps() -> None:
    client, store = make_client(now_iso=lambda: "2026-07-07T02:53:00.000Z")
    await store.save_registration({"account_id": ACCOUNT_ID})
    await store.save_heartbeat(
        {
            "account_id": ACCOUNT_ID,
            "market_open": True,
            "is_trade_allowed": True,
            "server_time": "2026.07.07 02:53",
            "last_heartbeat_at": "2026-07-07T02:52:50.000Z",
            "updated_at": "2026-07-07T02:52:50.000Z",
        }
    )
    await store.save_tick(
        {
            "account_id": ACCOUNT_ID,
            "symbol": "XAUUSD",
            "bid": 4162.05,
            "ask": 4162.28,
            "spread": 23,
            "time": "02:52:52",
            "received_at": "2026-07-07T02:52:52.000Z",
            "updated_at": "2026-07-07T02:52:52.000Z",
        }
    )
    response = client.get(f"/api/analysis_payload/{ACCOUNT_ID}", headers=USER_HEADERS)
    assert response.status_code == 200
    status = response.json()["market_status"]
    assert status["market_open"] is True
    assert status["is_trade_allowed"] is True
    assert status["mt4_server_time"] == "2026.07.07 02:53"
    assert status["tradeable"] is True
    assert status["stale"] is False


async def test_analysis_market_status_wall_clock_offset_not_stale() -> None:
    client, store = make_client(now_iso=lambda: "2026-07-19T23:10:00.000Z")
    await store.save_registration({"account_id": ACCOUNT_ID})
    await store.save_heartbeat(
        {
            "account_id": ACCOUNT_ID,
            "market_open": True,
            "is_trade_allowed": True,
            "server_time": "2026.07.20 02:09",
            "last_heartbeat_at": "2026-07-19T23:09:50.000Z",
            "updated_at": "2026-07-19T23:09:50.000Z",
        }
    )
    await store.save_tick(
        {
            "account_id": ACCOUNT_ID,
            "symbol": "XAUUSD",
            "bid": 3993.79,
            "ask": 3994.03,
            "spread": 24,
            "time": "02:09:30",
            "received_at": "2026-07-19T23:09:55.000Z",
            "updated_at": "2026-07-19T23:09:55.000Z",
        }
    )
    response = client.get(f"/api/analysis_payload/{ACCOUNT_ID}", headers=USER_HEADERS)
    assert response.status_code == 200
    status = response.json()["market_status"]
    assert status["market_open"] is True
    assert status["is_trade_allowed"] is True
    assert status["tradeable"] is True
    assert status["stale"] is False
    assert status["tick_age_ms"] < 60 * 1000
    assert status["heartbeat_age_ms"] < 60 * 1000


async def test_analysis_market_status_legacy_time_only_tick_no_receive_stamp() -> None:
    client, store = make_client(now_iso=lambda: "2026-07-07T02:53:00+08:00")
    await store.save_registration({"account_id": ACCOUNT_ID})
    await store.save_heartbeat(
        {"account_id": ACCOUNT_ID, "market_open": True, "is_trade_allowed": True, "server_time": "2026.07.07 02:53"}
    )
    await store.save_tick(
        {"account_id": ACCOUNT_ID, "symbol": "XAUUSD", "bid": 4162.05, "ask": 4162.28, "spread": 23, "time": "02:52:52"}
    )
    response = client.get(f"/api/analysis_payload/{ACCOUNT_ID}", headers=USER_HEADERS)
    assert response.status_code == 200
    status = response.json()["market_status"]
    assert status["market_open"] is True
    assert status["is_trade_allowed"] is True
    assert status["mt4_server_time"] == "2026.07.07 02:53"
    assert status["tradeable"] is True
    assert status["stale"] is False


async def test_analysis_market_status_rollover_time_only_tick() -> None:
    client, store = make_client(now_iso=lambda: "2026-07-07T00:12:00+08:00")
    await store.save_registration({"account_id": ACCOUNT_ID})
    await store.save_heartbeat(
        {"account_id": ACCOUNT_ID, "market_open": True, "is_trade_allowed": True, "server_time": "2026.07.07 00:12"}
    )
    await store.save_tick(
        {"account_id": ACCOUNT_ID, "symbol": "XAUUSD", "bid": 4162.05, "ask": 4162.28, "spread": 23, "time": "23:59:00"}
    )
    response = client.get(f"/api/analysis_payload/{ACCOUNT_ID}", headers=USER_HEADERS)
    assert response.status_code == 200
    status = response.json()["market_status"]
    assert status["market_open"] is True
    assert status["is_trade_allowed"] is True
    assert status["mt4_server_time"] == "2026.07.07 00:12"
    assert status["tradeable"] is True
    assert status["stale"] is False
    assert status["tick_age_ms"] <= 15 * 60 * 1000


async def test_analysis_market_status_stale_heartbeat() -> None:
    client, store = make_client(now_iso=lambda: "2026-07-07T03:00:00.000Z")
    await store.save_registration({"account_id": ACCOUNT_ID})
    await store.save_heartbeat(
        {
            "account_id": ACCOUNT_ID,
            "market_open": True,
            "is_trade_allowed": True,
            "server_time": "2026.07.07 03:00",
            "last_heartbeat_at": "2026-07-07T02:30:00.000Z",
            "updated_at": "2026-07-07T02:30:00.000Z",
        }
    )
    await store.save_tick(
        {
            "account_id": ACCOUNT_ID,
            "symbol": "XAUUSD",
            "bid": 4162.05,
            "ask": 4162.28,
            "spread": 23,
            "time": "02:59:30",
            "received_at": "2026-07-07T02:59:30.000Z",
            "updated_at": "2026-07-07T02:59:30.000Z",
        }
    )
    response = client.get(f"/api/analysis_payload/{ACCOUNT_ID}", headers=USER_HEADERS)
    assert response.status_code == 200
    status = response.json()["market_status"]
    assert status["market_open"] is False
    assert status["is_trade_allowed"] is False
    assert status["tradeable"] is False
    assert status["stale"] is True
    assert status["stale_reason"] == "heartbeat_stale"
    assert status["heartbeat_age_ms"] > 15 * 60 * 1000


async def test_analysis_market_status_restores_after_fresh_data() -> None:
    now = {"value": "2026-07-07T03:20:00.000Z"}
    client, store = make_client(now_iso=lambda: now["value"])
    await store.save_registration({"account_id": ACCOUNT_ID})
    await store.save_heartbeat(
        {
            "account_id": ACCOUNT_ID,
            "market_open": True,
            "is_trade_allowed": True,
            "server_time": "2026.07.07 02:40",
            "last_heartbeat_at": "2026-07-07T02:40:00.000Z",
            "updated_at": "2026-07-07T02:40:00.000Z",
        }
    )
    await store.save_tick(
        {
            "account_id": ACCOUNT_ID,
            "symbol": "XAUUSD",
            "bid": 4162.05,
            "ask": 4162.28,
            "spread": 23,
            "time": "02:40:00",
            "received_at": "2026-07-07T02:40:00.000Z",
            "updated_at": "2026-07-07T02:40:00.000Z",
        }
    )
    stale_response = client.get(f"/api/analysis_payload/{ACCOUNT_ID}", headers=USER_HEADERS)
    assert stale_response.status_code == 200
    stale_status = stale_response.json()["market_status"]
    assert stale_status["market_open"] is False
    assert stale_status["tradeable"] is False
    assert stale_status["stale"] is True

    now["value"] = "2026-07-07T03:21:00.000Z"
    await store.save_heartbeat(
        {
            "account_id": ACCOUNT_ID,
            "market_open": True,
            "is_trade_allowed": True,
            "server_time": "2026.07.07 03:21",
            "last_heartbeat_at": "2026-07-07T03:20:50.000Z",
            "updated_at": "2026-07-07T03:20:50.000Z",
        }
    )
    await store.save_tick(
        {
            "account_id": ACCOUNT_ID,
            "symbol": "XAUUSD",
            "bid": 4162.15,
            "ask": 4162.38,
            "spread": 23,
            "time": "03:20:55",
            "received_at": "2026-07-07T03:20:55.000Z",
            "updated_at": "2026-07-07T03:20:55.000Z",
        }
    )
    fresh_response = client.get(f"/api/analysis_payload/{ACCOUNT_ID}", headers=USER_HEADERS)
    assert fresh_response.status_code == 200
    fresh_status = fresh_response.json()["market_status"]
    assert fresh_status["market_open"] is True
    assert fresh_status["is_trade_allowed"] is True
    assert fresh_status["tradeable"] is True
    assert fresh_status["stale"] is False
