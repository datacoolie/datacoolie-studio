import type { JobRecord, MonitoringRecord, MonitoringReport } from "../../shared/api/types";
import type { MonitoringPageKey } from "../../app/moduleRegistry";

export type MonitoringRange = "24h" | "3d" | "7d" | "30d" | "90d" | "custom" | "all";
export type MonitoringGrain = "auto" | "hour" | "day" | "week" | "month";

export type MonitoringTabKey = MonitoringPageKey;

export interface MonitoringFilters {
  range: MonitoringRange;
  grain: MonitoringGrain;
  startTime: string;
  endTime: string;
  status: string;
  stage: string;
  connection: string;
  engine: string;
  provider: string;
  sourceType: string;
  destinationType: string;
  loadType: string;
  operationType: string;
  search: string;
  investigateKind: string;
  investigateValue: string;
}

export interface FilterOption {
  value: string;
  label: string;
  count?: number;
}

export interface FilteredMonitoringReport {
  jobs: JobRecord[];
  dataflows: MonitoringRecord[];
  failedRecords: MonitoringRecord[];
  slowestDataflows: MonitoringRecord[];
  performanceInvestigationQueue: MonitoringRecord[];
  volumeInvestigationQueue: MonitoringRecord[];
  volumeDataflowRegistry: MonitoringRecord[];
}

export const DEFAULT_MONITORING_FILTERS: MonitoringFilters = {
  range: "30d",
  grain: "auto",
  startTime: "",
  endTime: "",
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
  investigateKind: "",
  investigateValue: ""
};

const FILTER_KEYS = Object.keys(DEFAULT_MONITORING_FILTERS) as Array<keyof MonitoringFilters>;
const STATUS_OPTIONS = ["pending", "running", "succeeded", "failed", "skipped"];

export function filtersFromSearch(search: string): MonitoringFilters {
  const params = new URLSearchParams(search);
  const filters = { ...DEFAULT_MONITORING_FILTERS };
  for (const key of FILTER_KEYS) {
    const value = params.get(key);
    if (value !== null) {
      if (key === "range") {
        filters.range = isMonitoringRange(value) ? value : DEFAULT_MONITORING_FILTERS.range;
      } else if (key === "grain") {
        filters.grain = isMonitoringGrain(value) ? value : DEFAULT_MONITORING_FILTERS.grain;
      } else {
        filters[key] = value as never;
      }
    }
  }
  return filters;
}

export function writeFiltersToSearch(filters: MonitoringFilters) {
  const url = new URL(window.location.href);
  for (const key of FILTER_KEYS) {
    const value = filters[key];
    if ((key === "startTime" || key === "endTime") && filters.range !== "custom") {
      url.searchParams.delete(key);
    } else if (value && value !== DEFAULT_MONITORING_FILTERS[key]) {
      url.searchParams.set(key, value);
    } else {
      url.searchParams.delete(key);
    }
  }
  window.history.replaceState(null, "", `${url.pathname}${url.search}${url.hash}`);
}

export function hasActiveFilters(filters: MonitoringFilters) {
  return FILTER_KEYS.some((key) => {
    if ((key === "startTime" || key === "endTime") && filters.range !== "custom") return false;
    return filters[key] !== DEFAULT_MONITORING_FILTERS[key];
  });
}

export function filterReport(report: MonitoringReport, filters: MonitoringFilters): FilteredMonitoringReport {
  const reportTimezone = String(report.summary.timezone ?? "UTC");
  const dataflows = [
    ...report.failures.failed_records,
    ...report.performance.slowest_dataflows,
    ...(report.performance.investigation_queue ?? []),
    ...(report.volume.investigation_queue ?? [])
  ].filter((row, index, rows) => uniqueDataflowRun(row, index, rows)).filter((row) => matchesDataflow(row, filters, reportTimezone));
  const relatedJobIds = new Set(dataflows.map((row) => String(row.job_id ?? "")).filter(Boolean));
  const jobs = report.operations.failed_jobs
    .filter((row) => matchesJob(row, filters, reportTimezone))
    .filter((row) => !hasDataflowScopedFilter(filters) || relatedJobIds.has(String(row.job_id ?? "")));

  return {
    jobs,
    dataflows,
    failedRecords: report.failures.failed_records.filter((row) => matchesDataflow(row, filters, reportTimezone)),
    slowestDataflows: report.performance.slowest_dataflows.filter((row) => matchesDataflow(row, filters, reportTimezone)),
    performanceInvestigationQueue: (report.performance.investigation_queue ?? []).filter((row) => matchesDataflow(row, filters, reportTimezone)),
    volumeInvestigationQueue: (report.volume.investigation_queue ?? []).filter((row) => matchesDataflow(row, filters, reportTimezone)),
    volumeDataflowRegistry: (report.volume.dataflow_registry ?? []).filter((row) => matchesDataflow(row, filters, reportTimezone))
  };
}

