import { ArrowLeft, ArrowRight, Boxes, BriefcaseBusiness, Check, ChevronRight, Clock3, Copy, FileText, SearchCheck, Workflow, X } from "lucide-react";
import { isValidElement, useEffect, useMemo, useState, type CSSProperties, type ReactNode } from "react";
import { createPortal } from "react-dom";
import type { MonitoringRecord } from "../../shared/api/domainTypes";
import { useDrawerEscape } from "../../shared/hooks/useDrawerEscape";
import { formatTimestampForDisplay, hasExplicitTimezone, isTimestampFieldName } from "../../shared/time";
import { lifecycleStatusFromField, lifecycleStatusPresentation, type LifecycleStatus } from "../../shared/statusPresentation";
import { LineageFormatIcon } from "../lineage/components/LineageFormatIcon";
import { DataTable, StatusCell, display, formatBytes, formatNumber, formatSeconds, num, type TableColumn, type TableSort } from "./MonitoringCharts";
import {
  diagnosticsCategoryLabel,
  diagnosticsEvidenceItems,
  diagnosticsInvestigationActions,
  diagnosticsLinkedJobRow,
  diagnosticsRuleDescription,
  diagnosticsSeverityPresentation,
} from "./diagnosticsPresentation";
import { formatMaintenanceLag, maintenanceFormatIconKind, maintenanceTableHealthClass, maintenanceTableHealthLabel, maintenanceTableHealthTone } from "./maintenancePresentation";
import { CompactNumberValue, formatCompactNumber, formatPhasePercent, monitoringEndpointPresentation, TablePager } from "./components/monitoringPrimitives";
import { SystemLogViewer } from "./SystemLogViewer";
import { FreshnessDrawerHealthTone, FreshnessRunTimeCell, IssueCell, MaintenanceHealthChip, copyToClipboard, detailValue, firstValue, formatFreshnessAge, freshnessDrawerHealth, freshnessRunTimeLines, hasValue, highlightJson, humanize, isErrorField, jobStatusTone } from "./details/detailPrimitives";
import "./monitoring.css";
import { lazy } from "react";
const JobDetailSections = lazy(() => import("./details/JobDetails").then((module) => ({ default: module.JobDetailSections })));
const DataflowDetailSections = lazy(() => import("./details/DataflowDetails").then((module) => ({ default: module.DataflowDetailSections })));
const FreshnessDetailSections = lazy(() => import("./details/FreshnessDetails").then((module) => ({ default: module.FreshnessDetailSections })));
const VolumeDetailSections = lazy(() => import("./details/VolumeDetails").then((module) => ({ default: module.VolumeDetailSections })));
const MaintenanceDetailSections = lazy(() => import("./details/MaintenanceDetails").then((module) => ({ default: module.MaintenanceDetailSections })));
const DiagnosticsDetailSections = lazy(() => import("./details/DiagnosticsDetails").then((module) => ({ default: module.DiagnosticsDetailSections })));

export type MonitoringDetailKind = "job" | "dataflow" | "failure" | "performance" | "maintenance" | "freshness" | "volume" | "diagnostics";

export interface MonitoringDetailDrawerProps {
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
                compactNumbers
                columns={jobChildDataflowColumns(timezoneName)}
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
                compactNumbers
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

export function jobChildDataflowColumns(timezoneName?: string | null): TableColumn<MonitoringRecord>[] {
  return [
    { key: "dataflow_name", label: "Dataflow", sortable: true, width: 160 },
    {
      key: "stage_operation",
      label: "Stage / operation",
      sortable: true,
      sortKey: "stage",
      autoFit: true,
      minWidth: 112,
      maxWidth: 180,
      render: (child) => <ChildDataflowStageOperationCell row={child} />,
      measureValue: childDataflowStageOperationLines,
    },
    {
      key: "start_time",
      label: "Time",
      sortable: true,
      autoFit: true,
      minWidth: 144,
      maxWidth: 216,
      render: (child) => <FreshnessRunTimeCell row={child} timezoneName={timezoneName} />,
      measureValue: (child, activeTimezone) => freshnessRunTimeLines(child, activeTimezone),
    },
    { key: "status", label: "Status", sortable: true, autoFit: true, minWidth: 76, maxWidth: 104, render: (child) => <StatusCell row={child} /> },
    { key: "duration_seconds", label: "Duration", sortable: true, autoFit: true, minWidth: 82, maxWidth: 112, render: (child) => formatSeconds(num(child, "duration_seconds")) },
    {
      key: "row_counts",
      label: "Rows",
      sortable: true,
      sortKey: "source_rows_read",
      autoFit: true,
      minWidth: 118,
      maxWidth: 180,
      render: (child) => <ChildDataflowRowsCell row={child} />,
      measureValue: childDataflowCompactRowLines,
    },
    { key: "error_preview", label: "Issue", sortable: true, width: 240, className: "monitoring-child-issue-column", render: (child) => <IssueCell row={child} /> },
  ];
}

export function childDataflowStageOperationLines(row: MonitoringRecord) {
  return [String(row.stage || "-"), String(row.operation_type || "-")];
}

export function childDataflowRowLines(row: MonitoringRecord) {
  return [
    `${formatNumber(num(row, "source_rows_read"))} read`,
    `${formatNumber(num(row, "destination_rows_written"))} written`,
  ];
}

function childDataflowCompactRowLines(row: MonitoringRecord) {
  return [
    `${formatCompactNumber(num(row, "source_rows_read"))} read`,
    `${formatCompactNumber(num(row, "destination_rows_written"))} written`,
  ];
}

function ChildDataflowStageOperationCell({ row }: { row: MonitoringRecord }) {
  const [stage, operation] = childDataflowStageOperationLines(row);
  return (
    <span className="freshness-run-stack-cell" title={`Stage: ${stage}\nOperation: ${operation}`}>
      <strong>{stage}</strong>
      <small>{operation}</small>
    </span>
  );
}

function ChildDataflowRowsCell({ row }: { row: MonitoringRecord }) {
  const rowsRead = num(row, "source_rows_read");
  const rowsWritten = num(row, "destination_rows_written");
  return (
    <span className="freshness-run-stack-cell" title={`Rows read: ${formatNumber(rowsRead)}\nRows written: ${formatNumber(rowsWritten)}`}>
      <strong><CompactNumberValue value={rowsRead} /> read</strong>
      <small><CompactNumberValue value={rowsWritten} /> written</small>
    </span>
  );
}

const WATERMARK_FIELDS = [
  "movement_state",
  "source_watermark_before",
  "source_watermark_after",
  "source_watermark_effective",
  "source_watermark_columns",
];

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

function DiagnosticsSeverityLabel({ value }: { value: string }) {
  const presentation = diagnosticsSeverityPresentation(value);
  return <span className={`diagnostics-severity diagnostics-${presentation.tone}`}>{presentation.label}</span>;
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

function volumeRunEstRowsWritten(row: MonitoringRecord) {
  const observed = num(row, "destination_rows_written");
  const destinationIdentity = [row.destination_connection_type, row.destination_format, row.destination_name, row.destination_path]
    .map((value) => String(value ?? "").toLowerCase())
    .join(" ");
  const isLakehouse = ["lakehouse", "delta", "iceberg", "onelake", "deltalake"].some((token) => destinationIdentity.includes(token));
  return !isLakehouse && String(row.status ?? "").toLowerCase() === "succeeded" ? num(row, "source_rows_read") || observed : observed;
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
