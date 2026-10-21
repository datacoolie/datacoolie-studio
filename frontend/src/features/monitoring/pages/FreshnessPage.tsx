import { Help } from "../../../shared/components/Help";
import { monitoringDimensionLabel } from "../monitoringDimensions";
import type { EChartsOption } from "echarts";
import { memo, useMemo } from "react";
import type { MonitoringRecord, MonitoringReport } from "../../../shared/api/domainTypes";
import type { TableColumn } from "../../../shared/components/DataTable";
import { formatTimestampForStatusHistory } from "../../../shared/time";
import type { MonitoringFilters } from "../monitoringFilters";import { CompactNumberValue, DataTable, DetailMetric, EndpointCell, HealthStripCard, ReportChart, ReportPanel, TablePager, TableDateTimeValue, WatermarkBadge, baseChartOption, fixedHorizontalBarGrid, fixedHorizontalCategoryAxis, formatCompact, formatNumber, formatPercent, metricNumber, metricValue, monitoringEndpointPresentation, monitoringTimezone, horizontalBarDataZoom, horizontalBarSeriesSizing, reportChartPalette, reportChartGrid, resolveTrendBucketKeys, normalizeTrendBucketKey, normalizeTrendGrain, type TableSort } from "../components/monitoringPresentation";
import { formatAgeNumber, toApproximateNumber } from "../../../shared/lib/numberFormat";
import { formatFreshnessAge, freshnessAgeSeconds, freshnessHealthFromCounts } from "../details/detailPrimitives";
import { chartMetric, chartTooltip, firstChartParam, type ChartTooltipParams } from "../components/chartTooltip";

type FreshnessRow = Record<string, unknown>;

const STALE_THRESHOLD_DAYS = 7;
const WATERMARK_MOVEMENT_COLORS = {
  Initialized: reportChartPalette.teal,
  Advanced: reportChartPalette.success,
  Changed: reportChartPalette.amber,
  Empty: reportChartPalette.muted,
  Unchanged: reportChartPalette.skipped,
  Unknown: reportChartPalette.unknown,
  "Advanced %": reportChartPalette.blue,
} as const;
const WATERMARK_MOVEMENT_LEGEND = [
  ["Initialized", WATERMARK_MOVEMENT_COLORS.Initialized, "The first watermark value was recorded."],
  ["Advanced", WATERMARK_MOVEMENT_COLORS.Advanced, "The watermark moved forward."],
  ["Changed", WATERMARK_MOVEMENT_COLORS.Changed, "The saved watermark changed, but forward movement was not established."],
  ["Empty", WATERMARK_MOVEMENT_COLORS.Empty, "An incremental watermark bound was applied, but the source returned no rows and nothing was persisted."],
  ["Unchanged", WATERMARK_MOVEMENT_COLORS.Unchanged, "The watermark did not move."],
  ["Unknown", WATERMARK_MOVEMENT_COLORS.Unknown, "The movement could not be determined."],
  ["Advanced %", WATERMARK_MOVEMENT_COLORS["Advanced %"], "Advanced runs divided by all ETL runs in the current filters, including runs without watermark configuration."],
] as const;
const FRESHNESS_AGE_LEGEND = [
  ["Current ≤7d", reportChartPalette.unknown],
  ["Stale >7d", reportChartPalette.amber],
] as const;
const WATERMARK_COVERAGE_LEGEND = [
  ["Enabled", reportChartPalette.success],
  ["Not configured", reportChartPalette.unknown],
] as const;
const READ_BOUNDARY_ADJUSTMENT_LEGEND = [
  ["Adjusted", reportChartPalette.amber, "The reader resolved a different lower bound than the input evidence."],
] as const;
const WATERMARK_MOVEMENT_STATUS_NAMES = {
  ini: "Initialized",
  unc: "Unchanged",
  chg: "Changed",
  emp: "Empty",
  unk: "Unknown",
} as const;
const REGISTRY_VIRTUALIZATION = { overscan: 5 } as const;

function freshnessRowKey(row: FreshnessRow) {
  return String(row.dataflow_id ?? row.dataflow_name ?? row.source_name ?? "unknown");
}

type ReportState = { loading: boolean; error: string | null; retry: () => void };

export function FreshnessPage({
  report,
  filters,
  rows,
  totalRows,
  loading,
  sort,
  onSort,
  limit,
  offset,
  onPageChange,
  onPageSizeChange,
  onInspect,
  onInspectAdjustment,
  timezoneName,
  reportState,
  scrollResetKey,
  tablePending = false,
  tableError = null,
}: {
  report: MonitoringReport | null;
  filters: MonitoringFilters;
  rows: MonitoringRecord[];
  totalRows: number;
  loading: boolean;
  sort: TableSort;
  onSort: (sort: TableSort) => void;
  limit: number;
  offset: number;
  onPageChange: (offset: number) => void;
  onPageSizeChange: (limit: number) => void;
  onInspect?: (row: FreshnessRow) => void;
  onInspectAdjustment?: (row: FreshnessRow) => void;
  timezoneName?: string | null;
  reportState?: ReportState;
  scrollResetKey?: string;
  tablePending?: boolean;
  tableError?: string | null;
}) {
  const resolvedTimezoneName = timezoneName !== undefined
    ? timezoneName
    : report
      ? monitoringTimezone(report)
      : null;
  const hasTableResponse = !tablePending && !tableError;
  const registryState = tableError
    ? "Dataflows unavailable"
    : tablePending
      ? "Loading dataflows…"
      : null;
  return (
    <div className="monitoring-page monitoring-freshness-report">
      {report ? (
        <FreshnessReportContent
          part="health"
          report={report}
          filters={filters}
          timezoneName={resolvedTimezoneName ?? "UTC"}
          onInspect={onInspect}
          onInspectAdjustment={onInspectAdjustment}
        />
      ) : (
        <FreshnessReportPlaceholder part="health" reportState={reportState} />
      )}
      <div className="monitoring-freshness-content monitoring-equal-rows-3">
        {report ? (
          <FreshnessReportContent
            part="charts"
            report={report}
            filters={filters}
            timezoneName={resolvedTimezoneName ?? "UTC"}
            onInspect={onInspect}
            onInspectAdjustment={onInspectAdjustment}
          />
        ) : (
          <FreshnessReportPlaceholder part="charts" reportState={reportState} />
        )}
        <ReportPanel
          title="Dataflow freshness registry"
          subtitle={hasTableResponse ? <><CompactNumberValue value={totalRows} /> dataflows</> : undefined}
          titleTooltip="One row per environment/dataflow, with the latest freshness check and recorded watermark provenance. Click a row to inspect all three facets and canonical runs."
          className="monitoring-freshness-registry-panel"
          headerAction={hasTableResponse ? (
            <TablePager
              limit={limit}
              offset={offset}
              loadedRows={rows.length}
              totalRows={totalRows}
              loading={loading}
              onPageChange={onPageChange}
              onPageSizeChange={onPageSizeChange}
            />
          ) : undefined}
        >
          {registryState ? (
            <div className="monitoring-registry-table-state" role="status">{registryState}</div>
          ) : (
            <DataflowFreshnessTable
              rows={rows}
              offset={0}
              limit={limit}
              sort={sort}
              onSort={onSort}
              onInspect={onInspect}
              timezoneName={resolvedTimezoneName}
              scrollResetKey={scrollResetKey}
            />
          )}
        </ReportPanel>
      </div>
    </div>
  );
}

