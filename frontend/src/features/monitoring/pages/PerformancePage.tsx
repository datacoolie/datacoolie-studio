import { Help } from "../../../shared/components/Help";
import { monitoringDimensionLabel, monitoringRawDimensionLabel } from "../monitoringDimensions";
import { memo, useEffect, useMemo, useState, type CSSProperties, type ReactNode } from "react";
import type { EChartsOption } from "echarts";
import type { MonitoringRecord, MonitoringReport } from "../../../shared/api/domainTypes";
import type { TableSort } from "../../../shared/components/DataTable";
import type { MonitoringFilters } from "../monitoringFilters";
import { CompactNumberValue, DataTable, DetailMetric, DurationHeadline, HealthStripCard, ReportChart, ReportPanel, RuntimePhaseLegend, RuntimePhaseContribution, formatTimestampForDisplay, TablePager, bottomAnchoredValueXAxis, fixedHorizontalBarGrid, fixedHorizontalAxisLabel, durationIntent, durationPercentilesDetail, formatBytes, formatCompact, formatDurationMetric, formatTooltipBytes, formatTooltipNumber, formatPercent, formatSeconds, formatSecondsSingleDecimal, runtimePhaseContributionTooltip, fillMissingTrendDates, horizontalBarDataZoom, metricNumber, metricValue, monitoringTimezone, num, reportChartPalette, reportChartGrid } from "../components/monitoringPresentation";
import { DurationDistributionBoxPlot } from "../components/monitoringDurationPresentation";
import { scatterObservations } from "../components/monitoringObservation";
import { monitoringPhaseColors } from "./FailurePagePresentation";
import { PerformanceRegistryTable } from "./PerformanceRegistryTable";
import { baseChartOption } from "../ReportChart";
import {
  defaultPerformanceEfficiencyScope,
  filterPerformanceEfficiencyRows,
  performanceEfficiencyCoverage,
  performancePressureIntent,
  type PerformanceEfficiencyScope
} from "../performancePageModel";
import { chartDurationMetric, chartMetric, chartTooltip, firstChartParam } from "../components/chartTooltip";

type EfficiencyScaleMode = "linear" | "log";
const PHASE_COLORS: Record<string, string> = {
  ...monitoringPhaseColors,
  unknown: reportChartPalette.muted
};
const PERFORMANCE_TREND_COLORS = {
  p50: "#2563eb",
  p95: "#7c3aed",
  signals: "#d89b42",
} as const;

type ReportState = { loading: boolean; error: string | null; retry: () => void };

export function PerformancePage({
  report,
  rows,
  totalRows,
  loading,
  sort,
  onSort,
  limit,
  offset,
  onPageChange,
  onPageSizeChange,
  filters,
  onInspect,
  timezoneName,
  reportState,
  scrollResetKey,
  tablePending = false,
  tableError = null,
}: {
  report: MonitoringReport | null;
  rows: MonitoringRecord[];
  totalRows: number;
  loading: boolean;
  sort: TableSort;
  onSort: (sort: TableSort) => void;
  limit: number;
  offset: number;
  onPageChange: (offset: number) => void;
  onPageSizeChange: (limit: number) => void;
  filters: MonitoringFilters;
  onInspect?: (row: MonitoringRecord) => void;
  timezoneName?: string | null;
  reportState?: ReportState;
  scrollResetKey?: string;
  tablePending?: boolean;
  tableError?: string | null;
}) {
  const resolvedTimezoneName = timezoneName !== undefined ? timezoneName : report ? monitoringTimezone(report) : null;
  const hasTableResponse = !tablePending && !tableError;
  const registryState = tableError ? "Dataflows unavailable" : tablePending ? "Loading dataflows…" : null;
  return (
    <div className="monitoring-page monitoring-performance-report">
      {report ? <PerformanceReportContent part="health" report={report} filters={filters} timezoneName={resolvedTimezoneName ?? "UTC"} /> : <PerformanceReportPlaceholder part="health" reportState={reportState} />}
      <div className="monitoring-performance-content monitoring-equal-rows-3">
        {report ? <PerformanceReportContent part="charts" report={report} filters={filters} timezoneName={resolvedTimezoneName ?? "UTC"} /> : <PerformanceReportPlaceholder part="charts" reportState={reportState} />}
        <ReportPanel
          title="Dataflow performance registry"
          subtitle={hasTableResponse ? <><CompactNumberValue value={totalRows} /> dataflows · current filters</> : undefined}
          className="monitoring-performance-runs-panel"
          titleTooltip="One row per dataflow. Performance metrics are aggregated across canonical runs in the current filters; click a row to inspect all facets."
          headerAction={hasTableResponse ? <TablePager limit={limit} offset={offset} loadedRows={rows.length} totalRows={totalRows} loading={loading} onPageChange={onPageChange} onPageSizeChange={onPageSizeChange} /> : undefined}
        >
          {registryState ? <div className="monitoring-registry-table-state" role="status">{registryState}</div> : <PerformanceRegistryTable rows={rows} sort={sort} onSort={onSort} onInspect={onInspect} timezoneName={resolvedTimezoneName} scrollResetKey={scrollResetKey} />}
        </ReportPanel>
      </div>
    </div>
  );
}

