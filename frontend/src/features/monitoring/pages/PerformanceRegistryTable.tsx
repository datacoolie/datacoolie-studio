import { memo, useMemo } from "react";
import type { MonitoringRecord } from "../../../shared/api/domainTypes";
import type { TableColumn, TableSort } from "../../../shared/components/DataTable";
import { StatusPill } from "../../../shared/components/StatusPill";
import { Help } from "../../../shared/components/Help";
import type { HelpContent } from "../../../shared/components/helpContent";
import { lifecycleStatusPresentation } from "../../../shared/statusPresentation";
import { formatCompactNumber } from "../../../shared/lib/numberFormat";
import { formatTimestampForDisplay } from "../../../shared/time";
import { monitoringPhaseColors } from "./FailurePagePresentation";
import {
  CompactNumberValue,
  DataTable,
  EndpointCell,
  formatDurationMetric,
  formatTooltipBytes,
  formatTooltipNumber,
  metricNumber,
  metricValue,
  monitoringDimensionLabel,
  monitoringEndpointPresentation,
  type MetricValue,
  type DataTableVirtualizationOptions,
} from "../components/monitoringPresentation";
import { phaseContributionBottleneck, rowPhaseContribution } from "../monitoringPhaseContribution";

const REGISTRY_VIRTUALIZATION: DataTableVirtualizationOptions = { overscan: 5 };

export const PerformanceRegistryTable = memo(function PerformanceRegistryTable({
  rows,
  sort,
  onSort,
  onInspect,
  timezoneName,
  scrollResetKey,
}: {
  rows: MonitoringRecord[];
  sort?: TableSort;
  onSort: (sort: TableSort) => void;
  onInspect?: (row: MonitoringRecord) => void;
  timezoneName?: string | null;
  scrollResetKey?: string;
}) {
  const columns = useMemo<TableColumn<MonitoringRecord>[]>(() => [
    {
      key: "dataflow_name",
      label: "Dataflow",
      sortable: true,
      autoFit: true,
      minWidth: 152,
      maxWidth: 200,
      className: "performance-registry-dataflow-column",
      render: (row) => <DataflowRegistryNameCell row={row} />,
      measureValue: (row) => String(row.dataflow_name || "Unknown"),
    },
    {
      key: "stage",
      label: "Stage",
      sortable: true,
      minWidth: 90,
      maxWidth: 150,
      fillPriority: "normal",
      className: "performance-registry-stage-column",
      render: (row) => monitoringDimensionLabel(row.stage),
      measureValue: (row) => monitoringDimensionLabel(row.stage),
    },
    {
      key: "source_name",
      label: "Source",
      headerTitle: "Latest filtered source endpoint. Sorting uses connection name; table/path is display context.",
      sortable: true,
      width: 210,
      minWidth: 160,
      className: "performance-registry-endpoint-column",
      render: (row) => <EndpointCell row={row} direction="source" />,
      measureValue: (row) => endpointMeasureValue(row, "source"),
    },
    {
      key: "destination_name",
      label: "Destination",
      headerTitle: "Latest filtered destination endpoint. Sorting uses connection name; table/path is display context.",
      sortable: true,
      width: 210,
      minWidth: 160,
      className: "performance-registry-endpoint-column",
      render: (row) => <EndpointCell row={row} direction="destination" />,
      measureValue: (row) => endpointMeasureValue(row, "destination"),
    },
    {
      key: "run_count",
      label: "Runs",
      sortable: true,
      sortKey: "run_count",
      autoFit: true,
      maxWidth: Number.POSITIVE_INFINITY,
      className: "performance-registry-scalar-column",
      render: (row) => <PerformanceRunsCell row={row} />,
      measureValue: (row) => [formatCompactNumber(metricValue(row, "run_count"), "infix"), lifecycleCompactText(row)],
    },
    {
      key: "performance_p95_duration_seconds",
      label: "Runtime",
      headerTitle: "Total run time; second line: Avg – P95. Sorting uses P95 duration.",
      sortable: true,
      sortKey: "p95_duration_seconds",
      autoFit: true,
      maxWidth: Number.POSITIVE_INFINITY,
      className: "performance-registry-scalar-column",
      render: (row) => <RuntimeCell row={row} />,
      measureValue: (row) => [formatDurationMetric(totalRunTimeValue(row).value) + (isPartial(metricValue(row, "performance_duration_measured"), metricValue(row, "performance_timing_eligible")) ? "*" : ""), runtimeSummary(row)],
    },
    {
      key: "processed",
      label: "Workload",
      headerTitle: "Total rows (r) – bytes (b); second line: r/s – b/s. * indicates observed partial coverage.",
      autoFit: true,
      maxWidth: Number.POSITIVE_INFINITY,
      className: "performance-registry-workload-column",
      render: (row) => <WorkloadCell row={row} />,
      measureValue: (row) => [workloadMeasureValue(row, false), workloadMeasureValue(row, true)],
    },
    {
      key: "phase",
      label: "Phase",
      width: 100,
      minWidth: 80,
      className: "performance-registry-phase-column",
      render: (row) => <PerformancePhaseCell row={row} />,
    },
    {
      key: "latest_run_at",
      label: "Latest run",
      sortable: true,
      sortKey: "latest_run_at",
      autoFit: true,
      minWidth: 148,
      maxWidth: 194,
      className: "performance-registry-latest-column",
      render: (row) => <LatestRunCell row={row} timezoneName={timezoneName} />,
      measureValue: (row) => [formatTimestamp(row.latest_run_at, timezoneName), monitoringDimensionLabel(row.latest_run_status ?? row.status)],
    },
    {
      key: "performance_candidate_count",
      label: "Runs with signals",
      headerTitle: "Unique runs matching a performance signal. Sorting uses the run count.",
      sortable: true,
      sortKey: "performance_candidate_count",
      minWidth: 180,
      fillPriority: "last",
      className: "performance-registry-signals-column",
      render: (row) => <PerformanceSignalsCell row={row} />,
    },
  ], [timezoneName]);

  return (
    <DataTable<MonitoringRecord>
      rows={rows}
      compactNumbers
      compactNumberStyle="infix"
      columns={columns}
      maxRows={rows.length}
      sort={sort}
      onSort={onSort}
      onRowClick={onInspect}
      timezoneName={timezoneName}
      className="performance-investigation-table"
      fixedLayout
      sortMode="server"
      getRowKey={performanceRegistryRowKey}
      virtualization={REGISTRY_VIRTUALIZATION}
      scrollResetKey={scrollResetKey}
    />
  );
});