const FreshnessReportContent = memo(function FreshnessReportContent({
  part,
  report,
  filters,
  timezoneName,
  onInspect,
  onInspectAdjustment,
}: {
  part: "health" | "charts";
  report: MonitoringReport;
  filters: MonitoringFilters;
  timezoneName?: string | null;
  onInspect?: (row: FreshnessRow) => void;
  onInspectAdjustment?: (row: FreshnessRow) => void;
}) {
  const kpis = report.freshness.kpis;
  const watermarkTrendRows = fillMissingWatermarkMovementTrendRows(
    report.freshness.watermark_movement_by_date ?? [],
    filters,
    report.summary.date_range,
    timezoneName ?? "UTC",
    String(report.summary.effective_grain ?? filters.grain ?? "day")
  );
  const readBoundaryRows = report.freshness.read_boundary_adjustment_by_dataflow ?? [];
  const readBoundaryChartRows = readBoundaryAdjustmentChartRows(readBoundaryRows);
  const firstMetric = (...keys: string[]) => {
    for (const key of keys) {
      const value = metricValue(kpis, key);
      if (value !== undefined) return value;
    }
    return undefined;
  };
  const observedDataflowsValue = firstMetric("observed_dataflows");
  const staleDataflowsValue = firstMetric("stale_dataflows", "stale_candidates");
  const advancedRunsValue = firstMetric("watermark_advanced_runs");
  const totalRunsValue = firstMetric("watermark_rate_runs");
  const initializedRunsValue = firstMetric("watermark_initialized_runs");
  const unchangedRunsValue = firstMetric("watermark_unchanged_runs");
  const changedRunsValue = firstMetric("watermark_changed_runs");
  const emptyRunsValue = firstMetric("watermark_empty_runs");
  const saveErrorRunsValue = firstMetric("watermark_save_error_runs");
  const noCandidateRunsValue = firstMetric("watermark_no_candidate_runs");
  const notRequestedRunsValue = firstMetric("watermark_not_requested_runs");
  const notAttemptedRunsValue = firstMetric("watermark_not_attempted_runs");
  const unknownRunsValue = firstMetric("watermark_unknown_runs");
  const observedDataflows = metricNumber(observedDataflowsValue);
  const staleDataflows = metricNumber(staleDataflowsValue);
  const initializedRuns = metricNumber(initializedRunsValue);
  const changedRuns = metricNumber(changedRunsValue);
  const movementStatusMetrics = [
    ["ini", initializedRunsValue, initializedRuns ? "blue" : "neutral"],
    ["unc", unchangedRunsValue, "neutral"],
    ["chg", changedRunsValue, changedRuns ? "warning" : "neutral"],
    ["emp", emptyRunsValue, "neutral"],
    ["unk", unknownRunsValue, "neutral"],
  ] as const;
  const movementStatusSummary = movementStatusMetrics
    .map(([label, value]) => `${label}=${WATERMARK_MOVEMENT_STATUS_NAMES[label]} ${formatNumber(value)}`)
    .join(", ");
  const movementTitle = [
    "Advanced rate = Advanced / total ETL runs in the current filters × 100%.",
    `Total ETL runs: ${formatNumber(totalRunsValue)}. Includes Initialized, Unknown and runs without watermark configuration, across all run statuses.`,
    `Configured watermark movement counts: Initialized ${formatNumber(initializedRunsValue)}, Advanced ${formatNumber(advancedRunsValue)}, Unchanged ${formatNumber(unchangedRunsValue)}, Changed ${formatNumber(changedRunsValue)}, Empty ${formatNumber(emptyRunsValue)}, Unknown ${formatNumber(unknownRunsValue)}.`,
    `Status key: ${movementStatusSummary}.`,
    `Persistence outcomes: not requested ${formatNumber(notRequestedRunsValue)}, not attempted ${formatNumber(notAttemptedRunsValue)}, no candidate ${formatNumber(noCandidateRunsValue)}, save error ${formatNumber(saveErrorRunsValue)}.`,
  ].join("\n");
  const skippedStreakThreshold = Number(kpis.skipped_streak_threshold ?? 3);
  const staleThresholdDays = STALE_THRESHOLD_DAYS;
  const hasAgeEvidence = Number(kpis.dataflows_with_freshness_evidence ?? 0) > 0;
  const ageMetric = (field: string) => hasAgeEvidence ? formatFreshnessAge(kpis[field], null) : "-";
  const successfulRunsValue = firstMetric("successful_runs", "latest_successful_runs");
  const failedRunsValue = firstMetric("failed_runs");
  const skippedRunsValue = firstMetric("skipped_runs", "skipped_no_new_data");
  const latestStatusIssueDataflowsValue = firstMetric("latest_status_issue_dataflows", "latest_failed_or_active_dataflows");
  const latestWatermarkIssueDataflowsValue = firstMetric("latest_watermark_issue_dataflows");
  const latestCheckMissingDataflowsValue = firstMetric("latest_check_missing_dataflows");
  const latestStatusIssueDataflows = metricNumber(latestStatusIssueDataflowsValue);
  const latestWatermarkIssueDataflows = metricNumber(latestWatermarkIssueDataflowsValue);
  const latestCheckMissingDataflows = metricNumber(latestCheckMissingDataflowsValue);
  const health = freshnessHealthFromCounts({
    observedDataflows,
    staleDataflows,
    latestRunIssues: latestStatusIssueDataflows,
    latestWatermarkIssues: latestWatermarkIssueDataflows,
    missingChecks: latestCheckMissingDataflows,
    metricsKnown: [
      observedDataflowsValue,
      staleDataflowsValue,
      latestStatusIssueDataflowsValue,
      latestWatermarkIssueDataflowsValue,
      latestCheckMissingDataflowsValue,
    ].every(hasFreshnessValue),
  });
  const advancedRateValue = metricValue(kpis, "watermark_advanced_rate");
  const advancedRateLabel = hasFreshnessValue(advancedRateValue)
    ? formatPercent(advancedRateValue)
    : "unk";
  const healthTitle = [
    "Freshness health:",
    "- Evidence unknown: one or more freshness health inputs are missing.",
    "- No execution evidence: no dataflow runs in current filters.",
    `- Latest counts: stale ${formatNumber(staleDataflowsValue)}, run issue ${formatNumber(latestStatusIssueDataflowsValue)}, watermark issue ${formatNumber(latestWatermarkIssueDataflowsValue)}, missing check ${formatNumber(latestCheckMissingDataflowsValue)}.`,
    `- Stale: latest succeeded/skipped run is older than ${staleThresholdDays} days.`,
    "- Run issue: latest run is not succeeded or skipped.",
    "- Watermark issue: latest load/save failed or saved persistence evidence is incomplete.",
    "- No candidate and not attempted are neutral on their own; historical save errors do not override a healthy latest run.",
    "- Missing check: no succeeded/skipped ETL run supplies a check timestamp in this filtered range.",
    "- Consecutive skipped runs are diagnostic only; they do not change this health status."
  ].join("\n");
  const healthContent = (
      <section className="overview-health-strip monitoring-freshness-health-strip">
        <HealthStripCard
          label="Freshness health"
          value={health.label}
          detail={<span className="health-rate-detail"><DetailMetric label="stale" value={<CompactNumberValue value={staleDataflowsValue} />} tone={staleDataflows ? "warning" : "neutral"} /><span className="separator"> · </span><DetailMetric label="run issue" value={<CompactNumberValue value={latestStatusIssueDataflowsValue} />} tone={latestStatusIssueDataflows ? "warning" : "neutral"} /></span>}
          intent={health.intent}
          accent="intent"
          title={healthTitle}
        />
        <HealthStripCard
          label="Observed dataflows"
          value={<CompactNumberValue value={observedDataflowsValue} />}
          detail={<span className="health-rate-detail freshness-observed-runs-detail"><span className="health-detail-label">ETL runs · S · F · Skip</span><span className="health-detail-value"><b className="detail-value-good"><CompactNumberValue value={successfulRunsValue} /></b><span className="freshness-observed-run-divider" aria-hidden="true">|</span><b className="detail-value-bad"><CompactNumberValue value={failedRunsValue} /></b><span className="freshness-observed-run-divider" aria-hidden="true">|</span><b className="detail-value-warning"><CompactNumberValue value={skippedRunsValue} /></b></span></span>}
          title="Observed dataflows = distinct source/dataflow pairs in execution logs; sessions are consolidated in the registry. S/F/Skip = succeeded, failed, skipped ETL runs in current filters."
        />
        <HealthStripCard label="Stale dataflows" value={<CompactNumberValue value={staleDataflowsValue} />} detail={<DetailMetric label="of dataflows" value={formatPercent(metricValue(kpis, "stale_dataflow_rate"))} tone={staleDataflows ? "warning" : "neutral"} />} intent={staleDataflows ? "warning" : "neutral"} accent="intent" title={`Stale = latest succeeded/skipped run is older than ${staleThresholdDays} days. Counted by source/dataflow pair, with sessions consolidated.`} />
        <HealthStripCard
          label="Check age"
          value={`P95 ${ageMetric("p95_age_seconds")}`}
          detail={<span className="health-rate-detail"><DetailMetric label="min" value={ageMetric("min_age_seconds")} tone="neutral" labelFirst /><span className="separator"> · </span><DetailMetric label="P50" value={ageMetric("p50_age_seconds")} tone="blue" labelFirst /><span className="separator"> · </span><DetailMetric label="max" value={ageMetric("max_age_seconds")} tone={metricNumber(metricValue(kpis, "max_age_days")) > STALE_THRESHOLD_DAYS ? "warning" : "neutral"} labelFirst /></span>}
          intent={metricNumber(metricValue(kpis, "p95_age_days")) > STALE_THRESHOLD_DAYS ? "warning" : "neutral"}
          accent="intent"
          title={`P50/P95 age uses the latest succeeded/skipped ETL check for each source/dataflow pair. Stale begins after ${staleThresholdDays} days; min and max are shown for context.`}
        />
        <HealthStripCard label="Watermark coverage" value={formatPercent(metricValue(kpis, "watermark_coverage_rate"))} detail={<DetailMetric label="enabled flows" value={<CompactNumberValue value={metricValue(kpis, "watermark_enabled_dataflows")} />} tone="blue" />} intent={metricNumber(metricValue(kpis, "watermark_coverage_rate")) > 0 ? "good" : "neutral"} accent={metricNumber(metricValue(kpis, "watermark_coverage_rate")) > 0 ? "source" : "neutral"} title={`Coverage = enabled source/dataflow pairs / observed source/dataflow pairs in the filtered ETL range. Enabled ${formatNumber(metricValue(kpis, "watermark_enabled_dataflows"))} of ${formatNumber(observedDataflowsValue)}; sessions are consolidated.`} />
        <HealthStripCard
          label="Watermark movement"
          value={<><CompactNumberValue value={advancedRunsValue} /> - {advancedRateLabel} advanced</>}
          detail={<Help as="span" className="freshness-watermark-status-line" role="group" aria-label={`Watermark movement counts: ${movementStatusSummary}`} title={movementTitle}>{movementStatusMetrics.map(([label, value, tone], index) => <span className="freshness-watermark-status-pair" key={label}>{index > 0 ? " " : null}<span className="health-detail-label">{label}</span>{" "}<b className={`health-detail-value detail-value-${tone}`}><CompactNumberValue value={value} /></b></span>)}</Help>}
          className="freshness-watermark-movement-card"
          intent={latestWatermarkIssueDataflows ? "warning" : "neutral"}
          accent="intent"
          title={movementTitle}
        />
      </section>
  );
  const chartContent = (
      <>
        <section className="monitoring-freshness-top-grid">
          <ReportPanel title="Oldest / stale dataflows" subtitle="latest check age" titleTooltip={`Ranks source/dataflow pairs by age of the latest succeeded/skipped ETL check. Stale candidates are older than ${staleThresholdDays} days.`} headerAction={<FreshnessChartLegend label="Freshness age legend" items={FRESHNESS_AGE_LEGEND} />}>
            <ReportChart option={freshnessAgeByDataflowOption(report.freshness.age_by_dataflow ?? [], timezoneName)} height="100%" wheelDataZoomStep={1} />
          </ReportPanel>
          <ReportPanel title="Watermark movement trend" subtitle={`${report.summary.effective_grain ?? "day"} grain`} titleTooltip="Bars count movement states for ETL runs with watermark configuration. Advanced rate uses Advanced / all ETL runs in the current filters × 100%, including runs without watermark configuration, matching the summary card." headerAction={<WatermarkMovementLegend />}>
            <ReportChart option={watermarkMovementTrendOption(watermarkTrendRows)} height="100%" />
          </ReportPanel>
        </section>
        <section className="monitoring-freshness-middle-grid">
          <ReportPanel title="Freshness age distribution" subtitle="dataflows by age band" titleTooltip="Buckets each source/dataflow pair by the age of its latest succeeded/skipped ETL check. Unknown means no valid check timestamp exists in the filtered range.">
            <ReportChart option={freshnessAgeDistributionOption(report.freshness.age_distribution ?? [])} height="100%" />
          </ReportPanel>
          <ReportPanel title="Watermark coverage by stage" titleTooltip="Stage-level coverage = watermark-enabled source/dataflow pairs / observed pairs in the filtered ETL runs. Configuration or meaningful read/load/save evidence counts as enabled; not configured is neutral." headerAction={<FreshnessChartLegend label="Watermark coverage legend" items={WATERMARK_COVERAGE_LEGEND} />}>
            <ReportChart option={watermarkCoverageByStageOption(report.freshness.watermark_coverage_by_stage ?? [])} height="100%" wheelDataZoomStep={1} />
          </ReportPanel>
          <ReportPanel title="Read boundary adjustment" titleTooltip="Shows source/dataflow pairs with one or more runs where the resolved reader lower bound differs from the input lower bound. The bar length is the Adjusted run count; detailed evidence is in the drawer." headerAction={<FreshnessChartLegend label="Read boundary adjustment legend" items={READ_BOUNDARY_ADJUSTMENT_LEGEND} />}>
            <ReportChart option={readBoundaryAdjustmentOption(readBoundaryRows)} height="100%" wheelDataZoomStep={1} onPointClick={(params) => { const row = readBoundaryChartRows[Number(params.dataIndex ?? -1)]; if (row) (onInspectAdjustment ?? onInspect)?.(row); }} />
          </ReportPanel>
          <ReportPanel title="Consecutive skipped" subtitle="dataflows by streak" titleTooltip={`Groups source/dataflow pairs by their current consecutive skipped-run streak. The diagnostic threshold is ${skippedStreakThreshold}+ runs. Skipped is not a failure and is not used by Freshness health; it usually means no new incremental data was available.`}>
            <ReportChart option={skippedStreakDistributionOption(report.freshness.skipped_streak_distribution ?? [])} height="100%" />
          </ReportPanel>
        </section>
      </>
  );
  return part === "health" ? healthContent : chartContent;
});

