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
import { CompactNumberValue, formatPhasePercent, monitoringEndpointPresentation, TablePager } from "../components/monitoringPrimitives";
import { SystemLogViewer } from "../SystemLogViewer";
import { DataflowPhaseContribution, ErrorMessageBlock, FreshnessIdentitySection, FreshnessRunTimeCell, GroupedDetailCard, IssueCell, dataflowPhaseBottleneck, dataflowPhaseSegments, detailValue, firstValue, formatFreshnessAge, freshnessRunTimeLines, hasValue, humanize, phaseLabel } from "./detailPrimitives";

export function FreshnessDetailSections({
  row,
  relatedDataflows,
  total,
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
  offset: number;
  limit: number;
  sort: TableSort;
  onSort: (sort: TableSort) => void;
  onPageChange: (offset: number) => void;
  onPageSizeChange: (limit: number) => void;
  onOpenDataflow?: (row: MonitoringRecord) => void;
  timezoneName?: string | null;
}) {
  const watermarkConfigured = isFreshnessWatermarkConfigured(row);
  return (
    <>
      <FreshnessIdentitySection row={row} />

      <section className="monitoring-detail-section monitoring-freshness-health-section">
        <h3>Freshness health</h3>
        <div className={`monitoring-freshness-summary-grid${watermarkConfigured ? " has-configured-watermark" : ""}`}>
          <GroupedDetailCard
            title="Freshness evidence"
            rows={[
              ["Latest check", row.latest_freshness_at, "latest_freshness_at"],
              ["Age", formatFreshnessAge(row.age_seconds, row.age_days)],
              ["Basis status", row.latest_freshness_status, "status"],
            ]}
            showEmpty
            timezoneName={timezoneName}
          />
          <GroupedDetailCard
            title="Latest run"
            rows={[
              ["Time", row.latest_run_at, "latest_run_at"],
              ["Status", row.latest_run_status, "status"],
              ["Skipped streak", row.skipped_streak, "skipped_streak"],
              ["Runs in filter", <FreshnessRunMix row={row} />],
            ]}
            showEmpty
            timezoneName={timezoneName}
          />
          <GroupedDetailCard
            title="Watermark"
            rows={watermarkConfigured ? [
              ["Coverage", row.coverage_state],
              ["Movement", row.movement_state],
              ["Adjustment", row.adjustment_state],
              ["Watermark time", row.watermark_time, "watermark_time"],
              ["Latest successful", row.latest_success_watermark, "latest_success_watermark"],
              ["Columns", row.source_watermark_columns, "source_watermark_columns"],
              ["Before", row.source_watermark_before, "source_watermark_before"],
              ["Effective", row.source_watermark_effective, "source_watermark_effective"],
              ["After", row.source_watermark_after, "source_watermark_after"],
            ] : [["State", <span className="monitor-mini-badge badge-neutral">Not configured</span>]]}
            timezoneName={timezoneName}
            className="monitoring-freshness-watermark-card"
          />
        </div>
      </section>

      {hasValue(row.latest_error_message) ? (
        <section className="monitoring-detail-section monitoring-error-message-section">
          <h3>Latest error message</h3>
          <ErrorMessageBlock value={detailValue(row, "latest_error_message", timezoneName)} />
        </section>
      ) : null}

      <section className="monitoring-detail-section monitoring-freshness-metadata-details">
        <h3>Metadata context</h3>
        <div className="monitoring-dataflow-detail-grid monitoring-latest-metadata-grid">
          <GroupedDetailCard
            title="Dataflow"
            className="monitoring-freshness-metadata-card is-dataflow"
            rows={[
              ["Dataflow ID", row.dataflow_id],
              ["Workspace ID", row.workspace_id],
              ["Name", row.dataflow_name],
              ["Description", row.dataflow_description],
              ["Stage", row.stage],
              ["Group number", row.group_number],
              ["Execution order", row.execution_order],
              ["Processing mode", row.processing_mode],
              ["Operation type", row.operation_type],
              ["Active", row.is_active],
              ["Configure", row.configure, "configure"],
            ]}
            showEmpty
            timezoneName={timezoneName}
          />
          <GroupedDetailCard
            title="Source"
            className="monitoring-freshness-metadata-card is-source"
            rows={[
              ["Source ID", row.source_id],
              ["Source name", firstValue(row, ["source_name", "source_connection_name"])],
              ["Connection type", row.source_connection_type],
              ["Format", row.source_format],
              ["Catalog", row.source_catalog],
              ["Database", row.source_database],
              ["Schema", row.source_schema],
              ["Table", row.source_table],
              ["Full table", row.source_full_table],
              ["Path", row.source_path],
              ["Query", row.source_query, "source_query"],
              ["Python function", row.source_python_function],
              ["Action", row.source_action],
              ["Watermark columns", row.source_watermark_columns, "source_watermark_columns"],
              ["Filter", row.source_filter_expression, "source_filter_expression"],
              ["Configure", row.source_configure, "source_configure"],
            ]}
            showEmpty
            timezoneName={timezoneName}
          />
          <GroupedDetailCard
            title="Transform"
            className="monitoring-freshness-metadata-card is-transform"
            rows={[
              ["Deduplicate", row.transform_deduplicate_columns, "transform_deduplicate_columns"],
              ["Latest data", row.transform_latest_data_columns, "transform_latest_data_columns"],
              ["Filter", row.transform_filter_expression, "transform_filter_expression"],
              ["Additional columns", row.transform_additional_columns, "transform_additional_columns"],
              ["Schema hints", row.transform_schema_hints, "transform_schema_hints"],
              ["Select columns", row.transform_select_columns, "transform_select_columns"],
              ["Drop columns", row.transform_drop_columns, "transform_drop_columns"],
              ["Rename columns", row.transform_rename_columns, "transform_rename_columns"],
              ["Value rules", row.transform_value_rules, "transform_value_rules"],
              ["Hash columns", row.transform_hash_columns, "transform_hash_columns"],
              ["Masking rules", row.transform_masking_rules, "transform_masking_rules"],
              ["Configure", row.transform_configure, "transform_configure"],
            ]}
            showEmpty
            timezoneName={timezoneName}
          />
          <GroupedDetailCard
            title="Destination"
            className="monitoring-freshness-metadata-card is-destination"
            rows={[
              ["Destination ID", row.destination_id],
              ["Destination name", firstValue(row, ["destination_name", "destination_connection_name"])],
              ["Connection type", row.destination_connection_type],
              ["Format", row.destination_format],
              ["Catalog", row.destination_catalog],
              ["Database", row.destination_database],
              ["Schema", row.destination_schema],
              ["Table", row.destination_table],
              ["Full table", row.destination_full_table],
              ["Path", row.destination_path],
              ["Load type", row.destination_load_type],
              ["Merge keys", row.destination_merge_keys, "destination_merge_keys"],
              ["Partition columns", row.destination_partition_columns, "destination_partition_columns"],
              ["Configure", row.destination_configure, "destination_configure"],
            ]}
            showEmpty
            timezoneName={timezoneName}
          />
        </div>
      </section>

      <section className="monitoring-detail-section monitoring-freshness-runs-section">
        <div className="monitoring-detail-section-header monitoring-freshness-runs-header">
          <h3>Dataflow runs</h3>
          <TablePager
            limit={limit}
            offset={offset}
            loadedRows={relatedDataflows.length}
            totalRows={total}
            loading={false}
            onPageChange={onPageChange}
            onPageSizeChange={onPageSizeChange}
          />
        </div>
        <DataTable
          rows={relatedDataflows}
          compactNumbers
          columns={[
            {
              key: "start_time",
              label: "Time",
              sortable: true,
              autoFit: true,
              minWidth: 144,
              maxWidth: 216,
              render: (run) => <FreshnessRunTimeCell row={run} timezoneName={timezoneName} />,
              measureValue: (run, activeTimezone) => freshnessRunTimeLines(run, activeTimezone),
            },
            { key: "status", label: "Status", sortable: true, width: 82, render: (run) => <StatusCell row={run} /> },
            { key: "duration_seconds", label: "Duration", sortable: true, width: 78, render: (run) => formatSeconds(num(run, "duration_seconds")) },
            { key: "volume", label: "Volume", sortable: true, sortKey: "source_rows_read", width: 104, render: (run) => <FreshnessRunVolumeCell row={run} /> },
            { key: "movement_state", label: "Watermark", sortable: true, width: 110, render: (run) => <DrawerWatermarkBadge row={run} /> },
            { key: "phase_health", label: "Phase", sortable: true, width: 108, render: (run) => <FreshnessRunPhaseCell row={run} /> },
            { key: "error_preview", label: "Issue", sortable: true, width: 154, fillPriority: "last", className: "monitoring-child-issue-column", render: (run) => <IssueCell row={run} /> },
          ]}
          maxRows={limit}
          offset={0}
          onRowClick={onOpenDataflow}
          sort={sort}
          onSort={onSort}
          fixedLayout
          className="monitoring-child-dataflows-table monitoring-freshness-run-table"
          timezoneName={timezoneName}
        />
      </section>
    </>
  );
}

