import { memo, useMemo } from "react";
import { DestinationSignalsCell, signalMeasureValue } from "../destinationSignalsPresentation";
import type { MonitoringRecord } from "../../../shared/api/domainTypes";
import type { TableColumn, TableSort } from "../../../shared/components/DataTable";
import { StatusPill } from "../../../shared/components/StatusPill";
import { Help } from "../../../shared/components/Help";
import { lifecycleStatusPresentation } from "../../../shared/statusPresentation";
import type { HelpContent, HelpMetric } from "../../../shared/components/helpContent";
import { formatTimestampForDisplay } from "../../../shared/time";
import { LineageFormatIcon } from "../../../shared/components/FormatIcon";
import {
  CompactNumberValue,
  DataTable,
  formatCompact,
  formatTooltipBytes,
  formatTooltipNumber,
  formatBytes,
  metricNumber,
  metricValue,
  reportChartPalette,
} from "../components/monitoringPresentation";
import { maintenanceFormatIconKind } from "../maintenancePresentation";
import { monitoringConnectionStageClass } from "../monitoringStagePresentation";
import { compareMetricValues } from "../../../shared/lib/numberFormat";
import {
  destinationFormatLabel,
  destinationFormatUnknownRuns,
  destinationFormatVariants,
  destinationLatestFormat,
  DESTINATION_WORKLOAD_SCOPE,
  destinationLayoutFieldTooltip,
  destinationLayoutInvalidReason,
  maintenanceEvidenceLabel,
  maintenanceEvidenceState,
  destinationHelpContent,
  presentDestination,
  presentDestinationLayout,
  presentDestinationRows,
  type DestinationPresentation,
} from "../destinationPresentation";

const VIRTUALIZATION = { overscan: 5 } as const;
const LIFECYCLE_STATUSES = ["succeeded", "failed", "skipped", "running", "pending", "unknown"] as const;
const VISIBLE_LIFECYCLE_STATUSES = ["succeeded", "failed", "skipped"] as const;