function FreshnessReportPlaceholder({ part, reportState }: { part: "health" | "charts"; reportState?: ReportState }) {
  const error = reportState?.error;
  if (part === "health") {
    return (
      <>
        <section className="overview-health-strip monitoring-freshness-health-strip monitoring-report-placeholder-strip" aria-busy={!error}>
          {Array.from({ length: 6 }, (_, index) => <div className="monitoring-report-health-placeholder" key={index}><span>{error ? "Unavailable" : "Loading…"}</span></div>)}
        </section>
        {error ? <div className="monitoring-report-state is-error" role="alert"><span>Could not load freshness report: {error}</span>{reportState?.retry ? <button type="button" onClick={reportState.retry}>Retry</button> : null}</div> : null}
      </>
    );
  }
  return (
    <>
      <section className="monitoring-freshness-top-grid"><FreshnessChartPlaceholder title="Oldest / stale dataflows" /><FreshnessChartPlaceholder title="Watermark movement trend" /></section>
      <section className="monitoring-freshness-middle-grid"><FreshnessChartPlaceholder title="Freshness age distribution" /><FreshnessChartPlaceholder title="Watermark coverage by stage" /><FreshnessChartPlaceholder title="Read boundary adjustment" /><FreshnessChartPlaceholder title="Consecutive skipped" /></section>
    </>
  );
}

