import { ArrowLeft, Boxes, BriefcaseBusiness, Check, ChevronRight, Clock3, Copy, FileText, SearchCheck, Workflow, X } from "lucide-react";
import { Suspense, useMemo, useState, type CSSProperties } from "react";
import { createPortal } from "react-dom";
import type { MonitoringRecord } from "../../shared/api/domainTypes";
import { numberValue } from "../../shared/api/transportValues";
import { useDrawerEscape } from "../../shared/hooks/useDrawerEscape";
import { lifecycleStatusPresentation } from "../../shared/statusPresentation";
import { LineageFormatIcon } from "../../shared/components/FormatIcon";
import { StatusCell, type TableSort } from "../../shared/components/DataTable";
import { Help } from "../../shared/components/Help";
import {
  diagnosticsCategoryLabel,
  diagnosticsSeverityPresentation,
} from "./diagnosticsPresentation";
import { maintenanceFormatIconKind } from "./maintenancePresentation";
import { destinationTooltip, presentDestination } from "./destinationPresentation";
import { monitoringConnectionStageClass } from "./monitoringStagePresentation";
import { SystemLogViewer } from "./SystemLogViewer";
import { monitoringRowSessionScope, type MonitoringRegistryOrigin, type MonitoringRegistryRunReference } from "./monitoringQueries";
import { formatRawRecordJson, rawRecordJsonForCopy } from "../../shared/api/monitoringAdapters";
import { FreshnessDrawerHealthTone, copyToClipboard, detailValue, firstValue, formatFreshnessAge, freshnessDrawerHealth, hasValue, highlightJson, humanize, isErrorField, jobStatusTone } from "./details/detailPrimitives";
import { buildJobStory, jobIdentitySeed } from "./details/jobStoryModel";
import "./monitoring.css";
import { lazy } from "react";
export { jobChildDataflowColumns, childDataflowRowLines, childDataflowStageOperationLines } from "./details/JobExecution";
import { JobDetailSections, preloadJobDetailSections } from "./monitoringJobDetailModule";
const DataflowDetailSections = lazy(() => import("./details/DataflowDetails").then((module) => ({ default: module.DataflowDetailSections })));
const FreshnessDetailSections = lazy(() => import("./details/FreshnessDetails").then((module) => ({ default: module.FreshnessDetailSections })));
const DataflowRegistryDetailSections = lazy(() => import("./details/DataflowRegistryDetails").then((module) => ({ default: module.DataflowRegistryDetailSections })));
const VolumeDetailSections = lazy(() => import("./details/VolumeDetails").then((module) => ({ default: module.VolumeDetailSections })));
const MaintenanceDetailSections = lazy(() => import("./details/MaintenanceDetails").then((module) => ({ default: module.MaintenanceDetailSections })));
const DiagnosticsDetailSections = lazy(() => import("./details/DiagnosticsDetails").then((module) => ({ default: module.DiagnosticsDetailSections })));

export type MonitoringDetailKind = "job" | "dataflow" | "dataflow-registry" | "failure" | "performance" | "maintenance" | "freshness" | "volume" | "diagnostics";

