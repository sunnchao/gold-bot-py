"""Shared access helpers for the graph package (dataclass or dict values)."""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from typing import Any


def field_of(obj: Any, name: str, default: Any = None) -> Any:
    """Read a TS-named field from a dataclass or dict (None-safe)."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def coalesce(value: Any, default: Any) -> Any:
    """Mirror JS ``?? default``: only None falls back."""
    return default if value is None else value


def as_dict_shallow(value: Any) -> dict[str, Any]:
    """Shallow-convert a dataclass to a dict (passes dicts through)."""
    if isinstance(value, dict):
        return dict(value)
    if is_dataclass(value):
        return {f.name: getattr(value, f.name) for f in fields(value)}
    return dict(value)
