import { useEffect, useMemo, useState, type ReactNode } from 'react'

import { ApiError } from '@/shared/api/errors'
import { useQuickStatus } from '@/shared/api/status'
import {
  useCreateTrainingPlan,
  useExecuteTrainingPlan,
  useTrainingEvaluationDetail,
  useTrainingEvaluations,
  useTrainingPlanDetail,
  useTrainingPlans,
  useTrainingRunDetail,
  useTrainingRuns,
} from '@/shared/api/trainingLab'
import { formatDateTime, toStringArray } from '@/shared/lib/format'
import { useEventStream } from '@/shared/realtime/events'
import {
  buildAgentSnapshots,
  buildCycleStatusRows,
  buildFilterOptions,
  buildSpeechEntries,
  buildTimelineEntries,
  buildTrainingActionErrorViewModel,
  buildTrainingResultViewModels,
  filterTimelineEntries,
  type StrategyDiffViewModel,
  type TimelineEntry,
  type TrainingAgentSnapshot,
  type TrainingEventFilterState,
  type TrainingResultViewModel,
} from '@/shared/view-models/trainingLab'
import { ErrorState, EmptyState, LoadingState } from '@/shared/ui/AsyncState'
import { JsonView } from '@/shared/ui/JsonView'
import { KeyValueList } from '@/shared/ui/KeyValueList'
import { MetricCard } from '@/shared/ui/MetricCard'
import { Panel } from '@/shared/ui/Panel'
import { StatusBadge } from '@/shared/ui/StatusBadge'

type TrainingPlanFormState = {
  rounds: number
  mock: boolean
  goal: string
  notes: string
  tags: string
}

type TabKey = 'plans' | 'runs' | 'evaluations'

type UnknownRecord = Record<string, unknown>

const defaultForm: TrainingPlanFormState = {
  rounds: 3,
  mock: false,
  goal: '构建新前端训练实验室的第一条验证链路',
  notes: '通过冻结契约独立创建计划，并在 /app 中观察训练执行与事件流。',
  tags: 'frontend,lab,sprint2',
}

const defaultEventFilter: TrainingEventFilterState = {
  category: 'all',
  cycleId: '',
  stage: '',
  agent: '',
  kind: '',
}

function asRecord(value: unknown): UnknownRecord | null {
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as UnknownRecord) : null
}

function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : []
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

function toneFromStatus(value: unknown): 'neutral' | 'good' | 'warn' | 'danger' {
  const status = readString(value, 'unknown').toLowerCase()
  if (status === 'ok' || status === 'completed' || status === 'healthy' || status === 'pass') {
    return 'good'
  }
  if (status === 'no_data' || status === 'warning' || status === 'running' || status === 'review') {
    return 'warn'
  }
  if (status === 'error' || status === 'failed' || status === 'blocked') {
    return 'danger'
  }
  return 'neutral'
}

function toneFromBoolean(value: boolean | null): 'neutral' | 'good' | 'warn' {
  if (value === null) {
    return 'neutral'
  }
  return value ? 'good' : 'warn'
}

function toneFromMode(value: string): 'neutral' | 'good' | 'warn' | 'danger' {
  const mode = value.toLowerCase()
  if (mode === 'live' || mode === 'online') {
    return 'good'
  }
  if (mode === 'offline' || mode === 'review') {
    return 'neutral'
  }
  if (mode === 'mock' || mode === 'dry_run' || mode === 'degraded') {
    return 'warn'
  }
  if (mode === 'unavailable') {
    return 'danger'
  }
  return 'neutral'
}

function booleanLabel(value: boolean | null, trueLabel = '是', falseLabel = '否'): string {
  if (value === null) {
    return '--'
  }
  return value ? trueLabel : falseLabel
}

function formatFilterLabel(value: string, fallback: string): string {
  return value || fallback
}

function buildModeDrift(result: TrainingResultViewModel): string {
  if (!result.requestedDataMode || !result.effectiveDataMode) {
    return ''
  }
  if (result.requestedDataMode === '--' || result.effectiveDataMode === '--') {
    return ''
  }
  if (result.requestedDataMode === result.effectiveDataMode) {
    return ''
  }
  return `请求模式 ${result.requestedDataMode}，实际模式 ${result.effectiveDataMode}`
}

function renderModePills(values: Array<{ label: string; value: string }>) {
  return (
    <div className="status-badge-row status-badge-row--dense">
      {values.map((item) => (
        <StatusBadge key={`${item.label}-${item.value}`} tone={toneFromMode(item.value)}>
          {`${item.label}: ${item.value}`}
        </StatusBadge>
      ))}
    </div>
  )
}

function renderSourceBadges(sources: Record<string, boolean> | null) {
  if (!sources) {
    return null
  }
  return (
    <div className="status-badge-row status-badge-row--dense">
      {Object.entries(sources).map(([key, enabled]) => (
        <StatusBadge key={key} tone={enabled ? 'good' : 'neutral'}>
          {`${key}: ${enabled ? '可用' : '不可用'}`}
        </StatusBadge>
      ))}
    </div>
  )
}

function DetailSection({ title, testId, children }: { title: string; testId?: string; children: ReactNode }) {
  return (
    <section className="detail-section" data-testid={testId}>
      <h4 className="detail-section__title">{title}</h4>
      {children}
    </section>
  )
}

function StrategyDiffList({ diffs, testId }: { diffs: StrategyDiffViewModel[]; testId?: string }) {
  if (diffs.length === 0) {
    return <EmptyState title="本周期未发生参数变更" />
  }

  return (
    <div className="diff-list" data-testid={testId}>
      {diffs.map((diff, index) => (
        <article className="diff-card" key={`${diff.cycleId}-${diff.source}-${index}`}>
          <header className="diff-card__header">
            <strong>{diff.cycleId}</strong>
            <StatusBadge tone={diff.source === 'review_meeting' ? 'warn' : 'neutral'}>{diff.source}</StatusBadge>
          </header>
          {diff.notes ? <p className="panel-note">{diff.notes}</p> : null}
          <div className="diff-card__rows">
            {diff.changes.map((change) => (
              <div className="diff-card__row" key={`${diff.cycleId}-${diff.source}-${change.path}`}>
                <code>{change.path}</code>
                <span>{change.value}</span>
              </div>
            ))}
          </div>
        </article>
      ))}
    </div>
  )
}

