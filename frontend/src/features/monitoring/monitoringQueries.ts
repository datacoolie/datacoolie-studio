import { infiniteQueryOptions, keepPreviousData, queryOptions } from "@tanstack/react-query";
import { api } from "../../shared/api/client";
import type { MonitoringLogAdmission, MonitoringRecord, MonitoringRegistryRunReference as MonitoringRegistryRunReferenceTransport, MonitoringReport, MonitoringSessionScope } from "../../shared/api/domainTypes";
import type { MonitoringPageResponseTransport } from "../../shared/api/contractTypes";
import { ApiRequestError } from "../../shared/lib/errors";
import { environmentQueryKeys } from "../environments/environmentQueries";
import type { TableSort } from "../../shared/components/DataTable";
import type { MonitoringFilters, MonitoringTabKey } from "./monitoringFilters";

export const SYSTEM_LOG_PAGE_SIZE = 500;

const EMPTY_MONITORING_REPORT = {
  health: { status: "unknown", label: "Unknown", reasons: [] },
  attention: [],
  coverage: {},
  reconciliation: { status: "unknown", mismatch_count: 0, checks: [] },
  diagnostics: { kpis: {}, job_id_evidence: [], read_errors: [] },
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
  originScope: string;
}

export type MonitoringRegistryFacet = "freshness" | "performance" | "volume" | "all";

/**
 * Provenance for the registry response that produced a selected entity row.
 *
 * The drawer uses this as a validation fence.  A generation by itself is not
 * enough because the same entity can be present in a different filter, time
 * anchor, facet, or presentation revision.
 */
export interface MonitoringRegistryOrigin {
  environmentId: number;
  facet: MonitoringRegistryFacet;
  dataflowId: string;
  rowIdentity: string;
  scope: string;
  asOf: string | null;
  analyticsGeneration: string | null;
  /** Assets identity input revision that produced the selected row. */
  identityVersion?: string | null;
  presentationScope: string;
}

type MonitoringIdentitySummary = {
  analytics_generation?: string | null;
  as_of?: string | null;
  identity_version?: string | null;
};

/** Keep identity-version handling at the query boundary so old servers remain readable. */
export function monitoringIdentityVersion(value: unknown): string | null {
  if (value === undefined || value === null) return null;
  const normalized = String(value).trim();
  return normalized || null;
}

export function monitoringResponseIdentityVersion(
  response: { summary?: { identity_version?: string | null } } | null | undefined,
) {
  return monitoringIdentityVersion(response?.summary?.identity_version);
}

export function monitoringResponseMatchesIdentityVersion(
  response: { summary?: { identity_version?: string | null } } | null | undefined,
  expected: unknown,
) {
  const expectedVersion = monitoringIdentityVersion(expected);
  return !expectedVersion || monitoringResponseIdentityVersion(response) === expectedVersion;
}

export function monitoringRegistryVersionToken(origin: MonitoringRegistryOrigin | null | undefined) {
  return JSON.stringify([
    origin?.analyticsGeneration ?? null,
    origin?.identityVersion ?? null,
  ]);
}

export function monitoringRegistryScope(filters: MonitoringFilters) {
  return JSON.stringify(monitoringQueryParams(filters));
}

export function monitoringRegistryRowIdentity(row: Record<string, unknown>) {
  return JSON.stringify([
    row.dataflow_id ?? null,
    row.dataflow_run_id ?? null,
    row.latest_run_id ?? null,
    row._source_id ?? row.source_id ?? null,
    row.latest_source_id ?? null,
    row.log_session_id ?? null,
    row.latest_log_session_id ?? null,
    row._file_uri ?? null,
    row.latest_file_uri ?? null,
    row._record_line ?? null,
    row.latest_record_line ?? null,
  ]);
}

export function monitoringRegistryOriginForResponse(
  environmentId: number,
  facet: MonitoringRegistryFacet,
  filters: MonitoringFilters,
  row: Record<string, unknown>,
  response: { summary: MonitoringIdentitySummary },
  presentationScope: string | null | undefined,
): MonitoringRegistryOrigin | null {
  const dataflowId = String(row.dataflow_id ?? "");
  if (!dataflowId.trim()) return null;
  const analyticsGeneration = normalizeRegistryIdentity(response.summary.analytics_generation);
  const responseAsOf = normalizeRegistryIdentity(response.summary.as_of);
  return {
    environmentId,
    facet,
    dataflowId,
    rowIdentity: monitoringRegistryRowIdentity(row),
    scope: monitoringRegistryScope(filters),
    asOf: responseAsOf,
    analyticsGeneration,
    identityVersion: monitoringIdentityVersion(response.summary.identity_version),
    presentationScope: presentationScope ?? "",
  };
}

