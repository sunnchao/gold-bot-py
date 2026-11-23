"""迁移执行器测试(镜像 gold-bot packages/persistence/src/migrate.spec.ts)。"""

from __future__ import annotations

import json
import sqlite3

import pytest

from backend.persistence.migrate import load_migrations, run_migrations_sync


@pytest.fixture
def db_path(tmp_path) -> str:
    return str(tmp_path / "test.db")


def test_loads_all_migrations_in_order() -> None:
    migrations = load_migrations()
    assert len(migrations) > 0
    assert migrations[0].version == 1
    assert migrations[0].name == "init"
    for i in range(1, len(migrations)):
        assert migrations[i].version > migrations[i - 1].version


def test_runs_migrations_on_fresh_database(db_path: str) -> None:
    run_migrations_sync(db_path=db_path)
    conn = sqlite3.connect(db_path)
    try:
        applied = conn.execute("SELECT version, name FROM schema_migrations ORDER BY version").fetchall()
        assert len(applied) > 0
        assert applied[0][0] == 1
        assert applied[0][1] == "init"
    finally:
        conn.close()


def test_skips_already_applied_migrations(db_path: str) -> None:
    run_migrations_sync(db_path=db_path)
    conn = sqlite3.connect(db_path)
    first_run = conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
    conn.close()

    run_migrations_sync(db_path=db_path)

    conn = sqlite3.connect(db_path)
    try:
        second_run = conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
        assert second_run == first_run
    finally:
        conn.close()


def test_creates_all_expected_tables(db_path: str) -> None:
    run_migrations_sync(db_path=db_path)
    conn = sqlite3.connect(db_path)
    try:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for expected in (
            "schema_migrations",
            "ea_snapshots",
            "ea_events",
            "runtime_state",
            "runtime_commands",
            "position_states",
            "shadow_comparisons",
            "shadow_snapshots",
            "decision_events",
            "tokens",
            "token_accounts",
            "closed_trades",
            "daily_equity",
            "bar_close_events",
            "watch_ledgers",
            "patrol_logs",
            "market_bars",
        ):
            assert expected in tables, f"missing table {expected}"
    finally:
        conn.close()


def test_migration_0009_adds_adverse_add_on_columns(db_path: str) -> None:
    run_migrations_sync(db_path=db_path)
    conn = sqlite3.connect(db_path)
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(position_states)")}
        for expected in (
            "add_on_count",
            "last_add_on_time",
            "last_add_on_price",
            "group_id",
            "group_avg_entry",
            "group_best_sl",
        ):
            assert expected in columns, f"missing column {expected}"
        versions = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
        assert 9 in versions
    finally:
        conn.close()


def test_migration_0014_deduplicates_and_canonicalizes_mixed_case_keys(db_path: str) -> None:
    migrations = load_migrations()
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
              version INTEGER PRIMARY KEY,
              name TEXT NOT NULL,
              applied_at TEXT NOT NULL
            )
            """
        )
        for migration in migrations:
            if migration.version >= 14:
                continue
            conn.executescript(migration.sql)
            conn.execute(
                "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
                (migration.version, migration.name, "2026-11-22T00:00:00Z"),
            )

        conn.executemany(
            "INSERT INTO ea_snapshots (kind, account_id, symbol, timeframe, payload_json, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [
                (
                    "bars",
                    "acc-1",
                    "GOLDm#",
                    "H1",
                    json.dumps({"symbol": "GOLDm#", "v": "old"}),
                    "2026-08-25 06:26:22",
                ),
                (
                    "bars",
                    "acc-1",
                    "GOLDM#",
                    "h1",
                    json.dumps({"symbol": "GOLDm#", "v": "new"}),
                    "2026-11-23 08:11:17",
                ),
                (
                    "tick",
                    "acc-1",
                    "silverm#",
                    "",
                    json.dumps({"symbol": "silverm#", "v": "noncanon"}),
                    "2026-11-23 09:00:00",
                ),
                (
                    "tick",
                    "acc-1",
                    "SILVERM#",
                    "",
                    json.dumps({"symbol": "SILVERm#", "v": "canon_tie"}),
                    "2026-11-23 09:00:00",
                ),
                (
                    "positions",
                    "acc-1",
                    " XAUUSD ",
                    "",
                    json.dumps({"symbol": "XAUUSD"}),
                    "2026-11-23 09:00:00",
                ),
            ],
        )
        conn.executemany(
            "INSERT INTO position_states (account_id, symbol, ticket, tp1_hit, last_modify_time) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                ("acc-1", "GOLDm#", 9, 0, "2026-08-25T06:00:00Z"),
                ("acc-1", "GOLDM#", 9, 1, "2026-11-23T08:00:00Z"),
                ("acc-1", " silverm# ", 10, 1, "2026-11-23T08:00:00Z"),
            ],
        )
        conn.executemany(
            "INSERT INTO bar_close_events (account_id, symbol, timeframe, bar_time, claimed_at) VALUES (?, ?, ?, ?, ?)",
            [
                ("acc-1", "GOLDm#", "m15", "100", "2026-08-25 06:00:00"),
                ("acc-1", "GOLDM#", "M15", "100", "2026-11-23 08:00:00"),
                ("acc-1", " us100cash ", "h1", "200", "2026-11-23 08:00:00"),
            ],
        )
        conn.executemany(
            "INSERT INTO shadow_snapshots (account_id, symbol, source, payload_json, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                ("acc-1", "GOLDm#", "ea_analysis", json.dumps({"v": "old"}), "2026-08-25 06:00:00"),
                ("acc-1", "GOLDM#", "ea_analysis", json.dumps({"v": "new"}), "2026-11-23 08:00:00"),
            ],
        )
        conn.commit()
    finally:
        conn.close()

    run_migrations_sync(db_path=db_path)
    run_migrations_sync(db_path=db_path)

    conn = sqlite3.connect(db_path)
    try:
        versions = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
        assert 14 in versions

        snapshots = conn.execute(
            "SELECT kind, symbol, timeframe, payload_json FROM ea_snapshots ORDER BY kind, symbol"
        ).fetchall()
        assert [
            (k, s, tf, json.loads(p)["v"] if "v" in json.loads(p) else None)
            for k, s, tf, p in snapshots
        ] == [
            ("bars", "GOLDM#", "H1", "new"),
            ("positions", "XAUUSD", "", None),
            ("tick", "SILVERM#", "", "canon_tie"),
        ]
        # payload_json 内部保留 EA 原始符号大小写
        bars_payload = json.loads(next(p for k, _, _, p in snapshots if k == "bars"))
        assert bars_payload["symbol"] == "GOLDm#"

        pos_states = conn.execute(
            "SELECT symbol, ticket, tp1_hit FROM position_states ORDER BY ticket"
        ).fetchall()
        assert pos_states == [("GOLDM#", 9, 1), ("SILVERM#", 10, 1)]

        bar_events = conn.execute(
            "SELECT symbol, timeframe, bar_time FROM bar_close_events ORDER BY bar_time"
        ).fetchall()
        assert bar_events == [("GOLDM#", "M15", "100"), ("US100CASH", "H1", "200")]

        shadows = conn.execute("SELECT symbol, payload_json FROM shadow_snapshots").fetchall()
        assert len(shadows) == 1
        assert shadows[0][0] == "GOLDM#"
        assert json.loads(shadows[0][1]) == {"v": "new"}
    finally:
        conn.close()
