import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { OverviewPage } from '../pages/overview/OverviewPage'

const mockOverview = {
  status: 'OK',
  generated_at: '2026-04-13T08:00:00Z',
  cards: [
    { title: 'System Health', value: 'Healthy', detail: 'SQLite + API online', tone: 'green' },
    { title: 'Connected Accounts', value: '1', detail: 'active terminals reporting', tone: 'amber' },
    { title: 'Tradeable Accounts', value: '1', detail: 'market open and trading allowed', tone: 'blue' },
    { title: 'Cutover Health', value: 'Baseline Only', detail: 'Replay validated, shadow diff pending', tone: 'orange' }
  ],
  accounts: [
    {
      account_id: '90011087',
      broker: 'Demo Broker',
      server_name: 'Demo-1',
      connected: true,
      balance: 1000.5,
      equity: 1100.25,
      positions: 1,
      market_open: true,
      is_trade_allowed: true
    }
  ]
}

describe('OverviewPage', () => {
  it('renders overview cards from API payload', async () => {
    render(<OverviewPage initialData={mockOverview} />)
    expect(screen.getByText('Cutover Health')).toBeInTheDocument()
    expect(screen.getByText('Demo Broker')).toBeInTheDocument()
    expect(screen.getByText('Baseline Only')).toBeInTheDocument()
  })
})
