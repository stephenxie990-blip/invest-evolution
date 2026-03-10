import type { Locator, Page } from '@playwright/test'

export class DashboardPage {
  readonly page: Page
  readonly root: Locator
  readonly runtimeMetric: Locator
  readonly totalCyclesMetric: Locator
  readonly strategyCountMetric: Locator
  readonly labPlansMetric: Locator
  readonly latestDateMetric: Locator
  readonly modelMetric: Locator
  readonly toggleDeepStatus: Locator
  readonly statusBadges: Locator
  readonly deepStatusPanel: Locator

  constructor(page: Page) {
    this.page = page
    this.root = page.getByTestId('dashboard-page')
    this.runtimeMetric = page.getByTestId('metric-runtime-state')
    this.totalCyclesMetric = page.getByTestId('metric-total-cycles')
    this.strategyCountMetric = page.getByTestId('metric-strategy-count')
    this.labPlansMetric = page.getByTestId('metric-lab-plans')
    this.latestDateMetric = page.getByTestId('metric-latest-date')
    this.modelMetric = page.getByTestId('metric-model')
    this.toggleDeepStatus = page.getByTestId('toggle-deep-status')
    this.statusBadges = page.getByTestId('dashboard-status-badges')
    this.deepStatusPanel = page.getByTestId('deep-status-panel')
  }
}
