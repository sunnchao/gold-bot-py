"""内存版 EaStore(镜像 gold-bot packages/persistence/src/index.ts createInMemoryEaStore)。

用于单元测试与服务注入;语义必须与 SQLite 版逐项一致(共享 suite 验证)。
"""

from __future__ import annotations

from backend.persistence.helpers import (
    account_id,
    build_closed_trade_stats,
    build_stored_command,
    candidate_signal_decision_event,
    clone_array,
    clone_record,
    command_decision_event,
    command_delivery_rejection,
    command_result_decision_event,
    current_timestamp,
    filter_decision_events,
    filter_shadow_comparisons,
    is_active_ai_approve_pending,
    is_pending_signal_expired,
    normalize_api_token,
    normalize_decision_event,
    normalize_pending_signal,
    normalize_position_state,
    normalize_symbol,
    normalize_timeframe,
    numeric_field,
    position_state_from_row,
    requires_batch_cancel,
    resolve_display_symbols,
    string_array_field,
    string_field,
    summarize_shadow_comparisons,
    symbol_default,
    to_ea_command,
    update_pending_signal_payload,
)
from backend.persistence.records import (
    EaRecord,
    StoredApiToken,
    StoredCommand,
)
from backend.persistence.store import EaStore

SnapshotKey = tuple[str, str, str, str]  # (kind, account_id, symbol, timeframe)