export function isMonitoringRegistryOriginValid(origin: MonitoringRegistryOrigin | null | undefined): origin is MonitoringRegistryOrigin & { asOf: string; analyticsGeneration: string } {
  const asOfTime = origin?.asOf ? Date.parse(origin.asOf) : Number.NaN;
  return Boolean(
    origin
      && origin.environmentId > 0
      && origin.dataflowId
      && origin.scope
      && origin.asOf
      && Number.isFinite(asOfTime)
      && origin.analyticsGeneration
      && typeof origin.presentationScope === "string",
  );
}

export function sameMonitoringRegistryInstant(left: string | null | undefined, right: string | null | undefined) {
  if (!left || !right) return left === right;
  const leftTime = Date.parse(left);
  const rightTime = Date.parse(right);
  return Number.isFinite(leftTime) && Number.isFinite(rightTime)
    ? leftTime === rightTime
    : left === right;
}

export function monitoringRegistryResponseMatchesOrigin(
  response: { summary: MonitoringIdentitySummary } | null | undefined,
  origin: MonitoringRegistryOrigin | null | undefined,
) {
  const responseAsOf = normalizeRegistryIdentity(response?.summary.as_of);
  const responseAsOfTime = responseAsOf ? Date.parse(responseAsOf) : Number.NaN;
  return Boolean(
    isMonitoringRegistryOriginValid(origin)
      && response
      && normalizeRegistryIdentity(response.summary.analytics_generation) === origin.analyticsGeneration
      && responseAsOf
      && Number.isFinite(responseAsOfTime)
      && sameMonitoringRegistryInstant(responseAsOf, origin.asOf)
      && (monitoringIdentityVersion(origin.identityVersion) === null
        || monitoringIdentityVersion(response.summary.identity_version) === monitoringIdentityVersion(origin.identityVersion)),
  );
}

export function monitoringRegistryOriginsMatch(
  left: MonitoringRegistryOrigin | null | undefined,
  right: MonitoringRegistryOrigin | null | undefined,
) {
  return Boolean(
    isMonitoringRegistryOriginValid(left)
      && isMonitoringRegistryOriginValid(right)
      && left.environmentId === right.environmentId
      && left.facet === right.facet
      && left.dataflowId === right.dataflowId
      && left.scope === right.scope
      && left.presentationScope === right.presentationScope
      && left.analyticsGeneration === right.analyticsGeneration
      && monitoringIdentityVersion(left.identityVersion) === monitoringIdentityVersion(right.identityVersion)
      && sameMonitoringRegistryInstant(left.asOf, right.asOf),
  );
}

function normalizeRegistryIdentity(value: unknown): string | null {
  if (value === undefined || value === null) return null;
  const normalized = String(value).trim();
  return normalized || null;
}

function mergeSection<T extends object>(value: unknown, fallback: T): T {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return fallback;
  }
  return { ...fallback, ...(value as Partial<T>) };
}

function arrayOrDefault<T>(value: unknown, fallback: T[]): T[] {
  return Array.isArray(value) ? (value as T[]) : fallback;
}

export function normalizeMonitoringPage(response: MonitoringPageResponseTransport): MonitoringReport {
  const responseSummary = response.summary as typeof response.summary & { identity_version?: string | null };
  return {
    ...EMPTY_MONITORING_REPORT,
    summary: {
      ...response.summary,
      identity_version: monitoringIdentityVersion(responseSummary.identity_version),
      date_range: {
        min: response.summary.date_range?.min ?? null,
        max: response.summary.date_range?.max ?? null,
      },
    },
    log_admission: normalizeLogAdmission(response.log_admission),
    health: mergeSection(response.health, EMPTY_MONITORING_REPORT.health),
    attention: arrayOrDefault(response.attention, EMPTY_MONITORING_REPORT.attention),
    coverage: mergeSection(response.coverage, EMPTY_MONITORING_REPORT.coverage),
    reconciliation: mergeSection(response.reconciliation, EMPTY_MONITORING_REPORT.reconciliation),
    diagnostics: mergeSection(response.diagnostics, EMPTY_MONITORING_REPORT.diagnostics),
    operations: mergeSection(response.operations, EMPTY_MONITORING_REPORT.operations),
    failures: mergeSection(response.failures, EMPTY_MONITORING_REPORT.failures),
    performance: mergeSection(response.performance, EMPTY_MONITORING_REPORT.performance),
    volume: mergeSection(response.volume, EMPTY_MONITORING_REPORT.volume),
    maintenance: mergeSection(response.maintenance, EMPTY_MONITORING_REPORT.maintenance),
    freshness: mergeSection(response.freshness, EMPTY_MONITORING_REPORT.freshness),
    errors: arrayOrDefault(response.errors, EMPTY_MONITORING_REPORT.errors),
  };
}

