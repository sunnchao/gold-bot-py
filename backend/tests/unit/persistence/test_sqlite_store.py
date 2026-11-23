"""SQLite store 特有行为:迁移在创建时执行、跨重开持久化(镜像 index.spec.ts 最后一节)。"""

from __future__ import annotations

import json
import sqlite3

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


async def test_legacy_mixed_case_rows_read_newest_and_deduplicate(db_path: str) -> None:
    store = create_sqlite_store(db_path)
    await store.close()

    conn = sqlite3.connect(db_path)
    try:
        conn.executemany(
            "INSERT INTO ea_snapshots (kind, account_id, symbol, timeframe, payload_json, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [
                (
                    "bars",
                    "acc-1",
                    "GOLDm#",
                    "h1",
                    json.dumps({"symbol": "GOLDm#", "bars": [{"time": "old", "close": 4652.0}]}),
                    "2026-08-25 06:00:00",
                ),
                (
                    "bars",
                    "acc-1",
                    "GOLDM#",
                    "H1",
                    json.dumps({"symbol": "GOLDM#", "bars": [{"time": "new", "close": 3600.0}]}),
                    "2026-11-23 08:05:00",
                ),
                (
                    "tick",
                    "acc-1",
                    "GOLDM#",
                    "",
                    json.dumps({"symbol": "GOLDM#", "bid": 100.0}),
                    "2026-08-25 06:00:00",
                ),
                (
                    "tick",
                    "acc-1",
                    "GOLDm#",
                    "",
                    json.dumps({"symbol": "GOLDm#", "bid": 3602.0}),
                    "2026-11-23 08:01:00",
                ),
                (
                    "positions",
                    "acc-1",
                    "GOLDm#",
                    "",
                    json.dumps({"symbol": "GOLDm#", "positions": [{"ticket": 1}]}),
                    "2026-08-25 06:00:00",
                ),
                (
                    "positions",
                    "acc-1",
                    "GOLDM#",
                    "",
                    json.dumps({"symbol": "GOLDM#", "positions": [{"ticket": 2}]}),
                    "2026-11-23 08:00:00",
                ),
                (
                    "ai_result",
                    "acc-1",
                    "GOLDm#",
                    "",
                    json.dumps({"account_id": "acc-1", "symbol": "GOLDm#", "bias": "old"}),
                    "2026-08-25 06:00:00",
                ),
                (
                    "ai_result",
                    "acc-1",
                    "GOLDM#",
                    "",
                    json.dumps({"account_id": "acc-1", "symbol": "GOLDM#", "bias": "new"}),
                    "2026-11-23 08:00:00",
                ),
            ],
        )
        conn.executemany(
            "INSERT INTO position_states (account_id, symbol, ticket, tp1_hit, last_modify_time) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                ("acc-1", "GOLDm#", 9, 0, "2026-08-25T06:00:00Z"),
                ("acc-1", "GOLDM#", 9, 1, "2026-11-23T08:00:00Z"),
            ],
        )
        conn.executemany(
            "INSERT INTO shadow_snapshots (account_id, symbol, source, payload_json, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                ("acc-1", "GOLDm#", "ea_analysis", json.dumps({"signal": "old"}), "2026-08-25 06:00:00"),
                ("acc-1", "GOLDM#", "ea_analysis", json.dumps({"signal": "new"}), "2026-11-23 08:00:00"),
            ],
        )
        conn.commit()
    finally:
        conn.close()

    reopened = create_sqlite_store(db_path)
    try:
        assert await reopened.get_bars("acc-1", "goldm#", "h1") == [{"time": "new", "close": 3600.0}]
        tick = await reopened.get_latest_tick("acc-1", "GOLDM#")
        assert tick is not None and tick["bid"] == 3602.0
        assert await reopened.get_positions("acc-1", "GOLDm#") == [{"ticket": 2}]
        assert await reopened.get_positions("acc-1") == [{"ticket": 2}]
        ai_results = await reopened.get_ai_results("acc-1")
        assert len(ai_results) == 1 and ai_results[0]["bias"] == "new"
        states = await reopened.load_position_states("acc-1", "goldm#")
        assert len(states) == 1 and states[0]["tp1_hit"] is True
        shadow = await reopened.get_latest_shadow_snapshot("acc-1", "goldm#", "ea_analysis")
        assert shadow == {"signal": "new"}
        assert await reopened.list_symbols("acc-1") == ["GOLDm#"]
    finally:
        await reopened.close()
