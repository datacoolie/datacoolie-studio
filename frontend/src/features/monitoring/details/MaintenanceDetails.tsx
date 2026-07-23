import { ArrowLeft, ArrowRight, Boxes, BriefcaseBusiness, Check, ChevronRight, Clock3, Copy, FileText, SearchCheck, Workflow, X } from "lucide-react";
import { isValidElement, useEffect, useMemo, useState, type CSSProperties, type ReactNode } from "react";
import { createPortal } from "react-dom";
import type { MonitoringRecord } from "../../../shared/api/domainTypes";
import { useDrawerEscape } from "../../../shared/hooks/useDrawerEscape";
import { formatTimestampForDisplay, hasExplicitTimezone, isTimestampFieldName } from "../../../shared/time";
import { lifecycleStatusFromField, lifecycleStatusPresentation, type LifecycleStatus } from "../../../shared/statusPresentation";
import { LineageFormatIcon } from "../../lineage/components/LineageFormatIcon";
import { DataTable, StatusCell, display, formatBytes, formatNumber, formatSeconds, num, type TableSort } from "../MonitoringCharts";
import {
  diagnosticsCategoryLabel,
  diagnosticsEvidenceItems,
  diagnosticsInvestigationActions,
  diagnosticsLinkedJobRow,
  diagnosticsRuleDescription,
  diagnosticsSeverityPresentation,
} from "../diagnosticsPresentation";
import { formatMaintenanceLag, maintenanceFormatIconKind, maintenanceTableHealthClass, maintenanceTableHealthLabel, maintenanceTableHealthTone } from "../maintenancePresentation";
import { formatPhasePercent, monitoringEndpointPresentation, TablePager } from "../components/monitoringPrimitives";
import { SystemLogViewer } from "../SystemLogViewer";
import { GroupedDetailCard, IssueCell, MaintenanceHealthChip, detailValue, hasValue } from "./detailPrimitives";