function DataSourceUnavailableCard({
  title,
  subtitle,
  requestedDataMode,
  suggestions,
  diagnosticsIssues,
  diagnosticsSuggestions,
  availableSources,
  allowMockFallback,
  onEnableMock,
  testId,
}: {
  title: string
  subtitle: string
  requestedDataMode: string
  suggestions: string[]
  diagnosticsIssues: string[]
  diagnosticsSuggestions: string[]
  availableSources: Record<string, boolean> | null
  allowMockFallback: boolean | null
  onEnableMock: () => void
  testId?: string
}) {
  return (
    <div className="special-error-card" data-testid={testId}>
      <div className="special-error-card__header">
        <div>
          <h5>{title}</h5>
          <p>{subtitle}</p>
        </div>
        <StatusBadge tone="danger">503 / data_source_unavailable</StatusBadge>
      </div>
      <div className="special-error-card__body">
        <KeyValueList
          entries={[
            { label: '请求模式', value: requestedDataMode },
            { label: '允许自动回退 mock', value: booleanLabel(allowMockFallback, '允许', '不允许') },
          ]}
        />
        {renderSourceBadges(availableSources)}
        {diagnosticsIssues.length > 0 ? (
          <div>
            <strong>数据诊断</strong>
            <ul className="result-card__list">
              {diagnosticsIssues.map((item) => <li key={item}>{item}</li>)}
            </ul>
          </div>
        ) : null}
        {diagnosticsSuggestions.length > 0 ? (
          <div>
            <strong>诊断建议</strong>
            <ul className="result-card__list">
              {diagnosticsSuggestions.map((item) => <li key={item}>{item}</li>)}
            </ul>
          </div>
        ) : null}
        {suggestions.length > 0 ? (
          <div>
            <strong>下一步建议</strong>
            <ul className="result-card__list">
              {suggestions.map((item) => <li key={item}>{item}</li>)}
            </ul>
          </div>
        ) : null}
      </div>
      <div className="button-row special-error-card__actions">
        <a className="button button--secondary button-link" href="/app/data">检查离线库覆盖</a>
        <button className="button button--secondary" onClick={onEnableMock} type="button">切换到 Smoke / Demo 模式</button>
      </div>
    </div>
  )
}

function ResultCard({ result, onEnableMock }: { result: TrainingResultViewModel; onEnableMock: () => void }) {
  const driftNote = buildModeDrift(result)

  return (
    <article
      className={`result-card result-card--rich result-card--${result.status}${driftNote ? ' result-card--drift' : ''}`}
      data-testid={`result-card-${result.cycleId}`}
    >
      <header className="result-card__header">
        <div>
          <strong>{result.cycleId}</strong>
          <p className="panel-note">截止日 {result.cutoffDate}</p>
        </div>
        <div className="status-badge-row status-badge-row--dense">
          <StatusBadge tone={toneFromStatus(result.status)}>{result.status}</StatusBadge>
          {result.degraded ? <StatusBadge tone="warn">degraded</StatusBadge> : null}
          {result.llmMode !== '--' ? <StatusBadge tone={toneFromMode(result.llmMode)}>{result.llmMode}</StatusBadge> : null}
        </div>
      </header>

      <div className="result-card__metrics">
        <span>收益率 {result.returnPct}</span>
        <span>选股 {result.selectedCount}</span>
        <span>交易 {result.tradeCount}</span>
      </div>

      {renderModePills([
        { label: '请求模式', value: result.requestedDataMode },
        { label: '实际模式', value: result.effectiveDataMode },
        { label: 'LLM', value: result.llmMode },
      ])}

      <div className="status-badge-row status-badge-row--dense">
        <StatusBadge tone={toneFromBoolean(result.benchmarkPassed)}>{`Benchmark: ${booleanLabel(result.benchmarkPassed, '通过', '未通过')}`}</StatusBadge>
        <StatusBadge tone={toneFromBoolean(result.reviewApplied)}>{`Review 应用: ${booleanLabel(result.reviewApplied, '已应用', '未应用')}`}</StatusBadge>
        {result.verdict !== '--' ? <StatusBadge tone={toneFromStatus(result.verdict)}>{`Run Verdict: ${result.verdict}`}</StatusBadge> : null}
        {result.promotionVerdict !== '--' ? <StatusBadge tone={toneFromStatus(result.promotionVerdict)}>{`Promotion: ${result.promotionVerdict}`}</StatusBadge> : null}
      </div>

      {driftNote ? (
        <div className="state-block state-block--warn" data-testid="training-mode-drift">
          <strong>请求模式与实际模式不一致</strong>
          <div>{driftNote}</div>
          {result.degradeReason !== '--' ? <div>原因：{result.degradeReason}</div> : null}
        </div>
      ) : null}

      {result.status === 'no_data' ? (
        <div className="state-block state-block--warn">
          <strong>本轮无数据</strong>
          <div>阶段：{result.stage}</div>
          <div>原因：{result.reason}</div>
        </div>
      ) : null}

      {result.specialError ? (
        <DataSourceUnavailableCard
          title="真实训练数据不可用"
          subtitle="本次请求未显式启用 mock，系统按契约中止，而不是回退到假数据。"
          requestedDataMode={result.requestedDataMode}
          suggestions={result.suggestions}
          diagnosticsIssues={result.diagnosticsIssues}
          diagnosticsSuggestions={result.diagnosticsSuggestions}
          availableSources={result.availableSources}
          allowMockFallback={result.specialError.allow_mock_fallback}
          onEnableMock={onEnableMock}
        />
      ) : null}

      {!result.specialError && result.errorMessage ? (
        <div className="state-block state-block--danger">
          <strong>执行错误</strong>
          <div>{result.errorMessage}</div>
          {result.errorCode ? <div>错误码：{result.errorCode}</div> : null}
        </div>
      ) : null}

      {result.selectedStocks.length > 0 ? (
        <div>
          <strong>选股结果</strong>
          <div className="result-card__tags">
            {result.selectedStocks.map((stock) => (
              <code key={stock}>{stock}</code>
            ))}
          </div>
        </div>
      ) : null}

      {result.availableSources ? (
        <div>
          <strong>可用数据源</strong>
          {renderSourceBadges(result.availableSources)}
        </div>
      ) : null}

      {result.artifacts.length > 0 ? (
        <div>
          <strong>产物跳转区</strong>
          <div className="artifact-list">
            {result.artifacts.map((artifact) => (
              <div className="artifact-chip" key={`${result.cycleId}-${artifact.key}`}>
                <span>{artifact.label}</span>
                <code>{artifact.value}</code>
              </div>
            ))}
          </div>
        </div>
      ) : null}

      {result.strategyDiffs.length > 0 ? (
        <div>
          <strong>策略差异</strong>
          <StrategyDiffList diffs={result.strategyDiffs} />
        </div>
      ) : null}
    </article>
  )
}