function performanceRegistryRowKey(row: MonitoringRecord) {
  return String(row.dataflow_id ?? row.dataflow_name ?? row.source_name ?? "unknown");
}

function DataflowRegistryNameCell({ row }: { row: MonitoringRecord }) {
  const name = String(row.dataflow_name || "Unknown");
  const id = textValue(row.dataflow_id);
  const title = [`Dataflow: ${name}`, `Full ID: ${id || "-"}`].join("\n");
  return (
    <Help className="performance-registry-dataflow-cell" title={title}>
      <strong>{name}</strong>
    </Help>
  );
}

function PerformanceRunsCell({ row }: { row: MonitoringRecord }) {
  const runCount = metricValue(row, "run_count");
  const durationMeasured = metricValue(row, "performance_duration_measured");
  const durationEligible = metricValue(row, "performance_timing_eligible");
  const lifecycle = [
    ["Succeeded", metricValue(row, "succeeded_count")],
    ["Failed", metricValue(row, "failed_count")],
    ["Skipped", metricValue(row, "skipped_count")],
    ["Running", metricValue(row, "running_count")],
    ["Pending", metricValue(row, "pending_count")],
    ["Other", metricValue(row, "other_count")],
  ] as const;
  const title = [
    `All filtered canonical runs: ${formatTooltipNumber(runCount)}`,
    ...lifecycle.map(([label, value]) => `${label}: ${formatTooltipNumber(value)}`),
    `Root duration measured / eligible: ${formatTooltipNumber(durationMeasured)} / ${formatTooltipNumber(durationEligible)} succeeded/failed runs`,
  ].join("\n");
  return (
    <Help className="monitor-stack-cell performance-registry-runs-cell" title={title}>
      <strong><CompactNumberValue value={runCount} /></strong>
      <small className="performance-registry-lifecycle-values">
        {lifecycle.slice(0, 3).map(([label, value]) => (
          <Help key={label} title={`${label}: ${formatTooltipNumber(value)}`} aria-label={`${label}: ${formatTooltipNumber(value)}`} style={{ color: lifecycleStatusPresentation(label.toLowerCase())?.textColor }}>
            <CompactNumberValue value={value} />
          </Help>
        ))}
      </small>
    </Help>
  );
}

