"""镜像 apps/app-agent/src/utils/logger.ts(pino 单例日志器)。

Python 侧用标准库 logging 实现,保持 API 形态与测试语义:
- get_logger() 返回同一实例(重复调用共享)
- level 取决于 LOG_LEVEL 环境变量(默认 'info'),logger.level 属性返回字符串
- info / warn / error / debug / trace 方法,pino 风格 (obj?, msg)
- reset_logger() 清空单例
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

__all__ = ["get_logger", "reset_logger"]


def _serialize_context(context: Any) -> str:
    """pino 风格打印对象上下文;失败回退 str()。"""
    if isinstance(context, str):
        return context
    if context is None:
        return ""
    try:
        return json.dumps(context, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(context)


class AgentLogger:
    """pino 风格日志器包装:level 为字符串,方法形如 (obj?, msg)。"""

    def __init__(self, level: str) -> None:
        self.level = level
        self._logger = logging.getLogger("goldbot.app_agent")
        self._logger.setLevel(level.upper())
        if not self._logger.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(logging.Formatter("%(message)s"))
            self._logger.addHandler(handler)
        self._logger.propagate = False

    def _log(self, level: str, context: Any, msg: str) -> None:
        if context is None:
            text = str(msg)
        elif isinstance(context, str):
            text = f"{context} {str(msg)}" if msg else context
        else:
            prefix = _serialize_context(context)
            text = f"{prefix} {str(msg)}" if msg else prefix
        getattr(self._logger, level)(text)

    def info(self, context: Any = None, msg: str = "") -> None:
        self._log("info", context, msg)

    def warn(self, context: Any = None, msg: str = "") -> None:
        self._log("warning", context, msg)

    def error(self, context: Any = None, msg: str = "") -> None:
        self._log("error", context, msg)

    def debug(self, context: Any = None, msg: str = "") -> None:
        self._log("debug", context, msg)

    def trace(self, context: Any = None, msg: str = "") -> None:
        self._log("debug", context, msg)


_logger: AgentLogger | None = None


def get_logger() -> AgentLogger:
    """镜像 getLogger():返回单例,level 取 LOG_LEVEL ?? 'info'。"""
    global _logger
    if _logger is not None:
        return _logger
    level = os.environ.get("LOG_LEVEL", "info")
    _logger = AgentLogger(level)
    return _logger


def reset_logger() -> None:
    """镜像 resetLogger():清空单例。"""
    global _logger
    _logger = None
