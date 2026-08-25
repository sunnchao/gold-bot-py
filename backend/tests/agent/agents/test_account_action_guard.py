"""账户动作守卫单元测试(镜像 gold-bot account-action-guard.test.ts)。

覆盖:
- validateTradeActionForAccount:签约品种不匹配 / ai_symbols 缺失 / 大小写+空白容忍
- assertTicketBelongsToAccount:跨账户 ticket / ticket 缺失 / symbol 不匹配
"""

from backend.agents.agents.account_action_guard import (
    assert_ticket_belongs_to_account,
    validate_trade_action_for_account,
)


def account_view(**overrides):
    view = {
        "accountId": "A",
        "symbol": "GOLDm#",
        "aiSymbols": ["GOLDm#"],
        "realtimePrice": 3335,
        "atr": 4,
        "payload": {
            "account": {
                "account_id": "A",
                "equity": 10000,
                "balance": 10000,
                "margin": 0,
                "free_margin": 10000,
                "currency": "USD",
                "leverage": 500,
            },
            "market": {"symbol": "GOLDm#", "bid": 3335, "ask": 3335.2, "spread": 0.2},
            "indicators": {},
            "positions": [
                {
                    "ticket": 12345,
                    "symbol": "GOLDm#",
                    "strategy": "ai_signal",
                    "direction": "buy",
                    "entry_price": 3330,
                    "current_price": 3335,
                    "lots": 0.1,
                    "profit": 50,
                    "sl": 3320,
                    "tp": 3360,
                }
            ],
            "market_status": {"market_open": True, "is_trade_allowed": True, "tradeable": True},
            "strategy_mapping": {},
        },
    }
    view.update(overrides)
    return view


def market_order_action(**overrides):
    action = {
        "type": "place_market_order",
        "account_id": "A",
        "symbol": "GOLDm#",
        "side": "buy",
        "stop_loss": 3320,
        "take_profit_1": 3360,
        "lots": 0.1,
        "reason": "guard test",
    }
    action.update(overrides)
    return action


def test_rejects_opening_a_shared_market_symbol_that_is_not_the_account_contract():
    """TS it('rejects opening a shared market symbol that is not the account contract')"""
    result = validate_trade_action_for_account(
        market_order_action(symbol="XAUUSD", reason="wrong contract"),
        account_view(),
    )
    assert result == {"ok": False, "reason": "account.symbol_mismatch"}


def test_fails_closed_when_ai_symbols_are_missing():
    """TS it('fails closed when ai_symbols are missing')"""
    result = validate_trade_action_for_account(
        market_order_action(reason="missing whitelist"),
        account_view(aiSymbols=[]),
    )
    assert result == {"ok": False, "reason": "account.symbol_not_loaded"}


def test_matches_account_symbols_case_insensitively_after_trimming_whitespace():
    """TS it('matches account symbols case-insensitively after trimming whitespace')"""
    result = validate_trade_action_for_account(
        market_order_action(symbol=" goldm# ", reason="case variant"),
        account_view(aiSymbols=[" goldm# "]),
    )
    assert result == {"ok": True}


def test_rejects_cross_account_ticket_confusion_missing_tickets_and_symbol_mismatches():
    """TS it('rejects cross-account ticket confusion, missing tickets, and symbol mismatches')"""
    view = account_view()
    base = {
        "type": "modify_order",
        "account_id": "A",
        "symbol": "GOLDm#",
        "ticket": 12345,
        "new_sl": 3325,
        "reason": "modify",
    }

    assert assert_ticket_belongs_to_account(view, {**base, "account_id": "B"}) == {
        "ok": False,
        "reason": "order.account_mismatch",
    }
    assert assert_ticket_belongs_to_account(view, {**base, "ticket": 99999}) == {
        "ok": False,
        "reason": "order.ticket_not_found",
    }
    assert assert_ticket_belongs_to_account(view, {**base, "symbol": "XAUUSD"}) == {
        "ok": False,
        "reason": "order.symbol_mismatch",
    }

    position_without_symbol = dict(view["payload"]["positions"][0])
    position_without_symbol.pop("symbol", None)
    view_without_symbol = {
        **view,
        "payload": {
            **view["payload"],
            "positions": [position_without_symbol],
        },
    }
    assert assert_ticket_belongs_to_account(view_without_symbol, base) == {
        "ok": False,
        "reason": "order.symbol_mismatch",
    }
