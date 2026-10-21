import type {
  DiagnosticsFindingGroup,
  DiagnosticsFindingKind,
  DiagnosticsFindingObject,
  DiagnosticsScopeNotice,
} from "../../shared/api/domainTypes";

export type DiagnosticsFindingTone = "bad" | "warning" | "info";

const RULE_TITLES: Record<string, string> = {
  orphan_job_log: "Missing job log",
  missing_expected_children: "Missing expected child records",
  reconciliation_mismatch: "Reconciliation mismatch",
  field_completeness: "Field coverage",
  log_loss: "Log loss",
  diagnostic_capture: "Captured detail",
  linkage_unassessed: "Child evidence unassessed",
  reconciliation_unassessed: "Reconciliation unassessed",
  log_loss_unknown: "Loss counters unknown",
  scope_child_subset: "Child scope filtered",
};

const REASON_LABELS: Record<string, string> = {
  missing_job_log: "No matching job log",
  terminal_job_without_children: "Terminal job reports children; none recorded",
  count_mismatch: "Job totals differ from child records",
  critical_presence: "Presence below 80%",
  low_presence: "Presence below 95%",
  records: "Records dropped",
  bytes: "Bytes dropped",
  records_and_bytes: "Records and bytes dropped",
  unknown_counter: "Loss counter not reported",
  nonterminal: "Job is not terminal",
  unknown_loss: "Loss counters unknown",
  lost_detail: "Log detail was dropped",
  unknown_snapshot: "Snapshot time unknown",
  stale_snapshot: "Snapshot older than child records",
  unknown_total: "Expected total unknown",
  range_incomplete: "Time window excludes child records",
  unassessed: "Evidence is insufficient",
  filtered_child_scope: "Active filters restrict child rows",
};

const RESTRICTION_LABELS: Record<string, string> = {
  stage: "Stage",
  connection: "Connection",
  sourceType: "Source type",
  destinationType: "Destination type",
  loadType: "Load type",
  operationType: "Operation type",
  status: "Status",
  engine: "Engine",
  provider: "Provider",
  search: "Search",
  investigate: "Investigation",
};

export function findingKindPresentation(kind: DiagnosticsFindingKind): { label: string; tone: DiagnosticsFindingTone } {
  switch (kind) {
    case "issue": return { label: "Issue", tone: "bad" };
    case "warning": return { label: "Warning", tone: "warning" };
    case "limitation": return { label: "Limitation", tone: "info" };
    default: return { label: "Scope notice", tone: "info" };
  }
}

function humanize(value: string) {
  return value.replace(/_/g, " ").replace(/^\w/, (character) => character.toUpperCase());
}

export function findingRuleTitle(rule: string) {
  return RULE_TITLES[rule] ?? humanize(rule);
}

export function findingReasonLabel(reason: string) {
  return REASON_LABELS[reason] ?? humanize(reason);
}

/** The evidence section (capture pointer or field group) a group is scoped to. */
export function findingSectionLabel(rule: string, section: string) {
  if (rule === "diagnostic_capture") return section === "" ? "Diagnostic marker list" : section;
  if (rule === "field_completeness") return section.replace("/", " · ");
  return null;
}

export function findingReasonText(group: Pick<DiagnosticsFindingGroup, "rule" | "reason">) {
  return group.rule === "diagnostic_capture" ? humanize(group.reason) : findingReasonLabel(group.reason);
}

export function findingGroupSummary(group: Pick<DiagnosticsFindingGroup, "rule" | "reason" | "section">) {
  const section = findingSectionLabel(group.rule, group.section);
  const reason = findingReasonText(group);
  return section ? `${reason} · ${section}` : reason;
}

/** Name the counted unit explicitly; heterogeneous units are never summed. */
export function findingUnits(group: Pick<DiagnosticsFindingGroup, "subject_kind" | "rule">) {
  if (group.subject_kind === "dataflow_record") return { objects: "dataflow records", detail: "markers" };
  if (group.subject_kind === "field_group") return { objects: "field groups", detail: "missing field slots" };
  return { objects: "jobs", detail: group.rule.startsWith("reconciliation") ? "checks" : null };
}

/** The counted unit with correct number agreement, e.g. "1 job" or "73 jobs". */
export function findingCountLabel(count: unknown, units: string) {
  const singular = String(count) === "1" ? units.replace(/s$/, "") : units;
  return `${String(count)} ${singular}`;
}

export function scopeRestrictionLabels(restrictions: readonly string[]) {
  return restrictions.map((name) => RESTRICTION_LABELS[name] ?? humanize(name));
}

export function scopeNoticeText(notice: DiagnosticsScopeNotice) {
  const restrictions = scopeRestrictionLabels(notice.restrictions);
  const filters = restrictions.length ? restrictions.join(", ") : "the active filters";
  return `${notice.jobs} job${notice.jobs === "1" ? "" : "s"} · ${notice.checks} reconciliation checks are not compared: ${filters} limits child records to a subset. `
    + "This is a scope limit, not a failure. Open the full job or widen the filters to compare totals, snapshot and loss.";
}

function metricsOf(object: DiagnosticsFindingObject) {
  return (object.metrics ?? []).filter((metric) => metric.metric);
}