function hasDataflowScopedFilter(filters: MonitoringFilters) {
  return (
    splitFilterValues(filters.connection).length > 0 ||
    Boolean(filters.investigateKind && filters.investigateValue && filters.investigateKind !== "job_id")
  );
}

export function monitoringFilterOptions(report: MonitoringReport) {
  const dataflows = [
    ...report.failures.failed_records,
    ...report.performance.slowest_dataflows,
    ...(report.performance.investigation_queue ?? []),
    ...(report.volume.investigation_queue ?? [])
  ];
  const jobs = report.operations.failed_jobs;
  return {
    status: optionsFrom([...STATUS_OPTIONS, ...values(dataflows, "status"), ...values(jobs, "status")]),
    stage: optionsFrom(values(dataflows, "stage")),
    connection: optionsFrom([...values(dataflows, "source_name"), ...values(dataflows, "destination_name")]),
    engine: optionsFrom([...values(dataflows, "engine_name"), ...values(jobs, "engine_name")]),
    provider: optionsFrom(values(jobs, "metadata_provider_name")),
    sourceType: optionsFrom(values(dataflows, "source_connection_type")),
    destinationType: optionsFrom(values(dataflows, "destination_connection_type")),
    loadType: optionsFrom(values(dataflows, "destination_load_type")),
    operationType: optionsFrom(values(dataflows, "operation_type"))
  };
}

function matchesJob(row: JobRecord, filters: MonitoringFilters, timezoneName: string) {
  return matchesCommon(row, filters, timezoneName) && matchesSearch(row, filters.search) && matchesInvestigation(row, filters, false);
}

function matchesDataflow(row: MonitoringRecord, filters: MonitoringFilters, timezoneName: string) {
  return (
    matchesCommon(row, filters, timezoneName) &&
    matchesValue(row.stage, filters.stage) &&
    matchesConnection(row, filters.connection) &&
    matchesValue(row.source_connection_type, filters.sourceType) &&
    matchesValue(row.destination_connection_type, filters.destinationType) &&
    matchesValue(row.destination_load_type, filters.loadType) &&
    matchesValue(row.operation_type, filters.operationType) &&
    matchesSearch(row, filters.search) &&
    matchesInvestigation(row, filters, true)
  );
}

function matchesCommon(row: Record<string, unknown>, filters: MonitoringFilters, timezoneName: string) {
  return (
    matchesRange(row, filters, timezoneName) &&
    matchesValue(row.status, filters.status) &&
    matchesValue(row.engine_name, filters.engine) &&
    matchesValue(row.metadata_provider_name, filters.provider)
  );
}

function matchesRange(row: Record<string, unknown>, filters: MonitoringFilters, timezoneName: string) {
  const range = filters.range;
  if (range === "all") return true;
  const value = String(row.end_time ?? row.start_time ?? "");
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) return false;
  if (range === "custom") {
    const start = filters.startTime ? Date.parse(filters.startTime) : Number.NEGATIVE_INFINITY;
    const end = filters.endTime ? Date.parse(filters.endTime) : Number.POSITIVE_INFINITY;
    return timestamp >= start && timestamp <= end;
  }
  const days = range === "24h" ? 1 : range === "3d" ? 3 : range === "7d" ? 7 : range === "90d" ? 90 : 30;
  return Date.now() - timestamp <= days * 86400 * 1000;
}

function isMonitoringRange(value: string): value is MonitoringRange {
  return value === "24h" || value === "3d" || value === "7d" || value === "30d" || value === "90d" || value === "custom" || value === "all";
}

