CREATE TABLE IF NOT EXISTS watch_ledgers (
  account_id TEXT NOT NULL,
  symbol TEXT NOT NULL,
  ledger_json TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 1,
  htf_reviewed_at TEXT,
  m15_reviewed_at TEXT,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (account_id, symbol)
);

CREATE TABLE IF NOT EXISTS patrol_logs (
  id TEXT PRIMARY KEY,
  account_id TEXT NOT NULL,
  symbol TEXT NOT NULL,
  trigger_type TEXT NOT NULL,
  tier TEXT NOT NULL,
  alert_level TEXT NOT NULL,
  action_taken TEXT NOT NULL,
  summary TEXT NOT NULL,
  details_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_patrol_logs_account_symbol_time
  ON patrol_logs(account_id, symbol, created_at DESC);
