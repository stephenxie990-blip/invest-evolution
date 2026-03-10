import { expect, test } from '@playwright/test'

import { AppShellPage } from './pages/AppShellPage'
import { SettingsPage } from './pages/SettingsPage'

const quickPayload = {
  mode: 'quick',
  snapshot: {
    ts: '2026-03-10T10:00:00',
    detail_mode: 'fast',
    model: 'demo-model',
    runtime: { state: 'idle' },
    body: { total_cycles: 3, success_cycles: 2, failed_cycles: 1 },
    strategies: { total: 5, enabled: 3 },
    data: { latest_date: '20260310', quality: { health_status: 'healthy' } },
    training_lab: { plan_count: 1, run_count: 1, evaluation_count: 1 },
    brain: {},
    memory: {},
    bridge: {},
    plugins: {},
    config: {},
  },
}

test.describe('Settings', () => {
  test('loads config drafts, preserves unsaved edits on refetch, and saves updates', async ({ page }) => {
    let runtimePatch: Record<string, unknown> | null = null
    let evolutionPatch: Record<string, unknown> | null = null
    let runtimeGetCount = 0
    let evolutionGetCount = 0

    await page.route('**/api/lab/status/quick', async (route) => {
      await route.fulfill({ json: quickPayload })
    })

    await page.route('**/api/runtime_paths', async (route) => {
      if (route.request().method() === 'POST') {
        runtimePatch = route.request().postDataJSON() as Record<string, unknown>
        await route.fulfill({
          json: {
            status: 'ok',
            config: runtimePatch,
          },
        })
        return
      }

      runtimeGetCount += 1
      await route.fulfill({
        json: {
          status: 'ok',
          config: runtimeGetCount === 1
            ? {
                project_root: '/Users/zhangsan/Desktop/投资进化系统v1.0',
                runtime_dir: 'runtime/workspace',
              }
            : {
                project_root: '/server/changed/by-refetch',
                runtime_dir: 'runtime/from-refetch',
              },
        },
      })
    })

    await page.route('**/api/evolution_config', async (route) => {
      if (route.request().method() === 'POST') {
        evolutionPatch = route.request().postDataJSON() as Record<string, unknown>
        await route.fulfill({
          json: {
            status: 'ok',
            config: evolutionPatch,
          },
        })
        return
      }

      evolutionGetCount += 1
      await route.fulfill({
        json: {
          status: 'ok',
          config: evolutionGetCount === 1
            ? {
                population_size: 24,
                max_generations: 12,
                training_rounds: 3,
              }
            : {
                population_size: 99,
                max_generations: 77,
                training_rounds: 66,
              },
        },
      })
    })

    const appShell = new AppShellPage(page)
    const settings = new SettingsPage(page)

    await appShell.goto()
    await appShell.openSettings()

    await expect(settings.root).toBeVisible()
    await expect(settings.runtimePathsTextarea).toContainText('project_root')
    await expect(settings.runtimePathsTextarea).toContainText('runtime/workspace')
    await expect(settings.evolutionConfigTextarea).toContainText('population_size')
    await expect(settings.evolutionConfigTextarea).toContainText('max_generations')

    await settings.runtimePathsTextarea.fill(JSON.stringify({
      project_root: '/draft/not-overwritten',
      runtime_dir: 'runtime/local-draft',
    }, null, 2))
    await settings.evolutionConfigTextarea.fill(JSON.stringify({
      population_size: 88,
      max_generations: 44,
      training_rounds: 5,
    }, null, 2))

    await settings.refreshRuntimePaths.click()
    await settings.refreshEvolutionConfig.click()

    await expect.poll(() => runtimeGetCount).toBeGreaterThan(1)
    await expect.poll(() => evolutionGetCount).toBeGreaterThan(1)
    await expect(settings.runtimePathsTextarea).toContainText('/draft/not-overwritten')
    await expect(settings.runtimePathsTextarea).not.toContainText('/server/changed/by-refetch')
    await expect(settings.evolutionConfigTextarea).toContainText('88')
    await expect(settings.evolutionConfigTextarea).not.toContainText('99')

    const runtimeDraft = {
      project_root: '/Users/zhangsan/Desktop/投资进化系统v1.0',
      runtime_dir: 'runtime/frontend-ready',
      strategy_dir: 'strategies/generated',
    }

    await settings.runtimePathsTextarea.fill(JSON.stringify(runtimeDraft, null, 2))
    await settings.saveRuntimePaths.click()

    await expect(settings.successMessage).toContainText('Runtime Paths 已提交更新')
    await expect(settings.runtimePathsTextarea).toContainText('runtime/frontend-ready')
    expect(runtimePatch).toEqual(runtimeDraft)

    const evolutionDraft = {
      population_size: 32,
      max_generations: 18,
      training_rounds: 5,
      mutation_rate: 0.12,
    }

    await settings.evolutionConfigTextarea.fill(JSON.stringify(evolutionDraft, null, 2))
    await settings.saveEvolutionConfig.click()

    await expect(settings.successMessage).toContainText('Evolution Config 已提交更新')
    await expect(settings.evolutionConfigTextarea).toContainText('0.12')
    expect(evolutionPatch).toEqual(evolutionDraft)
  })
})
