import { useEffect, useMemo, useState } from 'react'

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
import { ApiError } from '@/shared/api/errors'
import { formatDateTime, prettyJson, toStringArray } from '@/shared/lib/format'
import { useEventStream } from '@/shared/realtime/events'
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

function asRecord(value: unknown): UnknownRecord | null {
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as UnknownRecord) : null
}

function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : []
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

function formatPercent(value: unknown): string {
  const number = readNumber(value)
  if (number === null) {
    return '--'
  }
  return `${number >= 0 ? '+' : ''}${number.toFixed(2)}%`
}

function toneFromStatus(value: unknown): 'neutral' | 'good' | 'warn' | 'danger' {
  const status = readString(value, 'unknown').toLowerCase()
  if (status === 'ok' || status === 'completed' || status === 'healthy') {
    return 'good'
  }
  if (status === 'no_data' || status === 'warning' || status === 'running') {
    return 'warn'
  }
  if (status === 'error' || status === 'failed') {
    return 'danger'
  }
  return 'neutral'
}

function ResultCards({ results }: { results: unknown[] }) {
  if (results.length === 0) {
    return <EmptyState title="当前详情没有 results 数组" />
  }

  return (
    <div className="result-card-grid" data-testid="result-card-grid">
      {results.map((result, index) => {
        const row = asRecord(result) ?? {}
        const cycleId = readString(row.cycle_id, `#${index + 1}`)
        const status = readString(row.status, 'unknown')
        const selectedStocks = asArray(row.selected_stocks)
        return (
          <article className="result-card" key={`${cycleId}-${index}`}>
            <header className="result-card__header">
              <strong>{cycleId}</strong>
              <StatusBadge tone={toneFromStatus(status)}>{status}</StatusBadge>
            </header>
            <div className="result-card__metrics">
              <span>收益率 {formatPercent(row.return_pct)}</span>
              <span>选股 {readNumber(row.selected_count) ?? '--'}</span>
              <span>交易 {readNumber(row.trade_count) ?? '--'}</span>
            </div>
            {selectedStocks.length > 0 ? (
              <div className="result-card__tags">
                {selectedStocks.map((stock) => (
                  <code key={String(stock)}>{String(stock)}</code>
                ))}
              </div>
            ) : null}
            {typeof row.error === 'string' && row.error ? <p className="text-danger">{row.error}</p> : null}
          </article>
        )
      })}
    </div>
  )
}

