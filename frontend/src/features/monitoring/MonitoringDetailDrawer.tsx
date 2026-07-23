import { ArrowLeft, ArrowRight, Boxes, BriefcaseBusiness, Check, ChevronRight, Clock3, Copy, FileText, SearchCheck, Workflow, X } from "lucide-react";
import { isValidElement, useEffect, useMemo, useState, type CSSProperties, type ReactNode } from "react";
import { createPortal } from "react-dom";
import type { MonitoringRecord } from "../../shared/api/types";
import { useDrawerEscape } from "../../shared/hooks/useDrawerEscape";
import { formatTimestampForDisplay, hasExplicitTimezone, isTimestampFieldName } from "../../shared/time";
import { lifecycleStatusFromField, lifecycleStatusPresentation, type LifecycleStatus } from "../../shared/statusPresentation";
import { LineageFormatIcon } from "../lineage/components/LineageFormatIcon";
import { DataTable, StatusCell, display, formatBytes, formatNumber, formatSeconds, num, type TableSort } from "./MonitoringCharts";
import {
  diagnosticsCategoryLabel,
  diagnosticsEvidenceItems,
  diagnosticsInvestigationActions,
  diagnosticsLinkedJobRow,
  diagnosticsRuleDescription,
  diagnosticsSeverityPresentation,
} from "./diagnosticsPresentation";
import { formatMaintenanceLag, maintenanceFormatIconKind, maintenanceTableHealthClass, maintenanceTableHealthLabel, maintenanceTableHealthTone } from "./maintenancePresentation";
import { formatPhasePercent, monitoringEndpointPresentation, TablePager } from "./monitoringShared";
import { SystemLogViewer } from "./SystemLogViewer";

export type MonitoringDetailKind = "job" | "dataflow" | "failure" | "performance" | "maintenance" | "freshness" | "volume" | "diagnostics";

interface MonitoringDetailDrawerProps {
  kind: MonitoringDetailKind;
  row: Record<string, unknown>;
  environmentId?: number | null;
  relatedDataflows?: MonitoringRecord[];
  relatedDataflowsTotal?: number;
  relatedDataflowsOffset?: number;
  relatedDataflowsLimit?: number;
  relatedDataflowsSort?: TableSort;
  relatedDataflowsLoading?: boolean;
  onRelatedDataflowsPageChange?: (offset: number) => void;
  onRelatedDataflowsPageSizeChange?: (limit: number) => void;
  onRelatedDataflowsSort?: (sort: TableSort) => void;
  reconciliationChecks?: Array<Record<string, string | number>>;
  timezoneName?: string | null;
  onOpenDataflow?: (row: MonitoringRecord) => void;
  onOpenJob?: (row: Record<string, unknown>) => void;
  onBack?: () => void;
  onClose: () => void;
}

const PRIMARY_FIELDS = [
  "job_id",
  "dataflow_run_id",
  "dataflow_id",
  "dataflow_name",
  "target",
  "stage",
  "status",
  "engine_name",
  "metadata_provider_name",
  "platform_name",
  "operation_type",
  "destination_operation_type",
  "start_time",
  "end_time",
  "duration_seconds",
];

const SOURCE_FIELDS = [
  "source_name",
  "source_connection_name",
  "source_connection_type",
  "source_format",
  "source_schema",
  "source_table",
  "source_path",
  "source_action",
  "source_rows_read",
  "source_duration_seconds",
  "source_status",
  "source_watermark_before",
  "source_watermark_after",
  "source_watermark_effective",
];

const TRANSFORM_FIELDS = [
  "transform_duration_seconds",
  "transform_status",
  "transformers_applied",
  "transform_error_message",
];

const DESTINATION_FIELDS = [
  "destination_name",
  "destination_connection_name",
  "destination_connection_type",
  "destination_format",
  "destination_schema",
  "destination_table",
  "destination_full_table",
  "destination_path",
  "destination_load_type",
  "destination_rows_written",
  "destination_rows_inserted",
  "destination_rows_updated",
  "destination_rows_deleted",
  "destination_files_added",
  "destination_files_removed",
  "destination_bytes_added",
  "destination_bytes_removed",
  "destination_bytes_saved",
  "destination_duration_seconds",
  "destination_status",
  "destination_operation_details",
];

const ERROR_FIELDS = [
  "failure_category",
  "failure_phase",
  "failure_message",
  "failure_signature",
  "error_message",
  "source_error_message",
  "transform_error_message",
  "destination_error_message",
  "last_error",
  "candidate_reason",
  "reason",
];

type DetailRow = [label: string, value: unknown, field?: string];
type SemanticIntent = "success" | "failed" | "skipped" | "running" | "pending" | "bad" | "neutral";
type DataflowPhaseKey = "source" | "transform" | "destination" | "overhead";
type SemanticValueModel =
  | { kind: "status"; value: string }
  | { kind: "count"; value: number; intent?: SemanticIntent }
  | { kind: "reconciliation"; status: string; mismatch: number }
  | { kind: "text"; value: string; intent?: SemanticIntent };

const SQL_BLOCK_FIELDS = new Set([
  "source_query",
  "source_filter_expression",
]);

const LIST_BLOCK_FIELDS = new Set([
  "source_watermark_columns",
  "transformers_applied",
  "destination_merge_keys",
]);

const JSON_BLOCK_FIELDS = new Set([
  "configure",
  "source_action",
  "source_configure",
  "source_watermark_before",
  "source_watermark_after",
  "source_watermark_effective",
  "transform_deduplicate_columns",
  "transform_latest_data_columns",
  "transform_filter_expression",
  "transform_additional_columns",
  "transform_schema_hints",
  "transform_configure",
  "destination_partition_columns",
  "destination_configure",
  "destination_operation_details",
]);

const DEFAULT_RELATED_DATAFLOW_SORT: TableSort = { sortBy: "start_time", sortDir: "desc" };

