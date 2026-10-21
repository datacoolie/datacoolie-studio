export type DiagnosticsTone = "good" | "warning" | "bad" | "info";

export type DiagnosticsEvidenceIntent = "neutral" | "warning" | "bad" | "info";

export interface DiagnosticsEvidenceItem {
  label: string;
  value: unknown;
  field?: string;
  intent?: DiagnosticsEvidenceIntent;
  wide?: boolean;
  primary?: boolean;
}

type DiagnosticsRow = Record<string, unknown>;

/**
 * Diagnostics has a deliberately conservative state vocabulary. The API may
 * add detail to an assessment object, while older reports only expose a
 * health_status string. The UI maps both to the same four visual states.
 */
export function diagnosticsAssessment(value: unknown, fallback: unknown = "not_assessed") {
  const raw = value && typeof value === "object" && !Array.isArray(value)
    ? ((value as DiagnosticsRow).status ?? (value as DiagnosticsRow).state ?? (value as DiagnosticsRow).assessment)
    : value;
  const normalized = String(raw ?? fallback).trim().toLowerCase().replace(/[-\s]+/g, "_");
  if (["healthy", "clear", "good", "ok", "passed", "assessed_clear"].includes(normalized)) return { label: "Clear", tone: "good" as const };
  if (["has_issues", "issue", "issues", "bad", "error", "failed", "confirmed_issue"].includes(normalized)) return { label: "Issue", tone: "bad" as const };
  if (normalized === "partial") return { label: "Partial", tone: "info" as const };
  if (["warning", "degraded", "incomplete"].includes(normalized)) return { label: "Warning", tone: "warning" as const };
  if (["no_evidence", "no_records", "empty", "unknown", "not_assessed", "unavailable", "not_comparable"].includes(normalized)) {
    return { label: normalized === "no_evidence" || normalized === "no_records" ? "No evidence" : "Not assessed", tone: "info" as const };
  }
  return { label: humanLabel(raw ?? fallback), tone: "info" as const };
}

export function diagnosticsSeverityPresentation(value: unknown): { label: string; tone: DiagnosticsTone } {
  const assessment = diagnosticsAssessment(value, "info");
  return { label: assessment.label, tone: assessment.tone };
}

export function diagnosticsLinkagePresentation(row: DiagnosticsRow): { label: string; tone: DiagnosticsTone } {
  const category = normalizeCategory(row.category);
  const count = numberValue(firstMetric(row, "count", "total", "records"));
  if (category === "matched") return { label: "Matched", tone: "good" };
  if (["no child expected", "expected zero", "job zero children expected"].includes(category)) return { label: "Expected zero", tone: "info" };
  if (["unassessed", "not assessed", "unknown"].includes(category) || normalizeCategory(row.status) === "not assessed") return { label: "Not assessed", tone: "info" };
  if (!Number.isFinite(count) || count <= 0) return { label: "Clear", tone: "good" };
  if (["orphan dataflow job id", "missing job log", "orphan"].includes(category)) return { label: "Issue", tone: "bad" };
  if (["job without dataflows", "job without dataflow records", "missing expected child evidence", "missing expected dataflow records"].includes(category)) return { label: "Issue", tone: "bad" };
  if (["job children unassessed", "job only"].includes(category)) return { label: "Not assessed", tone: "info" };
  return diagnosticsSeverityPresentation(row.severity ?? row.status);
}

export function diagnosticsSourceLabel(value: unknown) {
  const source = String(value ?? "").trim();
  const sourceId = source.match(/^source:(.+)$/i)?.[1];
  if (sourceId) return `Log source ${sourceId}`;
  if (source.toLowerCase() === "direct-reader") return "Direct reader";
  return source || "Unknown source";
}

