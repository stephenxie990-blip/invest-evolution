import type { Locator, Page } from '@playwright/test'

export class SettingsPage {
  readonly page: Page
  readonly root: Locator
  readonly runtimePathsTextarea: Locator
  readonly saveRuntimePaths: Locator
  readonly refreshRuntimePaths: Locator
  readonly evolutionConfigTextarea: Locator
  readonly saveEvolutionConfig: Locator
  readonly refreshEvolutionConfig: Locator
  readonly successMessage: Locator
  readonly configSecurityPanel: Locator
  readonly configLayerList: Locator
  readonly securityWarning: Locator
  readonly webUiShellModeSelect: Locator
  readonly frontendCanaryEnabledCheckbox: Locator
  readonly frontendRolloutHint: Locator

  constructor(page: Page) {
    this.page = page
    this.root = page.getByTestId('settings-page')
    this.runtimePathsTextarea = page.getByTestId('runtime-paths-textarea')
    this.saveRuntimePaths = page.getByTestId('save-runtime-paths')
    this.refreshRuntimePaths = page.getByTestId('refresh-runtime-paths')
    this.evolutionConfigTextarea = page.getByTestId('evolution-config-textarea')
    this.saveEvolutionConfig = page.getByTestId('save-evolution-config')
    this.refreshEvolutionConfig = page.getByTestId('refresh-evolution-config')
    this.successMessage = page.getByTestId('settings-success-message')
    this.configSecurityPanel = page.getByTestId('config-security-panel')
    this.configLayerList = page.getByTestId('config-layer-list')
    this.securityWarning = page.getByTestId('settings-security-warning')
    this.webUiShellModeSelect = page.getByTestId('web-ui-shell-mode-select')
    this.frontendCanaryEnabledCheckbox = page.getByTestId('frontend-canary-enabled-checkbox')
    this.frontendRolloutHint = page.getByTestId('frontend-rollout-hint')
  }
}
