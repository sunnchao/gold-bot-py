"""PostgreSQL store schema bootstrap tests."""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

import backend.persistence.postgres_store as postgres_store


class FakeResult:
    def __init__(self, rows: list[tuple[int]] | None = None) -> None:
        self._rows = rows or []

    def fetchall(self) -> list[tuple[int]]:
        return self._rows


class FakeConnection:
    def __init__(self) -> None:
        self.statements: list[tuple[str, dict | None]] = []

    async def execute(self, statement, params=None):
        sql = str(statement)
        self.statements.append((sql, params))
        if "SELECT version FROM schema_migrations" in sql:
            return FakeResult([(1,), (12,)])
        return FakeResult()


class FakeEngine:
    def __init__(self) -> None:
        self.connection = FakeConnection()
        self.begin_count = 0

    @asynccontextmanager
    async def begin(self):
        self.begin_count += 1
        yield self.connection

    async def dispose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_postgres_store_applies_pending_migrations_once(monkeypatch) -> None:
    engine = FakeEngine()
    monkeypatch.setattr(postgres_store, "create_async_engine", lambda *args, **kwargs: engine)
    monkeypatch.setattr(
        postgres_store,
        "load_migrations",
        lambda: [
            postgres_store.Migration(12, "daily_equity", "CREATE TABLE daily_equity (...);"),
            postgres_store.Migration(13, "bar_close_events", "CREATE TABLE bar_close_events (...);"),
        ],
    )

    store = postgres_store.PostgresEaStore("postgres://user:pass@db/goldbot")
    await store.ensure_schema()
    await store.ensure_schema()

    statements = engine.connection.statements
    assert engine.begin_count == 1
    assert "pg_advisory_xact_lock" in statements[0][0]
    assert sum("CREATE TABLE bar_close_events" in sql for sql, _ in statements) == 1
    assert any(
        "INSERT INTO schema_migrations" in sql
        and params == {"version": 13, "name": "bar_close_events"}
        for sql, params in statements
    )


def test_all_postgres_migrations_are_split_into_single_statements() -> None:
    """asyncpg/SQLAlchemy prepare receives one DDL statement at a time."""
    for migration in postgres_store.load_migrations():
        statements = postgres_store._postgres_migration_statements(migration)
        assert statements, f"{migration.version}_{migration.name}.sql"
        assert all(";" not in statement for statement in statements)


def test_translates_sqlite_autoincrement_for_postgres() -> None:
    migration = postgres_store.Migration(
        11,
        "closed_trades",
        "CREATE TABLE closed_trades (id INTEGER PRIMARY KEY AUTOINCREMENT);",
    )

    assert postgres_store._postgres_migration_statements(migration) == [
        "CREATE TABLE closed_trades (id BIGSERIAL PRIMARY KEY)"
    ]


def test_postgres_alter_table_migrations_are_idempotent() -> None:
    migration = postgres_store.Migration(
        9,
        "position_states_adverse_add_on",
        "ALTER TABLE position_states ADD COLUMN add_on_count INTEGER NOT NULL DEFAULT 0;",
    )

    assert postgres_store._postgres_migration_statements(migration) == [
        "ALTER TABLE position_states ADD COLUMN IF NOT EXISTS add_on_count INTEGER NOT NULL DEFAULT 0"
    ]
