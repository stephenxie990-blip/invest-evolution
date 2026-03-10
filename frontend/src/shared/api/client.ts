import { z } from 'zod'

import { ApiError, normalizeApiError } from '@/shared/api/errors'

const JSON_HEADERS = {
  'Content-Type': 'application/json',
}

export type ApiRequestOptions = {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'
  body?: unknown
  query?: Record<string, string | number | boolean | undefined | null>
  schema?: z.ZodTypeAny
  signal?: AbortSignal
  timeoutMs?: number
}

function buildUrl(path: string, query?: ApiRequestOptions['query']): string {
  if (!query) {
    return path
  }
  const url = new URL(path, window.location.origin)
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null || value === '') {
      continue
    }
    url.searchParams.set(key, String(value))
  }
  return `${url.pathname}${url.search}`
}

export async function apiRequest<T>(path: string, options: ApiRequestOptions = {}): Promise<T> {
  const controller = new AbortController()
  const timeoutMs = options.timeoutMs ?? 30_000
  const timeout = window.setTimeout(() => controller.abort(), timeoutMs)

  try {
    const response = await fetch(buildUrl(path, options.query), {
      method: options.method ?? 'GET',
      headers: JSON_HEADERS,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      signal: options.signal ?? controller.signal,
    })

    const text = await response.text()
    const payload = text ? JSON.parse(text) : null

    if (!response.ok) {
      throw normalizeApiError(payload, response.status)
    }

    if (options.schema) {
      const result = options.schema.safeParse(payload)
      if (!result.success) {
        throw new ApiError('接口返回与契约不匹配', response.status, 'schema_validation_failed', result.error.flatten())
      }
      return result.data as T
    }

    return payload as T
  } catch (error) {
    if (error instanceof ApiError) {
      throw error
    }
    if (error instanceof DOMException && error.name === 'AbortError') {
      if (options.signal?.aborted) {
        throw new ApiError('请求已取消', 499, 'request_aborted')
      }
      throw new ApiError('请求超时，请稍后重试', 408)
    }
    throw normalizeApiError(error)
  } finally {
    window.clearTimeout(timeout)
  }
}
