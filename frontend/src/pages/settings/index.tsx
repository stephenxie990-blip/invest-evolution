import { useEffect, useMemo, useState } from 'react'

import {
  useControlPlane,
  useEvolutionConfig,
  usePatchEvolutionConfig,
  usePatchRuntimePaths,
  useRuntimePaths,
} from '@/shared/api/settings'
import { ApiError } from '@/shared/api/errors'
import { ErrorState, LoadingState } from '@/shared/ui/AsyncState'
import { KeyValueList } from '@/shared/ui/KeyValueList'
import { Panel } from '@/shared/ui/Panel'
import { StatusBadge } from '@/shared/ui/StatusBadge'

type UnknownRecord = Record<string, unknown>

type UiShellMode = 'legacy' | 'app'

const NON_EDITABLE_EVOLUTION_CONFIG_KEYS = new Set([
  'config_layers',
  'local_override_path',
  'audit_log_path',
  'snapshot_dir',
  'frontend_canary_query_param',
])

function asRecord(value: unknown): UnknownRecord | null {
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as UnknownRecord) : null
}

function readString(value: unknown, fallback = '--'): string {
  return typeof value === 'string' && value.trim() ? value : fallback
}

function readBoolean(value: unknown, fallback = false): boolean {
  return typeof value === 'boolean' ? value : fallback
}

function readShellMode(value: unknown): UiShellMode {
  return value === 'app' ? 'app' : 'legacy'
}

function safeParseObject(value: string): UnknownRecord {
  const parsed = JSON.parse(value) as unknown
  const record = asRecord(parsed)
  if (!record) {
    throw new Error('配置必须是 JSON object')
  }
  return record
}

function sanitizeEvolutionConfig(config: UnknownRecord): UnknownRecord {
  return Object.fromEntries(Object.entries(config).filter(([key]) => !NON_EDITABLE_EVOLUTION_CONFIG_KEYS.has(key)))
}

function stringifyConfig(value: UnknownRecord): string {
  return JSON.stringify(value, null, 2)
}

function withRolloutValues(config: UnknownRecord, shellMode: UiShellMode, frontendCanaryEnabled: boolean): UnknownRecord {
  return {
    ...config,
    web_ui_shell_mode: shellMode,
    frontend_canary_enabled: frontendCanaryEnabled,
  }
}

function firstMaskedProviderSummary(controlPlanePayload: UnknownRecord): { providerName: string; apiBase: string; maskedKey: string } {
  const llm = asRecord(controlPlanePayload.llm) ?? {}
  const providers = asRecord(llm.providers) ?? {}
  for (const [providerName, rawProvider] of Object.entries(providers)) {
    const provider = asRecord(rawProvider) ?? {}
    return {
      providerName,
      apiBase: readString(provider.api_base, '未配置'),
      maskedKey: readString(provider.api_key, '未配置'),
    }
  }
  return {
    providerName: '未配置',
    apiBase: '未配置',
    maskedKey: '未配置',
  }
}

