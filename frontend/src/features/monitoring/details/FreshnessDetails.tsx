import { useMemo, type ReactNode } from "react";
import type { MonitoringRecord } from "../../../shared/api/domainTypes";
import { DataTable, StatusCell, type TableSort } from "../../../shared/components/DataTable";
import { Help } from "../../../shared/components/Help";
import { lifecycleStatusPresentation } from "../../../shared/statusPresentation";
import { parseRawRecordJson } from "../../../shared/api/monitoringAdapters";
import { formatTimestampForDisplay } from "../../../shared/time";
import {
  CompactNumberValue,
  DataflowPhaseCell,
  TablePager,
  WatermarkBadge,
  metricValue,
} from "../components/monitoringPresentation";
import {
  DataflowRouteEndpoint,
  compactDrawerIdentifier,
  copyToClipboard,
  dataflowEndpointSummary,
  firstValue,
  formatFreshnessAge,
  freshnessRunTimeLines,
  hasValue,
  IssueCell,
} from "./detailPrimitives";
import { StoryDisclosure, StoryDuration } from "./DataflowStoryPrimitives";
import { DataflowConfigurationBody } from "./DataflowStoryConfiguration";
import { DataflowReadWatermarkBody } from "./DataflowStoryWatermark";
import { DataflowTechnicalBody } from "./DataflowStoryDetails";
import { DataflowCopyValue } from "./DataflowCopyValue";
import { StoryExpandedValue, StoryFields, type StoryField } from "./DataflowStoryValues";
import { buildDataflowStory, storyStatus, type DataflowStory } from "./dataflowStoryModel";

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
  loading = false,
  error = null,
  onRetry,
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
  loading?: boolean;
  error?: string | null;
  onRetry?: () => void;
}) {
  const model = useMemo(() => buildDataflowStory(row, "ready"), [row]);
  return (
    <>
      <FreshnessIdentitySection row={row} />
      <FreshnessSummary row={row} timezoneName={timezoneName} />
      <FreshnessMessage row={row} />
      <FreshnessExecution
        rows={relatedDataflows}
        total={total}
        offset={offset}
        limit={limit}
        sort={sort}
        onSort={onSort}
        onPageChange={onPageChange}
        onPageSizeChange={onPageSizeChange}
        onOpenDataflow={onOpenDataflow}
        timezoneName={timezoneName}
        loading={loading}
        error={error}
        onRetry={onRetry}
      />
      <FreshnessReadWatermark row={row} model={model} />
      <FreshnessConfiguration row={row} model={model} />
      <FreshnessTechnical row={row} model={model} />
    </>
  );
}

function FreshnessIdentitySection({ row }: { row: Record<string, unknown> }) {
  const source = dataflowEndpointSummary(row, "source");
  const destination = dataflowEndpointSummary(row, "destination");
  const id = typeof row.dataflow_id === "string" || typeof row.dataflow_id === "number" ? row.dataflow_id : null;
  return (
    <section className="monitoring-detail-section monitoring-freshness-identity">
      <div className="monitoring-freshness-dataflow-context">
        <div className="monitoring-freshness-dataflow-name">
          <span>Dataflow</span>
          <strong>{String(row.dataflow_name ?? row.dataflow_id ?? "unk")}</strong>
        </div>
        <div className="monitoring-dataflow-route-card">
          <DataflowRouteEndpoint direction="source" endpoint={source} />
          <span className="monitoring-dataflow-route-arrow" aria-hidden="true">→</span>
          <DataflowRouteEndpoint direction="destination" endpoint={destination} />
        </div>
        <div className="monitoring-freshness-dataflow-id">
          <span>Dataflow ID</span>
          <CopyValue label="Dataflow ID" value={id}>{id === null ? "unk" : String(id)}</CopyValue>
        </div>
      </div>
    </section>
  );
}

