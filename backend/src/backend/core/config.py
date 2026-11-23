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
    GB_SOP_PATROL_ENABLED: bool = True
    GB_SOP_EXECUTION_MODE: str = "advisory_only"
    GB_SOP_PATROL_INTERVAL_SECONDS: int = 300
    GB_SOP_HTF_REVIEW_MINUTES: int = 60
    GB_SOP_MOVE_TRIGGER_PCT: float = 0.5
    GB_SOP_M15_ALWAYS_RUN_ENTRY: bool = False


def _parse_int_default(raw: str | None, default: int, *, minimum: int) -> int:
    if raw is None or not raw.strip():
        return default
    try:
        val = int(raw.strip())
    except ValueError:
        return default
    return max(minimum, val)


def _parse_float_default(raw: str | None, default: float, *, minimum: float) -> float:
    if raw is None or not raw.strip():
        return default
    try:
        val = float(raw.strip())
    except ValueError:
        return default
    return max(minimum, val)


def _parse_sop_mode(raw: str | None) -> str:
    if raw is not None and raw.strip().lower() == "integrated":
        return "integrated"
    return "advisory_only"


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
        GB_SOP_PATROL_ENABLED=_parse_boolean(env.get("GB_SOP_PATROL_ENABLED"), True),
        GB_SOP_EXECUTION_MODE=_parse_sop_mode(env.get("GB_SOP_EXECUTION_MODE")),
        GB_SOP_PATROL_INTERVAL_SECONDS=_parse_int_default(
            env.get("GB_SOP_PATROL_INTERVAL_SECONDS"), 300, minimum=15
        ),
        GB_SOP_HTF_REVIEW_MINUTES=_parse_int_default(
            env.get("GB_SOP_HTF_REVIEW_MINUTES"), 60, minimum=5
        ),
        GB_SOP_MOVE_TRIGGER_PCT=_parse_float_default(
            env.get("GB_SOP_MOVE_TRIGGER_PCT"), 0.5, minimum=0.05
        ),
        GB_SOP_M15_ALWAYS_RUN_ENTRY=_parse_boolean(env.get("GB_SOP_M15_ALWAYS_RUN_ENTRY"), False),
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
