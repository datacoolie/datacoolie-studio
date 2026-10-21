import { Help, HelpProvider } from "../../shared/components/Help";
import { Activity, AlertTriangle, BarChart3, Boxes, Clock3, FileWarning, Gauge, HardDrive, RefreshCw, Table2, Workflow } from "lucide-react";
import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import "./monitoring.css";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import type {
  JobRecord,
  MonitoringFilterOptionsResponse,
  MonitoringRecord,
  MonitoringReport
} from "../../shared/api/domainTypes";
import { EmptyState } from "../../shared/components/EmptyState";
import { ApiRequestError } from "../../shared/lib/errors";
import type { TableSort } from "../../shared/components/DataTable";import { MonitoringDetailKind } from "./MonitoringDetailDrawer";
import { jobIdentitySeed, mergeJobReconciliationEvidence } from "./details/jobStoryModel";
import { monitoringDimensionLabel } from "./monitoringDimensions";
import { monitoringDetailEvidenceRequest, monitoringFailureDetailKind, sameMonitoringJobScope } from "./monitoringDetailEvidence";
import { MonitoringFilterBar, type MonitoringSearchOption } from "./MonitoringFilterBar";
import { IntentPrefetchController } from "./intentPrefetch";
import {
  monitoringDataflowsOptions,
  monitoringDetailDataflowsOptions,
  monitoringDestinationDetailOptions,
  monitoringEvidenceOptions,
  monitoringDataflowRegistryOptions,
  monitoringJobRunDetailOptions,
  monitoringJobsOptions,
  monitoringQueryParams,
  monitoringReportOptions,
  monitoringReportScope,
  monitoringRegistryOriginForResponse,
  monitoringRegistryScope,
  monitoringRowSessionScope,
  monitoringRegistryAnchor,
  monitoringIdentityVersion,
  monitoringResponseIdentityVersion,
  monitoringResponseMatchesIdentityVersion,
  isMonitoringRegistryOriginValid,
  type MonitoringPageQueryData,
  type MonitoringRegistryOrigin,
  type MonitoringRegistryRunReference,
} from "./monitoringQueries";
import { freshnessFilterTransition } from "./monitoringFreshnessFilterGuard";
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
import { CompactNumberValue, metricNumber } from "./components/monitoringPresentation";
import { environmentQueryKeys } from "../environments/environmentQueries";
import { useDiagnosticsLogSourcesQuery } from "../sources/sourceQueries";

const MonitoringDetailDrawer = lazy(() => import("./MonitoringDetailDrawer").then((module) => ({ default: module.MonitoringDetailDrawer })));
const MonitoringDataflowRunDrawer = lazy(() => import("./MonitoringDataflowRunDrawer").then((module) => ({ default: module.MonitoringDataflowRunDrawer })));
const MonitoringDataflowRegistryDrawer = lazy(() => import("./MonitoringDataflowRegistryDrawer").then((module) => ({ default: module.MonitoringDataflowRegistryDrawer })));

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
  filterCountState?: "ready" | "loading" | "error";
  registryRefreshVersion?: number;
  timezoneName?: string | null;
  timezoneScope?: string;
  identityRevision?: string | null;
  timezoneLoading?: boolean;
  timezoneError?: string | null;
}

const pages = [
  { key: "overview", label: "Overview", icon: Gauge },
  { key: "jobs", label: "Jobs", icon: Table2 },
  { key: "dataflows", label: "Dataflows", icon: Workflow },
  { key: "failures", label: "Failures", icon: AlertTriangle },
  { key: "freshness", label: "Freshness", icon: Clock3 },
  { key: "performance", label: "Performance", icon: BarChart3 },
  { key: "volume", label: "Volume", icon: Boxes },
  { key: "maintenance", label: "Destinations", icon: HardDrive },
  { key: "diagnostics", label: "Diagnostics", icon: FileWarning }
] satisfies Array<{ key: MonitoringTabKey; label: string; icon: typeof Gauge }>;

const DEFAULT_JOB_RUN_TABLE_LIMIT = 100;
const DEFAULT_DATAFLOW_RUN_TABLE_LIMIT = 100;
const DEFAULT_PERFORMANCE_RUN_TABLE_LIMIT = 100;
const DEFAULT_DETAIL_EVIDENCE_LIMIT = 100;
const PAGE_INTENT_DELAY_MS = 150;

