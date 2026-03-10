function renderValue(value: unknown): string {
  if (value === null || value === undefined || value === '') {
    return '--'
  }
  if (Array.isArray(value)) {
    return value.join(', ')
  }
  if (typeof value === 'object') {
    return JSON.stringify(value)
  }
  return String(value)
}

export function DataTable({
  rows,
  testId,
  columns,
  maxRows = 12,
}: {
  rows: Array<Record<string, unknown>>
  testId?: string
  columns?: string[]
  maxRows?: number
}) {
  const visibleRows = rows.slice(0, maxRows)
  const visibleColumns = columns && columns.length > 0
    ? columns
    : Array.from(new Set(visibleRows.flatMap((row) => Object.keys(row)))).slice(0, 8)

  return (
    <div className="data-table-wrap" data-testid={testId}>
      <table className="data-table">
        <thead>
          <tr>
            {visibleColumns.map((column) => (
              <th key={column}>{column}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {visibleRows.map((row, index) => (
            <tr key={index}>
              {visibleColumns.map((column) => (
                <td key={`${index}-${column}`}>{renderValue(row[column])}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
