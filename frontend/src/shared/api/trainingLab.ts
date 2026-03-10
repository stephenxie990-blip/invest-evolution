import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { apiRequest } from '@/shared/api/client'
import {
  ArtifactListSchema,
  TrainingExecutionSchema,
  TrainingPlanSchema,
  type ArtifactList,
  type TrainingExecution,
  type TrainingPlan,
} from '@/shared/contracts/types'

export const trainingLabQueryKeys = {
  plans: ['training-lab', 'plans'] as const,
  planDetail: (planId: string) => ['training-lab', 'plans', planId] as const,
  runs: ['training-lab', 'runs'] as const,
  runDetail: (runId: string) => ['training-lab', 'runs', runId] as const,
  evaluations: ['training-lab', 'evaluations'] as const,
  evaluationDetail: (runId: string) => ['training-lab', 'evaluations', runId] as const,
}

export type CreateTrainingPlanInput = {
  rounds: number
  mock: boolean
  goal: string
  notes: string
  tags: string[]
  detail_mode?: 'fast' | 'slow'
}

export function useTrainingPlans(limit = 10) {
  return useQuery({
    queryKey: [...trainingLabQueryKeys.plans, limit],
    queryFn: () => apiRequest<ArtifactList>('/api/lab/training/plans', {
      query: { limit },
      schema: ArtifactListSchema,
    }),
  })
}

export function useTrainingPlanDetail(planId: string | null) {
  return useQuery({
    queryKey: planId ? trainingLabQueryKeys.planDetail(planId) : [...trainingLabQueryKeys.plans, 'empty'],
    queryFn: () => apiRequest<TrainingPlan>(`/api/lab/training/plans/${planId}`, {
      schema: TrainingPlanSchema,
    }),
    enabled: Boolean(planId),
  })
}

export function useTrainingRuns(limit = 10) {
  return useQuery({
    queryKey: [...trainingLabQueryKeys.runs, limit],
    queryFn: () => apiRequest<ArtifactList>('/api/lab/training/runs', {
      query: { limit },
      schema: ArtifactListSchema,
    }),
  })
}

export function useTrainingRunDetail(runId: string | null) {
  return useQuery({
    queryKey: runId ? trainingLabQueryKeys.runDetail(runId) : [...trainingLabQueryKeys.runs, 'empty'],
    queryFn: () => apiRequest<TrainingPlan>(`/api/lab/training/runs/${runId}`, {
      schema: TrainingPlanSchema,
    }),
    enabled: Boolean(runId),
  })
}

export function useTrainingEvaluations(limit = 10) {
  return useQuery({
    queryKey: [...trainingLabQueryKeys.evaluations, limit],
    queryFn: () => apiRequest<ArtifactList>('/api/lab/training/evaluations', {
      query: { limit },
      schema: ArtifactListSchema,
    }),
  })
}

export function useTrainingEvaluationDetail(runId: string | null) {
  return useQuery({
    queryKey: runId ? trainingLabQueryKeys.evaluationDetail(runId) : [...trainingLabQueryKeys.evaluations, 'empty'],
    queryFn: () => apiRequest<TrainingPlan>(`/api/lab/training/evaluations/${runId}`, {
      schema: TrainingPlanSchema,
    }),
    enabled: Boolean(runId),
  })
}

export function useCreateTrainingPlan() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (payload: CreateTrainingPlanInput) => apiRequest<TrainingPlan>('/api/lab/training/plans', {
      method: 'POST',
      body: payload,
      schema: TrainingPlanSchema,
      timeoutMs: 60_000,
    }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: trainingLabQueryKeys.plans })
    },
  })
}

export function useExecuteTrainingPlan() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (planId: string) => apiRequest<TrainingExecution>(`/api/lab/training/plans/${planId}/execute`, {
      method: 'POST',
      schema: TrainingExecutionSchema,
      timeoutMs: 300_000,
    }),
    onSuccess: async (payload) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: trainingLabQueryKeys.plans }),
        queryClient.invalidateQueries({ queryKey: trainingLabQueryKeys.runs }),
        queryClient.invalidateQueries({ queryKey: trainingLabQueryKeys.evaluations }),
      ])

      const runId = typeof payload.training_lab === 'object' && payload.training_lab
        ? (payload.training_lab as Record<string, unknown>).run as Record<string, unknown> | undefined
        : undefined

      if (runId && typeof runId.run_id === 'string') {
        await Promise.all([
          queryClient.invalidateQueries({ queryKey: trainingLabQueryKeys.runDetail(runId.run_id) }),
          queryClient.invalidateQueries({ queryKey: trainingLabQueryKeys.evaluationDetail(runId.run_id) }),
        ])
      }
    },
  })
}
