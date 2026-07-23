import type { JobRecord, MonitoringRecord, MonitoringReport } from "../../shared/api/types";
import { useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties, PointerEvent as ReactPointerEvent, ReactNode } from "react";
import { createPortal } from "react-dom";
import type { MonitoringFilters, MonitoringTabKey } from "./monitoringFilters";
import {
  BarList,
  DataTable,
  MetricGrid,
  Panel,
  ScatterPlot,
  StatusCell,
  type TableSort,
  formatBytes,
  formatNumber,
  formatSeconds,
  num
} from "./MonitoringCharts";
import { ReportChart, baseChartOption, reportChartPalette } from "./ReportChart";
import { formatTimestampForDisplay } from "../../shared/time";
import { LineageFormatIcon } from "../lineage/components/LineageFormatIcon";
import { assetIconKind } from "../lineage/model/presentation";

const OVERVIEW_STATUSES = ["succeeded", "failed", "skipped", "running", "pending"] as const;
const PHASE_KEYS = ["source", "transform", "destination", "overhead"] as const;
const FAILURE_PHASES = ["source", "transform", "destination", "overhead", "unknown"] as const;
const HORIZONTAL_BAR_VISIBLE_ROWS = 8;
const HORIZONTAL_BAR_LABEL_GAP = 16;
const REPORT_CHART_GRID_BOTTOM = 6;
const REPORT_CHART_TIGHT_GRID_BOTTOM = 2;
const REPORT_CHART_X_ZOOM_GRID_BOTTOM = 30;
const RUN_TABLE_PAGE_SIZES = [50, 100, 200] as const;
type HealthIntent = "neutral" | "bad" | "good" | "warning";
export type HealthCardAccent = HealthIntent | "source" | "transform" | "destination" | "storage" | "overhead" | "intent";
type PhaseKey = typeof PHASE_KEYS[number];
type FailurePhaseKey = typeof FAILURE_PHASES[number];

function HealthStripCard({
  label,
  value,
  detail,
  title,
  intent = "neutral",
  accent = "neutral",
  className = ""
}: {
  label: string;
  value: ReactNode;
  detail?: ReactNode;
  title?: string;
  intent?: "neutral" | "bad" | "good" | "warning";
  accent?: HealthCardAccent;
  className?: string;
}) {
  return (
    <div className={`overview-health-card health-card-${intent} ${healthCardAccentClass(accent, intent)}${className ? ` ${className}` : ""}`} title={title}>
      <span>{label}</span>
      <strong>{value}</strong>
      {detail ? <small>{detail}</small> : null}
    </div>
  );
}

function FailureBreakdownDetail({ jobFailed, flowFailed }: { jobFailed: number; flowFailed: number }) {
  return (
    <span className="health-failure-detail">
      <DetailMetric label="job failed" value={formatNumber(jobFailed)} tone="bad" />
      <span className="separator"> · </span>
      <DetailMetric label="flow failed" value={formatNumber(flowFailed)} tone="bad" />
    </span>
  );
}

function RateBreakdownDetail({ failedRate, windowFailedRate }: { failedRate: number; windowFailedRate: number }) {
  return (
    <span className="health-rate-detail">
      <DetailMetric label="failed" value={formatPercent(failedRate)} tone="bad" />
      <span className="separator"> · </span>
      <DetailMetric label="7d failed" value={formatPercent(windowFailedRate)} tone="bad" />
    </span>
  );
}

function DetailMetric({
  label,
  value,
  tone = "neutral",
  labelFirst = false
}: {
  label: string;
  value: ReactNode;
  tone?: "neutral" | "good" | "warning" | "bad" | "blue" | "purple" | "amber" | "read" | "written";
  labelFirst?: boolean;
}) {
  return (
    <span className="health-detail-token">
      {labelFirst ? <span className="health-detail-label">{label}</span> : null}
      <b className={`health-detail-value detail-value-${tone}`}>{value}</b>
      {labelFirst ? null : <span className="health-detail-label">{label}</span>}
    </span>
  );
}

function healthReasonSummary(reasons: string[]) {
  const items = reasons.filter((reason) => String(reason || "").trim().length > 0);
  if (!items.length) {
    return {
      primary: "No immediate monitoring issues detected.",
      additionalCount: 0
    };
  }
  return {
    primary: items[0],
    additionalCount: Math.max(0, items.length - 1)
  };
}

function healthReasonsTooltip(reasons: string[]) {
  const items = reasons.filter((reason) => String(reason || "").trim().length > 0);
  if (!items.length) return "No immediate monitoring issues detected.";
  return items.map((reason, index) => `${index + 1}. ${reason}`).join("\n");
}

function failureCategoriesRuleTooltip() {
  return [
    "Value source:",
    "- Count of failed dataflow runs in current filters.",
    "- Job failures are rollup/impact context and are not used for error hints.",
    "- Error text is resolved by phase priority: source -> transform -> destination -> overhead.",
    "- 1 failed dataflow run contributes to exactly 1 rule-based hint.",
    "- Hints are inferred from text patterns and can be wrong; use the raw error detail for confirmation.",
    "Hint rules:",
    "- Dependency: missing package/module or install-required messages (pip/conda/poetry install, module not found).",
    "- Connectivity: connection refused/reset, timeout, DNS/network reachability errors.",
    "- Authentication: oauth/auth/credential/token",
    "- Missing object: not found/does not exist/missing",
    "- Schema: schema/column/type",
    "- Validation: replay/assert/validation",
    "- Unspecified: empty message or 'none'",
    "- Other: no keyword match",
    "Phase rules:",
    "- Source: source_error_message has value.",
    "- Transform: transform_error_message has value.",
    "- Destination: destination_error_message has value.",
    "- Overhead: dataflow-level error_message/error_messages has value while source/transform/destination errors are empty.",
    "- Unknown: failed dataflow has no error message evidence.",
    "Display condition:",
    "- A hint appears when at least 1 failed dataflow run matches that hint.",
    "- Bars are sorted by count descending (tie-break by hint name)."
  ].join("\n");
}

function attentionQueueRuleTooltip() {
  return [
    "Value source:",
    "- Signals roll up actionable current-filter health evidence from Jobs, Dataflows, Failures, Performance, Maintenance, Freshness, and Diagnostics.",
    "Display condition:",
    "- Recent failed jobs/dataflows: last 3d => bad, else last 7d => warning.",
    "- Repeated failure: top failing dataflow has >= 3 failed runs.",
    "- No log evidence: warning when no monitoring logs are found in current filters.",
    "- Stale logs: warning when latest log age > 7 days.",
    "- Performance pressure uses the same P95/P50 thresholds as Performance; optimization candidates and a slow stage can also produce signals.",
    "- Maintenance coverage/lag, freshness, runtime context, active runs, linkage, cache, and reconciliation evidence reuse their page metrics.",
    "- High volume alone is not treated as an issue without anomaly evidence.",
    "- Queue is deduplicated, then ranked by severity and impact; it shows up to 8 signals."
  ].join("\n");
}

function durationStatsTitle(label: string, _stats: Record<string, number>) {
  return `${label} includes succeeded and failed runs.`;
}

function DurationHeadline({ avgSeconds, p50Seconds }: { avgSeconds: number; p50Seconds: number }) {
  return (
    <span className="health-duration-headline">
      <span className="duration-avg">AVG {formatSecondsSingleDecimal(avgSeconds)}</span>
      <span className="separator"> - </span>
      <span className="duration-p50">P50 {formatSecondsSingleDecimal(p50Seconds)}</span>
    </span>
  );
}

function WindowPairDetail({
  firstLabel,
  firstValue,
  firstTone,
  secondLabel,
  secondValue,
  secondTone
}: {
  firstLabel: string;
  firstValue: ReactNode;
  firstTone?: "good" | "warning" | "bad" | "neutral" | "headline";
  secondLabel: string;
  secondValue: ReactNode;
  secondTone?: "good" | "warning" | "bad" | "neutral" | "headline";
}) {
  return (
    <span className="health-window-detail">
      <span className={`window-slice window-slice-primary${firstTone ? ` window-slice-${firstTone}` : ""}`}>
        <em>{firstLabel}</em>
        <b>{firstValue}</b>
      </span>
      <span className="separator">/</span>
      <span className={`window-slice window-slice-secondary${secondTone ? ` window-slice-${secondTone}` : ""}`}>
        <em>{secondLabel}</em>
        <b>{secondValue}</b>
      </span>
    </span>
  );
}

function durationPercentilesDetail(stats: Record<string, number>) {
  const p75 = stats.q3_duration_seconds ?? 0;
  const p95 = stats.p95_duration_seconds ?? stats.p99_duration_seconds ?? 0;
  const p99 = stats.p99_duration_seconds ?? p95;
  return (
    <span className="health-duration-detail">
      <DetailMetric label="P75" value={formatSecondsSingleDecimal(p75)} tone="blue" labelFirst />
      <span className="separator"> · </span>
      <DetailMetric label="P95" value={formatSecondsSingleDecimal(p95)} tone="amber" labelFirst />
      <span className="separator"> · </span>
      <DetailMetric label="P99" value={formatSecondsSingleDecimal(p99)} tone="purple" labelFirst />
    </span>
  );
}

function ReportPanel({
  title,
  subtitle,
  titleTooltip,
  className,
  headerAction,
  children
}: {
  title: string;
  subtitle?: string;
  titleTooltip?: string;
  className?: string;
  headerAction?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className={`overview-report-panel${className ? ` ${className}` : ""}`}>
      <div className="overview-report-panel-header">
        <div className="overview-report-panel-title">
          <strong title={titleTooltip}>{title}</strong>
        </div>
        {subtitle ? <span className="overview-report-panel-subtitle">{subtitle}</span> : null}
        {headerAction ? <div className="overview-report-panel-action">{headerAction}</div> : null}
      </div>
      <div className="overview-report-panel-body">
        {children}
      </div>
    </section>
  );
}

function OperationHealthPanel({
  jobRows,
  dataflowRows
}: {
  jobRows: Array<Record<string, string | number>>;
  dataflowRows: Array<Record<string, string | number>>;
}) {
  const jobOperations = sortOperationRows(filterOperationRows(jobRows));
  const dataflowOperations = sortOperationRows(filterOperationRows(dataflowRows));
  if (!jobOperations.length && !dataflowOperations.length) {
    return <div className="table-empty">No operation signals in current filters.</div>;
  }
  return (
    <div className="overview-operation-health">
      <ReportChart option={operationHealthCombinedBarOption(jobOperations, dataflowOperations)} height="100%" />
    </div>
  );
}

function operationHealthCombinedBarOption(
  jobRows: Array<Record<string, string | number>>,
  dataflowRows: Array<Record<string, string | number>>
) {
  const hasJobRows = jobRows.length > 0;
  const hasDataflowRows = dataflowRows.length > 0;
  const jobLabels = jobRows.map((row) => String(row.operation_type ?? "unknown"));
  const dataflowLabels = dataflowRows.map((row) => String(row.operation_type ?? "unknown"));
  const hasBothGroups = hasJobRows && hasDataflowRows;
  const jobTop = hasBothGroups ? 14 : 8;
  const jobHeight = hasBothGroups ? "43%" : "86%";
  const dataflowTop = hasJobRows ? "54%" : 8;
  const dataflowHeight = hasBothGroups ? "43%" : "86%";
  const grids = [];
  const xAxes = [];
  const yAxes = [];
  const graphics = [];
  if (hasJobRows) {
    grids.push(fixedHorizontalBarGrid(76, false, { right: 14, top: jobTop, height: jobHeight }));
    xAxes.push(operationHealthValueAxis(0, "bottom"));
    yAxes.push(operationHealthCategoryAxis(jobLabels, 0));
    graphics.push(operationHealthGroupLabel("Job runs", hasBothGroups ? 0 : 0));
  }
  if (hasDataflowRows) {
    grids.push(fixedHorizontalBarGrid(76, false, { right: 14, top: dataflowTop, height: dataflowHeight }));
    xAxes.push(operationHealthValueAxis(hasJobRows ? 1 : 0, "bottom"));
    yAxes.push(operationHealthCategoryAxis(dataflowLabels, hasJobRows ? 1 : 0));
    graphics.push(operationHealthGroupLabel("Dataflow runs", hasJobRows ? "51.5%" : 0));
  }
  if (hasBothGroups) graphics.push(operationHealthDivider());
  const jobAxisIndex = hasJobRows ? 0 : -1;
  const dataflowAxisIndex = hasJobRows && hasDataflowRows ? 1 : hasDataflowRows ? 0 : -1;
  return baseChartOption({
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "shadow" },
      confine: false,
      appendToBody: true,
      extraCssText: "max-width: 260px; white-space: normal; line-height: 1.35; z-index: 9999;",
      formatter: (params: any) => {
        const first = Array.isArray(params) ? params[0] : params;
        const seriesName = String(first?.seriesName ?? "");
        const isDataflow = seriesName.startsWith("Dataflow ");
        const title = isDataflow ? "Dataflow runs" : "Job runs";
        const sourceRows = isDataflow ? dataflowRows : jobRows;
        const row = sourceRows[Number(first?.dataIndex ?? 0)] ?? {};
        return [
          `<strong>${title}</strong>`,
          ...operationHealthTooltip(row).split("\n")
        ].join("<br/>");
      }
    },
    legend: { show: false },
    grid: grids,
    xAxis: xAxes,
    yAxis: yAxes,
    graphic: graphics,
    series: [
      ...(hasJobRows
        ? OVERVIEW_STATUSES.map((status) => operationHealthStatusSeries("Job", status, jobRows, jobAxisIndex))
        : []),
      ...(hasDataflowRows
        ? OVERVIEW_STATUSES.map((status) => operationHealthStatusSeries("Dataflow", status, dataflowRows, dataflowAxisIndex))
        : [])
    ]
  });
}

function operationHealthValueAxis(gridIndex: number, position: "top" | "bottom") {
  return {
    type: "value" as const,
    gridIndex,
    position,
    axisLabel: { fontSize: 9, margin: 3, formatter: (value: number) => formatCompact(value) },
    axisTick: { show: false },
    axisLine: { lineStyle: { color: reportChartPalette.grid } },
    splitLine: { lineStyle: { color: reportChartPalette.grid } }
  };
}

function operationHealthCategoryAxis(labels: string[], gridIndex: number) {
  return {
    type: "category" as const,
    gridIndex,
    data: labels,
    inverse: true,
    boundaryGap: false,
    axisTick: { show: false },
    axisLabel: {
      fontSize: 10,
      color: reportChartPalette.muted,
      overflow: "truncate" as const,
      width: 76,
      margin: 4
    }
  };
}

function operationHealthStatusSeries(
  group: "Job" | "Dataflow",
  status: string,
  rows: Array<Record<string, string | number>>,
  axisIndex: number
) {
  return {
    name: `${group} ${status}`,
    type: "bar" as const,
    stack: `${group}-runs`,
    barWidth: 16,
    emphasis: { focus: "series" as const },
    itemStyle: { color: statusColor(status), borderRadius: 2 },
    xAxisIndex: axisIndex,
    yAxisIndex: axisIndex,
    data: rows.map((row) => num(row, status))
  };
}

function operationHealthGroupLabel(text: string, top: number | string) {
  return {
    type: "text" as const,
    left: 0,
    top,
    style: {
      text,
      fill: reportChartPalette.muted,
      font: "800 10px Inter, ui-sans-serif, system-ui",
      align: "left",
      backgroundColor: "#ffffff",
      padding: [1, 4, 1, 0],
      textTransform: "uppercase"
    },
    silent: true
  };
}

function operationHealthDivider() {
  return {
    type: "rect" as const,
    left: 0,
    right: 0,
    top: "50%",
    shape: { width: 520, height: 1 },
    style: {
      fill: reportChartPalette.grid
    },
    silent: true
  };
}

function RuntimePhaseContribution({
  rows,
  labelKey = "operation_type",
  emptyText = "No phase duration signals in current filters.",
  showLegend = true
}: {
  rows: Array<Record<string, string | number>>;
  labelKey?: string;
  emptyText?: string;
  showLegend?: boolean;
}) {
  const [tooltip, setTooltip] = useState<{
    row: Record<string, string | number>;
    style: CSSProperties;
  } | null>(null);
  const totalFromReport = rows.find((row) => num(row, "is_total") === 1);
  const visible = rows
    .filter((row) => num(row, "is_total") !== 1)
    .filter((row) => num(row, "total_duration_seconds") > 0)
    .sort((left, right) => {
      const duration = num(right, "total_duration_seconds") - num(left, "total_duration_seconds");
      if (duration !== 0) return duration;
      return phaseRowLabel(left, labelKey).localeCompare(phaseRowLabel(right, labelKey));
    });
  if (!visible.length) {
    return <div className="table-empty">{emptyText}</div>;
  }
  const totalRow = totalFromReport ?? runtimePhaseTotalRow(visible, labelKey);
  const displayRows = totalRow ? [totalRow, ...visible] : visible;
  return (
    <div className={`runtime-phase-contribution${showLegend ? "" : " runtime-phase-contribution-with-header-legend"}`}>
      {showLegend ? <RuntimePhaseLegend /> : null}
      <div className="runtime-phase-rows" style={{ gridTemplateRows: `repeat(${displayRows.length}, minmax(30px, 1fr))` }}>
        {displayRows.map((row) => {
          const totalDuration = num(row, "total_duration_seconds");
          const segments = runtimePhaseSegments(row);
          const label = phaseRowLabel(row, labelKey);
          const showTooltip = (target: HTMLElement) => {
            const rect = target.getBoundingClientRect();
            const width = Math.min(520, Math.max(320, window.innerWidth - 32));
            const left = Math.max(16, Math.min(window.innerWidth - width - 16, rect.right - width));
            const showBelow = rect.top < 210;
            setTooltip({
              row,
              style: {
                width,
                left,
                top: showBelow ? rect.bottom + 8 : rect.top - 8,
                transform: showBelow ? "none" : "translateY(-100%)"
              }
            });
          };
          return (
            <div
              key={label}
              className={`runtime-phase-operation-row${label === "Total" ? " runtime-phase-total-row" : ""}`}
              tabIndex={0}
              onMouseEnter={(event) => showTooltip(event.currentTarget)}
              onFocus={(event) => showTooltip(event.currentTarget)}
              onMouseLeave={() => setTooltip(null)}
              onBlur={() => setTooltip(null)}
            >
              <span className="runtime-phase-operation-label">
                <strong>{label}</strong>
                <small>{formatSeconds(totalDuration)}</small>
              </span>
              <div className="runtime-phase-bars">
                <div className="runtime-phase-stack">
                  {segments.map(({ phase, percent, width }) => {
                    return (
                      <span
                        key={phase}
                        className={`runtime-phase-segment phase-${phase}`}
                        style={{ flexBasis: `${width}%` }}
                      >
                        {percent >= 15 ? formatPhasePercent(percent) : null}
                      </span>
                    );
                  })}
                </div>
              </div>
            </div>
          );
        })}
      </div>
      {tooltip ? createPortal(
        <RuntimePhaseMatrixTooltip row={tooltip.row} labelKey={labelKey} style={tooltip.style} />,
        document.body
      ) : null}
    </div>
  );
}

function LifecycleStatusValues({
  skipped,
  running,
  pending
}: {
  skipped: number;
  running: number;
  pending: number;
}) {
  const items = lifecycleStatusItems(skipped, running, pending);
  return (
    <span className="job-lifecycle-status-values">
      {items.map((item, index) => (
        <span key={item.status}>
          {index ? <span className="separator"> / </span> : null}
          <span className={`job-lifecycle-status-value status-${item.status}`} style={{ color: item.color }}>
            {formatNumber(item.value)}
          </span>
        </span>
      ))}
    </span>
  );
}

function lifecycleStatusItems(skipped: number, running: number, pending: number) {
  return ([
    ["skipped", skipped],
    ["running", running],
    ["pending", pending]
  ] as const).map(([status, value]) => ({ status, value, color: statusColor(status) }));
}

type MonitoringChartLegendItem = readonly [label: string, color: string];

function MonitoringChartLegend({
  label,
  items
}: {
  label: string;
  items: ReadonlyArray<MonitoringChartLegendItem>;
}) {
  return (
    <div className="monitoring-chart-legend" aria-label={label}>
      {items.map(([itemLabel, color]) => (
        <span key={itemLabel}>
          <i style={{ backgroundColor: color }} aria-hidden="true" />
          {itemLabel}
        </span>
      ))}
    </div>
  );
}

function StatusTrendLegend() {
  return <MonitoringChartLegend label="Run status legend" items={[
    ...OVERVIEW_STATUSES.map((status) => [humanize(status), statusColor(status)] as const),
    ["Success rate", reportChartPalette.blue]
  ]} />;
}

function StatusHealthLegend() {
  return <MonitoringChartLegend
    label="Run status legend"
    items={OVERVIEW_STATUSES.map((status) => [humanize(status), statusColor(status)] as const)}
  />;
}

function WorkloadVolumeLegend() {
  return <MonitoringChartLegend label="Input and output workload legend" items={[
    ["Rows read", reportChartPalette.read],
    ["Est rows written", reportChartPalette.written],
    ["Bytes added", reportChartPalette.teal],
    ["Bytes removed", reportChartPalette.failed]
  ]} />;
}

export function healthCardAccentClass(accent: HealthCardAccent, intent: HealthIntent) {
  const resolved = accent === "intent" ? intent : accent;
  return `health-card-accent-${resolved}`;
}

function RuntimePhaseLegend() {
  return (
    <div className="runtime-phase-legend" aria-label="Phase legend">
      {PHASE_KEYS.map((phase) => (
        <span key={phase} className={`phase-chip phase-chip-${phase}`}>{phaseLabel(phase)}</span>
      ))}
    </div>
  );
}

