"""Token 引导(镜像 gold-bot apps/app-server/src/bootstrap/tokens.ts)。"""

from __future__ import annotations

import json
from pathlib import Path

from backend.persistence.records import BootstrapResult
from backend.persistence.store import EaStore


async def bootstrap_tokens(store: EaStore, admin_token: str, legacy_tokens_path: str | None = None) -> BootstrapResult:
    """从 GB_ADMIN_TOKEN 种子 admin token,并可导入遗留 tokens.json。"""
    result = BootstrapResult()

    if len(admin_token) > 0:
        existing = [t for t in await store.list_api_tokens() if t.get("token") == admin_token]
        if not existing:
            await store.save_api_token(
                {
                    "token": admin_token,
                    "name": "env-admin",
                    "is_admin": True,
                    "accounts": [],
                }
            )
            result.admin_tokens_seeded = 1
            print(f"✓ Seeded admin token from GB_ADMIN_TOKEN ({mask_token(admin_token)})")

    if legacy_tokens_path and Path(legacy_tokens_path).exists():
        try:
            content = json.loads(Path(legacy_tokens_path).read_text(encoding="utf-8"))
            records = content if isinstance(content, list) else []
            existing_tokens = {t.get("token") for t in await store.list_api_tokens()}
            for record in records:
                if not isinstance(record, dict):
                    continue
                token = record.get("token")
                if not isinstance(token, str) or len(token) == 0 or token in existing_tokens:
                    continue
                accounts = record.get("accounts")
                await store.save_api_token(
                    {
                        "token": token,
                        "name": str(record.get("name", "")),
                        "is_admin": bool(record.get("is_admin", False)),
                        "accounts": [a for a in accounts if isinstance(a, str)] if isinstance(accounts, list) else [],
                    }
                )
                existing_tokens.add(token)
                result.legacy_tokens_imported += 1
            if result.legacy_tokens_imported > 0:
                print(f"✓ Imported {result.legacy_tokens_imported} tokens from {legacy_tokens_path}")
        except Exception as error:
            print(f"✗ Failed to import legacy tokens from {legacy_tokens_path}: {error}")

    return result


def mask_token(token: str) -> str:
    if len(token) <= 8:
        return "***"
    return token[:4] + "..." + token[-4:]