function TimelineCard({ entry }: { entry: TimelineEntry }) {
  return (
    <article className="timeline-entry" data-testid={`timeline-entry-${entry.id}`}>
      <header className="timeline-entry__header">
        <div>
          <strong>{entry.title}</strong>
          <p>{entry.subtitle}</p>
        </div>
        <span>{formatDateTime(entry.receivedAt)}</span>
      </header>
      <div className="status-badge-row status-badge-row--dense">
        <StatusBadge tone={toneFromStatus(entry.type)}>{entry.type}</StatusBadge>
        {entry.cycleId ? <StatusBadge tone="neutral">{`Cycle ${entry.cycleId}`}</StatusBadge> : null}
        {entry.stage ? <StatusBadge tone="neutral">{entry.stage}</StatusBadge> : null}
        {entry.agent ? <StatusBadge tone="neutral">{entry.agent}</StatusBadge> : null}
        {entry.modes.map((mode) => (
          <StatusBadge key={`${entry.id}-${mode}`} tone={toneFromMode(mode)}>{mode}</StatusBadge>
        ))}
      </div>
      {entry.detail ? <p className="panel-note">{entry.detail}</p> : null}
    </article>
  )
}

function AgentOverview({ agents }: { agents: TrainingAgentSnapshot[] }) {
  if (agents.length === 0) {
    return <EmptyState title="等待 agent_status / agent_progress 事件进入" />
  }

  return (
    <div className="agent-grid" data-testid="agent-overview-grid">
      {agents.map((agent) => (
        <article className="agent-card" key={`${agent.agent}-${agent.updatedAt}`}>
          <header className="agent-card__header">
            <strong>{agent.agent}</strong>
            <StatusBadge tone={toneFromStatus(agent.status)}>{agent.status}</StatusBadge>
          </header>
          <KeyValueList
            entries={[
              { label: 'Stage', value: agent.stage },
              { label: 'Cycle', value: agent.cycleId },
              { label: 'Progress', value: agent.progressPct === null ? '--' : `${agent.progressPct}%` },
              { label: 'Updated', value: formatDateTime(agent.updatedAt) },
            ]}
          />
          <p className="panel-note">{agent.message}</p>
          {agent.thinking ? <p className="panel-note">思考：{agent.thinking}</p> : null}
        </article>
      ))}
    </div>
  )
}

function CycleStatusStrip({ rows }: { rows: ReturnType<typeof buildCycleStatusRows> }) {
  if (rows.length === 0) {
    return <EmptyState title="等待 cycle_start / cycle_complete / cycle_skipped 事件进入" />
  }

  return (
    <div className="cycle-strip" data-testid="cycle-status-strip">
      {rows.map((row) => (
        <article className="cycle-row" key={row.cycleId}>
          <header className="cycle-row__header">
            <strong>{row.cycleId}</strong>
            <StatusBadge tone={toneFromStatus(row.status)}>{row.status}</StatusBadge>
          </header>
          {renderModePills([
            { label: '请求', value: row.requestedDataMode },
            { label: '实际', value: row.effectiveDataMode },
            { label: 'LLM', value: row.llmMode },
          ])}
          <div className="cycle-row__footer">
            <span>收益 {row.returnPct}</span>
            <span>截止日 {row.cutoffDate}</span>
          </div>
          {row.degradeReason !== '--' ? <p className="panel-note">{row.degradeReason}</p> : null}
        </article>
      ))}
    </div>
  )
}

function SpeechCards({ entries }: { entries: TimelineEntry[] }) {
  if (entries.length === 0) {
    return <EmptyState title="等待 meeting_speech 事件进入" />
  }

  return (
    <div className="speech-grid" data-testid="speech-card-list">
      {entries.map((entry) => (
        <article className="speech-card" key={entry.id}>
          <header className="speech-card__header">
            <strong>{entry.title}</strong>
            <span>{formatDateTime(entry.receivedAt)}</span>
          </header>
          <p>{entry.detail}</p>
          <div className="status-badge-row status-badge-row--dense">
            {entry.cycleId ? <StatusBadge tone="neutral">{`Cycle ${entry.cycleId}`}</StatusBadge> : null}
            {entry.agent ? <StatusBadge tone="neutral">{entry.agent}</StatusBadge> : null}
            {entry.modes.map((mode) => <StatusBadge key={`${entry.id}-${mode}`} tone={toneFromMode(mode)}>{mode}</StatusBadge>)}
          </div>
        </article>
      ))}
    </div>
  )
}

