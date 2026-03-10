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
  test.beforeEach(async ({ page }) => {
    await page.route('**/api/lab/status/quick', async (route) => {
      await route.fulfill({
        json: {
          mode: 'quick',
          snapshot: {
            ts: '2026-03-10T10:00:00',
            detail_mode: 'fast',
            model: 'demo-model',
            runtime: { state: 'idle' },
            body: { total_cycles: 3, success_cycles: 2, failed_cycles: 1 },
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
          spec: { rounds: 3, mock: true, detail_mode: 'fast' },
          objective: {
            goal: '验证训练中心列表与详情',
            notes: '细化结构化展示层',
            tags: ['frontend', 'lab'],
          },
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
          plan: {
            plan_id: 'plan_demo_001',
            source: 'api',
            objective: { goal: '验证训练中心列表与详情' },
            spec: { rounds: 3, mock: true },
          },
          payload: {
            status: 'ok',
            summary: { total_cycles: 2, success_cycles: 1, failed_cycles: 1 },
            results: [
              {
                cycle_id: 'cycle_001',
                status: 'ok',
                return_pct: 1.5,
                selected_count: 3,
                trade_count: 5,
                selected_stocks: ['000001.SZ', '600519.SH'],
              },
              {
                cycle_id: 'cycle_002',
                status: 'error',
                return_pct: -0.4,
                selected_count: 0,
                trade_count: 1,
                error: 'mock failure',
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
          assessment: {
            total_results: 2,
            success_count: 1,
            no_data_count: 0,
            error_count: 1,
            avg_return_pct: 0.55,
            benchmark_pass_rate: 0.5,
          },
          promotion: {
            status: 'review',
            reason: '需要更多样本',
            avg_return_pct: 0.55,
            avg_strategy_score: 0.71,
            benchmark_pass_rate: 0.5,
            selected_baseline: 'hs300',
          },
        },
      })
    })

    await page.route('**/api/lab/training/plans', async (route) => {
      if (route.request().method() === 'POST') {
        await route.fulfill({
          json: {
            plan_id: 'plan_created_002',
            goal: '新建计划',
            created_at: '2026-03-10T11:00:00',
          },
        })
        return
      }
      await route.fallback()
    })

    await page.route('**/api/lab/training/plans/plan_demo_001/execute', async (route) => {
      await route.fulfill({
        json: {
          status: 'ok',
          training_lab: {
            plan: { plan_id: 'plan_demo_001' },
            run: { run_id: 'run_demo_001' },
            evaluation: { run_id: 'run_demo_001' },
          },
          summary: { total_cycles: 2, success_cycles: 1, failed_cycles: 1 },
        },
      })
    })

    await page.route('**/api/events', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: [
          'event: cycle_complete',
          'data: {"cycle_id":1,"cutoff_date":"20260310","return_pct":1.5,"is_profit":true,"selected_count":3,"selected_stocks":["000001.SZ","600519.SH"],"trade_count":5,"final_value":101500.0,"review_applied":true,"selection_mode":"mock","timestamp":"2026-03-10T10:11:00"}',
          '',
        ].join('\n'),
      })
    })
  })

  test('renders structured training details and execute flow', async ({ page }) => {
    const appShell = new AppShellPage(page)
    const trainingLab = new TrainingLabPage(page)

    await appShell.goto()
    await appShell.openTrainingLab()

    await expect(trainingLab.root).toBeVisible()
    await expect(trainingLab.overview).toBeVisible()
    await expect(trainingLab.planCountMetric).toContainText('1')
    await expect(trainingLab.runCountMetric).toContainText('1')
    await expect(trainingLab.evaluationCountMetric).toContainText('1')
    await expect(trainingLab.planItem('plan_demo_001')).toBeVisible()
    await expect(trainingLab.planDetailPanel).toContainText('plan_demo_001')
    await expect(trainingLab.planDetailPanel).toContainText('验证训练中心列表与详情')
    await expect(trainingLab.planJsonPanel).toContainText('detail_mode')
    await expect(trainingLab.eventStreamStatus).toHaveText(/(\/api\/events|重连中)/)

    await trainingLab.executePlanButton.click()
    await trainingLab.tabRuns.click()

    await expect(trainingLab.runItem('run_demo_001')).toBeVisible()
    await expect(trainingLab.runDetailPanel).toContainText('run_demo_001')
    await expect(trainingLab.runStatusMetric).toContainText('ok')
    await expect(trainingLab.resultCardGrid).toContainText('cycle_001')
    await expect(trainingLab.resultCardGrid).toContainText('000001.SZ')
    await expect(trainingLab.resultCardGrid).toContainText('mock failure')
    await expect(trainingLab.executionJsonPanel).toContainText('run_demo_001')
  })
})
