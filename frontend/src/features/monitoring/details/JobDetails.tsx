import { useMemo } from "react";
import { lifecycleStatusPresentation } from "../../../shared/statusPresentation";
import { metricDifference, type MetricValue } from "../../../shared/lib/numberFormat";
import { isLosslessJsonNumber } from "../../../shared/api/monitoringAdapters";
import { monitoringDrawerRawDimensionLabel } from "../monitoringDimensions";
import { StatusCell } from "../../../shared/components/DataTable";
import { Help } from "../../../shared/components/Help";
import { compactDrawerIdentifier, ErrorMessageBlock, detailValue, hasValue, looksJson } from "./detailPrimitives";
import { formatRuntimeTimestampForDisplay, humanize } from "./detailPrimitives";
import { StoryBytes, StoryCount, StoryDisclosure, StoryDuration, StoryJson } from "./DataflowStoryPrimitives";
import { StoryExpandedValue, StoryFields, type StoryField } from "./DataflowStoryValues";
import { DataflowCopyValue } from "./DataflowCopyValue";
import { storyScalar, type DetailAvailability } from "./dataflowStoryModel";
import { buildJobStory, jobMetricKeys, jobReconciliationNote as canonicalJobReconciliationNote, jobReconciliationScopeLabel as canonicalJobReconciliationScope, jobReconciliationStatus, jobRunConfigKeys, jobRuntimeKeys, jobSummaryKeys, type JobStory } from "./jobStoryModel";
import { JobExecution, type JobExecutionProps } from "./JobExecution";
import { JobReconciliation } from "./JobReconciliation";
import { ReconciliationBadge } from "../pages/JobsPageSupport";

export function JobDetailSections({ row, timezoneName, availability = "ready", onRetry, execution }: {
  row: Record<string, unknown>; timezoneName?: string | null; availability?: DetailAvailability;
  onRetry?: () => void; execution?: JobExecutionProps;
}) {
  const model = useMemo(() => buildJobStory(row, availability), [row, availability]);
  const job = model.reported;
  const reconciliationStatus = jobReconciliationStatus(model.evidence);
  return <div className="job-story">
    {availability !== "ready" || model.invalid ? <div className="dataflow-story-availability" role={availability === "error" || model.invalid ? "alert" : "status"}>
      {availability === "loading" ? "Loading Job run details…" : availability === "error" ? "Job run details could not be loaded." : availability === "not-found" ? "Job run details not found." : "Native Job payload is invalid."}
      {availability === "error" && onRetry ? <button type="button" className="small-button" onClick={onRetry}>Retry Job details</button> : null}
    </div> : null}
    {availability === "ready" && !model.invalid && !model.native ? <p className="job-story-note">Native payload unavailable; showing recorded Job fields.</p> : null}
    <section className="job-runtime-context" aria-label="Runtime context">{[["Platform", "platform_name"], ["Engine", "engine_name"], ["Metadata provider", "metadata_provider_name"]].map(([label, key]) => <span key={key}><span>{label}</span><Help title={typeof job[key] === "string" ? String(job[key]) : undefined}>{monitoringDrawerRawDimensionLabel(job[key])}</Help></span>)}</section>
    <section className="monitoring-detail-section job-story-summary"><h3>Summary &amp; insights</h3>
      <div className="job-summary-columns">
        <section className="job-section-block"><h4>Run summary</h4><StoryFields fields={[
          { label: "Status", path: "/status", value: job.status, content: <StatusCell row={{ status: job.status }} /> },
          { label: "Job ID", path: "/job_id", value: job.job_id, content: <JobSummaryId value={job.job_id} /> },
          { label: "Job no. | index", path: "/job_num", value: job.job_num, content: <span className="job-summary-ordinal-pair"><span>{summaryOrdinal(job.job_num)}</span><span>{summaryOrdinal(job.job_index)}</span></span> },
          ...["start_time", "end_time"].map(key => ({ label: key === "start_time" ? "Start" : "End", path: `/${key}`, value: job[key], content: typeof job[key] === "string" ? formatRuntimeTimestampForDisplay(String(job[key]), timezoneName) : "unk" })),
          { label: "Duration", path: "/duration_seconds", value: job.duration_seconds, content: <StoryDuration value={job.duration_seconds} unknownLabel="unk" /> },
          { label: "Stages", path: "/stages", ...listField(job.stages) },
          { label: "Operations", path: "/operation_types", ...listField(job.operation_types) },
        ]} /></section>
        <JobWorkload model={model} />
        <section className="job-section-block"><h4>Insight</h4><StoryFields fields={[
          { label: "Dataflows", path: "/total_dataflows", value: job.total_dataflows, content: <StoryCount value={job.total_dataflows} unknownLabel="unk" /> },
          ...["succeeded", "failed", "skipped", "running", "pending"].map(status => ({ label: humanize(status), path: `/total_${status}`, value: job[`total_${status}`], content: <span className={`job-count-${status}`}><StoryCount value={job[`total_${status}`]} unknownLabel="unk" /></span> })),
          { label: "Reconcile", path: "/reconciliation_status", value: reconciliationStatus, content: <ReconciliationBadge value={reconciliationStatus} /> },
          ...(model.evidence.child_p95_duration_seconds != null ? [{ label: "Child P95", path: "/child_p95_duration_seconds", value: null, content: <Help title="Observed duration percentile across all child operations"><StoryDuration value={model.evidence.child_p95_duration_seconds} unknownLabel="unk" /> (observed)</Help> }] : []),
          ...(model.evidence.log_records_dropped != null && Number(storyScalar(model.evidence.log_records_dropped)) > 0 ? [{ label: "Log loss", path: "/log_records_dropped", value: null, content: <span className="job-count-failed"><StoryCount value={model.evidence.log_records_dropped} unknownLabel="unk" /> records dropped</span> }] : []),
        ]} /></section>
      </div>
      <JobMessage title="Message" value={job.message} status={job.status} truncated={job.message_truncated} />
      {job.error_message != null && job.error_message !== job.message ? <JobMessage title="Error message" value={job.error_message} status="failed" truncated={job.error_message_truncated} /> : null}
    </section>
    <JobExecution {...execution} timezoneName={timezoneName} />
    <JobReconciliation model={model} timezoneName={timezoneName} />
    <JobConfiguration model={model} />
    <JobTechnical model={model} />
  </div>;
}