interface MonitoringDetailState {
  kind: MonitoringDetailKind;
  row: Record<string, unknown>;
  registryReference?: MonitoringRegistryRunReference;
  registryOrigin?: MonitoringRegistryOrigin | null;
  identityFence?: string;
}

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
  filterCountState = "ready",
  registryRefreshVersion = 0,
  timezoneName,
  timezoneScope,
  identityRevision,
  timezoneLoading = false,
  timezoneError = null,
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
  const registryPage = activePage === "freshness" || activePage === "performance" || activePage === "volume";
  const reportMatchesScope = reportData?.originScope === monitoringReportScope(environmentId, activePage, filters, timezoneScope, identityRevision) &&
    (!timezoneName || reportData.report.summary.timezone === timezoneName);
  const reportForView = (registryPage || activePage === "maintenance") && !reportMatchesScope ? null : reportData?.report ?? null;
  const displayedPage = registryPage ? activePage : reportData?.page ?? null;
  const diagnosticsSourcesQuery = useDiagnosticsLogSourcesQuery({
    environmentId,
    enabled: activePage === "diagnostics" || displayedPage === "diagnostics",
  });
  const resolvedTimezone = reportMatchesScope ? reportForView?.summary.timezone || timezoneName : timezoneName;
  const pageTransitionPending = !registryPage && reportLoading && displayedPage !== null && displayedPage !== activePage;
  const [detail, setDetail] = useState<MonitoringDetailState | null>(null);
  const [detailStack, setDetailStack] = useState<MonitoringDetailState[]>([]);
  const [registryIdentityNotice, setRegistryIdentityNotice] = useState(false);
  const registryDetail = detail?.kind === "dataflow-registry"
    ? detail : [...detailStack].reverse().find(item => item.kind === "dataflow-registry");
  const registryPresentationScope = timezoneScope ?? JSON.stringify([timezoneName ?? null]);
  const registryIdentityFence = monitoringIdentityVersion(identityRevision) ?? "";
  const selectedIdentityDetail = [detail, ...detailStack].find(item => item?.kind === "maintenance" || item?.kind === "dataflow-registry");
  const selectionFenceMatches = !selectedIdentityDetail || selectedIdentityDetail.identityFence === registryIdentityFence;
  const registryScopeKey = `${environmentId}:${monitoringRegistryScope(filters)}:${registryPresentationScope}:${registryIdentityFence}:${registryRefreshVersion}`;
  const [registryScope, setRegistryScope] = useState(() => ({ key: registryScopeKey, refreshVersion: registryRefreshVersion, asOf: monitoringRegistryAnchor() }));
  const currentRegistryScope = registryScope.key === registryScopeKey
    ? registryScope : { key: registryScopeKey, refreshVersion: registryRefreshVersion, asOf: registryScope.refreshVersion !== registryRefreshVersion ? new Date().toISOString() : monitoringRegistryAnchor() };
  if (registryScope.key !== registryScopeKey) setRegistryScope(currentRegistryScope);
  const registryAsOf = currentRegistryScope.asOf;
  const registryClockActive = Boolean(registryDetail) || (registryPage && (activePage === "freshness" || !["all", "custom"].includes(filters.range)));
  useEffect(() => {
    if (!registryClockActive) return;
    const advance = () => setRegistryScope(current => {
      const asOf = monitoringRegistryAnchor();
      return monitoringRegistryAnchor(Date.parse(current.asOf)) === asOf ? current : { ...current, key: registryScopeKey, asOf };
    });
    advance();
    const timer = window.setInterval(advance, 60_000);
    return () => window.clearInterval(timer);
  }, [registryClockActive, registryScopeKey]);
  useEffect(() => {
    if (!registryPage) return;
    void import("./MonitoringDataflowRegistryDrawer");
    void import("./details/DataflowRegistryDetails");
  }, [registryPage]);
  const detailRef = useRef(detail);
  const detailStackRef = useRef(detailStack);
  const freshnessDetailRequestRef = useRef(0);
  const freshnessFilterSignature = useMemo(() => JSON.stringify([monitoringRegistryScope(filters), registryPresentationScope]), [filters, registryPresentationScope]);
  const previousFreshnessFilterSignatureRef = useRef(freshnessFilterSignature);
  const drawerHistoryDepthRef = useRef(0);
  const suppressNextDrawerPopRef = useRef(false);
  const expireRegistrySelection = useCallback((showNotice = false) => {
    freshnessDetailRequestRef.current += 1;
    setDetail(null);
    setDetailStack([]);
    detailRef.current = null;
    detailStackRef.current = [];
    if (showNotice) setRegistryIdentityNotice(true);
    if (drawerHistoryDepthRef.current > 0) {
      suppressNextDrawerPopRef.current = true;
      window.history.go(-drawerHistoryDepthRef.current);
      drawerHistoryDepthRef.current = 0;
    }
  }, []);
  const previousRegistryIdentityFenceRef = useRef(registryIdentityFence);
  useEffect(() => {
    const previousFence = previousRegistryIdentityFenceRef.current;
    previousRegistryIdentityFenceRef.current = registryIdentityFence;
    if (previousFence === registryIdentityFence || !selectedIdentityDetail) return;
    // Metadata/log revisions invalidate the selected physical destination even
    // while the replacement registry response is still loading.  Closing here
    // prevents an old drawer response from remaining visible for the same
    // dataflow id; the user must choose the refreshed row afterwards.
    expireRegistrySelection(true);
  }, [expireRegistrySelection, selectedIdentityDetail, registryIdentityFence]);
  const [jobSort, setJobSort] = useState<TableSort>({ sortBy: "start_time", sortDir: "desc" });
  const [dataflowSort, setDataflowSort] = useState<TableSort>({ sortBy: "start_time", sortDir: "desc" });
  const [performanceSort, setPerformanceSort] = useState<TableSort>({ sortBy: "p95_duration_seconds", sortDir: "desc" });
  const [jobOffset, setJobOffset] = useState(0);
  const [jobLimit, setJobLimit] = useState(DEFAULT_JOB_RUN_TABLE_LIMIT);
  const [dataflowOffset, setDataflowOffset] = useState(0);
  const [dataflowLimit, setDataflowLimit] = useState(DEFAULT_DATAFLOW_RUN_TABLE_LIMIT);
  const [performanceOffset, setPerformanceOffset] = useState(0);
  const [performanceLimit, setPerformanceLimit] = useState(DEFAULT_PERFORMANCE_RUN_TABLE_LIMIT);
  const [freshnessSort, setFreshnessSort] = useState<TableSort>({ sortBy: "latest_freshness_at", sortDir: "desc" });
  const [freshnessOffset, setFreshnessOffset] = useState(0);
  const [freshnessLimit, setFreshnessLimit] = useState(100);
  const [volumeSort, setVolumeSort] = useState<TableSort>({ sortBy: "volume_rows_written", sortDir: "desc" });
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
    ...monitoringDataflowRegistryOptions(environmentId, filters, { limit: performanceLimit, offset: performanceOffset, sort: performanceSort }, undefined, "overview", registryAsOf, "performance", registryPresentationScope, registryIdentityFence),
    enabled: activePage === "performance" && !timezoneLoading,
  });
  const freshnessEvidenceQuery = useQuery({
    ...monitoringDataflowRegistryOptions(environmentId, filters, { limit: freshnessLimit, offset: freshnessOffset, sort: freshnessSort }, undefined, "overview", registryAsOf, "freshness", registryPresentationScope, registryIdentityFence),
    enabled: activePage === "freshness" && !timezoneLoading,
  });
  const volumeEvidenceQuery = useQuery({
    ...monitoringDataflowRegistryOptions(environmentId, filters, { limit: volumeLimit, offset: volumeOffset, sort: volumeSort }, undefined, "overview", registryAsOf, "volume", registryPresentationScope, registryIdentityFence),
    enabled: activePage === "volume" && !timezoneLoading,
  });
  const maintenanceEvidenceQuery = useQuery({
    ...monitoringEvidenceOptions(environmentId, "maintenance", filters, { limit: maintenanceLimit, offset: maintenanceOffset, sort: maintenanceSort }, "registry", registryIdentityFence),
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
  const performanceRuns = useRetainedRegistryList(performanceRunsQuery.data, `${environmentId}:${monitoringRegistryScope(filters)}:${registryPresentationScope}:${registryIdentityFence}:performance:${performanceLimit}:${performanceOffset}:${JSON.stringify(performanceSort)}`);
  const freshnessEvidence = useRetainedRegistryList(freshnessEvidenceQuery.data, `${environmentId}:${monitoringRegistryScope(filters)}:${registryPresentationScope}:${registryIdentityFence}:freshness:${freshnessLimit}:${freshnessOffset}:${JSON.stringify(freshnessSort)}`);
  const volumeEvidence = useRetainedRegistryList(volumeEvidenceQuery.data, `${environmentId}:${monitoringRegistryScope(filters)}:${registryPresentationScope}:${registryIdentityFence}:volume:${volumeLimit}:${volumeOffset}:${JSON.stringify(volumeSort)}`);
  const maintenanceEvidence = maintenanceEvidenceQuery.data ?? null;
  const runsLoading = activeRunsQuery?.isFetching ?? false;
  const runsError = activeRunsQuery?.error instanceof Error
    ? activeRunsQuery.error.message
    : activeRunsQuery?.error ? String(activeRunsQuery.error) : null;
  const displayedRegistryResponse = activePage === "freshness" ? freshnessEvidence
    : activePage === "performance" ? performanceRuns : activePage === "volume" ? volumeEvidence : null;
  const registryFacet = registryFacetForPage(activePage);
  const registryResponseIsCurrent = Boolean(activeRunsQuery?.data && !activeRunsQuery.isPlaceholderData && !activeRunsQuery.isError);
  const selectedRegistryRow = useMemo(() => {
    if (!registryDetail?.registryOrigin || !displayedRegistryResponse || !registryResponseIsCurrent) return null;
    const selectedId = String(registryDetail.row.dataflow_id ?? "");
    if (!selectedId.trim()) return null;
    const selectedIdentityVersion = monitoringIdentityVersion(registryDetail.registryOrigin.identityVersion);
    return displayedRegistryResponse.records.find(candidate => {
      if (String(candidate.dataflow_id ?? "") !== selectedId) return false;
      if (!selectedIdentityVersion) return true;
      return monitoringIdentityVersion(candidate.identity_version) === selectedIdentityVersion;
    }) ?? null;
  }, [displayedRegistryResponse, registryDetail, registryResponseIsCurrent]);
  const selectedRegistryOrigin = useMemo(() => {
    if (!registryDetail) return null;
    if (registryResponseIsCurrent && displayedRegistryResponse && registryFacet) {
      if (!registryDetail.registryOrigin || !selectedRegistryRow) return null;
      const currentIdentityVersion = monitoringResponseIdentityVersion(displayedRegistryResponse);
      const selectedIdentityVersion = monitoringIdentityVersion(registryDetail.registryOrigin.identityVersion);
      if (selectedIdentityVersion && currentIdentityVersion && selectedIdentityVersion !== currentIdentityVersion) return null;
      return monitoringRegistryOriginForResponse(
        environmentId,
        registryFacet,
        filters,
        selectedRegistryRow,
        displayedRegistryResponse,
        registryPresentationScope,
      );
    }
    return registryDetail.registryOrigin ?? null;
  }, [displayedRegistryResponse, environmentId, filters, registryDetail, registryFacet, registryPresentationScope, registryResponseIsCurrent, selectedRegistryRow]);
  const registryApiError = activeRunsQuery?.error instanceof ApiRequestError ? activeRunsQuery.error : null;
  const registryAnalyticsBlocked = registryPage && (reportErrorCode === "analytics_rebuild_required" ||
    registryApiError?.code === "analytics_rebuild_required");
  const registryRebuildReason = reportErrorCode === "analytics_rebuild_required" ? reportErrorReason
    : registryApiError?.code === "analytics_rebuild_required" && typeof registryApiError.detail === "object"
      ? String(registryApiError.detail?.reason ?? "") : null;
  const registryDisplayBlocked = registryAnalyticsBlocked || !resolvedTimezone;
  const registryReportState = useMemo(() => ({ loading: reportLoading, error: reportError, retry: onRetryReport }), [reportLoading, reportError, onRetryReport]);
  const refetchActiveRuns = activeRunsQuery?.refetch;
  useEffect(() => {
    if (!registryPage || registryRebuildReason !== "analytics_upgrade_in_progress" || !refetchActiveRuns) return;
    const timer = window.setTimeout(() => { void refetchActiveRuns(); }, 2_000);
    return () => window.clearTimeout(timer);
  }, [registryPage, registryRebuildReason, refetchActiveRuns, registryApiError]);
  const activeDetailEvidenceRequest = monitoringDetailEvidenceRequest(
    selectionFenceMatches ? detail : null,
    monitoringQueryParams(filters),
    { limit: detailDataflowLimit, offset: detailDataflowOffset, sort: detailDataflowSort },
  );
  const detailDataflowsQuery = useQuery(monitoringDetailDataflowsOptions(environmentId, activeDetailEvidenceRequest));
  const detailDataflows = detailDataflowsQuery.data ?? null;
  const detailIdentityVersion = detail?.kind === "maintenance" || detail?.kind === "freshness" || detail?.kind === "volume"
    ? monitoringIdentityVersion(detail.row.identity_version)
    : null;
  const destinationDetailQuery = useQuery(monitoringDestinationDetailOptions(environmentId, filters, detail?.kind === "maintenance" ? detail.row : null));
  const destinationDetailMatches = monitoringResponseMatchesIdentityVersion(destinationDetailQuery.data, detailIdentityVersion);
  const detailDataflowsIdentityMatches = monitoringResponseMatchesIdentityVersion(detailDataflows, detailIdentityVersion);
  const compatibleDetailDataflows = detailDataflowsIdentityMatches ? detailDataflows : null;
  const currentRegistryIdentityVersion = monitoringResponseIdentityVersion(displayedRegistryResponse);
  const selectedRegistryIdentityVersion = monitoringIdentityVersion(registryDetail?.registryOrigin?.identityVersion);
  const registrySelectionExpired = Boolean(
    registryDetail
      && selectedRegistryIdentityVersion
      && currentRegistryIdentityVersion
      && selectedRegistryIdentityVersion !== currentRegistryIdentityVersion,
  );
  const detailEvidenceExpired = Boolean(
    detail
      && detailIdentityVersion
      && ((detailDataflows
        && !detailDataflowsQuery.isFetching
        && !detailDataflowsIdentityMatches)
        || (detail.kind === "maintenance"
          && detailDataflowsQuery.error instanceof ApiRequestError
          && detailDataflowsQuery.error.status === 409)
        || (detail.kind === "maintenance" && (destinationDetailQuery.error instanceof ApiRequestError && destinationDetailQuery.error.status === 409
          || destinationDetailQuery.data && !destinationDetailMatches))),
  );
  useEffect(() => {
    if (!registrySelectionExpired && !detailEvidenceExpired) return;
    expireRegistrySelection(true);
  }, [detailEvidenceExpired, expireRegistrySelection, registrySelectionExpired]);
  const detailJobId = detail?.kind === "job" ? String(detail.row.job_id ?? "") : "";
  const detailJobSessionScope = detail?.kind === "job" ? monitoringRowSessionScope(detail.row) : undefined;
  const detailJobSessionId = detailJobSessionScope === "exact"
    ? String(detail?.row.log_session_id ?? "") : undefined;
  const detailJobRunQuery = useQuery(monitoringJobRunDetailOptions(
    environmentId,
    detailJobId,
    detailJobSessionId,
    detail?.row._source_id == null ? undefined : Number(detail.row._source_id),
    detailJobSessionScope,
  ));
  const jobAvailability = detailJobRunQuery.data != null ? "ready" : detailJobRunQuery.isError ? "error" : detailJobRunQuery.isPending ? "loading" : "not-found";
  const activeDetailRow = detail?.kind === "job"
    ? detailJobRunQuery.data ? mergeJobReconciliationEvidence(detailJobRunQuery.data, detail.row) : mergeJobReconciliationEvidence(jobIdentitySeed(detail.row), detail.row)
    : detail?.kind === "maintenance" && destinationDetailMatches ? destinationDetailQuery.data?.records[0] ?? detail.row : detail?.row;
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
    void queryClient.prefetchQuery(monitoringReportOptions(environmentId, page, filters, timezoneScope, identityRevision)).catch(() => undefined);
  };

  useEffect(() => {
    return () => intentPrefetch.current?.dispose();
  }, []);

  useEffect(() => {
    setDetail(null);
    setDetailStack([]);
    setRegistryIdentityNotice(false);
    detailRef.current = null;
    detailStackRef.current = [];
    drawerHistoryDepthRef.current = 0;
    freshnessDetailRequestRef.current += 1;
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
    const transition = freshnessFilterTransition(
      previousFreshnessFilterSignatureRef.current,
      freshnessFilterSignature,
      detail?.kind,
      detailStack.map((item) => item.kind),
    );
    if (!transition.changed) return;
    previousFreshnessFilterSignatureRef.current = freshnessFilterSignature;
    freshnessDetailRequestRef.current += 1;
    // Registry/run drawers hold an anchored query scope.  Any filter change
    // makes that scope obsolete, so close the stack and start a fresh anchor.
    setDetail(null);
    setDetailStack([]);
    setRegistryIdentityNotice(false);
    detailRef.current = null;
    detailStackRef.current = [];
    if (drawerHistoryDepthRef.current > 0) {
      suppressNextDrawerPopRef.current = true;
      window.history.go(-drawerHistoryDepthRef.current);
      drawerHistoryDepthRef.current = 0;
    }
  }, [detail, detailStack, freshnessFilterSignature]);

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
        freshnessDetailRequestRef.current += 1;
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

  const refreshRegistryForReference = useCallback((reference: MonitoringRegistryRunReference, reason?: string) => {
    if (reason === "identity_version_mismatch") expireRegistrySelection(true);
    void queryClient.invalidateQueries({
      queryKey: [...environmentQueryKeys.monitoring(environmentId), "dataflow-registry", reference.dataflow_id],
      refetchType: "active",
    });
  }, [environmentId, expireRegistrySelection, queryClient]);
  const commitRegistryDetail = useCallback((registryRow: Record<string, unknown>, registryOrigin: MonitoringRegistryOrigin | null = null) => {
    setRegistryIdentityNotice(false);
    freshnessDetailRequestRef.current += 1;
    setRegistryScope(current => {
      const asOf = monitoringRegistryAnchor();
      return monitoringRegistryAnchor(Date.parse(current.asOf)) === asOf ? current : { ...current, asOf };
    });
    window.history.pushState({ datacoolieMonitoringDrawer: true }, "", window.location.href);
    drawerHistoryDepthRef.current += 1;
    const nextDetail: MonitoringDetailState = { kind: "dataflow-registry", row: registryRow, registryOrigin, identityFence: registryIdentityFence };
    setDetail(nextDetail);
    setDetailStack([]);
    detailRef.current = nextDetail;
    detailStackRef.current = [];
  }, [registryIdentityFence]);
  const openRegistryDetail = useCallback((registryRow: Record<string, unknown>) => {
    if (!registryResponseIsCurrent) return;
    const directOrigin = registryFacet && displayedRegistryResponse && registryResponseIsCurrent
      && displayedRegistryResponse.records.some(candidate => candidate === registryRow)
      ? monitoringRegistryOriginForResponse(
        environmentId,
        registryFacet,
        filters,
        registryRow,
        displayedRegistryResponse,
        registryPresentationScope,
      )
      : null;
    if (directOrigin && isMonitoringRegistryOriginValid(directOrigin)) {
      commitRegistryDetail(registryRow, directOrigin);
      return;
    }

    const dataflowId = String(registryRow.dataflow_id ?? "");
    if (!dataflowId.trim()) return;
    commitRegistryDetail(registryRow);
  }, [commitRegistryDetail, displayedRegistryResponse, environmentId, filters, registryFacet, registryPresentationScope, registryResponseIsCurrent]);
  const changeFreshnessPageSize = useCallback((nextLimit: number) => { setFreshnessLimit(nextLimit); setFreshnessOffset(0); }, []);
  const changePerformancePageSize = useCallback((nextLimit: number) => { setPerformanceLimit(nextLimit); setPerformanceOffset(0); }, []);
  const changeVolumePageSize = useCallback((nextLimit: number) => { setVolumeLimit(nextLimit); setVolumeOffset(0); }, []);

  if (!reportForView && !reportLoading && !registryPage) {
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
    return <EmptyState icon={<Activity size={24} />} title="Add log source to inspect runs" />;
  }

  if (!reportForView && !registryPage) {
    return <EmptyState icon={<Activity size={24} />} title="Loading monitoring report" />;
  }

  const activeFilterCount = reportForView && hasActiveFilters(filters)
    ? metricNumber(reportForView.summary.dataflow_records) + metricNumber(reportForView.summary.job_records)
    : null;
  const grainWarning = reportForView ? grainAdjustmentMessage(filters, reportForView) : "";
  const logAdmission = reportForView?.log_admission;
  const excludedLogCount = metricNumber(logAdmission?.excluded);
  const pushDrawerHistory = () => {
    window.history.pushState({ datacoolieMonitoringDrawer: true }, "", window.location.href);
    drawerHistoryDepthRef.current += 1;
  };
  const openDetail = (nextDetail: MonitoringDetailState) => {
    nextDetail = { ...nextDetail, identityFence: registryIdentityFence };
    setRegistryIdentityNotice(false);
    pushDrawerHistory();
    setDetail(nextDetail);
    setDetailStack([]);
    detailRef.current = nextDetail;
    detailStackRef.current = [];
  };
  const pushDetail = (nextDetail: MonitoringDetailState) => {
    setRegistryIdentityNotice(false);
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
    setRegistryIdentityNotice(false);
    expireRegistrySelection(false);
  };
  const resolveJobDetailRow = (row: Record<string, unknown>) => {
    if (row.job_id === undefined || row.job_id === null) return row;
    const knownJobRows = [
      ...[...detailStack].reverse().filter((item) => item.kind === "job").map((item) => item.row),
      ...(detail?.kind === "job" ? [detail.row] : []),
      ...(jobRuns?.records ?? []),
      ...(reportForView?.operations.failed_jobs ?? [])
    ];
    return knownJobRows.find((candidate) => sameMonitoringJobScope(row, candidate)) ?? jobIdentitySeed(row);
  };
  const openLinkedJobDetail = (row: Record<string, unknown>) => {
    pushDetail({ kind: "job", row: resolveJobDetailRow(row) });
  };
  const openRegistryRun = (row: MonitoringRecord, reference?: MonitoringRegistryRunReference) => {
    pushDetail({ kind: "dataflow", row, registryReference: reference });
  };

  return (
    <HelpProvider identity={`${activePage}:${JSON.stringify(filters)}:${registryRefreshVersion}:${JSON.stringify(detail?.row ?? null)}`}><div className="view-stack monitoring-view-stack">
      <div className="monitoring-command-sticky">
        <section className="monitoring-command-panel">
          <div className="monitoring-header">
            <div>
              <h2>Monitoring</h2>
              <span>
                {reportForView ? <>
                {activePage === "maintenance" ? <><CompactNumberValue value={reportForView.summary.dataflow_records} /> operations, </> : <><CompactNumberValue value={reportForView.summary.job_records} /> jobs, <CompactNumberValue value={reportForView.summary.dataflow_records} /> dataflows, </>}
                {reportForView.summary.date_range.min || "-"} to {reportForView.summary.date_range.max || "-"}
                {reportForView.summary.effective_grain ? ` · ${reportForView.summary.effective_grain} grain` : ""}
                {activeFilterCount !== null ? ` · ${activeFilterCount} filtered records` : ""}
                </> : reportError ? "Report unavailable" : "Loading report…"}
                <Help as="span"
                  className={`monitoring-refresh-indicator${reportLoading ? " is-visible" : ""}`}
                  role="status"
                  aria-label={reportLoading ? "Refreshing monitoring data" : undefined}
                  title={reportLoading ? "Refreshing monitoring data" : undefined}
                >
                  <RefreshCw size={11} aria-hidden="true" />
                </Help>
              </span>
            </div>
            <nav className="monitoring-page-tabs" aria-label="Monitoring pages">
              {pages.map((page) => {
                const Icon = page.icon;
                return (
                  <Help as="button"
                    key={page.key}
                    className={activePage === page.key ? "active" : ""}
                    onClick={() => onPageChange(page.key)}
                    onPointerEnter={() => intentPrefetch.current?.schedule(page.key)}
                    onPointerLeave={() => intentPrefetch.current?.cancel(page.key)}
                    onPointerDown={() => intentPrefetch.current?.immediately(page.key)}
                    onFocus={() => intentPrefetch.current?.schedule(page.key)}
                    onBlur={() => intentPrefetch.current?.cancel(page.key)}
                  >
                    <Icon size={16} />
                    <span>{page.label}</span>
                  </Help>
                );
              })}
            </nav>
          </div>
          <MonitoringFilterBar
            environmentId={environmentId}
            options={filterOptions}
            countState={filterCountState}
            filters={filters}
            searchOptions={searchOptions}
            grainWarning={grainWarning}
            onChange={onFiltersChange}
          />
          {logAdmission && excludedLogCount > 0 ? (
            <div className="env-status-banner status-warning monitoring-log-admission-banner" role="status">
              <AlertTriangle size={18} aria-hidden="true" />
              <div>
                <strong>{logAdmission.unsupported_only ? "Unsupported log records excluded" : "Some log records were excluded"}</strong>
                <span>
                  <CompactNumberValue value={logAdmission.excluded} /> records were excluded from Monitoring because they are outside the supported log schema. These counts describe the current source snapshot across all recorded dates.
                  {logAdmission.files.length ? ` ${logAdmission.files.length} source file${logAdmission.files.length === 1 ? "" : "s"} contain exclusions.` : ""}
                </span>
              </div>
            </div>
          ) : null}
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
      {registryAnalyticsBlocked ? (
        <EmptyState icon={<AlertTriangle size={24} />} title="Monitoring analytics need attention" detail={reportError || runsError || "Monitoring analytics are updating."}
          action={<button onClick={registryRebuildReason === "analytics_upgrade_failed" ? onRetryUpgrade : registryRebuildReason === "analytics_upgrade_in_progress" ? onRetryReport : onOpenSources}>
            {registryRebuildReason === "analytics_upgrade_failed" ? "Retry upgrade" : registryRebuildReason === "analytics_upgrade_in_progress" ? "Check status" : "Open Sources"}
          </button>} />
      ) : null}
      {registryPage && !resolvedTimezone && !registryAnalyticsBlocked ? (
        <div className="monitoring-registry-section-state" role={timezoneError ? "alert" : "status"}>
          {timezoneError ? `Could not load display timezone: ${timezoneError}` : timezoneLoading ? "Loading display timezone…" : "Waiting for display timezone…"}
        </div>
      ) : null}
      <div className="monitoring-page-transition-content">
      <Suspense fallback={<EmptyState title="Loading monitoring page…" />}>
      {displayedPage === "overview" && reportForView ? (
        <MonitoringOverviewPage report={reportForView} filters={filters} onNavigate={onPageChange} />
      ) : null}
      {runsError && displayedPage === activePage && !registryPage ? <div className="app-error">{runsError}</div> : null}
      {registryPage && runsLoading && displayedRegistryResponse && !registryAnalyticsBlocked ? <div className="monitoring-page-transition-status" role="status" style={{ pointerEvents: "none" }}>Refreshing dataflows…</div> : null}
      {registryPage && runsError && !registryAnalyticsBlocked ? <div className="monitoring-registry-section-state" role="alert">{displayedRegistryResponse ? "Dataflow refresh failed; showing the last dataflows for these filters." : `Could not load dataflows: ${runsError}`} <button onClick={() => { void refetchActiveRuns?.(); }}>Retry</button></div> : null}
      {registryIdentityNotice ? <div className="monitoring-registry-section-state" role="status">Destination identity changed. Select a refreshed row to continue.</div> : null}
      {registryPage && reportError && reportForView ? <div className="monitoring-registry-section-state" role="alert">Report refresh failed; showing the last report for these filters. <button onClick={onRetryReport}>Retry</button></div> : null}
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
          onInspect={(row) => openDetail({ kind: monitoringFailureDetailKind(row), row })}
        />
      ) : null}
      {displayedPage === "diagnostics" && reportForView ? (
        <DiagnosticsPage report={reportForView} environmentId={environmentId} filters={filters} onInspect={(row) => openDetail({ kind: "diagnostics", row })}
          sourcesState={{
            data: diagnosticsSourcesQuery.data,
            loading: diagnosticsSourcesQuery.isFetching,
            error: diagnosticsSourcesQuery.isError ? String(diagnosticsSourcesQuery.error) : null,
            onRetry: () => { void diagnosticsSourcesQuery.refetch(); },
            onOpenSources,
          }} />
      ) : null}
      {displayedPage === "performance" && !registryDisplayBlocked ? (
        <PerformancePage
          report={reportForView}
          timezoneName={resolvedTimezone}
          reportState={registryReportState}
          scrollResetKey={`${environmentId}:${freshnessFilterSignature}:performance:${performanceRuns?.summary.offset ?? performanceOffset}:${performanceRuns?.summary.limit ?? performanceLimit}:${JSON.stringify(performanceSort)}`}
          tablePending={!performanceRuns}
          tableError={!performanceRuns ? runsError : null}
          rows={performanceRuns?.records ?? []}
          totalRows={performanceRuns?.summary.total_records ?? 0}
          loading={runsLoading && displayedPage === activePage}
          sort={performanceSort}
          onSort={setPerformanceSort}
          limit={performanceRuns?.summary.limit ?? performanceLimit}
          offset={performanceRuns?.summary.offset ?? performanceOffset}
          onPageChange={setPerformanceOffset}
          onPageSizeChange={changePerformancePageSize}
          filters={filters}
          onInspect={openRegistryDetail}
        />
      ) : null}
      {displayedPage === "volume" && !registryDisplayBlocked ? (
        <VolumePage
          report={reportForView}
          timezoneName={resolvedTimezone}
          reportState={registryReportState}
          scrollResetKey={`${environmentId}:${freshnessFilterSignature}:volume:${volumeEvidence?.summary.offset ?? volumeOffset}:${volumeEvidence?.summary.limit ?? volumeLimit}:${JSON.stringify(volumeSort)}`}
          tablePending={!volumeEvidence}
          tableError={!volumeEvidence ? runsError : null}
          filters={filters}
          rows={volumeEvidence?.records ?? []}
          totalRows={volumeEvidence?.summary.total_records ?? 0}
          loading={volumeEvidenceQuery.isFetching}
          sort={volumeSort}
          onSort={setVolumeSort}
          limit={volumeEvidence?.summary.limit ?? volumeLimit}
          offset={volumeEvidence?.summary.offset ?? volumeOffset}
          onPageChange={setVolumeOffset}
          onPageSizeChange={changeVolumePageSize}
          onInspect={openRegistryDetail}
        />
      ) : null}
      {activePage === "maintenance" ? (
        <MaintenancePage
          report={reportMatchesScope ? reportForView : null}
          reportState={{ loading: reportLoading, error: reportError, retry: onRetryReport }}
          timezoneName={resolvedTimezone}
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
          onInspect={reportMatchesScope && !maintenanceEvidenceQuery.isPlaceholderData ? (row) => openDetail({ kind: "maintenance", row }) : undefined}
        />
      ) : null}
      {displayedPage === "freshness" && !registryDisplayBlocked ? (
        <FreshnessPage
          report={reportForView}
          timezoneName={resolvedTimezone}
          reportState={registryReportState}
          scrollResetKey={`${environmentId}:${freshnessFilterSignature}:freshness:${freshnessEvidence?.summary.offset ?? freshnessOffset}:${freshnessEvidence?.summary.limit ?? freshnessLimit}:${JSON.stringify(freshnessSort)}`}
          tablePending={!freshnessEvidence}
          tableError={!freshnessEvidence ? runsError : null}
          filters={filters}
          rows={freshnessEvidence?.records ?? []}
          totalRows={freshnessEvidence?.summary.total_records ?? 0}
          loading={freshnessEvidenceQuery.isFetching}
          sort={freshnessSort}
          onSort={setFreshnessSort}
          limit={freshnessEvidence?.summary.limit ?? freshnessLimit}
          offset={freshnessEvidence?.summary.offset ?? freshnessOffset}
          onPageChange={setFreshnessOffset}
          onPageSizeChange={changeFreshnessPageSize}
          onInspect={openRegistryDetail}
          onInspectAdjustment={openRegistryDetail}
        />
      ) : null}
      </Suspense>
      </div>
      </div>
      {registryDetail && selectionFenceMatches && !registrySelectionExpired && resolvedTimezone && !registryAnalyticsBlocked ? (
        <Suspense fallback={null}>
          <MonitoringDataflowRegistryDrawer
            background={detail?.kind !== "dataflow-registry"}
            row={(selectedRegistryRow ?? registryDetail.row) as MonitoringRecord}
            environmentId={environmentId}
            filters={filters}
            asOf={registryAsOf}
            presentationScope={registryPresentationScope}
            parentOrigin={selectedRegistryOrigin}
            timezoneName={resolvedTimezone}
            onBack={detail?.kind === "dataflow-registry" && detailStack.length ? popDetail : undefined}
            onClose={closeDetail}
            onOpenDataflow={openRegistryRun}
          />
        </Suspense>
      ) : null}
      {detail && selectionFenceMatches && !detailEvidenceExpired && detail.kind !== "dataflow-registry" ? (
        <Suspense fallback={null}>
        {detail.kind === "dataflow" ? (
          <MonitoringDataflowRunDrawer
            row={detail.row as MonitoringRecord}
            environmentId={environmentId}
            registryReference={detail.registryReference}
            onRegistryProvenanceInvalid={refreshRegistryForReference}
            timezoneName={resolvedTimezone || reportForView?.summary.timezone || "UTC"}
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
            onOpenDataflow={(row) => pushDetail({ kind: "dataflow", row })}
            onOpenJob={openLinkedJobDetail}
            onBack={detailStack.length ? popDetail : undefined}
            onClose={closeDetail}
          />
        ) : (
          <MonitoringDetailDrawer
            kind={detail.kind}
            onOpenSources={onOpenSources}
            row={activeDetailRow ?? detail.row}
            destinationScopeLabel={[
              filters.range === "custom" ? `${filters.startTime || "—"} – ${filters.endTime || "—"}` : filters.range === "all" ? "All time" : `Last ${filters.range.replace(/(\d+)d$/u,"$1 days").replace(/(\d+)h$/u,"$1 hours")}`,
              ...(["status","operationType","stage","connection","engine","provider","sourceType","destinationType","loadType","search"] as const)
                .filter(key => filters[key] && filters[key] !== "all").map(key => `${key}: ${monitoringDimensionLabel(filters[key])}`),
              ...(filters.investigateKind ? ["Selected investigation"] : []),
            ].join(" · ")}
            jobDetailAvailability={jobAvailability}
            destinationDetailAvailability={destinationDetailQuery.data?.records[0] && destinationDetailMatches ? "ready" : destinationDetailQuery.isError ? "error" : destinationDetailQuery.isFetching ? "loading" : "not-found"}
            onRetryDestinationDetail={() => { void destinationDetailQuery.refetch(); }}
            onRetryJobDetail={() => { void detailJobRunQuery.refetch(); }}
            relatedDataflowsError={detailDataflowsQuery.isError ? String(detailDataflowsQuery.error) : null}
            onRetryRelatedDataflows={() => { void detailDataflowsQuery.refetch(); }}
            environmentId={environmentId}
            timezoneName={resolvedTimezone || reportForView?.summary.timezone || "UTC"}
            relatedDataflows={
              detail.kind === "job" || detail.kind === "freshness" || detail.kind === "maintenance" || detail.kind === "volume"
                ? compatibleDetailDataflows?.records ?? []
                : relatedDataflows(detail.row, dataflowRuns?.records ?? [])
            }
            relatedDataflowsTotal={compatibleDetailDataflows?.summary.total_records ?? compatibleDetailDataflows?.records.length ?? 0}
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
            relatedDataflowsLoading={Boolean(activeDetailEvidenceRequest && (detailDataflowsQuery.isFetching || !detailDataflowsIdentityMatches))}
            onOpenDataflow={(row) => pushDetail({ kind: "dataflow", row })}
            onOpenJob={openLinkedJobDetail}
            onBack={detailStack.length ? popDetail : undefined}
            onClose={closeDetail}
          />
        )}
        </Suspense>
      ) : null}
    </div></HelpProvider>
  );
}

