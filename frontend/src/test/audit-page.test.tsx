import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { AuditPage } from '../pages/audit/AuditPage'

const mockAudit = {
  status: 'OK',
  generated_at: '2026-04-13T08:00:00Z',
  report: {
    ready: false,
    protocol_error_rate: 0,
    signal_drift_rate: 0,
    command_drift_rate: 0,
    last_shadow_event_at: '',
    missing_capabilities: ['shadow_traffic']
  },
  summary: [
    { label: 'Replay Parity', value: 'validated', detail: 'Replay fixture matched baseline', tone: 'green' },
    { label: 'Shadow Drift', value: 'pending', detail: 'Waiting for mirrored production traffic', tone: 'orange' },
    { label: 'Protocol Errors', value: '0.00%', detail: 'No contract mismatches observed in replay mode', tone: 'green' }
  ],
  events: []
}

describe('AuditPage', () => {
  it('renders readiness summary and missing capability list', () => {
    render(<AuditPage initialData={mockAudit} />)

    expect(screen.getByText('Shadow Drift')).toBeInTheDocument()
    expect(screen.getByText('shadow_traffic')).toBeInTheDocument()
    expect(screen.getByText('pending')).toBeInTheDocument()
  })
})
