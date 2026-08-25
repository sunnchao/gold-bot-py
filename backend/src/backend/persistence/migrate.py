"""SQL migration 执行器(镜像 gold-bot packages/persistence/src/migrate.ts)。

DDL 唯一来源是 backend/src/backend/persistence/migrations/*.sql
(从源仓库 packages/persistence/src/migrations/ 逐字冻结)。
SQLAlchemy Core 只做查询层,建表完全由本执行器控制。

执行时机:store 初始化时同步执行(run_migrations_sync),与 TS DatabaseSync 做法一致。
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from backend.persistence.records import Migration

_MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def load_migrations() -> list[Migration]:
    """按文件名排序加载迁移文件;文件名为 ^(\\d+)_(.+)\\.sql$。"""
    files = sorted(path for path in _MIGRATIONS_DIR.iterdir() if path.suffix == ".sql")
    migrations: list[Migration] = []
    for path in files:
        match = re.match(r"^(\d+)_(.+)\.sql$", path.name)
        if match is None:
            raise ValueError(f"Invalid migration filename: {path.name}")
        migrations.append(
            Migration(version=int(match.group(1)), name=match.group(2), sql=path.read_text(encoding="utf-8"))
        )
    return migrations


def run_migrations_sync(db_path: str | None = None, conn: sqlite3.Connection | None = None) -> None:
    """在 SQLite 连接上应用未执行的迁移,并记录 schema_migrations。

    db_path 与 conn 二选一;给 db_path 时打开新连接并复用(不关闭调用方连接)。
    """
    own_connection = conn is None
    db = conn or sqlite3.connect(db_path or ":memory:")
    try:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
              version INTEGER PRIMARY KEY,
              name TEXT NOT NULL,
              applied_at TEXT NOT NULL
            )
            """
        )
        migrations = load_migrations()
        applied_versions = {
            row[0] for row in db.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()
        }
        for migration in migrations:
            if migration.version in applied_versions:
                continue
            try:
                db.executescript(migration.sql)
                db.execute(
                    "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
                    (migration.version, migration.name, _now_iso()),
                )
                print(f"✓ Applied migration {migration.version}_{migration.name}")
            except Exception as error:
                print(f"✗ Migration {migration.version}_{migration.name} failed: {error}")
                raise
        db.commit()
    finally:
        if own_connection:
            db.close()


def _now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