const PerformanceReportContent = memo(function PerformanceReportContent({ part, report, filters, timezoneName }: { part: "health" | "charts"; report: MonitoringReport; filters: MonitoringFilters; timezoneName: string }) {
  const kpis = report.performance.kpis ?? {};
  const durationStats = {
    avg_duration_seconds: metricValue(kpis, "avg_duration_seconds"),
    p50_duration_seconds: metricValue(kpis, "p50_duration_seconds"),
    q3_duration_seconds: metricValue(kpis, "p75_duration_seconds"),
    p95_duration_seconds: metricValue(kpis, "p95_duration_seconds"),
    p99_duration_seconds: metricValue(kpis, "p99_duration_seconds"),
    max_duration_seconds: metricValue(kpis, "max_duration_seconds")
  };
  const workloadEfficiencyRows = useMemo(() => (report.performance.workload_efficiency_points ?? []).map(normalizeEfficiencyPoint), [report.performance.workload_efficiency_points]);
  const [efficiencyScope, setEfficiencyScope] = useState<PerformanceEfficiencyScope>(() => defaultPerformanceEfficiencyScope(workloadEfficiencyRows));
  const scopedEfficiencyRows = useMemo(() => filterPerformanceEfficiencyRows(workloadEfficiencyRows, efficiencyScope), [workloadEfficiencyRows, efficiencyScope]);
  const efficiencyObservations = useMemo(() => performanceScatterObservations(scopedEfficiencyRows, efficiencyScope), [scopedEfficiencyRows, efficiencyScope]);
  const efficiencyPopulationCoverage = useMemo(() => performanceEfficiencyCoverage(kpis, efficiencyScope), [kpis, efficiencyScope]);
  const efficiencyCoverageLabel = efficiencyPopulationCoverage
    ? `${formatCompact(efficiencyObservations.points.length)} plotted · ${formatCompact(efficiencyPopulationCoverage.measured)} / ${formatCompact(efficiencyPopulationCoverage.eligible)} measured`
    : efficiencyObservations.compactCoverage;
  const efficiencyCoverageTooltip = efficiencyPopulationCoverage
    ? `Showing ${efficiencyObservations.points.length} sampled points (up to 200 per scope). ${formatTooltipNumber(efficiencyPopulationCoverage.measured)} / ${formatTooltipNumber(efficiencyPopulationCoverage.eligible)} eligible runs have both the workload coordinate and duration measured; ${formatTooltipNumber(efficiencyPopulationCoverage.eligible - efficiencyPopulationCoverage.measured)} are missing at least one coordinate.`
    : efficiencyObservations.coverage;
  const [efficiencyScale, setEfficiencyScale] = useState<EfficiencyScaleMode>(() => defaultEfficiencyScale(scopedEfficiencyRows, efficiencyScope));
  useEffect(() => { setEfficiencyScope(defaultPerformanceEfficiencyScope(workloadEfficiencyRows)); }, [workloadEfficiencyRows]);
  useEffect(() => { setEfficiencyScale(defaultEfficiencyScale(scopedEfficiencyRows, efficiencyScope)); }, [scopedEfficiencyRows, efficiencyScope]);
  const phaseCostChartRows = useMemo(() => phaseCostRows(report.performance.phase_contribution_by_stage_operation ?? []), [report.performance.phase_contribution_by_stage_operation]);
  const performanceTrendChartOption = useMemo(
    () => performanceTrendOption(report.performance.performance_trend ?? [], filters, report.summary.date_range, timezoneName, report.summary.effective_grain ?? undefined),
    [report.performance.performance_trend, filters, report.summary.date_range, report.summary.effective_grain, timezoneName]
  );
  const slowestProfilesChartOption = useMemo(() => slowestDataflowProfilesOption(report.performance.slowest_dataflow_profiles ?? []), [report.performance.slowest_dataflow_profiles]);
  const workloadEfficiencyChartOption = useMemo(
    () => workloadEfficiencyOption(scopedEfficiencyRows, efficiencyScale, efficiencyScope, efficiencyCoverageLabel),
    [scopedEfficiencyRows, efficiencyScale, efficiencyScope, efficiencyCoverageLabel]
  );
  const pressureRatioValue = metricValue(kpis, "duration_pressure_ratio");
  const pressureRatio = metricNumber(pressureRatioValue);
  const pressureIntent = performancePressureIntent(pressureRatio, metricNumber(metricValue(kpis, "p95_duration_seconds")));
  const candidateCountValue = metricValue(kpis, "optimization_candidate_count");
  const candidateCount = metricNumber(candidateCountValue);
  const durationMeasured = metricValue(kpis, "duration_runs_measured");
  const durationEligible = metricValue(kpis, "duration_runs_eligible");
  const durationCoverage = `${formatTooltipNumber(durationMeasured)} / ${formatTooltipNumber(durationEligible)} succeeded/failed runs with duration in the current filters.`;
  const partialDuration = durationMeasured != null && durationEligible != null && metricNumber(durationMeasured) < metricNumber(durationEligible);
  const healthContent = (
      <section className="overview-health-strip monitoring-performance-health-strip">
        <HealthStripCard label="Performance pressure" value={pressureRatio > 0 ? <CompactNumberValue value={pressureRatioValue} suffix="x" /> : "-"} detail={<span className="health-duration-detail"><DetailMetric label="outliers" value={<CompactNumberValue value={metricValue(kpis, "duration_outlier_count")} />} tone={metricNumber(metricValue(kpis, "duration_outlier_count")) ? "amber" : "neutral"} labelFirst /><span className="separator"> · </span><DetailMetric label="max" value={<Help as="span" title={formatTooltipNumber(metricValue(kpis, "max_duration_seconds"))}>{formatDurationMetric(metricValue(kpis, "max_duration_seconds"))}</Help>} tone="purple" labelFirst /></span>} intent={pressureIntent} accent="intent" title="Performance pressure = P95 duration / P50 duration. Warning when ratio is high and P95 is materially slow." />
        <HealthStripCard
          label="Dataflow duration"
          value={<PerformanceDurationValue kpis={kpis} timezoneName={timezoneName}><DurationHeadline avgSeconds={durationStats.avg_duration_seconds} p50Seconds={durationStats.p50_duration_seconds} /></PerformanceDurationValue>}
          detail={durationPercentilesDetail(durationStats)}
          intent={durationIntent(durationStats, metricNumber(durationStats.avg_duration_seconds), metricNumber(durationStats.p95_duration_seconds))}
          accent="intent"
          title={`Duration percentiles include succeeded/failed runs with duration evidence in the current filters.\n${durationCoverage}`}
          className="overview-health-card-duration"
        />
        <HealthStripCard
          label="Total run time"
          value={<Help as="span" title={`${formatTooltipNumber(metricValue(kpis, "total_run_time_seconds"))} seconds`}>{formatDurationMetric(metricValue(kpis, "total_run_time_seconds"))}</Help>}
          detail={<span><CompactNumberValue value={durationMeasured} /> runs with duration{partialDuration ? " · observed" : ""}</span>}
          intent="neutral"
          title={`Sum of recorded root run durations in the current filters. Missing durations are excluded; recorded zero durations count. Concurrent runs are added together, so this is accumulated run time, not elapsed wall-clock time.\n${durationCoverage}`}
        />
        <HealthStripCard label="Runs with performance signals" value={<CompactNumberValue value={candidateCountValue} />} detail={<span className="health-rate-detail"><DetailMetric label="small" value={<CompactNumberValue value={metricValue(kpis, "slow_small_workload_count")} />} tone="amber" labelFirst /><span className="separator"> · </span><DetailMetric label="maint" value={<CompactNumberValue value={metricValue(kpis, "slow_small_maintenance_count")} />} tone="neutral" labelFirst /><span className="separator"> · </span><DetailMetric label="overhead" value={<CompactNumberValue value={metricValue(kpis, "high_overhead_count")} />} tone="purple" labelFirst /><span className="separator"> · </span><DetailMetric label="skew" value={<CompactNumberValue value={metricValue(kpis, "phase_skew_count")} />} tone="blue" labelFirst /></span>} intent={candidateCount ? "warning" : "neutral"} accent={candidateCount ? "warning" : "neutral"} title={`Unique succeeded/failed runs matching at least one performance signal in the current filters. Signals suggest runs to investigate; they do not prove a performance problem. Rule-match counts can overlap and must not be added: slow-small pipeline workload, slow-small maintenance workload, high overhead, or phase skew.\nComplete phase timing: ${formatTooltipNumber(metricValue(kpis, "phase_runs_measured"))} / ${formatTooltipNumber(metricValue(kpis, "phase_runs_eligible"))} runs. Incomplete timing can leave signals undetected.`} />
      </section>
  );
  const chartContent = (
      <>
        <section className="monitoring-performance-primary-grid">
          <ReportPanel title="Stage duration distribution" subtitle="P50, P95 and outliers" titleTooltip="Box plot of dataflow durations grouped by stage. Sorts by P95 duration and shows IQR outliers."><DurationDistributionBoxPlot rows={report.performance.duration_distribution_by_stage ?? []} labelKey="stage" emptyText="No stage duration evidence in current filters." /></ReportPanel>
          <ReportPanel title="Phase cost by stage" titleTooltip={runtimePhaseContributionTooltip("operation type and stage")} headerAction={<RuntimePhaseLegend />}><RuntimePhaseContribution rows={phaseCostChartRows} labelKey="context" emptyText="No phase duration evidence in current filters." showLegend={false} /></ReportPanel>
          <ReportPanel title="Performance trend" className="monitoring-performance-trend-panel" titleTooltip="Executable dataflow duration and unique runs with performance signals over the selected time range. Empty buckets remain visible." headerAction={<PerformanceTrendLegend />}><ReportChart option={performanceTrendChartOption} height="100%" /></ReportPanel>
        </section>
        <section className="monitoring-performance-secondary-grid">
          <ReportPanel title="Workload efficiency map" subtitle={<Help as="span" title={efficiencyCoverageTooltip}>{efficiencyCoverageLabel}</Help>} titleTooltip="Each point is a sampled executable dataflow run. Up to 200 measured points are selected per scope, prioritizing performance signals and retaining representative baseline runs. Pipeline view uses rows processed; Maintenance uses bytes processed. Y = duration, color = bottleneck phase, and size = signal priority." headerAction={<div className="performance-efficiency-controls"><EfficiencyScopeToggle value={efficiencyScope} onChange={setEfficiencyScope} /><EfficiencyScaleToggle value={efficiencyScale} onChange={setEfficiencyScale} /></div>}><ReportChart option={workloadEfficiencyChartOption} height="100%" /></ReportPanel>
          <ReportPanel title="Slowest dataflow profiles" subtitle="P95 duration by dataflow" titleTooltip="Groups runs by dataflow_id and ranks by P95 duration. Color indicates the dominant runtime bottleneck phase."><ReportChart option={slowestProfilesChartOption} height="100%" wheelDataZoomStep={1} /></ReportPanel>
          <ReportPanel title="Runtime context profile" subtitle="platform / engine / provider" titleTooltip="Compares runtime context groups by run count, success rate, duration percentiles, throughput, and runs with performance signals."><RuntimeContextProfileTable rows={report.performance.runtime_context_profiles ?? []} /></ReportPanel>
        </section>
      </>
  );
  return part === "health" ? healthContent : chartContent;
});