function runtimePhaseContributionTooltip(grouping: string) {
  return [
    `Groups dataflow runs by ${grouping}.`,
    "Runs, Total, AVG, P95, and Failed exclude pending and running runs; eligible statuses are succeeded, failed, and skipped.",
    "Source, Transform, and Destination FAILED use their respective phase status. A failed run with no failed explicit phase is attributed to Overhead.",
    "Overhead = total run duration − source − transform − destination (never below zero). AVG and P95 use available duration evidence; missing duration is not treated as zero."
  ].join("\n");
}

function RuntimePhaseMatrixTooltip({
  row,
  labelKey,
  style
}: {
  row: Record<string, unknown>;
  labelKey: string;
  style?: CSSProperties;
}) {
  const label = phaseRowLabel(row, labelKey);
  const totalDuration = num(row, "total_duration_seconds");
  const phaseRows = PHASE_KEYS.map((phase) => ({
    phase,
    label: phaseLabel(phase),
    percent: num(row, `${phase}_duration_percent`),
    duration: num(row, `${phase}_duration_seconds`),
    avg: num(row, `${phase}_avg_duration_seconds`),
    p95: num(row, `${phase}_p95_duration_seconds`),
    runs: num(row, `${phase}_run_count`),
    failed: num(row, `${phase}_failed`)
  })).filter((item) => item.percent > 0 || item.duration > 0 || item.runs > 0 || item.failed > 0);
  return (
    <div className="runtime-phase-tooltip" role="tooltip" style={style}>
      <div className="runtime-phase-tooltip-title">
        <strong>{label}</strong>
        <span>{formatSeconds(totalDuration)}</span>
      </div>
      <table>
        <thead>
          <tr>
            <th>Phase</th>
            <th>%</th>
            <th>Total</th>
            <th>AVG</th>
            <th>P95</th>
            <th>Runs</th>
            <th>Failed</th>
          </tr>
        </thead>
        <tbody>
          {phaseRows.map((item) => (
            <tr key={item.phase}>
              <td><span className={`phase-dot phase-${item.phase}`} />{item.label}</td>
              <td>{formatPhasePercent(item.percent)}</td>
              <td>{formatSeconds(item.duration)}</td>
              <td>{item.avg > 0 ? formatSeconds(item.avg) : "-"}</td>
              <td>{item.p95 > 0 ? formatSeconds(item.p95) : "-"}</td>
              <td>{formatNumber(item.runs)}</td>
              <td className={item.failed > 0 ? "status-bad" : ""}>{formatNumber(item.failed)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function runtimePhaseTotalRow(rows: Array<Record<string, string | number>>, labelKey: string) {
  const durations = Object.fromEntries(
    PHASE_KEYS.map((phase) => [
      `${phase}_duration_seconds`,
      rows.reduce((sum, row) => sum + num(row, `${phase}_duration_seconds`), 0)
    ])
  ) as Record<string, number>;
  const phaseDurationTotal = PHASE_KEYS.reduce((sum, phase) => sum + durations[`${phase}_duration_seconds`], 0);
  if (phaseDurationTotal <= 0) return null;
  const totalRow: Record<string, string | number> = {
    [labelKey]: "Total",
    group: "Total",
    total_duration_seconds: phaseDurationTotal
  };
  for (const phase of PHASE_KEYS) {
    const duration = durations[`${phase}_duration_seconds`];
    totalRow[`${phase}_duration_seconds`] = duration;
    totalRow[`${phase}_duration_percent`] = (duration / phaseDurationTotal) * 100;
    const runCount = rows.reduce((sum, row) => sum + num(row, `${phase}_run_count`), 0);
    totalRow[`${phase}_run_count`] = runCount;
    totalRow[`${phase}_failed`] = rows.reduce((sum, row) => sum + num(row, `${phase}_failed`), 0);
    totalRow[`${phase}_avg_duration_seconds`] = runCount > 0 ? duration / runCount : 0;
    totalRow[`${phase}_p95_duration_seconds`] = 0;
  }
  return totalRow;
}

function phaseRowLabel(row: Record<string, unknown>, labelKey: string) {
  return String(row[labelKey] ?? row.group ?? row.operation_type ?? "unknown");
}

function runtimePhaseSegments(row: Record<string, unknown>) {
  const rawSegments = PHASE_KEYS.map((phase) => ({
    phase,
    percent: Math.max(0, num(row, `${phase}_duration_percent`))
  })).filter((segment) => segment.percent > 0);
  const totalPercent = rawSegments.reduce((sum, segment) => sum + segment.percent, 0);
  if (totalPercent <= 0) return [];
  return rawSegments.map((segment) => ({
    ...segment,
    width: (segment.percent / totalPercent) * 100
  }));
}

function WorkloadVolumeContextPanel({
  report,
  filters,
  dateRange,
  timezoneName,
  effectiveGrain
}: {
  report: MonitoringReport;
  filters: MonitoringFilters;
  dateRange: { min?: string | null; max?: string | null };
  timezoneName: string;
  effectiveGrain?: string;
}) {
  const trendOption = workloadVolumeTrendOption(
    report.volume.rows_by_date ?? [],
    report.volume.bytes_by_date ?? [],
    filters,
    dateRange,
    timezoneName,
    effectiveGrain,
    false
  );
  return (
    <div className="overview-workload-context">
      {trendOption ? (
        <div className="overview-workload-trend">
          <ReportChart option={trendOption} height="100%" />
        </div>
      ) : (
        <div className="table-empty">No workload volume trend in current filters.</div>
      )}
    </div>
  );
}

function workloadVolumeTrendOption(
  rowsByDate: Array<Record<string, string | number | null>>,
  bytesByDate: Array<Record<string, string | number | null>>,
  filters: MonitoringFilters,
  dateRange: { min?: string | null; max?: string | null },
  timezoneName: string,
  reportEffectiveGrain?: string,
  showLegend = true
) {
  const mergedByDate = mergeVolumeTrendRows(rowsByDate, bytesByDate);
  const effectiveGrain = String(mergedByDate.find((row) => row.grain)?.grain ?? reportEffectiveGrain ?? filters.grain ?? "day");
  const knownDateKeys = mergedByDate.map((row) => row.bucket || row.date).filter((date) => date && date !== "unknown");
  const dateKeys = resolveTrendBucketKeys(filters, dateRange, timezoneName, knownDateKeys, effectiveGrain);
  const mergedByDateMap = new Map(mergedByDate.map((row) => [row.bucket || row.date, row]));
  const visible =
    dateKeys.length > 0
      ? dateKeys.map((dateKey) => mergedByDateMap.get(dateKey) ?? createEmptyVolumeTrendRow(dateKey, effectiveGrain))
      : mergedByDate;
  if (!visible.length) return null;
  return baseChartOption({
    tooltip: {
      trigger: "axis",
      confine: true,
      formatter: (params: any) => {
        const first = Array.isArray(params) ? params[0] : params;
        const index = Number(first?.dataIndex ?? 0);
        const row = visible[index] ?? { date: "" };
        return [
          row.bucket || row.date || "",
          row.grain ? `Grain: ${row.grain}` : "",
          timezoneName ? `Timezone: ${timezoneName}` : "",
          `Rows read: ${formatNumber(row.rows_read)}`,
          `Estimated rows written: ${formatNumber(row.est_rows_written)}`,
          `Observed lakehouse rows written: ${formatNumber(row.rows_written)}`,
          `Lakehouse bytes added: ${formatBytes(num(row, "bytes_added"))}`,
          `Lakehouse bytes removed: ${formatBytes(num(row, "bytes_removed"))}`
        ].filter(Boolean).join("<br/>");
      }
    },
    legend: showLegend
      ? {
          top: 0,
          left: "center",
          itemWidth: 9,
          itemHeight: 9,
          textStyle: { fontSize: 10 }
        }
      : { show: false },
    grid: reportChartGrid({ left: 46, right: 52, top: showLegend ? 22 : 5, bottom: 5, containLabel: false }),
    xAxis: {
      type: "category",
      data: visible.map((row) => row.bucket || row.date),
      axisLabel: { fontSize: 10, hideOverlap: true },
      axisTick: { show: false }
    },
    yAxis: [
      {
        type: "value",
        name: "rows",
        nameTextStyle: { fontSize: 9, color: reportChartPalette.muted, padding: [0, 0, 0, 26] },
        axisLabel: { fontSize: 10, formatter: (value: number) => formatCompact(value) },
        splitLine: { lineStyle: { color: reportChartPalette.grid } }
      },
      {
        type: "value",
        name: "lakehouse bytes",
        nameTextStyle: { fontSize: 9, color: reportChartPalette.muted, padding: [0, 26, 0, 0] },
        axisLabel: { fontSize: 10, formatter: (value: number) => formatBytesShort(value) },
        splitLine: { show: false }
      }
    ],
    series: [
      {
        name: "Rows read",
        type: "bar",
        itemStyle: { color: reportChartPalette.read, borderRadius: [3, 3, 0, 0] },
        data: visible.map((row) => row.rows_read)
      },
      {
        name: "Est rows written",
        type: "bar",
        itemStyle: { color: reportChartPalette.written, borderRadius: [3, 3, 0, 0] },
        data: visible.map((row) => row.est_rows_written)
      },
      {
        name: "Lakehouse bytes added",
        type: "line",
        yAxisIndex: 1,
        connectNulls: true,
        showSymbol: false,
        showAllSymbol: false,
        symbol: "circle",
        symbolSize: 5,
        clip: false,
        z: 6,
        smooth: 0.2,
        lineStyle: { width: 1.5, color: reportChartPalette.teal },
        itemStyle: { color: "#ffffff", borderColor: reportChartPalette.teal, borderWidth: 1.3 },
        data: visible.map((row) => row.bytes_added)
      },
      {
        name: "Lakehouse bytes removed",
        type: "line",
        yAxisIndex: 1,
        connectNulls: true,
        showSymbol: false,
        showAllSymbol: false,
        symbol: "circle",
        symbolSize: 5,
        clip: false,
        z: 6,
        smooth: 0.2,
        lineStyle: { width: 1.5, color: reportChartPalette.failed },
        itemStyle: { color: "#ffffff", borderColor: reportChartPalette.failed, borderWidth: 1.3 },
        data: visible.map((row) => row.bytes_removed)
      }
    ]
  });
}

function jobStatusTrendOption(
  rows: Array<Record<string, string | number | null>>,
  filters: MonitoringFilters,
  dateRange: { min?: string | null; max?: string | null },
  timezoneName: string,
  effectiveGrain?: string
) {
  return statusTrendOption(rows, "jobs", filters, dateRange, timezoneName, effectiveGrain);
}

function dataflowStatusTrendOption(
  rows: Array<Record<string, string | number | null>>,
  filters: MonitoringFilters,
  dateRange: { min?: string | null; max?: string | null },
  timezoneName: string,
  effectiveGrain?: string
) {
  return statusTrendOption(rows, "dataflows", filters, dateRange, timezoneName, effectiveGrain);
}

function statusTrendOption(
  rows: Array<Record<string, string | number | null>>,
  runLabel: "jobs" | "dataflows",
  filters: MonitoringFilters,
  dateRange: { min?: string | null; max?: string | null },
  timezoneName: string,
  reportEffectiveGrain?: string
) {
  const visible = fillMissingTrendDates(rows, filters, dateRange, timezoneName, reportEffectiveGrain);
  const successRatePoints = visible.map((row) => trendLineRate(row, "success_rate", "succeeded"));
  const barSeries = OVERVIEW_STATUSES.map((status) => ({
    name: status,
    type: "bar" as const,
    stack: "runs",
    emphasis: { focus: "series" as const },
    itemStyle: { color: statusColor(status) },
    data: visible.map((row) => num(row, status)),
    yAxisIndex: 0
  }));
  return baseChartOption({
    tooltip: {
      trigger: "axis",
      confine: true,
      formatter: (params: any) => {
        const first = Array.isArray(params) ? params[0] : params;
        const index = Number(first?.dataIndex ?? 0);
        const row = visible[index] ?? {};
        const succeeded = num(row, "succeeded");
        const failed = num(row, "failed");
        const skipped = num(row, "skipped");
        const running = num(row, "running");
        const pending = num(row, "pending");
        const total = Number(row.total ?? succeeded + failed + skipped + running + pending + num(row, "unknown"));
        const successRate = trendRate(row, "success_rate", "succeeded");
        const failureRate = trendRate(row, "failure_rate", "failed");
        const rateLabel = successRate === null ? "N/A" : `${successRate.toFixed(2)}%`;
        const failureLabel = failureRate === null ? "N/A" : `${failureRate.toFixed(2)}%`;
        const bucketLabel = String(row.bucket ?? row.date ?? "");
        const grainLabel = row.grain ? `Grain: ${row.grain}` : "";
        const timezoneLabel = timezoneName ? `Timezone: ${timezoneName}` : "";
        const noRunLabel = total === 0 ? `No ${runLabel} ran in this bucket` : "";
        return [
          bucketLabel,
          grainLabel,
          timezoneLabel,
          `Total ${runLabel}: ${formatNumber(total)}`,
          `Succeeded: ${formatNumber(succeeded)}`,
          `Failed: ${formatNumber(failed)}`,
          `Skipped: ${formatNumber(skipped)}`,
          `Running: ${formatNumber(running)}`,
          `Pending: ${formatNumber(pending)}`,
          `Success rate: ${rateLabel}`,
          `Failure rate: ${failureLabel}`,
          noRunLabel
        ]
          .filter(Boolean)
          .join("<br/>");
      }
    },
    legend: { show: false },
    grid: reportChartGrid({ left: 36, right: 40, top: 5, bottom: 5, containLabel: false }),
    xAxis: {
      type: "category",
      data: visible.map((row) => rowLabel(row)),
      axisLabel: { fontSize: 10, hideOverlap: true },
      axisTick: { show: false }
    },
    yAxis: [
      {
        type: "value",
        axisLabel: { fontSize: 10 },
        splitLine: { lineStyle: { color: reportChartPalette.grid } }
      },
      {
        type: "value",
        min: 0,
        max: 100,
        axisLabel: { fontSize: 10, formatter: (value: number) => `${value}%` },
        splitLine: { show: false }
      }
    ],
    series: [
      ...barSeries,
      {
        name: "success rate",
        type: "line" as const,
        yAxisIndex: 1,
        connectNulls: true,
        showSymbol: false,
        showAllSymbol: false,
        symbol: "circle",
        symbolSize: 5,
        clip: false,
        z: 6,
        smooth: 0.2,
        lineStyle: { width: 1.6, color: reportChartPalette.blue },
        itemStyle: { color: "#ffffff", borderColor: reportChartPalette.blue, borderWidth: 1.5 },
        data: successRatePoints
      }
    ]
  });
}

function fillMissingTrendDates(
  rows: Array<Record<string, string | number | null>>,
  filters: MonitoringFilters,
  dateRange: { min?: string | null; max?: string | null },
  timezoneName: string,
  reportEffectiveGrain?: string
) {
  const effectiveGrain = String(rows.find((row) => row.grain)?.grain ?? reportEffectiveGrain ?? filters.grain ?? "day");
  const known = rows.filter((row) => rowLabel(row) !== "unknown");
  if (!known.length) {
    const keys = resolveTrendBucketKeys(filters, dateRange, timezoneName, [], effectiveGrain);
    return keys.map((dateKey) => createEmptyTrendRow(dateKey, effectiveGrain));
  }
  const rowByDate = new Map<string, Record<string, string | number | null>>();
  for (const row of known) {
    const dateKey = normalizeTrendBucketKey(rowLabel(row), effectiveGrain, timezoneName);
    if (!dateKey) continue;
    rowByDate.set(dateKey, { ...row, bucket: dateKey, date: dateKey });
  }
  const keys = resolveTrendBucketKeys(filters, dateRange, timezoneName, Array.from(rowByDate.keys()), effectiveGrain);
  if (!keys.length) return known;
  return keys.map((dateKey) => rowByDate.get(dateKey) ?? createEmptyTrendRow(dateKey, effectiveGrain));
}

function createEmptyTrendRow(dateKey: string, grain: string): Record<string, string | number> {
  return {
    date: dateKey,
    bucket: dateKey,
    grain: normalizeTrendGrain(grain),
    succeeded: 0,
    failed: 0,
    skipped: 0,
    running: 0,
    pending: 0,
    unknown: 0,
    total: 0,
    executable_total: 0
  };
}

function sortOperationRows(rows: Array<Record<string, string | number>>) {
  return rows
    .slice()
    .sort((left, right) => {
      const failed = num(right, "failed") - num(left, "failed");
      if (failed !== 0) return failed;
      const active = num(right, "running") + num(right, "pending") - (num(left, "running") + num(left, "pending"));
      if (active !== 0) return active;
      const total = operationTotal(right) - operationTotal(left);
      if (total !== 0) return total;
      return String(left.operation_type ?? "unknown").localeCompare(String(right.operation_type ?? "unknown"));
    });
}

function filterOperationRows(rows: Array<Record<string, string | number>>) {
  return rows.filter((row) => {
    const operation = String(row.operation_type ?? "unknown").trim();
    return operation !== "" && operation !== "not_available";
  });
}

function operationTotal(row: Record<string, unknown>) {
  return num(row, "count") || OVERVIEW_STATUSES.reduce((sum, status) => sum + num(row, status), 0);
}

function operationHealthTooltip(row: Record<string, unknown>) {
  return [
    String(row.operation_type ?? "unknown"),
    `Total: ${formatNumber(operationTotal(row))}`,
    `Succeeded: ${formatNumber(num(row, "succeeded"))}`,
    `Failed: ${formatNumber(num(row, "failed"))}`,
    `Skipped: ${formatNumber(num(row, "skipped"))}`,
    `Running: ${formatNumber(num(row, "running"))}`,
    `Pending: ${formatNumber(num(row, "pending"))}`
  ].join("\n");
}

function phaseLabel(phase: string) {
  if (phase === "source") return "Source";
  if (phase === "transform") return "Transform";
  if (phase === "destination") return "Destination";
  if (phase === "overhead") return "Overhead";
  return phase;
}

function createEmptyVolumeTrendRow(dateKey: string, grain: string) {
  return {
    date: dateKey,
    bucket: dateKey,
    grain: normalizeTrendGrain(grain),
    rows_read: 0,
    rows_written: 0,
    est_rows_written: 0,
    rows_output: 0,
    rows_output_estimated: 0,
    dataflow_runs: 0,
    bytes_added: 0,
    bytes_removed: 0,
    bytes_saved: 0
  };
}

function resolveTrendBucketKeys(
  filters: MonitoringFilters,
  dateRange: { min?: string | null; max?: string | null },
  timezoneName: string,
  knownDateKeys: string[],
  grainValue: string
) {
  const normalizedGrain = normalizeTrendGrain(grainValue);
  const grain = normalizedGrain === "auto" ? resolveClientTrendGrain(normalizedGrain, filters) : normalizedGrain;
  if (grain === "hour") return resolveTrendHourKeys(filters, dateRange, timezoneName, knownDateKeys);
  if (grain === "week") return resolveTrendWeekKeys(filters, dateRange, timezoneName, knownDateKeys);
  if (grain === "month") return resolveTrendMonthKeys(filters, dateRange, timezoneName, knownDateKeys);
  return resolveTrendDateKeys(filters, dateRange, timezoneName, knownDateKeys);
}

function normalizeTrendBucketKey(value: unknown, grainValue: string, timezoneName: string) {
  const rawValue = String(value ?? "").trim();
  if (!rawValue) return "";
  const grain = normalizeTrendGrain(grainValue);
  if (grain === "hour") {
    if (/^\d{4}-\d{2}-\d{2} \d{2}:00$/u.test(rawValue)) return rawValue;
    const timestamp = Date.parse(rawValue);
    return Number.isFinite(timestamp) ? timezoneHourKey(timestamp, timezoneName) : "";
  }
  if (grain === "week" && /^\d{4}-W\d{2}$/u.test(rawValue)) return rawValue;
  if (grain === "month" && /^\d{4}-\d{2}$/u.test(rawValue)) return rawValue;
  const dateKey = normalizeToDateKey(rawValue, timezoneName);
  if (!dateKey) return "";
  if (grain === "week") return weekKeyFromDateKey(dateKey);
  if (grain === "month") return dateKey.slice(0, 7);
  return dateKey;
}

function resolveTrendDateKeys(
  filters: MonitoringFilters,
  dateRange: { min?: string | null; max?: string | null },
  timezoneName: string,
  knownDateKeys: string[]
) {
  const range = filters.range;
  const today = timezoneDateKey(Date.now(), timezoneName);
  const minKnown = knownDateKeys.length ? knownDateKeys.slice().sort()[0] : "";
  const maxKnown = knownDateKeys.length ? knownDateKeys.slice().sort()[knownDateKeys.length - 1] : "";
  if (range === "24h" || range === "3d" || range === "7d" || range === "30d" || range === "90d") {
    const days = range === "24h" ? 1 : range === "3d" ? 3 : range === "7d" ? 7 : range === "90d" ? 90 : 30;
    if (!today) return [];
    const start = addDaysToDateKey(today, -(days - 1));
    return enumerateDateKeys(start, today);
  }
  if (range === "custom") {
    let start = normalizeToDateKey(filters.startTime, timezoneName) || normalizeToDateKey(dateRange.min ?? "", timezoneName) || minKnown;
    let end = normalizeToDateKey(filters.endTime, timezoneName) || normalizeToDateKey(dateRange.max ?? "", timezoneName) || maxKnown || today;
    if (!start) start = end;
    if (!end) end = start;
    return enumerateDateKeys(start, end);
  }
  if (range === "all") {
    let start = normalizeToDateKey(dateRange.min ?? "", timezoneName) || minKnown;
    let end = today || normalizeToDateKey(dateRange.max ?? "", timezoneName) || maxKnown;
    if (!start) start = end;
    if (!end) end = start;
    return enumerateDateKeys(start, end);
  }
  return enumerateDateKeys(minKnown, maxKnown);
}

function resolveTrendHourKeys(
  filters: MonitoringFilters,
  dateRange: { min?: string | null; max?: string | null },
  timezoneName: string,
  knownDateKeys: string[]
) {
  const range = filters.range;
  const now = Date.now();
  const minKnown = knownDateKeys.length ? knownDateKeys.slice().sort()[0] : "";
  const maxKnown = knownDateKeys.length ? knownDateKeys.slice().sort()[knownDateKeys.length - 1] : "";
  if (range === "24h" || range === "3d" || range === "7d" || range === "30d" || range === "90d") {
    const hours = range === "24h" ? 24 : range === "3d" ? 3 * 24 : range === "7d" ? 7 * 24 : range === "90d" ? 90 * 24 : 30 * 24;
    const endHour = floorToHour(now);
    const startHour = endHour - (hours - 1) * 3600 * 1000;
    return enumerateHourKeys(startHour, endHour, timezoneName);
  }
  if (range === "custom") {
    const start = parseFilterTime(filters.startTime) ?? parseFilterTime(dateRange.min ?? "") ?? parseHourKey(minKnown);
    const end = parseFilterTime(filters.endTime) ?? parseFilterTime(dateRange.max ?? "") ?? parseHourKey(maxKnown) ?? start;
    return enumerateHourKeys(start ?? end ?? now, end ?? start ?? now, timezoneName);
  }
  if (range === "all") {
    return knownDateKeys.slice().sort();
  }
  return knownDateKeys.slice().sort();
}

function resolveTrendWeekKeys(
  filters: MonitoringFilters,
  dateRange: { min?: string | null; max?: string | null },
  timezoneName: string,
  knownDateKeys: string[]
) {
  const dateKeys = resolveTrendDateKeys(filters, dateRange, timezoneName, knownDateKeys);
  if (!dateKeys.length) return knownDateKeys.slice().sort();
  return Array.from(new Set(dateKeys.map((dateKey) => weekKeyFromDateKey(dateKey)).filter(Boolean))).sort();
}

function resolveTrendMonthKeys(
  filters: MonitoringFilters,
  dateRange: { min?: string | null; max?: string | null },
  timezoneName: string,
  knownDateKeys: string[]
) {
  const dateKeys = resolveTrendDateKeys(filters, dateRange, timezoneName, knownDateKeys);
  if (!dateKeys.length) return knownDateKeys.slice().sort();
  return Array.from(new Set(dateKeys.map((dateKey) => dateKey.slice(0, 7)).filter(Boolean))).sort();
}

function rowLabel(row: Record<string, unknown>) {
  return String(row.bucket ?? row.date ?? "");
}

function normalizeToDateKey(value: string, timezoneName: string) {
  const trimmed = String(value ?? "").trim();
  if (!trimmed) return "";
  if (/^\d{4}-\d{2}-\d{2}$/.test(trimmed)) return trimmed;
  const timestamp = Date.parse(trimmed);
  if (!Number.isFinite(timestamp)) return "";
  return timezoneDateKey(timestamp, timezoneName);
}

function timezoneDateKey(timestamp: number, timezoneName: string): string {
  try {
    const formatter = new Intl.DateTimeFormat("en-CA", {
      timeZone: timezoneName,
      year: "numeric",
      month: "2-digit",
      day: "2-digit"
    });
    const parts = formatter.formatToParts(new Date(timestamp));
    const values = Object.fromEntries(parts.filter((part) => part.type !== "literal").map((part) => [part.type, part.value])) as Record<string, string>;
    if (!values.year || !values.month || !values.day) return "";
    return `${values.year}-${values.month}-${values.day}`;
  } catch {
    return "";
  }
}

function timezoneHourKey(timestamp: number, timezoneName: string): string {
  try {
    const formatter = new Intl.DateTimeFormat("en-CA", {
      timeZone: timezoneName,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      hourCycle: "h23"
    });
    const parts = formatter.formatToParts(new Date(timestamp));
    const values = Object.fromEntries(parts.filter((part) => part.type !== "literal").map((part) => [part.type, part.value])) as Record<string, string>;
    if (!values.year || !values.month || !values.day || !values.hour) return "";
    return `${values.year}-${values.month}-${values.day} ${values.hour}:00`;
  } catch {
    return "";
  }
}

function floorToHour(timestamp: number) {
  const date = new Date(timestamp);
  date.setUTCMinutes(0, 0, 0);
  return date.getTime();
}

function enumerateHourKeys(startTimestamp: number, endTimestamp: number, timezoneName: string) {
  if (!Number.isFinite(startTimestamp) || !Number.isFinite(endTimestamp)) return [];
  const from = Math.min(floorToHour(startTimestamp), floorToHour(endTimestamp));
  const to = Math.max(floorToHour(startTimestamp), floorToHour(endTimestamp));
  const keys: string[] = [];
  for (let cursor = from; cursor <= to; cursor += 3600 * 1000) {
    const key = timezoneHourKey(cursor, timezoneName);
    if (key && keys[keys.length - 1] !== key) keys.push(key);
  }
  return keys;
}

function parseFilterTime(value: string | null | undefined) {
  const trimmed = String(value ?? "").trim();
  if (!trimmed) return null;
  const timestamp = Date.parse(trimmed);
  return Number.isFinite(timestamp) ? timestamp : null;
}

function parseHourKey(value: string) {
  const match = /^(\d{4})-(\d{2})-(\d{2}) (\d{2}):00$/.exec(value);
  if (!match) return null;
  const [, year, month, day, hour] = match;
  return Date.UTC(Number(year), Number(month) - 1, Number(day), Number(hour));
}

function weekKeyFromDateKey(dateKey: string) {
  const date = dateKeyToUtcDate(dateKey);
  if (!date) return "";
  const day = date.getUTCDay() || 7;
  date.setUTCDate(date.getUTCDate() + 4 - day);
  const isoYear = date.getUTCFullYear();
  const yearStart = new Date(Date.UTC(isoYear, 0, 1));
  const week = Math.ceil(((date.getTime() - yearStart.getTime()) / 86400000 + 1) / 7);
  return `${isoYear}-W${String(week).padStart(2, "0")}`;
}

function resolveClientTrendGrain(grainValue: string, filters: MonitoringFilters) {
  const requestedGrain = normalizeTrendGrain(grainValue);
  const minimumGrain = minimumClientTrendGrain(filters);
  if (requestedGrain === "auto") return minimumGrain;
  const requestedIndex = DATE_GRAINS.indexOf(requestedGrain);
  const minimumIndex = DATE_GRAINS.indexOf(minimumGrain);
  if (requestedIndex < 0) return minimumGrain;
  return DATE_GRAINS[Math.max(requestedIndex, minimumIndex)];
}

function normalizeTrendGrain(grain: string) {
  return grain === "hour" || grain === "day" || grain === "week" || grain === "month" || grain === "auto" ? grain : "day";
}

const DATE_GRAINS = ["hour", "day", "week", "month"] as const;

function minimumClientTrendGrain(filters: MonitoringFilters) {
  if (filters.range === "24h" || filters.range === "3d") return "hour";
  if (filters.range === "7d" || filters.range === "30d" || filters.range === "90d") return "day";
  if (filters.range === "custom") {
    const start = parseFilterTime(filters.startTime);
    const end = parseFilterTime(filters.endTime);
    if (start !== null && end !== null) return minimumClientTrendGrainForSpan(Math.abs(end - start) / 1000);
  }
  if (filters.range === "all") return "month";
  return "day";
}

function minimumClientTrendGrainForSpan(spanSeconds: number) {
  if (spanSeconds <= 3 * 24 * 3600) return "hour";
  if (spanSeconds <= 90 * 86400) return "day";
  if (spanSeconds <= 365 * 86400) return "week";
  return "month";
}

export const monitoringTrendBucketTestUtils = {
  resolveTrendBucketKeys,
  fillMissingFailureTrendDates,
  workloadVolumeTrendOption
};

function dateKeyToUtcDate(dateKey: string) {
  const [year, month, day] = dateKey.split("-").map((value) => Number(value));
  if (!Number.isFinite(year) || !Number.isFinite(month) || !Number.isFinite(day)) return null;
  return new Date(Date.UTC(year, month - 1, day));
}

function utcDateToDateKey(date: Date) {
  const year = date.getUTCFullYear();
  const month = String(date.getUTCMonth() + 1).padStart(2, "0");
  const day = String(date.getUTCDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function addDaysToDateKey(dateKey: string, offsetDays: number) {
  const date = dateKeyToUtcDate(dateKey);
  if (!date) return "";
  date.setUTCDate(date.getUTCDate() + offsetDays);
  return utcDateToDateKey(date);
}

function enumerateDateKeys(startDateKey: string, endDateKey: string) {
  const start = dateKeyToUtcDate(startDateKey);
  const end = dateKeyToUtcDate(endDateKey);
  if (!start || !end) return [];
  const from = start.getTime() <= end.getTime() ? start : end;
  const to = start.getTime() <= end.getTime() ? end : start;
  const keys: string[] = [];
  for (const cursor = new Date(from.getTime()); cursor.getTime() <= to.getTime(); cursor.setUTCDate(cursor.getUTCDate() + 1)) {
    keys.push(utcDateToDateKey(cursor));
  }
  return keys;
}

function failureCategoryOption(rows: Array<Record<string, string | number>>) {
  const visible = rows
    .slice()
    .sort((left, right) => {
      const byCount = num(right, "count") - num(left, "count");
      if (byCount !== 0) return byCount;
      return String(left.category ?? "unknown").localeCompare(String(right.category ?? "unknown"));
    });
  const zoomConfig = horizontalBarDataZoom(visible.length);
  const hasZoom = Boolean(zoomConfig);
  const barSizing = horizontalBarSeriesSizing(visible.length);
  return baseChartOption({
    animation: false,
    animationDurationUpdate: 0,
    grid: fixedHorizontalBarGrid(110, hasZoom, { top: 12 }),
    tooltip: { trigger: "item", triggerOn: "mousemove", confine: true, axisPointer: { type: "none" } },
    xAxis: bottomAnchoredValueXAxis(),
    yAxis: fixedHorizontalCategoryAxis(visible.map((row) => String(row.category ?? "unknown")), 110),
    dataZoom: zoomConfig,
    series: [
      {
        name: "Failures",
        type: "bar",
        ...barSizing,
        itemStyle: { color: reportChartPalette.failed, borderRadius: [0, 3, 3, 0] },
        data: visible.map((row) => num(row, "count"))
      }
    ]
  });
}

function slowestDataflowOption(rows: Array<Record<string, unknown>>) {
  const hasP95 = rows.some((row) => typeof row.p95_duration_seconds === "number");
  const valueKey = hasP95 ? "p95_duration_seconds" : "duration_seconds";
  const seriesLabel = hasP95 ? "P95 duration" : "Duration";
  const visible = rows
    .slice()
    .sort((left, right) => valueOf(right, valueKey) - valueOf(left, valueKey));
  const zoomConfig = horizontalBarDataZoom(visible.length);
  const hasZoom = Boolean(zoomConfig);
  const barSizing = horizontalBarSeriesSizing(visible.length);
  return baseChartOption({
    animation: false,
    animationDurationUpdate: 0,
    grid: fixedHorizontalBarGrid(190, hasZoom, { top: 12 }),
    tooltip: { trigger: "item", triggerOn: "mousemove", confine: true, axisPointer: { type: "none" } },
    xAxis: bottomAnchoredValueXAxis({
      formatter: (value: number) => formatSeconds(value)
    }),
    yAxis: fixedHorizontalCategoryAxis(visible.map((row) => String(row.dataflow_name ?? row.dataflow_id ?? "unknown")), 190),
    dataZoom: zoomConfig,
    series: [
      {
        name: seriesLabel,
        type: "bar",
        ...barSizing,
        itemStyle: { color: reportChartPalette.amber, borderRadius: [0, 3, 3, 0] },
        data: visible.map((row) => valueOf(row, valueKey))
      }
    ]
  });
}

function slowestOrFailuresPanel(
  slowestRows: Array<Record<string, unknown>>,
  failureRows: Array<Record<string, string | number | null>>
): { option: ReturnType<typeof baseChartOption>; subtitle: string; empty: boolean } {
  const topFailures = failureRows
    .filter((row) => Number(row.error_count ?? 0) > 0)
    .sort((left, right) => {
      const byFailed = Number(right.error_count ?? 0) - Number(left.error_count ?? 0);
      if (byFailed !== 0) return byFailed;
      return String(left.dataflow_name ?? "unknown").localeCompare(String(right.dataflow_name ?? "unknown"));
    });
  if (topFailures.length) {
    const zoomConfig = horizontalBarDataZoom(topFailures.length);
    const hasZoom = Boolean(zoomConfig);
    const barSizing = horizontalBarSeriesSizing(topFailures.length);
    return {
      subtitle: "Failed dataflow runs",
      empty: false,
      option: baseChartOption({
        animation: false,
        animationDurationUpdate: 0,
        grid: fixedHorizontalBarGrid(180, hasZoom, { top: 12 }),
        tooltip: {
          trigger: "item",
          triggerOn: "mousemove",
          confine: true,
          axisPointer: { type: "none" },
          formatter: (params: any) => {
            const index = Number(params?.dataIndex ?? 0);
            const row = topFailures[index];
            if (!row) return "";
            return [
              String(row.dataflow_name ?? "unknown"),
              `Failed runs: ${formatNumber(Number(row.error_count ?? 0))}`,
              `Affected jobs: ${formatNumber(Number(row.affected_job_count ?? 0))}`,
              `Latest failure: ${String(row.last_time ?? "unknown")}`
            ].join("<br/>");
          }
        },
        xAxis: {
          ...bottomAnchoredValueXAxis()
        },
        yAxis: fixedHorizontalCategoryAxis(topFailures.map((row) => String(row.dataflow_name ?? "unknown")), 180),
        dataZoom: zoomConfig,
        series: [
          {
            name: "Failed runs",
            type: "bar",
            ...barSizing,
            itemStyle: { color: reportChartPalette.failed, borderRadius: [0, 3, 3, 0] },
            data: topFailures.map((row) => Number(row.error_count ?? 0))
          }
        ]
      })
    };
  }
  if (!slowestRows.length) {
    return {
      subtitle: "No failed or slow dataflow signals",
      empty: true,
      option: baseChartOption({})
    };
  }
  return {
    subtitle: "Top p95 duration dataflows",
    empty: false,
    option: slowestDataflowOption(slowestRows)
  };
}

function horizontalBarDataZoom(categoryCount: number, visibleRows = HORIZONTAL_BAR_VISIBLE_ROWS) {
  if (categoryCount <= visibleRows) return undefined;
  return [
    {
      type: "inside" as const,
      orient: "vertical" as const,
      yAxisIndex: [0],
      startValue: 0,
      endValue: visibleRows - 1,
      filterMode: "none" as const,
      zoomLock: true,
      moveOnMouseWheel: true,
      zoomOnMouseWheel: false,
      moveOnMouseMove: false
    },
    {
      type: "slider" as const,
      orient: "vertical" as const,
      yAxisIndex: [0],
      startValue: 0,
      endValue: visibleRows - 1,
      filterMode: "none" as const,
      zoomLock: true,
      width: 8,
      right: 2,
      showDataShadow: false,
      showDetail: false,
      brushSelect: false,
      handleSize: 0,
      moveHandleSize: 0,
      textStyle: { fontSize: 0 },
      borderColor: "rgba(17, 24, 39, 0.12)",
      backgroundColor: "rgba(17, 24, 39, 0.06)",
      fillerColor: "rgba(99, 102, 241, 0.18)"
    }
  ];
}

function horizontalBarSeriesSizing(categoryCount: number, visibleRows = HORIZONTAL_BAR_VISIBLE_ROWS) {
  if (categoryCount > visibleRows) {
    return {
      barWidth: 14,
      barCategoryGap: "42%"
    };
  }
  return {
    barMaxWidth: 22,
    barCategoryGap: categoryCount <= 4 ? "32%" : "38%"
  };
}

function fixedHorizontalBarGrid(
  labelWidth: number,
  hasZoom: boolean,
  overrides: Record<string, string | number | boolean> = {}
) {
  return {
    left: labelWidth + HORIZONTAL_BAR_LABEL_GAP,
    right: hasZoom ? 18 : 10,
    bottom: REPORT_CHART_GRID_BOTTOM,
    containLabel: false,
    ...overrides
  };
}

function reportChartGrid(overrides: Record<string, string | number | boolean> = {}) {
  return {
    left: 8,
    right: 10,
    top: 12,
    bottom: REPORT_CHART_GRID_BOTTOM,
    containLabel: true,
    ...overrides
  };
}

function reportTightChartGrid(overrides: Record<string, string | number | boolean> = {}) {
  return {
    ...reportChartGrid(overrides),
    bottom: REPORT_CHART_TIGHT_GRID_BOTTOM
  };
}

function reportXAxisZoomGrid(hasZoom: boolean, overrides: Record<string, string | number | boolean> = {}) {
  return {
    ...reportChartGrid(overrides),
    bottom: hasZoom ? REPORT_CHART_X_ZOOM_GRID_BOTTOM : REPORT_CHART_GRID_BOTTOM
  };
}

function fixedHorizontalCategoryAxis(
  labels: string[],
  labelWidth: number,
  overrides: Record<string, unknown> = {}
) {
  return {
    type: "category" as const,
    data: labels,
    inverse: true,
    axisTick: { show: false },
    axisLabel: fixedHorizontalAxisLabel(labelWidth),
    ...overrides
  };
}

function fixedHorizontalAxisLabel(labelWidth: number, overrides: Record<string, unknown> = {}) {
  return {
    show: true,
    align: "right" as const,
    fontSize: 10,
    color: reportChartPalette.muted,
    overflow: "truncate" as const,
    width: labelWidth,
    margin: 4,
    ...overrides
  };
}

function xCategoryDataZoom(categoryCount: number, visibleColumns = 12) {
  if (categoryCount <= visibleColumns) return undefined;
  return [
    {
      type: "inside" as const,
      xAxisIndex: [0],
      startValue: 0,
      endValue: visibleColumns - 1,
      filterMode: "filter" as const,
      zoomLock: true,
      moveOnMouseWheel: true,
      zoomOnMouseWheel: false,
      moveOnMouseMove: false
    },
    {
      type: "slider" as const,
      xAxisIndex: [0],
      startValue: 0,
      endValue: visibleColumns - 1,
      filterMode: "filter" as const,
      zoomLock: true,
      height: 8,
      bottom: 2,
      showDataShadow: false,
      showDetail: false,
      brushSelect: false,
      handleSize: 0,
      moveHandleSize: 0,
      textStyle: { fontSize: 0 },
      borderColor: "rgba(17, 24, 39, 0.12)",
      backgroundColor: "rgba(17, 24, 39, 0.06)",
      fillerColor: "rgba(99, 102, 241, 0.18)"
    }
  ];
}

function bottomAnchoredValueXAxis(
  axisLabelOverrides?: { formatter?: (value: number) => string }
) {
  return {
    type: "value" as const,
    position: "bottom" as const,
    axisLine: { onZero: false },
    axisLabel: {
      fontSize: 10,
      ...(axisLabelOverrides ?? {})
    },
    splitLine: { lineStyle: { color: reportChartPalette.grid } }
  };
}

function operationColor(operationType: string, index: number) {
  const palette = [
    "#2f7d7b",
    "#3d6fa8",
    "#8a6fd1",
    "#b87933",
    "#6f8f3a",
    "#a85f73",
    "#5c7f99",
    "#7a6a3a"
  ];
  let hash = index;
  for (let i = 0; i < operationType.length; i += 1) {
    hash = (hash * 31 + operationType.charCodeAt(i)) % palette.length;
  }
  return palette[Math.abs(hash) % palette.length];
}

function statusColor(status: string) {
  if (status === "succeeded") return reportChartPalette.success;
  if (status === "failed") return reportChartPalette.failed;
  if (status === "skipped") return reportChartPalette.skipped;
  if (status === "running") return reportChartPalette.running;
  if (status === "pending") return reportChartPalette.pending;
  return reportChartPalette.unknown;
}

function resolveAttentionTarget(target: string): MonitoringTabKey {
  if (target === "jobs") return "jobs";
  if (target === "dataflows") return "dataflows";
  if (target === "failures") return "failures";
  if (target === "performance") return "performance";
  if (target === "maintenance") return "maintenance";
  if (target === "freshness") return "freshness";
  if (target === "diagnostics" || target === "sources") return "diagnostics";
  return "overview";
}

function stageTotal(row: Record<string, string | number>) {
  return OVERVIEW_STATUSES.reduce((total, status) => total + num(row, status), 0);
}

function valueOf(row: Record<string, unknown>, key: string) {
  const value = row[key];
  return typeof value === "number" ? value : Number(value ?? 0);
}

function trendRate(row: Record<string, string | number | null>, key: "success_rate" | "failure_rate", basis: "succeeded" | "failed") {
  const explicit = row[key];
  if (typeof explicit === "number" && Number.isFinite(explicit)) return explicit;
  const succeeded = num(row, "succeeded");
  const failed = num(row, "failed");
  const executable = Number(row.executable_total ?? succeeded + failed);
  if (executable <= 0) return null;
  const numerator = basis === "succeeded" ? succeeded : failed;
  return Math.round((numerator / executable) * 10000) / 100;
}

function trendLineRate(row: Record<string, string | number | null>, key: "success_rate" | "failure_rate", basis: "succeeded" | "failed") {
  const total = Number(row.total ?? 0);
  if (Number.isFinite(total) && total === 0) return 0;
  return trendRate(row, key, basis);
}

function mergeVolumeTrendRows(
  rowsByDate: Array<Record<string, string | number | null>>,
  bytesByDate: Array<Record<string, string | number | null>> = []
) {
  type VolumeTrendBucket = {
    date: string;
    bucket: string;
    grain?: string;
    rows_read: number;
    rows_written: number;
    est_rows_written: number;
    rows_output: number;
    rows_output_estimated: number;
    dataflow_runs: number;
    bytes_added: number;
    bytes_removed: number;
    bytes_saved: number;
  };
  const buckets = new Map<
    string,
    VolumeTrendBucket
  >();
  const ensureBucket = (date: string) => {
    const existing = buckets.get(date);
    if (existing) return existing;
    const bucket: VolumeTrendBucket = {
      date,
      bucket: date,
      rows_read: 0,
      rows_written: 0,
      est_rows_written: 0,
      rows_output: 0,
      rows_output_estimated: 0,
      dataflow_runs: 0,
      bytes_added: 0,
      bytes_removed: 0,
      bytes_saved: 0
    };
    buckets.set(date, bucket);
    return bucket;
  };
  for (const row of rowsByDate) {
    const date = rowLabel(row);
    if (date === "unknown") continue;
    const bucket = ensureBucket(date);
    bucket.grain = String(row.grain ?? bucket.grain ?? "");
    bucket.rows_read = num(row, "rows_read");
    bucket.rows_written = num(row, "rows_written");
    bucket.est_rows_written = num(row, "est_rows_written");
    bucket.rows_output = num(row, "rows_output") || num(row, "rows_written");
    bucket.rows_output_estimated = num(row, "rows_output_estimated");
    bucket.dataflow_runs = num(row, "dataflow_runs");
  }
  for (const row of bytesByDate) {
    const date = rowLabel(row);
    if (date === "unknown") continue;
    const bucket = ensureBucket(date);
    bucket.grain = String(row.grain ?? bucket.grain ?? "");
    bucket.bytes_added = num(row, "bytes_added");
    bucket.bytes_removed = num(row, "bytes_removed");
    bucket.bytes_saved = num(row, "bytes_saved");
  }
  return Array.from(buckets.values()).sort((left, right) => left.date.localeCompare(right.date));
}

function formatCompact(value: number) {
  if (!Number.isFinite(value)) return "-";
  return new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 }).format(value);
}

function formatBytesShort(value: number) {
  if (!Number.isFinite(value) || value === 0) return "0";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let size = Math.abs(value);
  let unitIndex = 0;
  while (size >= 1024 && unitIndex < units.length - 1) {
    size /= 1024;
    unitIndex += 1;
  }
  const sign = value < 0 ? "-" : "";
  const digits = size >= 100 || unitIndex === 0 ? 0 : size >= 10 ? 1 : 2;
  return `${sign}${size.toFixed(digits)}${units[unitIndex]}`;
}

function formatPercent(value: number) {
  return `${Math.round(value * 100) / 100}%`;
}

function formatPhasePercent(value: number) {
  return `${Math.round(value * 10) / 10}%`;
}

function formatRelativeTime(value: string) {
  const timestamp = new Date(value).getTime();
  if (!Number.isFinite(timestamp)) return value;
  const seconds = Math.max(0, Math.floor((Date.now() - timestamp) / 1000));
  if (seconds < 60) return "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d ago`;
  return new Date(value).toLocaleDateString();
}

function shortIdentifier(value: unknown) {
  const text = value === null || value === undefined || value === "" ? "unknown" : String(value);
  if (text.length <= 18) return text;
  return `${text.slice(0, 8)}...${text.slice(-6)}`;
}

function formatSecondsSingleDecimal(value: number) {
  if (!Number.isFinite(value)) return "-";
  const formatter = new Intl.NumberFormat("en-US", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  if (value < 60) return `${formatter.format(value)}s`;
  return `${formatter.format(value / 60)}m`;
}

function successRateIntent(successRate: number, failureRate: number, executableRuns: number): HealthIntent {
  if (!Number.isFinite(executableRuns) || executableRuns <= 0) return "neutral";
  if (failureRate >= 5 || successRate < 95) return "bad";
  if (failureRate >= 1 || successRate < 99) return "warning";
  return "good";
}

function durationIntent(stats: Record<string, number>, fallbackAvg: number, fallbackP95: number): HealthIntent {
  const avg = Number(stats.avg_duration_seconds ?? fallbackAvg ?? 0);
  const p95 = Number(stats.p95_duration_seconds ?? fallbackP95 ?? 0);
  if (!Number.isFinite(avg) || !Number.isFinite(p95) || p95 <= 0) return "neutral";
  if (p95 >= Math.max(1200, avg * 3)) return "bad";
  if (p95 >= Math.max(600, avg * 2)) return "warning";
  return "good";
}

function JobStageHealthPanel({ rows }: { rows: Array<Record<string, string | number>> }) {
  const visible = rows
    .filter((row) => String(row.stage ?? "").trim() && stageTotal(row) > 0);
  if (!visible.length) return <div className="table-empty">No job x stage signals in current filters.</div>;
  return (
    <div className="overview-operation-health">
      <ReportChart option={jobStageHealthOption(visible)} height="100%" wheelDataZoomStep={1} />
    </div>
  );
}

function jobStageHealthOption(rows: Array<Record<string, string | number>>) {
  const labels = rows.map((row) => String(row.stage ?? "unknown"));
  const zoomConfig = horizontalBarDataZoom(labels.length);
  const barSizing = horizontalBarSeriesSizing(labels.length);
  return baseChartOption({
    tooltip: {
      trigger: "item",
      triggerOn: "mousemove",
      confine: false,
      appendToBody: true,
      extraCssText: "max-width: 280px; white-space: normal; word-break: break-word;",
      position: (point: [number, number], _params: any, _dom: unknown, _rect: unknown, size: any) => {
        const [x, y] = point;
        const [tooltipWidth, tooltipHeight] = size.contentSize;
        const [viewWidth, viewHeight] = size.viewSize;
        const left =
          x > viewWidth / 2
            ? Math.max(8, x - tooltipWidth - 24)
            : Math.min(viewWidth - tooltipWidth - 8, x + 24);
        const top = Math.max(8, Math.min(viewHeight - tooltipHeight - 8, y - tooltipHeight / 2));
        return [left, top];
      },
      formatter: (params: any) => {
        const row = rows[Number(params?.dataIndex ?? 0)] ?? {};
        return [
          `<strong>Stage: ${row.stage ?? "unknown"}</strong>`,
          `Touched jobs: ${formatNumber(num(row, "touched_jobs") || stageTotal(row))}`,
          `Succeeded: ${formatNumber(num(row, "succeeded"))}`,
          `Failed: ${formatNumber(num(row, "failed"))}`,
          `Skipped: ${formatNumber(num(row, "skipped"))}`,
          `Running: ${formatNumber(num(row, "running"))}`,
          `Pending: ${formatNumber(num(row, "pending"))}`
        ].join("<br/>");
      }
    },
    legend: { show: false },
    grid: fixedHorizontalBarGrid(116, Boolean(zoomConfig), { right: zoomConfig ? 24 : 10, top: 4 }),
    xAxis: {
      type: "value",
      min: 0,
      minInterval: 1,
      axisLabel: { fontSize: 9, margin: 3, formatter: (value: number) => formatCompact(value) },
      axisTick: { show: false },
      axisLine: { lineStyle: { color: reportChartPalette.grid } },
      splitLine: { lineStyle: { color: reportChartPalette.grid } }
    },
    yAxis: {
      type: "category",
      data: labels,
      inverse: true,
      axisTick: { show: false },
      axisLine: {
        show: true,
        lineStyle: { color: "#d9e1ea", width: 1 }
      },
      axisLabel: fixedHorizontalAxisLabel(104)
    },
    dataZoom: zoomConfig,
    series: OVERVIEW_STATUSES.map((status) => ({
      name: status,
      type: "bar" as const,
      stack: "job-stage-status",
      ...barSizing,
      emphasis: { focus: "series" as const },
      itemStyle: { color: statusColor(status), borderRadius: 2 },
      label: {
        show: true,
        position: "inside" as const,
        color: "#ffffff",
        fontSize: 9,
        formatter: (params: any) => {
          const value = Number(params?.value ?? 0);
          return value > 0 ? formatCompact(value) : "";
        }
      },
      data: rows.map((row) => num(row, status))
    }))
  });
}

function JobDurationByOperationBoxPlot({ rows }: { rows: Array<Record<string, unknown>> }) {
  return <DurationDistributionBoxPlot
    rows={rows}
    labelKey="operation_type"
    emptyText="No job operation duration data in current filters."
    entityKind="job"
  />;
}

function DurationDistributionBoxPlot({
  rows,
  labelKey,
  emptyText,
  entityKind = "dataflow"
}: {
  rows: Array<Record<string, unknown>>;
  labelKey: string;
  emptyText: string;
  entityKind?: "dataflow" | "job";
}) {
  const visible = rows.filter((row) => num(row, "count") > 0);
  if (!visible.length) return <div className="table-empty">{emptyText}</div>;
  return (
    <div className="monitoring-job-chart-fill">
      <ReportChart option={durationDistributionBoxOption(visible, labelKey, entityKind)} height="100%" wheelDataZoomStep={1} />
    </div>
  );
}

function durationDistributionBoxOption(
  rows: Array<Record<string, unknown>>,
  labelKey: string,
  entityKind: "dataflow" | "job" = "dataflow"
) {
  const visibleRows = 8;
  const zoomConfig = horizontalBarDataZoom(rows.length, visibleRows);
  const outlierPoints = rows.flatMap((row, rowIndex) => durationOutlierPoints(row, rowIndex));
  return baseChartOption({
    tooltip: {
      trigger: "item",
      confine: true,
      formatter: (params: any) => {
        const row = rows[Number(params?.dataIndex ?? 0)] ?? {};
        const label = row[labelKey] ?? row.group ?? "unknown";
        const operationMix = row.operation_mix ? `Operation mix: ${row.operation_mix}` : "";
        return [
          `<strong>${label}</strong>`,
          `${entityKind === "job" ? "Job" : "Dataflow"} runs: ${formatNumber(num(row, "count"))}`,
          `Outliers: ${formatNumber(num(row, "outlier_count"))}`,
          `Min: ${formatSeconds(num(row, "min_duration_seconds"))}`,
          `Q1: ${formatSeconds(num(row, "q1_duration_seconds"))}`,
          `Median: ${formatSeconds(num(row, "p50_duration_seconds"))}`,
          `Q3: ${formatSeconds(num(row, "q3_duration_seconds"))}`,
          `Max: ${formatSeconds(num(row, "max_duration_seconds"))}`,
          `Avg / P95: ${formatSeconds(num(row, "avg_duration_seconds"))} / ${formatSeconds(num(row, "p95_duration_seconds"))}`,
          `Succeeded / failed / skipped: ${formatNumber(num(row, "succeeded"))} / ${formatNumber(num(row, "failed"))} / ${formatNumber(num(row, "skipped"))}`,
          operationMix
        ].filter(Boolean).join("<br/>");
      }
    },
    grid: fixedHorizontalBarGrid(96, Boolean(zoomConfig), { left: 112, right: zoomConfig ? 24 : 10, top: 6 }),
    dataZoom: zoomConfig,
    xAxis: {
      type: "value",
      axisTick: { show: false },
      axisLabel: { fontSize: 10, formatter: (value: number) => formatSeconds(value) },
      splitLine: { lineStyle: { color: reportChartPalette.grid } }
    },
    yAxis: {
      type: "category",
      data: rows.map((row) => String(row[labelKey] ?? row.group ?? "unknown")),
      inverse: true,
      axisTick: { show: false },
      axisLine: {
        show: true,
        lineStyle: { color: "#d9e1ea", width: 1 }
      },
      axisLabel: {
        align: "right" as const,
        fontSize: 10,
        width: 102,
        overflow: "truncate" as const,
        margin: 8,
        color: reportChartPalette.muted
      }
    },
    series: [
      {
        name: "Duration",
        type: "boxplot" as const,
        itemStyle: {
          color: "rgba(61, 111, 168, 0.18)",
          borderColor: reportChartPalette.blue,
          borderWidth: 1.4
        },
        data: rows.map((row) => [
          num(row, "whisker_min_duration_seconds"),
          num(row, "q1_duration_seconds"),
          num(row, "p50_duration_seconds"),
          num(row, "q3_duration_seconds"),
          num(row, "whisker_max_duration_seconds")
        ])
      },
      {
        name: "Outliers",
        type: "scatter" as const,
        symbolSize: 5,
        itemStyle: {
          color: reportChartPalette.amber,
          borderColor: "#ffffff",
          borderWidth: 1
        },
        tooltip: {
          formatter: (params: any) => {
            const data = params?.data?.value ?? [];
            const meta = params?.data?.meta ?? {};
            if (entityKind === "job") {
              const runtime = [meta.engine_name, meta.metadata_provider_name, meta.platform_name]
                .map((value) => value || "unknown")
                .join(" / ");
              return [
                "<strong>Outlier</strong>",
                `Duration: ${formatSeconds(Number(data[0] ?? 0))}`,
                `Status: ${meta.status ?? "-"}`,
                `Runtime: ${runtime}`
              ].join("<br/>");
            }
            return [
              `<strong>${meta.stage ?? meta.group ?? "Outlier"}</strong>`,
              `Duration: ${formatSeconds(Number(data[0] ?? 0))}`,
              `Dataflow: ${meta.dataflow_name ?? "unknown"}`,
              `Run: ${meta.dataflow_run_id ?? "-"}`,
              `Status: ${meta.status ?? "-"}`,
              `Operation: ${meta.operation_type ?? "-"}`
            ].join("<br/>");
          }
        },
        data: outlierPoints as any
      }
    ]
  });
}

function durationOutlierPoints(row: Record<string, unknown>, rowIndex: number) {
  const rawOutliers = Array.isArray(row.outliers) ? row.outliers : [];
  const stage = String(row.stage ?? row.group ?? row.operation_type ?? "unknown");
  return rawOutliers
    .map((item) => {
      if (Array.isArray(item)) return {
        duration_seconds: item[0],
        dataflow_name: item[1],
        dataflow_run_id: item[2],
        status: item[3],
        operation_type: item[4],
        engine_name: item[5],
        metadata_provider_name: item[6],
        platform_name: item[7],
      };
      return item && typeof item === "object" ? item as Record<string, unknown> : null;
    })
    .filter((item): item is Record<string, unknown> => Boolean(item))
    .map((item) => ({
      value: [Number(item.duration_seconds ?? 0), rowIndex] as [number, number],
      meta: {
        ...item,
        stage,
        group: String(row.group ?? stage)
      }
    }));
}

function JobWorkloadEfficiencyScatter({
  rows,
  onInspect
}: {
  rows: Array<Record<string, string | number | null>>;
  onInspect?: (row: JobRecord) => void;
}) {
  const visible = rows.filter((row) => num(row, "duration_seconds") > 0).slice(0, 500);
  if (!visible.length) return <div className="table-empty">No workload efficiency points in current filters.</div>;
  return (
    <div className="monitoring-job-chart-fill">
      <ReportChart option={jobWorkloadEfficiencyOption(visible, onInspect)} height="100%" />
    </div>
  );
}

function WorkloadEfficiencyLegend({ rows }: { rows: Array<Record<string, string | number | null>> }) {
  const operationTypes = workloadEfficiencyOperationTypes(rows);
  return <MonitoringChartLegend
    label="Workload efficiency operation legend"
    items={operationTypes.map((operationType, index) => [humanize(operationType), operationColor(operationType, index)] as const)}
  />;
}

function workloadEfficiencyOperationTypes(rows: Array<Record<string, string | number | null>>) {
  return Array.from(new Set(
    rows
      .filter((row) => num(row, "duration_seconds") > 0)
      .slice(0, 500)
      .map((row) => String(row.operation_type ?? "unknown"))
  )).sort((left, right) => left.localeCompare(right));
}

function jobWorkloadEfficiencyOption(
  rows: Array<Record<string, string | number | null>>,
  onInspect?: (row: JobRecord) => void
) {
  const maxWorkload = Math.max(...rows.map((row) => num(row, "workload_size") || 1), 1);
  const maxChildren = Math.max(...rows.map((row) => num(row, "child_dataflow_count") || 0), 1);
  const operationTypes = workloadEfficiencyOperationTypes(rows);
  return baseChartOption({
    tooltip: {
      trigger: "item",
      confine: true,
      formatter: (params: any) => {
        const row = params?.data?.row ?? {};
        return [
          `<strong>${shortIdentifier(row.job_id)}</strong>`,
          `Status: ${row.status ?? "unknown"}`,
          `Dataflow operation: ${row.operation_type ?? "unknown"}`,
          `Dataflow runs: ${formatNumber(num(row, "child_dataflow_count"))}`,
          `Total duration: ${formatSeconds(num(row, "duration_seconds"))}`,
          `Rows read / written: ${formatNumber(num(row, "rows_read"))} / ${formatNumber(num(row, "rows_written"))}`,
          `Point size: rows read / duration (${formatNumber(num(row, "workload_size"))} rows/s)`,
          `Failed / skipped children: ${formatNumber(num(row, "failed_child_dataflows"))} / ${formatNumber(num(row, "skipped_child_dataflows"))}`,
          `Runtime: ${[row.platform_name, row.engine_name, row.metadata_provider_name].filter(Boolean).join(" / ") || "unknown"}`
        ].join("<br/>");
      }
    },
    legend: { show: false },
    grid: reportChartGrid({ left: 8, right: 8, top: 4, bottom: 0 }),
    xAxis: {
      type: "value",
      name: "dataflow runs",
      max: Math.ceil(maxChildren * 1.08),
      nameTextStyle: { fontSize: 9, color: reportChartPalette.muted },
      axisLabel: { fontSize: 10, formatter: (value: number) => formatCompact(value) },
      splitLine: { lineStyle: { color: reportChartPalette.grid } }
    },
    yAxis: {
      type: "value",
      name: "duration",
      nameTextStyle: { fontSize: 9, color: reportChartPalette.muted },
      axisLabel: { fontSize: 10, formatter: (value: number) => formatSeconds(value) },
      splitLine: { lineStyle: { color: reportChartPalette.grid } }
    },
    series: operationTypes.map((operationType, operationIndex) => {
      const operationRows = rows.filter((row) => String(row.operation_type ?? "unknown") === operationType);
      return {
        name: operationType,
        type: "scatter" as const,
        data: operationRows.map((row) => ({
          value: [num(row, "child_dataflow_count"), num(row, "duration_seconds"), num(row, "workload_size")],
          row
        })),
        symbolSize: (_value: unknown, params: any) => {
          const row = params?.data?.row ?? {};
          const workload = num(row, "workload_size") || 1;
          if (!workload || workload <= 1) return 6;
          return Math.max(6, Math.min(24, 6 + Math.sqrt(workload / maxWorkload) * 18));
        },
        itemStyle: {
          color: operationColor(operationType, operationIndex),
          opacity: 0.78,
          borderColor: "#ffffff",
          borderWidth: 1
        },
        emphasis: { focus: "self" as const }
      };
    })
  });
}

function ChildFanoutDistributionPanel({ rows }: { rows: Array<Record<string, string | number | null>> }) {
  const visible = buildFanoutHistogramBins(rows);
  if (!visible.length) return <div className="table-empty">No child fan-out histogram in current filters.</div>;
  return (
    <div className="monitoring-job-chart-fill">
      <ReportChart option={childFanoutDistributionOption(visible)} height="100%" />
    </div>
  );
}

function childFanoutDistributionOption(rows: Array<Record<string, string | number | null>>) {
  const zoomConfig = xCategoryDataZoom(rows.length, 16);
  return baseChartOption({
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "shadow" },
      confine: true,
      formatter: (params: any) => {
        const first = Array.isArray(params) ? params[0] : params;
        const row = rows[Number(first?.dataIndex ?? 0)] ?? {};
        return [
          `<strong>${row.bin_label ?? "unknown"}</strong>`,
          `Jobs: ${formatNumber(num(row, "jobs"))}`,
          `Bin range: ${formatNumber(num(row, "bin_start"))} to ${formatNumber(num(row, "bin_end"))} dataflows`,
          `Succeeded / failed / skipped: ${formatNumber(num(row, "succeeded"))} / ${formatNumber(num(row, "failed"))} / ${formatNumber(num(row, "skipped"))}`,
          `Running / pending: ${formatNumber(num(row, "running"))} / ${formatNumber(num(row, "pending"))}`
        ].join("<br/>");
      }
    },
    legend: { show: false },
    grid: {
      ...reportXAxisZoomGrid(Boolean(zoomConfig), { left: 8, right: 8, top: 16 }),
      bottom: zoomConfig ? REPORT_CHART_X_ZOOM_GRID_BOTTOM : 0,
      containLabel: false
    },
    xAxis: {
      type: "category",
      data: rows.map((row) => String(row.bin_label ?? "")),
      axisTick: { show: false },
      axisLabel: { fontSize: 9, color: reportChartPalette.muted, interval: 0, rotate: rows.length > 12 ? 28 : 0 }
    },
    yAxis: {
      type: "value",
      axisLabel: { fontSize: 10, formatter: (value: number) => formatCompact(value) },
      splitLine: { lineStyle: { color: reportChartPalette.grid } }
    },
    dataZoom: zoomConfig,
    series: [
      {
        name: "Jobs",
        type: "bar" as const,
        barCategoryGap: "0%",
        barGap: "0%",
        itemStyle: { color: reportChartPalette.blue, borderColor: "#ffffff", borderWidth: 0.8 },
        label: {
          show: true,
          position: "top" as const,
          color: reportChartPalette.text,
          fontSize: 9,
          formatter: (params: any) => {
            const value = Number(params?.value ?? 0);
            return value > 0 ? formatCompact(value) : "";
          }
        },
        data: rows.map((row) => num(row, "jobs"))
      }
    ]
  });
}

type FanoutHistogramBin = {
  bin_start: number;
  bin_end: number;
  bin_label: string;
  jobs: number;
  succeeded: number;
  failed: number;
  skipped: number;
  running: number;
  pending: number;
  unknown: number;
};

function buildFanoutHistogramBins(rows: Array<Record<string, string | number | null>>): FanoutHistogramBin[] {
  const source = rows
    .map((row) => ({
      totalDataflows: Math.max(0, Math.floor(num(row, "total_dataflows"))),
      jobs: num(row, "jobs"),
      succeeded: num(row, "succeeded"),
      failed: num(row, "failed"),
      skipped: num(row, "skipped"),
      running: num(row, "running"),
      pending: num(row, "pending"),
      unknown: num(row, "unknown")
    }))
    .filter((row) => row.jobs > 0);
  if (!source.length) return [];
  const maxValue = Math.max(...source.map((row) => row.totalDataflows), 1);
  const binSize = niceHistogramBinSize(maxValue, autoHistogramTargetBins(source));
  const maxBinStart = Math.floor(maxValue / binSize) * binSize;
  const bins: FanoutHistogramBin[] = [];
  for (let start = 0; start <= maxBinStart; start += binSize) {
    const end = start + binSize - 1;
    bins.push({
      bin_start: start,
      bin_end: end,
      bin_label: binSize === 1 ? String(start) : `${start}-${end}`,
      jobs: 0,
      succeeded: 0,
      failed: 0,
      skipped: 0,
      running: 0,
      pending: 0,
      unknown: 0
    });
  }
  source.forEach((row) => {
    const index = Math.min(bins.length - 1, Math.floor(row.totalDataflows / binSize));
    const target = bins[index];
    if (!target) return;
    target.jobs += row.jobs;
    target.succeeded += row.succeeded;
    target.failed += row.failed;
    target.skipped += row.skipped;
    target.running += row.running;
    target.pending += row.pending;
    target.unknown += row.unknown;
  });
  return bins;
}

function autoHistogramTargetBins(rows: Array<{ totalDataflows: number; jobs: number }>) {
  const totalJobs = rows.reduce((sum, row) => sum + row.jobs, 0);
  const distinctValues = new Set(rows.map((row) => row.totalDataflows)).size;
  const sqrtRule = Math.ceil(Math.sqrt(Math.max(totalJobs, 1)) * 1.6);
  const sturgesRule = Math.ceil(Math.log2(Math.max(totalJobs, 1)) + 1);
  const densityRule = Math.min(24, Math.max(1, distinctValues * 2));
  return Math.max(8, Math.min(24, Math.max(sqrtRule, sturgesRule, densityRule)));
}

function niceHistogramBinSize(maxValue: number, targetBins: number) {
  if (maxValue <= targetBins) return 1;
  const raw = maxValue / targetBins;
  const magnitude = Math.pow(10, Math.floor(Math.log10(raw)));
  const normalized = raw / magnitude;
  const nice = normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 5 ? 5 : 10;
  return Math.max(1, nice * magnitude);
}

function JobRuntimeConfigSignals({
  rows,
  engineRows
}: {
  rows: JobRecord[];
  engineRows: Array<Record<string, string | number>>;
}) {
  const signals = buildJobRuntimeConfigSignals(rows, engineRows).slice(0, 8);
  if (!signals.length) return <div className="table-empty">No runtime or configuration signals in current filters.</div>;
  return (
    <div className="job-config-signal-list">
      {signals.map((signal) => (
        <div key={`${signal.kind}-${signal.label}-${signal.value}`} className="job-config-signal-row" title={signal.title}>
          <strong>{signal.label}</strong>
          <span>{signal.value}</span>
          <em className={signal.failed ? "status-bad" : ""}>{signal.detail}</em>
        </div>
      ))}
    </div>
  );
}

function buildJobRuntimeConfigSignals(rows: JobRecord[], engineRows: Array<Record<string, string | number>>) {
  const runtimeSignals = engineRows.slice(0, 3).map((row) => ({
    kind: "runtime",
    label: String(row.engine_name ?? "Engine"),
    value: [row.metadata_provider_name, row.platform_name].filter(Boolean).join(" / ") || "runtime",
    detail: `${formatNumber(num(row, "jobs"))} jobs · ${formatPercent(num(row, "success_rate"))}`,
    failed: num(row, "failed"),
    title: `Engine: ${row.engine_name ?? "unknown"}\nProvider: ${row.metadata_provider_name ?? "unknown"}\nPlatform: ${row.platform_name ?? "unknown"}\nJobs: ${formatNumber(num(row, "jobs"))}\nFailed: ${formatNumber(num(row, "failed"))}`
  }));
  const configSignals = [
    jobConfigModeSignal(rows, "max_workers", "Max workers"),
    jobConfigModeSignal(rows, "retry_count", "Retry count"),
    jobConfigModeSignal(rows, "stop_on_error", "Stop on error"),
    jobConfigModeSignal(rows, "dry_run", "Dry run"),
    jobConfigModeSignal(rows, "retention_hours", "Retention")
  ].filter(Boolean) as Array<{ kind: string; label: string; value: string; detail: string; failed: number; title: string }>;
  return [...runtimeSignals, ...configSignals];
}

function jobConfigModeSignal(rows: JobRecord[], key: string, label: string) {
  const buckets = new Map<string, { count: number; failed: number }>();
  rows.forEach((row) => {
    const rawValue = row[key];
    if (!isMeaningfulConfigValue(rawValue)) return;
    const value = formatConfigValue(rawValue, key);
    const bucket = buckets.get(value) ?? { count: 0, failed: 0 };
    bucket.count += 1;
    if (String(row.status ?? "").toLowerCase() === "failed") bucket.failed += 1;
    buckets.set(value, bucket);
  });
  const [value, bucket] = Array.from(buckets.entries()).sort((left, right) => {
    const count = right[1].count - left[1].count;
    if (count !== 0) return count;
    return left[0].localeCompare(right[0]);
  })[0] ?? [];
  if (!value || !bucket) return null;
  return {
    kind: "config",
    label,
    value,
    detail: `${formatNumber(bucket.count)} jobs${bucket.failed ? ` · ${formatNumber(bucket.failed)} failed` : ""}`,
    failed: bucket.failed,
    title: `${label}: ${value}\nJobs: ${formatNumber(bucket.count)}\nFailed: ${formatNumber(bucket.failed)}`
  };
}

function isMeaningfulConfigValue(value: unknown) {
  return value !== null && value !== undefined && value !== "";
}

function formatConfigValue(value: unknown, key: string) {
  if (typeof value === "boolean") return value ? "true" : "false";
  if (key === "retention_hours" && typeof value === "number") return `${formatNumber(value)}h`;
  return String(value);
}

function SlowestJobsList({
  rows,
  p95Duration,
  p99Duration,
  onInspect
}: {
  rows: JobRecord[];
  p95Duration: number;
  p99Duration: number;
  onInspect?: (row: JobRecord) => void;
}) {
  const visible = rows.slice(0, 8);
  if (!visible.length) return <div className="table-empty">No job durations in current filters.</div>;
  return (
    <div className="monitoring-slowest-jobs-list">
      {visible.map((row, index) => {
        const duration = num(row, "duration_seconds");
        const failedChildren = num(row, "child_failed_count");
        const skippedChildren = num(row, "child_skipped_count");
        const totalChildren = num(row, "child_dataflow_count") || num(row, "total_dataflows");
        const mismatch = num(row, "reconciliation_mismatch_count");
        const severity = p99Duration && duration >= p99Duration ? "p99" : p95Duration && duration >= p95Duration ? "p95" : "normal";
        const runtime = [row.engine_name, row.metadata_provider_name, row.platform_name].filter(Boolean).join(" / ") || "unknown runtime";
        const durationBadge = severity === "p99" ? "P99+" : severity === "p95" ? "P95+" : String(row.status ?? "unknown");
        const impactDetail = `${formatNumber(failedChildren)} / ${formatNumber(totalChildren)} child${skippedChildren ? ` · ${formatNumber(skippedChildren)} skipped` : ""}${mismatch ? ` · ${formatNumber(mismatch)} mismatch` : ""}`;
        return (
          <button
            key={`${row.job_id}-${index}`}
            type="button"
            className={`monitoring-slowest-job-row slowest-${severity}`}
            title={`${row.job_id ?? ""}\n${runtime}\nDuration: ${formatSeconds(duration)}\n${impactDetail}`}
            onClick={() => onInspect?.(row)}
          >
            <span className="slowest-job-rank">{index + 1}</span>
            <span className="slowest-job-main">
              <strong>{shortIdentifier(row.job_id)}</strong>
            </span>
            <span className="slowest-job-runtime">{runtime}</span>
            <span className="slowest-job-duration">
              <strong>{formatSeconds(duration)}</strong>
              <small>{durationBadge}</small>
            </span>
            <span className="slowest-job-impact">
              <strong className={failedChildren ? "status-bad" : ""}>
                {formatNumber(failedChildren)} / {formatNumber(totalChildren)}
              </strong>
              <small>{skippedChildren ? `${formatNumber(skippedChildren)} skipped` : "child flows"}{mismatch ? ` · ${formatNumber(mismatch)} mismatch` : ""}</small>
            </span>
          </button>
        );
      })}
    </div>
  );
}

type RuntimeContextSortKey =
  | "engine_name"
  | "metadata_provider_name"
  | "platform_name"
  | "jobs"
  | "success_rate"
  | "avg_duration_seconds"
  | "p95_duration_seconds";

const RUNTIME_CONTEXT_AUTO_COLUMNS = [
  "Engine",
  "Provider",
  "Platform",
  "Jobs",
  "Success",
  "AVG / P95"
];
const RUNTIME_CONTEXT_COLUMN_GAP = 8;
const RUNTIME_CONTEXT_MIN_COLUMN_WIDTHS = [64, 68, 64, 46, 54, 66];
const RUNTIME_CONTEXT_MAX_COLUMN_WIDTHS = [190, 190, 150, 86, 82, 112];

function EngineProviderHealth({ rows }: { rows: Array<Record<string, string | number>> }) {
  const matrixRef = useRef<HTMLDivElement | null>(null);
  const [sort, setSort] = useState<{ key: RuntimeContextSortKey; dir: "asc" | "desc" }>({ key: "jobs", dir: "desc" });
  const [columnWidths, setColumnWidths] = useState<Array<number | null>>(() => RUNTIME_CONTEXT_AUTO_COLUMNS.map(() => null));
  const [matrixWidth, setMatrixWidth] = useState(0);
  const visible = useMemo(() => sortRuntimeContextRows(rows, sort.key, sort.dir), [rows, sort.key, sort.dir]);
  const autoColumnWidths = useMemo(() => buildRuntimeContextAutoWidths(visible), [visible]);
  const fittedColumnWidths = useMemo(
    () => fitRuntimeContextColumnWidths(columnWidths.map((width, index) => width ?? autoColumnWidths[index]), matrixWidth),
    [autoColumnWidths, columnWidths, matrixWidth]
  );
  const gridTemplateColumns = useMemo(
    () => fittedColumnWidths.map((width) => `${width}px`).join(" "),
    [fittedColumnWidths]
  );
  const gridStyle: CSSProperties = { gridTemplateColumns };
  useEffect(() => {
    const element = matrixRef.current;
    if (!element || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(([entry]) => {
      setMatrixWidth(Math.floor(entry.contentRect.width));
    });
    observer.observe(element);
    setMatrixWidth(Math.floor(element.getBoundingClientRect().width));
    return () => observer.disconnect();
  }, []);
  if (!visible.length) return <div className="table-empty">No runtime context in current filters.</div>;
  function toggleSort(key: RuntimeContextSortKey) {
    setSort((current) => ({ key, dir: current.key === key && current.dir === "desc" ? "asc" : "desc" }));
  }
  function startColumnResize(event: ReactPointerEvent<HTMLSpanElement>, index: number) {
    event.preventDefault();
    event.stopPropagation();
    const headerCell = event.currentTarget.parentElement;
    if (!headerCell) return;
    const startX = event.clientX;
    const startWidth = headerCell.getBoundingClientRect().width;
    const minWidth = RUNTIME_CONTEXT_MIN_COLUMN_WIDTHS[index] ?? 52;
    const handleMove = (moveEvent: PointerEvent) => {
      const nextWidth = Math.max(minWidth, Math.round(startWidth + moveEvent.clientX - startX));
      setColumnWidths((current) => current.map((width, widthIndex) => (widthIndex === index ? nextWidth : width)));
    };
    const handleUp = () => {
      window.removeEventListener("pointermove", handleMove);
      window.removeEventListener("pointerup", handleUp);
    };
    window.addEventListener("pointermove", handleMove);
    window.addEventListener("pointerup", handleUp);
  }
  function resetColumnWidth(index: number) {
    setColumnWidths((current) => current.map((width, widthIndex) => (widthIndex === index ? null : width)));
  }
  return (
    <div className="monitoring-compact-matrix" ref={matrixRef}>
      <div className="monitoring-compact-matrix-header" style={gridStyle}>
        <RuntimeContextHeaderCell
          label={`Engine${sortIndicator(sort, "engine_name")}`}
          title="Sort by engine"
          onSort={() => toggleSort("engine_name")}
          onResize={(event) => startColumnResize(event, 0)}
          onReset={() => resetColumnWidth(0)}
        />
        <RuntimeContextHeaderCell
          label={`Provider${sortIndicator(sort, "metadata_provider_name")}`}
          title="Sort by provider"
          onSort={() => toggleSort("metadata_provider_name")}
          onResize={(event) => startColumnResize(event, 1)}
          onReset={() => resetColumnWidth(1)}
        />
        <RuntimeContextHeaderCell
          label={`Platform${sortIndicator(sort, "platform_name")}`}
          title="Sort by platform"
          onSort={() => toggleSort("platform_name")}
          onResize={(event) => startColumnResize(event, 2)}
          onReset={() => resetColumnWidth(2)}
        />
        <RuntimeContextHeaderCell
          label={`Jobs${sortIndicator(sort, "jobs")}`}
          title="Sort by job runs"
          onSort={() => toggleSort("jobs")}
          onResize={(event) => startColumnResize(event, 3)}
          onReset={() => resetColumnWidth(3)}
        />
        <RuntimeContextHeaderCell
          label={`Success${sortIndicator(sort, "success_rate")}`}
          title="Sort by success rate"
          onSort={() => toggleSort("success_rate")}
          onResize={(event) => startColumnResize(event, 4)}
          onReset={() => resetColumnWidth(4)}
        />
        <RuntimeContextHeaderCell
          label={`AVG / P95${sortIndicator(sort, "avg_duration_seconds")}`}
          title="Sort by average duration. P95 is the second value in each row."
          onSort={() => toggleSort("avg_duration_seconds")}
          onResize={(event) => startColumnResize(event, 5)}
          onReset={() => resetColumnWidth(5)}
        />
      </div>
      {visible.map((row, index) => {
        const failed = num(row, "failed");
        const successRate = num(row, "success_rate");
        return (
          <div key={`${row.engine_name}-${row.metadata_provider_name}-${index}`} className="monitoring-compact-matrix-row" style={gridStyle}>
            <span className="monitoring-stack-cell" title={`${row.engine_name ?? "unknown"} / ${row.metadata_provider_name ?? "unknown"} / ${row.platform_name ?? "unknown"}`}>
              <strong>{row.engine_name ?? "unknown"}</strong>
            </span>
            <span title={String(row.metadata_provider_name ?? "unknown")}>{row.metadata_provider_name ?? "unknown"}</span>
            <span title={String(row.platform_name ?? "unknown")}>{row.platform_name ?? "unknown"}</span>
            <span>
              <strong>{formatNumber(num(row, "jobs"))}</strong>
              <small><span className="status-bad">{formatNumber(failed)}</span> failed</small>
            </span>
            <span className={successRate < 95 ? "status-bad" : "status-good"}>{formatPercent(successRate)}</span>
            <span>
              <strong>{formatSeconds(num(row, "avg_duration_seconds"))}</strong>
              <small>P95 {formatSeconds(num(row, "p95_duration_seconds"))}</small>
            </span>
          </div>
        );
      })}
    </div>
  );
}

function RuntimeContextHeaderCell({
  label,
  title,
  onSort,
  onResize,
  onReset
}: {
  label: string;
  title: string;
  onSort: () => void;
  onResize: (event: ReactPointerEvent<HTMLSpanElement>) => void;
  onReset: () => void;
}) {
  return (
    <span className="monitoring-resizable-header-cell">
      <button type="button" onClick={onSort} title={title}>
        {label}
      </button>
      <span
        className="monitoring-column-resizer"
        role="separator"
        aria-orientation="vertical"
        title="Drag to resize. Double click to auto width."
        onPointerDown={onResize}
        onDoubleClick={(event) => {
          event.preventDefault();
          event.stopPropagation();
          onReset();
        }}
      />
    </span>
  );
}

function buildRuntimeContextAutoWidths(rows: Array<Record<string, string | number>>) {
  const columnValues = RUNTIME_CONTEXT_AUTO_COLUMNS.map((label) => [label]);
  rows.forEach((row) => {
    const failed = num(row, "failed");
    columnValues[0].push(String(row.engine_name ?? "unknown"));
    columnValues[1].push(String(row.metadata_provider_name ?? "unknown"));
    columnValues[2].push(String(row.platform_name ?? "unknown"));
    columnValues[3].push(formatNumber(num(row, "jobs")), failed ? `${formatNumber(failed)} failed` : "0 failed");
    columnValues[4].push(formatPercent(num(row, "success_rate")));
    columnValues[5].push(formatSeconds(num(row, "avg_duration_seconds")), `P95 ${formatSeconds(num(row, "p95_duration_seconds"))}`);
  });
  return columnValues.map((values, index) => {
    const maxTextWidth = values.reduce((maxWidth, value) => Math.max(maxWidth, measureRuntimeContextText(value)), 0);
    const padding = index < 3 ? 22 : 16;
    return clampNumber(
      Math.ceil(maxTextWidth + padding),
      RUNTIME_CONTEXT_MIN_COLUMN_WIDTHS[index] ?? 52,
      RUNTIME_CONTEXT_MAX_COLUMN_WIDTHS[index] ?? 220
    );
  });
}

function fitRuntimeContextColumnWidths(widths: number[], availableWidth: number) {
  if (!availableWidth) return widths;
  const gapWidth = RUNTIME_CONTEXT_COLUMN_GAP * Math.max(0, widths.length - 1);
  const targetWidth = Math.max(0, availableWidth - gapWidth);
  const totalWidth = widths.reduce((sum, width) => sum + width, 0);
  if (totalWidth <= targetWidth) return widths;

  const minimums = RUNTIME_CONTEXT_MIN_COLUMN_WIDTHS;
  const minTotalWidth = minimums.reduce((sum, width) => sum + width, 0);
  if (targetWidth <= minTotalWidth) return minimums;

  const shrinkableTotal = widths.reduce((sum, width, index) => sum + Math.max(0, width - minimums[index]), 0);
  if (!shrinkableTotal) return widths;
  const shrinkNeeded = totalWidth - targetWidth;

  return widths.map((width, index) => {
    const minWidth = minimums[index];
    const shrinkable = Math.max(0, width - minWidth);
    const shrink = (shrinkable / shrinkableTotal) * shrinkNeeded;
    return Math.max(minWidth, Math.floor(width - shrink));
  });
}

let runtimeContextMeasureCanvas: HTMLCanvasElement | null = null;

function measureRuntimeContextText(value: string) {
  if (typeof document === "undefined") {
    return value.length * 7;
  }
  runtimeContextMeasureCanvas ??= document.createElement("canvas");
  const context = runtimeContextMeasureCanvas.getContext("2d");
  if (!context) return value.length * 7;
  context.font = "750 12px Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif";
  return context.measureText(value).width;
}

function clampNumber(value: number, min: number, max: number) {
  return Math.max(min, Math.min(max, value));
}

function sortRuntimeContextRows(
  rows: Array<Record<string, string | number>>,
  key: RuntimeContextSortKey,
  dir: "asc" | "desc"
) {
  const multiplier = dir === "asc" ? 1 : -1;
  return rows.slice().sort((left, right) => {
    const leftValue = runtimeContextSortValue(left, key);
    const rightValue = runtimeContextSortValue(right, key);
    if (typeof leftValue === "number" && typeof rightValue === "number") {
      const delta = leftValue - rightValue;
      if (delta !== 0) return delta * multiplier;
    } else {
      const delta = String(leftValue).localeCompare(String(rightValue));
      if (delta !== 0) return delta * multiplier;
    }
    const failed = num(right, "failed") - num(left, "failed");
    if (failed !== 0) return failed;
    return String(left.engine_name ?? "").localeCompare(String(right.engine_name ?? ""));
  });
}

function runtimeContextSortValue(row: Record<string, string | number>, key: RuntimeContextSortKey) {
  if (key === "jobs") return num(row, "jobs");
  if (key === "success_rate") return num(row, "success_rate");
  if (key === "avg_duration_seconds") return num(row, "avg_duration_seconds");
  if (key === "p95_duration_seconds") return num(row, "p95_duration_seconds");
  return String(row[key] ?? "unknown");
}

function sortIndicator(sort: { key: RuntimeContextSortKey; dir: "asc" | "desc" }, key: RuntimeContextSortKey) {
  return sort.key === key ? (sort.dir === "asc" ? " ↑" : " ↓") : "";
}

function ChildImpactList({
  rows,
  onInspect
}: {
  rows: Array<Record<string, string | number | null>>;
  onInspect?: (row: JobRecord) => void;
}) {
  const visible = rows.slice(0, 8);
  if (!visible.length) return <div className="table-empty">No child dataflow impact in current filters.</div>;
  return (
    <div className="monitoring-impact-list">
      {visible.map((row, index) => {
        const failed = num(row, "child_failed_count");
        const skipped = num(row, "child_skipped_count");
        const total = num(row, "child_dataflow_count");
        const mismatch = num(row, "reconciliation_mismatch_count");
        return (
          <button
            key={`${row.job_id}-${index}`}
            type="button"
            className="monitoring-impact-row"
            title={String(row.job_id ?? "")}
            onClick={() => onInspect?.(row as JobRecord)}
          >
            <strong>{shortIdentifier(row.job_id)}</strong>
            <span>
              <em className={failed ? "status-bad" : ""}>{formatNumber(failed)} failed</em>
              <em>{formatNumber(skipped)} skipped</em>
              <em>{formatNumber(total)} total</em>
            </span>
            {mismatch ? <small className="status-warning">{formatNumber(mismatch)} mismatch</small> : <small>matched</small>}
          </button>
        );
      })}
    </div>
  );
}

function JobAttentionList({
  rows,
  allJobs,
  onInspect
}: {
  rows: Array<Record<string, string | number | null>>;
  allJobs: JobRecord[];
  onInspect?: (row: JobRecord) => void;
}) {
  const visible = rows.slice(0, 8);
  if (!visible.length) return <div className="table-empty">No immediate job issues.</div>;
  return (
    <div className="overview-attention-compact monitoring-job-attention-list">
      {visible.map((item, index) => {
        const jobId = String(item.job_id ?? "");
        const matchedJob = allJobs.find((job) => String(job.job_id ?? "") === jobId);
        return (
          <button
            key={`${item.code}-${jobId}-${index}`}
            type="button"
            className={`overview-attention-item attention-${item.severity ?? "info"}`}
            onClick={() => onInspect?.(matchedJob ?? ({ job_id: jobId, status: item.severity, error_message: item.detail } as JobRecord))}
          >
            <strong>{item.title ?? "Job signal"}</strong>
            <span>{item.detail ?? jobId}</span>
          </button>
        );
      })}
    </div>
  );
}

function DataflowNameStatusHealthPanel({ rows }: { rows: Array<Record<string, string | number | null>> }) {
  const visible = rows.filter((row) => String(row.dataflow_name ?? "").trim() && stageTotal(row as Record<string, string | number>) > 0);
  if (!visible.length) return <div className="table-empty">No dataflow status health signals in current filters.</div>;
  return (
    <div className="overview-operation-health">
      <ReportChart option={dataflowNameStatusHealthOption(visible)} height="100%" wheelDataZoomStep={1} />
    </div>
  );
}

function dataflowNameStatusHealthOption(rows: Array<Record<string, string | number | null>>) {
  const labels = rows.map((row) => String(row.dataflow_name ?? "unknown"));
  const zoomConfig = horizontalBarDataZoom(labels.length);
  const barSizing = horizontalBarSeriesSizing(labels.length);
  return baseChartOption({
    tooltip: {
      trigger: "item",
      triggerOn: "mousemove",
      confine: false,
      appendToBody: true,
      extraCssText: "max-width: 300px; white-space: normal; word-break: break-word;",
      position: (point: number[], _params: unknown, _dom: unknown, _rect: unknown, size: { contentSize: number[]; viewSize: number[] }) => {
        const [x, y] = point;
        const [tooltipWidth, tooltipHeight] = size.contentSize;
        const [viewWidth, viewHeight] = size.viewSize;
        const left = x > viewWidth / 2 ? Math.max(8, x - tooltipWidth - 24) : Math.min(viewWidth - tooltipWidth - 8, x + 24);
        const top = Math.max(8, Math.min(viewHeight - tooltipHeight - 8, y - tooltipHeight / 2));
        return [left, top];
      },
      formatter: (params: any) => {
        const row = rows[Number(params?.dataIndex ?? 0)] ?? {};
        const statusLine = `S ${formatNumber(num(row, "succeeded"))} · F ${formatNumber(num(row, "failed"))} · Skip ${formatNumber(num(row, "skipped"))} · Run ${formatNumber(num(row, "running"))} · Pend ${formatNumber(num(row, "pending"))}`;
        return [
          `<div style="min-width:220px;max-width:300px">`,
          `<strong>${row.dataflow_name ?? "unknown"}</strong>`,
          `<div style="margin-top:4px;color:#536176">${row.stage ?? "unknown"} · ${row.operation_type ?? "unknown"}</div>`,
          `<div style="margin-top:6px">${formatNumber(num(row, "runs"))} runs · ${formatPercent(num(row, "success_rate"))} success</div>`,
          `<div style="margin-top:4px">${statusLine}</div>`,
          `<div style="margin-top:4px">AVG ${formatSeconds(num(row, "avg_duration_seconds"))} · P95 ${formatSeconds(num(row, "p95_duration_seconds"))} · Max ${formatSeconds(num(row, "max_duration_seconds"))}</div>`,
          `<div style="margin-top:4px">Rows ${formatNumber(num(row, "rows_read"))} / ${formatNumber(num(row, "rows_written"))}</div>`,
          `<div style="margin-top:4px;color:#536176">${row.source_name ?? "unknown"} -> ${row.destination_name ?? "unknown"}</div>`,
          `</div>`
        ].join("");
      }
    },
    legend: { show: false },
    grid: fixedHorizontalBarGrid(128, Boolean(zoomConfig), { right: zoomConfig ? 24 : 10, top: 4 }),
    xAxis: {
      type: "value",
      min: 0,
      minInterval: 1,
      axisLabel: { fontSize: 9, margin: 3, formatter: (value: number) => formatCompact(value) },
      axisTick: { show: false },
      axisLine: { lineStyle: { color: reportChartPalette.grid } },
      splitLine: { lineStyle: { color: reportChartPalette.grid } }
    },
    yAxis: {
      type: "category",
      data: labels,
      inverse: true,
      axisTick: { show: false },
      axisLine: {
        show: true,
        lineStyle: { color: "#d9e1ea", width: 1 }
      },
      axisLabel: fixedHorizontalAxisLabel(116)
    },
    dataZoom: zoomConfig,
    series: OVERVIEW_STATUSES.map((status) => ({
      name: status,
      type: "bar" as const,
      stack: "dataflow-name-status",
      ...barSizing,
      emphasis: { focus: "series" as const },
      itemStyle: { color: statusColor(status), borderRadius: 2 },
      label: {
        show: true,
        position: "inside" as const,
        color: "#ffffff",
        fontSize: 9,
        formatter: (params: any) => {
          const value = Number(params?.value ?? 0);
          return value > 0 ? formatCompact(value) : "";
        }
      },
      data: rows.map((row) => num(row, status))
    }))
  });
}

function DataflowEndpointHealthPanel({ rows }: { rows: Array<Record<string, string | number | null>> }) {
  const visible = rows.filter((row) => num(row, "runs") > 0);
  if (!visible.length) return <div className="table-empty">No endpoint health signals in current filters.</div>;
  return (
    <div className="dataflow-signal-list dataflow-endpoint-route-list">
      {visible.map((row, index) => {
        const failed = num(row, "failed");
        const succeeded = num(row, "succeeded");
        const successRate = num(row, "success_rate");
        const executableRuns = succeeded + failed;
        const failureRate = executableRuns > 0 ? (failed / executableRuns) * 100 : 0;
        const healthIntent = successRateIntent(successRate, failureRate, executableRuns);
        const rowsRead = num(row, "rows_read");
        const rowsWritten = num(row, "rows_written");
        const p95 = num(row, "p95_duration_seconds");
        const source = endpointRoutePresentation(row, "source");
        const destination = endpointRoutePresentation(row, "destination");
        const sourceFormat = String(row.source_format ?? row.source_connection_type ?? "unknown");
        const destinationFormat = String(row.destination_format ?? row.destination_connection_type ?? "unknown");
        return (
          <div
            key={`${source.connection}-${source.locator}-${destination.connection}-${destination.locator}-${index}`}
            className="dataflow-signal-row dataflow-endpoint-route-row"
            title={[
              `Source connection: ${source.connection}`,
              `Destination connection: ${destination.connection}`,
              `Source format: ${sourceFormat}`,
              `Destination format: ${destinationFormat}`,
              `Runs: ${formatNumber(num(row, "runs"))}`,
              `Succeeded / failed / skipped: ${formatNumber(num(row, "succeeded"))} / ${formatNumber(failed)} / ${formatNumber(num(row, "skipped"))}`,
              `Success rate: ${formatPercent(successRate)}`,
              `AVG / P95 duration: ${formatSeconds(num(row, "avg_duration_seconds"))} / ${formatSeconds(p95)}`,
              `Rows read / written: ${formatNumber(rowsRead)} / ${formatNumber(rowsWritten)}`,
              `Bytes added / removed: ${formatBytes(num(row, "bytes_added"))} / ${formatBytes(num(row, "bytes_removed"))}`
            ].join("\n")}
          >
            <div className="dataflow-route-flow">
              <div className="dataflow-route-line">
                <EndpointRouteNode endpoint={source} />
                <span className="dataflow-route-arrow" aria-hidden="true">→</span>
                <EndpointRouteNode endpoint={destination} />
              </div>
            </div>
            <div className="dataflow-route-health">
              <strong className={`status-${healthIntent}`}>{formatPercent(successRate)}</strong>
              <small>
                <span>{formatNumber(num(row, "runs"))} runs</span>
                {failed ? <><span aria-hidden="true"> · </span><span className="dataflow-route-failures">{formatNumber(failed)} failed</span></> : null}
              </small>
            </div>
            <div className="dataflow-route-volume">
              <strong>{formatNumber(rowsRead)} / {formatNumber(rowsWritten)}</strong>
              <small className="dataflow-route-duration">P95 {formatSeconds(p95)}</small>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function endpointRoutePresentation(row: Record<string, string | number | null>, direction: "source" | "destination") {
  const prefix = direction === "source" ? "source" : "destination";
  const value = (key: string) => row[`${prefix}_${key}`];
  const connection = cleanDisplayValue(value("name")) ?? "unknown connection";
  const connectionType = cleanDisplayValue(value("connection_type")) ?? "unknown type";
  const format = cleanDisplayValue(value("format") ?? connectionType) ?? "";
  const locator = connection;
  return { locator, connection, connectionType, format };
}

function EndpointRouteNode({ endpoint }: { endpoint: { locator: string; connection: string; connectionType: string; format: string } }) {
  return (
    <span className="dataflow-route-endpoint is-connection-only" title={`${endpoint.connection}\n${endpoint.connectionType}${endpoint.format ? `\n${endpoint.format}` : ""}`}>
      {endpoint.format ? (
        <span className="dataflow-route-endpoint-icon" aria-hidden="true">
          <LineageFormatIcon kind={assetIconKind(endpoint.format)} label={endpoint.format} size={18} />
        </span>
      ) : null}
      <span className="dataflow-route-endpoint-text">
        <strong>{endpoint.connection}</strong>
        <small>{endpoint.connectionType}</small>
      </span>
    </span>
  );
}

function DataflowWatermarkSignalPanel({ rows }: { rows: Array<Record<string, string | number | null>> }) {
  const visible = rows.filter((row) => num(row, "runs") > 0).slice(0, 10);
  if (!visible.length) return <div className="table-empty">No watermark or skip signals in current filters.</div>;
  return (
    <div className="dataflow-signal-list">
      {visible.map((row, index) => {
        const failed = num(row, "failed");
        const skipped = num(row, "skipped");
        const unchanged = num(row, "unchanged");
        const initialized = num(row, "initialized");
        const incomplete = num(row, "incomplete");
        const adjusted = num(row, "adjusted");
        const invalid = num(row, "invalid");
        const unknown = num(row, "unknown");
        return (
          <div
            key={`${row.dataflow_name}-${index}`}
            className="dataflow-signal-row dataflow-watermark-row"
            title={[
              `Dataflow: ${row.dataflow_name ?? "unknown"}`,
              `Runs: ${formatNumber(num(row, "runs"))}`,
              `Advanced / initialized / unchanged / incomplete / not configured / invalid / unknown: ${formatNumber(num(row, "advanced"))} / ${formatNumber(initialized)} / ${formatNumber(unchanged)} / ${formatNumber(incomplete)} / ${formatNumber(num(row, "not_configured") || num(row, "missing"))} / ${formatNumber(invalid)} / ${formatNumber(unknown)}`,
              `Adjusted effective boundary: ${formatNumber(adjusted)}`,
              `Skipped / failed: ${formatNumber(skipped)} / ${formatNumber(failed)}`,
              `Latest time: ${row.latest_time ?? "-"}`
            ].join("\n")}
          >
            <div className="dataflow-signal-main">
              <strong>{String(row.dataflow_name ?? "unknown")}</strong>
              <small>{formatNumber(num(row, "runs"))} runs</small>
            </div>
            <div className="dataflow-watermark-chips">
              <span className="wm-advanced">A {formatNumber(num(row, "advanced"))}</span>
              <span className="wm-info">Init {formatNumber(initialized)}</span>
              <span className={unchanged ? "wm-warning" : ""}>U {formatNumber(unchanged)}</span>
              <span className="wm-info">Inc {formatNumber(incomplete)}</span>
              <span className="wm-info">Adj {formatNumber(adjusted)}</span>
              <span>N/C {formatNumber(num(row, "not_configured") || num(row, "missing"))}</span>
              <span className={invalid ? "wm-bad" : ""}>I {formatNumber(invalid)}</span>
              <span>Unk {formatNumber(unknown)}</span>
            </div>
            <div className="dataflow-signal-status">
              <strong className={failed ? "status-bad" : skipped ? "status-warning" : ""}>
                {formatNumber(skipped)} / {formatNumber(failed)}
              </strong>
              <small>skipped / failed</small>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function FailureQueueTable({
  rows,
  maxRows,
  offset = 0,
  onInspect,
  timezoneName
}: {
  rows: Array<Record<string, string | number | null>>;
  maxRows: number;
  offset?: number;
  onInspect?: (row: Record<string, unknown>) => void;
  timezoneName?: string | null;
}) {
  return (
    <DataTable<Record<string, string | number | null>>
      rows={rows}
      columns={[
        { key: "failure_time", label: "Time", sortable: true, autoFit: true, maxWidth: 190, render: (row) => <TableDateTimeValue value={row.failure_time} timezoneName={timezoneName} />, measureValue: (row, activeTimezone) => formatTimestampForDisplay(row.failure_time, activeTimezone, "-") },
        { key: "dataflow_name", label: "Dataflow", sortable: true, width: 178, render: (row) => <FailureDataflowNameCell row={row} /> },
        { key: "failure_phase", label: "Phase", sortable: true, autoFit: true, maxWidth: 112, render: (row) => <FailurePhaseBadge value={row.failure_phase} />, measureValue: (row) => humanize(String(row.failure_phase || "unknown")) },
        { key: "failure_category", label: "Category", sortable: true, autoFit: true, maxWidth: 180, render: (row) => <CompactValue value={row.failure_category} />, measureValue: (row) => String(row.failure_category || "unknown") },
        { key: "failure_message", label: "Message", sortable: true, width: 300, render: (row) => <IssuePreview row={{ error_preview: row.failure_message }} /> }
      ]}
      maxRows={maxRows}
      offset={offset}
      onRowClick={onInspect}
      timezoneName={timezoneName}
      className="monitoring-failure-table monitoring-table-one-line"
    />
  );
}

function RepeatedFailureTable({
  rows,
  maxRows,
  timezoneName
}: {
  rows: Array<Record<string, string | number | null>>;
  maxRows: number;
  timezoneName?: string | null;
}) {
  return (
    <DataTable<Record<string, string | number | null>>
      rows={rows}
      columns={[
        { key: "failure_category", label: "Category", sortable: true, autoFit: true, maxWidth: 180, measureValue: (row) => String(row.failure_category || "unknown") },
        { key: "failure_phase", label: "Phase", sortable: true, autoFit: true, maxWidth: 112, render: (row) => <FailurePhaseBadge value={row.failure_phase} />, measureValue: (row) => humanize(String(row.failure_phase || "unknown")) },
        { key: "latest_error", label: "Error", sortable: true, minWidth: 240, fillPriority: "last", render: (row) => <IssuePreview row={{ error_preview: row.latest_error }} /> },
        { key: "failed_runs", label: "Runs", sortable: true, autoFit: true, minWidth: 52, maxWidth: 70 },
        { key: "affected_jobs", label: "Jobs", sortable: true, autoFit: true, minWidth: 50, maxWidth: 66 },
        { key: "affected_dataflows", label: "Flows", sortable: true, autoFit: true, minWidth: 52, maxWidth: 70 },
        { key: "latest_time", label: "Latest", sortable: true, autoFit: true, maxWidth: 190, render: (row) => <TableDateTimeValue value={row.latest_time} timezoneName={timezoneName} />, measureValue: (row, activeTimezone) => formatTimestampForDisplay(row.latest_time, activeTimezone, "-") }
      ]}
      maxRows={maxRows}
      timezoneName={timezoneName}
      className="monitoring-failure-table monitoring-table-one-line"
    />
  );
}

function EndpointImpactTable({
  rows,
  maxRows,
  timezoneName
}: {
  rows: Array<Record<string, string | number | null>>;
  maxRows: number;
  timezoneName?: string | null;
}) {
  return (
    <DataTable<Record<string, string | number | null>>
      rows={rows}
      columns={[
        { key: "source_name", label: "Route", sortable: true, minWidth: 210, fillPriority: "last", render: (row) => <FailureRouteCell row={row} /> },
        { key: "failed_runs", label: "Runs", sortable: true, autoFit: true, minWidth: 52, maxWidth: 70 },
        { key: "affected_jobs", label: "Jobs", sortable: true, autoFit: true, minWidth: 50, maxWidth: 66 },
        { key: "latest_time", label: "Latest", sortable: true, autoFit: true, maxWidth: 190, render: (row) => <TableDateTimeValue value={row.latest_time} timezoneName={timezoneName} />, measureValue: (row, activeTimezone) => formatTimestampForDisplay(row.latest_time, activeTimezone, "-") }
      ]}
      maxRows={maxRows}
      timezoneName={timezoneName}
      className="monitoring-failure-table monitoring-table-one-line"
    />
  );
}

function FailureDataflowNameCell({ row }: { row: Record<string, unknown> }) {
  const name = String(row.dataflow_name || "unknown");
  const details = [
    `Dataflow: ${name}`,
    row.dataflow_id ? `Dataflow ID: ${row.dataflow_id}` : null,
    row.dataflow_run_id ? `Run ID: ${row.dataflow_run_id}` : null,
    row.job_id ? `Job ID: ${row.job_id}` : null
  ].filter(Boolean).join("\n");
  return (
    <span className="monitor-inline-cell" title={details}>
      <strong>{name}</strong>
    </span>
  );
}

function FailureRouteCell({ row }: { row: Record<string, unknown> }) {
  const source = String(row.source_name ?? "unknown");
  const destination = String(row.destination_name ?? "unknown");
  const sourceFormat = String(row.source_format ?? row.source_connection_type ?? "");
  const destinationFormat = String(row.destination_format ?? row.destination_connection_type ?? "");
  return (
    <span className="failure-route-cell" title={`${source} -> ${destination}`}>
      <span className="failure-route-node">
        <span className="monitor-endpoint-icon" aria-hidden="true">
          <LineageFormatIcon kind={assetIconKind(sourceFormat || "unknown")} label={sourceFormat || "source"} size={16} />
        </span>
        <strong>{source}</strong>
      </span>
      <span className="failure-route-arrow">→</span>
      <span className="failure-route-node">
        <span className="monitor-endpoint-icon" aria-hidden="true">
          <LineageFormatIcon kind={assetIconKind(destinationFormat || "unknown")} label={destinationFormat || "destination"} size={16} />
        </span>
        <strong>{destination}</strong>
      </span>
    </span>
  );
}

function FailurePhaseBadge({ value }: { value: unknown }) {
  const textValue = String(value || "unknown");
  return <span className={`failure-phase-badge is-${textValue}`}>{humanize(textValue)}</span>;
}

const FAILURE_PHASE_LABELS: Record<FailurePhaseKey, string> = {
  source: "Source",
  transform: "Transform",
  destination: "Destination",
  overhead: "Overhead",
  unknown: "Unknown"
};

const monitoringPhaseColors = {
  source: "#2563eb",
  transform: "#7c3aed",
  destination: "#16805c",
  overhead: "#64748b",
  unknown: "#b45309"
} as const;

const FAILURE_PHASE_COLORS: Record<FailurePhaseKey, string> = {
  ...monitoringPhaseColors
};

const FAILURE_TREND_COLORS = {
  dataflows: "#c24141",
  jobs: "#7c3aed"
} as const;

function FailureTrendLegend() {
  return <MonitoringChartLegend label="Failure trend legend" items={[
    ["Dataflows", FAILURE_TREND_COLORS.dataflows],
    ["Jobs", FAILURE_TREND_COLORS.jobs]
  ]} />;
}

function FailurePhaseLegend() {
  return <MonitoringChartLegend
    label="Failure phase legend"
    items={FAILURE_PHASES.map((phase) => [FAILURE_PHASE_LABELS[phase], FAILURE_PHASE_COLORS[phase]] as const)}
  />;
}

function failureHorizontalBarOption(
  rows: Array<Record<string, string | number>>,
  labelKey: string,
  valueKey: string,
  _seriesName: string
) {
  const visibleRows = rows.filter((row) => num(row, valueKey) > 0);
  const zoomConfig = horizontalBarDataZoom(visibleRows.length);
  const barSizing = horizontalBarSeriesSizing(visibleRows.length);
  return failurePhaseHorizontalBarOption(visibleRows, labelKey, valueKey, zoomConfig, barSizing);
}

function failurePhaseHorizontalBarOption(
  visibleRows: Array<Record<string, string | number>>,
  labelKey: string,
  valueKey: string,
  zoomConfig: ReturnType<typeof horizontalBarDataZoom>,
  barSizing: ReturnType<typeof horizontalBarSeriesSizing>
) {
  return baseChartOption({
    tooltip: {
      trigger: "item",
      triggerOn: "mousemove",
      appendToBody: true,
      axisPointer: { type: "none" },
      extraCssText: "max-width: 300px; white-space: normal;",
      formatter: (params: any) => {
        const row = visibleRows[Number(params?.dataIndex ?? 0)] ?? {};
        const phase = failurePhaseFromSeriesName(String(params?.seriesName ?? ""));
        const phaseValue = phase ? failurePhaseCount(row, phase, valueKey) : Number(params?.value ?? 0);
        return [
          `<strong>${String(row[labelKey] ?? "unknown")}</strong>`,
          `${params?.marker ?? ""}${phase ? FAILURE_PHASE_LABELS[phase] : "Failures"}: ${formatNumber(phaseValue)}`,
          `Total failures: ${formatNumber(num(row, valueKey))}`
        ].join("<br/>");
      }
    },
    legend: { show: false },
    grid: fixedHorizontalBarGrid(96, Boolean(zoomConfig), { right: zoomConfig ? 14 : 8, top: 4 }),
    xAxis: { type: "value", axisLabel: { fontSize: 10, formatter: (value: number) => formatCompact(value) }, splitLine: { lineStyle: { color: reportChartPalette.grid } } },
    yAxis: fixedHorizontalCategoryAxis(visibleRows.map((row) => String(row[labelKey] ?? "unknown")), 96),
    dataZoom: zoomConfig,
    series: FAILURE_PHASES.map((phase) => ({
      name: FAILURE_PHASE_LABELS[phase],
      type: "bar",
      stack: "failures",
      ...barSizing,
      itemStyle: { color: FAILURE_PHASE_COLORS[phase], borderRadius: phase === "unknown" ? [0, 2, 2, 0] : 0 },
      data: visibleRows.map((row) => failurePhaseCount(row, phase, valueKey))
    }))
  });
}

function failurePhaseCount(row: Record<string, string | number>, phase: FailurePhaseKey, totalKey: string) {
  if (phase !== "unknown") return num(row, phase);
  const explicitUnknown = num(row, "unknown");
  if (explicitUnknown > 0) return explicitUnknown;
  const attributed = num(row, "source") + num(row, "transform") + num(row, "destination") + num(row, "overhead");
  return Math.max(0, num(row, totalKey) - attributed);
}

function failurePhaseFromSeriesName(seriesName: string): FailurePhaseKey | undefined {
  return FAILURE_PHASES.find((phase) => FAILURE_PHASE_LABELS[phase] === seriesName);
}

function failureTrendOption(
  rows: Array<Record<string, string | number | null>>,
  filters: MonitoringFilters,
  dateRange: { min?: string | null; max?: string | null },
  timezoneName: string,
  reportEffectiveGrain?: string
) {
  const visible = fillMissingFailureTrendDates(rows, filters, dateRange, timezoneName, reportEffectiveGrain);
  const dataflowColor = FAILURE_TREND_COLORS.dataflows;
  const jobColor = FAILURE_TREND_COLORS.jobs;
  return baseChartOption({
    tooltip: {
      trigger: "axis",
      appendToBody: true,
      formatter: (params: any) => {
        const points = Array.isArray(params) ? params : [params];
        const first = points[0];
        const index = Number(first?.dataIndex ?? 0);
        const row = visible[index] ?? {};
        return [
          String(row.bucket ?? row.date ?? ""),
          row.grain ? `Grain: ${row.grain}` : "",
          timezoneName ? `Timezone: ${timezoneName}` : "",
          ...points.map((point) => `${point.marker}${point.seriesName}: ${formatNumber(Number(point.value ?? 0))}`)
        ].filter(Boolean).join("<br/>");
      }
    },
    grid: reportChartGrid({ left: 42, right: 42, top: 5, bottom: 5, containLabel: false }),
    xAxis: {
      type: "category",
      data: visible.map((row) => String(row.date ?? row.bucket ?? "unknown")),
      axisTick: { show: false },
      axisLabel: { fontSize: 10, color: reportChartPalette.muted }
    },
    yAxis: [
      {
        type: "value",
        name: "dataflows",
        nameTextStyle: { fontSize: 9, color: reportChartPalette.muted, padding: [0, 0, 0, 24] },
        axisLabel: { fontSize: 10, formatter: (value: number) => formatCompact(value) },
        splitLine: { lineStyle: { color: reportChartPalette.grid } }
      },
      {
        type: "value",
        name: "jobs",
        nameTextStyle: { fontSize: 9, color: reportChartPalette.muted, padding: [0, 24, 0, 0] },
        axisLabel: { fontSize: 10, formatter: (value: number) => formatCompact(value) },
        splitLine: { show: false }
      }
    ],
    legend: { show: false },
    series: [
      {
        name: "Dataflows",
        type: "line",
        smooth: true,
        showSymbol: false,
        symbolSize: 5,
        lineStyle: { color: dataflowColor, width: 2 },
        itemStyle: { color: dataflowColor },
        areaStyle: { color: "rgba(194, 65, 65, 0.08)" },
        yAxisIndex: 0,
        data: visible.map((row) => num(row, "failed_dataflows") || num(row, "failed"))
      },
      {
        name: "Jobs",
        type: "line",
        smooth: true,
        showSymbol: false,
        symbolSize: 5,
        lineStyle: { color: jobColor, width: 2 },
        itemStyle: { color: jobColor },
        yAxisIndex: 1,
        data: visible.map((row) => num(row, "failed_jobs"))
      }
    ]
  });
}

function fillMissingFailureTrendDates(
  rows: Array<Record<string, string | number | null>>,
  filters: MonitoringFilters,
  dateRange: { min?: string | null; max?: string | null },
  timezoneName: string,
  reportEffectiveGrain?: string
) {
  const effectiveGrain = String(rows.find((row) => row.grain)?.grain ?? reportEffectiveGrain ?? filters.grain ?? "day");
  const known = rows.filter((row) => rowLabel(row) !== "unknown");
  const rowByDate = new Map<string, Record<string, string | number | null>>();
  for (const row of known) {
    const dateKey = rowLabel(row);
    if (!dateKey) continue;
    rowByDate.set(dateKey, row);
  }
  const keys = resolveTrendBucketKeys(filters, dateRange, timezoneName, Array.from(rowByDate.keys()), effectiveGrain);
  if (!keys.length) return known;
  return keys.map((dateKey) => rowByDate.get(dateKey) ?? createEmptyFailureTrendRow(dateKey, effectiveGrain));
}

function createEmptyFailureTrendRow(dateKey: string, grain: string): Record<string, string | number> {
  return {
    date: dateKey,
    bucket: dateKey,
    grain: normalizeTrendGrain(grain),
    failed_jobs: 0,
    failed_dataflows: 0,
    failed: 0
  };
}

function failureCategoryPhaseMatrixOption(rows: Array<Record<string, string | number>>) {
  const visibleRows = rows.filter((row) => num(row, "total") > 0);
  const zoomConfig = horizontalBarDataZoom(visibleRows.length);
  const barSizing = horizontalBarSeriesSizing(visibleRows.length);
  return baseChartOption({
    tooltip: {
      trigger: "item",
      triggerOn: "mousemove",
      appendToBody: true,
      axisPointer: { type: "none" },
      extraCssText: "max-width: 320px; white-space: normal;",
      formatter: (params: any) => {
        const row = visibleRows[Number(params?.dataIndex ?? 0)] ?? {};
        const phase = failurePhaseFromSeriesName(String(params?.seriesName ?? "")) ?? "unknown";
        const lines = [
          `<strong>${String(row.category ?? "unknown")}</strong>`,
          `${params?.marker ?? ""}${FAILURE_PHASE_LABELS[phase]}: ${formatNumber(failurePhaseCount(row, phase, "total"))}`,
          `Total failures: ${formatNumber(num(row, "total"))}`
        ];
        return lines.join("<br/>");
      }
    },
    legend: { show: false },
    grid: fixedHorizontalBarGrid(106, Boolean(zoomConfig), { right: zoomConfig ? 14 : 8, top: 4 }),
    xAxis: { type: "value", axisLabel: { fontSize: 10, formatter: (value: number) => formatCompact(value) }, splitLine: { lineStyle: { color: reportChartPalette.grid } } },
    yAxis: fixedHorizontalCategoryAxis(visibleRows.map((row) => String(row.category ?? "unknown")), 106),
    dataZoom: zoomConfig,
    series: FAILURE_PHASES.map((phase) => ({
      name: FAILURE_PHASE_LABELS[phase],
      type: "bar",
      stack: "failures",
      ...barSizing,
      itemStyle: { color: FAILURE_PHASE_COLORS[phase], borderRadius: phase === "unknown" ? [0, 2, 2, 0] : 0 },
      data: visibleRows.map((row) => failurePhaseCount(row, phase, "total"))
    }))
  });
}

function JobRunsTable({
  rows,
  sort,
  onSort,
  onInspect,
  timezoneName
}: {
  rows: JobRecord[];
  sort?: TableSort;
  onSort?: (sort: TableSort) => void;
  onInspect?: (row: JobRecord) => void;
  timezoneName?: string | null;
}) {
  return (
    <DataTable<JobRecord>
      rows={rows}
      columns={[
        { key: "job_id", label: "Job", sortable: true, width: 132, className: "job-run-col-job", render: (row) => <CopyableText value={row.job_id} displayValue={compactRunId(row.job_id)} /> },
        { key: "job_config", label: "Config", autoFit: true, minWidth: 72, maxWidth: 128, className: "job-run-col-config", render: (row) => <JobConfigCell row={row} />, measureValue: (row) => jobConfigLines(row) },
        { key: "runtime_context", label: "Runtime", autoFit: true, minWidth: 72, maxWidth: 170, className: "job-run-col-runtime", render: (row) => <RuntimeContextCell row={row} />, measureValue: (row) => runtimeContextLines(row) },
        { key: "stages", label: "Stages", sortable: true, autoFit: true, minWidth: 110, maxWidth: 160, fillPriority: "last", className: "job-run-col-stages", render: (row) => <JobListValue value={row.stages} multiline />, measureValue: (row) => parseListValue(row.stages).join(", ") || "-" },
        { key: "operation_types", label: "Operation", sortable: true, autoFit: true, minWidth: 86, maxWidth: 160, className: "job-run-col-operation", render: (row) => <JobListValue value={row.operation_types} />, measureValue: (row) => parseListValue(row.operation_types).join(", ") || "-" },
        { key: "start_time", label: "Start", sortable: true, autoFit: true, minWidth: 144, maxWidth: 190, className: "job-run-col-time", render: (row) => <TableDateTimeValue value={row.start_time} timezoneName={timezoneName} />, measureValue: (row, activeTimezone) => formatTimestampForDisplay(row.start_time, activeTimezone, "-") },
        { key: "end_time", label: "End", sortable: true, autoFit: true, minWidth: 144, maxWidth: 190, className: "job-run-col-time", render: (row) => <TableDateTimeValue value={row.end_time} timezoneName={timezoneName} />, measureValue: (row, activeTimezone) => formatTimestampForDisplay(row.end_time, activeTimezone, "-") },
        { key: "duration_seconds", label: "Duration", sortable: true, autoFit: true, minWidth: 76, maxWidth: 96, className: "job-run-col-duration", render: (row) => formatSeconds(num(row, "duration_seconds")), measureValue: (row) => formatSeconds(num(row, "duration_seconds")) },
        { key: "status", label: "Status", sortable: true, autoFit: true, minWidth: 88, maxWidth: 112, className: "job-run-col-status", render: (row) => <StatusCell row={row} />, measureValue: (row) => String(row.status || "unknown") },
        { key: "child_dataflow_count", label: "Child flows", autoFit: true, minWidth: 92, maxWidth: 132, className: "job-run-col-child", render: (row) => <ChildFlowSummary row={row} />, measureValue: (row) => childFlowSummaryLines(row) },
        { key: "volume", label: "Volume", autoFit: true, minWidth: 96, maxWidth: 142, className: "job-run-col-volume", render: (row) => <JobVolumeCell row={row} />, measureValue: (row) => jobVolumeLines(row) },
        { key: "reconciliation_status", label: "Reconcile", autoFit: true, minWidth: 94, maxWidth: 128, className: "job-run-col-reconcile", render: (row) => <ReconciliationBadge value={row.reconciliation_status} />, measureValue: (row) => humanize(String(row.reconciliation_status || "not_available")) },
        { key: "error_preview", label: "Issue", minWidth: 140, fillPriority: "last", className: "job-run-col-issue", render: (row) => <IssuePreview row={row} /> }
      ]}
      maxRows={rows.length}
      sort={sort}
      onSort={onSort}
      onRowClick={onInspect}
      timezoneName={timezoneName}
      fixedLayout
    />
  );
}

function JobListValue({ value, multiline = false }: { value: unknown; multiline?: boolean }) {
  const values = parseListValue(value);
  const label = values.length ? values.join(", ") : "-";
  return (
    <span className={`monitor-compact-value job-run-list-value${multiline ? " is-multiline" : ""}`} title={label}>
      {label}
    </span>
  );
}

function parseListValue(value: unknown) {
  if (Array.isArray(value)) return value.map((item) => String(item ?? "").trim()).filter(Boolean);
  const textValue = String(value ?? "").trim();
  if (!textValue) return [];
  if (textValue.startsWith("[") && textValue.endsWith("]")) {
    try {
      const parsed = JSON.parse(textValue);
      if (Array.isArray(parsed)) return parsed.map((item) => String(item ?? "").trim()).filter(Boolean);
    } catch {
      return [textValue];
    }
  }
  return textValue.split(",").map((item) => item.trim()).filter(Boolean);
}

function DataflowRunsTable({
  rows,
  maxRows = 12,
  includeReason = false,
  sort,
  onSort,
  onInspect,
  timezoneName
}: {
  rows: MonitoringRecord[];
  maxRows?: number;
  includeReason?: boolean;
  sort?: TableSort;
  onSort?: (sort: TableSort) => void;
  onInspect?: (row: MonitoringRecord) => void;
  timezoneName?: string | null;
}) {
  return (
    <DataTable<MonitoringRecord>
      rows={rows}
      columns={[
        { key: "job_id", label: "Job", sortable: true, width: 104, className: "dataflow-run-col-job", render: (row) => <CopyableText value={row.job_id} displayValue={compactRunId(row.job_id)} /> },
        { key: "dataflow_name", label: "Dataflow", sortable: true, width: 148, className: "dataflow-run-col-dataflow", render: (row) => <DataflowNameCell row={row} /> },
        { key: "context", label: "Context", sortable: true, sortKey: "stage", width: 118, className: "dataflow-run-col-context", render: (row) => <DataflowContextCell row={row} /> },
        { key: "source_name", label: "Source", sortable: true, width: 150, className: "dataflow-run-col-endpoint", render: (row) => <EndpointCell row={row} direction="source" /> },
        { key: "destination_name", label: "Destination", sortable: true, width: 150, className: "dataflow-run-col-endpoint", render: (row) => <EndpointCell row={row} direction="destination" /> },
        { key: "phase_health", label: "Phase", width: 140, className: "dataflow-run-col-phase", render: (row) => <DataflowPhaseCell row={row} /> },
        { key: "start_time", label: "Start", sortable: true, width: 178, className: "dataflow-run-col-time", render: (row) => <TableDateTimeValue value={row.start_time} timezoneName={timezoneName} /> },
        { key: "end_time", label: "End", sortable: true, width: 178, className: "dataflow-run-col-time", render: (row) => <TableDateTimeValue value={row.end_time} timezoneName={timezoneName} /> },
        { key: "duration_seconds", label: "Duration", sortable: true, width: 76, className: "dataflow-run-col-duration", render: (row) => formatSeconds(num(row, "duration_seconds")) },
        { key: "status", label: "Status", sortable: true, width: 98, className: "dataflow-run-col-status", render: (row) => <StatusCell row={row} /> },
        { key: "volume", label: "Volume", width: 106, className: "dataflow-run-col-volume", render: (row) => <DataflowVolumeCell row={row} /> },
        { key: "movement_state", label: "Watermark", width: 90, className: "dataflow-run-col-watermark", render: (row) => <WatermarkBadge value={row.movement_state} effective={row.source_watermark_effective} /> },
        { key: "error_preview", label: "Issue", minWidth: 64, fillPriority: "last", className: "dataflow-run-col-issue", render: (row) => <IssuePreview row={row} /> },
        ...(includeReason
          ? [
              { key: "potential_seconds_saved", label: "Potential saved", width: 120, render: (row: MonitoringRecord) => formatSeconds(num(row, "potential_seconds_saved")) },
              { key: "candidate_reason", label: "Reason", width: 180 }
            ]
          : [])
      ]}
      maxRows={onSort ? rows.length : maxRows}
      sort={sort}
      onSort={onSort}
      onRowClick={onInspect}
      timezoneName={timezoneName}
      fixedLayout
    />
  );
}

function DataflowNameCell({ row }: { row: MonitoringRecord }) {
  const name = row.dataflow_name ?? row.dataflow_id ?? "-";
  const id = row.dataflow_id ?? "";
  return (
    <span className="monitor-inline-cell" title={`Dataflow: ${name}\nID: ${id || "-"}`}>
      {String(name)}
    </span>
  );
}

function DataflowContextCell({ row }: { row: MonitoringRecord }) {
  const stage = String(row.stage || "unknown");
  const operation = String(row.operation_type || "unknown");
  return (
    <span
      className="monitor-stack-cell"
      title={[
        `Stage: ${stage}`,
        `Dataflow operation: ${operation}`,
        `Destination operation: ${row.destination_operation_type ?? "-"}`,
        `Load type: ${row.destination_load_type ?? row.load_type ?? "-"}`,
        `Group / order: ${row.group_number ?? "-"} / ${row.execution_order ?? "-"}`
      ].join("\n")}
    >
      <strong>{stage}</strong>
      <small>{operation}</small>
    </span>
  );
}

function DataflowPhaseCell({ row }: { row: MonitoringRecord }) {
  const source = num(row, "source_duration_seconds");
  const transform = num(row, "transform_duration_seconds");
  const destination = num(row, "destination_duration_seconds");
  const overhead = Math.max(0, optionalNum(row, "overhead_duration_seconds") ?? 0);
  const segments = normalizedPhaseSegments([
    { phase: "source", value: source },
    { phase: "transform", value: transform },
    { phase: "destination", value: destination },
    { phase: "overhead", value: overhead }
  ]);
  const pctByPhase = Object.fromEntries(segments.map((segment) => [segment.phase, segment.percent])) as Partial<Record<PhaseKey, number>>;
  const contributionPct = (phase: PhaseKey) => formatPhasePercent(pctByPhase[phase] ?? 0);
  const bottleneck = phaseBottleneck(row, source, transform, destination, overhead);
  const title = [
    `Phase reason: ${bottleneck.label}`,
    `Phase health: ${humanize(String(row.phase_health || "unknown"))}`,
    `Source: ${formatSeconds(source)} (${contributionPct("source")}) · ${row.source_status ?? "-"}`,
    `Transform: ${formatSeconds(transform)} (${contributionPct("transform")}) · ${row.transform_status ?? "-"}`,
    `Destination: ${formatSeconds(destination)} (${contributionPct("destination")}) · ${row.destination_status ?? "-"}`,
    `Overhead: ${formatSeconds(overhead)} (${contributionPct("overhead")})`,
    `Error phase: ${row.error_phase ?? "-"}`
  ].join("\n");
  return (
    <span className="dataflow-phase-cell" title={title}>
      <strong className={`dataflow-phase-reason phase-text-${bottleneck.phase}`}>{bottleneck.label}</strong>
      <span className="dataflow-phase-mini-stack" aria-label="Source, transform, destination, and overhead phase contribution">
        {segments.map((segment) => (
          <i key={segment.phase} className={`phase-${segment.phase}`} style={{ flex: `0 0 ${segment.percent}%` }} />
        ))}
      </span>
    </span>
  );
}

function normalizedPhaseSegments(segments: Array<{ phase: PhaseKey; value: number }>) {
  const visibleSegments = segments
    .map((segment) => ({ ...segment, value: Math.max(0, Number.isFinite(segment.value) ? segment.value : 0) }))
    .filter((segment) => segment.value > 0);
  const total = visibleSegments.reduce((sum, segment) => sum + segment.value, 0);
  if (total <= 0) return [];
  let usedPercent = 0;
  return visibleSegments.map((segment, index) => {
    const percent = index === visibleSegments.length - 1
      ? Math.max(0, 100 - usedPercent)
      : (segment.value / total) * 100;
    usedPercent += percent;
    return { ...segment, percent };
  });
}

function optionalNum(row: Record<string, unknown>, field: string) {
  const value = row[field];
  if (value === null || value === undefined || value === "") return null;
  if (Array.isArray(value) && !value.length) return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function phaseBottleneck(row: MonitoringRecord, source: number, transform: number, destination: number, overhead = 0) {
  const health = String(row.phase_health || "").toLowerCase();
  if (health.includes("source")) return { phase: "source", label: "Source" };
  if (health.includes("transform")) return { phase: "transform", label: "Transform" };
  if (health.includes("destination")) return { phase: "destination", label: "Destination" };
  if (health.includes("overhead") || health.includes("orchestration")) return { phase: "overhead", label: "Overhead" };
  const maxPhase = [
    { phase: "source", value: source },
    { phase: "transform", value: transform },
    { phase: "destination", value: destination },
    { phase: "overhead", value: overhead }
  ].sort((left, right) => right.value - left.value)[0];
  const label = humanize(maxPhase.phase);
  return { phase: maxPhase.phase, label };
}

function DataflowVolumeCell({ row }: { row: MonitoringRecord }) {
  const rowsRead = num(row, "source_rows_read");
  const rowsWritten = num(row, "destination_rows_written");
  const bytesAdded = num(row, "destination_bytes_added");
  const bytesRemoved = num(row, "destination_bytes_removed");
  const netBytes = bytesAdded - bytesRemoved;
  return (
    <span
      className="monitor-stack-cell"
      title={[
        `Rows read: ${formatNumber(rowsRead)}`,
        `Rows written: ${formatNumber(rowsWritten)}`,
        `Lakehouse bytes added: ${formatBytes(bytesAdded)}`,
        `Lakehouse bytes removed: ${formatBytes(bytesRemoved)}`,
        `Net lakehouse bytes: ${formatBytes(netBytes)}`
      ].join("\n")}
    >
      <strong>{formatNumber(rowsRead)} / {formatNumber(rowsWritten)}</strong>
      <small>{formatBytes(netBytes)}</small>
    </span>
  );
}

function CopyableText({ value, displayValue }: { value: unknown; displayValue?: string }) {
  const textValue = value === null || value === undefined || value === "" ? "-" : String(value);
  const visibleValue = displayValue ?? textValue;
  return (
    <button
      className="monitor-copy-value"
      type="button"
      title={textValue === "-" ? "" : `Copy ${textValue}`}
      onClick={(event) => {
        event.stopPropagation();
        if (textValue !== "-") void navigator.clipboard?.writeText(textValue);
      }}
    >
      {visibleValue}
    </button>
  );
}

function compactRunId(value: unknown) {
  if (value === null || value === undefined || value === "") return "-";
  const textValue = String(value);
  if (textValue.length <= 18) return textValue;
  return `${textValue.slice(0, 8)}...${textValue.slice(-6)}`;
}

function CompactValue({ value, fallback = "-" }: { value: unknown; fallback?: string }) {
  const textValue = value === null || value === undefined || value === "" ? fallback : String(value);
  return <span className="monitor-compact-value" title={textValue}>{textValue}</span>;
}

function TableDateTimeValue({ value, fallback = "-", timezoneName }: { value: unknown; fallback?: string; timezoneName?: string | null }) {
  const rawValue = value === null || value === undefined || value === "" ? fallback : String(value);
  const textValue = rawValue === fallback ? fallback : formatTimestampForDisplay(rawValue, timezoneName, fallback);
  return <span className="monitor-compact-value" title={textValue}>{textValue}</span>;
}

function monitoringTimezone(report: MonitoringReport) {
  return report.summary.timezone || "UTC";
}

function JobConfigCell({ row }: { row: JobRecord }) {
  const [mainLabel, stopValue] = jobConfigLines(row);
  const title = `${mainLabel === "-" ? "Workers / Retry: -" : mainLabel.replace(/^w /u, "Workers: ").replace(/ · r /u, " · Retry: ")}\nStop on error: ${stopValue === "-" ? "-" : stopValue.replace(/^stop /u, "")}`;
  return (
    <span className="monitor-stack-cell" title={title}>
      <strong>{mainLabel}</strong>
      <small>{stopValue}</small>
    </span>
  );
}

function jobConfigLines(row: JobRecord) {
  const mainParts = [
    isMeaningfulConfigValue(row.max_workers) ? `w ${formatConfigValue(row.max_workers, "max_workers")}` : "",
    isMeaningfulConfigValue(row.retry_count) ? `r ${formatConfigValue(row.retry_count, "retry_count")}` : ""
  ].filter(Boolean);
  const stopValue = isMeaningfulConfigValue(row.stop_on_error) ? `stop ${formatConfigValue(row.stop_on_error, "stop_on_error")}` : "-";
  const mainLabel = mainParts.length ? mainParts.join(" · ") : "-";
  return [mainLabel, stopValue];
}

function RuntimeContextCell({ row }: { row: JobRecord }) {
  const [mainLabel, provider] = runtimeContextLines(row);
  const [platform, engine] = mainLabel.split(" · ");
  const title = `Platform: ${platform} · Engine: ${engine}\nProvider: ${provider}`;
  return (
    <span className="monitor-stack-cell" title={title}>
      <strong>{mainLabel}</strong>
      <small>{provider}</small>
    </span>
  );
}

function runtimeContextLines(row: JobRecord) {
  const engine = shortRuntimeName(row.engine_name, "engine");
  const platform = shortRuntimeName(row.platform_name, "platform");
  const provider = String(row.metadata_provider_name || "unknown");
  const mainLabel = `${platform} · ${engine}`;
  return [mainLabel, provider];
}

function shortRuntimeName(value: unknown, kind: "engine" | "platform") {
  const fallback = kind === "engine" ? "unknown" : "unknown";
  const textValue = value === null || value === undefined || value === "" ? fallback : String(value);
  const stripped = textValue
    .replace(/\b(engine|platform)\b/giu, "")
    .replace(/(engine|platform)$/iu, "")
    .replace(/[_-]+/gu, " ")
    .replace(/\s+/gu, " ")
    .trim();
  return stripped || textValue;
}

function JobVolumeCell({ row }: { row: JobRecord }) {
  const [mainLabel, netBytesLabel] = jobVolumeLines(row);
  const bytesAdded = num(row, "total_bytes_added") || num(row, "child_total_bytes_added");
  const bytesRemoved = num(row, "total_bytes_removed") || num(row, "child_total_bytes_removed");
  return (
    <span
      className="monitor-stack-cell"
      title={`Rows read / written: ${mainLabel}\nBytes added: ${formatBytes(bytesAdded)} · Bytes removed: ${formatBytes(bytesRemoved)} · Net bytes: ${netBytesLabel}`}
    >
      <strong>{mainLabel}</strong>
      <small>{netBytesLabel}</small>
    </span>
  );
}

function jobVolumeLines(row: JobRecord) {
  const rowsRead = num(row, "total_rows_read") || num(row, "child_total_rows_read");
  const rowsWritten = num(row, "total_rows_written") || num(row, "child_total_rows_written");
  const bytesAdded = num(row, "total_bytes_added") || num(row, "child_total_bytes_added");
  const bytesRemoved = num(row, "total_bytes_removed") || num(row, "child_total_bytes_removed");
  const netBytes = bytesAdded - bytesRemoved;
  const mainLabel = `${formatNumber(rowsRead)} / ${formatNumber(rowsWritten)}`;
  const netBytesLabel = formatBytes(netBytes);
  return [mainLabel, netBytesLabel];
}

function EndpointCell({ row, direction }: { row: MonitoringRecord; direction: "source" | "destination" }) {
  const endpoint = monitoringEndpointPresentation(row, direction);
  return (
    <span className="monitor-endpoint-cell" title={endpoint.title}>
      {endpoint.format ? (
        <span className="monitor-endpoint-icon" aria-hidden="true">
          <LineageFormatIcon kind={assetIconKind(endpoint.format)} label={endpoint.format} size={18} />
        </span>
      ) : null}
      <span className="monitor-endpoint-text">
        <strong>{endpoint.locator}</strong>
        <small>{endpoint.connection}</small>
      </span>
    </span>
  );
}

function monitoringEndpointPresentation(row: MonitoringRecord, direction: "source" | "destination") {
  const prefix = direction === "source" ? "source" : "destination";
  const value = (key: string) => row[`${prefix}_${key}` as keyof MonitoringRecord];
  const connection = cleanDisplayValue(value("name") ?? value("connection_name")) ?? "unknown connection";
  const format = cleanDisplayValue(value("format")) ?? "";
  const table = cleanDisplayValue(value("table"));
  const fullTable = cleanDisplayValue(value("full_table"));
  const path = cleanDisplayValue(value("path"));
  const query = direction === "source" ? cleanDisplayValue(value("query")) : null;
  const pythonFunction = direction === "source" ? cleanDisplayValue(value("python_function")) : null;
  const display = cleanDisplayValue(value("display"));
  const locator =
    pythonFunctionTail(pythonFunction)
    ?? (query ? "SQL query" : null)
    ?? table
    ?? compactQualifiedTable(fullTable)
    ?? pathBasename(path)
    ?? display
    ?? "Unknown asset";
  const title = [
    `Asset: ${locator}`,
    `Connection: ${connection}`,
    format ? `Format: ${format}` : null,
    table ? `Table: ${table}` : null,
    fullTable ? `Full table: ${fullTable}` : null,
    path ? `Path: ${path}` : null,
    query ? "Query: SQL query" : null,
    pythonFunction ? `Python function: ${pythonFunction}` : null
  ].filter(Boolean).join("\n");
  return { locator, connection, format, title };
}

function cleanDisplayValue(value: unknown) {
  if (value === null || value === undefined) return null;
  const textValue = String(value).trim();
  return textValue ? textValue : null;
}

function compactQualifiedTable(value: string | null) {
  if (!value) return null;
  const parts = value.replace(/`/g, "").split(".").filter(Boolean);
  return parts.at(-1) || value;
}

function pythonFunctionTail(value: string | null) {
  if (!value) return null;
  return value.split(".").filter(Boolean).at(-1) || value;
}

function pathBasename(value: string | null) {
  if (!value) return null;
  const parts = value.split(/[\\/]+/).filter((part) => part && part !== ".");
  return parts.at(-1) || value;
}

function ChildFlowSummary({ row }: { row: JobRecord }) {
  const [mainLabel] = childFlowSummaryLines(row);
  const succeeded = num(row, "child_succeeded_count");
  const failed = num(row, "child_failed_count");
  const skipped = num(row, "child_skipped_count");
  return (
    <span
      className="monitor-stack-cell monitor-child-summary"
      title={`Total child flows: ${mainLabel}\nSucceeded: ${succeeded} / Failed: ${failed} / Skipped: ${skipped}`}
    >
      <strong>{mainLabel}</strong>
      <small>
        <span className={succeeded > 0 ? "monitor-child-value is-success" : "monitor-child-value"}>{formatNumber(succeeded)}</span>
        <span> / </span>
        <span className={failed > 0 ? "monitor-child-value is-failed" : "monitor-child-value"}>{formatNumber(failed)}</span>
        <span> / </span>
        <span className={skipped > 0 ? "monitor-child-value is-skipped" : "monitor-child-value"}>{formatNumber(skipped)}</span>
      </small>
    </span>
  );
}

function childFlowSummaryLines(row: JobRecord) {
  const total = num(row, "child_dataflow_count") || num(row, "total_dataflows");
  const succeeded = num(row, "child_succeeded_count");
  const failed = num(row, "child_failed_count");
  const skipped = num(row, "child_skipped_count");
  const mainLabel = formatNumber(total);
  const statusLabel = `${formatNumber(succeeded)} / ${formatNumber(failed)} / ${formatNumber(skipped)}`;
  return [mainLabel, statusLabel];
}

function ReconciliationBadge({ value }: { value: unknown }) {
  const textValue = String(value || "not_available");
  const intent = textValue === "mismatch" ? "bad" : textValue === "matched" ? "good" : "neutral";
  return <span className={`monitor-mini-badge badge-${intent}`}>{humanize(textValue)}</span>;
}

function PhaseHealthBadge({ value }: { value: unknown }) {
  const textValue = String(value || "unknown");
  const intent = textValue.includes("failed") ? "bad" : textValue === "ok" ? "good" : textValue.includes("bottleneck") ? "warning" : "neutral";
  return <span className={`monitor-mini-badge badge-${intent}`} title={humanize(textValue)}>{humanize(textValue)}</span>;
}

function WatermarkBadge({ value, effective }: { value: unknown; effective: unknown }) {
  const textValue = String(value || "not_configured");
  const intent = textValue === "advanced" ? "good" : textValue === "initialized" ? "neutral" : textValue === "unchanged" || textValue === "incomplete" || textValue === "invalid" ? "warning" : "neutral";
  const detail = effective === null || effective === undefined || effective === "" ? "" : String(effective);
  return <span className={`monitor-mini-badge badge-${intent}`} title={detail || humanize(textValue)}>{humanize(textValue)}</span>;
}

function IssuePreview({ row }: { row: Record<string, unknown> }) {
  const issue = String(row.error_preview || row.error_message || row.source_error_message || row.transform_error_message || row.destination_error_message || "");
  if (!issue) return <span className="monitor-muted">-</span>;
  return <span className="monitor-issue-preview" title={issue}>{issue}</span>;
}

function humanize(value: string) {
  return value.replace(/_/g, " ").replace(/\b\w/g, (char) => char.toUpperCase());
}

function formatCoverage(value: string | number | null) {
  if (typeof value === "number") return formatNumber(value);
  if (value === null || value === undefined || value === "") return "-";
  return String(value);
}

function tableSubtitle(records: number, totalRows: number, loading: boolean) {
  const prefix = loading ? "Loading" : `${formatNumber(records)} loaded`;
  return `${prefix} of ${formatNumber(totalRows)} records`;
}

function TablePager({
  limit,
  offset,
  loadedRows,
  totalRows,
  loading,
  onPageChange,
  onPageSizeChange
}: {
  limit: number;
  offset: number;
  loadedRows: number;
  totalRows: number;
  loading: boolean;
  onPageChange: (offset: number) => void;
  onPageSizeChange?: (limit: number) => void;
}) {
  const currentStart = totalRows ? offset + 1 : 0;
  const currentEnd = Math.min(totalRows, offset + loadedRows);
  const lastOffset = Math.max(0, Math.floor((Math.max(0, totalRows - 1)) / limit) * limit);
  const canPrevious = offset > 0 && !loading;
  const canNext = offset + limit < totalRows && !loading;
  return (
    <div className="table-pager">
      <span className="table-pager-range">
        {formatNumber(currentStart)}-{formatNumber(currentEnd)} of {formatNumber(totalRows)}
      </span>
      <div>
        {onPageSizeChange ? (
          <label className="table-pager-size">
            Rows
            <select
              value={limit}
              disabled={loading}
              onChange={(event) => onPageSizeChange(Number(event.target.value))}
            >
              {RUN_TABLE_PAGE_SIZES.map((size) => (
                <option key={size} value={size}>
                  {size}
                </option>
              ))}
            </select>
          </label>
        ) : null}
        <button className="text-action" type="button" disabled={!canPrevious} onClick={() => onPageChange(0)}>
          First
        </button>
        <button className="text-action" type="button" disabled={!canPrevious} onClick={() => onPageChange(Math.max(0, offset - limit))}>
          Prev
        </button>
        <button className="text-action" type="button" disabled={!canNext} onClick={() => onPageChange(offset + limit)}>
          Next
        </button>
        <button className="text-action" type="button" disabled={!canNext} onClick={() => onPageChange(lastOffset)}>
          Last
        </button>
      </div>
    </div>
  );
}

function DiagnosticsSeverity({ value }: { value: string }) {
  const normalized = value.toLowerCase();
  const intent = normalized === "error" || normalized === "bad" || normalized === "failed" ? "bad" : normalized === "warning" ? "warning" : "info";
  return <span className={`diagnostics-severity diagnostics-${intent}`}>{value}</span>;
}

function WatermarkValue({ value }: { value: unknown }) {
  if (value === null || value === undefined || value === "") return <span>-</span>;
  const textValue = String(value);
  let formatted = textValue;
  try {
    formatted = JSON.stringify(JSON.parse(textValue), null, 2);
  } catch {
    formatted = textValue;
  }
  return (
    <details className="watermark-cell">
      <summary>{textValue.length > 32 ? `${textValue.slice(0, 32)}...` : textValue}</summary>
      <pre>{formatted}</pre>
    </details>
  );
}

// ---------------------------------------------------------------------------
// Shared monitoring primitives barrel.
// The page components (Overview, Jobs, Dataflows, Failure, Diagnostics,
// Performance, Volume, Maintenance, Freshness) live in ./pages/*.tsx and import
// the helpers/sub-components/constants below from this module.
// ---------------------------------------------------------------------------
export {
  // Re-exported chart/format primitives so pages import everything from one module.
  BarList,
  DataTable,
  MetricGrid,
  Panel,
  ScatterPlot,
  StatusCell,
  formatBytes,
  formatNumber,
  formatSeconds,
  num,
  ReportChart,
  baseChartOption,
  reportChartPalette,
  monitoringPhaseColors,
  formatTimestampForDisplay,
  // Constants
  OVERVIEW_STATUSES,
  PHASE_KEYS,
  HORIZONTAL_BAR_VISIBLE_ROWS,
  RUN_TABLE_PAGE_SIZES,
  DATE_GRAINS,
  RUNTIME_CONTEXT_AUTO_COLUMNS,
  RUNTIME_CONTEXT_COLUMN_GAP,
  RUNTIME_CONTEXT_MIN_COLUMN_WIDTHS,
  RUNTIME_CONTEXT_MAX_COLUMN_WIDTHS,
  // Overview helpers & sub-components
  HealthStripCard,
  FailureBreakdownDetail,
  RateBreakdownDetail,
  DetailMetric,
  healthReasonSummary,
  healthReasonsTooltip,
  failureCategoriesRuleTooltip,
  attentionQueueRuleTooltip,
  durationStatsTitle,
  DurationHeadline,
  WindowPairDetail,
  durationPercentilesDetail,
  ReportPanel,
  MonitoringChartLegend,
  StatusHealthLegend,
  StatusTrendLegend,
  WorkloadVolumeLegend,
  OperationHealthPanel,
  operationHealthCombinedBarOption,
  operationHealthValueAxis,
  operationHealthCategoryAxis,
  operationHealthStatusSeries,
  operationHealthGroupLabel,
  operationHealthDivider,
  RuntimePhaseContribution,
  RuntimePhaseLegend,
  RuntimePhaseMatrixTooltip,
  runtimePhaseContributionTooltip,
  runtimePhaseTotalRow,
  phaseRowLabel,
  runtimePhaseSegments,
  WorkloadVolumeContextPanel,
  workloadVolumeTrendOption,
  jobStatusTrendOption,
  dataflowStatusTrendOption,
  statusTrendOption,
  fillMissingTrendDates,
  createEmptyTrendRow,
  sortOperationRows,
  filterOperationRows,
  operationTotal,
  operationHealthTooltip,
  phaseLabel,
  createEmptyVolumeTrendRow,
  resolveTrendBucketKeys,
  resolveTrendDateKeys,
  resolveTrendHourKeys,
  resolveTrendWeekKeys,
  resolveTrendMonthKeys,
  rowLabel,
  normalizeToDateKey,
  timezoneDateKey,
  timezoneHourKey,
  floorToHour,
  enumerateHourKeys,
  parseFilterTime,
  parseHourKey,
  weekKeyFromDateKey,
  resolveClientTrendGrain,
  normalizeTrendGrain,
  normalizeTrendBucketKey,
  minimumClientTrendGrain,
  minimumClientTrendGrainForSpan,
  dateKeyToUtcDate,
  utcDateToDateKey,
  addDaysToDateKey,
  enumerateDateKeys,
  failureCategoryOption,
  slowestDataflowOption,
  slowestOrFailuresPanel,
  fixedHorizontalBarGrid,
  fixedHorizontalCategoryAxis,
  horizontalBarDataZoom,
  horizontalBarSeriesSizing,
  xCategoryDataZoom,
  bottomAnchoredValueXAxis,
  reportChartGrid,
  reportTightChartGrid,
  reportXAxisZoomGrid,
  operationColor,
  statusColor,
  resolveAttentionTarget,
  stageTotal,
  valueOf,
  trendRate,
  trendLineRate,
  mergeVolumeTrendRows,
  formatCompact,
  formatBytesShort,
  formatPercent,
  formatPhasePercent,
  formatRelativeTime,
  shortIdentifier,
  formatSecondsSingleDecimal,
  successRateIntent,
  durationIntent,
  // Jobs helpers & sub-components
  JobStageHealthPanel,
  jobStageHealthOption,
  LifecycleStatusValues,
  lifecycleStatusItems,
  JobDurationByOperationBoxPlot,
  DurationDistributionBoxPlot,
  durationDistributionBoxOption,
  durationOutlierPoints,
  JobWorkloadEfficiencyScatter,
  WorkloadEfficiencyLegend,
  workloadEfficiencyOperationTypes,
  jobWorkloadEfficiencyOption,
  ChildFanoutDistributionPanel,
  childFanoutDistributionOption,
  buildFanoutHistogramBins,
  autoHistogramTargetBins,
  niceHistogramBinSize,
  JobRuntimeConfigSignals,
  buildJobRuntimeConfigSignals,
  jobConfigModeSignal,
  isMeaningfulConfigValue,
  formatConfigValue,
  SlowestJobsList,
  EngineProviderHealth,
  RuntimeContextHeaderCell,
  buildRuntimeContextAutoWidths,
  fitRuntimeContextColumnWidths,
  measureRuntimeContextText,
  clampNumber,
  sortRuntimeContextRows,
  runtimeContextSortValue,
  sortIndicator,
  ChildImpactList,
  JobAttentionList,
  // Dataflows helpers & sub-components
  DataflowNameStatusHealthPanel,
  dataflowNameStatusHealthOption,
  DataflowEndpointHealthPanel,
  endpointRoutePresentation,
  EndpointRouteNode,
  DataflowWatermarkSignalPanel,
  // Failure helpers & sub-components
  FailureQueueTable,
  RepeatedFailureTable,
  EndpointImpactTable,
  FailureDataflowNameCell,
  FailureRouteCell,
  FailurePhaseBadge,
  FailureTrendLegend,
  FailurePhaseLegend,
  failureHorizontalBarOption,
  failureTrendOption,
  failureCategoryPhaseMatrixOption,
  // Shared run tables & cell renderers
  JobRunsTable,
  JobListValue,
  parseListValue,
  DataflowRunsTable,
  DataflowNameCell,
  DataflowContextCell,
  DataflowPhaseCell,
  normalizedPhaseSegments,
  optionalNum,
  phaseBottleneck,
  DataflowVolumeCell,
  CopyableText,
  compactRunId,
  CompactValue,
  TableDateTimeValue,
  monitoringTimezone,
  JobConfigCell,
  RuntimeContextCell,
  shortRuntimeName,
  JobVolumeCell,
  EndpointCell,
  monitoringEndpointPresentation,
  cleanDisplayValue,
  compactQualifiedTable,
  pythonFunctionTail,
  pathBasename,
  ChildFlowSummary,
  ReconciliationBadge,
  PhaseHealthBadge,
  WatermarkBadge,
  IssuePreview,
  humanize,
  formatCoverage,
  tableSubtitle,
  TablePager,
  DiagnosticsSeverity,
  WatermarkValue
};

export type { TableSort, HealthIntent, PhaseKey, FanoutHistogramBin, RuntimeContextSortKey };
