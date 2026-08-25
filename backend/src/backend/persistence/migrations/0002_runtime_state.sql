-- Migration: 0002_runtime_state
-- Description: Runtime state and commands tables

CREATE TABLE IF NOT EXISTS runtime_state (
  account_id TEXT PRIMARY KEY,
  mode TEXT NOT NULL,
  cutover_enabled INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS runtime_commands (
  command_id TEXT PRIMARY KEY,
  account_id TEXT NOT NULL,
  status TEXT NOT NULL,
  source TEXT NOT NULL,
  symbol TEXT NOT NULL DEFAULT '',
  payload_json TEXT NOT NULL,
  result TEXT NOT NULL DEFAULT '',
  ticket INTEGER,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  delivered_at TEXT NOT NULL DEFAULT '',
  acked_at TEXT NOT NULL DEFAULT '',
  failed_at TEXT NOT NULL DEFAULT '',
  error_text TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_runtime_commands_account_status_created
  ON runtime_commands(account_id, status, created_at);

