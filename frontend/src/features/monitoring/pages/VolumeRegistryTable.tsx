import { memo, useMemo } from "react";
import type { MonitoringRecord } from "../../../shared/api/domainTypes";
import type { TableColumn, TableSort } from "../../../shared/components/DataTable";
import { StatusPill } from "../../../shared/components/StatusPill";
import { Help } from "../../../shared/components/Help";
import { lifecycleStatusPresentation } from "../../../shared/statusPresentation";
import { formatTimestampForDisplay } from "../../../shared/time";
import type { HelpContent, HelpMetric } from "../../../shared/components/helpContent";
import {
  CompactBytesValue, CompactNumberValue, DataTable, DataflowNameCell, EndpointCell,
  formatBytes, formatCompact, formatTooltipBytes, formatTooltipNumber, metricValue,
  monitoringDimensionLabel, monitoringEndpointPresentation,
  reportChartPalette,
  type MetricValue,
} from "../components/monitoringPresentation";
import { positiveVolumeMetric, registryVolumeObservation, registryVolumeSignals } from "../registryVolumeMetrics";

const REGISTRY_VIRTUALIZATION = { overscan: 5 } as const;
const SCALAR_COLUMN = { sortable: true, autoFit: true, minWidth: 48, maxWidth: Number.POSITIVE_INFINITY, className: "volume-registry-scalar-column" } as const;

export const VolumeRegistryTable = memo(function VolumeRegistryTable({ rows, sort, onSort, onInspect, timezoneName, scrollResetKey }: {
  rows: MonitoringRecord[]; sort?: TableSort; onSort?: (sort: TableSort) => void;
  onInspect?: (row: MonitoringRecord) => void; timezoneName?: string | null; scrollResetKey?: string;
}) {
  const scalar = SCALAR_COLUMN;
  const columns = useMemo<TableColumn<MonitoringRecord>[]>(() => [
    { key: "dataflow_name", label: "Dataflow", sortable: true, autoFit: true, minWidth: 152, maxWidth: 200, className: "volume-registry-name-column", render: (row) => <DataflowNameCell row={row} />, measureValue: (row) => String(row.dataflow_name ?? row.dataflow_id ?? "Unknown") },
    { key: "stage", label: "Stage", sortable: true, minWidth: 90, maxWidth: 150, fillPriority: "normal", render: (row) => monitoringDimensionLabel(row.stage), measureValue: (row) => monitoringDimensionLabel(row.stage) },
    ...(["source", "destination"] as const).map((direction): TableColumn<MonitoringRecord> => ({ key: `${direction}_name`, label: direction === "source" ? "Source" : "Destination", sortable: true, width: 210, minWidth: 160, headerTitle: "Latest filtered endpoint. Sorting uses connection name; table/path is display context.", render: (row) => <EndpointCell row={row} direction={direction} />, measureValue: (row) => { const endpoint = monitoringEndpointPresentation(row, direction, false); return [endpoint.locator, endpoint.connection]; } })),
    { ...scalar, key: "run_count", label: "Runs", render: (row) => <VolumeRunsCell row={row} />, measureValue: (row) => [formatCompact(metricValue(row, "run_count")), ["succeeded", "failed", "skipped"].map((status) => formatCompact(metricValue(row, `${status}_count`))).join("  ")] },
    { ...scalar, key: "volume_rows_written", label: "Rows", headerTitle: "Line 1: rows read; line 2: rows written. Each line: total - average per run recording that metric, including zero. All values use current filters. * indicates observed partial coverage. Sorting uses rows written total.", render: (row) => <VolumeRowsCell row={row} />, measureValue: (row) => (["read", "written"] as const).map((direction) => `${metricText(row, `volume_rows_${direction}`, " r")} - ${metricText(row, `volume_rows_${direction}`, " r/run", true)}`) },
    { ...scalar, key: "volume_files_changed", label: "Files", headerTitle: "Added / removed totals; second line: Avg per run recording each count. Sorting uses added + removed independent totals. * indicates observed partial coverage.", render: (row) => <span className="volume-registry-stack"><VolumePairLine row={row} fields={["files_added", "files_removed"]} /><small>Avg <VolumePairLine row={row} fields={["files_added", "files_removed"]} average /></small></span>, measureValue: (row) => [pairMeasure(row, "files"), `Avg ${pairMeasure(row, "files", true)}`] },
    { ...scalar, key: "volume_bytes_added", label: "Bytes", headerTitle: "Added / removed totals; second line: signed Net from runs recording both. Sorting uses bytes added. Independent added/removed totals need not subtract to paired Net. * indicates observed partial coverage.", render: (row) => <VolumeBytesCell row={row} />, measureValue: (row) => [pairMeasure(row, "bytes"), `Net ${metricText(row, "volume_net_bytes", "", false, true)}`] },
    { key: "latest_run_at", label: "Latest run", sortable: true, autoFit: true, minWidth: 148, maxWidth: 194, render: (row) => <Help className="volume-registry-stack" content={{ title: "Latest run ID", body: String(row.latest_run_id ?? "-") }}><strong>{timestamp(row, timezoneName)}</strong><small><StatusPill status={String(row.latest_run_status ?? row.status ?? "unknown")} /></small></Help>, measureValue: (row) => [timestamp(row, timezoneName), String(row.latest_run_status ?? row.status ?? "unknown")] },
    { key: "volume_candidate_priority", label: "Volume signals", sortable: true, minWidth: 180, fillPriority: "last", className: "volume-registry-signals-column", headerTitle: "Dataflow aggregate P95 matches and separately assessed run-level matches. Sorting uses dataflow aggregate signal priority.", render: (row) => <VolumeSignalsCell row={row} /> },
  ], [timezoneName]);
  return <DataTable rows={rows} compactNumbers compactNumberStyle="infix" columns={columns} maxRows={rows.length} sort={sort} onSort={onSort} onRowClick={onInspect} timezoneName={timezoneName} className="monitoring-volume-table" fixedLayout sortMode="server" getRowKey={(row) => String(row.dataflow_id ?? "unknown")} virtualization={REGISTRY_VIRTUALIZATION} scrollResetKey={scrollResetKey} />;
});

