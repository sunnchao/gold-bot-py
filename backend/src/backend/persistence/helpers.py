"""纯辅助函数(镜像 gold-bot packages/persistence/src/{index,helpers}.ts 辅助段)。"""

from __future__ import annotations

import math
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from backend.persistence.records import (
    BE_TRIGGER_ATR_DEFAULT,
    DEFAULT_SYMBOL,
    CommandStatus,
    EaCommand,
    EaRecord,
    StoredCommand,
    is_command_source,
    is_command_status,
)


def account_id(payload: EaRecord) -> str:
    value = payload.get("account_id")
    return value if isinstance(value, str) else ""


def symbol_default(payload: EaRecord) -> str:
    value = payload.get("symbol")
    return value if isinstance(value, str) and len(value) > 0 else DEFAULT_SYMBOL


def string_field(payload: EaRecord, field: str) -> str:
    value = payload.get(field)
    return value if isinstance(value, str) else ""


def numeric_field(payload: EaRecord, field: str) -> float:
    value = payload.get(field)
    return value if isinstance(value, (int, float)) and math.isfinite(value) else 0.0


def boolean_field(payload: EaRecord, field: str) -> bool:
    return payload.get(field) is True


def string_array_field(payload: EaRecord | None, field: str) -> list[str]:
    value = payload.get(field) if payload is not None else None
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and len(item) > 0]


def current_timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def unix_seconds(value: str) -> int:
    try:
        millis = datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000
    except ValueError:
        return math.floor(time_now_unixtime())
    return math.floor(millis / 1000)


def time_now_unixtime() -> float:
    return datetime.now(UTC).timestamp()


def timestamp_millis(value: str) -> float | None:
    try:
        millis = datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000
    except ValueError:
        return None
    return millis if math.isfinite(millis) else None


def equals_fold(left: str, right: str) -> bool:
    return left.strip().upper() == right.strip().upper()


def normalize_command_source(value: Any) -> str:
    return value if isinstance(value, str) and is_command_source(value) else "ea_analysis"


def clone_record(value: EaRecord) -> EaRecord:
    return __deep_copy(value)


def clone_array(value: list[Any]) -> list[EaRecord]:
    return __deep_copy(value)


