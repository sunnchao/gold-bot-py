import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from backend.agents.types.schemas import GoldbotPayloadSchema
from backend.api.app import create_api_app
from backend.api.routes.ai.index import ai_trade_plan_risk_gate
from backend.persistence.store import create_in_memory_store
from backend.services.command_lifecycle.index import CommandLifecycleService
from backend.services.scheduler import SchedulerService
from backend.trading_core.riskgate.economic_calendar import EVENT_BLACKOUT
from backend.trading_core.riskgate.riskgate import evaluate_risk_gate, market_event_window
from tests.integration.services.test_scheduler import FakeAnalysis

ACCOUNT = "acc-1"
SYMBOL = "XAUUSD"
EVENT_TIME = datetime(2030, 1, 7, 13, 30, tzinfo=UTC)


def at(minutes):
    return (EVENT_TIME + timedelta(minutes=minutes)).isoformat()


@pytest.fixture
def calendar(tmp_path, monkeypatch):
    path = tmp_path / "calendar.json"
    path.write_text(json.dumps([
        {"type": "CPI", "time_utc": at(0), "symbols": ["XAUUSD"]},
        {"type": "FOMC", "time_utc": at(0), "symbols": ["XAGUSD"]},
        {"type": "NFP", "time_utc": at(180), "symbols": ["XAUUSD"]},
    ]))
    monkeypatch.setenv("GB_ECONOMIC_CALENDAR_PATH", str(path))


@pytest.mark.parametrize(
    ("symbol", "minutes", "stage", "kind"),
    [
        ("GOLDm#", -1441, None, None),
        ("GOLDm#", -1440, "warning", "CPI"),
        ("XAUUSD", -300, "warning", "CPI"),
        ("XAUUSD", -240, "reduced", "CPI"),
        ("XAUUSD", -120, "reduced", "CPI"),
        ("XAUUSD", -30, "blackout", "CPI"),
        ("XAUUSD", -15, "blackout", "CPI"),
        ("XAUUSD", 20, "blackout", "CPI"),
        ("XAUUSD", 30, "reduced", "NFP"),
        ("XAUUSD", 45, "reduced", "NFP"),
        ("SILVERm#", 45, "blackout", "FOMC"),
        ("SILVERm#", 60, None, None),
        ("GBPJPY", -15, None, None),
    ],
)
def test_event_windows_are_utc_and_stricter_overlaps_win(calendar, symbol, minutes, stage, kind):
    window = market_event_window(symbol, at(minutes))
    assert (None if window is None else (window.stage, window.event.kind)) == (None if stage is None else (stage, kind))


@pytest.fixture
async def setup_account():
    store = create_in_memory_store()
    await store.set_runtime_mode(ACCOUNT, "cutover")
    await store.save_registration({"account_id": ACCOUNT, "leverage": 500, "ai_symbols": [SYMBOL, "GBPJPY"]})
    await store.save_heartbeat({
        "account_id": ACCOUNT, "equity": 10000, "free_margin": 9000,
        "market_open": True, "is_trade_allowed": True,
    })

    async def set_tick(now):
        await store.save_tick({"account_id": ACCOUNT, "symbol": SYMBOL, "bid": 2339.8, "ask": 2340, "time": now})

    return store, set_tick


def trade_plan(**overrides):
    return {
        "schema_version": "trade_plan.v1", "decision_id": "test-plan", "account_id": ACCOUNT,
        "symbol": SYMBOL, "mode": "approve", "side": "buy", "confidence": 82,
        "entry_zone": {"min": 2338, "max": 2338}, "execution_type": "limit", "requested_order_type": "BUY_LIMIT",
        "stop_loss": 2330, "take_profit": [2350, 2360], "max_lots": 0.10,
        "reason_codes": ["mode.approve"], "narrative": "test limit", "expires_at": at(120),
        **overrides,
    }


