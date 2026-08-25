-- Migration: 0003_position_states
-- Description: Position state tracking table

CREATE TABLE IF NOT EXISTS position_states (
  account_id TEXT NOT NULL,
  symbol TEXT NOT NULL,
  ticket INTEGER NOT NULL,
  tp1_hit INTEGER NOT NULL DEFAULT 0,
  tp2_hit INTEGER NOT NULL DEFAULT 0,
  max_profit_atr REAL NOT NULL DEFAULT 0,
  be_moved INTEGER NOT NULL DEFAULT 0,
  be_trigger_atr REAL NOT NULL DEFAULT 1.0,
  open_time TEXT NOT NULL DEFAULT '',
  last_modify_time TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (account_id, symbol, ticket)
);