export function diagnosticsCategoryLabel(value: unknown) {
  const normalized = normalizeCategory(value);
  const labels: Record<string, string> = {
    "field completeness": "Field coverage",
    "field coverage": "Field coverage",
    "source coverage": "Source readiness",
    "diagnostic capture": "Captured detail",
    capture: "Captured detail",
    "reconciliation coverage": "Reconciliation coverage",
    "insufficient reconciliation coverage": "Reconciliation coverage",
    "reconciliation mismatch": "Reconciliation mismatch",
    "orphan dataflow job id": "Missing job log",
    "job without dataflows": "Missing expected child evidence",
    "job without dataflow records": "Missing expected child evidence",
    "missing expected child evidence": "Missing expected child evidence",
    "missing expected dataflow records": "Missing expected child evidence",
    "job zero children expected": "Expected zero children",
    "job children unassessed": "Child evidence unassessed",
    "read/cache warning": "Read/cache warning",
    "log loss": "Log loss",
    "log loss unassessed": "Log loss coverage",
    "job log loss": "Log loss",
    "job log loss unassessed": "Loss counters unknown",
    "scope notice": "Child scope filtered",
  };
  return labels[normalized] ?? (normalized ? humanLabel(normalized) : "Diagnostics");
}

export function diagnosticsRuleDescription(value: unknown) {
  const category = normalizeCategory(value);
  switch (category) {
    case "read/cache warning":
      return "A log source emitted a read or cache warning.";
    case "orphan dataflow job id":
    case "missing job log":
      return "Dataflow logs reference a job identity that is missing from the evaluated job evidence.";
    case "job without dataflows":
    case "job without dataflow records":
    case "missing expected child evidence":
    case "missing expected dataflow records":
      return "A terminal job expected child dataflow evidence, but no eligible child records were found.";
    case "job children unassessed":
      return "Child evidence cannot be assessed until the recorded scope, job snapshot, expected total and log-loss evidence are sufficient.";
    case "no child expected":
      return "The job explicitly reported that no child dataflow records were expected.";
    case "reconciliation mismatch":
      return "A comparable job total does not match the child dataflow rollup.";
    case "reconciliation coverage":
    case "insufficient reconciliation coverage":
      return "The selected evidence does not provide a comparable population for reconciliation.";
    case "field completeness":
    case "field coverage":
      return "A required Monitoring evidence-field group is below its presence threshold.";
    case "source coverage":
      return "The source readiness observation needs review for the current log evidence.";
    case "diagnostic capture":
    case "capture":
      return "A dataflow record contains captured diagnostic detail or an omitted diagnostic marker.";
    case "log loss":
      return "The published evidence reports dropped records or bytes for the evaluated scope.";
    case "log loss unassessed":
      return "Some evaluated jobs do not publish loss counters, so the observed subtotal does not cover the full scope.";
    case "job log loss":
      return "This job's latest published observation reports dropped records or bytes. Child records may be incomplete in the log; absence is not proof of absence from the source.";
    case "job log loss unassessed":
      return "This job does not publish every loss counter, so its log completeness cannot be established.";
    case "scope notice":
      return "Active filters restrict the child rows, so job totals are not compared in this projection. The job's own snapshot and loss counters are available on the canonical job.";
    default:
      return "Diagnostics evidence needs review.";
  }
}

