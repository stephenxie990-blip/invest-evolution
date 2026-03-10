import { expect, test } from '@playwright/test'

import { AppShellPage } from './pages/AppShellPage'
import { ModelsPage } from './pages/ModelsPage'

const quickPayload = {
  mode: 'quick',
  snapshot: {
    ts: '2026-03-10T10:00:00',
    detail_mode: 'fast',
    model: 'momentum_v1',
    runtime: { state: 'idle' },
    body: { total_cycles: 8, success_cycles: 6, failed_cycles: 2 },
    strategies: { total: 4, enabled: 3 },
    data: { latest_date: '20260310', quality: { health_status: 'healthy' } },
    training_lab: { plan_count: 2, run_count: 4, evaluation_count: 2 },
    brain: {},
    memory: {},
    bridge: {},
    plugins: {},
    config: {},
  },
}

const contractPayload = {
  contract_id: 'frontend-api-contract',
  version: 'v1',
  frontend_shell_mount: '/app',
  api_base: '/api',
  endpoints: [
    { group: 'models', path: '/api/investment-models', method: 'GET' },
    { group: 'models', path: '/api/leaderboard', method: 'GET' },
    { group: 'models', path: '/api/allocator', method: 'GET' },
    { group: 'strategies', path: '/api/strategies', method: 'GET' },
  ],
}

test.describe('Models', () => {
  test('shows model overview, leaderboard, allocator, and strategy reload flow', async ({ page }) => {
    await page.route('**/api/lab/status/quick', async (route) => {
      await route.fulfill({ json: quickPayload })
    })

    await page.route('**/api/contracts/frontend-v1', async (route) => {
      await route.fulfill({ json: contractPayload })
    })

    await page.route('**/api/investment-models', async (route) => {
      await route.fulfill({
        json: {
          items: ['momentum_v1', 'value_v2', 'quality_v1'],
          active_model: 'momentum_v1',
          active_config: 'invest/models/configs/momentum_v1.yaml',
        },
      })
    })

    await page.route('**/api/leaderboard', async (route) => {
      await route.fulfill({
        json: {
          generated_at: '2026-03-10T11:00:00',
          total_records: 18,
          total_models: 3,
          best_model: {
            model_name: 'momentum_v1',
            config_name: 'momentum_v1.yaml',
            score: 12.8,
            avg_return_pct: 4.5,
            benchmark_pass_rate: 0.83,
          },
          entries: [
            {
              rank: 1,
              model_name: 'momentum_v1',
              config_name: 'momentum_v1.yaml',
              score: 12.8,
              avg_return_pct: 4.5,
              avg_sharpe_ratio: 1.35,
              benchmark_pass_rate: 0.83,
            },
            {
              rank: 2,
              model_name: 'value_v2',
              config_name: 'value_v2.yaml',
              score: 10.1,
              avg_return_pct: 3.1,
              avg_sharpe_ratio: 1.11,
              benchmark_pass_rate: 0.74,
            },
          ],
        },
      })
    })

    let oscillationAllocatorRequests = 0

    await page.route('**/api/allocator?regime=oscillation&top_n=3', async (route) => {
      oscillationAllocatorRequests += 1

      await route.fulfill({
        json: {
          leaderboard_generated_at: '2026-03-10T11:00:00',
          allocation: oscillationAllocatorRequests === 1
            ? {
                as_of_date: '20260310',
                regime: 'oscillation',
                active_models: ['momentum_v1', 'value_v2'],
                model_weights: { momentum_v1: 0.55, value_v2: 0.25 },
                selected_configs: { momentum_v1: 'momentum_v1.yaml', value_v2: 'value_v2.yaml' },
                cash_reserve: 0.2,
                confidence: 0.78,
                reasoning: '震荡市偏重动量与价值组合。',
              }
            : {
                as_of_date: '20260310',
                regime: 'oscillation',
                active_models: ['quality_v1'],
                model_weights: { quality_v1: 0.64 },
                selected_configs: { quality_v1: 'quality_v1.yaml' },
                cash_reserve: 0.16,
                confidence: 0.81,
                reasoning: '同参数手动刷新后返回更新推荐。',
              },
        },
      })
    })

    await page.route('**/api/allocator?regime=bull&top_n=2', async (route) => {
      await route.fulfill({
        json: {
          leaderboard_generated_at: '2026-03-10T11:00:00',
          allocation: {
            as_of_date: '20260310',
            regime: 'bull',
            active_models: ['momentum_v1', 'quality_v1'],
            model_weights: { momentum_v1: 0.62, quality_v1: 0.18 },
            selected_configs: { momentum_v1: 'momentum_v1.yaml', quality_v1: 'quality_v1.yaml' },
            cash_reserve: 0.12,
            confidence: 0.86,
            reasoning: '牛市优先趋势延续模型。',
          },
        },
      })
    })

    await page.route('**/api/strategies', async (route) => {
      await route.fulfill({
        json: {
          count: 2,
          items: [
            {
              gene_id: 'momentum_trend',
              name: 'Momentum Trend',
              kind: 'md',
              path: 'strategies/generated/momentum_trend.md',
              enabled: true,
              priority: 80,
              description: '趋势强化',
            },
            {
              gene_id: 'value_rebound',
              name: 'Value Rebound',
              kind: 'json',
              path: 'strategies/generated/value_rebound.json',
              enabled: false,
              priority: 60,
              description: '价值回归',
            },
          ],
        },
      })
    })

    await page.route('**/api/strategies/reload', async (route) => {
      await route.fulfill({
        json: {
          count: 2,
          genes: [
            { gene_id: 'momentum_trend', name: 'Momentum Trend' },
            { gene_id: 'value_rebound', name: 'Value Rebound' },
          ],
        },
      })
    })

    const appShell = new AppShellPage(page)
    const models = new ModelsPage(page)

    await appShell.goto()
    await appShell.openModels()

    await expect(models.root).toBeVisible()
    await expect(models.modelCountMetric).toContainText('3')
    await expect(models.activeModelMetric).toContainText('momentum_v1')
    await expect(models.strategyCountMetric).toContainText('2')
    await expect(models.allocationCountMetric).toContainText('2')
    await expect(models.leaderboardTable).toContainText('momentum_v1')
    await expect(models.strategyTable).toContainText('Momentum Trend')
    await expect(models.allocatorPanel).toContainText('震荡市偏重动量与价值组合')

    await models.refreshAllocatorButton.click()

    await expect.poll(() => oscillationAllocatorRequests).toBe(2)
    await expect(models.allocatorPanel).toContainText('同参数手动刷新后返回更新推荐')
    await expect(models.allocatorPanel).toContainText('quality_v1')

    await models.regimeSelect.selectOption('bull')
    await models.topNSelect.selectOption('2')
    await models.refreshAllocatorButton.click()

    await expect(models.allocatorPanel).toContainText('牛市优先趋势延续模型')
    await expect(models.allocatorPanel).toContainText('quality_v1')

    await models.reloadStrategiesButton.click()
    await expect(models.reloadMessage).toContainText('已重新加载 2 个策略基因')
  })
})
