"""镜像 packages/trading-core/src/riskgate/riskgate.spec.ts(vitest 逐用例对拍)。

每个 describe/it 映射为一个 pytest 用例或 parametrize 分支;
toEqual 用逐字段断言,包含关系用 `in`,布尔/状态用身份与字面量比较。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from backend.trading_core.riskgate import evaluate_market_filters, evaluate_risk_gate

NOW = "2026-06-06T09:00:00.000Z"


def valid_input() -> dict[str, Any]:
    """镜像 spec validInput()。"""
    return {
        "now": NOW,
        "account": {"accountId": "90011087", "leverage": 500},
        "runtime": {
            "equity": 1100.25,
            "freeMargin": 1000.25,
            "marketOpen": True,
            "isTradeAllowed": True,
            "lastTickAt": "2026-06-06T08:59:50.000Z",
        },
        "state": {
            "tick": {
                "symbol": "XAUUSD",
                "bid": 3335.55,
                "ask": 3335.75,
                "spread": 0.2,
            },
            "positions": [],
        },
        "plan": {
            "decisionId": "tpv1_gate_test",
            "accountId": "90011087",
            "symbol": "XAUUSD",
            "mode": "approve",
            "side": "buy",
            "entryZone": {"min": 3335.55, "max": 3335.75},
            "stopLoss": 3328,
            "takeProfit": [3350],
            "maxLots": 0.2,
            "expiresAt": "2026-06-06T09:15:00.000Z",
        },
        "sourceStrategy": "pullback",
        "allowAdd": False,
        "allowHedge": False,
    }


def atr_bars(base_atr: float, history_count: int, latest_atr: float) -> list[dict[str, Any]]:
    """镜像 spec atrBars():historyCount 根历史 bar + 1 根最新 bar。"""
    bars = [{"atr": base_atr, "close": 3300 + i} for i in range(history_count)]
    bars.append({"atr": latest_atr, "close": 3300 + history_count})
    return bars


def valid_market_filter_input() -> dict[str, Any]:
    """镜像 spec validMarketFilterInput()。"""
    return {
        "now": "2026-06-04T13:00:00.000Z",
        "symbol": "XAUUSD",
        "runtime": {
            "marketOpen": True,
            "isTradeAllowed": True,
            "lastTickAt": "2026-06-04T12:59:50.000Z",
        },
        "state": {
            "tick": {"symbol": "XAUUSD", "bid": 3335.55, "ask": 3335.75, "spread": 0.2},
            "bars": {"H1": atr_bars(1, 24, 1)},
        },
    }


# ---- riskgate 用例的变异器(镜像 spec it.each 的 mutate) ----
def _closed_market(i: dict[str, Any]) -> None:
    i["runtime"]["marketOpen"] = False


def _trade_disabled(i: dict[str, Any]) -> None:
    i["runtime"]["isTradeAllowed"] = False


def _stale_tick(i: dict[str, Any]) -> None:
    i["runtime"]["lastTickAt"] = "2026-06-06T08:57:00.000Z"


def _wide_spread(i: dict[str, Any]) -> None:
    i["state"]["tick"]["spread"] = 80.1


def _expired_plan(i: dict[str, Any]) -> None:
    i["plan"]["expiresAt"] = "2026-06-06T08:59:59.000Z"


# ---- market filter 用例的变异器 ----
def _filter_closed_market(i: dict[str, Any]) -> None:
    i["runtime"]["marketOpen"] = False


def _filter_trade_disabled(i: dict[str, Any]) -> None:
    i["runtime"]["isTradeAllowed"] = False


def _filter_stale_tick(i: dict[str, Any]) -> None:
    i["runtime"]["lastTickAt"] = "2026-06-04T12:57:00.000Z"


def _filter_wide_spread(i: dict[str, Any]) -> None:
    i["state"]["tick"]["spread"] = 8.1


def _filter_friday_close_window(i: dict[str, Any]) -> None:
    i["now"] = "2026-06-05T20:45:00.000Z"
    i["runtime"]["lastTickAt"] = "2026-06-05T20:44:50.000Z"


def _filter_rollover_window(i: dict[str, Any]) -> None:
    i["now"] = "2026-06-04T21:58:00.000Z"
    i["runtime"]["lastTickAt"] = "2026-06-04T21:57:50.000Z"


def _filter_low_liquidity_session(i: dict[str, Any]) -> None:
    i["now"] = "2026-06-04T22:30:00.000Z"
    i["runtime"]["lastTickAt"] = "2026-06-04T22:29:50.000Z"


def _filter_atr_expansion(i: dict[str, Any]) -> None:
    i["state"]["bars"] = {"M30": atr_bars(1, 24, 2.2)}


class TestRiskGateParity:
    def test_accepts_absent_plan_with_go_parity_reason_code(self) -> None:
        input_ = valid_input()
        input_["plan"] = None

        result = evaluate_risk_gate(input_)

        assert result["status"] == "accepted"
        assert result["auditOnly"] is False
        assert "plan.absent" in result["reasonCodes"]

    @pytest.mark.parametrize("mode", ["approve", "modify"])
    def test_allows_mode_past_the_audit_only_guard(self, mode: str) -> None:
        input_ = valid_input()
        input_["plan"]["mode"] = mode
        input_["plan"]["maxLots"] = 0.02

        result = evaluate_risk_gate(input_)

        assert result["status"] == "accepted"
        assert result["auditOnly"] is False
        assert "lots.accepted" in result["reasonCodes"]

    @pytest.mark.parametrize("mode", ["observe", "veto"])
    def test_keeps_mode_audit_only(self, mode: str) -> None:
        input_ = valid_input()
        input_["plan"]["mode"] = mode

        result = evaluate_risk_gate(input_)

        assert result["auditOnly"] is True

    @pytest.mark.parametrize(
        ("_name", "mutate", "want_code"),
        [
            ("closed market", _closed_market, "market.closed"),
            ("trade disabled", _trade_disabled, "market.trade_not_allowed"),
            ("stale tick", _stale_tick, "tick.stale"),
            ("wide spread", _wide_spread, "spread.too_wide"),
            ("expired plan", _expired_plan, "plan.expired"),
        ],
    )
    def test_rejects_with_go_reason_code(
        self, _name: str, mutate: Callable[[dict[str, Any]], None], want_code: str
    ) -> None:
        input_ = valid_input()
        mutate(input_)

        result = evaluate_risk_gate(input_)

        assert result["status"] == "rejected"
        assert want_code in result["reasonCodes"]

    def test_uses_ea_configured_max_spread_instead_of_static_server_metadata(self) -> None:
        input_ = valid_input()
        input_["state"]["tick"]["spread"] = 21
        input_["state"]["tick"]["maxSpread"] = 20

        rejected = evaluate_risk_gate(input_)

        assert rejected["status"] == "rejected"
        assert "spread.too_wide" in rejected["reasonCodes"]

        input_["state"]["tick"]["maxSpread"] = 25
        accepted = evaluate_risk_gate(input_)

        assert "spread.too_wide" not in accepted["reasonCodes"]

    @pytest.mark.parametrize(
        ("_name", "stop_loss", "want_code"),
        [
            ("missing", 0, "sl.missing"),
            ("too close", 3335.5, "sl.too_close"),
            ("too far", 3150, "sl.too_far"),
        ],
    )
    def test_rejects_stop_loss_with_go_reason_code(self, _name: str, stop_loss: float, want_code: str) -> None:
        input_ = valid_input()
        input_["plan"]["stopLoss"] = stop_loss

        result = evaluate_risk_gate(input_)

        assert result["status"] == "rejected"
        assert want_code in result["reasonCodes"]

    @pytest.mark.parametrize(
        ("_name", "position_type", "position_strategy", "source_strategy", "want_code"),
        [
            ("same side add", "BUY", "pullback", "pullback", "position.add_not_allowed"),
            ("opposite side hedge", "SELL", "pullback", "pullback", "position.hedge_not_allowed"),
            (
                "missing position strategy keeps backward compatibility",
                "BUY",
                "",
                "ai_signal",
                "position.add_not_allowed",
            ),
        ],
    )
    def test_rejects_position_conflicts(
        self,
        _name: str,
        position_type: str,
        position_strategy: str,
        source_strategy: str,
        want_code: str,
    ) -> None:
        input_ = valid_input()
        input_["sourceStrategy"] = source_strategy
        input_["state"]["positions"] = [
            {"ticket": 123456, "symbol": "XAUUSD", "type": position_type, "lots": 0.1, "strategy": position_strategy}
        ]

        result = evaluate_risk_gate(input_)

        assert result["status"] == "rejected"
        assert want_code in result["reasonCodes"]

    def test_does_not_reject_different_strategy_positions_as_conflicts(self) -> None:
        input_ = valid_input()
        input_["sourceStrategy"] = "ai_signal"
        input_["state"]["positions"] = [
            {"ticket": 123456, "symbol": "XAUUSD", "type": "BUY", "lots": 0.1, "strategy": "pullback"}
        ]

        result = evaluate_risk_gate(input_)

        assert "position.add_not_allowed" not in result["reasonCodes"]
        assert "position.hedge_not_allowed" not in result["reasonCodes"]

    def test_clamps_oversized_lots_using_go_compatible_risk_and_margin_limits(self) -> None:
        input_ = valid_input()
        input_["plan"]["maxLots"] = 3.77

        result = evaluate_risk_gate(input_)

        assert result["status"] == "clamped"
        assert "lots.clamped" in result["reasonCodes"]
        assert result["allowedLots"] > 0
        assert result["allowedLots"] < input_["plan"]["maxLots"]

    @pytest.mark.parametrize("mode", ["close", "reduce"])
    def test_accepts_mode_as_audit_safe_executable_mode(self, mode: str) -> None:
        input_ = valid_input()
        input_["plan"]["mode"] = mode
        input_["plan"]["side"] = "none"
        input_["plan"]["stopLoss"] = 0

        result = evaluate_risk_gate(input_)

        assert result["status"] == "accepted"
        assert "action.audit_safe" in result["reasonCodes"]


class TestMarketFilterParity:
    @pytest.mark.parametrize(
        ("_name", "mutate", "severity", "code"),
        [
            ("closed market", _filter_closed_market, "blocking", "market.closed"),
            ("trade disabled", _filter_trade_disabled, "blocking", "market.trade_not_allowed"),
            ("stale tick", _filter_stale_tick, "blocking", "tick.stale"),
            ("wide spread", _filter_wide_spread, "blocking", "spread.too_wide"),
            ("friday close window", _filter_friday_close_window, "blocking", "session.friday_close_window"),
            ("rollover window", _filter_rollover_window, "warning", "session.rollover_window"),
            ("low liquidity session", _filter_low_liquidity_session, "warning", "session.low_liquidity"),
            ("abnormal ATR expansion", _filter_atr_expansion, "warning", "volatility.atr_expansion"),
        ],
    )
    def test_mirrors_go_filter(
        self, _name: str, mutate: Callable[[dict[str, Any]], None], severity: str, code: str
    ) -> None:
        input_ = valid_market_filter_input()
        mutate(input_)

        result = evaluate_market_filters(input_)

        assert code in result["reason_codes"]
        if severity == "blocking":
            assert result["blocked"] is True
            assert {"code": code, "severity": "blocking"} in result["blocking"]
        else:
            assert {"code": code, "severity": "warning"} in result["warnings"]

    def test_has_no_active_filters_for_normal_markets(self) -> None:
        result = evaluate_market_filters(valid_market_filter_input())

        assert result == {
            "blocked": False,
            "blocking": [],
            "reason_codes": [],
            "warnings": [],
        }

    def test_uses_ea_configured_max_spread_for_market_filters(self) -> None:
        input_ = valid_market_filter_input()
        input_["state"]["tick"]["spread"] = 21
        input_["state"]["tick"]["maxSpread"] = 25

        result = evaluate_market_filters(input_)

        assert "spread.too_wide" not in result["reason_codes"]
        assert result["blocked"] is False

        input_["state"]["tick"]["maxSpread"] = 20
        rejected = evaluate_market_filters(input_)

        assert "spread.too_wide" in rejected["reason_codes"]
        assert rejected["blocked"] is True
