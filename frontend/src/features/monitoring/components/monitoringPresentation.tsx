import { Help, HelpButton } from "../../../shared/components/Help";
import { useMemo } from "react";
import type { CSSProperties, ReactNode } from "react";
import {
  formatBytes as formatBytesApprox,
  formatSeconds,
  num
} from "../../../shared/components/DataTable";
import { formatTooltipBytes, formatTooltipNumber, metricDifference, presentNumber, toApproximateNumber, type MetricValue } from "../../../shared/lib/numberFormat";
import { formatTimestampForDisplay } from "../../../shared/time";
import { LineageFormatIcon } from "../../../shared/components/FormatIcon";
import { assetIconKind } from "../../../shared/formatPresentation";
import { monitoringConnectionStageClass } from "../monitoringStagePresentation";
import { reportChartPalette } from "../ReportChart";
import {
  type HealthCardAccent,
  type HealthIntent,
  type MonitoringRecord,
  type MonitoringReport,
  OVERVIEW_STATUSES,
  PHASE_KEYS,
  RUN_TABLE_PAGE_SIZES
} from "./monitoringModel";
import { phaseLabel } from "./monitoringTime";
import {
  lifecycleStatusItems,
  statusColor
} from "./monitoringChartOptions";
import {
  cleanDisplayValue,
  compactQualifiedTable,
  formatPhasePercent,
  formatSecondsSingleDecimal,
  humanize,
  optionalNum,
  pathBasename,
  pythonFunctionTail
} from "./monitoringFormatting";
import { effectiveMonitoringMessage } from "../monitoringMessage";
import { rowObservation } from "./monitoringObservation";
import { monitoringDimensionLabel, monitoringRawDimensionLabel } from "../monitoringDimensions";
import { phaseContributionBottleneck, rowPhaseContribution } from "../monitoringPhaseContribution";

function formatNumber(value: MetricValue) {
  return formatTooltipNumber(value);
}

function formatBytes(value: MetricValue) {
  const approximate = toApproximateNumber(value);
  return approximate === null ? "-" : formatBytesApprox(approximate);
}

export * from "./monitoringModel";
export * from "./monitoringTime";
export * from "./monitoringChartOptions";
export * from "./monitoringFormatting";
export * from "../../../shared/components/DataTable";
export * from "../../../shared/components/FormatIcon";
export * from "../../../shared/lib/numberFormat";
export { ReportChart, baseChartOption, reportChartPalette } from "../ReportChart";
export { formatConfigValue, isMeaningfulConfigValue } from "../monitoringConfigPresentation";
export { formatTimestampForDisplay, resolveIntlTimezone } from "../../../shared/time";
export { formatBytes, formatNumber };
export { LineageFormatIcon };
export { monitoringDimensionLabel };

/** Compact Monitoring metrics; exact transported values stay in title/aria. */
export function CompactNumberValue({
  value,
  prefix,
  suffix,
  fallback = "-",
  className,
  style,
}: {
  value: MetricValue;
  prefix?: string;
  suffix?: string;
  fallback?: string;
  className?: string;
  style?: CSSProperties;
}) {
  const presented = presentNumber(value, { prefix, suffix, compactStyle: "infix" });
  const display = presented.display === "-" ? fallback : presented.display;
  const tooltipValue = formatTooltipNumber(value);
  const exact = tooltipValue === "-" ? fallback : `${prefix ?? ""}${tooltipValue}${suffix ?? ""}`;
  return (
    <Help as="span" className={["monitor-compact-value", className].filter(Boolean).join(" ")} style={style} title={exact} aria-label={exact}>
      {display}
    </Help>
  );
}

/** Render a compact byte value while keeping the transported value in the tooltip. */
export function CompactBytesValue({
  value,
  fallback = "-",
  className,
  style,
}: {
  value: MetricValue;
  fallback?: string;
  className?: string;
  style?: CSSProperties;
}) {
  const display = formatBytes(value);
  const exact = formatTooltipBytes(value);
  return (
    <Help as="span" className={["monitor-compact-value", className].filter(Boolean).join(" ")} style={style} title={exact === "-" ? fallback : exact} aria-label={exact === "-" ? fallback : exact}>
      {display === "-" ? fallback : display}
    </Help>
  );
}

