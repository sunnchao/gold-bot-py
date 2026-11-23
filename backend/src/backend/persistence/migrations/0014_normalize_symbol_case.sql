-- Deduplicate legacy mixed-case/whitespace symbol & timeframe keys and canonicalize primary keys.
-- Safe for both SQLite and PostgreSQL (parsed by _postgres_migration_statements).

DELETE FROM ea_snapshots
WHERE EXISTS (
  SELECT 1 FROM ea_snapshots AS newer
  WHERE newer.kind = ea_snapshots.kind
    AND newer.account_id = ea_snapshots.account_id
    AND upper(trim(newer.symbol)) = upper(trim(ea_snapshots.symbol))
    AND upper(trim(newer.timeframe)) = upper(trim(ea_snapshots.timeframe))
    AND (newer.symbol <> ea_snapshots.symbol OR newer.timeframe <> ea_snapshots.timeframe)
    AND (
      newer.updated_at > ea_snapshots.updated_at
      OR (
        newer.updated_at = ea_snapshots.updated_at
        AND (
          (CASE WHEN newer.symbol = upper(trim(newer.symbol)) AND newer.timeframe = upper(trim(newer.timeframe)) THEN 1 ELSE 0 END)
          > (CASE WHEN ea_snapshots.symbol = upper(trim(ea_snapshots.symbol)) AND ea_snapshots.timeframe = upper(trim(ea_snapshots.timeframe)) THEN 1 ELSE 0 END)
          OR (
            (CASE WHEN newer.symbol = upper(trim(newer.symbol)) AND newer.timeframe = upper(trim(newer.timeframe)) THEN 1 ELSE 0 END)
            = (CASE WHEN ea_snapshots.symbol = upper(trim(ea_snapshots.symbol)) AND ea_snapshots.timeframe = upper(trim(ea_snapshots.timeframe)) THEN 1 ELSE 0 END)
            AND (
              newer.symbol < ea_snapshots.symbol
              OR (newer.symbol = ea_snapshots.symbol AND newer.timeframe < ea_snapshots.timeframe)
            )
          )
        )
      )
    )
);

UPDATE ea_snapshots
SET symbol = upper(trim(symbol)),
    timeframe = upper(trim(timeframe))
WHERE symbol <> upper(trim(symbol))
   OR timeframe <> upper(trim(timeframe));

DELETE FROM position_states
WHERE EXISTS (
  SELECT 1 FROM position_states AS newer
  WHERE newer.account_id = position_states.account_id
    AND newer.ticket = position_states.ticket
    AND upper(trim(newer.symbol)) = upper(trim(position_states.symbol))
    AND newer.symbol <> position_states.symbol
    AND (
      newer.last_modify_time > position_states.last_modify_time
      OR (
        newer.last_modify_time = position_states.last_modify_time
        AND (
          (CASE WHEN newer.symbol = upper(trim(newer.symbol)) THEN 1 ELSE 0 END)
          > (CASE WHEN position_states.symbol = upper(trim(position_states.symbol)) THEN 1 ELSE 0 END)
          OR (
            (CASE WHEN newer.symbol = upper(trim(newer.symbol)) THEN 1 ELSE 0 END)
            = (CASE WHEN position_states.symbol = upper(trim(position_states.symbol)) THEN 1 ELSE 0 END)
            AND newer.symbol < position_states.symbol
          )
        )
      )
    )
);

UPDATE position_states
SET symbol = upper(trim(symbol))
WHERE symbol <> upper(trim(symbol));

DELETE FROM bar_close_events
WHERE EXISTS (
  SELECT 1 FROM bar_close_events AS newer
  WHERE newer.account_id = bar_close_events.account_id
    AND newer.bar_time = bar_close_events.bar_time
    AND upper(trim(newer.symbol)) = upper(trim(bar_close_events.symbol))
    AND upper(trim(newer.timeframe)) = upper(trim(bar_close_events.timeframe))
    AND (newer.symbol <> bar_close_events.symbol OR newer.timeframe <> bar_close_events.timeframe)
    AND (
      newer.claimed_at > bar_close_events.claimed_at
      OR (
        newer.claimed_at = bar_close_events.claimed_at
        AND (
          (CASE WHEN newer.symbol = upper(trim(newer.symbol)) AND newer.timeframe = upper(trim(newer.timeframe)) THEN 1 ELSE 0 END)
          > (CASE WHEN bar_close_events.symbol = upper(trim(bar_close_events.symbol)) AND bar_close_events.timeframe = upper(trim(bar_close_events.timeframe)) THEN 1 ELSE 0 END)
          OR (
            (CASE WHEN newer.symbol = upper(trim(newer.symbol)) AND newer.timeframe = upper(trim(newer.timeframe)) THEN 1 ELSE 0 END)
            = (CASE WHEN bar_close_events.symbol = upper(trim(bar_close_events.symbol)) AND bar_close_events.timeframe = upper(trim(bar_close_events.timeframe)) THEN 1 ELSE 0 END)
            AND (
              newer.symbol < bar_close_events.symbol
              OR (newer.symbol = bar_close_events.symbol AND newer.timeframe < bar_close_events.timeframe)
            )
          )
        )
      )
    )
);

UPDATE bar_close_events
SET symbol = upper(trim(symbol)),
    timeframe = upper(trim(timeframe))
WHERE symbol <> upper(trim(symbol))
   OR timeframe <> upper(trim(timeframe));

DELETE FROM shadow_snapshots
WHERE EXISTS (
  SELECT 1 FROM shadow_snapshots AS newer
  WHERE newer.account_id = shadow_snapshots.account_id
    AND newer.source = shadow_snapshots.source
    AND upper(trim(newer.symbol)) = upper(trim(shadow_snapshots.symbol))
    AND newer.symbol <> shadow_snapshots.symbol
    AND (
      newer.updated_at > shadow_snapshots.updated_at
      OR (
        newer.updated_at = shadow_snapshots.updated_at
        AND (
          (CASE WHEN newer.symbol = upper(trim(newer.symbol)) THEN 1 ELSE 0 END)
          > (CASE WHEN shadow_snapshots.symbol = upper(trim(shadow_snapshots.symbol)) THEN 1 ELSE 0 END)
          OR (
            (CASE WHEN newer.symbol = upper(trim(newer.symbol)) THEN 1 ELSE 0 END)
            = (CASE WHEN shadow_snapshots.symbol = upper(trim(shadow_snapshots.symbol)) THEN 1 ELSE 0 END)
            AND newer.symbol < shadow_snapshots.symbol
          )
        )
      )
    )
);

UPDATE shadow_snapshots
SET symbol = upper(trim(symbol))
WHERE symbol <> upper(trim(symbol));
