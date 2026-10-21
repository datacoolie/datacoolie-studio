import { QueryClient, QueryObserver } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../../shared/api/client";
import type { JobRecord, MonitoringPageResponse, MonitoringRecordsResponse, MonitoringRecord, MonitoringRegistryResponse, MonitoringRegistryRunReference, SystemLogResponse } from "../../shared/api/domainTypes";
import { environmentQueryKeys } from "../environments/environmentQueries";
import { ApiRequestError } from "../../shared/lib/errors";
import { DEFAULT_MONITORING_FILTERS } from "./monitoringFilters";
import {
  monitoringDataflowsOptions,
  monitoringDataflowRunDetailOptions,
  monitoringDetailDataflowsOptions,
  monitoringFreshnessEntityOptions,
  monitoringJobRunDetailOptions,
  monitoringQueryParams,
  monitoringReportOptions,
  monitoringFilterOptionsOptions,
  monitoringEvidenceOptions,
  monitoringDataflowRegistryOptions,
  systemLogScopeParams,
  systemLogDownloadParams,
  monitoringSystemLogsOptions,
  nextSystemLogOffset,
  normalizeMonitoringPage,
  SYSTEM_LOG_PAGE_SIZE,
} from "./monitoringQueries";

describe("detail child query identity transitions", () => {
  it("separates compact facets while keeping entity detail independent of origin", () => {
    const paging = { limit: 100, offset: 0, sort: { sortBy: "latest_run_at", sortDir: "desc" as const } };
    const freshness = monitoringDataflowRegistryOptions(7, DEFAULT_MONITORING_FILTERS, paging, undefined, "overview", "anchor", "freshness");
    const performance = monitoringDataflowRegistryOptions(7, DEFAULT_MONITORING_FILTERS, paging, undefined, "overview", "anchor", "performance");
    expect(freshness.queryKey).not.toEqual(performance.queryKey);
    expect(monitoringDataflowRegistryOptions(7, DEFAULT_MONITORING_FILTERS, paging, "df", "overview", "anchor", "freshness").queryKey)
      .toEqual(monitoringDataflowRegistryOptions(7, DEFAULT_MONITORING_FILTERS, paging, "df", "overview", "anchor", "volume").queryKey);
  });
  it("keeps same-Job paging placeholders but never exposes another Job's rows during delayed/error loading", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const a = { key: "job:A:null::1", params: { investigateValue: "A", limit: 100, offset: 0 } };
    const aOptions = monitoringDetailDataflowsOptions(7, a);
    const response = { records: [{ job_id: "A", dataflow_id: "A-child" } as MonitoringRecord], summary: { records: 1, total_records: 1, limit: 100, offset: 0 }, errors: [] } as MonitoringRecordsResponse<MonitoringRecord>;
    client.setQueryData(aOptions.queryKey, response);
    let rejectPending: (reason: Error) => void = () => undefined;
    const request = vi.spyOn(api, "getMonitoringDataflows").mockImplementation(() => new Promise((_, reject) => { rejectPending = reject; }));
    const observer = new QueryObserver(client, aOptions);
    const unsubscribe = observer.subscribe(() => undefined);
    observer.setOptions(monitoringDetailDataflowsOptions(7, { ...a, params: { ...a.params, offset: 100 } }));
    expect(observer.getCurrentResult().isPlaceholderData).toBe(true);
    expect(observer.getCurrentResult().data?.records[0].job_id).toBe("A");
    observer.setOptions(monitoringDetailDataflowsOptions(7, { ...a, params: { ...a.params, range: "7d" } }));
    expect(observer.getCurrentResult().data).toBeUndefined();
    expect(observer.getCurrentResult().isPending).toBe(true);
    observer.setOptions(monitoringDetailDataflowsOptions(7, { key: "job:B:exact:other:2", params: { investigateValue: "B", limit: 100, offset: 0 } }));
    expect(observer.getCurrentResult().data).toBeUndefined();
    expect(observer.getCurrentResult().isPending).toBe(true);
    await vi.waitFor(() => expect(request).toHaveBeenCalledTimes(3));
    rejectPending(new ApiRequestError("B failed", 409, null));
    await vi.waitFor(() => expect(observer.getCurrentResult().isError).toBe(true));
    expect(observer.getCurrentResult().data).toBeUndefined();
    unsubscribe(); client.clear(); request.mockRestore();
  });
});

