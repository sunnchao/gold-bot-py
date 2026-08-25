"""AnalysisService(镜像 gold-bot apps/app-server/src/services/analysis/service.ts)。

按 TS 语义逐字移植:analyzeAccountSymbol 组装 runReplay 的输入快照并附上
positionSummary;persistPositionStates 落库新状态并清理过期行。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from backend.persistence.records import BE_TRIGGER_ATR_DEFAULT, EaRecord
from backend.persistence.store import EaStore
from backend.trading_core.positionmgr import summarize_positions
from backend.trading_core.replay import run_replay
from backend.trading_core.riskgate.riskgate import base_symbol

__all__ = [
    "AnalysisService",
    "create_analysis_service",
]

PositionManagerState = dict[str, Any]
PositionStateRecord = dict[str, Any]


def _default_now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class AnalysisService:
    def __init__(self, store: EaStore, now_iso: Callable[[], str] | None = None) -> None:
        self.store = store
        self._now_iso = now_iso if now_iso is not None else _default_now_iso

    async def analyze_account_symbol(self, account_id: str, symbol: str) -> dict[str, Any]:
        latest_tick = (await self.store.get_latest_tick(account_id, symbol)) or {}
        heartbeat = (await self.store.get_heartbeat(account_id)) or {}
        positions = _filter_positions_for_symbol(symbol, await self.store.get_positions(account_id, symbol))
        latest_ai_result = next(
            (
                result
                for result in (await self.store.get_ai_results(account_id))
                if _string_field(result, "symbol") == symbol
            ),
            None,
        )
        h1_bars = await self.store.get_bars(account_id, symbol, "H1")
        return {
            "replay": run_replay(
                {
                    "account_id": account_id,
                    "symbol": symbol,
                    "analysis_time": self._now_iso(),
                    "current_price": _current_price_for_replay(_current_price_from_tick(latest_tick), h1_bars),
                    "bars": {
                        "H1": h1_bars,
                        "H4": await self.store.get_bars(account_id, symbol, "H4"),
                        "M30": await self.store.get_bars(account_id, symbol, "M30"),
                        "M15": await self.store.get_bars(account_id, symbol, "M15"),
                        "M5": await self.store.get_bars(account_id, symbol, "M5"),
                        "M1": await self.store.get_bars(account_id, symbol, "M1"),
                        "D1": await self.store.get_bars(account_id, symbol, "D1"),
                    },
                    "positions": positions,
                    "position_states": await self.store.load_position_states(account_id, symbol),
                    "account": {
                        "equity": _optional_number_field(heartbeat, "equity"),
                        "balance": _optional_number_field(heartbeat, "balance"),
                    },
                    "ai_result": _replay_ai_result(latest_ai_result),
                }
            ),
            "positionSummary": summarize_positions(
                {
                    "accountId": account_id,
                    "symbol": symbol,
                    "positions": [_to_position_manager_position(position) for position in positions],
                }
            ),
        }

    async def persist_position_states(
        self,
        account_id: str,
        symbol: str,
        states: list[PositionManagerState] | None,
    ) -> None:
        if states is None:
            return
        for state in states:
            await self.store.save_position_state(account_id, symbol, _to_position_state_record(state, self._now_iso()))
        active_tickets = [int(_coalesce(state, "ticket", default=0)) for state in states]
        await self.store.delete_stale_position_states(account_id, symbol, active_tickets)


def create_analysis_service(options: dict[str, Any]) -> AnalysisService:
    """工厂:coordinator 在 main.py 组装依赖时通过 options 注入。"""
    return AnalysisService(
        store=options["store"],
        now_iso=options.get("now_iso"),
    )


def _replay_ai_result(payload: EaRecord | None) -> dict[str, Any] | None:
    if payload is None:
        return None
    return {
        "suggested_sl": _optional_number_field(payload, "suggested_sl"),
        "suggested_tp": _optional_number_field(payload, "suggested_tp"),
    }


def _current_price_from_tick(tick: EaRecord) -> float:
    ask = tick.get("ask")
    bid = tick.get("bid")
    ask_value = ask if _is_number(ask) else None
    bid_value = bid if _is_number(bid) else None
    if ask_value is not None:
        return float(ask_value)
    if bid_value is not None:
        return float(bid_value)
    return 0.0


def _current_price_for_replay(current_price: float, h1_bars: list[EaRecord]) -> float:
    if current_price != 0:
        return current_price
    latest_h1_close = h1_bars[-1].get("close") if len(h1_bars) > 0 else None
    if isinstance(latest_h1_close, (int, float)) and not isinstance(latest_h1_close, bool):
        return float(latest_h1_close)
    return current_price


def _filter_positions_for_symbol(symbol: str, positions: list[EaRecord]) -> list[EaRecord]:
    base = base_symbol(symbol)
    return [
        position
        for position in positions
        if _string_field(position, "symbol") == "" or base_symbol(_string_field(position, "symbol")) == base
    ]


def _string_field(record: EaRecord, field: str) -> str:
    value = record[field] if field in record else None
    return value if isinstance(value, str) else ""


def _optional_number_field(record: EaRecord, field: str) -> float | None:
    value = record[field] if field in record else None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _coalesce(record: EaRecord, key: str, default: Any = None) -> Any:
    value = record.get(key)
    return default if value is None else value


def _to_position_manager_position(position: EaRecord) -> dict[str, Any]:
    """镜像 toPositionManagerPosition:字段级类型守卫,保留 undefined 键位。"""
    return {
        "ticket": position.get("ticket") if _is_number(position.get("ticket")) else None,
        "symbol": position.get("symbol") if isinstance(position.get("symbol"), str) else "",
        "type": position.get("type") if isinstance(position.get("type"), str) else "",
        "lots": position.get("lots") if _is_number(position.get("lots")) else None,
        "openPrice": position.get("openPrice") if _is_number(position.get("openPrice")) else None,
        "open_price": position.get("open_price") if _is_number(position.get("open_price")) else None,
        "profit": position.get("profit") if _is_number(position.get("profit")) else None,
        "comment": position.get("comment") if isinstance(position.get("comment"), str) else "",
        "strategy": position.get("strategy") if isinstance(position.get("strategy"), str) else "",
        "magic": position.get("magic") if _is_number(position.get("magic")) else None,
    }


def _to_position_state_record(state: PositionManagerState, now_iso: str) -> PositionStateRecord:
    """镜像 toPositionStateRecord:`??` 逐字段合并,last_modify_time 恒为 now。"""
    return {
        "ticket": state["ticket"],
        "tp1_hit": _nullish_coalesce(state, "tp1Hit", "tp1_hit", default=False),
        "tp2_hit": _nullish_coalesce(state, "tp2Hit", "tp2_hit", default=False),
        "max_profit_atr": _nullish_coalesce(state, "maxProfitAtr", "max_profit_atr", default=0),
        "be_moved": _nullish_coalesce(state, "beMoved", "be_moved", default=False),
        "be_trigger_atr": _nullish_coalesce(state, "beTriggerAtr", "be_trigger_atr", default=BE_TRIGGER_ATR_DEFAULT),
        "best_sl": _nullish_coalesce(state, "bestSl", "best_sl", default=0),
        "open_time": _nullish_coalesce(state, "openTime", "open_time", default=now_iso),
        "last_modify_time": now_iso,
        "add_on_count": _nullish_coalesce(state, "addOnCount", "add_on_count", default=0),
        "last_add_on_time": _nullish_coalesce(state, "lastAddOnTime", "last_add_on_time", default=""),
        "last_add_on_price": _nullish_coalesce(state, "lastAddOnPrice", "last_add_on_price", default=0),
        "group_id": _nullish_coalesce(state, "groupId", "group_id", default=""),
        "group_avg_entry": _nullish_coalesce(state, "groupAvgEntry", "group_avg_entry", default=0),
        "group_best_sl": _nullish_coalesce(state, "groupBestSl", "group_best_sl", default=0),
        "trailing_closed": _nullish_coalesce(state, "trailingClosed", "trailing_closed", default=False),
    }


def _nullish_coalesce(state: PositionManagerState, *keys: str, default: Any) -> Any:
    for key in keys:
        value = state.get(key)
        if value is not None:
            return value
    return default
