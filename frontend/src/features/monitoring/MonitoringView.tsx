import { Activity, AlertTriangle, BarChart3, Boxes, Clock3, FileWarning, Gauge, HardDrive, RefreshCw, Table2, Workflow } from "lucide-react";
import { lazy, Suspense, useEffect, useMemo, useRef, useState } from "react";
import "./monitoring.css";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import type {
  JobRecord,
  MonitoringFilterOptionsResponse,
  MonitoringRecord,
  MonitoringReport
} from "../../shared/api/domainTypes";
import { EmptyState } from "../../shared/components/EmptyState";
import type { TableSort } from "./MonitoringCharts";import { MonitoringDetailKind } from "./MonitoringDetailDrawer";
import { mergeDataflowRunDetail, monitoringDetailEvidenceRequest } from "./monitoringDetailEvidence";
import { MonitoringFilterBar, type MonitoringSearchOption } from "./MonitoringFilterBar";
import { IntentPrefetchController } from "./intentPrefetch";
import {
  monitoringDataflowsOptions,
  monitoringDetailDataflowsOptions,
  monitoringEvidenceOptions,
  monitoringJobRunDetailOptions,
  monitoringJobsOptions,
  monitoringQueryParams,
  monitoringReportOptions,
  type MonitoringPageQueryData,
} from "./monitoringQueries";
import {
  DataflowsPage,
  DiagnosticsPage,
  FailurePage,
  FreshnessPage,
  JobsPage,
  MaintenancePage,
  MonitoringOverviewPage,
  PerformancePage,
  preloadMonitoringPage,
  VolumePage
} from "./monitoringPageModules";
import {
  hasActiveFilters,
  type MonitoringFilters,
  type MonitoringTabKey,
} from "./monitoringFilters";

const MonitoringDetailDrawer = lazy(() => import("./MonitoringDetailDrawer").then((module) => ({ default: module.MonitoringDetailDrawer })));
const MonitoringDataflowRunDrawer = lazy(() => import("./MonitoringDataflowRunDrawer").then((module) => ({ default: module.MonitoringDataflowRunDrawer })));

