export function formatDateTime(value: unknown): string {
  if (typeof value !== 'string' || value.trim() === '') {
    return '--'
  }
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return value
  }
  return new Intl.DateTimeFormat('zh-CN', {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(date)
}

export function prettyJson(value: unknown): string {
  return JSON.stringify(value, null, 2)
}

export function toStringArray(input: string): string[] {
  return input
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean)
}
