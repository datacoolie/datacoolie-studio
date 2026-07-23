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

export function diagnosticsSeverityPresentation(value: unknown): { label: string; tone: DiagnosticsTone } {
  const normalized = String(value ?? "info").trim().toLowerCase();
  if (normalized === "bad" || normalized === "error") return { label: "Issue", tone: "bad" };
  if (normalized === "warning") return { label: "Warning", tone: "warning" };
  if (normalized === "good" || normalized === "clear" || normalized === "healthy") return { label: "Clear", tone: "good" };
  return { label: "Info", tone: "info" };
}

export function diagnosticsLinkagePresentation(row: DiagnosticsRow): { label: string; tone: DiagnosticsTone } {
  const category = String(row.category ?? "").toLowerCase();
  const count = Number(row.count ?? 0);
  if (category === "matched") return { label: "Matched", tone: "good" };
  if (!Number.isFinite(count) || count <= 0) return { label: "Clear", tone: "good" };
  if (category === "orphan_dataflow_job_id") return { label: "Issue", tone: "bad" };
  if (category === "job_without_dataflow_records") return { label: "Warning", tone: "warning" };
  return diagnosticsSeverityPresentation(row.severity);
}

export function diagnosticsSourceLabel(value: unknown) {
  const source = String(value ?? "").trim();
  const sourceId = source.match(/^source:(.+)$/i)?.[1];
  if (sourceId) return `Log source ${sourceId}`;
  if (source.toLowerCase() === "direct-reader") return "Direct reader";
  return source || "Unknown source";
}

export function diagnosticsCategoryLabel(value: unknown) {
  const normalized = String(value ?? "").trim().toLowerCase();
  if (normalized === "field completeness") return "Evidence coverage";
  return normalized
    ? normalized.replace(/_/g, " ").replace(/\b\w/g, (character) => character.toUpperCase())
    : "Diagnostics";
}

export function diagnosticsRuleDescription(value: unknown) {
  switch (String(value ?? "").trim().toLowerCase()) {
    case "read/cache warning":
      return "A log source emitted a read or cache warning.";
    case "orphan dataflow job id":
      return "Dataflow logs reference a job ID that is missing from job logs.";
    case "job without dataflows":
      return "A job log exists but no child dataflow records were found.";
    case "reconciliation mismatch":
      return "A job total does not match the child dataflow rollup.";
    case "field completeness":
      return "A required Monitoring evidence-field group is below its completeness threshold.";
    case "source coverage":
      return "A log source has warning evidence in the current filter.";
    default:
      return "Diagnostics evidence needs review.";
  }
}

export function diagnosticsApplicabilityLabel(value: unknown) {
  return String(value ?? "").trim().toLowerCase() === "conditional" ? "Conditional" : "Required";
}

export function diagnosticsEvidenceItems(
  categoryValue: unknown,
  row: DiagnosticsRow,
  evidence: DiagnosticsRow,
): DiagnosticsEvidenceItem[] {
  const category = String(categoryValue ?? "").trim().toLowerCase();
  const severity = diagnosticsSeverityPresentation(row.severity ?? evidence.severity);
  const abnormalIntent: DiagnosticsEvidenceIntent = severity.tone === "bad" ? "bad" : severity.tone === "warning" ? "warning" : "neutral";

  if (category === "field completeness") {
    return compactEvidenceItems([
      {
        label: "Completeness",
        value: hasPresentationValue(evidence.completeness_rate) ? `${evidence.completeness_rate}%` : null,
        intent: abnormalIntent,
        primary: true,
      },
      { label: "Applicability", value: diagnosticsApplicabilityLabel(evidence.applicability), intent: evidence.applicability === "conditional" ? "info" : "neutral" },
      { label: "Record type", value: evidence.record_type },
      { label: "Group", value: evidence.group },
      { label: "Records", value: evidence.records },
      { label: "Required fields", value: evidence.required_fields },
      { label: "Present values", value: evidence.present_values },
      { label: "Missing values", value: evidence.missing_values, intent: abnormalIntent },
      { label: "Fields", value: evidence.fields, wide: true },
    ]);
  }
  if (category === "reconciliation mismatch") {
    return compactEvidenceItems([
      { label: "Job ID", value: evidence.job_id ?? row.target, wide: true },
      { label: "Metric", value: evidence.metric },
      { label: "Expected", value: evidence.expected },
      { label: "Observed", value: evidence.observed },
      { label: "Difference", value: evidence.difference, intent: abnormalIntent, primary: true },
    ]);
  }
  if (category === "source coverage") {
    return compactEvidenceItems([
      { label: "Source", value: diagnosticsSourceLabel(evidence.source ?? row.target) },
      { label: "File kind", value: evidence.file_kind },
      { label: "Files", value: evidence.file_count },
      { label: "Records", value: evidence.records },
      { label: "Job records", value: evidence.job_records },
      { label: "Dataflow records", value: evidence.dataflow_records },
      { label: "Warnings", value: evidence.warning_count, intent: Number(evidence.warning_count ?? 0) > 0 ? "warning" : "neutral", primary: true },
      { label: "Latest log", value: evidence.latest_log_at, field: "latest_log_at" },
      { label: "Latest ingested", value: evidence.latest_ingested_at, field: "latest_ingested_at" },
    ]);
  }
  if (category === "orphan dataflow job id" || category === "job without dataflows") {
    return compactEvidenceItems([
      { label: "Job ID", value: evidence.job_id ?? row.target, wide: true },
      { label: "Dataflow records", value: evidence.dataflow_records },
      { label: "Job total dataflows", value: evidence.job_total_dataflows },
      { label: "Latest", value: row.latest_time, field: "latest_time" },
    ]);
  }
  if (category === "read/cache warning") {
    return compactEvidenceItems([
      { label: "Source / path", value: evidence.uri ?? evidence.path ?? row.target, wide: true },
      { label: "Status", value: evidence.status ?? evidence.severity, intent: abnormalIntent },
      { label: "Message", value: evidence.message ?? evidence.error, intent: abnormalIntent, wide: true },
    ]);
  }
  const fallback = Object.entries(evidence).slice(0, 12).map(([key, value]) => ({
    label: key.replace(/_/g, " ").replace(/\b\w/g, (character) => character.toUpperCase()),
    value,
    field: key,
  }));
  return fallback.length ? fallback : compactEvidenceItems([
    { label: "Target", value: row.target, wide: true },
    { label: "Latest", value: row.latest_time, field: "latest_time" },
  ]);
}

