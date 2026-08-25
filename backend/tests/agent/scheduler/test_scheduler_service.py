"""Mirror of gold-bot scheduler.service.test.ts."""

from __future__ import annotations

from typing import Any

import pytest

from backend.agents.scheduler.scheduler import SchedulerService


def create_queue_mock() -> dict[str, Any]:
    return {
        "get_repeatable_jobs_calls": [],
        "remove_repeatable_by_key_calls": [],
        "add_calls": [],
    }


class FakeQueue:
    def __init__(self, repeatable: list[dict[str, Any]] | None = None) -> None:
        self.repeatable = list(repeatable or [])
        self.removed: list[str] = []
        self.added: list[tuple[str, dict[str, Any], dict[str, Any]]] = []

    async def get_repeatable_jobs(self) -> list[dict[str, Any]]:
        return self.repeatable

    async def remove_repeatable_by_key(self, key: str) -> None:
        self.removed.append(key)

    async def add(self, name: str, data: dict[str, Any], opts: dict[str, Any]) -> None:
        self.added.append((name, data, opts))


class FakeConfig:
    def __init__(
        self,
        schedule_cron: str,
        static_accounts: list[Any],
        analysis_trigger_mode: str = "cron",
    ) -> None:
        self.schedule_cron = schedule_cron
        self.analysis_trigger_mode = analysis_trigger_mode
        self.static_accounts = static_accounts
        self.updated: list[tuple[str, list[str]]] = []

    def update_account_symbols(self, account_id: str, symbols: list[str]) -> None:
        self.updated.append((account_id, list(symbols)))


class FakeGoldbotApi:
    def __init__(
        self,
        result: dict[str, Any] | None = None,
        error: Exception | None = None,
        accounts: list[dict[str, Any]] | None = None,
        accounts_error: Exception | None = None,
        symbols_by_account: dict[str, list[str]] | None = None,
    ) -> None:
        self.result = result
        self.error = error
        self.accounts = list(accounts or [])
        self.accounts_error = accounts_error
        self.symbols_by_account = symbols_by_account
        self.calls: list[str] = []
        self.account_list_calls = 0

    async def fetch_accounts(self) -> list[dict[str, Any]]:
        self.account_list_calls += 1
        if self.accounts_error is not None:
            raise self.accounts_error
        return list(self.accounts)

    async def fetch_account_symbols(self, account_id: str) -> dict[str, list[str]]:
        self.calls.append(account_id)
        if self.error is not None:
            raise self.error
        if self.symbols_by_account is not None:
            return {"symbols": list(self.symbols_by_account.get(account_id, []))}
        return self.result or {"symbols": []}


@pytest.mark.asyncio
async def test_registers_the_configured_repeatable_cron_job_on_init() -> None:
    analysis_queue = FakeQueue(repeatable=[{"key": "old-repeat"}])
    position_poll_queue = FakeQueue(repeatable=[{"key": "old-poll-repeat"}])
    config = FakeConfig(
        schedule_cron="*/5 * * * *",
        static_accounts=[{"id": "acc-001", "symbols": ["XAUUSD"]}],
    )
    goldbot_api = FakeGoldbotApi(result={"symbols": ["XAUUSD", "US100Cash"]})
    service = SchedulerService(analysis_queue, position_poll_queue, config, goldbot_api)

    await service.on_module_init()
    await service.on_module_destroy()

    assert goldbot_api.calls == ["acc-001"]
    assert config.updated == [("acc-001", ["XAUUSD", "US100Cash"])]
    assert analysis_queue.removed == ["old-repeat"]
    assert analysis_queue.added[0][0] == "scheduled-analysis"
    assert analysis_queue.added[0][1] == {}
    assert analysis_queue.added[0][2]["repeat"] == {"pattern": "*/5 * * * *"}
    assert position_poll_queue.removed == ["old-poll-repeat"]
    assert position_poll_queue.added[0][0] == "position-poll"
    assert position_poll_queue.added[0][1] == {}
    assert position_poll_queue.added[0][2]["repeat"] == {"every": 15 * 60 * 1000}
    assert goldbot_api.account_list_calls == 1
    assert service.get_status()["running"] is True
    assert isinstance(service.get_status()["lastRunTime"], str)


