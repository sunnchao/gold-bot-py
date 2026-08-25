export type Tone = 'green' | 'amber' | 'blue' | 'orange' | 'red' | 'neutral'

export type OverviewCard = {
  title: string
  value: string
  detail: string
  tone: Tone | string
}

export type OverviewAccount = {
  account_id: string
  broker: string
  server_name: string
  connected: boolean
  balance: number
  equity: number
  positions: number
  market_open: boolean
  is_trade_allowed: boolean
}

export type OverviewResponse = {
  status: string
  generated_at: string
  cards: OverviewCard[]
  accounts: OverviewAccount[]
}

export type AccountsResponse = {
  status: string
  accounts: OverviewAccount[]
}

export type DecisionEvent = {
  id: number
  decision_id: string
  account_id: string
  symbol: string
  stage: string
  status: string
  reason_codes: string[]
  summary: Record<string, unknown>
  related_command_id: string
  created_at: string
}

export type AccountDetail = {
  status: string
  account: {
    account_id: string
    equity: number
    balance: number
    margin: number
    free_margin: number
    currency: string
    leverage: number
    broker: string
    server_name: string
    connected: boolean
  }
  market: {
    symbol: string
    bid: number
    ask: number
    spread: number
    time: string
  }
  positions: Array<{
    ticket: number
    strategy: string
    magic?: number
    direction: string
    entry_price: number
    current_price: number
    lots: number
    profit: number
    pnl_percent: number
    sl: number
    tp: number
    hold_seconds?: number
    hold_hours: number
    comment: string
  }>
  indicators: Record<
    string,
    | {
        close: number
        open?: number
        high?: number
        low?: number
        ema20: number
        ema50: number
        ema200?: number
        rsi: number
        adx: number
        atr: number
        macd?: number
        macd_signal?: number
        macd_hist: number
        bb_upper: number
        bb_middle: number
        bb_lower: number
        stoch_k: number
        stoch_d?: number
        vol_sma?: number
        fib_236?: number
        fib_382?: number
        fib_500?: number
        fib_618?: number
        fib_786?: number
        pp?: number
        r1?: number
        r2?: number
        s1?: number
        s2?: number
        bars_count: number
      }
    | null
  >
  ai_result: Record<string, unknown>
  market_filters?: unknown
  decision_events?: DecisionEvent[]
}

export type CutoverReport = {
  ready: boolean
  protocol_error_rate: number
  signal_drift_rate: number
  command_drift_rate: number
  last_shadow_event_at: string
  missing_capabilities: string[]
}

export type AuditSummaryItem = {
  label: string
  value: string
  detail: string
  tone: Tone | string
}

export type DashboardEvent = {
  event_id: string
  event_type: string
  account_id?: string
  source: string
  timestamp: string
  payload: Record<string, unknown> | null
}

export type AuditResponse = {
  status: string
  generated_at: string
  report: CutoverReport
  summary: AuditSummaryItem[]
  events: DashboardEvent[]
}

const tokenStorageKey = 'gold-bot.dashboard-token'

export function resolveDashboardToken(): string {
  if (typeof window === 'undefined') {
    return ''
  }

  const storage = window.localStorage
  const url = new URL(window.location.href)
  const fromQuery = url.searchParams.get('token')?.trim() ?? ''
  if (fromQuery) {
    if (storage && typeof storage.setItem === 'function') {
      storage.setItem(tokenStorageKey, fromQuery)
    }
    return fromQuery
  }

  if (storage && typeof storage.getItem === 'function') {
    return storage.getItem(tokenStorageKey)?.trim() ?? ''
  }
  return ''
}

export function buildStreamURL(token: string): string {
  if (typeof window === 'undefined') {
    return ''
  }
  const url = new URL('/api/v1/events/stream', window.location.origin)
  if (token) {
    url.searchParams.set('token', token)
  }
  return url.toString()
}

async function requestJSON<T>(path: string): Promise<T> {
  const token = resolveDashboardToken()
  const response = await fetch(path, {
    cache: 'no-store',
    headers: token ? { 'X-API-Token': token } : {}
  })

  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`
    try {
      const body = (await response.json()) as { message?: string }
      if (body.message) {
        message = body.message
      }
    } catch {}

    if (!token && (response.status === 401 || response.status === 403)) {
      message = '访问被拒绝，请使用 ?token=... 参数打开面板。'
    }
    throw new Error(message)
  }

  return (await response.json()) as T
}

export function getOverview() {
  return requestJSON<OverviewResponse>('/api/v1/overview')
}

export function getAccounts() {
  return requestJSON<AccountsResponse>('/api/v1/accounts')
}

export function getAccountDetail(accountId: string) {
  return requestJSON<AccountDetail>(`/api/v1/accounts/${encodeURIComponent(accountId)}`)
}

export function getAudit() {
  return requestJSON<AuditResponse>('/api/v1/audit')
}