function RuntimeCell({ row }: { row: MonitoringRecord }) {
  const { value, observed } = totalRunTimeValue(row);
  const measured = metricValue(row, "performance_duration_measured");
  const eligible = metricValue(row, "performance_timing_eligible");
  const partial = isPartial(measured, eligible);
  const content: HelpContent = {
    title: "Runtime",
    context: "Accumulated root duration across succeeded/failed runs; concurrent runs add together.",
    metrics: [
      { label: "Total", value: `${formatDurationMetric(value)}${value != null && (observed || partial) ? " · observed" : ""}` },
      { label: "Avg", value: formatDurationMetric(metricValue(row, "performance_avg_duration_seconds")) },
      { label: "P95", value: formatDurationMetric(metricValue(row, "performance_p95_duration_seconds")) },
      { label: "Measured / eligible", value: `${formatTooltipNumber(measured)} / ${formatTooltipNumber(eligible)} runs` },
    ],
  };
  return (
    <Help className="monitor-stack-cell performance-registry-metric-cell" content={content}>
      <strong>{formatDurationMetric(value)}{value != null && (observed || partial) ? <ObservedMarker /> : null}</strong>
      <small>{runtimeSummary(row)}</small>
    </Help>
  );
}

function WorkloadCell({ row }: { row: MonitoringRecord }) {
  const lines = (["rows", "bytes"] as const)
    .map((unit) => workloadLine(row, unit, false))
    .filter((line): line is WorkloadLine => line !== null);
  const unknownRuns = metricValue(row, "performance_unknown_operation_runs");
  if (!lines.length) {
    return <Help className="performance-registry-unavailable-cell" title="No succeeded/failed executable operation population in the current filters.">-</Help>;
  }
  const metrics: Array<{ label: string; value: string }> = [];
  for (const line of lines) {
    if (line.value !== null && line.value !== undefined) {
      metrics.push({
        label: unitLabel(line.unit),
        value: `${formatMetricExact(line.value, line.unit)} · ${formatTooltipNumber(line.measured)} / ${formatTooltipNumber(line.eligible)} runs`,
      });
    }
    if (line.rate !== null && line.rate !== undefined) {
      metrics.push({
        label: `${unitLabel(line.unit)}/s`,
        value: `${formatMetricExact(line.rate, line.unit)} · ${formatTooltipNumber(line.pairedRuns)} / ${formatTooltipNumber(line.eligible)} runs`,
      });
    }
  }
  const content: HelpContent = {
    title: "Workload",
    context: "Pipeline rows = read + written; maintenance bytes = added + removed. Rates use paired runs.",
    body: metrics.length ? undefined : "No workload values recorded.",
    metrics,
    note: unknownOperationNote(unknownRuns) || undefined,
  };
  return (
    <Help className="performance-registry-workload-cell" content={content}>
      <span className="performance-registry-workload-line">
        {lines.map((line, index) => <span key={line.unit}>{index ? <span className="performance-registry-value-separator"> – </span> : null}<WorkloadValue line={line} /></span>)}
      </span>
      <span className="performance-registry-workload-line is-rate">
        {lines.map((line, index) => <span key={line.unit}>{index ? <span className="performance-registry-value-separator"> – </span> : null}<WorkloadValue line={line} rate /></span>)}
      </span>
    </Help>
  );
}

type WorkloadUnit = "rows" | "bytes";
type WorkloadLine = {
  unit: WorkloadUnit;
  value: MetricValue;
  eligible: MetricValue;
  measured: MetricValue;
  observed: boolean;
  pairedRuns: MetricValue;
  rate: MetricValue;
};

function workloadLine(row: MonitoringRecord, unit: WorkloadUnit, rate: boolean): WorkloadLine | null {
  const prefix = `performance_processed_${unit}`;
  const eligible = metricValue(row, `${prefix}_eligible`);
  if (metricNumber(eligible) <= 0) return null;
  const measured = metricValue(row, `${prefix}_measured`);
  const total = metricValue(row, prefix);
  const observedTotal = metricValue(row, `performance_observed_processed_${unit}`);
  const value = metricNumber(measured) > 0 ? (total ?? observedTotal) : null;
  const pairedRuns = metricValue(row, `${prefix}_paired_runs`);
  const rateValue = metricValue(row, `${prefix}_per_second`);
  return {
    unit,
    value: rate ? rateValue : value,
    eligible,
    measured,
    observed: metricNumber(measured) < metricNumber(eligible) || total == null,
    pairedRuns,
    rate: rateValue,
  };
}

function ObservedMarker() {
  return <Help as="sup" className="performance-registry-observed" title="Observed partial coverage" aria-label="Observed partial coverage">*</Help>;
}