function isMonitoringGrain(value: string): value is MonitoringGrain {
  return value === "auto" || value === "hour" || value === "day" || value === "week" || value === "month";
}

function matchesValue(value: unknown, filterValue: string) {
  const selected = splitFilterValues(filterValue);
  return selected.length === 0 || selected.includes(String(value ?? "unknown"));
}

function matchesConnection(row: MonitoringRecord, filterValue: string) {
  const selected = splitFilterValues(filterValue);
  if (selected.length === 0) return true;
  return selected.includes(String(row.source_name ?? "unknown")) || selected.includes(String(row.destination_name ?? "unknown"));
}

function splitFilterValues(filterValue: string | undefined) {
  if (!filterValue || filterValue === "all") return [];
  return filterValue.split("|").map((value) => value.trim()).filter(Boolean);
}

function matchesSearch(row: Record<string, unknown>, search: string) {
  const query = normalizeInvestigationValue(search);
  if (!query) return true;
  return Object.values(row).some((value) => normalizeInvestigationValue(value).includes(query));
}

function matchesInvestigation(row: Record<string, unknown>, filters: MonitoringFilters, isDataflow: boolean) {
  const kind = filters.investigateKind;
  const value = normalizeInvestigationValue(filters.investigateValue);
  if (!kind || !value) return true;
  if (kind === "job_id") return normalizeInvestigationValue(row.job_id) === value;
  if (!isDataflow) return true;
  if (kind === "dataflow_run_id") return normalizeInvestigationValue(row.dataflow_run_id) === value;
  if (kind === "dataflow") {
    return normalizeInvestigationValue(row.dataflow_id) === value || normalizeInvestigationValue(row.dataflow_name) === value;
  }
  if (kind === "destination_table") {
    const connection = normalizeInvestigationValue(row.destination_name ?? row.destination_connection_name ?? "unknown");
    const fullTable = normalizeInvestigationValue(row.destination_full_table);
    const table = normalizeInvestigationValue(row.destination_table);
    const path = normalizeInvestigationValue(row.destination_path);
    return [
      row.target,
      row.target_display,
      fullTable,
      table,
      path,
      connection && fullTable ? `${connection}::${fullTable}` : "",
      connection && table ? `${connection}::${table}` : "",
      connection && path ? `${connection}::${path}` : "",
    ].some((candidate) => normalizeInvestigationValue(candidate) === value);
  }
  return true;
}

function normalizeInvestigationValue(value: unknown) {
  return String(value ?? "").trim().replace(/`/g, "").toLowerCase();
}

function uniqueDataflowRun(row: MonitoringRecord, index: number, rows: MonitoringRecord[]) {
  const key = row.dataflow_run_id ?? `${row.job_id ?? ""}:${row.dataflow_id ?? ""}:${row.stage ?? ""}:${row.end_time ?? ""}`;
  return rows.findIndex((candidate) => {
    const candidateKey = candidate.dataflow_run_id ?? `${candidate.job_id ?? ""}:${candidate.dataflow_id ?? ""}:${candidate.stage ?? ""}:${candidate.end_time ?? ""}`;
    return candidateKey === key;
  }) === index;
}

function values(rows: Array<Record<string, unknown>>, key: string) {
  return rows.map((row) => row[key]).filter((value) => value !== null && value !== undefined && value !== "");
}

function optionsFrom(values: unknown[]): FilterOption[] {
  const unique = Array.from(new Set(values.map((value) => String(value)))).sort((a, b) => a.localeCompare(b));
  return [{ value: "all", label: "All" }, ...unique.map((value) => ({ value, label: value }))];
}

function timezoneDateKey(timestamp: number, timezoneName: string): string {
  try {
    const formatter = new Intl.DateTimeFormat("en-CA", {
      timeZone: timezoneName,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    });
    const parts = formatter.formatToParts(new Date(timestamp));
    const values = Object.fromEntries(
      parts.filter((part) => part.type !== "literal").map((part) => [part.type, part.value])
    ) as Record<string, string>;
    if (!values.year || !values.month || !values.day) return "";
    return `${values.year}-${values.month}-${values.day}`;
  } catch {
    return "";
  }
}