export function reconciliationEvidenceBasis(row: DiagnosticsRow): string | null {
  const coverage = String(firstMetric(row, "scope_coverage", "child_scope_coverage") ?? "").trim().toLowerCase();
  const status = String(firstMetric(row, "status", "severity") ?? "").trim().toLowerCase();
  const hasObservedAt = hasPresentationValue(row.observed_at);
  const hasLatestChildTime = hasPresentationValue(row.latest_child_time);
  const completeRecordsDropped = firstMetric(row, "log_records_dropped");
  const completeBytesDropped = firstMetric(row, "log_bytes_dropped");
  const observedRecordsDropped = firstMetric(row, "log_records_dropped_observed_subtotal");
  const observedBytesDropped = firstMetric(row, "log_bytes_dropped_observed_subtotal");
  const hasRecordsDropped = hasPresentationValue(completeRecordsDropped);
  const hasBytesDropped = hasPresentationValue(completeBytesDropped);
  const hasObservedRecords = hasPresentationValue(observedRecordsDropped);
  const hasObservedBytes = hasPresentationValue(observedBytesDropped);
  if (!coverage && !status && !hasObservedAt && !hasLatestChildTime && !hasRecordsDropped && !hasBytesDropped && !hasObservedRecords && !hasObservedBytes) return null;

  const snapshot = coverage === "unknown_snapshot" || !hasObservedAt
    ? "Snapshot time unknown"
    : coverage === "stale_snapshot"
      ? "Snapshot older than latest child"
      : "Snapshot time available";
  const scope = coverage === "subset" || coverage === "insufficient_coverage"
    ? "Child scope is filtered"
    : coverage === "unknown_total"
      ? "Expected total unknown"
      : coverage === "unknown_loss"
        ? "Loss scope unknown"
        : coverage === "lost_detail"
          ? "Log detail was dropped"
          : coverage && !["full", "unknown_snapshot", "stale_snapshot", "unknown_loss", "lost_detail"].includes(coverage)
            ? `Scope ${humanLabel(coverage)}`
            : "Full child scope";
  const recordsDropped = numberValue(hasRecordsDropped ? completeRecordsDropped : observedRecordsDropped);
  const bytesDropped = numberValue(hasBytesDropped ? completeBytesDropped : observedBytesDropped);
  const loss = !hasRecordsDropped && !hasObservedRecords
    ? "Loss counters unknown"
    : !hasRecordsDropped
      ? recordsDropped > 0 || (hasObservedBytes && bytesDropped > 0)
        ? "Observed loss subtotal"
        : "Observed loss subtotal is zero"
      : recordsDropped > 0 || (hasBytesDropped && bytesDropped > 0)
      ? "Log detail was dropped"
      : !hasBytesDropped
        ? "Record loss counter is zero; byte loss unknown"
        : "Loss counters are zero; record loss is not disproven";
  const parts = [snapshot, scope, loss];
  if (["insufficient_coverage", "subset"].includes(coverage) && !parts.includes("Child scope is filtered")) parts.splice(1, 0, "Coverage is insufficient");
  return Array.from(new Set(parts)).join(" · ");
}

export function diagnosticsApplicabilityLabel(value: unknown) {
  const normalized = normalizeCategory(value);
  if (normalized === "conditional") return "Conditional";
  if (["optional", "informational"].includes(normalized)) return "Optional";
  if (["not applicable", "not_applicable"].includes(normalized)) return "Not applicable";
  return "Required";
}