function FreshnessChartPlaceholder({ title }: { title: string }) {
  return <ReportPanel title={title}><div className="monitoring-report-chart-placeholder" aria-hidden="true" /></ReportPanel>;
}

const DataflowFreshnessTable = memo(function DataflowFreshnessTable({
  rows,
  offset,
  limit,
  sort,
  onSort,
  onInspect,
  timezoneName,
  scrollResetKey
}: {
  rows: FreshnessRow[];
  offset: number;
  limit: number;
  sort: TableSort;
  onSort: (sort: TableSort) => void;
  onInspect?: (row: FreshnessRow) => void;
  timezoneName?: string | null;
  scrollResetKey?: string;
}) {
  const columns = useMemo<TableColumn<FreshnessRow>[]>(() => [
    { key: "dataflow_name", label: "Dataflow", sortable: true, minWidth: 150, fillPriority: "normal", className: "freshness-registry-cell-single-line", render: (row) => <CompactDataflowCell row={row} />, measureValue: (row) => String(row.dataflow_name ?? row.dataflow_id ?? "unknown") },
    { key: "stage", label: "Stage", sortable: true, minWidth: 90, maxWidth: 150, fillPriority: "normal", className: "freshness-registry-cell-single-line", render: (row) => monitoringDimensionLabel(row.stage), measureValue: (row) => monitoringDimensionLabel(row.stage) },
    { key: "source_name", label: "Source", sortable: true, width: 210, render: (row) => <EndpointCell row={row as MonitoringRecord} direction="source" />, measureValue: (row) => { const endpoint = monitoringEndpointPresentation(row as MonitoringRecord, "source", false); return [endpoint.locator, endpoint.connection]; } },
    { key: "destination_name", label: "Destination", sortable: true, width: 210, render: (row) => <EndpointCell row={row as MonitoringRecord} direction="destination" />, measureValue: (row) => { const endpoint = monitoringEndpointPresentation(row as MonitoringRecord, "destination", false); return [endpoint.locator, endpoint.connection]; } },
    { key: "destination_load_type", label: "Load", sortable: true, autoFit: true, minWidth: 58, maxWidth: 112, className: "freshness-registry-cell-single-line", render: (row) => monitoringDimensionLabel(row.destination_load_type), measureValue: (row) => monitoringDimensionLabel(row.destination_load_type) },
    { key: "latest_freshness_at", label: "Latest check", sortable: true, autoFit: true, minWidth: 132, maxWidth: 178, className: "freshness-registry-cell-single-line freshness-latest-check-cell", render: (row) => <TableDateTimeValue value={row.latest_freshness_at} timezoneName={timezoneName} />, measureValue: (row) => row.latest_freshness_at && timezoneName ? formatTimestampForStatusHistory(String(row.latest_freshness_at), timezoneName) : String(row.latest_freshness_at ?? "-") },
    { key: "age_days", label: "Age", sortable: true, autoFit: true, minWidth: 54, maxWidth: 76, className: "freshness-registry-cell-single-line", render: (row) => <AgeCell row={row} />, measureValue: (row) => formatFreshnessAge(row.age_seconds, row.age_days) },
    { key: "last_statuses", label: "Recent runs", sortable: true, sortKey: "latest_run_status", autoFit: true, minWidth: 88, maxWidth: 104, className: "freshness-registry-cell-single-line", render: (row) => <LastStatusCell row={row} timezoneName={timezoneName} />, measureValue: (row) => {
      const count = Array.isArray(row.last_statuses) ? Math.min(row.last_statuses.length, 5) : 0;
      return count ? "●".repeat(count) : "-";
    } },
    { key: "movement_state", label: "Watermark", sortable: true, autoFit: true, minWidth: 98, maxWidth: 138, className: "freshness-registry-cell-single-line", render: (row) => <WatermarkBadge value={row.movement_state ?? row.save_status ?? row.load_status ?? row.coverage_state} />, measureValue: (row) => String(row.movement_state ?? row.save_status ?? row.load_status ?? "Not configured") },
    { key: "latest_success_watermark", label: "Saved watermark", sortable: true, sortKey: "latest_success_watermark", minWidth: 160, fillPriority: "last", render: (row) => <LatestWatermarkCell row={row} /> }
  ], [timezoneName]);

  return (
    <DataTable<FreshnessRow>
      rows={rows}
      compactNumbers
      compactNumberStyle="infix"
      columns={columns}
      maxRows={limit}
      offset={offset}
      onRowClick={onInspect}
      sort={sort}
      onSort={onSort}
      timezoneName={timezoneName}
      className="monitoring-freshness-table"
      sortMode="server"
      getRowKey={freshnessRowKey}
      virtualization={REGISTRY_VIRTUALIZATION}
      scrollResetKey={scrollResetKey}
    />
  );
});

