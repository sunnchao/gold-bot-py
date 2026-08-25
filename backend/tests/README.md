# 测试映射台账(源测试 → 目标测试)

| 项 | 值 |
|---|---|
| 建立 | 2026-08-22(port-baseline-20260822) |
| 用途 | TLS 规范化:每个源测试必须有目标落点;缺失即模块未完成(DOD 第 3 条) |
| 转化规则 | R 命名 `X.spec.ts`→`test_x.py`;目录镜像;对拍/契约/集成按层归属 |
| 分层 | L1 对拍 / L2 契约 / L3 单元 / L4 集成 / L5 agent / L6 前端(见 docs/porting/TDD.md) |

> 仅列出等价映射;新增用例按 TDD.md 规范补充,不在此表登记。

## packages/trading-core → backend/tests/unit/trading_core(10 个源文件)| 源文件 | 目标文件 | 里程碑 | 状态 | 说明 |
|---|---|---|---|---|
| `packages/trading-core/src/engine/config.spec.ts` | `backend/tests/unit/trading_core/test_config.py` | M3 | ✅ 13 用例(P0) | L3,13 个 it() 全镜像 + TS 运行时 25 次调用对拍一致 |
| `packages/trading-core/src/engine/engine.spec.ts` | `backend/tests/unit/trading_core/test_engine.py` | M3 | ✅ 19+2skip(P0) | L3,逐 it() 镜像;2 个 skip 镜像 it.skip |
| `packages/trading-core/src/harmonic/detector.spec.ts` | `backend/tests/unit/trading_core/test_harmonic_detector.py` | M3 | ✅ 5 用例(P0) | L3,全字段数值/字符串逐字节对拍 |
| `packages/trading-core/src/index.spec.ts` | `backend/tests/unit/trading_core/test_index.py` | M3 | ✅ 2 用例(P0) | L3,入口 status 断言 |
| `packages/trading-core/src/indicators/candlestick.spec.ts` | `backend/tests/unit/trading_core/test_candlestick.py` | M3 | ✅ 6 用例(P0) | L3,10 种形态 + 方向分类 |
| `packages/trading-core/src/indicators/indicator.spec.ts` | `backend/tests/unit/trading_core/test_indicator.py` | M3 | ✅ 11 用例(P0) | L3,EMA/ATR/RSI/MACD/Fib/pivots/ADX/Bollinger/Stoch 尾部与 Go oracle 夹具全等 |
| `packages/trading-core/src/positionmgr/manager.spec.ts` | `backend/tests/unit/trading_core/test_positionmgr_manager.py` | M3 | ✅ 65 用例(P0) | L3,65 个 it() 全镜像;toFixed/舍入边界逐位一致 |
| `packages/trading-core/src/replay/replay.spec.ts` | `backend/tests/unit/trading_core/test_replay.py` | M3 | ✅ 24 用例(P0) | **L1 金标准对拍 PASS**:input.json→run_replay 输出与 expected.json(signal+10 logs+position_commands)全等;另含 23 个代表性行为用例 |
| `packages/trading-core/src/riskgate/riskgate.spec.ts` | `backend/tests/unit/trading_core/test_riskgate.py` | M3 | ✅ 31 用例(P0) | L3,21 riskgate + 10 market-filters(含 it.each 参数组) |
| `packages/trading-core/src/smc/detector.spec.ts` | `backend/tests/unit/trading_core/test_smc_detector.py` | M3 | ✅ 22 用例(P0) | L3,swing/break/FVG/sweep/OB 全语义 + TS↔Python 差分对拍 |

## packages/observability → backend/tests/unit/observability(2 个源文件)

| 源文件 | 目标文件 | 里程碑 | 状态 | 说明 |
|---|---|---|---|---|
| `packages/observability/src/index.spec.ts` | `backend/tests/unit/observability/test_index.py` | M2 | ✅ 30 用例(P0) | L3,health/SSE hub/帧格式/shadow-report 逐项断言 |
| `packages/observability/src/metrics.spec.ts` | `backend/tests/unit/observability/test_metrics.py` | M2 | ✅ 同左 | L3,24 指标名/help/labels/桶逐项断言 + collector/中间件 |

## packages/config + breakout-cache + shared-contracts