export function diagnosticsInvestigationActions(row: DiagnosticsRow, evidence: DiagnosticsRow) {
  const category = String(row.category ?? "").trim().toLowerCase();
  const primary = String(row.action_hint ?? "").trim();
  const actions = primary ? [primary] : [];
  if (category === "read/cache warning") {
    actions.push("Validate the source path, storage credentials, and file format.");
    actions.push("Run sync again after fixing the source issue.");
  } else if (category === "orphan dataflow job id") {
    actions.push("Check whether job run logs exist for the same run window.");
    actions.push("Compare the job ID in dataflow logs with cached job logs.");
  } else if (category === "job without dataflows") {
    actions.push("Check whether dataflow run logs were written and cached for this job ID.");
    actions.push("Inspect ETL log source coverage for missing dataflow files.");
  } else if (category === "reconciliation mismatch") {
    actions.push("Open the job drawer and compare job totals with child dataflow rows.");
    actions.push(`Review metric ${String(evidence.metric ?? "mismatch")} for this job.`);
  } else if (category === "field completeness") {
    actions.push("Confirm the ETL log version emits this field group.");
    actions.push("This affects evidence coverage, not Core integrity.");
  } else if (category === "source coverage") {
    actions.push("Open Sources and validate or sync the affected ETL log path.");
    actions.push("Check latest log and latest ingested timestamps for stale cache evidence.");
  }
  return Array.from(new Set(actions)).filter(Boolean);
}

function compactEvidenceItems(items: DiagnosticsEvidenceItem[]) {
  return items.filter((item) => hasPresentationValue(item.value));
}

function hasPresentationValue(value: unknown) {
  return value !== null && value !== undefined && value !== "";
}

export function diagnosticsCoverageSummary(rows: DiagnosticsRow[]) {
  const normalized: Array<DiagnosticsRow & { applicability: string; actionable: boolean; severity: string }> = rows.map((row) => {
    const group = String(row.group ?? "").toLowerCase();
    const conditional = row.applicability === "conditional" || group === "watermark evidence" || group === "maintenance evidence";
    const actionable = row.actionable === undefined ? !conditional : Boolean(row.actionable);
    const severity = String(row.severity ?? "info").toLowerCase();
    return { ...row, applicability: conditional ? "conditional" : "universal", actionable, severity };
  });
  const issues = normalized.filter((row) => row.actionable && (row.severity === "bad" || row.severity === "error" || row.severity === "warning"));
  const conditional = normalized.filter((row) => row.applicability === "conditional");
  const ready = normalized.filter((row) => row.actionable && row.severity === "good");
  const unavailable = normalized.filter((row) => row.actionable && row.severity === "info");
  return {
    issues,
    conditional,
    ready,
    unavailable,
    visible: normalized.slice().sort((left, right) => {
      const rank = (row: DiagnosticsRow & { applicability: string; actionable: boolean; severity: string }) => {
        if (row.actionable && ["bad", "error", "warning"].includes(row.severity)) return 0;
        if (row.applicability === "conditional") return 1;
        if (row.actionable && row.severity === "good") return 2;
        return 3;
      };
      return rank(left) - rank(right)
        || String(left.record_type ?? "").localeCompare(String(right.record_type ?? ""))
        || String(left.group ?? "").localeCompare(String(right.group ?? ""));
    }),
  };
}

export function diagnosticsLinkedJobRow(row: DiagnosticsRow, evidence: DiagnosticsRow) {
  const category = String(row.category ?? "").trim().toLowerCase();
  if (category === "orphan dataflow job id") return null;
  if (!["job without dataflows", "reconciliation mismatch"].includes(category)) return null;
  const jobId = String(evidence.job_id ?? row.job_id ?? "").trim();
  if (!jobId) return null;
  return {
    ...row,
    ...evidence,
    job_id: jobId,
    status: evidence.job_status ?? row.job_status ?? row.status,
  };
}
