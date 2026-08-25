# Frontend

Gold Bolt 运营面板(移植自 gold-bot `apps/app-web`,Next.js → Vite)。

React 19 + TypeScript + Vite + Tailwind CSS 3 + Vitest。

## 路由(全页 `<a>` 导航,与 gold-bot 静态导出一致)

| 路径 | 页面 | 源对应 |
|---|---|---|
| `/` | 运营总览 | `app/page.tsx` + `components/overview-page.tsx` |
| `/accounts/` | 账户列表 | `app/accounts/page.tsx` + `components/accounts-page.tsx` |
| `/accounts/:accountId/` | 账户详情 | `app/accounts/[accountId]/page.tsx` + `components/account-detail-page.tsx` |
| `/audit/` | 审计与就绪 | `app/audit/page.tsx` + `components/audit-page.tsx` |

Vite dev 与静态托管均通过 SPA fallback(index.html)承载深链;账户 ID 由
`AccountRoutePage` 从 `window.location.pathname` 客户端解析(对应源端 `usePathname`)。

## 开发

```bash
npm install
npm run dev
```

开发服务器默认 `http://localhost:5173`,`/api` 代理到 FastAPI(`localhost:8000`,
保留 `/api` 前缀;`VITE_PROXY_TARGET=http://backend:8000` 用于 Docker 内联)。
Token 认证通过 `?token=...` 查询参数或 localStorage 中的 `gold-bot.dashboard-token`。

生产:frontend 镜像用 nginx(`try_files` + `/accounts/` 回 `__dynamic__`);
backend 镜像把 `frontend/dist` 拷到 `/opt/dashboard` 与 `apps/app-web/dist`,
`create_api_app` 在 JSON 404 之前按 TS `staticDashboardResponse` 回 HTML。

## 质量门禁

```bash
npm run test      # vitest(4 文件 6 用例)
npm run build     # tsc -b && vite build
npm run lint      # oxlint(0 errors)
```

## 移植说明

- 源 `components/*` → `src/pages/{overview,accounts,audit}/*`;共享 `ui.tsx` /
  `dashboard-shell.tsx` → `src/components/`;`lib/{api,events}.ts` 原样移植。
- 移除 `'use client'` 与 `next/navigation`;文本中 "Next.js 静态面板/Go 管理 API"
  等栈标识改为 Vite/FastAPI 表述。
- 纯函数(市场过滤器解析、positionMetrics 等)保持原样,测试用例 1:1 移植。
- `ui.tsx` 删除了源端未使用的 `toneLabels`(目标 tsconfig 开启 `noUnusedLocals`)。