export function MonitoringDetailDrawer({
  kind,
  row,
  environmentId,
  relatedDataflows = [],
  relatedDataflowsTotal = relatedDataflows.length,
  relatedDataflowsOffset = 0,
  relatedDataflowsLimit = 100,
  relatedDataflowsSort = DEFAULT_RELATED_DATAFLOW_SORT,
  relatedDataflowsLoading = false,
  onRelatedDataflowsPageChange,
  onRelatedDataflowsPageSizeChange,
  onRelatedDataflowsSort,
  reconciliationChecks = [],
  timezoneName,
  onOpenDataflow,
  onOpenJob,
  onBack,
  onClose,
}: MonitoringDetailDrawerProps) {
  const title = detailTitle(row, kind);
  const [headerCopied, setHeaderCopied] = useState(false);
  const [systemLogsOpen, setSystemLogsOpen] = useState(false);

  useDrawerEscape(onClose, !systemLogsOpen);

  const sortableRelatedDataflows = useMemo(
    () => kind === "volume"
      ? relatedDataflows.map((item) => ({ ...item, volume_est_rows_written: volumeRunEstRowsWritten(item) }))
      : relatedDataflows,
    [kind, relatedDataflows]
  );
  const handleChildDataflowSort = (nextSort: TableSort) => {
    onRelatedDataflowsSort?.(nextSort);
  };
  const copyTitle = headerCopyValue(row, kind, title);
  const copyLabel = kind === "job" ? "job id" : kind === "dataflow" ? "dataflow run id" : `${kindLabel(kind)} title`;
  const jobId = typeof row.job_id === "string" ? row.job_id : "";
  const dataflowId = kind === "dataflow" && typeof row.dataflow_id === "string" ? row.dataflow_id : "";
  const drawerStatusClass = kind === "job" || kind === "dataflow" ? jobStatusTone(row.status) : "";
  const jobStatusPresentation = kind === "job" ? lifecycleStatusPresentation(row.status) : null;
  const jobStatusStyle = jobStatusPresentation ? {
    "--monitoring-job-status-surface": jobStatusPresentation.drawerSurface,
    "--monitoring-job-status-border": jobStatusPresentation.drawerBorder,
  } as CSSProperties : undefined;
  const freshnessHealthClass = kind === "freshness" ? ` is-freshness-${freshnessDrawerHealth(row).tone}` : "";
  const maintenanceHealthClass = kind === "maintenance" ? ` is-maintenance-health-${maintenanceTableHealthTone(row.table_health)}` : "";
  const diagnosticsHealthClass = kind === "diagnostics" ? ` is-diagnostics-${diagnosticsSeverityPresentation(row.severity).tone}` : "";
  return createPortal(
    <div className="metadata-drawer-backdrop monitoring-detail-backdrop" onMouseDown={onClose}>
      <aside className={`metadata-drawer monitoring-detail-drawer is-${kind}${drawerStatusClass ? ` ${drawerStatusClass}` : ""}${freshnessHealthClass}${maintenanceHealthClass}${diagnosticsHealthClass}`} style={jobStatusStyle} aria-label="Monitoring details" onMouseDown={(event) => event.stopPropagation()}>
        <header className="metadata-drawer-header">
          {onBack ? (
            <button className="icon-action monitoring-detail-back" type="button" aria-label="Back to previous monitoring detail" onClick={onBack}>
              <ArrowLeft size={18} />
            </button>
          ) : null}
          <div className="monitoring-detail-heading">
            {kind === "freshness" ? (
              <>
                <div className="monitoring-detail-title-row monitoring-freshness-title-row">
                  <Clock3 className="monitoring-detail-kind-icon" size={14} aria-hidden="true" />
                  <span className="monitoring-detail-kind-label">Freshness</span>
                  <h2>{title}</h2>
                  {copyTitle ? (
                    <button
                      className="icon-action monitoring-detail-copy"
                      type="button"
                      aria-label={`Copy ${copyLabel}`}
                      title={`Copy ${copyTitle}`}
                      onClick={(event) => {
                        event.stopPropagation();
                        void copyToClipboard(copyTitle, setHeaderCopied);
                      }}
                    >
                      {headerCopied ? <Check size={14} /> : <Copy size={14} />}
                    </button>
                  ) : null}
                </div>
                <FreshnessHeaderChips row={row} />
              </>
            ) : kind === "diagnostics" ? (
              <>
                <div className="monitoring-detail-kind-row monitoring-diagnostics-kind-row">
                  <SearchCheck className="monitoring-detail-kind-icon" size={15} aria-hidden="true" />
                  <span className="monitoring-detail-kind-label">Diagnostics finding</span>
                  <span className="monitoring-diagnostics-category">{diagnosticsCategoryLabel(row.category)}</span>
                  <DiagnosticsSeverityLabel value={String(row.severity ?? "info")} />
                </div>
                <div className="monitoring-detail-title-row">
                  <h2>{title}</h2>
                  {copyTitle ? (
                    <button
                      className="icon-action monitoring-detail-copy"
                      type="button"
                      aria-label={`Copy ${copyLabel}`}
                      title={`Copy ${copyTitle}`}
                      onClick={(event) => {
                        event.stopPropagation();
                        void copyToClipboard(copyTitle, setHeaderCopied);
                      }}
                    >
                      {headerCopied ? <Check size={14} /> : <Copy size={14} />}
                    </button>
                  ) : null}
                </div>
              </>
            ) : (
              <>
                <div className="monitoring-detail-kind-row">
                  {kind === "job" ? <BriefcaseBusiness className="monitoring-detail-kind-icon" size={14} aria-hidden="true" /> : null}
                  {kind === "dataflow" ? <Workflow className="monitoring-detail-kind-icon" size={14} aria-hidden="true" /> : null}
                  {kind === "volume" ? <Boxes className="monitoring-detail-kind-icon" size={14} aria-hidden="true" /> : null}
                  {kind === "maintenance" ? (
                    <LineageFormatIcon
                      kind={maintenanceFormatIconKind(row.destination_format ?? row.format ?? row.destination_connection_type)}
                      label={String(row.destination_format ?? row.format ?? "destination table")}
                      size={16}
                    />
                  ) : null}
                  <span className="monitoring-detail-kind-label">{kind === "job" ? "Job run" : kind === "dataflow" ? "Dataflow run" : kind === "volume" ? "Volume" : kind === "maintenance" ? "Destination table" : kindLabel(kind)}</span>
                  {kind === "job" || kind === "dataflow" ? <StatusCell row={row} /> : null}
                  {kind === "maintenance" ? <MaintenanceHealthChip health={row.table_health} reason={row.attention_reason} /> : null}
                </div>
                <div className="monitoring-detail-title-row">
                  <h2>{title}</h2>
                  {copyTitle ? (
                    <button
                      className="icon-action monitoring-detail-copy"
                      type="button"
                      aria-label={`Copy ${copyLabel}`}
                      title={`Copy ${copyTitle}`}
                      onClick={(event) => {
                        event.stopPropagation();
                        void copyToClipboard(copyTitle, setHeaderCopied);
                      }}
                    >
                      {headerCopied ? <Check size={14} /> : <Copy size={14} />}
                    </button>
                  ) : null}
                  {environmentId && jobId && kind !== "volume" && kind !== "maintenance" ? (
                    <button
                      className="icon-action monitoring-detail-copy"
                      type="button"
                      aria-label="View system logs"
                      title="View system logs"
                      onClick={(event) => {
                        event.stopPropagation();
                        setSystemLogsOpen(true);
                      }}
                    >
                      <FileText size={14} />
                    </button>
                  ) : null}
                </div>
                {kind === "maintenance" ? <MaintenanceHeaderContext row={row} title={title} /> : null}
              </>
            )}
          </div>
          <button className="icon-action" type="button" aria-label="Close monitoring details" onClick={onClose}>
            <X size={18} />
          </button>
        </header>
        <div className="metadata-drawer-body monitoring-detail-body">
          {kind === "job" ? (
            <>
              <JobDetailSections row={row} timezoneName={timezoneName} />
            </>
          ) : kind === "dataflow" ? (
            <DataflowDetailSections row={row} timezoneName={timezoneName} />
          ) : kind === "freshness" ? (
            <FreshnessDetailSections
              row={row}
              relatedDataflows={sortableRelatedDataflows}
              total={relatedDataflowsTotal}
              offset={relatedDataflowsOffset}
              limit={relatedDataflowsLimit}
              sort={relatedDataflowsSort}
              onSort={handleChildDataflowSort}
              onPageChange={(offset) => onRelatedDataflowsPageChange?.(offset)}
              onPageSizeChange={(limit) => onRelatedDataflowsPageSizeChange?.(limit)}
              onOpenDataflow={onOpenDataflow}
              timezoneName={timezoneName}
            />
          ) : kind === "volume" ? (
            <VolumeDetailSections
              row={row}
              relatedDataflows={sortableRelatedDataflows}
              total={relatedDataflowsTotal}
              offset={relatedDataflowsOffset}
              limit={relatedDataflowsLimit}
              sort={relatedDataflowsSort}
              onSort={handleChildDataflowSort}
              onPageChange={(offset) => onRelatedDataflowsPageChange?.(offset)}
              onPageSizeChange={(limit) => onRelatedDataflowsPageSizeChange?.(limit)}
              onOpenDataflow={onOpenDataflow}
              timezoneName={timezoneName}
            />
          ) : kind === "maintenance" ? (
            <MaintenanceDetailSections
              row={row}
              relatedDataflows={sortableRelatedDataflows}
              total={relatedDataflowsTotal}
              loading={relatedDataflowsLoading}
              offset={relatedDataflowsOffset}
              limit={relatedDataflowsLimit}
              sort={relatedDataflowsSort}
              onSort={handleChildDataflowSort}
              onPageChange={(offset) => onRelatedDataflowsPageChange?.(offset)}
              onPageSizeChange={(limit) => onRelatedDataflowsPageSizeChange?.(limit)}
              onOpenDataflow={onOpenDataflow}
              timezoneName={timezoneName}
            />
          ) : kind === "diagnostics" ? (
            <DiagnosticsDetailSections row={row} timezoneName={timezoneName} onOpenJob={onOpenJob} />
          ) : (
            <DetailSection title="Detail" row={row} fields={PRIMARY_FIELDS} timezoneName={timezoneName} />
          )}
          {kind === "dataflow" && row.job_id ? (
            <LinkedJobSection row={row} onOpenJob={onOpenJob} />
          ) : null}
          {kind !== "job" && kind !== "dataflow" && kind !== "freshness" && kind !== "volume" && kind !== "maintenance" && kind !== "diagnostics" ? (
            <>
              <DetailSection title="Source runtime" row={row} fields={SOURCE_FIELDS} timezoneName={timezoneName} />
              <DetailSection title="Transform runtime" row={row} fields={TRANSFORM_FIELDS} timezoneName={timezoneName} />
              <DetailSection title="Destination runtime" row={row} fields={DESTINATION_FIELDS} timezoneName={timezoneName} />
              <DetailSection title="Watermark" row={row} fields={WATERMARK_FIELDS} wide timezoneName={timezoneName} />
              <DetailSection title="Errors and notes" row={row} fields={ERROR_FIELDS} wide timezoneName={timezoneName} />
            </>
          ) : null}
          {kind === "job" && (relatedDataflows.length || relatedDataflowsLoading) ? (
            <section className="monitoring-detail-section">
              <div className="monitoring-detail-section-header monitoring-freshness-runs-header monitoring-child-dataflows-section-header">
                <div className="monitoring-child-dataflows-header">
                  <h3>Child dataflows</h3>
                </div>
                <TablePager
                  limit={relatedDataflowsLimit}
                  offset={relatedDataflowsOffset}
                  loadedRows={relatedDataflows.length}
                  totalRows={relatedDataflowsTotal}
                  loading={relatedDataflowsLoading}
                  onPageChange={(offset) => onRelatedDataflowsPageChange?.(offset)}
                  onPageSizeChange={(limit) => onRelatedDataflowsPageSizeChange?.(limit)}
                />
              </div>
              <DataTable
                rows={sortableRelatedDataflows}
                columns={[
                  { key: "dataflow_name", label: "Dataflow", sortable: true, width: 160 },
                  { key: "stage", label: "Stage", sortable: true, autoFit: true, minWidth: 64, maxWidth: 132 },
                  { key: "operation_type", label: "Operation", sortable: true, autoFit: true, minWidth: 72, maxWidth: 116 },
                  { key: "status", label: "Status", sortable: true, autoFit: true, minWidth: 76, maxWidth: 104, render: (child) => <StatusCell row={child} /> },
                  { key: "duration_seconds", label: "Duration", sortable: true, autoFit: true, minWidth: 82, maxWidth: 112, render: (child) => formatSeconds(num(child, "duration_seconds")) },
                  { key: "source_rows_read", label: "Rows read", sortable: true, autoFit: true, minWidth: 82, maxWidth: 128 },
                  { key: "destination_rows_written", label: "Rows written", sortable: true, autoFit: true, minWidth: 98, maxWidth: 144 },
                  { key: "error_preview", label: "Issue", sortable: true, width: 240, className: "monitoring-child-issue-column", render: (child) => <IssueCell row={child} /> },
                ]}
                maxRows={relatedDataflowsLimit}
                offset={0}
                onRowClick={onOpenDataflow}
                sort={relatedDataflowsSort}
                onSort={handleChildDataflowSort}
                fixedLayout
                className="monitoring-child-dataflows-table"
                timezoneName={timezoneName}
              />
            </section>
          ) : null}
          {kind === "job" && reconciliationChecks.length ? (
            <section className="monitoring-detail-section">
              <h3>Reconciliation checks</h3>
              <DataTable
                rows={reconciliationChecks}
                columns={[
                  { key: "severity", label: "Severity" },
                  { key: "metric", label: "Metric" },
                  { key: "expected", label: "Expected" },
                  { key: "observed", label: "Observed" },
                  { key: "difference", label: "Difference" },
                ]}
                maxRows={20}
              />
            </section>
          ) : null}
          <RawPayloadSection row={row} label={kind === "diagnostics" ? "Full evidence" : "Raw payload"} />
        </div>
      </aside>
      {systemLogsOpen && environmentId && jobId ? (
        <SystemLogViewer
          environmentId={environmentId}
          jobId={jobId}
          dataflowId={dataflowId || undefined}
          timezoneName={timezoneName}
          onClose={() => setSystemLogsOpen(false)}
        />
      ) : null}
    </div>,
    document.body
  );
}

const WATERMARK_FIELDS = [
  "movement_state",
  "source_watermark_before",
  "source_watermark_after",
  "source_watermark_effective",
  "source_watermark_columns",
];

function firstValue(row: Record<string, unknown>, fields: string[]) {
  for (const field of fields) {
    if (hasValue(row[field])) return row[field];
  }
  return null;
}

function jobStatusTone(value: unknown) {
  const status = String(value ?? "unknown").trim().toLowerCase();
  if (status === "succeeded" || status === "success") return "is-succeeded";
  if (status === "failed" || status === "error") return "is-failed";
  if (status === "skipped" || status === "warning") return "is-skipped";
  if (status === "running") return "is-running";
  if (status === "pending") return "is-pending";
  return "is-unknown";
}

export function phaseRuntimeStatusClass(value: unknown) {
  return `monitoring-dataflow-runtime-card ${jobStatusTone(value)}`;
}