export function findingObjectIssueText(object: DiagnosticsFindingObject) {
  switch (object.rule) {
    case "orphan_job_log":
      return "Dataflow records reference a job_id with no matching job log.";
    case "missing_expected_children":
      return "A terminal job reports child dataflows, but no matching child records were recorded.";
    case "reconciliation_mismatch": {
      const metrics = metricsOf(object);
      return `${metrics.length} reconciliation check${metrics.length === 1 ? "" : "s"} differ from child records.`;
    }
    case "reconciliation_unassessed":
      return `${object.checks ?? metricsOf(object).length} reconciliation checks cannot be assessed: ${findingReasonLabel(object.reason).toLowerCase()}.`;
    case "linkage_unassessed":
      return `Child records cannot be assessed: ${findingReasonLabel(object.reason).toLowerCase()}.`;
    case "log_loss":
      return `${findingReasonLabel(object.reason)} for this job.`;
    case "log_loss_unknown":
      return "This job does not report every loss counter.";
    case "diagnostic_capture":
      return `Diagnostic detail was ${object.reason} at ${findingSectionLabel("diagnostic_capture", object.section ?? "")}.`;
    case "scope_child_subset":
      return "Child records are filtered; totals, snapshot and loss are compared on the full job.";
    case "field_completeness":
      return "A required evidence-field group is below its presence threshold.";
    default:
      return "Diagnostics evidence needs review.";
  }
}

/**
 * Adapt a finding object to the existing drawer row so the canonical drawer,
 * evidence cards and Job/run/Source navigation are reused. Identity fields
 * keep their exact nullable values.
 */
export function findingObjectToDrawerRow(object: DiagnosticsFindingObject, kind: DiagnosticsFindingKind): Record<string, unknown> {
  const identity = {
    _source_id: object.source_id ?? null,
    log_session_id: object.log_session_id ?? null,
    job_id: object.job_id ?? null,
  };
  const jobLevel = {
    ...identity,
    job_status: object.job_status ?? null,
    job_total_dataflows: object.job_total_dataflows ?? null,
    job_observed_at: object.observed_at ?? null,
    job_log_records_dropped: object.records_dropped ?? null,
    job_log_bytes_dropped: object.bytes_dropped ?? null,
  };
  const metrics = metricsOf(object);
  let category: string;
  let evidence: Record<string, unknown>;
  switch (object.rule) {
    case "orphan_job_log":
      category = "orphan dataflow job id";
      evidence = { ...identity, dataflow_records: object.filtered_dataflow_records ?? null };
      break;
    case "missing_expected_children":
      category = "missing expected dataflow records";
      evidence = { ...identity, job_status: object.job_status ?? null, job_total_dataflows: object.job_total_dataflows ?? null };
      break;
    case "linkage_unassessed":
      category = "job children unassessed";
      evidence = {
        ...jobLevel,
        filtered_dataflow_records: object.filtered_dataflow_records ?? null,
        recorded_dataflow_records: object.recorded_dataflow_records ?? null,
        excluded_dataflow_records: object.excluded_dataflow_records ?? null,
        unassessed_reason: object.reason,
      };
      break;
    case "reconciliation_mismatch":
    case "reconciliation_unassessed":
      category = object.rule === "reconciliation_mismatch" ? "reconciliation mismatch" : "insufficient reconciliation coverage";
      evidence = {
        ...identity,
        job_status: object.job_status ?? null,
        assessment: object.rule === "reconciliation_mismatch" ? "issue" : "not_assessed",
        metrics,
        scope_coverage: object.rule === "reconciliation_mismatch" ? "full" : object.reason,
        observed_at: object.observed_at ?? null,
        log_records_dropped: object.records_dropped ?? null,
        log_bytes_dropped: object.bytes_dropped ?? null,
      };
      break;
    case "log_loss":
    case "log_loss_unknown":
      category = object.rule === "log_loss" ? "job log loss" : "job log loss unassessed";
      evidence = { ...jobLevel, loss_reason: object.reason };
      break;
    case "diagnostic_capture":
      category = "diagnostic capture";
      evidence = {
        ...identity,
        dataflow_id: object.dataflow_id ?? null,
        dataflow_run_id: object.dataflow_run_id ?? null,
        dataflow_name: object.dataflow_name ?? null,
        record_type: "dataflow_run_log",
        field: object.section ?? "",
        reason: object.reason,
        markers: object.occurrences ?? null,
        file_uri: object.file_uri ?? null,
        record_line: object.record_line ?? null,
        latest_time: object.latest_time ?? null,
      };
      break;
    case "field_completeness":
      category = "field completeness";
      evidence = { ...(object.field_group ?? {}) };
      break;
    default:
      category = "scope notice";
      evidence = { ...jobLevel, checks: object.checks ?? null };
  }
  const severity = kind === "issue" ? "bad" : kind === "warning" ? "warning" : "info";
  const target = object.rule === "field_completeness"
    ? findingSectionLabel("field_completeness", object.section)
    : object.rule === "diagnostic_capture"
      ? (object.dataflow_run_id ?? object.job_id)
      : object.job_id;
  return {
    severity,
    category,
    issue: findingObjectIssueText(object),
    target: target ?? "—",
    latest_time: object.latest_time ?? null,
    action_hint: "",
    ...identity,
    dataflow_id: object.dataflow_id ?? undefined,
    dataflow_run_id: object.dataflow_run_id ?? undefined,
    finding_rule: object.rule,
    finding_reason: object.reason,
    finding_kind: kind,
    evidence,
  };
}
