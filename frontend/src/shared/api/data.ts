import { useMutation, useQuery } from '@tanstack/react-query'

import { apiRequest } from '@/shared/api/client'
import {
  CountedItemsSchema,
  DataDownloadSchema,
  DataStatusSchema,
  type CountedItemsResponse,
  type DataDownloadResponse,
  type DataStatusResponse,
} from '@/shared/contracts/types'

export type DataDatasetKind = 'capital_flow' | 'dragon_tiger' | 'intraday_60m'

export type DataQueryInput = {
  dataset: DataDatasetKind
  codes?: string
  start?: string
  end?: string
  limit?: number
}

export const dataQueryKeys = {
  status: (refreshKey: number) => ['data', 'status', refreshKey] as const,
  items: (query: DataQueryInput | null) => ['data', 'items', query?.dataset ?? 'idle', query?.codes ?? '', query?.start ?? '', query?.end ?? '', query?.limit ?? 0] as const,
}

const datasetPathMap: Record<DataDatasetKind, string> = {
  capital_flow: '/api/data/capital_flow',
  dragon_tiger: '/api/data/dragon_tiger',
  intraday_60m: '/api/data/intraday_60m',
}

export function useDataStatus(refreshKey = 0) {
  return useQuery({
    queryKey: dataQueryKeys.status(refreshKey),
    queryFn: () => apiRequest<DataStatusResponse>('/api/data/status', {
      query: refreshKey > 0 ? { refresh: true } : undefined,
      schema: DataStatusSchema,
    }),
    staleTime: 60_000,
  })
}

export function useDataItems(query: DataQueryInput | null) {
  return useQuery({
    queryKey: dataQueryKeys.items(query),
    queryFn: () => apiRequest<CountedItemsResponse>(datasetPathMap[query!.dataset], {
      query: {
        codes: query?.codes,
        start: query?.start,
        end: query?.end,
        limit: query?.limit,
      },
      schema: CountedItemsSchema,
    }),
    enabled: Boolean(query),
    staleTime: 30_000,
  })
}

export function useDataDownload() {
  return useMutation({
    mutationFn: () => apiRequest<DataDownloadResponse>('/api/data/download', {
      method: 'POST',
      body: {},
      schema: DataDownloadSchema,
    }),
  })
}
