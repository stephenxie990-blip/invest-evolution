import { useMemo, useState } from 'react'

import { useFrontendContract } from '@/shared/api/contracts'
import {
  useAllocator,
  useInvestmentModels,
  useLeaderboard,
  useReloadStrategies,
  useStrategies,
} from '@/shared/api/models'
import { ApiError } from '@/shared/api/errors'
import { formatDateTime } from '@/shared/lib/format'
import { ErrorState, LoadingState } from '@/shared/ui/AsyncState'
import { DataTable } from '@/shared/ui/DataTable'
import { JsonView } from '@/shared/ui/JsonView'
import { KeyValueList } from '@/shared/ui/KeyValueList'
import { MetricCard } from '@/shared/ui/MetricCard'
import { Panel } from '@/shared/ui/Panel'
import { StatusBadge } from '@/shared/ui/StatusBadge'

type UnknownRecord = Record<string, unknown>

type AllocatorFormState = {
  regime: 'bull' | 'bear' | 'oscillation' | 'unknown'
  topN: number
}

const defaultAllocator: AllocatorFormState = {
  regime: 'oscillation',
  topN: 3,
}

function asRecord(value: unknown): UnknownRecord | null {
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as UnknownRecord) : null
}

function asRecords(value: unknown): UnknownRecord[] {
  return Array.isArray(value)
    ? value.filter((item): item is UnknownRecord => Boolean(asRecord(item))).map((item) => item as UnknownRecord)
    : []
}

function asStringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.map((item) => String(item)) : []
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

function formatRatio(value: unknown): string {
  const number = readNumber(value)
  if (number === null) {
    return '--'
  }
  return number.toFixed(2)
}

function toneFromEnabled(value: unknown): 'good' | 'warn' {
  return Boolean(value) ? 'good' : 'warn'
}

function isSameAllocatorQuery(left: AllocatorFormState, right: AllocatorFormState): boolean {
  return left.regime === right.regime && left.topN === right.topN
}

