"""按 GB_EA_STORE_* 选择持久层。"""

from __future__ import annotations

from backend.core.config import GoldBotEnv, load_gold_bot_env
from backend.persistence.store import EaStore, create_in_memory_store, create_postgres_store, create_sqlite_store

__all__ = ["create_store_from_env"]


def create_store_from_env(env: GoldBotEnv | None = None) -> EaStore:
    loaded = env if env is not None else load_gold_bot_env()
    postgres_dsn = loaded.GB_EA_STORE_POSTGRES_DSN.strip()
    if postgres_dsn:
        return create_postgres_store(postgres_dsn)
    if loaded.GB_EA_STORE_SQLITE_PATH.strip():
        return create_sqlite_store(loaded.GB_EA_STORE_SQLITE_PATH)
    return create_in_memory_store()


async def ensure_store_schema(store: EaStore) -> None:
    """Initialize schemas for stores that manage their own database migrations."""
    ensure_schema = getattr(store, "ensure_schema", None)
    if ensure_schema is not None:
        await ensure_schema()
