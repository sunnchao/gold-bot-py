"""Replay runner 契约(镜像 apps/app-agent/src/evaluation/replay-runner.test.ts)。"""

from __future__ import annotations

import json

import pytest

from backend.agents.evaluation.replay_runner import (
    capture_replay_fixture,
    compute_replay_metrics,
    fixture_to_candidate_result,
    replay_fixture,
)


def base_payload() -> dict:
    return {
        "account": {
            "account_id": "90011087",
            "balance": 10000,
            "equity": 10100,
            "margin": 100,
            "free_margin": 10000,
            "currency": "USD",
            "leverage": 500,
        },
        "market": {"symbol": "XAUUSD", "bid": 3335.5, "ask": 3335.7, "spread": 0.2},
        "indicators": {},
        "positions": [],
        "market_status": {"market_open": True, "is_trade_allowed": True, "tradeable": True},
        "strategy_mapping": {},
    }


def base_pending_signal() -> dict:
    return {
        "id": 42,
        "account_id": "90011087",
        "symbol": "XAUUSD",
        "side": "buy",
        "score": 78,
        "strategy": "breakout",
        "indicators": "RSI=58",
        "status": "pending",
        "created_at": "2026-06-06T09:00:00.000Z",
        "expires_at": "2026-06-06T09:15:00.000Z",
        "arbitration_result": "buy",
        "arbitration_reason": "momentum aligned",
    }


def trade_plan(overrides: dict | None = None) -> dict:
    plan = {
        "schema_version": "trade_plan.v1",
        "decision_id": "tpv1_fixture",
        "account_id": "90011087",
        "symbol": "XAUUSD",
        "mode": "approve",
        "side": "buy",
        "confidence": 82,
        "entry_zone": {"min": 3335.5, "max": 3335.7},
        "stop_loss": 3328,
        "take_profit": [3350],
        "max_lots": 0.2,
        "expires_at": "2026-06-06T09:15:00.000Z",
        "reason_codes": ["mode.approve", "side.buy"],
        "conflicts": [],
        "narrative": "multi-timeframe bullish alignment",
    }
    if overrides:
        plan.update(overrides)
    return plan


async def test_capture_replay_fixture_stores_inputs_and_redacts_secrets() -> None:
    payload = {
        **base_payload(),
        "diagnostics": {"api_key": "sk-live-secret-value", "authorization": "Bearer abc.def.ghi"},
    }
    fixture = capture_replay_fixture(
        {
            "fixtureId": "xauusd-20260606-0900",
            "capturedAt": "2026-06-06T09:00:00.000Z",
            "accountId": "90011087",
            "symbol": "XAUUSD",
            "analysisPayload": payload,
            "pendingSignal": base_pending_signal(),
            "llmResponses": [
                {
                    "agent": "technical",
                    "rawResponse": '{"bias":"bullish","note":"api_key=sk-another-secret"}',
                    "parsedOutput": {"bias": "bullish"},
                },
                {"agent": "risk", "rawResponse": "not json", "parseError": "no JSON found"},
            ],
            "parsedOutputs": {"technical": {"bias": "bullish"}, "risk": None},
            "finalTradePlan": trade_plan(),
        }
    )

    assert fixture["schema_version"] == "replay_fixture.v1"
    assert fixture["fixture_id"] == "xauusd-20260606-0900"
    assert fixture["account_id"] == "90011087"
    assert fixture["symbol"] == "XAUUSD"
    assert fixture["pending_signal"]["side"] == "buy"
    assert fixture["final_trade_plan"]["side"] == "buy"
    assert fixture["final_trade_plan"]["max_lots"] == 0.2
    assert len(fixture["llm_responses"]) == 2
    assert fixture["parsed_outputs"] == {"technical": {"bias": "bullish"}, "risk": None}

    serialized = json.dumps(fixture)
    assert "sk-live-secret-value" not in serialized
    assert "sk-another-secret" not in serialized
    assert "Bearer abc.def.ghi" not in serialized
    assert "[REDACTED]" in serialized


