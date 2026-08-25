"""SQLite store 特有行为:迁移在创建时执行、跨重开持久化(镜像 index.spec.ts 最后一节)。"""

from __future__ import annotations

import pytest

from backend.persistence.store import create_sqlite_store


@pytest.fixture
def db_path(tmp_path) -> str:
    return str(tmp_path / "persist.db")


async def test_persists_ea_lifecycle_snapshots_and_queued_commands_across_reopen(db_path: str) -> None:
    store = create_sqlite_store(db_path)
    await store.save_registration({"account_id": "acc-1", "broker": "Demo"})
    await store.save_tick({"account_id": "acc-1", "symbol": "XAUUSD", "bid": 3335.0})
    await store.save_bars({"account_id": "acc-1", "symbol": "XAUUSD", "timeframe": "H1", "bars": [{"time": "1"}]})
    await store.enqueue_command(
        "acc-1",
        {"account_id": "acc-1", "command_id": "cmd_1", "action": "SIGNAL", "source": "ea_analysis", "symbol": "XAUUSD"},
    )
    assert await store.claim_bar_close_event("acc-1", "XAUUSD", "M30", "2026-08-25T08:00:00Z") is True
    await store.close()

    reopened = create_sqlite_store(db_path)
    try:
        assert (await reopened.get_registration("acc-1")) == {"account_id": "acc-1", "broker": "Demo"}
        assert (await reopened.get_latest_tick("acc-1", "XAUUSD"))["bid"] == 3335.0
        assert len(await reopened.get_bars("acc-1", "XAUUSD", "H1")) == 1
        command = await reopened.get_command("cmd_1")
        assert command is not None and command["status"] == "queued"
        assert await reopened.claim_bar_close_event("acc-1", "XAUUSD", "M30", "2026-08-25T08:00:00Z") is False
    finally:
        await reopened.close()


async def test_reopen_does_not_reapply_migrations(db_path: str) -> None:
    store = create_sqlite_store(db_path)
    await store.close()
    reopened = create_sqlite_store(db_path)  # 二次打开:迁移已应用,应跳过
    try:
        assert await reopened.get_runtime_mode("acc-1") == "oracle"
    finally:
        await reopened.close()
