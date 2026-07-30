import type { SourceDeleteImpact, SourceDeleteImpactItem, SourcePath, SourceSyncStatus } from "../../shared/api/domainTypes";
import { sourceKey, type SourceKind } from "../../shared/lib/sources";

export type SourceBatchAction = "validate" | "sync" | "delete";
export type SourceOperationAction = SourceBatchAction | "retry";

export type SourceBatchEntry = {
  kind: SourceKind;
  id: number;
};

export type SourceBatchResult = {
  total: number;
  succeeded: number;
  warnings: number;
  failed: number;
  errors: string[];
};

export type SourceOperation = {
  environmentId: number;
  kind: SourceKind;
  sourceId: number;
  action: SourceOperationAction;
  startedAt: string;
};

export type SourceOperations = Record<string, SourceOperation>;

export type SourceWorkspaceEntry = {
  source: SourcePath;
  kind: SourceKind;
};

export const LOG_REFRESH_INTERVALS = [1, 2, 5, 15, 30, 60] as const;
export const SOURCE_SYNC_STATUS_POLL_INTERVAL_MS = 30_000;
export const SOURCE_IDLE_STATUS_POLL_MIN_MS = 30_000;
export const SOURCE_IDLE_STATUS_POLL_MAX_MS = 300_000;
export const LOCAL_OBSERVATION_DEDUP_MS = 10_000;

export function shouldStartLocalObservation(
  visibilityState: DocumentVisibilityState,
  state: { inFlight: boolean; lastStartedAt: number } | undefined,
  nowMs = Date.now(),
) {
  return visibilityState === "visible"
    && !state?.inFlight
    && (!state || nowMs - state.lastStartedAt >= LOCAL_OBSERVATION_DEDUP_MS);
}

export function isSourceSyncRunning(status: SourceSyncStatus | null | undefined) {
  return Boolean(status?.active_operation)
    || status?.status === "running"
    || status?.latest_job?.status === "running";
}

export function hasRunningSourceSync(syncStatuses: Record<string, SourceSyncStatus>) {
  return Object.values(syncStatuses).some(isSourceSyncRunning);
}

export function sourceSyncStatusPollInterval(
  syncStatuses: Record<string, SourceSyncStatus>,
  nowMs = Date.now(),
) {
  if (hasRunningSourceSync(syncStatuses)) return SOURCE_SYNC_STATUS_POLL_INTERVAL_MS;
  const dueTimes = Object.values(syncStatuses)
    .map((status) => status.next_check_at ? Date.parse(status.next_check_at) : Number.NaN)
    .filter(Number.isFinite);
  if (!dueTimes.length) return false;
  return Math.min(
    SOURCE_IDLE_STATUS_POLL_MAX_MS,
    Math.max(SOURCE_IDLE_STATUS_POLL_MIN_MS, Math.min(...dueTimes) - nowMs + 2_000),
  );
}

export function sourceOperationKey(
  environmentId: number,
  kind: SourceKind,
  sourceId: number,
) {
  return `${environmentId}:${kind}:${sourceId}`;
}

export function beginSourceOperations(
  current: SourceOperations,
  environmentId: number,
  entries: SourceBatchEntry[],
  action: SourceOperationAction,
  startedAt = new Date().toISOString(),
): SourceOperations {
  if (!entries.length) return current;
  const next = { ...current };
  for (const entry of entries) {
    next[sourceOperationKey(environmentId, entry.kind, entry.id)] = {
      environmentId,
      kind: entry.kind,
      sourceId: entry.id,
      action,
      startedAt,
    };
  }
  return next;
}

export function finishSourceOperations(
  current: SourceOperations,
  environmentId: number,
  entries: SourceBatchEntry[],
): SourceOperations {
  const keys = entries
    .map((entry) => sourceOperationKey(environmentId, entry.kind, entry.id))
    .filter((key) => key in current);
  if (!keys.length) return current;
  const next = { ...current };
  keys.forEach((key) => delete next[key]);
  return next;
}

export function sourceOperationFor(
  operations: SourceOperations,
  environmentId: number | null,
  kind: SourceKind,
  sourceId: number,
) {
  return environmentId
    ? operations[sourceOperationKey(environmentId, kind, sourceId)]
    : undefined;
}

export function logRefreshInterval(source: SourcePath) {
  return source.sync_interval_minutes ?? 1;
}