function VolumeRunsCell({ row }: { row: MonitoringRecord }) {
  const lifecycle = ["succeeded", "failed", "skipped", "running", "pending", "other"];
  const metrics: HelpMetric[] = [
    { label: "Total", value: formatTooltipNumber(metricValue(row, "run_count")) },
    ...lifecycle.map((status) => ({
      label: status.charAt(0).toUpperCase() + status.slice(1),
      value: formatTooltipNumber(metricValue(row, `${status}_count`)),
      valueColor: lifecycleStatusPresentation(status)?.textColor ?? reportChartPalette.muted,
    })),
  ];
  return <Help className="volume-registry-stack" content={{ title: "Filtered runs", context: "Current filters", metrics }}>
    <strong><CompactNumberValue value={metricValue(row, "run_count")} /></strong>
    <small className="volume-registry-lifecycle-values">{lifecycle.slice(0, 3).map((status) => <Help key={status} title={`${status}: ${formatTooltipNumber(metricValue(row, `${status}_count`))}`} aria-label={`${status}: ${formatTooltipNumber(metricValue(row, `${status}_count`))}`} style={{ color: lifecycleStatusPresentation(status)?.textColor }}><CompactNumberValue value={metricValue(row, `${status}_count`)} /></Help>)}</small>
  </Help>;
}