function reconciliationSummary(row: Record<string, unknown>) {
  const status = row.reconciliation_status;
  const mismatch = row.reconciliation_mismatch_count;
  if (!hasValue(status) && !hasValue(mismatch)) return null;
  return {
    kind: "reconciliation",
    status: hasValue(status) ? String(status) : "-",
    mismatch: Number(mismatch) || 0,
  };
}

function JobDetailSections({
  row,
  timezoneName,
}: {
  row: Record<string, unknown>;
  timezoneName?: string | null;
}) {
  const operationTypes = formatListLikeValue(row.operation_types) || "-";
  return (
    <>
      <section className="monitoring-detail-section">
        <h3>Job run</h3>
        <div className="monitoring-job-detail-grid">
          <GroupedDetailCard
            title="Identity"
            className="monitoring-job-identity-card"
            rows={[
              ["Workspace ID", row.workspace_id],
              ["Num", firstValue(row, ["job_num", "job_number", "num"])],
              ["Index", row.job_index],
              ["Operation", operationTypes],
            ]}
            showEmpty
            timezoneName={timezoneName}
          />
          <GroupedDetailCard
            title="Execution"
            className="monitoring-job-execution-card"
            rows={[
              ["Status", <StatusCell row={row} />],
              ["Start", row.start_time, "start_time"],
              ["End", row.end_time, "end_time"],
              ["Duration", row.duration_seconds, "duration_seconds"],
            ]}
            timezoneName={timezoneName}
          />
          <GroupedDetailCard
            title="Runtime context"
            className="monitoring-job-runtime-card"
            rows={[
              ["Platform", row.platform_name],
              ["Engine", row.engine_name],
              ["Provider", row.metadata_provider_name],
              ["Watermark manager", row.watermark_manager_name],
            ]}
            showEmpty
            timezoneName={timezoneName}
          />
          <GroupedDetailCard
            title="Run config"
            className="monitoring-job-config-card"
            rows={[
              ["Dry run", row.dry_run],
              ["Stop on error", row.stop_on_error],
              ["Max workers", row.max_workers],
              ["Retry count", row.retry_count],
              ["Retry delay", row.retry_delay],
              ["Retention hours", row.retention_hours],
            ]}
            timezoneName={timezoneName}
          />
          {hasValue(row.stages) ? (
            <div className="monitoring-job-group-card monitoring-job-stages">
              <span>Stages</span>
              <JsonBlock value={row.stages} compactArray />
            </div>
          ) : null}
        </div>
      </section>

      <section className="monitoring-detail-section">
        <h3>Job summary</h3>
        <div className="monitoring-job-detail-grid">
          <GroupedDetailCard
            title="Dataflow totals"
            rows={[
              ["Total", row.total_dataflows ?? row.child_dataflow_count, "total_dataflows"],
              ["Succeeded", row.total_succeeded ?? row.child_succeeded_count, "total_succeeded"],
              ["Failed", row.total_failed ?? row.child_failed_count, "total_failed"],
              ["Skipped", row.total_skipped ?? row.child_skipped_count, "total_skipped"],
              ["Running", row.total_running, "total_running"],
              ["Pending", row.total_pending, "total_pending"],
              ["Reconciliation", reconciliationSummary(row)],
              ["Child P95 duration", row.child_p95_duration_seconds, "child_p95_duration_seconds"],
            ]}
            timezoneName={timezoneName}
          />
          <GroupedDetailCard
            title="Row totals"
            rows={[
              ["Read", row.total_rows_read ?? row.child_total_rows_read, "total_rows_read"],
              ["Written", row.total_rows_written ?? row.child_total_rows_written, "total_rows_written"],
              ["Inserted", row.total_rows_inserted ?? row.child_total_rows_inserted, "total_rows_inserted"],
              ["Updated", row.total_rows_updated ?? row.child_total_rows_updated, "total_rows_updated"],
              ["Deleted", row.total_rows_deleted, "total_rows_deleted"],
            ]}
            timezoneName={timezoneName}
          />
          <GroupedDetailCard
            title="File and byte totals"
            rows={[
              ["Files added", row.total_files_added],
              ["Files removed", row.total_files_removed],
              ["Bytes added", row.total_bytes_added, "total_bytes_added"],
              ["Bytes removed", row.total_bytes_removed, "total_bytes_removed"],
            ]}
            timezoneName={timezoneName}
          />
        </div>
      </section>

      <ErrorMessageSection row={row} timezoneName={timezoneName} />
    </>
  );
}

function formatListLikeValue(value: unknown) {
  if (!hasValue(value)) return "";
  if (Array.isArray(value)) return value.map((item) => String(item ?? "").trim()).filter(Boolean).join(", ");
  const textValue = String(value).trim();
  if (!textValue) return "";
  if (looksJson(textValue)) {
    try {
      const parsed = JSON.parse(textValue);
      if (Array.isArray(parsed)) return parsed.map((item) => String(item ?? "").trim()).filter(Boolean).join(", ");
    } catch {
      return textValue;
    }
  }
  return textValue.split(",").map((item) => item.trim()).filter(Boolean).join(", ");
}

function DataflowRunSummary({ row, timezoneName }: { row: Record<string, unknown>; timezoneName?: string | null }) {
  const source = dataflowEndpointSummary(row, "source");
  const destination = dataflowEndpointSummary(row, "destination");
  const loadType = firstValue(row, ["destination_load_type", "load_type", "destination_operation_type"]);
  const bytesAdded = num(row, "destination_bytes_added");
  const bytesRemoved = num(row, "destination_bytes_removed");
  const bottleneck = dataflowPhaseBottleneck(row);
  return (
    <section className="monitoring-detail-section monitoring-dataflow-summary-section">
      <div className="monitoring-dataflow-route-card">
        <DataflowRouteEndpoint direction="source" endpoint={source} />
        <ArrowRight className="monitoring-dataflow-route-arrow" size={18} aria-hidden="true" />
        <DataflowRouteEndpoint direction="destination" endpoint={destination} />
        <div className="monitoring-dataflow-route-context">
          <span>{String(row.stage || "unknown stage")}</span>
          <span>{String(row.operation_type || "unknown operation")}</span>
          <span>{String(loadType || "unknown load type")}</span>
        </div>
      </div>
      <div className="monitoring-dataflow-summary-grid">
        <GroupedDetailCard
          title="Execution"
          className="monitoring-dataflow-execution-card"
          rows={[
            ["Status", <StatusCell row={row} />],
            ["Run ID", row.dataflow_run_id],
            ["Start", row.start_time, "start_time"],
            ["End", row.end_time, "end_time"],
            ["Duration", row.duration_seconds, "duration_seconds"],
            ["Retry attempts", row.retry_attempts],
          ]}
          showEmpty
          timezoneName={timezoneName}
        />
        <GroupedDetailCard
          title="Phase health"
          className={`monitoring-dataflow-phase-card${bottleneck ? ` phase-health-${bottleneck.phase}` : ""}`}
          rows={[
            ["Bottleneck", <DataflowPhaseBottleneck row={row} />],
            ["Contribution", <DataflowPhaseContribution row={row} />, "phase_contribution"],
            ["Source", row.source_duration_seconds, "source_duration_seconds"],
            ["Transform", row.transform_duration_seconds, "transform_duration_seconds"],
            ["Destination", row.destination_duration_seconds, "destination_duration_seconds"],
            ["Overhead", row.overhead_duration_seconds, "overhead_duration_seconds"],
          ]}
          showEmpty
          timezoneName={timezoneName}
        />
        <GroupedDetailCard
          title="Workload"
          className="monitoring-dataflow-workload-card"
          rows={[
            ["Rows read", row.source_rows_read, "source_rows_read"],
            ["Rows written", row.destination_rows_written, "destination_rows_written"],
            ["Bytes added", row.destination_bytes_added, "destination_bytes_added"],
            ["Bytes removed", row.destination_bytes_removed, "destination_bytes_removed"],
            ["Net bytes", bytesAdded - bytesRemoved, "net_bytes"],
            ["Watermark", row.movement_state],
          ]}
          showEmpty
          timezoneName={timezoneName}
        />
      </div>
    </section>
  );
}

function dataflowEndpointSummary(row: Record<string, unknown>, direction: "source" | "destination") {
  const endpoint = monitoringEndpointPresentation(row as MonitoringRecord, direction);
  return {
    asset: endpoint.locator,
    connection: endpoint.connection,
    format: endpoint.format || String(firstValue(row, [`${direction}_connection_type`]) || "unknown format"),
  };
}

function DataflowRouteEndpoint({
  direction,
  endpoint,
}: {
  direction: "source" | "destination";
  endpoint: { asset: string; connection: string; format: string };
}) {
  return (
    <div className={`monitoring-dataflow-route-endpoint is-${direction}`}>
      <span>{direction}</span>
      <strong>{endpoint.asset}</strong>
      <small>{endpoint.connection} · {endpoint.format}</small>
    </div>
  );
}

