import { Help } from "../../../shared/components/Help";
import { monitoringRawDimensionLabel } from "../monitoringDimensions";
import type { JobRecord } from "../../../shared/api/domainTypes";
import {
  DataTable,
  StatusCell,
  type TableSort,
  formatSeconds,
  num
} from "../../../shared/components/DataTable";
import { ReportChart } from "../ReportChart";
import { formatTimestampForDisplay } from "../../../shared/time";
import { DurationDistributionBoxPlot } from "../components/monitoringDurationPresentation";
import { childFanoutDistributionOption, jobStageHealthOption, jobWorkloadEfficiencyOption, workloadEfficiencyOperationTypes } from "./JobsPagePresentation";
import { CompactBytesValue, CompactNumberValue, CopyableText, IssuePreview, MonitoringChartLegend, TableDateTimeValue, formatCompact, formatTooltipBytes, formatTooltipNumber, humanize, metricNumber, metricValue, operationColor, stageTotal } from "../components/monitoringPresentation";
import { formatConfigValue, isMeaningfulConfigValue } from "../monitoringConfigPresentation";
import { metricDifference, toApproximateNumber, type MetricValue } from "../../../shared/lib/numberFormat";
import { jobReconciliationStatus } from "../details/jobStoryModel";

export function JobStageHealthPanel({ rows }: { rows: Array<Record<string, string | number>> }) {
  const visible = rows
    .filter((row) => String(row.stage ?? "").trim() && stageTotal(row) > 0);
  if (!visible.length) return <div className="table-empty">No job x stage signals in current filters.</div>;
  return (
    <div className="overview-operation-health">
      <ReportChart option={jobStageHealthOption(visible)} height="100%" wheelDataZoomStep={1} />
    </div>
  );
}

export function JobDurationByOperationBoxPlot({ rows }: { rows: Array<Record<string, unknown>> }) {
  return <DurationDistributionBoxPlot
    rows={rows}
    labelKey="operation_type"
    emptyText="No job operation duration data in current filters."
    entityKind="job"
  />;
}

export function JobWorkloadEfficiencyScatter({
  rows
}: {
  rows: Array<Record<string, string | number | null>>;
}) {
  const visible = rows.filter((row) => num(row, "duration_seconds") > 0);
  if (!visible.length) return <div className="table-empty">No workload efficiency points in current filters.</div>;
  return (
    <div className="monitoring-job-chart-fill">
      <ReportChart option={jobWorkloadEfficiencyOption(visible)} height="100%" />
    </div>
  );
}

export function WorkloadEfficiencyLegend({ rows }: { rows: Array<Record<string, string | number | null>> }) {
  const operationTypes = workloadEfficiencyOperationTypes(rows);
  return <MonitoringChartLegend
    label="Workload efficiency operation legend"
    items={operationTypes.map((operationType, index) => [humanize(operationType), operationColor(operationType, index)] as const)}
  />;
}

export function ChildFanoutDistributionPanel({ rows }: { rows: Array<Record<string, string | number | null>> }) {
  const visible = buildFanoutHistogramBins(rows);
  if (!visible.length) return <div className="table-empty">No child fan-out histogram in current filters.</div>;
  return (
    <div className="monitoring-job-chart-fill">
      <ReportChart option={childFanoutDistributionOption(visible)} height="100%" />
    </div>
  );
}