export function MaintenanceDetailSections({
  row,
  relatedDataflows,
  total,
  loading,
  offset,
  limit,
  sort,
  onSort,
  onPageChange,
  onPageSizeChange,
  onOpenDataflow,
  timezoneName,
}: {
  row: Record<string, unknown>;
  relatedDataflows: MonitoringRecord[];
  total: number;
  loading: boolean;
  offset: number;
  limit: number;
  sort: TableSort;
  onSort: (sort: TableSort) => void;
  onPageChange: (offset: number) => void;
  onPageSizeChange: (limit: number) => void;
  onOpenDataflow?: (row: MonitoringRecord) => void;
  timezoneName?: string | null;
}) {
  const destinationRuns = relatedDataflows;
  const contributingDataflows = Array.isArray(row.upstream_dataflows)
    ? row.upstream_dataflows as MonitoringRecord[]
    : [];
  const upstreamRunCount = Number(row.upstream_run_count ?? 0);
  return (
    <>
      <section className="monitoring-detail-section monitoring-maintenance-summary-section">
        <div className="monitoring-maintenance-summary-grid">
          <GroupedDetailCard
            title="Health"
            rows={[
              ["Status", <MaintenanceHealthChip key="health" health={row.table_health} reason={row.attention_reason} />],
              ["Health reason", row.attention_reason],
              ["Latest maintenance", row.latest_maintenance_time, "latest_maintenance_time"],
              ["Latest ETL write", row.latest_etl_write_time, "latest_etl_write_time"],
              ["Lag", formatMaintenanceLag(num(row, "maintenance_lag_seconds"))],
              ["Active lakehouse", row.active_lakehouse_table],
              ["Maintenance coverage", row.maintained_table],
            ]}
            showEmpty
            timezoneName={timezoneName}
            className={`monitoring-maintenance-health-card ${maintenanceTableHealthClass(row.table_health)}`}
          />
          <GroupedDetailCard
            title="Run outcome"
            rows={[
              ["Runs", row.run_count, "run_count"],
              ["Latest status", row.latest_status, "latest_status"],
              ["Succeeded", row.succeeded, "succeeded"],
              ["Failed", row.failed, "failed"],
              ["Skipped", row.skipped, "skipped"],
              ["Running", row.running, "running"],
              ["Pending", row.pending, "pending"],
              ["Unknown", row.unknown, "unknown"],
              ["No-op runs", <MaintenanceMetricValue key="no-op-runs" tone="warning" value={formatNumber(num(row, "no_op_runs"))} />],
              ["No-op duration", <MaintenanceMetricValue key="no-op-duration" tone="warning" value={formatSeconds(num(row, "no_op_duration_seconds"))} />],
            ]}
            showEmpty
            timezoneName={timezoneName}
            className="monitoring-maintenance-outcome-card"
          />
          <GroupedDetailCard
            title="Storage impact"
            rows={[
              ["Bytes reclaimed", <MaintenanceMetricValue key="bytes-reclaimed" tone="reclaim" value={formatBytes(num(row, "bytes_reclaimed"))} />],
              ["Files removed", <MaintenanceMetricValue key="files-removed" tone="files" value={formatNumber(num(row, "files_removed"))} />],
              ["Bytes saved", <MaintenanceMetricValue key="bytes-saved" tone="reclaim" value={formatBytes(num(row, "bytes_saved"))} />],
              ["Total duration", formatSeconds(num(row, "duration_seconds"))],
              ["Efficiency", `${formatBytes(num(row, "bytes_reclaimed_per_second"))}/s`],
            ]}
            showEmpty
            timezoneName={timezoneName}
            className="monitoring-maintenance-storage-card"
          />
        </div>
      </section>

      <section className="monitoring-detail-section">
        <div className="monitoring-detail-section-header monitoring-child-dataflows-header">
          <h3>Upstream dataflows</h3>
          <small>{`${contributingDataflows.length} dataflows · ${upstreamRunCount} ETL runs`}</small>
        </div>
        {loading ? <MaintenanceRelatedLoading /> : (
          <DataTable
            rows={contributingDataflows}
            columns={[
              { key: "dataflow_name", label: "Dataflow", sortable: true, minWidth: 180, fillPriority: "normal", render: (item) => <MaintenanceDataflowCell row={item} /> },
              { key: "source", label: "Source", sortable: true, minWidth: 150, fillPriority: "last", render: (item) => <MaintenanceSourceCell row={item} /> },
              { key: "load_type", label: "Load", sortable: true, autoFit: true, minWidth: 72, maxWidth: 112 },
              { key: "latest_status", label: "Latest", sortable: true, sortKey: "latest_time", minWidth: 164, maxWidth: 184, render: (item) => <MaintenanceLatestCell row={item} timezoneName={timezoneName} /> },
              { key: "run_count", label: "Runs / rows", sortable: true, autoFit: true, minWidth: 104, maxWidth: 128, render: (item) => <MaintenanceContributingVolumeCell row={item} /> },
            ]}
            maxRows={Math.max(1, contributingDataflows.length)}
            fixedLayout
            timezoneName={timezoneName}
            className="monitoring-child-dataflows-table monitoring-maintenance-upstream-table"
          />
        )}
      </section>

      <section className="monitoring-detail-section">
        <div className="monitoring-detail-section-header monitoring-freshness-runs-header monitoring-child-dataflows-section-header">
          <div className="monitoring-child-dataflows-header">
            <h3>Run history</h3>
            <small>{loading ? "Loading destination runs…" : `${total} runs into this destination`}</small>
          </div>
          {!loading ? (
            <TablePager
              limit={limit}
              offset={offset}
              loadedRows={destinationRuns.length}
              totalRows={total}
              loading={loading}
              onPageChange={onPageChange}
              onPageSizeChange={onPageSizeChange}
            />
          ) : null}
        </div>
        {loading ? <MaintenanceRelatedLoading /> : (
          <DataTable
            rows={destinationRuns}
            columns={[
              { key: "dataflow_name", label: "Dataflow", sortable: true, minWidth: 180, fillPriority: "normal", render: (item) => <MaintenanceDataflowCell row={item} /> },
              { key: "source", label: "Source", sortable: true, minWidth: 150, maxWidth: 190, fillPriority: "normal", render: (item) => <MaintenanceSourceCell row={item} /> },
              { key: "latest", label: "Latest", sortable: true, sortKey: "end_time", autoFit: true, minWidth: 156, maxWidth: 184, render: (item) => <MaintenanceLatestCell row={item} timezoneName={timezoneName} /> },
              { key: "duration_seconds", label: "Duration", sortable: true, autoFit: true, minWidth: 76, maxWidth: 104, render: (item) => formatSeconds(num(item, "duration_seconds")) },
              { key: "volume", label: "Volume", sortable: true, sortKey: "source_rows_read", minWidth: 116, maxWidth: 136, render: (item) => <MaintenanceRunVolumeCell row={item} /> },
              { key: "error_message", label: "Issue", sortable: true, minWidth: 140, fillPriority: "last", render: (item) => <IssueCell row={item} /> },
            ]}
            maxRows={limit}
            offset={0}
            onRowClick={onOpenDataflow}
            sort={sort}
            onSort={onSort}
            fixedLayout
            timezoneName={timezoneName}
            className="monitoring-child-dataflows-table monitoring-maintenance-run-history-table"
          />
        )}
      </section>
    </>
  );
}

export function MaintenanceMetricValue({ tone, value }: { tone: "reclaim" | "files" | "warning"; value: string }) {
  return <span className={`monitoring-maintenance-metric-value is-${tone}`}>{value}</span>;
}

export function MaintenanceRelatedLoading() {
  return <div className="table-empty monitoring-maintenance-related-loading">Loading related evidence…</div>;
}

