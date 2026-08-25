"""AI approve 命令构建(镜像 gold-bot apps/app-server/src/services/ai-approve/command.ts)。

逐字移植 TS 语义:command_id 由 `unixNanos(nowIso)` 生成、expiration = unixSeconds + 4h、
取利目标经 resolveAIApproveExecutableTakeProfits 归一化、加仓元数据(scale_in_*)只在
favorable/adverse 且存在 positions 时附加;输入输出统一使用 dict[str, Any]。
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from backend.persistence.records import EaRecord
from backend.services.ai_approve.rules import (
    pick_ai_approve_entry_price,
    resolve_ai_approve_executable_take_profits,
    round2,
)

__all__ = [
    "AIApproveCommandInput",
    "AiApproveCommand",
    "build_ai_approve_command_candidate",
    "create_ai_approve_command",
]

AIApproveCommandInput = dict[str, Any]


def build_ai_approve_command_candidate(command_input: AIApproveCommandInput) -> EaRecord:
    """镜像 buildAIApproveCommandCandidate:由已验收的门槛结果构建 EA 候选命令。"""
    trade_plan = command_input["tradePlan"]
    side = _string_field(trade_plan, "side").upper()
    entry_zone = _record_field(trade_plan, "entry_zone")
    entry_min = 0.0 if entry_zone is None else _number_field(entry_zone, "min")
    entry_max = 0.0 if entry_zone is None else _number_field(entry_zone, "max")
    entry = pick_ai_approve_entry_price(entry_zone)
    confidence = _number_field(trade_plan, "confidence")
    expiration = _unix_seconds(str(command_input["nowIso"])) + 4 * 60 * 60
    take_profit_values = _array_number_field(trade_plan, "take_profit")
    stop_loss = _number_field(trade_plan, "stop_loss")
    take_profits = resolve_ai_approve_executable_take_profits(
        {
            "side": side,
            "entry": entry,
            "stopLoss": stop_loss,
            "takeProfitValues": take_profit_values,
        }
    )
    if not take_profits["accepted"]:
        raise ValueError(
            f"AI approve command received invalid {take_profits['label']}: {take_profits['reason']}"
        )
    candidate: EaRecord = {
        "command_id": (
            f"ai_pending_{command_input['accountId']}_{command_input['symbol']}_"
            f"{_unix_nanos(str(command_input['nowIso']))}"
        ),
        "action": "SIGNAL",
        "symbol": command_input["symbol"],
        "type": side,
        "entry": entry,
        "entry_min": entry_min,
        "entry_max": entry_max,
        "sl": stop_loss,
        "tp": take_profits["legacyTakeProfit"],
        "tp1": take_profits["tp1"],
        "tp2": take_profits["tp2"],
        "lots": 0,
        "order_type": command_input["orderType"],
        "expiration": expiration,
        "score": confidence,
        "strategy": "ai_signal",
        "source": "ai_approve",
        "confidence": confidence,
        "decision_id": _string_field(trade_plan, "decision_id"),
        "reason": _string_field(trade_plan, "narrative"),
        "trade_plan_mode": _string_field(trade_plan, "mode"),
        "risk_gate": command_input["riskGate"],
        "tp_split": bool(take_profits["tpSplit"]),
    }

    add_on_type = _string_field(trade_plan, "add_on_type")
    if (add_on_type in ("favorable", "adverse")) and command_input.get("positions") is not None:
        positions = [
            pos
            for pos in command_input["positions"]
            if (
                _string_field(pos, "symbol").strip().upper()
                == str(command_input["symbol"]).strip().upper()
                or _string_field(pos, "symbol").strip().upper() == ""
            )
            and _string_field(pos, "type").strip().upper() == side
        ]
        if len(positions) > 0:
            largest_ticket: float = 0.0
            for pos in positions:
                ticket = _number_field(pos, "ticket")
                if ticket > largest_ticket:
                    largest_ticket = ticket
            total_lots = 0.0
            weighted_entry = 0.0
            for pos in positions:
                lots = _number_field(pos, "lots")
                open_price = _number_field(pos, "open_price") or _number_field(pos, "openPrice")
                if lots > 0 and open_price > 0:
                    total_lots += lots
                    weighted_entry += lots * open_price
            group_avg_entry = weighted_entry / total_lots if total_lots > 0 else 0.0
            open_prices = [
                open_price
                for pos in positions
                if (open_price := _number_field(pos, "open_price") or _number_field(pos, "openPrice")) > 0
            ]
            if len(open_prices) == 0:
                group_best_sl = 0.0
            elif add_on_type == "adverse":
                group_best_sl = min(open_prices) if side == "BUY" else max(open_prices)
            else:
                group_best_sl = max(open_prices) if side == "BUY" else min(open_prices)

            candidate["scale_in_parent_ticket"] = largest_ticket
            candidate["weighted_avg_entry"] = round2(group_avg_entry)
            candidate["unified_sl"] = round2(group_best_sl)
            candidate["scale_in_count"] = len(positions)

            if add_on_type == "adverse":
                candidate["scale_in_add_on_type"] = "adverse"
                candidate["scale_in_add_on_level"] = _number_field(trade_plan, "add_on_level") or 1

    return candidate


class AiApproveCommand:
    """供协调器注入的组合门面;模块函数 build_ai_approve_command_candidate 保持 1:1 语义。"""

    def __init__(
        self,
        metrics: Any = None,
        now_iso: Callable[[], str] | None = None,
        now_unix: Callable[[], int] | None = None,
        now_ms: Callable[[], float] | None = None,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self._metrics = metrics
        self._now_iso = now_iso or _default_now_iso
        self._now_unix = now_unix or _default_now_unix
        self._now_ms = now_ms or _default_now_ms
        self._log = log if log is not None else (lambda _message: None)

    def build_candidate(
        self,
        account_id: str,
        symbol: str,
        trade_plan: EaRecord,
        risk_gate: EaRecord,
        order_type: str,
        *,
        now_iso: str | None = None,
        positions: list[EaRecord] | None = None,
    ) -> EaRecord:
        command_input: AIApproveCommandInput = {
            "accountId": account_id,
            "symbol": symbol,
            "tradePlan": trade_plan,
            "riskGate": risk_gate,
            "nowIso": now_iso if now_iso is not None else self._now_iso(),
            "orderType": order_type,
        }
        if positions is not None:
            command_input["positions"] = positions
        candidate = build_ai_approve_command_candidate(command_input)
        self._log(f"[AI_APPROVE] built command {candidate['command_id']} {candidate['order_type']}")
        return candidate


def create_ai_approve_command(**options: Any) -> AiApproveCommand:
    return AiApproveCommand(**options)


def _unix_nanos(value: str) -> str:
    return str(int(_unix_millis(value)) * 1_000_000)


def _unix_seconds(value: str) -> int:
    return math.floor(_unix_millis(value) / 1000)


def _unix_millis(value: str) -> float:
    millis = _parse_millis(value)
    return millis if millis is not None else time.time() * 1000.0


def _parse_millis(value: str) -> float | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        millis = parsed.timestamp() * 1000.0
        return millis if math.isfinite(millis) else None
    except ValueError:
        return None


def _record_field(record: EaRecord, field: str) -> EaRecord | None:
    value = record.get(field)
    if isinstance(value, dict) and not isinstance(value, list):
        return value
    return None


def _number_field(record: EaRecord, field: str) -> float:
    value = record.get(field)
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return value
    return 0.0


def _string_field(record: EaRecord, field: str) -> str:
    value = record.get(field)
    return value if isinstance(value, str) else ""


def _array_number_field(record: EaRecord, field: str) -> list[float]:
    value = record.get(field)
    if not isinstance(value, list):
        return []
    return [
        item
        for item in value
        if isinstance(item, (int, float)) and not isinstance(item, bool) and math.isfinite(item)
    ]


def _default_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _default_now_unix() -> int:
    return int(datetime.now(UTC).timestamp())


def _default_now_ms() -> float:
    return time.time() * 1000.0
