import { cleanup, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { AccountDetailPage } from '../pages/accounts/AccountDetailPage'

const mockDetail = {
  status: 'OK',
  account: {
    account_id: '90011087',
    balance: 1000.5,
    equity: 1100.25,
    margin: 88.4,
    free_margin: 1011.85,
    currency: 'USD',
    leverage: 500,
    broker: 'Demo Broker',
    server_name: 'Demo-1',
    connected: true
  },
  market: {
    symbol: 'XAUUSD',
    bid: 3335.55,
    ask: 3335.75,
    spread: 0.2,
    time: '08:00:00'
  },
  market_filters: [
    {
      severity: 'blocking',
      reason_codes: ['market.closed']
    }
  ],
  positions: [
    {
      ticket: 123456,
      strategy: 'pullback',
      direction: 'BUY',
      lots: 0.2,
      profit: 25.5,
      pnl_percent: 0.38,
      entry_price: 3330.2,
      current_price: 3335.75,
      sl: 3328,
      tp: 3342.5,
      hold_hours: 5.5,
      comment: 'test trade'
    }
  ],
  indicators: {
    H1: {
      close: 3335.75,
      ema20: 3334.4,
      ema50: 3330.2,
      rsi: 52.1,
      adx: 71.5,
      atr: 2.64,
      macd_hist: -0.82,
      bb_upper: 3341.03,
      bb_middle: 0,
      bb_lower: 3330.8,
      stoch_k: 61.4,
      bars_count: 150
    }
  },
  ai_result: {
    bias: 'bullish',
    confidence: 0.84,
    exit_plan: 'hold',
    trade_plan: {
      decision_id: 'tpv1_dashboard',
      mode: 'approve',
      side: 'buy',
      confidence: 84,
      reason_codes: ['mode.approve', 'side.buy'],
      conflicts: [],
      expires_at: '2026-06-06T09:15:00Z',
      narrative: 'Trend agrees with H1 support',
      metadata: {
        market_filters: {
          warnings: ['spread.wide']
        }
      }
    }
  },
  decision_events: [
    {
      id: 2,
      decision_id: 'tpv1_dashboard',
      account_id: '90011087',
      symbol: 'XAUUSD',
      stage: 'risk_gate',
      status: 'clamped',
      reason_codes: ['lots.clamped'],
      summary: {
        mode: 'approve',
        status: 'clamped',
        allowed_lots: 0.12,
        requested_lots: 0.2
      },
      related_command_id: '',
      created_at: '2026-06-06T08:01:00Z'
    },
    {
      id: 1,
      decision_id: 'tpv1_dashboard',
      account_id: '90011087',
      symbol: 'XAUUSD',
      stage: 'ai_result',
      status: 'accepted',
      reason_codes: ['mode.approve', 'side.buy'],
      summary: {
        mode: 'approve',
        confidence: 84
      },
      related_command_id: '',
      created_at: '2026-06-06T08:00:00Z'
    }
  ]
}

describe('AccountDetailPage', () => {
  afterEach(() => {
    cleanup()
  })

  it('renders account, position and ai result sections', () => {
    render(<AccountDetailPage accountId="90011087" initialData={mockDetail} />)

    expect(screen.getByText('90011087')).toBeInTheDocument()
    expect(screen.getByText('pullback')).toBeInTheDocument()
    expect(screen.getByText(/bullish/i)).toBeInTheDocument()
  })

  it('shows trader cockpit decision summary, risk gate and position metrics', () => {
    render(<AccountDetailPage accountId="90011087" initialData={mockDetail} />)

    expect(screen.getByText('决策摘要')).toBeInTheDocument()
    expect(screen.getAllByText('tpv1_dashboard').length).toBeGreaterThan(0)
    expect(screen.getByText('approve / buy')).toBeInTheDocument()
    expect(screen.getAllByText('lots.clamped').length).toBeGreaterThan(0)
    expect(screen.getByText('风险门')).toBeInTheDocument()
    expect(screen.getByText('最近决策事件')).toBeInTheDocument()
    expect(screen.getByText('risk_gate')).toBeInTheDocument()
    expect(screen.getByText('SL距离')).toBeInTheDocument()
    expect(screen.getByText('R倍数')).toBeInTheDocument()
    expect(screen.getByText('2.52R')).toBeInTheDocument()
  })

  it('shows market filter reason codes and severities before the AI narrative', () => {
    render(<AccountDetailPage accountId="90011087" initialData={mockDetail} />)

    const decisionPanel = screen.getByText('决策摘要').closest('section')
    expect(decisionPanel).not.toBeNull()
    const panel = within(decisionPanel as HTMLElement)

    expect(panel.getByText('Market Filters')).toBeInTheDocument()
    expect(panel.getByText('blocking')).toBeInTheDocument()
    expect(panel.getByText('market.closed')).toBeInTheDocument()
    expect(panel.getByText('warning')).toBeInTheDocument()
    expect(panel.getByText('spread.wide')).toBeInTheDocument()

    const firstCode = panel.getByText('market.closed')
    const narrative = panel.getByText('Trend agrees with H1 support')
    expect(firstCode.compareDocumentPosition(narrative) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })
})