export function diagnosticsEvidenceItems(categoryValue: unknown, row: DiagnosticsRow, evidence: DiagnosticsRow): DiagnosticsEvidenceItem[] {
  const category = normalizeCategory(categoryValue);
  const severity = diagnosticsSeverityPresentation(row.severity ?? row.status ?? evidence.severity ?? evidence.status);
  const abnormalIntent: DiagnosticsEvidenceIntent = severity.tone === "bad" ? "bad" : severity.tone === "warning" ? "warning" : "neutral";

  if (["field completeness", "field coverage"].includes(category)) {
    const presence = firstMetric(evidence, "presence_rate", "completeness_rate");
    const availability = firstMetric(evidence, "value_availability_rate", "availability_rate");
    return compactEvidenceItems([
      { label: "Presence", value: hasPresentationValue(presence) ? `${presence}%` : null, intent: abnormalIntent, primary: true },
      { label: "Value availability", value: hasPresentationValue(availability) ? `${availability}%` : "Not measured", intent: "info" },
      { label: "Applicability", value: diagnosticsApplicabilityLabel(evidence.applicability), intent: evidence.applicability === "conditional" ? "info" : "neutral" },
      { label: "Record type", value: evidence.record_type },
      { label: "Group", value: evidence.group },
      { label: "Records", value: firstMetric(evidence, "records", "record_count") },
      { label: "Applicable fields", value: firstMetric(evidence, "applicable_fields", "required_fields") },
      { label: "Present fields", value: firstMetric(evidence, "present_fields", "present_values") },
      { label: "Missing fields", value: firstMetric(evidence, "missing_fields", "missing_values"), intent: abnormalIntent },
      { label: "Fields", value: evidence.fields, wide: true },
    ]);
  }
  if (["reconciliation mismatch", "reconciliation coverage", "insufficient reconciliation coverage"].includes(category)) {
    const reconciliation = { ...row, ...evidence };
    const metrics = Array.isArray(evidence.metrics) ? evidence.metrics as DiagnosticsRow[] : [];
    const metricItems: DiagnosticsEvidenceItem[] = metrics.map((metric) => ({
      label: humanLabel(metric.metric),
      value: hasPresentationValue(metric.expected)
        ? `expected ${String(metric.expected)} · observed ${String(metric.observed)}${hasPresentationValue(metric.difference) ? ` · diff ${String(metric.difference)}` : ""}`
        : `observed ${String(metric.observed)} · expected unknown`,
      intent: hasPresentationValue(metric.difference) && numberValue(metric.difference) !== 0 ? abnormalIntent : "neutral",
      wide: true,
    }));
    return compactEvidenceItems([
      { label: "Job ID", value: evidence.job_id ?? row.target, wide: true },
      { label: "Status", value: evidence.status ?? evidence.job_status ?? row.status },
      { label: "Metric", value: evidence.metric },
      ...metricItems,
      { label: "Scope coverage", value: firstMetric(reconciliation, "scope_coverage", "child_scope_coverage") },
      { label: "Assessment", value: diagnosticsAssessment(firstMetric(reconciliation, "assessment", "status"), "not_assessed").label, intent: abnormalIntent },
      { label: "Evidence basis", value: reconciliationEvidenceBasis(reconciliation), wide: true, intent: abnormalIntent },
      { label: "Expected", value: evidence.expected },
      { label: "Observed", value: evidence.observed },
      { label: "Difference", value: evidence.difference, intent: abnormalIntent, primary: true },
      { label: "Observed at", value: reconciliation.observed_at, field: "observed_at" },
      { label: "Latest child", value: reconciliation.latest_child_time, field: "latest_child_time" },
      { label: "Records dropped", value: reconciliation.log_records_dropped },
      { label: "Bytes dropped", value: reconciliation.log_bytes_dropped },
      { label: "Observed records", value: reconciliation.log_records_dropped_observed_subtotal },
      { label: "Observed bytes", value: reconciliation.log_bytes_dropped_observed_subtotal },
      { label: "Reporting jobs", value: reconciliation.log_records_dropped_reporting_jobs },
      { label: "Unknown jobs", value: reconciliation.log_loss_unknown_jobs, intent: "info" },
    ]);
  }
  if (category === "source coverage") {
    return compactEvidenceItems([
      { label: "Source", value: diagnosticsSourceLabel(evidence.source ?? row.target) },
      { label: "Readiness", value: firstMetric(evidence, "readiness", "status"), intent: abnormalIntent, primary: true },
      { label: "File kind", value: evidence.file_kind },
      { label: "Files", value: evidence.file_count },
      { label: "Records", value: evidence.records },
      { label: "Job records", value: evidence.job_records },
      { label: "Dataflow records", value: evidence.dataflow_records },
      { label: "Latest log", value: evidence.latest_log_at, field: "latest_log_at" },
      { label: "Latest observed", value: firstMetric(evidence, "latest_observed_at", "latest_ingested_at"), field: "latest_ingested_at" },
    ]);
  }
  if (["orphan dataflow job id", "job without dataflows", "job without dataflow records", "missing expected child evidence", "missing expected dataflow records", "job children unassessed", "no child expected", "job zero children expected"].includes(category)) {
    return compactEvidenceItems([
      { label: "Job ID", value: evidence.job_id ?? row.target, wide: true },
      { label: "Job status", value: firstMetric(evidence, "job_status", "status") },
      { label: "Child records", value: firstMetric(evidence, "dataflow_records", "child_records") },
      { label: "Expected children", value: firstMetric(evidence, "expected_children", "job_total_dataflows") },
      { label: "Assessment reason", value: evidence.unassessed_reason ? humanLabel(evidence.unassessed_reason) : null, intent: "info", wide: true },
      { label: "Scope coverage", value: evidence.scope_coverage, intent: "info" },
      { label: "Filtered children", value: evidence.filtered_dataflow_records },
      { label: "Recorded children", value: evidence.recorded_dataflow_records },
      { label: "Excluded children", value: evidence.excluded_dataflow_records },
      { label: "Job snapshot", value: evidence.job_observed_at, field: "observed_at" },
      { label: "Records dropped", value: evidence.job_log_records_dropped },
      { label: "Latest", value: row.latest_time, field: "latest_time" },
    ]);
  }
  if (["job log loss", "job log loss unassessed", "scope notice"].includes(category)) {
    const isNotice = category === "scope notice";
    return compactEvidenceItems([
      { label: "Job ID", value: evidence.job_id ?? row.target, wide: true },
      { label: "Job status", value: evidence.job_status },
      { label: "Expected children", value: evidence.job_total_dataflows },
      { label: "Records dropped", value: evidence.job_log_records_dropped ?? (isNotice ? null : "Not reported"), intent: numberValue(evidence.job_log_records_dropped) > 0 ? abnormalIntent : "neutral" },
      { label: "Bytes dropped", value: evidence.job_log_bytes_dropped ?? (isNotice ? null : "Not reported"), intent: numberValue(evidence.job_log_bytes_dropped) > 0 ? abnormalIntent : "neutral" },
      { label: "Job snapshot", value: evidence.job_observed_at, field: "observed_at" },
      { label: "Reconciliation checks", value: isNotice ? evidence.checks : null, intent: "info" },
      { label: "Latest", value: row.latest_time, field: "latest_time" },
    ]);
  }
  if (["read/cache warning", "log loss", "log loss unassessed"].includes(category)) {
    return compactEvidenceItems([
      { label: "Source / path", value: evidence.uri ?? evidence.path ?? row.target, wide: true },
      { label: "Status", value: evidence.status ?? evidence.severity, intent: abnormalIntent },
      { label: "Records dropped", value: evidence.log_records_dropped },
      { label: "Bytes dropped", value: evidence.log_bytes_dropped },
      { label: "Observed records", value: firstMetric(evidence, "log_records_dropped_observed_subtotal") },
      { label: "Observed bytes", value: firstMetric(evidence, "log_bytes_dropped_observed_subtotal") },
      { label: "Reporting jobs", value: firstMetric(evidence, "log_records_dropped_reporting_jobs") },
      { label: "Unknown jobs", value: firstMetric(evidence, "log_loss_unknown_jobs"), intent: "info" },
      { label: "Coverage", value: firstMetric(evidence, "log_loss_coverage"), intent: "info" },
      { label: "Message", value: evidence.message ?? evidence.error, intent: abnormalIntent, wide: true },
    ]);
  }
  if (["diagnostic capture", "capture", "captured detail"].includes(category)) {
    return compactEvidenceItems([
      { label: "Source", value: diagnosticsSourceLabel(evidence._source_id ?? evidence.source_id ?? row.target) },
      { label: "Record type", value: humanLabel(evidence.record_type) },
      { label: "Field", value: evidence.field === "" ? "Diagnostic marker list (empty pointer)" : evidence.field },
      { label: "Reason", value: evidence.reason ?? evidence.message, wide: true, intent: abnormalIntent, primary: true },
      { label: "Run", value: evidence.dataflow_run_id ?? evidence.run_id, wide: true },
      { label: "Latest", value: evidence.latest_time, field: "latest_time" },
    ]);
  }
  const fallback = Object.entries(evidence).slice(0, 12).map(([key, value]) => ({ label: humanLabel(key), value, field: key }));
  return fallback.length ? fallback : compactEvidenceItems([
    { label: "Target", value: row.target, wide: true },
    { label: "Latest", value: row.latest_time, field: "latest_time" },
  ]);
}