| 源文件 | 目标文件 | 里程碑 | 状态 | 说明 |
|---|---|---|---|---|
| `packages/config/src/env.spec.ts` | `backend/tests/unit/core/test_config.py` | M0 | ✅ 4 用例 | L3;缺省值/ADMIN_TOKEN 回退/端口/日亏比例 |
| `packages/breakout-cache/src/cache.spec.ts` | `backend/tests/unit/breakout_cache/test_cache.py` | M0 | ✅ 同左 | L3;Redis ping/TTL/非法 URL + 内存过期 |
| `packages/shared-contracts/src/endpoint.spec.ts` | `backend/tests/unit/shared_contracts/test_endpoint.py` | M1 | ✅ 同左 | L3;EA 路由表 + extract_auth_token 优先级 |
| `packages/shared-contracts/src/runtime.spec.ts` | `backend/tests/unit/shared_contracts/test_runtime.py` | M1 | ✅ 同左 | L3;runtime/command 枚举 |
| `packages/shared-contracts/src/strategy.spec.ts` | `backend/tests/unit/shared_contracts/test_strategy.py` | M1 | ✅ 同左 | L3;EA 策略名冻结表 |
| `packages/shared-contracts/src/fixture.spec.ts` | `backend/tests/unit/shared_contracts/test_fixture.py` | M1 | ✅ 同左 | L3;7+11 oracle 夹具可解析 |

## packages/notifications → backend/tests/unit/notifications(2 个源文件)

| 源文件 | 目标文件 | 里程碑 | 状态 | 说明 |
|---|---|---|---|---|
| `packages/notifications/src/discord.spec.ts` | `backend/tests/unit/notifications/test_discord.py` | M2 | ✅ 同左 | L3,冷却窗口/JSON 载荷/fire-and-forget 日志 |
| `packages/notifications/src/feishu.spec.ts` | `backend/tests/unit/notifications/test_feishu.py` | M2 | ✅ 同左 | L3,HMAC-SHA256 签名/交互卡片/冷却窗口 |

## packages/persistence → backend/tests/unit/persistence + auth/tokens(4 个源文件)

| 源文件 | 目标文件 | 里程碑 | 状态 | 说明 |
|---|---|---|---|---|
| `packages/persistence/src/index.spec.ts` | `backend/tests/unit/persistence/test_store_suite.py` | M1 | ✅ 96 用例(P0) | L3;同一套参数化跑 in-memory + sqlite |
| `packages/persistence/src/migrate.spec.ts` | `backend/tests/unit/persistence/test_migrate.py` | M1 | ✅ 同左 | L3;12 个运行时迁移原样导入 + schema_migrations 幂等 |
| `packages/persistence/src/postgres-query.spec.ts` | `backend/tests/unit/persistence/test_store_suite.py` | M1 | ✅ 同左 | L3;查询语义(快照/命令/决策/影子/令牌/已平仓/日权益)逐项断言 |
| `packages/persistence/src/postgres.spec.ts` | `backend/tests/unit/persistence/test_sqlite_store.py` | M1 | ✅ 同左 | L3;EA 生命周期跨重开持久化 + 迁移跳过 |
| 附带:`apps/app-server/src/middleware/auth.spec.ts`(若有) | `backend/tests/unit/test_auth.py` | M1 | ✅ 同左 | L3;X-API-Token/X-API-Key/?token 提取与路由授权 |
| 附带:`apps/app-server/src/bootstrap/tokens.spec.ts` | `backend/tests/integration/bootstrap/test_tokens.py` | M1 | ✅ 同左 | L4;admin 种子 + legacy tokens.json 导入 |

## apps/app-agent → backend/tests/agent(34 个源文件)

