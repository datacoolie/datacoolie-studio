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
import { CompactValue, FAILURE_PHASES, FAILURE_PHASE_COLORS, FAILURE_PHASE_LABELS, FAILURE_TREND_COLORS, IssuePreview, MonitoringChartLegend, TableDateTimeValue, humanize } from "../components/monitoringPrimitives";

export function FailureQueueTable({
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
      compactNumbers
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

export function RepeatedFailureTable({
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
      compactNumbers
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

export function EndpointImpactTable({
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
      compactNumbers
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

export function FailureDataflowNameCell({ row }: { row: Record<string, unknown> }) {
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

export function FailureRouteCell({ row }: { row: Record<string, unknown> }) {
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

export function FailurePhaseBadge({ value }: { value: unknown }) {
  const textValue = String(value || "unknown");
  return <span className={`failure-phase-badge is-${textValue}`}>{humanize(textValue)}</span>;
}

export function FailureTrendLegend() {
  return <MonitoringChartLegend label="Failure trend legend" items={[
    ["Dataflows", FAILURE_TREND_COLORS.dataflows],
    ["Jobs", FAILURE_TREND_COLORS.jobs]
  ]} />;
}

export function FailurePhaseLegend() {
  return <MonitoringChartLegend
    label="Failure phase legend"
    items={FAILURE_PHASES.map((phase) => [FAILURE_PHASE_LABELS[phase], FAILURE_PHASE_COLORS[phase]] as const)}
  />;
}
