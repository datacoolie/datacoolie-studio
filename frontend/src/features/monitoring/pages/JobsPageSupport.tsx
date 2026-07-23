import type { JobRecord, MonitoringRecord, MonitoringReport } from "../../../shared/api/domainTypes";
import { useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties, PointerEvent as ReactPointerEvent, ReactNode } from "react";
import { createPortal } from "react-dom";
import type { MonitoringFilters, MonitoringTabKey } from "../monitoringFilters";
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
} from "../MonitoringCharts";
import { ReportChart, baseChartOption, reportChartPalette } from "../ReportChart";
import { formatTimestampForDisplay } from "../../../shared/time";
import { LineageFormatIcon } from "../../lineage/components/LineageFormatIcon";
import { assetIconKind } from "../../lineage/model/presentation";
import { CopyableText, DurationDistributionBoxPlot, IssuePreview, MonitoringChartLegend, TableDateTimeValue, childFanoutDistributionOption, compactRunId, humanize, jobStageHealthOption, jobWorkloadEfficiencyOption, operationColor, stageTotal, workloadEfficiencyOperationTypes } from "../components/monitoringPrimitives";

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

export function isMeaningfulConfigValue(value: unknown) {
  return value !== null && value !== undefined && value !== "";
}

export function formatConfigValue(value: unknown, key: string) {
  if (typeof value === "boolean") return value ? "true" : "false";
  if (key === "retention_hours" && typeof value === "number") return `${formatNumber(value)}h`;
  return String(value);
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

export function JobListValue({ value, multiline = false }: { value: unknown; multiline?: boolean }) {
  const values = parseListValue(value);
  const label = values.length ? values.join(", ") : "-";
  return (
    <span className={`monitor-compact-value job-run-list-value${multiline ? " is-multiline" : ""}`} title={label}>
      {label}
    </span>
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
    <span className="monitor-stack-cell" title={title}>
      <strong>{mainLabel}</strong>
      <small>{stopValue}</small>
    </span>
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
  const title = `Platform: ${platform} · Engine: ${engine}\nProvider: ${provider}`;
  return (
    <span className="monitor-stack-cell" title={title}>
      <strong>{mainLabel}</strong>
      <small>{provider}</small>
    </span>
  );
}

export function runtimeContextLines(row: JobRecord) {
  const engine = shortRuntimeName(row.engine_name, "engine");
  const platform = shortRuntimeName(row.platform_name, "platform");
  const provider = String(row.metadata_provider_name || "unknown");
  const mainLabel = `${platform} · ${engine}`;
  return [mainLabel, provider];
}

export function shortRuntimeName(value: unknown, kind: "engine" | "platform") {
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

export function JobVolumeCell({ row }: { row: JobRecord }) {
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

export function jobVolumeLines(row: JobRecord) {
  const rowsRead = num(row, "total_rows_read") || num(row, "child_total_rows_read");
  const rowsWritten = num(row, "total_rows_written") || num(row, "child_total_rows_written");
  const bytesAdded = num(row, "total_bytes_added") || num(row, "child_total_bytes_added");
  const bytesRemoved = num(row, "total_bytes_removed") || num(row, "child_total_bytes_removed");
  const netBytes = bytesAdded - bytesRemoved;
  const mainLabel = `${formatNumber(rowsRead)} / ${formatNumber(rowsWritten)}`;
  const netBytesLabel = formatBytes(netBytes);
  return [mainLabel, netBytesLabel];
}

export function ChildFlowSummary({ row }: { row: JobRecord }) {
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

export function childFlowSummaryLines(row: JobRecord) {
  const total = num(row, "child_dataflow_count") || num(row, "total_dataflows");
  const succeeded = num(row, "child_succeeded_count");
  const failed = num(row, "child_failed_count");
  const skipped = num(row, "child_skipped_count");
  const mainLabel = formatNumber(total);
  const statusLabel = `${formatNumber(succeeded)} / ${formatNumber(failed)} / ${formatNumber(skipped)}`;
  return [mainLabel, statusLabel];
}

export function ReconciliationBadge({ value }: { value: unknown }) {
  const textValue = String(value || "not_available");
  const intent = textValue === "mismatch" ? "bad" : textValue === "matched" ? "good" : "neutral";
  return <span className={`monitor-mini-badge badge-${intent}`}>{humanize(textValue)}</span>;
}