function PerformanceReportPlaceholder({ part, reportState }: { part: "health" | "charts"; reportState?: ReportState }) {
  const error = reportState?.error;
  if (part === "health") {
    return <>
      <section className="overview-health-strip monitoring-performance-health-strip monitoring-report-placeholder-strip" aria-busy={!error}>{Array.from({ length: 4 }, (_, index) => <div className="monitoring-report-health-placeholder" key={index}><span>{error ? "Unavailable" : "Loading…"}</span></div>)}</section>
      {error ? <div className="monitoring-report-state is-error" role="alert"><span>Could not load performance report: {error}</span>{reportState?.retry ? <button type="button" onClick={reportState.retry}>Retry</button> : null}</div> : null}
    </>;
  }
  return <>
    <section className="monitoring-performance-primary-grid">{["Stage duration distribution", "Phase cost by stage", "Performance trend"].map((title) => <ReportPanel key={title} title={title}><div className="monitoring-report-chart-placeholder" aria-hidden="true" /></ReportPanel>)}</section>
    <section className="monitoring-performance-secondary-grid">{["Workload efficiency map", "Slowest dataflow profiles", "Runtime context profile"].map((title) => <ReportPanel key={title} title={title}><div className="monitoring-report-chart-placeholder" aria-hidden="true" /></ReportPanel>)}</section>
  </>;
}