function DataflowDetailSections({ row, timezoneName }: { row: Record<string, unknown>; timezoneName?: string | null }) {
  return (
    <>
      <DataflowRunSummary row={row} timezoneName={timezoneName} />

      {hasPerformanceEvidence(row) ? <PerformanceDetailSections row={row} /> : null}

      <FailureEvidenceSections row={row} timezoneName={timezoneName} />

      <section className="monitoring-detail-section monitoring-dataflow-section monitoring-dataflow-section-configuration">
        <h3>Dataflow configuration</h3>
        <div className="monitoring-dataflow-detail-grid">
          <GroupedDetailCard
            title="Identity and configuration"
            className="monitoring-dataflow-config-card"
            rows={[
              ["Dataflow ID", row.dataflow_id],
              ["Workspace ID", row.workspace_id],
              ["Name", row.dataflow_name],
              ["Description", row.dataflow_description],
              ["Stage", row.stage],
              ["Group number", row.group_number],
              ["Execution order", row.execution_order],
              ["Processing mode", row.processing_mode],
              ["Active", row.is_active],
              ["Configure", row.configure, "configure"],
            ]}
            showEmpty
            timezoneName={timezoneName}
          />
        </div>
      </section>

      <section className="monitoring-detail-section monitoring-dataflow-section monitoring-dataflow-section-source">
        <h3>Source</h3>
        <div className="monitoring-dataflow-detail-grid">
          <GroupedDetailCard
            title="Master data"
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
              ["Watermark columns", row.source_watermark_columns, "source_watermark_columns"],
              ["Filter", row.source_filter_expression, "source_filter_expression"],
              ["Configure", row.source_configure, "source_configure"],
            ]}
            showEmpty
            timezoneName={timezoneName}
          />
          <GroupedDetailCard
            title="Runtime info"
            className={phaseRuntimeStatusClass(row.source_status)}
            rows={[
              ["Start", row.source_start_time, "source_start_time"],
              ["End", row.source_end_time, "source_end_time"],
              ["Duration", row.source_duration_seconds, "source_duration_seconds"],
              ["Status", row.source_status, "source_status"],
              ["Error", row.source_error_message, "source_error_message"],
              ["Rows read", row.source_rows_read, "source_rows_read"],
              ["Action", row.source_action, "source_action"],
              ["Watermark before", row.source_watermark_before, "source_watermark_before"],
              ["Watermark after", row.source_watermark_after, "source_watermark_after"],
              ["Watermark effective", row.source_watermark_effective, "source_watermark_effective"],
            ]}
            showEmpty
            timezoneName={timezoneName}
          />
        </div>
      </section>

      <section className="monitoring-detail-section monitoring-dataflow-section monitoring-dataflow-section-transform">
        <h3>Transform</h3>
        <div className="monitoring-dataflow-detail-grid">
          <GroupedDetailCard
            title="Master data"
            rows={[
              ["Deduplicate", row.transform_deduplicate_columns, "transform_deduplicate_columns"],
              ["Latest data", row.transform_latest_data_columns, "transform_latest_data_columns"],
              ["Filter", row.transform_filter_expression, "transform_filter_expression"],
              ["Additional columns", row.transform_additional_columns, "transform_additional_columns"],
              ["Schema hints", row.transform_schema_hints, "transform_schema_hints"],
              ["Configure", row.transform_configure, "transform_configure"],
            ]}
            showEmpty
            timezoneName={timezoneName}
          />
          <GroupedDetailCard
            title="Runtime info"
            className={phaseRuntimeStatusClass(row.transform_status)}
            rows={[
              ["Start", row.transform_start_time, "transform_start_time"],
              ["End", row.transform_end_time, "transform_end_time"],
              ["Duration", row.transform_duration_seconds, "transform_duration_seconds"],
              ["Status", row.transform_status, "transform_status"],
              ["Error", row.transform_error_message, "transform_error_message"],
              ["Transformers applied", row.transformers_applied, "transformers_applied"],
            ]}
            showEmpty
            timezoneName={timezoneName}
          />
        </div>
      </section>

      <section className="monitoring-detail-section monitoring-dataflow-section monitoring-dataflow-section-destination">
        <h3>Destination</h3>
        <div className="monitoring-dataflow-detail-grid">
          <GroupedDetailCard
            title="Master data"
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
          <GroupedDetailCard
            title="Runtime info"
            className={phaseRuntimeStatusClass(row.destination_status)}
            rows={[
              ["Start", row.destination_start_time, "destination_start_time"],
              ["End", row.destination_end_time, "destination_end_time"],
              ["Duration", row.destination_duration_seconds, "destination_duration_seconds"],
              ["Status", row.destination_status, "destination_status"],
              ["Error", row.destination_error_message, "destination_error_message"],
              ["Operation type", row.destination_operation_type],
              ["Written", row.destination_rows_written, "destination_rows_written"],
              ["Inserted", row.destination_rows_inserted, "destination_rows_inserted"],
              ["Updated", row.destination_rows_updated, "destination_rows_updated"],
              ["Deleted", row.destination_rows_deleted, "destination_rows_deleted"],
              ["Files added", row.destination_files_added],
              ["Files removed", row.destination_files_removed],
              ["Bytes added", row.destination_bytes_added, "destination_bytes_added"],
              ["Bytes removed", row.destination_bytes_removed, "destination_bytes_removed"],
              ["Bytes saved", row.destination_bytes_saved, "destination_bytes_saved"],
              ["Operation details", row.destination_operation_details, "destination_operation_details"],
            ]}
            showEmpty
            timezoneName={timezoneName}
          />
        </div>
      </section>
    </>
  );
}

function hasPerformanceEvidence(row: Record<string, unknown>) {
  return [
    row.performance_bottleneck_phase,
    row.performance_candidate_code,
    row.performance_candidate_reason,
    row.performance_candidate_priority,
    row.performance_rows_processed,
    row.performance_rows_per_second,
    row.performance_overhead_ratio,
    row.performance_dominant_phase_ratio,
  ].some(hasValue);
}