export const MaintenanceRegistryTable = memo(function MaintenanceRegistryTable({ rows, sort, onSort, timezoneName, onInspect, scrollResetKey }: {
  rows: MonitoringRecord[];
  sort: TableSort;
  onSort: (sort: TableSort) => void;
  timezoneName?: string | null;
  onInspect?: (row: MonitoringRecord) => void;
  scrollResetKey?: string;
}) {
  const destinations = useMemo(() => new Map(presentDestinationRows(rows).map(value => [value.id, value])), [rows]);
  const columns = useMemo<TableColumn<MonitoringRecord>[]>(() => [
    {
      key: "target_display",
      label: "Destination",
      sortable: true,
      width: 250,
      minWidth: 180,
      maxWidth: 340,
      render: (row) => <DestinationCell row={row} presentation={destinations.get(String(row.target))} />,
      measureValue: (row) => { const value = destinations.get(String(row.target)) ?? presentDestination(row); return [value.name, value.connection]; },
    },
    {
      key: "destination_load_type",
      label: "Write layout",
      headerTitle: "Latest recorded write mode and configured partition keys within the filters; this is configuration evidence, not verified storage layout. Hover for provenance and historical modes.",
      autoFit: true,
      minWidth: 48,
      maxWidth: 230,
      render: row => <WriteLayoutCell row={row} timezoneName={timezoneName} />,
      measureValue: row => writeLayoutLines(row),
    },
    {
      key: "run_count",
      label: "Runs",
      headerTitle: "Total filtered runs. Second line: Succeeded / Failed / Skipped root run counts; all statuses are in the tooltip.",
      className: "destination-registry-numeric-column",
      sortable: true,
      autoFit: true,
      minWidth: 48,
      maxWidth: Number.POSITIVE_INFINITY,
      render: (row) => <RunCountCell row={row} />,
      measureValue: (row) => runMeasureValue(row),
    },
    {
      key: "latest_time",
      sortKey: "latest_time",
      label: "Latest operation",
      headerTitle: "Latest run within the current filters: timestamp, root operation and root status. Destination phase details are in the tooltip.",
      sortable: true,
      autoFit: true,
      minWidth: 48,
      maxWidth: Number.POSITIVE_INFINITY,
      render: (row) => <LatestOperationCell row={row} timezoneName={timezoneName} />,
      measureValue: (row) => latestMeasureValue(row, timezoneName),
    },
    {
      key: "workload_rows_written",
      label: "Rows",
      className: "destination-registry-numeric-column",
      sortable: true,
      autoFit: true,
      minWidth: 48,
      maxWidth: Number.POSITIVE_INFINITY,
      headerTitle: `Sort by total Written. Read / Written are summed within the current filters. * means partial measurements; coverage is in the tooltip. ${DESTINATION_WORKLOAD_SCOPE}.`,
      render: (row) => <DestinationWorkloadRowsCell row={row} />,
      measureValue: (row) => workloadMeasureValue(row),
    },
    {
      key: "files_added",
      label: "Files",
      className: "destination-registry-numeric-column",
      headerTitle: "Sort by total Added. + added / − removed: qualified physical changes within the filters, not the current object file count. * means partial measurements.",
      sortable: true,
      autoFit: true,
      minWidth: 48,
      maxWidth: Number.POSITIVE_INFINITY,
      render: (row) => <DestinationFilesCell row={row} />,
      measureValue: (row) => effectMeasureValue(row, "files"),
    },
    {
      key: "bytes_added",
      label: "Bytes",
      className: "destination-registry-numeric-column",
      headerTitle: "Sort by total Added. + added / − removed: qualified physical changes within the filters, not current object size. * means partial measurements.",
      sortable: true,
      autoFit: true,
      minWidth: 48,
      maxWidth: Number.POSITIVE_INFINITY,
      render: (row) => <DestinationBytesCell row={row} />,
      measureValue: (row) => effectMeasureValue(row, "bytes"),
    },
    {
      key: "maintenance_run_count",
      label: "Maintenance",
      headerTitle: "Sort by total attempts. Latest maintenance status and timestamp within the current filters; Succeeded requires confirmed non-dry-run destination success, as in Maintenance status.",
      sortable: true,
      autoFit: true,
      minWidth: 48,
      maxWidth: Number.POSITIVE_INFINITY,
      render: (row) => <MaintenanceCell row={row} timezoneName={timezoneName} />,
      measureValue: (row) => maintenanceMeasureValue(row, timezoneName),
    },
    {
      key: "attention_priority",
      label: "Signals",
      headerTitle: "Backend-ranked signals: failures, conflicts/invalid evidence, missing evidence, then activity information. Within a severity, sort by affected count. +N is the number of additional signal types; hover for exact evidence.",
      sortable: true,
      minWidth: 170,
      fillPriority: "last",
      className: "maintenance-registry-signals-column",
      render: (row) => <DestinationSignalsCell row={row} />,
      measureValue: (row) => signalMeasureValue(row),
    },
  ], [timezoneName, destinations]);

  return (
    <DataTable
      rows={rows}
      columns={columns}
      compactNumbers
      compactNumberStyle="infix"
      maxRows={rows.length}
      sort={sort}
      onSort={onSort}
      onRowClick={onInspect}
      timezoneName={timezoneName}
      className="monitoring-maintenance-table monitoring-destination-registry-table"
      fixedLayout
      sortMode="server"
      getRowKey={(row) => String(row.target ?? row.target_display ?? "unknown")}
      virtualization={VIRTUALIZATION}
      scrollResetKey={scrollResetKey}
    />
  );
});

function firstMetric(row: Record<string, unknown>, ...keys: string[]) {
  for (const key of keys) {
    const value = metricValue(row, key);
    if (value !== undefined && value !== null && value !== "") return value;
  }
  return null;
}

function firstText(row: Record<string, unknown>, ...keys: string[]) {
  for (const key of keys) {
    const value = row[key];
    if (value !== undefined && value !== null && String(value).trim() !== "") return String(value);
  }
  return "";
}

function compactValue(value: ReturnType<typeof metricValue>, bytes = false) {
  const display = bytes ? formatBytes(value) : formatCompact(value);
  return display === "-" ? "—" : display;
}

function runMeasureValue(row: Record<string, unknown>) {
  return [compactValue(firstMetric(row, "run_count")),
    VISIBLE_LIFECYCLE_STATUSES.map(status => compactValue(metricValue(row, status))).join("   ")];
}

