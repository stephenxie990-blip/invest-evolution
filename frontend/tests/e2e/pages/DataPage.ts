import type { Locator, Page } from '@playwright/test'

export class DataPage {
  readonly page: Page
  readonly root: Locator
  readonly latestDateMetric: Locator
  readonly stockCountMetric: Locator
  readonly klineCountMetric: Locator
  readonly qualityMetric: Locator
  readonly refreshStatusButton: Locator
  readonly downloadButton: Locator
  readonly downloadMessage: Locator
  readonly capitalFlowTab: Locator
  readonly dragonTigerTab: Locator
  readonly intradayTab: Locator
  readonly codesInput: Locator
  readonly startInput: Locator
  readonly endInput: Locator
  readonly limitInput: Locator
  readonly queryButton: Locator
  readonly queryCountMetric: Locator
  readonly previewTable: Locator
  readonly queryJsonPanel: Locator

  constructor(page: Page) {
    this.page = page
    this.root = page.getByTestId('data-page')
    this.latestDateMetric = page.getByTestId('metric-data-latest-date')
    this.stockCountMetric = page.getByTestId('metric-data-stock-count')
    this.klineCountMetric = page.getByTestId('metric-data-kline-count')
    this.qualityMetric = page.getByTestId('metric-data-quality-status')
    this.refreshStatusButton = page.getByTestId('refresh-data-status')
    this.downloadButton = page.getByTestId('trigger-data-download')
    this.downloadMessage = page.getByTestId('data-download-message')
    this.capitalFlowTab = page.getByTestId('dataset-capital-flow')
    this.dragonTigerTab = page.getByTestId('dataset-dragon-tiger')
    this.intradayTab = page.getByTestId('dataset-intraday-60m')
    this.codesInput = page.getByTestId('query-codes-input')
    this.startInput = page.getByTestId('query-start-input')
    this.endInput = page.getByTestId('query-end-input')
    this.limitInput = page.getByTestId('query-limit-input')
    this.queryButton = page.getByTestId('run-data-query')
    this.queryCountMetric = page.getByTestId('metric-query-count')
    this.previewTable = page.getByTestId('data-preview-table')
    this.queryJsonPanel = page.getByTestId('query-json-panel')
  }
}
