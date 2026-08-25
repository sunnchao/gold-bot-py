"""按 GB_EA_STORE_* 选择内存库或 SQLite(镜像 app-server 启动时的 store 选择)。"""

from __future__ import annotations

from backend.core.config import GoldBotEnv, load_gold_bot_env
from backend.persistence.store import EaStore, create_in_memory_store, create_sqlite_store

__all__ = ["create_store_from_env"]


def create_store_from_env(env: GoldBotEnv | None = None) -> EaStore:
    loaded = env if env is not None else load_gold_bot_env()
    if loaded.GB_EA_STORE_SQLITE_PATH.strip():
        return create_sqlite_store(loaded.GB_EA_STORE_SQLITE_PATH)
    return create_in_memory_store()