export function isMaintenanceRun(row: Record<string, unknown>) {
  const operationType = String(row.operation_type ?? "").toLowerCase();
  const destinationOperationType = String(row.destination_operation_type ?? "").toLowerCase();
  return operationType === "maintenance" || ["compact", "cleanup", "maintenance"].includes(destinationOperationType);
}

export function MaintenanceDataflowCell({ row }: { row: Record<string, unknown> }) {
  const name = String(row.dataflow_name ?? row.dataflow_id ?? "unknown dataflow");
  const stage = String(row.stage ?? "unknown");
  const operation = String(row.operation_type ?? "unknown");
  const destinationOperation = String(row.destination_operation_type ?? "-");
  const context = [stage, operation, destinationOperation !== "-" && destinationOperation !== operation ? destinationOperation : ""].filter(Boolean).join(" · ");
  return (
    <span
      className="freshness-run-stack-cell"
      title={[
        `Stage: ${stage}`,
        `Operation: ${operation}`,
        `Destination operation: ${destinationOperation}`,
      ].join("\n")}
    >
      <strong>{name}</strong>
      <small>{context}</small>
    </span>
  );
}

export function MaintenanceSourceCell({ row }: { row: Record<string, unknown> }) {
  const source = maintenanceSourceParts(row);
  return (
    <span
      className="maintenance-source-cell"
      title={[
        `Source connection: ${source.connection}`,
        `Source object: ${source.object}`,
      ].join("\n")}
    >
      <strong>{source.connection}</strong>
      <small>{source.object}</small>
    </span>
  );
}

export function MaintenanceLatestCell({ row, timezoneName }: { row: Record<string, unknown>; timezoneName?: string | null }) {
  const status = String(row.latest_status ?? row.status ?? "unknown");
  const latestField = hasValue(row.latest_time) ? "latest_time" : hasValue(row.end_time) ? "end_time" : "start_time";
  const latest = detailValue(row, latestField, timezoneName);
  return (
    <span
      className="freshness-run-stack-cell"
      title={[
        `Latest status: ${status}`,
        `Latest time: ${String(latest ?? "-")}`,
      ].join("\n")}
    >
      <strong className={`maintenance-status-text ${maintenanceStatusTextClass(status)}`}>{status}</strong>
      <small>{latest}</small>
    </span>
  );
}

export function maintenanceStatusTextClass(status: string) {
  const normalized = status.toLowerCase();
  if (normalized === "succeeded") return "is-succeeded";
  if (normalized === "failed") return "is-failed";
  if (normalized === "skipped") return "is-skipped";
  if (normalized === "running") return "is-running";
  if (normalized === "pending") return "is-pending";
  return "is-unknown";
}

export function MaintenanceContributingVolumeCell({ row }: { row: Record<string, unknown> }) {
  const runs = num(row, "run_count");
  const rowsRead = num(row, "rows_read");
  return (
    <span
      className="freshness-run-stack-cell"
      title={[
        `Runs: ${formatNumber(runs)}`,
        `Rows read: ${formatNumber(rowsRead)}`,
      ].join("\n")}
    >
      <strong>{formatNumber(runs)}</strong>
      <small>{formatNumber(rowsRead)}</small>
    </span>
  );
}

export function MaintenanceRunVolumeCell({ row }: { row: Record<string, unknown> }) {
  const rowsRead = num(row, "source_rows_read");
  const rowsWritten = num(row, "destination_rows_written");
  const bytesAdded = num(row, "destination_bytes_added");
  const bytesRemoved = num(row, "destination_bytes_removed");
  const filesRemoved = num(row, "destination_files_removed");
  const isMaintenance = isMaintenanceRun(row);
  const primary = isMaintenance ? formatBytes(bytesRemoved) : `${formatNumber(rowsRead)} / ${formatNumber(rowsWritten)}`;
  const secondary = isMaintenance ? `${formatNumber(filesRemoved)} files` : formatBytes(bytesAdded - bytesRemoved);
  return (
    <span
      className="freshness-run-stack-cell"
      title={[
        `Rows read: ${formatNumber(rowsRead)}`,
        `Rows written: ${formatNumber(rowsWritten)}`,
        `Lakehouse bytes added: ${formatBytes(bytesAdded)}`,
        `Lakehouse bytes removed: ${formatBytes(bytesRemoved)}`,
        `Files removed: ${formatNumber(filesRemoved)}`,
      ].join("\n")}
    >
      <strong>{primary}</strong>
      <small>{secondary}</small>
    </span>
  );
}

export function maintenanceSourceParts(row: Record<string, unknown>) {
  if (hasValue(row.source)) {
    const [connection, ...rest] = String(row.source).split(" · ");
    return {
      connection: connection || "unknown",
      object: rest.join(" · ") || "-",
    };
  }
  const connection = row.source_name ?? row.source_connection_name ?? "unknown";
  const table = row.source_full_table ?? row.source_table ?? row.source_path ?? row.source_python_function ?? row.source_query ?? "-";
  return {
    connection: String(connection),
    object: String(table),
  };
}