function FreshnessSummary({ row, timezoneName }: { row: Record<string, unknown>; timezoneName?: string | null }) {
  return (
    <section className="monitoring-detail-section monitoring-freshness-summary-section">
      <h3>Summary &amp; insights</h3>
      <div className="monitoring-freshness-summary-grid">
        <SummaryCard title="Freshness summary" className="is-summary" fields={[
          { label: "Health", value: freshnessHealthLabel(row) },
          { label: "Latest check", value: row.latest_freshness_at, field: "latest_freshness_at", timezoneName },
          { label: "Freshness status", value: row.latest_freshness_status ?? "unk", field: "latest_freshness_status" },
          { label: "Check age", value: formatFreshnessAge(row.age_seconds, row.age_days) },
          { label: "Stale threshold", value: "7d" },
        ]} />
        <SummaryCard title="Run activity" className="is-run-activity" fields={[
          { label: "Latest run status", value: row.latest_run_status ?? "unk", field: "latest_run_status", content: <StatusCell row={{ status: storyStatus(row.latest_run_status) }} /> },
          { label: "Latest run ID", value: row.latest_run_id, content: <CopyValue label="Run ID" value={row.latest_run_id}>{compactDrawerIdentifier(row.latest_run_id) ?? "unk"}</CopyValue> },
          { label: "Latest run", value: row.latest_run_at, field: "latest_run_at", timezoneName },
          { label: "Runs", value: metricValue(row, "run_count"), content: <CompactNumberValue value={metricValue(row, "run_count")} /> },
          { label: "Skipped streak", value: metricValue(row, "skipped_streak"), content: <CompactNumberValue value={metricValue(row, "skipped_streak")} /> },
          { label: "Status counts", value: null, content: <FreshnessRunMix row={row} /> },
        ]} />
        <SummaryCard title="Watermark insight" className="is-watermark-insight" fields={[
          { label: "Movement", value: row.movement_state ?? "unknown", content: <WatermarkBadge value={row.movement_state ?? "unknown"} /> },
          { label: "Configuration", value: row.configuration_state ?? "unk" },
          { label: "Load | Save", value: null, content: <span>{String(row.load_status ?? "unk")} <span className="monitoring-pair-divider">|</span> {String(row.save_status ?? "unk")}</span> },
          { label: "Last saved in filter", value: row.latest_success_watermark, content: <WatermarkPreview value={row.latest_success_watermark} /> },
          { label: "Read adjustment", value: row.adjustment_state ?? "unk" },
        ]} />
      </div>
    </section>
  );
}