export function diagnosticsInvestigationActions(row: DiagnosticsRow, evidence: DiagnosticsRow) {
  const category = normalizeCategory(row.category);
  const primary = String(row.action_hint ?? "").trim();
  const actions = primary ? [primary] : [];
  if (category === "read/cache warning") {
    actions.push("Validate the source path, storage credentials, and file format.");
    actions.push("Run source validation again after fixing the issue.");
  } else if (["orphan dataflow job id", "missing job log"].includes(category)) {
    actions.push("Check whether job run evidence exists for the same source, session, and run window.");
    actions.push("Compare the job identity in dataflow logs with the published job evidence.");
  } else if (["job without dataflows", "job without dataflow records", "missing expected child evidence", "missing expected dataflow records"].includes(category)) {
    const expected = numberValue(firstMetric(evidence, "expected_children", "job_total_dataflows"));
    if (expected > 0) {
      actions.push("Inspect the dataflow evidence for this exact source, session, and job identity.");
      actions.push("Review log source coverage for missing child records.");
    }
  } else if (category === "job children unassessed") {
    actions.push("Open the exact job scope and inspect its recorded child population.");
  } else if (["reconciliation mismatch", "reconciliation coverage", "insufficient reconciliation coverage"].includes(category)) {
    actions.push("Open the related job and compare the expected total with eligible child dataflow records.");
    if (evidence.metric) actions.push(`Review metric ${String(evidence.metric)} for this job.`);
  } else if (["field completeness", "field coverage"].includes(category)) {
    actions.push("Confirm the admitted log version emits this field group.");
    actions.push("This affects evidence coverage; it is separate from execution health.");
  } else if (category === "source coverage") {
    actions.push("Open Sources and validate the affected log source.");
    actions.push("Check the latest log and latest observation timestamps for stale evidence.");
  } else if (["log loss", "log loss unassessed", "job log loss", "job log loss unassessed"].includes(category)) {
    actions.push("Check which evaluated jobs publish loss counters and review the observed subtotal separately from the full scope.");
  } else if (category === "scope notice") {
    actions.push("Open the full job to compare its totals, snapshot and loss counters.");
    actions.push("Or remove the child filters to reconcile the whole job population.");
  } else if (["diagnostic capture", "capture"].includes(category)) {
    actions.push("Open the affected dataflow run and inspect its diagnostic fields.");
  }
  return Array.from(new Set(actions)).filter(Boolean);
}