export function SettingsPage() {
  const runtimePaths = useRuntimePaths()
  const evolutionConfig = useEvolutionConfig()
  const controlPlane = useControlPlane()
  const patchRuntimePaths = usePatchRuntimePaths()
  const patchEvolutionConfig = usePatchEvolutionConfig()

  const [runtimeDraft, setRuntimeDraft] = useState('{}')
  const [evolutionDraft, setEvolutionDraft] = useState('{}')
  const [runtimeDirty, setRuntimeDirty] = useState(false)
  const [evolutionDirty, setEvolutionDirty] = useState(false)
  const [shellMode, setShellMode] = useState<UiShellMode>('legacy')
  const [frontendCanaryEnabled, setFrontendCanaryEnabled] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)

  const runtimeConfig = asRecord(runtimePaths.data?.config) ?? {}
  const evolutionConfigPayload = asRecord(evolutionConfig.data?.config) ?? {}
  const controlPlanePayload = asRecord(controlPlane.data?.config) ?? {}
  const controlPlaneSummary = firstMaskedProviderSummary(controlPlanePayload)
  const canaryQueryParam = readString(evolutionConfigPayload.frontend_canary_query_param, '__frontend')

  useEffect(() => {
    if (runtimePaths.data && !runtimeDirty) {
      setRuntimeDraft(JSON.stringify(runtimePaths.data.config, null, 2))
    }
  }, [runtimeDirty, runtimePaths.data])

  useEffect(() => {
    if (evolutionConfig.data && !evolutionDirty) {
      const sanitized = sanitizeEvolutionConfig(evolutionConfigPayload)
      setEvolutionDraft(stringifyConfig(sanitized))
      setShellMode(readShellMode(evolutionConfigPayload.web_ui_shell_mode))
      setFrontendCanaryEnabled(readBoolean(evolutionConfigPayload.frontend_canary_enabled))
    }
  }, [evolutionConfig.data, evolutionDirty, evolutionConfigPayload])

  const saveRuntimePaths = async () => {
    setMessage(null)
    setErrorMessage(null)
    try {
      const payload = safeParseObject(runtimeDraft)
      const response = await patchRuntimePaths.mutateAsync(payload)
      setRuntimeDraft(JSON.stringify(response.config, null, 2))
      setRuntimeDirty(false)
      setMessage('Runtime Paths 已提交更新')
    } catch (error) {
      setErrorMessage(error instanceof ApiError ? error.message : 'Runtime Paths 保存失败')
    }
  }

  const saveEvolutionConfig = async () => {
    setMessage(null)
    setErrorMessage(null)
    try {
      const payload = withRolloutValues(safeParseObject(evolutionDraft), shellMode, frontendCanaryEnabled)
      const response = await patchEvolutionConfig.mutateAsync(payload)
      const updatedConfig = asRecord(response.config) ?? {}
      setEvolutionDraft(stringifyConfig(sanitizeEvolutionConfig(updatedConfig)))
      setShellMode(readShellMode(updatedConfig.web_ui_shell_mode))
      setFrontendCanaryEnabled(readBoolean(updatedConfig.frontend_canary_enabled))
      setEvolutionDirty(false)
      setMessage('Evolution Config 已提交更新')
    } catch (error) {
      setErrorMessage(error instanceof ApiError ? error.message : 'Evolution Config 保存失败')
    }
  }

  const updateRolloutDraft = (nextMode: UiShellMode, nextCanaryEnabled: boolean) => {
    setShellMode(nextMode)
    setFrontendCanaryEnabled(nextCanaryEnabled)
    setEvolutionDirty(true)

    try {
      const nextDraft = withRolloutValues(safeParseObject(evolutionDraft), nextMode, nextCanaryEnabled)
      setEvolutionDraft(stringifyConfig(nextDraft))
    } catch {
    }
  }

  const rolloutSummary = useMemo(() => {
    const canaryUrl = `/?${canaryQueryParam}=app`
    return [
      { label: 'Root Shell', value: shellMode },
      { label: 'Canary Enabled', value: frontendCanaryEnabled ? 'true' : 'false' },
      { label: 'Canary URL', value: canaryUrl },
      { label: 'Legacy Rollback', value: '/legacy' },
      { label: 'Standalone App', value: '/app' },
    ]
  }, [canaryQueryParam, frontendCanaryEnabled, shellMode])

  return (
    <div className="page-grid" data-testid="settings-page">
      <Panel title="Control Plane 安全与分层">
        {controlPlane.isLoading ? <LoadingState label="正在读取 control plane metadata..." /> : null}
        {controlPlane.error ? <ErrorState error={controlPlane.error} /> : null}
        {!controlPlane.isLoading && !controlPlane.error ? (
          <div className="content-stack" data-testid="config-security-panel">
            <div className="status-badge-row">
              <StatusBadge tone={controlPlaneSummary.maskedKey === '未配置' ? 'danger' : 'good'}>{`provider: ${controlPlaneSummary.providerName}`}</StatusBadge>
              <StatusBadge tone={shellMode === 'app' ? 'good' : 'neutral'}>{`web_ui_shell_mode: ${shellMode}`}</StatusBadge>
              <StatusBadge tone={frontendCanaryEnabled ? 'warn' : 'neutral'}>{`frontend_canary_enabled: ${frontendCanaryEnabled}`}</StatusBadge>
            </div>
            <KeyValueList
              entries={[
                { label: 'Provider', value: controlPlaneSummary.providerName },
                { label: 'LLM API Base', value: controlPlaneSummary.apiBase },
                { label: 'LLM Key Masked', value: controlPlaneSummary.maskedKey },
                { label: 'Control Plane', value: readString(controlPlane.data?.config_path) },
                { label: 'Local Override', value: readString(controlPlane.data?.local_override_path) },
                { label: 'Audit Log', value: readString(controlPlane.data?.audit_log_path) },
                { label: 'Snapshot Dir', value: readString(controlPlane.data?.snapshot_dir) },
              ]}
            />
            <div className="state-block state-block--muted" data-testid="settings-security-hint">
              LLM 模型与密钥已统一迁移到 `/api/control_plane`；`/api/evolution_config` 仅保留训练参数与发布开关。
            </div>
          </div>
        ) : null}
      </Panel>

      <Panel title="前端发布开关">
        <div className="content-stack" data-testid="frontend-rollout-panel">
          <div className="form-grid">
            <label>
              <span>Root Shell 模式</span>
              <select
                className="input"
                data-testid="web-ui-shell-mode-select"
                onChange={(event) => updateRolloutDraft(event.target.value === 'app' ? 'app' : 'legacy', frontendCanaryEnabled)}
                value={shellMode}
              >
                <option value="legacy">legacy</option>
                <option value="app">app</option>
              </select>
            </label>
            <label className="checkbox-field">
              <input
                checked={frontendCanaryEnabled}
                data-testid="frontend-canary-enabled-checkbox"
                onChange={(event) => updateRolloutDraft(shellMode, event.target.checked)}
                type="checkbox"
              />
              <span>开启 Canary 入口</span>
            </label>
          </div>
          <KeyValueList entries={rolloutSummary} />
          <div className="state-block state-block--muted" data-testid="frontend-rollout-hint">
            `legacy` 模式下，根路径 `/` 继续走旧壳；启用 canary 后，可通过 `?{canaryQueryParam}=app` 或请求头 `X-Invest-Frontend-Canary: app` 进入新前端。`/legacy` 始终保留为回滚入口。
          </div>
        </div>
      </Panel>

      <Panel title="运行路径配置" actions={<button className="button button--secondary" data-testid="refresh-runtime-paths" onClick={() => void runtimePaths.refetch()} type="button">刷新 Runtime Paths</button>}>
        {runtimePaths.isLoading ? <LoadingState label="正在读取 runtime paths..." /> : null}
        {runtimePaths.error ? <ErrorState error={runtimePaths.error} /> : null}
        {!runtimePaths.isLoading && !runtimePaths.error ? (
          <div className="content-stack">
            <KeyValueList
              entries={[
                { label: 'Training Output', value: readString(runtimeConfig.training_output_dir) },
                { label: 'Meeting Logs', value: readString(runtimeConfig.meeting_log_dir) },
                { label: 'Config Audit Log', value: readString(runtimeConfig.config_audit_log_path) },
                { label: 'Config Snapshot Dir', value: readString(runtimeConfig.config_snapshot_dir) },
              ]}
            />
            <textarea className="textarea textarea--lg" data-testid="runtime-paths-textarea" onChange={(event) => { setRuntimeDirty(true); setRuntimeDraft(event.target.value) }} value={runtimeDraft} />
            <div className="button-row">
              <button className="button" data-testid="save-runtime-paths" disabled={patchRuntimePaths.isPending} onClick={saveRuntimePaths} type="button">
                {patchRuntimePaths.isPending ? '提交中...' : '保存 Runtime Paths'}
              </button>
            </div>
          </div>
        ) : null}
      </Panel>

      <Panel title="进化配置" actions={<button className="button button--secondary" data-testid="refresh-evolution-config" onClick={() => void evolutionConfig.refetch()} type="button">刷新 Evolution Config</button>}>
        {evolutionConfig.isLoading ? <LoadingState label="正在读取 evolution config..." /> : null}
        {evolutionConfig.error ? <ErrorState error={evolutionConfig.error} /> : null}
        {!evolutionConfig.isLoading && !evolutionConfig.error ? (
          <div className="content-stack">
            <div className="state-block state-block--muted" data-testid="evolution-config-safety-note">
              这里只保留训练参数与发布开关；LLM 模型、Provider、密钥请到 Control Plane 面板或 `/api/control_plane` 管理。
            </div>
            <textarea className="textarea textarea--lg" data-testid="evolution-config-textarea" onChange={(event) => { setEvolutionDirty(true); setEvolutionDraft(event.target.value) }} value={evolutionDraft} />
            <div className="button-row">
              <button className="button" data-testid="save-evolution-config" disabled={patchEvolutionConfig.isPending} onClick={saveEvolutionConfig} type="button">
                {patchEvolutionConfig.isPending ? '提交中...' : '保存 Evolution Config'}
              </button>
            </div>
          </div>
        ) : null}
      </Panel>

      {message ? <div className="state-block" data-testid="settings-success-message">{message}</div> : null}
      {errorMessage ? <ErrorState error={new ApiError(errorMessage)} /> : null}
    </div>
  )
}