function SummaryCard({ title, fields, className = "" }: { title: string; fields: Array<{ label: string; value: unknown; field?: string; content?: ReactNode; timezoneName?: string | null }>; className?: string }) {
  return (
    <section className={`dataflow-section-block monitoring-freshness-summary-card ${className}`}>
      <h4>{title}</h4>
      <dl className="monitoring-freshness-summary-fields">
        {fields.map((field) => (
          <div key={field.label}>
            <dt>{field.label}</dt>
            <dd>{field.content ?? <SummaryValue value={field.value} field={field.field} timezoneName={field.timezoneName} />}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

function SummaryValue({ value, field, timezoneName }: { value: unknown; field?: string; timezoneName?: string | null }) {
  if (value === null || value === undefined || value === "") return <span>unk</span>;
  if (field && (field.endsWith("_at") || field.endsWith("_time")) && typeof value === "string") {
    return <Help title={value}>{formatTimestampForDisplay(value, timezoneName, "unk")}</Help>;
  }
  return <span>{String(value)}</span>;
}

function FreshnessMessage({ row }: { row: Record<string, unknown> }) {
  const status = lifecycleStatusPresentation(row.latest_run_status);
  return (
    <section className="monitoring-detail-section monitoring-freshness-message-section" style={status ? { background: status.drawerSurface } : undefined}>
      <h3>Message</h3>
      <div className="monitoring-freshness-message-value monitoring-message-scroll">{hasValue(row.latest_message) ? String(row.latest_message) : "unk"}</div>
    </section>
  );
}

function FreshnessExecution({
  rows,
  total,
  offset,
  limit,
  sort,
  onSort,
  onPageChange,
  onPageSizeChange,
  onOpenDataflow,
  timezoneName,
  loading,
  error,
  onRetry,
}: {
  rows: MonitoringRecord[];
  total: number;
  offset: number;
  limit: number;
  sort: TableSort;
  onSort: (sort: TableSort) => void;
  onPageChange: (offset: number) => void;
  onPageSizeChange: (limit: number) => void;
  onOpenDataflow?: (row: MonitoringRecord) => void;
  timezoneName?: string | null;
  loading: boolean;
  error: string | null;
  onRetry?: () => void;
}) {
  return (
    <section className="monitoring-detail-section monitoring-freshness-runs-section">
      <div className="monitoring-freshness-execution-heading"><h3>Execution</h3></div>
      {error ? <div className="monitoring-freshness-availability" role="alert">Execution could not be loaded. {onRetry ? <button type="button" className="small-button" onClick={onRetry}>Retry</button> : null}</div> : null}
      <div className="monitoring-freshness-execution-table">
        <TablePager limit={limit} offset={offset} loadedRows={rows.length} totalRows={total} loading={loading} onPageChange={onPageChange} onPageSizeChange={onPageSizeChange} />
        {rows.length ? <DataTable
          rows={rows}
          compactNumbers
          compactNumberStyle="infix"
          columns={[
            { key: "start_time", label: "Time", sortable: true, width: 170, render: (run) => <FreshnessRunTimeCell row={run} timezoneName={timezoneName} /> },
            { key: "phase_health", label: "Phase", sortable: true, width: 100, render: (run) => <DataflowPhaseCell row={run} unknownLabel="unk" /> },
            { key: "duration_seconds", label: "Duration", sortable: true, autoFit: true, minWidth: 68, maxWidth: 104, render: (run) => <StoryDuration value={run.duration_seconds} /> },
            { key: "status", label: "Status", sortable: true, autoFit: true, minWidth: 68, maxWidth: 120, render: (run) => <StatusCell row={run} /> },
            { key: "source_rows_read", label: "Rows", sortable: true, sortKey: "source_rows_read", autoFit: true, minWidth: 82, maxWidth: 130, render: (run) => <FreshnessRunRowsCell row={run} /> },
            { key: "movement_state", label: "Watermark", sortable: true, autoFit: true, minWidth: 86, maxWidth: 126, render: (run) => <WatermarkBadge value={run.movement_state ?? "unknown"} /> },
            { key: "message", label: "Message", sortable: true, width: 200, fillPriority: "last", className: "monitoring-child-issue-column", render: (run) => <IssueCell row={run} /> },
          ]}
          maxRows={limit}
          offset={0}
          onRowClick={onOpenDataflow}
          sort={sort}
          onSort={onSort}
          fixedLayout
          className="monitoring-child-dataflows-table monitoring-freshness-run-table"
          timezoneName={timezoneName}
        /> : !loading && !error ? <p className="monitor-muted">No runs recorded.</p> : null}
      </div>
    </section>
  );
}

function FreshnessReadWatermark({ row, model }: { row: Record<string, unknown>; model: DataflowStory }) {
  return <StoryDisclosure className="monitoring-freshness-collapsed-section" title="Read &amp; watermark">
    {() => <div className="monitoring-freshness-read-watermark-body">
      {model.final ? <DataflowReadWatermarkBody model={model} /> : <FreshnessAggregateReadWatermark row={row} />}
      <section className="dataflow-section-block monitoring-freshness-last-saved">
        <h4>Last saved in filter</h4>
        <StoryExpandedValue value={parsedValue(row.latest_success_watermark)} showType={false} />
      </section>
    </div>}
  </StoryDisclosure>;
}

function FreshnessAggregateReadWatermark({ row }: { row: Record<string, unknown> }) {
  return <div className="monitoring-freshness-read-watermark-grid">
    <section className="dataflow-section-block dataflow-read-region"><h4>Read</h4><StoryFields fields={[
      { label: "Mode", value: row.selection, path: "/selection" },
      { label: "Request", value: row.read_request, path: "/read_request" },
      { label: "Adjustment", value: row.adjustment_state, path: "/adjustment_state" },
    ]} /></section>
    <section className="dataflow-section-block dataflow-watermark-region"><h4>Watermark</h4><StoryFields fields={[
      { label: "Movement", value: row.movement_state ?? "unknown", path: "/movement_state", content: <WatermarkBadge value={row.movement_state ?? "unknown"} /> },
      { label: "Load | Save", value: null, path: "/watermark", content: <span>{String(row.load_status ?? "unk")} | {String(row.save_status ?? "unk")}</span> },
      { label: "Loaded", value: row.loaded_watermark, path: "/loaded_watermark" },
      { label: "Merge base", value: row.merge_base, path: "/merge_base" },
      { label: "Candidate", value: row.candidate, path: "/candidate" },
      { label: "Saved", value: row.saved_watermark, path: "/saved_watermark" },
    ]} /></section>
  </div>;
}

function FreshnessConfiguration({ row, model }: { row: Record<string, unknown>; model: DataflowStory }) {
  return <StoryDisclosure className="monitoring-freshness-collapsed-section" title="Configuration">{() => <>
    <p className="monitor-muted dataflow-config-note">Metadata recorded in filtered logs.</p>
    <DataflowConfigurationBody row={row} diagnostics={model.diagnostics} />
  </>}</StoryDisclosure>;
}

function FreshnessTechnical({ row, model }: { row: Record<string, unknown>; model: DataflowStory }) {
  return <StoryDisclosure className="monitoring-freshness-collapsed-section" title="Technical details">
    {() => model.payload ? <DataflowTechnicalBody model={model} row={row} /> : <FreshnessTechnicalFallback row={row} />}
  </StoryDisclosure>;
}

function FreshnessTechnicalFallback({ row }: { row: Record<string, unknown> }) {
  const rawFields: Array<[string, unknown, string]> = [
    ["Run ID", row.latest_run_id, "latest_run_id"],
    ["Dataflow ID", row.dataflow_id, "dataflow_id"],
    ["Job ID", row.job_id, "job_id"],
    ["Session ID", row.log_session_id, "log_session_id"],
    ["Log source ID", row.latest_source_id ?? row._source_id, "_source_id"],
    ["Workspace ID", row.workspace_id, "workspace_id"],
    ["Job number", row.job_num, "job_num"],
    ["Job index", row.job_index, "job_index"],
    ["Record type", row._type, "_type"],
    ["File", row.latest_file_uri ?? row._file_uri, "_file_uri"],
    ["Record line", row.latest_record_line ?? row._record_line, "_record_line"],
    ["DataCoolie version", row.datacoolie_version, "datacoolie_version"],
    ["Log schema version", row.log_schema_version, "log_schema_version"],
    ["Latest error", row.latest_error_message, "latest_error_message"],
  ];
  const fields: StoryField[] = rawFields.map(([label, value, field]) => ({
    label,
    value,
    path: `/${field}`,
    text: true,
    content: <DataflowCopyValue label={label} value={value} />,
  }));
  return <section className="dataflow-section-block dataflow-technical-identity"><h4>Identity and log context</h4><StoryFields fields={fields} /></section>;
}

function FreshnessRunRowsCell({ row }: { row: MonitoringRecord }) {
  const read = metricValue(row, "source_rows_read");
  const written = metricValue(row, "destination_rows_written");
  return <span className="freshness-run-stack-cell"><span>{read == null ? "unk" : <CompactNumberValue value={read} />} / {written == null ? "unk" : <CompactNumberValue value={written} />}</span></span>;
}

function FreshnessRunTimeCell({ row, timezoneName }: { row: Record<string, unknown>; timezoneName?: string | null }) {
  const [start, end] = freshnessRunTimeLines(row, timezoneName);
  return <Help className="freshness-run-stack-cell" title={`Start: ${start}\nEnd: ${end.slice(2)}`}><span>{start}</span><small>{end}</small></Help>;
}

function FreshnessRunMix({ row }: { row: Record<string, unknown> }) {
  const values: Array<[string, string, unknown]> = [
    ["S", "is-success", row.succeeded_count ?? row.succeeded_runs],
    ["F", "is-failed", row.failed_count ?? row.failed_runs],
    ["Skip", "is-warning", row.skipped_count ?? row.skipped_runs],
    ["Run", "", row.running_count ?? row.running_runs],
    ["Pend", "", row.pending_count ?? row.pending_runs],
  ];
  return <Help className="monitoring-freshness-run-mix" title="Succeeded / failed / skipped / running / pending">{values.map(([label, className, value]) => <span key={label}><span>{label}</span><strong className={className}>{formatMetric(value)}</strong></span>)}</Help>;
}

function formatMetric(value: unknown) {
  return value === null || value === undefined || value === "" ? "unk" : <CompactNumberValue value={value as string | number} />;
}

function WatermarkPreview({ value }: { value: unknown }) {
  if (!hasValue(value)) return <span>unk</span>;
  const parsed = parsedValue(value);
  return <Help as="div" className="monitoring-freshness-watermark-preview" title={typeof value === "string" ? value : undefined}><StoryExpandedValue value={parsed} showType={false} /></Help>;
}

function parsedValue(value: unknown) {
  if (typeof value !== "string") return value;
  const parsed = parseRawRecordJson(value);
  return parsed === null && value.trim() !== "null" ? value : parsed;
}

function CopyValue({ label, value, children }: { label: string; value: unknown; children: ReactNode }) {
  const exact = value === null || value === undefined ? "unk" : String(value);
  return <Help as="button" type="button" className="dataflow-value-copy-button" aria-label={`Copy ${label}`} title={`Click to copy ${label}`} onClick={() => { void copyToClipboard(exact, () => undefined); }}>{children}</Help>;
}

function freshnessHealthLabel(row: Record<string, unknown>) {
  const status = String(row.latest_run_status ?? "").toLowerCase();
  const age = Number(row.age_seconds ?? (Number(row.age_days) * 86_400));
  if (status === "failed" || status === "error") return "Needs review";
  if (Number.isFinite(age) && age > 7 * 86_400) return "Stale";
  if (!hasValue(row.latest_freshness_at) && !hasValue(row.latest_run_at)) return "No evidence";
  return "Current";
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
  const latestConfiguration = String(row.configuration_state ?? "").trim().toLowerCase();
  if (["configured", "not_configured"].includes(latestConfiguration)) return latestConfiguration === "configured";
  const coverage = String(row.coverage_state ?? "").trim().toLowerCase();
  if (["not_configured", "not configured", "not_requested", "not_attempted", "no_candidate"].includes(coverage)) return false;
  return [row.load_status, row.watermark_load_status, row.save_status, row.merge_base, row.loaded_watermark, row.candidate, row.watermark_to_save, row.saved_watermark].some(hasValue) || coverage === "configured";
}

export function watermarkStatusLabel(row: Record<string, unknown>) {
  const classification = firstValue(row, ["movement_state", "save_status", "load_status", "coverage_state"]);
  return hasValue(classification) ? String(classification) : "unknown";
}

export function DrawerWatermarkBadge({ row }: { row: Record<string, unknown> }) {
  return <WatermarkBadge value={watermarkStatusLabel(row)} />;
}

export function watermarkIntent(value: string) {
  const normalized = value.toLowerCase();
  if (["advanced", "initialized", "saved"].includes(normalized)) return "good";
  if (["changed", "unchanged", "unknown"].includes(normalized)) return "warning";
  if (["save_error", "load_error"].includes(normalized)) return "failed";
  return "neutral";
}
