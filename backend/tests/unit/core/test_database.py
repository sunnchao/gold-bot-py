"""core.database store selector tests."""

from __future__ import annotations

from backend.core.config import GoldBotEnv
from backend.core.database import create_store_from_env
from backend.persistence.inmemory import InMemoryEaStore
from backend.persistence.postgres_store import PostgresEaStore


def env(sqlite_path: str = "", postgres_dsn: str = "") -> GoldBotEnv:
    return GoldBotEnv(
        GB_APP_ENV="test",
        GB_APP_SERVER_HOST="127.0.0.1",
        GB_APP_SERVER_PORT=3000,
        GB_EA_STORE_SQLITE_PATH=sqlite_path,
        GB_EA_STORE_POSTGRES_DSN=postgres_dsn,
        GB_NODE_SHADOW_MODE=False,
        GB_ADMIN_TOKEN="",
        GB_LEGACY_TOKENS_PATH="",
        GB_REDIS_URL="",
        GB_DISCORD_WEBHOOK_URL="",
        GB_FEISHU_WEBHOOK_URL="",
        GB_FEISHU_SECRET="",
    )


async def test_prefers_postgres_store_when_dsn_is_configured() -> None:
    store = create_store_from_env(env(sqlite_path="/tmp/should-not-win.sqlite", postgres_dsn="postgres://u:p@db:5432/goldbot?sslmode=disable"))
    try:
        assert isinstance(store, PostgresEaStore)
    finally:
        await store.close()


def test_uses_in_memory_store_when_no_persistence_is_configured() -> None:
    store = create_store_from_env(env())
    assert isinstance(store, InMemoryEaStore)
