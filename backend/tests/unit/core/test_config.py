"""镜像 packages/config/src/env.spec.ts。"""

from __future__ import annotations

from backend.core.config import GoldBotEnv, load_gold_bot_env


def test_loads_placeholder_gb_variables_with_defaults() -> None:
    assert load_gold_bot_env({}) == GoldBotEnv(
        GB_APP_ENV="development",
        GB_APP_SERVER_HOST="127.0.0.1",
        GB_APP_SERVER_PORT=3000,
        GB_EA_STORE_SQLITE_PATH="",
        GB_EA_STORE_POSTGRES_DSN="",
        GB_NODE_SHADOW_MODE=False,
        GB_ADMIN_TOKEN="",
        GB_LEGACY_TOKENS_PATH="",
        GB_REDIS_URL="",
        GB_DISCORD_WEBHOOK_URL="",
        GB_FEISHU_WEBHOOK_URL="",
        GB_FEISHU_SECRET="",
    )


def test_parses_explicit_app_server_settings() -> None:
    assert load_gold_bot_env(
        {
            "GB_APP_ENV": "test",
            "GB_APP_SERVER_HOST": "0.0.0.0",
            "GB_APP_SERVER_PORT": "3100",
            "GB_EA_STORE_SQLITE_PATH": "/tmp/gold-bot-ea.sqlite",
            "GB_NODE_SHADOW_MODE": "false",
            "GB_ADMIN_TOKEN": "gb-admin-token",
        }
    ) == GoldBotEnv(
        GB_APP_ENV="test",
        GB_APP_SERVER_HOST="0.0.0.0",
        GB_APP_SERVER_PORT=3100,
        GB_EA_STORE_SQLITE_PATH="/tmp/gold-bot-ea.sqlite",
        GB_EA_STORE_POSTGRES_DSN="",
        GB_NODE_SHADOW_MODE=False,
        GB_ADMIN_TOKEN="gb-admin-token",
        GB_LEGACY_TOKENS_PATH="",
        GB_REDIS_URL="",
        GB_DISCORD_WEBHOOK_URL="",
        GB_FEISHU_WEBHOOK_URL="",
        GB_FEISHU_SECRET="",
    )


def test_falls_back_to_legacy_admin_token() -> None:
    assert load_gold_bot_env({"ADMIN_TOKEN": "legacy-admin-token"}).GB_ADMIN_TOKEN == "legacy-admin-token"


def test_ignores_max_daily_loss_environment_variable() -> None:
    loaded = load_gold_bot_env({"GB_MAX_DAILY_LOSS_PCT": "0.03"})

    assert not hasattr(loaded, "GB_MAX_DAILY_LOSS_PCT")
