import { expect, test } from '@playwright/test'

import { AppShellPage } from './pages/AppShellPage'
import { DataPage } from './pages/DataPage'

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
    { group: 'data', path: '/api/data/status', method: 'GET' },
    { group: 'data', path: '/api/data/capital_flow', method: 'GET' },
    { group: 'data', path: '/api/data/dragon_tiger', method: 'GET' },
    { group: 'data', path: '/api/data/intraday_60m', method: 'GET' },
    { group: 'data', path: '/api/data/download', method: 'POST' },
  ],
}

test.describe('Data', () => {
  test('shows data summary, triggers sync, and previews queried dataset rows', async ({ page }) => {
    await page.route('**/api/lab/status/quick', async (route) => {
      await route.fulfill({ json: quickPayload })
    })

    await page.route('**/api/contracts/frontend-v1', async (route) => {
      await route.fulfill({ json: contractPayload })
    })

    await page.route('**/api/data/status**', async (route) => {
      const url = new URL(route.request().url())
      const refreshed = url.searchParams.get('refresh') === 'true'
      await route.fulfill({
        json: {
          db_path: 'runtime/market_data.db',
          size_mb: refreshed ? 734.5 : 730.2,
          stock_count: 5231,
          kline_count: 3123456,
          financial_count: 18000,
          calendar_count: 3400,
          status_count: 2100,
          factor_count: 122000,
          capital_flow_count: 45000,
          dragon_tiger_count: 380,
          intraday_60m_count: 160000,
          latest_date: '20260310',
          index_count: 12,
          index_kline_count: 23000,
          index_latest_date: '20260310',
          schema: 'canonical_v1',
          detail_mode: refreshed ? 'slow' : 'fast',
          quality: {
            health_status: refreshed ? 'warning' : 'healthy',
            latest_audit_at: '2026-03-10T12:00:00',
            missing_tables: refreshed ? ['factor_daily'] : [],
          },
        },
      })
    })

    await page.route('**/api/data/download', async (route) => {
      await route.fulfill({
        json: {
          status: 'started',
          message: '后台同步已启动',
        },
      })
    })

    let capitalFlowRequests = 0

    await page.route('**/api/data/capital_flow**', async (route) => {
      capitalFlowRequests += 1

      if (capitalFlowRequests === 1) {
        await route.fulfill({
          json: {
            count: 2,
            items: [
              {
                code: '000001.SZ',
                trade_date: '20260310',
                close: 12.34,
                pct_chg: 1.2,
                main_net_inflow: 2300000,
              },
              {
                code: '600519.SH',
                trade_date: '20260310',
                close: 1688.0,
                pct_chg: 0.8,
                main_net_inflow: 8200000,
              },
            ],
          },
        })
        return
      }

      await route.fulfill({
        json: {
          count: 1,
          items: [
            {
              code: '300750.SZ',
              trade_date: '20260310',
              close: 245.6,
              pct_chg: 2.1,
              main_net_inflow: 12500000,
            },
          ],
        },
      })
    })

    const appShell = new AppShellPage(page)
    const data = new DataPage(page)

    await appShell.goto()
    await appShell.openData()

    await expect(data.root).toBeVisible()
    await expect(data.latestDateMetric).toContainText('20260310')
    await expect(data.stockCountMetric).toContainText('5231')
    await expect(data.klineCountMetric).toContainText('3123456')
    await expect(data.qualityMetric).toContainText('healthy')

    await data.downloadButton.click()
    await expect(data.downloadMessage).toContainText('后台同步已启动')

    await data.refreshStatusButton.click()
    await expect(data.qualityMetric).toContainText('warning')

    await data.capitalFlowTab.click()
    await data.codesInput.fill('000001.SZ,600519.SH')
    await data.startInput.fill('20260301')
    await data.endInput.fill('20260310')
    await data.limitInput.fill('20')
    await data.queryButton.click()

    await expect(data.queryCountMetric).toContainText('2')
    await expect(data.previewTable).toContainText('000001.SZ')
    await expect(data.previewTable).toContainText('main_net_inflow')
    await expect(data.queryJsonPanel).toContainText('600519.SH')

    await data.queryButton.click()

    await expect(data.queryCountMetric).toContainText('1')
    await expect(data.previewTable).toContainText('300750.SZ')
    await expect(data.queryJsonPanel).toContainText('12500000')
  })
})