function useRetainedRegistryList<T>(data: T | undefined, scope: string) {
  const retained = useRef<{ scope: string; data: T | null }>({ scope, data: data ?? null });
  if (retained.current.scope !== scope || data !== undefined) retained.current = { scope, data: data ?? null };
  return data ?? retained.current.data;
}

function registryFacetForPage(page: MonitoringTabKey): "freshness" | "performance" | "volume" | null {
  return page === "freshness" || page === "performance" || page === "volume" ? page : null;
}

function relatedDataflows(row: Record<string, unknown>, rows: MonitoringRecord[]) {
  if (row.job_id === undefined || row.job_id === null) return [];
  return rows.filter((candidate) => sameMonitoringJobScope(row, candidate));
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
  const jobs = uniqueRows([...jobRows, ...(report?.operations.failed_jobs ?? [])], (row) => String(row.job_id ?? ""));
  const dataflows = uniqueRows(
    [
      ...dataflowRows,
      ...(report?.failures.failed_records ?? [])
    ],
    (row) => String(row.dataflow_run_id ?? row.dataflow_id ?? row.dataflow_name ?? "")
  );
  const options: MonitoringSearchOption[] = [];

  for (const row of jobs) {
    const jobId = String(row.job_id ?? "");
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
    const runId = String(row.dataflow_run_id ?? "");
    const dataflowId = String(row.dataflow_id ?? "");
    const dataflowIdLabel = dataflowId.trim();
    const dataflowName = String(row.dataflow_name ?? "").trim();
    if (dataflowName || dataflowIdLabel) {
      options.push({
        key: `dataflow-name:${dataflowName || dataflowIdLabel}`,
        kind: "dataflow",
        label: dataflowName || dataflowIdLabel,
        detail: connectionFlowDetail(row),
        investigateKind: "dataflow",
        value: dataflowName || dataflowIdLabel
      });
    }
    if (dataflowIdLabel && dataflowName) {
      options.push({
        key: `dataflow-id:${dataflowId}`,
        kind: "dataflow",
        label: dataflowId,
        detail: dataflowName,
        investigateKind: "dataflow_id",
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
