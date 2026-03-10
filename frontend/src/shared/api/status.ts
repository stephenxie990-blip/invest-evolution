import { useQuery } from '@tanstack/react-query'

import { apiRequest } from '@/shared/api/client'
import { LabStatusEnvelopeSchema, type LabStatusEnvelope } from '@/shared/contracts/types'

export const statusQueryKeys = {
  quick: ['status', 'quick'] as const,
  deep: ['status', 'deep'] as const,
}

export function fetchQuickStatus() {
  return apiRequest<LabStatusEnvelope>('/api/lab/status/quick', {
    schema: LabStatusEnvelopeSchema,
  })
}

export function fetchDeepStatus() {
  return apiRequest<LabStatusEnvelope>('/api/lab/status/deep', {
    schema: LabStatusEnvelopeSchema,
  })
}

export function useQuickStatus() {
  return useQuery({
    queryKey: statusQueryKeys.quick,
    queryFn: fetchQuickStatus,
    refetchInterval: 15_000,
  })
}

export function useDeepStatus(enabled: boolean) {
  return useQuery({
    queryKey: statusQueryKeys.deep,
    queryFn: fetchDeepStatus,
    enabled,
    staleTime: 60_000,
  })
}