function VolumeRowsCell({ row }: { row: MonitoringRecord }) {
  return <span className="volume-registry-stack">{(["read", "written"] as const).map((direction) => {
    const key = `volume_rows_${direction}`;
    const total = registryVolumeObservation(row, key);
    const average = registryVolumeObservation(row, key, true);
    const valueColor = direction === "read" ? reportChartPalette.read : reportChartPalette.written;
    const coverage = volumeCoverage(total.coverage ?? average.coverage);
    const content: HelpContent = {
      title: `Rows ${direction}`,
      context: "Current filters",
      metrics: [
        { label: "Total", value: `${formatTooltipNumber(total.value)} r`, valueColor },
        { label: "Average per run", value: `${formatTooltipNumber(average.value)} r/run`, valueColor },
        { label: "Coverage", value: coverage },
      ],
      note: "Average includes recorded zeroes; runs with missing values are excluded. * marks partial coverage.",
    };
    return <Help as="strong" key={direction} className={`volume-metric-${direction}`} content={content}>
      <VolumeMetric row={row} field={key} suffix=" r" /> <span className="volume-registry-separator">-</span> <small><VolumeMetric row={row} field={key} average suffix=" r/run" /></small>
    </Help>;
  })}</span>;
}

const fieldLabels: Record<string, string> = { files_added: "Files added", files_removed: "Files removed", bytes_added: "Bytes added", bytes_removed: "Bytes removed" };

function VolumePairLine({ row, fields, average = false }: { row: MonitoringRecord; fields: string[]; average?: boolean }) {
  return <span className="volume-registry-pair">{fields.map((field, index) => <Help key={field} content={volumeMetricContent(row, `volume_${field}`, fieldLabels[field], field.startsWith("bytes"), average)}>{index ? <span className="volume-registry-separator"> / </span> : null}<span className={`volume-registry-action is-${field.split("_")[1]}`}><VolumeMetric row={row} field={`volume_${field}`} average={average} bytes={field.startsWith("bytes")} /></span></Help>)}</span>;
}

function VolumeBytesCell({ row }: { row: MonitoringRecord }) {
  const added = registryVolumeObservation(row, "volume_bytes_added");
  const removed = registryVolumeObservation(row, "volume_bytes_removed");
  const net = registryVolumeObservation(row, "volume_net_bytes");
  const content: HelpContent = {
    title: "Bytes",
    context: "Current filters",
    metrics: [
      byteMetric("Added", added.value, added.coverage, reportChartPalette.written),
      byteMetric("Removed", removed.value, removed.coverage, reportChartPalette.failed),
      byteMetric("Paired net", net.value, net.coverage, signedValueColor(net.value), true),
    ],
    note: "Added and removed are independent totals. Paired net sums each run’s added minus removed bytes only when both values are recorded; coverage can differ.",
  };
  return <Help className="volume-registry-stack" content={content}>
    <strong><VolumePairLine row={row} fields={["bytes_added", "bytes_removed"]} /></strong>
    <small>Net <VolumeMetric row={row} field="volume_net_bytes" bytes signed /></small>
  </Help>;
}

function VolumeMetric({ row, field, average = false, bytes = false, signed = false, suffix = "" }: { row: MonitoringRecord; field: string; average?: boolean; bytes?: boolean; signed?: boolean; suffix?: string }) {
  const observation = registryVolumeObservation(row, field, average);
  return <Help title={observation.coverage ?? undefined}>{signed && positiveVolumeMetric(observation.value) ? "+" : ""}{bytes ? <CompactBytesValue value={observation.value} /> : <CompactNumberValue value={observation.value} suffix={observation.value == null ? "" : suffix} />}{observation.partial && observation.value != null ? <span className="volume-registry-observed">*</span> : null}</Help>;
}

function VolumeSignalsCell({ row }: { row: MonitoringRecord }) {
  const labels = registryVolumeSignals(row).map((signal) => String(signal.label ?? "Signal"));
  const runCount = metricValue(row, "volume_candidate_run_count");
  return <Help className="volume-registry-signals-cell" content={volumeSignalContent(row)}>{labels.length ? <>Dataflow: {labels.join(" · ")}</> : "No dataflow matches"}{positiveVolumeMetric(runCount) ? <> · Runs: <CompactNumberValue value={runCount} /> with volume signals</> : null}</Help>;
}

