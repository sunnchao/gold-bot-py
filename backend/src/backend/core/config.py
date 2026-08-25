"""镜像 packages/config/src/env.ts:loadGoldBotEnv。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

__all__ = ["GoldBotEnv", "load_gold_bot_env"]


@dataclass(frozen=True, slots=True)
class GoldBotEnv:
    GB_APP_ENV: str
    GB_APP_SERVER_HOST: str
    GB_APP_SERVER_PORT: int
    GB_EA_STORE_SQLITE_PATH: str
    GB_EA_STORE_POSTGRES_DSN: str
    GB_NODE_SHADOW_MODE: bool
    GB_ADMIN_TOKEN: str
    GB_LEGACY_TOKENS_PATH: str
    GB_REDIS_URL: str
    GB_DISCORD_WEBHOOK_URL: str
    GB_FEISHU_WEBHOOK_URL: str
    GB_FEISHU_SECRET: str


def load_gold_bot_env(source: Mapping[str, str | None] | None = None) -> GoldBotEnv:
    """镜像 loadGoldBotEnv:缺省值、ADMIN_TOKEN 回退、端口/布尔/比例校验。"""
    env = source if source is not None else __import__("os").environ
    return GoldBotEnv(
        GB_APP_ENV=_string(env, "GB_APP_ENV", "development"),
        GB_APP_SERVER_HOST=_string(env, "GB_APP_SERVER_HOST", "127.0.0.1"),
        GB_APP_SERVER_PORT=_parse_port(env.get("GB_APP_SERVER_PORT")),
        GB_EA_STORE_SQLITE_PATH=_string(env, "GB_EA_STORE_SQLITE_PATH", ""),
        GB_EA_STORE_POSTGRES_DSN=_string(env, "GB_EA_STORE_POSTGRES_DSN", ""),
        GB_NODE_SHADOW_MODE=_parse_boolean(env.get("GB_NODE_SHADOW_MODE"), False),
        GB_ADMIN_TOKEN=_string(env, "GB_ADMIN_TOKEN") or _string(env, "ADMIN_TOKEN", ""),
        GB_LEGACY_TOKENS_PATH=_string(env, "GB_LEGACY_TOKENS_PATH", ""),
        GB_REDIS_URL=_string(env, "GB_REDIS_URL", ""),
        GB_DISCORD_WEBHOOK_URL=_string(env, "GB_DISCORD_WEBHOOK_URL", ""),
        GB_FEISHU_WEBHOOK_URL=_string(env, "GB_FEISHU_WEBHOOK_URL", ""),
        GB_FEISHU_SECRET=_string(env, "GB_FEISHU_SECRET", ""),
    )


def _string(source: Mapping[str, str | None], key: str, default: str = "") -> str:
    value = source.get(key)
    return default if value is None else value


def _parse_port(value: str | None) -> int:
    if value is None or value.strip() == "":
        return 3000
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"GB_APP_SERVER_PORT must be a valid TCP port, got {value}") from exc
    if parsed <= 0 or parsed > 65535:
        raise ValueError(f"GB_APP_SERVER_PORT must be a valid TCP port, got {value}")
    return parsed


def _parse_boolean(value: str | None, fallback: bool) -> bool:
    if value is None or value.strip() == "":
        return fallback
    if value == "true":
        return True
    if value == "false":
        return False
    raise ValueError(f'Expected boolean string "true" or "false", got {value}')
