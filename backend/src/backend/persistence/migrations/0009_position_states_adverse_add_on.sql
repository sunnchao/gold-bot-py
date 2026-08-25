-- Migration: 0009_position_states_adverse_add_on
-- Description: Persist adverse add-on state so level/spacing/count tracking survives restarts.
-- Fields are per-ticket (aligned with best_sl from 0008); group aggregation is inferred from
-- same symbol+side row sets. group_id is a soft label, not enforced unique in Phase 2.

ALTER TABLE position_states ADD COLUMN add_on_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE position_states ADD COLUMN last_add_on_time TEXT NOT NULL DEFAULT '';
ALTER TABLE position_states ADD COLUMN last_add_on_price REAL NOT NULL DEFAULT 0;
ALTER TABLE position_states ADD COLUMN group_id TEXT NOT NULL DEFAULT '';
ALTER TABLE position_states ADD COLUMN group_avg_entry REAL NOT NULL DEFAULT 0;
ALTER TABLE position_states ADD COLUMN group_best_sl REAL NOT NULL DEFAULT 0;
