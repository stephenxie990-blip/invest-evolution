import type { ReactNode } from 'react'

import { ApiError } from '@/shared/api/errors'

export function LoadingState({ label = '加载中...' }: { label?: string }) {
  return <div className="state-block">{label}</div>
}

export function ErrorState({ error, action }: { error: unknown; action?: ReactNode }) {
  const message = error instanceof ApiError ? error.message : '未知错误'
  return (
    <div className="state-block state-block--danger">
      <div>{message}</div>
      {action ? <div className="state-block__action">{action}</div> : null}
    </div>
  )
}

export function EmptyState({ title }: { title: string }) {
  return <div className="state-block state-block--muted">{title}</div>
}
