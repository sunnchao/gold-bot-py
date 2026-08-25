"""下单动作转换单元测试(镜像 gold-bot trade-action-converter.test.ts)。

覆盖:
- TRADE_ACTION_TOOLS_LEGACY 形状(无 account_id 字段)
- 账户感知转换 toolUseToTradeAction:手数校验/缺失 account_id/modify/close
- 旧契约转换 toolUseToTradeActionLegacy
"""

from backend.agents.agents._support import get_symbol_profile
from backend.agents.agents.trade_action_converter import (
    TRADE_ACTION_TOOLS,
    TRADE_ACTION_TOOLS_LEGACY,
    tool_use_to_trade_action,
    tool_use_to_trade_action_legacy,
)


def test_keeps_legacy_tools_account_unaware_and_limited_to_open_or_hold_actions():
    """TS it('keeps legacy tools account-unaware and limited to open-or-hold actions')"""
    assert [tool["name"] for tool in TRADE_ACTION_TOOLS_LEGACY] == [
        "place_pending_order",
        "place_market_order",
        "do_nothing",
    ]
    for tool in TRADE_ACTION_TOOLS_LEGACY:
        schema = tool["input_schema"]
        assert "account_id" not in schema["required"]
        assert "account_id" not in schema["properties"]


def test_converts_legacy_market_actions_without_account_id_or_symbol():
    """TS it('converts legacy market actions without account_id or symbol')"""
    action = tool_use_to_trade_action_legacy(
        {
            "name": "place_market_order",
            "input": {
                "account_id": "ignored",
                "symbol": "ignored",
                "side": "buy",
                "stop_loss": 4280,
                "take_profit_1": 4310,
                "lots": 0.1,
                "reason": "legacy",
            },
        },
        4290,
        get_symbol_profile("XAUUSD"),
    )

    assert action == {
        "type": "place_market_order",
        "side": "buy",
        "stop_loss": 4280,
        "take_profit_1": 4310,
        "take_profit_2": None,
        "lots": 0.1,
        "reason": "legacy",
    }


def test_rejects_market_orders_below_the_symbol_minimum_lot_size():
    """TS it('rejects market orders below the symbol minimum lot size')"""
    action = tool_use_to_trade_action(
        {
            "name": "place_market_order",
            "input": {
                "account_id": "90011087",
                "symbol": "GOLDm#",
                "side": "buy",
                "stop_loss": 4280,
                "take_profit_1": 4310,
                "lots": 0.05,
                "reason": "test",
            },
        },
        4290,
        get_symbol_profile("GOLDm#"),
    )

    assert action["type"] == "do_nothing"
    assert action["account_id"] == "90011087"
    assert "outside allowed range 0.1-0.5" in action["reasoning"]


def test_allows_the_platform_minimum_lot_size_for_us100_cash():
    """TS it('allows the platform minimum lot size for US100Cash')"""
    action = tool_use_to_trade_action(
        {
            "name": "place_market_order",
            "input": {
                "account_id": "90011087",
                "symbol": "US100Cash",
                "side": "buy",
                "stop_loss": 25000,
                "take_profit_1": 25200,
                "lots": 0.01,
                "reason": "test",
            },
        },
        25100,
        get_symbol_profile("US100Cash"),
    )

    assert action["type"] == "place_market_order"
    assert action["lots"] == 0.01


def test_rejects_pending_orders_above_the_symbol_maximum_lot_size():
    """TS it('rejects pending orders above the symbol maximum lot size')"""
    action = tool_use_to_trade_action(
        {
            "name": "place_pending_order",
            "input": {
                "account_id": "90011087",
                "symbol": "XAUUSD",
                "side": "buy",
                "entry_price": 4280,
                "stop_loss": 4270,
                "take_profit_1": 4310,
                "lots": 0.6,
                "order_type": "limit",
                "reason": "test",
            },
        },
        4290,
        get_symbol_profile("XAUUSD"),
    )

    assert action["type"] == "do_nothing"
    assert action["account_id"] == "90011087"
    assert "outside allowed range 0.01-0.5" in action["reasoning"]


def test_drops_opening_tool_calls_without_account_id():
    """TS it('drops opening tool calls without account_id')"""
    action = tool_use_to_trade_action(
        {
            "name": "place_market_order",
            "input": {
                "symbol": "XAUUSD",
                "side": "buy",
                "stop_loss": 4280,
                "take_profit_1": 4310,
                "lots": 0.1,
                "reason": "test",
            },
        },
        4290,
        get_symbol_profile("XAUUSD"),
    )

    assert action is None


def test_converts_modify_and_close_tools_with_account_aware_identity_fields():
    """TS it('converts modify and close tools with account-aware identity fields')"""
    assert tool_use_to_trade_action(
        {
            "name": "modify_order",
            "input": {
                "account_id": "90011087",
                "symbol": "XAUUSD",
                "ticket": 12345,
                "new_sl": 4280,
                "reason": "tighten stop",
            },
        },
        4290,
        get_symbol_profile("XAUUSD"),
    ) == {
        "type": "modify_order",
        "account_id": "90011087",
        "symbol": "XAUUSD",
        "ticket": 12345,
        "new_sl": 4280,
        "new_tp1": None,
        "new_tp2": None,
        "reason": "tighten stop",
    }

    assert tool_use_to_trade_action(
        {
            "name": "close_order",
            "input": {
                "account_id": "90011087",
                "symbol": "XAUUSD",
                "ticket": 12345,
                "reason": "close",
            },
        },
        4290,
        get_symbol_profile("XAUUSD"),
    ) == {
        "type": "close_order",
        "account_id": "90011087",
        "symbol": "XAUUSD",
        "ticket": 12345,
        "reason": "close",
    }


def test_account_aware_tool_schemas_require_account_id():
    """Python 侧补充:账户感知工具 schema 必须包含 account_id(与 legacy 对照)。"""
    for tool in TRADE_ACTION_TOOLS:
        schema = tool["input_schema"]
        assert "account_id" in schema["required"]
        assert "account_id" in schema["properties"]