export function monitoringQueryParams(filters: MonitoringFilters) {
  const sessionScope = resolvedSessionScope(filters.investigateLogSessionScope, filters.investigateLogSessionId);
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
    log_session_scope: sessionScope,
    ...(sessionScope === "exact" ? { log_session_id: filters.investigateLogSessionId ?? "" } : {}),
    ...(filters.investigateSourceId == null ? {} : { investigateSourceId: filters.investigateSourceId }),
  };
}

export function monitoringReportScope(environmentId: number, page: MonitoringTabKey, filters: MonitoringFilters, timezoneScope?: string, identityRevision?: string | null) {
  return JSON.stringify([environmentId, page, monitoringQueryParams(filters), timezoneScope ?? null, monitoringIdentityVersion(identityRevision)]);
}

export function monitoringReportOptions(environmentId: number, page: MonitoringTabKey, filters: MonitoringFilters, timezoneScope?: string, identityRevision?: string | null) {
  const params = monitoringQueryParams(filters);
  const timeDependent = page === "freshness";
  return queryOptions({
    queryKey: [...environmentQueryKeys.monitoringReport(environmentId, page, params), ...(timezoneScope === undefined ? [] : [timezoneScope]), monitoringIdentityVersion(identityRevision)],
    queryFn: async ({ signal }): Promise<MonitoringPageQueryData> => ({
      page,
      originScope: monitoringReportScope(environmentId, page, filters, timezoneScope, identityRevision),
      report: normalizeMonitoringPage(await api.getMonitoringPage(environmentId, page, params, signal)),
    }),
    staleTime: timeDependent ? 60_000 : Number.POSITIVE_INFINITY,
    refetchInterval: timeDependent ? 60_000 : false,
    placeholderData: keepPreviousData,
  });
}

export function monitoringFilterOptionsOptions(environmentId: number, filters: MonitoringFilters) {
  const params = {
    range: filters.range,
    ...(filters.range === "custom" ? { startTime: filters.startTime, endTime: filters.endTime } : {}),
  };
  const relative = filters.range !== "all" && filters.range !== "custom";
  return queryOptions({
    queryKey: environmentQueryKeys.monitoringFilterOptions(environmentId, params),
    queryFn: ({ signal }) => api.getMonitoringFilterOptions(environmentId, params, signal),
    staleTime: relative ? 60_000 : Number.POSITIVE_INFINITY,
    refetchInterval: relative ? 60_000 : false,
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
    queryFn: ({ signal }) => api.getMonitoringJobs(environmentId, params, signal),
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
    queryFn: ({ signal }) => api.getMonitoringDataflows(environmentId, params, signal),
    staleTime: Number.POSITIVE_INFINITY,
    placeholderData: keepPreviousData,
  });
}

/**
 * Physical provenance carried by a registry-origin run click.
 *
 * The registry table is a canonical view over possibly duplicated source
 * records.  Keeping this context at the query boundary lets the exact-run
 * caller reject a different physical winner before it is merged into the
 * clicked row.  All fields are optional for compatibility with ordinary run
 * drawers; registry-origin callers populate the fields they observed.
 */
export type MonitoringRegistryRunReference = MonitoringRegistryRunReferenceTransport & {
  analyticsGeneration?: string | null;
  logSessionScope?: MonitoringSessionScope;
  registryOrigin?: MonitoringRegistryOrigin;
};

export interface MonitoringDataflowRunDetailEnvelope {
  row: MonitoringRecord | null;
  summary: {
    analytics_generation?: string | null;
    as_of?: string | null;
    identity_version?: string | null;
  };
}

