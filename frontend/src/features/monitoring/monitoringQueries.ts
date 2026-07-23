import { infiniteQueryOptions, keepPreviousData, queryOptions } from "@tanstack/react-query";
import { api } from "../../shared/api/client";
import type {
  MonitoringPageResponse,
  MonitoringReport,
} from "../../shared/api/domainTypes";
import { environmentQueryKeys } from "../environments/environmentQueries";
import type { TableSort } from "./MonitoringCharts";
import type { MonitoringFilters, MonitoringTabKey } from "./monitoringFilters";

export const SYSTEM_LOG_PAGE_SIZE = 500;

const EMPTY_MONITORING_REPORT = {
  health: { status: "unknown", label: "Unknown", reasons: [] },
  attention: [],
  coverage: {},
  reconciliation: { status: "unknown", mismatch_count: 0, checks: [] },
  diagnostics: { kpis: {}, job_id_evidence: [], read_errors: [] },
  metric_definitions: {},
  operations: {
    kpis: {}, job_status_distribution: [], jobs_by_date_status: [], dataflows_by_date_status: [],
    failed_jobs: [], dataflow_kpis: {}, status_by_stage: [],
  },
  failures: {
    failed_by_stage: [], failed_by_source_connection_type: [], top_failing_dataflows: [],
    error_categories: [], failure_trend_by_date: [], failed_records: [],
  },
  performance: {
  },
  volume: {
    kpis: {}, rows_by_date: [], bytes_by_date: [], volume_by_load_type: [], top_dataflows_by_rows_written: [],
  },
  maintenance: {
    kpis: {}, format_comparison: [], bytes_reclaimed_by_date: [],
  },
  freshness: {
    kpis: {}, age_by_dataflow: [],
  },
  errors: [],
} satisfies Omit<MonitoringReport, "summary">;

export interface MonitoringPageQueryData {
  page: MonitoringTabKey;
  report: MonitoringReport;
}

export function normalizeMonitoringPage(response: MonitoringPageResponse): MonitoringReport {
  const { schema_version: _schemaVersion, page: _page, ...sections } = response;
  return {
    ...EMPTY_MONITORING_REPORT,
    ...sections,
    metric_definitions: {},
    errors: response.errors ?? [],
  };
}

export function monitoringQueryParams(filters: MonitoringFilters) {
  return {
    range: filters.range,
    grain: filters.grain,
    startTime: filters.range === "custom" ? filters.startTime : undefined,
    endTime: filters.range === "custom" ? filters.endTime : undefined,
    status: filters.status,
    stage: filters.stage,
    connection: filters.connection,
    engine: filters.engine,
    provider: filters.provider,
    sourceType: filters.sourceType,
    destinationType: filters.destinationType,
    loadType: filters.loadType,
    operationType: filters.operationType,
    search: filters.search,
    investigateKind: filters.investigateKind,
    investigateValue: filters.investigateValue,
  };
}

export function monitoringReportOptions(environmentId: number, page: MonitoringTabKey, filters: MonitoringFilters) {
  const params = monitoringQueryParams(filters);
  const timeDependent = page === "freshness";
  return queryOptions({
    queryKey: environmentQueryKeys.monitoringReport(environmentId, page, params),
    queryFn: async (): Promise<MonitoringPageQueryData> => ({
      page,
      report: normalizeMonitoringPage(await api.getMonitoringPage(environmentId, page, params)),
    }),
    staleTime: timeDependent ? 60_000 : Number.POSITIVE_INFINITY,
    refetchInterval: timeDependent ? 60_000 : false,
    placeholderData: keepPreviousData,
  });
}

export function monitoringFilterOptionsOptions(environmentId: number) {
  return queryOptions({
    queryKey: environmentQueryKeys.monitoringFilterOptions(environmentId),
    queryFn: () => api.getMonitoringFilterOptions(environmentId),
    staleTime: Number.POSITIVE_INFINITY,
  });
}

export function monitoringJobsOptions(
  environmentId: number,
  filters: MonitoringFilters,
  pagination: { limit: number; offset: number; sort: TableSort },
) {
  const params = { ...monitoringQueryParams(filters), limit: pagination.limit, offset: pagination.offset, sortBy: pagination.sort.sortBy, sortDir: pagination.sort.sortDir };
  return queryOptions({
    queryKey: [...environmentQueryKeys.monitoring(environmentId), "runs", "jobs", params] as const,
    queryFn: () => api.getMonitoringJobs(environmentId, params),
    staleTime: Number.POSITIVE_INFINITY,
    placeholderData: keepPreviousData,
  });
}

