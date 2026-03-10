export function StatusBadge({ tone = 'neutral', children }: { tone?: 'neutral' | 'good' | 'warn' | 'danger'; children: string }) {
  return <span className={`status-badge status-badge--${tone}`}>{children}</span>
}
