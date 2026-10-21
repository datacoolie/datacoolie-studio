import { parseRawRecordJson } from "../../../shared/api/monitoringAdapters";
import { compareMetricValues, isMetricInteger, metricDifference, type MetricValue } from "../../../shared/lib/numberFormat";
import { storyRecord, storyScalar, type DetailAvailability } from "./dataflowStoryModel";

export const jobRunConfigKeys = ["max_workers", "stop_on_error", "retry_count", "retry_delay", "dry_run", "retention_hours", "run_attributes"];
export const jobRuntimeKeys = ["platform_name", "engine_name", "metadata_provider_name", "watermark_manager_name"];
export const jobSummaryKeys = ["status", "job_id", "start_time", "end_time", "duration_seconds", "stages", "operation_types", "message", "error_message"];
export const jobMetricKeys = ["total_dataflows", "total_succeeded", "total_failed", "total_skipped", "total_running", "total_pending", "total_rows_read", "total_rows_written", "total_rows_inserted", "total_rows_updated", "total_rows_deleted", "total_files_added", "total_files_removed", "total_bytes_added", "total_bytes_removed"];

/** Linked Dataflow rows supply identity only; their metrics/payload are never Job evidence. */
export function jobIdentitySeed(row: Record<string, unknown>) {
  const keys = ["job_id", "log_session_id", "_source_id", "source_id"];
  return Object.fromEntries(keys.filter(key => Object.prototype.hasOwnProperty.call(row, key)).map(key => [key, row[key]]));
}

export function buildJobStory(row: Record<string, unknown>, availability: DetailAvailability = "ready") {
  const payload = typeof row.raw_record_json === "string" ? row.raw_record_json : null;
  const parsed = payload === null ? null : storyRecord(parseRawRecordJson(payload));
  const invalid = payload !== null && (!parsed || parsed._type !== "job_run_log");
  const native = invalid ? null : parsed;
  const reported = availability === "ready" && !invalid ? native ?? row : jobIdentitySeed(row);
  const comparisonReported = storyRecord(row.reconciliation_reported_totals) ?? reported;
  return { reported, comparisonReported, evidence: row, native, payload, invalid, availability };
}
export type JobStory = ReturnType<typeof buildJobStory>;

/**
 * Reconciliation is calculated by the Job runs backend projection. Keep the
 * fields that describe that projection together when a detail payload is
 * merged with the selected table row, otherwise a detail query can silently
 * recalculate coverage with a different filter scope.
 */
export const jobReconciliationEvidenceKeys = [
  "reconciliation_status",
  "reconciliation_mismatch_count",
  "child_scope_coverage",
  "scope_coverage",
  "child_dataflow_count",
  "child_succeeded_count",
  "child_failed_count",
  "child_skipped_count",
  "child_running_count",
  "child_pending_count",
  "child_p95_duration_seconds",
  "latest_child_time",
  "child_total_rows_read",
  "child_total_rows_written",
  "child_total_bytes_added",
  "child_total_bytes_removed",
  "child_metric_eligible_count",
  "child_rows_read_measured_count",
  "child_rows_written_measured_count",
  "child_bytes_added_measured_count",
  "child_bytes_removed_measured_count",
  "observed_at",
  "log_records_dropped",
  "log_bytes_dropped",
] as const;

/** The same status consumed by the Job runs table and the Job drawer. */
export function jobReconciliationStatus(row: Record<string, unknown>) {
  const value = row.reconciliation_status;
  return typeof value === "string" && value.trim() ? value.trim() : "not_available";
}

/** Raw scope used by the backend reconciliation projection. */
export function jobReconciliationScopeValue(row: Record<string, unknown>) {
  const value = row.child_scope_coverage ?? row.scope_coverage;
  return typeof value === "string" && value.trim() ? value.trim() : "unknown";
}

export function jobReconciliationScopeLabel(row: Record<string, unknown>) {
  const scope = jobReconciliationScopeValue(row);
  if (scope === "subset" || scope === "insufficient_coverage") return "Filtered child dataflows";
  if (scope === "unknown_total") return "Expected child total unknown";
  if (scope === "unknown_loss") return "Child log coverage unknown";
  if (scope === "lost_detail") return "Child log coverage incomplete";
  if (scope === "nonterminal") return "Job is still in progress";
  if (scope === "range_incomplete") return "Time window excludes child activity";
  if (["full", "unknown_snapshot", "stale_snapshot"].includes(scope)) return "All child dataflows";
  return scope === "unknown" ? "unk" : scope;
}

export function jobReconciliationNote(row: Record<string, unknown>) {
  const scope = jobReconciliationScopeValue(row);
  if (scope === "full" && (row.total_dataflows === null || row.total_dataflows === undefined || row.total_dataflows === "")) {
    return "The expected job total was not reported; available child totals are shown.";
  }
  const notes: Record<string, string> = {
    full: "Compares the Job's reported dataflow count with linked child logs.",
    subset: "Current filters limit the child dataflows used for comparison.",
    insufficient_coverage: "There is not enough child log coverage to compare totals.",
    unknown_snapshot: "The job snapshot time is missing; its freshness cannot be checked.",
    stale_snapshot: "Child activity is newer than the job snapshot; totals may differ.",
    unknown_total: "The job did not report an expected child total.",
    unknown_loss: "Log loss counters are missing; completeness cannot be confirmed.",
    lost_detail: "Log records were dropped; child totals may be incomplete.",
    nonterminal: "Final totals are not assessed while the job is in progress.",
    range_incomplete: "The selected time window excludes some recorded child activity.",
  };
  return notes[scope] ?? "Compares reported job totals with available child dataflow logs.";
}