function EfficiencyScaleToggle({
  value,
  onChange
}: {
  value: EfficiencyScaleMode;
  onChange: (value: EfficiencyScaleMode) => void;
}) {
  return (
    <div
      className="segmented-control performance-scale-toggle"
      role="group"
      aria-label="Workload efficiency scale"
    >
      <button
        type="button"
        className={value === "linear" ? "active" : ""}
        aria-pressed={value === "linear"}
        onClick={() => onChange("linear")}
      >
        Linear
      </button>
      <button
        type="button"
        className={value === "log" ? "active" : ""}
        aria-pressed={value === "log"}
        onClick={() => onChange("log")}
      >
        Log
      </button>
    </div>
  );
}

function EfficiencyScopeToggle({
  value,
  onChange
}: {
  value: PerformanceEfficiencyScope;
  onChange: (value: PerformanceEfficiencyScope) => void;
}) {
  return (
    <div className="segmented-control performance-scope-toggle" role="group" aria-label="Workload efficiency run type">
      {(["etl", "maintenance"] as const).map((scope) => (
        <button
          key={scope}
          type="button"
          className={value === scope ? "active" : ""}
          aria-pressed={value === scope}
          onClick={() => onChange(scope)}
        >
          {scope === "etl" ? "Pipeline" : "Maint."}
        </button>
      ))}
    </div>
  );
}

function RuntimeContextProfileTable({ rows }: { rows: Array<Record<string, string | number | null>> }) {
  const columns = useMemo(() => [
    { key: "engine_name", label: "Runtime context", sortable: true, width: 132, maxWidth: 190, fillPriority: "normal" as const, render: (row: Record<string, string | number | null>) => <RuntimeContextCell row={row} /> },
    { key: "run_count", label: "Runs / health", sortable: true, width: 116, maxWidth: 150, fillPriority: "normal" as const, render: (row: Record<string, string | number | null>) => <RuntimeHealthCell row={row} /> },
    { key: "p50_duration_seconds", label: "Performance", sortable: true, width: 188, maxWidth: 340, fillPriority: "normal" as const, render: (row: Record<string, string | number | null>) => <RuntimePerformanceCell row={row} /> }
  ], []);
  return (
    <DataTable<Record<string, string | number | null>>
      rows={rows}
      compactNumbers
      compactNumberStyle="infix"
      columns={columns}
      maxRows={rows.length}
      className="performance-runtime-context-table"
      fixedLayout
    />
  );
}

function RuntimeContextCell({ row }: { row: Record<string, string | number | null> }) {
  const engine = monitoringDimensionLabel(row.engine_name);
  const provider = monitoringDimensionLabel(row.metadata_provider_name);
  const platform = monitoringDimensionLabel(row.platform_name);
  const primary = `${compactRuntimeContextValue(engine, "engine")} · ${compactRuntimeContextValue(provider, "provider")}`;
  const secondary = compactRuntimeContextValue(platform, "platform");
  const title = `${engine} · ${provider} · ${platform}`;
  return (
    <Help as="span" className="runtime-context-cell" title={title}>
      <strong>{primary}</strong>
      <small>{secondary}</small>
    </Help>
  );
}