export function monitoringDataflowRunDetailOptions(
  environmentId: number,
  dataflowRunId: string,
  logSessionId?: string | null,
  sourceId?: number,
  logSessionScope?: MonitoringSessionScope,
  registryReference?: MonitoringRegistryRunReference,
) {
  const normalizedRunId = dataflowRunId;
  const sessionScope = registryReference?.logSessionScope ?? resolvedSessionScope(logSessionScope, logSessionId);
  const requestedSessionId: string | null | undefined = registryReference?.log_session_id ?? logSessionId;
  const requestedSourceId = registryReference?._source_id ?? sourceId;
  const params = {
    range: "all",
    investigateKind: "dataflow_run_id",
    investigateValue: normalizedRunId,
    log_session_scope: sessionScope,
    ...(sessionScope === "exact" ? { log_session_id: requestedSessionId ?? "" } : {}),
    ...(requestedSourceId == null ? {} : { investigateSourceId: requestedSourceId }),
    limit: 1,
    offset: 0,
  };
  return queryOptions({
    queryKey: [
      ...environmentQueryKeys.monitoring(environmentId),
      "dataflow-run-detail",
      normalizedRunId,
      sessionScope,
      requestedSessionId ?? null,
      requestedSourceId ?? null,
      registryReference ?? null,
    ] as const,
    queryFn: async ({ signal }): Promise<MonitoringRecord | MonitoringDataflowRunDetailEnvelope | null> => {
      const response = await api.getMonitoringDataflows(environmentId, params, signal);
      const row = response.records[0] ?? null;
      return registryReference
        ? { row, summary: response.summary }
        : row;
    },
    staleTime: Number.POSITIVE_INFINITY,
    enabled: Boolean(normalizedRunId),
  });
}

