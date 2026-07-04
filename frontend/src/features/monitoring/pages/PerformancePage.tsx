import { useEffect, useState } from "react";
import type { EChartsOption } from "echarts";
import type { MonitoringRecord, MonitoringReport } from "../../../shared/api/types";
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
  formatPercent,
  formatSeconds,
  formatSecondsSingleDecimal,
  horizontalBarDataZoom,
  monitoringTimezone,
  num,
  reportChartPalette,
  reportChartGrid
} from "../monitoringShared";
import { baseChartOption } from "../ReportChart";

const PERFORMANCE_PAGE_SIZE = 100;
type EfficiencyScaleMode = "linear" | "log";
const PHASE_COLORS: Record<string, string> = {
  source: reportChartPalette.blue,
  transform: "#7c6ee6",
  destination: "#0f9f8f",
  overhead: "#64748b",
  unknown: reportChartPalette.muted
};

export function PerformancePage({
  report,
  rows,
  onInspect
}: {
  report: MonitoringReport;
  rows?: MonitoringRecord[];
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
  const investigationRows = rows ?? report.performance.investigation_queue ?? report.performance.slowest_dataflows ?? [];
  const workloadEfficiencyRows = report.performance.workload_efficiency_points ?? [];
  const [efficiencyScale, setEfficiencyScale] = useState<EfficiencyScaleMode>(() => defaultEfficiencyScale(workloadEfficiencyRows));
  const [offset, setOffset] = useState(0);
  const [limit, setLimit] = useState(PERFORMANCE_PAGE_SIZE);

  useEffect(() => {
    setOffset(0);
  }, [report, rows]);

  useEffect(() => {
    setEfficiencyScale(defaultEfficiencyScale(workloadEfficiencyRows));
  }, [report, workloadEfficiencyRows]);

  const phaseSummary = phaseShareSummary(kpis);
  const pressureRatio = Number(kpis.duration_pressure_ratio ?? 0);
  const pressureIntent = performancePressureIntent(pressureRatio, Number(kpis.p95_duration_seconds ?? 0));
  const candidateCount = Number(kpis.optimization_candidate_count ?? 0);
  const visibleRows = investigationRows.slice(offset, offset + limit);

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
          title="Performance pressure = P95 duration / P50 duration. Warning when ratio is high and P95 is materially slow."
        />
        <HealthStripCard
          label="Dataflow duration"
          value={<DurationHeadline avgSeconds={durationStats.avg_duration_seconds} p50Seconds={durationStats.p50_duration_seconds} />}
          detail={durationPercentilesDetail(durationStats)}
          intent={durationIntent(durationStats, durationStats.avg_duration_seconds, durationStats.p95_duration_seconds)}
          title="Duration percentiles include dataflow runs with duration evidence in current filters."
          className="overview-health-card-duration"
        />
        <HealthStripCard
          label="Slowest run"
          value={formatSecondsSingleDecimal(Number(kpis.slowest_run_duration_seconds ?? 0))}
          detail={
            <span className="health-rate-detail">
              <DetailMetric label={String(kpis.slowest_run_dataflow_name ?? "-")} value={String(kpis.slowest_run_stage ?? "-")} tone="blue" labelFirst />
            </span>
          }
          intent={Number(kpis.slowest_run_duration_seconds ?? 0) ? "warning" : "neutral"}
          title="Max duration_seconds in the current filters."
        />
        <HealthStripCard
          label="Bottleneck phase"
          value={<span className={`phase-text-${String(kpis.bottleneck_phase ?? "unknown")}`}>{phaseLabel(kpis.bottleneck_phase)}</span>}
          detail={phaseSummary}
          intent="neutral"
          title="Largest total runtime share across source, transform, destination, and overhead."
        />
        <HealthStripCard
          label="Throughput"
          value={`${formatNumber(Number(kpis.rows_read_per_second ?? 0))}/s`}
          detail={
            <span className="health-rate-detail">
              <DetailMetric label="read" value={formatNumber(Number(kpis.total_rows_read ?? 0))} tone="blue" labelFirst />
              <span className="separator"> · </span>
              <DetailMetric label="written" value={formatNumber(Number(kpis.total_rows_written ?? 0))} tone="purple" labelFirst />
            </span>
          }
          intent="neutral"
          title="Rows read per second = source rows read / total dataflow duration. Rows written can be sparse for non-lakehouse destinations."
        />
        <HealthStripCard
          label="Optimization candidates"
          value={formatNumber(candidateCount)}
          detail={
            <span className="health-rate-detail">
              <DetailMetric label="small" value={formatNumber(Number(kpis.slow_small_workload_count ?? 0))} tone="amber" labelFirst />
              <span className="separator"> · </span>
              <DetailMetric label="overhead" value={formatNumber(Number(kpis.high_overhead_count ?? 0))} tone="purple" labelFirst />
              <span className="separator"> · </span>
              <DetailMetric label="skew" value={formatNumber(Number(kpis.phase_skew_count ?? 0))} tone="blue" labelFirst />
            </span>
          }
          intent={candidateCount ? "warning" : "good"}
          title="Candidate rules: slow-small workload, high overhead, or one phase taking at least 90% of a slow run."
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
              rows={report.performance.duration_distribution_by_stage ?? report.performance.duration_by_stage ?? []}
              labelKey="stage"
              emptyText="No stage duration evidence in current filters."
            />
          </ReportPanel>
          <ReportPanel
            title="Phase cost by stage"
            subtitle="source / transform / destination / overhead"
            titleTooltip="Runtime contribution grouped by operation type and stage. Overhead is orchestration time outside source, transform, and destination."
          >
            <RuntimePhaseContribution
              rows={phaseCostRows(report.performance.phase_contribution_by_stage_operation ?? [])}
              labelKey="context"
              emptyText="No phase duration evidence in current filters."
            />
          </ReportPanel>
        </section>

        <section className="monitoring-performance-secondary-grid">
          <ReportPanel
            title="Workload efficiency map"
            subtitle={efficiencyScale === "log" ? "duration vs workload · log scale" : "duration vs workload · linear scale"}
            titleTooltip="Each point is a dataflow run. X = rows processed, Y = duration, color = bottleneck phase, size = performance candidate severity. Log scale keeps small and very large workloads visible together; tooltips always show raw values."
            headerAction={<EfficiencyScaleToggle value={efficiencyScale} onChange={setEfficiencyScale} />}
          >
            <ReportChart
              option={workloadEfficiencyOption(workloadEfficiencyRows, efficiencyScale)}
              height="100%"
            />
          </ReportPanel>
          <ReportPanel
            title="Slowest dataflow profiles"
            subtitle="P95 duration by dataflow"
            titleTooltip="Groups runs by dataflow_id and ranks by P95 duration. Color indicates the dominant runtime bottleneck phase."
          >
            <ReportChart
              option={slowestDataflowProfilesOption(report.performance.slowest_dataflow_profiles ?? report.performance.slowest_dataflows_by_p95 ?? [])}
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
          className="monitoring-performance-runs-panel"
          titleTooltip="Prioritized dataflow runs sorted by optimization candidate priority, duration, and latest time."
          headerAction={
            <TablePager
              limit={limit}
              offset={offset}
              loadedRows={visibleRows.length}
              totalRows={investigationRows.length}
              loading={false}
              onPageChange={setOffset}
              onPageSizeChange={(nextLimit) => {
                setLimit(nextLimit);
                setOffset(0);
              }}
            />
          }
        >
          <PerformanceInvestigationTable rows={visibleRows} onInspect={onInspect} timezoneName={timezoneName} />
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

function RuntimeContextProfileTable({ rows }: { rows: Array<Record<string, string | number | null>> }) {
  return (
    <DataTable<Record<string, string | number | null>>
      rows={rows}
      columns={[
        { key: "context", label: "Context", sortable: true, autoFit: true, minWidth: 116, maxWidth: 176, render: (row) => <RuntimeContextCell value={row.context} /> },
        { key: "runs", label: "Runs / success", sortable: true, autoFit: true, minWidth: 78, maxWidth: 92, render: (row) => <RuntimeMetricPair primary={formatNumber(num(row, "runs"))} secondary={formatPercent(num(row, "success_rate"))} secondaryTone={num(row, "success_rate") >= 95 ? "good" : num(row, "success_rate") > 0 ? "warning" : "neutral"} /> },
        { key: "p50_duration_seconds", label: "Performance", sortable: true, minWidth: 112, fillPriority: "last", render: (row) => <RuntimePerformanceCell row={row} /> }
      ]}
      maxRows={rows.length}
      className="performance-runtime-context-table"
      fixedLayout
    />
  );
}

function RuntimeContextCell({ value }: { value: unknown }) {
  const parts = String(value ?? "unknown").split(" · ").map((part) => part.trim()).filter(Boolean);
  const primary = parts.slice(0, 2).join(" · ") || "unknown";
  const secondary = parts.slice(2).join(" · ");
  const title = parts.join(" · ") || "unknown";
  return (
    <span className="runtime-context-cell" title={title}>
      <strong>{primary}</strong>
      {secondary ? <small>{secondary}</small> : null}
    </span>
  );
}

function RuntimePerformanceCell({ row }: { row: Record<string, string | number | null> }) {
  const candidateCount = num(row, "slow_candidate_count");
  return (
    <span className="runtime-context-performance-cell">
      <span>
        <b>P50</b> {formatSecondsSingleDecimal(num(row, "p50_duration_seconds"))}
        <em>/</em>
        <b>P95</b> {formatSecondsSingleDecimal(num(row, "p95_duration_seconds"))}
      </span>
      <small>
        <span className={candidateCount ? "runtime-context-metric-warning" : ""}>{formatNumber(candidateCount)} candidates</span>
      </small>
    </span>
  );
}

function RuntimeMetricPair({
  primary,
  secondary,
  secondaryTone = "neutral"
}: {
  primary: string;
  secondary: string;
  secondaryTone?: "neutral" | "good" | "warning" | "blue";
}) {
  return (
    <span className="runtime-context-metric-pair">
      <strong>{primary}</strong>
      <small className={`runtime-context-metric-${secondaryTone}`}>{secondary}</small>
    </span>
  );
}

function PerformanceInvestigationTable({
  rows,
  onInspect,
  timezoneName
}: {
  rows: MonitoringRecord[];
  onInspect?: (row: MonitoringRecord) => void;
  timezoneName?: string | null;
}) {
  return (
    <DataTable<MonitoringRecord>
      rows={rows}
      columns={[
        { key: "job_id", label: "Job", sortable: true, width: 104, render: (row) => <CopyableText value={row.job_id} displayValue={compactRunId(row.job_id)} /> },
        { key: "dataflow_name", label: "Dataflow", sortable: true, width: 150, render: (row) => <DataflowNameCell row={row} /> },
        { key: "context", label: "Context", sortable: true, sortKey: "stage", width: 120, render: (row) => <DataflowContextCell row={row} /> },
        { key: "phase", label: "Bottleneck", sortable: true, sortKey: "performance_bottleneck_phase", width: 138, render: (row) => <DataflowPhaseCell row={row} /> },
        { key: "volume", label: "Workload", width: 112, render: (row) => <DataflowVolumeCell row={row} /> },
        { key: "duration_seconds", label: "Duration", sortable: true, autoFit: true, minWidth: 76, maxWidth: 96, render: (row) => formatSeconds(num(row, "duration_seconds")) },
        { key: "start_time", label: "Start", sortable: true, width: 178, render: (row) => <TableDateTimeValue value={row.start_time} timezoneName={timezoneName} /> },
        { key: "end_time", label: "End", sortable: true, width: 178, render: (row) => <TableDateTimeValue value={row.end_time} timezoneName={timezoneName} /> },
        { key: "status", label: "Status", sortable: true, width: 96, render: (row) => <StatusCell row={row} /> },
        { key: "performance_candidate_reason", label: "Reason", minWidth: 90, fillPriority: "last", render: (row) => <PerformanceReasonCell row={row} /> },
        { key: "error_preview", label: "Issue", minWidth: 70, fillPriority: "last", render: (row) => <IssuePreview row={row} /> }
      ]}
      maxRows={rows.length}
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
    return <span className="performance-reason performance-reason-warning" title={String(reason)}>{String(reason)}</span>;
  }
  return <span className="performance-reason" title="No performance candidate rule matched.">-</span>;
}

function workloadEfficiencyOption(rows: Array<Record<string, string | number | null>>, scaleMode: EfficiencyScaleMode): EChartsOption {
  const visible = rows.slice(0, 600);
  if (!visible.length) return emptyChartOption("No workload efficiency signals.");
  const maxPriority = Math.max(1, ...visible.map((row) => num(row, "performance_candidate_priority")));
  const xValues = visible.map((row) => num(row, "rows_processed"));
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
          `Rows processed: ${formatNumber(num(row, "rows_processed"))}`,
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
      axisLabel: { fontSize: 10, margin: 4, formatter: (value: number) => formatWorkloadAxis(value, scaleMode) },
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
                  label: { formatter: `P50 rows ${formatCompact(xMedian)}` }
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
          chartAxisValue(num(row, "rows_processed"), scaleMode),
          chartAxisValue(num(row, "duration_seconds"), scaleMode)
        ])
      }
    ]
  });
}

