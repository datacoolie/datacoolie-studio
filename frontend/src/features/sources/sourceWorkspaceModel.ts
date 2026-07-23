import type { SourceDeleteImpact, SourceDeleteImpactItem, SourcePath, SourceSyncStatus } from "../../shared/api/types";
import { sourceKey, type SourceKind } from "../../shared/lib/sources";

export type SourceWorkspaceEntry = {
  source: SourcePath;
  kind: SourceKind;
};

export const LOG_REFRESH_INTERVALS = [1, 2, 5, 15, 30, 60] as const;

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

export function sourceDeletionWarning(kind: SourceKind, name: string, count = 1) {
  if (kind === "logs") {
    const target = count === 1 ? `log source \"${name}\"` : `${count} log sources`;
    return `Delete ${target}? Cached log data and sync history for ${count === 1 ? "this source" : "these sources"} will be removed from Datacoolie Studio. Original log files will not be deleted.`;
  }
  return count === 1 ? `Remove source \"${name}\"?` : `Remove ${count} sources?`;
}
