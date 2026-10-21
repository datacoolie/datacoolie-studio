import { Help } from "../../../shared/components/Help";
import type { MonitoringRecord } from "../../../shared/api/domainTypes";
import { monitoringDimensionLabel } from "../monitoringDimensions";
import {
  DataTable,
  StatusCell,
  type TableSort,
  formatSeconds,
  num
} from "../../../shared/components/DataTable";
import { ReportChart } from "../ReportChart";
import { statusColor } from "../components/monitoringChartOptions";
import { chartDurationMetric, chartMetric } from "../components/chartTooltip";
import { EndpointRouteNode, dataflowNameStatusHealthOption } from "./DataflowsPagePresentation";
import { CompactNumberValue, CopyableText, DataflowContextCell, DataflowNameCell, DataflowPhaseCell, DataflowVolumeCell, EndpointCell, IssuePreview, TableDateTimeValue, WatermarkBadge, compactRunId, formatTooltipBytes, formatTooltipNumber, formatPercent, metricValue, stageTotal } from "../components/monitoringPresentation";

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
        const successRate = num(row, "success_rate");
        const rowsReadValue = metricValue(row, "rows_read");
        const rowsWrittenValue = metricValue(row, "rows_written");
        const p95 = num(row, "p95_duration_seconds");
        const source = endpointRoutePresentation(row, "source");
        const destination = endpointRoutePresentation(row, "destination");
        const sourceFormat = monitoringDimensionLabel(row.source_format ?? row.source_connection_type);
        const destinationFormat = monitoringDimensionLabel(row.destination_format ?? row.destination_connection_type);
        return (
          <Help as="div"
            key={`${source.connection}-${source.locator}-${destination.connection}-${destination.locator}-${index}`}
            className="dataflow-signal-row dataflow-endpoint-route-row"
            content={endpointHealthTooltipContent(row, source, destination, sourceFormat, destinationFormat)}
          >
            <div className="dataflow-route-flow">
              <div className="dataflow-route-line">
                <EndpointRouteNode endpoint={source} />
                <span className="dataflow-route-arrow" aria-hidden="true">→</span>
                <EndpointRouteNode endpoint={destination} />
              </div>
            </div>
            <div className="dataflow-route-health">
              <strong>{formatPercent(successRate)}</strong>
              <small>
                <span><CompactNumberValue value={num(row, "runs")} /> runs</span>
                {failed ? <><span aria-hidden="true"> · </span><span><CompactNumberValue value={failed} /> failed</span></> : null}
              </small>
            </div>
            <div className="dataflow-route-volume">
              <strong><CompactNumberValue value={rowsReadValue} /> / <CompactNumberValue value={rowsWrittenValue} /></strong>
              <small className="dataflow-route-duration">P95 {formatSeconds(p95)}</small>
            </div>
          </Help>
        );
      })}
    </div>
  );
}

export function endpointHealthTooltipContent(
  row: Record<string, string | number | null>,
  source: ReturnType<typeof endpointRoutePresentation>,
  destination: ReturnType<typeof endpointRoutePresentation>,
  sourceFormat: string,
  destinationFormat: string
) {
  const rowsReadValue = metricValue(row, "rows_read");
  const rowsWrittenValue = metricValue(row, "rows_written");
  const bytesAddedValue = metricValue(row, "bytes_added");
  const bytesRemovedValue = metricValue(row, "bytes_removed");
  return {
    title: `${source.connection} → ${destination.connection}`,
    context: `Source: ${sourceFormat}\nDestination: ${destinationFormat}`,
    metrics: [
      chartMetric("Dataflow runs", formatTooltipNumber(metricValue(row, "runs"))),
      chartMetric("Success rate", formatPercent(metricValue(row, "success_rate"))),
      { ...chartMetric("Succeeded", formatTooltipNumber(metricValue(row, "succeeded"))), valueColor: statusColor("succeeded") },
      { ...chartMetric("Failed", formatTooltipNumber(metricValue(row, "failed"))), valueColor: statusColor("failed") },
      { ...chartMetric("Skipped", formatTooltipNumber(metricValue(row, "skipped"))), valueColor: statusColor("skipped") },
      { ...chartMetric("Running", formatTooltipNumber(metricValue(row, "running"))), valueColor: statusColor("running") },
      { ...chartMetric("Pending", formatTooltipNumber(metricValue(row, "pending"))), valueColor: statusColor("pending") },
      { ...chartMetric("Unknown", formatTooltipNumber(metricValue(row, "unknown"))), valueColor: statusColor("unknown") },
      chartMetric("AVG / P95 duration", `${chartDurationMetric(metricValue(row, "avg_duration_seconds"), { compactOnly: true })} / ${chartDurationMetric(metricValue(row, "p95_duration_seconds"), { compactOnly: true })}`),
      chartMetric("Rows read / written", `${formatTooltipNumber(rowsReadValue)} / ${formatTooltipNumber(rowsWrittenValue)}`),
      chartMetric("Bytes added / removed", `${formatTooltipBytes(bytesAddedValue)} / ${formatTooltipBytes(bytesRemovedValue)}`),
    ]
  };
}

