import type { Locator, Page } from '@playwright/test'

export class TrainingLabPage {
  readonly page: Page
  readonly root: Locator
  readonly overview: Locator
  readonly createPlanButton: Locator
  readonly executePlanButton: Locator
  readonly mockCheckbox: Locator
  readonly modeNote: Locator
  readonly planCountMetric: Locator
  readonly runCountMetric: Locator
  readonly evaluationCountMetric: Locator
  readonly eventCountMetric: Locator
  readonly planDetailPanel: Locator
  readonly planJsonPanel: Locator
  readonly eventStreamStatus: Locator
  readonly tabRuns: Locator
  readonly tabEvaluations: Locator
  readonly runDetailPanel: Locator
  readonly runStatusMetric: Locator
  readonly resultCardGrid: Locator
  readonly executionJsonPanel: Locator
  readonly specialErrorCard: Locator
  readonly timelinePanel: Locator
  readonly timelineList: Locator
  readonly speechCardList: Locator
  readonly strategyDiffPanel: Locator
  readonly strategyDiffList: Locator
  readonly evaluationDetailPanel: Locator
  readonly evaluationWarning: Locator
  readonly timelineCategorySpeech: Locator
  readonly timelineCycleFilter: Locator

  constructor(page: Page) {
    this.page = page
    this.root = page.getByTestId('training-lab-page')
    this.overview = page.getByTestId('training-lab-overview')
    this.createPlanButton = page.getByTestId('create-training-plan')
    this.executePlanButton = page.getByTestId('execute-training-plan')
    this.mockCheckbox = page.getByTestId('training-mock-checkbox')
    this.modeNote = page.getByTestId('training-mode-note')
    this.planCountMetric = page.getByTestId('metric-plan-count')
    this.runCountMetric = page.getByTestId('metric-run-count')
    this.evaluationCountMetric = page.getByTestId('metric-evaluation-count')
    this.eventCountMetric = page.getByTestId('metric-event-count')
    this.planDetailPanel = page.getByTestId('plan-detail-panel')
    this.planJsonPanel = page.getByTestId('plan-json-panel')
    this.eventStreamStatus = page.getByTestId('event-stream-status')
    this.tabRuns = page.getByTestId('tab-runs')
    this.tabEvaluations = page.getByTestId('tab-evaluations')
    this.runDetailPanel = page.getByTestId('run-detail-panel')
    this.runStatusMetric = page.getByTestId('run-detail-status')
    this.resultCardGrid = page.getByTestId('result-card-grid')
    this.executionJsonPanel = page.getByTestId('execution-json-panel')
    this.specialErrorCard = page.getByTestId('special-error-card')
    this.timelinePanel = page.getByTestId('timeline-panel')
    this.timelineList = page.getByTestId('timeline-list')
    this.speechCardList = page.getByTestId('speech-card-list')
    this.strategyDiffPanel = page.getByTestId('strategy-diff-panel')
    this.strategyDiffList = page.getByTestId('strategy-diff-list')
    this.evaluationDetailPanel = page.getByTestId('evaluation-detail-panel')
    this.evaluationWarning = page.getByTestId('training-evaluation-warning')
    this.timelineCategorySpeech = page.getByTestId('timeline-category-speech')
    this.timelineCycleFilter = page.getByTestId('timeline-cycle-filter')
  }

  planItem(planId: string) {
    return this.page.getByTestId(`plan-item-${planId}`)
  }

  runItem(runId: string) {
    return this.page.getByTestId(`run-item-${runId}`)
  }

  evaluationItem(runId: string) {
    return this.page.getByTestId(`evaluation-item-${runId}`)
  }

  resultCard(cycleId: string) {
    return this.page.getByTestId(`result-card-${cycleId}`)
  }
}
