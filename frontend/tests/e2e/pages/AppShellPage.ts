import type { Locator, Page } from '@playwright/test'

export class AppShellPage {
  readonly page: Page
  readonly root: Locator
  readonly dashboardNav: Locator
  readonly trainingLabNav: Locator
  readonly modelsNav: Locator
  readonly dataNav: Locator
  readonly settingsNav: Locator

  constructor(page: Page) {
    this.page = page
    this.root = page.getByTestId('app-shell')
    this.dashboardNav = page.getByTestId('nav-dashboard')
    this.trainingLabNav = page.getByTestId('nav-training-lab')
    this.modelsNav = page.getByTestId('nav-models')
    this.dataNav = page.getByTestId('nav-data')
    this.settingsNav = page.getByTestId('nav-settings')
  }

  async goto() {
    await this.page.goto('/app/')
    await this.root.waitFor()
  }

  async openDashboard() {
    await this.dashboardNav.click()
  }

  async openTrainingLab() {
    await this.trainingLabNav.click()
  }

  async openModels() {
    await this.modelsNav.click()
  }

  async openData() {
    await this.dataNav.click()
  }

  async openSettings() {
    await this.settingsNav.click()
  }
}