function compactRuntimeContextValue(value: string, suffix: "engine" | "provider" | "platform") {
  const compact = value.replace(new RegExp(`\\s*${suffix}$`, "i"), "").trim();
  return compact || value;
}

function RuntimePerformanceCell({ row }: { row: Record<string, string | number | null> }) {
  const candidateCountValue = metricValue(row, "performance_candidate_count") ?? metricValue(row, "candidate_count");
  const candidateCount = metricNumber(candidateCountValue);
  const p50Value = metricValue(row, "performance_p50_duration_seconds") ?? metricValue(row, "p50_duration_seconds");
  const p95Value = metricValue(row, "performance_p95_duration_seconds") ?? metricValue(row, "p95_duration_seconds");
  const rowsReadPerSecondValue = metricValue(row, "performance_rows_read_per_second") ?? metricValue(row, "rows_read_per_second");
  return (
    <span className="runtime-context-performance-cell">
      <span>
        <Help as="span" className="runtime-context-duration-p50" title={`P50 duration: ${formatTooltipNumber(p50Value)}`}><b>P50</b> {formatSecondsSingleDecimal(metricNumber(p50Value))}</Help>
        <em>/</em>
        <Help as="span" className="runtime-context-duration-p95" title={`P95 duration: ${formatTooltipNumber(p95Value)}`}><b>P95</b> {formatSecondsSingleDecimal(metricNumber(p95Value))}</Help>
      </span>
      <small>
        <span className="runtime-context-throughput"><CompactNumberValue value={rowsReadPerSecondValue} suffix="/s" /></span>
        <em>·</em>
        <span className={candidateCount ? "runtime-context-candidates-warning" : "runtime-context-candidates-neutral"}>
          <CompactNumberValue value={candidateCountValue} /> runs with signals
        </span>
      </small>
    </span>
  );
}

function RuntimeHealthCell({ row }: { row: Record<string, string | number | null> }) {
  const successRate = num(row, "success_rate");
  const failed = num(row, "failed");
  const successTone = successRate >= 95 ? "good" : successRate >= 90 ? "warning" : "bad";
  return (
    <span className="runtime-context-metric-pair">
      <CompactNumberValue value={num(row, "run_count")} />
      <small>
        <span className={`runtime-context-success-${successTone}`}>{formatPercent(successRate)}</span>
        <em>·</em>
        <span className={failed ? "runtime-context-failed-bad" : "runtime-context-failed-neutral"}>
          <CompactNumberValue value={failed} /> failed
        </span>
      </small>
    </span>
  );
}

function PerformanceDurationValue({ kpis, timezoneName, children }: { kpis: Record<string, unknown>; timezoneName: string; children: ReactNode }) {
  const name = String(kpis.slowest_run_dataflow_name ?? kpis.slowest_run_dataflow_id ?? "-");
  const stage = monitoringDimensionLabel(kpis.slowest_run_stage);
  const operation = monitoringDimensionLabel(kpis.slowest_run_operation_type);
  const content = {
    title: "Duration and slowest run",
    context: `${formatTooltipNumber(metricValue(kpis, "duration_runs_measured"))} / ${formatTooltipNumber(metricValue(kpis, "duration_runs_eligible"))} succeeded/failed runs with duration`,
    metrics: [
      {label: "Slowest", value: chartDurationMetric(metricValue(kpis, "slowest_run_duration_seconds"))},
      {label: "Dataflow", value: name},
      {label: "Start", value: formatTimestampForDisplay(String(kpis.slowest_run_start_time ?? ""), timezoneName)},
      {label: "End", value: formatTimestampForDisplay(String(kpis.slowest_run_end_time ?? ""), timezoneName)},
      {label: "Stage", value: stage}, {label: "Operation", value: operation},
      {label: "Status", value: String(kpis.slowest_run_status ?? "-")},
    ],
  };
  return <Help className="performance-duration-value" content={content}>{children}</Help>;
}

function PerformanceTrendLegend() {
  return (
    <div className="performance-trend-legend" aria-label="Performance trend legend">
      <span className="performance-trend-p50" style={{ "--performance-trend-color": PERFORMANCE_TREND_COLORS.p50 } as CSSProperties}>P50</span>
      <span className="performance-trend-p95" style={{ "--performance-trend-color": PERFORMANCE_TREND_COLORS.p95 } as CSSProperties}>P95</span>
      <span className="performance-trend-candidates" style={{ "--performance-trend-color": PERFORMANCE_TREND_COLORS.signals } as CSSProperties}>Runs with signals</span>
    </div>
  );
}