function CompactDataflowCell({ row }: { row: FreshnessRow }) {
  const name = String(row.dataflow_name ?? row.dataflow_id ?? "unknown");
  return (
    <Help as="span" className="monitor-stack-cell" title={name}>
      <strong>{name}</strong>
    </Help>
  );
}

function LatestWatermarkCell({ row }: { row: FreshnessRow }) {
  const value = freshnessSavedWatermarkValue(row);
  const displayValue = freshnessSavedWatermarkDisplayValue(row);
  const truncated = row.latest_success_watermark_truncated === true;
  return (
    <Help as="span" className="monitor-stack-cell freshness-latest-watermark" title={`Saved watermark${truncated ? " preview (truncated)" : ""}: ${value}`}>
      <span>{displayValue}</span>
    </Help>
  );
}

export function AgeCell({ row }: { row: FreshnessRow }) {
  const ageSeconds = freshnessAgeSeconds(row.age_seconds, row.age_days);
  const tone = ageSeconds !== null && ageSeconds > STALE_THRESHOLD_DAYS * 86400 ? "is-warning" : "";
  return <span className={`freshness-age-value ${tone}`}>{formatFreshnessAge(row.age_seconds, row.age_days)}</span>;
}

function LastStatusCell({ row, timezoneName }: { row: FreshnessRow; timezoneName?: string | null }) {
  const statuses = freshnessRecentStatusItems(row);
  if (!statuses.length) {
    return <span className="freshness-status-history freshness-status-history-empty" aria-label="No recent runs">-</span>;
  }
  return (
    <span className="freshness-status-history" aria-label="Last five statuses, oldest to newest">
      {statuses.map((item, index) => {
        const record: Record<string, unknown> = item && typeof item === "object" ? item as Record<string, unknown> : { status: item };
        const status = String(record.status ?? "unknown").toLowerCase();
        const timeValue = record.time ? String(record.time) : "";
        const title = [
          status,
          timeValue && timezoneName ? formatTimestampForStatusHistory(timeValue, timezoneName) : timeValue,
          record.dataflow_run_id ? `run ${record.dataflow_run_id}` : "",
        ].filter(Boolean).join("\n");
        return <Help as="i" key={`${status}-${index}`} className={`status-bg-${status}`} title={title || status} />;
      })}
    </span>
  );
}

function WatermarkMovementLegend() {
  return <FreshnessChartLegend label="Watermark movement legend" items={WATERMARK_MOVEMENT_LEGEND} />;
}

function FreshnessChartLegend({
  label,
  items,
}: {
  label: string;
  items: ReadonlyArray<readonly [string, string, string?]>;
}) {
  return (
    <div className="freshness-chart-legend" aria-label={label}>
      {items.map(([itemLabel, color, description]) => (
        <Help as="span" key={itemLabel} title={description}>
          <i style={{ backgroundColor: color }} aria-hidden="true" />
          {itemLabel}
        </Help>
      ))}
    </div>
  );
}

export function freshnessSavedWatermarkValue(row: Record<string, unknown>) {
  const watermark = row.latest_success_watermark_preview ?? row.latest_success_watermark;
  return hasFreshnessValue(watermark) ? formatWatermarkValue(watermark) : "-";
}

export function freshnessSavedWatermarkDisplayValue(row: Record<string, unknown>) {
  const watermark = row.latest_success_watermark_preview ?? row.latest_success_watermark;
  if (!hasFreshnessValue(watermark)) return "-";
  const objectValue = parseWatermarkObject(watermark);
  if (!objectValue) return formatWatermarkValue(watermark);
  return Object.entries(objectValue)
    .map(([key, value]) => `${key}: ${formatWatermarkPart(value)}`)
    .join(" · ");
}

export function freshnessRecentStatusItems(row: Record<string, unknown>) {
  const statuses = Array.isArray(row.last_statuses) ? row.last_statuses.slice(0, 5) : [];
  return statuses.reverse();
}

function hasFreshnessValue(value: unknown) {
  if (Array.isArray(value)) return value.length > 0;
  return value !== null && value !== undefined && value !== "";
}

function formatWatermarkValue(value: unknown) {
  if (value && typeof value === "object") {
    try {
      return JSON.stringify(value);
    } catch {
      return String(value);
    }
  }
  return String(value);
}

function parseWatermarkObject(value: unknown): Record<string, unknown> | null {
  const candidate = typeof value === "string" ? parseJsonValue(value) : value;
  if (!candidate || typeof candidate !== "object" || Array.isArray(candidate)) return null;
  return candidate as Record<string, unknown>;
}

function parseJsonValue(value: string) {
  try {
    return JSON.parse(value) as unknown;
  } catch {
    return null;
  }
}

function formatWatermarkPart(value: unknown) {
  if (value && typeof value === "object") return formatWatermarkValue(value);
  return String(value);
}

function formatFreshnessTimestamp(value: unknown, timezoneName?: string | null) {
  if (!hasFreshnessValue(value)) return "-";
  const text = String(value);
  return timezoneName ? formatTimestampForStatusHistory(text, timezoneName) : text;
}

export function freshnessAgeByDataflowOption(rows: FreshnessRow[], timezoneName?: string | null): EChartsOption {
  const visible = [...rows]
    .filter((row) => row.age_days !== null && row.age_days !== undefined && Number.isFinite(Number(row.age_days)))
    .sort((a, b) => Number(b.age_days ?? 0) - Number(a.age_days ?? 0));
  const zoomConfig = horizontalBarDataZoom(visible.length);
  const barSizing = horizontalBarSeriesSizing(visible.length);
  if (!visible.length) return emptyChartOption("No dataflow freshness evidence.");
  return baseChartOption({
    tooltip: {
      trigger: "item",
      triggerOn: "mousemove",
      axisPointer: { type: "none" },
      formatter: chartTooltip((params) => {
        const row = visible[Number(firstChartParam(params)?.dataIndex ?? 0)] ?? {};
        const stale = Number(row.age_days ?? 0) > STALE_THRESHOLD_DAYS;
        return {
          title: String(row.dataflow_name ?? row.dataflow_id ?? "unknown"),
          metrics: [
            chartMetric("Age", formatAgeSeconds(Number(row.age_seconds ?? Number(row.age_days ?? 0) * 86400))),
            chartMetric("Freshness", stale ? "Stale" : "Current", stale ? "warning" : "success"),
            chartMetric("Target", String(row.target ?? "unknown")),
            chartMetric(timezoneName ? `Latest check (${timezoneName})` : "Latest check", formatFreshnessTimestamp(row.latest_freshness_at, timezoneName)),
            chartMetric("Latest check status", String(row.latest_freshness_status ?? "unknown")),
            chartMetric("Latest run status", String(row.latest_run_status ?? "unknown")),
          ]
        };
      })
    },
    grid: fixedHorizontalBarGrid(132, Boolean(zoomConfig), { top: 6 }),
    xAxis: { type: "value", axisLabel: { fontSize: 10, formatter: (value: number) => `${formatCompact(value)}d` }, splitLine: { lineStyle: { color: reportChartPalette.grid } } },
    yAxis: fixedHorizontalCategoryAxis(
      visible.map((row) => String(row.dataflow_name ?? row.dataflow_id ?? "unknown")),
      132,
      { axisLine: { show: true, lineStyle: { color: "#d9e1ea", width: 1 } } }
    ),
    dataZoom: zoomConfig,
    series: [{
      name: "Age days",
      type: "bar",
      ...barSizing,
      label: optionalValueLabel("right", (value) => `${formatCompact(value)}d`),
      labelLayout: { hideOverlap: true },
      itemStyle: { borderRadius: 2 },
      data: visible.map((row) => ({
        value: Number(row.age_days ?? 0),
        itemStyle: { color: freshnessAgeColor(Number(row.age_days ?? 0)) }
      }))
    }]
  });
}