@pytest.mark.asyncio
async def test_removes_cron_analysis_job_in_bar_close_mode() -> None:
    analysis_queue = FakeQueue(repeatable=[{"key": "old-repeat"}])
    position_poll_queue = FakeQueue()
    config = FakeConfig("*/30 * * * *", [], analysis_trigger_mode="bar_close")
    service = SchedulerService(analysis_queue, position_poll_queue, config, FakeGoldbotApi())

    await service.on_module_init()
    await service.on_module_destroy()

    assert analysis_queue.removed == ["old-repeat"]
    assert analysis_queue.added == []
    assert position_poll_queue.added[0][0] == "position-poll"


@pytest.mark.asyncio
async def test_discovers_new_ea_accounts_and_their_symbols() -> None:
    analysis_queue = FakeQueue()
    position_poll_queue = FakeQueue()
    config = FakeConfig(schedule_cron="*/5 * * * *", static_accounts=[])
    goldbot_api = FakeGoldbotApi(
        accounts=[{"account_id": "90011087"}, {"account_id": "90022000"}],
        symbols_by_account={
            "90011087": ["XAUUSD", "XAGUSD"],
            "90022000": ["GBPJPY"],
        },
    )
    service = SchedulerService(analysis_queue, position_poll_queue, config, goldbot_api)

    await service.on_module_init()
    await service.on_module_destroy()

    assert goldbot_api.account_list_calls == 1
    assert sorted(goldbot_api.calls) == ["90011087", "90022000"]
    assert sorted(config.updated) == [
        ("90011087", ["XAUUSD", "XAGUSD"]),
        ("90022000", ["GBPJPY"]),
    ]


@pytest.mark.asyncio
async def test_adds_newly_registered_ea_accounts_to_existing_runtime_set() -> None:
    analysis_queue = FakeQueue()
    position_poll_queue = FakeQueue()
    config = FakeConfig(
        schedule_cron="*/5 * * * *",
        static_accounts=[{"id": "acc-001", "symbols": ["XAUUSD"]}],
    )
    goldbot_api = FakeGoldbotApi(
        accounts=[{"account_id": "acc-001"}, {"account_id": "90011087"}],
        symbols_by_account={
            "acc-001": ["XAUUSD", "US100Cash"],
            "90011087": ["GBPJPY"],
        },
    )
    service = SchedulerService(analysis_queue, position_poll_queue, config, goldbot_api)

    await service.on_module_init()
    await service.on_module_destroy()

    assert sorted(goldbot_api.calls) == ["90011087", "acc-001"]
    assert sorted(config.updated) == [
        ("90011087", ["GBPJPY"]),
        ("acc-001", ["XAUUSD", "US100Cash"]),
    ]


@pytest.mark.asyncio
async def test_falls_back_to_static_symbols_when_goldbot_returns_no_symbols() -> None:
    analysis_queue = FakeQueue()
    position_poll_queue = FakeQueue()
    config = FakeConfig(
        schedule_cron="*/5 * * * *",
        static_accounts=[{"id": "acc-001", "symbols": ["XAUUSD", "GBPJPY"]}],
    )
    goldbot_api = FakeGoldbotApi(result={"symbols": []})
    service = SchedulerService(analysis_queue, position_poll_queue, config, goldbot_api)

    await service.on_module_init()
    await service.on_module_destroy()

    assert config.updated == [("acc-001", ["XAUUSD", "GBPJPY"])]


@pytest.mark.asyncio
async def test_falls_back_to_static_symbols_when_goldbot_fetch_fails() -> None:
    analysis_queue = FakeQueue()
    position_poll_queue = FakeQueue()
    config = FakeConfig(
        schedule_cron="*/5 * * * *",
        static_accounts=[{"id": "acc-001", "symbols": ["XAUUSD", "GBPJPY"]}],
    )
    goldbot_api = FakeGoldbotApi(error=RuntimeError("goldbot unavailable"))
    service = SchedulerService(analysis_queue, position_poll_queue, config, goldbot_api)

    await service.on_module_init()
    await service.on_module_destroy()

    assert config.updated == [("acc-001", ["XAUUSD", "GBPJPY"])]


@pytest.mark.asyncio
async def test_status_reports_not_running_before_init() -> None:
    service = SchedulerService(FakeQueue(), FakeQueue(), FakeConfig("*/5 * * * *", []), FakeGoldbotApi())
    assert service.get_status() == {"running": False, "lastRunTime": None}