function performanceTrendOption(
  rows: Array<Record<string, string | number | null>>,
  filters: MonitoringFilters,
  dateRange: { min?: string | null; max?: string | null },
  timezoneName: string,
  effectiveGrain?: string
): EChartsOption {
  const visible = fillMissingTrendDates(rows, filters, dateRange, timezoneName, effectiveGrain);
  if (!visible.length) return emptyChartOption("No performance trend evidence.");
  const labels = visible.map((row) => String(row.bucket ?? row.date ?? ""));
  const hasRuns = (row: Record<string, string | number | null>) => num(row, "run_count") > 0;
  return baseChartOption({
    grid: reportChartGrid({ left: 42, right: 42, top: 5, bottom: 5, containLabel: false }),
    tooltip: {
      trigger: "axis",
      formatter: chartTooltip((params) => {
        const first = firstChartParam(params);
        const row = visible[Number(first?.dataIndex ?? 0)] ?? {};
        const runCountValue = metricValue(row, "run_count");
        const runCount = metricNumber(runCountValue);
        return {
          title: String(row.bucket ?? row.date ?? "unknown"),
          context: `Timezone: ${timezoneName}`,
          metrics: [
            chartMetric("Runs", formatTooltipNumber(runCountValue)),
            ...(runCount ? [
              { ...chartMetric("P50", `${formatTooltipNumber(metricValue(row, "p50_duration_seconds"))} s`), valueColor: PERFORMANCE_TREND_COLORS.p50 },
              { ...chartMetric("P95", `${formatTooltipNumber(metricValue(row, "p95_duration_seconds"))} s`), valueColor: PERFORMANCE_TREND_COLORS.p95 },
            ] : []),
            { ...chartMetric("Runs with performance signals", formatTooltipNumber(metricValue(row, "candidate_count"))), valueColor: PERFORMANCE_TREND_COLORS.signals },
          ],
          note: runCount ? undefined : "No executable runs",
        };
      })
    },
    xAxis: {
      type: "category",
      data: labels,
      boundaryGap: true,
      axisTick: { show: false },
      axisLabel: { fontSize: 10, hideOverlap: true },
      axisLine: { lineStyle: { color: reportChartPalette.grid } }
    },
    yAxis: [
      {
        type: "value",
        min: 0,
        axisLabel: { fontSize: 10, formatter: (value: number) => formatSeconds(value) },
        splitLine: { lineStyle: { color: reportChartPalette.grid } }
      },
      {
        type: "value",
        min: 0,
        minInterval: 1,
        axisLabel: { fontSize: 10, formatter: (value: number) => formatCompact(value) },
        splitLine: { show: false }
      }
    ],
    series: [
      {
        name: "Runs with signals",
        type: "bar",
        yAxisIndex: 1,
        barMaxWidth: 14,
        itemStyle: { color: PERFORMANCE_TREND_COLORS.signals, opacity: 0.55, borderRadius: [3, 3, 0, 0] },
        data: visible.map((row) => num(row, "candidate_count"))
      },
      {
        name: "P50",
        type: "line",
        showSymbol: false,
        connectNulls: false,
        smooth: 0.18,
        lineStyle: { color: PERFORMANCE_TREND_COLORS.p50, width: 1.8 },
        itemStyle: { color: PERFORMANCE_TREND_COLORS.p50 },
        data: visible.map((row) => hasRuns(row) ? num(row, "p50_duration_seconds") : 0)
      },
      {
        name: "P95",
        type: "line",
        showSymbol: false,
        connectNulls: false,
        smooth: 0.18,
        lineStyle: { color: PERFORMANCE_TREND_COLORS.p95, width: 1.8 },
        itemStyle: { color: PERFORMANCE_TREND_COLORS.p95 },
        data: visible.map((row) => hasRuns(row) ? num(row, "p95_duration_seconds") : 0)
      }
    ]
  });
}

export const performancePageTestUtils = {
  performanceTrendOption,
  workloadEfficiencyOption,
  slowestDataflowProfilesOption,
};

function normalizeEfficiencyPoint(
  point: Record<string, string | number | null> | Array<string | number | null>
): Record<string, string | number | null> {
  if (!Array.isArray(point)) return point;
  const keys = [
    "dataflow_run_id", "dataflow_name", "stage", "operation_type",
    "duration_seconds", "rows_processed", "maintenance_bytes_processed",
    "maintenance_files_processed", "rows_read_per_second",
    "destination_bytes_added", "destination_bytes_removed",
    "performance_bottleneck_phase", "performance_candidate_reason",
    "performance_candidate_priority",
  ];
  return Object.fromEntries(keys.map((key, index) => [key, point[index] ?? null]));
}

