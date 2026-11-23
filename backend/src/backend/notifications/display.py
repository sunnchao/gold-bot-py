from __future__ import annotations

import re
from typing import Any

_HIGHER_TIMEFRAME = re.compile(r"(?<![a-z0-9])htf(?![a-z0-9])", re.IGNORECASE | re.ASCII)
_DAILY_BUDGET = re.compile(
    r"(?:日(?:内|线)?|当[天日]|每日)\s*(?:ATR|平均真实波幅)\s*(?:(?:波动|振幅)?\s*预算|已?耗尽)"
    r"|(?:当日|日内)\s*(?:振幅|波幅|波动空间)\s*(?:已满|(?:已)?耗尽)"
    r"|daily\s+(?:ATR\s+budget|range\s+(?:is\s+)?exhaust(?:ed|ion))",
    re.IGNORECASE,
)
_ENTRY_REFUSAL = re.compile(
    r"(?:因此|所以|因而|故|建议|暂时|暂|应)*\s*(?:不开新仓|不新开仓|禁止开仓|禁止追价|不再开仓|不开仓)"
    r"|(?:so\s+|therefore\s+)?(?:no\s+new\s+(?:entries|positions|trades)|do\s+not\s+open\s+new\s+positions)",
    re.IGNORECASE,
)


def clean_feishu_text(text: str) -> str:
    text = _HIGHER_TIMEFRAME.sub("高周期(1H/4H)", text)
    if not _DAILY_BUDGET.search(text):
        return text
    parts = re.split(r"([，,；;。！？!?|\n]|\.(?=\s|$))", text)
    budgets = {index for index in range(0, len(parts), 2) if _DAILY_BUDGET.search(parts[index])}
    for index in range(0, len(parts), 2):
        adjacent = (index - 2 in budgets and parts[index - 1] in ("，", ",")) or (
            index + 2 in budgets and parts[index + 1] in ("，", ",")
        )
        refusal = adjacent and all(
            _ENTRY_REFUSAL.fullmatch(clause.strip(" *`（）()")) for clause in parts[index].split("/")
        )
        if index in budgets or refusal:
            parts[index] = ""
    for index in range(0, len(parts) - 1, 2):
        if not parts[index] and parts[index + 1] != "\n":
            parts[index + 1] = ""
    return "".join(parts).strip().rstrip("，,；;|")


def clean_feishu_card(card: dict[str, Any]) -> dict[str, Any]:
    cleaned = card.copy()
    for key, value in card.items():
        if key == "content" and isinstance(value, str):
            cleaned[key] = clean_feishu_text(value)
        elif isinstance(value, dict):
            cleaned[key] = clean_feishu_card(value)
        elif isinstance(value, list):
            cleaned[key] = [clean_feishu_card(item) if isinstance(item, dict) else item for item in value]
    return cleaned
