"""SOP 闭环集成回放测试: 模拟 2026-11-19 ~ 21 金银盯盘全周期、风控拦截与 Admin 查询接口。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from backend.agents.graph.compose import compose_final_signal
from backend.agents.types.analysis import ArbitrationResult, TechnicalAnalysis, TradeRecommendation
from backend.api.app import create_api_app
from backend.persistence.store import create_sqlite_store
from backend.services.sop.alerts import AlertRouter
from backend.services.sop.patrol import PatrolService


def _make_bars(count: int, *, start: float, step: float, spread: float = 0.8) -> list[dict[str, Any]]:
    bars: list[dict[str, Any]] = []
    for i in range(count):
        close = round(start + i * step, 3)
        bars.append(
            {
                "time": f"2026-11-{1 + (i % 18):02d}T{i % 24:02d}:00:00Z",
                "open": close - step * 0.4,
                "high": close + spread,
                "low": close - spread,
                "close": close,
            }
        )
    return bars


class FakeFeishu:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send(self, payload: dict[str, Any]) -> None:
        self.sent.append(payload)


def test_compose_blocks_counter_htf_m15_and_exhausted_logic_unless_double_evidence() -> None:
    ledger = {
        "htf_anchor": {
            "macro_bias": "bullish_breakout",
            "intraday_direction": "long",
            "short_term_targets": [65.0],
            "long_term_targets": [68.5],
            "veto_boundary": {"long_invalid_below": 61.44},
        },
        "playbooks": [{"id": "pb-1", "state": "armed", "direction": "long"}],
    }
    tech = TechnicalAnalysis(
        bias="bearish",
        confidence=75,
        phase="consolidation",
        indicators_summary="M15 pullback",
        support_levels=[],
        resistance_levels=[],
        recommendation="none",
        rationale="M15 pullback",
    )
    arb = ArbitrationResult(
        final_direction="short",
        action="open",
        confidence=78,
        reasoning="M15 尝试反手做空",
        phase="correction",
        primary_contradiction="none",
        united_front_analysis="none",
        trade_recommendation=TradeRecommendation(
            direction="sell",
            entry_price=64.0,
            stop_loss=64.8,
            take_profit_1=62.5,
            take_profit_2=None,
            risk_reward_ratio=1.9,
            position_size_lots=0.1,
            rationale="M15 短线回撤",
        ),
    )

    # 1. M15 无双重证据逆转 HTF 多头 -> 被硬拦截
    blocked = compose_final_signal(
        {
            "accountId": "acc-1",
            "symbol": "XAGUSD",
            "analysisRole": "m15_entry",
            "previousLedger": ledger,
            "patrolContext": {"trigger_reasons": ["m15_close"]},
            "technicalAnalysis": tech,
            "arbitration": arb,
            "comprehensiveAnalysis": {"sop_context": {"analysis_role": "m15_entry"}},
        }
    )
    assert blocked is not None
    assert blocked.arbitration is not None
    assert blocked.arbitration["action"] == "hold"
    assert blocked.trade_plan is None
    assert blocked.sop_context is not None
    assert "sop.m15_counter_htf_blocked" in blocked.sop_context["sop_blocks"]
    assert "sop.reverse_missing_double_evidence" in blocked.sop_context["sop_blocks"]

    # 2. 有双重证据 (veto_boundary_breached + false_breakout_reclaim) 时允许反转
    allowed = compose_final_signal(
        {
            "accountId": "acc-1",
            "symbol": "XAGUSD",
            "analysisRole": "m15_entry",
            "previousLedger": ledger,
            "patrolContext": {
                "trigger_reasons": ["veto_boundary_breached", "false_breakout_reclaim"],
                "double_evidence": True,
            },
            "technicalAnalysis": tech,
            "arbitration": arb,
        }
    )
    assert allowed is not None
    assert allowed.arbitration is not None
    assert allowed.arbitration["action"] == "open"
    assert allowed.trade_plan is not None

    # 3. 剧本已全部穷尽 -> sop.logic_exhausted 阻止继续追单
    exhausted_ledger = {**ledger, "playbooks": [{"id": "pb-1", "state": "exhausted"}]}
    exhausted = compose_final_signal(
        {
            "accountId": "acc-1",
            "symbol": "XAGUSD",
            "analysisRole": "m15_entry",
            "previousLedger": exhausted_ledger,
            "patrolContext": {"trigger_reasons": ["logic_exhausted"]},
            "technicalAnalysis": tech,
            "arbitration": ArbitrationResult(
                final_direction="long",
                action="open",
                confidence=76,
                reasoning="追高",
                phase="trending_up",
                primary_contradiction="none",
                united_front_analysis="none",
            ),
        }
    )
    assert exhausted is not None
    assert exhausted.sop_context is not None
    assert "sop.logic_exhausted" in exhausted.sop_context["sop_blocks"]

    # 4. 从 HTF hold + bearish 实盘推导出的空头账本能成功拦截 M15 逆势做多
    from datetime import UTC, datetime

    from backend.agents.agents.comprehensive_analyst import _build_sop_context
    from backend.services.sop.ledger import reconcile_ledger_after_review

    live_payload = {
        "market": {"bid": 3610.0, "ask": 3610.5},
        "daily_structure": {"status": "ok", "macro_bias": "bearish", "prior_major_high": 3650.0},
        "confluence_levels": [
            {"price": 3630.2, "role": "resistance", "tier": "core", "sources": ["H1_SWING"]},
            {"price": 3596.08, "role": "support", "tier": "core", "sources": ["H4_SWING"]},
        ],
    }
    live_res = {
        "technical": {"bias": "bearish", "confidence": 74},
        "risk": {},
        "arbitration": {"final_direction": "hold", "action": "hold", "reasoning": "需等待反弹拒绝确认"},
    }
    live_sop_ctx = _build_sop_context(
        result=live_res,
        payload=live_payload,
        options={"analysisRole": "htf_anchor"},
    )
    reconciled_short = reconcile_ledger_after_review(
        None,
        account_id="81124211",
        symbol="GOLDM#",
        role="htf_anchor",
        review_result={"bias": "bearish", "confidence": 74, "sop_context": live_sop_ctx},
        daily_structure=live_payload["daily_structure"],
        confluence_levels=live_payload["confluence_levels"],
        current_price=3610.0,
        now=datetime(2026, 11, 23, 9, 36, tzinfo=UTC),
    )
    assert reconciled_short["htf_anchor"]["intraday_direction"] == "short"
    assert reconciled_short["htf_anchor"]["veto_boundary"]["short_invalid_above"] == 3630.2

    counter_buy = compose_final_signal(
        {
            "accountId": "81124211",
            "symbol": "GOLDM#",
            "analysisRole": "m15_entry",
            "previousLedger": reconciled_short,
            "patrolContext": {"trigger_reasons": ["m15_close"]},
            "technicalAnalysis": TechnicalAnalysis(
                bias="bullish",
                confidence=80,
                phase="consolidation",
                indicators_summary="M15 bounce",
                support_levels=[],
                resistance_levels=[],
                recommendation="none",
                rationale="M15 bounce",
            ),
            "arbitration": ArbitrationResult(
                final_direction="buy",
                action="open",
                confidence=80,
                reasoning="M15 短线反弹尝试做多",
                phase="correction",
                primary_contradiction="none",
                united_front_analysis="none",
                trade_recommendation=TradeRecommendation(
                    direction="buy",
                    entry_price=3611.0,
                    stop_loss=3602.0,
                    take_profit_1=3625.0,
                    take_profit_2=None,
                    risk_reward_ratio=1.6,
                    position_size_lots=0.1,
                    rationale="M15 bounce buy",
                ),
            ),
        }
    )
    assert counter_buy is not None
    assert counter_buy.arbitration is not None
    assert counter_buy.arbitration["action"] == "hold"
    assert counter_buy.trade_plan is None
    assert counter_buy.sop_context is not None
    assert "sop.m15_counter_htf_blocked" in counter_buy.sop_context["sop_blocks"]

    # 5. 已移除日 ATR 预算耗尽限制: 即使 payload["day_range"]["range_exhausted"]=True 也不再拦截顺势开仓
    atr_unblocked = compose_final_signal(
        {
            "accountId": "acc-1",
            "symbol": "XAGUSD",
            "analysisRole": "m15_entry",
            "previousLedger": {
                **ledger,
                "playbooks": [{"id": "pb-1", "state": "exhausted", "direction": "long"}],
            },
            "payload": {"day_range": {"status": "ok", "atr_consumed_pct": 140.0, "range_exhausted": True}},
            "patrolContext": {"trigger_reasons": ["m15_close"]},
            "technicalAnalysis": TechnicalAnalysis(
                bias="bullish",
                confidence=80,
                phase="trending",
                indicators_summary="M15 continuation",
                support_levels=[],
                resistance_levels=[],
                recommendation="none",
                rationale="M15 continuation",
            ),
            "arbitration": ArbitrationResult(
                final_direction="buy",
                action="open",
                confidence=82,
                reasoning="顺高周期多头方向入场",
                phase="markup",
                primary_contradiction="none",
                united_front_analysis="none",
                trade_recommendation=TradeRecommendation(
                    direction="buy",
                    entry_price=64.0,
                    stop_loss=63.2,
                    take_profit_1=65.2,
                    take_profit_2=None,
                    risk_reward_ratio=1.5,
                    position_size_lots=0.1,
                    rationale="M15顺势回踩买入",
                ),
            ),
        }
    )
    assert atr_unblocked is not None
    assert atr_unblocked.arbitration is not None
    assert atr_unblocked.arbitration["action"] == "open"
    assert atr_unblocked.trade_plan is not None

    # 6. 收盘后日/周复盘角色绝不产出可下单 trade_plan
    daily_review_sig = compose_final_signal(
        {
            "accountId": "acc-1",
            "symbol": "XAGUSD",
            "analysisRole": "daily_close_review",
            "previousLedger": ledger,
            "technicalAnalysis": tech,
            "arbitration": arb,
        }
    )
    assert daily_review_sig is not None
    assert daily_review_sig.trade_plan is None
    assert "sop.m15_counter_htf_blocked" not in (daily_review_sig.sop_context or {}).get("sop_blocks", [])


async def test_sqlite_closed_loop_replay_and_admin_endpoints(tmp_path) -> None:
    db_path = tmp_path / "sop_replay.db"
    store = create_sqlite_store(str(db_path))
    feishu = FakeFeishu()
    clock = [datetime(2026, 11, 19, 8, 0, tzinfo=UTC)]

    try:
        # 种子账户与白银 D1/H4/H1/M15 突破行情
        await store.save_registration({"account_id": "acc-1", "ai_symbols": ["XAGUSD", "GOLDm#"]})
        await store.save_heartbeat({"account_id": "acc-1", "market_open": True, "balance": 10000})
        await store.save_tick(
            {"account_id": "acc-1", "symbol": "XAGUSD", "bid": 62.5, "ask": 62.53, "spread": 3.0}
        )
        await store.save_bars(
            {
                "account_id": "acc-1",
                "symbol": "XAGUSD",
                "timeframe": "D1",
                "bars": _make_bars(20, start=53.0, step=0.5),
            }
        )
        await store.save_bars(
            {
                "account_id": "acc-1",
                "symbol": "XAGUSD",
                "timeframe": "H4",
                "bars": _make_bars(20, start=58.0, step=0.25),
            }
        )
        await store.save_bars(
            {
                "account_id": "acc-1",
                "symbol": "XAGUSD",
                "timeframe": "H1",
                "bars": _make_bars(20, start=60.0, step=0.12),
            }
        )
        await store.save_bars(
            {
                "account_id": "acc-1",
                "symbol": "XAGUSD",
                "timeframe": "M15",
                "bars": _make_bars(20, start=61.0, step=0.08),
            }
        )

        class ReplayWorkflow:
            async def run(self, _acc: str, _sym: str, state: dict[str, Any]) -> dict[str, Any]:
                role = state.get("analysisRole")
                if role == "htf_anchor":
                    return {
                        "ai_result": {
                            "bias": "bullish",
                            "confidence": 85,
                            "suggested_sl": 61.44,
                            "trade_plan": {"entry_price": 62.6, "stop_loss": 61.44, "take_profits": [64.2, 65.8]},
                            "sop_context": {
                                "macro_bias": "bullish_breakout",
                                "intraday_direction": "long",
                                "short_term_targets": [64.2, 65.8],
                                "long_term_targets": [68.5],
                                "playbooks": [
                                    {
                                        "id": "pb-xag-1",
                                        "role": "primary",
                                        "direction": "long",
                                        "state": "armed",
                                        "trigger_zone": {"low": 62.6, "high": 63.1},
                                        "entry_price": 62.7,
                                        "stop_loss": 61.44,
                                        "targets": [64.2, 65.8],
                                        "invalid_if": {"price_below": 61.44},
                                    }
                                ],
                            },
                        }
                    }
                return {
                    "ai_result": {
                        "bias": "bullish",
                        "confidence": 88,
                        "trade_action": "BUY",
                        "trade_plan": {"entry_price": 62.8, "stop_loss": 61.8, "take_profits": [64.2]},
                    }
                }

        patrol = PatrolService(
            store=store,
            workflow_service=ReplayWorkflow(),
            alert_router=AlertRouter(feishu=feishu, now_fn=lambda: clock[0]),
            now_fn=lambda: clock[0],
        )

        # Day 1 (11-19): HTF 锚定初始化 -> 无动作静默 -> M15 突破触发
        r1 = await patrol.run_patrol("acc-1", "XAGUSD", trigger_type="patrol")
        assert r1["htf_ran"] is True

        clock[0] += timedelta(minutes=15)
        r_trigger = await patrol.run_patrol(
            "acc-1",
            "XAGUSD",
            trigger_type="m15_close",
            snapshot={
                "price": 62.8,
                "bars_by_tf": {"M15": [{"close": 62.8}], "H1": [{"close": 62.8}]},
                "market_open": True,
                "data_stale": False,
            },
        )
        assert r_trigger["m15_ran"] is True
        assert r_trigger["alert_level"] == "actionable"

        # 存入一条含 '#' 的微型合约账本, 验证 Admin 接口 URL 解码
        await store.save_watch_ledger(
            "acc-1",
            "GOLDm#",
            {"htf_anchor": {"intraday_direction": "long"}, "playbooks": []},
        )

        app = create_api_app(
            {
                "store": store,
                "valid_tokens": {"admin-tok"},
                "admin_tokens": {"admin-tok"},
                "token_accounts": {"admin-tok": set()},
                "sop_patrol": patrol,
                "sop_patrol_enabled": True,
            }
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            res_ledger = await client.get(
                "/api/v1/sop/ledger/acc-1/XAGUSD",
                headers={"X-API-Token": "admin-tok"},
            )
            assert res_ledger.status_code == 200
            body_l = res_ledger.json()
            assert body_l["ledger"]["htf_anchor"]["intraday_direction"] == "long"

            res_logs = await client.get(
                "/api/v1/sop/patrol-logs/acc-1/XAGUSD?limit=10",
                headers={"X-API-Token": "admin-tok"},
            )
            assert res_logs.status_code == 200
            body_logs = res_logs.json()
            assert len(body_logs["logs"]) >= 2

            res_hash_sym = await client.get(
                "/api/v1/sop/ledger/acc-1/GOLDm%23",
                headers={"X-API-Token": "admin-tok"},
            )
            assert res_hash_sym.status_code == 200
            assert res_hash_sym.json()["symbol"] == "GOLDM#"
    finally:
        await store.close()