function freshnessAgeColor(ageDays: number) {
  return ageDays > STALE_THRESHOLD_DAYS ? reportChartPalette.amber : reportChartPalette.unknown;
}

function advancedRateTooltip(row: FreshnessRow) {
  const rate = metricValue(row, "advanced_rate");
  const advanced = metricValue(row, "advanced");
  const total = metricValue(row, "watermark_rate_runs");
  if (hasFreshnessValue(rate)) {
    return `${formatPercent(rate)} (${formatNumber(advanced)} advanced / ${formatNumber(total)} ETL runs)`;
  }
  if (hasFreshnessValue(total)) {
    return `unk (${formatNumber(total)} ETL runs; rate unavailable)`;
  }
  return "unk (ETL run count unavailable; rate unavailable)";
}

export function watermarkMovementTrendOption(rows: FreshnessRow[]): EChartsOption {
  if (!rows.length) return emptyChartOption("No watermark movement evidence.");
  const maxTotal = Math.max(1, ...rows.map(watermarkEnabledRuns));
  const statuses = [
    ["initialized", "Initialized"],
    ["advanced", "Advanced"],
    ["changed", "Changed"],
    ["empty", "Empty"],
    ["unchanged", "Unchanged"],
    ["unknown", "Unknown"]
  ] as const;
  return baseChartOption({
    tooltip: {
      trigger: "axis",
      confine: true,
      formatter: chartTooltip((params) => watermarkMovementHelpContent(params, rows))
    },
    legend: { show: false },
    grid: reportChartGrid({ left: 36, right: 40, top: 6, bottom: 5, containLabel: false }),
    xAxis: { type: "category", data: rows.map((row) => String(row.date ?? row.bucket ?? "unknown")), axisTick: { show: false }, axisLabel: { fontSize: 10, color: reportChartPalette.muted, hideOverlap: true } },
    yAxis: [
      { type: "value", axisLabel: { fontSize: 10, formatter: (value: number) => formatCompact(value) }, splitLine: { lineStyle: { color: reportChartPalette.grid } } },
      { type: "value", min: 0, max: 100, axisLabel: { fontSize: 10, formatter: (value: number) => `${value}%` }, splitLine: { show: false } }
    ],
    series: [
      ...statuses.map(([key, label]) => ({
        name: label,
        type: "bar" as const,
        stack: "movement",
        barMaxWidth: 28,
        label: optionalInsideShareLabel(
          rows,
          watermarkEnabledRuns,
          (value) => formatCompact(value),
          { maxTotal }
        ),
        labelLayout: { hideOverlap: true },
        itemStyle: { color: WATERMARK_MOVEMENT_COLORS[label], borderRadius: key === "unknown" ? [2, 2, 0, 0] : 0 },
        data: rows.map((row) => metricNumber(metricValue(row, key), 0) === 0 && !hasFreshnessValue(metricValue(row, key)) ? null : metricNumber(metricValue(row, key), 0))
      })),
      {
        name: "Advanced %",
        type: "line" as const,
        yAxisIndex: 1,
        connectNulls: false,
        showSymbol: false,
        showAllSymbol: false,
        symbol: "circle",
        symbolSize: 5,
        clip: false,
        z: 6,
        smooth: 0.2,
        color: reportChartPalette.blue,
        lineStyle: { width: 1.6, color: reportChartPalette.blue },
        itemStyle: { color: "#ffffff", borderColor: reportChartPalette.blue, borderWidth: 1.5 },
        data: rows.map((row) => hasFreshnessValue(row.advanced_rate) ? metricNumber(metricValue(row, "advanced_rate"), 0) : null)
      }
    ]
  });
}

export function watermarkMovementHelpContent(params: ChartTooltipParams | ChartTooltipParams[], rows: FreshnessRow[]) {
  const points = Array.isArray(params) ? params : [params];
  const row = rows[Number(points[0]?.dataIndex ?? 0)] ?? {};
  const statusKeyByLabel: Record<string, string> = { Initialized: "initialized", Advanced: "advanced", Changed: "changed", Empty: "empty", Unchanged: "unchanged", Unknown: "unknown" };
  const statusMetrics = points
    .filter((point) => point.seriesName !== "Advanced %")
    .map((point) => {
      const label = String(point.seriesName ?? "Unknown");
      return {
        ...chartMetric(label, formatNumber(metricValue(row, statusKeyByLabel[label] ?? ""))),
        valueColor: WATERMARK_MOVEMENT_COLORS[label as keyof typeof WATERMARK_MOVEMENT_COLORS],
      };
    });
  return {
    title: String(row.bucket ?? row.date ?? "unknown"),
    context: row.grain ? `Grain: ${row.grain}` : undefined,
    metrics: [
      ...statusMetrics,
      chartMetric("Configured watermark runs", formatNumber(metricValue(row, "watermark_enabled_runs"))),
      chartMetric("Total ETL runs", formatNumber(metricValue(row, "watermark_rate_runs"))),
      {
        ...chartMetric("Advanced rate", advancedRateTooltip(row)),
        valueColor: WATERMARK_MOVEMENT_COLORS["Advanced %"],
      },
    ]
  };
}