function listSummary(value: unknown) { return value == null ? "unk" : formatListLikeValue(value) || (value === "" ? '""' : "[]"); }
function summaryOrdinal(value: unknown) {
  const scalar = storyScalar(value);
  return scalar === null || scalar === undefined || scalar === "" || typeof scalar === "object" ? "unk" : String(scalar);
}
function listField(value: unknown): Pick<StoryField, "value" | "content" | "kind"> {
  const structured = Array.isArray(value) || value !== null && typeof value === "object" && !isLosslessJsonNumber(value) || typeof value === "string" && looksJson(value);
  return structured
    ? { value, kind: "json", content: <StoryExpandedValue value={value} kind="json" showType={false} compact /> }
    : { value, content: listSummary(value) };
}
function JobSummaryId({ value }: { value: unknown }) {
  if (typeof value !== "string") return <span>unk</span>;
  const compact = compactDrawerIdentifier(value) ?? '""';
  return <span className="job-summary-id"><DataflowCopyValue label="Job ID" value={value} title={value}>{compact || '""'}</DataflowCopyValue></span>;
}
function JobMessage({ title, value, status, truncated }: { title: string; value: unknown; status: unknown; truncated: unknown }) {
  if (value == null) return null;
  return <section className="job-section-block job-message" style={{ background: lifecycleStatusPresentation(status)?.drawerSurface }}><h4>{title}</h4><div className="monitoring-message-scroll">{typeof value === "string" ? value || '""' : String(storyScalar(value))}</div>{truncated === true ? <small>Message truncated in log.</small> : null}</section>;
}
function JobWorkload({ model }: { model: JobStory }) {
  const groups = [{ label: "Rows", rows: [
    { pairs: [["Read", "total_rows_read"], ["Written", "total_rows_written"]], actions: ["read", "written"] },
    { pairs: [["Insert", "total_rows_inserted"], ["Update", "total_rows_updated"], ["Delete", "total_rows_deleted"]], actions: ["inserted", "updated", "deleted"] },
  ] },
  { label: "Files", rows: [
    { pairs: [["Add", "total_files_added"], ["Remove", "total_files_removed"]], actions: ["added", "removed"] },
  ] },
  { label: "Bytes", rows: [
    { pairs: [["Add", "total_bytes_added"], ["Remove", "total_bytes_removed"]], actions: ["added", "removed"] },
    { pairs: [["Net", "__net_bytes"]], actions: ["net"] },
  ] }] as const;
  const net = metricDifference(storyScalar(model.reported.total_bytes_added) as MetricValue, storyScalar(model.reported.total_bytes_removed) as MetricValue);
  return <section className="job-section-block job-workload"><h4>Run workload</h4>{groups.map((group) => <div className="job-workload-group" key={group.label}><h5>{group.label}</h5>{group.rows.map((row) => <dl className={`dataflow-story-fields job-pair job-pair-${row.pairs.length}`} key={row.pairs.map(([label]) => label).join("-")}><div><dt>{row.pairs.map(([label]) => <span key={label}>{label}</span>)}</dt><dd>{row.pairs.map(([label, key], i) => <span key={key}><span aria-label={label} className={`job-action-${row.actions[i]}`}>{key === "__net_bytes" ? <StoryBytes value={net} unknownLabel="unk" /> : key.includes("bytes") ? <StoryBytes value={model.reported[key]} unknownLabel="unk" /> : <StoryCount value={model.reported[key]} unknownLabel="unk" />}</span></span>)}</dd></div></dl>)}</div>)}</section>;
}

