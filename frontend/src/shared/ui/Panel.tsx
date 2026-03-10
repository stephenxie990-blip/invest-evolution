import type { PropsWithChildren, ReactNode } from 'react'

export function Panel({ title, actions, children }: PropsWithChildren<{ title: string; actions?: ReactNode }>) {
  return (
    <section className="panel-card">
      <div className="panel-card__header">
        <h3>{title}</h3>
        {actions ? <div className="panel-card__actions">{actions}</div> : null}
      </div>
      <div className="panel-card__body">{children}</div>
    </section>
  )
}
