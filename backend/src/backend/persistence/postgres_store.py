"""SQLAlchemy Core async PostgreSQL store.

Postgres schema is managed outside this Python process (existing production DB/migrations).
This adapter intentionally reuses the SQLite query layer because the table contract is
text-first and already mirrors the TS persistence schema. Only SQLite-specific rowid /
INSERT OR IGNORE behavior is replaced with PostgreSQL equivalents.
"""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from backend.persistence.sqlite_store import SqliteEaStore


class PostgresEaStore(SqliteEaStore):
    def __init__(self, dsn: str) -> None:
        self._path = dsn
        self._engine = create_async_engine(_sqlalchemy_asyncpg_url(dsn), pool_pre_ping=True)

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