function workloadEfficiencyOption(
  rows: Array<Record<string, string | number | null>>,
  scaleMode: EfficiencyScaleMode,
  scope: PerformanceEfficiencyScope,
  coverageLabel?: string
): EChartsOption {
  const observations = performanceScatterObservations(rows, scope);
  const visible = observations.points.map((point) => point.row);
  if (!visible.length) return emptyChartOption(`No measured workload efficiency points.\n${coverageLabel ?? observations.compactCoverage}`);
  const maxPriority = Math.max(1, ...visible.map((row) => num(row, "performance_candidate_priority")));
  const xValues = observations.points.map((point) => point.x);
  const yValues = observations.points.map((point) => point.y);
  const xMedian = percentileFromNumbers(xValues.filter((value) => value > 0), 0.5);
  const yP95 = percentileFromNumbers(yValues.filter((value) => value > 0), 0.95);
  return baseChartOption({
    grid: reportChartGrid({ left: 42, right: 8, top: 4, containLabel: false }),
    tooltip: {
      trigger: "item",
      formatter: chartTooltip((params) => {
        const row = visible[Number(firstChartParam(params)?.dataIndex ?? 0)] ?? {};
        return {
          title: String(row.dataflow_name ?? "unknown"),
          context: `Run: ${row.dataflow_run_id ?? "-"}`,
          metrics: [
            chartMetric("Stage / operation", `${monitoringDimensionLabel(row.stage)} · ${monitoringDimensionLabel(row.operation_type)}`),
            chartMetric(scope === "maintenance" ? "Bytes processed" : "Rows processed", scope === "maintenance" ? formatTooltipBytes(metricValue(row, "maintenance_bytes_processed")) : formatTooltipNumber(metricValue(row, "rows_processed"))),
            ...(scope === "maintenance" ? [chartMetric("Files processed", formatTooltipNumber(metricValue(row, "maintenance_files_processed")))] : []),
            ...(scope === "maintenance" ? [] : [chartMetric("Rows/s", formatTooltipNumber(metricValue(row, "rows_read_per_second")))]),
            chartMetric("Duration", `${formatTooltipNumber(metricValue(row, "duration_seconds"))} s`),
            { ...chartMetric("Bottleneck", phaseLabel(row.performance_bottleneck_phase)), valueColor: phaseColor(row.performance_bottleneck_phase) },
            chartMetric("Lakehouse bytes added / removed", `${formatTooltipBytes(metricValue(row, "destination_bytes_added"))} / ${formatTooltipBytes(metricValue(row, "destination_bytes_removed"))}`),
          ],
          note: row.performance_candidate_reason ? `Signal: ${row.performance_candidate_reason}` : undefined,
        };
      })
    },
    xAxis: {
      type: "value",
      scale: true,
      axisLine: { onZero: false },
      axisTick: { length: 3 },
      axisLabel: { fontSize: 10, margin: 4, formatter: (value: number) => scope === "maintenance" ? formatBytes(rawAxisValue(value, scaleMode)) : formatWorkloadAxis(value, scaleMode) },
      splitLine: { lineStyle: { color: reportChartPalette.grid } }
    },
    yAxis: {
      type: "value",
      scale: true,
      axisLine: { onZero: false },
      axisTick: { length: 3 },
      axisLabel: { fontSize: 10, margin: 4, formatter: (value: number) => formatDurationAxis(value, scaleMode) },
      splitLine: { lineStyle: { color: reportChartPalette.grid } }
    },
    series: [
      {
        name: "Dataflow runs",
        type: "scatter",
        symbolSize: (_value: unknown, params: any) => {
          const row = visible[Number(params?.dataIndex ?? 0)] ?? {};
          const priority = num(row, "performance_candidate_priority");
          const reasonCount = String(row.performance_candidate_reason ?? "").trim() ? 1 : 0;
          return Math.max(5, Math.min(18, 5 + Math.sqrt(priority / maxPriority) * 10 + reasonCount * 2));
        },
        itemStyle: {
          color: (params: any) => phaseColor(visible[Number(params?.dataIndex ?? 0)]?.performance_bottleneck_phase),
          opacity: 0.76,
          borderColor: "#ffffff",
          borderWidth: 1
        },
        markLine: {
          silent: true,
          symbol: "none",
          lineStyle: { color: reportChartPalette.muted, type: "dashed", width: 1, opacity: 0.5 },
          label: { color: reportChartPalette.muted, fontSize: 9 },
          data: [
            xMedian > 0
              ? {
                  xAxis: chartAxisValue(xMedian, scaleMode),
                  label: { formatter: scope === "maintenance" ? `P50 ${formatBytes(xMedian)}` : `P50 rows ${formatCompact(xMedian)}` }
                }
              : null,
            yP95 > 0
              ? {
                  yAxis: chartAxisValue(yP95, scaleMode),
                  label: { formatter: `P95 ${formatSeconds(yP95)}` }
                }
              : null
          ].filter(Boolean) as Array<Record<string, unknown>>
        },
        data: observations.points.map((point) => [
          chartAxisValue(point.x, scaleMode),
          chartAxisValue(point.y, scaleMode)
        ])
      }
    ]
  });
}

function performanceScatterObservations(rows: Array<Record<string, string | number | null>>, scope: PerformanceEfficiencyScope) {
  return scatterObservations(rows.slice(0, 600),
    (row) => metricValue(row, scope === "maintenance" ? "maintenance_bytes_processed" : "rows_processed"),
    (row) => metricValue(row, "duration_seconds"));
}

function performanceWorkloadValue(row: Record<string, string | number | null>, scope: PerformanceEfficiencyScope) {
  return scope === "maintenance" ? num(row, "maintenance_bytes_processed") : num(row, "rows_processed");
}

function defaultEfficiencyScale(
  rows: Array<Record<string, string | number | null>>,
  scope: PerformanceEfficiencyScope
): EfficiencyScaleMode {
  const values = rows.map((row) => performanceWorkloadValue(row, scope)).filter((value) => value > 0);
  if (values.length < 2) return "linear";
  const p50 = percentileFromNumbers(values, 0.5);
  const max = Math.max(...values);
  return p50 > 0 && max / p50 >= 1000 ? "log" : "linear";
}

function chartAxisValue(value: number, scaleMode: EfficiencyScaleMode) {
  if (scaleMode === "linear") return value;
  return Math.log10(Math.max(0, value) + 1);
}

