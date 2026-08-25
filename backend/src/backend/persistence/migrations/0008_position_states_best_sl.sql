-- Migration: 0008_position_states_best_sl
-- Description: Persist bestSl (group-level best stop-loss) so group BE/trailing state survives restarts.
-- Aligns be_trigger_atr default with position_manager in-memory default (1.5).

ALTER TABLE position_states ADD COLUMN best_sl REAL NOT NULL DEFAULT 0;