@pytest.mark.parametrize(
    ("minutes", "plan_overrides", "dual_only", "expected"),
    [
        (-300, {}, False, "warning"),
        (-120, {}, False, "reduced"),
        (-120, {"expires_at": at(-60)}, False, "reduced"),
        (-120, {"add_on": True}, False, "event.add_on_not_allowed"),
        (-120, {"max_lots": 0.01}, False, "lots.below_min_after_scale"),
        (-120, {}, True, "event.dual_pending_not_allowed"),
        (-15, {}, False, EVENT_BLACKOUT),
        (20, {}, True, EVENT_BLACKOUT),
    ],
)
async def test_ai_route_enforces_calendar(calendar, setup_account, minutes, plan_overrides, dual_only, expected):
    store, set_tick = setup_account
    now = at(minutes)
    await set_tick(now)
    token = "test-token"
    app = create_api_app({
        "store": store, "now_iso": lambda: now, "valid_tokens": {token}, "token_accounts": {token: {ACCOUNT}},
    })
    plan = trade_plan(**plan_overrides)
    payload = {"dual_trade_plan": {"is_dual_direction": True, "buy": plan}} if dual_only else {"trade_plan": plan}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test", headers={"X-API-Token": token},
    ) as client:
        market = (await client.get(f"/api/v2/analysis_payload/{ACCOUNT}/{SYMBOL}")).json()
        filters = GoldbotPayloadSchema.model_validate(market).market_filters
        assert filters.event_window["type"] == "CPI"
        response = await client.post(f"/api/v2/ai_result/{ACCOUNT}/{SYMBOL}", json=payload)
        assert response.status_code == 200
        commands = await store.list_commands(ACCOUNT)
        if expected in ("warning", "reduced"):
            assert filters.event_window["stage"] == expected
            assert len(commands) == 1
            command = commands[0]
            if expected == "warning":
                assert "event.upcoming_24h" in filters.reason_codes
                assert "lot_scale" not in command
                assert command["tp_split"] is True
            else:
                assert command["lot_scale"] == 0.5
                assert command["lots"] == pytest.approx(0.05)
                assert command["tp_split"] is False
                earlier_deadline = min(at(-30), plan["expires_at"])
                assert command["expiration"] == int(datetime.fromisoformat(earlier_deadline).timestamp())
                delivered = await client.post("/poll", json={"account_id": ACCOUNT, "event_controls": True})
                assert delivered.json()["commands"][0]["lot_scale"] == 0.5
        else:
            assert not commands
            if dual_only:
                events = await store.list_decision_events({"account_id": ACCOUNT})
                assert any(event["summary"].get("pending_gate_reason") == expected for event in events)
            else:
                assert expected in response.json()["risk_gate"]["reason_codes"]


@pytest.mark.parametrize("strategy", ["pullback", "scale_in"])
async def test_replay_uses_the_same_reduced_window(calendar, setup_account, strategy):
    store, set_tick = setup_account
    await set_tick(at(-120))
    analysis = FakeAnalysis(lambda: {"replay": {"signal": {
        "side": "BUY", "strategy": strategy, "entry": 2338, "stop_loss": 2330,
        "tp1": 2350, "tp2": 2360, "atr": 2, "lots": 0.10,
    }}})
    scheduler = SchedulerService(analysis, CommandLifecycleService(store), store=store, now_iso=lambda: at(-120))

    await scheduler.enqueue_analysis(ACCOUNT, SYMBOL, "H1")

    commands = await store.list_commands(ACCOUNT)
    if strategy == "scale_in":
        assert not commands
    else:
        assert len(commands) == 1
        assert commands[0]["order_type"] == "BUY_LIMIT"
        assert commands[0]["lot_scale"] == 0.5
        assert commands[0]["tp_split"] is False
        assert commands[0]["expiration"] == int(datetime.fromisoformat(at(-30)).timestamp())


async def test_reduced_window_rejects_a_second_pending_order(calendar, setup_account):
    store, set_tick = setup_account
    await set_tick(at(-120))
    await store.save_positions({"account_id": ACCOUNT, "symbol": SYMBOL, "positions": [
        {"ticket": 42, "type": "SELL_LIMIT", "lots": 0.1, "symbol": SYMBOL},
    ]})
    gate = await ai_trade_plan_risk_gate(store, ACCOUNT, SYMBOL, trade_plan(), at(-120))

    assert gate["status"] == "rejected"
    assert gate["reason_codes"] == ["event.dual_pending_not_allowed"]


async def test_blackout_enqueues_one_batch_and_does_not_modify_open_positions(calendar, setup_account):
    store, set_tick = setup_account
    await set_tick(at(-15))
    position = {"ticket": 42, "type": "BUY", "lots": 0.1, "sl": 2330, "symbol": SYMBOL}
    await store.save_positions({"account_id": ACCOUNT, "symbol": SYMBOL, "positions": [position]})
    analysis = FakeAnalysis(lambda: {"replay": {
        "signal": {"side": "BUY", "strategy": "pullback", "entry": 2338, "stop_loss": 2330, "lots": 0.1},
        "position_commands": [{"action": "MODIFY", "ticket": 42, "new_sl": 2335}],
    }})
    scheduler = SchedulerService(analysis, CommandLifecycleService(store), store=store, now_iso=lambda: at(-15))

    await scheduler.enqueue_position_review(ACCOUNT, SYMBOL)
    await scheduler._event_cancellation_cycle()
    await scheduler._event_cancellation_cycle()

    commands = await store.list_commands(ACCOUNT)
    assert [(cmd["action"], cmd["ticket"], cmd["symbol"], cmd["reason"]) for cmd in commands] == [
        ("CANCEL_PENDING", 0, SYMBOL, EVENT_BLACKOUT),
    ]
    assert (await store.get_positions(ACCOUNT, SYMBOL)) == [position]
    assert not await store.poll_commands(ACCOUNT, now_iso=at(-15), event_controls=False)
    assert (await store.poll_commands(ACCOUNT, now_iso=at(-15)))[0]["ticket"] == 0


def test_blackout_does_not_reject_explicit_risk_reduction(calendar):
    result = evaluate_risk_gate({
        "now": at(-15), "plan": {"symbol": SYMBOL, "mode": "reduce", "side": "sell"},
        "state": {"tick": {"bid": 2339.8, "ask": 2340}},
        "runtime": {"marketOpen": True, "isTradeAllowed": True, "lastTickAt": at(-15)},
    })
    assert result["reasonCodes"] == ["action.audit_safe"]
