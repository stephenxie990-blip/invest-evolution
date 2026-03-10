export function KeyValueList({ entries }: { entries: Array<{ label: string; value: unknown }> }) {
  return (
    <dl className="key-value-list">
      {entries.map((entry) => (
        <div className="key-value-list__row" key={entry.label}>
          <dt>{entry.label}</dt>
          <dd>{entry.value === null || entry.value === undefined || entry.value === '' ? '--' : String(entry.value)}</dd>
        </div>
      ))}
    </dl>
  )
}
