import { QueryClient } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../../shared/api/client";
import type { JobRecord, MonitoringPageResponse, MonitoringRecordsResponse, MonitoringRecord, SystemLogResponse } from "../../shared/api/domainTypes";
import { environmentQueryKeys } from "../environments/environmentQueries";
import { DEFAULT_MONITORING_FILTERS } from "./monitoringFilters";
import {
  monitoringDataflowsOptions,
  monitoringDataflowRunDetailOptions,
  monitoringJobRunDetailOptions,
  monitoringReportOptions,
  monitoringSystemLogsOptions,
  nextSystemLogOffset,
  normalizeMonitoringPage,
  SYSTEM_LOG_PAGE_SIZE,
} from "./monitoringQueries";

const summary = {
  dataflow_records: 0,
  job_records: 0,
  date_range: {},
  active_engines: 0,
  active_metadata_providers: 0,
  log_paths: 0,
};
const overviewResponse = {
  schema_version: "monitoring-page.v9",
  page: "overview",
  summary,
  health: { status: "healthy", label: "Healthy", reasons: [] },
  attention: [],
  operations: { kpis: {}, job_status_distribution: [], jobs_by_date_status: [], dataflows_by_date_status: [], failed_jobs: [], dataflow_kpis: {}, status_by_stage: [] },
  failures: { failed_by_stage: [], failed_by_source_connection_type: [], top_failing_dataflows: [], error_categories: [], failure_trend_by_date: [], failed_records: [] },
} as MonitoringPageResponse;

afterEach(() => vi.restoreAllMocks());

describe("Monitoring query ownership", () => {
  it("normalizes a compact page without restoring transferred metric definitions", () => {
    const report = normalizeMonitoringPage(overviewResponse);
    expect(report.health.status).toBe("healthy");
    expect(report.volume.top_dataflows_by_rows_written).toEqual([]);
    expect(report.freshness.age_by_dataflow).toEqual([]);
    expect(report.metric_definitions).toEqual({});
  });

  it("separates Environment, page, filter, and paging identities", () => {
    const overview = monitoringReportOptions(7, "overview", DEFAULT_MONITORING_FILTERS).queryKey;
    const jobs = monitoringReportOptions(7, "jobs", DEFAULT_MONITORING_FILTERS).queryKey;
    const anotherEnvironment = monitoringReportOptions(8, "overview", DEFAULT_MONITORING_FILTERS).queryKey;
    const pageOne = monitoringDataflowsOptions(7, DEFAULT_MONITORING_FILTERS, { limit: 100, offset: 0, sort: { sortBy: "start_time", sortDir: "desc" } }).queryKey;
    const pageTwo = monitoringDataflowsOptions(7, DEFAULT_MONITORING_FILTERS, { limit: 100, offset: 100, sort: { sortBy: "start_time", sortDir: "desc" } }).queryKey;
    expect(overview).not.toEqual(jobs);
    expect(overview).not.toEqual(anotherEnvironment);
    expect(pageOne).not.toEqual(pageTwo);
  });

  it("refreshes time-dependent Freshness metrics without polling static pages", () => {
    const freshness = monitoringReportOptions(7, "freshness", DEFAULT_MONITORING_FILTERS);
    const volume = monitoringReportOptions(7, "volume", DEFAULT_MONITORING_FILTERS);

    expect(freshness.staleTime).toBe(60_000);
    expect(freshness.refetchInterval).toBe(60_000);
    expect(volume.staleTime).toBe(Number.POSITIVE_INFINITY);
    expect(volume.refetchInterval).toBe(false);
  });

  it("loads one canonical dataflow run by exact run identity across all time", async () => {
    const records = {
      records: [{ dataflow_run_id: "run-42", source_table: "raw.orders" }],
      errors: [],
      summary: { records: 1, total_records: 1, limit: 1, offset: 0 },
    } as MonitoringRecordsResponse<MonitoringRecord>;
    const request = vi.spyOn(api, "getMonitoringDataflows").mockResolvedValue(records);

    const result = await new QueryClient().fetchQuery(monitoringDataflowRunDetailOptions(7, " run-42 "));

    expect(result).toEqual(records.records[0]);
    expect(request).toHaveBeenCalledWith(7, {
      range: "all",
      investigateKind: "dataflow_run_id",
      investigateValue: "run-42",
      limit: 1,
      offset: 0,
    });
  });

  it("loads one canonical job run by exact job identity across all time", async () => {
    const records = {
      records: [{ job_id: "job-42", engine_name: "duckdb" }],
      errors: [],
      summary: { records: 1, total_records: 1, limit: 1, offset: 0 },
    } as MonitoringRecordsResponse<JobRecord>;
    const request = vi.spyOn(api, "getMonitoringJobs").mockResolvedValue(records);

    const result = await new QueryClient().fetchQuery(monitoringJobRunDetailOptions(7, " job-42 "));

    expect(result).toEqual(records.records[0]);
    expect(request).toHaveBeenCalledWith(7, {
      range: "all",
      investigateKind: "job_id",
      investigateValue: "job-42",
      limit: 1,
      offset: 0,
    });
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
    const options = monitoringSystemLogsOptions(7, { jobId: "job-1", dataflowId: "df-1", includeDataflowLogs: false, level: "ERROR", query: "timeout" });
    await client.fetchInfiniteQuery(options);
    await client.fetchInfiniteQuery(options);
    expect(request).toHaveBeenCalledTimes(1);
    expect(nextSystemLogOffset(
      { records: Array.from({ length: SYSTEM_LOG_PAGE_SIZE }), total: SYSTEM_LOG_PAGE_SIZE + 1 },
      [{ records: Array.from({ length: SYSTEM_LOG_PAGE_SIZE }) }],
    )).toBe(SYSTEM_LOG_PAGE_SIZE);
    expect(nextSystemLogOffset({ records: [], total: SYSTEM_LOG_PAGE_SIZE }, [{ records: [] }])).toBeUndefined();
  });
});
