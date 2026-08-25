import { startTransition, useEffect, useState } from 'react'
import { connectEventStream } from '../../lib/events'
import {
  getOverview,
  resolveDashboardToken,
  type DashboardEvent,
  type OverviewResponse
} from '../../lib/api'
import { DashboardShell } from '../../components/dashboard-shell'
import { EmptyState, Panel, ToneBadge, formatMoney, formatTimestamp } from '../../components/ui'

export function OverviewPage({ initialData }: { initialData?: OverviewResponse }) {
  const [data, setData] = useState<OverviewResponse | null>(initialData ?? null)
  const [events, setEvents] = useState<DashboardEvent[]>([])
  const [loading, setLoading] = useState(!initialData)
  const [error, setError] = useState('')

  useEffect(() => {
    if (initialData) {
      return
    }

    let cancelled = false
    setLoading(true)
    void getOverview()
      .then((next) => {
        if (cancelled) {
          return
        }
        startTransition(() => {
          setData(next)
        })
        setError('')
      })
      .catch((reason: unknown) => {
        if (cancelled) {
          return
        }
        setError(reason instanceof Error ? reason.message : '加载总览数据失败。')
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false)
        }
      })

    return () => {
      cancelled = true
    }
  }, [initialData])

  useEffect(() => {
    const token = resolveDashboardToken()
    if (!token) {
      return
    }
    return connectEventStream(token, (event: DashboardEvent) => {
      setEvents((current) => [event, ...current].slice(0, 8))
    })
  }, [])

  return (
    <DashboardShell
      active="overview"
      eyebrow="Gold Bolt"
      title="运营总览"
      description="实时监控账户连接状态、市场交易资格和切换就绪状态。"
      status={data?.status ?? '连接中'}
    >
      {error ? (
        <div className="rounded-3xl border border-rose-300/20 bg-rose-400/10 px-4 py-3 text-sm text-rose-100">
          {error}
        </div>
      ) : null}

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        {data?.cards.map((card) => (
          <Panel key={card.title} title={card.title} subtitle={card.detail}>
            <div className="flex items-center justify-between gap-4">
              <p className="text-3xl font-semibold tracking-tight text-stone-50">{card.value}</p>
              <ToneBadge tone={card.tone}>{card.tone}</ToneBadge>
            </div>
          </Panel>
        ))}
      </div>

      <div className="grid gap-6 xl:grid-cols-[1.45fr_0.9fr]">
        <Panel
          title="账户列表"
          subtitle={
            data ? `数据生成于 ${formatTimestamp(data.generated_at)}，来源 /api/v1/overview。` : '等待服务器返回首次快照数据...'
          }
        >
          {!data && loading ? (
            <EmptyState title="加载中" detail="正在等待 API 返回首次总览数据..." />
          ) : data && data.accounts.length > 0 ? (
            <div className="overflow-x-auto">
              <table className="min-w-full text-left text-sm">
                <thead className="text-xs uppercase tracking-[0.2em] text-stone-500">
                  <tr>
                    <th className="pb-3">账户</th>
                    <th className="pb-3">状态</th>
                    <th className="pb-3">余额</th>
                    <th className="pb-3">净值</th>
                    <th className="pb-3">持仓</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-white/5">
                  {data.accounts.map((account) => (
                    <tr key={account.account_id} className="align-top">
                      <td className="py-4 pr-4">
                        <a href={`/accounts/${account.account_id}/`} className="font-semibold text-stone-50 underline-offset-4 hover:underline">
                          {account.account_id}
                        </a>
                        <p className="mt-1 text-stone-400">{account.broker}</p>
                        <p className="text-xs uppercase tracking-[0.18em] text-stone-500">{account.server_name}</p>
                      </td>
                      <td className="py-4 pr-4">
                        <div className="flex flex-wrap gap-2">
                          <ToneBadge tone={account.connected ? 'green' : 'red'}>
                            {account.connected ? '已连接' : '离线'}
                          </ToneBadge>
                          <ToneBadge tone={account.market_open && account.is_trade_allowed ? 'blue' : 'orange'}>
                            {account.market_open && account.is_trade_allowed ? '可交易' : '受限'}
                          </ToneBadge>
                        </div>
                      </td>
                      <td className="py-4 pr-4 text-stone-200">{formatMoney(account.balance)}</td>
                      <td className="py-4 pr-4 text-stone-200">{formatMoney(account.equity)}</td>
                      <td className="py-4 text-stone-200">{account.positions}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <EmptyState title="暂无账户" detail="尚未收到任何 MT4 终端上报的快照数据。" />
          )}
        </Panel>

        <Panel title="实时事件流" subtitle="通过 SSE 推送的事件，使用同一个管理 Token 认证。">
          {events.length > 0 ? (
            <div className="space-y-3">
              {events.map((event) => (
                <article key={event.event_id} className="rounded-2xl bg-black/20 px-4 py-3">
                  <div className="flex items-center justify-between gap-3">
                    <p className="text-sm font-semibold text-stone-100">{event.event_type}</p>
                    <ToneBadge tone="blue">{event.source}</ToneBadge>
                  </div>
                  <p className="mt-2 text-xs uppercase tracking-[0.18em] text-stone-500">
                    {event.account_id || '系统'} · {formatTimestamp(event.timestamp)}
                  </p>
                </article>
              ))}
            </div>
          ) : (
            <EmptyState title="暂无实时事件" detail="AI 分析结果或切换事件发布后将自动显示，无需刷新页面。" />
          )}
        </Panel>
      </div>
    </DashboardShell>
  )
}