function WorkloadValue({ line, rate = false }: { line: WorkloadLine; rate?: boolean }) {
  const value = rate ? line.rate : line.value;
  const unavailable = value === null || value === undefined;
  const observed = rate ? metricNumber(line.pairedRuns) < metricNumber(line.eligible) : line.observed;
  const unit = line.unit === "rows" ? "r" : "b";
  const display = <CompactNumberValue value={value} fallback="-" suffix={` ${unit}${rate ? "/s" : ""}`} />;
  return (
    <span className="performance-registry-workload-value" data-unavailable={unavailable ? "true" : undefined}>
      {display}{observed && !unavailable ? <ObservedMarker /> : null}
    </span>
  );
}

function PerformancePhaseCell({ row }: { row: MonitoringRecord }) {
  const { root, timing } = rowPhaseContribution(row);
  const bottleneck = phaseContributionBottleneck(root, timing);
  const measured = metricValue(row, "performance_timing_measured");
  const eligible = metricValue(row, "performance_timing_eligible");
  const content: HelpContent = {
    title: `Phase contribution · ${bottleneck.label}`,
    context: "Aggregated from eligible run phase times.",
    metrics: [
      ...timing.phases.map((phase) => ({
        label: phaseLabel(phase.phase),
        value: phase.duration === null
          ? "Unavailable"
          : `${formatDurationMetric(phase.duration)}${phase.share === null ? "" : ` · ${phase.share.toFixed(1)}%`}`,
        valueColor: phaseColor(phase.phase),
      })),
      { label: "Measured / eligible", value: `${formatTooltipNumber(measured)} / ${formatTooltipNumber(eligible)} runs` },
    ],
  };
  return (
    <Help className="dataflow-phase-cell performance-registry-phase-cell" content={content}>
      <strong className="dataflow-phase-reason">
        {bottleneck.phases.length
          ? bottleneck.phases.map((phase, index) => <span key={phase}>{index ? " · " : ""}<span className={`phase-text-${phase}`}>{phaseLabel(phase)}</span></span>)
          : "Unknown"}
      </strong>
      <span className="dataflow-phase-mini-stack" aria-label="Source, transform, destination, and overhead phase contribution">
        {timing.phases.filter((phase) => phase.share !== null && phase.share > 0).map((phase) => <i key={phase.phase} className={`phase-${phase.phase}`} style={{ flex: `0 0 ${phase.share}%` }} />)}
      </span>
    </Help>
  );
}

function PerformanceSignalsCell({ row }: { row: MonitoringRecord }) {
  const count = signalCountValue(row);
  const reasons = signalReasonSummary(row);
  const rules = [
    ["Slow small pipeline", "performance_slow_small_workload_count"],
    ["Slow small maintenance", "performance_slow_small_maintenance_count"],
    ["High overhead", "performance_high_overhead_count"],
    ["Phase skew", "performance_phase_skew_count"],
  ] as const;
  const matchingRules = rules.flatMap(([label, key]) => {
    const value = metricValue(row, key);
    return metricNumber(value) > 0 ? [{ label, value: formatTooltipNumber(value) }] : [];
  });
  const content: HelpContent = {
    title: `${formatTooltipNumber(count)} runs with signals`,
    context: "Unique runs; rule matches overlap.",
    metrics: matchingRules,
    note: reasons,
  };
  return (
    <Help className="performance-registry-signals-cell" content={content}>
      <strong><CompactNumberValue value={count} /></strong>{metricNumber(count) === 1 ? " run: " : " runs: "}{reasons}
    </Help>
  );
}

function LatestRunCell({ row, timezoneName }: { row: MonitoringRecord; timezoneName?: string | null }) {
  const rawTime = textValue(row.latest_run_at);
  const displayTime = formatTimestamp(row.latest_run_at, timezoneName);
  const status = textValue(row.latest_run_status ?? row.status) || "unknown";
  const jobId = textValue(row.latest_job_id ?? row.job_id) || "-";
  const message = latestMessage(row);
  const title = [
    `Latest run: ${displayTime}`,
    rawTime && rawTime !== displayTime ? `Timestamp: ${rawTime}` : "",
    `Status: ${status}`,
    `Job: ${jobId}`,
    `Message: ${message ?? "-"}`,
  ].filter(Boolean).join("\n");
  return (
    <Help className="monitor-stack-cell performance-registry-latest-cell" title={title}>
      <strong>{displayTime}</strong>
      <small><StatusPill status={status} /></small>
    </Help>
  );
}

