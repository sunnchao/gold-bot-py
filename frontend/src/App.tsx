import { useEffect, useState } from 'react'
import { OverviewPage } from './pages/overview/OverviewPage'
import { AccountsPage } from './pages/accounts/AccountsPage'
import { AccountRoutePage } from './pages/accounts/AccountRoutePage'
import { AuditPage } from './pages/audit/AuditPage'

// 路由对应 gold-bot app-web(app/ 静态导出):
//   /                 app/page.tsx                      → 运营总览
//   /accounts/        app/accounts/page.tsx             → 账户列表
//   /accounts/:id/    app/accounts/[accountId]/page.tsx → 账户详情(客户端解析路径)
//   /audit/           app/audit/page.tsx                → 审计与就绪
// 导航使用普通 <a href> 全页加载,与 gold-bot 静态导出行为一致;
// Vite dev 与静态托管均以 index.html 作为 SPA fallback。

function pathSegments(pathname: string): string[] {
  return pathname.split('/').filter(Boolean)
}

function DashboardApp() {
  const [pathname, setPathname] = useState(() => window.location.pathname)

  useEffect(() => {
    const update = () => setPathname(window.location.pathname)
    window.addEventListener('popstate', update)
    return () => {
      window.removeEventListener('popstate', update)
    }
  }, [])

  const segments = pathSegments(pathname)

  if (segments[0] === 'accounts') {
    if (segments[1]) {
      return <AccountRoutePage />
    }
    return <AccountsPage />
  }

  if (segments[0] === 'audit') {
    return <AuditPage />
  }

  return <OverviewPage />
}

export default DashboardApp