export function ModelsPage() {
  const contract = useFrontendContract()
  const investmentModels = useInvestmentModels()
  const leaderboard = useLeaderboard()
  const strategies = useStrategies()
  const reloadStrategies = useReloadStrategies()

  const [allocatorDraft, setAllocatorDraft] = useState<AllocatorFormState>(defaultAllocator)
  const [allocatorQuery, setAllocatorQuery] = useState<AllocatorFormState>(defaultAllocator)
  const [reloadMessage, setReloadMessage] = useState<string | null>(null)
  const [reloadError, setReloadError] = useState<string | null>(null)

  const allocator = useAllocator(allocatorQuery.regime, allocatorQuery.topN)

  const bestModel = asRecord(leaderboard.data?.best_model)
  const leaderboardEntries = asRecords(leaderboard.data?.entries)
  const allocation = asRecord(allocator.data?.allocation)
  const strategyRows = strategies.data?.items ?? []
  const activeModels = asStringArray(allocation?.active_models)
  const modelWeights = asRecord(allocation?.model_weights) ?? {}

  const leaderboardRows = useMemo(
    () => leaderboardEntries.slice(0, 5).map((entry) => ({
      rank: entry.rank,
      model_name: entry.model_name,
      config_name: entry.config_name,
      score: formatRatio(entry.score),
      avg_return_pct: formatPercent(entry.avg_return_pct),
      avg_sharpe_ratio: formatRatio(entry.avg_sharpe_ratio),
      benchmark_pass_rate: formatPercent((readNumber(entry.benchmark_pass_rate) ?? 0) * 100),
    })),
    [leaderboardEntries],
  )

  const contractRows = useMemo(
    () => contract.data?.endpoints.filter((item) => {
      const group = String(item.group ?? '')
      return group === 'models' || group === 'strategies'
    }) ?? [],
    [contract.data],
  )

  const triggerAllocatorRefresh = async () => {
    if (isSameAllocatorQuery(allocatorDraft, allocatorQuery)) {
      await allocator.refetch()
      return
    }

    setAllocatorQuery(allocatorDraft)
  }

  const triggerReloadStrategies = async () => {
    setReloadMessage(null)
    setReloadError(null)
    try {
      const payload = await reloadStrategies.mutateAsync()
      setReloadMessage(`已重新加载 ${payload.count} 个策略基因`)
    } catch (error) {
      setReloadError(error instanceof ApiError ? error.message : '策略重载失败')
    }
  }

  return (
    <div className="page-grid" data-testid="models-page">
      <section className="metrics-grid">
        <MetricCard label="模型数" testId="metric-model-count" value={investmentModels.data?.items.length ?? 0} hint="投资模型注册表" />
        <MetricCard label="当前模型" testId="metric-active-model" value={investmentModels.data?.active_model ?? '--'} hint={investmentModels.data?.active_config ?? '--'} />
        <MetricCard label="策略基因" testId="metric-strategy-count-models" value={strategies.data?.count ?? 0} hint={`启用 ${strategyRows.filter((item) => item.enabled).length}`} />
        <MetricCard label="分配模型" testId="metric-allocation-count" value={activeModels.length} hint={`regime=${allocatorQuery.regime}`} />
        <MetricCard label="排行榜模型" value={leaderboard.data?.total_models ?? 0} hint={`records ${leaderboard.data?.total_records ?? 0}`} />
        <MetricCard label="最佳收益" value={formatPercent(bestModel?.avg_return_pct)} hint={readString(bestModel?.model_name)} />
      </section>

      <Panel title="模型概览">
        {investmentModels.isLoading ? <LoadingState label="正在加载模型注册表..." /> : null}
        {investmentModels.error ? <ErrorState error={investmentModels.error} /> : null}
        {investmentModels.data ? (
          <div className="content-stack">
            <KeyValueList
              entries={[
                { label: 'Active Model', value: investmentModels.data.active_model },
                { label: 'Active Config', value: investmentModels.data.active_config },
                { label: 'Leaderboard Generated', value: formatDateTime(leaderboard.data?.generated_at) },
                { label: 'Top Model', value: bestModel?.model_name },
              ]}
            />
            <div className="status-badge-row">
              {investmentModels.data.items.map((name) => (
                <StatusBadge key={name} tone={name === investmentModels.data.active_model ? 'good' : 'neutral'}>
                  {name}
                </StatusBadge>
              ))}
            </div>
          </div>
        ) : null}
      </Panel>

      <Panel title="Allocator 推荐">
        <div className="content-stack" data-testid="allocator-panel">
          <div className="control-row">
            <label>
              <span>Regime</span>
              <select
                className="input"
                data-testid="allocator-regime-select"
                onChange={(event) => setAllocatorDraft((current) => ({
                  ...current,
                  regime: event.target.value as AllocatorFormState['regime'],
                }))}
                value={allocatorDraft.regime}
              >
                <option value="bull">bull</option>
                <option value="bear">bear</option>
                <option value="oscillation">oscillation</option>
                <option value="unknown">unknown</option>
              </select>
            </label>
            <label>
              <span>Top N</span>
              <select
                className="input"
                data-testid="allocator-topn-select"
                onChange={(event) => setAllocatorDraft((current) => ({
                  ...current,
                  topN: Number(event.target.value),
                }))}
                value={String(allocatorDraft.topN)}
              >
                {[1, 2, 3, 4].map((value) => (
                  <option key={value} value={value}>{value}</option>
                ))}
              </select>
            </label>
            <div className="control-row__actions">
              <button className="button button--secondary" data-testid="refresh-allocator" onClick={triggerAllocatorRefresh} type="button">
                刷新推荐
              </button>
            </div>
          </div>

          {allocator.isLoading ? <LoadingState label="正在生成 allocation recommendation..." /> : null}
          {allocator.error ? <ErrorState error={allocator.error} /> : null}
          {allocator.data ? (
            <>
              <div className="split-grid">
                <KeyValueList
                  entries={[
                    { label: 'Regime', value: allocation?.regime },
                    { label: 'As Of', value: allocation?.as_of_date },
                    { label: 'Cash Reserve', value: formatPercent((readNumber(allocation?.cash_reserve) ?? 0) * 100) },
                    { label: 'Confidence', value: formatRatio(allocation?.confidence) },
                  ]}
                />
                <KeyValueList
                  entries={[
                    { label: 'Leaderboard Generated', value: formatDateTime(allocator.data.leaderboard_generated_at) },
                    { label: 'Active Models', value: activeModels.join(', ') },
                    { label: 'Reasoning', value: allocation?.reasoning },
                    { label: 'Configs', value: Object.values(asRecord(allocation?.selected_configs) ?? {}).join(', ') },
                  ]}
                />
              </div>
              {Object.keys(modelWeights).length > 0 ? (
                <DataTable
                  columns={['model', 'weight']}
                  rows={Object.entries(modelWeights).map(([model, weight]) => ({
                    model,
                    weight: formatPercent((readNumber(weight) ?? 0) * 100),
                  }))}
                />
              ) : null}
            </>
          ) : null}
        </div>
      </Panel>

      <Panel title="排行榜预览">
        {leaderboard.isLoading ? <LoadingState label="正在读取 leaderboard..." /> : null}
        {leaderboard.error ? <ErrorState error={leaderboard.error} /> : null}
        {leaderboard.data ? (
          <div className="content-stack">
            <KeyValueList
              entries={[
                { label: 'Generated At', value: formatDateTime(leaderboard.data.generated_at) },
                { label: 'Total Models', value: leaderboard.data.total_models },
                { label: 'Total Records', value: leaderboard.data.total_records },
                { label: 'Best Model', value: bestModel?.model_name },
              ]}
            />
            {leaderboardRows.length > 0 ? <DataTable rows={leaderboardRows} testId="leaderboard-table" /> : null}
          </div>
        ) : null}
      </Panel>

      <Panel title="策略基因资产">
        <div className="content-stack">
          <div className="button-row">
            <button className="button" data-testid="reload-strategies" disabled={reloadStrategies.isPending} onClick={triggerReloadStrategies} type="button">
              {reloadStrategies.isPending ? '重载中...' : '重新加载策略'}
            </button>
          </div>
          {reloadMessage ? <div className="state-block" data-testid="reload-strategies-message">{reloadMessage}</div> : null}
          {reloadError ? <ErrorState error={new ApiError(reloadError)} /> : null}
          {strategies.isLoading ? <LoadingState label="正在读取策略基因..." /> : null}
          {strategies.error ? <ErrorState error={strategies.error} /> : null}
          {strategies.data ? (
            <div className="content-stack">
              <div className="status-badge-row">
                {strategyRows.map((item) => (
                  <StatusBadge key={item.gene_id} tone={toneFromEnabled(item.enabled)}>
                    {item.gene_id}
                  </StatusBadge>
                ))}
              </div>
              <DataTable
                columns={['gene_id', 'name', 'kind', 'enabled', 'priority', 'path']}
                rows={strategyRows.map((item) => ({
                  gene_id: item.gene_id,
                  name: item.name,
                  kind: item.kind,
                  enabled: item.enabled,
                  priority: item.priority,
                  path: item.path,
                }))}
                testId="strategy-table"
              />
            </div>
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
