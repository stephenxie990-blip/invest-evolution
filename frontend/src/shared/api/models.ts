import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { apiRequest } from '@/shared/api/client'
import {
  AllocatorSchema,
  InvestmentModelsSchema,
  LeaderboardSchema,
  StrategyGeneListSchema,
  StrategyReloadSchema,
  type AllocatorResponse,
  type InvestmentModelsResponse,
  type LeaderboardResponse,
  type StrategyGeneListResponse,
  type StrategyReloadResponse,
} from '@/shared/contracts/types'

export const modelsQueryKeys = {
  investmentModels: ['models', 'investment-models'] as const,
  leaderboard: ['models', 'leaderboard'] as const,
  allocator: (regime: string, topN: number) => ['models', 'allocator', regime, topN] as const,
  strategies: ['models', 'strategies'] as const,
}

export function useInvestmentModels() {
  return useQuery({
    queryKey: modelsQueryKeys.investmentModels,
    queryFn: () => apiRequest<InvestmentModelsResponse>('/api/investment-models', {
      schema: InvestmentModelsSchema,
    }),
    staleTime: 60_000,
  })
}

export function useLeaderboard() {
  return useQuery({
    queryKey: modelsQueryKeys.leaderboard,
    queryFn: () => apiRequest<LeaderboardResponse>('/api/leaderboard', {
      schema: LeaderboardSchema,
    }),
    staleTime: 60_000,
  })
}

export function useAllocator(regime: string, topN: number) {
  return useQuery({
    queryKey: modelsQueryKeys.allocator(regime, topN),
    queryFn: () => apiRequest<AllocatorResponse>('/api/allocator', {
      query: { regime, top_n: topN },
      schema: AllocatorSchema,
    }),
    staleTime: 60_000,
  })
}

export function useStrategies() {
  return useQuery({
    queryKey: modelsQueryKeys.strategies,
    queryFn: () => apiRequest<StrategyGeneListResponse>('/api/strategies', {
      schema: StrategyGeneListSchema,
    }),
    staleTime: 60_000,
  })
}

export function useReloadStrategies() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: () => apiRequest<StrategyReloadResponse>('/api/strategies/reload', {
      method: 'POST',
      body: {},
      schema: StrategyReloadSchema,
    }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: modelsQueryKeys.strategies })
    },
  })
}
