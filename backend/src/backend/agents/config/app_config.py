"""镜像 apps/app-agent/src/config/app-config.service.ts。

env/zod 配置 → pydantic:
- AccountConfigSchema / AppConfigSchema 的默认值与 coerce 语义 1:1 镜像。
- BooleanConfigSchema: 'true'/'1'/'yes'/'on' → true;'false'/'0'/'no'/'off'/'' → false;
  其余字符串/非 bool 报错。
- z.coerce.boolean:Boolean(value) 语义(已验证 zod 3.25:'false' 字符串 → true),
  与 BooleanConfigSchema 的字符串映射不同,严格按 TS 实现。
- z.coerce.number:Number(value) 语义('' → 0、' abc ' → NaN→报错,支持 1e3 等)。
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Annotated, Any, Literal
from urllib.parse import urlparse

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator

__all__ = [
    "AccountConfig",
    "AccountConfigSchema",
    "AppConfig",
    "AppConfigService",
    "validate_config",
]

TRUTHY_STRINGS = ("true", "1", "yes", "on")
FALSY_STRINGS = ("false", "0", "no", "off", "")


def _js_number(value: Any) -> float:
    """镜像 JS Number(value):空串 → 0,空白剥离,非法 → NaN。"""
    if value is None:
        return 0.0
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if text == "":
            return 0.0
        try:
            return float(text)
        except ValueError:
            return math.nan
    return math.nan


def _coerce_boolean(value: Any) -> bool:
    """镜像 zod z.coerce.boolean():Boolean(value)。"""
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        # Boolean(str):仅空串为 falsy,任何非空字符串(含 'false'/'0')→ true
        return value != ""
    return bool(value)


def _env_boolean(value: Any) -> bool:
    """镜像 BooleanConfigSchema(z.preprocess + z.boolean())。"""
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in TRUTHY_STRINGS:
            return True
        if normalized in FALSY_STRINGS:
            return False
        raise ValueError(f"invalid boolean string: {value!r}")
    if isinstance(value, bool):
        return value
    raise ValueError(f"expected boolean, got {type(value).__name__}: {value!r}")


def _coerce_int(value: Any, *, positive: bool) -> int:
    n = _js_number(value)
    if math.isnan(n) or not math.isfinite(n) or not n.is_integer():
        raise ValueError(f"expected an integer, got {value!r}")
    result = int(n)
    if positive and result <= 0:
        raise ValueError(f"expected a positive integer, got {value!r}")
    return result


def _coerce_positive_number(value: Any) -> float:
    n = _js_number(value)
    if math.isnan(n) or not math.isfinite(n) or n <= 0:
        raise ValueError(f"expected a positive number, got {value!r}")
    return n


def _min_len_string(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError(f"expected a string, got {type(value).__name__}: {value!r}")
    if len(value) < 1:
        raise ValueError("string must have length >= 1")
    return value


def _url_string(value: Any) -> str:
    text = _min_len_string(value)
    parsed = urlparse(text)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(f"invalid URL: {text!r}")
    return text


def _list_min_len(n: int) -> Any:
    def check(value: Any) -> Any:
        if not isinstance(value, list):
            raise ValueError("expected an array")
        if len(value) < n:
            raise ValueError(f"array must have length >= {n}")
        return value

    return check


# ---------------------------------------------------------------- AccountConfig


AccountConfigSchema = dict[str, Any]


class AccountConfig(BaseModel):
    """镜像 AccountConfigSchema:z.string().min(1);array min(1)。"""

    model_config = ConfigDict(extra="ignore")

    id: Annotated[str, AfterValidator(_min_len_string)]
    symbols: Annotated[list[Annotated[str, AfterValidator(_min_len_string)]], AfterValidator(_list_min_len(1))]


# ---------------------------------------------------------------- AppConfig


class AppConfig(BaseModel):
    """镜像 AppConfigSchema。coerce 字段用 before 校验器实现,默认值即 zod 默认。"""

    model_config = ConfigDict(extra="ignore")

    goldbotApiUrl: str = Field(default="http://127.0.0.1:3000")
    goldbotApiToken: str = Field(default="test-token")
    redisUrl: str = Field(default="redis://localhost:6379")
    llmProvider: str = Field(default="openai")
    llmBaseUrl: str = Field(default="https://api.openai.com/v1")
    llmApiKey: str = Field(default="sk-test")
    llmModel: str = Field(default="gpt-4o")
    llmTradeModel: str = Field(default="deepseek-v4-flash-0731")
    llmFallbackModel: str = Field(default="gpt-4o-mini")
    llmTimeout: int = Field(default=240000)
    llmMaxRetries: int = Field(default=3)
    llmEnablePromptCaching: bool = Field(default=False)
    marketFirstEnabled: bool = Field(default=False)
    marketBarAccount: str = Field(default="90011087")
    marketInsightTtlMs: int = Field(default=600000)
    priceDeviationToleranceAtr: float = Field(default=0.25)
    analysisTriggerMode: Literal["bar_close", "cron"] = "bar_close"
    scheduleCron: str = Field(default="*/5 * * * *")
    accounts: list[AccountConfig] = Field(default_factory=list)
    logLevel: Literal["trace", "debug", "info", "warn", "error", "fatal"] = "info"
    port: int = Field(default=3100)

    # -- zod 语义的 field 校验(before coerce + 约束) -------------------

    @field_validator("goldbotApiUrl", "llmBaseUrl", mode="before")
    @classmethod
    def _validate_url(cls, value: Any) -> Any:
        # z.string().url()
        return _url_string(value)

    @field_validator(
        "goldbotApiToken",
        "redisUrl",
        "llmProvider",
        "llmApiKey",
        "llmModel",
        "llmTradeModel",
        "llmFallbackModel",
        "marketBarAccount",
        "scheduleCron",
        mode="before",
    )
    @classmethod
    def _validate_min_one_string(cls, value: Any) -> Any:
        # z.string().min(1)
        return _min_len_string(value)

    @field_validator("llmTimeout", mode="before")
    @classmethod
    def _validate_llm_timeout(cls, value: Any) -> Any:
        # z.coerce.number().int().positive()
        return _coerce_int(value, positive=True)

    @field_validator("llmMaxRetries", mode="before")
    @classmethod
    def _validate_llm_max_retries(cls, value: Any) -> Any:
        # z.coerce.number().int().min(0)
        return _coerce_int(value, positive=False)

    @field_validator("llmEnablePromptCaching", mode="before")
    @classmethod
    def _validate_llm_cache_bool(cls, value: Any) -> Any:
        # z.coerce.boolean()
        return _coerce_boolean(value)

    @field_validator("marketFirstEnabled", mode="before")
    @classmethod
    def _validate_market_first_enabled(cls, value: Any) -> Any:
        # BooleanConfigSchema
        return _env_boolean(value)

    @field_validator("marketInsightTtlMs", mode="before")
    @classmethod
    def _validate_insight_ttl(cls, value: Any) -> Any:
        # z.coerce.number().int().positive()
        return _coerce_int(value, positive=True)

    @field_validator("priceDeviationToleranceAtr", mode="before")
    @classmethod
    def _validate_price_deviation(cls, value: Any) -> Any:
        # z.coerce.number().positive()
        return _coerce_positive_number(value)

    @field_validator("port", mode="before")
    @classmethod
    def _validate_port(cls, value: Any) -> Any:
        # z.coerce.number().int().positive()
        return _coerce_int(value, positive=True)


# ---------------------------------------------------------------- validateConfig


def _pick(env: Mapping[str, Any], key: str, default: Any) -> Any:
    """镜像 JS `env.KEY ?? default`:键缺失或值为 null/undefined 时取默认,
    空字符串等 falsy 值原样保留(交给 zod 校验)。"""
    value = env.get(key, default)
    return default if value is None else value


def validate_config(env: Mapping[str, Any]) -> AppConfig:
    """镜像 validateConfig:用 `env.KEY ?? fallback` 填齐 raw(默认值与 TS 完全一致),
    再按 AppConfigSchema 解析/校验。"""
    raw = {
        "goldbotApiUrl": _pick(env, "GOLDBOT_API_URL", "http://127.0.0.1:3000"),
        "goldbotApiToken": _pick(env, "GOLDBOT_API_TOKEN", "test-token"),
        "redisUrl": _pick(env, "REDIS_URL", "redis://localhost:6379"),
        "llmProvider": _pick(env, "LLM_PROVIDER", "openai"),
        "llmBaseUrl": _pick(env, "LLM_BASE_URL", "https://api.openai.com/v1"),
        "llmApiKey": _pick(env, "LLM_API_KEY", "sk-test"),
        "llmModel": _pick(env, "LLM_MODEL", "gpt-4o"),
        "llmTradeModel": _pick(env, "LLM_TRADE_MODEL", "deepseek-v4-flash-0731"),
        "llmFallbackModel": _pick(env, "LLM_FALLBACK_MODEL", "gpt-4o-mini"),
        "llmTimeout": _pick(env, "LLM_TIMEOUT", "120000"),
        "llmMaxRetries": _pick(env, "LLM_MAX_RETRIES", "3"),
        "llmEnablePromptCaching": _pick(env, "LLM_ENABLE_PROMPT_CACHING", "false"),
        "marketFirstEnabled": _pick(env, "MARKET_FIRST_ENABLED", "false"),
        "marketBarAccount": _pick(env, "MARKET_BAR_ACCOUNT", "90011087"),
        "marketInsightTtlMs": _pick(env, "MARKET_INSIGHT_TTL_MS", "600000"),
        "priceDeviationToleranceAtr": _pick(env, "PRICE_DEVIATION_TOLERANCE_ATR", "0.25"),
        "analysisTriggerMode": _pick(env, "ANALYSIS_TRIGGER_MODE", "bar_close"),
        "scheduleCron": _pick(env, "SCHEDULE_CRON", "*/5 * * * *"),
        "accounts": [],
        "logLevel": _pick(env, "LOG_LEVEL", "info"),
        "port": _pick(env, "PORT", "3100"),
    }
    return AppConfig.model_validate(raw)


# ---------------------------------------------------------------- Service


class AppConfigService:
    """镜像 AppConfigService:暴露强类型 getter,accounts 返回防御性副本。"""

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._accounts_config: list[AccountConfig] = [account.model_copy(deep=True) for account in config.accounts]

    @property
    def port(self) -> int:
        return self._config.port

    @property
    def log_level(self) -> Literal["trace", "debug", "info", "warn", "error", "fatal"]:
        return self._config.logLevel

    @property
    def goldbot(self) -> dict[str, str]:
        return {
            "apiUrl": self._config.goldbotApiUrl,
            "apiToken": self._config.goldbotApiToken,
        }

    @property
    def redis_url(self) -> str:
        return self._config.redisUrl

    @property
    def llm(self) -> dict[str, Any]:
        return {
            "provider": self._config.llmProvider,
            "baseUrl": self._config.llmBaseUrl,
            "apiKey": self._config.llmApiKey,
            "model": self._config.llmModel,
            "fallbackModel": self._config.llmFallbackModel,
            "timeout": self._config.llmTimeout,
            "maxRetries": self._config.llmMaxRetries,
            "enablePromptCaching": self._config.llmEnablePromptCaching,
        }

    @property
    def llm_trade_model(self) -> str:
        return self._config.llmTradeModel

    @property
    def market_first_enabled(self) -> bool:
        return self._config.marketFirstEnabled

    @property
    def market_bar_account(self) -> str:
        return self._config.marketBarAccount

    @property
    def market_insight_ttl_ms(self) -> int:
        return self._config.marketInsightTtlMs

    @property
    def price_deviation_tolerance_atr(self) -> float:
        return self._config.priceDeviationToleranceAtr

    @property
    def schedule_cron(self) -> str:
        return self._config.scheduleCron

    @property
    def analysis_trigger_mode(self) -> Literal["bar_close", "cron"]:
        return self._config.analysisTriggerMode

    @property
    def accounts(self) -> list[AccountConfig]:
        return [account.model_copy(deep=True) for account in self._accounts_config]

    @property
    def static_accounts(self) -> list[AccountConfig]:
        return [account.model_copy(deep=True) for account in self._config.accounts]

    @property
    def raw(self) -> AppConfig:
        return self._config.model_copy(update={"accounts": self.accounts})

    def update_account_symbols(self, account_id: str, symbols: list[str]) -> None:
        """去重 + trim + 去空,按 AccountConfig 校验。

        数字 ID 视为 EA 轮询发现的实时账户,upsert 到运行时列表(匹配则更新,
        未知则追加);非数字 ID 只更新静态列表中已存在的账户,不做追加。
        """
        normalized_symbols = list(dict.fromkeys(symbol.strip() for symbol in symbols if symbol.strip()))
        updated = AccountConfig(id=account_id, symbols=normalized_symbols)

        for index, account in enumerate(self._accounts_config):
            if account.id == account_id:
                self._accounts_config[index] = updated
                return

        if not account_id.isdigit():
            return
        self._accounts_config.append(updated)
