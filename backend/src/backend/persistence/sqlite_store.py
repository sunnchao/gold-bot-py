"""SQLAlchemy Core 异步 SQLite store(镜像 gold-bot packages/persistence/src/index.ts createSqliteEaStore)。

DDL 完全由 migrations/ 控制;本模块只做查询层。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from backend.persistence.helpers import (
    account_id,
    build_runtime_command,
    build_stored_command,
    candidate_signal_decision_event,
    clone_record,
    command_decision_event,
    command_result_decision_event,
    current_timestamp,
    filter_shadow_comparisons,
    from_json,
    is_active_ai_approve_pending,
    is_pending_signal_expired,
    is_runtime_command_expired,
    normalize_api_token,
    normalize_decision_event,
    normalize_pending_signal,
    normalize_position_state,
    numeric_field,
    position_state_from_row,
    string_array_field,
    string_field,
    summarize_shadow_comparisons,
    symbol_default,
    to_ea_command,
    to_json,
    update_pending_signal_payload,
)
from backend.persistence.records import EaRecord, StoredCommand, is_runtime_mode


class SqliteEaStore:
    def __init__(self, path: str) -> None:
        self._path = path
        self._engine = create_async_engine(f"sqlite+aiosqlite:///{path}")

    # ------------------------------------------------------------------ 基础
    def _connect(self) -> AsyncConnection:
        return self._engine.connect()

    async def _row(self, conn: AsyncConnection, sql: str, **params: Any) -> dict[str, Any] | None:
        result = await conn.execute(text(sql), params)
        mapping = result.mappings().first()
        return dict(mapping) if mapping is not None else None

    async def _rows(self, conn: AsyncConnection, sql: str, **params: Any) -> list[dict[str, Any]]:
        result = await conn.execute(text(sql), params)
        return [dict(row) for row in result.mappings().all()]

    # ------------------------------------------------------------------ 快照
    async def _save_snapshot(
        self, conn: AsyncConnection, kind: str, account: str, symbol: str, payload: EaRecord, timeframe: str = ""
    ) -> None:
        await conn.execute(
            text(
                """
                INSERT INTO ea_snapshots (kind, account_id, symbol, timeframe, payload_json, updated_at)
                VALUES (:kind, :account, :symbol, :timeframe, :payload, CURRENT_TIMESTAMP)
                ON CONFLICT(kind, account_id, symbol, timeframe)
                DO UPDATE SET payload_json = excluded.payload_json, updated_at = CURRENT_TIMESTAMP
                """
            ),
            {"kind": kind, "account": account, "symbol": symbol, "timeframe": timeframe, "payload": to_json(payload)},
        )

    async def _get_snapshot(
        self, conn: AsyncConnection, kind: str, account: str, symbol: str, timeframe: str = ""
    ) -> EaRecord | None:
        row = await self._row(
            conn,
            """
            SELECT payload_json FROM ea_snapshots
            WHERE kind = :kind AND account_id = :account AND symbol = :symbol AND timeframe = :timeframe
            """,
            kind=kind,
            account=account,
            symbol=symbol,
            timeframe=timeframe,
        )
        return from_json(row["payload_json"]) if row is not None and isinstance(row.get("payload_json"), str) else None

    async def save_registration(self, payload: EaRecord) -> None:
        async with self._connect() as conn:
            await self._save_snapshot(conn, "registration", account_id(payload), "", payload)
            await conn.commit()

    async def get_registration(self, account_id_: str) -> EaRecord | None:
        async with self._connect() as conn:
            return await self._get_snapshot(conn, "registration", account_id_, "")

    async def save_heartbeat(self, payload: EaRecord) -> None:
        async with self._connect() as conn:
            await self._save_snapshot(conn, "heartbeat", account_id(payload), "", payload)
            await conn.commit()

    async def get_heartbeat(self, account_id_: str) -> EaRecord | None:
        async with self._connect() as conn:
            return await self._get_snapshot(conn, "heartbeat", account_id_, "")

    async def save_tick(self, payload: EaRecord) -> None:
        async with self._connect() as conn:
            await self._save_snapshot(conn, "tick", account_id(payload), symbol_default(payload), payload)
            await conn.commit()

    async def get_latest_tick(self, account_id_: str, symbol: str) -> EaRecord | None:
        async with self._connect() as conn:
            return await self._get_snapshot(conn, "tick", account_id_, symbol)

    async def save_bars(self, payload: EaRecord) -> None:
        async with self._connect() as conn:
            await self._save_snapshot(
                conn, "bars", account_id(payload), symbol_default(payload), payload, string_field(payload, "timeframe")
            )
            await conn.commit()

    async def get_bars(self, account_id_: str, symbol: str, timeframe: str) -> list[EaRecord]:
        async with self._connect() as conn:
            record = await self._get_snapshot(conn, "bars", account_id_, symbol, timeframe)
            bars = record.get("bars") if record is not None else None
            return [clone_record(b) for b in bars] if isinstance(bars, list) else []

    async def claim_bar_close_event(
        self, account_id_: str, symbol: str, timeframe: str, bar_time: str
    ) -> bool:
        async with self._connect() as conn:
            result = await conn.execute(
                text(
                    """
                    INSERT INTO bar_close_events (account_id, symbol, timeframe, bar_time)
                    VALUES (:account, :symbol, :timeframe, :bar_time)
                    ON CONFLICT(account_id, symbol, timeframe, bar_time) DO NOTHING
                    """
                ),
                {
                    "account": account_id_,
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "bar_time": bar_time,
                },
            )
            await conn.commit()
            return result.rowcount == 1

    async def save_positions(self, payload: EaRecord) -> None:
        async with self._connect() as conn:
            await self._save_snapshot(conn, "positions", account_id(payload), symbol_default(payload), payload)
            await conn.commit()

    async def get_positions(self, account_id_: str, symbol: str | None = None) -> list[EaRecord]:
        async with self._connect() as conn:
            if symbol is not None and len(symbol) > 0:
                record = await self._get_snapshot(conn, "positions", account_id_, symbol)
                positions = record.get("positions") if record is not None else None
                return [clone_record(p) for p in positions] if isinstance(positions, list) else []
            rows = await self._rows(
                conn,
                "SELECT payload_json FROM ea_snapshots "
                "WHERE kind = 'positions' AND account_id = :account ORDER BY rowid ASC",
                account=account_id_,
            )
            out: list[EaRecord] = []
            for row in rows:
                payload = from_json(row["payload_json"])
                if isinstance(payload, dict) and isinstance(payload.get("positions"), list):
                    out.extend(clone_record(p) for p in payload["positions"])
            return out

    # ------------------------------------------------------------------ 持仓状态
    async def save_position_state(self, account_id_: str, symbol: str, state: EaRecord) -> None:
        normalized = normalize_position_state(state)
        async with self._connect() as conn:
            await conn.execute(
                text(
                    """
                    INSERT INTO position_states (
                      account_id, symbol, ticket, tp1_hit, tp2_hit, max_profit_atr, be_moved, be_trigger_atr,
                      best_sl, open_time, last_modify_time, add_on_count, last_add_on_time, last_add_on_price,
                      group_id, group_avg_entry, group_best_sl, trailing_closed
                    ) VALUES (
                      :account_id, :symbol, :ticket, :tp1_hit, :tp2_hit, :max_profit_atr, :be_moved, :be_trigger_atr,
                      :best_sl, :open_time, :last_modify_time, :add_on_count, :last_add_on_time, :last_add_on_price,
                      :group_id, :group_avg_entry, :group_best_sl, :trailing_closed
                    )
                    ON CONFLICT(account_id, symbol, ticket) DO UPDATE SET
                      tp1_hit = excluded.tp1_hit, tp2_hit = excluded.tp2_hit,
                      max_profit_atr = excluded.max_profit_atr, be_moved = excluded.be_moved,
                      be_trigger_atr = excluded.be_trigger_atr, best_sl = excluded.best_sl,
                      open_time = excluded.open_time, last_modify_time = excluded.last_modify_time,
                      add_on_count = excluded.add_on_count, last_add_on_time = excluded.last_add_on_time,
                      last_add_on_price = excluded.last_add_on_price, group_id = excluded.group_id,
                      group_avg_entry = excluded.group_avg_entry, group_best_sl = excluded.group_best_sl,
                      trailing_closed = excluded.trailing_closed
                    """
                ),
                {
                    "account_id": account_id_,
                    "symbol": symbol,
                    "ticket": int(normalized["ticket"]),
                    "tp1_hit": 1 if normalized["tp1_hit"] else 0,
                    "tp2_hit": 1 if normalized["tp2_hit"] else 0,
                    "max_profit_atr": normalized["max_profit_atr"],
                    "be_moved": 1 if normalized["be_moved"] else 0,
                    "be_trigger_atr": normalized["be_trigger_atr"],
                    "best_sl": normalized["best_sl"],
                    "open_time": normalized["open_time"],
                    "last_modify_time": normalized["last_modify_time"],
                    "add_on_count": normalized["add_on_count"],
                    "last_add_on_time": normalized["last_add_on_time"],
                    "last_add_on_price": normalized["last_add_on_price"],
                    "group_id": normalized["group_id"],
                    "group_avg_entry": normalized["group_avg_entry"],
                    "group_best_sl": normalized["group_best_sl"],
                    "trailing_closed": 1 if normalized["trailing_closed"] else 0,
                },
            )
            await conn.commit()

    async def load_position_states(self, account_id_: str, symbol: str) -> list[EaRecord]:
        async with self._connect() as conn:
            rows = await self._rows(
                conn,
                """
                SELECT ticket, tp1_hit, tp2_hit, max_profit_atr, be_moved, be_trigger_atr, best_sl,
                       open_time, last_modify_time, add_on_count, last_add_on_time, last_add_on_price,
                       group_id, group_avg_entry, group_best_sl, trailing_closed
                FROM position_states WHERE account_id = :account AND symbol = :symbol
                ORDER BY ticket ASC
                """,
                account=account_id_,
                symbol=symbol,
            )
            states = [position_state_from_row(row) for row in rows]
            states.sort(key=lambda s: int(s["ticket"]))
            return states

    async def delete_stale_position_states(self, account_id_: str, symbol: str, active_tickets: list[int]) -> None:
        async with self._connect() as conn:
            if not active_tickets:
                await conn.execute(
                    text("DELETE FROM position_states WHERE account_id = :account AND symbol = :symbol"),
                    {"account": account_id_, "symbol": symbol},
                )
            else:
                placeholders = ", ".join(f":t{i}" for i in range(len(active_tickets)))
                await conn.execute(
                    text(
                        f"DELETE FROM position_states WHERE account_id = :account AND symbol = :symbol "
                        f"AND ticket NOT IN ({placeholders})"
                    ),
                    {"account": account_id_, "symbol": symbol, **{f"t{i}": t for i, t in enumerate(active_tickets)}},
                )
            await conn.commit()

    # ------------------------------------------------------------------ 订单回报
    async def _insert_event(
        self, conn: AsyncConnection, kind: str, account: str, symbol: str, payload: EaRecord, delivered: int
    ) -> None:
        await conn.execute(
            text(
                "INSERT INTO ea_events (kind, account_id, symbol, payload_json, delivered) "
                "VALUES (:kind, :account, :symbol, :payload, :delivered)"
            ),
            {"kind": kind, "account": account, "symbol": symbol, "payload": to_json(payload), "delivered": delivered},
        )

    async def save_order_result(self, payload: EaRecord) -> None:
        async with self._connect() as conn:
            await self._insert_event(conn, "order_result", account_id(payload), "", payload, 1)
            await conn.commit()

    async def get_order_results(self, account_id_: str) -> list[EaRecord]:
        async with self._connect() as conn:
            rows = await self._rows(
                conn,
                "SELECT payload_json FROM ea_events "
                "WHERE kind = 'order_result' AND account_id = :account ORDER BY rowid ASC",
                account=account_id_,
            )
            return [from_json(row["payload_json"]) for row in rows if isinstance(row.get("payload_json"), str)]

    # ------------------------------------------------------------------ 命令生命周期
    async def enqueue_command(self, account_id_: str, command: EaRecord) -> None:
        stored, visible = build_stored_command(account_id_, command, "queued")
        async with self._connect() as conn:
            await self._insert_runtime_command_row(conn, stored, visible)
            await self._run_command_decision(conn, stored, "command_enqueued", "pending", stored["created_at"], visible)
            await conn.commit()

    async def save_command_candidate(self, account_id_: str, candidate: EaRecord) -> StoredCommand:
        stored, visible = build_stored_command(account_id_, candidate, "draft")
        async with self._connect() as conn:
            await self._insert_runtime_command_row(conn, stored, visible)
            await conn.commit()
        return clone_record(stored)

    async def _insert_runtime_command_row(self, conn: AsyncConnection, stored: StoredCommand, visible: bool) -> None:
        await conn.execute(
            text(
                """
                INSERT INTO runtime_commands (
                  command_id, account_id, status, source, symbol, payload_json, result, ticket,
                  created_at, delivered_at, updated_at
                ) VALUES (:command_id, :account_id, :status, :source, :symbol, :payload, '', :ticket,
                  :created_at, '', CURRENT_TIMESTAMP)
                """
            ),
            {
                "command_id": stored["command_id"],
                "account_id": stored["account_id"],
                "status": stored["status"],
                "source": stored["source"],
                "symbol": symbol_default(stored),
                "payload": to_json(to_ea_command(stored, visible)),
                "ticket": stored.get("ticket")
                if isinstance(stored.get("ticket"), int) and not isinstance(stored.get("ticket"), bool)
                else None,
                "created_at": stored.get("created_at", current_timestamp()),
            },
        )

    async def _run_command_decision(
        self, conn: AsyncConnection, command: StoredCommand, stage: str, status: str, created_at: str, visible: bool
    ) -> None:
        event = command_decision_event(command, stage, status, created_at, visible)
        if event is not None:
            await self._insert_decision_event(conn, event)

    async def _select_runtime_command(
        self, conn: AsyncConnection, command_id: str
    ) -> tuple[StoredCommand, bool] | None:
        row = await self._row(
            conn,
            """
            SELECT command_id, account_id, status, source, payload_json, result, ticket,
                   created_at, delivered_at, acked_at, failed_at, error_text
            FROM runtime_commands WHERE command_id = :command_id
            """,
            command_id=command_id,
        )
        if row is None:
            return None
        return build_runtime_command(command_id, row)

    async def promote_command(self, command_id: str) -> None:
        async with self._connect() as conn:
            found = await self._select_runtime_command(conn, command_id)
            await conn.execute(
                text(
                    "UPDATE runtime_commands SET status = 'queued', "
                    "updated_at = CURRENT_TIMESTAMP WHERE command_id = :command_id"
                ),
                {"command_id": command_id},
            )
            if found is not None and found[0].get("status") != "queued":
                stored, visible = found
                await self._run_command_decision(
                    conn, stored, "command_enqueued", "pending", stored["created_at"], visible
                )
            await conn.commit()

    async def demote_command_to_shadow_only(self, command_id: str) -> None:
        async with self._connect() as conn:
            await conn.execute(
                text(
                    "UPDATE runtime_commands SET status = 'shadow_only', "
                    "updated_at = CURRENT_TIMESTAMP WHERE command_id = :command_id"
                ),
                {"command_id": command_id},
            )
            await conn.commit()

    async def get_command(self, command_id: str) -> StoredCommand | None:
        async with self._connect() as conn:
            found = await self._select_runtime_command(conn, command_id)
            return clone_record(found[0]) if found is not None else None

    async def list_commands(self, account_id_: str) -> list[StoredCommand]:
        async with self._connect() as conn:
            rows = await self._rows(
                conn,
                """
                SELECT command_id, account_id, status, source, payload_json, result, ticket,
                       created_at, delivered_at, acked_at, failed_at, error_text
                FROM runtime_commands WHERE account_id = :account
                ORDER BY created_at ASC, command_id ASC
                """,
                account=account_id_,
            )
            return [build_runtime_command(row["command_id"], row)[0] for row in rows]

    async def has_active_ai_approve_pending(self, account_id_: str, symbol: str, side: str, now_iso: str) -> bool:
        async with self._connect() as conn:
            rows = await self._rows(
                conn,
                """
                SELECT command_id, account_id, status, source, payload_json, result, ticket,
                       created_at, delivered_at, acked_at, failed_at, error_text
                FROM runtime_commands WHERE account_id = :account AND status = 'queued'
                ORDER BY created_at ASC, command_id ASC
                """,
                account=account_id_,
            )
            commands = [build_runtime_command(row["command_id"], row)[0] for row in rows]
            return any(is_active_ai_approve_pending(c, account_id_, symbol, side, now_iso) for c in commands)

    async def get_runtime_mode(self, account_id_: str) -> str:
        async with self._connect() as conn:
            row = await self._row(
                conn, "SELECT mode FROM runtime_state WHERE account_id = :account", account=account_id_
            )
            mode = row.get("mode") if row is not None else None
            return mode if isinstance(mode, str) and is_runtime_mode(mode) else "oracle"

    async def set_runtime_mode(self, account_id_: str, mode: str) -> None:
        async with self._connect() as conn:
            await conn.execute(
                text(
                    """
                    INSERT INTO runtime_state (account_id, mode, cutover_enabled, updated_at)
                    VALUES (:account, :mode, :cutover, CURRENT_TIMESTAMP)
                    ON CONFLICT(account_id) DO UPDATE SET
                      mode = excluded.mode, cutover_enabled = excluded.cutover_enabled, updated_at = CURRENT_TIMESTAMP
                    """
                ),
                {"account": account_id_, "mode": mode, "cutover": 1 if mode == "cutover" else 0},
            )
            await conn.commit()

    async def reconcile_command_result(
        self,
        account_id_: str,
        command_id: str,
        result: str,
        ticket: int | None = None,
        error_text: str = "",
        created_at: str | None = None,
    ) -> bool:
        created_at = created_at or current_timestamp()
        normalized_ticket = ticket if ticket is not None else 0
        is_ack = result == "OK"
        status = "acked" if is_ack else "failed"
        timestamp_field = "acked_at" if is_ack else "failed_at"
        async with self._connect() as conn:
            write = await conn.execute(
                text(
                    f"""
                    UPDATE runtime_commands
                    SET status = :status, result = :result, ticket = :ticket, error_text = :error_text,
                        {timestamp_field} = :created_at, updated_at = CURRENT_TIMESTAMP
                    WHERE command_id = :command_id AND account_id = :account AND status = 'delivered'
                    """
                ),
                {
                    "status": status,
                    "result": result,
                    "ticket": normalized_ticket,
                    "error_text": error_text,
                    "created_at": created_at,
                    "command_id": command_id,
                    "account": account_id_,
                },
            )
            if write.rowcount == 0:
                return False
            await self._insert_event(
                conn,
                "order_result",
                account_id_,
                "",
                {
                    "account_id": account_id_,
                    "command_id": command_id,
                    "result": result,
                    "ticket": normalized_ticket,
                    "error_text": error_text,
                    "created_at": created_at,
                },
                1,
            )
            found = await self._select_runtime_command(conn, command_id)
            if found is not None:
                stored, visible = found
                event = command_result_decision_event(
                    stored, result, normalized_ticket, error_text, created_at, visible
                )
                if event is not None:
                    await self._insert_decision_event(conn, event)
            await conn.commit()
            return True

    async def poll_commands(self, account_id_: str) -> list[EaRecord]:
        async with self._connect() as conn:
            rows = await self._rows(
                conn,
                """
                SELECT command_id, account_id, status, source, payload_json, result, ticket,
                       created_at, delivered_at, acked_at, failed_at, error_text
                FROM runtime_commands WHERE account_id = :account AND status = 'queued'
                ORDER BY created_at ASC, command_id ASC
                """,
                account=account_id_,
            )
            delivered: list[EaRecord] = []
            delivered_at = current_timestamp()
            for row in rows:
                command, visible = build_runtime_command(row["command_id"], row)
                if is_runtime_command_expired(command, delivered_at):
                    await conn.execute(
                        text(
                            """
                            UPDATE runtime_commands
                            SET status = 'failed', result = 'expired', ticket = 0,
                                error_text = 'command expired before delivery', failed_at = :delivered_at,
                                updated_at = CURRENT_TIMESTAMP
                            WHERE command_id = :command_id AND account_id = :account AND status = 'queued'
                            """
                        ),
                        {"delivered_at": delivered_at, "command_id": row["command_id"], "account": account_id_},
                    )
                    expired: StoredCommand = {
                        **command,
                        "status": "failed",
                        "result": "expired",
                        "ticket": 0,
                        "error_text": "command expired before delivery",
                        "failed_at": delivered_at,
                    }
                    event = command_result_decision_event(
                        expired, "expired", 0, "command expired before delivery", delivered_at, visible
                    )
                    if event is not None:
                        await self._insert_decision_event(conn, event)
                    continue
                await conn.execute(
                    text(
                        """
                        UPDATE runtime_commands
                        SET status = 'delivered', delivered_at = :delivered_at, updated_at = CURRENT_TIMESTAMP
                        WHERE command_id = :command_id
                        """
                    ),
                    {"delivered_at": delivered_at, "command_id": row["command_id"]},
                )
                delivered_command: StoredCommand = {**command, "status": "delivered", "delivered_at": delivered_at}
                await self._run_command_decision(
                    conn, delivered_command, "command_delivered", "delivered", delivered_at, visible
                )
                delivered.append(to_ea_command(delivered_command, visible))
            await conn.commit()
            return delivered

    # ------------------------------------------------------------------ 影子校验
    async def record_shadow_comparison(self, payload: EaRecord) -> None:
        async with self._connect() as conn:
            await conn.execute(
                text(
                    """
                    INSERT INTO shadow_comparisons (
                      account_id, symbol, protocol_ok, signal_drift, command_drift, oracle_compared,
                      source, created_at
                    ) VALUES (
                      :account_id, :symbol, :protocol_ok, :signal_drift, :command_drift, :oracle_compared,
                      :source, :created_at
                    )
                    """
                ),
                {
                    "account_id": payload.get("account_id", ""),
                    "symbol": payload.get("symbol", ""),
                    "protocol_ok": 1 if payload.get("protocol_ok", True) else 0,
                    "signal_drift": 1 if payload.get("signal_drift", False) else 0,
                    "command_drift": 1 if payload.get("command_drift", False) else 0,
                    "oracle_compared": 1 if payload.get("oracle_compared", False) else 0,
                    "source": payload.get("source", "ea_analysis"),
                    "created_at": payload.get("created_at", current_timestamp()),
                },
            )
            await conn.commit()

    async def list_shadow_comparisons(self, filter_: EaRecord | None = None) -> list[EaRecord]:
        async with self._connect() as conn:
            rows = await self._rows(
                conn,
                "SELECT * FROM shadow_comparisons ORDER BY created_at ASC",
            )
            comparisons: list[EaRecord] = []
            for row in rows:
                source = row.get("source")
                comparisons.append(
                    {
                        "account_id": row["account_id"],
                        "symbol": row["symbol"],
                        "protocol_ok": int(row["protocol_ok"]) == 1,
                        "signal_drift": int(row["signal_drift"]) == 1,
                        "command_drift": int(row["command_drift"]) == 1,
                        "oracle_compared": int(row["oracle_compared"]) == 1,
                        "source": source if source in ("position_review", "ai_result") else "ea_analysis",
                        "created_at": row["created_at"],
                    }
                )
            return filter_shadow_comparisons(comparisons, filter_)

    async def summarize_shadow_comparisons(self, filter_: EaRecord | None = None) -> EaRecord:
        return summarize_shadow_comparisons(await self.list_shadow_comparisons(filter_))

    async def save_shadow_snapshot(self, payload: EaRecord) -> None:
        async with self._connect() as conn:
            await conn.execute(
                text(
                    """
                    INSERT INTO shadow_snapshots (account_id, symbol, source, payload_json, updated_at)
                    VALUES (:account_id, :symbol, :source, :payload, CURRENT_TIMESTAMP)
                    ON CONFLICT(account_id, symbol, source)
                    DO UPDATE SET payload_json = excluded.payload_json, updated_at = CURRENT_TIMESTAMP
                    """
                ),
                {
                    "account_id": payload.get("account_id", ""),
                    "symbol": payload.get("symbol", ""),
                    "source": payload.get("source", ""),
                    "payload": to_json(payload),
                },
            )
            await conn.commit()

    async def get_latest_shadow_snapshot(self, account_id_: str, symbol: str, source: str) -> EaRecord | None:
        async with self._connect() as conn:
            row = await self._row(
                conn,
                "SELECT payload_json FROM shadow_snapshots "
                "WHERE account_id = :account AND symbol = :symbol AND source = :source",
                account=account_id_,
                symbol=symbol,
                source=source,
            )
            value = row.get("payload_json") if row is not None else None
            result = from_json(value) if isinstance(value, str) else None
            return result if isinstance(result, dict) else None

    # ------------------------------------------------------------------ 决策时间线
    async def _insert_decision_event(self, conn: AsyncConnection, event: EaRecord) -> None:
        normalized = normalize_decision_event(event)
        await conn.execute(
            text(
                """
                INSERT INTO decision_events (
                  decision_id, account_id, symbol, stage, status, reason_codes_json, summary_json,
                  related_command_id, created_at
                ) VALUES (
                  :decision_id, :account_id, :symbol, :stage, :status, :reason_codes, :summary,
                  :related_command_id, :created_at
                )
                """
            ),
            {
                "decision_id": normalized["decision_id"],
                "account_id": normalized["account_id"],
                "symbol": normalized["symbol"],
                "stage": normalized["stage"],
                "status": normalized["status"],
                "reason_codes": to_json(normalized["reason_codes"]),
                "summary": to_json(normalized["summary"]),
                "related_command_id": normalized["related_command_id"],
                "created_at": normalized["created_at"],
            },
        )

    async def record_decision_event(self, payload: EaRecord) -> None:
        async with self._connect() as conn:
            await self._insert_decision_event(conn, payload)
            await conn.commit()

    async def list_decision_events(self, filter_: EaRecord) -> list[EaRecord]:
        clauses = ["account_id = :account"]
        params: dict[str, Any] = {"account": string_field(filter_, "account_id")}
        if len(string_field(filter_, "symbol")) > 0:
            clauses.append("symbol = :symbol")
            params["symbol"] = string_field(filter_, "symbol")
        if len(string_field(filter_, "status")) > 0:
            clauses.append("status = :status")
            params["status"] = string_field(filter_, "status")
        limit = filter_.get("limit")
        params["limit"] = 50 if not isinstance(limit, int) or limit <= 0 or limit > 200 else limit
        async with self._connect() as conn:
            rows = await self._rows(
                conn,
                f"""
                SELECT id, decision_id, account_id, symbol, stage, status,
                       reason_codes_json, summary_json, related_command_id, created_at
                FROM decision_events
                WHERE {" AND ".join(clauses)}
                ORDER BY created_at DESC, id DESC
                LIMIT :limit
                """,
                **params,
            )
        events: list[EaRecord] = []
        for row in rows:
            reason_codes = from_json(row["reason_codes_json"])
            summary = from_json(row["summary_json"])
            events.append(
                {
                    "id": int(row["id"]),
                    "decision_id": row["decision_id"],
                    "account_id": row["account_id"],
                    "symbol": row["symbol"],
                    "stage": row["stage"],
                    "status": row["status"],
                    "reason_codes": [c for c in reason_codes if isinstance(c, str)]
                    if isinstance(reason_codes, list)
                    else [],
                    "summary": summary if isinstance(summary, dict) else {},
                    "related_command_id": row["related_command_id"],
                    "created_at": row["created_at"],
                }
            )
        return events

    # ------------------------------------------------------------------ 候选信号
    async def save_pending_signal(self, payload: EaRecord) -> None:
        signal = normalize_pending_signal(payload)
        explicit_id = int(numeric_field(signal, "id"))
        async with self._connect() as conn:
            if explicit_id > 0:
                # 显式 id 仅替换已存在信号;不插入、不记录决策事件(镜像 TS)
                await self._replace_pending_signal_in_sqlite(conn, signal)
                await conn.commit()
                return
            signal["id"] = await self._next_pending_signal_id_in_sqlite(conn)
            await self._insert_event(conn, "pending_signal", account_id(signal), symbol_default(signal), signal, 1)
            event = candidate_signal_decision_event(signal)
            if event is not None:
                await self._insert_decision_event(conn, event)
            await conn.commit()

    async def _next_pending_signal_id_in_sqlite(self, conn: AsyncConnection) -> int:
        rows = await self._rows(
            conn,
            "SELECT payload_json FROM ea_events WHERE kind = 'pending_signal' ORDER BY rowid ASC",
        )
        max_id = 0
        for row in rows:
            payload = from_json(row["payload_json"])
            if isinstance(payload, dict):
                max_id = max(max_id, int(numeric_field(payload, "id")))
        return max_id + 1

    async def _replace_pending_signal_in_sqlite(self, conn: AsyncConnection, signal: EaRecord) -> bool:
        signal_id = int(numeric_field(signal, "id"))
        rows = await self._rows(
            conn,
            "SELECT rowid AS row_id, payload_json FROM ea_events WHERE kind = 'pending_signal' ORDER BY rowid ASC",
        )
        for row in rows:
            payload = from_json(row["payload_json"])
            if isinstance(payload, dict) and int(numeric_field(payload, "id")) == signal_id:
                await conn.execute(
                    text(
                        "UPDATE ea_events SET account_id = :account, symbol = :symbol, "
                        "payload_json = :payload WHERE rowid = :row_id"
                    ),
                    {
                        "account": account_id(signal),
                        "symbol": symbol_default(signal),
                        "payload": to_json(signal),
                        "row_id": row["row_id"],
                    },
                )
                return True
        return False

    async def get_pending_signals(self, account_id_: str, symbol: str) -> list[EaRecord]:
        async with self._connect() as conn:
            rows = await self._rows(
                conn,
                """
                SELECT payload_json FROM ea_events
                WHERE kind = 'pending_signal' AND account_id = :account AND symbol = :symbol
                ORDER BY rowid ASC
                """,
                account=account_id_,
                symbol=symbol,
            )
            payloads: list[EaRecord] = []
            for row in rows:
                payload = from_json(row["payload_json"])
                if isinstance(payload, dict):
                    payloads.append(payload)
            pending = [p for p in payloads if string_field(p, "status") == "pending"]
            pending.sort(key=lambda p: string_field(p, "created_at"), reverse=True)
            return [clone_record(p) for p in pending]

    async def get_pending_signal_by_id(self, account_id_: str, symbol: str, id_: int) -> EaRecord | None:
        for signal in await self.get_pending_signals(account_id_, symbol):
            if int(numeric_field(signal, "id")) == id_:
                return clone_record(signal)
        return None

    async def update_pending_signal_arbitration(self, id_: int, result: str, reason: str) -> bool:
        async with self._connect() as conn:
            rows = await self._rows(
                conn,
                "SELECT rowid AS row_id, payload_json FROM ea_events WHERE kind = 'pending_signal' ORDER BY rowid ASC",
            )
            for row in rows:
                payload = from_json(row["payload_json"])
                if isinstance(payload, dict) and int(numeric_field(payload, "id")) == id_:
                    update_pending_signal_payload(payload, result, reason)
                    await conn.execute(
                        text("UPDATE ea_events SET payload_json = :payload WHERE rowid = :row_id"),
                        {"payload": to_json(payload), "row_id": row["row_id"]},
                    )
                    await conn.commit()
                    return True
        return False

    async def expire_pending_signals(self, now_iso: str) -> int:
        expired = 0
        async with self._connect() as conn:
            rows = await self._rows(
                conn,
                "SELECT rowid AS row_id, payload_json FROM ea_events WHERE kind = 'pending_signal' ORDER BY rowid ASC",
            )
            for row in rows:
                payload = from_json(row["payload_json"])
                if (
                    isinstance(payload, dict)
                    and string_field(payload, "status") == "pending"
                    and is_pending_signal_expired(payload, now_iso)
                ):
                    payload["status"] = "timeout"
                    payload["arbitration_result"] = "timeout"
                    payload["arbitration_reason"] = "expired"
                    await conn.execute(
                        text("UPDATE ea_events SET payload_json = :payload WHERE rowid = :row_id"),
                        {"payload": to_json(payload), "row_id": row["row_id"]},
                    )
                    expired += 1
            await conn.commit()
        return expired

    # ------------------------------------------------------------------ AI 结果
    async def save_ai_result(self, account_id_: str, symbol: str, payload: EaRecord) -> None:
        async with self._connect() as conn:
            await self._save_snapshot(
                conn, "ai_result", account_id_, symbol, {"account_id": account_id_, "symbol": symbol, **payload}
            )
            await conn.commit()

    async def get_ai_results(self, account_id_: str) -> list[EaRecord]:
        async with self._connect() as conn:
            rows = await self._rows(
                conn,
                "SELECT payload_json FROM ea_snapshots "
                "WHERE kind = 'ai_result' AND account_id = :account ORDER BY rowid ASC",
                account=account_id_,
            )
            results: list[EaRecord] = []
            for row in rows:
                payload = from_json(row["payload_json"])
                if isinstance(payload, dict):
                    results.append(clone_record(payload))
            return results

    # ------------------------------------------------------------------ Token
    async def save_api_token(self, payload: EaRecord) -> None:
        token = normalize_api_token(payload)
        async with self._connect() as conn:
            await conn.execute(
                text(
                    """
                    INSERT INTO tokens (token, name, is_admin, created_at)
                    VALUES (:token, :name, :is_admin, :created_at)
                    ON CONFLICT(token) DO UPDATE SET name = excluded.name, is_admin = excluded.is_admin
                    """
                ),
                {
                    "token": token["token"],
                    "name": token["name"],
                    "is_admin": 1 if token["is_admin"] else 0,
                    "created_at": token["created_at"],
                },
            )
            await conn.execute(text("DELETE FROM token_accounts WHERE token = :token"), {"token": token["token"]})
            for account in token["accounts"]:
                await conn.execute(
                    text("INSERT OR IGNORE INTO token_accounts (token, account_id) VALUES (:token, :account)"),
                    {"token": token["token"], "account": account},
                )
            await conn.commit()

    async def list_api_tokens(self) -> list[EaRecord]:
        async with self._connect() as conn:
            rows = await self._rows(
                conn, "SELECT token, name, is_admin, created_at FROM tokens ORDER BY created_at ASC, token ASC"
            )
            tokens: list[EaRecord] = []
            for row in rows:
                accounts = await self._rows(
                    conn,
                    "SELECT account_id FROM token_accounts WHERE token = :token ORDER BY account_id ASC",
                    token=row["token"],
                )
                tokens.append(
                    {
                        "token": row["token"],
                        "name": row["name"],
                        "accounts": [a["account_id"] for a in accounts],
                        "is_admin": int(row["is_admin"]) == 1,
                        "created_at": row["created_at"],
                    }
                )
            return tokens

    async def delete_api_token(self, token: str) -> bool:
        async with self._connect() as conn:
            await conn.execute(text("DELETE FROM token_accounts WHERE token = :token"), {"token": token})
            result = await conn.execute(text("DELETE FROM tokens WHERE token = :token"), {"token": token})
            await conn.commit()
            return result.rowcount > 0

    # ------------------------------------------------------------------ 账户/品种
    async def list_account_ids(self) -> list[str]:
        async with self._connect() as conn:
            rows = await self._rows(
                conn,
                "SELECT account_id FROM ea_snapshots GROUP BY account_id ORDER BY account_id ASC",
            )
            events = await self._rows(
                conn,
                "SELECT account_id FROM ea_events GROUP BY account_id ORDER BY account_id ASC",
            )
        out: list[str] = []
        for row in [*rows, *events]:
            value = row["account_id"]
            if isinstance(value, str) and len(value) > 0 and value not in out:
                out.append(value)
        return out

    async def list_symbols(self, account_id_: str) -> list[str]:
        async with self._connect() as conn:
            rows = await self._rows(
                conn,
                """
                SELECT DISTINCT symbol FROM ea_snapshots
                WHERE account_id = :account AND symbol <> '' AND kind IN ('tick', 'bars', 'positions')
                ORDER BY rowid ASC
                """,
                account=account_id_,
            )
        out: list[str] = []
        for row in rows:
            value = row["symbol"]
            if isinstance(value, str) and len(value) > 0 and value not in out:
                out.append(value)
        return out

    async def list_ai_symbols(self, account_id_: str) -> list[str]:
        registration_symbols = string_array_field(await self.get_registration(account_id_), "ai_symbols")
        if registration_symbols:
            return registration_symbols
        heartbeat_symbols = string_array_field(await self.get_heartbeat(account_id_), "ai_symbols")
        return heartbeat_symbols if heartbeat_symbols else sorted(await self.list_symbols(account_id_))

    # ------------------------------------------------------------------ 已平仓/日权益
    async def save_closed_trade(self, payload: EaRecord) -> None:
        async with self._connect() as conn:
            await conn.execute(
                text(
                    """
                    INSERT INTO closed_trades
                      (account_id, ticket, magic, symbol, strategy, side, open_price, close_price,
                       lots, profit, open_time, close_time, duration_min)
                    VALUES (:account_id, :ticket, :magic, :symbol, :strategy, :side, :open_price, :close_price,
                            :lots, :profit, :open_time, :close_time, :duration_min)
                    ON CONFLICT(account_id, ticket) DO UPDATE SET
                      profit = excluded.profit, close_price = excluded.close_price,
                      close_time = excluded.close_time, duration_min = excluded.duration_min
                    """
                ),
                {
                    "account_id": payload.get("account_id", ""),
                    "ticket": payload.get("ticket", 0),
                    "magic": payload.get("magic", 0),
                    "symbol": payload.get("symbol", ""),
                    "strategy": payload.get("strategy", ""),
                    "side": payload.get("side", ""),
                    "open_price": payload.get("open_price", 0),
                    "close_price": payload.get("close_price", 0),
                    "lots": payload.get("lots", 0),
                    "profit": payload.get("profit", 0),
                    "open_time": payload.get("open_time", ""),
                    "close_time": payload.get("close_time", ""),
                    "duration_min": payload.get("duration_min", 0),
                },
            )
            await conn.commit()

    async def get_closed_trade_stats(self, account_id_: str) -> list[EaRecord]:
        async with self._connect() as conn:
            rows = await self._rows(
                conn,
                """
                SELECT strategy, COUNT(*) AS total,
                  SUM(CASE WHEN profit > 0 THEN 1 ELSE 0 END) AS wins,
                  SUM(CASE WHEN profit <= 0 THEN 1 ELSE 0 END) AS losses,
                  SUM(profit) AS total_profit,
                  AVG(profit) AS avg_profit,
                  AVG(CASE WHEN profit > 0 THEN profit END) AS avg_win,
                  AVG(CASE WHEN profit <= 0 THEN profit END) AS avg_loss,
                  AVG(duration_min) AS avg_duration_min
                FROM closed_trades WHERE account_id = :account
                GROUP BY strategy ORDER BY total DESC
                """,
                account=account_id_,
            )
        stats: list[EaRecord] = []
        for row in rows:
            wins = float(row["wins"] or 0)
            total = float(row["total"] or 0)
            win_rate = wins / total if total > 0 else 0.0
            avg_win = float(row["avg_win"] or 0)
            avg_loss = float(row["avg_loss"] or 0)
            stats.append(
                {
                    "strategy": str(row["strategy"]),
                    "total": int(total),
                    "wins": int(wins),
                    "losses": int(float(row["losses"] or 0)),
                    "win_rate": win_rate,
                    "total_profit": float(row["total_profit"] or 0),
                    "avg_profit": float(row["avg_profit"] or 0),
                    "avg_win": avg_win,
                    "avg_loss": avg_loss,
                    "expectancy": win_rate * avg_win + (1 - win_rate) * avg_loss,
                    "avg_duration_min": float(row["avg_duration_min"] or 0),
                }
            )
        # 与 TS 一致:empty → []
        return stats

    async def get_daily_start_equity(self, account_id_: str, utc_date: str) -> float | None:
        async with self._connect() as conn:
            row = await self._row(
                conn,
                "SELECT start_equity FROM daily_equity WHERE account_id = :account AND utc_date = :utc_date",
                account=account_id_,
                utc_date=utc_date,
            )
        value = row.get("start_equity") if row is not None else None
        return float(value) if isinstance(value, (int, float)) else None

    async def save_daily_start_equity(self, account_id_: str, utc_date: str, equity: float) -> None:
        async with self._connect() as conn:
            await conn.execute(
                text(
                    """
                    INSERT INTO daily_equity (account_id, utc_date, start_equity)
                    VALUES (:account, :utc_date, :equity)
                    ON CONFLICT(account_id, utc_date) DO NOTHING
                    """
                ),
                {"account": account_id_, "utc_date": utc_date, "equity": equity},
            )
            await conn.commit()

    async def close(self) -> None:
        await self._engine.dispose()