export function diagnosticsCoverageSummary(rows: DiagnosticsRow[]) {
  const normalized: Array<DiagnosticsRow & { applicability: string; actionable: boolean; severity: string }> = rows.map((row) => {
    const group = normalizeCategory(row.group);
    const sourceApplicability = normalizeCategory(row.applicability ?? row.scope);
    const optional = ["optional", "informational"].includes(sourceApplicability);
    const conditional = sourceApplicability === "conditional" || group === "watermark evidence" || group === "maintenance evidence";
    // A conditional group with no records has no applicable population to assess.
    // Treat both an explicit false and an omitted actionable flag as N/A; only
    // an explicitly actionable group with zero records remains assessable.
    const notApplicable = ["not applicable", "not_applicable"].includes(sourceApplicability)
      || (conditional && row.actionable !== true && numberValue(row.records) === 0);
    const applicability = notApplicable ? "not_applicable" : optional ? "optional" : conditional ? "conditional" : "required";
    const actionable = row.actionable === undefined ? !conditional && !optional && !notApplicable : Boolean(row.actionable);
    const severity = normalizeCategory(row.severity ?? row.status ?? "info");
    return { ...row, applicability, actionable, severity };
  });
  const issues = normalized.filter((row) => row.actionable && ["bad", "error", "warning", "issue"].includes(row.severity));
  const conditional = normalized.filter((row) => row.applicability === "conditional");
  const optional = normalized.filter((row) => row.applicability === "optional");
  const notApplicable = normalized.filter((row) => row.applicability === "not_applicable");
  const ready = normalized.filter((row) => row.actionable && ["good", "clear", "healthy", "ok"].includes(row.severity));
  const unavailable = normalized.filter((row) => row.applicability !== "not_applicable" && (!row.actionable || ["info", "unknown", "not assessed", "not_assessed"].includes(row.severity)));
  return {
    issues,
    conditional,
    optional,
    notApplicable,
    ready,
    unavailable,
    visible: normalized.slice().sort((left, right) => {
      const rank = (row: typeof normalized[number]) => {
        if (row.actionable && ["bad", "error", "warning", "issue"].includes(row.severity)) return 0;
        if (row.applicability === "conditional") return 1;
        if (row.applicability === "optional") return 2;
        if (row.actionable && ["good", "clear", "healthy", "ok"].includes(row.severity)) return 3;
        return 4;
      };
      return rank(left) - rank(right) || String(left.record_type ?? "").localeCompare(String(right.record_type ?? "")) || String(left.group ?? "").localeCompare(String(right.group ?? ""));
    }),
  };
}

