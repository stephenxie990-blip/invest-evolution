import { useState } from 'react'

import { useDeepStatus, useQuickStatus } from '@/shared/api/status'
import { LoadingState, ErrorState } from '@/shared/ui/AsyncState'
import { JsonView } from '@/shared/ui/JsonView'
import { KeyValueList } from '@/shared/ui/KeyValueList'
import { MetricCard } from '@/shared/ui/MetricCard'
import { Panel } from '@/shared/ui/Panel'
import { StatusBadge } from '@/shared/ui/StatusBadge'
import { formatDateTime } from '@/shared/lib/format'

export function DashboardPage() {
  const [showDeep, setShowDeep] = useState(false)
  const quick = useQuickStatus()
  const deep = useDeepStatus(showDeep)

  if (quick.isLoading) {
    return <LoadingState label="正在加载快速状态..." />
  }

  if (quick.error || !quick.data) {
    return <ErrorState error={quick.error} />
  }

  const snapshot = quick.data.snapshot
  const body = snapshot.body as Record<string, unknown>
  const strategies = snapshot.strategies as Record<string, unknown>
  const data = snapshot.data as Record<string, unknown>
  const trainingLab = snapshot.training_lab as Record<string, unknown>
  const runtime = snapshot.runtime as Record<string, unknown>
  const lastResult = typeof body.last_result === 'object' && body.last_result ? body.last_result as Record<string, unknown> : null
  const healthStatus = typeof (data.quality as Record<string, unknown> | undefined)?.health_status === 'string'
    ? String((data.quality as Record<string, unknown>).health_status)
    : 'unknown'

  return (
    <div className="page-grid" data-testid="dashboard-page">
      <section className="metrics-grid" data-testid="dashboard-metrics">
        <MetricCard label="运行状态" value={String(runtime.state ?? '--')} hint={`模式: ${quick.data.mode}`} testId="metric-runtime-state" />
        <MetricCard label="训练总轮次" value={Number(body.total_cycles ?? 0)} hint={`成功 ${Number(body.success_cycles ?? 0)} / 失败 ${Number(body.failed_cycles ?? 0)}`} testId="metric-total-cycles" />
        <MetricCard label="策略基因" value={Number(strategies.total ?? 0)} hint={`已启用 ${Number(strategies.enabled ?? 0)}`} testId="metric-strategy-count" />
        <MetricCard label="实验室计划" value={Number(trainingLab.plan_count ?? 0)} hint={`Runs ${Number(trainingLab.run_count ?? 0)} / Eval ${Number(trainingLab.evaluation_count ?? 0)}`} testId="metric-lab-plans" />
        <MetricCard label="最新交易日" value={String(data.latest_date ?? '--')} hint={`质量 ${healthStatus}`} testId="metric-latest-date" />
        <MetricCard label="模型" value={String(snapshot.model ?? '--')} hint={`更新时间 ${formatDateTime(snapshot.ts)}`} testId="metric-model" />
      </section>

      <Panel
        title="快速状态摘要"
        actions={
          <button className="button button--secondary" data-testid="toggle-deep-status" onClick={() => setShowDeep((value) => !value)} type="button">
            {showDeep ? '隐藏深度诊断' : '加载深度诊断'}
          </button>
        }
      >
        <div className="summary-grid">
          <KeyValueList
            entries={[
              { label: '实例 ID', value: snapshot.instance_id },
              { label: '工作区', value: snapshot.workspace },
              { label: '策略目录', value: snapshot.strategy_dir },
              { label: '自动驾驶', value: snapshot.autopilot_enabled },
              { label: 'Heartbeat', value: snapshot.heartbeat_enabled },
              { label: '最后更新时间', value: formatDateTime(snapshot.ts) },
            ]}
          />
          <div className="status-badge-row" data-testid="dashboard-status-badges">
            <StatusBadge tone={runtime.state === 'idle' ? 'good' : 'warn'}>{String(runtime.state ?? 'unknown')}</StatusBadge>
            <StatusBadge tone={healthStatus === 'healthy' ? 'good' : healthStatus === 'warning' ? 'warn' : 'danger'}>{healthStatus}</StatusBadge>
          </div>
        </div>
      </Panel>

      <Panel title="最近训练结果">
        {lastResult ? (
          <KeyValueList
            entries={[
              { label: '状态', value: lastResult.status },
              { label: '收益率', value: lastResult.return_pct },
              { label: '选股数', value: lastResult.selected_count },
              { label: '交易数', value: lastResult.trade_count },
              { label: '错误', value: lastResult.error },
            ]}
          />
        ) : (
          <div className="state-block state-block--muted">暂无最近训练结果</div>
        )}
      </Panel>

      <Panel title="数据概况">
        <KeyValueList
          entries={[
            { label: '数据库大小(MB)', value: data.size_mb },
            { label: '股票数', value: data.stock_count },
            { label: '日线条数', value: data.kline_count },
            { label: '分钟线条数', value: data.intraday_60m_count },
            { label: '龙虎榜条数', value: data.dragon_tiger_count },
            { label: '资金流条数', value: data.capital_flow_count },
          ]}
        />
      </Panel>

      {showDeep ? (
        <Panel title="深度诊断 JSON">
          <div data-testid="deep-status-panel">
            {deep.isLoading ? <LoadingState label="正在加载深度诊断..." /> : null}
            {deep.error ? <ErrorState error={deep.error} /> : null}
            {deep.data ? <JsonView value={deep.data.snapshot} /> : null}
          </div>
        </Panel>
      ) : null}
    </div>
  )
}
