import { expect, test } from '@playwright/test'

import { AppShellPage } from './pages/AppShellPage'
import { TrainingLabPage } from './pages/TrainingLabPage'

const plansPayload = {
  count: 1,
  items: [
    {
      plan_id: 'plan_demo_001',
      goal: '验证训练中心列表与详情',
      created_at: '2026-03-10T10:00:00',
      source: 'api',
    },
  ],
}

const runsPayload = {
  count: 1,
  items: [
    {
      run_id: 'run_demo_001',
      plan_id: 'plan_demo_001',
      created_at: '2026-03-10T10:10:00',
      status: 'ok',
    },
  ],
}

const evaluationsPayload = {
  count: 1,
  items: [
    {
      run_id: 'run_demo_001',
      plan_id: 'plan_demo_001',
      created_at: '2026-03-10T10:12:00',
      status: 'ok',
    },
  ],
}

test.describe('Training Lab', () => {
  let capturedCreateBody: Record<string, unknown> | null
  let executeAttempts: number

  test.beforeEach(async ({ page }) => {
    capturedCreateBody = null
    executeAttempts = 0

    await page.route('**/api/lab/status/quick', async (route) => {
      await route.fulfill({
        json: {
          mode: 'quick',
          snapshot: {
            ts: '2026-03-10T10:00:00',
            detail_mode: 'fast',
            model: 'demo-model',
            runtime: { state: 'idle' },
            body: { total_cycles: 3, success_cycles: 1, failed_cycles: 2 },
            strategies: { total: 5, enabled: 3 },
            data: { latest_date: '20260310', quality: { health_status: 'healthy' } },
            training_lab: { plan_count: 1, run_count: 1, evaluation_count: 1 },
            brain: {}, memory: {}, bridge: {}, plugins: {}, config: {},
          },
        },
      })
    })

    await page.route('**/api/lab/training/plans?limit=10', async (route) => {
      await route.fulfill({ json: plansPayload })
    })

    await page.route('**/api/lab/training/plans/plan_demo_001', async (route) => {
      await route.fulfill({
        json: {
          plan_id: 'plan_demo_001',
          created_at: '2026-03-10T10:00:00',
          status: 'ready',
          source: 'api',
          auto_generated: false,
          spec: { rounds: 3, mock: false, detail_mode: 'fast' },
          objective: {
            goal: '验证训练中心列表与详情',
            notes: '细化结构化展示层',
            tags: ['frontend', 'lab'],
          },
          artifacts: {},
          last_run_id: 'run_demo_001',
        },
      })
    })

    await page.route('**/api/lab/training/plans/plan_created_002', async (route) => {
      await route.fulfill({
        json: {
          plan_id: 'plan_created_002',
          created_at: '2026-03-10T11:00:00',
          status: 'ready',
          source: 'api',
          auto_generated: false,
          spec: { rounds: 3, mock: false, detail_mode: 'fast' },
          objective: {
            goal: '新建计划',
            notes: '默认真实模式',
            tags: ['frontend', 'qa'],
          },
          artifacts: {},
          last_run_id: null,
        },
      })
    })

    await page.route('**/api/lab/training/runs?limit=10', async (route) => {
      await route.fulfill({ json: runsPayload })
    })

    await page.route('**/api/lab/training/runs/run_demo_001', async (route) => {
      await route.fulfill({
        json: {
          run_id: 'run_demo_001',
          plan_id: 'plan_demo_001',
          created_at: '2026-03-10T10:10:00',
          status: 'ok',
          error: '',
          plan: {
            plan_id: 'plan_demo_001',
            source: 'api',
            objective: { goal: '验证训练中心列表与详情' },
            spec: { rounds: 3, mock: false },
          },
          payload: {
            status: 'ok',
            summary: { total_cycles: 3, success_cycles: 1, failed_cycles: 2 },
            results: [
              {
                cycle_id: 'cycle_001',
                cutoff_date: '20260310',
                status: 'ok',
                requested_data_mode: 'live',
                effective_data_mode: 'offline',
                llm_mode: 'full',
                degraded: true,
                degrade_reason: 'online source timeout, switched to offline cache',
                return_pct: 1.5,
                selected_count: 3,
                trade_count: 5,
                benchmark_passed: false,
                review_applied: true,
                verdict: 'hold',
                promotion_verdict: 'review',
                selection_mode: 'factor_mix',
                selected_stocks: ['000001.SZ', '600519.SH'],
                cycle_result_path: 'runtime/cycle_001.json',
                selection_meeting_markdown_path: 'runtime/selection_001.md',
                review_meeting_markdown_path: 'runtime/review_001.md',
                evaluation_path: 'runtime/evaluation_001.json',
                optimization_events: [
                  {
                    cycle_id: 'cycle_001',
                    trigger: 'loss_optimization',
                    stage: 'optimizer',
                    notes: 'learning rate tuned',
                    applied_change: {
                      params: { learning_rate: 0.2, stop_loss: 0.08 },
                      scoring: { momentum: 0.72 },
                    },
                  },
                  {
                    cycle_id: 'cycle_001',
                    trigger: 'review',
                    stage: 'review_meeting',
                    applied_change: {
                      params: { rebalance_window: 10 },
                    },
                  },
                ],
              },
              {
                cycle_id: 'cycle_002',
                cutoff_date: '20260311',
                status: 'no_data',
                stage: 'selection',
                reason: 'offline universe empty',
                requested_data_mode: 'live',
                effective_data_mode: 'offline',
                llm_mode: 'full',
                degraded: false,
                return_pct: 0,
                selected_count: 0,
                trade_count: 0,
              },
              {
                cycle_id: 'cycle_003',
                cutoff_date: '20260312',
                status: 'error',
                requested_data_mode: 'live',
                effective_data_mode: 'unavailable',
                llm_mode: 'full',
                degraded: false,
                error: '真实训练数据不可用',
                error_payload: {
                  error: '真实训练数据不可用',
                  error_code: 'data_source_unavailable',
                  cutoff_date: '20260312',
                  stock_count: 320,
                  min_history_days: 120,
                  requested_data_mode: 'live',
                  available_sources: { offline: false, online: false, mock: true },
                  offline_diagnostics: {
                    issues: ['离线库覆盖不足'],
                    suggestions: ['补齐离线日线库'],
                  },
                  online_error: 'network unavailable',
                  suggestions: ['切换到 Smoke / Demo 模式', '检查离线库覆盖'],
                  allow_mock_fallback: false,
                },
              },
            ],
          },
        },
      })
    })

    await page.route('**/api/lab/training/evaluations?limit=10', async (route) => {
      await route.fulfill({ json: evaluationsPayload })
    })

    await page.route('**/api/lab/training/evaluations/run_demo_001', async (route) => {
      await route.fulfill({
        json: {
          run_id: 'run_demo_001',
          plan_id: 'plan_demo_001',
          created_at: '2026-03-10T10:12:00',
          status: 'ok',
          objective: { goal: '验证训练中心列表与详情' },
          spec: { rounds: 3, mock: false },
          assessment: {
            total_results: 3,
            success_count: 1,
            no_data_count: 1,
            error_count: 1,
            avg_return_pct: 0.5,
            benchmark_pass_rate: 0,
          },
          promotion: {
            verdict: 'hold',
            status: 'review',
            reason: '需要更多样本',
            avg_return_pct: 0.5,
            avg_strategy_score: 0.71,
            benchmark_pass_rate: 0,
            selected_baseline: 'hs300',
            checks: [{ name: 'benchmark', passed: false }],
            gates: { benchmark: false, risk: true },
          },
          error: '',
          artifacts: { evaluation_path: 'runtime/evaluation_001.json' },
        },
      })
    })

    await page.route('**/api/lab/training/plans', async (route) => {
      if (route.request().method() === 'POST') {
        capturedCreateBody = route.request().postDataJSON() as Record<string, unknown>
        await route.fulfill({
          json: {
            plan_id: 'plan_created_002',
            created_at: '2026-03-10T11:00:00',
            status: 'ready',
            source: 'api',
            auto_generated: false,
            spec: { rounds: 3, mock: false, detail_mode: 'fast' },
            objective: {
              goal: '新建计划',
              notes: '默认真实模式',
              tags: ['frontend', 'qa'],
            },
            artifacts: {},
            last_run_id: null,
          },
        })
        return
      }
      await route.fallback()
    })

    await page.route('**/api/lab/training/plans/*/execute', async (route) => {
      executeAttempts += 1

      if (executeAttempts === 1) {
        await route.fulfill({
          json: {
            status: 'ok',
            rounds: 3,
            results: [],
            training_lab: {
              plan: { plan_id: 'plan_demo_001' },
              run: { run_id: 'run_demo_001' },
              evaluation: { run_id: 'run_demo_001' },
            },
            summary: { total_cycles: 3, success_cycles: 1, failed_cycles: 2 },
          },
        })
        return
      }

      await route.fulfill({
        status: 503,
        json: {
          error: '真实训练数据不可用',
          error_code: 'data_source_unavailable',
          cutoff_date: '20260312',
          stock_count: 320,
          min_history_days: 120,
          requested_data_mode: 'live',
          available_sources: { offline: false, online: false, mock: true },
          offline_diagnostics: {
            issues: ['离线库覆盖不足'],
            suggestions: ['补齐离线日线库'],
          },
          online_error: 'network unavailable',
          suggestions: ['切换到 Smoke / Demo 模式', '检查离线库覆盖'],
          allow_mock_fallback: false,
        },
      })
    })

    await page.route('**/api/events', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: [
          'event: cycle_start',
          'data: {"cycle_id":1,"cutoff_date":"20260310","phase":"selection","requested_data_mode":"live","llm_mode":"full","timestamp":"2026-03-10T10:10:01"}',
          '',
          'event: agent_status',
          'data: {"timestamp":"2026-03-10T10:10:02","cycle_id":1,"agent":"researcher","status":"running","message":"scanning candidates","stage":"selection","progress_pct":40}',
          '',
          'event: module_log',
          'data: {"timestamp":"2026-03-10T10:10:03","cycle_id":1,"module":"selector","title":"候选池构建","message":"完成候选池过滤","kind":"selector","level":"info","requested_data_mode":"live","effective_data_mode":"offline","llm_mode":"full"}',
          '',
          'event: meeting_speech',
          'data: {"timestamp":"2026-03-10T10:10:04","cycle_id":1,"meeting":"selection_meeting","speaker":"allocator","agent":"allocator","speech":"增配低波动因子并降低换手。","role":"chair"}',
          '',
          'event: cycle_complete',
          'data: {"cycle_id":1,"cutoff_date":"20260310","return_pct":1.5,"is_profit":true,"selected_count":3,"selected_stocks":["000001.SZ","600519.SH"],"trade_count":5,"final_value":101500.0,"review_applied":true,"selection_mode":"factor_mix","requested_data_mode":"live","effective_data_mode":"offline","llm_mode":"full","degraded":true,"degrade_reason":"online source timeout, switched to offline cache","timestamp":"2026-03-10T10:11:00"}',
          '',
          'event: cycle_skipped',
          'data: {"status":"no_data","cycle_id":2,"cutoff_date":"20260311","stage":"selection","reason":"offline universe empty","requested_data_mode":"live","effective_data_mode":"offline","llm_mode":"full","degraded":false,"degrade_reason":"","timestamp":"2026-03-10T10:12:00"}',
          '',
        ].join('\n'),
      })
    })
  })

  test('renders training-lab semantic details, realtime panels, and structured errors', async ({ page }) => {
    const appShell = new AppShellPage(page)
    const trainingLab = new TrainingLabPage(page)

    await appShell.goto()
    await appShell.openTrainingLab()

    await expect(trainingLab.root).toBeVisible()
    await expect(trainingLab.overview).toBeVisible()
    await expect(trainingLab.planCountMetric).toContainText('1')
    await expect(trainingLab.runCountMetric).toContainText('1')
    await expect(trainingLab.evaluationCountMetric).toContainText('1')
    await expect(trainingLab.modeNote).toContainText('默认使用真实数据 / 离线库')
    await expect(trainingLab.mockCheckbox).not.toBeChecked()

    await trainingLab.createPlanButton.click()
    expect(capturedCreateBody).not.toBeNull()
    expect(capturedCreateBody).toMatchObject({
      rounds: 3,
      mock: false,
      goal: '构建新前端训练实验室的第一条验证链路',
      detail_mode: 'fast',
    })

    await expect(trainingLab.planJsonPanel).toContainText('detail_mode')

    await trainingLab.executePlanButton.click()
    await expect(trainingLab.executionJsonPanel).toContainText('run_demo_001')

    await trainingLab.tabRuns.click()
    await expect(trainingLab.runItem('run_demo_001')).toBeVisible()
    await expect(trainingLab.runDetailPanel).toContainText('run_demo_001')
    await expect(trainingLab.runStatusMetric).toContainText('ok')
    await expect(trainingLab.resultCardGrid).toContainText('cycle_001')
    await expect(trainingLab.resultCardGrid).toContainText('请求模式: live')
    await expect(trainingLab.resultCardGrid).toContainText('实际模式: offline')
    await expect(trainingLab.resultCardGrid).toContainText('真实训练数据不可用')
    await expect(trainingLab.resultCard('cycle_001')).toContainText('000001.SZ')
    await expect(trainingLab.resultCard('cycle_001')).toContainText('online source timeout, switched to offline cache')
    await expect(trainingLab.strategyDiffList).toContainText('learning_rate')
    await expect(trainingLab.strategyDiffList).toContainText('review_meeting')

    await trainingLab.tabEvaluations.click()
    await expect(trainingLab.evaluationItem('run_demo_001')).toBeVisible()
    await expect(trainingLab.evaluationDetailPanel).toContainText('Promotion Verdict')
    await expect(trainingLab.evaluationWarning).toBeVisible()

    await expect(trainingLab.timelinePanel).toBeVisible()
    await expect(trainingLab.speechCardList).toContainText('增配低波动因子并降低换手。')
    await trainingLab.timelineCategorySpeech.click()
    await expect(trainingLab.timelineList).toContainText('selection_meeting')
    await trainingLab.timelineCycleFilter.selectOption('1')
    await expect(trainingLab.timelineList).toContainText('allocator')

    await trainingLab.executePlanButton.click()
    await expect(trainingLab.specialErrorCard).toBeVisible()
    await expect(trainingLab.specialErrorCard).toContainText('503 / data_source_unavailable')
    await expect(trainingLab.specialErrorCard).toContainText('切换到 Smoke / Demo 模式')
  })
})
