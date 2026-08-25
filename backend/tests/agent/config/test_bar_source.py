"""镜像 gold-bot `apps/app-agent/src/config/bar-source.service.test.ts`。"""
from dataclasses import asdict

import pytest

from backend.agents.config.bar_source import BarSourceService, atr_of, canonical_symbol


class _FakeConfig:
    @property
    def market_bar_account(self) -> str:
        return "90011087"


def payload(symbol: str) -> dict:
    return {
        "account": {
            "account_id": "90011087",
            "equity": 1,
            "balance": 1,
            "margin": 0,
            "free_margin": 1,
            "currency": "USD",
            "leverage": 100,
        },
        "market": {"symbol": symbol, "bid": 100, "ask": 101, "spread": 1},
        "indicators": {},
        "positions": [],
        "market_status": {"market_open": True, "is_trade_allowed": True, "tradeable": True},
        "strategy_mapping": {},
        "bars": {
            "H1": [
                {"time": "1", "open": 98, "high": 102, "low": 97, "close": 100},
                {"time": "2", "open": 100, "high": 104, "low": 99, "close": 103},
            ]
        },
    }


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("GOLD", "XAUUSD"),
        ("GOLDm#", "XAUUSD"),
        ("SILVERm#", "XAGUSD"),
        ("US100Cash", "US100CASH"),
        ("gbpjpy", "GBPJPY"),
    ],
)
def test_canonicalizes_symbols(raw, expected):
    # TS: it.each([...]) 'canonicalizes %s to %s'
    assert canonical_symbol(raw) == expected


async def test_uses_the_master_account_symbol_when_the_canonical_symbol_is_loaded():
    # TS: bar source service 'uses the master account symbol when the canonical symbol is loaded'
    class _FakeApi:
        async def fetch_account_symbols(self, account_id: str) -> dict:
            return {"symbols": ["XAUUSD", "US100Cash"]}

    service = BarSourceService(_FakeConfig(), _FakeApi())
    resolution = await service.bar_source_for("81124211", "GOLDm#")

    assert asdict(resolution) == {
        "canonicalSymbol": "XAUUSD",
        "sourceAccount": "90011087",
        "sourceSymbol": "XAUUSD",
        "useShared": True,
    }


async def test_falls_back_to_the_account_when_the_master_account_did_not_load_the_symbol():
    # TS: bar source service 'falls back to the account when the master account did not load the symbol'
    class _FakeApi:
        async def fetch_account_symbols(self, account_id: str) -> dict:
            return {"symbols": ["XAUUSD"]}

    service = BarSourceService(_FakeConfig(), _FakeApi())
    resolution = await service.bar_source_for("81124211", "GBPJPY")

    assert asdict(resolution) == {
        "canonicalSymbol": "GBPJPY",
        "sourceAccount": "81124211",
        "sourceSymbol": "GBPJPY",
        "useShared": False,
    }


def test_calculates_atr_from_bars_when_the_payload_does_not_include_an_atr_field():
    # TS: bar source service 'calculates ATR from bars when the payload does not include an ATR field'
    atr = atr_of(payload("XAUUSD"))
    assert atr == pytest.approx(5.0)