export interface MonitoringDetailDrawerProps {
  background?: boolean;
  jobDetailAvailability?: "loading" | "error" | "not-found" | "ready";
  destinationDetailAvailability?: "loading" | "error" | "not-found" | "ready";
  destinationScopeLabel?: string;
  onRetryDestinationDetail?: () => void;
  onRetryJobDetail?: () => void;
  relatedDataflowsError?: string | null;
  onRetryRelatedDataflows?: () => void;
  dataflowDetailAvailability?: "loading" | "error" | "not-found" | "ready";
  onRetryDataflowDetail?: () => void;
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
  timezoneName?: string | null;
  jobLabel?: string | null;
  dataflowLabel?: string | null;
  onOpenDataflow?: (row: MonitoringRecord, reference?: MonitoringRegistryRunReference) => void;
  onOpenJob?: (row: Record<string, unknown>) => void;
  onOpenSources?: () => void;
  onBack?: () => void;
  onClose: () => void;
  onOpenRegistrySection?: (section: "read-watermark" | "configuration" | "technical") => void;
  registrySectionStates?: Partial<Record<"read-watermark" | "configuration" | "technical", { loading: boolean; ready?: boolean; empty?: boolean; error: string | null; retry: () => void }>>;
  registrySectionRows?: Partial<Record<"read-watermark" | "configuration" | "technical", Record<string, unknown>>>;
  registryAnalyticsGeneration?: string | null;
  registryOrigin?: MonitoringRegistryOrigin | null;
  onOpenRegistryRun?: (row: MonitoringRecord, reference?: MonitoringRegistryRunReference) => void;
  registryRuntimeRows?: Record<string, unknown>[];
  registryRuntimeTotal?: number;
  registryRuntimeOffset?: number;
  registryRuntimeLimit?: number;
  registryRuntimeLoading?: boolean;
  registryRuntimeError?: string | null;
  onRetryRegistryRuntime?: () => void;
  registryRuntimeSort?: TableSort;
  onRegistryRuntimePageChange?: (offset: number) => void;
  onRegistryRuntimePageSizeChange?: (limit: number) => void;
  onRegistryRuntimeSort?: (sort: TableSort) => void;
  registryOverviewState?: { loading: boolean; ready: boolean; preview?: boolean; error: string | null; retry: () => void };
  registryExecutionRows?: MonitoringRecord[];
  registryExecutionTotal?: number;
  registryExecutionOffset?: number;
  registryExecutionLimit?: number;
  onRegistryExecutionPageChange?: (offset: number) => void;
  onRegistryExecutionPageSizeChange?: (limit: number) => void;
  registryExecutionLoading?: boolean;
  registryExecutionError?: string | null;
  onRetryRegistryExecution?: () => void;
  registryExecutionSort?: TableSort;
  onRegistryExecutionSort?: (sort: TableSort) => void;
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
  background = false,
  kind,
  dataflowDetailAvailability = "ready",
  onRetryDataflowDetail,
  row,
  environmentId,
  jobDetailAvailability = "ready",
  destinationDetailAvailability = "ready",
  destinationScopeLabel,
  onRetryDestinationDetail,
  onRetryJobDetail,
  relatedDataflowsError,
  onRetryRelatedDataflows,
  relatedDataflows = [],
  relatedDataflowsTotal = relatedDataflows.length,
  relatedDataflowsOffset = 0,
  relatedDataflowsLimit = 100,
  relatedDataflowsSort = DEFAULT_RELATED_DATAFLOW_SORT,
  relatedDataflowsLoading = false,
  onRelatedDataflowsPageChange,
  onRelatedDataflowsPageSizeChange,
  onRelatedDataflowsSort,
  timezoneName,
  jobLabel,
  dataflowLabel,
  onOpenDataflow,
  onOpenJob,
  onOpenSources,
  onBack,
  onClose,
  onOpenRegistrySection,
  registrySectionStates,
  registrySectionRows,
  registryAnalyticsGeneration,
  registryOrigin,
  onOpenRegistryRun,
  registryRuntimeRows,
  registryRuntimeTotal,
  registryRuntimeOffset,
  registryRuntimeLimit,
  registryRuntimeLoading,
  registryRuntimeError,
  onRetryRegistryRuntime,
  registryRuntimeSort,
  onRegistryRuntimePageChange,
  onRegistryRuntimePageSizeChange,
  onRegistryRuntimeSort,
  registryOverviewState,
  registryExecutionRows,
  registryExecutionTotal,
  registryExecutionOffset,
  registryExecutionLimit,
  onRegistryExecutionPageChange,
  onRegistryExecutionPageSizeChange,
  registryExecutionLoading,
  registryExecutionError,
  onRetryRegistryExecution,
  registryExecutionSort,
  onRegistryExecutionSort,
}: MonitoringDetailDrawerProps) {
  const headerStatus = useMemo(() => kind === "job" ? buildJobStory(row, jobDetailAvailability).reported.status : row.status, [kind, row, jobDetailAvailability]);
  const title = detailTitle(row, kind);
  const [headerCopied, setHeaderCopied] = useState(false);
  const [systemLogsOpen, setSystemLogsOpen] = useState(false);

  useDrawerEscape(onClose, !systemLogsOpen);

  const sortableRelatedDataflows = relatedDataflows;
  const handleChildDataflowSort = (nextSort: TableSort) => {
    onRelatedDataflowsSort?.(nextSort);
  };
  const copyTitle = headerCopyValue(row, kind, title);
  const copyLabel = kind === "job" ? "job id" : kind === "dataflow" ? "dataflow run id" : kind === "dataflow-registry" ? "dataflow id" : kind === "maintenance" ? "destination location" : `${kindLabel(kind)} title`;
  const jobId = typeof row.job_id === "string" ? row.job_id : "";
  const sourceIdValue = row._source_id ?? row.source_id;
  const sourceId = sourceIdValue === undefined || sourceIdValue === null
    ? undefined
    : numberValue(sourceIdValue);
  const logSessionId = typeof row.log_session_id === "string" ? row.log_session_id : "";
  const logSessionScope = monitoringRowSessionScope(row);
  const dataflowId = kind === "dataflow" && typeof row.dataflow_id === "string" ? row.dataflow_id : "";
  const dataflowRunId = kind === "dataflow" && typeof row.dataflow_run_id === "string" ? row.dataflow_run_id : "";
  const eventName = typeof row.event_name === "string" ? row.event_name : "";
  const resolvedJobLabel = jobLabel ?? (typeof row.job_name === "string" ? row.job_name : undefined);
  const resolvedDataflowLabel = dataflowLabel ?? (typeof row.dataflow_name === "string" ? row.dataflow_name : undefined);
  const drawerStatusClass = kind === "job" || kind === "dataflow" ? jobStatusTone(headerStatus) : "";
  const jobStatusPresentation = kind === "job" ? lifecycleStatusPresentation(headerStatus) : null;
  const jobStatusStyle = jobStatusPresentation ? {
    "--monitoring-job-status-surface": jobStatusPresentation.drawerSurface,
    "--monitoring-job-status-border": jobStatusPresentation.drawerBorder,
  } as CSSProperties : undefined;
  const freshnessHealthClass = kind === "freshness" ? ` is-freshness-${freshnessDrawerHealth(row).tone}` : "";
  const diagnosticsHealthClass = kind === "diagnostics" ? ` is-diagnostics-${diagnosticsSeverityPresentation(row.severity).tone}` : "";
  return createPortal(
    <div className={`metadata-drawer-backdrop monitoring-detail-backdrop${background ? " is-background" : ""}`} onMouseDown={background ? undefined : onClose}>
      <aside className={`metadata-drawer monitoring-detail-drawer is-${kind}${drawerStatusClass ? ` ${drawerStatusClass}` : ""}${freshnessHealthClass}${diagnosticsHealthClass}`} style={jobStatusStyle} aria-label="Monitoring details" onMouseDown={(event) => event.stopPropagation()}>
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
                    <Help as="button"
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
                    </Help>
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
                    <Help as="button"
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
                    </Help>
                  ) : null}
                </div>
              </>
            ) : (
              <>
                <div className="monitoring-detail-kind-row">
                  {kind === "job" ? <BriefcaseBusiness className="monitoring-detail-kind-icon" size={14} aria-hidden="true" /> : null}
                  {kind === "dataflow" || kind === "dataflow-registry" ? <Workflow className="monitoring-detail-kind-icon" size={14} aria-hidden="true" /> : null}
                  {kind === "volume" ? <Boxes className="monitoring-detail-kind-icon" size={14} aria-hidden="true" /> : null}
                  {kind === "maintenance" ? (
                    <LineageFormatIcon
                      kind={maintenanceFormatIconKind(row.destination_format ?? row.format ?? row.destination_connection_type)}
                      label={String(row.destination_format ?? row.format ?? "destination table")}
                      size={16}
                    />
                  ) : null}
                  <span className="monitoring-detail-kind-label">{kind === "job" ? "Job run" : kind === "dataflow" ? "Dataflow run" : kind === "dataflow-registry" ? "Dataflow" : kind === "volume" ? "Volume" : kind === "maintenance" ? "Destination" : kindLabel(kind)}</span>
                  {kind === "job" || kind === "dataflow" ? <StatusCell row={{ status: headerStatus }} /> : null}
                  {kind === "maintenance" ? <DestinationIdentityChip row={row} /> : null}
                </div>
                <div className="monitoring-detail-title-row">
                  <h2>{title}</h2>
                  {copyTitle && kind !== "dataflow-registry" ? (
                    <Help as="button"
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
                    </Help>
                  ) : null}
                  {environmentId && jobId && kind !== "volume" && kind !== "maintenance" ? (
                    <Help as="button"
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
                    </Help>
                  ) : null}
                </div>
                {kind === "maintenance" ? <><MaintenanceHeaderContext row={row} /><small className="destination-drawer-scope">{destinationScopeLabel || "Current filters"}</small></> : null}
              </>
            )}
          </div>
          <button className="icon-action" type="button" aria-label="Close monitoring details" onClick={onClose}>
            <X size={18} />
          </button>
        </header>
        <div className="metadata-drawer-body monitoring-detail-body">
          {kind === "dataflow" && row.job_id ? <LinkedJobSection row={row} onOpenJob={onOpenJob} /> : null}
          {kind === "job" ? (
            <Suspense fallback={<section className="monitoring-detail-section" role="status">Loading job details…</section>}>
              <JobDetailSections key={`${String(row._source_id ?? row.source_id ?? "all")}:${monitoringRowSessionScope(row)}:${String(row.log_session_id ?? "")}:${String(row.job_id)}`} row={row} availability={jobDetailAvailability} onRetry={onRetryJobDetail} timezoneName={timezoneName}
                execution={{ rows: sortableRelatedDataflows, total: relatedDataflowsTotal, offset: relatedDataflowsOffset, limit: relatedDataflowsLimit, sort: relatedDataflowsSort, loading: relatedDataflowsLoading, error: relatedDataflowsError, onRetry: onRetryRelatedDataflows, onPageChange: onRelatedDataflowsPageChange, onPageSizeChange: onRelatedDataflowsPageSizeChange, onSort: handleChildDataflowSort, onOpenDataflow }} />
            </Suspense>
          ) : kind === "dataflow" ? (
            <DataflowDetailSections key={`${row._source_id}:${row.log_session_id}:${row.dataflow_run_id}`} row={row} timezoneName={timezoneName} availability={dataflowDetailAvailability} onRetry={onRetryDataflowDetail} />
          ) : kind === "dataflow-registry" ? (
            <Suspense fallback={<section className="monitoring-detail-section" role="status">Loading dataflow registry details…</section>}>
              <DataflowRegistryDetailSections key={`${environmentId ?? "environment"}:${String(row.dataflow_id ?? "")}`} row={row} timezoneName={timezoneName} overviewState={registryOverviewState} onSectionOpen={onOpenRegistrySection} sectionStates={registrySectionStates} sectionRows={registrySectionRows} analyticsGeneration={registryAnalyticsGeneration} registryOrigin={registryOrigin} runtimeRows={registryRuntimeRows} runtimeTotal={registryRuntimeTotal} runtimeOffset={registryRuntimeOffset} runtimeLimit={registryRuntimeLimit} runtimeLoading={registryRuntimeLoading} runtimeError={registryRuntimeError} onRetryRuntime={onRetryRegistryRuntime} runtimeSort={registryRuntimeSort} onRuntimePageChange={onRegistryRuntimePageChange} onRuntimePageSizeChange={onRegistryRuntimePageSizeChange} onRuntimeSort={onRegistryRuntimeSort} onOpenRun={onOpenRegistryRun} executionRows={registryExecutionRows} executionTotal={registryExecutionTotal} executionOffset={registryExecutionOffset} executionLimit={registryExecutionLimit} onExecutionPageChange={onRegistryExecutionPageChange} onExecutionPageSizeChange={onRegistryExecutionPageSizeChange} executionLoading={registryExecutionLoading} executionError={registryExecutionError} onRetryExecution={onRetryRegistryExecution} executionSort={registryExecutionSort} onExecutionSort={onRegistryExecutionSort} onOpenDataflow={onOpenDataflow} />
            </Suspense>
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
              loading={relatedDataflowsLoading}
              error={relatedDataflowsError}
              onRetry={onRetryRelatedDataflows}
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
              detailAvailability={destinationDetailAvailability}
              onRetryDetail={onRetryDestinationDetail}
              historyError={relatedDataflowsError}
              onRetryHistory={onRetryRelatedDataflows}
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
            <DiagnosticsDetailSections row={row} timezoneName={timezoneName} onOpenJob={onOpenJob} onOpenDataflow={onOpenDataflow} onOpenSources={onOpenSources} />
          ) : (
            <DetailSection title="Detail" row={row} fields={PRIMARY_FIELDS} timezoneName={timezoneName} />
          )}
          {kind !== "job" && kind !== "dataflow" && kind !== "dataflow-registry" && kind !== "freshness" && kind !== "volume" && kind !== "maintenance" && kind !== "diagnostics" ? (
            <>
              <DetailSection title="Source runtime" row={row} fields={SOURCE_FIELDS} timezoneName={timezoneName} />
              <DetailSection title="Transform runtime" row={row} fields={TRANSFORM_FIELDS} timezoneName={timezoneName} />
              <DetailSection title="Destination runtime" row={row} fields={DESTINATION_FIELDS} timezoneName={timezoneName} />
              <DetailSection title="Watermark" row={row} fields={WATERMARK_FIELDS} wide timezoneName={timezoneName} />
              <DetailSection title="Errors and notes" row={row} fields={ERROR_FIELDS} wide timezoneName={timezoneName} />
            </>
          ) : null}
          {kind !== "dataflow" && kind !== "dataflow-registry" && kind !== "job" && kind !== "freshness" && kind !== "maintenance" ? <RawPayloadSection row={row} label={kind === "diagnostics" ? "Full evidence" : "Raw payload"} /> : null}
        </div>
      </aside>
      {systemLogsOpen && environmentId && jobId ? (
        <SystemLogViewer
          environmentId={environmentId}
          jobId={jobId}
          jobLabel={resolvedJobLabel}
          sourceId={sourceId}
          logSessionScope={logSessionScope}
          logSessionId={logSessionScope === "exact" ? logSessionId : undefined}
          dataflowId={dataflowId || undefined}
          dataflowLabel={resolvedDataflowLabel}
          dataflowRunId={dataflowRunId || undefined}
          eventName={eventName || undefined}
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
  "source_watermark_columns",
];

function MaintenanceHeaderContext({ row }: { row: Record<string, unknown> }) {
  const identity = presentDestination(row);
  const stageClass = monitoringConnectionStageClass(identity.connection);
  return (
    <div className={["monitoring-maintenance-header-context", stageClass].filter(Boolean).join(" ")}>
      <Help title={destinationTooltip(identity)}>{identity.subtitle}</Help>
    </div>
  );
}

function DestinationIdentityChip({ row }: { row: Record<string, unknown> }) {
  const identity = String(row.identity_status ?? "unknown").trim().toLowerCase();
  const provenance = identityProvenanceLabel(row);
  const label = identity === "resolved"
    ? provenance === "published metadata fallback" ? "Resolved · metadata fallback" : "Resolved"
    : identity === "unresolved"
      ? "Identity unresolved"
      : identity === "conflict"
        ? "Identity conflict"
        : "Identity unknown";
  const reason = String(row.identity_reason ?? row.attention_reason ?? "").trim();
  const title = [reason, provenance ? `Source: ${provenance}` : ""].filter(Boolean).join(" · ") || label;
  const tone = identity === "conflict" ? "is-conflict" : identity === "unresolved" ? "is-unresolved" : identity === "resolved" ? "is-succeeded" : "is-unknown";
  return <Help className={`monitoring-destination-identity-chip ${tone}`} title={title}>{label}</Help>;
}

function identityProvenanceLabel(row: Record<string, unknown>) {
  const provenance = row.identity_provenance;
  if (!provenance || typeof provenance !== "object" || Array.isArray(provenance)) return "";
  const values = Object.values(provenance as Record<string, unknown>);
  if (values.includes("metadata")) return "published metadata fallback";
  if (values.includes("captured")) return "captured endpoint";
  if (values.includes("derived")) return "derived identity";
  return "";
}

function DiagnosticsSeverityLabel({ value }: { value: string }) {
  const presentation = diagnosticsSeverityPresentation(value);
  return <span className={`diagnostics-severity diagnostics-${presentation.tone}`}>{presentation.label}</span>;
}

function FreshnessHeaderChips({ row }: { row: Record<string, unknown> }) {
  const health = freshnessDrawerHealth(row);
  const latestStatus = String(row.latest_run_status ?? "unknown").toLowerCase();
  const saveStatus = String(firstValue(row, ["save_status", "persistence_state"]) ?? "").toLowerCase();
  const watermarkState = saveStatus === "save_error" ? saveStatus : String(firstValue(row, ["movement_state", "save_status", "load_status", "coverage_state"]) ?? "not_configured").toLowerCase();
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
  if (["advanced", "initialized", "saved"].includes(status)) return "success";
  if (["changed", "unchanged", "unknown"].includes(status)) return "warning";
  if (status === "save_error") return "failed";
  return "neutral";
}

function LinkedJobSection({
  row,
  onOpenJob,
}: {
  row: Record<string, unknown>;
  onOpenJob?: (row: Record<string, unknown>) => void;
}) {
  const [copied, setCopied] = useState(false);
  const jobId = String(row.job_id ?? "");
  if (!jobId) return null;
  const jobRow = jobIdentitySeed(row);
  return (
    <section className="monitoring-detail-section monitoring-linked-job-section dataflow-run-job">
      <span>Job</span>
      <div className="dataflow-run-job-identity">
        <button
          className="monitoring-linked-job-row"
          type="button"
          onClick={() => onOpenJob?.(jobRow)}
          onMouseEnter={onOpenJob ? preloadJobDetailSections : undefined}
          onFocus={onOpenJob ? preloadJobDetailSections : undefined}
          onPointerDown={onOpenJob ? preloadJobDetailSections : undefined}
          disabled={!onOpenJob}
        >
          <BriefcaseBusiness size={15} aria-hidden="true" />
          <span className="monitoring-linked-job-identity">
            <strong>{jobId}</strong>
          </span>
        </button>
        <Help as="button" type="button" className="icon-action small" aria-label="Copy job id" title={`Copy ${jobId}`} onClick={() => { void copyToClipboard(jobId, setCopied); }}>{copied ? <Check size={14} /> : <Copy size={14} />}</Help>
      </div>
      {onOpenJob ? <Help as="button" type="button" className="icon-action small dataflow-run-job-open" aria-label="Open job details" title="Open job details" onMouseEnter={preloadJobDetailSections} onFocus={preloadJobDetailSections} onPointerDown={preloadJobDetailSections} onClick={() => onOpenJob(jobRow)}><ChevronRight size={16} aria-hidden="true" /></Help> : null}
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
  if (kind === "job") return String(row.job_id ?? "Job");
  if (kind === "maintenance") return presentDestination(row).name;
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
  if (kind === "maintenance") return presentDestination(row).locator;
  if (kind === "job") return String(row.job_id ?? title ?? "");
  if (kind === "dataflow" || kind === "dataflow-registry") return String(kind === "dataflow-registry" ? row.dataflow_id ?? title ?? "" : row.dataflow_run_id ?? row.dataflow_id ?? title ?? "");
  return String(title ?? "");
}

function kindLabel(kind: MonitoringDetailKind) {
  return kind.replace(/_/g, " ");
}

function RawPayloadSection({ row, label = "Raw payload" }: { row: Record<string, unknown>; label?: string }) {
  const [copied, setCopied] = useState(false);
  const executionPayload = rawRecordJsonForCopy(row.raw_record_json ?? row.latest_raw_record_json);
  const payload = executionPayload ?? JSON.stringify(row, null, 2) ?? String(row);
  const displayPayload = executionPayload ? formatRawRecordJson(executionPayload) : payload;
  return (
    <details className="monitoring-raw-detail">
      <summary>{label}</summary>
      <div className="monitoring-json-box monitoring-raw-json-box">
        <Help as="button"
          className="icon-action small monitoring-json-copy"
          type="button"
          aria-label={executionPayload ? "Copy reconstructed execution JSON" : `Copy ${label.toLowerCase()} JSON`}
          title={executionPayload ? "Copy reconstructed execution JSON" : "Copy JSON"}
          onClick={(event) => {
            event.preventDefault();
            event.stopPropagation();
            void copyToClipboard(executionPayload ?? payload, setCopied);
          }}
        >
          {copied ? <Check size={13} /> : <Copy size={13} />}
        </Help>
        <pre>{highlightJson(displayPayload)}</pre>
      </div>
    </details>
  );
}
