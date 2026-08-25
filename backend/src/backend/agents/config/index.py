"""镜像 apps/app-agent/src/config/index.ts。

loadConfig / loadConfigFromEnv / resetConfig 带模块级缓存。
"""

from __future__ import annotations

import importlib
import os
from typing import Any

from backend.agents.config.app_config import AppConfig, validate_config

__all__ = ["load_config", "load_config_from_env", "reset_config"]

_cached_config: AppConfig | None = None


def load_config() -> AppConfig:
    """镜像 loadConfig:尝试 dotenv 加载 .env,再按 env 解析(带缓存)。"""
    global _cached_config
    if _cached_config is not None:
        return _cached_config

    try:
        module: Any = importlib.import_module("dotenv")
        load_dotenv = getattr(module, "load_dotenv", None)
        if callable(load_dotenv):
            load_dotenv()
    except (ImportError, AttributeError):
        pass

    return load_config_from_env()


def load_config_from_env() -> AppConfig:
    """镜像 loadConfigFromEnv:直接从 os.environ 解析,无 dotenv(带缓存)。"""
    global _cached_config
    if _cached_config is not None:
        return _cached_config

    _cached_config = validate_config(os.environ)
    return _cached_config


def reset_config() -> None:
    """镜像 resetConfig:清空缓存。"""
    global _cached_config
    _cached_config = None
