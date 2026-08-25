import { buildRealtimeURL, type DashboardEvent } from './api'

export type RealtimeStatus = 'connecting' | 'connected' | 'disconnected'

/**
 * 实时事件通道(WebSocket,替代原 EventSource/SSE)。
 *
 * - 连接 /api/v1/ws/events?token=...(浏览器 WebSocket 无法携带自定义 header,
 *   与管理 API 的 ?token= 查询参数鉴权一致)
 * - 每帧 JSON:除 {"type":"ping"} 心跳帧外,其余按 DashboardEvent 分发
 * - 断线自动重连(指数退避,上限 15s,与 EventSource 的自动重连语义对齐)
 *
 * 返回清理函数:关闭 socket 并停止重连。
 */
export function connectEventStream(
  token: string,
  onEvent: (event: DashboardEvent) => void,
  onStatus?: (status: RealtimeStatus) => void
): () => void {
  if (!token || typeof window === 'undefined' || typeof WebSocket === 'undefined') {
    return () => undefined
  }

  let socket: WebSocket | null = null
  let closed = false
  let retry = 0
  let retryTimer: number | null = null

  const setStatus = (status: RealtimeStatus) => onStatus?.(status)

  const connect = () => {
    if (closed) {
      return
    }
    setStatus('connecting')
    socket = new WebSocket(buildRealtimeURL(token))

    socket.onopen = () => {
      retry = 0
      setStatus('connected')
    }
    socket.onmessage = (message: MessageEvent<string>) => {
      try {
        const frame = JSON.parse(message.data) as { type?: string } & Partial<DashboardEvent>
        if (frame.type === 'ping' || !frame.event_type) {
          return
        }
        onEvent(frame as DashboardEvent)
      } catch {}
    }
    socket.onerror = () => {
      socket?.close()
    }
    socket.onclose = () => {
      if (closed) {
        return
      }
      setStatus('disconnected')
      const delay = Math.min(1000 * 2 ** retry, 15000)
      retry += 1
      retryTimer = window.setTimeout(connect, delay)
    }
  }

  connect()

  return () => {
    closed = true
    if (retryTimer !== null) {
      window.clearTimeout(retryTimer)
    }
    socket?.close()
  }
}
