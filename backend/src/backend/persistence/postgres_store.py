"""SQLAlchemy Core async PostgreSQL store.

Postgres schema is managed outside this Python process (existing production DB/migrations).
This adapter intentionally reuses the SQLite query layer because the table contract is
text-first and already mirrors the TS persistence schema. Only SQLite-specific rowid /
INSERT OR IGNORE behavior is replaced with PostgreSQL equivalents.
"""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from backend.persistence.migrate import load_migrations
from backend.persistence.records import Migration
from backend.persistence.sqlite_store import SqliteEaStore


class PostgresEaStore(SqliteEaStore):
    def __init__(self, dsn: str) -> None:
        self._path = dsn
        self._engine = create_async_engine(_sqlalchemy_asyncpg_url(dsn), pool_pre_ping=True)
        self._schema_ready = False

    async def ensure_schema(self) -> None:
        """Apply bundled migrations once before serving PostgreSQL-backed traffic."""
        if self._schema_ready:
            return
        async with self._engine.begin() as conn:
            await conn.execute(text("SELECT pg_advisory_xact_lock(71649512013001)"))
            await conn.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS schema_migrations (
                      version INTEGER PRIMARY KEY,
                      name TEXT NOT NULL,
                      applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
            )
            result = await conn.execute(text("SELECT version FROM schema_migrations"))
            applied_versions = {row[0] for row in result.fetchall()}
            for migration in load_migrations():
                if migration.version in applied_versions:
                    continue
                for statement in _postgres_migration_statements(migration):
                    await conn.execute(text(statement))
                await conn.execute(
                    text(
                        """
                        INSERT INTO schema_migrations (version, name, applied_at)
                        VALUES (:version, :name, CURRENT_TIMESTAMP)
                        """
                    ),
                    {"version": migration.version, "name": migration.name},
                )
        self._schema_ready = True

    def _connect(self) -> AsyncConnection:
        return self._engine.connect()

    @property
    def _row_order_column(self) -> str:
        return "id"

    @property
    def _insert_token_account_ignore_sql(self) -> str:
        return "INSERT INTO token_accounts (token, account_id) VALUES (:token, :account) ON CONFLICT DO NOTHING"


def _sqlalchemy_asyncpg_url(dsn: str) -> str:
    trimmed = dsn.strip()
    if trimmed.startswith("postgresql+asyncpg://"):
        url = trimmed
    elif trimmed.startswith("postgresql://"):
        url = "postgresql+asyncpg://" + trimmed[len("postgresql://") :]
    elif trimmed.startswith("postgres://"):
        url = "postgresql+asyncpg://" + trimmed[len("postgres://") :]
    else:
        url = trimmed

    parts = urlsplit(url)
    query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True) if key != "sslmode"]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def _postgres_migration_statements(migration: Migration) -> list[str]:
    sql = migration.sql
    if migration.version in {5, 11}:
        sql = sql.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY")
    if migration.version in {8, 9, 10}:
        sql = sql.replace("ADD COLUMN ", "ADD COLUMN IF NOT EXISTS ")
    uncommented = "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--"))
    return [statement.strip() for statement in uncommented.split(";") if statement.strip()]