function DestinationCell({ row, presentation }: { row: Record<string, unknown>; presentation?: DestinationPresentation }) {
  const parts = presentation ?? presentDestination(row);
  const stageClass = monitoringConnectionStageClass(parts.connection);
  const format = destinationLatestFormat(row) || parts.format;
  return (
    <Help className={["monitor-endpoint-cell", stageClass].filter(Boolean).join(" ")} content={destinationRegistryHelpContent(row, parts)}>
      <Help className="monitor-endpoint-icon" content={destinationFormatHelpContent(row)}><LineageFormatIcon kind={maintenanceFormatIconKind(format)} label={format} size={18} /></Help>
      <span className="monitor-endpoint-text"><strong>{parts.name}</strong><small>{parts.connection}</small></span>
    </Help>
  );
}

function RunCountCell({ row }: { row: Record<string, unknown> }) {
  const total = firstMetric(row, "run_count");
  return (
    <Help className="monitor-stack-cell" content={runCountHelpContent(row)}>
      <strong><CompactNumberValue value={total} fallback="—" /></strong>
      <small className="volume-registry-lifecycle-values">
        {VISIBLE_LIFECYCLE_STATUSES.map((status) => <span key={status} aria-label={`${status}: ${formatTooltipNumber(metricValue(row, status))}`} style={{ color: lifecycleStatusPresentation(status)?.textColor }}>{compactValue(metricValue(row, status))}</span>)}
      </small>
    </Help>
  );
}

function runCountHelpContent(row: Record<string, unknown>): HelpContent {
  const metrics: HelpMetric[] = [
    { label: "Total runs", value: formatTooltipNumber(firstMetric(row, "run_count")) },
    ...LIFECYCLE_STATUSES.map(status => ({
      label: statusLabel(status),
      value: formatTooltipNumber(metricValue(row, status)),
      valueColor: statusValueColor(status),
    })),
  ];
  return { title: "Runs", context: "Root run counts within the current filters.", metrics };
}

function latestTime(row: Record<string, unknown>) {
  return firstText(row, "latest_time");
}

function latestStatus(row: Record<string, unknown>) {
  const value = firstText(row, "latest_status").trim().toLowerCase();
  return !value || value === "__monitoring_unknown__" ? "unknown" : value;
}

function statusLabel(status: string) {
  const value = status.trim().toLowerCase();
  if (!value || value === "__monitoring_unknown__") return "Unknown";
  if (value === "dry_run") return "Dry run";
  return value.charAt(0).toUpperCase() + value.slice(1);
}

function statusValueColor(status: string) {
  return lifecycleStatusPresentation(status)?.textColor ?? reportChartPalette.muted;
}

function RegistryStatus({ status }: { status: string }) {
  const normalized = status.toLowerCase();
  if (normalized === "dry_run") return <span className="status-pill status-unknown">Dry run</span>;
  return <StatusPill status={statusLabel(normalized)} />;
}

function operationLabel(value: string) {
  const normalized = value.trim().toLowerCase();
  if (!normalized || normalized === "unknown" || normalized === "__monitoring_unknown__") return "Unknown";
  return normalized === "etl" ? "ETL" : normalized.replace(/_/gu, " ").replace(/^./u, letter => letter.toUpperCase());
}

function latestMeasureValue(row: Record<string, unknown>, timezoneName?: string | null) {
  const time = latestTime(row);
  return [time ? formatTimestampForDisplay(time, timezoneName, "—") : "—",
    `${operationLabel(firstText(row, "root_operation_type", "operation_type"))} · ${statusLabel(latestStatus(row))}`];
}

function LatestOperationCell({ row, timezoneName }: { row: Record<string, unknown>; timezoneName?: string | null }) {
  const time = latestTime(row);
  const status = latestStatus(row);
  const rootOperation = firstText(row, "root_operation_type", "operation_type");
  return (
    <Help className="monitor-stack-cell" content={latestOperationHelpContent(row, timezoneName)}>
      <strong>{time ? formatTimestampForDisplay(time, timezoneName, "—") : "—"}</strong>
      <small className="destination-registry-operation-status"><span>{operationLabel(rootOperation)}</span><RegistryStatus status={status} /></small>
    </Help>
  );
}

