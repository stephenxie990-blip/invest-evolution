import { useEffect, useMemo, useState } from 'react'

import { useFrontendContract } from '@/shared/api/contracts'
import { type DataDatasetKind, type DataQueryInput, useDataDownload, useDataItems, useDataStatus } from '@/shared/api/data'
import { ApiError } from '@/shared/api/errors'
import { formatDateTime } from '@/shared/lib/format'
import { ErrorState, EmptyState, LoadingState } from '@/shared/ui/AsyncState'
import { DataTable } from '@/shared/ui/DataTable'
import { JsonView } from '@/shared/ui/JsonView'
import { KeyValueList } from '@/shared/ui/KeyValueList'
import { MetricCard } from '@/shared/ui/MetricCard'
import { Panel } from '@/shared/ui/Panel'
import { StatusBadge } from '@/shared/ui/StatusBadge'

type UnknownRecord = Record<string, unknown>

type DataQueryFormState = {
  dataset: DataDatasetKind
  codes: string
  start: string
  end: string
  limit: number
}

const defaultQuery: DataQueryFormState = {
  dataset: 'capital_flow',
  codes: '',
  start: '',
  end: '',
  limit: 200,
}

function asRecord(value: unknown): UnknownRecord | null {
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as UnknownRecord) : null
}

function readString(value: unknown, fallback = '--'): string {
  return typeof value === 'string' && value.trim() ? value : fallback
}

function readNumber(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value
  }
  if (typeof value === 'string') {
    const parsed = Number(value)
    return Number.isFinite(parsed) ? parsed : null
  }
  return null
}


function formatDecimal(value: unknown): string {
  const number = readNumber(value)
  return number === null ? '--' : number.toFixed(1)
}

function toneFromQuality(value: unknown): 'good' | 'warn' | 'danger' | 'neutral' {
  const status = readString(value, 'unknown').toLowerCase()
  if (status === 'healthy') {
    return 'good'
  }
  if (status === 'warning') {
    return 'warn'
  }
  if (status === 'error') {
    return 'danger'
  }
  return 'neutral'
}

function buildSubmittedQuery(draft: DataQueryFormState): DataQueryInput {
  return {
    dataset: draft.dataset,
    codes: draft.codes,
    start: draft.start,
    end: draft.end,
    limit: draft.limit,
  }
}

function isSameQuery(left: DataQueryInput | null, right: DataQueryInput): boolean {
  if (!left) {
    return false
  }
  return left.dataset === right.dataset
    && (left.codes ?? '') === (right.codes ?? '')
    && (left.start ?? '') === (right.start ?? '')
    && (left.end ?? '') === (right.end ?? '')
    && (left.limit ?? 0) === (right.limit ?? 0)
}

