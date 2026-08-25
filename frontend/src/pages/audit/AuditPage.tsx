import { startTransition, useEffect, useState } from 'react'
import { getAudit, type AuditResponse } from '../../lib/api'
import { DashboardShell } from '../../components/dashboard-shell'
import { EmptyState, Panel, ToneBadge } from '../../components/ui'
import { formatPercent, formatTimestamp } from '../../lib/format'

export function AuditPage({ initialData }: { initialData?: AuditResponse }) {
  const [data, setData] = useState<AuditResponse | null>(initialData ?? null)
  const [error, setError] = useState('')

  useEffect(() => {
    if (initialData) {
      return
    }

    let cancelled = false
    void getAudit()
      .then((next) => {
        if (cancelled) {
          return
        }
        startTransition(() => {
          setData(next)
        })
      })
      .catch((reason: unknown) => {
        if (!cancelled) {
          setError(reason instanceof Error ? reason.message : '加载审计数据失败。')
        }
      })
    return () => {
      cancelled = true
    }
  }, [initialData])

  const report = data?.report

  return (
    <DashboardShell
      active="audit"
      eyebrow="双轨切换"
      title="审计与就绪"
      description="Python 与 Go 一致性可见化：回放验证、镜像流量偏差和协议错误率汇总为上线就绪视图。"
      status={data?.status ?? '连接中'}
    >
      {error ? (
        <div className="rounded-3xl border border-rose-300/20 bg-rose-400/10 px-4 py-3 text-sm text-rose-100">
          {error}
        </div>
      ) : null}

      <div className="grid gap-6 xl:grid-cols-[1.1fr_0.9fr]">
        <Panel title="就绪状态" subtitle={data ? `快照生成于 ${formatTimestamp(data.generated_at)}。` : '等待审计报告...'}>
          {report ? (
            <div className="space-y-5">
              <div className="flex flex-wrap items-center gap-3">
                <ToneBadge tone={report.ready ? 'green' : 'orange'}>{report.ready ? '就绪' : '未就绪'}</ToneBadge>
                {report.last_shadow_event_at ? (
                  <span className="text-sm text-stone-400">最近镜像事件: {formatTimestamp(report.last_shadow_event_at)}</span>
                ) : (
                  <span className="text-sm text-stone-500">尚未观察到镜像流量。</span>
                )}
              </div>
              <div className="metric-grid">
                <Metric label="协议错误率" value={formatPercent(report.protocol_error_rate)} />
                <Metric label="信号偏差率" value={formatPercent(report.signal_drift_rate)} />
                <Metric label="指令偏差率" value={formatPercent(report.command_drift_rate)} />
              </div>
              <div>
                <p className="text-xs uppercase tracking-[0.18em] text-stone-500">缺失能力</p>
                {report.missing_capabilities.length > 0 ? (
                  <div className="mt-3 flex flex-wrap gap-2">
                    {report.missing_capabilities.map((item) => (
                      <ToneBadge key={item} tone="orange">
                        {item}
                      </ToneBadge>
                    ))}
                  </div>
                ) : (
                  <p className="mt-3 text-sm text-stone-400">所有必要能力均已就绪，仅阈值合规性决定最终就绪状态。</p>
                )}
              </div>
            </div>
          ) : (
            <EmptyState title="暂无报告" detail="API 尚未返回审计数据。" />
          )}
        </Panel>

        <Panel title="切换摘要" subtitle="直接反映切换服务的检查结果。">
          {data && data.summary.length > 0 ? (
            <div className="space-y-3">
              {data.summary.map((item) => (
                <article key={item.label} className="rounded-2xl bg-black/20 px-4 py-4">
                  <div className="flex items-center justify-between gap-3">
                    <p className="text-sm font-semibold text-stone-100">{item.label}</p>
                    <ToneBadge tone={item.tone}>{item.value}</ToneBadge>
                  </div>
                  <p className="mt-2 text-sm text-stone-400">{item.detail}</p>
                </article>
              ))}
            </div>
          ) : (
            <EmptyState title="暂无摘要" detail="切换服务尚未发布就绪检查结果。" />
          )}
        </Panel>
      </div>
    </DashboardShell>
  )
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-xs uppercase tracking-[0.18em] text-stone-500">{label}</p>
      <p className="mt-2 text-2xl font-semibold text-stone-50">{value}</p>
    </div>
  )
}