export function fillMissingWatermarkMovementTrendRows(
  rows: FreshnessRow[],
  filters: MonitoringFilters,
  dateRange: { min?: string | null; max?: string | null },
  timezoneName: string,
  reportEffectiveGrain = "day"
) {
  const effectiveGrain = String(rows.find((row) => row.grain)?.grain ?? reportEffectiveGrain ?? filters.grain ?? "day");
  const knownRows = rows.flatMap((row) => {
    const key = normalizeTrendBucketKey(row.bucket ?? row.date, effectiveGrain, timezoneName);
    return key ? [{ ...row, date: key, bucket: key }] : [];
  });
  const rowByKey = new Map(knownRows.map((row) => [String(row.bucket), row]));
  const bucketKeys = resolveTrendBucketKeys(
    filters,
    dateRange,
    timezoneName,
    Array.from(rowByKey.keys()),
    effectiveGrain
  );
  if (!bucketKeys.length) return knownRows;
  return bucketKeys.map((bucket) => ({
    ...createEmptyWatermarkMovementTrendRow(bucket, effectiveGrain),
    ...(rowByKey.get(bucket) ?? {})
  }));
}

function createEmptyWatermarkMovementTrendRow(bucket: string, grain: string): FreshnessRow {
  return {
    date: bucket,
    bucket,
    grain: normalizeTrendGrain(grain),
    initialized: 0,
    advanced: 0,
    changed: 0,
    empty: 0,
    unchanged: 0,
    unknown: 0,
    watermark_enabled_runs: 0,
    watermark_rate_runs: 0,
    advanced_rate: null,
    not_requested: 0,
    not_attempted: 0,
    no_candidate: 0,
    save_error: 0
  };
}

export function freshnessAgeDistributionOption(rows: FreshnessRow[]): EChartsOption {
  if (!rows.length) return emptyChartOption("No age distribution evidence.");
  return baseChartOption({
    tooltip: {
      trigger: "item",
      formatter: chartTooltip((params) => {
        const point = firstChartParam(params);
        const row = rows[Number(point?.dataIndex ?? 0)] ?? {};
        return { title: String(point?.name ?? row.bucket ?? "unknown"), metrics: [chartMetric("Dataflows", formatNumber(metricValue(row, "dataflows")))] };
      })
    },
    grid: reportChartGrid({ left: 34, right: 8, top: 18, bottom: 5, containLabel: false }),
    xAxis: { type: "category", data: rows.map((row) => String(row.bucket ?? "unknown")), axisTick: { show: false }, axisLabel: { fontSize: 10, color: reportChartPalette.muted } },
    yAxis: { type: "value", axisLabel: { fontSize: 10, formatter: (value: number) => formatCompact(value) }, splitLine: { lineStyle: { color: reportChartPalette.grid } } },
    series: [{
      name: "Dataflows",
      type: "bar",
      barMaxWidth: 34,
      label: optionalValueLabel("top", (value) => formatCompact(value)),
      labelLayout: { hideOverlap: true },
      itemStyle: { borderRadius: 2 },
      data: rows.map((row) => ({
        value: toApproximateNumber(metricValue(row, "dataflows")),
        itemStyle: { color: freshnessAgeBucketColor(String(row.bucket ?? "unknown")) }
      }))
    }]
  });
}

function freshnessAgeBucketColor(bucket: string) {
  const normalized = bucket.trim().toLowerCase();
  if (normalized === "≤1d") return reportChartPalette.teal;
  if (normalized === "1–3d") return reportChartPalette.blue;
  if (normalized === "3–7d") return reportChartPalette.pending;
  if (normalized === "7–30d") return reportChartPalette.amber;
  if (normalized === ">30d") return reportChartPalette.failed;
  return reportChartPalette.unknown;
}

export function watermarkCoverageByStageOption(rows: FreshnessRow[]): EChartsOption {
  const visible = [...rows]
    .filter((row) => Number(row.observed_dataflows ?? 0) > 0)
    .sort((left, right) => String(left.stage ?? "unknown").localeCompare(String(right.stage ?? "unknown")));
  const zoomConfig = horizontalBarDataZoom(visible.length);
  const barSizing = horizontalBarSeriesSizing(visible.length);
  if (!visible.length) return emptyChartOption("No watermark coverage evidence.");
  return baseChartOption({
    tooltip: {
      trigger: "item",
      triggerOn: "mousemove",
      axisPointer: { type: "none" },
      formatter: chartTooltip((params) => {
        const row = visible[Number(firstChartParam(params)?.dataIndex ?? 0)] ?? {};
        return {
          title: monitoringDimensionLabel(row.stage),
          metrics: [
            chartMetric("Observed dataflows", formatNumber(metricValue(row, "observed_dataflows"))),
            chartMetric("Enabled dataflows", formatNumber(metricValue(row, "watermark_enabled_dataflows")), "success"),
            chartMetric("Not configured dataflows", formatNumber(notConfiguredDataflows(row))),
            chartMetric("Coverage", formatPercent(metricValue(row, "coverage_rate"))),
          ]
        };
      })
    },
    legend: { show: false },
    grid: fixedHorizontalBarGrid(96, Boolean(zoomConfig), { top: 6 }),
    xAxis: { type: "value", max: 100, axisLabel: { fontSize: 10, formatter: (value: number) => `${value}%` }, splitLine: { lineStyle: { color: reportChartPalette.grid } } },
    yAxis: fixedHorizontalCategoryAxis(
      visible.map((row) => monitoringDimensionLabel(row.stage)),
      96,
      { axisLine: { show: true, lineStyle: { color: "#d9e1ea", width: 1 } } }
    ),
    dataZoom: zoomConfig,
    series: [
      {
        name: "Enabled",
        type: "bar",
        stack: "coverage",
        ...barSizing,
        label: optionalInsideShareLabel(visible, () => 100, (value) => `${formatCompact(value)}%`),
        labelLayout: { hideOverlap: true },
        itemStyle: { color: reportChartPalette.success, borderRadius: [2, 0, 0, 2] },
        data: visible.map((row) => toApproximateNumber(metricValue(row, "coverage_rate")))
      },
      {
        name: "Not configured",
        type: "bar",
        stack: "coverage",
        ...barSizing,
        label: optionalInsideShareLabel(visible, () => 100, (value) => `${formatCompact(value)}%`),
        labelLayout: { hideOverlap: true },
        itemStyle: { color: reportChartPalette.unknown, borderRadius: [0, 2, 2, 0] },
        data: visible.map((row) => {
          const coverage = toApproximateNumber(metricValue(row, "coverage_rate"));
          return coverage === null ? null : Math.max(0, 100 - coverage);
        })
      }
    ]
  });
}