export function DataPage() {
  const contract = useFrontendContract()
  const [statusRefreshKey, setStatusRefreshKey] = useState(0)
  const [queryDraft, setQueryDraft] = useState<DataQueryFormState>(defaultQuery)
  const [submittedQuery, setSubmittedQuery] = useState<DataQueryInput | null>(null)
  const [downloadMessage, setDownloadMessage] = useState<string | null>(null)
  const [downloadError, setDownloadError] = useState<string | null>(null)

  const status = useDataStatus(statusRefreshKey)
  const dataItems = useDataItems(submittedQuery)
  const download = useDataDownload()

  useEffect(() => {
    if (!status.data) {
      return
    }
    if (queryDraft.start || queryDraft.end) {
      return
    }
    setQueryDraft((current) => ({
      ...current,
      start: status.data.latest_date,
      end: status.data.latest_date,
    }))
  }, [queryDraft.end, queryDraft.start, status.data])

  const queryRows = useMemo(
    () => dataItems.data?.items.map((item) => ({ ...item })) ?? [],
    [dataItems.data],
  )

  const contractRows = useMemo(
    () => contract.data?.endpoints.filter((item) => String(item.group ?? '') === 'data') ?? [],
    [contract.data],
  )

  const quality = asRecord(status.data?.quality)
  const healthStatus = readString(quality?.health_status, 'unknown')

  const submitQuery = async () => {
    const nextQuery = buildSubmittedQuery(queryDraft)

    if (isSameQuery(submittedQuery, nextQuery)) {
      await dataItems.refetch()
      return
    }

    setSubmittedQuery(nextQuery)
  }

  const triggerDownload = async () => {
    setDownloadMessage(null)
    setDownloadError(null)
    try {
      const payload = await download.mutateAsync()
      setDownloadMessage(payload.message)
    } catch (error) {
      setDownloadError(error instanceof ApiError ? error.message : '后台同步启动失败')
    }
  }

  const previewColumns = useMemo(
    () => Array.from(new Set(queryRows.flatMap((row) => Object.keys(row)))).slice(0, 8),
    [queryRows],
  )

  return (
    <div className="page-grid" data-testid="data-page">
      <section className="metrics-grid">
        <MetricCard label="最新交易日" testId="metric-data-latest-date" value={status.data?.latest_date ?? '--'} hint={status.data?.detail_mode ?? '--'} />
        <MetricCard label="股票数" testId="metric-data-stock-count" value={status.data?.stock_count ?? 0} hint={readString(status.data?.db_path)} />
        <MetricCard label="日线条数" testId="metric-data-kline-count" value={status.data?.kline_count ?? 0} hint={`size ${formatDecimal(status.data?.size_mb)} MB`} />
        <MetricCard label="质量状态" testId="metric-data-quality-status" value={healthStatus} hint={formatDateTime(quality?.latest_audit_at)} />
        <MetricCard label="资金流条数" value={status.data?.capital_flow_count ?? 0} hint="capital_flow" />
        <MetricCard label="60m 条数" value={status.data?.intraday_60m_count ?? 0} hint="intraday_60m" />
      </section>

      <Panel title="数据仓状态与同步">
        <div className="content-stack">
          <div className="button-row">
            <button className="button button--secondary" data-testid="refresh-data-status" onClick={() => setStatusRefreshKey((value) => value + 1)} type="button">
              刷新状态
            </button>
            <button className="button" data-testid="trigger-data-download" disabled={download.isPending} onClick={triggerDownload} type="button">
              {download.isPending ? '启动中...' : '启动后台同步'}
            </button>
          </div>

          {downloadMessage ? <div className="state-block" data-testid="data-download-message">{downloadMessage}</div> : null}
          {downloadError ? <ErrorState error={new ApiError(downloadError)} /> : null}
          {status.isLoading ? <LoadingState label="正在读取数据仓状态..." /> : null}
          {status.error ? <ErrorState error={status.error} /> : null}
          {status.data ? (
            <>
              <div className="status-badge-row">
                <StatusBadge tone={toneFromQuality(quality?.health_status)}>{healthStatus}</StatusBadge>
                <StatusBadge tone={status.data.detail_mode === 'slow' ? 'warn' : 'good'}>{status.data.detail_mode}</StatusBadge>
                <StatusBadge tone="neutral">{`schema ${status.data.schema}`}</StatusBadge>
              </div>
              <div className="split-grid">
                <KeyValueList
                  entries={[
                    { label: 'DB Path', value: status.data.db_path },
                    { label: 'Size MB', value: formatDecimal(status.data.size_mb) },
                    { label: 'Index Count', value: status.data.index_count },
                    { label: 'Index Latest', value: status.data.index_latest_date },
                  ]}
                />
                <KeyValueList
                  entries={[
                    { label: 'Financial Rows', value: status.data.financial_count },
                    { label: 'Factor Rows', value: status.data.factor_count },
                    { label: 'Dragon Tiger Rows', value: status.data.dragon_tiger_count },
                    { label: 'Missing Tables', value: Array.isArray(quality?.missing_tables) ? quality?.missing_tables.join(', ') : quality?.missing_tables },
                  ]}
                />
              </div>
            </>
          ) : null}
        </div>
      </Panel>

      <Panel title="数据查询工作台">
        <div className="content-stack">
          <div className="tab-row">
            <button className={`tab-button${queryDraft.dataset === 'capital_flow' ? ' tab-button--active' : ''}`} data-testid="dataset-capital-flow" onClick={() => setQueryDraft((current) => ({ ...current, dataset: 'capital_flow', limit: 200 }))} type="button">
              capital_flow
            </button>
            <button className={`tab-button${queryDraft.dataset === 'dragon_tiger' ? ' tab-button--active' : ''}`} data-testid="dataset-dragon-tiger" onClick={() => setQueryDraft((current) => ({ ...current, dataset: 'dragon_tiger', limit: 200 }))} type="button">
              dragon_tiger
            </button>
            <button className={`tab-button${queryDraft.dataset === 'intraday_60m' ? ' tab-button--active' : ''}`} data-testid="dataset-intraday-60m" onClick={() => setQueryDraft((current) => ({ ...current, dataset: 'intraday_60m', limit: 500 }))} type="button">
              intraday_60m
            </button>
          </div>

          <div className="form-grid">
            <label className="form-grid__full">
              <span>Codes</span>
              <input className="input" data-testid="query-codes-input" onChange={(event) => setQueryDraft((current) => ({ ...current, codes: event.target.value }))} placeholder="000001.SZ,600519.SH" type="text" value={queryDraft.codes} />
            </label>
            <label>
              <span>Start</span>
              <input className="input" data-testid="query-start-input" onChange={(event) => setQueryDraft((current) => ({ ...current, start: event.target.value }))} placeholder="YYYYMMDD" type="text" value={queryDraft.start} />
            </label>
            <label>
              <span>End</span>
              <input className="input" data-testid="query-end-input" onChange={(event) => setQueryDraft((current) => ({ ...current, end: event.target.value }))} placeholder="YYYYMMDD" type="text" value={queryDraft.end} />
            </label>
            <label>
              <span>Limit</span>
              <input className="input" data-testid="query-limit-input" min={1} onChange={(event) => setQueryDraft((current) => ({ ...current, limit: Number(event.target.value) }))} type="number" value={queryDraft.limit} />
            </label>
          </div>

          <div className="button-row">
            <button className="button" data-testid="run-data-query" onClick={submitQuery} type="button">
              运行查询
            </button>
          </div>
        </div>
      </Panel>

      <Panel title="查询结果预览">
        <div className="content-stack">
          <section className="metrics-grid metrics-grid--compact">
            <MetricCard label="结果条数" testId="metric-query-count" value={dataItems.data?.count ?? 0} hint={submittedQuery?.dataset ?? '未执行'} />
            <MetricCard label="Query Limit" value={submittedQuery?.limit ?? '--'} hint={submittedQuery?.codes || 'all codes'} />
            <MetricCard label="Date Range" value={submittedQuery ? `${submittedQuery.start || '--'} → ${submittedQuery.end || '--'}` : '--'} hint={submittedQuery?.dataset ?? '--'} />
          </section>

          {dataItems.isLoading ? <LoadingState label="正在查询数据集..." /> : null}
          {dataItems.error ? <ErrorState error={dataItems.error} /> : null}
          {!submittedQuery ? <EmptyState title="请选择数据集并执行查询" /> : null}
          {submittedQuery && dataItems.data && queryRows.length === 0 ? <EmptyState title="当前筛选条件下没有数据" /> : null}
          {queryRows.length > 0 ? <DataTable columns={previewColumns} rows={queryRows} testId="data-preview-table" /> : null}
          {dataItems.data ? (
            <section className="detail-section" data-testid="query-json-panel">
              <h4 className="detail-section__title">原始 JSON</h4>
              <JsonView value={dataItems.data} />
            </section>
          ) : null}
        </div>
      </Panel>

      <Panel title="相关契约接口">
        {contract.isLoading ? <LoadingState label="正在读取契约..." /> : null}
        {contract.error ? <ErrorState error={contract.error} /> : null}
        {contractRows.length > 0 ? <JsonView value={contractRows} /> : null}
      </Panel>
    </div>
  )
}