function defaultEfficiencyScale(rows: Array<Record<string, string | number | null>>): EfficiencyScaleMode {
  const values = rows.map((row) => num(row, "rows_processed")).filter((value) => value > 0);
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
      <DetailMetric label="S" value={formatPercent(Number(kpis.source_duration_percent ?? 0))} tone="blue" labelFirst />
      <span className="separator"> · </span>
      <DetailMetric label="T" value={formatPercent(Number(kpis.transform_duration_percent ?? 0))} tone="purple" labelFirst />
      <span className="separator"> · </span>
      <DetailMetric label="D" value={formatPercent(Number(kpis.destination_duration_percent ?? 0))} tone="good" labelFirst />
      <span className="separator"> · </span>
      <DetailMetric label="O" value={formatPercent(Number(kpis.overhead_duration_percent ?? 0))} tone="neutral" labelFirst />
    </span>
  );
}

function phaseCostRows(rows: Array<Record<string, string | number>>) {
  return rows.map((row) => ({
    ...row,
    context: abbreviateOperationContext(String(row.context ?? row.group ?? "unknown"))
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

function performancePressureIntent(ratio: number, p95: number) {
  if (!ratio || !p95) return "neutral";
  if (ratio >= 10 && p95 >= 60) return "bad";
  if (ratio >= 5 && p95 >= 30) return "warning";
  return "good";
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