| 源文件 | 目标文件 | 里程碑 | 状态 | 说明 |
|---|---|---|---|---|
| `apps/app-agent/src/agents/account-action-guard.test.ts` | `backend/tests/agent/agents/test_account_action_guard.py` | M7 | ✅ 4 用例 | L5;isSymbolLoaded/ticket 归属/账户校验 |
| `apps/app-agent/src/agents/comprehensive-analyst.test.ts` | `backend/tests/agent/agents/test_comprehensive_analyst.py` | M7 | ✅ 13 用例 | L5;prompt/structured retry/fallback |
| `apps/app-agent/src/agents/publisher.test.ts` | `backend/tests/agent/agents/test_publisher.py` | M7 | ✅ 7 用例 | L5;Feishu/Discord 载荷 + 并发 |
| `apps/app-agent/src/agents/trade-action-converter.test.ts` | `backend/tests/agent/agents/test_trade_action_converter.py` | M7 | ✅ 8 用例 | L5;function-calling 转换 |
| `apps/app-agent/src/agents/*(technical/risk/sr/mao,无 TS 测试)` | `backend/tests/agent/agents/test_{technical_analyst,risk_manager,sr_analyst,mao_arbitrator}.py` | M7 | ✅ 21 用例 | L5;pytest 侧补齐 |
| `apps/app-agent/src/config/app-config.service.test.ts` | `backend/tests/agent/config/test_app_config.py` | M7 | ✅ 同左 | L5;zod coerce 语义镜像 |
| `apps/app-agent/src/config/bar-source.service.test.ts` | `backend/tests/agent/config/test_bar_source.py` | M7 | ✅ 同左 | L5;canonical symbol/ATR 回退 |
| `apps/app-agent/src/config/index.test.ts` | `backend/tests/agent/config/test_index_config.py` | M7 | ✅ 同左 | L5;env 加载缓存 |
| `apps/app-agent/src/config/symbol-profile.test.ts` | `backend/tests/agent/config/test_symbol_profile.py` | M7 | ✅ 同左 | L5;9 个 profile + micro-contract;正则 /gi bug 已修 |
| `apps/app-agent/src/evaluation/replay-runner.test.ts` | `backend/tests/agent/evaluation/test_replay_runner.py` | M7 | ✅ 4 用例 | L5;redactSecrets/漂移率(补建) |
| `apps/app-agent/src/graph/compose.spec.ts` | `backend/tests/agent/graph/test_compose_spec.py` | M7 | ✅ 同左 | L5;trade_plan.v1 组合 |
| `apps/app-agent/src/graph/compose.test.ts` | `backend/tests/agent/graph/test_compose.py` | M7 | ✅ 同左 | L5;schema 校验 |
| `apps/app-agent/src/graph/edges.test.ts` | `backend/tests/agent/graph/test_edges.py` | M7 | ✅ 同左 | L5;routeAfterFetch/Arbitration |
| `apps/app-agent/src/graph/market-insight-cache.service.test.ts` | `backend/tests/agent/graph/test_market_insight_cache.py` | M7 | ✅ 同左 | L5;TTL 缓存 |
| `apps/app-agent/src/graph/workflow-nodes.service.test.ts` | `backend/tests/agent/graph/test_workflow_nodes.py` | M7 | ✅ 同左 | L5;节点服务 |
| `apps/app-agent/src/graph/workflow.service.test.ts` | `backend/tests/agent/graph/test_workflow.py` | M7 | ✅ 同左 | L5;invoke/duration |
| `apps/app-agent/src/health/health.controller.test.ts` | `backend/tests/agent/health/test_health.py` | M7 | ✅ 4 用例 | L5;redis/goldbot 探测(补建) |
| `apps/app-agent/src/metrics/llm-cache-metrics.test.ts` | `backend/tests/agent/metrics/test_llm_cache_metrics.py` | M7 | ✅ 3 用例 | L5;hit-rate/计数(补建,prometheus_client unregister 重建实现 reset) |
| `apps/app-agent/src/results/results.controller.test.ts` | `backend/tests/agent/results/test_results.py` | M7 | ✅ 3 用例 | L5;limit 校验(补建) |
| `apps/app-agent/src/scheduler/*.test.ts` | `backend/tests/agent/scheduler/test_{scheduler_service,analysis_processor,position_poll_processor}.py` | M7 | ✅ 同左 | L5;队列注入 |
| `apps/app-agent/src/tools/chanlun-core.test.ts` | `backend/tests/agent/tools/test_chanlun_core.py` | M7 | ✅ 同左 | L5;包含/分型/笔/中枢 |
| `apps/app-agent/src/tools/elliott-wave.test.ts` | `backend/tests/agent/tools/test_elliott_wave.py` | M7 | ✅ 同左 | L5;波浪计数 |
| `apps/app-agent/src/tools/llm-client.test.ts` | `backend/tests/agent/tools/test_llm_client.py` | M7 | ✅ 同左 | L5;httpx MockTransport 离线 |
| `apps/app-agent/src/tools/pattern-detector.test.ts` | `backend/tests/agent/tools/test_pattern_detector.py` | M7 | ✅ 同左 | L5;形态检测;TS lookback 默认值 bug 已修 |
| `apps/app-agent/src/tools/sr-calculator.test.ts` | `backend/tests/agent/tools/test_sr_calculator.py` | M7 | ✅ 同左 | L5;S/R/Fib/pivot/心理位 |
| `apps/app-agent/src/tools/goldbot-api.test.ts` | `backend/tests/agent/tools/test_goldbot_api.py` | M7 | ✅ 同左 | L5;httpx MockTransport、trailing slash、pending_signal 对象/数组/空数组 |
| `apps/app-agent/src/trigger/trigger.controller.test.ts` + `trigger.module.test.ts` | `backend/tests/agent/trigger/test_trigger.py` | M7 | ✅ 7 用例 | L5;白名单/幂等窗/账号契约(补建) |
| `apps/app-agent/src/types/agent.test.ts` | `backend/tests/agent/types/test_agent.py` | M7 | ✅ 同左 | L5;状态默认值 |
| `apps/app-agent/src/types/types.test.ts` | `backend/tests/agent/types/test_types.py` | M7 | ✅ 同左 | L5;当前接口适配(TS 测试用陈旧形状) |
| `apps/app-agent/src/utils/logger.test.ts` | `backend/tests/agent/utils/test_logger.py` | M7 | ✅ 同左 | L5;pino 风格 |
| `apps/app-agent/src/utils/markdown-parser.spec.ts` | `backend/tests/agent/utils/test_markdown_parser.py` | M7 | ✅ 同左 | L5;markdown 解析 |
| `apps/app-agent/src/utils/price-validator.test.ts` | `backend/tests/agent/utils/test_price_validator.py` | M7 | ✅ 同左 | L5;SL/TP 方向/RR |
| 附带:`apps/app-agent/src/store/analysis-store.service.ts` | `backend/tests/agent/store/test_analysis_store.py` | M7 | ✅ 4 用例 | L5;aiosqlite 建表/查询(补建) |