def to_json(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def from_json(value: str) -> Any:
    import json

    return json.loads(value)


def __deep_copy(value: Any) -> Any:
    import copy

    return copy.deepcopy(value)


def is_ack_result(result: str) -> bool:
    return result == "OK"


def make_command_id() -> str:
    import random
    import time

    suffix = "".join(random.choices("abcdefghijklmnopqrstuvwxyz0123456789", k=6))
    return f"cmd_{int(time.time() * 1000)}_{suffix}"


def build_stored_command(account: str, candidate: EaRecord, status: CommandStatus) -> tuple[StoredCommand, bool]:
    """镜像 createStoredCommand;返回 (stored, poll_source_visible)。"""
    source = normalize_command_source(candidate.get("source"))
    command_id = candidate.get("command_id")
    if not isinstance(command_id, str) or len(command_id) == 0:
        command_id = make_command_id()
    stored: StoredCommand = {
        **clone_record(candidate),
        "account_id": account,
        "command_id": command_id,
        "action": str(candidate.get("action", "")),
        "source": source,
        "status": status,
        "created_at": current_timestamp(),
    }
    poll_visible = "source" in candidate
    return stored, poll_visible


def to_ea_command(command: StoredCommand, poll_source_visible: bool = False) -> EaCommand:
    """镜像 toEaCommand:移除与 EA 通信无关的服务端字段。"""
    out: EaCommand = dict(command)
    for key in (
        "account_id",
        "status",
        "created_at",
        "delivered_at",
        "acked_at",
        "failed_at",
        "error_text",
        "result",
    ):
        out.pop(key, None)
    if not poll_source_visible:
        out.pop("source", None)
    if out.get("ticket") is None:
        out.pop("ticket", None)
    return out


def normalize_position_state(state: EaRecord) -> PositionState_Internal:
    """镜像 normalizePositionState(field 级校验 + 默认值)。"""
    now = current_timestamp()
    return {
        "ticket": int(numeric_field(state, "ticket")),
        "tp1_hit": state.get("tp1_hit") is True,
        "tp2_hit": state.get("tp2_hit") is True,
        "max_profit_atr": numeric_field(state, "max_profit_atr"),
        "be_moved": state.get("be_moved") is True,
        "be_trigger_atr": (
            numeric_field(state, "be_trigger_atr")
            if math.isfinite(numeric_field(state, "be_trigger_atr"))
            else BE_TRIGGER_ATR_DEFAULT
        ),
        "best_sl": numeric_field(state, "best_sl"),
        "open_time": string_field(state, "open_time") or now,
        "last_modify_time": string_field(state, "last_modify_time") or now,
        "add_on_count": (
            int(numeric_field(state, "add_on_count"))
            if numeric_field(state, "add_on_count") == int(numeric_field(state, "add_on_count"))
            else 0
        ),
        "last_add_on_time": string_field(state, "last_add_on_time"),
        "last_add_on_price": numeric_field(state, "last_add_on_price"),
        "group_id": string_field(state, "group_id"),
        "group_avg_entry": numeric_field(state, "group_avg_entry"),
        "group_best_sl": numeric_field(state, "group_best_sl"),
        "trailing_closed": state.get("trailing_closed") is True,
    }


PositionState_Internal = dict[str, Any]


def position_state_from_row(row: dict[str, Any]) -> EaRecord:
    return {
        "ticket": int(row["ticket"]),
        "tp1_hit": int(row["tp1_hit"]) != 0,
        "tp2_hit": int(row["tp2_hit"]) != 0,
        "max_profit_atr": float(row["max_profit_atr"]),
        "be_moved": int(row["be_moved"]) != 0,
        "be_trigger_atr": float(row["be_trigger_atr"]),
        "best_sl": float(row["best_sl"]),
        "open_time": str(row["open_time"]),
        "last_modify_time": str(row["last_modify_time"]),
        "add_on_count": int(row["add_on_count"]) or 0,
        "last_add_on_time": str(row["last_add_on_time"]),
        "last_add_on_price": float(row["last_add_on_price"]) or 0.0,
        "group_id": str(row["group_id"]),
        "group_avg_entry": float(row["group_avg_entry"]) or 0.0,
        "group_best_sl": float(row["group_best_sl"]) or 0.0,
        "trailing_closed": int(row["trailing_closed"]) != 0,
    }


def is_runtime_command_expired(command: StoredCommand, now_iso: str) -> bool:
    now = unix_seconds(now_iso)
    expiration = command.get("expiration")
    if isinstance(expiration, (int, float)) and math.isfinite(expiration):
        return math.trunc(expiration) <= now
    if command.get("source") == "ai_approve":
        created = timestamp_millis(command.get("created_at", ""))
        if created is not None:
            return math.floor(created / 1000) + 4 * 60 * 60 <= now
    return False


def is_active_ai_approve_pending(command: StoredCommand, account: str, symbol: str, side: str, now_iso: str) -> bool:
    if (
        command.get("account_id") != account
        or command.get("status") != "queued"
        or command.get("source") != "ai_approve"
    ):
        return False
    if not equals_fold(string_field(command, "symbol"), symbol):
        return False
    if not equals_fold(string_field(command, "type"), side):
        return False
    expiration = command.get("expiration")
    if (
        isinstance(expiration, (int, float))
        and math.isfinite(expiration)
        and math.trunc(expiration) <= unix_seconds(now_iso)
    ):
        return False
    return True


def is_pending_signal_expired(signal: EaRecord, now_iso: str) -> bool:
    expires_at = string_field(signal, "expires_at")
    expires_ms = timestamp_millis(expires_at)
    now_ms = timestamp_millis(now_iso)
    if expires_ms is not None and now_ms is not None:
        return expires_ms < now_ms
    return expires_at < now_iso


def update_pending_signal_payload(signal: EaRecord, result: str, reason: str) -> None:
    signal["status"] = "rejected" if result == "rejected" else "approved"
    signal["arbitration_result"] = result
    signal["arbitration_reason"] = reason


def normalize_pending_signal(payload: EaRecord) -> EaRecord:
    out = clone_record(payload)
    if len(string_field(out, "status")) == 0:
        out["status"] = "pending"
    return out


def normalize_api_token(payload: EaRecord) -> EaRecord:
    accounts = payload.get("accounts")
    return {
        "token": string_field(payload, "token"),
        "name": string_field(payload, "name"),
        "accounts": list(accounts) if isinstance(accounts, list) else [],
        "is_admin": payload.get("is_admin") is True,
        "created_at": string_field(payload, "created_at") or current_timestamp(),
    }


def normalize_decision_event(payload: EaRecord) -> EaRecord:
    reason_codes = payload.get("reason_codes")
    summary = payload.get("summary")
    return {
        "decision_id": string_field(payload, "decision_id"),
        "account_id": string_field(payload, "account_id"),
        "symbol": string_field(payload, "symbol"),
        "stage": string_field(payload, "stage"),
        "status": string_field(payload, "status"),
        "reason_codes": [c for c in reason_codes if isinstance(c, str)] if isinstance(reason_codes, list) else [],
        "summary": dict(summary) if isinstance(summary, dict) else {},
        "related_command_id": string_field(payload, "related_command_id"),
        "created_at": string_field(payload, "created_at"),
    }


def command_decision_reason_codes(command: StoredCommand, poll_source_visible: bool) -> list[str]:
    codes = [f"command.{command.get('action', '')}"]
    if poll_source_visible:
        codes.append(f"source.{command.get('source', '')}")
    return codes


def command_decision_event(
    command: StoredCommand,
    stage: str,
    status: str,
    created_at: str,
    poll_source_visible: bool,
    summary_extra: EaRecord | None = None,
) -> EaRecord | None:
    decision_id = string_field(command, "decision_id")
    if len(decision_id) == 0:
        return None
    symbol = string_field(command, "symbol") if len(string_field(command, "symbol")) > 0 else DEFAULT_SYMBOL
    summary: EaRecord = {
        "command_id": command.get("command_id", ""),
        "action": command.get("action", ""),
    }
    if summary_extra:
        summary.update(summary_extra)
    return {
        "decision_id": decision_id,
        "account_id": string_field(command, "account_id"),
        "symbol": symbol,
        "stage": stage,
        "status": status,
        "reason_codes": command_decision_reason_codes(command, poll_source_visible),
        "summary": summary,
        "related_command_id": command.get("command_id", ""),
        "created_at": created_at,
    }


def command_result_decision_event(
    command: StoredCommand,
    result: str,
    ticket: int,
    error_text: str,
    created_at: str,
    poll_source_visible: bool,
) -> EaRecord | None:
    status = "acked" if command.get("status") == "acked" else "failed"
    return command_decision_event(
        command,
        "order_result",
        status,
        created_at,
        poll_source_visible,
        {"result": result, "ticket": ticket, "error_text": error_text},
    )


def candidate_signal_decision_event(signal: EaRecord) -> EaRecord | None:
    signal_id = numeric_field(signal, "id")
    if signal_id <= 0:
        return None
    account = account_id(signal)
    symbol = symbol_default(signal)
    strategy = string_field(signal, "strategy")
    created_at = string_field(signal, "created_at") or current_timestamp()
    return {
        "decision_id": f"candidate_{account}_{symbol}_{int(signal_id)}",
        "account_id": account,
        "symbol": symbol,
        "stage": "candidate_signal",
        "status": "pending",
        "reason_codes": [f"candidate.{strategy}"] if len(strategy) > 0 else ["candidate."],
        "summary": {
            "signal_id": int(signal_id),
            "side": string_field(signal, "side"),
            "score": numeric_field(signal, "score"),
            "strategy": strategy,
            "expires_at": string_field(signal, "expires_at"),
        },
        "related_command_id": "",
        "created_at": created_at,
    }


def normalize_decision_limit(limit: int | None) -> int:
    return 50 if limit is None or limit <= 0 or limit > 200 else limit


def filter_decision_events(events: list[EaRecord], filter_: EaRecord) -> list[EaRecord]:
    account = string_field(filter_, "account_id")
    symbol = string_field(filter_, "symbol")
    status = string_field(filter_, "status")
    limit = normalize_decision_limit(int(numeric_field(filter_, "limit")) if filter_.get("limit") else None)
    filtered = [
        e
        for e in events
        if e.get("account_id") == account
        and (len(symbol) == 0 or str(e.get("symbol", "")).upper() == symbol.upper())
        and (len(status) == 0 or e.get("status") == status)
    ]
    filtered.sort(key=lambda e: (e.get("created_at", ""), e.get("id", 0)), reverse=True)
    return filtered[:limit]


def filter_shadow_comparisons(comparisons: list[EaRecord], filter_: EaRecord | None) -> list[EaRecord]:
    if filter_ is None:
        return comparisons
    out: list[EaRecord] = []
    for c in comparisons:
        if "account_id" in filter_ and c.get("account_id") != filter_["account_id"]:
            continue
        if "symbol" in filter_ and c.get("symbol") != filter_["symbol"]:
            continue
        if "source" in filter_ and c.get("source") != filter_["source"]:
            continue
        for key, default in (
            ("protocol_ok", True),
            ("signal_drift", False),
            ("command_drift", False),
            ("oracle_compared", False),
        ):
            if key in filter_ and bool(c.get(key, default)) != bool(filter_[key]):
                break
        else:
            if "created_at_gte" in filter_ and c.get("created_at", "") < filter_["created_at_gte"]:
                continue
            if "created_at_lte" in filter_ and c.get("created_at", "") > filter_["created_at_lte"]:
                continue
            out.append(c)
    return out


def summarize_shadow_comparisons(comparisons: list[EaRecord]) -> EaRecord:
    return {
        "comparisons": len(comparisons),
        "protocol_errors": sum(1 for c in comparisons if not bool(c.get("protocol_ok", True))),
        "signal_drifts": sum(1 for c in comparisons if bool(c.get("signal_drift", False))),
        "command_drifts": sum(1 for c in comparisons if bool(c.get("command_drift", False))),
        "oracle_compared": sum(1 for c in comparisons if bool(c.get("oracle_compared", False))),
        "first_created_at": comparisons[0].get("created_at", "") if comparisons else "",
        "last_created_at": comparisons[-1].get("created_at", "") if comparisons else "",
    }


def build_closed_trade_stats(trades: Iterable[EaRecord]) -> list[EaRecord]:
    by_strategy: dict[str, list[EaRecord]] = {}
    for trade in trades:
        key = string_field(trade, "strategy") or "unknown"
        by_strategy.setdefault(key, []).append(trade)
    result: list[EaRecord] = []
    for strategy, bucket in by_strategy.items():
        wins = [t for t in bucket if float(t.get("profit", 0)) > 0]
        losses = [t for t in bucket if float(t.get("profit", 0)) <= 0]
        total_profit = sum(float(t.get("profit", 0)) for t in bucket)
        avg_win = sum(float(t.get("profit", 0)) for t in wins) / len(wins) if wins else 0.0
        avg_loss = sum(float(t.get("profit", 0)) for t in losses) / len(losses) if losses else 0.0
        win_rate = len(wins) / len(bucket) if bucket else 0.0
        result.append(
            {
                "strategy": strategy,
                "total": len(bucket),
                "wins": len(wins),
                "losses": len(losses),
                "win_rate": win_rate,
                "total_profit": total_profit,
                "avg_profit": total_profit / len(bucket) if bucket else 0.0,
                "avg_win": avg_win,
                "avg_loss": avg_loss,
                "expectancy": win_rate * avg_win + (1 - win_rate) * avg_loss,
                "avg_duration_min": (
                    sum(float(t.get("duration_min", 0)) for t in bucket) / len(bucket) if bucket else 0.0
                ),
            }
        )
    result.sort(key=lambda r: r["total"], reverse=True)
    return result


def build_runtime_command(command_id: str, row: EaRecord) -> tuple[StoredCommand, bool]:
    """镜像 buildRuntimeCommand:从 runtime_commands 行还原 StoredCommand。"""
    payload = from_json(string_field(row, "payload_json"))
    if not isinstance(payload, dict):
        payload = {}
    status = string_field(row, "status") if is_command_status(string_field(row, "status")) else "draft"
    source = string_field(row, "source") if is_command_source(string_field(row, "source")) else "ea_analysis"
    command: StoredCommand = {
        **payload,
        "account_id": string_field(row, "account_id"),
        "command_id": (
            string_field(payload, "command_id") if len(string_field(payload, "command_id")) > 0 else command_id
        ),
        "action": string_field(payload, "action"),
        "source": source,
        "status": status,
        "created_at": string_field(row, "created_at"),
    }
    if len(string_field(row, "delivered_at")) > 0:
        command["delivered_at"] = string_field(row, "delivered_at")
    if len(string_field(row, "acked_at")) > 0:
        command["acked_at"] = string_field(row, "acked_at")
    if len(string_field(row, "failed_at")) > 0:
        command["failed_at"] = string_field(row, "failed_at")
    if len(string_field(row, "result")) > 0:
        command["result"] = string_field(row, "result")
    ticket = row.get("ticket")
    if isinstance(ticket, (int, float)) and not isinstance(ticket, bool):
        command["ticket"] = int(ticket)
    if len(string_field(row, "error_text")) > 0:
        command["error_text"] = string_field(row, "error_text")
    payload_source_visible = "source" in payload
    return command, payload_source_visible
