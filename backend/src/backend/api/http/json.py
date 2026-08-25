"""JSON 解析辅助(镜像 apps/app-server/src/http/json.ts + app.ts parseStrictJsonObject)。"""

from __future__ import annotations

import json
from typing import Any

__all__ = ["parse_json_object", "parse_strict_json_object"]


def parse_json_object(raw_body: str) -> tuple[bool, dict[str, Any]]:
    """等价 parseJsonObject:空串/非对象 → ok=True 且空 body;非法 JSON → ok=False。"""
    try:
        parsed: Any = {} if raw_body.strip() == "" else json.loads(raw_body)
    except (ValueError, TypeError):
        return False, {}
    if parsed is None or not isinstance(parsed, dict):
        return True, {}
    return True, parsed


def parse_strict_json_object(raw_body: str) -> tuple[bool, dict[str, Any]]:
    """等价 parseStrictJsonObject:空串/非对象/非法 JSON 均 ok=False。"""
    if raw_body.strip() == "":
        return False, {}
    try:
        parsed: Any = json.loads(raw_body)
    except (ValueError, TypeError):
        return False, {}
    if parsed is None or not isinstance(parsed, dict):
        return False, {}
    return True, parsed