## apps/app-server(15 个源文件,按层拆分)

| 源文件 | 目标文件 | 里程碑 | 状态 | 说明 |
|---|---|---|---|---|
| `apps/app-server/src/app.spec.ts` | `backend/tests/integration/test_app.py` + `test_admin.py` + `test_dashboard.py` | M4-M9 | ✅ 62+19+1 用例(P0) | L4;test_app.py=62 非 admin 用例,test_admin.py=19 用例,test_dashboard.py=SPA 静态托管(dist 存在时 GET/HEAD 回 index.html / accounts/__dynamic__);无 dist 时 `/not-found` 仍为 JSON 404 |
| `apps/app-server/src/bootstrap/tokens.spec.ts` | `backend/tests/integration/bootstrap/test_tokens.py` | M1 | ✅ 同左 | L4 |
| `apps/app-server/src/dockerfile.spec.ts` | `backend/tests/unit/ops/test_dockerfile.py` | M0 | ✅ 同左 | L3 镜像契约 |
| `apps/app-server/src/routes/ai.spec.ts` | `backend/tests/unit/api/test_ai.py` | M6 | ✅ 15 用例(P0) | L2;非 GET 两个 spec it() 镜像 + analysis_payload 快照/market_status/strategy_mapping/ai_result 决策事件/风险命令入队 |
| `apps/app-server/src/routes/ai-result-method.spec.ts` | `backend/tests/unit/api/test_ai.py` | M6 | ✅ 2 用例(P0) | L2;并入 test_ai.py(PUT ai_result / PATCH v2 ai_result) |
| `apps/app-server/src/routes/ea-lifecycle-normalization.spec.ts` | `backend/tests/unit/api/test_ea_lifecycle.py` | M4 | ✅ 12 用例(P0) | L2;register 标量拒绝/heartbeat 默认值+tick symbol 默认逐条镜像 + EA 端点鉴权/绑定/校验矩阵 |
| `apps/app-server/src/routes/indicator-alert.spec.ts` | `backend/tests/unit/api/test_indicator_alert.py` | M4 | ✅ 7 用例(P0) | L2;TTL 4h 去重(原载荷保留)/Go 可解码拒绝/结构化克隆语义 |
| 附带:`apps/app-server/src/routes/visual.ts`(poll 语义) | `backend/tests/unit/api/test_visual.py` | M4 | ✅ 9 用例(P0) | L2;鉴权 401/403/405 + tick/AI 摘要默认值 + trade_plan 回退 + alerts 过滤 |
| 附带:`apps/app-server/src/app.ts`(trade_history/version/download/__contracts/healthz/metrics) | `backend/tests/unit/api/test_trade_history.py` + `test_version_download.py` | M4 | ✅ 16 用例(P0) | L2;magic→strategy 映射/MT 时长/已平仓入库+指标,版本回退/权限/下载字节/契约端点 |
| 附带:`apps/app-server/src/app.ts`(streamEvents SSE) | `backend/tests/unit/api/test_sse_events.py` | M4 | ✅ 2 用例(P0) | L2;admin 鉴权 401/403 + 订阅→帧转发→断开退订(单元级生成器) |
| `apps/app-server/src/services/ai-approve/command.spec.ts` | `backend/tests/integration/services/test_ai_approve_command.py` | M5 | ✅ 9 用例(P0) | L4,逐 it() 镜像;toMatchObject 递归子集匹配 |
| `apps/app-server/src/services/ai-approve/gate.spec.ts` | `backend/tests/integration/services/test_ai_approve_gate.py` | M5 | ✅ 34 用例(P0) | L4,23 pending + 3 favorable + 8 adverse add-on,Go lots 减半规则 |
| `apps/app-server/src/services/ai-approve/rules.spec.ts` | `backend/tests/integration/services/test_ai_approve_rules.py` | M5 | ✅ 19 用例(P0) | L4,order intent/take-profit 归一化/保护方向;toBeCloseTo(12) |
| `apps/app-server/src/services/analysis/service.spec.ts` | `backend/tests/integration/services/test_analysis.py` | M5 | ✅ 4 用例(P0) | L4,AI 结果注入/H1 收盘回退/D1 趋势/过滤不相关 symbol |
| `apps/app-server/src/services/arbitration/service.spec.ts` | `backend/tests/integration/services/test_arbitration.py` | M5 | ✅ 11 用例(P0) | L4,5min TTL/阈值放行/admin 修订/中止信号 |
| `apps/app-server/src/services/command-lifecycle/service.spec.ts` | `backend/tests/integration/services/test_command_lifecycle.py` | M5 | ✅ 5 用例(P0) | L4,cutover/shadow/ai_approve→ai_result 来源/4108 清理 |
| `apps/app-server/src/services/scheduler/service.spec.ts` | `backend/tests/integration/services/test_scheduler.py` | M5 | ✅ 28 用例(P0) | L4,replay 发布/AI SL 冷却/STOPLEVEL 130/日亏损护栏/周五窗口 |
| `apps/app-server/src/services/shadow/service.spec.ts` | `backend/tests/integration/services/test_shadow.py` | M5 | ✅ 5 用例(P0) | L4,metrics/qualification/快照对比/漂移稳定 JSON |

