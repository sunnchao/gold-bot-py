CREATE TABLE IF NOT EXISTS market_bars (
  account_id TEXT NOT NULL,
  symbol TEXT NOT NULL,
  timeframe TEXT NOT NULL,
  bar_time TEXT NOT NULL,
  bar_time_ms BIGINT NOT NULL,
  open DOUBLE PRECISION,
  high DOUBLE PRECISION,
  low DOUBLE PRECISION,
  close DOUBLE PRECISION,
  volume BIGINT,
  payload_json TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (account_id, symbol, timeframe, bar_time)
);

CREATE INDEX IF NOT EXISTS idx_market_bars_lookup
  ON market_bars(account_id, symbol, timeframe, bar_time_ms DESC, bar_time DESC);
