import { useEffect, useState } from 'react'

import {
  useEvolutionConfig,
  usePatchEvolutionConfig,
  usePatchRuntimePaths,
  useRuntimePaths,
} from '@/shared/api/settings'
import { ApiError } from '@/shared/api/errors'
import { LoadingState, ErrorState } from '@/shared/ui/AsyncState'
import { Panel } from '@/shared/ui/Panel'

export function SettingsPage() {
  const runtimePaths = useRuntimePaths()
  const evolutionConfig = useEvolutionConfig()
  const patchRuntimePaths = usePatchRuntimePaths()
  const patchEvolutionConfig = usePatchEvolutionConfig()

  const [runtimeDraft, setRuntimeDraft] = useState('{}')
  const [evolutionDraft, setEvolutionDraft] = useState('{}')
  const [runtimeDirty, setRuntimeDirty] = useState(false)
  const [evolutionDirty, setEvolutionDirty] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)

  useEffect(() => {
    if (runtimePaths.data && !runtimeDirty) {
      setRuntimeDraft(JSON.stringify(runtimePaths.data.config, null, 2))
    }
  }, [runtimeDirty, runtimePaths.data])

  useEffect(() => {
    if (evolutionConfig.data && !evolutionDirty) {
      setEvolutionDraft(JSON.stringify(evolutionConfig.data.config, null, 2))
    }
  }, [evolutionConfig.data, evolutionDirty])

  const saveRuntimePaths = async () => {
    setMessage(null)
    setErrorMessage(null)
    try {
      const payload = JSON.parse(runtimeDraft) as Record<string, unknown>
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
      const payload = JSON.parse(evolutionDraft) as Record<string, unknown>
      const response = await patchEvolutionConfig.mutateAsync(payload)
      setEvolutionDraft(JSON.stringify(response.config, null, 2))
      setEvolutionDirty(false)
      setMessage('Evolution Config 已提交更新')
    } catch (error) {
      setErrorMessage(error instanceof ApiError ? error.message : 'Evolution Config 保存失败')
    }
  }

  return (
    <div className="page-grid" data-testid="settings-page">
      <Panel
        title="运行路径配置"
        actions={(
          <button className="button button--secondary" data-testid="refresh-runtime-paths" onClick={() => void runtimePaths.refetch()} type="button">
            刷新 Runtime Paths
          </button>
        )}
      >
        {runtimePaths.isLoading ? <LoadingState label="正在读取 runtime paths..." /> : null}
        {runtimePaths.error ? <ErrorState error={runtimePaths.error} /> : null}
        {!runtimePaths.isLoading && !runtimePaths.error ? (
          <>
            <textarea
              className="textarea textarea--lg"
              data-testid="runtime-paths-textarea"
              onChange={(event) => { setRuntimeDirty(true); setRuntimeDraft(event.target.value) }}
              value={runtimeDraft}
            />
            <div className="button-row">
              <button className="button" data-testid="save-runtime-paths" disabled={patchRuntimePaths.isPending} onClick={saveRuntimePaths} type="button">
                {patchRuntimePaths.isPending ? '提交中...' : '保存 Runtime Paths'}
              </button>
            </div>
          </>
        ) : null}
      </Panel>

      <Panel
        title="进化配置"
        actions={(
          <button className="button button--secondary" data-testid="refresh-evolution-config" onClick={() => void evolutionConfig.refetch()} type="button">
            刷新 Evolution Config
          </button>
        )}
      >
        {evolutionConfig.isLoading ? <LoadingState label="正在读取 evolution config..." /> : null}
        {evolutionConfig.error ? <ErrorState error={evolutionConfig.error} /> : null}
        {!evolutionConfig.isLoading && !evolutionConfig.error ? (
          <>
            <textarea
              className="textarea textarea--lg"
              data-testid="evolution-config-textarea"
              onChange={(event) => { setEvolutionDirty(true); setEvolutionDraft(event.target.value) }}
              value={evolutionDraft}
            />
            <div className="button-row">
              <button className="button" data-testid="save-evolution-config" disabled={patchEvolutionConfig.isPending} onClick={saveEvolutionConfig} type="button">
                {patchEvolutionConfig.isPending ? '提交中...' : '保存 Evolution Config'}
              </button>
            </div>
          </>
        ) : null}
      </Panel>

      {message ? <div className="state-block" data-testid="settings-success-message">{message}</div> : null}
      {errorMessage ? <ErrorState error={new ApiError(errorMessage)} /> : null}
    </div>
  )
}
