import { startTransition, useEffect, useState } from 'react'
import { getAccounts, type OverviewAccount } from '../../lib/api'
import { DashboardShell } from '../../components/dashboard-shell'
import { EmptyState, Panel, ToneBadge } from '../../components/ui'
import { formatMoney } from '../../lib/format'

export function AccountsPage() {
  const [accounts, setAccounts] = useState<OverviewAccount[]>([])
  const [status, setStatus] = useState('连接中')
  const [error, setError] = useState('')

  useEffect(() => {
    let cancelled = false
    void getAccounts()
      .then((response) => {
        if (cancelled) {
          return
        }
        startTransition(() => {
          setAccounts(response.accounts)
        })
        setStatus(response.status)
      })
      .catch((reason: unknown) => {
        if (!cancelled) {
          setError(reason instanceof Error ? reason.message : '加载账户数据失败。')
        }
      })
    return () => {
      cancelled = true
    }
  }, [])

  return (
    <DashboardShell
      active="accounts"
      eyebrow="账户工作区"
      title="账户总览"
      description="查看各账户的最新运行快照，深入查看指标、持仓和 AI 分析结果。"
      status={status}
    >
      {error ? (
        <div className="rounded-3xl border border-rose-300/20 bg-rose-400/10 px-4 py-3 text-sm text-rose-100">
          {error}
        </div>
      ) : null}

      {accounts.length > 0 ? (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {accounts.map((account) => (
            <Panel key={account.account_id} title={account.account_id} subtitle={`${account.broker} · ${account.server_name}`}>
              <div className="space-y-4">
                <div className="flex flex-wrap gap-2">
                  <ToneBadge tone={account.connected ? 'green' : 'red'}>
                    {account.connected ? '已连接' : '离线'}
                  </ToneBadge>
                  <ToneBadge tone={account.market_open && account.is_trade_allowed ? 'blue' : 'orange'}>
                    {account.market_open && account.is_trade_allowed ? '可交易' : '受限'}
                  </ToneBadge>
                </div>
                <div className="metric-grid">
                  <div>
                    <p className="text-xs uppercase tracking-[0.18em] text-stone-500">余额</p>
                    <p className="mt-2 text-xl font-semibold text-stone-50">{formatMoney(account.balance)}</p>
                  </div>
                  <div>
                    <p className="text-xs uppercase tracking-[0.18em] text-stone-500">净值</p>
                    <p className="mt-2 text-xl font-semibold text-stone-50">{formatMoney(account.equity)}</p>
                  </div>
                  <div>
                    <p className="text-xs uppercase tracking-[0.18em] text-stone-500">持仓数</p>
                    <p className="mt-2 text-xl font-semibold text-stone-50">{account.positions}</p>
                  </div>
                </div>
                <a
                  href={`/accounts/${account.account_id}/`}
                  className="inline-flex rounded-full bg-amber-300/12 px-4 py-2 text-sm font-medium text-amber-100 ring-1 ring-amber-200/30 transition hover:bg-amber-300/18"
                >
                  查看详情
                </a>
              </div>
            </Panel>
          ))}
        </div>
      ) : (
        <Panel title="账户总览" subtitle="暂无可用数据。">
          <EmptyState title="暂无运行数据" detail="等待 MT4 客户端发送 /register 和 /heartbeat 数据，然后刷新此页面。" />
        </Panel>
      )}
    </DashboardShell>
  )
}
