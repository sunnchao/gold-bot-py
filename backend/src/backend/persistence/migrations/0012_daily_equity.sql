-- Migration: 0012_daily_equity
-- Description: Per-account daily starting equity keyed by UTC date.
-- EA 的 MaxDailyLoss=5% 存在两个缺陷：EA 重启即清零、且用券商本地时间切日。
-- 服务端在此表按 UTC 日持久化每账户"当日起始权益"，供 scheduler 的
-- 日亏保护（Phase 5.1）计算当日已实现回撤并阻断新信号/LLM 分析。

CREATE TABLE IF NOT EXISTS daily_equity (
  account_id   TEXT NOT NULL,
  utc_date     TEXT NOT NULL,              -- YYYY-MM-DD（UTC 日）
  start_equity REAL NOT NULL,
  created_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (account_id, utc_date)
);