export function metricValue(row: Record<string, unknown>, key: string): MetricValue {
  const value = row[key];
  return typeof value === "string" || typeof value === "number" || value === null ? value : undefined;
}

export function metricNumber(value: MetricValue, fallback = 0) {
  if (value === null || value === undefined || value === "") return fallback;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

export function ObservedNumberValue({ row, field, totalField }: { row: Record<string, unknown>; field: string; totalField?: string }) {
  const observation = rowObservation(row, field, totalField);
  return <Help as="span" className="monitor-observed-value" content={{title:"Recorded measurement",metrics:[{label:field.replace(/_/g," "),value:formatTooltipNumber(observation.value)}],context:observation.coverage ?? undefined}}>
    <CompactNumberValue value={observation.value} />
    {observation.partial && observation.value != null ? <span className="monitor-observed-label">observed</span> : null}
  </Help>;
}

export function MeasurementCoverage({ row, field }: { row: Record<string, unknown>; field: string }) {
  const observation = rowObservation(row, field);
  return <Help as="span" className="monitor-measurement-coverage" title={observation.coverage ?? undefined} aria-label={observation.coverage ?? undefined}>{observation.compactCoverage ?? "Measurement coverage unavailable"}</Help>;
}

export function HealthStripCard({
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
  intent?: HealthIntent;
  accent?: HealthCardAccent;
  className?: string;
}) {
  return (
    <div className={`overview-health-card health-card-${intent} ${healthCardAccentClass(accent, intent)}${className ? ` ${className}` : ""}`}>
      <span className={title ? "health-card-label has-help" : "health-card-label"}>{label}</span>
      {title ? <HelpButton className="help-card-corner" content={{title: label, body: title}} label={`About ${label}`} /> : null}
      <strong>{value}</strong>
      {detail ? <small>{detail}</small> : null}
    </div>
  );
}

export function DetailMetric({
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

export function failureCategoriesRuleTooltip() {
  return [
    "Value source:",
    "- Count of failed dataflow runs in current filters.",
    "- Job status failures are context and are not used for error hints.",
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
    "- Only runs with status = failed contribute to failure phases.",
    "- Source, transform, destination: the corresponding phase status = failed; message may be empty.",
    "- Causal phase: first failed phase in source, transform, destination order.",
    "- Overhead: failed run with no explicitly failed execution phase, including empty messages.",
    "- Unknown: no classified failure phase is supplied.",
    "Display condition:",
    "- A hint appears when at least 1 failed dataflow run matches that hint.",
    "- Bars are sorted by count descending (tie-break by hint name)."
  ].join("\n");
}

export function durationStatsTitle(label: string, _stats: Record<string, MetricValue>) {
  return `${label} includes succeeded and failed runs.`;
}

export function formatDurationMetric(value: MetricValue) {
  const approximate = toApproximateNumber(value);
  return approximate === null ? "-" : formatSecondsSingleDecimal(approximate);
}

export function DurationHeadline({ avgSeconds, p50Seconds }: { avgSeconds: MetricValue; p50Seconds: MetricValue }) {
  return (
    <span className="health-duration-headline">
      <Help as="span" className="duration-avg" title={`${formatTooltipNumber(avgSeconds)} seconds`}>AVG {formatDurationMetric(avgSeconds)}</Help>
      <span className="separator"> - </span>
      <Help as="span" className="duration-p50" title={`${formatTooltipNumber(p50Seconds)} seconds`}>P50 {formatDurationMetric(p50Seconds)}</Help>
    </span>
  );
}

export function WindowPairDetail({
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

export function durationPercentilesDetail(stats: Record<string, MetricValue>) {
  const metricOr = (...keys: string[]): MetricValue => {
    for (const key of keys) {
      if (stats[key] !== undefined) return stats[key];
    }
    return undefined;
  };
  const p75 = metricOr("q3_duration_seconds", "p75_duration_seconds");
  const p95 = metricOr("p95_duration_seconds", "p99_duration_seconds");
  const p99 = metricOr("p99_duration_seconds", "p95_duration_seconds");
  return (
    <span className="health-duration-detail">
      <DetailMetric label="P75" value={<Help as="span" title={`${formatTooltipNumber(p75)} seconds`}>{formatDurationMetric(p75)}</Help>} tone="blue" labelFirst />
      <span className="separator"> · </span>
      <DetailMetric label="P95" value={<Help as="span" title={`${formatTooltipNumber(p95)} seconds`}>{formatDurationMetric(p95)}</Help>} tone="amber" labelFirst />
      <span className="separator"> · </span>
      <DetailMetric label="P99" value={<Help as="span" title={`${formatTooltipNumber(p99)} seconds`}>{formatDurationMetric(p99)}</Help>} tone="purple" labelFirst />
    </span>
  );
}

export function ReportPanel({
  title,
  subtitle,
  titleTooltip,
  className,
  headerAction,
  children
}: {
  title: string;
  subtitle?: ReactNode;
  titleTooltip?: string;
  className?: string;
  headerAction?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className={`overview-report-panel${className ? ` ${className}` : ""}`}>
      <div className="overview-report-panel-header">
        <div className="overview-report-panel-title"><strong>{title}</strong></div>
        {subtitle ? <span className="overview-report-panel-subtitle">{subtitle}</span> : null}
        {headerAction ? <div className="overview-report-panel-action">{headerAction}</div> : null}
      </div>
      {titleTooltip ? <div className="help-panel-corner"><HelpButton content={{title,body:titleTooltip}} label={`About ${title}`} /></div> : null}
      <div className="overview-report-panel-body">{children}</div>
    </section>
  );
}

export function RuntimePhaseContribution({
  rows,
  labelKey = "operation_type",
  emptyText = "No phase duration signals in current filters.",
  showLegend = true
}: {
  rows: Array<Record<string, string | number | null>>;
  labelKey?: string;
  emptyText?: string;
  showLegend?: boolean;
}) {
  const totalFromReport = rows.find((row) => num(row, "is_total") === 1);
  const visible = rows
    .filter((row) => num(row, "is_total") !== 1)
    .filter((row) => num(row, "total_duration_seconds") > 0 || num(row, "phase_runs_eligible") > 0)
    .sort((left, right) => {
      const duration = num(right, "total_duration_seconds") - num(left, "total_duration_seconds");
      if (duration !== 0) return duration;
      return phaseRowLabel(left, labelKey).localeCompare(phaseRowLabel(right, labelKey));
    });
  if (!visible.length) return <div className="table-empty">{emptyText}</div>;
  const totalRow = totalFromReport ?? runtimePhaseTotalRow(visible, labelKey);
  const displayRows = totalRow ? [totalRow, ...visible] : visible;
  return (
    <div className={`runtime-phase-contribution${showLegend ? "" : " runtime-phase-contribution-with-header-legend"}`}>
      {showLegend ? <RuntimePhaseLegend /> : null}
      <div className="runtime-phase-rows" style={{ gridTemplateRows: `repeat(${displayRows.length}, minmax(30px, 1fr))` }}>
        {displayRows.map((row) => {
          const totalDuration = optionalNum(row, "total_duration_seconds");
          const segments = runtimePhaseSegments(row);
          const label = phaseRowLabel(row, labelKey);
          return (
            <Help as="div" key={`${row.is_total ?? 0}:${row[labelKey] ?? row.group ?? row.operation_type}`} className={`runtime-phase-operation-row${num(row, "is_total") === 1 ? " runtime-phase-total-row" : ""}`} identity={row} help={<RuntimePhaseMatrixTooltip row={row} labelKey={labelKey} />}>
              <span className="runtime-phase-operation-label">
                <strong>{label}</strong>
                <small>
                  {totalDuration === null ? "Unavailable" : formatSeconds(totalDuration)}
                  {row.phase_runs_eligible != null ? <> · <CompactNumberValue value={row.phase_runs_eligible} /> runs</> : null}
                </small>
              </span>
              <div className="runtime-phase-bars"><div className="runtime-phase-stack">
                {segments.map(({ phase, percent, width }) => <span key={phase} className={`runtime-phase-segment phase-${phase}`} style={{ flexBasis: `${width}%` }}>{percent >= 15 ? formatPhasePercent(percent) : null}</span>)}
                {!segments.length ? <span className="monitor-measurement-coverage">{totalDuration === null ? "Phase timing unavailable" : "No elapsed phase time"}</span> : null}
              </div></div>
            </Help>
          );
        })}
      </div>
    </div>
  );
}

export function lifecycleStatusItemsForDisplay(skipped: MetricValue, running: MetricValue, pending: MetricValue) {
  return lifecycleStatusItems(metricNumber(skipped), metricNumber(running), metricNumber(pending));
}

export function LifecycleStatusValues({ skipped, running, pending }: { skipped: MetricValue; running: MetricValue; pending: MetricValue }) {
  const items = lifecycleStatusItemsForDisplay(skipped, running, pending);
  const rawValues: Record<string, MetricValue> = { skipped, running, pending };
  return <span className="job-lifecycle-status-values">{items.map((item, index) => <span key={item.status}>{index ? <span className="separator"> / </span> : null}<span className={`job-lifecycle-status-value status-${item.status}`} style={{ color: item.color }}><CompactNumberValue value={rawValues[item.status] ?? item.value} /></span></span>)}</span>;
}

export type MonitoringChartLegendItem = readonly [label: string, color: string];

export function MonitoringChartLegend({ label, items }: { label: string; items: ReadonlyArray<MonitoringChartLegendItem> }) {
  return <div className="monitoring-chart-legend" aria-label={label}>{items.map(([itemLabel, color]) => <span key={itemLabel}><i style={{ backgroundColor: color }} aria-hidden="true" />{itemLabel}</span>)}</div>;
}

export function StatusTrendLegend() {
  return <MonitoringChartLegend label="Run status legend" items={[...OVERVIEW_STATUSES.map((status) => [humanize(status), statusColor(status)] as const), ["Success rate", reportChartPalette.blue]]} />;
}

export function StatusHealthLegend() {
  return <MonitoringChartLegend label="Run status legend" items={OVERVIEW_STATUSES.map((status) => [humanize(status), statusColor(status)] as const)} />;
}

export function healthCardAccentClass(accent: HealthCardAccent, intent: HealthIntent) {
  const resolved = accent === "intent" ? intent : accent;
  return `health-card-accent-${resolved}`;
}

export function RuntimePhaseLegend() {
  return <div className="runtime-phase-legend" aria-label="Phase legend">{PHASE_KEYS.map((phase) => <span key={phase} className={`phase-chip phase-chip-${phase}`}>{phaseLabel(phase)}</span>)}</div>;
}

export function runtimePhaseContributionTooltip(grouping: string) {
  return `Phase duration shares by ${grouping} for all runs in the current filters. Phases use recorded final-attempt durations. Tooltip Total uses execution durations including retries, with AVG/P95 calculated from run observations and each run counted once. Missing values stay unavailable. Overhead is attempt time outside the recorded phases.`;
}

export function RuntimePhaseMatrixTooltip({ row, labelKey, style }: { row: Record<string, unknown>; labelKey: string; style?: CSSProperties }) {
  const label = phaseRowLabel(row, labelKey);
  const duration = (value: unknown) => {
    const seconds = toApproximateNumber(value as MetricValue);
    if (seconds === null) return "Unavailable";
    return seconds < 60 ? `${formatTooltipNumber(value as MetricValue)}s` : `${formatTooltipNumber(seconds / 60)}m`;
  };
  const phaseValue = (phase: typeof PHASE_KEYS[number], value: string) =>
    value === "Unavailable" || value === "-" ? value : <span className={`phase-text-${phase}`}>{value}</span>;
  const phaseRows = PHASE_KEYS.map((phase) => ({ phase, label: phaseLabel(phase), percent: optionalNum(row, `${phase}_duration_percent`), duration: row[`${phase}_duration_seconds`], avg: row[`${phase}_avg_duration_seconds`], p95: row[`${phase}_p95_duration_seconds`], runs: row[`${phase}_run_count`], failed: row[`${phase}_failed`] })).filter((item) => (item.percent ?? 0) > 0 || Number(item.duration ?? 0) > 0 || Number(item.runs ?? 0) > 0 || Number(item.failed ?? 0) > 0);
  const totalDuration = optionalNum(row, "run_scope_duration_seconds");
  const totalFailed = row.run_scope_failed_count as MetricValue;
  return <div className="help-content help-wide" style={style}>
    <table className="help-phase-table" aria-label={`Phase contribution for ${label}`}><thead><tr><th>Phase</th><th>%</th><th>Total</th><th>AVG</th><th>P95</th><th>Runs</th><th>Failed</th></tr></thead><tbody>{phaseRows.map((item) => <tr key={item.phase}>
      <td data-label="Phase"><span className={`phase-text-${item.phase}`}>{item.label}</span></td><td data-label="%">{phaseValue(item.phase, item.percent === null ? "Unavailable" : formatPhasePercent(item.percent))}</td>
      <td data-label="Total">{phaseValue(item.phase, duration(item.duration))}</td><td data-label="AVG">{phaseValue(item.phase, Number(item.runs ?? 0) > 0 ? duration(item.avg) : "Unavailable")}</td><td data-label="P95">{phaseValue(item.phase, Number(item.runs ?? 0) > 0 ? duration(item.p95) : "Unavailable")}</td>
      <td data-label="Runs">{phaseValue(item.phase, formatTooltipNumber(item.runs as MetricValue))}</td><td data-label="Failed" className={Number(item.failed ?? 0) > 0 ? "help-tone-failed" : ""}>{formatTooltipNumber(item.failed as MetricValue)}</td>
    </tr>)}</tbody><tfoot><tr>
      <td data-label="Phase">Total</td><td data-label="%">{totalDuration === null ? "—" : totalDuration > 0 ? "100%" : "0%"}</td>
      <td data-label="Total">{duration(row.run_scope_duration_seconds)}</td><td data-label="AVG">{duration(row.run_scope_avg_duration_seconds)}</td><td data-label="P95">{duration(row.run_scope_p95_duration_seconds)}</td>
      <td data-label="Runs">{formatTooltipNumber(row.run_scope_run_count as MetricValue)}</td><td data-label="Failed" className={Number(totalFailed ?? 0) > 0 ? "help-tone-failed" : ""}>{formatTooltipNumber(totalFailed)}</td>
    </tr></tfoot></table>
  </div>;
}

export function runtimePhaseTotalRow(rows: Array<Record<string, string | number | null>>, labelKey: string) {
  const durations = Object.fromEntries(PHASE_KEYS.map((phase) => [`${phase}_duration_seconds`, rows.reduce((sum, row) => sum + num(row, `${phase}_duration_seconds`), 0)])) as Record<string, number>;
  const phaseDurationTotal = PHASE_KEYS.reduce((sum, phase) => sum + durations[`${phase}_duration_seconds`], 0);
  if (phaseDurationTotal <= 0) return null;
  const totalRow: Record<string, string | number> = { [labelKey]: "Total", group: "Total", is_total: 1, total_duration_seconds: phaseDurationTotal };
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

export function phaseRowLabel(row: Record<string, unknown>, labelKey: string) {
  return monitoringDimensionLabel(row[labelKey] ?? row.group ?? row.operation_type);
}

export function runtimePhaseSegments(row: Record<string, unknown>) {
  const rawSegments = PHASE_KEYS.map((phase) => ({ phase, percent: Math.max(0, num(row, `${phase}_duration_percent`)) })).filter((segment) => segment.percent > 0);
  const totalPercent = rawSegments.reduce((sum, segment) => sum + segment.percent, 0);
  if (totalPercent <= 0) return [];
  return rawSegments.map((segment) => ({ ...segment, width: (segment.percent / totalPercent) * 100 }));
}

export function DataflowNameCell({ row }: { row: MonitoringRecord }) {
  const name = row.dataflow_name ?? row.dataflow_id ?? "-";
  const id = row.dataflow_id ?? "";
  return <Help as="span" className="monitor-inline-cell" title={`Dataflow: ${name}\nID: ${id || "-"}`}>{String(name)}</Help>;
}

export function DataflowContextCell({ row, rawDimensions = false }: { row: MonitoringRecord; rawDimensions?: boolean }) {
  const label = rawDimensions ? monitoringRawDimensionLabel : monitoringDimensionLabel;
  const stage = label(row.stage);
  const operation = label(row.operation_type);
  return <Help as="span" className="monitor-stack-cell" title={[`Stage: ${stage}`, `Dataflow operation: ${operation}`, `Destination operation: ${label(row.destination_operation_type)}`, `Load type: ${label(row.destination_load_type ?? row.load_type)}`, `Group / order: ${row.group_number ?? "-"} / ${row.execution_order ?? "-"}`, `Log schema version: ${row.log_schema_version ?? "-"}`].join("\n")}><strong>{stage}</strong><small>{operation}</small></Help>;
}

export function DataflowPhaseCell({ row, unknownLabel = "unk" }: { row: MonitoringRecord; unknownLabel?: string }) {
  const {root,timing}=useMemo(()=>rowPhaseContribution(row),[row]);
  const bottleneck=phaseContributionBottleneck(root,timing);
  const aggregated = Object.prototype.hasOwnProperty.call(row, "performance_source_duration_seconds");
  const title=[`Bottleneck: ${bottleneck.label}`,aggregated ? "Contribution from summed eligible run phase times." : "Contribution to recorded final-attempt phase time.",...timing.phases.map(phase=>`${phase.phase[0].toUpperCase()+phase.phase.slice(1)}: ${phase.duration===null ? "Unavailable" : formatSeconds(phase.duration)}${phase.share===null ? "" : ` · ${phase.share.toFixed(1)}%`}${phase.status ? ` · ${phase.status}` : ""}`),`Error phase: ${row.error_phase ?? "-"}`].join("\n");
  return <Help as="span" className="dataflow-phase-cell" title={title}><strong className="dataflow-phase-reason">{bottleneck.phases.length ? bottleneck.phases.map((phase,index)=><span key={phase}>{index ? " · " : ""}<span className={`phase-text-${phase}`}>{phase[0].toUpperCase()+phase.slice(1)}</span></span>) : unknownLabel}</strong><span className="dataflow-phase-mini-stack" aria-label="Source, transform, destination, and overhead phase contribution">{timing.phases.filter(phase=>phase.share!==null && phase.share>0).map(phase=><i key={phase.phase} className={`phase-${phase.phase}`} style={{flex:`0 0 ${phase.share}%`}} />)}</span></Help>;
}

export function DataflowVolumeCell({ row }: { row: MonitoringRecord }) {
  const rowsRead = metricValue(row, "volume_rows_read") ?? metricValue(row, "source_rows_read");
  const rowsWritten = metricValue(row, "volume_rows_written") ?? metricValue(row, "destination_rows_written");
  const bytesAdded = metricValue(row, "volume_bytes_added") ?? metricValue(row, "destination_bytes_added");
  const bytesRemoved = metricValue(row, "volume_bytes_removed") ?? metricValue(row, "destination_bytes_removed");
  const netBytes = metricValue(row, "volume_net_bytes") ?? metricDifference(bytesAdded, bytesRemoved);
  return <Help as="span" className="monitor-stack-cell" content={{title:"Workload volume", metrics:[
    {label:"Rows read", value:formatTooltipNumber(rowsRead), valueColor:reportChartPalette.read}, {label:"Rows written",value:formatTooltipNumber(rowsWritten), valueColor:reportChartPalette.written},
    {label:"Lakehouse bytes added",value:formatTooltipBytes(bytesAdded), valueColor:reportChartPalette.teal}, {label:"Lakehouse bytes removed",value:formatTooltipBytes(bytesRemoved), valueColor:reportChartPalette.failed},
    {label:"Net lakehouse bytes",value:formatTooltipBytes(netBytes)},
  ]}}><strong><CompactNumberValue value={rowsRead} /> / <CompactNumberValue value={rowsWritten} /></strong><small><CompactBytesValue value={netBytes} /></small></Help>;
}

export function CopyableText({ value, displayValue }: { value: unknown; displayValue?: string }) {
  const textValue = value === null || value === undefined || value === "" ? "-" : String(value);
  const visibleValue = displayValue ?? textValue;
  return <Help as="button" className="monitor-copy-value" type="button" title={textValue === "-" ? "" : `Copy ${textValue}`} onClick={(event) => { event.stopPropagation(); if (textValue !== "-") void navigator.clipboard?.writeText(textValue); }}>{visibleValue}</Help>;
}

export function compactRunId(value: unknown) {
  if (value === null || value === undefined || value === "") return "-";
  const textValue = String(value);
  if (textValue.length <= 18) return textValue;
  return `${textValue.slice(0, 8)}...${textValue.slice(-6)}`;
}

export function CompactValue({ value, fallback = "-" }: { value: unknown; fallback?: string }) {
  if (typeof value === "number") return <CompactNumberValue value={value} fallback={fallback} />;
  const textValue = value === null || value === undefined || value === "" ? fallback : String(value);
  return <Help as="span" className="monitor-compact-value" title={textValue}>{textValue}</Help>;
}

export function TableDateTimeValue({ value, fallback = "-", timezoneName, showTooltip = true }: { value: unknown; fallback?: string; timezoneName?: string | null; showTooltip?: boolean }) {
  const rawValue = value === null || value === undefined || value === "" ? fallback : String(value);
  const textValue = rawValue === fallback ? fallback : formatTimestampForDisplay(rawValue, timezoneName, fallback);
  return showTooltip
    ? <Help as="span" className="monitor-compact-value" title={textValue}>{textValue}</Help>
    : <span className="monitor-compact-value">{textValue}</span>;
}

export function monitoringTimezone(report: MonitoringReport) {
  return report.summary.timezone || "UTC";
}

export function EndpointCell({ row, direction, rawDimensions = false }: { row: MonitoringRecord; direction: "source" | "destination"; rawDimensions?: boolean }) {
  const endpoint = monitoringEndpointPresentation(row, direction, rawDimensions);
  const stageClass = monitoringConnectionStageClass(endpoint.connection);
  return <Help as="span" className={["monitor-endpoint-cell", stageClass].filter(Boolean).join(" ")} title={endpoint.title}>{endpoint.format ? <span className="monitor-endpoint-icon" aria-hidden="true"><LineageFormatIcon kind={assetIconKind(endpoint.format)} label={endpoint.format} size={18} /></span> : null}<span className="monitor-endpoint-text"><strong>{endpoint.locator}</strong><small>{endpoint.connection}</small></span></Help>;
}

export function monitoringEndpointPresentation(row: MonitoringRecord, direction: "source" | "destination", rawDimensions = true) {
  const prefix = direction === "source" ? "source" : "destination";
  const value = (key: string) => row[`${prefix}_${key}` as keyof MonitoringRecord];
  const connection = monitoringRawDimensionLabel(value("name") ?? value("connection_name"));
  const format = (rawDimensions || direction === "destination" ? monitoringRawDimensionLabel : monitoringDimensionLabel)(value("format"));
  const table = cleanDisplayValue(value("table"));
  const fullTable = cleanDisplayValue(value("full_table"));
  const path = cleanDisplayValue(value("path"));
  const query = direction === "source" ? cleanDisplayValue(value("query")) : null;
  const pythonFunction = direction === "source" ? cleanDisplayValue(value("python_function")) : null;
  const display = cleanDisplayValue(value("display"));
  const locator = pythonFunctionTail(pythonFunction) ?? (query ? "SQL query" : null) ?? table ?? compactQualifiedTable(fullTable) ?? pathBasename(path) ?? display ?? "Unknown asset";
  const title = [`Asset: ${locator}`, `Connection: ${connection}`, format ? `Format: ${format}` : null, table ? `Table: ${table}` : null, fullTable ? `Full table: ${fullTable}` : null, path ? `Path: ${path}` : null, query ? "Query: SQL query" : null, pythonFunction ? `Python function: ${pythonFunction}` : null].filter(Boolean).join("\n");
  return { locator, connection, format, title };
}

export function WatermarkBadge({ value }: { value: unknown }) {
  const textValue = String(value || "not_configured");
  const normalized = textValue.trim().toLowerCase();
  const intent = normalized === "advanced" || normalized === "saved" ? "good" : normalized === "initialized" || normalized === "empty" ? "neutral" : ["changed", "unchanged", "unknown"].includes(normalized) ? "warning" : normalized === "save_error" || normalized === "load_error" ? "failed" : "neutral";
  const description = WATERMARK_BADGE_TOOLTIPS[normalized] ?? humanize(textValue);
  return <Help as="span" className={`monitor-mini-badge badge-${intent}`} title={description}>{humanize(textValue)}</Help>;
}

const WATERMARK_BADGE_TOOLTIPS: Record<string, string> = {
  advanced: "Watermark moved forward.",
  initialized: "First watermark value was recorded.",
  changed: "Saved watermark changed without confirmed forward movement.",
  unchanged: "Saved watermark stayed the same.",
  empty: "Incremental read returned no rows; no watermark was persisted.",
  unknown: "Watermark movement could not be determined.",
  saved: "Watermark was saved.",
  save_error: "Watermark save failed.",
  load_error: "Watermark load failed.",
  configured: "Watermark is configured.",
  not_configured: "No watermark configuration or native watermark evidence.",
};

export function IssuePreview({ row }: { row: Record<string, unknown> }) {
  const message = effectiveMonitoringMessage(row);
  if (message === null) return <span className="monitor-muted">-</span>;
  return <Help as="span" className="monitor-issue-preview" title={message}>{message}</Help>;
}

export function TablePager({ limit, offset, loadedRows, totalRows, loading, onPageChange, onPageSizeChange }: { limit: number; offset: number; loadedRows: number; totalRows: number; loading: boolean; onPageChange: (offset: number) => void; onPageSizeChange?: (limit: number) => void }) {
  const currentStart = totalRows ? offset + 1 : 0;
  const currentEnd = Math.min(totalRows, offset + loadedRows);
  const lastOffset = Math.max(0, Math.floor(Math.max(0, totalRows - 1) / limit) * limit);
  const canPrevious = offset > 0 && !loading;
  const canNext = offset + limit < totalRows && !loading;
  return <div className="table-pager"><span className="table-pager-range">{formatNumber(currentStart)}-{formatNumber(currentEnd)} of {formatNumber(totalRows)}</span><div>{onPageSizeChange ? <label className="table-pager-size">Rows<select value={limit} disabled={loading} onChange={(event) => onPageSizeChange(Number(event.target.value))}>{RUN_TABLE_PAGE_SIZES.map((size) => <option key={size} value={size}>{size}</option>)}</select></label> : null}<button className="text-action" type="button" disabled={!canPrevious} onClick={() => onPageChange(0)}>First</button><button className="text-action" type="button" disabled={!canPrevious} onClick={() => onPageChange(Math.max(0, offset - limit))}>Prev</button><button className="text-action" type="button" disabled={!canNext} onClick={() => onPageChange(offset + limit)}>Next</button><button className="text-action" type="button" disabled={!canNext} onClick={() => onPageChange(lastOffset)}>Last</button></div></div>;
}