function latestOperationHelpContent(row: Record<string, unknown>, timezoneName?: string | null): HelpContent {
  const time = latestTime(row);
  const rootStatus = latestStatus(row);
  const phaseStatus = firstText(row, "destination_status").trim().toLowerCase();
  const normalizedPhaseStatus = !phaseStatus ? "unavailable" : phaseStatus === "__monitoring_unknown__" ? "unknown" : phaseStatus;
  return {
    title: "Latest operation",
    context: "Root run and destination phase within the current filters.",
    metrics: [
      { label: "Timestamp", value: time ? formatTimestampForDisplay(time, timezoneName, "—") : "—" },
      { label: "Root operation", value: operationLabel(firstText(row, "root_operation_type", "operation_type")) },
      { label: "Root status", value: statusLabel(rootStatus), valueColor: statusValueColor(rootStatus) },
      { label: "Destination phase operation", value: operationLabel(firstText(row, "destination_operation_type")) },
      { label: "Destination phase status", value: normalizedPhaseStatus === "unavailable" ? "Unavailable" : statusLabel(normalizedPhaseStatus), valueColor: statusValueColor(normalizedPhaseStatus) },
    ],
  };
}

function destinationFormatHelpContent(row: Record<string, unknown>): HelpContent {
  const layout = presentDestinationLayout(row);
  const latest = destinationLatestFormat(row);
  const variants = destinationFormatVariants(row);
  const unknownRuns = destinationFormatUnknownRuns(row);
  const origin = layout.origins.format;
  const metrics: HelpMetric[] = [
    { label: "Latest format", value: latest ? destinationFormatLabel(latest) : "Unknown" },
    ...(origin && origin !== "unknown" ? [{ label: "Origin", value: origin === "metadata" ? "Metadata fallback" : "Captured" }] : []),
    ...(variants.length > 1 ? [{ label: "Known formats", value: variants.map(destinationFormatLabel).join(", ") }] : []),
    ...(unknownRuns !== null && unknownRuns > 0 ? [{ label: "Unknown format runs", value: formatTooltipNumber(unknownRuns) }] : []),
  ];
  const note = [
    layoutProvenanceNote(layout, "format", "Format provenance"),
    destinationLayoutInvalidReason(layout, "format"),
  ].filter(Boolean).join("\n");
  return { title: "Destination format", metrics, ...(note ? { note } : {}) };
}

function destinationRegistryHelpContent(row: Record<string, unknown>, presentation: DestinationPresentation): HelpContent {
  const base = destinationHelpContent(presentation);
  const layout = presentDestinationLayout(row);
  const latest = destinationLatestFormat(row);
  const variants = destinationFormatVariants(row);
  const unknownRuns = destinationFormatUnknownRuns(row);
  const formatDiffers = latest && destinationFormatLabel(latest).toLowerCase() !== destinationFormatLabel(presentation.format).toLowerCase();
  const identityReason = firstText(row, "identity_reason");
  const note = [
    identityReason ? `Identity evidence: ${identityReason}` : "",
    layoutProvenanceNote(layout, "format", "Format provenance"),
    destinationLayoutInvalidReason(layout, "format"),
  ].filter(Boolean).join("\n");
  return {
    ...base,
    metrics: [
      ...(base.metrics ?? []),
      ...(formatDiffers ? [{ label: "Latest format", value: destinationFormatLabel(latest) }] : []),
      ...(layout.origins.format && layout.origins.format !== "unknown" ? [{ label: "Format origin", value: layout.origins.format === "metadata" ? "Metadata fallback" : "Captured" }] : []),
      ...(variants.length > 1 ? [{ label: "Known formats", value: variants.map(destinationFormatLabel).join(", ") }] : []),
      ...(unknownRuns !== null && unknownRuns > 0 ? [{ label: "Unknown format runs", value: formatTooltipNumber(unknownRuns) }] : []),
    ],
    ...(note ? { note } : {}),
  };
}

function layoutProvenanceNote(layout: ReturnType<typeof presentDestinationLayout>, field: "format" | "load_type" | "partition_columns" | "partition_expressions", label: string) {
  const tooltip = destinationLayoutFieldTooltip(layout, field);
  if (!tooltip) return "";
  const details = tooltip.split("\n").filter(line => line && !line.startsWith("Origin:"));
  return details.length ? `${label}: ${details.join("; ")}` : "";
}

function effectMeasureValue(row: Record<string, unknown>, kind: "files" | "bytes") {
  return (["added", "removed"] as const).map(direction => {
    const state = metricState(row, `${kind}_${direction}`);
    return `${direction === "added" ? "+" : "−"} ${compactValue(state.value, kind === "bytes")}${state.partial ? "*" : ""}`;
  });
}