function MaintenanceDetailSections({
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

function MaintenanceHeaderContext({ row, title }: { row: Record<string, unknown>; title: string }) {
  const canonicalTarget = String(row.target ?? title);
  const connection = String(firstValue(row, ["destination_name", "destination_connection_name"]) ?? "unknown connection");
  const connectionType = String(row.destination_connection_type ?? "unknown");
  const format = String(row.destination_format ?? row.format ?? "table");
  const normalize = (value: string) => value.replace(/`/g, "").trim().toLowerCase();
  const showCanonicalTarget = Boolean(canonicalTarget && normalize(canonicalTarget) !== normalize(title));
  return (
    <div className="monitoring-maintenance-header-context">
      <span>{connection} · {connectionType} · {format}</span>
      {showCanonicalTarget ? <code title={canonicalTarget}>{canonicalTarget}</code> : null}
    </div>
  );
}

function MaintenanceHealthChip({ health, reason }: { health: unknown; reason?: unknown }) {
  return (
    <span className={`maintenance-table-health-chip ${maintenanceTableHealthClass(health)}`} title={String(reason ?? maintenanceTableHealthLabel(health))}>
      {maintenanceTableHealthLabel(health)}
    </span>
  );
}

function MaintenanceMetricValue({ tone, value }: { tone: "reclaim" | "files" | "warning"; value: string }) {
  return <span className={`monitoring-maintenance-metric-value is-${tone}`}>{value}</span>;
}

function MaintenanceRelatedLoading() {
  return <div className="table-empty monitoring-maintenance-related-loading">Loading related evidence…</div>;
}

function isMaintenanceRun(row: Record<string, unknown>) {
  const operationType = String(row.operation_type ?? "").toLowerCase();
  const destinationOperationType = String(row.destination_operation_type ?? "").toLowerCase();
  return operationType === "maintenance" || ["compact", "cleanup", "maintenance"].includes(destinationOperationType);
}

function MaintenanceDataflowCell({ row }: { row: Record<string, unknown> }) {
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

function MaintenanceSourceCell({ row }: { row: Record<string, unknown> }) {
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

function MaintenanceLatestCell({ row, timezoneName }: { row: Record<string, unknown>; timezoneName?: string | null }) {
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

function maintenanceStatusTextClass(status: string) {
  const normalized = status.toLowerCase();
  if (normalized === "succeeded") return "is-succeeded";
  if (normalized === "failed") return "is-failed";
  if (normalized === "skipped") return "is-skipped";
  if (normalized === "running") return "is-running";
  if (normalized === "pending") return "is-pending";
  return "is-unknown";
}

function MaintenanceContributingVolumeCell({ row }: { row: Record<string, unknown> }) {
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

function MaintenanceRunVolumeCell({ row }: { row: Record<string, unknown> }) {
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

function maintenanceSourceParts(row: Record<string, unknown>) {
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

function DiagnosticsDetailSections({
  row,
  timezoneName,
  onOpenJob,
}: {
  row: Record<string, unknown>;
  timezoneName?: string | null;
  onOpenJob?: (row: Record<string, unknown>) => void;
}) {
  const evidence = evidenceObject(row.evidence);
  const category = String(row.category ?? "diagnostics");
  const actionItems = diagnosticsInvestigationActions(row, evidence);
  const evidenceItems = diagnosticsEvidenceItems(category, row, evidence);
  const linkedJob = diagnosticsLinkedJobRow(row, evidence);
  return (
    <>
      <section className="monitoring-detail-section monitoring-diagnostics-finding">
        <h3>What happened</h3>
        <div className="monitoring-diagnostics-finding-callout">
          <strong>{display(row, "issue")}</strong>
          <p>{diagnosticsRuleDescription(category)}</p>
          {hasValue(row.latest_time) ? (
            <small>Latest observed · {renderGroupedValue(row.latest_time, timezoneName, "latest_time")}</small>
          ) : null}
        </div>
      </section>

      <section className="monitoring-detail-section">
        <h3>Evidence</h3>
        <div className="monitoring-diagnostics-evidence-grid">
          {evidenceItems.map((item) => (
            <div
              key={item.label}
              className={`monitoring-diagnostics-evidence-card${item.intent && item.intent !== "neutral" ? ` is-${item.intent}` : ""}${item.wide ? " is-wide" : ""}${item.primary ? " is-primary" : ""}`}
            >
              <span>{item.label}</span>
              <strong>{renderGroupedValue(item.value, timezoneName, item.field)}</strong>
            </div>
          ))}
        </div>
      </section>

      {linkedJob ? <DiagnosticsLinkedJobSection row={linkedJob} onOpenJob={onOpenJob} /> : null}

      {actionItems.length ? (
        <section className="monitoring-detail-section">
          <h3>Investigation path</h3>
          <ol className="monitoring-diagnostics-action-list">
            {actionItems.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ol>
        </section>
      ) : null}
    </>
  );
}

function DiagnosticsSeverityLabel({ value }: { value: string }) {
  const presentation = diagnosticsSeverityPresentation(value);
  return <span className={`diagnostics-severity diagnostics-${presentation.tone}`}>{presentation.label}</span>;
}

function evidenceObject(value: unknown): Record<string, unknown> {
  if (value && typeof value === "object" && !Array.isArray(value)) return value as Record<string, unknown>;
  if (typeof value === "string" && looksJson(value)) {
    const parsed = parseJsonLike(value);
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) return parsed as Record<string, unknown>;
  }
  return {};
}

type FreshnessDrawerHealthTone = "success" | "warning" | "failed" | "neutral";

export function freshnessDrawerHealth(row: Record<string, unknown>): { label: string; tone: FreshnessDrawerHealthTone } {
  const hasEvidence = hasValue(row.latest_freshness_at) || hasValue(row.latest_run_at) || num(row, "run_count") > 0;
  if (!hasEvidence) return { label: "No evidence", tone: "neutral" };
  const latestStatus = String(firstValue(row, ["latest_run_status", "latest_freshness_status"]) ?? "").trim().toLowerCase();
  const watermarkState = String(firstValue(row, ["movement_state", "coverage_state"]) ?? "").trim().toLowerCase();
  if (latestStatus === "failed" || watermarkState === "invalid") return { label: "Needs review", tone: "failed" };
  if (["running", "pending"].includes(latestStatus) || watermarkState === "incomplete") return { label: "Needs review", tone: "warning" };
  const ageDays = Number(row.age_days ?? (Number(row.age_seconds) / 86_400));
  if (Number.isFinite(ageDays) && ageDays > 7) return { label: "Stale", tone: "warning" };
  return { label: "Current", tone: "success" };
}

function FreshnessHeaderChips({ row }: { row: Record<string, unknown> }) {
  const health = freshnessDrawerHealth(row);
  const latestStatus = String(firstValue(row, ["latest_run_status", "latest_freshness_status"]) ?? "unknown").toLowerCase();
  const watermarkState = String(firstValue(row, ["movement_state", "coverage_state"]) ?? "not_configured").toLowerCase();
  return (
    <div className="monitoring-freshness-header-chips" aria-label="Freshness status summary">
      <span className={`monitoring-freshness-header-chip is-${health.tone}`}>{health.label}</span>
      <span className="monitoring-freshness-header-chip">age: {formatFreshnessAge(row.age_seconds, row.age_days)}</span>
      <span className={`monitoring-freshness-header-chip is-${statusHeaderTone(latestStatus)}`}>latest: {humanize(latestStatus)}</span>
      <span className={`monitoring-freshness-header-chip is-${watermarkHeaderTone(watermarkState)}`}>watermark: {humanize(watermarkState)}</span>
    </div>
  );
}

function statusHeaderTone(status: string): FreshnessDrawerHealthTone {
  if (status === "succeeded") return "success";
  if (status === "failed") return "failed";
  if (["skipped", "running", "pending"].includes(status)) return "warning";
  return "neutral";
}

function watermarkHeaderTone(status: string): FreshnessDrawerHealthTone {
  if (["advanced", "initialized"].includes(status)) return "success";
  if (["unchanged", "incomplete"].includes(status)) return "warning";
  if (status === "invalid") return "failed";
  return "neutral";
}

function VolumeDetailSections({
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
  const hasVolumeEvidence = num(row, "candidate_run_count") > 0
    || hasValue(row.volume_candidate_reason)
    || (Array.isArray(row.volume_candidate_signals) && row.volume_candidate_signals.length > 0);
  return (
    <>
      <FreshnessIdentitySection row={row} />
      <section className="monitoring-detail-section monitoring-volume-summary-section">
        <h3>Volume summary</h3>
        <div className="monitoring-freshness-summary-grid monitoring-volume-summary-grid">
          <GroupedDetailCard
            title="Workload totals"
            rows={[
              ["Runs", formatNumber(num(row, "run_count"))],
              ["Rows read", formatNumber(num(row, "volume_rows_read"))],
              ["Est rows written", formatNumber(num(row, "volume_est_rows_written"))],
              ["Observed lakehouse rows", formatNumber(num(row, "volume_lakehouse_rows_written"))],
            ]}
            showEmpty
            className="monitoring-volume-summary-card is-workload"
          />
          <GroupedDetailCard
            title="Lakehouse changes"
            rows={[
              ["Inserted / updated / deleted", <VolumeAggregateRowChanges row={row} />],
              ["Files added / removed", <VolumeAggregateFiles row={row} />],
              ["Bytes added / removed", <VolumeAggregateBytes row={row} />],
              ["Net bytes", <VolumeNetBytes value={num(row, "volume_net_bytes")} />],
            ]}
            showEmpty
            className="monitoring-volume-summary-card is-storage"
          />
          <GroupedDetailCard
            title="Per-run profile"
            rows={[
              ["Average rows read", formatNumber(num(row, "avg_rows_read"))],
              ["Average est rows written", formatNumber(num(row, "avg_est_rows_written"))],
              ["Peak rows read", formatNumber(num(row, "peak_rows_read"))],
              ["P95 rows read", formatNumber(num(row, "p95_rows_read"))],
              ["Average duration", formatSeconds(num(row, "avg_duration_seconds"))],
              ["P95 duration", formatSeconds(num(row, "p95_duration_seconds"))],
            ]}
            showEmpty
            className="monitoring-volume-summary-card is-profile"
          />
          <GroupedDetailCard
            title="Volume evidence"
            rows={[
              ["Primary signal", row.volume_candidate_reason || "No P95 aggregate signal"],
              ["Candidate runs", formatNumber(num(row, "candidate_run_count"))],
              ["Matched signals", <VolumeSignalList value={row.volume_candidate_signals} />],
            ]}
            showEmpty
            className={`monitoring-volume-summary-card is-evidence${hasVolumeEvidence ? " has-evidence" : ""}`}
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
          columns={[
            { key: "start_time", label: "Time", sortable: true, autoFit: true, minWidth: 144, maxWidth: 216, render: (run) => <FreshnessRunTimeCell row={run} timezoneName={timezoneName} /> },
            { key: "status", label: "Status", sortable: true, autoFit: true, minWidth: 76, maxWidth: 104, render: (run) => <StatusCell row={run} /> },
            { key: "source_rows_read", label: "Rows read", sortable: true, autoFit: true, minWidth: 82, maxWidth: 128, render: (run) => formatNumber(num(run, "source_rows_read")) },
            { key: "volume_est_rows_written", label: "Est rows written", sortable: true, autoFit: true, minWidth: 104, maxWidth: 148, render: (run) => formatNumber(num(run, "volume_est_rows_written")) },
            { key: "destination_rows_inserted", label: "Row changes", sortable: true, autoFit: true, minWidth: 104, maxWidth: 148, render: (run) => <VolumeRunRowChangesCell row={run} /> },
            { key: "destination_files_added", label: "Files + / −", sortable: true, autoFit: true, minWidth: 84, maxWidth: 112, render: (run) => <VolumeRunFilesCell row={run} /> },
            { key: "destination_bytes_added", label: "Net bytes", sortable: true, autoFit: true, minWidth: 86, maxWidth: 124, render: (run) => formatBytes(num(run, "destination_bytes_added") - num(run, "destination_bytes_removed")) },
            { key: "duration_seconds", label: "Duration", sortable: true, autoFit: true, minWidth: 76, maxWidth: 104, render: (run) => formatSeconds(num(run, "duration_seconds")) },
          ]}
          maxRows={limit}
          offset={0}
          onRowClick={onOpenDataflow}
          sort={sort}
          onSort={onSort}
          fixedLayout
          className="monitoring-child-dataflows-table monitoring-volume-run-table"
          timezoneName={timezoneName}
        />
      </section>
    </>
  );
}

function VolumeAggregateRowChanges({ row }: { row: Record<string, unknown> }) {
  const inserted = formatNumber(num(row, "volume_rows_inserted"));
  const updated = formatNumber(num(row, "volume_rows_updated"));
  const deleted = formatNumber(num(row, "volume_rows_deleted"));
  return (
    <span className="monitoring-volume-change-values" title={`Inserted / updated / deleted: ${inserted} / ${updated} / ${deleted}`}>
      <span className="is-positive">{inserted}</span><i>/</i>
      <span className="is-warning">{updated}</span><i>/</i>
      <span className="is-negative">{deleted}</span>
    </span>
  );
}

function VolumeAggregateFiles({ row }: { row: Record<string, unknown> }) {
  const added = formatNumber(num(row, "volume_files_added"));
  const removed = formatNumber(num(row, "volume_files_removed"));
  return (
    <span className="monitoring-volume-change-values" title={`Files added / removed: ${added} / ${removed}`}>
      <span className="is-positive">{added}</span><i>/</i>
      <span className="is-negative">{removed}</span>
    </span>
  );
}

function VolumeAggregateBytes({ row }: { row: Record<string, unknown> }) {
  const added = formatBytes(num(row, "volume_bytes_added"));
  const removed = formatBytes(num(row, "volume_bytes_removed"));
  return (
    <span className="monitoring-volume-change-values" title={`Bytes added / removed: ${added} / ${removed}`}>
      <span className="is-positive">{added}</span><i>/</i>
      <span className="is-negative">{removed}</span>
    </span>
  );
}

function VolumeNetBytes({ value }: { value: number }) {
  const tone = value > 0 ? "is-positive" : value < 0 ? "is-negative" : "is-neutral";
  return <span className={`monitoring-volume-net-bytes ${tone}`}>{formatBytes(value)}</span>;
}

function VolumeSignalList({ value }: { value: unknown }) {
  if (!Array.isArray(value) || !value.length) return <span>-</span>;
  return (
    <span className="monitoring-volume-signal-list">
      {value.map((signal, index) => {
        const item = signal && typeof signal === "object" ? signal as Record<string, unknown> : {};
        const label = String(item.label ?? item.kind ?? "Signal");
        const ratio = Number(item.ratio ?? 0);
        return <span key={`${label}-${index}`}>{label}{ratio > 0 ? ` · ${ratio.toFixed(2)}× P95` : ""}</span>;
      })}
    </span>
  );
}

function volumeRunEstRowsWritten(row: MonitoringRecord) {
  const observed = num(row, "destination_rows_written");
  const destinationIdentity = [row.destination_connection_type, row.destination_format, row.destination_name, row.destination_path]
    .map((value) => String(value ?? "").toLowerCase())
    .join(" ");
  const isLakehouse = ["lakehouse", "delta", "iceberg", "onelake", "deltalake"].some((token) => destinationIdentity.includes(token));
  return !isLakehouse && String(row.status ?? "").toLowerCase() === "succeeded" ? num(row, "source_rows_read") || observed : observed;
}

function VolumeRunRowChangesCell({ row }: { row: MonitoringRecord }) {
  const inserted = num(row, "destination_rows_inserted");
  const updated = num(row, "destination_rows_updated");
  const deleted = num(row, "destination_rows_deleted");
  const value = `${formatNumber(inserted)} / ${formatNumber(updated)} / ${formatNumber(deleted)}`;
  return (
    <span className="volume-row-changes-inline" title={`Inserted / updated / deleted: ${value}`}>
      <span className="is-insert">{formatNumber(inserted)}</span><i>/</i>
      <span className="is-update">{formatNumber(updated)}</span><i>/</i>
      <span className="is-delete">{formatNumber(deleted)}</span>
    </span>
  );
}

function VolumeRunFilesCell({ row }: { row: MonitoringRecord }) {
  const added = num(row, "destination_files_added");
  const removed = num(row, "destination_files_removed");
  const title = `Files added / removed: ${formatNumber(added)} / ${formatNumber(removed)}`;
  return (
    <span className="volume-files-changed-inline" title={title}>
      <span className="is-added">{formatNumber(added)}</span><i>/</i>
      <span className="is-removed">{formatNumber(removed)}</span>
    </span>
  );
}

function PerformanceDetailSections({ row }: { row: Record<string, unknown> }) {
  const candidateReason = firstValue(row, ["performance_candidate_reason", "candidate_reason"]);
  return (
    <section className="monitoring-detail-section monitoring-performance-evidence-section">
      <h3>Performance evidence</h3>
      <div className="monitoring-dataflow-detail-grid monitoring-performance-evidence-grid">
        <GroupedDetailCard
          title="Optimization signal"
          rows={[
            ["Primary reason", candidateReason],
            ["Rule", row.performance_candidate_code],
            ["Priority", row.performance_candidate_priority],
          ]}
          showEmpty
        />
        <GroupedDetailCard
          title="Efficiency"
          rows={[
            ["Rows processed", formatNumber(num(row, "performance_rows_processed"))],
            ["Rows / second", formatNumber(num(row, "performance_rows_per_second"))],
            ["Overhead share", performanceRatioLabel(row.performance_overhead_ratio)],
            ["Dominant share", performanceRatioLabel(row.performance_dominant_phase_ratio)],
          ]}
          showEmpty
        />
        <GroupedDetailCard
          title="Phase cost"
          rows={[
            ["Bottleneck", row.performance_bottleneck_phase],
            ["Source", formatSeconds(num(row, "source_duration_seconds"))],
            ["Transform", formatSeconds(num(row, "transform_duration_seconds"))],
            ["Destination", formatSeconds(num(row, "destination_duration_seconds"))],
            ["Overhead", formatSeconds(num(row, "overhead_duration_seconds"))],
          ]}
          showEmpty
        />
      </div>
    </section>
  );
}

function performanceRatioLabel(value: unknown) {
  if (!hasValue(value)) return "-";
  const ratio = Number(value);
  return Number.isFinite(ratio) ? formatPhasePercent(ratio * 100) : "-";
}

function FreshnessDetailSections({
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

function FreshnessIdentitySection({ row }: { row: Record<string, unknown> }) {
  const source = dataflowEndpointSummary(row, "source");
  const destination = dataflowEndpointSummary(row, "destination");
  const context = [row.stage, row.operation_type, row.processing_mode, row.destination_load_type]
    .filter(hasValue)
    .map(String);
  return (
    <section className="monitoring-detail-section monitoring-freshness-identity">
      <div className="monitoring-dataflow-route-card">
        <DataflowRouteEndpoint direction="source" endpoint={source} />
        <ArrowRight className="monitoring-dataflow-route-arrow" size={18} aria-hidden="true" />
        <DataflowRouteEndpoint direction="destination" endpoint={destination} />
        <div className="monitoring-freshness-dataflow-id">
          <span>Dataflow ID</span>
          <strong>{String(row.dataflow_id ?? "-")}</strong>
        </div>
        {context.length ? (
          <div className="monitoring-dataflow-route-context">
            {context.map((value, index) => <span key={`${value}-${index}`}>{value}</span>)}
          </div>
        ) : null}
      </div>
    </section>
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

function FreshnessRunMix({ row }: { row: Record<string, unknown> }) {
  return (
    <span className="monitoring-freshness-run-mix" title="Succeeded / failed / skipped / running / pending">
      <strong>{formatNumber(num(row, "run_count"))}</strong><span>total</span><i>·</i>
      <span>S</span>
      <strong className="is-success">{formatNumber(num(row, "succeeded_count"))}</strong>
      <span>F</span>
      <strong className="is-failed">{formatNumber(num(row, "failed_count"))}</strong>
      <span>Skip</span>
      <strong className="is-warning">{formatNumber(num(row, "skipped_count"))}</strong>
      <span>Run</span>
      <strong>{formatNumber(num(row, "running_count"))}</strong>
      <span>Pend</span>
      <strong>{formatNumber(num(row, "pending_count"))}</strong>
    </span>
  );
}

function FailureEvidenceSections({ row, timezoneName }: { row: Record<string, unknown>; timezoneName?: string | null }) {
  const message = failureErrorMessage(row);
  const diagnosticTags = failureDiagnosticTags(row.failure_tags);
  const hasFailureContext = [
    row.failure_phase,
    row.failure_category,
  ].some(hasValue);
  if (!message && !hasFailureContext) return null;
  return (
    <section className="monitoring-detail-section monitoring-failure-evidence-section">
      <h3>Errors and notes</h3>
      {message ? <ErrorMessageBlock value={message} /> : null}
      {hasFailureContext ? (
        <div className="monitoring-dataflow-detail-grid">
          <GroupedDetailCard
            title="Failure context"
            rows={[
              ["Failure phase", row.failure_phase],
              ["Failure category", row.failure_category],
            ]}
            timezoneName={timezoneName}
          />
        </div>
      ) : null}
      {diagnosticTags.length ? (
        <div className="monitoring-failure-diagnostic-signals" aria-label="Failure diagnostic signals">
          <strong>Diagnostic signals</strong>
          <div>
            {diagnosticTags.map((tag) => <span key={tag}>{tag}</span>)}
          </div>
        </div>
      ) : null}
    </section>
  );
}

export function failureDiagnosticTags(value: unknown) {
  if (!Array.isArray(value)) return [];
  return Array.from(new Set(
    value.map((item) => String(item ?? "").trim()).filter(Boolean)
  )).slice(0, 5);
}

function FreshnessRunTimeCell({ row, timezoneName }: { row: Record<string, unknown>; timezoneName?: string | null }) {
  const [start, end] = freshnessRunTimeLines(row, timezoneName);
  return (
    <span
      className="freshness-run-stack-cell"
      title={[
        `Start: ${start}`,
        `End: ${end.slice(2)}`,
      ].join("\n")}
    >
      <strong>{start}</strong>
      <small><span>{end}</span></small>
    </span>
  );
}

export function freshnessRunTimeLines(row: Record<string, unknown>, timezoneName?: string | null) {
  const start = formatTimestampForDisplay(row.start_time, timezoneName, "-");
  const end = formatTimestampForDisplay(row.end_time, timezoneName, "-");
  return [start, `→ ${end}`];
}

function FreshnessRunPhaseCell({ row }: { row: Record<string, unknown> }) {
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

function FreshnessRunVolumeCell({ row }: { row: Record<string, unknown> }) {
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
      <strong>{formatNumber(rowsRead)} / {formatNumber(rowsWritten)}</strong>
      <small>{formatBytes(netBytes)}</small>
    </span>
  );
}

function watermarkStatusLabel(row: Record<string, unknown>) {
  const classification = firstValue(row, ["movement_state"]);
  return hasValue(classification) ? String(classification) : "-";
}

function DrawerWatermarkBadge({ row }: { row: Record<string, unknown> }) {
  const value = watermarkStatusLabel(row);
  const intent = watermarkIntent(value);
  const effective = row.source_watermark_effective;
  const detail = hasValue(effective) ? String(effective) : humanize(value);
  return <span className={`monitor-mini-badge badge-${intent}`} title={detail}>{humanize(value)}</span>;
}

function watermarkIntent(value: string) {
  if (value === "advanced") return "good";
  if (value === "unchanged" || value === "incomplete" || value === "invalid") return "warning";
  return "neutral";
}

function formatFreshnessAge(ageSeconds: unknown, ageDays: unknown) {
  const seconds = Number(ageSeconds);
  if (Number.isFinite(seconds)) {
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
    if (seconds < 86400) return `${Math.round((seconds / 3600) * 10) / 10}h`;
    return `${Math.round((seconds / 86400) * 10) / 10}d`;
  }
  const days = Number(ageDays);
  return Number.isFinite(days) ? `${Math.round(days * 10) / 10}d` : "-";
}

function failureErrorMessage(row: Record<string, unknown>) {
  return firstValue(row, [
    "failure_message",
    "source_error_message",
    "transform_error_message",
    "destination_error_message",
    "error_messages",
    "error_message",
  ]);
}

function DataflowPhaseBottleneck({ row }: { row: Record<string, unknown> }) {
  const bottleneck = dataflowPhaseBottleneck(row);
  if (!bottleneck) return <span className="monitor-muted">-</span>;
  return (
    <span className="monitoring-phase-bottleneck">
      <span className={`phase-text-${bottleneck.phase}`}>{bottleneck.label}</span>
      {" "}
      <PhaseContributionSummary row={row} />
    </span>
  );
}

function PhaseContributionSummary({ row }: { row: Record<string, unknown> }) {
  const segments = dataflowPhaseSegments(row);
  if (!segments.length) return null;
  return (
    <span className="monitoring-phase-bottleneck-summary" aria-label="Phase contribution summary">
      <span className="monitoring-phase-summary-punctuation">(</span>
      {segments.map((segment, index) => (
        <span key={segment.phase} className={`phase-text-${segment.phase}`}>
          {phaseShortLabel(segment.phase)} {formatPhasePercent(segment.percent)}
        </span>
      ))}
      <span className="monitoring-phase-summary-punctuation">)</span>
    </span>
  );
}

function DataflowPhaseContribution({ row }: { row: Record<string, unknown> }) {
  const segments = dataflowPhaseSegments(row);
  if (!segments.length) return <span className="monitor-muted">-</span>;
  const title = segments
    .map((segment) => `${phaseLabel(segment.phase)}: ${formatSeconds(segment.value)} (${formatPhasePercent(segment.percent)})`)
    .join("\n");
  return (
    <div className="monitoring-phase-contribution" title={title}>
      <div className="monitoring-phase-stack" aria-label="Dataflow phase contribution">
        {segments.map((segment) => (
          <i key={segment.phase} className={`phase-${segment.phase}`} style={{ flex: `0 0 ${segment.percent}%` }} />
        ))}
      </div>
    </div>
  );
}

function dataflowPhaseBottleneck(row: Record<string, unknown>) {
  const phaseHealth = String(row.phase_health ?? "").toLowerCase();
  for (const phase of DATAFLOW_PHASE_KEYS) {
    if (phaseHealth.includes(phase)) return { phase, label: phaseLabel(phase) };
  }
  const segments = dataflowPhaseDurations(row);
  const maxSegment = segments.sort((left, right) => right.value - left.value)[0];
  return maxSegment && maxSegment.value > 0 ? { phase: maxSegment.phase, label: phaseLabel(maxSegment.phase) } : null;
}

const DATAFLOW_PHASE_KEYS: DataflowPhaseKey[] = ["source", "transform", "destination", "overhead"];

function dataflowPhaseSegments(row: Record<string, unknown>) {
  const visible = dataflowPhaseDurations(row).filter((segment) => segment.value > 0);
  const total = visible.reduce((sum, segment) => sum + segment.value, 0);
  if (total <= 0) return [];
  let usedPercent = 0;
  return visible.map((segment, index) => {
    const percent = index === visible.length - 1 ? Math.max(0, 100 - usedPercent) : (segment.value / total) * 100;
    usedPercent += percent;
    return { ...segment, percent };
  });
}

function dataflowPhaseDurations(row: Record<string, unknown>) {
  const source = Math.max(0, num(row, "source_duration_seconds"));
  const transform = Math.max(0, num(row, "transform_duration_seconds"));
  const destination = Math.max(0, num(row, "destination_duration_seconds"));
  const overhead = Math.max(0, optionalNum(row, "overhead_duration_seconds") ?? 0);
  return [
    { phase: "source" as const, value: source },
    { phase: "transform" as const, value: transform },
    { phase: "destination" as const, value: destination },
    { phase: "overhead" as const, value: overhead },
  ];
}

function phaseLabel(phase: DataflowPhaseKey) {
  if (phase === "source") return "Source";
  if (phase === "transform") return "Transform";
  if (phase === "destination") return "Destination";
  return "Overhead";
}

function phaseShortLabel(phase: DataflowPhaseKey) {
  if (phase === "source") return "S";
  if (phase === "transform") return "T";
  if (phase === "destination") return "D";
  return "O";
}

function ErrorMessageSection({ row, timezoneName }: { row: Record<string, unknown>; timezoneName?: string | null }) {
  if (!hasValue(row.error_message)) return null;
  return (
    <section className="monitoring-detail-section monitoring-error-message-section">
      <h3>Error Message</h3>
      <ErrorMessageBlock value={detailValue(row, "error_message", timezoneName)} />
    </section>
  );
}

function ErrorMessageBlock({ value }: { value: unknown }) {
  return (
    <div className="monitoring-error-message-body">
      {isValidElement(value) ? value : String(value ?? "-")}
    </div>
  );
}

function GroupedDetailCard({
  title,
  rows,
  timezoneName,
  showEmpty = false,
  className,
}: {
  title: string;
  rows: DetailRow[];
  timezoneName?: string | null;
  showEmpty?: boolean;
  className?: string;
}) {
  const visibleRows = showEmpty ? rows : rows.filter(([, value]) => hasValue(value));
  if (!visibleRows.length) return null;
  return (
    <div className={`monitoring-job-group-card${className ? ` ${className}` : ""}`}>
      <span>{title}</span>
      <dl>
        {visibleRows.map(([label, value, field]) => {
          const isBlock = isGroupedBlockValue(value, field);
          return (
          <div key={label} className={[isBlock ? "is-block-value" : "", isErrorField(field ?? "") ? "is-error-value" : ""].filter(Boolean).join(" ") || undefined}>
            <dt>{label}</dt>
            <dd>{renderGroupedValue(value, timezoneName, field)}</dd>
          </div>
          );
        })}
      </dl>
    </div>
  );
}

function renderGroupedValue(value: unknown, timezoneName?: string | null, field?: string): ReactNode {
  if (isValidElement(value)) return value;
  if (!hasValue(value)) return "-";
  if (isSemanticValue(value)) return <SemanticValue value={value} />;
  if (typeof value === "string" && field && timezoneName && isTimestampFieldName(field)) {
    return isRuntimeTimestampField(field)
      ? formatRuntimeTimestampForDisplay(value, timezoneName)
      : formatTimestampForDisplay(value, timezoneName);
  }
  if (field && SQL_BLOCK_FIELDS.has(field)) return <CodeBlock value={value} kind="sql" />;
  if (field && LIST_BLOCK_FIELDS.has(field)) return <CodeBlock value={value} kind="list" />;
  if (field && JSON_BLOCK_FIELDS.has(field)) return <CodeBlock value={value} kind="json" />;
  if (field?.endsWith("duration_seconds")) return formatSeconds(Number(value) || 0);
  if (field?.includes("bytes")) return formatBytes(Number(value) || 0);
  if (typeof value === "boolean") return value ? "true" : "false";
  if (Array.isArray(value) || (value && typeof value === "object")) return <JsonBlock value={value} />;
  if (typeof value === "string" && looksJson(value)) return <JsonBlock value={value} />;
  if (typeof value === "number") return <SemanticValue value={semanticNumber(field, value)} />;
  if (typeof value === "string") return <SemanticValue value={semanticText(field, value)} />;
  return value as ReactNode;
}

function isRuntimeTimestampField(field: string) {
  return /^(source|transform|destination)_(start|end)_time$/u.test(field);
}

export function formatRuntimeTimestampForDisplay(value: unknown, timezoneName?: string | null) {
  const rawValue = String(value ?? "").trim();
  if (!rawValue) return "-";
  const normalized = hasExplicitTimezone(rawValue) ? rawValue : `${rawValue}Z`;
  return formatTimestampForDisplay(normalized, timezoneName);
}

function isGroupedBlockValue(value: unknown, field?: string) {
  if (!hasValue(value) || isValidElement(value) || isSemanticValue(value)) return false;
  if (field && (SQL_BLOCK_FIELDS.has(field) || LIST_BLOCK_FIELDS.has(field) || JSON_BLOCK_FIELDS.has(field))) return true;
  if (Array.isArray(value) || (value && typeof value === "object")) return true;
  return typeof value === "string" && looksJson(value);
}

function isSemanticValue(value: unknown): value is SemanticValueModel {
  return Boolean(value && typeof value === "object" && "kind" in value);
}

export function semanticNumber(field: string | undefined, value: number): SemanticValueModel {
  const lowerField = String(field ?? "").toLowerCase();
  let intent: SemanticIntent = "neutral";
  const lifecycleStatus = lifecycleStatusFromField(lowerField);
  if (lifecycleStatus) intent = semanticStatusIntent(lifecycleStatus);
  if (lowerField.includes("success") && !lifecycleStatus) intent = "success";
  if (lowerField.includes("error") && !lifecycleStatus) intent = value > 0 ? "failed" : "neutral";
  if (lowerField.includes("mismatch")) intent = value > 0 ? "failed" : "neutral";
  return { kind: "count", value, intent };
}

function semanticText(field: string | undefined, value: string): SemanticValueModel {
  const lowerValue = value.toLowerCase();
  const lowerField = String(field ?? "").toLowerCase();
  if (lowerField === "status" || ["succeeded", "failed", "skipped", "running", "pending"].includes(lowerValue)) {
    return { kind: "status", value };
  }
  if (lowerField.includes("reconciliation") || lowerValue === "matched" || lowerValue === "mismatch") {
    return { kind: "text", value, intent: lowerValue === "matched" ? "success" : lowerValue === "mismatch" ? "failed" : "neutral" };
  }
  return { kind: "text", value };
}

function SemanticValue({ value }: { value: SemanticValueModel }) {
  if (value.kind === "reconciliation") {
    const statusIntent = value.status.toLowerCase() === "matched" ? "success" : value.status.toLowerCase() === "mismatch" ? "failed" : "neutral";
    const countIntent = value.mismatch > 0 ? "failed" : "neutral";
    return (
      <span className="monitoring-semantic-pair">
        <span className={`monitoring-semantic-value is-${statusIntent}`} style={semanticIntentStyle(statusIntent)}>{value.status}</span>
        <span aria-hidden="true">·</span>
        <span className={`monitoring-semantic-value is-${countIntent}`} style={semanticIntentStyle(countIntent)}>{display({ value: value.mismatch }, "value")}</span>
      </span>
    );
  }
  if (value.kind === "count") {
    const intent = value.intent ?? "neutral";
    return <span className={`monitoring-semantic-value is-${intent}`} style={semanticIntentStyle(intent)}>{display({ value: value.value }, "value")}</span>;
  }
  const intent = semanticIntent(value);
  return <span className={`monitoring-semantic-value is-${intent}`} style={semanticIntentStyle(intent)}>{value.value}</span>;
}

function semanticIntent(value: SemanticValueModel) {
  if ("intent" in value && value.intent) return value.intent;
  if (value.kind !== "status") return "neutral";
  const normalized = value.value.toLowerCase();
  if (normalized === "succeeded") return "success";
  if (normalized === "failed") return "failed";
  if (normalized === "skipped") return "skipped";
  if (normalized === "running") return "running";
  if (normalized === "pending") return "pending";
  return "neutral";
}

function semanticStatusIntent(status: LifecycleStatus): SemanticIntent {
  return status === "succeeded" ? "success" : status;
}

function semanticIntentStyle(intent: SemanticIntent): CSSProperties | undefined {
  const status = intent === "success" ? "succeeded" : intent === "bad" ? "failed" : intent;
  const presentation = lifecycleStatusPresentation(status);
  return presentation ? { color: presentation.textColor } : undefined;
}

function CodeBlock({ value, kind }: { value: unknown; kind: "json" | "list" | "sql" }) {
  const [copied, setCopied] = useState(false);
  const formatted = kind === "sql" ? formatSqlBlock(value) : kind === "list" ? formatListBlock(value) : formatCompactJsonBlock(value);
  const isJsonLike = kind !== "sql";
  return (
    <div className={`monitoring-code-box monitoring-code-box-${kind}`}>
      <button
        className="icon-action small monitoring-json-copy"
        type="button"
        aria-label={`Copy ${kind.toUpperCase()}`}
        title={`Copy ${kind.toUpperCase()}`}
        onClick={(event) => {
          event.preventDefault();
          event.stopPropagation();
          void copyToClipboard(formatted, setCopied);
        }}
      >
        {copied ? <Check size={12} /> : <Copy size={12} />}
      </button>
      <pre className="monitoring-code-block">
        {isJsonLike ? highlightJson(formatted) : formatted}
      </pre>
    </div>
  );
}

function JsonBlock({ value, compactArray = false }: { value: unknown; compactArray?: boolean }) {
  const [copied, setCopied] = useState(false);
  const formatted = formatJsonValue(value, compactArray);
  return (
    <div className="monitoring-json-box monitoring-inline-json-box">
      <button
        className="icon-action small monitoring-json-copy"
        type="button"
        aria-label="Copy JSON"
        title="Copy JSON"
        onClick={(event) => {
          event.preventDefault();
          event.stopPropagation();
          void copyToClipboard(formatted, setCopied);
        }}
      >
        {copied ? <Check size={12} /> : <Copy size={12} />}
      </button>
      <pre className="monitoring-inline-json monitoring-json-light">{highlightJson(formatted)}</pre>
    </div>
  );
}

function safeJsonStringify(value: unknown) {
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function formatJsonValue(value: unknown, compactArray = false) {
  if (compactArray) return formatCompactJsonArray(value);
  return typeof value === "string" && looksJson(value) ? formatJson(value) : safeJsonStringify(value);
}

function formatCompactJsonBlock(value: unknown) {
  const parsed = parseJsonLike(value);
  if (Array.isArray(parsed)) return formatCompactJsonArray(parsed);
  if (parsed && typeof parsed === "object") return formatCompactJsonObject(parsed as Record<string, unknown>);
  if (typeof value === "string" && looksJson(value)) return formatJson(value);
  return String(value);
}

function formatListBlock(value: unknown) {
  const parsed = parseJsonLike(value);
  if (Array.isArray(parsed)) return formatCompactJsonArray(parsed);
  if (typeof value === "string") {
    const values = value.split(",").map((item) => item.trim()).filter(Boolean);
    if (values.length > 1) return formatCompactJsonArray(values);
  }
  return formatCompactJsonBlock(value);
}

function formatSqlBlock(value: unknown) {
  const sql = String(value).trim().replace(/\s+/g, " ");
  if (!sql) return "-";
  return sql
    .replace(/\b(left join|right join|inner join|outer join|union all|group by|order by|from|where|join|having|union)\b/giu, "\n$1")
    .replace(/\b(and|or)\b/giu, "\n  $1")
    .replace(/\s*,\s*/gu, ",\n  ")
    .trim();
}

function parseJsonLike(value: unknown) {
  if (typeof value !== "string") return value;
  const trimmed = value.trim();
  if (!looksJson(trimmed)) return value;
  try {
    return JSON.parse(trimmed);
  } catch {
    return value;
  }
}

function formatCompactJsonObject(value: Record<string, unknown>) {
  const entries = Object.entries(value);
  if (!entries.length) return "{}";
  const lines: string[] = ["{"];
  entries.forEach(([key, item], index) => {
    const suffix = index + 1 < entries.length ? "," : "";
    lines.push(`  ${JSON.stringify(key)}: ${JSON.stringify(item)}${suffix}`);
  });
  lines.push("}");
  return lines.join("\n");
}

function formatCompactJsonArray(value: unknown) {
  let parsed = value;
  if (typeof value === "string") {
    try {
      parsed = JSON.parse(value);
    } catch {
      return value;
    }
  }
  if (!Array.isArray(parsed)) return safeJsonStringify(parsed);
  if (!parsed.length) return "[]";
  if (parsed.every(isPrimitiveJsonValue)) {
    return `[\n  ${parsed.map((item) => JSON.stringify(item)).join(", ")}\n]`;
  }
  return JSON.stringify(parsed, null, 2);
}

function isPrimitiveJsonValue(value: unknown) {
  return value === null || ["string", "number", "boolean"].includes(typeof value);
}

function highlightJson(value: string) {
  const tokens = value.match(/"(?:\\.|[^"\\])*"(?=\s*:)|"(?:\\.|[^"\\])*"|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|\b(?:true|false|null)\b|[{}\[\],:]/gu);
  if (!tokens) return value;

  const nodes: ReactNode[] = [];
  let cursor = 0;

  tokens.forEach((token, index) => {
    const start = value.indexOf(token, cursor);
    if (start < cursor) return;
    if (start > cursor) nodes.push(value.slice(cursor, start));
    nodes.push(
      <span key={`${start}-${index}`} className={`json-token ${jsonTokenClass(token, value.slice(start + token.length))}`}>
        {token}
      </span>
    );
    cursor = start + token.length;
  });

  if (cursor < value.length) nodes.push(value.slice(cursor));
  return nodes;
}

function jsonTokenClass(token: string, afterToken: string) {
  if (/^"/u.test(token) && afterToken.trimStart().startsWith(":")) return "json-key";
  if (/^"/u.test(token)) return "json-string";
  if (/^-?\d/u.test(token)) return "json-number";
  if (token === "true" || token === "false") return "json-boolean";
  if (token === "null") return "json-null";
  return "json-punctuation";
}

function LinkedJobSection({
  row,
  onOpenJob,
}: {
  row: Record<string, unknown>;
  onOpenJob?: (row: Record<string, unknown>) => void;
}) {
  const jobId = String(row.job_id ?? "");
  if (!jobId) return null;
  const jobRow = {
    ...row,
    job_id: jobId,
    status: row.linked_job_status ?? row.job_status,
    duration_seconds: row.linked_job_duration_seconds ?? row.job_duration_seconds,
    engine_name: row.engine_name,
    metadata_provider_name: row.metadata_provider_name,
    platform_name: row.platform_name,
  };
  return (
    <section className="monitoring-detail-section monitoring-linked-job-section">
      <h3>Linked job</h3>
      <button
        className="monitoring-linked-job-row"
        type="button"
        onClick={() => onOpenJob?.(jobRow)}
        disabled={!onOpenJob}
      >
        <BriefcaseBusiness size={15} aria-hidden="true" />
        <span className="monitoring-linked-job-identity">
          <strong>{jobId}</strong>
          <small>Parent job run</small>
        </span>
        <StatusCell row={jobRow} />
        <span className="monitoring-linked-job-duration">{formatSeconds(num(jobRow, "duration_seconds"))}</span>
        <ChevronRight size={16} aria-hidden="true" />
      </button>
    </section>
  );
}

function DiagnosticsLinkedJobSection({
  row,
  onOpenJob,
}: {
  row: Record<string, unknown>;
  onOpenJob?: (row: Record<string, unknown>) => void;
}) {
  const jobId = String(row.job_id ?? "");
  if (!jobId) return null;
  return (
    <section className="monitoring-detail-section monitoring-linked-job-section">
      <h3>Linked job</h3>
      <button
        className="monitoring-linked-job-row is-diagnostics-link"
        type="button"
        onClick={() => onOpenJob?.(row)}
        disabled={!onOpenJob}
      >
        <BriefcaseBusiness size={15} aria-hidden="true" />
        <span className="monitoring-linked-job-identity">
          <strong>{jobId}</strong>
          <small>Open canonical job run</small>
        </span>
        <ChevronRight size={16} aria-hidden="true" />
      </button>
    </section>
  );
}

function DetailSection({
  title,
  row,
  fields,
  wide = false,
  timezoneName,
}: {
  title: string;
  row: Record<string, unknown>;
  fields: string[];
  wide?: boolean;
  timezoneName?: string | null;
}) {
  const items = fields.filter((field) => hasValue(row[field]));
  if (!items.length) return null;
  return (
    <section className="monitoring-detail-section">
      <h3>{title}</h3>
      <div className={`monitoring-detail-grid${wide ? " wide" : ""}`}>
        {items.map((field) => (
          <div key={field} className={`monitoring-detail-item${isErrorField(field) ? " monitoring-detail-error-item" : ""}`}>
            <span>{humanize(field)}</span>
            <strong>{field === "status" ? <StatusCell row={row} /> : detailValue(row, field, timezoneName)}</strong>
          </div>
        ))}
      </div>
    </section>
  );
}

function detailTitle(row: Record<string, unknown>, kind: MonitoringDetailKind) {
  return String(
    row.dataflow_name ??
      row.job_id ??
      row.failure_target ??
      row.target_display ??
      row.target ??
      row.table ??
      row.dataflow_id ??
      row.category ??
      kindLabel(kind)
  );
}

function headerCopyValue(row: Record<string, unknown>, kind: MonitoringDetailKind, title: string) {
  if (kind === "job") return String(row.job_id ?? title ?? "");
  if (kind === "dataflow") return String(row.dataflow_run_id ?? row.dataflow_id ?? title ?? "");
  return String(title ?? "");
}

function kindLabel(kind: MonitoringDetailKind) {
  return kind.replace(/_/g, " ");
}

function detailValue(row: Record<string, unknown>, field: string, timezoneName?: string | null) {
  const value = row[field];
  if (field.endsWith("duration_seconds")) return `${display(row, field)}s`;
  if (field.includes("bytes")) return formatBytes(num(row, field));
  if (typeof value === "string" && timezoneName && isTimestampFieldName(field)) return formatTimestampForDisplay(value, timezoneName);
  if (Array.isArray(value) || (value && typeof value === "object")) {
    return <JsonBlock value={value} />;
  }
  if (typeof value === "string" && looksJson(value)) {
    return <JsonBlock value={value} />;
  }
  return display(row, field);
}

function isErrorField(field: string) {
  const normalized = field.toLowerCase();
  return normalized.includes("error") || normalized.includes("issue") || normalized === "last_error";
}

function RawPayloadSection({ row, label = "Raw payload" }: { row: Record<string, unknown>; label?: string }) {
  const [copied, setCopied] = useState(false);
  const payload = JSON.stringify(row, null, 2);
  return (
    <details className="monitoring-raw-detail">
      <summary>{label}</summary>
      <div className="monitoring-json-box monitoring-raw-json-box">
        <button
          className="icon-action small monitoring-json-copy"
          type="button"
          aria-label={`Copy ${label.toLowerCase()} JSON`}
          title="Copy JSON"
          onClick={(event) => {
            event.preventDefault();
            event.stopPropagation();
            void copyToClipboard(payload, setCopied);
          }}
        >
          {copied ? <Check size={13} /> : <Copy size={13} />}
        </button>
        <pre>{highlightJson(payload)}</pre>
      </div>
    </details>
  );
}

function IssueCell({ row }: { row: MonitoringRecord }) {
  const issue = String(row.error_preview || row.error_message || row.source_error_message || row.transform_error_message || row.destination_error_message || "");
  if (!issue) return <span className="monitor-muted">-</span>;
  const status = String(row.status || "").toLowerCase();
  return (
    <span className={`monitoring-issue-cell${status === "failed" ? " is-error" : ""}`} title={issue} aria-label={issue}>
      {issue}
    </span>
  );
}

async function copyToClipboard(value: string, setCopied: (copied: boolean) => void) {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(value);
    } else {
      fallbackCopyToClipboard(value);
    }
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1200);
  } catch {
    try {
      fallbackCopyToClipboard(value);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1200);
    } catch {
      setCopied(false);
    }
  }
}

function fallbackCopyToClipboard(value: string) {
  const textarea = document.createElement("textarea");
  textarea.value = value;
  textarea.setAttribute("readonly", "true");
  textarea.style.position = "fixed";
  textarea.style.left = "-9999px";
  textarea.style.top = "0";
  document.body.appendChild(textarea);
  textarea.select();
  document.execCommand("copy");
  document.body.removeChild(textarea);
}

function hasValue(value: unknown) {
  if (Array.isArray(value)) return value.length > 0;
  return value !== null && value !== undefined && value !== "";
}

function optionalNum(row: Record<string, unknown>, field: string) {
  const value = row[field];
  if (!hasValue(value)) return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function looksJson(value: string) {
  const trimmed = value.trim();
  return (trimmed.startsWith("{") && trimmed.endsWith("}")) || (trimmed.startsWith("[") && trimmed.endsWith("]"));
}

function formatJson(value: string) {
  try {
    return JSON.stringify(JSON.parse(value), null, 2);
  } catch {
    return value;
  }
}

function humanize(value: string) {
  return value.replace(/_/g, " ").replace(/\b\w/g, (char) => char.toUpperCase());
}
