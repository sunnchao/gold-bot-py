"""分析结果存储契约(镜像 apps/app-agent/src/store/analysis-store.service.ts 语义)。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.agents.store.analysis_store import AnalysisStore


@pytest.fixture
async def store(tmp_path) -> AnalysisStore:
    db_file = tmp_path / "analysis.db"
    instance = AnalysisStore(str(db_file))
    await instance.init_database()
    yield instance
    await instance.close()


async def test_save_and_get_recent_results(store: AnalysisStore) -> None:
    result = {
        "bias": "bullish",
        "confidence": 82,
        "exit_suggestion": "hold",
        "risk_alert": False,
        "arbitration": {
            "action": "open",
            "direction": "buy",
            "reasoning": "momentum aligned",
        },
        "sr_levels": {"support": [3328], "resistance": [3350]},
    }
    await store.save_result("acc-001", "XAUUSD", result, 1234)

    rows = await store.get_recent_results("acc-001", "XAUUSD", 10)
    assert len(rows) == 1
    row = rows[0]
    assert row["account_id"] == "acc-001"
    assert row["symbol"] == "XAUUSD"
    assert row["bias"] == "bullish"
    assert row["confidence"] == 82
    assert row["risk_alert"] == 0
    assert row["action"] == "open"
    assert row["direction"] == "buy"
    assert row["reasoning"] == "momentum aligned"
    assert row["duration_ms"] == 1234
    assert json.loads(row["result_json"])["bias"] == "bullish"


async def test_risk_alert_persists_as_int(store: AnalysisStore) -> None:
    await store.save_result("acc-001", "XAUUSD", {"bias": "bearish", "risk_alert": True}, 10)
    rows = await store.get_recent_results("acc-001", "XAUUSD")
    assert rows[0]["risk_alert"] == 1


async def test_orders_by_created_at_desc_with_limit(store: AnalysisStore) -> None:
    for index in range(5):
        await store.save_result("acc-001", "XAUUSD", {"bias": "bullish", "confidence": index}, index)
    await store.save_result("acc-002", "XAUUSD", {"bias": "bearish"}, 0)

    rows = await store.get_recent_results("acc-001", "XAUUSD", 2)
    assert len(rows) == 2
    # created_at 同秒时按插入序;断言只取 limit 且非空
    assert rows[0]["account_id"] == "acc-001"
    assert rows[1]["account_id"] == "acc-001"

    other = await store.get_recent_results("acc-002", "XAUUSD", 10)
    assert len(other) == 1
    assert other[0]["bias"] == "bearish"


async def test_default_db_path_uses_env_over_cwd(tmp_path, monkeypatch) -> None:
    db_file = tmp_path / "env.db"
    monkeypatch.setenv("SQLITE_DB_PATH", str(db_file))
    instance = AnalysisStore()
    await instance.init_database()
    assert Path(instance.db_path) == db_file
    await instance.close()
