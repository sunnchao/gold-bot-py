"""镜像 apps/app-agent/src/utils/markdown-parser.ts。

LLM 响应的 Markdown 结构化输出解析器,替代严格 JSON 解析:
格式:
  ## SECTION NAME
  - Key: Value
    - list item 1
    - list item 2
所有正则/归一化/默认值语义与 TS 逐条对齐。
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from typing import Any, Literal

__all__ = [
    "detect_format",
    "extract_fields",
    "extract_list_items",
    "extract_warnings",
    "get_boolean_field",
    "get_enum_field",
    "get_number_field",
    "get_string_field",
    "parse_sr_level_line",
    "parse_sr_levels",
    "parse_warnings_line",
    "split_sections",
]

_HEADER_RE = re.compile(r"^##\s+(.+)")
_FIELD_RE = re.compile(r"^-\s+(.+?):\s+(.*)")
_LIST_ITEM_RE = re.compile(r"^\s{2,}-\s+(.+)")
_NUMBER_RE = re.compile(r"-?\d+\.?\d*")
_TAG_RE = re.compile(r"<[^>]*>")


# ---------------------------------------------------------------- key 归一化

def normalize_key(key: str) -> str:
    """Lowercase + 空格/连字符 → 下划线。"""
    return re.sub(r"[\s-]+", "_", key.strip().lower())


# ---------------------------------------------------------------- section 切分

def split_sections(raw: str) -> dict[str, str]:
    """按 `## HEADER` 切分 raw 输出为 sections,key 归一化。"""
    sections: dict[str, str] = {}
    lines = raw.split("\n")
    current_section: str | None = None
    current_content: list[str] = []

    for line in lines:
        header_match = _HEADER_RE.match(line)
        if header_match:
            if current_section is not None:
                sections[current_section] = "\n".join(current_content)
            current_section = normalize_key(header_match.group(1))
            current_content = []
        else:
            current_content.append(line)

    if current_section is not None:
        sections[current_section] = "\n".join(current_content)

    # Fallback:无 ## 头但有内容 → 整个 raw 作为 'root' 段
    if len(sections) == 0 and raw.strip():
        sections["root"] = raw

    return sections


# ---------------------------------------------------------------- 字段提取

def extract_fields(section: str) -> dict[str, str]:
    """提取 `- Key: Value` 对,返回 normalized_key → raw_value(首个出现者胜)。"""
    fields: dict[str, str] = {}
    for line in section.split("\n"):
        kv_match = _FIELD_RE.match(line)
        if kv_match:
            key = normalize_key(kv_match.group(1))
            value = kv_match.group(2).strip()
            if key not in fields:
                fields[key] = value
    return fields


def extract_list_items(section: str) -> list[str]:
    """提取缩进列表项(2+ 空格 + '-' 的管道分隔行);排除非管道的 KV 行。"""
    items: list[str] = []
    for line in section.split("\n"):
        list_match = _LIST_ITEM_RE.match(line)
        if list_match:
            content = list_match.group(1).strip()
            # 排除形如 key: value 的 KV(但保留含 '|' 的 S/R 行)
            if not re.match(r"^[^|]+:", content) or "|" in content:
                items.append(content)
    return items


# ---------------------------------------------------------------- 类型安全访问器

def get_enum_field[T: str](
    fields: Mapping[str, str],
    key: str,
    allowed: Sequence[T],
    default_val: T,
) -> T:
    """安全提取枚举值:大小写不敏感 + 模糊匹配(去下划线/连字符/空格)。"""
    raw = fields.get(normalize_key(key))
    if not raw:
        return default_val

    normalized = raw.strip().lower()

    # 精确匹配
    for allowed_val in allowed:
        if allowed_val.lower() == normalized:
            return allowed_val

    # 模糊匹配:去掉 _ - 空格
    cleaned = re.sub(r"[_\s-]", "", normalized)
    for allowed_val in allowed:
        if re.sub(r"[_\s-]", "", allowed_val).lower() == cleaned:
            return allowed_val

    return default_val


def get_number_field(
    fields: Mapping[str, str],
    key: str,
    default_val: float,
    opts: Mapping[str, float] | None = None,
) -> float:
    """安全提取数值:取 raw 中第一个合法数字(含负数/小数);任何失败返回默认值。"""
    raw = fields.get(normalize_key(key))
    if not raw:
        return default_val

    num_match = _NUMBER_RE.search(raw)
    if not num_match:
        return default_val

    try:
        num = float(num_match.group(0))
    except ValueError:
        return default_val
    if not math.isfinite(num):
        return default_val

    opts = opts or {}
    if opts.get("min") is not None and num < float(opts["min"]):
        return default_val
    if opts.get("max") is not None and num > float(opts["max"]):
        return default_val

    return num


def get_boolean_field(fields: Mapping[str, str], key: str, default_val: bool) -> bool:
    """安全提取布尔值:接受 true/false、1/0、yes/no(大小写不敏感)。"""
    raw = fields.get(normalize_key(key))
    if not raw:
        return default_val

    normalized = raw.strip().lower()
    if normalized in ("true", "1", "yes"):
        return True
    if normalized in ("false", "0", "no"):
        return False

    return default_val


def get_string_field(fields: Mapping[str, str], key: str, default_val: str = "", max_length: int = 2000) -> str:
    """安全提取字符串:去掉 HTML 标签并按 maxLength 截断。"""
    raw = fields.get(normalize_key(key))
    if not raw or raw.strip() == "":
        return default_val

    sanitized = _TAG_RE.sub("", raw).strip()
    if len(sanitized) == 0:
        return default_val
    return sanitized[:max_length] if len(sanitized) > max_length else sanitized


# ---------------------------------------------------------------- 专用解析器

SRLevelParsed = dict[str, Any]
"""S/R level line 格式:`4287.50 | support | strong | H1 | 3`"""


def _js_number(value: str) -> float:
    """镜像 JS Number(string):空串 → 0,其余按 float 解析,失败 → NaN。"""
    if value == "":
        return 0.0
    try:
        return float(value)
    except ValueError:
        return float("nan")


def parse_sr_level_line(
    line: str,
    expected_type: Literal["support", "resistance"],
) -> SRLevelParsed | None:
    parts = [part.strip() for part in line.split("|")]
    if len(parts) < 2:
        return None

    price = _js_number(parts[0])
    if not math.isfinite(price) or price <= 0:
        return None

    # parts[1] 常为类型列,但为了 consistency 强制 expectedType
    strength_raw = (parts[2] if len(parts) > 2 else "").lower()
    strength = strength_raw if strength_raw in ("strong", "moderate", "weak") else "moderate"

    timeframe = parts[3] if len(parts) > 3 and parts[3] else "H1"

    touches_raw = _js_number(parts[4]) if len(parts) > 4 else float("nan")
    touches = (
        max(0, min(20, _math_round(touches_raw)))
        if math.isfinite(touches_raw)
        else 1
    )

    return {
        "price": price,
        "type": expected_type,
        "strength": strength,
        "timeframe": timeframe,
        "touches": touches,
    }


def parse_sr_levels(lines: Sequence[str], expected_type: Literal["support", "resistance"]) -> list[SRLevelParsed]:
    results: list[SRLevelParsed] = []
    for line in lines:
        parsed = parse_sr_level_line(line, expected_type)
        if parsed:
            results.append(parsed)
    return results[:6]  # Max 6 levels


def parse_warnings_line(line: str) -> list[str]:
    """Warnings 行:分号分隔字符串,去 HTML,过滤空与超长,最多 10 条。"""
    if not line:
        return []
    return [
        entry
        for entry in (
            _TAG_RE.sub("", segment.strip())
            for segment in line.split(";")
        )
        if len(entry) > 0 and len(entry) <= 500
    ][:10]


def extract_warnings(fields: Mapping[str, str], list_items: Sequence[str]) -> list[str]:
    """优先取 Warnings 字段(分号分隔);否则取形似 warning 的列表项。"""
    warnings_field = fields.get("warnings")
    if warnings_field:
        return parse_warnings_line(warnings_field)

    if len(list_items) > 0:
        return [
            entry
            for entry in (_TAG_RE.sub("", item.strip()) for item in list_items)
            if len(entry) > 0 and len(entry) <= 500
        ][:10]

    return []


# ---------------------------------------------------------------- 双格式探测

def detect_format(raw: str) -> Literal["markdown", "json", "unknown"]:
    """先试 Markdown;无 ## 头则尝试 JSON 提取。"""
    if re.search(r"^##\s+", raw, re.MULTILINE):
        return "markdown"
    if re.search(r"\{[\s\S]*\}", raw):
        return "json"
    return "unknown"


def _math_round(value: float) -> int:
    # 镜像 JS Math.round:floor(x + 0.5)
    return math.floor(value + 0.5)
