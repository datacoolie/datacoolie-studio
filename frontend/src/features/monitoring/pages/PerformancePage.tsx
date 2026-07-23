import { useEffect, useMemo, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import type { EChartsOption } from "echarts";
import type { MonitoringRecord, MonitoringReport } from "../../../shared/api/types";
import type { TableColumn, TableSort } from "../MonitoringCharts";
import type { MonitoringFilters } from "../monitoringFilters";
import {
  CompactValue,
  CopyableText,
  DataTable,
  DataflowContextCell,
  DataflowNameCell,
  DataflowPhaseCell,
  DataflowVolumeCell,
  DetailMetric,
  DurationDistributionBoxPlot,
  DurationHeadline,
  HealthStripCard,
  IssuePreview,
  ReportChart,
  ReportPanel,
  RuntimePhaseLegend,
  RuntimePhaseContribution,
  StatusCell,
  TableDateTimeValue,
  TablePager,
  bottomAnchoredValueXAxis,
  compactRunId,
  durationIntent,
  durationPercentilesDetail,
  formatBytes,
  formatCompact,
  formatNumber,
  formatPhasePercent,
  formatPercent,
  formatSeconds,
  formatSecondsSingleDecimal,
  runtimePhaseContributionTooltip,
  fillMissingTrendDates,
  horizontalBarDataZoom,
  monitoringTimezone,
  monitoringPhaseColors,
  num,
  reportChartPalette,
  reportChartGrid
} from "../monitoringShared";
import { baseChartOption } from "../ReportChart";
import {
  defaultPerformanceEfficiencyScope,
  filterPerformanceEfficiencyRows,
  performancePressureIntent,
  type PerformanceEfficiencyScope
} from "../performancePageModel";

type EfficiencyScaleMode = "linear" | "log";
const PHASE_COLORS: Record<string, string> = {
  ...monitoringPhaseColors,
  unknown: reportChartPalette.muted
};

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
  onInspect
}: {
  report: MonitoringReport;
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
}) {
  const timezoneName = monitoringTimezone(report);
  const kpis = report.performance.kpis ?? {};
  const durationStats = {
    avg_duration_seconds: Number(kpis.avg_duration_seconds ?? 0),
    p50_duration_seconds: Number(kpis.p50_duration_seconds ?? 0),
    q3_duration_seconds: Number(kpis.p75_duration_seconds ?? 0),
    p95_duration_seconds: Number(kpis.p95_duration_seconds ?? 0),
    p99_duration_seconds: Number(kpis.p99_duration_seconds ?? 0),
    max_duration_seconds: Number(kpis.max_duration_seconds ?? 0)
  };
  const investigationRows = rows;
  const workloadEfficiencyRows = useMemo(
    () => (report.performance.workload_efficiency_points ?? []).map(normalizeEfficiencyPoint),
    [report.performance.workload_efficiency_points]
  );
  const [efficiencyScope, setEfficiencyScope] = useState<PerformanceEfficiencyScope>(() => defaultPerformanceEfficiencyScope(workloadEfficiencyRows));
  const scopedEfficiencyRows = useMemo(
    () => filterPerformanceEfficiencyRows(workloadEfficiencyRows, efficiencyScope),
    [workloadEfficiencyRows, efficiencyScope]
  );
  const [efficiencyScale, setEfficiencyScale] = useState<EfficiencyScaleMode>(() => defaultEfficiencyScale(scopedEfficiencyRows, efficiencyScope));
  useEffect(() => {
    setEfficiencyScope(defaultPerformanceEfficiencyScope(workloadEfficiencyRows));
  }, [workloadEfficiencyRows]);

  useEffect(() => {
    setEfficiencyScale(defaultEfficiencyScale(scopedEfficiencyRows, efficiencyScope));
  }, [scopedEfficiencyRows, efficiencyScope]);

  const phaseSummary = phaseShareSummary(kpis);
  const pressureRatio = Number(kpis.duration_pressure_ratio ?? 0);
  const pressureIntent = performancePressureIntent(pressureRatio, Number(kpis.p95_duration_seconds ?? 0));
  const candidateCount = Number(kpis.optimization_candidate_count ?? 0);
  const visibleRows = investigationRows;

  return (
    <div className="monitoring-page monitoring-performance-report">
      <section className="overview-health-strip monitoring-performance-health-strip">
        <HealthStripCard
          label="Performance pressure"
          value={pressureRatio > 0 ? `${formatNumber(pressureRatio)}x` : "-"}
          detail={
            <span className="health-duration-detail">
              <DetailMetric label="outliers" value={formatNumber(Number(kpis.duration_outlier_count ?? 0))} tone={Number(kpis.duration_outlier_count ?? 0) ? "amber" : "neutral"} labelFirst />
              <span className="separator"> · </span>
              <DetailMetric label="max" value={formatSecondsSingleDecimal(Number(kpis.max_duration_seconds ?? 0))} tone="purple" labelFirst />
            </span>
          }
          intent={pressureIntent}
          accent="intent"
          title="Performance pressure = P95 duration / P50 duration. Warning when ratio is high and P95 is materially slow."
        />
        <HealthStripCard
          label="Dataflow duration"
          value={<DurationHeadline avgSeconds={durationStats.avg_duration_seconds} p50Seconds={durationStats.p50_duration_seconds} />}
          detail={durationPercentilesDetail(durationStats)}
          intent={durationIntent(durationStats, durationStats.avg_duration_seconds, durationStats.p95_duration_seconds)}
          accent="intent"
          title="Duration percentiles include dataflow runs with duration evidence in current filters."
          className="overview-health-card-duration"
        />
        <HealthStripCard
          label="Slowest run"
          value={formatSecondsSingleDecimal(Number(kpis.slowest_run_duration_seconds ?? 0))}
          detail={<SlowestRunDetail kpis={kpis} timezoneName={timezoneName} />}
          intent={slowestRunIntent(Number(kpis.slowest_run_duration_seconds ?? 0), Number(kpis.p95_duration_seconds ?? 0))}
          accent="intent"
          title="Max duration_seconds in the current filters."
        />
        <HealthStripCard
          label="Bottleneck phase"
          value={<span className={`phase-text-${String(kpis.bottleneck_phase ?? "unknown")}`}>{phaseLabel(kpis.bottleneck_phase)}</span>}
          detail={phaseSummary}
          intent="neutral"
          accent={performancePhaseAccent(kpis.bottleneck_phase)}
          title="Largest total runtime share across source, transform, destination, and overhead."
        />
        <HealthStripCard
          label="Effective read throughput"
          value={`${formatNumber(Number(kpis.rows_read_per_second ?? 0))}/s`}
          detail={
            <span className="health-rate-detail">
              <DetailMetric label="read" value={formatNumber(Number(kpis.total_rows_read ?? 0))} tone="read" labelFirst />
              <span className="separator"> · </span>
              <DetailMetric label="written" value={formatNumber(Number(kpis.total_rows_written ?? 0))} tone="written" labelFirst />
            </span>
          }
          intent="neutral"
          accent="source"
          title="Rows read per second = source rows read / total dataflow duration. Rows written can be sparse for non-lakehouse destinations."
        />
        <HealthStripCard
          label="Optimization candidates"
          value={formatNumber(candidateCount)}
          detail={
            <span className="health-rate-detail">
              <DetailMetric label="small" value={formatNumber(Number(kpis.slow_small_workload_count ?? 0))} tone="amber" labelFirst />
              <span className="separator"> · </span>
              <DetailMetric label="maint" value={formatNumber(Number(kpis.slow_small_maintenance_count ?? 0))} tone="neutral" labelFirst />
              <span className="separator"> · </span>
              <DetailMetric label="overhead" value={formatNumber(Number(kpis.high_overhead_count ?? 0))} tone="purple" labelFirst />
              <span className="separator"> · </span>
              <DetailMetric label="skew" value={formatNumber(Number(kpis.phase_skew_count ?? 0))} tone="blue" labelFirst />
            </span>
          }
          intent={candidateCount ? "warning" : "good"}
          accent={candidateCount ? "warning" : "neutral"}
          title="Unique candidate runs. Rule-match counts can overlap: slow-small pipeline workload, slow-small maintenance workload, high overhead, or phase skew."
        />
      </section>

      <div className="monitoring-performance-content report-layout-performance-3">
        <section className="monitoring-performance-primary-grid">
          <ReportPanel
            title="Stage duration distribution"
            subtitle="P50, P95 and outliers"
            titleTooltip="Box plot of dataflow durations grouped by stage. Sorts by P95 duration and shows IQR outliers."
          >
            <DurationDistributionBoxPlot
              rows={report.performance.duration_distribution_by_stage ?? []}
              labelKey="stage"
              emptyText="No stage duration evidence in current filters."
            />
          </ReportPanel>
          <ReportPanel
            title="Phase cost by stage"
            titleTooltip={runtimePhaseContributionTooltip("operation type and stage")}
            headerAction={<RuntimePhaseLegend />}
          >
            <RuntimePhaseContribution
              rows={phaseCostRows(report.performance.phase_contribution_by_stage_operation ?? [])}
              labelKey="context"
              emptyText="No phase duration evidence in current filters."
              showLegend={false}
            />
          </ReportPanel>
          <ReportPanel
            title="Performance trend"
            className="monitoring-performance-trend-panel"
            titleTooltip="Executable dataflow duration and unique optimization candidates over the selected time range. Empty buckets remain visible."
            headerAction={<PerformanceTrendLegend />}
          >
            <ReportChart
              option={performanceTrendOption(
                report.performance.performance_trend ?? [],
                filters,
                report.summary.date_range,
                timezoneName,
                report.summary.effective_grain ?? undefined
              )}
              height="100%"
            />
          </ReportPanel>
        </section>

        <section className="monitoring-performance-secondary-grid">
          <ReportPanel
            title="Workload efficiency map"
            titleTooltip="Each point is an executable dataflow run. Pipeline view uses rows processed; Maintenance uses bytes processed. Y = duration, color = bottleneck phase, and size = candidate severity."
            headerAction={
              <div className="performance-efficiency-controls">
                <EfficiencyScopeToggle value={efficiencyScope} onChange={setEfficiencyScope} />
                <EfficiencyScaleToggle value={efficiencyScale} onChange={setEfficiencyScale} />
              </div>
            }
          >
            <ReportChart
              option={workloadEfficiencyOption(scopedEfficiencyRows, efficiencyScale, efficiencyScope)}
              height="100%"
            />
          </ReportPanel>
          <ReportPanel
            title="Slowest dataflow profiles"
            subtitle="P95 duration by dataflow"
            titleTooltip="Groups runs by dataflow_id and ranks by P95 duration. Color indicates the dominant runtime bottleneck phase."
          >
            <ReportChart
              option={slowestDataflowProfilesOption(report.performance.slowest_dataflow_profiles ?? [])}
              height="100%"
              wheelDataZoomStep={1}
            />
          </ReportPanel>
          <ReportPanel
            title="Runtime context profile"
            subtitle="platform / engine / provider"
            titleTooltip="Compares runtime context groups by run count, success rate, duration percentiles, throughput, and candidate count."
          >
            <RuntimeContextProfileTable rows={report.performance.runtime_context_profiles ?? []} />
          </ReportPanel>
        </section>

        <ReportPanel
          title="Performance investigation queue"
          subtitle="optimization candidates first, then slowest runs"
          className="monitoring-performance-runs-panel"
          titleTooltip="Prioritized dataflow runs sorted by optimization candidate priority, duration, and latest time."
          headerAction={
            <TablePager
              limit={limit}
              offset={offset}
              loadedRows={visibleRows.length}
              totalRows={totalRows}
              loading={loading}
              onPageChange={onPageChange}
              onPageSizeChange={onPageSizeChange}
            />
          }
        >
          <PerformanceInvestigationTable
            rows={visibleRows}
            sort={sort}
            onSort={onSort}
            onInspect={onInspect}
            timezoneName={timezoneName}
          />
        </ReportPanel>
      </div>
    </div>
  );
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
      title="Linear shows raw axis spacing. Log keeps small and very large workloads visible together; tooltip values remain raw."
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
      {(["etl", "maintenance", "all"] as const).map((scope) => (
        <button
          key={scope}
          type="button"
          className={value === scope ? "active" : ""}
          aria-pressed={value === scope}
          onClick={() => onChange(scope)}
        >
          {scope === "etl" ? "Pipeline" : scope === "maintenance" ? "Maint." : "All"}
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
      columns={columns}
      maxRows={rows.length}
      className="performance-runtime-context-table"
      fixedLayout
    />
  );
}

function RuntimeContextCell({ row }: { row: Record<string, string | number | null> }) {
  const engine = String(row.engine_name ?? "unknown");
  const provider = String(row.metadata_provider_name ?? "unknown");
  const platform = String(row.platform_name ?? "unknown");
  const primary = `${compactRuntimeContextValue(engine, "engine")} · ${compactRuntimeContextValue(provider, "provider")}`;
  const secondary = compactRuntimeContextValue(platform, "platform");
  const title = `${engine} · ${provider} · ${platform}`;
  return (
    <span className="runtime-context-cell" title={title}>
      <strong>{primary}</strong>
      <small>{secondary}</small>
    </span>
  );
}

function compactRuntimeContextValue(value: string, suffix: "engine" | "provider" | "platform") {
  const compact = value.replace(new RegExp(`\\s*${suffix}$`, "i"), "").trim();
  return compact || value;
}

function RuntimePerformanceCell({ row }: { row: Record<string, string | number | null> }) {
  const candidateCount = num(row, "candidate_count");
  return (
    <span className="runtime-context-performance-cell">
      <span>
        <span className="runtime-context-duration-p50"><b>P50</b> {formatSecondsSingleDecimal(num(row, "p50_duration_seconds"))}</span>
        <em>/</em>
        <span className="runtime-context-duration-p95"><b>P95</b> {formatSecondsSingleDecimal(num(row, "p95_duration_seconds"))}</span>
      </span>
      <small>
        <span className="runtime-context-throughput">{formatNumber(num(row, "rows_read_per_second"))}/s</span>
        <em>·</em>
        <span className={candidateCount ? "runtime-context-candidates-warning" : "runtime-context-candidates-neutral"}>
          {formatNumber(candidateCount)} candidates
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
      <strong>{formatNumber(num(row, "run_count"))}</strong>
      <small>
        <span className={`runtime-context-success-${successTone}`}>{formatPercent(successRate)}</span>
        <em>·</em>
        <span className={failed ? "runtime-context-failed-bad" : "runtime-context-failed-neutral"}>
          {formatNumber(failed)} failed
        </span>
      </small>
    </span>
  );
}

function SlowestRunDetail({ kpis, timezoneName }: { kpis: Record<string, unknown>; timezoneName: string }) {
  const name = String(kpis.slowest_run_dataflow_name ?? kpis.slowest_run_dataflow_id ?? "-");
  const stage = String(kpis.slowest_run_stage ?? "-");
  const operation = String(kpis.slowest_run_operation_type ?? "-");
  const [tooltipPosition, setTooltipPosition] = useState<{ top: number; left: number; above: boolean } | null>(null);
  const showTooltip = (target: HTMLElement) => {
    const rect = target.getBoundingClientRect();
    const tooltipWidth = 300;
    const above = window.innerHeight - rect.bottom < 190 && rect.top > 190;
    setTooltipPosition({
      top: above ? rect.top - 8 : rect.bottom + 8,
      left: Math.max(8, Math.min(rect.left, window.innerWidth - tooltipWidth - 8)),
      above
    });
  };
  return (
    <>
      <span
        className="performance-slowest-run-detail"
        tabIndex={0}
        aria-describedby={tooltipPosition ? "performance-slowest-run-tooltip" : undefined}
        onMouseEnter={(event) => showTooltip(event.currentTarget)}
        onMouseLeave={() => setTooltipPosition(null)}
        onFocus={(event) => showTooltip(event.currentTarget)}
        onBlur={() => setTooltipPosition(null)}
        onKeyDown={(event) => {
          if (event.key === "Escape") setTooltipPosition(null);
        }}
      >
        <small>
          <span>{name}</span>
          <em>·</em>
          <span className="performance-slowest-run-stage">{stage}</span>
          {operation !== "-" ? <><em>·</em><span>{operation}</span></> : null}
        </small>
      </span>
      {tooltipPosition ? createPortal(
        <div
          id="performance-slowest-run-tooltip"
          className="performance-slowest-run-tooltip"
          role="tooltip"
          style={{
            top: tooltipPosition.top,
            left: tooltipPosition.left,
            transform: tooltipPosition.above ? "translateY(-100%)" : undefined
          }}
        >
          <TooltipRow label="Dataflow" value={name} />
          <TooltipRow label="Start" value={<TableDateTimeValue value={kpis.slowest_run_start_time} timezoneName={timezoneName} />} />
          <TooltipRow label="End" value={<TableDateTimeValue value={kpis.slowest_run_end_time} timezoneName={timezoneName} />} />
          <TooltipRow label="Stage" value={stage} valueClassName="performance-slowest-run-stage" />
          <TooltipRow label="Operation" value={operation} />
          <TooltipRow label="Status" value={String(kpis.slowest_run_status ?? "-")} />
        </div>,
        document.body
      ) : null}
    </>
  );
}

function TooltipRow({ label, value, valueClassName }: { label: string; value: ReactNode; valueClassName?: string }) {
  return (
    <div className="performance-slowest-run-tooltip-row">
      <span>{label}</span>
      <strong className={valueClassName}>{value}</strong>
    </div>
  );
}

function PerformanceInvestigationTable({
  rows,
  sort,
  onSort,
  onInspect,
  timezoneName
}: {
  rows: MonitoringRecord[];
  sort?: TableSort;
  onSort: (sort: TableSort) => void;
  onInspect?: (row: MonitoringRecord) => void;
  timezoneName?: string | null;
}) {
  const columns = useMemo<TableColumn<MonitoringRecord>[]>(() => [
    { key: "job_id", label: "Job", sortable: true, autoFit: true, minWidth: 96, maxWidth: 112, render: (row) => <CopyableText value={row.job_id} displayValue={compactRunId(row.job_id)} /> },
    { key: "dataflow_name", label: "Dataflow", sortable: true, minWidth: 144, fillPriority: "last", render: (row) => <DataflowNameCell row={row} /> },
    { key: "context", label: "Context", sortable: true, sortKey: "stage", autoFit: true, minWidth: 108, maxWidth: 132, render: (row) => <DataflowContextCell row={row} /> },
    { key: "phase", label: "Bottleneck", sortable: true, sortKey: "performance_bottleneck_phase", autoFit: true, minWidth: 116, maxWidth: 140, render: (row) => <DataflowPhaseCell row={row} /> },
    { key: "volume", label: "Workload", autoFit: true, minWidth: 104, maxWidth: 124, render: (row) => <DataflowVolumeCell row={row} /> },
    { key: "duration_seconds", label: "Duration", sortable: true, autoFit: true, minWidth: 76, maxWidth: 96, render: (row) => formatSeconds(num(row, "duration_seconds")) },
    { key: "start_time", label: "Start", sortable: true, autoFit: true, minWidth: 132, maxWidth: 178, render: (row) => <TableDateTimeValue value={row.start_time} timezoneName={timezoneName} /> },
    { key: "end_time", label: "End", sortable: true, autoFit: true, minWidth: 132, maxWidth: 178, render: (row) => <TableDateTimeValue value={row.end_time} timezoneName={timezoneName} /> },
    { key: "status", label: "Status", sortable: true, autoFit: true, minWidth: 84, maxWidth: 100, render: (row) => <StatusCell row={row} /> },
    { key: "performance_candidate_reason", label: "Primary reason", minWidth: 108, fillPriority: "last", render: (row) => <PerformanceReasonCell row={row} /> },
    { key: "error_preview", label: "Issue", minWidth: 140, fillPriority: "last", render: (row) => <IssuePreview row={row} /> }
  ], [timezoneName]);
  return (
    <DataTable<MonitoringRecord>
      rows={rows}
      columns={columns}
      maxRows={rows.length}
      sort={sort}
      onSort={onSort}
      onRowClick={onInspect}
      timezoneName={timezoneName}
      className="performance-investigation-table"
      fixedLayout
    />
  );
}

function PerformanceReasonCell({ row }: { row: MonitoringRecord }) {
  const reason = row.performance_candidate_reason;
  if (reason) {
    const reasons = Array.isArray(row.performance_candidate_reasons)
      ? row.performance_candidate_reasons.map(String).filter(Boolean)
      : [String(reason)];
    const additionalCount = Math.max(0, reasons.length - 1);
    return (
      <span className="performance-reason performance-reason-warning" title={reasons.join(" · ")}>
        {String(reason)}{additionalCount ? ` +${additionalCount}` : ""}
      </span>
    );
  }
  return <span className="performance-reason" title="No performance candidate rule matched.">-</span>;
}

function PerformanceTrendLegend() {
  return (
    <div className="performance-trend-legend" aria-label="Performance trend legend">
      <span className="performance-trend-p50">P50</span>
      <span className="performance-trend-p95">P95</span>
      <span className="performance-trend-candidates">Candidates</span>
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
      confine: true,
      formatter: (params: any) => {
        const first = Array.isArray(params) ? params[0] : params;
        const row = visible[Number(first?.dataIndex ?? 0)] ?? {};
        const runCount = num(row, "run_count");
        return [
          `<strong>${row.bucket ?? row.date ?? "unknown"}</strong>`,
          `Timezone: ${timezoneName}`,
          `Runs: ${formatNumber(runCount)}`,
          runCount ? `P50: ${formatSeconds(num(row, "p50_duration_seconds"))}` : "No executable runs",
          runCount ? `P95: ${formatSeconds(num(row, "p95_duration_seconds"))}` : "",
          `Candidates: ${formatNumber(num(row, "candidate_count"))}`
        ].filter(Boolean).join("<br/>");
      }
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
        name: "Candidates",
        type: "bar",
        yAxisIndex: 1,
        barMaxWidth: 14,
        itemStyle: { color: "#d89b42", opacity: 0.55, borderRadius: [3, 3, 0, 0] },
        data: visible.map((row) => num(row, "candidate_count"))
      },
      {
        name: "P50",
        type: "line",
        showSymbol: false,
        connectNulls: false,
        smooth: 0.18,
        lineStyle: { color: "#2563eb", width: 1.8 },
        itemStyle: { color: "#2563eb" },
        data: visible.map((row) => hasRuns(row) ? num(row, "p50_duration_seconds") : 0)
      },
      {
        name: "P95",
        type: "line",
        showSymbol: false,
        connectNulls: false,
        smooth: 0.18,
        lineStyle: { color: "#7c3aed", width: 1.8 },
        itemStyle: { color: "#7c3aed" },
        data: visible.map((row) => hasRuns(row) ? num(row, "p95_duration_seconds") : 0)
      }
    ]
  });
}