function metricState(row: Record<string, unknown>, key: string) {
  const exact = metricValue(row, key);
  const observed = metricValue(row, `${key}_observed`);
  const measured = metricValue(row, `${key}_measured_runs`);
  const denominator = metricValue(row, "run_count");
  const value = exact ?? observed;
  const partial = value != null && (exact == null || measured != null && denominator != null && compareMetricValues(measured, denominator) < 0);
  return { value, measured, denominator, partial };
}

function workloadState(row: Record<string, unknown>, direction: "read" | "written") {
  const key = `workload_rows_${direction}`;
  const value = metricValue(row, key) ?? metricValue(row, `${key}_observed`);
  const measured = metricValue(row, `${key}_measured_runs`);
  const eligible = metricValue(row, `${key}_eligible_runs`);
  const exact = metricValue(row, key);
  const partial = value != null && (exact == null || measured != null && eligible != null && compareMetricValues(measured, eligible) < 0);
  return { value, measured, eligible, partial };
}

function workloadRowsHelpContent(row: Record<string, unknown>): HelpContent {
  const read = workloadState(row, "read");
  const written = workloadState(row, "written");
  return {
    title: "Rows",
    body: DESTINATION_WORKLOAD_SCOPE,
    context: "Totals within the current filters; * marks partial measurements.",
    metrics: [
      { label: "Read", value: `${formatTooltipNumber(read.value)}${read.partial ? "*" : ""}`, valueColor: reportChartPalette.read },
      { label: "Read coverage", value: `${formatTooltipNumber(read.measured)} / ${formatTooltipNumber(read.eligible)} eligible reports` },
      { label: "Written", value: `${formatTooltipNumber(written.value)}${written.partial ? "*" : ""}`, valueColor: reportChartPalette.written },
      { label: "Written coverage", value: `${formatTooltipNumber(written.measured)} / ${formatTooltipNumber(written.eligible)} eligible reports` },
    ],
  };
}

function workloadMeasureValue(row: Record<string, unknown>) {
  return (["read", "written"] as const).map(direction => {
    const state = workloadState(row, direction);
    return `${direction === "read" ? "Read" : "Written"}  ${compactValue(state.value)}${state.partial ? "*" : ""}`;
  });
}

function PartialMark({ partial }: { partial: boolean }) {
  return partial ? <sup className="destination-registry-partial" aria-label="Partial measurements; hover the cell for coverage">*</sup> : null;
}

function DestinationWorkloadRowsCell({ row }: { row: Record<string, unknown> }) {
  const read = workloadState(row, "read");
  const written = workloadState(row, "written");
  const line = (label: string, state: ReturnType<typeof workloadState>) => (
    <span><small className="destination-registry-workload-label">{label}</small>
      <span className="destination-registry-workload-value" aria-label={formatTooltipNumber(state.value)}>{compactValue(state.value)}<PartialMark partial={state.partial} /></span>
    </span>
  );
  return <Help className="monitor-stack-cell destination-registry-workload" content={workloadRowsHelpContent(row)}>{line("Read", read)}{line("Written", written)}</Help>;
}

function DestinationFilesCell({ row }: { row: Record<string, unknown> }) {
  return <DestinationEffectCell row={row} kind="files" />;
}

function DestinationBytesCell({ row }: { row: Record<string, unknown> }) {
  return <DestinationEffectCell row={row} kind="bytes" />;
}

function DestinationEffectCell({ row, kind }: { row: Record<string, unknown>; kind: "files" | "bytes" }) {
  const added = metricState(row, `${kind}_added`);
  const removed = metricState(row, `${kind}_removed`);
  const exact = kind === "bytes" ? formatTooltipBytes : formatTooltipNumber;
  const line = (direction: "added" | "removed", state: ReturnType<typeof metricState>) => {
    const positive = state.value != null && compareMetricValues(state.value, 0) > 0;
    return <span className={positive ? `destination-registry-effect is-${direction}` : "destination-registry-effect"}>
      {state.value == null ? "—" : `${direction === "added" ? "+" : "−"} ${compactValue(state.value, kind === "bytes")}`}<PartialMark partial={state.partial} />
    </span>;
  };
  return <Help className="monitor-stack-cell" content={effectHelpContent(row, kind, exact)}><strong>{line("added", added)}</strong><small>{line("removed", removed)}</small></Help>;
}

function coverageLabel(state: ReturnType<typeof metricState>) {
  return state.measured == null || state.denominator == null ? "unavailable" : `${formatTooltipNumber(state.measured)} / ${formatTooltipNumber(state.denominator)} filtered reports`;
}

