import { buildStreamURL, type DashboardEvent } from './api'

export function connectEventStream(token: string, onEvent: (event: DashboardEvent) => void) {
  if (!token || typeof window === 'undefined' || typeof EventSource === 'undefined') {
    return () => undefined
  }

  const stream = new EventSource(buildStreamURL(token))
  stream.onmessage = (message) => {
    try {
      onEvent(JSON.parse(message.data) as DashboardEvent)
    } catch {}
  }
  return () => {
    stream.close()
  }
}
