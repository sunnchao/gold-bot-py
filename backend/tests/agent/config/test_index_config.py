"""镜像 gold-bot `apps/app-agent/src/config/index.test.ts`。"""

import pytest

from backend.agents.config.index import load_config_from_env, reset_config

REQUIRED_ENV = {
    "GOLDBOT_API_URL": "http://localhost:8880",
    "GOLDBOT_API_TOKEN": "test-token",
    "LLM_API_KEY": "sk-test-key",
    "LLM_MODEL": "gpt-4o",
}


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    """每次测试前清空配置缓存,设置必需的隔离环境变量,并清理可选项。"""
    reset_config()
    for key, value in REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)
    for key in ("ACCOUNTS_CONFIG", "ACCOUNTS_CONFIG_FILE", "REDIS_URL", "SCHEDULE_CRON", "LOG_LEVEL", "PORT"):
        monkeypatch.delenv(key, raising=False)
    yield
    reset_config()

def test_should_load_valid_config_from_env_vars():
    # TS: loadConfig 'should load valid config from env vars'
    config = load_config_from_env()
    assert config is not None
    assert config.goldbotApiUrl == "http://localhost:8880"
    assert config.goldbotApiToken == "test-token"
    assert config.llmModel == "gpt-4o"
    assert config.accounts == []


def test_should_cache_config_on_subsequent_calls():
    # TS: loadConfig 'should cache config on subsequent calls'
    config1 = load_config_from_env()
    config2 = load_config_from_env()
    assert config1 is config2


def test_should_boot_without_accounts_config(monkeypatch):
    monkeypatch.delenv("ACCOUNTS_CONFIG", raising=False)
    monkeypatch.delenv("ACCOUNTS_CONFIG_FILE", raising=False)
    monkeypatch.setenv("ACCOUNTS_CONFIG", "not-json")

    config = load_config_from_env()
    assert config.accounts == []


def test_should_use_default_values_for_optional_fields():
    # TS: loadConfig 'should use default values for optional fields'
    config = load_config_from_env()
    assert config.logLevel == "info"
    assert config.port == 3100
    assert config.scheduleCron == "*/5 * * * *"
