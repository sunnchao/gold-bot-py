-- Migration: 0011_closed_trades
-- Description: Track closed trade outcomes per strategy.
-- EA reports closed deals via POST /api/trade_history; this table stores
-- the realized P/L so win rate, expectancy, and R-multiple can be computed.

CREATE TABLE IF NOT EXISTS closed_trades (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  account_id   TEXT NOT NULL,
  ticket       INTEGER NOT NULL,
  magic        INTEGER NOT NULL DEFAULT 0,
  symbol       TEXT NOT NULL,
  strategy     TEXT NOT NULL DEFAULT '',
  side         TEXT NOT NULL DEFAULT '',   -- BUY | SELL
  open_price   REAL NOT NULL DEFAULT 0,
  close_price  REAL NOT NULL DEFAULT 0,
  lots         REAL NOT NULL DEFAULT 0,
  profit       REAL NOT NULL DEFAULT 0,   -- realized P/L (includes swap/commission)
  open_time    TEXT NOT NULL DEFAULT '',
  close_time   TEXT NOT NULL DEFAULT '',
  duration_min INTEGER NOT NULL DEFAULT 0,
  created_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (account_id, ticket)
);

CREATE INDEX IF NOT EXISTS idx_closed_trades_account_strategy
  ON closed_trades (account_id, strategy, close_time);