export function monitoringDataflowsOptions(
  environmentId: number,
  filters: MonitoringFilters,
  pagination: { limit: number; offset: number; sort: TableSort },
) {
  const params = { ...monitoringQueryParams(filters), limit: pagination.limit, offset: pagination.offset, sortBy: pagination.sort.sortBy, sortDir: pagination.sort.sortDir };
  return queryOptions({
    queryKey: [...environmentQueryKeys.monitoring(environmentId), "runs", "dataflows", params] as const,
    queryFn: () => api.getMonitoringDataflows(environmentId, params),
    staleTime: Number.POSITIVE_INFINITY,
    placeholderData: keepPreviousData,
  });
}

export function monitoringDataflowRunDetailOptions(environmentId: number, dataflowRunId: string) {
  const normalizedRunId = dataflowRunId.trim();
  const params = {
    range: "all",
    investigateKind: "dataflow_run_id",
    investigateValue: normalizedRunId,
    limit: 1,
    offset: 0,
  };
  return queryOptions({
    queryKey: [...environmentQueryKeys.monitoring(environmentId), "dataflow-run-detail", normalizedRunId] as const,
    queryFn: async () => (await api.getMonitoringDataflows(environmentId, params)).records[0] ?? null,
    staleTime: Number.POSITIVE_INFINITY,
    enabled: Boolean(normalizedRunId),
  });
}

export function monitoringJobRunDetailOptions(environmentId: number, jobId: string) {
  const normalizedJobId = jobId.trim();
  const params = {
    range: "all",
    investigateKind: "job_id",
    investigateValue: normalizedJobId,
    limit: 1,
    offset: 0,
  };
  return queryOptions({
    queryKey: [...environmentQueryKeys.monitoring(environmentId), "job-run-detail", normalizedJobId] as const,
    queryFn: async () => (await api.getMonitoringJobs(environmentId, params)).records[0] ?? null,
    staleTime: Number.POSITIVE_INFINITY,
    enabled: Boolean(normalizedJobId),
  });
}

export type MonitoringEvidencePage = "performance" | "freshness" | "volume" | "maintenance";

export function monitoringEvidenceOptions(
  environmentId: number,
  page: MonitoringEvidencePage,
  filters: MonitoringFilters,
  pagination: { limit: number; offset: number; sort: TableSort },
) {
  const params = { ...monitoringQueryParams(filters), limit: pagination.limit, offset: pagination.offset, sortBy: pagination.sort.sortBy, sortDir: pagination.sort.sortDir };
  return queryOptions({
    queryKey: [...environmentQueryKeys.monitoring(environmentId), "evidence", page, params] as const,
    queryFn: () => api.getMonitoringPageEvidence(environmentId, page, params),
    staleTime: Number.POSITIVE_INFINITY,
    placeholderData: keepPreviousData,
  });
}

export function monitoringDetailDataflowsOptions(
  environmentId: number,
  request: { key: string; params: Record<string, string | number | undefined> } | null,
) {
  return queryOptions({
    queryKey: [...environmentQueryKeys.monitoring(environmentId), "detail-dataflows", request?.key ?? "inactive", request?.params ?? {}] as const,
    queryFn: () => api.getMonitoringDataflows(environmentId, request!.params),
    staleTime: Number.POSITIVE_INFINITY,
    enabled: Boolean(request),
    placeholderData: keepPreviousData,
  });
}

export interface SystemLogQueryScope {
  jobId: string;
  dataflowId?: string;
  includeDataflowLogs: boolean;
  level: string;
  query: string;
}

export function systemLogScopeParams(dataflowId: string | undefined, includeDataflowLogs: boolean) {
  if (dataflowId) return { dataflow_id: dataflowId };
  return includeDataflowLogs ? { include_dataflow_logs: 1 } : {};
}

export function monitoringSystemLogsOptions(environmentId: number, scope: SystemLogQueryScope) {
  return infiniteQueryOptions({
    queryKey: [...environmentQueryKeys.monitoring(environmentId), "system-logs", scope] as const,
    queryFn: ({ pageParam }) => api.getMonitoringSystemLogs(environmentId, {
      job_id: scope.jobId,
      ...systemLogScopeParams(scope.dataflowId, scope.includeDataflowLogs),
      level: scope.level || undefined,
      q: scope.query || undefined,
      limit: SYSTEM_LOG_PAGE_SIZE,
      offset: pageParam,
    }),
    initialPageParam: 0,
    getNextPageParam: nextSystemLogOffset,
    staleTime: Number.POSITIVE_INFINITY,
  });
}

export function nextSystemLogOffset(
  lastPage: { records: unknown[]; total: number },
  pages: Array<{ records: unknown[] }>,
) {
  const loaded = pages.reduce((total, page) => total + page.records.length, 0);
  return loaded < lastPage.total && lastPage.records.length === SYSTEM_LOG_PAGE_SIZE ? loaded : undefined;
}