export function monitoringJobRunDetailOptions(environmentId: number, jobId: string, logSessionId?: string | null, sourceId?: number, logSessionScope?: MonitoringSessionScope) {
  const normalizedJobId = jobId;
  const sessionScope = resolvedSessionScope(logSessionScope, logSessionId);
  const params = {
    range: "all",
    investigateKind: "job_id",
    investigateValue: normalizedJobId,
    log_session_scope: sessionScope,
    ...(sessionScope === "exact" ? { log_session_id: logSessionId ?? "" } : {}),
    ...(sourceId == null ? {} : { investigateSourceId: sourceId }),
    limit: 1,
    offset: 0,
  };
  return queryOptions({
    queryKey: [...environmentQueryKeys.monitoring(environmentId), "job-run-detail", normalizedJobId, sessionScope, logSessionId ?? null, sourceId ?? null] as const,
    queryFn: async ({ signal }) => (await api.getMonitoringJobs(environmentId, params, signal)).records[0] ?? null,
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
  view?: "detail" | "registry",
  identityRevision?: string | null,
) {
  const params = {
    ...monitoringQueryParams(filters),
    ...(view ? { view } : {}),
    limit: pagination.limit,
    offset: pagination.offset,
    sortBy: pagination.sort.sortBy,
    sortDir: pagination.sort.sortDir,
  };
  return queryOptions({
    queryKey: [...environmentQueryKeys.monitoring(environmentId), "evidence", page, params, monitoringIdentityVersion(identityRevision)] as const,
    queryFn: ({ signal }) => api.getMonitoringPageEvidence(environmentId, page, params, signal),
    staleTime: Number.POSITIVE_INFINITY,
    placeholderData: (previous, previousQuery) => {
      if (page !== "maintenance") return previous;
      const previousParams = previousQuery?.queryKey[5] as Record<string, unknown> | undefined;
      const paging = new Set(["limit", "offset", "sortBy", "sortDir"]);
      return previousQuery?.queryKey[4] === page
        && previousQuery?.queryKey[6] === monitoringIdentityVersion(identityRevision)
        && previousParams && Object.keys(params)
        .filter(key => !paging.has(key)).every(key => previousParams[key] === (params as Record<string, unknown>)[key])
        ? previous : undefined;
    },
  });
}

/**
 * Page Diagnostics finding groups (`registry`) or one group's affected objects
 * (`detail`). Every request carries the page anchor and generation so a changed
 * snapshot is rejected (409) instead of mixing rows from two generations.
 */
export function monitoringDiagnosticsEvidenceOptions(
  environmentId: number,
  filters: MonitoringFilters,
  request: {
    view: "registry" | "detail";
    findingGroup?: string | null;
    findingKind?: "all" | "findings" | "limitations";
    limit: number;
    offset: number;
    sort?: TableSort | null;
    asOf: string | null;
    generation: string | null;
    enabled?: boolean;
  },
) {
  const params = {
    ...monitoringQueryParams(filters),
    view: request.view,
    ...(request.view === "detail" ? { findingGroup: request.findingGroup ?? "" } : { findingKind: request.findingKind ?? "all" }),
    ...(request.asOf ? { asOf: request.asOf } : {}),
    ...(request.generation ? { expectedGeneration: request.generation } : {}),
    limit: request.limit,
    offset: request.offset,
    ...(request.sort ? { sortBy: request.sort.sortBy, sortDir: request.sort.sortDir } : {}),
  };
  const scope = (value: Record<string, unknown>) => {
    const { limit: _limit, offset: _offset, sortBy: _sortBy, sortDir: _sortDir, ...rest } = value;
    return JSON.stringify(rest);
  };
  return queryOptions({
    queryKey: [...environmentQueryKeys.monitoring(environmentId), "diagnostics-evidence", params] as const,
    queryFn: ({ signal }) => api.getMonitoringDiagnosticsEvidence(environmentId, params, signal),
    staleTime: Number.POSITIVE_INFINITY,
    enabled: request.enabled !== false && (request.view === "registry" || Boolean(request.findingGroup)),
    retry: (count, error) => !(error instanceof ApiRequestError && error.status === 409) && count < 2,
    placeholderData: (previous, previousQuery) => {
      const previousParams = previousQuery?.queryKey[4] as Record<string, unknown> | undefined;
      return previousParams && scope(previousParams) === scope(params) ? previous : undefined;
    },
  });
}

/**
 * Page-independent registry identity.  The entry page is intentionally absent
 * from the key so equal filters and analytics generation share one entity
 * population across Freshness, Performance and Volume.
 */
export function monitoringRegistryAnchor(now = Date.now()) {
  return new Date(Math.floor(now / 60_000) * 60_000).toISOString();
}

export function monitoringDataflowRegistryOptions(
  environmentId: number,
  filters: MonitoringFilters,
  pagination: { limit: number; offset: number; sort: TableSort },
  targetDataflowId?: string | null,
  section: "overview" | "runtime" | "execution" | "read-watermark" | "configuration" | "technical" = "overview",
  asOf?: string,
  facet: "all" | "freshness" | "performance" | "volume" = "all",
  presentationScope = "",
  registryGeneration = "",
) {
  const params = {
    ...monitoringQueryParams(filters),
    asOf,
    ...(targetDataflowId == null ? { facet } : {}),
    limit: pagination.limit,
    offset: pagination.offset,
    sortBy: pagination.sort.sortBy,
    sortDir: pagination.sort.sortDir,
    ...(targetDataflowId != null ? { section } : {}),
  };
  return queryOptions({
    queryKey: [...environmentQueryKeys.monitoring(environmentId), "dataflow-registry", targetDataflowId ?? "list", section, params, presentationScope, registryGeneration] as const,
    queryFn: ({ signal }) => targetDataflowId == null
      ? api.getMonitoringDataflowRegistry(environmentId, params, signal)
      : api.getMonitoringDataflowRegistryDetail(environmentId, targetDataflowId, params, signal),
    staleTime: Number.POSITIVE_INFINITY,
    enabled: targetDataflowId == null || Boolean(targetDataflowId),
    placeholderData: (previous, previousQuery) => {
      const previousParams = previousQuery?.queryKey[6] as typeof params | undefined;
      if (previousQuery?.queryKey[7] !== presentationScope || previousQuery?.queryKey[8] !== registryGeneration) return undefined;
      if (!previousParams || previousQuery?.queryKey[1] !== environmentId || previousQuery?.queryKey[4] !== (targetDataflowId ?? "list") || previousQuery?.queryKey[5] !== section) return undefined;
      if (targetDataflowId != null && (previousParams.limit !== params.limit || previousParams.offset !== params.offset || previousParams.sortBy !== params.sortBy || previousParams.sortDir !== params.sortDir)) return undefined;
      const { asOf: _oldAnchor, limit: _oldLimit, offset: _oldOffset, sortBy: _oldSort, sortDir: _oldDirection, ...oldScope } = previousParams;
      const { asOf: _newAnchor, limit: _newLimit, offset: _newOffset, sortBy: _newSort, sortDir: _newDirection, ...newScope } = params;
      return JSON.stringify(oldScope) === JSON.stringify(newScope) ? previous : undefined;
    },
  });
}

/**
 * Resolve one Freshness registry entity before opening an investigation drawer.
 * The chart is populated from a complete aggregate and may contain an entity
 * outside the currently paged registry rows, so the drawer must not be opened
 * with the aggregate row as if it were the latest registry record.
 */
export function monitoringFreshnessEntityOptions(
  environmentId: number,
  filters: MonitoringFilters,
  target: { sourceId?: number | null; dataflowId?: string | null },
) {
  const dataflowId = target.dataflowId == null ? "" : String(target.dataflowId);
  const sourceId = target.sourceId == null ? undefined : Number(target.sourceId);
  const filterParams = monitoringQueryParams(filters);
  const params = {
    ...filterParams,
    view: "detail",
    investigateKind: "dataflow_id",
    investigateValue: dataflowId,
    ...(sourceId == null || !Number.isFinite(sourceId) ? {} : { investigateSourceId: sourceId }),
    limit: 1,
    offset: 0,
    sortBy: "latest_freshness_at",
    sortDir: "desc",
  };
  return queryOptions({
    queryKey: [...environmentQueryKeys.monitoring(environmentId), "freshness-entity", params] as const,
    queryFn: ({ signal }) => api.getMonitoringPageEvidence(environmentId, "freshness", params, signal),
    staleTime: 0,
    enabled: Boolean(dataflowId),
  });
}

export function monitoringDetailDataflowsOptions(
  environmentId: number,
  request: { key: string; params: Record<string, string | number | undefined> } | null,
) {
  return queryOptions({
    queryKey: [...environmentQueryKeys.monitoring(environmentId), "detail-dataflows", request?.key ?? "inactive", request?.params ?? {}] as const,
    queryFn: ({ signal }) => request!.params.view === "destination-runs"
      ? api.getMonitoringPageEvidence(environmentId, "maintenance", request!.params, signal)
      : api.getMonitoringDataflows(environmentId, request!.params, signal),
    staleTime: Number.POSITIVE_INFINITY,
    // A destination history response can return 409 when its Assets identity
    // revision has expired. Retrying that deterministic selection only delays
    // the drawer fence and keeps the obsolete row visible longer.
    retry: (failureCount, error) => !(error instanceof ApiRequestError && error.status === 409) && failureCount < 1,
    enabled: Boolean(request),
    placeholderData: (previous, previousQuery) => {
      const previousParams = previousQuery?.queryKey[5] as Record<string, unknown> | undefined;
      const paginationFields = new Set(["limit", "offset", "sortBy", "sortDir"]);
      const scopeFields = new Set([
        ...Object.keys(previousParams ?? {}),
        ...Object.keys(request?.params ?? {}),
      ]);
      const sameScope = previousParams && request && [...scopeFields]
        .filter(field => !paginationFields.has(field))
        .every(field => previousParams[field] === request.params[field]);
      return previousQuery?.queryKey[1] === environmentId
        && previousQuery?.queryKey[4] === request?.key
        && request && sameScope
        ? previous : undefined;
    },
  });
}

export function monitoringDestinationDetailOptions(environmentId: number, filters: MonitoringFilters, row: MonitoringRecord | null) {
  const params = {
    ...monitoringQueryParams(filters), view: "detail",
    destinationTarget: row ? String(row.target ?? "") : "",
    expectedIdentityVersion: row ? monitoringIdentityVersion(row.identity_version) ?? undefined : undefined,
    limit: 1, offset: 0, sortBy: "run_count", sortDir: "desc",
  };
  return queryOptions({
    queryKey: [...environmentQueryKeys.monitoring(environmentId), "destination-detail", params] as const,
    queryFn: ({ signal }) => api.getMonitoringPageEvidence(environmentId, "maintenance", params, signal),
    enabled: Boolean(params.destinationTarget), staleTime: Number.POSITIVE_INFINITY,
    retry: (failureCount, error) => !(error instanceof ApiRequestError && error.status === 409) && failureCount < 1,
  });
}

export interface SystemLogQueryScope {
  jobId: string;
  sourceId?: number;
  logSessionScope?: MonitoringSessionScope;
  logSessionId?: string | null;
  dataflowId?: string;
  dataflowRunId?: string;
  eventName?: string;
  includeDataflowLogs: boolean;
  level: string;
  query: string;
}

export function systemLogScopeParams(
  dataflowId: string | undefined,
  includeDataflowLogs: boolean,
  dataflowRunId?: string,
  eventName?: string,
  sourceId?: number,
  logSessionId?: string | null,
  logSessionScope?: MonitoringSessionScope,
) {
  const params: Record<string, string | number> = {};
  if (sourceId !== undefined) params.source_id = sourceId;
  const sessionScope = resolvedSessionScope(logSessionScope, logSessionId);
  params.log_session_scope = sessionScope;
  if (sessionScope === "exact") params.log_session_id = logSessionId ?? "";
  if (dataflowId) params.dataflow_id = dataflowId;
  if (dataflowRunId) params.dataflow_run_id = dataflowRunId;
  if (eventName) params.event_name = eventName;
  if (includeDataflowLogs && !dataflowId && !dataflowRunId) {
    params.include_dataflow_logs = 1;
  }
  return params;
}

export function monitoringSystemLogsOptions(environmentId: number, scope: SystemLogQueryScope) {
  return infiniteQueryOptions({
    queryKey: [...environmentQueryKeys.monitoring(environmentId), "system-logs", scope] as const,
    queryFn: ({ pageParam, signal }) => api.getMonitoringSystemLogs(environmentId, {
      job_id: scope.jobId,
      ...systemLogScopeParams(
        scope.dataflowId,
        scope.includeDataflowLogs,
        scope.dataflowRunId,
        scope.eventName,
        scope.sourceId,
        scope.logSessionId,
        scope.logSessionScope,
      ),
      level: scope.level || undefined,
      q: scope.query || undefined,
      limit: SYSTEM_LOG_PAGE_SIZE,
      offset: pageParam,
    }, signal),
    initialPageParam: 0,
    getNextPageParam: nextSystemLogOffset,
    staleTime: Number.POSITIVE_INFINITY,
  });
}

export function systemLogDownloadParams(scope: Pick<SystemLogQueryScope, "jobId" | "sourceId" | "logSessionScope" | "logSessionId">) {
  return {
    job_id: scope.jobId,
    ...systemLogScopeParams(undefined, false, undefined, undefined, scope.sourceId, scope.logSessionId, scope.logSessionScope),
  };
}

function normalizeLogAdmission(value: unknown): MonitoringLogAdmission | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const source = value as Record<string, unknown>;
  const files = Array.isArray(source.files)
    ? source.files.filter((item): item is Record<string, unknown> => Boolean(item && typeof item === "object" && !Array.isArray(item))).map((item) => ({
      source_id: typeof item.source_id === "number" ? item.source_id : null,
      uri: typeof item.uri === "string" ? item.uri : null,
      admitted: metricTransport(item.admitted),
      excluded: metricTransport(item.excluded),
      versions: normalizeMetricRecord(item.versions),
      locations: Array.isArray(item.locations)
        ? item.locations.filter((location): location is Record<string, unknown> => Boolean(location && typeof location === "object" && !Array.isArray(location))).map((location) => ({
          line: typeof location.line === "number" ? location.line : null,
          version: typeof location.version === "string" ? location.version : null,
        }))
        : [],
    }))
    : [];
  return {
    admitted: metricTransport(source.admitted),
    excluded: metricTransport(source.excluded),
    unsupported_only: source.unsupported_only === true,
    files,
  };
}

function normalizeMetricRecord(value: unknown) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  return Object.fromEntries(Object.entries(value as Record<string, unknown>).map(([key, entry]) => [key, metricTransport(entry)]));
}

function metricTransport(value: unknown) {
  return typeof value === "string" || typeof value === "number" || value === null ? value : null;
}

export function resolvedSessionScope(scope?: MonitoringSessionScope, sessionId?: string | null): MonitoringSessionScope {
  if (scope) return scope;
  return sessionId !== undefined ? "exact" : "all";
}

export function monitoringRowSessionScope(row: Record<string, unknown>): MonitoringSessionScope {
  if (!Object.prototype.hasOwnProperty.call(row, "log_session_id")) return "all";
  return row.log_session_id === null ? "null" : "exact";
}

export function nextSystemLogOffset(
  lastPage: { records: unknown[]; total: number },
  pages: Array<{ records: unknown[] }>,
) {
  const loaded = pages.reduce((total, page) => total + page.records.length, 0);
  return loaded < lastPage.total && lastPage.records.length === SYSTEM_LOG_PAGE_SIZE ? loaded : undefined;
}