export function endpointRoutePresentation(row: Record<string, string | number | null>, direction: "source" | "destination") {
  const prefix = direction === "source" ? "source" : "destination";
  const value = (key: string) => row[`${prefix}_${key}`];
  const connection = monitoringDimensionLabel(value("name"));
  const connectionType = monitoringDimensionLabel(value("connection_type"));
  const format = monitoringDimensionLabel(value("format") ?? value("connection_type"));
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
      compactNumbers
      compactNumberStyle="infix"
      columns={[
        { key: "job_id", label: "Job", sortable: true, width: 104, className: "dataflow-run-col-job", render: (row) => <CopyableText value={row.job_id} displayValue={compactRunId(row.job_id)} /> },
        { key: "dataflow_name", label: "Dataflow", sortable: true, width: 148, className: "dataflow-run-col-dataflow", render: (row) => <DataflowNameCell row={row} /> },
        { key: "context", label: "Context", sortable: true, sortKey: "stage", width: 118, className: "dataflow-run-col-context", render: (row) => <DataflowContextCell row={row} rawDimensions /> },
        { key: "source_name", label: "Source", sortable: true, width: 150, className: "dataflow-run-col-endpoint", render: (row) => <EndpointCell row={row} direction="source" rawDimensions /> },
        { key: "destination_name", label: "Destination", sortable: true, width: 150, className: "dataflow-run-col-endpoint", render: (row) => <EndpointCell row={row} direction="destination" rawDimensions /> },
        { key: "phase_health", label: "Phase", width: 100, className: "dataflow-run-col-phase", render: (row) => <DataflowPhaseCell row={row} /> },
        { key: "start_time", label: "Start", sortable: true, width: 178, className: "dataflow-run-col-time", render: (row) => <TableDateTimeValue value={row.start_time} timezoneName={timezoneName} showTooltip={false} /> },
        { key: "end_time", label: "End", sortable: true, width: 178, className: "dataflow-run-col-time", render: (row) => <TableDateTimeValue value={row.end_time} timezoneName={timezoneName} showTooltip={false} /> },
        { key: "duration_seconds", label: "Duration", sortable: true, width: 76, className: "dataflow-run-col-duration", render: (row) => formatSeconds(num(row, "duration_seconds")) },
        { key: "status", label: "Status", sortable: true, width: 98, className: "dataflow-run-col-status", render: (row) => <StatusCell row={row} /> },
        { key: "volume", label: "Volume", width: 106, className: "dataflow-run-col-volume", render: (row) => <DataflowVolumeCell row={row} /> },
        { key: "movement_state", label: "Watermark", width: 90, className: "dataflow-run-col-watermark", render: (row) => <WatermarkBadge value={row.movement_state} /> },
        { key: "message", label: "Message", sortable: true, minWidth: 64, fillPriority: "last", className: "dataflow-run-col-issue", render: (row) => <IssuePreview row={row} /> },
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
