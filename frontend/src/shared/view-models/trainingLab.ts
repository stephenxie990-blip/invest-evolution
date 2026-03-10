import { type ApiError, isDataSourceUnavailableApiError, isDataSourceUnavailablePayload } from '@/shared/api/errors'
import type { DataSourceUnavailableError } from '@/shared/contracts/types'
import type { RuntimeEvent } from '@/shared/realtime/events'

type UnknownRecord = Record<string, unknown>

type EventCategory = 'speech' | 'logs' | 'cycle' | 'agent' | 'other'

export type TrainingMode = 'live' | 'mock' | 'offline' | 'online' | 'degraded' | 'dry_run'

export type TrainingEventFilterState = {
  category: 'all' | EventCategory
  cycleId: string
  stage: string
  agent: string
  kind: string
}

export type TrainingAgentSnapshot = {
  agent: string
  status: string
  message: string
  stage: string
  progressPct: number | null
  cycleId: string
  updatedAt: string
  thinking: string
}

export type CycleStatusViewModel = {
  cycleId: string
  cutoffDate: string
  status: string
  requestedDataMode: string
  effectiveDataMode: string
  llmMode: string
  degraded: boolean
  degradeReason: string
  returnPct: string
}

export type TimelineEntry = {
  id: string
  category: EventCategory
  type: string
  title: string
  subtitle: string
  detail: string
  cycleId: string
  stage: string
  agent: string
  kind: string
  modes: TrainingMode[]
  payload: unknown
  receivedAt: string
}

export type TrainingArtifactEntry = {
  key: string
  label: string
  value: string
}

export type StrategyDiffViewModel = {
  cycleId: string
  source: string
  notes: string
  changes: Array<{ path: string; value: string }>
}

export type TrainingResultViewModel = {
  id: string
  status: 'ok' | 'no_data' | 'error' | 'unknown'
  cycleId: string
  cutoffDate: string
  requestedDataMode: string
  effectiveDataMode: string
  llmMode: string
  degraded: boolean
  degradeReason: string
  returnPct: string
  selectedCount: string
  tradeCount: string
  benchmarkPassed: boolean | null
  reviewApplied: boolean | null
  verdict: string
  promotionVerdict: string
  selectionMode: string
  stage: string
  reason: string
  selectedStocks: string[]
  errorMessage: string
  errorCode: string
  suggestions: string[]
  diagnosticsIssues: string[]
  diagnosticsSuggestions: string[]
  availableSources: Record<string, boolean> | null
  artifacts: TrainingArtifactEntry[]
  strategyDiffs: StrategyDiffViewModel[]
  specialError: DataSourceUnavailableError | null
  raw: UnknownRecord
}

export type TrainingActionErrorViewModel = {
  title: string
  subtitle: string
  suggestions: string[]
  diagnosticsIssues: string[]
  diagnosticsSuggestions: string[]
  availableSources: Record<string, boolean> | null
  requestedDataMode: string
  allowMockFallback: boolean | null
  raw: DataSourceUnavailableError
}

function asRecord(value: unknown): UnknownRecord | null {
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as UnknownRecord) : null
}

function asRecords(value: unknown): UnknownRecord[] {
  return Array.isArray(value)
    ? value.filter((item): item is UnknownRecord => Boolean(asRecord(item))).map((item) => item as UnknownRecord)
    : []
}

function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : []
}

function toStrings(value: unknown): string[] {
  return asArray(value).map((item) => String(item)).filter(Boolean)
}

function readString(value: unknown, fallback = '--'): string {
  return typeof value === 'string' && value.trim() ? value : fallback
}