function effectHelpContent(row: Record<string, unknown>, kind: "files" | "bytes", exact: (value: ReturnType<typeof metricValue>) => string): HelpContent {
  const added = metricState(row, `${kind}_added`);
  const removed = metricState(row, `${kind}_removed`);
  const addedColor = added.value != null && compareMetricValues(added.value, 0) > 0 ? "var(--color-success)" : undefined;
  const removedColor = removed.value != null && compareMetricValues(removed.value, 0) > 0 ? "var(--color-danger)" : undefined;
  return {
    title: kind === "files" ? "File changes" : "Byte changes",
    body: `Qualified physical ${kind} changes within the current filters, not current object ${kind === "bytes" ? "size" : "file count"}.`,
    metrics: [
      { label: "Added", value: `${exact(added.value)}${added.partial ? "*" : ""}`, valueColor: addedColor },
      { label: "Added coverage", value: coverageLabel(added) },
      { label: "Removed", value: `${exact(removed.value)}${removed.partial ? "*" : ""}`, valueColor: removedColor },
      { label: "Removed coverage", value: coverageLabel(removed) },
    ],
    note: "A removal is a recorded change, not a failure. * marks partial measurements.",
  };
}

function maintenanceState(row: Record<string, unknown>) {
  return maintenanceEvidenceState(row);
}

function maintenanceHelpContent(row: Record<string, unknown>, timezoneName?: string | null): HelpContent {
  const state = maintenanceState(row);
  if (state !== "recorded") {
    return {
      title: "Maintenance",
      body: state === "not_recorded"
        ? "No maintenance was recorded within the current filters; this does not imply it is overdue."
        : maintenanceEvidenceLabel(state),
    };
  }
  const outcome = firstText(row, "latest_maintenance_outcome") || "unknown";
  const latest = firstText(row, "latest_maintenance_time");
  const successful = firstText(row, "latest_successful_maintenance_time");
  const outcomeColor = outcome === "dry_run" ? "var(--color-text-muted)" : statusValueColor(outcome);
  return {
    title: "Maintenance",
    context: "Succeeded follows Maintenance status: dry runs are separate and require confirmed non-dry-run destination success.",
    metrics: [
      { label: "Attempts", value: formatTooltipNumber(firstMetric(row, "maintenance_run_count")) },
      ...LIFECYCLE_STATUSES.map(status => ({
        label: `Root ${statusLabel(status)}`,
        value: formatTooltipNumber(firstMetric(row, `maintenance_${status}`)),
        valueColor: statusValueColor(status),
      })),
      { label: "Latest outcome", value: statusLabel(outcome), valueColor: outcomeColor },
      { label: "Latest attempt", value: latest ? formatTimestampForDisplay(latest, timezoneName, "—") : "—" },
      { label: "Latest root-successful attempt", value: successful ? formatTimestampForDisplay(successful, timezoneName, "—") : "—" },
    ],
  };
}

function maintenanceMeasureValue(row: Record<string, unknown>, timezoneName?: string | null) {
  if (maintenanceState(row) !== "recorded") return [maintenanceState(row) === "not_recorded" ? "No maintenance recorded" : maintenanceEvidenceLabel(maintenanceState(row))];
  const latest = firstText(row, "latest_maintenance_time");
  return [latest ? formatTimestampForDisplay(latest, timezoneName, "—") : "—",
    `${compactValue(firstMetric(row, "maintenance_run_count"))} ${metricNumber(firstMetric(row, "maintenance_run_count")) === 1 ? "attempt" : "attempts"}  ${statusLabel(firstText(row, "latest_maintenance_outcome") || "unknown")}`];
}

function MaintenanceCell({ row, timezoneName }: { row: Record<string, unknown>; timezoneName?: string | null }) {
  const state = maintenanceState(row);
  if (state !== "recorded") return <Help className={`destination-registry-maintenance is-${state}`} content={maintenanceHelpContent(row, timezoneName)}>{state === "not_recorded" ? "No maintenance recorded" : maintenanceEvidenceLabel(state)}</Help>;
  const runs = firstMetric(row, "maintenance_run_count");
  const latest = firstText(row, "latest_maintenance_time");
  const outcome = firstText(row, "latest_maintenance_outcome") || "unknown";
  return <Help className="monitor-stack-cell destination-registry-maintenance" content={maintenanceHelpContent(row, timezoneName)}>
    <strong>{latest ? formatTimestampForDisplay(latest, timezoneName, "—") : "—"}</strong>
    <small className="destination-registry-operation-status"><span><CompactNumberValue value={runs} fallback="—" /> {metricNumber(runs) === 1 ? "attempt" : "attempts"}</span><RegistryStatus status={outcome} /></small>
  </Help>;
}

