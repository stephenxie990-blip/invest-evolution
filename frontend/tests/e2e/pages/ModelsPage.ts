import type { Locator, Page } from '@playwright/test'

export class ModelsPage {
  readonly page: Page
  readonly root: Locator
  readonly modelCountMetric: Locator
  readonly activeModelMetric: Locator
  readonly strategyCountMetric: Locator
  readonly allocationCountMetric: Locator
  readonly allocatorPanel: Locator
  readonly regimeSelect: Locator
  readonly topNSelect: Locator
  readonly refreshAllocatorButton: Locator
  readonly leaderboardTable: Locator
  readonly strategyTable: Locator
  readonly reloadStrategiesButton: Locator
  readonly reloadMessage: Locator

  constructor(page: Page) {
    this.page = page
    this.root = page.getByTestId('models-page')
    this.modelCountMetric = page.getByTestId('metric-model-count')
    this.activeModelMetric = page.getByTestId('metric-active-model')
    this.strategyCountMetric = page.getByTestId('metric-strategy-count-models')
    this.allocationCountMetric = page.getByTestId('metric-allocation-count')
    this.allocatorPanel = page.getByTestId('allocator-panel')
    this.regimeSelect = page.getByTestId('allocator-regime-select')
    this.topNSelect = page.getByTestId('allocator-topn-select')
    this.refreshAllocatorButton = page.getByTestId('refresh-allocator')
    this.leaderboardTable = page.getByTestId('leaderboard-table')
    this.strategyTable = page.getByTestId('strategy-table')
    this.reloadStrategiesButton = page.getByTestId('reload-strategies')
    this.reloadMessage = page.getByTestId('reload-strategies-message')
  }
}
