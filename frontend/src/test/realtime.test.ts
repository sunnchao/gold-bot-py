import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { buildRealtimeURL, type DashboardEvent } from '../lib/api'
import { connectEventStream, type RealtimeStatus } from '../lib/events'

describe('buildRealtimeURL', () => {
  it('builds a ws:// URL with the token in the query string', () => {
    const url = new URL(buildRealtimeURL('admin-token'))
    expect(url.protocol).toBe('ws:')
    expect(url.pathname).toBe('/api/v1/ws/events')
    expect(url.searchParams.get('token')).toBe('admin-token')
  })

  it('omits the token when absent', () => {
    const url = new URL(buildRealtimeURL(''))
    expect(url.searchParams.has('token')).toBe(false)
  })
})

type Handler = ((event: unknown) => void) | null

class FakeWebSocket {
  static instances: FakeWebSocket[] = []

  url: string
  onopen: Handler = null
  onmessage: Handler = null
  onclose: Handler = null
  onerror: Handler = null
  closed = false

  constructor(url: string) {
    this.url = url
    FakeWebSocket.instances.push(this)
  }

  close() {
    this.closed = true
  }

  // --- 测试驱动桩方法 ---
  emitOpen() {
    this.onopen?.({})
  }

  emitMessage(data: string) {
    this.onmessage?.({ data })
  }

  emitClose() {
    this.onclose?.({})
  }
}

describe('connectEventStream (WebSocket)', () => {
  beforeEach(() => {
    FakeWebSocket.instances = []
    vi.stubGlobal('WebSocket', FakeWebSocket)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.useRealTimers()
  })

  it('opens the realtime WebSocket and forwards event frames', () => {
    const events: DashboardEvent[] = []
    const statuses: RealtimeStatus[] = []
    const cleanup = connectEventStream('admin-token', (event) => events.push(event), (status) =>
      statuses.push(status)
    )

    expect(FakeWebSocket.instances).toHaveLength(1)
    const socket = FakeWebSocket.instances[0]
    expect(socket.url).toContain('/api/v1/ws/events?token=admin-token')

    socket.emitOpen()
    expect(statuses).toContain('connected')

    const event: DashboardEvent = {
      event_id: 'evt_1',
      event_type: 'account_update',
      account_id: '90011087',
      source: 'ea',
      timestamp: '2026-04-13T16:00:00+08:00',
      payload: { balance: 1200 }
    }
    socket.emitMessage(JSON.stringify(event))
    expect(events).toEqual([event])

    // 心跳帧不进入事件列表
    socket.emitMessage(JSON.stringify({ type: 'ping' }))
    expect(events).toHaveLength(1)

    cleanup()
    expect(socket.closed).toBe(true)
  })

  it('reconnects after the socket closes unless cleaned up', () => {
    vi.useFakeTimers()
    const cleanup = connectEventStream('admin-token', () => undefined)

    const first = FakeWebSocket.instances[0]
    first.emitOpen()
    first.emitClose()

    vi.advanceTimersByTime(1000)
    expect(FakeWebSocket.instances).toHaveLength(2)

    cleanup()
    FakeWebSocket.instances[1]?.emitClose()
    // 清理后不再重连
    expect(FakeWebSocket.instances).toHaveLength(2)
  })

  it('returns a noop cleanup when no token is available', () => {
    const cleanup = connectEventStream('', () => undefined)
    cleanup()
    expect(FakeWebSocket.instances).toHaveLength(0)
  })
})