export function freshnessEndpointLabel(row: Record<string, unknown>, side: "source" | "destination") {
  const connection = firstValue(row, [`${side}_connection_name`, `${side}_name`]);
  const format = String(row[`${side}_format`] ?? "").trim().toLowerCase();
  let asset: unknown;
  if (format.includes("sql") || (side === "source" && hasValue(row.source_query))) asset = "sql query";
  else if (format.includes("function") || (side === "source" && hasValue(row.source_python_function))) asset = "python function";
  else asset = firstValue(row, [`${side}_table`, `${side}_full_table`, `${side}_path`, side === "destination" ? "target" : "source_name"]);
  return [connection, asset].filter(hasValue).map(String).join(" - ") || "-";
}

export function isFreshnessWatermarkConfigured(row: Record<string, unknown>) {
  const coverage = String(row.coverage_state ?? "").trim().toLowerCase();
  const movement = String(row.movement_state ?? "").trim().toLowerCase();
  if ([coverage, movement].some((value) => value === "not_configured" || value === "not configured")) return false;
  return [
    row.source_watermark_columns,
    row.source_watermark_before,
    row.source_watermark_effective,
    row.source_watermark_after,
    row.watermark_time,
  ].some(hasValue) || Boolean(coverage || movement);
}

export function FreshnessRunMix({ row }: { row: Record<string, unknown> }) {
  return (
    <span className="monitoring-freshness-run-mix" title="Succeeded / failed / skipped / running / pending">
      <strong><CompactNumberValue value={num(row, "run_count")} /></strong><span>total</span><i>·</i>
      <span>S</span>
      <strong className="is-success"><CompactNumberValue value={num(row, "succeeded_count")} /></strong>
      <span>F</span>
      <strong className="is-failed"><CompactNumberValue value={num(row, "failed_count")} /></strong>
      <span>Skip</span>
      <strong className="is-warning"><CompactNumberValue value={num(row, "skipped_count")} /></strong>
      <span>Run</span>
      <strong><CompactNumberValue value={num(row, "running_count")} /></strong>
      <span>Pend</span>
      <strong><CompactNumberValue value={num(row, "pending_count")} /></strong>
    </span>
  );
}

