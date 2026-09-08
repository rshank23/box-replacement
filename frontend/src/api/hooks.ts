import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api, newCorrelationId } from './client';
import { groupIntoJobs } from './jobs';
import type {
  AuditEntry,
  DashboardStats,
  Failure,
  FailureResolveResponse,
  FileRoot,
  Job,
  MappingOverride,
  MappingRule,
  MappingRuleInput,
  MBoxEntry,
  PocCounts,
  PocRunResponse,
  PocStatus,
  SamAction,
  Transfer,
  TransferSubmitRequest,
  TransferSubmitResponse,
} from './types';

export const queryKeys = {
  stats: ['dashboard', 'stats'] as const,
  transfers: (params: Record<string, unknown>) => ['transfers', params] as const,
  jobs: ['transfers', 'jobs'] as const,
  failures: (params: Record<string, unknown>) => ['failures', params] as const,
  mappings: (activeOnly: boolean) => ['mappings', activeOnly] as const,
  audit: (params: Record<string, unknown>) => ['audit', params] as const,
  samQueue: ['dashboard', 'sam-queue'] as const,
  browse: (root: string, path: string) => ['mbox', 'browse', root, path] as const,
  pocStatus: ['poc', 'status'] as const,
};

export function useDashboardStats() {
  return useQuery({
    queryKey: queryKeys.stats,
    queryFn: () => api.get<DashboardStats>('/api/dashboard/stats'),
    refetchInterval: 30_000,
  });
}

export function useTransfers(params: { status?: string; study?: string; limit?: number } = {}) {
  const query = { limit: 500, ...params };
  return useQuery({
    queryKey: queryKeys.transfers(query),
    queryFn: () => api.get<Transfer[]>('/api/transfers', query),
  });
}

export function useJobs(): { jobs: Job[]; isLoading: boolean; error: unknown } {
  const { data, isLoading, error } = useTransfers({ limit: 500 });
  return { jobs: groupIntoJobs(data ?? []), isLoading, error };
}

export function useFailures(params: { status?: string; reason?: string } = {}) {
  const query = { status: 'OPEN', ...params };
  return useQuery({
    queryKey: queryKeys.failures(query),
    queryFn: () => api.get<Failure[]>('/api/failures', query),
  });
}

export function useSamQueue() {
  return useQuery({
    queryKey: queryKeys.samQueue,
    queryFn: () => api.get<SamAction[]>('/api/dashboard/sam-queue'),
  });
}

export function useMappings(activeOnly = true) {
  return useQuery({
    queryKey: queryKeys.mappings(activeOnly),
    queryFn: () => api.get<MappingRule[]>('/api/mappings', { active_only: activeOnly }),
  });
}

export function useAudit(params: {
  from?: string;
  to?: string;
  action?: string;
  performed_by?: string;
  correlation_id?: string;
  limit?: number;
}) {
  const query = { limit: 500, ...params };
  return useQuery({
    queryKey: queryKeys.audit(query),
    queryFn: () => api.get<AuditEntry[]>('/api/audit', query),
  });
}

export function useBrowse(path: string, enabled = true, root: FileRoot = 'source') {
  return useQuery({
    queryKey: queryKeys.browse(root, path),
    queryFn: () => api.get<MBoxEntry[]>('/api/mbox/browse', { path, root }),
    enabled,
  });
}

export function usePocStatus() {
  return useQuery({
    queryKey: queryKeys.pocStatus,
    queryFn: () => api.get<PocStatus>('/api/poc/status'),
    refetchInterval: 15_000,
  });
}

function useInvalidateAll() {
  const queryClient = useQueryClient();
  return () => {
    void queryClient.invalidateQueries({ queryKey: ['transfers'] });
    void queryClient.invalidateQueries({ queryKey: ['failures'] });
    void queryClient.invalidateQueries({ queryKey: ['dashboard'] });
    void queryClient.invalidateQueries({ queryKey: ['audit'] });
    void queryClient.invalidateQueries({ queryKey: ['poc'] });
    void queryClient.invalidateQueries({ queryKey: ['mbox'] });
  };
}

export function useRunPoc() {
  const invalidate = useInvalidateAll();
  return useMutation({
    mutationFn: (overrideDuplicate: boolean) =>
      api.post<PocRunResponse>('/api/poc/run', { override_duplicate: overrideDuplicate }),
    onSuccess: invalidate,
  });
}

export function useResetPoc() {
  const invalidate = useInvalidateAll();
  return useMutation({
    mutationFn: () => api.post<PocCounts>('/api/poc/reset'),
    onSuccess: invalidate,
  });
}

export function useSubmitTransfers() {
  const invalidate = useInvalidateAll();
  return useMutation({
    mutationFn: (payload: TransferSubmitRequest) => {
      const correlationId = payload.correlation_id ?? newCorrelationId();
      return api.post<TransferSubmitResponse>(
        '/api/transfers/submit',
        { ...payload, correlation_id: correlationId },
        { correlationId },
      );
    },
    onSuccess: invalidate,
  });
}

export function useRetryTransfer() {
  const invalidate = useInvalidateAll();
  return useMutation({
    mutationFn: (transferId: string) => api.post<Transfer>(`/api/transfers/${transferId}/retry`),
    onSuccess: invalidate,
  });
}

export function useResolveFailure() {
  const invalidate = useInvalidateAll();
  return useMutation({
    mutationFn: (input: { failureId: number; override: MappingOverride; persistAsRule: boolean; reason?: string }) =>
      api.put<FailureResolveResponse>(`/api/failures/${input.failureId}/resolve`, {
        mapping_override: input.override,
        persist_as_rule: input.persistAsRule,
        reason: input.reason,
      }),
    onSuccess: invalidate,
  });
}

export function useEscalateFailure() {
  const invalidate = useInvalidateAll();
  return useMutation({
    mutationFn: (input: { failureId: number; assignee?: string }) =>
      api.put<Failure>(`/api/failures/${input.failureId}/escalate`, undefined, {
        params: input.assignee ? { assignee: input.assignee } : undefined,
      }),
    onSuccess: invalidate,
  });
}

export function useCreateMapping() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: MappingRuleInput) => api.post<MappingRule>('/api/mappings', input),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['mappings'] }),
  });
}

export function useUpdateMapping() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: { mappingId: number; changes: Partial<MappingRuleInput> }) =>
      api.put<MappingRule>(`/api/mappings/${input.mappingId}`, input.changes),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['mappings'] }),
  });
}

export function useDeactivateMapping() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (mappingId: number) => api.delete<MappingRule>(`/api/mappings/${mappingId}`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['mappings'] }),
  });
}

export function useRefreshPicklists() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<{ refreshed_at: string; counts: Record<string, number> }>('/api/admin/picklists/refresh'),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['dashboard'] }),
  });
}
