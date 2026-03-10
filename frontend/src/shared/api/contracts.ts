import { useQuery } from '@tanstack/react-query'
import { z } from 'zod'

import { apiRequest } from '@/shared/api/client'

const ContractCatalogSchema = z.object({
  count: z.number().int(),
  items: z.array(z.object({
    id: z.string(),
    format: z.string(),
    kind: z.string(),
    path: z.string(),
    source_path: z.string().optional(),
    shell_mount: z.string().optional(),
  }).passthrough()),
}).passthrough()

const FrontendContractSchema = z.object({
  contract_id: z.string(),
  version: z.string().optional(),
  frontend_shell_mount: z.string(),
  api_base: z.string(),
  published_at: z.string().optional(),
  endpoints: z.array(z.record(z.string(), z.unknown())),
  sse: z.record(z.string(), z.unknown()).optional(),
}).passthrough()

export type ContractCatalog = z.infer<typeof ContractCatalogSchema>
export type FrontendContract = z.infer<typeof FrontendContractSchema>

export function useContractCatalog() {
  return useQuery({
    queryKey: ['contracts', 'catalog'],
    queryFn: () => apiRequest<ContractCatalog>('/api/contracts', {
      schema: ContractCatalogSchema,
    }),
    staleTime: 300_000,
  })
}

export function useFrontendContract() {
  return useQuery({
    queryKey: ['contracts', 'frontend-v1'],
    queryFn: () => apiRequest<FrontendContract>('/api/contracts/frontend-v1', {
      schema: FrontendContractSchema,
    }),
    staleTime: 300_000,
  })
}