interface MonitoringViewProps {
  environmentId: number;
  activePage: MonitoringTabKey;
  onPageChange: (page: MonitoringTabKey) => void;
  filters: MonitoringFilters;
  onFiltersChange: (filters: MonitoringFilters) => void;
  reportData: MonitoringPageQueryData | null;
  reportLoading: boolean;
  reportError: string | null;
  reportErrorCode: string | null;
  reportErrorReason: string | null;
  onRetryReport: () => void;
  onRetryUpgrade: () => void;
  onOpenSources: () => void;
  filterOptions: MonitoringFilterOptionsResponse | null;
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
const DEFAULT_PERFORMANCE_RUN_TABLE_LIMIT = 100;
const DEFAULT_DETAIL_EVIDENCE_LIMIT = 100;
const PAGE_INTENT_DELAY_MS = 150;

export function MonitoringView({
  environmentId,
  activePage,
  onPageChange,
  filters,
  onFiltersChange,
  reportData,
  reportLoading,
  reportError,
  reportErrorCode,
  reportErrorReason,
  onRetryReport,
  onRetryUpgrade,
  onOpenSources,
  filterOptions,
}: MonitoringViewProps) {
  const queryClient = useQueryClient();
  const reportPrefetchRef = useRef<(page: MonitoringTabKey) => void>(() => undefined);
  const intentPrefetch = useRef<IntentPrefetchController<MonitoringTabKey> | null>(null);
  if (!intentPrefetch.current) {
    intentPrefetch.current = new IntentPrefetchController(
      (page) => reportPrefetchRef.current(page),
      PAGE_INTENT_DELAY_MS
    );
  }
  const reportForView = reportData?.report ?? null;
  const displayedPage = reportData?.page ?? null;
  const pageTransitionPending = reportLoading && displayedPage !== null && displayedPage !== activePage;
  const [detail, setDetail] = useState<{ kind: MonitoringDetailKind; row: Record<string, unknown> } | null>(null);
  const [detailStack, setDetailStack] = useState<Array<{ kind: MonitoringDetailKind; row: Record<string, unknown> }>>([]);
  const detailRef = useRef(detail);
  const detailStackRef = useRef(detailStack);
  const drawerHistoryDepthRef = useRef(0);
  const suppressNextDrawerPopRef = useRef(false);
  const [jobSort, setJobSort] = useState<TableSort>({ sortBy: "start_time", sortDir: "desc" });
  const [dataflowSort, setDataflowSort] = useState<TableSort>({ sortBy: "start_time", sortDir: "desc" });
  const [performanceSort, setPerformanceSort] = useState<TableSort>({ sortBy: "performance_candidate_priority", sortDir: "desc" });
  const [jobOffset, setJobOffset] = useState(0);
  const [jobLimit, setJobLimit] = useState(DEFAULT_JOB_RUN_TABLE_LIMIT);
  const [dataflowOffset, setDataflowOffset] = useState(0);
  const [dataflowLimit, setDataflowLimit] = useState(DEFAULT_DATAFLOW_RUN_TABLE_LIMIT);
  const [performanceOffset, setPerformanceOffset] = useState(0);
  const [performanceLimit, setPerformanceLimit] = useState(DEFAULT_PERFORMANCE_RUN_TABLE_LIMIT);
  const [freshnessSort, setFreshnessSort] = useState<TableSort>({ sortBy: "latest_freshness_at", sortDir: "desc" });
  const [freshnessOffset, setFreshnessOffset] = useState(0);
  const [freshnessLimit, setFreshnessLimit] = useState(100);
  const [volumeSort, setVolumeSort] = useState<TableSort>({ sortBy: "volume_candidate_priority", sortDir: "desc" });
  const [volumeOffset, setVolumeOffset] = useState(0);
  const [volumeLimit, setVolumeLimit] = useState(100);
  const [maintenanceSort, setMaintenanceSort] = useState<TableSort>({ sortBy: "attention_priority", sortDir: "desc" });
  const [maintenanceOffset, setMaintenanceOffset] = useState(0);
  const [maintenanceLimit, setMaintenanceLimit] = useState(100);
  const [detailDataflowOffset, setDetailDataflowOffset] = useState(0);
  const [detailDataflowLimit, setDetailDataflowLimit] = useState(DEFAULT_DETAIL_EVIDENCE_LIMIT);
  const [detailDataflowSort, setDetailDataflowSort] = useState<TableSort>({ sortBy: "start_time", sortDir: "desc" });
  const jobRunsQuery = useQuery({
    ...monitoringJobsOptions(environmentId, filters, { limit: jobLimit, offset: jobOffset, sort: jobSort }),
    enabled: activePage === "jobs",
  });
  const dataflowRunsQuery = useQuery({
    ...monitoringDataflowsOptions(environmentId, filters, { limit: dataflowLimit, offset: dataflowOffset, sort: dataflowSort }),
    enabled: activePage === "dataflows",
  });
  const performanceRunsQuery = useQuery({
    ...monitoringEvidenceOptions(environmentId, "performance", filters, { limit: performanceLimit, offset: performanceOffset, sort: performanceSort }),
    enabled: activePage === "performance",
  });
  const freshnessEvidenceQuery = useQuery({
    ...monitoringEvidenceOptions(environmentId, "freshness", filters, { limit: freshnessLimit, offset: freshnessOffset, sort: freshnessSort }),
    enabled: activePage === "freshness",
  });
  const volumeEvidenceQuery = useQuery({
    ...monitoringEvidenceOptions(environmentId, "volume", filters, { limit: volumeLimit, offset: volumeOffset, sort: volumeSort }),
    enabled: activePage === "volume",
  });
  const maintenanceEvidenceQuery = useQuery({
    ...monitoringEvidenceOptions(environmentId, "maintenance", filters, { limit: maintenanceLimit, offset: maintenanceOffset, sort: maintenanceSort }),
    enabled: activePage === "maintenance",
  });
  const activeRunsQuery = activePage === "jobs"
    ? jobRunsQuery
    : activePage === "dataflows"
      ? dataflowRunsQuery
      : activePage === "performance"
        ? performanceRunsQuery
        : activePage === "freshness"
          ? freshnessEvidenceQuery
          : activePage === "volume"
            ? volumeEvidenceQuery
            : activePage === "maintenance"
              ? maintenanceEvidenceQuery
        : null;
  const jobRuns = jobRunsQuery.data ?? null;
  const dataflowRuns = dataflowRunsQuery.data ?? null;
  const performanceRuns = performanceRunsQuery.data ?? null;
  const freshnessEvidence = freshnessEvidenceQuery.data ?? null;
  const volumeEvidence = volumeEvidenceQuery.data ?? null;
  const maintenanceEvidence = maintenanceEvidenceQuery.data ?? null;
  const runsLoading = activeRunsQuery?.isFetching ?? false;
  const runsError = activeRunsQuery?.error instanceof Error
    ? activeRunsQuery.error.message
    : activeRunsQuery?.error ? String(activeRunsQuery.error) : null;
  const activeDetailEvidenceRequest = monitoringDetailEvidenceRequest(
    detail,
    monitoringQueryParams(filters),
    { limit: detailDataflowLimit, offset: detailDataflowOffset, sort: detailDataflowSort },
  );
  const detailDataflowsQuery = useQuery(monitoringDetailDataflowsOptions(environmentId, activeDetailEvidenceRequest));
  const detailDataflows = detailDataflowsQuery.data ?? null;
  const detailJobId = detail?.kind === "job" ? String(detail.row.job_id ?? "").trim() : "";
  const detailJobRunQuery = useQuery(monitoringJobRunDetailOptions(environmentId, detailJobId));
  const activeDetailRow = detail?.kind === "job"
    ? mergeDataflowRunDetail(detail.row, detailJobRunQuery.data)
    : detail?.row;
  const searchOptions = useMemo(
    () => buildMonitoringSearchOptions(
      reportForView,
      jobRuns?.records ?? [],
      [
        ...(dataflowRuns?.records ?? []),
        ...(performanceRuns?.records ?? []),
        ...(freshnessEvidence?.records ?? []),
        ...(volumeEvidence?.records ?? []),
        ...(maintenanceEvidence?.records ?? []),
      ]
    ),
    [reportForView, jobRuns, dataflowRuns, performanceRuns, freshnessEvidence, volumeEvidence, maintenanceEvidence]
  );
  reportPrefetchRef.current = (page) => {
    void preloadMonitoringPage(page).catch(() => undefined);
    void queryClient.prefetchQuery(monitoringReportOptions(environmentId, page, filters)).catch(() => undefined);
  };

  useEffect(() => {
    return () => intentPrefetch.current?.dispose();
  }, []);

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
    setJobOffset(0);
    setDataflowOffset(0);
    setPerformanceOffset(0);
    setFreshnessOffset(0);
    setVolumeOffset(0);
    setMaintenanceOffset(0);
  }, [
    filters,
    jobSort,
    dataflowSort,
    performanceSort,
    freshnessSort,
    volumeSort,
    maintenanceSort,
  ]);

  useEffect(() => {
    setDetailDataflowOffset(0);
    setDetailDataflowLimit(DEFAULT_DETAIL_EVIDENCE_LIMIT);
    setDetailDataflowSort({ sortBy: "start_time", sortDir: "desc" });
  }, [detail?.kind, detail?.row]);

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

  if (!reportForView && !reportLoading) {
    if (reportError) {
      if (reportErrorCode === "analytics_rebuild_required") {
        const upgradeInProgress = reportErrorReason === "analytics_upgrade_in_progress";
        const upgradeFailed = reportErrorReason === "analytics_upgrade_failed";
        return (
          <EmptyState
            icon={<AlertTriangle size={24} />}
            title={upgradeInProgress ? "Updating Monitoring analytics" : upgradeFailed ? "Analytics upgrade needs retry" : "Monitoring analytics need to be rebuilt"}
            detail={upgradeInProgress
              ? "Studio is rebuilding every Log source into a validated DuckDB candidate. This page refreshes automatically."
              : upgradeFailed
                ? "The previous cache is still intact. Studio will retry automatically, or you can retry now."
                : "Sync the Log sources to recreate the disposable analytics cache. Source configuration and sync history are preserved."}
            action={upgradeFailed
              ? <button onClick={onRetryUpgrade}>Retry upgrade</button>
              : upgradeInProgress
                ? <button onClick={onRetryReport}>Check status</button>
                : <button onClick={onOpenSources}>Open Sources</button>}
          />
        );
      }
      return (
        <EmptyState
          icon={<AlertTriangle size={24} />}
          title="Could not load monitoring report"
          detail={reportError}
          action={<button onClick={onRetryReport}>Retry</button>}
        />
      );
    }
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
                <span
                  className={`monitoring-refresh-indicator${reportLoading ? " is-visible" : ""}`}
                  role="status"
                  aria-label={reportLoading ? "Refreshing monitoring data" : undefined}
                  title={reportLoading ? "Refreshing monitoring data" : undefined}
                >
                  <RefreshCw size={11} aria-hidden="true" />
                </span>
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
                    onPointerEnter={() => intentPrefetch.current?.schedule(page.key)}
                    onPointerLeave={() => intentPrefetch.current?.cancel(page.key)}
                    onPointerDown={() => intentPrefetch.current?.immediately(page.key)}
                    onFocus={() => intentPrefetch.current?.schedule(page.key)}
                    onBlur={() => intentPrefetch.current?.cancel(page.key)}
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
            onChange={onFiltersChange}
          />
        </section>
      </div>

      <div
        className={`monitoring-page-transition${pageTransitionPending ? " is-pending" : ""}`}
        aria-busy={pageTransitionPending}
      >
      {pageTransitionPending ? (
        <div className="monitoring-page-transition-loading" role="status">
          <RefreshCw size={28} aria-hidden="true" />
          <span>Loading {pages.find((page) => page.key === activePage)?.label ?? "page"}…</span>
        </div>
      ) : null}
      {reportError && displayedPage !== activePage ? (
        <div className="monitoring-page-transition-status is-error" role="alert">
          <span>
            Could not load {pages.find((page) => page.key === activePage)?.label ?? "page"}; showing{" "}
            {pages.find((page) => page.key === displayedPage)?.label ?? "previous page"}.
          </span>
          <button onClick={reportErrorReason === "analytics_upgrade_failed" ? onRetryUpgrade : reportErrorReason === "analytics_upgrade_in_progress" ? onRetryReport : reportErrorCode === "analytics_rebuild_required" ? onOpenSources : onRetryReport}>
            {reportErrorReason === "analytics_upgrade_failed" ? "Retry upgrade" : reportErrorReason === "analytics_upgrade_in_progress" ? "Check status" : reportErrorCode === "analytics_rebuild_required" ? "Open Sources" : "Retry"}
          </button>
        </div>
      ) : null}
      <div className="monitoring-page-transition-content">
      <Suspense fallback={<EmptyState title="Loading monitoring page…" />}>
      {displayedPage === "overview" && reportForView ? (
        <MonitoringOverviewPage report={reportForView} filters={filters} onNavigate={onPageChange} />
      ) : null}
      {runsError && displayedPage === activePage ? <div className="app-error">{runsError}</div> : null}
      {displayedPage === "jobs" && reportForView ? (
        <JobsPage
          report={reportForView}
          filters={filters}
          rows={jobRuns?.records ?? []}
          totalRows={jobRuns?.summary.total_records ?? 0}
          loading={runsLoading && displayedPage === activePage}
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
      {displayedPage === "dataflows" && reportForView ? (
        <DataflowsPage
          report={reportForView}
          filters={filters}
          rows={dataflowRuns?.records ?? []}
          totalRows={dataflowRuns?.summary.total_records ?? 0}
          loading={runsLoading && displayedPage === activePage}
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
      {displayedPage === "failures" && reportForView ? (
        <FailurePage
          report={reportForView}
          rows={reportForView.failures.failed_records}
          filters={filters}
          onInspect={(row) => openDetail({ kind: failureDetailKind(row), row })}
        />
      ) : null}
      {displayedPage === "diagnostics" ? (
        <DiagnosticsPage report={reportForView} onInspect={(row) => openDetail({ kind: "diagnostics", row })} />
      ) : null}
      {displayedPage === "performance" && reportForView ? (
        <PerformancePage
          report={reportForView}
          rows={performanceRuns?.records ?? []}
          totalRows={performanceRuns?.summary.total_records ?? 0}
          loading={runsLoading && displayedPage === activePage}
          sort={performanceSort}
          onSort={setPerformanceSort}
          limit={performanceLimit}
          offset={performanceRuns?.summary.offset ?? performanceOffset}
          onPageChange={setPerformanceOffset}
          onPageSizeChange={(nextLimit) => {
            setPerformanceLimit(nextLimit);
            setPerformanceOffset(0);
          }}
          filters={filters}
          onInspect={(row) => openDetail({ kind: "dataflow", row })}
        />
      ) : null}
      {displayedPage === "volume" ? (
        <VolumePage
          report={reportForView}
          filters={filters}
          rows={volumeEvidence?.records ?? []}
          totalRows={volumeEvidence?.summary.total_records ?? 0}
          loading={volumeEvidenceQuery.isFetching}
          sort={volumeSort}
          onSort={setVolumeSort}
          limit={volumeLimit}
          offset={volumeEvidence?.summary.offset ?? volumeOffset}
          onPageChange={setVolumeOffset}
          onPageSizeChange={(nextLimit) => { setVolumeLimit(nextLimit); setVolumeOffset(0); }}
          onInspect={(row) => openDetail({ kind: "volume", row })}
        />
      ) : null}
      {displayedPage === "maintenance" ? (
        <MaintenancePage
          report={reportForView}
          filters={filters}
          rows={maintenanceEvidence?.records ?? []}
          totalRows={maintenanceEvidence?.summary.total_records ?? 0}
          loading={maintenanceEvidenceQuery.isFetching}
          sort={maintenanceSort}
          onSort={setMaintenanceSort}
          limit={maintenanceLimit}
          offset={maintenanceEvidence?.summary.offset ?? maintenanceOffset}
          onPageChange={setMaintenanceOffset}
          onPageSizeChange={(nextLimit) => { setMaintenanceLimit(nextLimit); setMaintenanceOffset(0); }}
          onInspect={(row) => openDetail({ kind: "maintenance", row })}
        />
      ) : null}
      {displayedPage === "freshness" ? (
        <FreshnessPage
          report={reportForView}
          filters={filters}
          rows={freshnessEvidence?.records ?? []}
          totalRows={freshnessEvidence?.summary.total_records ?? 0}
          loading={freshnessEvidenceQuery.isFetching}
          sort={freshnessSort}
          onSort={setFreshnessSort}
          limit={freshnessLimit}
          offset={freshnessEvidence?.summary.offset ?? freshnessOffset}
          onPageChange={setFreshnessOffset}
          onPageSizeChange={(nextLimit) => { setFreshnessLimit(nextLimit); setFreshnessOffset(0); }}
          onInspect={(row) => openDetail({ kind: "freshness", row })}
        />
      ) : null}
      </Suspense>
      </div>
      </div>
      {detail ? (
        <Suspense fallback={null}>
        {detail.kind === "dataflow" ? (
          <MonitoringDataflowRunDrawer
            row={detail.row as MonitoringRecord}
            environmentId={environmentId}
            timezoneName={reportForView.summary.timezone || "UTC"}
            relatedDataflows={relatedDataflows(detail.row, dataflowRuns?.records ?? [])}
            relatedDataflowsTotal={detailDataflows?.summary.total_records ?? detailDataflows?.records.length ?? 0}
            relatedDataflowsOffset={detailDataflowOffset}
            relatedDataflowsLimit={detailDataflowLimit}
            relatedDataflowsSort={detailDataflowSort}
            onRelatedDataflowsPageChange={setDetailDataflowOffset}
            onRelatedDataflowsPageSizeChange={(nextLimit) => {
              setDetailDataflowLimit(nextLimit);
              setDetailDataflowOffset(0);
            }}
            onRelatedDataflowsSort={(nextSort) => {
              setDetailDataflowSort(nextSort);
              setDetailDataflowOffset(0);
            }}
            relatedDataflowsLoading={Boolean(activeDetailEvidenceRequest && detailDataflowsQuery.isFetching)}
            reconciliationChecks={relatedReconciliationChecks(detail.row, reportForView.reconciliation.checks)}
            onOpenDataflow={(row) => pushDetail({ kind: "dataflow", row })}
            onOpenJob={openLinkedJobDetail}
            onBack={detailStack.length ? popDetail : undefined}
            onClose={closeDetail}
          />
        ) : (
          <MonitoringDetailDrawer
            kind={detail.kind}
            row={activeDetailRow ?? detail.row}
            environmentId={environmentId}
            timezoneName={reportForView.summary.timezone || "UTC"}
            relatedDataflows={
              detail.kind === "job" || detail.kind === "freshness" || detail.kind === "maintenance" || detail.kind === "volume"
                ? detailDataflows?.records ?? []
                : relatedDataflows(detail.row, dataflowRuns?.records ?? [])
            }
            relatedDataflowsTotal={detailDataflows?.summary.total_records ?? detailDataflows?.records.length ?? 0}
            relatedDataflowsOffset={detailDataflowOffset}
            relatedDataflowsLimit={detailDataflowLimit}
            relatedDataflowsSort={detailDataflowSort}
            onRelatedDataflowsPageChange={setDetailDataflowOffset}
            onRelatedDataflowsPageSizeChange={(nextLimit) => {
              setDetailDataflowLimit(nextLimit);
              setDetailDataflowOffset(0);
            }}
            onRelatedDataflowsSort={(nextSort) => {
              setDetailDataflowSort(nextSort);
              setDetailDataflowOffset(0);
            }}
            relatedDataflowsLoading={Boolean(activeDetailEvidenceRequest && detailDataflowsQuery.isFetching)}
            reconciliationChecks={relatedReconciliationChecks(detail.row, reportForView.reconciliation.checks)}
            onOpenDataflow={(row) => pushDetail({ kind: "dataflow", row })}
            onOpenJob={openLinkedJobDetail}
            onBack={detailStack.length ? popDetail : undefined}
            onClose={closeDetail}
          />
        )}
        </Suspense>
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

function relatedReconciliationChecks(row: Record<string, unknown>, checks: Array<Record<string, string | number>>) {
  const jobId = row.job_id ? String(row.job_id) : "";
  if (!jobId) return [];
  return checks.filter((candidate) => String(candidate.job_id ?? "") === jobId);
}

function grainAdjustmentMessage(filters: MonitoringFilters, report: MonitoringReport) {
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
      ...report.failures.failed_records
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
