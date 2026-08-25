# migrations

SQLAlchemy Core 方案下的 **DDL 唯一来源**。

约定(与 PREPARATION.md 第 2 节一致):migration 文件逐字保留源仓库 `gold-bot/migrations/` 的 0001~0007(含 `schema_migrations` 版本表),SQLAlchemy Core 只做查询层,不负责建表。

## 计划文件

- `0001_init.sql` — 初始 schema(accounts / account_runtime / account_state 等)
- `0002_legacy_auth_runtime.sql`、`0003_command_queue.sql`、`0004_account_state.sql`、`0005_position_states.sql`、`0006_multi_symbol.sql`、`0007_decision_timeline.sql`
- `schema_migrations` 版本表由迁移执行器维护(等价源 `migrate.ts`)

## 状态

在 M1 从源仓库逐字复制并接入迁移执行器;在此之前本目录仅占位。