export function diagnosticsLinkedJobRow(row: DiagnosticsRow, evidence: DiagnosticsRow) {
  const category = normalizeCategory(row.category);
  if (["orphan dataflow job id", "missing job log", "source coverage", "field coverage", "field completeness", "diagnostic capture", "capture", "job zero children expected", "no child expected"].includes(category)) return null;
  const expected = numberValue(firstMetric(evidence, "expected_children", "job_total_dataflows"));
  if (["job without dataflows", "job without dataflow records", "missing expected child evidence", "missing expected dataflow records"].includes(category) && expected <= 0) return null;
  if (!["job without dataflows", "job without dataflow records", "missing expected child evidence", "missing expected dataflow records", "job children unassessed", "reconciliation mismatch", "reconciliation coverage", "insufficient reconciliation coverage", "job log loss", "job log loss unassessed", "scope notice"].includes(category)) return null;
  const jobIdValue = firstExactIdentityValue([evidence, row], "job_id");
  if (jobIdValue === undefined || jobIdValue === null) return null;
  const linked: DiagnosticsRow = { ...row, ...evidence, job_id: String(jobIdValue), status: evidence.job_status ?? row.job_status ?? row.status };
  const sourceValue = firstExactIdentityValue([evidence, row], "_source_id");
  const sourceId = sourceValue === undefined ? firstExactIdentityValue([evidence, row], "source_id") : sourceValue;
  if (sourceId !== undefined) linked._source_id = sourceId;
  const sessionId = firstExactIdentityValue([evidence, row], "log_session_id");
  if (sessionId !== undefined) linked.log_session_id = sessionId;
  const sessionScope = firstExactIdentityValue([evidence, row], "log_session_scope");
  if (sessionScope !== undefined) linked.log_session_scope = sessionScope;
  return linked;
}

export function firstMetric(row: DiagnosticsRow | null | undefined, ...keys: string[]) {
  if (!row) return undefined;
  for (const key of keys) if (Object.prototype.hasOwnProperty.call(row, key) && row[key] !== undefined) return row[key];
  return undefined;
}

export function numberValue(value: unknown) {
  if (typeof value === "bigint") return Number(value);
  if (typeof value === "number") return Number.isFinite(value) ? value : 0;
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : 0;
  }
  return 0;
}

function normalizeCategory(value: unknown) {
  return String(value ?? "").trim().toLowerCase().replace(/_/g, " ").replace(/\s+/g, " ");
}

function compactEvidenceItems(items: DiagnosticsEvidenceItem[]) {
  return items.filter((item) => hasPresentationValue(item.value));
}

function hasPresentationValue(value: unknown) {
  return value !== null && value !== undefined && value !== "";
}

function firstExactIdentityValue(rows: DiagnosticsRow[], field: string) {
  for (const source of rows) {
    if (!Object.prototype.hasOwnProperty.call(source, field)) continue;
    // Identity fields are opaque and nullable. An explicit null session is a
    // valid canonical scope; only an absent property means no identity was
    // supplied by the API.
    return source[field];
  }
  return undefined;
}

function humanLabel(value: unknown) {
  const text = String(value ?? "unknown");
  if (!text || text === "null" || text === "undefined") return "Unknown";
  return text.replace(/_/g, " ").replace(/\b\w/g, (character) => character.toUpperCase());
}
