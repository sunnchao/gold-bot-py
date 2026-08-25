-- Migration: 0005_decision_events
-- Description: AI decision timeline table

CREATE TABLE IF NOT EXISTS decision_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  decision_id TEXT NOT NULL,
  account_id TEXT NOT NULL,
  symbol TEXT NOT NULL,
  stage TEXT NOT NULL,
  status TEXT NOT NULL,
  reason_codes_json TEXT NOT NULL DEFAULT '[]',
  summary_json TEXT NOT NULL DEFAULT '{}',
  related_command_id TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_decision_events_account_symbol_created
  ON decision_events(account_id, symbol, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_decision_events_account_status_created
  ON decision_events(account_id, status, created_at DESC);
