import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { apiRequest } from '@/shared/api/client'
import {
  ControlPlaneSchema,
  EvolutionConfigSchema,
  RuntimePathsSchema,
  type ControlPlaneResponse,
  type EvolutionConfigResponse,
  type RuntimePathsResponse,
} from '@/shared/contracts/types'

export const settingsQueryKeys = {
  runtimePaths: ['settings', 'runtime-paths'] as const,
  evolutionConfig: ['settings', 'evolution-config'] as const,
  controlPlane: ['settings', 'control-plane'] as const,
}

export function useRuntimePaths() {
  return useQuery({
    queryKey: settingsQueryKeys.runtimePaths,
    queryFn: () => apiRequest<RuntimePathsResponse>('/api/runtime_paths', {
      schema: RuntimePathsSchema,
    }),
  })
}

export function useEvolutionConfig() {
  return useQuery({
    queryKey: settingsQueryKeys.evolutionConfig,
    queryFn: () => apiRequest<EvolutionConfigResponse>('/api/evolution_config', {
      schema: EvolutionConfigSchema,
    }),
  })
}


export function useControlPlane() {
  return useQuery({
    queryKey: settingsQueryKeys.controlPlane,
    queryFn: () => apiRequest<ControlPlaneResponse>('/api/control_plane', {
      schema: ControlPlaneSchema,
    }),
  })
}

export function usePatchRuntimePaths() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (patch: Record<string, unknown>) => apiRequest<RuntimePathsResponse>('/api/runtime_paths', {
      method: 'POST',
      body: patch,
      schema: RuntimePathsSchema,
    }),
    onSuccess: async (payload) => {
      queryClient.setQueryData(settingsQueryKeys.runtimePaths, payload)
    },
  })
}

export function usePatchEvolutionConfig() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (patch: Record<string, unknown>) => apiRequest<EvolutionConfigResponse>('/api/evolution_config', {
      method: 'POST',
      body: patch,
      schema: EvolutionConfigSchema,
    }),
    onSuccess: async (payload) => {
      queryClient.setQueryData(settingsQueryKeys.evolutionConfig, payload)
    },
  })
}