export function logScheduleLabel(source: SourcePath, now = Date.now()) {
  if (!source.sync_schedule_enabled) return "Off";
  if (!source.enabled) return "Paused";
  const interval = logRefreshInterval(source);
  if (!source.last_scheduled_sync_at) return `Starts within ${formatInterval(interval)}`;
  const dueAt = new Date(source.last_scheduled_sync_at).getTime() + interval * 60_000;
  if (!Number.isFinite(dueAt) || dueAt <= now) return "Due now";
  const minutes = Math.max(1, Math.ceil((dueAt - now) / 60_000));
  return `Next in ${formatInterval(minutes)}`;
}

export function aggregateDeleteImpacts(impacts: SourceDeleteImpact[]): SourceDeleteImpactItem[] {
  const totals = new Map<string, SourceDeleteImpactItem>();
  for (const impact of impacts) {
    for (const item of impact.impacts) {
      const key = `${item.kind}:${item.label}`;
      const existing = totals.get(key);
      if (existing) existing.count += item.count;
      else totals.set(key, { ...item });
    }
  }
  return [...totals.values()];
}

function formatInterval(minutes: number) {
  return minutes === 60 ? "1 hour" : `${minutes} min`;
}

export function summarizeSourceHealth(
  entries: SourceWorkspaceEntry[],
  syncStatuses: Record<string, SourceSyncStatus>
) {
  let enabled = 0;
  let readable = 0;
  let current = 0;

  for (const { source, kind } of entries) {
    if (source.enabled) enabled += 1;
    if (source.latest_validation?.status === "ok" || source.latest_validation?.status === "warning") readable += 1;
    const status = syncStatuses[sourceKey(kind, source.id)];
    if (status?.status === "ok") current += 1;
  }

  return { enabled, readable, current };
}

export function sourceDisplayName(source: SourcePath) {
  if (source.label) return source.label;
  const parts = source.uri.replace(/\\/g, "/").split("/").filter(Boolean);
  return parts.at(-1) || source.uri;
}

export function sourceDisplayPath(source: SourcePath, kind: SourceKind) {
  const location = source.configured_location;
  if (!location) return source.uri;

  const locationKey = kind === "metadata" ? "metadata_uri" : kind === "code" ? "code_uri" : null;
  const inputBase = locationKey
    ? location.input_locations[locationKey] || location.input_uri
    : location.input_uri;
  const canonicalBase = locationKey
    ? location.canonical_locations[locationKey] || location.canonical_uri
    : location.canonical_uri;
  const suffix = sourceLocationSuffix(source.uri, canonicalBase);
  return suffix === null ? source.uri : joinDisplayLocation(inputBase, suffix);
}

function sourceLocationSuffix(uri: string, base: string) {
  const normalizedUri = uri.replace(/\\/g, "/").replace(/\/+$/, "");
  const normalizedBase = base.replace(/\\/g, "/").replace(/\/+$/, "");
  const caseInsensitive = /^[a-z]:\//i.test(normalizedBase);
  const comparableUri = caseInsensitive ? normalizedUri.toLowerCase() : normalizedUri;
  const comparableBase = caseInsensitive ? normalizedBase.toLowerCase() : normalizedBase;
  if (comparableUri === comparableBase) return "";
  return comparableUri.startsWith(`${comparableBase}/`)
    ? normalizedUri.slice(normalizedBase.length)
    : null;
}

function joinDisplayLocation(base: string, suffix: string) {
  if (!suffix) return base;
  const separator = base.includes("://") || !base.includes("\\") ? "/" : "\\";
  const normalizedBase = base.replace(/[\\/]+$/, "");
  const relative = suffix.replace(/^[\\/]+/, "").replace(/[\\/]/g, separator);
  return `${normalizedBase}${separator}${relative}`;
}

export function sourceDeletionWarning(kind: SourceKind, name: string, count = 1) {
  if (kind === "logs") {
    const target = count === 1 ? `log source \"${name}\"` : `${count} log sources`;
    return `Delete ${target}? Cached log data and sync history for ${count === 1 ? "this source" : "these sources"} will be removed from Datacoolie Studio. Original log files will not be deleted.`;
  }
  return count === 1 ? `Remove source \"${name}\"?` : `Remove ${count} sources?`;
}