function rawAxisValue(value: number, scaleMode: EfficiencyScaleMode) {
  if (scaleMode === "linear") return value;
  return Math.max(0, Math.pow(10, value) - 1);
}

function formatWorkloadAxis(value: number, scaleMode: EfficiencyScaleMode) {
  return formatCompact(rawAxisValue(value, scaleMode));
}

function formatDurationAxis(value: number, scaleMode: EfficiencyScaleMode) {
  return formatSeconds(rawAxisValue(value, scaleMode));
}

function percentileFromNumbers(values: number[], percentile: number) {
  if (!values.length) return 0;
  const sorted = values.slice().sort((left, right) => left - right);
  const index = (sorted.length - 1) * percentile;
  const lower = Math.floor(index);
  const upper = Math.ceil(index);
  if (lower === upper) return sorted[lower] ?? 0;
  const weight = index - lower;
  return (sorted[lower] ?? 0) * (1 - weight) + (sorted[upper] ?? 0) * weight;
}

function slowestDataflowProfilesOption(rows: Array<Record<string, string | number | null>>): EChartsOption {
  const visible = rows
    .slice()
    .sort((left, right) => num(right, "p95_duration_seconds") - num(left, "p95_duration_seconds"));
  if (!visible.length) return emptyChartOption("No slow dataflow profiles.");
  const labels = visible.map((row) => String(row.dataflow_name ?? row.dataflow_id ?? "unknown"));
  const zoomConfig = horizontalBarDataZoom(labels.length);
  return baseChartOption({
    grid: fixedHorizontalBarGrid(132, Boolean(zoomConfig), { right: zoomConfig ? 22 : 10, top: 8 }),
    tooltip: {
      trigger: "item",
      triggerOn: "mousemove",
      formatter: chartTooltip((params) => {
        const row = visible[Number(firstChartParam(params)?.dataIndex ?? 0)] ?? {};
        return {
          title: String(row.dataflow_name ?? "unknown"),
          metrics: [
            chartMetric("Stage / operation", `${monitoringDimensionLabel(row.stage)} · ${monitoringDimensionLabel(row.operation_type)}`),
            chartMetric("Runs", formatTooltipNumber(metricValue(row, "run_count"))),
            chartMetric("AVG / P50 / P95 / P99", `${formatTooltipNumber(metricValue(row, "avg_duration_seconds"))} s / ${formatTooltipNumber(metricValue(row, "p50_duration_seconds"))} s / ${formatTooltipNumber(metricValue(row, "p95_duration_seconds"))} s / ${formatTooltipNumber(metricValue(row, "p99_duration_seconds"))} s`),
            chartMetric("Max", `${formatTooltipNumber(metricValue(row, "max_duration_seconds"))} s`),
            { ...chartMetric("Bottleneck", phaseLabel(row.performance_bottleneck_phase)), valueColor: phaseColor(row.performance_bottleneck_phase) },
          ],
          note: `Source -> destination: ${monitoringRawDimensionLabel(row.source_name)} -> ${monitoringRawDimensionLabel(row.destination_name)}`,
        };
      })
    },
    xAxis: bottomAnchoredValueXAxis({ formatter: (value: number) => formatSeconds(value) }),
    yAxis: {
      type: "category",
      data: labels,
      inverse: true,
      axisTick: { show: false },
      axisLine: { show: true, lineStyle: { color: reportChartPalette.grid } },
      axisLabel: fixedHorizontalAxisLabel(132, { margin: 8 })
    },
    dataZoom: zoomConfig,
    series: [
      {
        name: "P95 duration",
        type: "bar",
        barMaxWidth: 18,
        itemStyle: {
          color: (params: any) => phaseColor(visible[Number(params?.dataIndex ?? 0)]?.performance_bottleneck_phase),
          borderRadius: [0, 3, 3, 0]
        },
        label: {
          show: true,
          position: "right",
          color: reportChartPalette.text,
          fontSize: 9,
          formatter: (params: any) => {
            const value = Number(params?.value ?? 0);
            return value > 0 ? formatSeconds(value) : "";
          }
        },
        data: visible.map((row) => num(row, "p95_duration_seconds"))
      }
    ]
  });
}

function emptyChartOption(message: string): EChartsOption {
  return baseChartOption({
    graphic: {
      type: "text",
      left: "center",
      top: "middle",
      style: {
        text: message,
        fill: reportChartPalette.muted,
        fontSize: 12,
        fontWeight: 600
      }
    }
  });
}

function phaseCostRows(rows: Array<Record<string, string | number | null>>) {
  return rows.map((row) => ({
    ...row,
    context: Number(row.is_total ?? 0) === 1
      ? "Total"
      : row.context ?? row.group ?? "Unknown"
  }));
}



function phaseColor(value: unknown) {
  return PHASE_COLORS[String(value ?? "unknown").toLowerCase()] ?? PHASE_COLORS.unknown;
}

function phaseLabel(value: unknown) {
  const text = String(value ?? "unknown").toLowerCase();
  if (text === "source") return "Source";
  if (text === "transform") return "Transform";
  if (text === "destination") return "Destination";
  if (text === "overhead") return "Overhead";
  return "Unknown";
}
