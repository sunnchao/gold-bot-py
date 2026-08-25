-- Migration: 0001_init
-- Description: Initial schema for ea_snapshots and ea_events

CREATE TABLE IF NOT EXISTS ea_snapshots (
  kind TEXT NOT NULL,
  account_id TEXT NOT NULL,
  symbol TEXT NOT NULL DEFAULT '',
  timeframe TEXT NOT NULL DEFAULT '',
  payload_json TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (kind, account_id, symbol, timeframe)
);

CREATE TABLE IF NOT EXISTS ea_events (
  kind TEXT NOT NULL,
  account_id TEXT NOT NULL,
  symbol TEXT NOT NULL DEFAULT '',
  payload_json TEXT NOT NULL,
  delivered INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_ea_events_kind_account_delivered
  ON ea_events(kind, account_id, delivered, created_at);

