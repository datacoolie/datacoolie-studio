import type { SourceValidationResult } from "../../shared/api/domainTypes";
import type { SourceSyncStatus } from "../../shared/api/contractTypes";
import { formatAbsoluteTime } from "../../shared/time";

export type SourceStatusTone = "ok" | "warn" | "error" | "muted";
export type SourceStatusIcon = "check" | "warning" | "error" | "clock" | "running" | "refresh";

export type SourceStatusView = {
  label: string;
  tone: SourceStatusTone;
  icon: SourceStatusIcon;
  timestamp?: string | null;
  tooltip: string;
};

export function sourceStatusDetailMessage(error?: Record<string, unknown> | null) {
  const message = error?.message;
  return typeof message === "string" && message.trim() ? message.trim() : null;
}

export function validationStateFromResult(validation: SourceValidationResult | null) {
  if (!validation) return "not_validated";
  if (validation.status === "ok") return "ready";
  if (validation.status === "warning") return "warning";
  return "invalid";
}

export function validationLabel(state: string) {
  return {
    not_validated: "Not validated",
    validating: "Validating",
    ready: "Ready",
    warning: "Warning",
    invalid: "Invalid",
  }[state] ?? "Not validated";
}

export function validationTooltip(
  status: SourceSyncStatus["validation"] | null,
  fallback: SourceValidationResult | null,
  timestamp: string | null | undefined,
  timezoneName: string | null,
) {
  const summary = status?.summary ?? (fallback ? compactValidationSummary(fallback) : null);
  const error = sourceStatusDetailMessage(status?.error) ?? fallback?.errors?.[0]?.message;
  return [
    timestamp ? `Validated ${formatAbsoluteTime(timestamp, timezoneName) ?? timestamp}` : "Not yet validated",
    summary,
    error ? `Error: ${error}` : null,
  ].filter(Boolean).join(" · ");
}

export function checkLabel(state: string) {
  return {
    never: "Not checked",
    checking: "Checking",
    unchanged: "No changes",
    changed: "Changes found",
    error: "Check failed",
    paused: "Paused",
  }[state] ?? "Not checked";
}

export function checkTooltip(
  observation: SourceSyncStatus["observation"] | null,
  timestamp: string | null | undefined,
  timezoneName: string | null,
) {
  const nextCheck = observation?.next_check_at;
  const pendingChanges = observation?.pending_changes;
  const syncBaseline = pendingChanges === true
    ? "Changes since last sync"
    : pendingChanges === false
      ? "No changes since last sync"
      : null;
  const error = sourceStatusDetailMessage(observation?.error);
  return [
    timestamp ? `Checked ${formatAbsoluteTime(timestamp, timezoneName) ?? timestamp}` : "Not yet checked",
    syncBaseline,
    nextCheck ? `Next check ${formatAbsoluteTime(nextCheck, timezoneName) ?? nextCheck}` : null,
    error ? `Error: ${error}` : null,
  ].filter(Boolean).join(" · ");
}

export function syncLabel(state: string) {
  return {
    never: "Never synced",
    running: "Running",
    succeeded: "Succeeded",
    failed: "Failed",
  }[state] ?? "Never synced";
}

export function syncExecutionTooltip(
  execution: SourceSyncStatus["sync_execution"] | null,
  timestamp: string | null | undefined,
  timezoneName: string | null,
) {
  const trigger = execution?.trigger ? `Trigger: ${execution.trigger}` : null;
  const error = sourceStatusDetailMessage(execution?.error);
  const lastSuccess = execution?.last_successful_at;
  const timestampClause = timestamp
    ? `${execution?.state === "running" ? "Started" : "Completed"} ${formatAbsoluteTime(timestamp, timezoneName) ?? timestamp}`
    : "No sync has completed";
  const detail = error ? `Error: ${error}` : execution?.summary;
  const history = lastSuccess && (execution?.state === "running" || execution?.state === "failed")
    ? `Last success ${formatAbsoluteTime(lastSuccess, timezoneName) ?? lastSuccess}`
    : null;
  if (history) {
    return [timestampClause, error ? detail : trigger ?? detail, history].filter(Boolean).join(" · ");
  }
  return [timestampClause, trigger, detail].filter(Boolean).slice(0, 3).join(" · ");
}

function compactValidationSummary(validation: SourceValidationResult) {
  const counts = validation.record_counts ?? {};
  if (validation.source_kind === "metadata") {
    return validation.detected_format ? `${validation.detected_format} file` : "metadata file";
  }
  if (validation.source_kind === "sql") {
    const files = counts.sql_files;
    return typeof files === "number" ? `${files} SQL file${files === 1 ? "" : "s"}` : "SQL source";
  }
  if (validation.source_kind === "logs") {
    const jobs = counts.job_json_files;
    const dataflows = counts.dataflow_json_files;
    const systems = counts.system_json_files;
    if (typeof jobs === "number" || typeof dataflows === "number" || typeof systems === "number") {
      return `${jobs ?? 0} job, ${dataflows ?? 0} run, ${systems ?? 0} system files`;
    }
  }
  if (validation.source_kind === "code") {
    return `${counts.python_files ?? 0} Python files, ${counts.modules ?? 0} modules`;
  }
  return validation.message;
}