describe("original job log download scope", () => {
  it("preserves exact empty session and lexical job identity in the shared API URL", () => {
    const params = systemLogDownloadParams({ jobId: " JOB/+?# ", sourceId: 2, logSessionScope: "exact", logSessionId: "" });
    const url = new URL(api.monitoringSystemLogDownloadUrl(1, params), "http://localhost");
    expect(url.pathname).toBe("/api/v1/environments/1/monitoring/system-logs/download");
    expect(Object.fromEntries(url.searchParams)).toEqual({ job_id: " JOB/+?# ", source_id: "2", log_session_scope: "exact", log_session_id: "" });
  });

  it("does not include preview filters, pagination or child scope in export parameters", () => {
    const scope = { jobId: "job-1", sourceId: 3, logSessionScope: "null" as const, dataflowId: "df-1", dataflowRunId: "run-1", includeDataflowLogs: false, level: "ERROR", query: "needle" };
    expect(systemLogDownloadParams(scope)).toEqual({ job_id: "job-1", source_id: 3, log_session_scope: "null" });
  });
});

const summary = {
  dataflow_records: 0,
  job_records: 0,
  date_range: {},
  active_engines: 0,
  active_metadata_providers: 0,
  log_base_paths: 0,
};
const overviewResponse = {
  schema_version: "monitoring-page.v11",
  page: "overview",
  summary,
  health: { status: "healthy", label: "Healthy", reasons: [] },
  attention: [],
  operations: { kpis: {}, job_status_distribution: [], jobs_by_date_status: [], dataflows_by_date_status: [], failed_jobs: [], dataflow_kpis: {}, status_by_stage: [] },
  failures: { failed_by_stage: [], failed_by_source_connection_type: [], top_failing_dataflows: [], error_categories: [], failure_trend_by_date: [], failed_records: [] },
} as unknown as MonitoringPageResponse;

afterEach(() => vi.restoreAllMocks());