export function readBoundaryAdjustmentOption(rows: FreshnessRow[]): EChartsOption {
  const visible = readBoundaryAdjustmentChartRows(rows);
  const zoomConfig = horizontalBarDataZoom(visible.length);
  const barSizing = horizontalBarSeriesSizing(visible.length);
  if (!visible.length) {
    return emptyChartOption(rows.length ? "No adjusted read-boundary evidence." : "No read-boundary evidence.");
  }
  const maxAdjusted = Math.max(1, ...visible.map((row) => readBoundaryCount(row, "adjusted")));
  return baseChartOption({
    tooltip: {
      trigger: "item",
      triggerOn: "mousemove",
      axisPointer: { type: "none" },
      formatter: chartTooltip((params) => {
        const row = visible[Number(firstChartParam(params)?.dataIndex ?? 0)] ?? {};
        return {
          title: String(row.dataflow_name ?? row.dataflow_id ?? "unknown"),
          metrics: [
            chartMetric("Source", String(row.source_name ?? "unknown")),
            chartMetric("Adjusted runs", formatNumber(readBoundaryCount(row, "adjusted")), "warning"),
          ]
        };
      }),
    },
    legend: { show: false },
    grid: fixedHorizontalBarGrid(132, Boolean(zoomConfig), { top: 6 }),
    xAxis: {
      type: "value",
      min: 0,
      max: chartLabelHeadroomMax(maxAdjusted),
      axisLabel: { fontSize: 10, formatter: (value: number) => formatCompact(value) },
      splitLine: { lineStyle: { color: reportChartPalette.grid } },
    },
    yAxis: fixedHorizontalCategoryAxis(
      visible.map((row) => String(row.dataflow_name ?? row.dataflow_id ?? "unknown")),
      132,
      { axisLine: { show: true, lineStyle: { color: "#d9e1ea", width: 1 } } },
    ),
    dataZoom: zoomConfig,
    series: [{
      name: "Adjusted",
      type: "bar" as const,
      ...barSizing,
      label: optionalInsideShareLabel(visible, (row) => readBoundaryCount(row, "adjusted"), (value) => formatCompact(value), { maxTotal: maxAdjusted }),
      labelLayout: { hideOverlap: true },
      itemStyle: { color: reportChartPalette.amber, borderRadius: [2, 2, 0, 0] },
      data: visible.map((row) => readBoundaryCount(row, "adjusted")),
    }],
  });
}

export function readBoundaryAdjustmentChartRows(rows: FreshnessRow[]): FreshnessRow[] {
  return [...rows]
    .filter((row) => readBoundaryCount(row, "adjusted") > 0)
    .sort((left, right) => {
      const adjustedDelta = readBoundaryCount(right, "adjusted") - readBoundaryCount(left, "adjusted");
      if (adjustedDelta !== 0) return adjustedDelta;
      return String(left.dataflow_name ?? left.dataflow_id ?? "unknown")
        .localeCompare(String(right.dataflow_name ?? right.dataflow_id ?? "unknown"));
    });
}

function readBoundaryCount(row: FreshnessRow, state: string) {
  const field = `adjustment_${state}_runs`;
  const explicit = metricValue(row, field);
  if (hasFreshnessValue(explicit)) return metricNumber(explicit, 0);
  return String(row.adjustment_state ?? "unknown").toLowerCase() === state ? 1 : 0;
}

export function skippedStreakDistributionOption(rows: FreshnessRow[]): EChartsOption {
  if (!rows.length) return emptyChartOption("No consecutive skipped dataflow streaks.");
  const maxValue = Math.max(1, ...rows.map((row) => Number(row.dataflows ?? 0)));
  return baseChartOption({
    tooltip: {
      trigger: "item",
      formatter: chartTooltip((params) => {
        const point = firstChartParam(params);
        const row = rows[Number(point?.dataIndex ?? 0)] ?? {};
        return { title: String(point?.name ?? row.bucket ?? "unknown"), metrics: [chartMetric("Dataflows", formatNumber(metricValue(row, "dataflows")))] };
      })
    },
    grid: reportChartGrid({ left: 30, right: 8, top: 18, bottom: 5, containLabel: false }),
    xAxis: { type: "category", data: rows.map((row) => String(row.bucket ?? "unknown")), axisTick: { show: false }, axisLabel: { fontSize: 10, color: reportChartPalette.muted } },
    yAxis: { type: "value", max: chartLabelHeadroomMax(maxValue), axisLabel: { fontSize: 10, formatter: (value: number) => formatCompact(value) }, splitLine: { lineStyle: { color: reportChartPalette.grid } } },
    series: [{
      name: "Dataflows",
      type: "bar",
      barMaxWidth: 34,
      label: optionalValueLabel("top", (value) => formatCompact(value)),
      labelLayout: { hideOverlap: true },
      itemStyle: { color: reportChartPalette.skipped, borderRadius: 2 },
      data: rows.map((row) => toApproximateNumber(metricValue(row, "dataflows")))
    }]
  });
}

function optionalValueLabel(
  position: "top" | "right",
  formatter: (value: number) => string
) {
  return {
    show: true,
    position,
    color: reportChartPalette.text,
    fontSize: 10,
    formatter: (params: { value?: unknown }) => {
      const value = Number(params.value ?? 0);
      if (!Number.isFinite(value) || value <= 0) return "";
      return formatter(value);
    }
  };
}

function optionalInsideShareLabel(
  rows: FreshnessRow[],
  totalForRow: (row: FreshnessRow) => number,
  formatter: (value: number) => string,
  options: { minShare?: number; minAxisShare?: number; maxTotal?: number } = {}
) {
  const minShare = options.minShare ?? 15;
  const minAxisShare = options.minAxisShare ?? 7;
  const maxTotal = Math.max(1, options.maxTotal ?? Math.max(1, ...rows.map(totalForRow)));
  return {
    show: true,
    position: "inside" as const,
    color: "#ffffff",
    fontSize: 10,
    formatter: (params: { value?: unknown; dataIndex?: number }) => {
      const value = Number(params.value ?? 0);
      const row = rows[Number(params.dataIndex ?? 0)] ?? {};
      const total = totalForRow(row);
      if (!Number.isFinite(value) || value <= 0 || !Number.isFinite(total) || total <= 0) return "";
      if ((value / total) * 100 < minShare) return "";
      if ((value / maxTotal) * 100 < minAxisShare) return "";
      return formatter(value);
    }
  };
}

function chartLabelHeadroomMax(maxValue: number) {
  return Math.ceil(Math.max(1, maxValue) * 1.18);
}

function emptyChartOption(message: string): EChartsOption {
  return baseChartOption({
    title: { text: message, left: "center", top: "middle", textStyle: { color: reportChartPalette.muted, fontSize: 12, fontWeight: 500 } },
    xAxis: { show: false },
    yAxis: { show: false },
    series: []
  });
}

function formatAgeSeconds(value: number) {
  if (!Number.isFinite(value)) return "-";
  if (value < 60) return `${formatAgeNumber(Math.max(0, value))}s`;
  const minutes = value / 60;
  if (minutes < 60) return `${formatAgeNumber(minutes)}m`;
  const hours = minutes / 60;
  if (hours < 24) return `${formatAgeNumber(hours)}h`;
  return `${formatAgeNumber(value / 86400)}d`;
}

function notConfiguredDataflows(row: FreshnessRow) {
  return metricValue(row, "not_configured_dataflows");
}

function watermarkEnabledRuns(row: FreshnessRow) {
  return Number(row.watermark_enabled_runs ?? (
    metricNumber(metricValue(row, "initialized"))
    + metricNumber(metricValue(row, "advanced"))
    + metricNumber(metricValue(row, "changed"))
    + metricNumber(metricValue(row, "empty"))
    + metricNumber(metricValue(row, "unchanged"))
    + metricNumber(metricValue(row, "unknown"))
  ));
}
