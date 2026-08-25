import { useEffect, useState, type ReactNode } from 'react'
import { ToneBadge, cx } from './ui'

type ShellProps = {
  active: 'overview' | 'accounts' | 'audit'
  eyebrow: string
  title: string
  description: string
  status: string
  children: ReactNode
}

const navItems = [
  { key: 'overview', href: '/', label: '总览' },
  { key: 'accounts', href: '/accounts/', label: '账户' },
  { key: 'audit', href: '/audit/', label: '审计' }
] as const

export function DashboardShell({ active, eyebrow, title, description, status, children }: ShellProps) {
  const [suffix, setSuffix] = useState('')

  useEffect(() => {
    if (typeof window === 'undefined') {
      return
    }
    const params = new URLSearchParams(window.location.search)
    const token = params.get('token')
    setSuffix(token ? `?token=${encodeURIComponent(token)}` : '')
  }, [])

  return (
    <main className="min-h-screen px-4 py-5 sm:px-6 lg:px-8">
      <div className="mx-auto max-w-7xl space-y-6">
        <header className="glass-panel relative overflow-hidden rounded-[36px] px-6 py-7 sm:px-8">
          <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_top_left,rgba(243,198,89,0.18),transparent_35%),radial-gradient(circle_at_right,rgba(125,168,255,0.12),transparent_28%)]" />
          <div className="relative flex flex-col gap-6 lg:flex-row lg:items-end lg:justify-between">
            <div className="max-w-3xl">
              <p className="text-xs font-semibold uppercase tracking-[0.32em] text-amber-200/80">{eyebrow}</p>
              <h1 className="mt-3 text-4xl font-semibold tracking-tight text-stone-50 sm:text-5xl">{title}</h1>
              <p className="mt-4 max-w-2xl text-sm leading-7 text-stone-300 sm:text-base">{description}</p>
            </div>
            <div className="space-y-3">
              <ToneBadge tone="amber">{status}</ToneBadge>
              <p className="max-w-xs text-sm leading-6 text-stone-400">
                Vite 静态面板，数据来自 FastAPI 管理 API。Token 认证方式不变。
              </p>
            </div>
          </div>
          <nav className="relative mt-8 flex flex-wrap gap-3">
            {navItems.map((item) => (
              <a
                key={item.key}
                href={`${item.href}${suffix}`}
                className={cx(
                  'rounded-full px-4 py-2 text-sm font-medium transition',
                  active === item.key
                    ? 'bg-amber-300/14 text-amber-100 ring-1 ring-amber-200/35'
                    : 'bg-white/5 text-stone-300 ring-1 ring-white/10 hover:bg-white/10 hover:text-stone-100'
                )}
              >
                {item.label}
              </a>
            ))}
          </nav>
        </header>
        {children}
      </div>
    </main>
  )
}
