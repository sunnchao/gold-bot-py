import { startTransition, useEffect, useState } from 'react'
import { getAccountDetail, type AccountDetail, type DecisionEvent } from '../../lib/api'
import { DashboardShell } from '../../components/dashboard-shell'
import { EmptyState, JsonPreview, Panel, ToneBadge, formatMoney, formatNumber, formatTimestamp } from '../../components/ui'

export function AccountDetailPage({
  accountId,
  initialData
}: {
  accountId: string
  initialData?: AccountDetail
}) {
  const [data, setData] = useState<AccountDetail | null>(initialData ?? null)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!accountId || initialData) {
      return
    }

    let cancelled = false
    void getAccountDetail(accountId)
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
        if (!cancelled) {
          setError(reason instanceof Error ? reason.message : '加载账户详情失败。')
        }
      })

    return () => {
      cancelled = true
    }
  }, [accountId, initialData])

  return (
    <DashboardShell
      active="accounts"
      eyebrow="账户详情"
      title={accountId || '账户详情'}
      description="资金快照、市场行情、持仓详情、技术指标和最新 AI 分析结果。"
      status={data?.status ?? '连接中'}
    >
      {error ? (
        <div className="rounded-3xl border border-rose-300/20 bg-rose-400/10 px-4 py-3 text-sm text-rose-100">
          {error}
        </div>
      ) : null}

      {!accountId ? (
        <Panel title="账户详情" subtitle="从总览或账户列表页进入查看具体账户。">
          <EmptyState title="未选择账户" detail="静态导出面板已就绪，但未从当前路径解析到账户 ID。" />
        </Panel>
      ) : data ? (
        <div className="space-y-6">
          <div className="grid gap-6 xl:grid-cols-[1.1fr_0.9fr]">
            <Panel title="资金快照" subtitle={`${data.account.broker} · ${data.account.server_name}`}>
              <div className="metric-grid">
                <div>
                  <p className="text-xs uppercase tracking-[0.18em] text-stone-500">余额</p>
                  <p className="mt-2 text-2xl font-semibold text-stone-50">{formatMoney(data.account.balance, data.account.currency)}</p>
                </div>
                <div>
                  <p className="text-xs uppercase tracking-[0.18em] text-stone-500">净值</p>
                  <p className="mt-2 text-2xl font-semibold text-stone-50">{formatMoney(data.account.equity, data.account.currency)}</p>
                </div>
                <div>
                  <p className="text-xs uppercase tracking-[0.18em] text-stone-500">可用保证金</p>
                  <p className="mt-2 text-2xl font-semibold text-stone-50">{formatMoney(data.account.free_margin, data.account.currency)}</p>
                </div>
                <div>
                  <p className="text-xs uppercase tracking-[0.18em] text-stone-500">杠杆</p>
                  <p className="mt-2 text-2xl font-semibold text-stone-50">1:{data.account.leverage}</p>
                </div>
              </div>
              <div className="mt-5 flex flex-wrap gap-2">
                <ToneBadge tone={data.account.connected ? 'green' : 'red'}>
                  {data.account.connected ? '已连接' : '离线'}
                </ToneBadge>
                <ToneBadge tone="blue">{data.market.symbol}</ToneBadge>
              </div>
            </Panel>

            <Panel title="行情快照" subtitle="EA 最新推送的 Tick 数据。">
              <div className="metric-grid">
                <div>
                  <p className="text-xs uppercase tracking-[0.18em] text-stone-500">买价</p>
                  <p className="mt-2 text-2xl font-semibold text-stone-50">{formatNumber(data.market.bid)}</p>
                </div>
                <div>
                  <p className="text-xs uppercase tracking-[0.18em] text-stone-500">卖价</p>
                  <p className="mt-2 text-2xl font-semibold text-stone-50">{formatNumber(data.market.ask)}</p>
                </div>
                <div>
                  <p className="text-xs uppercase tracking-[0.18em] text-stone-500">点差</p>
                  <p className="mt-2 text-2xl font-semibold text-stone-50">{formatNumber(data.market.spread)}</p>
                </div>
                <div>
                  <p className="text-xs uppercase tracking-[0.18em] text-stone-500">Tick 时间</p>
                  <p className="mt-2 text-2xl font-semibold text-stone-50">{data.market.time || '无'}</p>
                </div>
              </div>
            </Panel>
          </div>

          <DecisionCockpit data={data} />

          <Panel title="当前持仓" subtitle="实时持仓列表及仓位管理上下文。">
            {data.positions.length > 0 ? (
              <div className="overflow-x-auto">
                <table className="min-w-full text-left text-sm">
                  <thead className="text-xs uppercase tracking-[0.2em] text-stone-500">
                    <tr>
                      <th className="pb-3">订单号</th>
                      <th className="pb-3">策略</th>
                      <th className="pb-3">方向</th>
                      <th className="pb-3">手数</th>
                      <th className="pb-3">盈亏</th>
                      <th className="pb-3">SL距离</th>
                      <th className="pb-3">R倍数</th>
                      <th className="pb-3">持仓时长</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-white/5">
                    {data.positions.map((position) => {
                      const metrics = positionMetrics(position)
                      return (
                        <tr key={position.ticket}>
                          <td className="py-4 pr-4 text-stone-100">{position.ticket}</td>
                          <td className="py-4 pr-4 text-stone-300">{position.strategy || '未标记'}</td>
                          <td className="py-4 pr-4">
                            <ToneBadge tone={position.direction === 'BUY' ? 'green' : 'red'}>
                              {position.direction === 'BUY' ? '买入' : '卖出'}
                            </ToneBadge>
                          </td>
                          <td className="py-4 pr-4 text-stone-300">{position.lots}</td>
                          <td className="py-4 pr-4 text-stone-300">{formatMoney(position.profit, data.account.currency)}</td>
                          <td className="py-4 pr-4 text-stone-300">{metrics.slDistance}</td>
                          <td className="py-4 pr-4 text-stone-300">{metrics.rMultiple}</td>
                          <td className="py-4 text-stone-300">{position.hold_hours.toFixed(2)}h</td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            ) : (
              <EmptyState title="暂无持仓" detail="当前账户没有未平仓交易。" />
            )}
          </Panel>

          <div className="grid gap-6 xl:grid-cols-[1.1fr_0.9fr]">
            <Panel title="技术指标" subtitle="按时间周期分组的最新 K 线指标数据。">
              <div className="space-y-4">
                {Object.entries(data.indicators)
                  .filter(([, pack]) => Boolean(pack))
                  .map(([timeframe, pack]) =>
                    pack ? (
                      <article key={timeframe} className="rounded-2xl bg-black/20 px-4 py-4">
                        <div className="mb-3 flex items-center justify-between gap-3">
                          <p className="text-sm font-semibold uppercase tracking-[0.18em] text-stone-100">{timeframe}</p>
                          <ToneBadge tone="blue">{pack.bars_count} 根K线</ToneBadge>
                        </div>
                        <div className="metric-grid">
                          <Metric label="收盘价" value={formatNumber(pack.close)} />
                          <Metric label="EMA20" value={formatNumber(pack.ema20)} />
                          <Metric label="EMA50" value={formatNumber(pack.ema50)} />
                          <Metric label="RSI" value={formatNumber(pack.rsi)} />
                          <Metric label="ADX" value={formatNumber(pack.adx)} />
                          <Metric label="ATR" value={formatNumber(pack.atr)} />
                          {pack.macd_hist != null && <Metric label="MACD柱" value={formatNumber(pack.macd_hist)} />}
                          {pack.stoch_k != null && <Metric label="StochK" value={formatNumber(pack.stoch_k)} />}
                          {pack.stoch_d != null && <Metric label="StochD" value={formatNumber(pack.stoch_d)} />}
                          {pack.vol_sma != null && <Metric label="VolSMA" value={formatNumber(pack.vol_sma)} />}
                          {pack.bb_upper != null && <Metric label="BB上轨" value={formatNumber(pack.bb_upper)} />}
                          {pack.bb_lower != null && <Metric label="BB下轨" value={formatNumber(pack.bb_lower)} />}
                          {pack.fib_382 != null && <Metric label="Fib38.2%" value={formatNumber(pack.fib_382)} />}
                          {pack.fib_618 != null && <Metric label="Fib61.8%" value={formatNumber(pack.fib_618)} />}
                          {pack.pp != null && <Metric label="枢轴PP" value={formatNumber(pack.pp)} />}
                          {pack.r1 != null && <Metric label="R1" value={formatNumber(pack.r1)} />}
                          {pack.s1 != null && <Metric label="S1" value={formatNumber(pack.s1)} />}
                        </div>
                      </article>
                    ) : null
                  )}
              </div>
            </Panel>

            <Panel title="AI 分析结果" subtitle="该账户最新存储的 AI 兼容性分析结果。">
              <JsonPreview value={data.ai_result} />
            </Panel>
          </div>
        </div>
      ) : (
        <Panel title="加载账户详情" subtitle={`正在请求 /api/v1/accounts/${accountId}...`}>
          <EmptyState title="加载中" detail="API 正在准备该账户的详细数据。" />
        </Panel>
      )}
    </DashboardShell>
  )
}

function DecisionCockpit({ data }: { data: AccountDetail }) {
  const tradePlan = asRecord(data.ai_result.trade_plan)
  const events = data.decision_events ?? []
  const riskEvent = events.find((event) => event.stage === 'risk_gate')
  const latestEvent = events[0]
  const decisionID = textValue(tradePlan?.decision_id) || latestEvent?.decision_id || '暂无'
  const mode = textValue(tradePlan?.mode) || textValue(latestEvent?.summary.mode) || 'observe'
  const side = textValue(tradePlan?.side) || textValue(latestEvent?.summary.side) || 'none'
  const confidence = numberValue(tradePlan?.confidence) ?? numberValue(latestEvent?.summary.confidence)
  const reasonCodes = stringArray(tradePlan?.reason_codes)
  const conflicts = stringArray(tradePlan?.conflicts)
  const narrative = textValue(tradePlan?.narrative)
  const marketFilters = collectMarketFilterCodes(data, tradePlan)
  const riskCodes = riskEvent?.reason_codes ?? []

  return (
    <div className="grid gap-6 xl:grid-cols-[0.95fr_1.05fr]">
      <Panel title="决策摘要" subtitle="结构化 trade_plan.v1 和最新风险审查。">
        <div className="space-y-4">
          <div className="rounded-2xl bg-black/20 px-4 py-4">
            <p className="text-xs uppercase tracking-[0.18em] text-stone-500">Decision ID</p>
            <p className="mt-2 break-all text-lg font-semibold text-stone-100">{decisionID}</p>
          </div>
          <div className="metric-grid">
            <Metric label="模式 / 方向" value={`${mode} / ${side}`} />
            <Metric label="置信度" value={confidence == null ? '无' : `${confidence}%`} />
            <Metric label="过期时间" value={textValue(tradePlan?.expires_at) || '无'} />
          </div>
          <CodeChips title="Reason Codes" values={reasonCodes} empty="无决策理由码" />
          <CodeChips title="Conflicts" values={conflicts} empty="无冲突" tone="orange" />
          <MarketFilterChips items={marketFilters} />
          {narrative ? <p className="rounded-2xl bg-black/20 px-4 py-3 text-sm leading-6 text-stone-300">{narrative}</p> : null}
        </div>
      </Panel>

      <Panel title="风险门" subtitle="确定性审查优先于自然语言叙述。">
        {riskEvent ? (
          <div className="space-y-4">
            <div className="flex flex-wrap items-center gap-3">
              <ToneBadge tone={toneForStatus(riskEvent.status)}>{riskEvent.status}</ToneBadge>
              <span className="text-sm text-stone-400">{formatTimestamp(riskEvent.created_at)}</span>
            </div>
            <div className="metric-grid">
              <Metric label="请求手数" value={summaryNumber(riskEvent, 'requested_lots')} />
              <Metric label="允许手数" value={summaryNumber(riskEvent, 'allowed_lots')} />
              <Metric label="审计模式" value={summaryBoolean(riskEvent, 'audit_only')} />
            </div>
            <CodeChips title="Risk Codes" values={riskCodes} empty="无风险门代码" tone={riskEvent.status === 'rejected' ? 'red' : 'amber'} />
          </div>
        ) : (
          <EmptyState title="暂无风险门" detail="收到 trade_plan 并完成风险审查后会显示结果。" />
        )}
      </Panel>

      <Panel title="最近决策事件" subtitle="按时间倒序展示 AI、风险门、命令和 EA 回执阶段。" className="xl:col-span-2">
        {events.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="min-w-full text-left text-sm">
              <thead className="text-xs uppercase tracking-[0.2em] text-stone-500">
                <tr>
                  <th className="pb-3">阶段</th>
                  <th className="pb-3">状态</th>
                  <th className="pb-3">决策</th>
                  <th className="pb-3">命令</th>
                  <th className="pb-3">理由码</th>
                  <th className="pb-3">时间</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/5">
                {events.slice(0, 8).map((event) => (
                  <tr key={`${event.id}-${event.stage}`}>
                    <td className="py-4 pr-4 text-stone-100">{event.stage}</td>
                    <td className="py-4 pr-4">
                      <ToneBadge tone={toneForStatus(event.status)}>{event.status}</ToneBadge>
                    </td>
                    <td className="py-4 pr-4 text-stone-300">{event.decision_id}</td>
                    <td className="py-4 pr-4 text-stone-300">{event.related_command_id || '无'}</td>
                    <td className="py-4 pr-4">
                      <div className="flex flex-wrap gap-2">
                        {(event.reason_codes.length > 0 ? event.reason_codes : ['none']).map((code) => (
                          <ToneBadge key={code} tone="neutral">{code}</ToneBadge>
                        ))}
                      </div>
                    </td>
                    <td className="py-4 text-stone-300">{formatTimestamp(event.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState title="暂无决策事件" detail="AI 结果、风险门、命令下发或 EA 回执到达后会显示在这里。" />
        )}
      </Panel>
    </div>
  )
}

type MarketFilterCode = {
  severity: string
  code: string
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-xs uppercase tracking-[0.18em] text-stone-500">{label}</p>
      <p className="mt-2 text-lg font-semibold text-stone-100">{value}</p>
    </div>
  )
}

function MarketFilterChips({ items }: { items: MarketFilterCode[] }) {
  if (items.length === 0) {
    return null
  }

  const groups = groupMarketFilterCodes(items)

  return (
    <div className="rounded-2xl bg-black/20 px-4 py-3">
      <p className="text-xs uppercase tracking-[0.18em] text-stone-500">Market Filters</p>
      <div className="mt-3 space-y-2">
        {groups.map(({ severity, codes }) => (
          <div key={severity} className="flex flex-wrap items-center gap-2">
            <ToneBadge tone={toneForMarketFilterSeverity(severity)}>{severity}</ToneBadge>
            {codes.map((code) => (
              <ToneBadge key={code} tone="neutral">
                {code}
              </ToneBadge>
            ))}
          </div>
        ))}
      </div>
    </div>
  )
}

function CodeChips({
  title,
  values,
  empty,
  tone = 'neutral'
}: {
  title: string
  values: string[]
  empty: string
  tone?: string
}) {
  const items = values.length > 0 ? values : [empty]
  return (
    <div>
      <p className="text-xs uppercase tracking-[0.18em] text-stone-500">{title}</p>
      <div className="mt-2 flex flex-wrap gap-2">
        {items.map((value) => (
          <ToneBadge key={value} tone={values.length > 0 ? tone : 'neutral'}>
            {value}
          </ToneBadge>
        ))}
      </div>
    </div>
  )
}

function collectMarketFilterCodes(data: AccountDetail, tradePlan: Record<string, unknown> | null): MarketFilterCode[] {
  const accountDetail = asRecord(data)
  const aiResult = asRecord(data.ai_result)
  const aiMetadata = asRecord(aiResult?.metadata)
  const tradePlanMetadata = asRecord(tradePlan?.metadata)
  const sources = [
    marketFilterValue(accountDetail),
    marketFilterValue(aiResult),
    marketFilterValue(aiMetadata),
    marketFilterValue(tradePlan),
    marketFilterValue(tradePlanMetadata)
  ]

  return dedupeMarketFilters(sources.flatMap((source) => parseMarketFilterValue(source, 'warning')))
}

function marketFilterValue(record: Record<string, unknown> | null) {
  return valueForKeys(record, ['market_filters', 'marketFilters', 'market_filter', 'marketFilter'])
}

function parseMarketFilterValue(value: unknown, fallbackSeverity: string, depth = 0): MarketFilterCode[] {
  if (depth > 4 || value == null) {
    return []
  }

  const singleCode = textValue(value)
  if (singleCode) {
    return [{ severity: fallbackSeverity, code: singleCode }]
  }

  if (Array.isArray(value)) {
    return value.flatMap((item) => parseMarketFilterValue(item, fallbackSeverity, depth + 1))
  }

  const record = asRecord(value)
  if (!record) {
    return []
  }

  const severity = severityFromMarketFilterRecord(record, fallbackSeverity)
  const directCodes = valuesForKeys(record, ['reason_codes', 'reasonCodes', 'reason_code', 'reasonCode', 'codes', 'code'])
    .flatMap((codeValue) => codesFromValue(codeValue))
    .map((code) => ({ severity, code }))

  const blockingCodes = valuesForKeys(record, ['blocking', 'blockers', 'blocked', 'blocks'])
    .flatMap((codeValue) => parseMarketFilterValue(codeValue, 'blocking', depth + 1))
  const warningCodes = valuesForKeys(record, ['warnings', 'warning', 'warns'])
    .flatMap((codeValue) => parseMarketFilterValue(codeValue, 'warning', depth + 1))
  const nestedCodes = valuesForKeys(record, ['filters', 'items', 'results', 'checks', 'entries'])
    .flatMap((codeValue) => parseMarketFilterValue(codeValue, severity, depth + 1))

  return [...directCodes, ...blockingCodes, ...warningCodes, ...nestedCodes]
}

function codesFromValue(value: unknown): string[] {
  const singleCode = textValue(value)
  if (singleCode) {
    return [singleCode]
  }
  return stringArray(value)
}

function severityFromMarketFilterRecord(record: Record<string, unknown>, fallbackSeverity: string) {
  const directSeverity = normalizeMarketFilterSeverity(
    valueForKeys(record, ['severity', 'level', 'status', 'type', 'category']),
    fallbackSeverity
  )

  if (directSeverity !== fallbackSeverity) {
    return directSeverity
  }
  if (record.blocking === true || record.blocked === true) {
    return 'blocking'
  }
  if (record.warning === true || record.warn === true) {
    return 'warning'
  }
  return directSeverity
}

function normalizeMarketFilterSeverity(value: unknown, fallbackSeverity: string) {
  const severity = textValue(value).trim().toLowerCase()
  if (!severity) {
    return fallbackSeverity
  }

  if (
    ['blocking', 'blocked', 'blocker', 'block', 'deny', 'denied', 'reject', 'rejected', 'critical', 'fatal', 'error'].includes(severity)
  ) {
    return 'blocking'
  }
  if (['warning', 'warnings', 'warn', 'caution', 'soft', 'clamped'].includes(severity)) {
    return 'warning'
  }
  if (['info', 'informational', 'notice', 'pass', 'passed', 'allowed'].includes(severity)) {
    return 'info'
  }
  return severity
}

function valueForKeys(record: Record<string, unknown> | null, keys: string[]) {
  if (!record) {
    return undefined
  }
  return keys.map((key) => record[key]).find((value) => value != null)
}

function valuesForKeys(record: Record<string, unknown>, keys: string[]) {
  return keys.map((key) => record[key]).filter((value) => value != null)
}

function dedupeMarketFilters(items: MarketFilterCode[]) {
  const seen = new Set<string>()
  return items.filter((item) => {
    if (!item.code) {
      return false
    }
    const key = `${item.severity}:${item.code}`
    if (seen.has(key)) {
      return false
    }
    seen.add(key)
    return true
  })
}

function groupMarketFilterCodes(items: MarketFilterCode[]) {
  const order = ['blocking', 'warning', 'info']
  const groups = new Map<string, string[]>()
  items.forEach((item) => {
    groups.set(item.severity, [...(groups.get(item.severity) ?? []), item.code])
  })

  return [...groups.entries()]
    .sort(([left], [right]) => {
      const leftIndex = order.indexOf(left)
      const rightIndex = order.indexOf(right)
      if (leftIndex === -1 && rightIndex === -1) {
        return 0
      }
      if (leftIndex === -1) {
        return 1
      }
      if (rightIndex === -1) {
        return -1
      }
      return leftIndex - rightIndex
    })
    .map(([severity, codes]) => ({ severity, codes }))
}

function toneForMarketFilterSeverity(severity: string) {
  switch (severity) {
    case 'blocking':
      return 'red'
    case 'warning':
      return 'amber'
    case 'info':
      return 'blue'
    default:
      return 'neutral'
  }
}

function positionMetrics(position: AccountDetail['positions'][number]) {
  const slDistance = position.sl > 0 ? Math.abs(position.entry_price - position.sl) : 0
  const direction = position.direction.toUpperCase()
  const profitDistance = direction === 'SELL'
    ? position.entry_price - position.current_price
    : position.current_price - position.entry_price
  const rMultiple = slDistance > 0 ? profitDistance / slDistance : null

  return {
    slDistance: slDistance > 0 ? formatNumber(slDistance) : '无',
    rMultiple: rMultiple == null ? '无' : `${rMultiple.toFixed(2)}R`
  }
}

function toneForStatus(status: string) {
  switch (status) {
    case 'accepted':
    case 'acked':
    case 'delivered':
      return 'green'
    case 'clamped':
    case 'pending':
      return 'amber'
    case 'rejected':
    case 'failed':
      return 'red'
    default:
      return 'neutral'
  }
}

function summaryNumber(event: DecisionEvent, key: string) {
  const value = numberValue(event.summary[key])
  return value == null ? '无' : formatNumber(value)
}

function summaryBoolean(event: DecisionEvent, key: string) {
  const value = event.summary[key]
  if (typeof value !== 'boolean') {
    return '无'
  }
  return value ? '是' : '否'
}

function asRecord(value: unknown): Record<string, unknown> | null {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    return null
  }
  return value as Record<string, unknown>
}

function textValue(value: unknown) {
  return typeof value === 'string' ? value : ''
}

function numberValue(value: unknown) {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function stringArray(value: unknown) {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : []
}
