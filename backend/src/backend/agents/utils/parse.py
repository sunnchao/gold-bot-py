"""镜像 apps/app-agent/src/utils/parse.ts。

LLM 响应的安全 JSON 解析工具,包装 pydantic 校验,agent 永不因非法 LLM
输出而抛异常(失败返回 None / 空 dict)。
"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, ValidationError

from backend.agents.utils.logger import get_logger

__all__ = ["extract_json", "safe_parse_batch_response", "safe_parse_response"]

_JSON_OBJECT_RE = re.compile(r"\{[\s\S]*\}")


def extract_json(raw: str) -> str | None:
    """提取可能被包裹字符串中的第一个 JSON 对象。"""
    match = _JSON_OBJECT_RE.search(raw)
    return match.group(0) if match else None


def _parse_json(raw: str) -> Any | None:
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


def safe_parse_response(
    raw: str,
    schema: type[BaseModel],
    context: dict[str, Any] | None = None,
) -> BaseModel | None:
    """镜像 safeParseResponse:单个 LLM 响应的安全解析。

    任何失败(无 JSON / JSON 非法 / schema 不匹配)返回 None;日志形如 TS。"""
    logger = get_logger()
    json_text = extract_json(raw)
    if json_text is None:
        logger.warn({"raw": raw[:200], **(context or {})}, "safeParse: no JSON found")
        return None

    parsed = _parse_json(json_text)
    if parsed is None:
        logger.warn(
            {"raw": raw[:200], "err": "JSON.parse failed", **(context or {})},
            "safeParse: JSON.parse failed",
        )
        return None

    try:
        return schema.model_validate(parsed)
    except ValidationError as exc:
        logger.warn(
            {"issues": str(exc)[:500], **(context or {})},
            "safeParse: validation failed",
        )
        return None


def safe_parse_batch_response(
    raw: str,
    schema: type[BaseModel],
    context: dict[str, Any] | None = None,
) -> dict[str, BaseModel]:
    """镜像 safeParseBatchResponse:按 symbol 键控的批量响应;失败条目静默丢弃。"""
    logger = get_logger()
    json_text = extract_json(raw)
    if json_text is None:
        logger.warn({"raw": raw[:200], **(context or {})}, "safeParseBatch: no JSON found")
        return {}

    parsed = _parse_json(json_text)
    if parsed is None or not isinstance(parsed, dict):
        logger.warn({"raw": raw[:200], **(context or {})}, "safeParseBatch: JSON.parse failed")
        return {}

    results: dict[str, BaseModel] = {}
    for symbol, data in parsed.items():
        try:
            results[str(symbol)] = schema.model_validate(data)
        except ValidationError:
            logger.warn(
                {"symbol": str(symbol), **(context or {})},
                "safeParseBatch: per-symbol validation failed",
            )
    return results
