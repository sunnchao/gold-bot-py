"""Agent 结果查询契约(镜像 apps/app-agent/src/results/results.controller.test.ts)。"""

from __future__ import annotations

import pytest

from backend.agents.results.results import ResultsError, get_results


class FakeStore:
    def __init__(self, rows=None):
        self.rows = rows if rows is not None else []
        self.calls: list[tuple[str, str, int]] = []

    def get_recent_results(self, account_id: str, symbol: str, limit: int):
        self.calls.append((account_id, symbol, limit))
        return self.rows


def test_returns_recent_results_with_valid_limit() -> None:
    rows = [{"id": 1, "account_id": "acc-001", "symbol": "XAUUSD"}]
    store = FakeStore(rows)

    result = get_results(store, "acc-001", "XAUUSD", "5")

    assert store.calls == [("acc-001", "XAUUSD", 5)]
    assert result == {"accountId": "acc-001", "symbol": "XAUUSD", "count": 1, "results": rows}


def test_uses_default_limit_10_when_omitted() -> None:
    store = FakeStore()

    get_results(store, "acc-001", "XAUUSD")

    assert store.calls == [("acc-001", "XAUUSD", 10)]


def test_throws_bad_request_for_invalid_limits() -> None:
    store = FakeStore()

    for limit in ("101", "0", "abc"):
        with pytest.raises(ResultsError, match="limit must be between 1 and 100"):
            get_results(store, "acc-001", "XAUUSD", limit)
    assert store.calls == []