function volumeMetricContent(row: MonitoringRecord, field: string, label: string, bytes: boolean, average: boolean): HelpContent {
  const observation = registryVolumeObservation(row, field, average);
  const value = bytes ? formatTooltipBytes(observation.value) : formatTooltipNumber(observation.value);
  return {
    title: label,
    metrics: [
      { label: average ? "Average per run" : "Total", value, valueColor: volumeFieldColor(field) },
      { label: "Coverage", value: volumeCoverage(observation.coverage) },
    ],
    note: average
      ? "Includes recorded zeroes; runs with missing values are excluded. * marks partial coverage."
      : "Total in current filters. * marks partial coverage.",
  };
}

function volumeFieldColor(field: string) {
  if (field.endsWith("_read")) return reportChartPalette.read;
  if (field.endsWith("_written")) return reportChartPalette.written;
  if (field.endsWith("_added")) return reportChartPalette.written;
  if (field.endsWith("_removed")) return reportChartPalette.failed;
  return reportChartPalette.muted;
}

function byteMetric(label: string, value: MetricValue, coverage: string | null | undefined, valueColor: string, signed = false): HelpMetric {
  const formatted = formatTooltipBytes(value);
  const signedValue = signed && positiveVolumeMetric(value) ? `+${formatted}` : formatted;
  return { label, value: `${signedValue} · ${volumeCoverage(coverage)}`, valueColor };
}

function signedValueColor(value: MetricValue) {
  if (positiveVolumeMetric(value)) return reportChartPalette.teal;
  if (String(value ?? "").trim().startsWith("-")) return reportChartPalette.failed;
  return reportChartPalette.muted;
}

function volumeCoverage(coverage: string | null | undefined) {
  return coverage?.replace(/ runs measured$/u, " runs") ?? "Unavailable";
}

function volumeSignalContent(row: MonitoringRecord): HelpContent {
  const signals = registryVolumeSignals(row);
  const reasons = Array.isArray(row.volume_candidate_run_reasons)
    ? row.volume_candidate_run_reasons.map(String).join(" · ")
    : "";
  const note = [
    `Run-level matches: ${formatTooltipNumber(metricValue(row, "volume_candidate_run_count"))}${reasons ? ` · ${reasons}` : ""}. Evaluated separately using filtered-run P95 thresholds.`,
    "Byte signal is the absolute value of independent added total minus independent removed total. Its coverage may differ from paired net.",
    signals.length ? "" : "No detected signal does not confirm complete metric coverage.",
  ].filter(Boolean).join("\n");
  return {
    title: "Volume signals",
    context: "Dataflow totals vs P95 across filtered dataflows",
    metrics: signals.map((signal) => {
      const kind = String(signal.kind ?? "");
      const value = metricValue(signal, "value");
      const valueColor = kind === "read"
        ? reportChartPalette.read
        : kind === "rows_written" || kind === "lakehouse_rows"
          ? reportChartPalette.written
          : kind === "files"
            ? reportChartPalette.blue
            : kind === "bytes"
              ? signedValueColor(value)
              : reportChartPalette.muted;
      return {
        label: String(signal.label ?? "Signal"),
        value: `${formatTooltipNumber(value)} · P95 ${formatTooltipNumber(metricValue(signal, "threshold"))} · ${formatTooltipNumber(metricValue(signal, "ratio"))}×`,
        valueColor,
      };
    }),
    note,
  };
}

function metricText(row: MonitoringRecord, field: string, suffix = "", average = false, signed = false) {
  const observation = registryVolumeObservation(row, field, average);
  const value = field.includes("bytes") ? formatBytes(observation.value) : formatCompact(observation.value);
  return `${signed && positiveVolumeMetric(observation.value) ? "+" : ""}${value}${observation.value == null ? "" : suffix}${observation.partial && observation.value != null ? "*" : ""}`;
}

function pairMeasure(row: MonitoringRecord, kind: "files" | "bytes", average = false) {
  return ["added", "removed"].map((action) => metricText(row, `volume_${kind}_${action}`, "", average)).join(" / ");
}

function timestamp(row: MonitoringRecord, timezoneName?: string | null) {
  return row.latest_run_at == null ? "-" : formatTimestampForDisplay(String(row.latest_run_at), timezoneName, "-");
}
