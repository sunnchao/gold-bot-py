"""镜像 apps/app-agent/src/utils/stable-stringify.ts。

LLM prompt 缓存的确定性 JSON 序列化:
  1. 对象键按字母序排序 → 相同数据 = 相同字符串 = 缓存命中
  2. 去除所有空白 → 更小的 payload
  3. 优雅处理循环引用(None)、NaN/Infinity(→ null,镜像 JSON.stringify)、
     datetime(→ ISO 字符串,镜像 JS Date.toISOString)
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any

__all__ = ["stable_stringify", "stable_stringify_pretty"]


def _sort_object(obj: Any) -> Any:
    """镜像 sortObject:递归排序键、跳过 undefined(此处 None 视为 JSON null,
    与 JS 的 null 语义一致;JS 的 undefined 在 Python 数据中不存在)。"""
    if obj is None:
        return None
    if isinstance(obj, bool):
        return obj
    if isinstance(obj, (int, float)):
        # JS JSON.stringify(NaN / Infinity) === 'null'
        if not math.isfinite(float(obj)):
            return None
        return obj
    if isinstance(obj, str):
        return obj
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Mapping):
        sorted_result: dict[str, Any] = {}
        for key in sorted(obj.keys(), key=str):
            sorted_result[str(key)] = _sort_object(obj[key])
        return sorted_result
    if isinstance(obj, Sequence) and not isinstance(obj, (str, bytes, bytearray)):
        return [_sort_object(item) for item in obj]
    return obj


def stable_stringify(obj: Any) -> str:
    """镜像 stableStringify:确定性 JSON.stringify — 无空白,非 ASCII 不转义。"""
    return json.dumps(_sort_object(obj), separators=(",", ":"), ensure_ascii=False)


def stable_stringify_pretty(obj: Any, indent: int = 2) -> str:
    """镜像 stableStringifyPretty:仅用于调试/日志,勿用于 prompt 内容。"""
    return json.dumps(_sort_object(obj), indent=indent, ensure_ascii=False)
