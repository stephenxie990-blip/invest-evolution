import { expect, test } from '@playwright/test'

import { AppShellPage } from './pages/AppShellPage'
import { DashboardPage } from './pages/DashboardPage'

const quickPayload = {
  mode: 'quick',
  snapshot: {
    ts: '2026-03-10T10:00:00',
    instance_id: 'quick-instance',
    workspace: '/Users/zhangsan/Desktop/投资进化系统v1.0',
    strategy_dir: 'strategies/generated',
    autopilot_enabled: true,
    heartbeat_enabled: true,
    detail_mode: 'fast',
    model: 'demo-model',
    runtime: { state: 'idle' },
    body: {
      total_cycles: 12,
      success_cycles: 9,
      failed_cycles: 3,
      last_result: {
        status: 'ok',
        return_pct: 1.88,
        selected_count: 6,
        trade_count: 9,
      },
    },
    strategies: { total: 18, enabled: 11 },
    data: {
      latest_date: '20260310',
      size_mb: 512,
      stock_count: 5200,
      kline_count: 3000000,
      intraday_60m_count: 180000,
      dragon_tiger_count: 420,
      capital_flow_count: 21000,
      quality: { health_status: 'healthy' },
    },
    training_lab: { plan_count: 4, run_count: 7, evaluation_count: 5 },
    brain: {},
    memory: {},
    bridge: {},
    plugins: {},
    config: {},
  },
}

const deepPayload = {
  mode: 'deep',
  snapshot: {
    ...quickPayload.snapshot,
    instance_id: 'deep-instance',
    model: 'deep-model',
    runtime: { state: 'idle', worker_count: 2 },
    memory: { cache_entries: 128 },
    config: { llm_router: 'priority' },
  },
}

test.describe('Dashboard', () => {
  test.beforeEach(async ({ page }) => {
    await page.route('**/api/lab/status/quick', async (route) => {
      await route.fulfill({ json: quickPayload })
    })

    await page.route('**/api/lab/status/deep', async (route) => {
      await route.fulfill({ json: deepPayload })
    })
  })

  test('renders overview metrics and loads deep diagnostics on demand', async ({ page }) => {
    const appShell = new AppShellPage(page)
    const dashboard = new DashboardPage(page)

    await appShell.goto()
    await appShell.openDashboard()

    await expect(dashboard.root).toBeVisible()
    await expect(dashboard.runtimeMetric).toContainText('idle')
    await expect(dashboard.totalCyclesMetric).toContainText('12')
    await expect(dashboard.strategyCountMetric).toContainText('18')
    await expect(dashboard.labPlansMetric).toContainText('4')
    await expect(dashboard.latestDateMetric).toContainText('20260310')
    await expect(dashboard.modelMetric).toContainText('demo-model')
    await expect(dashboard.statusBadges).toContainText('idle')
    await expect(dashboard.statusBadges).toContainText('healthy')

    await dashboard.toggleDeepStatus.click()

    await expect(dashboard.deepStatusPanel).toBeVisible()
    await expect(dashboard.deepStatusPanel).toContainText('deep-instance')
    await expect(dashboard.deepStatusPanel).toContainText('deep-model')
    await expect(dashboard.deepStatusPanel).toContainText('cache_entries')
  })
})
