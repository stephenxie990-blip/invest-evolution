import { useEffect, useMemo, useState } from 'react'

import { RuntimeEventPayloadSchemas, type KnownRuntimeEventType } from '@/shared/contracts/types'

export type RuntimeEvent = {
  id: string
  type: KnownRuntimeEventType
  receivedAt: string
  payload: unknown
}

export function useEventStream(enabled = true, limit = 100) {
  const [events, setEvents] = useState<RuntimeEvent[]>([])
  const [connected, setConnected] = useState(false)
  const [lastError, setLastError] = useState<string | null>(null)

  useEffect(() => {
    if (!enabled) {
      return
    }

    const source = new EventSource('/api/events')
    const listeners = Object.keys(RuntimeEventPayloadSchemas) as KnownRuntimeEventType[]

    const pushEvent = (event: MessageEvent<string>, type: KnownRuntimeEventType) => {
      try {
        const payload = event.data ? JSON.parse(event.data) : null
        const schema = RuntimeEventPayloadSchemas[type]
        const parsed = schema.safeParse(payload)
        if (!parsed.success) {
          setLastError(`事件契约不匹配: ${type}`)
          return
        }

        setEvents((current) => {
          const next = [{
            id: `${type}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
            type,
            receivedAt: new Date().toISOString(),
            payload: parsed.data,
          }, ...current]
          return next.slice(0, limit)
        })
      } catch {
        setLastError('事件解析失败')
      }
    }

    source.onopen = () => {
      setConnected(true)
      setLastError(null)
    }

    source.onerror = () => {
      setConnected(false)
      setLastError('事件流连接中断，浏览器将自动重试')
    }

    listeners.forEach((type) => {
      source.addEventListener(type, (event) => pushEvent(event as MessageEvent<string>, type))
    })

    return () => {
      source.close()
    }
  }, [enabled, limit])

  return useMemo(() => ({
    connected,
    events,
    lastError,
  }), [connected, events, lastError])
}
