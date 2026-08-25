"""Token 引导集成测试(镜像 apps/app-server/src/bootstrap/tokens.spec.ts)。"""

from __future__ import annotations

import json

import pytest

from backend.bootstrap.tokens import bootstrap_tokens, mask_token
from backend.persistence.store import create_in_memory_store, create_sqlite_store


@pytest.fixture(
    params=[lambda _p: create_in_memory_store(), lambda p: create_sqlite_store(p)], ids=["in-memory", "sqlite"]
)
async def store(request: pytest.FixtureRequest, tmp_path):
    instance = request.param(str(tmp_path / "tokens.db"))
    yield instance
    await instance.close()


async def test_seeds_admin_token_from_env(store) -> None:
    result = await bootstrap_tokens(store, "admin-secret-token")
    assert result.admin_tokens_seeded == 1
    assert result.legacy_tokens_imported == 0
    tokens = await store.list_api_tokens()
    assert len(tokens) == 1
    assert tokens[0]["token"] == "admin-secret-token"
    assert tokens[0]["name"] == "env-admin"
    assert tokens[0]["is_admin"] is True


async def test_admin_token_seeding_is_idempotent(store) -> None:
    await bootstrap_tokens(store, "admin-secret-token")
    result = await bootstrap_tokens(store, "admin-secret-token")
    assert result.admin_tokens_seeded == 0
    assert len(await store.list_api_tokens()) == 1


async def test_empty_admin_token_seeds_nothing(store) -> None:
    result = await bootstrap_tokens(store, "")
    assert result.admin_tokens_seeded == 0
    assert await store.list_api_tokens() == []


async def test_imports_legacy_tokens_json(store, tmp_path) -> None:
    legacy_path = tmp_path / "tokens.json"
    legacy_path.write_text(
        json.dumps(
            [
                {"token": "legacy-1", "name": "bot-a", "is_admin": False, "accounts": ["acc-1"]},
                {"token": "legacy-2", "is_admin": True, "accounts": []},
                {"token": "invalid-record"},  # 缺少 is_admin/accounts → 默认值导入
            ]
        )
    )
    result = await bootstrap_tokens(store, "", str(legacy_path))
    assert result.legacy_tokens_imported == 3
    tokens = {t["token"]: t for t in await store.list_api_tokens()}
    assert tokens["legacy-1"]["accounts"] == ["acc-1"]
    assert tokens["legacy-2"]["is_admin"] is True
    assert tokens["invalid-record"]["accounts"] == []


async def test_legacy_import_skips_existing_tokens(store, tmp_path) -> None:
    await bootstrap_tokens(store, "dup-token")
    legacy_path = tmp_path / "tokens.json"
    legacy_path.write_text(json.dumps([{"token": "dup-token", "accounts": ["acc-9"]}, {"token": "new-token"}]))
    result = await bootstrap_tokens(store, "", str(legacy_path))
    assert result.legacy_tokens_imported == 1
    tokens = {t["token"]: t for t in await store.list_api_tokens()}
    assert tokens["dup-token"]["name"] == "env-admin"  # 未被覆盖
    assert "new-token" in tokens


async def test_missing_legacy_path_is_noop(store) -> None:
    result = await bootstrap_tokens(store, "", "/nonexistent/tokens.json")
    assert result.legacy_tokens_imported == 0


async def test_corrupt_legacy_json_is_swallowed(store, tmp_path) -> None:
    legacy_path = tmp_path / "tokens.json"
    legacy_path.write_text("{not json")
    result = await bootstrap_tokens(store, "", str(legacy_path))
    assert result.legacy_tokens_imported == 0


def test_mask_token() -> None:
    assert mask_token("short") == "***"
    masked = mask_token("0123456789abcdef")
    assert masked == "0123...cdef"
