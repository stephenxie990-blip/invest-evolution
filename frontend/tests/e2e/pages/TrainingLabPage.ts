import type { Locator, Page } from '@playwright/test'

export class TrainingLabPage {
  readonly page: Page
  readonly root: Locator
  readonly overview: Locator
  readonly createPlanButton: Locator
  readonly executePlanButton: Locator
  readonly planCountMetric: Locator
  readonly runCountMetric: Locator
  readonly evaluationCountMetric: Locator
  readonly eventCountMetric: Locator
  readonly planDetailPanel: Locator
  readonly planJsonPanel: Locator
  readonly eventStreamStatus: Locator
  readonly tabRuns: Locator
  readonly runDetailPanel: Locator
  readonly runStatusMetric: Locator
  readonly resultCardGrid: Locator
  readonly executionJsonPanel: Locator

  constructor(page: Page) {
    this.page = page
    this.root = page.getByTestId('training-lab-page')
    this.overview = page.getByTestId('training-lab-overview')
    this.createPlanButton = page.getByTestId('create-training-plan')
    this.executePlanButton = page.getByTestId('execute-training-plan')
    this.planCountMetric = page.getByTestId('metric-plan-count')
    this.runCountMetric = page.getByTestId('metric-run-count')
    this.evaluationCountMetric = page.getByTestId('metric-evaluation-count')
    this.eventCountMetric = page.getByTestId('metric-event-count')
    this.planDetailPanel = page.getByTestId('plan-detail-panel')
    this.planJsonPanel = page.getByTestId('plan-json-panel')
    this.eventStreamStatus = page.getByTestId('event-stream-status')
    this.tabRuns = page.getByTestId('tab-runs')
    this.runDetailPanel = page.getByTestId('run-detail-panel')
    this.runStatusMetric = page.getByTestId('run-detail-status')
    this.resultCardGrid = page.getByTestId('result-card-grid')
    this.executionJsonPanel = page.getByTestId('execution-json-panel')
  }

  planItem(planId: string) {
    return this.page.getByTestId(`plan-item-${planId}`)
  }

  runItem(runId: string) {
    return this.page.getByTestId(`run-item-${runId}`)
  }
}