export function jobCapturedFields(record: Record<string, unknown>, keys: string[], copy = false): StoryField[] {
  const labels: Record<string, string> = { job_id: "Job ID", job_num: "Job number", job_index: "Job index", workspace_id: "Workspace ID", log_session_id: "Session ID", _source_id: "Source ID", source_id: "Source ID", _file_uri: "File", _record_line: "Record line", platform_name: "Platform", engine_name: "Engine", metadata_provider_name: "Metadata provider", watermark_manager_name: "Watermark manager" };
  return keys.map(key => {
    const value = record[key];
    const label = labels[key] ?? humanize(key);
    const structured = value != null && typeof value === "object" && !isLosslessJsonNumber(value) || typeof value === "string" && /^[\[{]/u.test(value.trim());
    return { label, path: `/${key}`, value, text: true, kind: structured ? "json" as const : undefined,
      content: value == null ? "unk" : copy && !structured ? <DataflowCopyValue label={label} value={value} /> : undefined };
  });
}
export function JobConfiguration({ model }: { model: JobStory }) {
  return <StoryDisclosure title="Configuration" className="job-configuration">{() => <div className="job-two-columns">{[["Run configuration", jobRunConfigKeys], ["Runtime configuration", jobRuntimeKeys]].map(([title, keys]) => <section className="job-section-block" key={String(title)}><h4>{String(title)}</h4><StoryFields expandStructured fields={jobCapturedFields(model.reported, keys as string[])} /></section>)}</div>}</StoryDisclosure>;
}
export function JobTechnical({ model }: { model: JobStory }) {
  const identityKeys = ["job_id", "job_num", "job_index", "workspace_id", "log_session_id", "_source_id", "source_id", "_file_uri", "_record_line"];
  const excluded = new Set([...jobRunConfigKeys, ...jobRuntimeKeys, ...jobSummaryKeys, ...jobMetricKeys, ...identityKeys, "observed_at", "raw_record_json", "latest_raw_record_json", "reconciliation_reported_totals"]);
  const nativeExtra = Object.keys(model.reported).filter(key => !excluded.has(key));
  const evidenceKeys = Object.keys(model.evidence).filter(key => !excluded.has(key) && !nativeExtra.includes(key));
  const identity = { ...model.evidence, ...model.reported };
  return <StoryDisclosure title="Technical details" className="job-technical">{() => <>
    <section className="job-section-block"><h4>Identity and log context</h4><StoryFields expandStructured fields={jobCapturedFields(identity, identityKeys, true)} /></section>
    <section className="job-section-block"><h4>Capture and additional details</h4><StoryFields className="job-technical-capture-grid" expandStructured fields={[...jobCapturedFields(model.reported, nativeExtra), ...jobCapturedFields(model.evidence, evidenceKeys)]} /></section>
    <StoryDisclosure title={<Help title="Execution log record stored by Studio.">Execution JSON</Help>}>{() => model.native || model.payload !== null
      ? <StoryJson value={model.native ?? model.payload} copyPayload={model.payload} />
      : <p className="job-story-note">Native Job payload unavailable.</p>}</StoryDisclosure>
  </>}</StoryDisclosure>;
}

export function reconciliationSummary(row: Record<string, unknown>) {
  const status = jobReconciliationStatus(row);
  const mismatch = row.reconciliation_mismatch_count;
  if (status === "not_available" && !hasValue(mismatch)) return null;
  return {
    kind: "reconciliation",
    status,
    mismatch: mismatch === undefined ? null : mismatch as MetricValue,
  };
}

export function jobReconciliationScope(row: Record<string, unknown>) {
  return canonicalJobReconciliationScope(row);
}

export function jobReconciliationNote(row: Record<string, unknown>) {
  return canonicalJobReconciliationNote(row);
}

export function formatListLikeValue(value: unknown) {
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

export function ErrorMessageSection({ row, timezoneName }: { row: Record<string, unknown>; timezoneName?: string | null }) {
  if (!hasValue(row.error_message)) return null;
  return (
    <section className="monitoring-detail-section monitoring-error-message-section">
      <h3>Error Message</h3>
      <ErrorMessageBlock value={detailValue(row, "error_message", timezoneName)} />
    </section>
  );
}