function endpointMeasureValue(row: MonitoringRecord, direction: "source" | "destination") {
  const endpoint = monitoringEndpointPresentation(row, direction, false);
  return [endpoint.locator, endpoint.connection];
}

function totalRunTimeValue(row: MonitoringRecord) {
  const measured = metricNumber(metricValue(row, "performance_duration_measured"));
  const strict = metricValue(row, "performance_total_run_time_seconds");
  const observedTotal = metricValue(row, "performance_observed_total_run_time_seconds");
  if (measured > 0 && strict !== null && strict !== undefined) return { value: strict, observed: false };
  if (measured > 0 && observedTotal !== null && observedTotal !== undefined) return { value: observedTotal, observed: true };
  return { value: null, observed: false };
}

function signalCountValue(row: MonitoringRecord): MetricValue {
  const value = metricValue(row, "performance_candidate_count");
  return value === null || value === undefined ? 0 : value;
}

function signalReasonSummary(row: MonitoringRecord) {
  const raw = row.performance_candidate_reasons;
  const reasons = Array.isArray(raw)
    ? raw.map((reason) => String(reason).trim()).filter(Boolean)
    : parseReasonText(raw);
  const unique = Array.from(new Set(reasons));
  if (unique.length) return unique.join(" · ");
  return metricNumber(signalCountValue(row)) > 0 ? "Matching rules recorded" : "No matching rules";
}

function parseReasonText(value: unknown) {
  if (typeof value !== "string" || !value.trim()) return [];
  const text = value.trim();
  try {
    const parsed: unknown = JSON.parse(text);
    if (Array.isArray(parsed)) return parsed.map((reason) => String(reason).trim()).filter(Boolean);
  } catch {
    // DuckDB list values may arrive as a readable comma-delimited string.
  }
  return text
    .replace(/^\[/u, "")
    .replace(/\]$/u, "")
    .split(/\s*,\s*/u)
    .map((reason) => reason.trim().replace(/^['"]|['"]$/gu, ""))
    .filter(Boolean);
}

function latestMessage(row: MonitoringRecord) {
  const values = [row.latest_message, row.latest_error_message, row.message, row.error_message];
  const selected = values.find((value) => typeof value === "string" && value.trim());
  return selected === undefined ? null : String(selected);
}

function lifecycleCompactText(row: MonitoringRecord) {
  return ["succeeded", "failed", "skipped"].map((status) => formatCompactNumber(metricValue(row, `${status}_count`), "infix")).join("  ");
}

function workloadMeasureValue(row: MonitoringRecord, rate: boolean) {
  return (["rows", "bytes"] as const)
    .map((unit) => workloadLine(row, unit, rate))
    .filter((line): line is WorkloadLine => line !== null)
    .map((line) => `${formatCompactNumber(line.value, "infix")} ${line.unit === "rows" ? "r" : "b"}${rate ? "/s" : ""}${line.value != null && line.observed ? "*" : ""}`)
    .join(" – ");
}

function runtimeSummary(row: MonitoringRecord) {
  return `${formatDurationMetric(metricValue(row, "performance_avg_duration_seconds"))} – ${formatDurationMetric(metricValue(row, "performance_p95_duration_seconds"))}`;
}

function formatTimestamp(value: unknown, timezoneName?: string | null) {
  if (value === null || value === undefined || value === "") return "-";
  return formatTimestampForDisplay(String(value), timezoneName, "-");
}

function textValue(value: unknown) {
  return value === null || value === undefined || value === "" ? "" : String(value);
}

function isPartial(measured: MetricValue, eligible: MetricValue) {
  return metricNumber(eligible) > 0 && metricNumber(measured) < metricNumber(eligible);
}

function unknownOperationNote(value: MetricValue) {
  const count = metricNumber(value);
  return count > 0
    ? `${formatTooltipNumber(value)} runs have unknown operation type; workload follows API classification.`
    : "";
}

function unitLabel(unit: WorkloadUnit) {
  return unit === "rows" ? "Rows" : "Bytes";
}

function formatMetricExact(value: MetricValue, unit: WorkloadUnit) {
  return unit === "bytes" ? formatTooltipBytes(value) : formatTooltipNumber(value);
}

function phaseLabel(value: string) {
  return value ? value[0].toUpperCase() + value.slice(1) : "Unknown";
}

function phaseColor(value: string) {
  return monitoringPhaseColors[value as keyof typeof monitoringPhaseColors];
}