export function FreshnessRunPhaseCell({ row }: { row: Record<string, unknown> }) {
  const bottleneck = dataflowPhaseBottleneck(row);
  const title = dataflowPhaseSegments(row)
    .map((segment) => `${phaseLabel(segment.phase)}: ${formatSeconds(segment.value)} (${formatPhasePercent(segment.percent)})`)
    .join("\n");
  return (
    <span className="freshness-run-phase-cell" title={[bottleneck ? `Bottleneck: ${bottleneck.label}` : "", title].filter(Boolean).join("\n")}>
      {bottleneck ? <strong className={`phase-text-${bottleneck.phase}`}>{bottleneck.label}</strong> : <strong className="phase-text-unknown">-</strong>}
      <DataflowPhaseContribution row={row} />
    </span>
  );
}

export function FreshnessRunVolumeCell({ row }: { row: Record<string, unknown> }) {
  const rowsRead = num(row, "source_rows_read");
  const rowsWritten = num(row, "destination_rows_written");
  const bytesAdded = num(row, "destination_bytes_added");
  const bytesRemoved = num(row, "destination_bytes_removed");
  const netBytes = bytesAdded - bytesRemoved;
  return (
    <span
      className="freshness-run-stack-cell"
      title={[
        `Rows read: ${formatNumber(rowsRead)}`,
        `Rows written: ${formatNumber(rowsWritten)}`,
        `Lakehouse bytes added: ${formatBytes(bytesAdded)}`,
        `Lakehouse bytes removed: ${formatBytes(bytesRemoved)}`,
        `Net lakehouse bytes: ${formatBytes(netBytes)}`,
      ].join("\n")}
    >
      <strong><CompactNumberValue value={rowsRead} /> / <CompactNumberValue value={rowsWritten} /></strong>
      <small>{formatBytes(netBytes)}</small>
    </span>
  );
}

export function watermarkStatusLabel(row: Record<string, unknown>) {
  const classification = firstValue(row, ["movement_state"]);
  return hasValue(classification) ? String(classification) : "-";
}

export function DrawerWatermarkBadge({ row }: { row: Record<string, unknown> }) {
  const value = watermarkStatusLabel(row);
  const intent = watermarkIntent(value);
  const effective = row.source_watermark_effective;
  const detail = hasValue(effective) ? String(effective) : humanize(value);
  return <span className={`monitor-mini-badge badge-${intent}`} title={detail}>{humanize(value)}</span>;
}

export function watermarkIntent(value: string) {
  if (value === "advanced") return "good";
  if (value === "unchanged" || value === "incomplete" || value === "invalid") return "warning";
  return "neutral";
}