function writeLayoutLines(row: Record<string, unknown>) {
  const layout = presentDestinationLayout(row);
  const partitions = layout.partition_columns;
  return [layout.load_type ? operationLabel(layout.load_type) : "—",
    partitions === null ? "—" : partitions.length ? `${partitions.length} partition key${partitions.length === 1 ? "" : "s"}` : "Unpartitioned"];
}

function WriteLayoutCell({ row, timezoneName }: { row: Record<string, unknown>; timezoneName?: string | null }) {
  const [mode, partitions] = writeLayoutLines(row);
  return <Help className="monitor-stack-cell destination-registry-write-layout" content={writeLayoutHelpContent(row, timezoneName)}><strong>{mode}</strong><small>{partitions}</small></Help>;
}

function writeLayoutHelpContent(row: Record<string, unknown>, timezoneName?: string | null): HelpContent {
  const layout = presentDestinationLayout(row);
  const mode = writeLayoutLines(row)[0];
  const origin = layout.origins.load_type;
  const modes = Array.isArray(row.load_type_variants)
    ? [...new Set(row.load_type_variants.filter((value): value is string => typeof value === "string").map(value => operationLabel(value)))]
    : [];
  const metrics: HelpMetric[] = [];
  if (layout.load_type) metrics.push({ label: "Write mode", value: mode });
  if (origin && origin !== "unknown") metrics.push({ label: "Mode origin", value: origin === "metadata" ? "Metadata fallback" : "Captured" });
  if (modes.length > 1) metrics.push({ label: "Observed modes", value: modes.join(", ") });
  if (layout.partition_columns !== null) {
    metrics.push({ label: "Configured partition keys", value: layout.partition_columns.length ? layout.partition_columns.join(", ") : "None (explicitly unpartitioned)" });
  }
  if (layout.origins.partition_columns && layout.origins.partition_columns !== "unknown") {
    metrics.push({ label: "Partition origin", value: layout.origins.partition_columns === "metadata" ? "Metadata fallback" : "Captured" });
  }
  if (layout.partition_expressions != null) {
    metrics.push({ label: "Partition expressions", value: layout.partition_expressions.length ? layout.partition_expressions.join(", ") : "None" });
  }
  if (layout.origins.partition_expressions && layout.origins.partition_expressions !== "unknown") {
    metrics.push({ label: "Expression origin", value: layout.origins.partition_expressions === "metadata" ? "Metadata fallback" : "Captured" });
  }
  if (layout.observation_time) metrics.push({ label: "Observed at", value: formatTimestampForDisplay(layout.observation_time, timezoneName, "—") });

  const notes = [
    layoutProvenanceNote(layout, "load_type", "Write mode provenance"),
    layoutProvenanceNote(layout, "partition_columns", "Partition key provenance"),
    layoutProvenanceNote(layout, "partition_expressions", "Partition expression provenance"),
    ...(Array.isArray(layout.conflicts) ? layout.conflicts.flatMap(conflict => {
      if (!conflict || typeof conflict !== "object") return [];
      return [`Configuration conflict (${conflict.field}): ${JSON.stringify(conflict.values)}`];
    }) : []),
    destinationLayoutInvalidReason(layout),
  ].filter(Boolean);
  const hasConfiguration = Boolean(layout.load_type || layout.partition_columns !== null || layout.partition_expressions != null);
  return {
    title: "Write layout",
    context: "Latest recorded write configuration; this does not verify physical storage layout.",
    ...(hasConfiguration ? { metrics } : { body: "No write configuration evidence is available within the current filters." }),
    ...(notes.length ? { note: notes.join("\n") } : {}),
  };
}

export const destinationRegistryHelpTestUtils = {
  runCountHelpContent,
  latestOperationHelpContent,
  destinationFormatHelpContent,
  destinationRegistryHelpContent,
  workloadRowsHelpContent,
  effectHelpContent,
  maintenanceHelpContent,
  writeLayoutHelpContent,
};
