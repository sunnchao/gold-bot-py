-- Migration: 0006_tokens
-- Description: API tokens and account mapping tables

CREATE TABLE IF NOT EXISTS tokens (
  token TEXT PRIMARY KEY,
  name TEXT NOT NULL DEFAULT '',
  is_admin INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS token_accounts (
  token TEXT NOT NULL,
  account_id TEXT NOT NULL,
  PRIMARY KEY (token, account_id),
  FOREIGN KEY (token) REFERENCES tokens(token) ON DELETE CASCADE
);