/** Preserve the table's reconciliation evidence when a detail payload arrives. */
export function mergeJobReconciliationEvidence(detail: Record<string, unknown>, selected: Record<string, unknown>) {
  const merged = { ...detail };
  const selectedComparison = typeof selected.reconciliation_status === "string";
  for (const key of jobReconciliationEvidenceKeys) {
    if (selectedComparison || Object.prototype.hasOwnProperty.call(selected, key) && selected[key] !== undefined) merged[key] = selected[key];
  }
  if (selectedComparison) {
    const selectedStory = buildJobStory(selected);
    merged.reconciliation_reported_totals = selectedStory.invalid ? {} : Object.fromEntries(
      jobComparisonMetrics.map(([, key]) => [key, selectedStory.comparisonReported[key]]),
    );
  }
  return merged;
}

export const jobComparisonMetrics = [
  ["Dataflows", "total_dataflows", "child_dataflow_count", "All child operations"],
  ["Succeeded", "total_succeeded", "child_succeeded_count", "All child operations"],
  ["Failed", "total_failed", "child_failed_count", "All child operations"],
  ["Skipped", "total_skipped", "child_skipped_count", "All child operations"],
  ["Running", "total_running", "child_running_count", "All child operations"],
  ["Pending", "total_pending", "child_pending_count", "All child operations"],
  ["Rows read", "total_rows_read", "child_total_rows_read", "Excludes maintenance", "child_rows_read_measured_count"],
  ["Rows written", "total_rows_written", "child_total_rows_written", "Excludes maintenance", "child_rows_written_measured_count"],
  ["Bytes added", "total_bytes_added", "child_total_bytes_added", "All child operations", "child_bytes_added_measured_count"],
  ["Bytes removed", "total_bytes_removed", "child_total_bytes_removed", "All child operations", "child_bytes_removed_measured_count"],
] as const;

export function jobComparisonRows(model: JobStory) {
  return jobComparisonMetrics.filter(([, reported, observed]) => Object.prototype.hasOwnProperty.call(model.comparisonReported, reported) || Object.prototype.hasOwnProperty.call(model.evidence, observed)).map(([label, reportedKey, observedKey, population, ...coverage]) => {
    const reported = storyScalar(model.comparisonReported[reportedKey]) as MetricValue;
    const observed = storyScalar(model.evidence[observedKey]) as MetricValue;
    const group = label === "Dataflows" ? "Dataflow count" : label.startsWith("Rows") ? "Rows" : label.startsWith("Bytes") ? "Bytes" : "Status counts";
    const measured = coverage[0] ? storyScalar(model.evidence[coverage[0]]) as MetricValue : undefined;
    const eligible = coverage[0] ? storyScalar(model.evidence[group === "Rows" ? "child_metric_eligible_count" : "child_dataflow_count"]) as MetricValue : undefined;
    const measurementComplete = !coverage.length || validCoverageCount(measured) && validCoverageCount(eligible) && compareMetricValues(measured, eligible) === 0;
    const scopeComplete = jobReconciliationScopeValue(model.evidence) === "full";
    const available = model.availability === "ready" && !model.invalid;
    const difference = available && scopeComplete && measurementComplete ? metricDifference(observed, reported) : null;
    const differenceNote = !available ? "Job evidence unavailable"
      : !scopeComplete ? "Comparison coverage incomplete"
      : !measurementComplete ? validCoverageCount(measured) && validCoverageCount(eligible) ? "Measurements incomplete" : "Measurement coverage unknown"
      : difference === null ? "Measurement unavailable" : null;
    return { metric: label, reported, observed, difference, differenceNote, population, measured, eligible, group };
  });
}

function validCoverageCount(value: MetricValue) {
  return isMetricInteger(value) && compareMetricValues(value, 0) >= 0;
}

export function jobComparisonConclusion(model: JobStory) {
  if (model.availability !== "ready" || model.invalid) return "unk";
  const status = jobReconciliationStatus(model.evidence);
  const scope = jobReconciliationScopeValue(model.evidence);
  const caveats: Record<string, string> = { stale_snapshot: "Snapshot is older than child activity", unknown_snapshot: "Snapshot time unknown", unknown_loss: "Child log coverage unknown", lost_detail: "Child log coverage incomplete", subset: "Filtered child comparison", insufficient_coverage: "Child coverage insufficient", unknown_total: "Expected total unknown", nonterminal: "Job is still in progress", range_incomplete: "Time window excludes child activity" };
  if (scope !== "full" && scope !== "unknown") return caveats[scope] ?? "Comparison coverage incomplete";
  const expected = storyScalar(model.comparisonReported.total_dataflows);
  if (expected === null || expected === undefined || expected === "") return "Expected total unknown";
  if (status === "mismatch") return "Child count mismatch";
  if (status === "matched") return "Child count matched";
  return "Comparison unavailable";
}