describe("Monitoring query ownership", () => {
  it("keys filter counts by time range only and reuses counts across other slicers", async () => {
    const client = new QueryClient();
    const response = { options: {}, summary: { source: "duckdb_filter_values", fields: 0 }, errors: [] };
    const request = vi.spyOn(api, "getMonitoringFilterOptions").mockResolvedValue(response);
    const initial = monitoringFilterOptionsOptions(7, DEFAULT_MONITORING_FILTERS);
    const other = monitoringFilterOptionsOptions(7, { ...DEFAULT_MONITORING_FILTERS, operationType: "maintenance", status: "failed", stage: "load", grain: "hour", search: "orders", startTime: "ignored" });
    expect(initial.queryKey).toEqual(other.queryKey);
    await client.fetchQuery(initial);
    await client.fetchQuery(other);
    expect(request).toHaveBeenCalledTimes(1);
    expect(request).toHaveBeenCalledWith(7, { range: "30d" }, expect.any(AbortSignal));
    const recent = monitoringFilterOptionsOptions(7, { ...DEFAULT_MONITORING_FILTERS, range: "24h" });
    expect(recent.queryKey).not.toEqual(initial.queryKey);
    expect(recent.placeholderData).toBeUndefined();
    expect(recent.staleTime).toBe(60_000);
    expect(recent.refetchInterval).toBe(60_000);
    await client.fetchQuery(recent);
    expect(request).toHaveBeenCalledTimes(2);
    await client.invalidateQueries({ queryKey: environmentQueryKeys.monitoring(7) });
    await client.fetchQuery(recent);
    expect(request).toHaveBeenCalledTimes(3);
  });

  it("includes custom bounds and Environment in filter keys with stable all-time freshness", () => {
    const custom = { ...DEFAULT_MONITORING_FILTERS, range: "custom" as const, startTime: "2026-10-01T00:00:00Z", endTime: "2026-10-02T00:00:00Z" };
    const key = monitoringFilterOptionsOptions(7, custom).queryKey;
    expect(key).not.toEqual(monitoringFilterOptionsOptions(8, custom).queryKey);
    expect(key).not.toEqual(monitoringFilterOptionsOptions(7, { ...custom, endTime: "2026-10-03T00:00:00Z" }).queryKey);
    const all = monitoringFilterOptionsOptions(7, { ...custom, range: "all" });
    expect(all.queryKey.at(-1)).toEqual({ range: "all" });
    expect(all.staleTime).toBe(Number.POSITIVE_INFINITY);
    expect(all.refetchInterval).toBe(false);
  });

  it("normalizes a compact page without restoring removed metric definitions", () => {
    const report = normalizeMonitoringPage(overviewResponse);
    expect(report.health.status).toBe("healthy");
    expect(report.volume.top_dataflows_by_rows_written).toEqual([]);
    expect(report.freshness.age_by_dataflow).toEqual([]);
    expect("metric_definitions" in report).toBe(false);
  });

  it("preserves exact log-admission counts and exclusion locations for the shared warning", () => {
    const report = normalizeMonitoringPage({
      ...overviewResponse,
      log_admission: {
        admitted: "9007199254740993",
        excluded: "2",
        unsupported_only: false,
        files: [{ source_id: 7, uri: "old.jsonl", admitted: "1", excluded: "2", versions: { "5": "2" }, locations: [{ line: 9, version: "5" }] }]
      }
    } as unknown as MonitoringPageResponse);

    expect(report.log_admission).toEqual({
      admitted: "9007199254740993",
      excluded: "2",
      unsupported_only: false,
      files: [{ source_id: 7, uri: "old.jsonl", admitted: "1", excluded: "2", versions: { "5": "2" }, locations: [{ line: 9, version: "5" }] }]
    });
  });

  it("separates Environment, page, filter, and paging identities", () => {
    const overview = monitoringReportOptions(7, "overview", DEFAULT_MONITORING_FILTERS).queryKey;
    const jobs = monitoringReportOptions(7, "jobs", DEFAULT_MONITORING_FILTERS).queryKey;
    const anotherEnvironment = monitoringReportOptions(8, "overview", DEFAULT_MONITORING_FILTERS).queryKey;
    const pageOne = monitoringDataflowsOptions(7, DEFAULT_MONITORING_FILTERS, { limit: 100, offset: 0, sort: { sortBy: "start_time", sortDir: "desc" } }).queryKey;
    const pageTwo = monitoringDataflowsOptions(7, DEFAULT_MONITORING_FILTERS, { limit: 100, offset: 100, sort: { sortBy: "start_time", sortDir: "desc" } }).queryKey;
    const freshnessRegistry = monitoringEvidenceOptions(7, "freshness", DEFAULT_MONITORING_FILTERS, { limit: 100, offset: 0, sort: { sortBy: "latest_freshness_at", sortDir: "desc" } }, "registry");
    expect(overview).not.toEqual(jobs);
    expect(overview).not.toEqual(anotherEnvironment);
    expect(pageOne).not.toEqual(pageTwo);
    expect(freshnessRegistry.queryKey[5]).toMatchObject({ view: "registry" });
  });

  it("refreshes time-dependent Freshness metrics without polling static pages", () => {
    const freshness = monitoringReportOptions(7, "freshness", DEFAULT_MONITORING_FILTERS);
    const volume = monitoringReportOptions(7, "volume", DEFAULT_MONITORING_FILTERS);

    expect(freshness.staleTime).toBe(60_000);
    expect(freshness.refetchInterval).toBe(60_000);
    expect(volume.staleTime).toBe(Number.POSITIVE_INFINITY);
    expect(volume.refetchInterval).toBe(false);
  });

  it("uses one page-independent query identity for the shared dataflow registry", () => {
    const options = monitoringDataflowRegistryOptions(7, DEFAULT_MONITORING_FILTERS, {
      limit: 100,
      offset: 0,
      sort: { sortBy: "latest_run_at", sortDir: "desc" },
    });
    expect(options.queryKey).toContain("dataflow-registry");
    expect(options.queryKey.join(" ")).not.toContain("freshness");
    expect(options.queryKey.join(" ")).not.toContain("performance");
  });

  it("resolves a shared registry drawer by exact dataflow identity", async () => {
    const response = {
      records: [{ dataflow_id: "df-42", latest_run_id: "run-9" }],
      errors: [],
      summary: { records: 1, total_records: 1, limit: 1, offset: 0 },
      section: "overview",
    } as MonitoringRegistryResponse;
    const request = vi.spyOn(api, "getMonitoringDataflowRegistryDetail").mockResolvedValue(response);
    const options = monitoringDataflowRegistryOptions(
      7,
      DEFAULT_MONITORING_FILTERS,
      { limit: 1, offset: 0, sort: { sortBy: "latest_run_at", sortDir: "desc" } },
      " df-42 ",
    );

    const result = await new QueryClient().fetchQuery(options);

    expect(result).toEqual(response);
    expect(request).toHaveBeenCalledWith(7, " df-42 ", expect.objectContaining({
      range: "30d",
      limit: 1,
      offset: 0,
      sortBy: "latest_run_at",
      sortDir: "desc",
    }), expect.any(AbortSignal));
    expect(options.queryKey.join(" ")).toContain("dataflow-registry");
  });

  it("loads one canonical dataflow run by exact run identity across all time", async () => {
    const records = {
      records: [{ dataflow_run_id: "run-42", source_table: "raw.orders" }],
      errors: [],
      summary: { records: 1, total_records: 1, limit: 1, offset: 0 },
    } as MonitoringRecordsResponse<MonitoringRecord>;
    const request = vi.spyOn(api, "getMonitoringDataflows").mockResolvedValue(records);

    const result = await new QueryClient().fetchQuery(monitoringDataflowRunDetailOptions(7, " run-42 ", "SESSION-A", 2));

    expect(result).toEqual(records.records[0]);
    expect(request).toHaveBeenCalledWith(7, {
      range: "all",
      investigateKind: "dataflow_run_id",
      investigateValue: " run-42 ",
      log_session_scope: "exact",
      log_session_id: "SESSION-A",
      investigateSourceId: 2,
      limit: 1,
      offset: 0,
    }, expect.any(AbortSignal));
  });

  it("retains registry generation and physical provenance in the exact-run query identity", async () => {
    const reference: MonitoringRegistryRunReference & { analyticsGeneration: string; logSessionScope: "exact" } = {
      dataflow_id: "df-42",
      dataflow_run_id: "run-42",
      _source_id: 2,
      log_session_id: "SESSION-A",
      _file_uri: "logs/2.jsonl",
      _record_line: 11,
      analyticsGeneration: "generation-4",
      logSessionScope: "exact",
    };
    const response = {
      records: [{ dataflow_id: "df-42", dataflow_run_id: "run-42", _source_id: 2 }],
      errors: [],
      summary: { records: 1, total_records: 1, limit: 1, offset: 0, analytics_generation: "generation-4" },
    } as MonitoringRecordsResponse<MonitoringRecord>;
    const request = vi.spyOn(api, "getMonitoringDataflows").mockResolvedValue(response);
    const options = monitoringDataflowRunDetailOptions(7, "run-42", undefined, undefined, undefined, reference);
    const result = await new QueryClient().fetchQuery(options);
    expect(result).toMatchObject({ row: response.records[0], summary: { analytics_generation: "generation-4" } });
    expect(JSON.stringify(options.queryKey)).toContain("generation-4");
    expect(request).toHaveBeenCalledWith(7, expect.objectContaining({
      investigateValue: "run-42",
      log_session_scope: "exact",
      log_session_id: "SESSION-A",
      investigateSourceId: 2,
    }), expect.any(AbortSignal));
  });

  it("loads one canonical job run by exact job identity across all time", async () => {
    const records = {
      records: [{ job_id: "job-42", engine_name: "duckdb" }],
      errors: [],
      summary: { records: 1, total_records: 1, limit: 1, offset: 0 },
    } as MonitoringRecordsResponse<JobRecord>;
    const request = vi.spyOn(api, "getMonitoringJobs").mockResolvedValue(records);

    expect(monitoringJobRunDetailOptions(7, " job-42 ", "SESSION-A", 1).queryKey)
      .not.toEqual(monitoringJobRunDetailOptions(7, " job-42 ", "SESSION-A", 2).queryKey);
    expect(monitoringDataflowRunDetailOptions(7, " run-42 ", "SESSION-A", 1).queryKey)
      .not.toEqual(monitoringDataflowRunDetailOptions(7, " run-42 ", "SESSION-A", 2).queryKey);
    const result = await new QueryClient().fetchQuery(monitoringJobRunDetailOptions(7, " job-42 ", "SESSION-A", 2));

    expect(result).toEqual(records.records[0]);
    expect(request).toHaveBeenCalledWith(7, {
      range: "all",
      investigateKind: "job_id",
      investigateValue: " job-42 ",
      log_session_scope: "exact",
      log_session_id: "SESSION-A",
      investigateSourceId: 2,
      limit: 1,
      offset: 0,
    }, expect.any(AbortSignal));
  });

  it("resolves a Freshness aggregate click to the exact source/dataflow registry entity", async () => {
    const response = {
      records: [{ _source_id: 4, dataflow_id: "df-42", adjustment_state: "adjusted" }],
      errors: [],
      summary: { records: 1, total_records: 1, limit: 1, offset: 0 },
    } as MonitoringRecordsResponse<MonitoringRecord>;
    const request = vi.spyOn(api, "getMonitoringPageEvidence").mockResolvedValue(response as never);
    const options = monitoringFreshnessEntityOptions(7, DEFAULT_MONITORING_FILTERS, { sourceId: 4, dataflowId: " df-42 " });
    expect(options.staleTime).toBe(0);

    const result = await new QueryClient().fetchQuery(options);

    expect(result).toEqual(response);
    expect(request).toHaveBeenCalledWith(7, "freshness", {
      range: "30d",
      grain: "auto",
      startTime: undefined,
      endTime: undefined,
      status: "all",
      stage: "all",
      connection: "all",
      engine: "all",
      provider: "all",
      sourceType: "all",
      destinationType: "all",
      loadType: "all",
      operationType: "all",
      search: "",
      investigateKind: "dataflow_id",
      investigateValue: " df-42 ",
      log_session_scope: "all",
      investigateSourceId: 4,
      limit: 1,
      offset: 0,
      sortBy: "latest_freshness_at",
      sortDir: "desc",
      view: "detail",
    }, expect.any(AbortSignal));
  });

  it("reuses a fresh page and refetches runtime resources after operations invalidation", async () => {
    const client = new QueryClient();
    const reportRequest = vi.spyOn(api, "getMonitoringPage").mockResolvedValue(overviewResponse);
    const records = { records: [], errors: [], summary: { records: 0, total_records: 0, limit: 100, offset: 0 } } as MonitoringRecordsResponse<MonitoringRecord>;
    const dataflowRequest = vi.spyOn(api, "getMonitoringDataflows").mockResolvedValue(records);
    const reportOptions = monitoringReportOptions(7, "overview", DEFAULT_MONITORING_FILTERS);
    const dataflowOptions = monitoringDataflowsOptions(7, DEFAULT_MONITORING_FILTERS, { limit: 100, offset: 0, sort: { sortBy: "start_time", sortDir: "desc" } });
    await client.fetchQuery(reportOptions);
    await client.fetchQuery(reportOptions);
    await client.fetchQuery(dataflowOptions);
    await client.fetchQuery(dataflowOptions);
    expect(reportRequest).toHaveBeenCalledTimes(1);
    expect(dataflowRequest).toHaveBeenCalledTimes(1);
    await client.invalidateQueries({ queryKey: environmentQueryKeys.monitoring(7) });
    await client.fetchQuery(reportOptions);
    await client.fetchQuery(dataflowOptions);
    expect(reportRequest).toHaveBeenCalledTimes(2);
    expect(dataflowRequest).toHaveBeenCalledTimes(2);
  });

  it("retains exact System Log scopes and computes bounded next offsets", async () => {
    const client = new QueryClient();
    const response = { records: [], total: 0, files: [], errors: [] } as SystemLogResponse;
    const request = vi.spyOn(api, "getMonitoringSystemLogs").mockResolvedValue(response);
    const options = monitoringSystemLogsOptions(7, { jobId: "job-1", dataflowId: "df-1", dataflowRunId: "run-1", eventName: "dataflow.started", includeDataflowLogs: false, level: "ERROR", query: "timeout" });
    await client.fetchInfiniteQuery(options);
    await client.fetchInfiniteQuery(options);
    expect(request).toHaveBeenCalledTimes(1);
    expect(request).toHaveBeenCalledWith(7, expect.objectContaining({
      job_id: "job-1",
      dataflow_id: "df-1",
      dataflow_run_id: "run-1",
      event_name: "dataflow.started",
    }), expect.any(AbortSignal));
    expect(nextSystemLogOffset(
      { records: Array.from({ length: SYSTEM_LOG_PAGE_SIZE }), total: SYSTEM_LOG_PAGE_SIZE + 1 },
      [{ records: Array.from({ length: SYSTEM_LOG_PAGE_SIZE }) }],
    )).toBe(SYSTEM_LOG_PAGE_SIZE);
    expect(nextSystemLogOffset({ records: [], total: SYSTEM_LOG_PAGE_SIZE }, [{ records: [] }])).toBeUndefined();
  });

  it("discriminates all, NULL, and exact empty session scopes in params and cache keys", () => {
    const all = monitoringQueryParams({ ...DEFAULT_MONITORING_FILTERS, investigateLogSessionScope: "all", investigateLogSessionId: null });
    const nullSession = monitoringQueryParams({ ...DEFAULT_MONITORING_FILTERS, investigateLogSessionScope: "null", investigateLogSessionId: null });
    const exactEmpty = monitoringQueryParams({ ...DEFAULT_MONITORING_FILTERS, investigateLogSessionScope: "exact", investigateLogSessionId: "" });
    expect(all).toMatchObject({ log_session_scope: "all" });
    expect(all).not.toHaveProperty("log_session_id");
    expect(nullSession).toMatchObject({ log_session_scope: "null" });
    expect(nullSession).not.toHaveProperty("log_session_id");
    expect(exactEmpty).toMatchObject({ log_session_scope: "exact", log_session_id: "" });
    expect(systemLogScopeParams(undefined, false, undefined, undefined, undefined, "", "exact"))
      .toEqual({ log_session_scope: "exact", log_session_id: "" });
    expect(monitoringReportOptions(7, "overview", { ...DEFAULT_MONITORING_FILTERS, investigateLogSessionScope: "all" }).queryKey)
      .not.toEqual(monitoringReportOptions(7, "overview", { ...DEFAULT_MONITORING_FILTERS, investigateLogSessionScope: "exact", investigateLogSessionId: "" }).queryKey);
  });
});