function readOptionalString(value: unknown): string {
  return typeof value === 'string' && value.trim() ? value : ''
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

function formatPercent(value: unknown): string {
  const number = readNumber(value)
  if (number === null) {
    return '--'
  }
  return `${number >= 0 ? '+' : ''}${number.toFixed(2)}%`
}

function formatValue(value: unknown): string {
  if (value === null || value === undefined || value === '') {
    return '--'
  }
  if (Array.isArray(value)) {
    return value.map((item) => formatValue(item)).join(', ')
  }
  if (typeof value === 'object') {
    return JSON.stringify(value)
  }
  return String(value)
}

function flattenChange(value: unknown, prefix = ''): Array<{ path: string; value: string }> {
  const record = asRecord(value)
  if (!record) {
    return prefix ? [{ path: prefix, value: formatValue(value) }] : []
  }

  return Object.entries(record).flatMap(([key, nested]) => {
    const path = prefix ? `${prefix}.${key}` : key
    const nestedRecord = asRecord(nested)
    if (nestedRecord) {
      return flattenChange(nestedRecord, path)
    }
    return [{ path, value: formatValue(nested) }]
  })
}

function sourceLabelFromEvent(event: UnknownRecord): string {
  const trigger = readOptionalString(event.trigger)
  const stage = readOptionalString(event.stage)
  const notes = `${trigger} ${stage}`.toLowerCase()
  if (notes.includes('review')) {
    return 'review_meeting'
  }
  if (notes.includes('loss') || notes.includes('opt')) {
    return 'loss_optimization'
  }
  if (stage) {
    return stage
  }
  if (trigger) {
    return trigger
  }
  return 'optimization'
}

function collectArtifacts(record: UnknownRecord): TrainingArtifactEntry[] {
  const labels: Record<string, string> = {
    cycle_result_path: 'Cycle Result',
    selection_meeting_json_path: 'Selection Meeting (JSON)',
    selection_meeting_markdown_path: 'Selection Meeting (Markdown)',
    review_meeting_json_path: 'Review Meeting (JSON)',
    review_meeting_markdown_path: 'Review Meeting (Markdown)',
    evaluation_path: 'Evaluation',
    optimization_events_path: 'Optimization Events',
    config_snapshot_path: 'Config Snapshot',
  }

  const artifacts = asRecord(record.artifacts) ?? {}
  const merged = {
    ...Object.fromEntries(Object.entries(record).filter(([key, value]) => key.endsWith('_path') && typeof value === 'string')),
    ...artifacts,
  }

  return Object.entries(merged)
    .filter(([, value]) => typeof value === 'string' && value)
    .map(([key, value]) => ({
      key,
      label: labels[key] ?? key,
      value: String(value),
    }))
}

function detectSpecialError(row: UnknownRecord): DataSourceUnavailableError | null {
  const direct = row.error_code ? row : asRecord(row.error_payload)
  if (direct && isDataSourceUnavailablePayload(direct)) {
    return direct
  }
  return null
}

function extractModeTags(record: UnknownRecord): TrainingMode[] {
  const tags: TrainingMode[] = []
  const requested = readOptionalString(record.requested_data_mode)
  const effective = readOptionalString(record.effective_data_mode)
  const llmMode = readOptionalString(record.llm_mode)
  if (requested === 'live') tags.push('live')
  if (requested === 'mock') tags.push('mock')
  if (effective === 'offline') tags.push('offline')
  if (effective === 'online') tags.push('online')
  if (llmMode === 'dry_run') tags.push('dry_run')
  if (record.degraded === true) tags.push('degraded')
  return Array.from(new Set(tags))
}

export function buildTrainingResultViewModels(results: unknown[]): TrainingResultViewModel[] {
  return asRecords(results).map((row, index) => {
    const status = readString(row.status, 'unknown') as TrainingResultViewModel['status']
    const specialError = detectSpecialError(row)
    const offlineDiagnostics = asRecord(row.offline_diagnostics) ?? asRecord(asRecord(row.error_payload)?.offline_diagnostics) ?? {}
    const availableSources = asRecord(row.available_sources) ?? asRecord(asRecord(row.error_payload)?.available_sources)
    const optimizationEvents = asRecords(row.optimization_events)
    const strategyDiffs = optimizationEvents
      .filter((event) => asRecord(event.applied_change))
      .map((event) => ({
        cycleId: readString(event.cycle_id ?? row.cycle_id, `#${index + 1}`),
        source: sourceLabelFromEvent(event),
        notes: readOptionalString(event.notes),
        changes: flattenChange(event.applied_change),
      }))
      .filter((event) => event.changes.length > 0)

    return {
      id: `${readString(row.cycle_id, `#${index + 1}`)}-${index}`,
      status,
      cycleId: readString(row.cycle_id, `#${index + 1}`),
      cutoffDate: readString(row.cutoff_date),
      requestedDataMode: readString(row.requested_data_mode ?? specialError?.requested_data_mode),
      effectiveDataMode: readString(
        row.effective_data_mode
          ?? row.data_mode
          ?? (specialError ? 'unavailable' : undefined),
      ),
      llmMode: readString(row.llm_mode),
      degraded: Boolean(row.degraded),
      degradeReason: readString(row.degrade_reason),
      returnPct: formatPercent(row.return_pct),
      selectedCount: formatValue(row.selected_count),
      tradeCount: formatValue(row.trade_count),
      benchmarkPassed: typeof row.benchmark_passed === 'boolean' ? row.benchmark_passed : null,
      reviewApplied: typeof row.review_applied === 'boolean' ? row.review_applied : null,
      verdict: readString(row.verdict),
      promotionVerdict: readString(row.promotion_verdict),
      selectionMode: readString(row.selection_mode),
      stage: readString(row.stage),
      reason: readString(row.reason),
      selectedStocks: toStrings(row.selected_stocks),
      errorMessage: readString(row.error ?? asRecord(row.error_payload)?.error, ''),
      errorCode: readString(row.error_code ?? asRecord(row.error_payload)?.error_code, ''),
      suggestions: toStrings(row.suggestions ?? asRecord(row.error_payload)?.suggestions),
      diagnosticsIssues: toStrings(offlineDiagnostics.issues),
      diagnosticsSuggestions: toStrings(offlineDiagnostics.suggestions),
      availableSources: availableSources ? {
        offline: Boolean(availableSources.offline),
        online: Boolean(availableSources.online),
        mock: Boolean(availableSources.mock),
      } : null,
      artifacts: collectArtifacts(row),
      strategyDiffs,
      specialError,
      raw: row,
    }
  })
}

export function buildTrainingActionErrorViewModel(error: ApiError | null | undefined): TrainingActionErrorViewModel | null {
  if (!error || !isDataSourceUnavailableApiError(error) || !isDataSourceUnavailablePayload(error.detail)) {
    return null
  }

  const detail = error.detail
  const offlineDiagnostics = asRecord(detail.offline_diagnostics) ?? {}
  return {
    title: '真实训练数据不可用',
    subtitle: '本次请求未显式启用 mock，系统已按契约中止，而不是回退到假数据。',
    suggestions: toStrings(detail.suggestions),
    diagnosticsIssues: toStrings(offlineDiagnostics.issues),
    diagnosticsSuggestions: toStrings(offlineDiagnostics.suggestions),
    availableSources: {
      offline: Boolean(detail.available_sources.offline),
      online: Boolean(detail.available_sources.online),
      mock: Boolean(detail.available_sources.mock),
    },
    requestedDataMode: detail.requested_data_mode,
    allowMockFallback: detail.allow_mock_fallback,
    raw: detail,
  }
}

function eventCategory(type: string): EventCategory {
  if (type === 'meeting_speech') return 'speech'
  if (type === 'module_log') return 'logs'
  if (type === 'cycle_start' || type === 'cycle_complete' || type === 'cycle_skipped') return 'cycle'
  if (type === 'agent_status' || type === 'agent_progress') return 'agent'
  return 'other'
}

export function buildTimelineEntries(events: RuntimeEvent[]): TimelineEntry[] {
  return events.map((event) => {
    const payload = asRecord(event.payload) ?? {}
    const type = event.type
    const category = eventCategory(type)
    const cycleId = payload.cycle_id !== undefined ? String(payload.cycle_id) : ''
    const stage = readOptionalString(payload.stage)
    const agent = readOptionalString(payload.agent ?? payload.speaker)
    const kind = readOptionalString(payload.kind)

    if (type === 'meeting_speech') {
      return {
        id: event.id,
        category,
        type,
        title: `${readString(payload.meeting)} · ${readString(payload.speaker ?? payload.agent)}`,
        subtitle: readString(payload.role),
        detail: readString(payload.speech),
        cycleId,
        stage,
        agent,
        kind,
        modes: extractModeTags(payload),
        payload: event.payload,
        receivedAt: event.receivedAt,
      }
    }

    if (type === 'module_log') {
      return {
        id: event.id,
        category,
        type,
        title: readString(payload.title),
        subtitle: `${readString(payload.module)} / ${readString(payload.level)}`,
        detail: readString(payload.message),
        cycleId,
        stage,
        agent,
        kind,
        modes: extractModeTags(payload),
        payload: event.payload,
        receivedAt: event.receivedAt,
      }
    }

    if (type === 'agent_status' || type === 'agent_progress') {
      return {
        id: event.id,
        category,
        type,
        title: `${readString(payload.agent)} · ${readString(payload.status)}`,
        subtitle: readString(payload.stage),
        detail: readString(payload.message),
        cycleId,
        stage,
        agent: readString(payload.agent),
        kind,
        modes: extractModeTags(payload),
        payload: event.payload,
        receivedAt: event.receivedAt,
      }
    }

    if (type === 'cycle_complete') {
      return {
        id: event.id,
        category,
        type,
        title: `Cycle ${cycleId} 完成`,
        subtitle: `${formatPercent(payload.return_pct)} / ${readString(payload.effective_data_mode)}`,
        detail: readString(payload.degrade_reason),
        cycleId,
        stage,
        agent,
        kind,
        modes: extractModeTags(payload),
        payload: event.payload,
        receivedAt: event.receivedAt,
      }
    }

    if (type === 'cycle_start') {
      return {
        id: event.id,
        category,
        type,
        title: `Cycle ${cycleId} 开始`,
        subtitle: `${readString(payload.cutoff_date)} / ${readString(payload.requested_data_mode)}`,
        detail: readString(payload.phase),
        cycleId,
        stage: readString(payload.phase),
        agent,
        kind,
        modes: extractModeTags(payload),
        payload: event.payload,
        receivedAt: event.receivedAt,
      }
    }

    return {
      id: event.id,
      category,
      type,
      title: type,
      subtitle: cycleId ? `Cycle ${cycleId}` : '--',
      detail: readString(payload.message ?? payload.reason),
      cycleId,
      stage,
      agent,
      kind,
      modes: extractModeTags(payload),
      payload: event.payload,
      receivedAt: event.receivedAt,
    }
  })
}

export function filterTimelineEntries(entries: TimelineEntry[], filter: TrainingEventFilterState): TimelineEntry[] {
  return entries.filter((entry) => {
    if (filter.category !== 'all' && entry.category !== filter.category) {
      return false
    }
    if (filter.cycleId && entry.cycleId !== filter.cycleId) {
      return false
    }
    if (filter.stage && entry.stage !== filter.stage) {
      return false
    }
    if (filter.agent && entry.agent !== filter.agent) {
      return false
    }
    if (filter.kind && entry.kind !== filter.kind) {
      return false
    }
    return true
  })
}

export function buildFilterOptions(entries: TimelineEntry[]) {
  return {
    cycles: Array.from(new Set(entries.map((entry) => entry.cycleId).filter(Boolean))),
    stages: Array.from(new Set(entries.map((entry) => entry.stage).filter(Boolean))),
    agents: Array.from(new Set(entries.map((entry) => entry.agent).filter(Boolean))),
    kinds: Array.from(new Set(entries.map((entry) => entry.kind).filter(Boolean))),
  }
}

export function buildAgentSnapshots(entries: TimelineEntry[]): TrainingAgentSnapshot[] {
  const map = new Map<string, TrainingAgentSnapshot>()
  entries
    .filter((entry) => entry.category === 'agent')
    .forEach((entry) => {
      const payload = asRecord(entry.payload) ?? {}
      map.set(entry.agent || entry.title, {
        agent: readString(payload.agent),
        status: readString(payload.status),
        message: readString(payload.message),
        stage: readString(payload.stage),
        progressPct: readNumber(payload.progress_pct),
        cycleId: entry.cycleId,
        updatedAt: entry.receivedAt,
        thinking: readString(payload.thinking, ''),
      })
    })
  return Array.from(map.values())
}

export function buildCycleStatusRows(entries: TimelineEntry[]): CycleStatusViewModel[] {
  const rows = new Map<string, CycleStatusViewModel>()
  entries
    .filter((entry) => entry.category === 'cycle')
    .forEach((entry) => {
      const payload = asRecord(entry.payload) ?? {}
      const cycleId = entry.cycleId || readString(payload.cycle_id)
      const current = rows.get(cycleId) ?? {
        cycleId,
        cutoffDate: readString(payload.cutoff_date),
        status: entry.type === 'cycle_start' ? 'running' : entry.type === 'cycle_complete' ? 'completed' : 'no_data',
        requestedDataMode: readString(payload.requested_data_mode),
        effectiveDataMode: readString(payload.effective_data_mode),
        llmMode: readString(payload.llm_mode),
        degraded: Boolean(payload.degraded),
        degradeReason: readString(payload.degrade_reason),
        returnPct: formatPercent(payload.return_pct),
      }
      rows.set(cycleId, {
        ...current,
        cutoffDate: readString(payload.cutoff_date, current.cutoffDate),
        status: entry.type === 'cycle_start' ? current.status : entry.type === 'cycle_complete' ? 'completed' : 'no_data',
        requestedDataMode: readString(payload.requested_data_mode, current.requestedDataMode),
        effectiveDataMode: readString(payload.effective_data_mode, current.effectiveDataMode),
        llmMode: readString(payload.llm_mode, current.llmMode),
        degraded: typeof payload.degraded === 'boolean' ? payload.degraded : current.degraded,
        degradeReason: readString(payload.degrade_reason, current.degradeReason),
        returnPct: payload.return_pct !== undefined ? formatPercent(payload.return_pct) : current.returnPct,
      })
    })
  return Array.from(rows.values())
}

export function buildSpeechEntries(entries: TimelineEntry[]): TimelineEntry[] {
  return entries.filter((entry) => entry.category === 'speech')
}
