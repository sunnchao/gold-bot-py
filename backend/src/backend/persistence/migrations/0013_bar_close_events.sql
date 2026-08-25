-- Persist exact-once claims for event-driven M15/M30 analysis.

CREATE TABLE IF NOT EXISTS bar_close_events (
  account_id TEXT NOT NULL,
  symbol TEXT NOT NULL,
  timeframe TEXT NOT NULL,
  bar_time TEXT NOT NULL,
  claimed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (account_id, symbol, timeframe, bar_time)
);