export type FanoutHistogramBin = {
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

export function buildFanoutHistogramBins(rows: Array<Record<string, string | number | null>>): FanoutHistogramBin[] {
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

export function autoHistogramTargetBins(rows: Array<{ totalDataflows: number; jobs: number }>) {
  const totalJobs = rows.reduce((sum, row) => sum + row.jobs, 0);
  const distinctValues = new Set(rows.map((row) => row.totalDataflows)).size;
  const sqrtRule = Math.ceil(Math.sqrt(Math.max(totalJobs, 1)) * 1.6);
  const sturgesRule = Math.ceil(Math.log2(Math.max(totalJobs, 1)) + 1);
  const densityRule = Math.min(24, Math.max(1, distinctValues * 2));
  return Math.max(8, Math.min(24, Math.max(sqrtRule, sturgesRule, densityRule)));
}

export function niceHistogramBinSize(maxValue: number, targetBins: number) {
  if (maxValue <= targetBins) return 1;
  const raw = maxValue / targetBins;
  const magnitude = Math.pow(10, Math.floor(Math.log10(raw)));
  const normalized = raw / magnitude;
  const nice = normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 5 ? 5 : 10;
  return Math.max(1, nice * magnitude);
}

export function JobRunsTable({
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
      compactNumbers
      compactNumberStyle="infix"
      columns={[
        { key: "job_id", label: "Job", sortable: true, width: 132, className: "job-run-col-job", render: (row) => <JobRunIdentityCell row={row} /> },
        { key: "job_config", label: "Config", autoFit: true, minWidth: 72, maxWidth: 128, className: "job-run-col-config", render: (row) => <JobConfigCell row={row} />, measureValue: (row) => jobConfigLines(row) },
        { key: "runtime_context", label: "Runtime", autoFit: true, minWidth: 72, maxWidth: 170, className: "job-run-col-runtime", render: (row) => <RuntimeContextCell row={row} />, measureValue: (row) => runtimeContextLines(row) },
        { key: "stages", label: "Stages", sortable: true, autoFit: true, minWidth: 110, maxWidth: 160, fillPriority: "last", className: "job-run-col-stages", render: (row) => <JobListValue value={row.stages} multiline />, measureValue: (row) => parseListValue(row.stages).join(", ") || "-" },
        { key: "operation_types", label: "Operation", sortable: true, autoFit: true, minWidth: 86, maxWidth: 160, className: "job-run-col-operation", render: (row) => <JobListValue value={row.operation_types} />, measureValue: (row) => parseListValue(row.operation_types).join(", ") || "-" },
        { key: "start_time", label: "Start", sortable: true, autoFit: true, minWidth: 144, maxWidth: 190, className: "job-run-col-time", render: (row) => <TableDateTimeValue value={row.start_time} timezoneName={timezoneName} />, measureValue: (row, activeTimezone) => formatTimestampForDisplay(row.start_time, activeTimezone, "-") },
        { key: "end_time", label: "End", sortable: true, autoFit: true, minWidth: 144, maxWidth: 190, className: "job-run-col-time", render: (row) => <TableDateTimeValue value={row.end_time} timezoneName={timezoneName} />, measureValue: (row, activeTimezone) => formatTimestampForDisplay(row.end_time, activeTimezone, "-") },
        { key: "duration_seconds", label: "Duration", sortable: true, autoFit: true, minWidth: 76, maxWidth: 96, className: "job-run-col-duration", render: (row) => formatSeconds(num(row, "duration_seconds")), measureValue: (row) => formatSeconds(num(row, "duration_seconds")) },
        { key: "status", label: "Status", sortable: true, autoFit: true, minWidth: 88, maxWidth: 112, className: "job-run-col-status", render: (row) => <StatusCell row={row} />, measureValue: (row) => String(row.status || "unknown") },
        { key: "child_dataflow_count", label: "Child flows", autoFit: true, minWidth: 92, maxWidth: 132, className: "job-run-col-child", render: (row) => <ChildFlowSummary row={row} />, measureValue: (row) => childFlowSummaryCompactLines(row) },
        { key: "volume", label: "Volume", autoFit: true, minWidth: 96, maxWidth: 142, className: "job-run-col-volume", render: (row) => <JobVolumeCell row={row} />, measureValue: (row) => jobVolumeCompactLines(row) },
        { key: "reconciliation_status", label: "Reconcile", autoFit: true, minWidth: 94, maxWidth: 128, className: "job-run-col-reconcile", render: (row) => <ReconciliationBadge value={jobReconciliationStatus(row)} />, measureValue: (row) => humanize(jobReconciliationStatus(row)) },
        { key: "message", label: "Message", sortable: true, minWidth: 140, fillPriority: "last", className: "job-run-col-issue", render: (row) => <IssuePreview row={row} /> }
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

function JobRunIdentityCell({ row }: { row: JobRecord }) {
  const jobNumber = jobOrdinalValue(row.job_num);
  const jobIndex = jobOrdinalValue(row.job_index);
  const ordinalSuffix = `:${jobNumber}|${jobIndex}`;
  return <span className="job-run-identity-cell">
    <CopyableText value={row.job_id} displayValue={compactJobRunId(row.job_id, ordinalSuffix)} />
    <Help as="span" className="job-run-ordinal-suffix" title={`Job number: ${jobNumber}\nJob index: ${jobIndex}`}>{ordinalSuffix}</Help>
  </span>;
}

function compactJobRunId(value: unknown, suffix: string) {
  const text = value === null || value === undefined || value === "" ? "-" : String(value);
  const maxCharacters = Math.max(7, 19 - suffix.length);
  if (text.length <= maxCharacters) return text;
  const edgeCharacters = Math.floor((maxCharacters - 5) / 2);
  return `${text.slice(0, edgeCharacters)}-...-${text.slice(-edgeCharacters)}`;
}

function jobOrdinalValue(value: unknown) {
  return value === null || value === undefined || value === "" || typeof value === "object" ? "unk" : String(value);
}

export function JobListValue({ value, multiline = false }: { value: unknown; multiline?: boolean }) {
  const values = parseListValue(value);
  const label = values.length ? values.map(monitoringRawDimensionLabel).join(", ") : "Unknown";
  return (
    <Help as="span" className={`monitor-compact-value job-run-list-value${multiline ? " is-multiline" : ""}`} title={label}>
      {label}
    </Help>
  );
}

export function parseListValue(value: unknown) {
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

export function JobConfigCell({ row }: { row: JobRecord }) {
  const [mainLabel, stopValue] = jobConfigLines(row);
  const title = `${mainLabel === "-" ? "Workers / Retry: -" : mainLabel.replace(/^w /u, "Workers: ").replace(/ · r /u, " · Retry: ")}\nStop on error: ${stopValue === "-" ? "-" : stopValue.replace(/^stop /u, "")}`;
  return (
    <Help as="span" className="monitor-stack-cell" title={title}>
      <strong>{mainLabel}</strong>
      <small>{stopValue}</small>
    </Help>
  );
}

export function jobConfigLines(row: JobRecord) {
  const mainParts = [
    isMeaningfulConfigValue(row.max_workers) ? `w ${formatConfigValue(row.max_workers, "max_workers")}` : "",
    isMeaningfulConfigValue(row.retry_count) ? `r ${formatConfigValue(row.retry_count, "retry_count")}` : ""
  ].filter(Boolean);
  const stopValue = isMeaningfulConfigValue(row.stop_on_error) ? `stop ${formatConfigValue(row.stop_on_error, "stop_on_error")}` : "-";
  const mainLabel = mainParts.length ? mainParts.join(" · ") : "-";
  return [mainLabel, stopValue];
}

export function RuntimeContextCell({ row }: { row: JobRecord }) {
  const [mainLabel, provider] = runtimeContextLines(row);
  const [platform, engine] = mainLabel.split(" · ");
  const title = `Platform: ${platform} · Engine: ${engine}\nProvider: ${provider}\nLog schema version: ${row.log_schema_version ?? "-"}`;
  return (
    <Help as="span" className="monitor-stack-cell" title={title}>
      <strong>{mainLabel}</strong>
      <small>{provider}</small>
    </Help>
  );
}

export function runtimeContextLines(row: JobRecord) {
  const engine = shortRuntimeName(row.engine_name, "engine");
  const platform = shortRuntimeName(row.platform_name, "platform");
  const provider = monitoringRawDimensionLabel(row.metadata_provider_name);
  const mainLabel = `${platform} · ${engine}`;
  return [mainLabel, provider];
}

export function shortRuntimeName(value: unknown, _kind: "engine" | "platform") {
  const textValue = monitoringRawDimensionLabel(value);
  const stripped = textValue
    .replace(/\b(engine|platform)\b/giu, "")
    .replace(/(engine|platform)$/iu, "")
    .replace(/[_-]+/gu, " ")
    .replace(/\s+/gu, " ")
    .trim();
  return stripped || textValue;
}

export function JobVolumeCell({ row }: { row: JobRecord }) {
  const rowsRead = firstMetric(row, "total_rows_read", "child_total_rows_read");
  const rowsWritten = firstMetric(row, "total_rows_written", "child_total_rows_written");
  const bytesAdded = firstMetric(row, "total_bytes_added", "child_total_bytes_added");
  const bytesRemoved = firstMetric(row, "total_bytes_removed", "child_total_bytes_removed");
  const netBytes = metricDifference(bytesAdded, bytesRemoved);
  return (
    <Help as="span"
      className="monitor-stack-cell"
      title={`Rows read / written: ${formatTooltipNumber(rowsRead)} / ${formatTooltipNumber(rowsWritten)}\nBytes added: ${formatTooltipBytes(bytesAdded)} · Bytes removed: ${formatTooltipBytes(bytesRemoved)} · Net bytes: ${formatTooltipBytes(netBytes)}`}
    >
      <strong><CompactNumberValue value={rowsRead} /> / <CompactNumberValue value={rowsWritten} /></strong>
      <small><CompactBytesValue value={netBytes} /></small>
    </Help>
  );
}

function jobVolumeCompactLines(row: JobRecord) {
  const rowsRead = firstMetric(row, "total_rows_read", "child_total_rows_read");
  const rowsWritten = firstMetric(row, "total_rows_written", "child_total_rows_written");
  const bytesAdded = firstMetric(row, "total_bytes_added", "child_total_bytes_added");
  const bytesRemoved = firstMetric(row, "total_bytes_removed", "child_total_bytes_removed");
  const netBytes = metricDifference(bytesAdded, bytesRemoved);
  return [`${formatCompactMetric(rowsRead)} / ${formatCompactMetric(rowsWritten)}`, formatTooltipBytes(netBytes)];
}

function firstMetric(row: Record<string, unknown>, ...keys: string[]): MetricValue {
  for (const key of keys) {
    const value = metricValue(row, key);
    if (value !== undefined && value !== null) return value;
  }
  return undefined;
}

function formatCompactMetric(value: MetricValue) {
  const approximate = toApproximateNumber(value);
  return approximate === null ? "-" : formatCompact(approximate);
}

export function ChildFlowSummary({ row }: { row: JobRecord }) {
  const total = firstMetric(row, "child_dataflow_count", "total_dataflows");
  const succeeded = metricValue(row, "child_succeeded_count");
  const failed = metricValue(row, "child_failed_count");
  const skipped = metricValue(row, "child_skipped_count");
  const succeededNumber = metricNumber(succeeded);
  const failedNumber = metricNumber(failed);
  const skippedNumber = metricNumber(skipped);
  return (
    <Help as="span"
      className="monitor-stack-cell monitor-child-summary"
      title={`Total child flows: ${formatTooltipNumber(total)}\nSucceeded: ${formatTooltipNumber(succeeded)} / Failed: ${formatTooltipNumber(failed)} / Skipped: ${formatTooltipNumber(skipped)}`}
    >
      <strong><CompactNumberValue value={total} /></strong>
      <small>
        <span className={succeededNumber > 0 ? "monitor-child-value is-success" : "monitor-child-value"}><CompactNumberValue value={succeeded} /></span>
        <span> / </span>
        <span className={failedNumber > 0 ? "monitor-child-value is-failed" : "monitor-child-value"}><CompactNumberValue value={failed} /></span>
        <span> / </span>
        <span className={skippedNumber > 0 ? "monitor-child-value is-skipped" : "monitor-child-value"}><CompactNumberValue value={skipped} /></span>
      </small>
    </Help>
  );
}

function childFlowSummaryCompactLines(row: JobRecord) {
  const total = firstMetric(row, "child_dataflow_count", "total_dataflows");
  const succeeded = metricValue(row, "child_succeeded_count");
  const failed = metricValue(row, "child_failed_count");
  const skipped = metricValue(row, "child_skipped_count");
  return [
    formatCompactMetric(total),
    `${formatCompactMetric(succeeded)} / ${formatCompactMetric(failed)} / ${formatCompactMetric(skipped)}`
  ];
}

export function ReconciliationBadge({ value }: { value: unknown }) {
  const textValue = String(value || "not_available");
  const intent = textValue === "mismatch" ? "bad" : textValue === "matched" ? "good" : "neutral";
  return <span className={`monitor-mini-badge badge-${intent}`}>{humanize(textValue)}</span>;
}