export const performancePageTestUtils = {
  performanceTrendOption
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
  scope: PerformanceEfficiencyScope
): EChartsOption {
  const visible = rows.slice(0, 600);
  if (!visible.length) return emptyChartOption("No workload efficiency signals.");
  const maxPriority = Math.max(1, ...visible.map((row) => num(row, "performance_candidate_priority")));
  const xValues = visible.map((row) => performanceWorkloadValue(row, scope));
  const yValues = visible.map((row) => num(row, "duration_seconds"));
  const xMedian = percentileFromNumbers(xValues.filter((value) => value > 0), 0.5);
  const yP95 = percentileFromNumbers(yValues.filter((value) => value > 0), 0.95);
  return baseChartOption({
    grid: reportChartGrid({ left: 42, right: 8, top: 4, containLabel: false }),
    tooltip: {
      trigger: "item",
      confine: false,
      appendToBody: true,
      extraCssText: "max-width: 340px; white-space: normal; line-height: 1.4;",
      formatter: (params: any) => {
        const row = visible[Number(params?.dataIndex ?? 0)] ?? {};
        return [
          `<strong>${row.dataflow_name ?? "unknown"}</strong>`,
          `Run: ${row.dataflow_run_id ?? "-"}`,
          `Stage / operation: ${row.stage ?? "unknown"} · ${row.operation_type ?? "unknown"}`,
          scope === "maintenance"
            ? `Bytes processed: ${formatBytes(num(row, "maintenance_bytes_processed"))}`
            : `Rows processed: ${formatNumber(num(row, "rows_processed"))}`,
          scope === "maintenance" ? `Files processed: ${formatNumber(num(row, "maintenance_files_processed"))}` : "",
          `Rows/s: ${formatNumber(num(row, "rows_read_per_second"))}`,
          `Duration: ${formatSeconds(num(row, "duration_seconds"))}`,
          `Bottleneck: ${phaseLabel(row.performance_bottleneck_phase)}`,
          `Lakehouse bytes added / removed: ${formatBytes(num(row, "destination_bytes_added"))} / ${formatBytes(num(row, "destination_bytes_removed"))}`,
          row.performance_candidate_reason ? `Candidate: ${row.performance_candidate_reason}` : ""
        ].filter(Boolean).join("<br/>");
      }
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
        data: visible.map((row) => [
          chartAxisValue(performanceWorkloadValue(row, scope), scaleMode),
          chartAxisValue(num(row, "duration_seconds"), scaleMode)
        ])
      }
    ]
  });
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
    grid: reportChartGrid({ left: 148, right: zoomConfig ? 22 : 10, top: 8, containLabel: false }),
    tooltip: {
      trigger: "item",
      triggerOn: "mousemove",
      confine: false,
      appendToBody: true,
      extraCssText: "max-width: 330px; white-space: normal; line-height: 1.4;",
      formatter: (params: any) => {
        const row = visible[Number(params?.dataIndex ?? 0)] ?? {};
        return [
          `<strong>${row.dataflow_name ?? "unknown"}</strong>`,
          `Stage / operation: ${row.stage ?? "unknown"} · ${row.operation_type ?? "unknown"}`,
          `Runs: ${formatNumber(num(row, "run_count"))}`,
          `AVG / P50 / P95 / P99: ${formatSeconds(num(row, "avg_duration_seconds"))} / ${formatSeconds(num(row, "p50_duration_seconds"))} / ${formatSeconds(num(row, "p95_duration_seconds"))} / ${formatSeconds(num(row, "p99_duration_seconds"))}`,
          `Max: ${formatSeconds(num(row, "max_duration_seconds"))}`,
          `Bottleneck: ${phaseLabel(row.performance_bottleneck_phase)}`,
          `Source -> destination: ${row.source_name ?? "-"} -> ${row.destination_name ?? "-"}`
        ].join("<br/>");
      }
    },
    xAxis: bottomAnchoredValueXAxis({ formatter: (value: number) => formatSeconds(value) }),
    yAxis: {
      type: "category",
      data: labels,
      inverse: true,
      axisTick: { show: false },
      axisLine: { show: true, lineStyle: { color: reportChartPalette.grid } },
      axisLabel: {
        align: "right",
        fontSize: 10,
        width: 136,
        overflow: "truncate",
        margin: 8,
        color: reportChartPalette.muted
      }
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

function phaseShareSummary(kpis: Record<string, unknown>) {
  return (
    <span className="health-rate-detail performance-phase-share-detail">
      <DetailMetric label="S" value={formatPhasePercent(Number(kpis.source_duration_percent ?? 0))} tone="blue" labelFirst />
      <span className="separator"> · </span>
      <DetailMetric label="T" value={formatPhasePercent(Number(kpis.transform_duration_percent ?? 0))} tone="purple" labelFirst />
      <span className="separator"> · </span>
      <DetailMetric label="D" value={formatPhasePercent(Number(kpis.destination_duration_percent ?? 0))} tone="good" labelFirst />
      <span className="separator"> · </span>
      <DetailMetric label="O" value={formatPhasePercent(Number(kpis.overhead_duration_percent ?? 0))} tone="neutral" labelFirst />
    </span>
  );
}

function phaseCostRows(rows: Array<Record<string, string | number>>) {
  return rows.map((row) => ({
    ...row,
    context: Number(row.is_total ?? 0) === 1
      ? "Total"
      : abbreviateOperationContext(String(row.context ?? row.group ?? "unknown"))
  }));
}

function abbreviateOperationContext(value: string) {
  const [operation, ...rest] = value.split(" · ");
  if (!rest.length) return abbreviateOperation(operation);
  return [abbreviateOperation(operation), ...rest].join(" · ");
}

function abbreviateOperation(value: string) {
  const normalized = value.trim().toLowerCase();
  if (!normalized || normalized === "unknown") return "unk";
  if (normalized === "maintenance") return "mnt";
  if (normalized === "etl") return "etl";
  return normalized.slice(0, 4);
}

function slowestRunIntent(slowest: number, p95: number) {
  if (!slowest || !p95) return "neutral";
  if (slowest >= Math.max(300, p95 * 10)) return "warning";
  return "neutral";
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

function performancePhaseAccent(value: unknown): "source" | "transform" | "destination" | "overhead" | "neutral" {
  const phase = String(value ?? "unknown").toLowerCase();
  if (phase === "source" || phase === "transform" || phase === "destination" || phase === "overhead") return phase;
  return "neutral";
}
