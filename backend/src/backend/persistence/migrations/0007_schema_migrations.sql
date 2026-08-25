-- Migration: 0007_schema_migrations
-- Description: Migration tracking table (created first by migrate.ts)

CREATE TABLE IF NOT EXISTS schema_migrations (
  version INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  applied_at TEXT NOT NULL
);