class InMemoryEaStore(EaStore):
    """dict 驱动的 EaStore;与 SQLite 版共享同一测试套件。"""

    def __init__(self) -> None:
        self._snapshots: dict[SnapshotKey, EaRecord] = {}
        self._snapshot_order: dict[SnapshotKey, int] = {}
        self._snapshot_seq = 0
        self._events: list[EaRecord] = []  # {kind, account_id, symbol, payload, delivered}
        self._position_states: dict[str, EaRecord] = {}  # key account:symbol:ticket
        self._runtime_modes: dict[str, str] = {}
        self._commands: dict[str, StoredCommand] = {}
        self._poll_source_visible: dict[str, bool] = {}
        self._shadow_comparisons: list[EaRecord] = []
        self._shadow_snapshots: dict[str, EaRecord] = {}
        self._decision_events: list[EaRecord] = []
        self._pending_signals: dict[str, list[EaRecord]] = {}
        self._ai_results: dict[tuple[str, str], EaRecord] = {}
        self._api_tokens: dict[str, EaRecord] = {}
        self._closed_trades: list[EaRecord] = []
        self._daily_equities: dict[str, float] = {}
        self._bar_close_events: set[tuple[str, str, str, str]] = set()
        self._watch_ledgers: dict[tuple[str, str], EaRecord] = {}
        self._patrol_logs: list[EaRecord] = []
        self._next_decision_event_id = 1
        self._next_pending_signal_id = 1
        self._next_patrol_log_id = 1

    # ------------------------------------------------------------------ 快照
    def _snapshot(self, kind: str, account: str, symbol: str, timeframe: str = "") -> EaRecord | None:
        key = (kind, account, normalize_symbol(symbol), normalize_timeframe(timeframe))
        value = self._snapshots.get(key)
        return None if value is None else clone_record(value)

    def _save_snapshot(self, kind: str, account: str, symbol: str, payload: EaRecord, timeframe: str = "") -> None:
        # 写侧统一折叠大小写(与 SQLite/PG 版一致),保证同品种只有一份快照;
        # 否则 GOLDM#(bars) 与 GOLDm#(tick) 会共存,读侧只能命中其中一份(可能是旧数据)。
        key = (kind, account, normalize_symbol(symbol), normalize_timeframe(timeframe))
        self._snapshots[key] = clone_record(payload)
        self._snapshot_seq += 1
        self._snapshot_order[key] = self._snapshot_seq

    async def save_registration(self, payload: EaRecord) -> None:
        self._save_snapshot("registration", account_id(payload), "", payload)

    async def get_registration(self, account_id_: str) -> EaRecord | None:
        return self._snapshot("registration", account_id_, "")

    async def save_heartbeat(self, payload: EaRecord) -> None:
        self._save_snapshot("heartbeat", account_id(payload), "", payload)

    async def get_heartbeat(self, account_id_: str) -> EaRecord | None:
        return self._snapshot("heartbeat", account_id_, "")

    async def save_tick(self, payload: EaRecord) -> None:
        self._save_snapshot("tick", account_id(payload), symbol_default(payload), payload)

    async def get_latest_tick(self, account_id_: str, symbol: str) -> EaRecord | None:
        return self._snapshot("tick", account_id_, symbol)

    async def save_bars(self, payload: EaRecord) -> None:
        self._save_snapshot(
            "bars", account_id(payload), symbol_default(payload), payload, string_field(payload, "timeframe")
        )

    async def get_bars(self, account_id_: str, symbol: str, timeframe: str) -> list[EaRecord]:
        record = self._snapshot("bars", account_id_, symbol, timeframe)
        bars = record.get("bars") if record is not None else None
        return clone_array(bars) if isinstance(bars, list) else []

    async def claim_bar_close_event(
        self, account_id_: str, symbol: str, timeframe: str, bar_time: str
    ) -> bool:
        key = (account_id_, normalize_symbol(symbol), normalize_timeframe(timeframe), bar_time)
        if key in self._bar_close_events:
            return False
        self._bar_close_events.add(key)
        return True

    async def save_positions(self, payload: EaRecord) -> None:
        self._save_snapshot("positions", account_id(payload), symbol_default(payload), payload)

    async def get_positions(self, account_id_: str, symbol: str | None = None) -> list[EaRecord]:
        if symbol is not None and len(symbol) > 0:
            record = self._snapshot("positions", account_id_, symbol)
            positions = record.get("positions") if isinstance(record, dict) else None
            return clone_array(positions) if isinstance(positions, list) else []
        records: list[EaRecord] = []
        for (kind, account, _key_symbol, _), value in self._snapshots.items():
            if kind != "positions" or account != account_id_:
                continue
            positions = value.get("positions")
            if isinstance(positions, list):
                records.extend(clone_array(positions))
        return records

    # ------------------------------------------------------------------ 命令
    async def enqueue_command(self, account_id_: str, command: EaRecord) -> None:
        stored, visible = build_stored_command(account_id_, command, "queued")
        self._commands[stored["command_id"]] = stored
        self._poll_source_visible[stored["command_id"]] = visible
        self._record_command_decision(stored, "command_enqueued", "pending", stored["created_at"], visible)

    async def save_command_candidate(self, account_id_: str, candidate: EaRecord) -> StoredCommand:
        stored, visible = build_stored_command(account_id_, candidate, "draft")
        self._commands[stored["command_id"]] = stored
        self._poll_source_visible[stored["command_id"]] = visible
        return clone_record(stored)

    async def promote_command(self, command_id: str) -> None:
        command = self._commands.get(command_id)
        if command is None:
            return
        was_queued = command.get("status") == "queued"
        command["status"] = "queued"
        if not was_queued:
            self._record_command_decision(
                command,
                "command_enqueued",
                "pending",
                command.get("created_at", ""),
                self._poll_source_visible.get(command_id, False),
            )

    async def demote_command_to_shadow_only(self, command_id: str) -> None:
        command = self._commands.get(command_id)
        if command is not None:
            command["status"] = "shadow_only"

    async def get_command(self, command_id: str) -> StoredCommand | None:
        command = self._commands.get(command_id)
        return None if command is None else clone_record(command)

    async def list_commands(self, account_id_: str) -> list[StoredCommand]:
        return [
            clone_record(c)
            for c in sorted(
                (c for c in self._commands.values() if c.get("account_id") == account_id_),
                key=lambda c: c.get("created_at", ""),
            )
        ]

    async def has_active_ai_approve_pending(self, account_id_: str, symbol: str, side: str, now_iso: str) -> bool:
        return any(is_active_ai_approve_pending(c, account_id_, symbol, side, now_iso) for c in self._commands.values())

    async def get_runtime_mode(self, account_id_: str) -> str:
        return self._runtime_modes.get(account_id_, "oracle")

    async def set_runtime_mode(self, account_id_: str, mode: str) -> None:
        self._runtime_modes[account_id_] = mode

    async def reconcile_command_result(
        self,
        account_id_: str,
        command_id: str,
        result: str,
        ticket: int | None = None,
        error_text: str = "",
        created_at: str | None = None,
    ) -> bool:
        command = self._commands.get(command_id)
        if command is None or command.get("account_id") != account_id_ or command.get("status") != "delivered":
            return False
        created_at = created_at or current_timestamp()
        normalized_ticket = ticket if ticket is not None else 0
        status = "acked" if result == "OK" else "failed"
        command["result"] = result
        command["ticket"] = normalized_ticket
        command["error_text"] = error_text
        command["status"] = status
        if status == "acked":
            command["acked_at"] = created_at
        else:
            command["failed_at"] = created_at
        self._events.append(
            {
                "kind": "order_result",
                "account_id": account_id_,
                "symbol": "",
                "payload": {
                    "account_id": account_id_,
                    "command_id": command_id,
                    "result": result,
                    "ticket": normalized_ticket,
                    "error_text": error_text,
                    "created_at": created_at,
                },
                "delivered": 1,
            }
        )
        event = command_result_decision_event(
            command, result, normalized_ticket, error_text, created_at, self._poll_source_visible.get(command_id, False)
        )
        if event is not None:
            self._decision_events.append(self._finalize_decision(event))
        return True

    async def save_order_result(self, payload: EaRecord) -> None:
        self._events.append(
            {
                "kind": "order_result",
                "account_id": account_id(payload),
                "symbol": "",
                "payload": clone_record(payload),
                "delivered": 1,
            }
        )

    async def get_order_results(self, account_id_: str) -> list[EaRecord]:
        return [
            clone_record(e["payload"])
            for e in self._events
            if e["kind"] == "order_result" and e["account_id"] == account_id_
        ]

    async def poll_commands(
        self, account_id_: str, *, now_iso: str | None = None, event_controls: bool = True,
    ) -> list[EaRecord]:
        pending = sorted(
            (c for c in self._commands.values() if c.get("account_id") == account_id_ and c.get("status") == "queued"),
            key=lambda c: (c.get("created_at", ""), c.get("command_id", "")),
        )
        delivered_at = now_iso or current_timestamp()
        delivered: list[StoredCommand] = []
        for command in pending:
            command_id = command.get("command_id", "")
            rejection = command_delivery_rejection(command, delivered_at, event_controls)
            if rejection is not None:
                result, error_text = rejection
                command["status"] = "failed"
                command["result"] = result
                command["ticket"] = 0
                command["error_text"] = error_text
                command["failed_at"] = delivered_at
                event = command_result_decision_event(
                    command,
                    result,
                    0,
                    error_text,
                    delivered_at,
                    self._poll_source_visible.get(command_id, False),
                )
                if event is not None:
                    self._decision_events.append(self._finalize_decision(event))
                continue
            if not event_controls and requires_batch_cancel(command):
                continue
            command["status"] = "delivered"
            command["delivered_at"] = delivered_at
            self._record_command_decision(
                command,
                "command_delivered",
                "delivered",
                delivered_at,
                self._poll_source_visible.get(command_id, False),
            )
            delivered.append(command)
        return [to_ea_command(c, self._poll_source_visible.get(c.get("command_id", ""), False)) for c in delivered]

    # ------------------------------------------------------------------ 持仓状态
    async def save_position_state(self, account_id_: str, symbol: str, state: EaRecord) -> None:
        normalized = normalize_position_state(state)
        self._position_states[f"{account_id_}:{normalize_symbol(symbol)}:{normalized['ticket']}"] = normalized

    async def load_position_states(self, account_id_: str, symbol: str) -> list[EaRecord]:
        # 只折叠 symbol 段;account_id 精确匹配(大小写不同的账户是不同账户)。
        prefix = f"{account_id_}:{normalize_symbol(symbol)}:"
        states = [position_state_from_row(v) for k, v in self._position_states.items() if k.startswith(prefix)]
        return sorted(states, key=lambda s: int(s["ticket"]))

    async def delete_stale_position_states(self, account_id_: str, symbol: str, active_tickets: list[int]) -> None:
        active = set(active_tickets)
        prefix = f"{account_id_}:{normalize_symbol(symbol)}:"
        for key in list(self._position_states.keys()):
            if not key.startswith(prefix):
                continue
            ticket = int(key[len(prefix) :])
            if ticket not in active:
                del self._position_states[key]

    # ------------------------------------------------------------------ 影子
    async def record_shadow_comparison(self, payload: EaRecord) -> None:
        self._shadow_comparisons.append(clone_record(payload))

    async def list_shadow_comparisons(self, filter_: EaRecord | None = None) -> list[EaRecord]:
        return clone_array(filter_shadow_comparisons(self._shadow_comparisons, filter_))

    async def summarize_shadow_comparisons(self, filter_: EaRecord | None = None) -> EaRecord:
        return summarize_shadow_comparisons(filter_shadow_comparisons(self._shadow_comparisons, filter_))

    async def save_shadow_snapshot(self, payload: EaRecord) -> None:
        symbol_norm = normalize_symbol(string_field(payload, "symbol"))
        key = f"{payload.get('account_id', '')}:{symbol_norm}:{payload.get('source', '')}"
        self._shadow_snapshots[key] = clone_record(payload)

    async def get_latest_shadow_snapshot(self, account_id_: str, symbol: str, source: str) -> EaRecord | None:
        value = self._shadow_snapshots.get(f"{account_id_}:{normalize_symbol(symbol)}:{source}")
        return None if value is None else clone_record(value)

    # ------------------------------------------------------------------ 决策时间线
    def _finalize_decision(self, event: EaRecord) -> EaRecord:
        normalized = normalize_decision_event(event)
        normalized["id"] = self._next_decision_event_id
        self._next_decision_event_id += 1
        return normalized

    def _record_command_decision(
        self, command: StoredCommand, stage: str, status: str, created_at: str, visible: bool
    ) -> None:
        event = command_decision_event(command, stage, status, created_at, visible)
        if event is not None:
            self._decision_events.append(self._finalize_decision(event))

    async def record_decision_event(self, payload: EaRecord) -> None:
        self._decision_events.append(self._finalize_decision(payload))

    async def list_decision_events(self, filter_: EaRecord) -> list[EaRecord]:
        return clone_array(filter_decision_events(self._decision_events, filter_))

    # ------------------------------------------------------------------ 候选信号
    async def save_pending_signal(self, payload: EaRecord) -> None:
        signal = normalize_pending_signal(payload)
        explicit_id = int(numeric_field(signal, "id"))
        key = f"{account_id(signal)}:{normalize_symbol(symbol_default(signal))}"
        if explicit_id > 0:
            if self._replace_pending_signal(signal, key):
                self._next_pending_signal_id = max(self._next_pending_signal_id, explicit_id + 1)
            return
        signal["id"] = self._next_pending_signal_id
        self._next_pending_signal_id += 1
        self._pending_signals.setdefault(key, []).append(clone_record(signal))
        event = candidate_signal_decision_event(signal)
        if event is not None:
            self._decision_events.append(self._finalize_decision(event))

    def _replace_pending_signal(self, signal: EaRecord, next_key: str) -> bool:
        signal_id = int(numeric_field(signal, "id"))
        for current_key, entries in list(self._pending_signals.items()):
            for index, entry in enumerate(entries):
                if int(numeric_field(entry, "id")) != signal_id:
                    continue
                if current_key == next_key:
                    entries[index] = clone_record(signal)
                else:
                    remaining = [*entries[:index], *entries[index + 1 :]]
                    if remaining:
                        self._pending_signals[current_key] = remaining
                    else:
                        del self._pending_signals[current_key]
                    self._pending_signals.setdefault(next_key, []).append(clone_record(signal))
                return True
        return False

    async def get_pending_signals(self, account_id_: str, symbol: str) -> list[EaRecord]:
        # 大小写不敏感只作用于 symbol;account_id 精确匹配(与 SQLite 版语义对齐)。
        entries = self._pending_signals.get(f"{account_id_}:{normalize_symbol(symbol)}", [])
        pending = [e for e in entries if string_field(e, "status") == "pending"]
        pending.sort(key=lambda e: string_field(e, "created_at"), reverse=True)
        return [clone_record(e) for e in pending]

    async def get_pending_signal_by_id(self, account_id_: str, symbol: str, id_: int) -> EaRecord | None:
        for entry in self._pending_signals.get(f"{account_id_}:{normalize_symbol(symbol)}", []):
            if int(numeric_field(entry, "id")) == id_:
                return clone_record(entry)
        return None

    async def update_pending_signal_arbitration(self, id_: int, result: str, reason: str) -> bool:
        for entries in self._pending_signals.values():
            for signal in entries:
                if int(numeric_field(signal, "id")) == id_:
                    update_pending_signal_payload(signal, result, reason)
                    return True
        return False

    async def expire_pending_signals(self, now_iso: str) -> int:
        expired = 0
        for entries in self._pending_signals.values():
            for signal in entries:
                if string_field(signal, "status") == "pending" and is_pending_signal_expired(signal, now_iso):
                    signal["status"] = "timeout"
                    signal["arbitration_result"] = "timeout"
                    signal["arbitration_reason"] = "expired"
                    expired += 1
        return expired

    # ------------------------------------------------------------------ AI 结果
    async def save_ai_result(self, account_id_: str, symbol: str, payload: EaRecord) -> None:
        self._ai_results[(account_id_, normalize_symbol(symbol))] = {
            "account_id": account_id_,
            "symbol": symbol,
            **clone_record(payload),
        }

    async def get_ai_results(self, account_id_: str) -> list[EaRecord]:
        return [clone_record(v) for k, v in self._ai_results.items() if k[0] == account_id_]

    # ------------------------------------------------------------------ Token
    async def save_api_token(self, payload: EaRecord) -> None:
        token = normalize_api_token(payload)
        self._api_tokens[token["token"]] = token

    async def list_api_tokens(self) -> list[StoredApiToken]:
        return [
            clone_record(t)
            for t in sorted(self._api_tokens.values(), key=lambda t: (t.get("created_at", ""), t.get("token", "")))
        ]

    async def delete_api_token(self, token: str) -> bool:
        return self._api_tokens.pop(token, None) is not None

    # ------------------------------------------------------------------ 账户/品种
    async def list_account_ids(self) -> list[str]:
        out: list[str] = []
        for _kind, account, _symbol, _timeframe in self._snapshots:
            self._append_unique(out, account)
        for event in self._events:
            self._append_unique(out, string_field(event, "account_id"))
        for account, _symbol in self._ai_results:
            self._append_unique(out, account)
        return out

    async def list_symbols(self, account_id_: str) -> list[str]:
        entries = [
            (
                key[0],
                key[2],
                string_field(value, "symbol"),
                string_field(value, "updated_at"),
                self._snapshot_order.get(key, 0),
            )
            for key, value in self._snapshots.items()
            if key[0] in ("tick", "bars", "positions") and key[1] == account_id_ and len(key[2]) > 0
        ]
        return resolve_display_symbols(entries)

    async def list_ai_symbols(self, account_id_: str) -> list[str]:
        registration_symbols = string_array_field(self._snapshot("registration", account_id_, ""), "ai_symbols")
        if registration_symbols:
            return registration_symbols
        heartbeat_symbols = string_array_field(self._snapshot("heartbeat", account_id_, ""), "ai_symbols")
        if heartbeat_symbols:
            return heartbeat_symbols
        return sorted(await self.list_symbols(account_id_))

    @staticmethod
    def _append_unique(out: list[str], value: str) -> None:
        if len(value) > 0 and value not in out:
            out.append(value)

    # ------------------------------------------------------------------ 已平仓/日权益
    async def save_closed_trade(self, payload: EaRecord) -> None:
        for index, trade in enumerate(self._closed_trades):
            if trade.get("account_id") == payload.get("account_id") and trade.get("ticket") == payload.get("ticket"):
                self._closed_trades[index] = {**payload}
                return
        self._closed_trades.append({**payload})

    async def get_closed_trade_stats(self, account_id_: str) -> list[EaRecord]:
        return build_closed_trade_stats(t for t in self._closed_trades if t.get("account_id") == account_id_)

    async def get_daily_start_equity(self, account_id_: str, utc_date: str) -> float | None:
        return self._daily_equities.get(f"{account_id_}|{utc_date}")

    async def save_daily_start_equity(self, account_id_: str, utc_date: str, equity: float) -> None:
        key = f"{account_id_}|{utc_date}"
        if key not in self._daily_equities:
            self._daily_equities[key] = equity

    # ---- SOP 增量记忆账本 & 巡检日志
    async def get_watch_ledger(self, account_id_: str, symbol: str) -> EaRecord | None:
        key = (account_id_, normalize_symbol(symbol))
        existing = self._watch_ledgers.get(key)
        return clone_record(existing) if existing is not None else None

    async def save_watch_ledger(self, account_id_: str, symbol: str, ledger: EaRecord) -> EaRecord:
        norm_symbol = normalize_symbol(symbol)
        key = (account_id_, norm_symbol)
        existing = self._watch_ledgers.get(key)
        next_version = (
            int(existing.get("version") or 0) + 1
            if existing is not None
            else max(1, int(ledger.get("version") or 1))
        )
        now = current_timestamp()
        stored = clone_record(ledger)
        stored["account_id"] = account_id_
        stored["symbol"] = norm_symbol
        stored["version"] = next_version
        stored["updated_at"] = now
        stored["htf_reviewed_at"] = stored.get("htf_reviewed_at")
        stored["m15_reviewed_at"] = stored.get("m15_reviewed_at")
        self._watch_ledgers[key] = stored
        return clone_record(stored)

    async def append_patrol_log(self, payload: EaRecord) -> EaRecord:
        log_id = str(payload.get("id") or f"patrol-{self._next_patrol_log_id}")
        self._next_patrol_log_id += 1
        raw_details = payload.get("details")
        entry: EaRecord = {
            "id": log_id,
            "account_id": str(payload["account_id"]),
            "symbol": normalize_symbol(str(payload["symbol"])),
            "trigger_type": str(payload.get("trigger_type") or ""),
            "tier": str(payload.get("tier") or ""),
            "alert_level": str(payload.get("alert_level") or "silent"),
            "action_taken": str(payload.get("action_taken") or ""),
            "summary": str(payload.get("summary") or ""),
            "details": clone_record(raw_details) if isinstance(raw_details, dict) else {},
            "created_at": str(payload.get("created_at") or current_timestamp()),
        }
        self._patrol_logs.append(entry)
        return clone_record(entry)

    async def list_patrol_logs(
        self,
        account_id_: str,
        symbol: str | None = None,
        *,
        limit: int = 50,
        since: str | None = None,
    ) -> list[EaRecord]:
        safe_limit = max(1, min(int(limit), 500))
        norm_symbol = normalize_symbol(symbol) if symbol is not None and symbol.strip() else None
        since_val = since.strip() if since is not None and since.strip() else None
        filtered = [
            entry
            for entry in self._patrol_logs
            if entry["account_id"] == account_id_
            and (norm_symbol is None or entry["symbol"] == norm_symbol)
            and (since_val is None or entry["created_at"] >= since_val)
        ]
        ordered = sorted(
            filtered,
            key=lambda item: (str(item["created_at"]), str(item["id"])),
            reverse=True,
        )
        return [clone_record(item) for item in ordered[:safe_limit]]

    async def close(self) -> None:
        return None
