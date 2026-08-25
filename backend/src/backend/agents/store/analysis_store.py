"""Agent 分析结果存储(镜像 apps/app-agent/src/store/analysis-store.service.ts)。

使用 aiosqlite(与 backend persistence 层一致)镜像 better-sqlite3 的建表与查询语义;
WAL / busy_timeout pragma 镜像 TS。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import aiosqlite

_DDL = """
CREATE TABLE IF NOT EXISTS analysis_results (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id    TEXT    NOT NULL,
    symbol        TEXT    NOT NULL,
    bias          TEXT    NOT NULL,
    confidence    INTEGER NOT NULL,
    exit_suggestion TEXT  NOT NULL,
    risk_alert    INTEGER NOT NULL DEFAULT 0,
    alert_reason  TEXT,
    action        TEXT,
    direction     TEXT,
    reasoning     TEXT,
    sr_levels     TEXT,
    result_json   TEXT    NOT NULL,
    duration_ms   INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_analysis_account_symbol
  ON analysis_results (account_id, symbol, created_at DESC);
"""


class AnalysisStore:
    def __init__(self, db_path: str | None = None) -> None:
        resolved = db_path or os.environ.get("SQLITE_DB_PATH") or str(
            Path.cwd() / "data" / "analysis.db"
        )
        dir_path = Path(resolved).parent
        if not dir_path.exists():
            dir_path.mkdir(parents=True, exist_ok=True)
        self.db_path = resolved
        self._db: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self._db = await aiosqlite.connect(self.db_path)
        await self._db.execute("PRAGMA journal_mode = WAL")
        await self._db.execute("PRAGMA busy_timeout = 5000")
        await self._db.executescript(_DDL)
        await self._db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def init_database(self) -> None:
        if self._db is None:
            await self.connect()
        assert self._db is not None
        await self._db.executescript(_DDL)
        await self._db.commit()

    async def save_result(
        self, account_id: str, symbol: str, result: dict[str, Any], duration: int
    ) -> None:
        assert self._db is not None
        arbitration = result.get("arbitration") or {}
        sr_levels = result.get("sr_levels")
        await self._db.execute(
            """
            INSERT INTO analysis_results
              (account_id, symbol, bias, confidence, exit_suggestion, risk_alert,
               alert_reason, action, direction, reasoning, sr_levels, result_json, duration_ms)
            VALUES
              (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                account_id,
                symbol,
                str(result.get("bias") or ""),
                int(result.get("confidence") or 0),
                str(result.get("exit_suggestion") or ""),
                1 if result.get("risk_alert") else 0,
                result.get("alert_reason"),
                arbitration.get("action"),
                arbitration.get("direction"),
                arbitration.get("reasoning"),
                json.dumps(sr_levels, ensure_ascii=False) if sr_levels else None,
                json.dumps(result, ensure_ascii=False),
                int(duration),
            ),
        )
        await self._db.commit()

    async def get_recent_results(
        self, account_id: str, symbol: str, limit: int = 10
    ) -> list[dict[str, Any]]:
        assert self._db is not None
        cursor = await self._db.execute(
            """
            SELECT * FROM analysis_results
            WHERE account_id = ? AND symbol = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (account_id, symbol, limit),
        )
        rows = await cursor.fetchall()
        columns = [description[0] for description in cursor.description]
        return [dict(zip(columns, row, strict=True)) for row in rows]
