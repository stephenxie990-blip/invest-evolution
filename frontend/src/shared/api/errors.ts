import { DataSourceUnavailableErrorSchema, type DataSourceUnavailableError } from '@/shared/contracts/types'

export class ApiError extends Error {
  readonly status: number
  readonly code?: string
  readonly detail?: unknown

  constructor(message: string, status = 500, code?: string, detail?: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.detail = detail
  }
}

export function isDataSourceUnavailablePayload(payload: unknown): payload is DataSourceUnavailableError {
  return DataSourceUnavailableErrorSchema.safeParse(payload).success
}

export function isDataSourceUnavailableApiError(error: unknown): error is ApiError & { detail: DataSourceUnavailableError } {
  return error instanceof ApiError
    && (error.code === 'data_source_unavailable' || isDataSourceUnavailablePayload(error.detail))
}

export function normalizeApiError(payload: unknown, fallbackStatus = 500): ApiError {
  if (payload && typeof payload === 'object') {
    const record = payload as Record<string, unknown>
    if (typeof record.error === 'string') {
      const status = typeof record.statusCode === 'number' ? record.statusCode : fallbackStatus
      const code = typeof record.code === 'string'
        ? record.code
        : typeof record.error_code === 'string'
          ? record.error_code
          : undefined
      return new ApiError(record.error, status, code, payload)
    }
    if (record.status === 'error' && typeof record.error === 'string') {
      const code = typeof record.code === 'string'
        ? record.code
        : typeof record.error_code === 'string'
          ? record.error_code
          : undefined
      return new ApiError(record.error, fallbackStatus, code, payload)
    }
  }
  if (payload instanceof Error) {
    return new ApiError(payload.message, fallbackStatus, undefined, payload)
  }
  return new ApiError('未知接口错误', fallbackStatus, undefined, payload)
}
