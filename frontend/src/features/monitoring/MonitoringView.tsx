import { Activity, AlertTriangle, BarChart3, Boxes, Clock3, FileWarning, Gauge, HardDrive, Table2, Workflow } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../../shared/api/client";
import type {
  JobRecord,
  MonitoringFilterOptionsResponse,
  MonitoringRecord,
  MonitoringRecordsResponse,
  MonitoringReport
} from "../../shared/api/types";
import { EmptyState } from "../../shared/components/EmptyState";
import type { TableSort } from "./MonitoringCharts";
import { MonitoringDetailDrawer, type MonitoringDetailKind } from "./MonitoringDetailDrawer";
import { MonitoringFilterBar, type MonitoringSearchOption } from "./MonitoringFilterBar";
import {
  DataflowsPage,
  DiagnosticsPage,
  FailurePage,
  FreshnessPage,
  JobsPage,
  MaintenancePage,
  MonitoringOverviewPage,
  PerformancePage,
  VolumePage
} from "./MonitoringPages";
import {
  filterReport,
  filtersFromSearch,
  hasActiveFilters,
  type MonitoringTabKey,
  writeFiltersToSearch
} from "./monitoringFilters";

interface MonitoringViewProps {
  environmentId: number;
  report: MonitoringReport | null;
  loading: boolean;
  activePage: MonitoringTabKey;
  onPageChange: (page: MonitoringTabKey) => void;
}

const pages = [
  { key: "overview", label: "Overview", icon: Gauge },
  { key: "jobs", label: "Jobs", icon: Table2 },
  { key: "dataflows", label: "Dataflows", icon: Workflow },
  { key: "failures", label: "Failures", icon: AlertTriangle },
  { key: "freshness", label: "Freshness", icon: Clock3 },
  { key: "performance", label: "Performance", icon: BarChart3 },
  { key: "volume", label: "Volume", icon: Boxes },
  { key: "maintenance", label: "Maintenance", icon: HardDrive },
  { key: "diagnostics", label: "Diagnostics", icon: FileWarning }
] satisfies Array<{ key: MonitoringTabKey; label: string; icon: typeof Gauge }>;

const DEFAULT_JOB_RUN_TABLE_LIMIT = 100;
const DEFAULT_DATAFLOW_RUN_TABLE_LIMIT = 100;
const DETAIL_CHILD_DATAFLOW_LIMIT = 10000;

