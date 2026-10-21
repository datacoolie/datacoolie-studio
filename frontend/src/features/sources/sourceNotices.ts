import type {
  LogSyncRequest,
  SourceImportResponse,
  SourceSyncStatus,
} from "../../shared/api/contractTypes";
import type {
  SourceBatchAction,
  SourceBatchEntry,
  SourceBatchResult,
} from "./sourceWorkspaceModel";
import { formatSourceImportSummary } from "./sourceSetupModel";
import type { OperationNotice } from "../../shared/components/OperationNotification";
import type { SourceEntry } from "./sourcePageTypes";

export type SourceOperationNotice = OperationNotice | null;

export function toBatchEntries(entries: SourceEntry[]): SourceBatchEntry[] {
  return entries.map((entry) => ({ kind: entry.kind, id: entry.source.id }));
}

export function operationFailureNotice(title: string, detail = "Review the error and retry the action."): SourceOperationNotice {
  return {
    tone: "error",
    title,
    detail,
    errors: [],
  };
}

export function batchNotice(action: SourceBatchAction, result: SourceBatchResult, context?: string): SourceOperationNotice {
  const actionLabel = action === "validate" ? "Validation" : action === "sync" ? "Sync" : "Deletion";
  const completed = result.succeeded + result.warnings;
  const parts = [
    `${result.succeeded} ${action === "validate" ? "validated" : action === "sync" ? "synced" : "deleted"}`,
    result.warnings ? `${result.warnings} completed with warning${result.warnings === 1 ? "" : "s"}` : "",
    result.failed ? `${result.failed} failed` : "",
  ].filter(Boolean);
  return {
    tone: result.failed === result.total ? "error" : result.failed || result.warnings ? "warning" : "success",
    title: result.failed ? `${actionLabel} completed with issues` : `${actionLabel} complete`,
    detail: `${context ? `${context} · ` : ""}${completed}/${result.total} sources processed · ${parts.join(" · ")}`,
    errors: result.errors,
  };
}

export function logSyncNotice(status: SourceSyncStatus, request: LogSyncRequest): SourceOperationNotice {
  const result = status.sync_execution?.result ?? {};
  const counts = [
    countSummary(result, "candidate_files", "candidate files"),
    countSummary(result, "replaced_files", "replaced"),
    countSummary(result, "inserted_rows", "rows"),
    countSummary(result, "unchanged_files", "unchanged"),
  ].filter(Boolean);
  return {
    tone: status.status === "error" ? "error" : status.status === "warning" ? "warning" : "success",
    title: status.status === "error" ? "Log sync failed" : "Log sync complete",
    detail: [logSyncModeLabel(request), ...counts, status.message].filter(Boolean).join(" · "),
    errors: status.status === "error" ? [status.message] : [],
  };
}

export function logSyncModeLabel(request: LogSyncRequest) {
  return request.mode === "incremental" ? "Incremental" : "Incremental + lookback";
}

function countSummary(result: Record<string, unknown>, key: string, label: string) {
  const value = result[key];
  return typeof value === "number" ? `${value.toLocaleString()} ${label}` : "";
}

export function importNotice(result: SourceImportResponse | null, title: string): SourceOperationNotice {
  if (!result) return operationFailureNotice(`${title.replace(" complete", "")} could not be completed`);
  const created = result.summary.created ?? result.created.length;
  const existing = result.summary.existing ?? result.existing.length;
  const staleItems = result.stale ?? [];
  const stale = result.summary.stale ?? staleItems.length;
  const initializationQueued = result.summary.initialization_queued ?? 0;
  const errors = result.errors.map((item) => String(item.message ?? item.uri ?? JSON.stringify(item)));
  return {
    tone: errors.length ? created || existing ? "warning" : "error" : stale ? "warning" : "success",
    title,
    detail: `${formatSourceImportSummary({ summary: result.summary, created, existing })}${stale ? ` · ${stale} missing` : ""}${initializationQueued ? ` · ${initializationQueued} initializing` : ""}`,
    errors: [
      ...errors,
      ...staleItems.map((item) => `${item.uri} is no longer present and was disabled`),
    ],
  };
}