export function TrainingLabPage() {
  const [form, setForm] = useState<TrainingPlanFormState>(defaultForm)
  const [activeTab, setActiveTab] = useState<TabKey>('plans')
  const [selectedPlanId, setSelectedPlanId] = useState<string | null>(null)
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null)
  const [selectedEvaluationRunId, setSelectedEvaluationRunId] = useState<string | null>(null)
  const [lastExecutionPayload, setLastExecutionPayload] = useState<unknown>(null)
  const [executeAbortController, setExecuteAbortController] = useState<AbortController | null>(null)
  const [actionMessage, setActionMessage] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [eventFilter, setEventFilter] = useState<TrainingEventFilterState>(defaultEventFilter)

  const quickStatus = useQuickStatus()
  const plans = useTrainingPlans()
  const planDetail = useTrainingPlanDetail(selectedPlanId)
  const runs = useTrainingRuns()
  const runDetail = useTrainingRunDetail(selectedRunId)
  const evaluations = useTrainingEvaluations()
  const evaluationDetail = useTrainingEvaluationDetail(selectedEvaluationRunId)
  const createPlan = useCreateTrainingPlan()
  const executePlan = useExecuteTrainingPlan()
  const eventStream = useEventStream(true, 120)

  useEffect(() => {
    if (!selectedPlanId && plans.data?.items.length) {
      const first = plans.data.items[0]
      if (typeof first.plan_id === 'string') {
        setSelectedPlanId(first.plan_id)
      }
    }
  }, [plans.data, selectedPlanId])

  useEffect(() => {
    if (!selectedRunId && runs.data?.items.length) {
      const first = runs.data.items[0]
      if (typeof first.run_id === 'string') {
        setSelectedRunId(first.run_id)
      }
    }
  }, [runs.data, selectedRunId])

  useEffect(() => {
    if (!selectedEvaluationRunId && evaluations.data?.items.length) {
      const first = evaluations.data.items[0]
      if (typeof first.run_id === 'string') {
        setSelectedEvaluationRunId(first.run_id)
      }
    }
  }, [evaluations.data, selectedEvaluationRunId])

  const selectedPlanSummary = useMemo(() => {
    if (!selectedPlanId || !plans.data) {
      return null
    }
    return plans.data.items.find((item) => String(item.plan_id ?? '') === selectedPlanId) ?? null
  }, [plans.data, selectedPlanId])

  const selectedRunSummary = useMemo(() => {
    if (!selectedRunId || !runs.data) {
      return null
    }
    return runs.data.items.find((item) => String(item.run_id ?? '') === selectedRunId) ?? null
  }, [runs.data, selectedRunId])

  const selectedEvaluationSummary = useMemo(() => {
    if (!selectedEvaluationRunId || !evaluations.data) {
      return null
    }
    return evaluations.data.items.find((item) => String(item.run_id ?? '') === selectedEvaluationRunId) ?? null
  }, [evaluations.data, selectedEvaluationRunId])

  const quickSnapshot = quickStatus.data?.snapshot
  const quickTrainingLab = asRecord(quickSnapshot?.training_lab)
  const quickData = asRecord(quickSnapshot?.data)
  const quickQuality = asRecord(quickData?.quality)

  const planRecord = asRecord(planDetail.data)
  const planSpec = asRecord(planRecord?.spec)
  const planObjective = asRecord(planRecord?.objective)

  const runRecord = asRecord(runDetail.data)
  const runPayload = asRecord(runRecord?.payload)
  const runSummary = asRecord(runPayload?.summary)
  const runResults = asArray(runPayload?.results)
  const runResultViewModels = useMemo(() => buildTrainingResultViewModels(runResults), [runResults])

  const evaluationRecord = asRecord(evaluationDetail.data)
  const evaluationAssessment = asRecord(evaluationRecord?.assessment)
  const evaluationPromotion = asRecord(evaluationRecord?.promotion)
  const evaluationBenchmarkPassRate = readNumber(evaluationAssessment?.benchmark_pass_rate)
  const evaluationShouldWarn = (readNumber(evaluationAssessment?.success_count) ?? 0) > 0 && evaluationBenchmarkPassRate === 0

  const executionRecord = asRecord(lastExecutionPayload)
  const executionTrainingLab = asRecord(executionRecord?.training_lab)
  const executionRun = asRecord(executionTrainingLab?.run)
  const executionSummary = asRecord(executionRecord?.summary)

  const timelineEntries = useMemo(() => buildTimelineEntries(eventStream.events), [eventStream.events])
  const eventOptions = useMemo(() => buildFilterOptions(timelineEntries), [timelineEntries])
  const filteredTimeline = useMemo(() => filterTimelineEntries(timelineEntries, eventFilter), [timelineEntries, eventFilter])
  const speechEntries = useMemo(() => buildSpeechEntries(filteredTimeline), [filteredTimeline])
  const agentSnapshots = useMemo(() => buildAgentSnapshots(timelineEntries), [timelineEntries])
  const cycleStatusRows = useMemo(() => buildCycleStatusRows(timelineEntries), [timelineEntries])
  const strategyDiffs = useMemo(() => runResultViewModels.flatMap((item) => item.strategyDiffs), [runResultViewModels])
  const executeActionError = useMemo(
    () => buildTrainingActionErrorViewModel(executePlan.error instanceof ApiError ? executePlan.error : null),
    [executePlan.error],
  )

  useEffect(() => {
    setEventFilter((current) => ({
      ...current,
      cycleId: current.cycleId && !eventOptions.cycles.includes(current.cycleId) ? '' : current.cycleId,
      stage: current.stage && !eventOptions.stages.includes(current.stage) ? '' : current.stage,
      agent: current.agent && !eventOptions.agents.includes(current.agent) ? '' : current.agent,
      kind: current.kind && !eventOptions.kinds.includes(current.kind) ? '' : current.kind,
    }))
  }, [eventOptions])

  const submitCreatePlan = async () => {
    setActionMessage(null)
    setActionError(null)
    createPlan.reset()
    try {
      const payload = await createPlan.mutateAsync({
        rounds: form.rounds,
        mock: form.mock,
        goal: form.goal,
        notes: form.notes,
        tags: toStringArray(form.tags),
        detail_mode: 'fast',
      })
      if (typeof payload.plan_id === 'string') {
        setSelectedPlanId(payload.plan_id)
        setActiveTab('plans')
        setActionMessage(`已创建训练计划 ${payload.plan_id}`)
      }
    } catch (error) {
      setActionError(error instanceof ApiError ? error.message : '创建计划失败')
    }
  }

  const submitExecutePlan = async () => {
    if (!selectedPlanId) {
      setActionError('请先选择一个计划后再执行')
      return
    }

    const controller = new AbortController()
    setExecuteAbortController(controller)
    setActionMessage(null)
    setActionError(null)
    executePlan.reset()

    try {
      const payload = await executePlan.mutateAsync({ planId: selectedPlanId, signal: controller.signal })
      setLastExecutionPayload(payload)
      const trainingLab = asRecord(payload.training_lab)
      const run = asRecord(trainingLab?.run)
      if (typeof run?.run_id === 'string') {
        setSelectedRunId(run.run_id)
        setSelectedEvaluationRunId(run.run_id)
        setActiveTab('runs')
        setActionMessage(`计划 ${selectedPlanId} 已完成执行，当前聚焦运行 ${run.run_id}`)
      }
    } catch (error) {
      if (error instanceof ApiError && error.code === 'request_aborted') {
        setActionMessage('已取消当前执行请求，可稍后重试。')
      } else {
        setActionError(error instanceof ApiError ? error.message : '执行计划失败')
      }
    } finally {
      setExecuteAbortController(null)
    }
  }

  const enableMockMode = () => {
    setForm((current) => ({ ...current, mock: true }))
    setActionMessage('已切换到 Smoke / Demo 模式，下一次创建计划会显式提交 mock=true。')
    setActionError(null)
  }

  const cancelExecutePlan = () => {
    executeAbortController?.abort()
  }

  const promotionEntries = useMemo(() => {
    const promotion = evaluationPromotion ?? {}
    return [
      { label: 'Promotion Verdict', value: readString(promotion.verdict) },
      { label: 'Promotion Status', value: readString(promotion.status) },
      { label: 'Promotion Reason', value: readString(promotion.reason) },
      { label: 'Avg Return', value: formatPercent(promotion.avg_return_pct) },
      { label: 'Avg Strategy Score', value: readString(promotion.avg_strategy_score) },
      { label: 'Benchmark Pass Rate', value: evaluationBenchmarkPassRate === null ? '--' : `${(evaluationBenchmarkPassRate * 100).toFixed(0)}%` },
      { label: 'Selected Baseline', value: readString(promotion.selected_baseline) },
    ]
  }, [evaluationBenchmarkPassRate, evaluationPromotion])

  const promotionExtras = useMemo(() => {
    const promotion = evaluationPromotion ?? {}
    const knownKeys = new Set(['verdict', 'status', 'reason', 'avg_return_pct', 'avg_strategy_score', 'benchmark_pass_rate', 'selected_baseline'])
    return Object.fromEntries(Object.entries(promotion).filter(([key]) => !knownKeys.has(key)))
  }, [evaluationPromotion])

  return (
    <div className="page-grid page-grid--training" data-testid="training-lab-page">
      <section className="metrics-grid panel-span-full" data-testid="training-lab-overview">
        <MetricCard hint="最近 artifact 列表 / 运行态快照" label="计划数" testId="metric-plan-count" value={readNumber(quickTrainingLab?.plan_count) ?? plans.data?.count ?? 0} />
        <MetricCard hint="最近运行记录" label="运行数" testId="metric-run-count" value={readNumber(quickTrainingLab?.run_count) ?? runs.data?.count ?? 0} />
        <MetricCard hint="最近评估记录" label="评估数" testId="metric-evaluation-count" value={readNumber(quickTrainingLab?.evaluation_count) ?? evaluations.data?.count ?? 0} />
        <MetricCard hint="SSE 捕获的近期事件" label="事件流" testId="metric-event-count" value={eventStream.events.length} />
        <MetricCard hint={readString(quickData?.latest_date)} label="最新数据日" value={readString(quickData?.latest_date)} />
        <MetricCard hint={`数据健康 ${readString(quickQuality?.health_status)}`} label="默认训练模式" testId="metric-form-mock" value={form.mock ? 'Smoke / Demo' : '真实模式优先'} />
      </section>

      <div>
        <Panel title="创建训练计划">
          <div className="content-stack">
            <p className="panel-note" data-testid="training-mode-note">
              默认使用真实数据 / 离线库；只有显式勾选“Smoke / Demo 模式”时，前端才会提交 <code>mock=true</code>。
            </p>
            <div className="form-grid">
              <label>
                <span>轮次</span>
                <input
                  className="input"
                  data-testid="training-rounds-input"
                  min={1}
                  onChange={(event) => setForm((current) => ({ ...current, rounds: Number(event.target.value) }))}
                  type="number"
                  value={form.rounds}
                />
              </label>
              <label className="checkbox-field">
                <input
                  checked={form.mock}
                  data-testid="training-mock-checkbox"
                  onChange={(event) => setForm((current) => ({ ...current, mock: event.target.checked }))}
                  type="checkbox"
                />
                <span>Smoke / Demo 模式</span>
              </label>
              <label className="form-grid__full">
                <span>目标</span>
                <input
                  className="input"
                  data-testid="training-goal-input"
                  onChange={(event) => setForm((current) => ({ ...current, goal: event.target.value }))}
                  type="text"
                  value={form.goal}
                />
              </label>
              <label className="form-grid__full">
                <span>备注</span>
                <textarea
                  className="textarea"
                  data-testid="training-notes-input"
                  onChange={(event) => setForm((current) => ({ ...current, notes: event.target.value }))}
                  value={form.notes}
                />
              </label>
              <label className="form-grid__full">
                <span>标签</span>
                <input
                  className="input"
                  data-testid="training-tags-input"
                  onChange={(event) => setForm((current) => ({ ...current, tags: event.target.value }))}
                  type="text"
                  value={form.tags}
                />
              </label>
            </div>
            <div className="button-row">
              <button className="button" data-testid="create-training-plan" disabled={createPlan.isPending} onClick={submitCreatePlan} type="button">
                {createPlan.isPending ? '创建中...' : '创建计划'}
              </button>
              <button className="button button--secondary" data-testid="execute-training-plan" disabled={!selectedPlanId || executePlan.isPending} onClick={submitExecutePlan} type="button">
                {executePlan.isPending ? '执行中...' : '执行计划'}
              </button>
              {executePlan.isPending ? (
                <button className="button button--secondary" data-testid="cancel-training-execution" onClick={cancelExecutePlan} type="button">
                  取消等待
                </button>
              ) : null}
            </div>
            {actionMessage ? <div className="state-block state-block--muted">{actionMessage}</div> : null}
            {actionError ? <div className="state-block state-block--danger">{actionError}</div> : null}
            {executeActionError ? (
              <DataSourceUnavailableCard
                title={executeActionError.title}
                subtitle={executeActionError.subtitle}
                requestedDataMode={executeActionError.requestedDataMode}
                suggestions={executeActionError.suggestions}
                diagnosticsIssues={executeActionError.diagnosticsIssues}
                diagnosticsSuggestions={executeActionError.diagnosticsSuggestions}
                availableSources={executeActionError.availableSources}
                allowMockFallback={executeActionError.allowMockFallback}
                onEnableMock={enableMockMode}
                testId="special-error-card"
              />
            ) : null}
          </div>
        </Panel>
      </div>

      <div>
        <Panel title="最近执行结果">
          {executionRecord ? (
            <div className="content-stack">
              <section className="detail-metrics-grid">
                <MetricCard hint={readString(executionRun?.run_id)} label="执行状态" testId="execution-status" value={readString(executionRecord?.status)} />
                <MetricCard hint={`成功 ${readNumber(executionSummary?.success_cycles) ?? '--'}`} label="总轮次" value={readNumber(executionSummary?.total_cycles) ?? '--'} />
                <MetricCard hint={`计划 ${readString(asRecord(executionTrainingLab?.plan)?.plan_id)}`} label="失败轮次" value={readNumber(executionSummary?.failed_cycles) ?? '--'} />
                <MetricCard hint={readString(asRecord(executionTrainingLab?.evaluation)?.run_id)} label="Run ID" value={readString(executionRun?.run_id)} />
              </section>
              <DetailSection testId="execution-json-panel" title="执行返回 JSON">
                <JsonView value={lastExecutionPayload} />
              </DetailSection>
            </div>
          ) : (
            <EmptyState title="执行计划后将在这里展示结构化结果与返回 JSON" />
          )}
        </Panel>
      </div>

      <div className="panel-span-full">
        <Panel title="Training Lab 详情">
          <div className="tab-row">
            <button className={`tab-button${activeTab === 'plans' ? ' tab-button--active' : ''}`} data-testid="tab-plans" onClick={() => setActiveTab('plans')} type="button">计划</button>
            <button className={`tab-button${activeTab === 'runs' ? ' tab-button--active' : ''}`} data-testid="tab-runs" onClick={() => setActiveTab('runs')} type="button">运行</button>
            <button className={`tab-button${activeTab === 'evaluations' ? ' tab-button--active' : ''}`} data-testid="tab-evaluations" onClick={() => setActiveTab('evaluations')} type="button">评估</button>
          </div>

          {activeTab === 'plans' ? (
            <div className="detail-layout">
              <div className="detail-layout__sidebar" data-testid="plans-list">
                {plans.isLoading ? <LoadingState label="正在加载训练计划..." /> : null}
                {plans.error ? <ErrorState error={plans.error} /> : null}
                {plans.data?.items.length ? (
                  <div className="list-block">
                    {plans.data.items.map((item) => {
                      const planId = String(item.plan_id ?? '--')
                      return (
                        <button
                          className={`list-item${selectedPlanId === planId ? ' list-item--active' : ''}`}
                          data-testid={`plan-item-${planId}`}
                          key={planId}
                          onClick={() => setSelectedPlanId(planId)}
                          type="button"
                        >
                          <strong>{planId}</strong>
                          <span>{readString(item.goal)}</span>
                          <span>{formatDateTime(item.created_at)}</span>
                        </button>
                      )
                    })}
                  </div>
                ) : null}
                {plans.data && plans.data.items.length === 0 ? <EmptyState title="暂无训练计划" /> : null}
              </div>
              <div className="detail-layout__main" data-testid="plan-detail-panel">
                {planDetail.isLoading ? <LoadingState label="正在加载计划详情..." /> : null}
                {planDetail.error ? <ErrorState error={planDetail.error} /> : null}
                {selectedPlanId ? (
                  <div className="content-stack">
                    <section className="detail-metrics-grid">
                      <MetricCard hint={readString(planRecord?.plan_id)} label="计划状态" value={readString(planRecord?.status)} />
                      <MetricCard hint={readString(planRecord?.source)} label="轮次" value={readNumber(planSpec?.rounds) ?? '--'} />
                      <MetricCard hint={readString(planObjective?.goal)} label="请求模式" value={planSpec?.mock === true ? 'mock' : 'live'} />
                      <MetricCard hint={readString(planSpec?.detail_mode)} label="最后运行" value={readString(planRecord?.last_run_id)} />
                    </section>
                    <DetailSection title="计划摘要">
                      <KeyValueList
                        entries={[
                          { label: 'Plan ID', value: planRecord?.plan_id ?? selectedPlanSummary?.plan_id },
                          { label: 'Created At', value: formatDateTime(planRecord?.created_at ?? selectedPlanSummary?.created_at) },
                          { label: 'Goal', value: planObjective?.goal ?? selectedPlanSummary?.goal },
                          { label: 'Notes', value: planObjective?.notes },
                          { label: 'Tags', value: asArray(planObjective?.tags).join(', ') },
                        ]}
                      />
                    </DetailSection>
                    <DetailSection title="Spec / Objective">
                      <div className="split-grid">
                        <KeyValueList
                          entries={[
                            { label: 'Rounds', value: planSpec?.rounds },
                            { label: 'Mock', value: planSpec?.mock },
                            { label: 'Detail Mode', value: planSpec?.detail_mode },
                          ]}
                        />
                        <KeyValueList
                          entries={[
                            { label: 'Goal', value: planObjective?.goal },
                            { label: 'Notes', value: planObjective?.notes },
                            { label: 'Tags', value: asArray(planObjective?.tags).join(', ') },
                          ]}
                        />
                      </div>
                    </DetailSection>
                    <DetailSection testId="plan-json-panel" title="原始 JSON">
                      <JsonView value={planDetail.data ?? selectedPlanSummary} />
                    </DetailSection>
                  </div>
                ) : (
                  <EmptyState title="请选择一条训练计划" />
                )}
              </div>
            </div>
          ) : null}

          {activeTab === 'runs' ? (
            <div className="detail-layout">
              <div className="detail-layout__sidebar" data-testid="runs-list">
                {runs.isLoading ? <LoadingState label="正在加载运行记录..." /> : null}
                {runs.error ? <ErrorState error={runs.error} /> : null}
                {runs.data?.items.length ? (
                  <div className="list-block">
                    {runs.data.items.map((item) => {
                      const runId = String(item.run_id ?? '--')
                      return (
                        <button
                          className={`list-item${selectedRunId === runId ? ' list-item--active' : ''}`}
                          data-testid={`run-item-${runId}`}
                          key={runId}
                          onClick={() => setSelectedRunId(runId)}
                          type="button"
                        >
                          <strong>{runId}</strong>
                          <span>{String(item.plan_id ?? '--')}</span>
                          <span>{formatDateTime(item.created_at)}</span>
                        </button>
                      )
                    })}
                  </div>
                ) : null}
                {runs.data && runs.data.items.length === 0 ? <EmptyState title="暂无训练运行记录" /> : null}
              </div>
              <div className="detail-layout__main" data-testid="run-detail-panel">
                {runDetail.isLoading ? <LoadingState label="正在加载运行详情..." /> : null}
                {runDetail.error ? <ErrorState error={runDetail.error} /> : null}
                {selectedRunId ? (
                  <div className="content-stack">
                    <section className="detail-metrics-grid">
                      <MetricCard hint={readString(runRecord?.run_id)} label="运行状态" testId="run-detail-status" value={readString(runRecord?.status)} />
                      <MetricCard hint={`plan=${readString(runRecord?.plan_id)}`} label="结果数" value={runResultViewModels.length} />
                      <MetricCard hint={`总计 ${readNumber(runSummary?.total_cycles) ?? '--'}`} label="成功轮次" value={readNumber(runSummary?.success_cycles) ?? '--'} />
                      <MetricCard hint={readString(runRecord?.error)} label="失败轮次" value={readNumber(runSummary?.failed_cycles) ?? '--'} />
                    </section>
                    <DetailSection title="运行摘要">
                      <KeyValueList
                        entries={[
                          { label: 'Run ID', value: runRecord?.run_id ?? selectedRunSummary?.run_id },
                          { label: 'Plan ID', value: runRecord?.plan_id ?? selectedRunSummary?.plan_id },
                          { label: 'Created At', value: formatDateTime(runRecord?.created_at ?? selectedRunSummary?.created_at) },
                          { label: 'Goal', value: asRecord(asRecord(runRecord?.plan)?.objective)?.goal },
                          { label: 'Requested Mode', value: asRecord(runRecord?.plan)?.spec ? (asRecord(asRecord(runRecord?.plan)?.spec)?.mock === true ? 'mock' : 'live') : '--' },
                        ]}
                      />
                    </DetailSection>
                    <DetailSection title="周期结果卡片">
                      {runResultViewModels.length === 0 ? (
                        <EmptyState title="当前详情没有 results 数组" />
                      ) : (
                        <div className="result-card-grid" data-testid="result-card-grid">
                          {runResultViewModels.map((result) => (
                            <ResultCard key={result.id} onEnableMock={enableMockMode} result={result} />
                          ))}
                        </div>
                      )}
                    </DetailSection>
                    <DetailSection testId="run-json-panel" title="原始 JSON">
                      <JsonView value={runDetail.data ?? selectedRunSummary} />
                    </DetailSection>
                  </div>
                ) : (
                  <EmptyState title="请选择一条运行记录" />
                )}
              </div>
            </div>
          ) : null}

          {activeTab === 'evaluations' ? (
            <div className="detail-layout">
              <div className="detail-layout__sidebar" data-testid="evaluations-list">
                {evaluations.isLoading ? <LoadingState label="正在加载评估记录..." /> : null}
                {evaluations.error ? <ErrorState error={evaluations.error} /> : null}
                {evaluations.data?.items.length ? (
                  <div className="list-block">
                    {evaluations.data.items.map((item) => {
                      const runId = String(item.run_id ?? '--')
                      return (
                        <button
                          className={`list-item${selectedEvaluationRunId === runId ? ' list-item--active' : ''}`}
                          data-testid={`evaluation-item-${runId}`}
                          key={runId}
                          onClick={() => setSelectedEvaluationRunId(runId)}
                          type="button"
                        >
                          <strong>{runId}</strong>
                          <span>{String(item.plan_id ?? '--')}</span>
                          <span>{formatDateTime(item.created_at)}</span>
                        </button>
                      )
                    })}
                  </div>
                ) : null}
                {evaluations.data && evaluations.data.items.length === 0 ? <EmptyState title="暂无训练评估记录" /> : null}
              </div>
              <div className="detail-layout__main" data-testid="evaluation-detail-panel">
                {evaluationDetail.isLoading ? <LoadingState label="正在加载评估详情..." /> : null}
                {evaluationDetail.error ? <ErrorState error={evaluationDetail.error} /> : null}
                {selectedEvaluationRunId ? (
                  <div className="content-stack">
                    <section className="detail-metrics-grid">
                      <MetricCard hint={readString(evaluationRecord?.run_id)} label="运行状态" testId="evaluation-detail-status" value={readString(evaluationRecord?.status)} />
                      <MetricCard hint={`总计 ${readNumber(evaluationAssessment?.total_results) ?? '--'}`} label="训练成功数" value={readNumber(evaluationAssessment?.success_count) ?? '--'} />
                      <MetricCard hint={`无数据 ${readNumber(evaluationAssessment?.no_data_count) ?? '--'} / 错误 ${readNumber(evaluationAssessment?.error_count) ?? '--'}`} label="Benchmark Pass Rate" value={evaluationBenchmarkPassRate === null ? '--' : `${(evaluationBenchmarkPassRate * 100).toFixed(0)}%`} />
                      <MetricCard hint={readString(evaluationPromotion?.status)} label="晋升 Verdict" value={readString(evaluationPromotion?.verdict)} />
                    </section>
                    {evaluationShouldWarn ? (
                      <div className="state-block state-block--warn" data-testid="training-evaluation-warning">
                        训练成功轮次大于 0，但 benchmark pass rate 为 0；请分开理解“训练成功”与“晋升通过”。
                      </div>
                    ) : null}
                    <DetailSection title="评估摘要">
                      <KeyValueList
                        entries={[
                          { label: 'Run ID', value: evaluationRecord?.run_id ?? selectedEvaluationSummary?.run_id },
                          { label: 'Plan ID', value: evaluationRecord?.plan_id ?? selectedEvaluationSummary?.plan_id },
                          { label: 'Created At', value: formatDateTime(evaluationRecord?.created_at ?? selectedEvaluationSummary?.created_at) },
                          { label: 'Avg Return', value: formatPercent(evaluationAssessment?.avg_return_pct) },
                          { label: 'Error', value: readOptionalString(evaluationRecord?.error) || '--' },
                        ]}
                      />
                    </DetailSection>
                    <DetailSection title="Assessment">
                      <KeyValueList
                        entries={[
                          { label: 'Total Results', value: evaluationAssessment?.total_results },
                          { label: 'Success Count', value: evaluationAssessment?.success_count },
                          { label: 'No Data Count', value: evaluationAssessment?.no_data_count },
                          { label: 'Error Count', value: evaluationAssessment?.error_count },
                          { label: 'Avg Return', value: formatPercent(evaluationAssessment?.avg_return_pct) },
                          { label: 'Benchmark Pass Rate', value: evaluationBenchmarkPassRate === null ? '--' : `${(evaluationBenchmarkPassRate * 100).toFixed(0)}%` },
                        ]}
                      />
                    </DetailSection>
                    <DetailSection title="Promotion Gate">
                      <div className="split-grid">
                        <KeyValueList entries={promotionEntries.slice(0, 4)} />
                        <KeyValueList entries={promotionEntries.slice(4)} />
                      </div>
                      {Object.keys(promotionExtras).length > 0 ? (
                        <div className="content-stack">
                          <p className="panel-note">预留扩展 gate/checks 字段，不写死后续结构。</p>
                          <JsonView value={promotionExtras} />
                        </div>
                      ) : null}
                    </DetailSection>
                    <DetailSection testId="evaluation-json-panel" title="原始 JSON">
                      <JsonView value={evaluationDetail.data ?? selectedEvaluationSummary} />
                    </DetailSection>
                  </div>
                ) : (
                  <EmptyState title="请选择一条评估记录" />
                )}
              </div>
            </div>
          ) : null}
        </Panel>
      </div>

      <div className="panel-span-full">
        <Panel title="策略差异 / Strategy Diff">
          <div className="content-stack" data-testid="strategy-diff-panel">
            <p className="panel-note">从 optimization_events[].applied_change 提取参数差异，并将 review meeting 变更独立归类。</p>
            <StrategyDiffList diffs={strategyDiffs} testId="strategy-diff-list" />
          </div>
        </Panel>
      </div>

      <div className="panel-span-full">
        <Panel title="实时观测 / SSE 时间线">
          <div className="content-stack" data-testid="timeline-panel">
            <div className="event-stream-header">
              <span data-testid="event-stream-status">{eventStream.connected ? '已连接 /api/events' : '事件流重连中'}</span>
              {eventStream.lastError ? <span className="text-danger">{eventStream.lastError}</span> : null}
            </div>

            <DetailSection title="周期状态条">
              <CycleStatusStrip rows={cycleStatusRows} />
            </DetailSection>

            <div className="split-grid split-grid--wide">
              <DetailSection title="Agent 概览">
                <AgentOverview agents={agentSnapshots} />
              </DetailSection>
              <DetailSection title="Speech Cards">
                <SpeechCards entries={speechEntries} />
              </DetailSection>
            </div>

            <DetailSection title="Timeline Filters">
              <div className="timeline-filter-chips">
                {[
                  { key: 'all', label: '全部事件' },
                  { key: 'speech', label: '只看发言' },
                  { key: 'logs', label: '只看模块日志' },
                  { key: 'cycle', label: '只看周期事件' },
                  { key: 'agent', label: '只看 Agent' },
                ].map((item) => (
                  <button
                    className={`tab-button${eventFilter.category === item.key ? ' tab-button--active' : ''}`}
                    data-testid={`timeline-category-${item.key}`}
                    key={item.key}
                    onClick={() => setEventFilter((current) => ({ ...current, category: item.key as TrainingEventFilterState['category'] }))}
                    type="button"
                  >
                    {item.label}
                  </button>
                ))}
              </div>
              <div className="control-row timeline-filter-grid">
                <label>
                  <span>Cycle</span>
                  <select className="input" data-testid="timeline-cycle-filter" onChange={(event) => setEventFilter((current) => ({ ...current, cycleId: event.target.value }))} value={eventFilter.cycleId}>
                    <option value="">全部</option>
                    {eventOptions.cycles.map((item) => <option key={item} value={item}>{formatFilterLabel(item, '全部')}</option>)}
                  </select>
                </label>
                <label>
                  <span>Stage</span>
                  <select className="input" data-testid="timeline-stage-filter" onChange={(event) => setEventFilter((current) => ({ ...current, stage: event.target.value }))} value={eventFilter.stage}>
                    <option value="">全部</option>
                    {eventOptions.stages.map((item) => <option key={item} value={item}>{formatFilterLabel(item, '全部')}</option>)}
                  </select>
                </label>
                <label>
                  <span>Agent</span>
                  <select className="input" data-testid="timeline-agent-filter" onChange={(event) => setEventFilter((current) => ({ ...current, agent: event.target.value }))} value={eventFilter.agent}>
                    <option value="">全部</option>
                    {eventOptions.agents.map((item) => <option key={item} value={item}>{formatFilterLabel(item, '全部')}</option>)}
                  </select>
                </label>
              </div>
              <div className="control-row timeline-filter-grid timeline-filter-grid--secondary">
                <label>
                  <span>Kind</span>
                  <select className="input" data-testid="timeline-kind-filter" onChange={(event) => setEventFilter((current) => ({ ...current, kind: event.target.value }))} value={eventFilter.kind}>
                    <option value="">全部</option>
                    {eventOptions.kinds.map((item) => <option key={item} value={item}>{formatFilterLabel(item, '全部')}</option>)}
                  </select>
                </label>
              </div>
            </DetailSection>

            <DetailSection title="训练时间线" testId="timeline-list">
              {filteredTimeline.length === 0 ? (
                <EmptyState title="当前过滤条件下没有事件" />
              ) : (
                <div className="timeline-list">
                  {filteredTimeline.map((entry) => <TimelineCard entry={entry} key={entry.id} />)}
                </div>
              )}
            </DetailSection>
          </div>
        </Panel>
      </div>
    </div>
  )
}
