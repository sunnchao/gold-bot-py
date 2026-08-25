"""镜像 gold-bot `apps/app-agent/src/config/app-config.service.test.ts`。"""
from backend.agents.config.app_config import AppConfigService, validate_config

BASE_ENV = {
    "GOLDBOT_API_URL": "http://127.0.0.1:3000",
    "GOLDBOT_API_TOKEN": "test-token",
    "REDIS_URL": "redis://localhost:6379",
    "LLM_PROVIDER": "openai",
    "LLM_BASE_URL": "https://api.openai.com/v1",
    "LLM_API_KEY": "sk-test-key",
    "LLM_MODEL": "gpt-4o",
    "LLM_TRADE_MODEL": "deepseek-v4-flash-0731",
    "LLM_FALLBACK_MODEL": "gpt-4o-mini",
    "LLM_TIMEOUT": "240000",
    "LLM_MAX_RETRIES": "3",
    "SCHEDULE_CRON": "*/5 * * * *",
    "LOG_LEVEL": "info",
    "PORT": "3100",
}


def test_validates_and_coerces_environment_variables():
    config = validate_config(BASE_ENV)

    assert config.port == 3100
    assert config.accounts == []
    assert config.llmTimeout == 240000
    assert config.llmTradeModel == "deepseek-v4-flash-0731"
    assert config.analysisTriggerMode == "bar_close"


def test_ignores_accounts_config_environment_variable():
    env = {
        **BASE_ENV,
        "ACCOUNTS_CONFIG": "not-json",
        "ACCOUNTS_CONFIG_FILE": "/tmp/nonexistent-accounts.json",
    }

    config = validate_config(env)

    assert config.accounts == []


def test_exposes_strongly_typed_config_values():
    service = AppConfigService(validate_config(BASE_ENV))

    assert service.port == 3100
    assert service.goldbot == {
        "apiUrl": "http://127.0.0.1:3000",
        "apiToken": "test-token",
    }
    assert service.llm["model"] == "gpt-4o"
    assert service.llm_trade_model == "deepseek-v4-flash-0731"
    assert service.analysis_trigger_mode == "bar_close"
    assert service.accounts == []


def test_defaults_the_trade_model_when_llm_trade_model_is_not_configured():
    env = {**BASE_ENV, "LLM_TRADE_MODEL": "deepseek-v4-flash-0731"}
    service = AppConfigService(validate_config(env))

    assert service.llm_trade_model == "deepseek-v4-flash-0731"


def test_upserts_runtime_accounts_discovered_from_ea():
    service = AppConfigService(validate_config(BASE_ENV))

    service.update_account_symbols("acc-001", ["XAUUSD"])
    service.update_account_symbols("acc-001", ["XAUUSD", "US100Cash", "XAUUSD"])
    service.update_account_symbols("90011087", ["GBPJPY"])

    assert [account.model_dump() for account in service.accounts] == [
        {"id": "90011087", "symbols": ["GBPJPY"]},
    ]
    assert service.static_accounts == []
    assert [account.model_dump() for account in service.raw.accounts] == [
        {"id": "90011087", "symbols": ["GBPJPY"]},
    ]


def test_returns_defensive_copies_of_account_config():
    service = AppConfigService(validate_config(BASE_ENV))
    service.update_account_symbols("90011087", ["XAUUSD"])
    accounts = service.accounts

    accounts[0].symbols.append("US100Cash")

    assert service.accounts[0].symbols == ["XAUUSD"]


def test_defaults_the_goldbot_api_url_to_the_node_app_server_authority():
    env = {**BASE_ENV, "GOLDBOT_API_URL": "http://127.0.0.1:3000"}
    config = validate_config(env)

    assert config.goldbotApiUrl == "http://127.0.0.1:3000"