async def test_replay_fixture_uses_stored_data_without_live_runner() -> None:
    fixture = capture_replay_fixture(
        {
            "fixtureId": "offline-fixture",
            "capturedAt": "2026-06-06T09:00:00.000Z",
            "accountId": "90011087",
            "symbol": "XAUUSD",
            "analysisPayload": base_payload(),
            "pendingSignal": base_pending_signal(),
            "llmResponses": [
                {"agent": "mao", "rawResponse": '{"final_direction":"buy"}', "parsedOutput": {"final_direction": "buy"}}
            ],
            "parsedOutputs": {"arbitration": {"final_direction": "buy"}},
            "finalTradePlan": trade_plan(),
        }
    )
    calls: list[str] = []

    async def live_runner(fixture: dict) -> dict:
        calls.append("live")
        raise AssertionError("live LLM should not be called")

    result = await replay_fixture(fixture, {"liveRunner": live_runner})

    assert calls == []
    assert result["fixture_id"] == "offline-fixture"
    assert result["account_id"] == "90011087"
    assert result["symbol"] == "XAUUSD"
    assert result["source"] == "fixture"
    assert result["trade_plan"]["side"] == "buy"
    assert result["trade_plan"]["mode"] == "approve"
    assert result["parse_failures"] == []


async def test_replay_fixture_live_runner_requires_live_runner_when_allow_live() -> None:
    fixture = fixture_to_candidate_result(
        capture_replay_fixture(
            {
                "fixtureId": "live-gate",
                "accountId": "90011087",
                "symbol": "XAUUSD",
                "analysisPayload": base_payload(),
                "llmResponses": [],
                "parsedOutputs": {},
            }
        )
    )
    with pytest.raises(ValueError, match="allowLiveLlm requires a liveRunner"):
        await replay_fixture(fixture, {"allowLiveLlm": True})


def _plan_row(fixture_id: str, decision_id: str, **overrides) -> dict:
    return {
        "fixture_id": fixture_id,
        "account_id": "90011087",
        "symbol": "XAUUSD",
        "source": "fixture",
        "trade_plan": trade_plan({"decision_id": decision_id, **overrides}),
        "parsed_outputs": {},
        "parse_failures": [],
    }


async def test_compute_replay_metrics_drift_rates() -> None:
    baseline = [
        _plan_row("a", "a", side="buy", mode="approve", stop_loss=3328, max_lots=0.2),
        _plan_row("b", "b", side="sell", mode="modify", stop_loss=3348, max_lots=0.1),
    ]
    candidate = [
        dict(
            _plan_row("a", "a-new", side="sell", mode="approve", stop_loss=3335, max_lots=0.2),
            source="candidate",
            parse_failures=["technical"],
        ),
        dict(
            _plan_row("b", "b-new", side="sell", mode="close", stop_loss=3348.5, max_lots=0.15),
            source="candidate",
        ),
    ]

    metrics = compute_replay_metrics(baseline, candidate, {"stopLossTolerance": 1, "maxLotsTolerance": 0.01})

    assert metrics["total_fixtures"] == 2
    assert metrics["compared_fixtures"] == 2
    assert metrics["parse_failure_count"] == 1
    assert metrics["parse_failure_rate"] == 0.5
    assert metrics["direction_drift_count"] == 1
    assert metrics["direction_drift_rate"] == 0.5
    assert metrics["mode_drift_count"] == 1
    assert metrics["mode_drift_rate"] == 0.5
    assert metrics["stop_loss_drift_count"] == 1
    assert metrics["stop_loss_drift_rate"] == 0.5
    assert metrics["max_lots_drift_count"] == 1
    assert metrics["max_lots_drift_rate"] == 0.5
    assert metrics["stop_loss_average_abs_delta"] == 3.75
    assert metrics["stop_loss_max_abs_delta"] == 7
    assert metrics["max_lots_average_abs_delta"] == 0.025
    assert metrics["max_lots_max_abs_delta"] == pytest.approx(0.05)
