import { ArrowLeft, Check, Copy, FileText, X } from "lucide-react";
import { isValidElement, useEffect, useMemo, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { api } from "../../shared/api/client";
import type { MonitoringRecord, SystemLogResponse } from "../../shared/api/types";
import { formatTimestampForDisplay, isTimestampFieldName } from "../../shared/time";
import { DataTable, StatusCell, display, formatBytes, formatNumber, formatSeconds, num, type TableSort } from "./MonitoringCharts";
import { TablePager } from "./monitoringShared";

export type MonitoringDetailKind = "job" | "dataflow" | "failure" | "performance" | "maintenance" | "freshness" | "diagnostics";

interface MonitoringDetailDrawerProps {
  kind: MonitoringDetailKind;
  row: Record<string, unknown>;
  environmentId?: number | null;
  relatedDataflows?: MonitoringRecord[];
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
type SemanticIntent = "success" | "failed" | "skipped" | "bad" | "neutral";
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

export function MonitoringDetailDrawer({
  kind,
  row,
  environmentId,
  relatedDataflows = [],
  reconciliationChecks = [],
  timezoneName,
  onOpenDataflow,
  onOpenJob,
  onBack,
  onClose,
}: MonitoringDetailDrawerProps) {
  const title = detailTitle(row, kind);
  const [childDataflowSort, setChildDataflowSort] = useState<TableSort>({ sortBy: "start_time", sortDir: "desc" });
  const [childDataflowOffset, setChildDataflowOffset] = useState(0);
  const [childDataflowLimit, setChildDataflowLimit] = useState(50);
  const [headerCopied, setHeaderCopied] = useState(false);
  const [systemLogsOpen, setSystemLogsOpen] = useState(false);
  const [systemLogsLoading, setSystemLogsLoading] = useState(false);
  const [systemLogs, setSystemLogs] = useState<SystemLogResponse | null>(null);
  const [systemLogsError, setSystemLogsError] = useState<string | null>(null);
  const sortedRelatedDataflows = useMemo(
    () => sortRows(relatedDataflows, childDataflowSort),
    [relatedDataflows, childDataflowSort]
  );
  useEffect(() => {
    if (childDataflowOffset >= sortedRelatedDataflows.length) setChildDataflowOffset(0);
  }, [childDataflowOffset, sortedRelatedDataflows.length]);
  const handleChildDataflowSort = (nextSort: TableSort) => {
    setChildDataflowSort(nextSort);
    setChildDataflowOffset(0);
  };
  const copyTitle = headerCopyValue(row, kind, title);
  const copyLabel = kind === "job" || kind === "dataflow" ? `${kindLabel(kind)} id` : `${kindLabel(kind)} title`;
  const jobId = typeof row.job_id === "string" ? row.job_id : "";
  const dataflowId = kind === "dataflow" && typeof row.dataflow_id === "string" ? row.dataflow_id : "";
  async function toggleSystemLogs() {
    if (!environmentId || !jobId) return;
    const nextOpen = !systemLogsOpen;
    setSystemLogsOpen(nextOpen);
    if (!nextOpen || systemLogs || systemLogsLoading) return;
    setSystemLogsLoading(true);
    setSystemLogsError(null);
    try {
      setSystemLogs(
        await api.getMonitoringSystemLogs(environmentId, {
          job_id: jobId,
          dataflow_id: dataflowId || undefined,
          limit: 500,
          offset: 0,
        })
      );
    } catch (err) {
      setSystemLogsError(err instanceof Error ? err.message : String(err));
    } finally {
      setSystemLogsLoading(false);
    }
  }
  return createPortal(
    <div className="metadata-drawer-backdrop monitoring-detail-backdrop" onMouseDown={onClose}>
      <aside className="metadata-drawer monitoring-detail-drawer" aria-label="Monitoring details" onMouseDown={(event) => event.stopPropagation()}>
        <header className="metadata-drawer-header">
          {onBack ? (
            <button className="icon-action monitoring-detail-back" type="button" aria-label="Back to previous monitoring detail" onClick={onBack}>
              <ArrowLeft size={18} />
            </button>
          ) : null}
          <div className="monitoring-detail-heading">
            <span>{kindLabel(kind)}</span>
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
              {environmentId && jobId ? (
                <button
                  className="icon-action monitoring-detail-copy"
                  type="button"
                  aria-label="Show system logs"
                  title={systemLogsOpen ? "Hide system logs" : "Show system logs"}
                  onClick={(event) => {
                    event.stopPropagation();
                    void toggleSystemLogs();
                  }}
                >
                  <FileText size={14} />
                </button>
              ) : null}
            </div>
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
              relatedDataflows={sortedRelatedDataflows}
              offset={childDataflowOffset}
              limit={childDataflowLimit}
              sort={childDataflowSort}
              onSort={handleChildDataflowSort}
              onPageChange={setChildDataflowOffset}
              onPageSizeChange={(nextLimit) => {
                setChildDataflowLimit(nextLimit);
                setChildDataflowOffset(0);
              }}
              onOpenDataflow={onOpenDataflow}
              timezoneName={timezoneName}
            />
          ) : kind === "maintenance" ? (
            <MaintenanceDetailSections
              row={row}
              relatedDataflows={sortedRelatedDataflows}
              onOpenDataflow={onOpenDataflow}
              timezoneName={timezoneName}
            />
          ) : kind === "diagnostics" ? (
            <DiagnosticsDetailSections row={row} timezoneName={timezoneName} />
          ) : (
            <DetailSection title="Detail" row={row} fields={PRIMARY_FIELDS} timezoneName={timezoneName} />
          )}
          {kind === "dataflow" && row.job_id ? (
            <LinkedJobSection row={row} onOpenJob={onOpenJob} />
          ) : null}
          {systemLogsOpen ? (
            <SystemLogsSection logs={systemLogs} loading={systemLogsLoading} error={systemLogsError} dataflowScoped={Boolean(dataflowId)} timezoneName={timezoneName} />
          ) : null}
          {kind !== "job" && kind !== "dataflow" && kind !== "freshness" && kind !== "maintenance" && kind !== "diagnostics" ? (
            <>
              <DetailSection title="Source runtime" row={row} fields={SOURCE_FIELDS} timezoneName={timezoneName} />
              <DetailSection title="Transform runtime" row={row} fields={TRANSFORM_FIELDS} timezoneName={timezoneName} />
              <DetailSection title="Destination runtime" row={row} fields={DESTINATION_FIELDS} timezoneName={timezoneName} />
              <DetailSection title="Watermark" row={row} fields={WATERMARK_FIELDS} wide timezoneName={timezoneName} />
              <DetailSection title="Errors and notes" row={row} fields={ERROR_FIELDS} wide timezoneName={timezoneName} />
            </>
          ) : null}
          {kind === "job" && relatedDataflows.length ? (
            <section className="monitoring-detail-section">
              <div className="monitoring-detail-section-header monitoring-child-dataflows-header">
                <h3>Child dataflows</h3>
                <small>{relatedDataflows.length} dataflow runs</small>
              </div>
              <DataTable
                rows={sortedRelatedDataflows}
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
                maxRows={relatedDataflows.length}
                onRowClick={onOpenDataflow}
                sort={childDataflowSort}
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
          <RawPayloadSection row={row} />
        </div>
      </aside>
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

function SystemLogsSection({
  logs,
  loading,
  error,
  dataflowScoped,
  timezoneName,
}: {
  logs: SystemLogResponse | null;
  loading: boolean;
  error: string | null;
  dataflowScoped: boolean;
  timezoneName?: string | null;
}) {
  const records = logs?.records ?? [];
  return (
    <section className="monitoring-detail-section">
      <div className="monitoring-detail-section-header monitoring-child-dataflows-header">
        <h3>System logs</h3>
        <small>{loading ? "Loading..." : `${logs?.total ?? 0} records · ${logs?.files.length ?? 0} files${dataflowScoped ? " · dataflow scoped" : ""}`}</small>
      </div>
      {error ? <p className="monitoring-detail-error-message">System log read failed: {error}</p> : null}
      {!loading && !error && logs && !logs.files.length ? (
        <p className="monitoring-detail-muted">No system log file is indexed for this job. Run Sync on the log source first.</p>
      ) : null}
      {logs?.errors?.length ? (
        <p className="monitoring-detail-muted">{logs.errors.length} read warnings while loading system logs.</p>
      ) : null}
      {records.length ? (
        <DataTable
          rows={records}
          columns={[
            { key: "ts", label: "Time", sortable: true, width: 184, render: (item) => detailValue(item, "ts", timezoneName) },
            { key: "level", label: "Level", sortable: true, autoFit: true, minWidth: 64, maxWidth: 92 },
            { key: "logger", label: "Logger", sortable: true, width: 160 },
            { key: "dataflow_id", label: "Dataflow", sortable: true, width: 140 },
            { key: "msg", label: "Message", sortable: true, minWidth: 260, fillPriority: "last", render: (item) => <SystemLogMessage row={item} /> },
          ]}
          maxRows={Math.min(200, Math.max(1, records.length))}
          fixedLayout
          timezoneName={timezoneName}
          className="monitoring-child-dataflows-table"
        />
      ) : loading ? (
        <p className="monitoring-detail-muted">Loading system logs...</p>
      ) : null}
    </section>
  );
}

function SystemLogMessage({ row }: { row: Record<string, unknown> }) {
  const message = String(row.msg ?? row.message ?? "-");
  const title = JSON.stringify(row, null, 2);
  return <span className="monitoring-child-issue-cell" title={title}>{message}</span>;
}

function firstValue(row: Record<string, unknown>, fields: string[]) {
  for (const field of fields) {
    if (hasValue(row[field])) return row[field];
  }
  return null;
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

function DataflowDetailSections({ row, timezoneName }: { row: Record<string, unknown>; timezoneName?: string | null }) {
  return (
    <>
      <section className="monitoring-detail-section">
        <h3>Dataflow</h3>
        <div className="monitoring-dataflow-detail-grid">
          <GroupedDetailCard
            title="Master data"
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
          <GroupedDetailCard
            title="Runtime info"
            rows={[
              ["Run ID", row.dataflow_run_id],
              ["Operation", row.operation_type],
              ["Start", row.start_time, "start_time"],
              ["End", row.end_time, "end_time"],
              ["Duration", row.duration_seconds, "duration_seconds"],
              ["Status", <StatusCell row={row} />],
              ["Phase bottleneck", <DataflowPhaseBottleneck row={row} />],
              ["Phase contribution", <DataflowPhaseContribution row={row} />, "phase_contribution"],
              ["Retry attempts", row.retry_attempts],
            ]}
            showEmpty
            timezoneName={timezoneName}
          />
        </div>
      </section>

      <FailureEvidenceSections row={row} timezoneName={timezoneName} />

      <section className="monitoring-detail-section">
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

      <section className="monitoring-detail-section">
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

      <section className="monitoring-detail-section">
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

function MaintenanceDetailSections({
  row,
  relatedDataflows,
  onOpenDataflow,
  timezoneName,
}: {
  row: Record<string, unknown>;
  relatedDataflows: MonitoringRecord[];
  onOpenDataflow?: (row: MonitoringRecord) => void;
  timezoneName?: string | null;
}) {
  const nonMaintenanceRuns = relatedDataflows.filter((item) => !isMaintenanceRun(item));
  const destinationRuns = relatedDataflows
    .slice()
    .sort((left, right) => timeValue(right.end_time ?? right.start_time) - timeValue(left.end_time ?? left.start_time));
  const contributingDataflows = maintenanceContributingDataflows(nonMaintenanceRuns);
  return (
    <>
      <section className="monitoring-detail-section">
        <h3>Destination table</h3>
        <div className="monitoring-job-detail-grid">
          <GroupedDetailCard
            title="Health"
            rows={[
              ["Status", row.table_health],
              ["Health reason", row.attention_reason],
              ["Latest maintenance", row.latest_maintenance_time, "latest_maintenance_time"],
              ["Latest ETL write", row.latest_etl_write_time, "latest_etl_write_time"],
              ["Lag", row.maintenance_lag_seconds, "maintenance_lag_seconds"],
            ]}
            showEmpty
            timezoneName={timezoneName}
          />
          <GroupedDetailCard
            title="Target identity"
            rows={[
              ["Target", row.target],
              ["Display target", row.target_display],
              ["Destination table", row.destination_table],
              ["Full table", row.destination_full_table],
              ["Path", row.destination_path],
              ["Connection", firstValue(row, ["destination_name", "destination_connection_name"])],
              ["Connection type", row.destination_connection_type],
              ["Format", row.destination_format ?? row.format],
            ]}
            showEmpty
            timezoneName={timezoneName}
          />
          <GroupedDetailCard
            title="Maintenance evidence"
            rows={[
              ["Runs", row.run_count],
              ["Succeeded", row.succeeded],
              ["Failed", row.failed],
              ["Skipped", row.skipped],
              ["Bytes reclaimed", row.bytes_reclaimed, "bytes_reclaimed"],
              ["Files removed", row.files_removed],
              ["Bytes saved", row.bytes_saved, "bytes_saved"],
              ["Efficiency", `${formatBytes(num(row, "bytes_reclaimed_per_second"))}/s`],
              ["No-op runs", row.no_op_runs],
              ["No-op duration", row.no_op_duration_seconds, "no_op_duration_seconds"],
            ]}
            showEmpty
            timezoneName={timezoneName}
          />
        </div>
      </section>

      <section className="monitoring-detail-section">
        <div className="monitoring-detail-section-header monitoring-child-dataflows-header">
          <h3>Contributing dataflows</h3>
          <small>{contributingDataflows.length} dataflows · {nonMaintenanceRuns.length} non-maintenance runs</small>
        </div>
        <DataTable
          rows={contributingDataflows}
          columns={[
            { key: "dataflow_name", label: "Dataflow", sortable: true, width: 150 },
            { key: "context", label: "Context", sortable: true, sortKey: "stage", width: 100, render: (item) => <MaintenanceContextCell row={item} /> },
            { key: "source", label: "Source", sortable: true, width: 140, fillPriority: "last", render: (item) => <MaintenanceSourceCell row={item} /> },
            { key: "load_type", label: "Load", sortable: true, autoFit: true, minWidth: 72, maxWidth: 112 },
            { key: "latest_status", label: "Latest", sortable: true, sortKey: "latest_time", width: 176, render: (item) => <MaintenanceLatestCell row={item} timezoneName={timezoneName} /> },
            { key: "run_count", label: "Runs / rows", sortable: true, width: 112, render: (item) => <MaintenanceContributingVolumeCell row={item} /> },
          ]}
          maxRows={Math.max(1, contributingDataflows.length)}
          fixedLayout
          timezoneName={timezoneName}
          className="monitoring-child-dataflows-table"
        />
      </section>

      <section className="monitoring-detail-section">
        <div className="monitoring-detail-section-header monitoring-child-dataflows-header">
          <h3>Destination runs</h3>
          <small>{destinationRuns.length} dataflow runs into this destination</small>
        </div>
        <DataTable
          rows={destinationRuns}
          columns={[
            { key: "dataflow_name", label: "Dataflow", sortable: true, width: 150 },
            { key: "context", label: "Context", sortable: true, sortKey: "stage", width: 100, render: (item) => <MaintenanceContextCell row={item} /> },
            { key: "source", label: "Source", sortable: true, width: 140, fillPriority: "last", render: (item) => <MaintenanceSourceCell row={item} /> },
            { key: "status", label: "Status", sortable: true, autoFit: true, minWidth: 76, maxWidth: 104, render: (item) => <StatusCell row={item} /> },
            { key: "duration_seconds", label: "Duration", sortable: true, autoFit: true, minWidth: 82, maxWidth: 112, render: (item) => formatSeconds(num(item, "duration_seconds")) },
            { key: "volume", label: "Volume", sortable: true, sortKey: "source_rows_read", width: 126, render: (item) => <MaintenanceRunVolumeCell row={item} /> },
            { key: "end_time", label: "End", sortable: true, width: 176, render: (item) => detailValue(item, "end_time", timezoneName) },
            { key: "error_message", label: "Issue", sortable: true, minWidth: 120, fillPriority: "last", render: (item) => <IssueCell row={item} /> },
          ]}
          maxRows={Math.min(100, Math.max(1, destinationRuns.length))}
          onRowClick={onOpenDataflow}
          fixedLayout
          timezoneName={timezoneName}
          className="monitoring-child-dataflows-table"
        />
      </section>
    </>
  );
}

function isMaintenanceRun(row: Record<string, unknown>) {
  const operationType = String(row.operation_type ?? "").toLowerCase();
  const destinationOperationType = String(row.destination_operation_type ?? "").toLowerCase();
  return operationType === "maintenance" || ["compact", "cleanup", "maintenance"].includes(destinationOperationType);
}

function MaintenanceContextCell({ row }: { row: Record<string, unknown> }) {
  const stage = String(row.stage ?? "unknown");
  const operation = String(row.operation_type ?? "unknown");
  const destinationOperation = String(row.destination_operation_type ?? "-");
  return (
    <span
      className="freshness-run-stack-cell"
      title={[
        `Stage: ${stage}`,
        `Operation: ${operation}`,
        `Destination operation: ${destinationOperation}`,
      ].join("\n")}
    >
      <strong>{stage}</strong>
      <small>{operation}</small>
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
  const latest = detailValue(row, "latest_time", timezoneName);
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

function maintenanceContributingDataflows(rows: MonitoringRecord[]) {
  const buckets = new Map<string, MonitoringRecord[]>();
  rows.forEach((row) => {
    const key = String(row.dataflow_id ?? row.dataflow_name ?? "unknown");
    const items = buckets.get(key) ?? [];
    items.push(row);
    buckets.set(key, items);
  });
  return Array.from(buckets.entries()).map(([dataflowId, items]) => {
    const latest = items.slice().sort((left, right) => timeValue(right.end_time ?? right.start_time) - timeValue(left.end_time ?? left.start_time))[0] ?? {};
    return {
      dataflow_id: dataflowId,
      dataflow_name: latest.dataflow_name ?? dataflowId,
      stage: latest.stage ?? "unknown",
      operation_type: latest.operation_type ?? "unknown",
      source: maintenanceSourceLabel(latest),
      load_type: latest.destination_load_type ?? latest.destination_operation_type ?? "-",
      latest_status: latest.status ?? "unknown",
      latest_time: latest.end_time ?? latest.start_time,
      run_count: items.length,
      rows_read: items.reduce((sum, item) => sum + num(item, "source_rows_read"), 0),
    };
  }).sort((left, right) => String(left.dataflow_name).localeCompare(String(right.dataflow_name)));
}

function maintenanceSourceLabel(row: Record<string, unknown>) {
  const source = maintenanceSourceParts(row);
  return `${source.connection} · ${source.object}`;
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

function timeValue(value: unknown) {
  const parsed = Date.parse(String(value ?? ""));
  return Number.isFinite(parsed) ? parsed : 0;
}

function DiagnosticsDetailSections({
  row,
  timezoneName,
}: {
  row: Record<string, unknown>;
  timezoneName?: string | null;
}) {
  const evidence = evidenceObject(row.evidence);
  const category = String(row.category ?? "diagnostics");
  const severity = String(row.severity ?? "info");
  const actionItems = diagnosticsActions(row, evidence);
  const impactRows = diagnosticsImpactRows(category, row, evidence);
  return (
    <>
      <section className="monitoring-detail-section monitoring-diagnostics-finding">
        <div className="monitoring-detail-section-header">
          <h3>Finding summary</h3>
          <DiagnosticsSeverityLabel value={severity} />
        </div>
        <div className="monitoring-diagnostics-summary-grid">
          <GroupedDetailCard
            title="Issue"
            rows={[
              ["Category", humanize(category)],
              ["Target", row.target],
              ["Latest", row.latest_time, "latest_time"],
              ["Rule", diagnosticsRuleDescription(category)],
            ]}
            showEmpty
            timezoneName={timezoneName}
          />
          <GroupedDetailCard
            title="What happened"
            rows={[
              ["Issue", row.issue],
              ["Action hint", row.action_hint],
            ]}
            showEmpty
            timezoneName={timezoneName}
          />
        </div>
      </section>

      {impactRows.length ? (
        <section className="monitoring-detail-section">
          <h3>Impact scope</h3>
          <div className="monitoring-diagnostics-impact-grid">
            {impactRows.map(([label, value, field]) => (
              <div key={label} className="monitoring-diagnostics-impact-card">
                <span>{label}</span>
                <strong>{renderGroupedValue(value, timezoneName, field)}</strong>
              </div>
            ))}
          </div>
        </section>
      ) : null}

      <section className="monitoring-detail-section">
        <h3>Evidence</h3>
        <div className="monitoring-job-detail-grid monitoring-diagnostics-evidence-grid">
          {diagnosticsEvidenceCards(category, evidence, timezoneName)}
        </div>
      </section>

      <section className="monitoring-detail-section">
        <h3>Investigation path</h3>
        <ol className="monitoring-diagnostics-action-list">
          {actionItems.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ol>
      </section>

      <section className="monitoring-detail-section">
        <h3>Raw evidence</h3>
        <JsonBlock value={evidence} />
      </section>
    </>
  );
}

function DiagnosticsSeverityLabel({ value }: { value: string }) {
  const normalized = value.toLowerCase();
  const intent = normalized === "bad" || normalized === "error" ? "bad" : normalized;
  return <span className={`diagnostics-severity diagnostics-${intent}`}>{value}</span>;
}

function evidenceObject(value: unknown): Record<string, unknown> {
  if (value && typeof value === "object" && !Array.isArray(value)) return value as Record<string, unknown>;
  if (typeof value === "string" && looksJson(value)) {
    const parsed = parseJsonLike(value);
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) return parsed as Record<string, unknown>;
  }
  return {};
}

function diagnosticsRuleDescription(category: string) {
  switch (category) {
    case "read/cache warning":
      return "A log source emitted a read or cache warning.";
    case "orphan dataflow job id":
      return "Dataflow logs reference a job_id that is missing from job logs.";
    case "job without dataflows":
      return "A job log exists but no child dataflow records were found.";
    case "reconciliation mismatch":
      return "A job total does not match the child dataflow rollup.";
    case "field completeness":
      return "A Monitoring evidence-field group is below completeness threshold.";
    case "source coverage":
      return "A log source has warning evidence in the current filter.";
    default:
      return "Diagnostics evidence needs review.";
  }
}

function diagnosticsImpactRows(category: string, row: Record<string, unknown>, evidence: Record<string, unknown>): DetailRow[] {
  if (category === "reconciliation mismatch") {
    return [
      ["Job ID", evidence.job_id ?? row.target],
      ["Metric", evidence.metric],
      ["Expected", evidence.expected],
      ["Observed", evidence.observed],
      ["Difference", evidence.difference],
    ];
  }
  if (category === "field completeness") {
    return [
      ["Record type", evidence.record_type],
      ["Group", evidence.group],
      ["Records", evidence.records],
      ["Required fields", evidence.required_fields],
      ["Present values", evidence.present_values],
      ["Missing values", evidence.missing_values],
      ["Completeness", hasValue(evidence.completeness_rate) ? `${evidence.completeness_rate}%` : null],
    ];
  }
  if (category === "source coverage") {
    return [
      ["Source", evidence.source ?? row.target],
      ["File kind", evidence.file_kind],
      ["Files", evidence.file_count],
      ["Records", evidence.records],
      ["Job records", evidence.job_records],
      ["Dataflow records", evidence.dataflow_records],
      ["Warnings", evidence.warning_count],
      ["Latest log", evidence.latest_log_at, "latest_log_at"],
      ["Latest ingested", evidence.latest_ingested_at, "latest_ingested_at"],
    ];
  }
  if (category === "orphan dataflow job id" || category === "job without dataflows") {
    return [
      ["Job ID", evidence.job_id ?? row.target],
      ["Dataflow records", evidence.dataflow_records],
      ["Job total dataflows", evidence.job_total_dataflows],
      ["Latest", row.latest_time, "latest_time"],
    ];
  }
  if (category === "read/cache warning") {
    return [
      ["Source/path", evidence.uri ?? evidence.path ?? row.target],
      ["Status", evidence.status ?? evidence.severity],
      ["Message", evidence.message ?? evidence.error],
    ];
  }
  return [
    ["Target", row.target],
    ["Latest", row.latest_time, "latest_time"],
  ];
}

function diagnosticsEvidenceCards(category: string, evidence: Record<string, unknown>, timezoneName?: string | null) {
  if (category === "reconciliation mismatch") {
    return (
      <>
        <GroupedDetailCard
          title="Reconciliation"
          rows={[
            ["Severity", evidence.severity],
            ["Metric", evidence.metric],
            ["Expected", evidence.expected],
            ["Observed", evidence.observed],
            ["Difference", evidence.difference],
          ]}
          showEmpty
          timezoneName={timezoneName}
        />
        <GroupedDetailCard
          title="Job evidence"
          rows={[
            ["Job ID", evidence.job_id],
            ["Status", evidence.status],
          ]}
          showEmpty
          timezoneName={timezoneName}
        />
      </>
    );
  }
  if (category === "field completeness") {
    return (
      <>
        <GroupedDetailCard
          title="Completeness"
          rows={[
            ["Record type", evidence.record_type],
            ["Group", evidence.group],
            ["Completeness", hasValue(evidence.completeness_rate) ? `${evidence.completeness_rate}%` : null],
            ["Missing values", evidence.missing_values],
          ]}
          showEmpty
          timezoneName={timezoneName}
        />
        <GroupedDetailCard
          title="Fields"
          rows={[
            ["Fields", evidence.fields],
            ["Records", evidence.records],
            ["Required fields", evidence.required_fields],
            ["Present values", evidence.present_values],
          ]}
          showEmpty
          timezoneName={timezoneName}
        />
      </>
    );
  }
  if (category === "source coverage") {
    return (
      <>
        <GroupedDetailCard
          title="Source"
          rows={[
            ["Source", evidence.source],
            ["Source ID", evidence.source_id],
            ["File kind", evidence.file_kind],
            ["Files", evidence.file_count],
          ]}
          showEmpty
          timezoneName={timezoneName}
        />
        <GroupedDetailCard
          title="Coverage"
          rows={[
            ["Records", evidence.records],
            ["Job records", evidence.job_records],
            ["Dataflow records", evidence.dataflow_records],
            ["Warning count", evidence.warning_count],
            ["Latest log", evidence.latest_log_at, "latest_log_at"],
            ["Latest ingested", evidence.latest_ingested_at, "latest_ingested_at"],
          ]}
          showEmpty
          timezoneName={timezoneName}
        />
      </>
    );
  }
  if (category === "read/cache warning") {
    return (
      <GroupedDetailCard
        title="Read/cache warning"
        rows={[
          ["Path", evidence.path ?? evidence.uri],
          ["Status", evidence.status ?? evidence.severity],
          ["Message", evidence.message ?? evidence.error],
        ]}
        showEmpty
        timezoneName={timezoneName}
      />
    );
  }
  return (
    <GroupedDetailCard
      title="Evidence fields"
      rows={Object.entries(evidence).slice(0, 12).map(([key, value]) => [humanize(key), value, key] as DetailRow)}
      showEmpty
      timezoneName={timezoneName}
    />
  );
}

function diagnosticsActions(row: Record<string, unknown>, evidence: Record<string, unknown>) {
  const category = String(row.category ?? "");
  const primary = String(row.action_hint ?? "").trim();
  const actions = primary ? [primary] : [];
  if (category === "read/cache warning") {
    actions.push("Validate the source path, storage credentials, and file format.");
    actions.push("Run sync again after fixing the source issue.");
  } else if (category === "orphan dataflow job id") {
    actions.push("Check whether job_run_log files exist for the same run window.");
    actions.push("Compare the job_id in dataflow logs with cached job logs.");
  } else if (category === "job without dataflows") {
    actions.push("Check whether dataflow_run_log files were written and cached for this job_id.");
    actions.push("Inspect the ETL log source coverage for missing dataflow files.");
  } else if (category === "reconciliation mismatch") {
    actions.push("Open the job drawer and compare job totals with child dataflow rows.");
    actions.push(`Review metric ${String(evidence.metric ?? "mismatch")} for this job.`);
  } else if (category === "field completeness") {
    actions.push("Confirm the ETL log version emits this field group.");
    actions.push("Treat this as evidence coverage; it does not determine Diagnostics health.");
  } else if (category === "source coverage") {
    actions.push("Open Sources and validate or sync the affected ETL log path.");
    actions.push("Check latest log and latest ingested timestamps for stale cache evidence.");
  }
  return Array.from(new Set(actions)).filter(Boolean);
}

function FreshnessDetailSections({
  row,
  relatedDataflows,
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
  offset: number;
  limit: number;
  sort: TableSort;
  onSort: (sort: TableSort) => void;
  onPageChange: (offset: number) => void;
  onPageSizeChange: (limit: number) => void;
  onOpenDataflow?: (row: MonitoringRecord) => void;
  timezoneName?: string | null;
}) {
  return (
    <>
      <section className="monitoring-detail-section">
        <h3>Freshness summary</h3>
        <div className="monitoring-freshness-summary-grid">
          <GroupedDetailCard
            title="Freshness"
            rows={[
              ["Latest freshness", row.latest_freshness_at, "latest_freshness_at"],
              ["Latest freshness status", row.latest_freshness_status, "status"],
              ["Age", formatFreshnessAge(row.age_seconds, row.age_days)],
              ["Latest run", row.latest_run_at, "latest_run_at"],
              ["Latest run status", row.latest_run_status, "status"],
              ["Skipped streak", row.skipped_streak, "skipped_streak"],
            ]}
            showEmpty
            timezoneName={timezoneName}
          />
          <GroupedDetailCard
            title="Run counts in filter"
            rows={[
              ["Runs", row.run_count, "run_count"],
              ["Succeeded", row.succeeded_count, "succeeded_count"],
              ["Failed", row.failed_count, "failed_count"],
              ["Skipped", row.skipped_count, "skipped_count"],
              ["Running", row.running_count, "running_count"],
              ["Pending", row.pending_count, "pending_count"],
              ["Last success", row.last_success_at, "last_success_at"],
              ["Last failed", row.last_failed_at, "last_failed_at"],
            ]}
            showEmpty
            timezoneName={timezoneName}
          />
          <GroupedDetailCard
            title="Watermark"
            rows={[
              ["Coverage", row.coverage_state],
              ["Movement", row.movement_state],
              ["Adjustment", row.adjustment_state],
              ["Watermark time", row.watermark_time, "watermark_time"],
              ["Columns", row.source_watermark_columns, "source_watermark_columns"],
              ["Before", row.source_watermark_before, "source_watermark_before"],
              ["Effective", row.source_watermark_effective, "source_watermark_effective"],
              ["After", row.source_watermark_after, "source_watermark_after"],
            ]}
            showEmpty
            timezoneName={timezoneName}
            className="monitoring-freshness-watermark-card"
          />
        </div>
      </section>

      <section className="monitoring-detail-section">
        <h3>Latest metadata</h3>
        <div className="monitoring-dataflow-detail-grid monitoring-latest-metadata-grid">
          <GroupedDetailCard
            title="Dataflow"
            rows={[
              ["Dataflow ID", row.dataflow_id],
              ["Workspace ID", row.workspace_id],
              ["Name", row.dataflow_name],
              ["Description", row.dataflow_description],
              ["Stage", row.stage],
              ["Operation", row.operation_type],
              ["Processing mode", row.processing_mode],
              ["Active", row.is_active],
            ]}
            showEmpty
            timezoneName={timezoneName}
          />
          <GroupedDetailCard
            title="Source"
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
              ["Filter", row.source_filter_expression, "source_filter_expression"],
              ["Configure", row.source_configure, "source_configure"],
            ]}
            showEmpty
            timezoneName={timezoneName}
          />
          <GroupedDetailCard
            title="Transform"
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
              ["Target", row.target],
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

      {hasValue(row.latest_error_message) ? (
        <section className="monitoring-detail-section monitoring-error-message-section">
          <h3>Latest error message</h3>
          <ErrorMessageBlock value={detailValue(row, "latest_error_message", timezoneName)} />
        </section>
      ) : null}

      <section className="monitoring-detail-section">
        <div className="monitoring-detail-section-header monitoring-freshness-runs-header">
          <div className="monitoring-child-dataflows-header">
            <h3>Dataflow runs</h3>
            <small>{relatedDataflows.length} runs in current filter</small>
          </div>
          <TablePager
            limit={limit}
            offset={offset}
            loadedRows={Math.min(limit, Math.max(0, relatedDataflows.length - offset))}
            totalRows={relatedDataflows.length}
            loading={false}
            onPageChange={onPageChange}
            onPageSizeChange={onPageSizeChange}
          />
        </div>
        <DataTable
          rows={relatedDataflows}
          columns={[
            { key: "dataflow_run_id", label: "Run", sortable: true, width: 120, render: (run) => compactRunId(run.dataflow_run_id) },
            { key: "context", label: "Context", sortable: true, sortKey: "stage", width: 112, render: (run) => <FreshnessRunContextCell row={run} /> },
            { key: "start_time", label: "Time", sortable: true, width: 180, render: (run) => <FreshnessRunTimeCell row={run} timezoneName={timezoneName} /> },
            { key: "status", label: "Status", sortable: true, autoFit: true, minWidth: 76, maxWidth: 104, render: (run) => <StatusCell row={run} /> },
            { key: "duration_seconds", label: "Duration", sortable: true, autoFit: true, minWidth: 82, maxWidth: 112, render: (run) => formatSeconds(num(run, "duration_seconds")) },
            { key: "volume", label: "Volume", sortable: true, sortKey: "source_rows_read", autoFit: true, minWidth: 90, maxWidth: 150, render: (run) => <FreshnessRunVolumeCell row={run} /> },
            { key: "movement_state", label: "Watermark", sortable: true, autoFit: true, minWidth: 106, maxWidth: 150, render: (run) => <DrawerWatermarkBadge row={run} /> },
            { key: "phase_health", label: "Phase", sortable: true, width: 100, render: (run) => <FreshnessRunPhaseCell row={run} /> },
            { key: "error_preview", label: "Issue", sortable: true, width: 220, fillPriority: "last", className: "monitoring-child-issue-column", render: (run) => <IssueCell row={run} /> },
          ]}
          maxRows={limit}
          offset={offset}
          onRowClick={onOpenDataflow}
          sort={sort}
          onSort={onSort}
          fixedLayout
          className="monitoring-child-dataflows-table"
          timezoneName={timezoneName}
        />
      </section>
    </>
  );
}

function FailureEvidenceSections({ row, timezoneName }: { row: Record<string, unknown>; timezoneName?: string | null }) {
  const message = failureErrorMessage(row);
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
    </section>
  );
}

function compactRunId(value: unknown) {
  const textValue = String(value ?? "").trim();
  if (!textValue) return "-";
  return textValue.length > 18 ? `${textValue.slice(0, 8)}...${textValue.slice(-6)}` : textValue;
}

function FreshnessRunTimeCell({ row, timezoneName }: { row: Record<string, unknown>; timezoneName?: string | null }) {
  const start = detailValue(row, "start_time", timezoneName);
  const end = detailValue(row, "end_time", timezoneName);
  return (
    <span
      className="freshness-run-stack-cell"
      title={[
        `Start: ${String(start ?? "-")}`,
        `End: ${String(end ?? "-")}`,
      ].join("\n")}
    >
      <strong>{start}</strong>
      <small>{`→ ${end}`}</small>
    </span>
  );
}

function FreshnessRunContextCell({ row }: { row: Record<string, unknown> }) {
  const stage = String(row.stage || "unknown");
  const operation = String(row.operation_type || "unknown");
  return (
    <span
      className="freshness-run-stack-cell"
      title={[
        `Stage: ${stage}`,
        `Operation: ${operation}`,
        `Destination operation: ${row.destination_operation_type ?? "-"}`,
        `Load type: ${row.destination_load_type ?? row.load_type ?? "-"}`,
      ].join("\n")}
    >
      <strong>{stage}</strong>
      <small>{operation}</small>
    </span>
  );
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

function formatPhasePercent(value: number) {
  return `${Math.round(value * 100) / 100}%`;
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
          <div key={label} className={isBlock ? "is-block-value" : undefined}>
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
    return formatTimestampForDisplay(value, timezoneName);
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

function isGroupedBlockValue(value: unknown, field?: string) {
  if (!hasValue(value) || isValidElement(value) || isSemanticValue(value)) return false;
  if (field && (SQL_BLOCK_FIELDS.has(field) || LIST_BLOCK_FIELDS.has(field) || JSON_BLOCK_FIELDS.has(field))) return true;
  if (Array.isArray(value) || (value && typeof value === "object")) return true;
  return typeof value === "string" && looksJson(value);
}

function isSemanticValue(value: unknown): value is SemanticValueModel {
  return Boolean(value && typeof value === "object" && "kind" in value);
}

function semanticNumber(field: string | undefined, value: number): SemanticValueModel {
  const lowerField = String(field ?? "").toLowerCase();
  let intent: SemanticIntent = "neutral";
  if (lowerField.includes("succeeded") || lowerField.includes("success")) intent = "success";
  if (lowerField.includes("failed") || lowerField.includes("error")) intent = value > 0 ? "failed" : "neutral";
  if (lowerField.includes("skipped")) intent = value > 0 ? "skipped" : "neutral";
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
        <span className={`monitoring-semantic-value is-${statusIntent}`}>{value.status}</span>
        <span aria-hidden="true">·</span>
        <span className={`monitoring-semantic-value is-${countIntent}`}>{display({ value: value.mismatch }, "value")}</span>
      </span>
    );
  }
  if (value.kind === "count") {
    return <span className={`monitoring-semantic-value is-${value.intent ?? "neutral"}`}>{display({ value: value.value }, "value")}</span>;
  }
  return <span className={`monitoring-semantic-value is-${semanticIntent(value)}`}>{value.value}</span>;
}

function semanticIntent(value: SemanticValueModel) {
  if ("intent" in value && value.intent) return value.intent;
  if (value.kind !== "status") return "neutral";
  const normalized = value.value.toLowerCase();
  if (normalized === "succeeded") return "success";
  if (normalized === "failed") return "failed";
  if (normalized === "skipped") return "skipped";
  return "neutral";
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
    <section className="monitoring-detail-section">
      <div className="monitoring-detail-section-header">
        <h3>Linked job</h3>
        <button className="text-action" type="button" onClick={() => onOpenJob?.(jobRow)}>
          Open job
        </button>
      </div>
      <div className="monitoring-detail-grid">
        <div className="monitoring-detail-item">
          <span>Job</span>
          <strong>{jobId}</strong>
        </div>
        <div className="monitoring-detail-item">
          <span>Status</span>
          <strong><StatusCell row={jobRow} /></strong>
        </div>
        <div className="monitoring-detail-item">
          <span>Duration</span>
          <strong>{formatSeconds(num(jobRow, "duration_seconds"))}</strong>
        </div>
      </div>
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

function RawPayloadSection({ row }: { row: Record<string, unknown> }) {
  const [copied, setCopied] = useState(false);
  const payload = JSON.stringify(row, null, 2);
  return (
    <details className="monitoring-raw-detail">
      <summary>Raw payload</summary>
      <div className="monitoring-json-box monitoring-raw-json-box">
        <button
          className="icon-action small monitoring-json-copy"
          type="button"
          aria-label="Copy raw payload JSON"
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

function sortRows<T extends Record<string, unknown>>(rows: T[], sort: TableSort) {
  return [...rows].sort((left, right) => {
    const leftValue = left[sort.sortBy];
    const rightValue = right[sort.sortBy];
    const result = compareValues(leftValue, rightValue);
    return sort.sortDir === "desc" ? -result : result;
  });
}

function compareValues(left: unknown, right: unknown) {
  if (left === right) return 0;
  if (!hasValue(left)) return -1;
  if (!hasValue(right)) return 1;
  const leftNumber = Number(left);
  const rightNumber = Number(right);
  if (Number.isFinite(leftNumber) && Number.isFinite(rightNumber)) return leftNumber - rightNumber;
  const leftTime = typeof left === "string" ? Date.parse(left) : Number.NaN;
  const rightTime = typeof right === "string" ? Date.parse(right) : Number.NaN;
  if (Number.isFinite(leftTime) && Number.isFinite(rightTime)) return leftTime - rightTime;
  return String(left).localeCompare(String(right), undefined, { numeric: true, sensitivity: "base" });
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
