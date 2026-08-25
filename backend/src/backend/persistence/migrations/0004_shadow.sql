-- Migration: 0004_shadow
-- Description: Shadow mode comparison tables

CREATE TABLE IF NOT EXISTS shadow_comparisons (
  account_id TEXT NOT NULL,
  symbol TEXT NOT NULL,
  protocol_ok INTEGER NOT NULL,
  signal_drift INTEGER NOT NULL,
  command_drift INTEGER NOT NULL,
  oracle_compared INTEGER NOT NULL,
  source TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_shadow_comparisons_created
  ON shadow_comparisons(created_at);

CREATE TABLE IF NOT EXISTS shadow_snapshots (
  account_id TEXT NOT NULL,
  symbol TEXT NOT NULL,
  source TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (account_id, symbol, source)
);

