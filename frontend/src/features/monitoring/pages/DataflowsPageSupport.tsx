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
import { CopyableText, DataflowContextCell, DataflowNameCell, DataflowPhaseCell, DataflowVolumeCell, EndpointCell, EndpointRouteNode, IssuePreview, TableDateTimeValue, WatermarkBadge, cleanDisplayValue, compactRunId, dataflowNameStatusHealthOption, formatPercent, stageTotal, successRateIntent } from "../components/monitoringPrimitives";

export function DataflowNameStatusHealthPanel({ rows }: { rows: Array<Record<string, string | number | null>> }) {
  const visible = rows.filter((row) => String(row.dataflow_name ?? "").trim() && stageTotal(row as Record<string, string | number>) > 0);
  if (!visible.length) return <div className="table-empty">No dataflow status health signals in current filters.</div>;
  return (
    <div className="overview-operation-health">
      <ReportChart option={dataflowNameStatusHealthOption(visible)} height="100%" wheelDataZoomStep={1} />
    </div>
  );
}

export function DataflowEndpointHealthPanel({ rows }: { rows: Array<Record<string, string | number | null>> }) {
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

export function endpointRoutePresentation(row: Record<string, string | number | null>, direction: "source" | "destination") {
  const prefix = direction === "source" ? "source" : "destination";
  const value = (key: string) => row[`${prefix}_${key}`];
  const connection = cleanDisplayValue(value("name")) ?? "unknown connection";
  const connectionType = cleanDisplayValue(value("connection_type")) ?? "unknown type";
  const format = cleanDisplayValue(value("format") ?? connectionType) ?? "";
  const locator = connection;
  return { locator, connection, connectionType, format };
}

export function DataflowRunsTable({
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
