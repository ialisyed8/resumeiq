/**
 * React Query hooks. Server state only — UI state stays in components and
 * filter state lives in the URL.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/api/client'
import type {
  CandidateDetail, CategoryWeights, DashboardData, Job, QuarantineItem,
  ResultsResponse, ScreeningStatus,
} from '@/api/types'

export const keys = {
  dashboard: ['dashboard'] as const,
  jobs: ['jobs'] as const,
  job: (id: string) => ['job', id] as const,
  screening: (id: string) => ['screening', id] as const,
  results: (id: string, params: string) => ['results', id, params] as const,
  quarantine: (id: string) => ['quarantine', id] as const,
  candidate: (batch: string, id: string, blind: boolean) =>
    ['candidate', batch, id, blind] as const,
  screenings: ['screenings'] as const,
  report: (id: string) => ['report', id] as const,
  audit: (id: string) => ['audit', id] as const,
}

export const useDashboard = () =>
  useQuery({ queryKey: keys.dashboard, queryFn: () => api.get<DashboardData>('/dashboard') })

export const useJob = (id: string | undefined) =>
  useQuery({
    queryKey: keys.job(id ?? ''),
    queryFn: () => api.get<Job>(`/jobs/${id}`),
    enabled: Boolean(id),
  })

export const useScreening = (id: string | undefined, poll = false) =>
  useQuery({
    queryKey: keys.screening(id ?? ''),
    queryFn: () => api.get<ScreeningStatus>(`/screenings/${id}`),
    enabled: Boolean(id),
    // SSE drives progress; this is a safety net if the stream drops.
    refetchInterval: poll ? 4000 : false,
  })

export interface ResultsParams {
  page?: number
  page_size?: number
  tier?: string
  decision_status?: string
  min_experience?: number
  max_experience?: number
  q?: string
  sort?: string
  blind?: boolean
}

export function useResults(batchId: string | undefined, params: ResultsParams) {
  const search = new URLSearchParams()
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== '' && v !== 'all') search.set(k, String(v))
  })
  const qs = search.toString()
  return useQuery({
    queryKey: keys.results(batchId ?? '', qs),
    queryFn: () => api.get<ResultsResponse>(`/screenings/${batchId}/results?${qs}`),
    enabled: Boolean(batchId),
    placeholderData: (previous) => previous, // keeps the table stable while filtering
  })
}

export const useQuarantine = (batchId: string | undefined) =>
  useQuery({
    queryKey: keys.quarantine(batchId ?? ''),
    queryFn: () => api.get<{ items: QuarantineItem[]; count: number; note: string }>(
      `/screenings/${batchId}/quarantine`,
    ),
    enabled: Boolean(batchId),
  })

export const useCandidate = (batchId?: string, candidateId?: string, blind = true) =>
  useQuery({
    queryKey: keys.candidate(batchId ?? '', candidateId ?? '', blind),
    queryFn: () => api.get<CandidateDetail>(
      `/screenings/${batchId}/candidates/${candidateId}?blind=${blind}`,
    ),
    enabled: Boolean(batchId && candidateId),
  })

export function useDecision(batchId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ candidateId, action, note }: {
      candidateId: string; action: string; note?: string
    }) => api.post<{ status: string; message: string }>(
      `/screenings/${batchId}/candidates/${candidateId}/decision`, { action, note },
    ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['results'] })
      qc.invalidateQueries({ queryKey: ['candidate'] })
      qc.invalidateQueries({ queryKey: keys.dashboard })
    },
  })
}

export function useRescore(batchId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (weights: CategoryWeights) =>
      api.post<{ candidates_scored: number; note: string }>(
        `/screenings/${batchId}/rescore`, weights,
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['results'] })
      qc.invalidateQueries({ queryKey: ['candidate'] })
    },
  })
}

export function useEvidenceOverride(batchId: string, candidateId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ requirementId, verdict, reason }: {
      requirementId: string; verdict: string; reason: string
    }) => api.patch(
      `/screenings/${batchId}/candidates/${candidateId}/evidence/${requirementId}`,
      { verdict, reason },
    ),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['candidate'] }),
  })
}

export function useRequirementUpdate(jobId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ requirementId, patch }: { requirementId: string; patch: object }) =>
      api.patch(`/jobs/${jobId}/requirements/${requirementId}`, patch),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.job(jobId) }),
  })
}

export function useRequirementDelete(jobId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (requirementId: string) =>
      api.delete(`/jobs/${jobId}/requirements/${requirementId}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.job(jobId) }),
  })
}

export function useRequirementAdd(jobId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: object) => api.post(`/jobs/${jobId}/requirements`, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.job(jobId) }),
  })
}