export function MonitoringView({ environmentId, report, loading, activePage, onPageChange }: MonitoringViewProps) {
  const [filters, setFilters] = useState(() => filtersFromSearch(window.location.search));
  const [reportData, setReportData] = useState<MonitoringReport | null>(report);
  const [filterOptions, setFilterOptions] = useState<MonitoringFilterOptionsResponse | null>(null);
  const [reportLoading, setReportLoading] = useState(false);
  const reportForView = reportData ?? report;
  const filtered = useMemo(() => (reportForView ? filterReport(reportForView, filters) : null), [reportForView, filters]);
  const [jobRuns, setJobRuns] = useState<MonitoringRecordsResponse<JobRecord> | null>(null);
  const [dataflowRuns, setDataflowRuns] = useState<MonitoringRecordsResponse<MonitoringRecord> | null>(null);
  const [runsLoading, setRunsLoading] = useState(false);
  const [runsError, setRunsError] = useState<string | null>(null);
  const [detail, setDetail] = useState<{ kind: MonitoringDetailKind; row: Record<string, unknown> } | null>(null);
  const [detailStack, setDetailStack] = useState<Array<{ kind: MonitoringDetailKind; row: Record<string, unknown> }>>([]);
  const detailRef = useRef(detail);
  const detailStackRef = useRef(detailStack);
  const drawerHistoryDepthRef = useRef(0);
  const suppressNextDrawerPopRef = useRef(false);
  const [jobSort, setJobSort] = useState<TableSort>({ sortBy: "start_time", sortDir: "desc" });
  const [dataflowSort, setDataflowSort] = useState<TableSort>({ sortBy: "start_time", sortDir: "desc" });
  const [jobOffset, setJobOffset] = useState(0);
  const [jobLimit, setJobLimit] = useState(DEFAULT_JOB_RUN_TABLE_LIMIT);
  const [dataflowOffset, setDataflowOffset] = useState(0);
  const [dataflowLimit, setDataflowLimit] = useState(DEFAULT_DATAFLOW_RUN_TABLE_LIMIT);
  const [detailDataflows, setDetailDataflows] = useState<MonitoringRecord[]>([]);
  const hasReportForView = Boolean(reportForView);
  const searchOptions = useMemo(
    () => buildMonitoringSearchOptions(reportForView, jobRuns?.records ?? [], dataflowRuns?.records ?? []),
    [reportForView, jobRuns, dataflowRuns]
  );

  useEffect(() => {
    writeFiltersToSearch(filters);
  }, [filters]);

  useEffect(() => {
    setReportData(report);
  }, [environmentId, report]);

  useEffect(() => {
    setDetail(null);
    setDetailStack([]);
    detailRef.current = null;
    detailStackRef.current = [];
    drawerHistoryDepthRef.current = 0;
  }, [environmentId]);

  useEffect(() => {
    detailRef.current = detail;
    detailStackRef.current = detailStack;
  }, [detail, detailStack]);

  useEffect(() => {
    let cancelled = false;
    setFilterOptions(null);
    api.getMonitoringFilterOptions(environmentId)
      .then((response) => {
        if (!cancelled) setFilterOptions(response);
      })
      .catch(() => {
        if (!cancelled) setFilterOptions(null);
      });
    return () => {
      cancelled = true;
    };
  }, [environmentId]);

  useEffect(() => {
    setJobOffset(0);
    setDataflowOffset(0);
  }, [filters, jobSort, dataflowSort]);

  useEffect(() => {
    let cancelled = false;
    const params = monitoringQueryParams(filters);
    setReportLoading(true);
    api.getMonitoringReport(environmentId, params)
      .then((nextReport) => {
        if (!cancelled) setReportData(nextReport);
      })
      .catch(() => {
        if (!cancelled && report) setReportData(report);
      })
      .finally(() => {
        if (!cancelled) setReportLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [environmentId, filters, report]);

  useEffect(() => {
    if (!hasReportForView) return;
    if (activePage !== "jobs" && activePage !== "dataflows") {
      setRunsLoading(false);
      setRunsError(null);
      return;
    }
    let cancelled = false;
    const params = monitoringQueryParams(filters);
    setRunsLoading(true);
    setRunsError(null);
    const request =
      activePage === "jobs"
        ? api.getMonitoringJobs(environmentId, { ...params, limit: jobLimit, offset: jobOffset, sortBy: jobSort.sortBy, sortDir: jobSort.sortDir })
        : api.getMonitoringDataflows(environmentId, {
            ...params,
            limit: dataflowLimit,
            offset: dataflowOffset,
            sortBy: dataflowSort.sortBy,
            sortDir: dataflowSort.sortDir
          });
    request
      .then((response) => {
        if (cancelled) return;
        if (activePage === "jobs") {
          setJobRuns(response as MonitoringRecordsResponse<JobRecord>);
        } else {
          setDataflowRuns(response as MonitoringRecordsResponse<MonitoringRecord>);
        }
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setRunsError(error instanceof Error ? error.message : String(error));
      })
      .finally(() => {
        if (!cancelled) setRunsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [environmentId, filters, hasReportForView, activePage, jobSort, dataflowSort, jobOffset, jobLimit, dataflowOffset, dataflowLimit]);

  useEffect(() => {
    if (!detail || (detail.kind !== "job" && detail.kind !== "freshness" && detail.kind !== "maintenance")) {
      setDetailDataflows([]);
      return;
    }
    const investigateKind = detail.kind === "job" ? "job_id" : detail.kind === "maintenance" ? "destination_table" : "dataflow";
    const investigateValue =
      detail.kind === "job"
        ? String(detail.row.job_id ?? "").trim()
        : detail.kind === "maintenance"
          ? maintenanceInvestigateValue(detail.row)
          : dataflowInvestigateValue(detail.row);
    if (!investigateValue) {
      setDetailDataflows([]);
      return;
    }
    let cancelled = false;
    const params = monitoringQueryParams(filters);
    const detailParams = detail.kind === "maintenance" ? { ...params, operationType: "all" } : params;
    api.getMonitoringDataflows(environmentId, {
      ...detailParams,
      limit: DETAIL_CHILD_DATAFLOW_LIMIT,
      offset: 0,
      sortBy: "start_time",
      sortDir: "desc",
      investigateKind,
      investigateValue
    })
      .then((response) => {
        if (!cancelled) setDetailDataflows(response.records);
      })
      .catch(() => {
        if (!cancelled) setDetailDataflows([]);
      });
    return () => {
      cancelled = true;
    };
  }, [detail, environmentId, filters]);

  useEffect(() => {
    const popDetailState = () => {
      const stack = detailStackRef.current;
      const previous = stack[stack.length - 1];
      if (previous) {
        const nextStack = stack.slice(0, -1);
        setDetail(previous);
        setDetailStack(nextStack);
        detailRef.current = previous;
        detailStackRef.current = nextStack;
      } else {
        setDetail(null);
        setDetailStack([]);
        detailRef.current = null;
        detailStackRef.current = [];
      }
      drawerHistoryDepthRef.current = Math.max(0, drawerHistoryDepthRef.current - 1);
    };

    const handlePopState = () => {
      if (suppressNextDrawerPopRef.current) {
        suppressNextDrawerPopRef.current = false;
        drawerHistoryDepthRef.current = 0;
        return;
      }
      if (!detailRef.current || drawerHistoryDepthRef.current <= 0) return;
      popDetailState();
    };

    window.addEventListener("popstate", handlePopState);
    return () => window.removeEventListener("popstate", handlePopState);
  }, []);

  if (!reportForView && !loading && !reportLoading) {
    return <EmptyState icon={<Activity size={24} />} title="Add ETL log path to inspect runs" />;
  }

  if (!reportForView) {
    return <EmptyState icon={<Activity size={24} />} title="Loading monitoring report" />;
  }

  const activeFilterCount = hasActiveFilters(filters) ? reportForView.summary.dataflow_records + reportForView.summary.job_records : null;
  const grainWarning = grainAdjustmentMessage(filters, reportForView);
  const pushDrawerHistory = () => {
    window.history.pushState({ datacoolieMonitoringDrawer: true }, "", window.location.href);
    drawerHistoryDepthRef.current += 1;
  };
  const openDetail = (nextDetail: { kind: MonitoringDetailKind; row: Record<string, unknown> }) => {
    pushDrawerHistory();
    setDetail(nextDetail);
    setDetailStack([]);
    detailRef.current = nextDetail;
    detailStackRef.current = [];
  };
  const pushDetail = (nextDetail: { kind: MonitoringDetailKind; row: Record<string, unknown> }) => {
    pushDrawerHistory();
    if (detail) {
      const nextStack = [...detailStackRef.current, detail];
      setDetailStack(nextStack);
      detailStackRef.current = nextStack;
    }
    setDetail(nextDetail);
    detailRef.current = nextDetail;
  };
  const popDetail = () => {
    if (drawerHistoryDepthRef.current > 0) {
      window.history.back();
      return;
    }
    setDetailStack((stack) => {
      const previous = stack[stack.length - 1];
      if (previous) setDetail(previous);
      return stack.slice(0, -1);
    });
  };
  const closeDetail = () => {
    setDetail(null);
    setDetailStack([]);
    detailRef.current = null;
    detailStackRef.current = [];
    if (drawerHistoryDepthRef.current > 0) {
      suppressNextDrawerPopRef.current = true;
      window.history.go(-drawerHistoryDepthRef.current);
      drawerHistoryDepthRef.current = 0;
    }
  };
  const resolveJobDetailRow = (row: Record<string, unknown>) => {
    const jobId = String(row.job_id ?? "").trim();
    if (!jobId) return row;
    const knownJobRows = [
      ...[...detailStack].reverse().filter((item) => item.kind === "job").map((item) => item.row),
      ...(detail?.kind === "job" ? [detail.row] : []),
      ...(jobRuns?.records ?? []),
      ...(filtered?.jobs ?? []),
      ...reportForView.operations.failed_jobs
    ];
    return knownJobRows.find((candidate) => String(candidate.job_id ?? "") === jobId) ?? row;
  };
  const openLinkedJobDetail = (row: Record<string, unknown>) => {
    pushDetail({ kind: "job", row: resolveJobDetailRow(row) });
  };

  return (
    <div className="view-stack monitoring-view-stack">
      <div className="monitoring-command-sticky">
        <section className="monitoring-command-panel">
          <div className="monitoring-header">
            <div>
              <h2>Monitoring</h2>
              <span>
                {reportForView.summary.job_records} jobs, {reportForView.summary.dataflow_records} dataflows,{" "}
                {reportForView.summary.date_range.min || "-"} to {reportForView.summary.date_range.max || "-"}
                {reportForView.summary.effective_grain ? ` · ${reportForView.summary.effective_grain} grain` : ""}
                {activeFilterCount !== null ? ` · ${activeFilterCount} filtered records` : ""}
                {reportLoading ? " · refreshing" : ""}
              </span>
            </div>
            <nav className="monitoring-page-tabs" aria-label="Monitoring pages">
              {pages.map((page) => {
                const Icon = page.icon;
                return (
                  <button
                    key={page.key}
                    className={activePage === page.key ? "active" : ""}
                    onClick={() => onPageChange(page.key)}
                    title={page.label}
                  >
                    <Icon size={16} />
                    <span>{page.label}</span>
                  </button>
                );
              })}
            </nav>
          </div>
          <MonitoringFilterBar
            environmentId={environmentId}
            options={filterOptions}
            filters={filters}
            searchOptions={searchOptions}
            grainWarning={grainWarning}
            onChange={setFilters}
          />
        </section>
      </div>

      {activePage === "overview" && filtered ? (
        <MonitoringOverviewPage report={reportForView} filters={filters} onNavigate={onPageChange} />
      ) : null}
      {runsError ? <div className="app-error">{runsError}</div> : null}
      {activePage === "jobs" && filtered ? (
        <JobsPage
          report={reportForView}
          filters={filters}
          rows={jobRuns?.records ?? filtered.jobs}
          totalRows={jobRuns?.summary.total_records ?? filtered.jobs.length}
          loading={runsLoading}
          filtered={hasActiveFilters(filters)}
          sort={jobSort}
          onSort={setJobSort}
          limit={jobLimit}
          offset={jobRuns?.summary.offset ?? jobOffset}
          onPageChange={setJobOffset}
          onPageSizeChange={(nextLimit) => {
            setJobLimit(nextLimit);
            setJobOffset(0);
          }}
          onInspect={(row) => openDetail({ kind: "job", row })}
        />
      ) : null}
      {activePage === "dataflows" && filtered ? (
        <DataflowsPage
          report={reportForView}
          filters={filters}
          rows={dataflowRuns?.records ?? filtered.dataflows}
          totalRows={dataflowRuns?.summary.total_records ?? filtered.dataflows.length}
          loading={runsLoading}
          sort={dataflowSort}
          onSort={setDataflowSort}
          limit={dataflowLimit}
          offset={dataflowRuns?.summary.offset ?? dataflowOffset}
          onPageChange={setDataflowOffset}
          onPageSizeChange={(nextLimit) => {
            setDataflowLimit(nextLimit);
            setDataflowOffset(0);
          }}
          onInspect={(row) => openDetail({ kind: "dataflow", row })}
        />
      ) : null}
      {activePage === "failures" && filtered ? (
        <FailurePage
          report={reportForView}
          rows={filtered.failedRecords}
          filters={filters}
          onInspect={(row) => openDetail({ kind: failureDetailKind(row), row })}
        />
      ) : null}
      {activePage === "diagnostics" ? (
        <DiagnosticsPage report={reportForView} onInspect={(row) => openDetail({ kind: "diagnostics", row })} />
      ) : null}
      {activePage === "performance" && filtered ? (
        <PerformancePage
          report={reportForView}
          rows={filtered.performanceInvestigationQueue}
          onInspect={(row) => openDetail({ kind: "dataflow", row })}
        />
      ) : null}
      {activePage === "volume" ? (
        <VolumePage
          report={reportForView}
          filters={filters}
          rows={filtered?.volumeInvestigationQueue}
          onInspect={(row) => openDetail({ kind: "dataflow", row })}
        />
      ) : null}
      {activePage === "maintenance" ? (
        <MaintenancePage report={reportForView} filters={filters} onInspect={(row) => openDetail({ kind: "maintenance", row })} />
      ) : null}
      {activePage === "freshness" ? (
        <FreshnessPage report={reportForView} onInspect={(row) => openDetail({ kind: "freshness", row })} />
      ) : null}
      {detail ? (
        <MonitoringDetailDrawer
          kind={detail.kind}
          row={detail.row}
          environmentId={environmentId}
          timezoneName={reportForView.summary.timezone || "UTC"}
          relatedDataflows={
            detail.kind === "job" || detail.kind === "freshness" || detail.kind === "maintenance"
              ? detailDataflows
              : relatedDataflows(detail.row, dataflowRuns?.records ?? filtered?.dataflows ?? [])
          }
          reconciliationChecks={relatedReconciliationChecks(detail.row, reportForView.reconciliation.checks)}
          onOpenDataflow={(row) => pushDetail({ kind: "dataflow", row })}
          onOpenJob={openLinkedJobDetail}
          onBack={detailStack.length ? popDetail : undefined}
          onClose={closeDetail}
        />
      ) : null}
    </div>
  );
}

function failureDetailKind(row: Record<string, unknown>): MonitoringDetailKind {
  return row.failure_kind === "dataflow" || row.dataflow_run_id || row.dataflow_id ? "dataflow" : "failure";
}

function relatedDataflows(row: Record<string, unknown>, rows: MonitoringRecord[]) {
  const jobId = row.job_id ? String(row.job_id) : "";
  if (!jobId) return [];
  return rows.filter((candidate) => String(candidate.job_id ?? "") === jobId);
}

function dataflowInvestigateValue(row: Record<string, unknown>) {
  return String(row.dataflow_id ?? "").trim();
}

function maintenanceInvestigateValue(row: Record<string, unknown>) {
  return String(row.target ?? row.table ?? row.destination_full_table ?? row.destination_table ?? row.destination_path ?? "").trim();
}

function relatedReconciliationChecks(row: Record<string, unknown>, checks: Array<Record<string, string | number>>) {
  const jobId = row.job_id ? String(row.job_id) : "";
  if (!jobId) return [];
  return checks.filter((candidate) => String(candidate.job_id ?? "") === jobId);
}

function monitoringQueryParams(filters: ReturnType<typeof filtersFromSearch>) {
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
    investigateValue: filters.investigateValue
  };
}

function grainAdjustmentMessage(filters: ReturnType<typeof filtersFromSearch>, report: MonitoringReport) {
  const requested = String(report.summary.requested_grain ?? filters.grain ?? "").trim();
  const effective = String(report.summary.effective_grain ?? "").trim();
  if (!requested || !effective || requested === "auto" || requested === effective) return "";
  return `Grain adjusted from ${requested} to ${effective} for this time range. Narrow the range or use ${effective}/larger grain.`;
}

function buildMonitoringSearchOptions(
  report: MonitoringReport | null,
  jobRows: JobRecord[],
  dataflowRows: MonitoringRecord[]
): MonitoringSearchOption[] {
  if (!report) return [];
  const jobs = uniqueRows([...jobRows, ...report.operations.failed_jobs], (row) => String(row.job_id ?? ""));
  const dataflows = uniqueRows(
    [
      ...dataflowRows,
      ...report.failures.failed_records,
      ...report.performance.slowest_dataflows,
      ...(report.performance.investigation_queue ?? []),
      ...(report.volume.investigation_queue ?? [])
    ],
    (row) => String(row.dataflow_run_id ?? row.dataflow_id ?? row.dataflow_name ?? "")
  );
  const options: MonitoringSearchOption[] = [];

  for (const row of jobs) {
    const jobId = String(row.job_id ?? "").trim();
    if (!jobId) continue;
    options.push({
      key: `job:${jobId}`,
      kind: "job",
      label: jobId,
      detail: compactDetail([row.status, row.engine_name, row.platform_name, row.metadata_provider_name]),
      investigateKind: "job_id",
      value: jobId
    });
  }

  for (const row of dataflows) {
    const runId = String(row.dataflow_run_id ?? "").trim();
    const dataflowId = String(row.dataflow_id ?? "").trim();
    const dataflowName = String(row.dataflow_name ?? "").trim();
    if (dataflowName || dataflowId) {
      options.push({
        key: `dataflow-name:${dataflowName || dataflowId}`,
        kind: "dataflow",
        label: dataflowName || dataflowId,
        detail: connectionFlowDetail(row),
        investigateKind: "dataflow",
        value: dataflowName || dataflowId
      });
    }
    if (dataflowId && dataflowName) {
      options.push({
        key: `dataflow-id:${dataflowId}`,
        kind: "dataflow",
        label: dataflowId,
        detail: dataflowName,
        investigateKind: "dataflow",
        value: dataflowId
      });
    }
    if (runId) {
      options.push({
        key: `dataflow-run:${runId}`,
        kind: "dataflow run",
        label: runId,
        detail: compactDetail([row.status, dataflowName || dataflowId || "dataflow"]),
        investigateKind: "dataflow_run_id",
        value: runId
      });
    }
    const tableIdentity = tableSearchIdentity(row, "destination");
    if (tableIdentity) {
      options.push({
        key: `destination-table:${tableIdentity}`,
        kind: "table",
        label: tableIdentity,
        detail: compactDetail(["dest", row.destination_name]),
        investigateKind: "destination_table",
        value: tableIdentity
      });
    }
  }

  for (const row of report.maintenance.table_registry ?? []) {
    const tableIdentity = tableSearchIdentity(row, "destination");
    const target = firstString(row, ["target"]);
    const targetDisplay = firstString(row, ["target_display"]);
    const value = targetDisplay || tableIdentity || target;
    if (!value) continue;
    options.push({
      key: `maintenance-destination-table:${target || value}`,
      kind: "table",
      label: value,
      detail: compactDetail(["dest", row.destination_name, row.destination_connection_type]),
      investigateKind: "destination_table",
      value
    });
    if (target && target !== value) {
      options.push({
        key: `maintenance-destination-target:${target}`,
        kind: "table",
        label: target,
        detail: compactDetail(["dest", targetDisplay || tableIdentity, row.destination_name]),
        investigateKind: "destination_table",
        value: target
      });
    }
  }

  return uniqueRows(options, (option) => `${option.kind}:${option.value}:${option.detail}`).slice(0, 250);
}

function compactDetail(values: unknown[]) {
  const detail = values.map((value) => String(value ?? "").trim()).filter(Boolean).join(" · ");
  return detail || "unknown";
}

function connectionFlowDetail(row: MonitoringRecord) {
  const source = String(row.source_name ?? "").trim() || "unknown source";
  const destination = String(row.destination_name ?? "").trim() || "unknown destination";
  return `${source} - ${destination}`;
}

function uniqueRows<T>(rows: T[], getKey: (row: T) => string) {
  const seen = new Set<string>();
  const result: T[] = [];
  for (const row of rows) {
    const key = getKey(row);
    if (!key || seen.has(key)) continue;
    seen.add(key);
    result.push(row);
  }
  return result;
}

function tableSearchIdentity(row: Record<string, unknown>, direction: "source" | "destination") {
  const fullTable = firstString(row, [`${direction}_full_table`]).replace(/`/g, "");
  if (fullTable) return fullTable;
  const targetDisplay = direction === "destination" ? firstString(row, ["target_display"]) : "";
  if (targetDisplay) return targetDisplay;
  const catalog = firstString(row, [`${direction}_catalog`, `${direction}_catalog_name`]);
  const database = firstString(row, [`${direction}_database`, `${direction}_database_name`]);
  const schema = firstString(row, [`${direction}_schema`, `${direction}_schema_name`]);
  const table = firstString(row, [`${direction}_table`, `${direction}_table_name`]);
  const path = firstString(row, [`${direction}_path`, `${direction}_physical_path`, `${direction}_uri`]);
  const qualified = [catalog, database, schema, table].filter(Boolean).join(".");
  return qualified || path || "";
}

function firstString(row: Record<string, unknown>, keys: string[]) {
  for (const key of keys) {
    const value = row[key];
    if (value !== null && value !== undefined && value !== "") return String(value);
  }
  return "";
}
