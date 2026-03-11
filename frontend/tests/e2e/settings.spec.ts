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
  test('loads config drafts, shows control plane metadata, preserves unsaved edits on refetch, and saves updates', async ({ page }) => {
    let runtimePatch: Record<string, unknown> | null = null
    let evolutionPatch: Record<string, unknown> | null = null
    let runtimeGetCount = 0
    let evolutionGetCount = 0
    let controlPlaneGetCount = 0

    await page.route('**/api/lab/status/quick', async (route) => {
      await route.fulfill({ json: quickPayload })
    })

    await page.route('**/api/runtime_paths', async (route) => {
      if (route.request().method() === 'POST') {
        runtimePatch = route.request().postDataJSON() as Record<string, unknown>
        await route.fulfill({ json: { status: 'ok', config: runtimePatch } })
        return
      }

      runtimeGetCount += 1
      await route.fulfill({
        json: {
          status: 'ok',
          config: runtimeGetCount === 1
            ? {
                training_output_dir: '/Users/zhangsan/Desktop/投资进化系统v1.0/runtime',
                meeting_log_dir: '/Users/zhangsan/Desktop/投资进化系统v1.0/runtime/logs/meetings',
                config_audit_log_path: '/Users/zhangsan/Desktop/投资进化系统v1.0/runtime/state/config_changes.jsonl',
                config_snapshot_dir: '/Users/zhangsan/Desktop/投资进化系统v1.0/runtime/state/config_snapshots',
              }
            : {
                training_output_dir: '/server/changed/by-refetch',
                meeting_log_dir: '/server/runtime/meetings',
                config_audit_log_path: '/server/runtime/audit.jsonl',
                config_snapshot_dir: '/server/runtime/snapshots',
              },
        },
      })
    })

    await page.route('**/api/control_plane', async (route) => {
      controlPlaneGetCount += 1
      await route.fulfill({
        json: {
          status: 'ok',
          restart_required: false,
          config_path: '/Users/zhangsan/Desktop/投资进化系统v1.0/config/control_plane.yaml',
          local_override_path: '/Users/zhangsan/Desktop/投资进化系统v1.0/config/control_plane.local.yaml',
          audit_log_path: '/Users/zhangsan/Desktop/投资进化系统v1.0/runtime/state/control_plane_changes.jsonl',
          snapshot_dir: '/Users/zhangsan/Desktop/投资进化系统v1.0/runtime/state/control_plane_snapshots',
          config: {
            llm: {
              providers: {
                legacy_default: {
                  api_base: controlPlaneGetCount === 1 ? 'https://provider.example/v1' : 'https://provider.example/v2',
                  api_key: controlPlaneGetCount === 1 ? '********1234' : '********9999',
                },
              },
            },
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
            config: {
              ...evolutionPatch,
              config_layers: [
                '/Users/zhangsan/Desktop/投资进化系统v1.0/config/evolution.yaml',
                '/Users/zhangsan/Desktop/投资进化系统v1.0/config/evolution.local.yaml',
              ],
              local_override_path: '/Users/zhangsan/Desktop/投资进化系统v1.0/config/evolution.local.yaml',
              frontend_canary_query_param: '__frontend',
              audit_log_path: '/Users/zhangsan/Desktop/投资进化系统v1.0/runtime/state/config_changes.jsonl',
              snapshot_dir: '/Users/zhangsan/Desktop/投资进化系统v1.0/runtime/state/config_snapshots',
            },
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
                web_ui_shell_mode: 'legacy',
                frontend_canary_enabled: false,
                config_layers: [
                  '/Users/zhangsan/Desktop/投资进化系统v1.0/config/evolution.yaml',
                  '/Users/zhangsan/Desktop/投资进化系统v1.0/config/evolution.local.yaml',
                ],
                local_override_path: '/Users/zhangsan/Desktop/投资进化系统v1.0/config/evolution.local.yaml',
                frontend_canary_query_param: '__frontend',
                audit_log_path: '/Users/zhangsan/Desktop/投资进化系统v1.0/runtime/state/config_changes.jsonl',
                snapshot_dir: '/Users/zhangsan/Desktop/投资进化系统v1.0/runtime/state/config_snapshots',
              }
            : {
                population_size: 99,
                max_generations: 77,
                training_rounds: 66,
                web_ui_shell_mode: 'legacy',
                frontend_canary_enabled: false,
                config_layers: ['/server/config/evolution.yaml'],
                local_override_path: '/server/config/evolution.local.yaml',
                frontend_canary_query_param: '__frontend',
                audit_log_path: '/server/runtime/audit.jsonl',
                snapshot_dir: '/server/runtime/snapshots',
              },
        },
      })
    })

    const appShell = new AppShellPage(page)
    const settings = new SettingsPage(page)

    await appShell.goto()
    await appShell.openSettings()

    await expect(settings.root).toBeVisible()
    await expect(settings.runtimePathsTextarea).toContainText('training_output_dir')
    await expect(settings.evolutionConfigTextarea).toContainText('population_size')
    await expect(settings.configSecurityPanel).toContainText('legacy_default')
    await expect(settings.configSecurityPanel).toContainText('https://provider.example/v1')
    await expect(settings.configSecurityPanel).toContainText('********1234')
    await expect(settings.securityHint).toContainText('/api/control_plane')
    await expect(settings.evolutionConfigTextarea).not.toContainText('llm_api_key_masked')
    await expect(settings.webUiShellModeSelect).toHaveValue('legacy')
    await expect(settings.frontendCanaryEnabledCheckbox).not.toBeChecked()
    await expect(settings.frontendRolloutHint).toContainText('?__frontend=app')

    await settings.runtimePathsTextarea.fill(JSON.stringify({
      training_output_dir: '/draft/not-overwritten',
      meeting_log_dir: '/draft/runtime/meetings',
      config_audit_log_path: '/draft/runtime/audit.jsonl',
      config_snapshot_dir: '/draft/runtime/snapshots',
    }, null, 2))
    await settings.evolutionConfigTextarea.fill(JSON.stringify({
      population_size: 88,
      max_generations: 44,
      training_rounds: 5,
      web_ui_shell_mode: 'legacy',
      frontend_canary_enabled: false,
    }, null, 2))
    await settings.webUiShellModeSelect.selectOption('app')
    await settings.frontendCanaryEnabledCheckbox.check()

    await settings.refreshRuntimePaths.click()
    await settings.refreshEvolutionConfig.click()

    await expect.poll(() => runtimeGetCount).toBeGreaterThan(1)
    await expect.poll(() => evolutionGetCount).toBeGreaterThan(1)
    await expect(settings.runtimePathsTextarea).toContainText('/draft/not-overwritten')
    await expect(settings.runtimePathsTextarea).not.toContainText('/server/changed/by-refetch')
    await expect(settings.evolutionConfigTextarea).toContainText('88')
    await expect(settings.evolutionConfigTextarea).not.toContainText('99')
    await expect(settings.webUiShellModeSelect).toHaveValue('app')
    await expect(settings.frontendCanaryEnabledCheckbox).toBeChecked()

    const runtimeDraft = {
      training_output_dir: '/Users/zhangsan/Desktop/投资进化系统v1.0/runtime/frontend-ready',
      meeting_log_dir: '/Users/zhangsan/Desktop/投资进化系统v1.0/runtime/logs/meetings',
      config_audit_log_path: '/Users/zhangsan/Desktop/投资进化系统v1.0/runtime/state/config_changes.jsonl',
      config_snapshot_dir: '/Users/zhangsan/Desktop/投资进化系统v1.0/runtime/state/config_snapshots',
    }

    await settings.runtimePathsTextarea.fill(JSON.stringify(runtimeDraft, null, 2))
    await settings.saveRuntimePaths.click()

    await expect(settings.successMessage).toContainText('Runtime Paths 已提交更新')
    await expect(settings.runtimePathsTextarea).toContainText('frontend-ready')
    expect(runtimePatch).toEqual(runtimeDraft)

    const evolutionDraft = {
      population_size: 32,
      max_generations: 18,
      training_rounds: 5,
      mutation_rate: 0.12,
      web_ui_shell_mode: 'app',
      frontend_canary_enabled: true,
    }

    await settings.evolutionConfigTextarea.fill(JSON.stringify(evolutionDraft, null, 2))
    await settings.webUiShellModeSelect.selectOption('app')
    await settings.frontendCanaryEnabledCheckbox.check()
    await settings.saveEvolutionConfig.click()

    await expect(settings.successMessage).toContainText('Evolution Config 已提交更新')
    await expect(settings.evolutionConfigTextarea).toContainText('0.12')
    await expect(settings.configSecurityPanel).toContainText('https://provider.example/v1')
    expect(evolutionPatch).toEqual(evolutionDraft)
  })
})
