import { prettyJson } from '@/shared/lib/format'

export function JsonView({ value }: { value: unknown }) {
  return <pre className="json-view">{prettyJson(value)}</pre>
}