## apps/app-web(1 个源文件 + 3 个组件测试)

| 源文件 | 目标文件 | 里程碑 | 状态 | 说明 |
|---|---|---|---|---|
| `apps/app-web/tests/toolchain-contract.test.ts` | `frontend/src/toolchain-contract.test.tsx` | M8 | ✅ 1 用例 | L6 工具链冒烟:渲染 Dashboard Shell(源端为 package.json/Dockerfile 契约,目标侧改为渲染冒烟) |
| `apps/app-web/tests/overview.test.tsx` | `frontend/src/test/overview.test.tsx` | M8 | ✅ 1 用例 | L6;initialData 快照渲染卡片/账户行 |
| `apps/app-web/tests/account-detail.test.tsx` | `frontend/src/test/account-detail.test.tsx` | M8 | ✅ 3 用例 | L6;决策舱/风险门/持仓 R 倍数/Market Filters 顺序 |
| `apps/app-web/tests/audit-page.test.tsx` | `frontend/src/test/audit-page.test.tsx` | M8 | ✅ 1 用例 | L6;就绪摘要/缺失能力 |

> M8 组件移植:源 `components/*` → `frontend/src/pages/{overview,accounts,audit}/*`,`lib/{api,events}.ts` 原样,
> `ui/dashboard-shell` → `frontend/src/components/{ui,dashboard-shell}.tsx;deviation:Next.js(usePathname/静态导出)
> → Vite 路径路由(SPA fallback + window.location.pathname),导航仍为全页 `<a>`;”Next.js/Go” 栈标识文案改为 Vite/FastAPI。
