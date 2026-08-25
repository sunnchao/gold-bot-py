"""迁移执行器测试(镜像 gold-bot packages/persistence/src/migrate.spec.ts)。"""

from __future__ import annotations

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