function DetailSection({
  title,
  summary,
  metrics,
  raw,
  rawTestId,
  extra,
}: {
  title: string
  summary?: Array<{ label: string; value: unknown }>
  metrics?: Array<{ label: string; value: string | number; hint?: string; testId?: string }>
  raw: unknown
  rawTestId: string
  extra?: React.ReactNode
}) {
  return (
    <div className="content-stack">
      {metrics && metrics.length > 0 ? (
        <section className="detail-metrics-grid">
          {metrics.map((metric) => (
            <MetricCard key={metric.label} hint={metric.hint} label={metric.label} testId={metric.testId} value={metric.value} />
          ))}
        </section>
      ) : null}
      {summary && summary.length > 0 ? (
        <section className="detail-section">
          <h4 className="detail-section__title">{title}摘要</h4>
          <KeyValueList entries={summary} />
        </section>
      ) : null}
      {extra ? <section className="detail-section">{extra}</section> : null}
      <section className="detail-section" data-testid={rawTestId}>
        <h4 className="detail-section__title">原始 JSON</h4>
        <JsonView value={raw} />
      </section>
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
  const [lastError, setLastError] = useState<string | null>(null)

  const plans = useTrainingPlans()
  const planDetail = useTrainingPlanDetail(selectedPlanId)
  const runs = useTrainingRuns()
  const runDetail = useTrainingRunDetail(selectedRunId)
  const evaluations = useTrainingEvaluations()
  const evaluationDetail = useTrainingEvaluationDetail(selectedEvaluationRunId)
  const createPlan = useCreateTrainingPlan()
  const executePlan = useExecuteTrainingPlan()
  const eventStream = useEventStream(true, 40)

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

  const submitCreatePlan = async () => {
    setLastError(null)
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
      }
    } catch (error) {
      setLastError(error instanceof ApiError ? error.message : '创建计划失败')
    }
  }

  const submitExecutePlan = async () => {
    if (!selectedPlanId) {
      setLastError('请先选择一个计划后再执行')
      return
    }
    setLastError(null)
    try {
      const payload = await executePlan.mutateAsync(selectedPlanId)
      setLastExecutionPayload(payload)
      const trainingLab = asRecord(payload.training_lab)
      const run = asRecord(trainingLab?.run)
      if (typeof run?.run_id === 'string') {
        setSelectedRunId(run.run_id)
        setSelectedEvaluationRunId(run.run_id)
        setActiveTab('runs')
      }
    } catch (error) {
      setLastError(error instanceof ApiError ? error.message : '执行计划失败')
    }
  }

  const planRecord = asRecord(planDetail.data)
  const planSpec = asRecord(planRecord?.spec)
  const planObjective = asRecord(planRecord?.objective)

  const runRecord = asRecord(runDetail.data)
  const runPayload = asRecord(runRecord?.payload)
  const runSummary = asRecord(runPayload?.summary)
  const runResults = asArray(runPayload?.results)

  const evaluationRecord = asRecord(evaluationDetail.data)
  const evaluationAssessment = asRecord(evaluationRecord?.assessment)
  const evaluationPromotion = asRecord(evaluationRecord?.promotion)

  const executionRecord = asRecord(lastExecutionPayload)
  const executionTrainingLab = asRecord(executionRecord?.training_lab)
  const executionRun = asRecord(executionTrainingLab?.run)
  const executionSummary = asRecord(executionRecord?.summary)

  return (
    <div className="page-grid page-grid--training" data-testid="training-lab-page">
      <section className="metrics-grid" data-testid="training-lab-overview">
        <MetricCard hint="最近 artifact 列表" label="计划数" testId="metric-plan-count" value={plans.data?.count ?? 0} />
        <MetricCard hint="最近运行记录" label="运行数" testId="metric-run-count" value={runs.data?.count ?? 0} />
        <MetricCard hint="最近评估记录" label="评估数" testId="metric-evaluation-count" value={evaluations.data?.count ?? 0} />
        <MetricCard hint="SSE 捕获的近期事件" label="事件流" testId="metric-event-count" value={eventStream.events.length} />
        <MetricCard hint="是否保持 mock" label="表单轮次" testId="metric-form-rounds" value={form.rounds} />
        <MetricCard hint={form.mock ? '使用模拟数据' : '真实数据/离线库'} label="Mock 开关" testId="metric-form-mock" value={form.mock ? 'ON' : 'OFF'} />
      </section>

      <Panel title="创建训练计划">
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
            <span>Mock 模式</span>
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
          <button className="button button--secondary" data-testid="execute-training-plan" disabled={executePlan.isPending} onClick={submitExecutePlan} type="button">
            {executePlan.isPending ? '执行中...' : '执行选中计划'}
          </button>
        </div>
        {lastError ? <ErrorState error={new ApiError(lastError)} /> : null}
      </Panel>

      <Panel title="训练资产浏览器">
        <div className="tab-row" data-testid="training-asset-tabs">
          <button className={`tab-button${activeTab === 'plans' ? ' tab-button--active' : ''}`} data-testid="tab-plans" onClick={() => setActiveTab('plans')} type="button">计划</button>
          <button className={`tab-button${activeTab === 'runs' ? ' tab-button--active' : ''}`} data-testid="tab-runs" onClick={() => setActiveTab('runs')} type="button">运行</button>
          <button className={`tab-button${activeTab === 'evaluations' ? ' tab-button--active' : ''}`} data-testid="tab-evaluations" onClick={() => setActiveTab('evaluations')} type="button">评估</button>
        </div>

        {activeTab === 'plans' ? (
          <div className="detail-layout">
            <div className="detail-layout__sidebar" data-testid="plans-list">
              {plans.isLoading ? <LoadingState label="正在加载计划列表..." /> : null}
              {plans.error ? <ErrorState error={plans.error} /> : null}
              {plans.data && plans.data.items.length > 0 ? (
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
                        <span>{String(item.goal ?? item.source ?? '--')}</span>
                        <span>{formatDateTime(item.created_at)}</span>
                      </button>
                    )
                  })}
                </div>
              ) : null}
              {plans.data && plans.data.items.length === 0 ? <EmptyState title="暂无训练计划" /> : null}
            </div>
            <div className="detail-layout__main" data-testid="plan-detail-panel">
              {selectedPlanSummary ? (
                <DetailSection
                  title="训练计划"
                  metrics={[
                    { label: '状态', value: readString(planRecord?.status), hint: readString(planRecord?.source) },
                    { label: '轮次', value: readNumber(planSpec?.rounds) ?? '--', hint: `detail=${readString(planSpec?.detail_mode)}` },
                    { label: 'Mock', value: planSpec?.mock === true ? 'ON' : 'OFF', hint: readString(planRecord?.plan_id) },
                    { label: '标签数', value: asArray(planObjective?.tags).length, hint: formatDateTime(planRecord?.created_at) },
                  ]}
                  summary={[
                    { label: 'Plan ID', value: planRecord?.plan_id },
                    { label: 'Goal', value: planObjective?.goal },
                    { label: 'Notes', value: planObjective?.notes },
                    { label: 'Source', value: planRecord?.source },
                    { label: 'Auto Generated', value: planRecord?.auto_generated },
                    { label: 'Latest Run', value: planRecord?.last_run_id },
                  ]}
                  raw={planDetail.data ?? selectedPlanSummary}
                  rawTestId="plan-json-panel"
                  extra={
                    <>
                      <h4 className="detail-section__title">Spec / Objective</h4>
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
                    </>
                  }
                />
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
              {runs.data && runs.data.items.length > 0 ? (
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
              {selectedRunSummary ? (
                <DetailSection
                  title="训练运行"
                  metrics={[
                    { label: '状态', value: readString(runRecord?.status), hint: readString(runRecord?.run_id), testId: 'run-detail-status' },
                    { label: '结果数', value: runResults.length, hint: `plan=${readString(runRecord?.plan_id)}` },
                    { label: '成功轮次', value: readNumber(runSummary?.success_cycles) ?? '--', hint: `总计 ${readNumber(runSummary?.total_cycles) ?? '--'}` },
                    { label: '失败轮次', value: readNumber(runSummary?.failed_cycles) ?? '--', hint: readString(runRecord?.error) },
                  ]}
                  summary={[
                    { label: 'Run ID', value: runRecord?.run_id },
                    { label: 'Plan ID', value: runRecord?.plan_id },
                    { label: 'Created At', value: formatDateTime(runRecord?.created_at) },
                    { label: 'Source', value: asRecord(runRecord?.plan)?.source },
                    { label: 'Goal', value: asRecord(asRecord(runRecord?.plan)?.objective)?.goal },
                  ]}
                  raw={runDetail.data ?? selectedRunSummary}
                  rawTestId="run-json-panel"
                  extra={
                    <>
                      <h4 className="detail-section__title">周期结果卡片</h4>
                      <ResultCards results={runResults} />
                    </>
                  }
                />
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
              {evaluations.data && evaluations.data.items.length > 0 ? (
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
              {selectedEvaluationSummary ? (
                <DetailSection
                  title="训练评估"
                  metrics={[
                    { label: '状态', value: readString(evaluationRecord?.status), hint: readString(evaluationRecord?.run_id), testId: 'evaluation-detail-status' },
                    { label: '成功', value: readNumber(evaluationAssessment?.success_count) ?? '--', hint: `总计 ${readNumber(evaluationAssessment?.total_results) ?? '--'}` },
                    { label: '无数据', value: readNumber(evaluationAssessment?.no_data_count) ?? '--', hint: `错误 ${readNumber(evaluationAssessment?.error_count) ?? '--'}` },
                    { label: '平均收益', value: formatPercent(evaluationAssessment?.avg_return_pct), hint: `Benchmark ${formatPercent((readNumber(evaluationAssessment?.benchmark_pass_rate) ?? 0) * 100)}` },
                  ]}
                  summary={[
                    { label: 'Run ID', value: evaluationRecord?.run_id },
                    { label: 'Plan ID', value: evaluationRecord?.plan_id },
                    { label: 'Created At', value: formatDateTime(evaluationRecord?.created_at) },
                    { label: 'Promotion Status', value: evaluationPromotion?.status },
                    { label: 'Promotion Reason', value: evaluationPromotion?.reason },
                    { label: 'Error', value: evaluationRecord?.error },
                  ]}
                  raw={evaluationDetail.data ?? selectedEvaluationSummary}
                  rawTestId="evaluation-json-panel"
                  extra={
                    <>
                      <h4 className="detail-section__title">Promotion Gate</h4>
                      <div className="split-grid">
                        <KeyValueList
                          entries={[
                            { label: 'Status', value: evaluationPromotion?.status },
                            { label: 'Reason', value: evaluationPromotion?.reason },
                            { label: 'Avg Return', value: formatPercent(evaluationPromotion?.avg_return_pct) },
                          ]}
                        />
                        <KeyValueList
                          entries={[
                            { label: 'Strategy Score', value: evaluationPromotion?.avg_strategy_score },
                            { label: 'Benchmark Pass Rate', value: evaluationPromotion?.benchmark_pass_rate },
                            { label: 'Selected Baseline', value: evaluationPromotion?.selected_baseline },
                          ]}
                        />
                      </div>
                    </>
                  }
                />
              ) : (
                <EmptyState title="请选择一条评估记录" />
              )}
            </div>
          </div>
        ) : null}
      </Panel>

      <Panel title="实时事件流">
        <div className="event-stream-header">
          <span data-testid="event-stream-status">{eventStream.connected ? '已连接 /api/events' : '事件流重连中'}</span>
          {eventStream.lastError ? <span className="text-danger">{eventStream.lastError}</span> : null}
        </div>
        {eventStream.events.length === 0 ? (
          <EmptyState title="暂无实时事件" />
        ) : (
          <div className="event-stream" data-testid="event-stream-list">
            {eventStream.events.map((event) => (
              <article className="event-card" key={event.id}>
                <header>
                  <strong>{event.type}</strong>
                  <span>{formatDateTime(event.receivedAt)}</span>
                </header>
                <pre>{prettyJson(event.payload)}</pre>
              </article>
            ))}
          </div>
        )}
      </Panel>

      <Panel title="最近执行结果">
        {executionRecord ? (
          <DetailSection
            title="执行返回"
            metrics={[
              { label: '执行状态', value: readString(executionRecord?.status), hint: readString(executionRun?.run_id), testId: 'execution-status' },
              { label: '总轮次', value: readNumber(executionSummary?.total_cycles) ?? '--', hint: `成功 ${readNumber(executionSummary?.success_cycles) ?? '--'}` },
              { label: '失败轮次', value: readNumber(executionSummary?.failed_cycles) ?? '--', hint: `计划 ${readString(asRecord(executionTrainingLab?.plan)?.plan_id)}` },
            ]}
            summary={[
              { label: 'Plan ID', value: asRecord(executionTrainingLab?.plan)?.plan_id },
              { label: 'Run ID', value: executionRun?.run_id },
              { label: 'Evaluation Run ID', value: asRecord(executionTrainingLab?.evaluation)?.run_id },
            ]}
            raw={lastExecutionPayload}
            rawTestId="execution-json-panel"
          />
        ) : (
          <EmptyState title="执行计划后将在这里展示结构化结果与返回 JSON" />
        )}
      </Panel>
    </div>
  )
}
