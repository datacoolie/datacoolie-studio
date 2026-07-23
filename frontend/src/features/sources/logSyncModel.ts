import type { LogSyncRequest } from "../../shared/api/types";

export type LogSyncDraft = {
  mode: LogSyncRequest["mode"];
  fromPartition: string;
  toPartition: string;
};

export type LogSyncActivity = "syncing" | "done" | "error";
export type LogSyncActivities = Record<number, LogSyncActivity>;

export const DEFAULT_LOG_SYNC_DRAFT: LogSyncDraft = {
  mode: "incremental",
  fromPartition: "",
  toPartition: ""
};

const ISO_PARTITION_DATE = /^\d{4}-\d{2}-\d{2}$/;

export function validateLogSyncDraft(draft: LogSyncDraft): string | null {
  if (draft.mode === "incremental") return null;
  if (!draft.fromPartition || !draft.toPartition) return "Choose both lookback dates.";
  if (!isIsoDate(draft.fromPartition) || !isIsoDate(draft.toPartition)) {
    return "Lookback dates must use YYYY-MM-DD.";
  }
  if (draft.fromPartition > draft.toPartition) return "From date cannot be after To date.";
  return null;
}

export function toLogSyncRequest(draft: LogSyncDraft): LogSyncRequest {
  const error = validateLogSyncDraft(draft);
  if (error) throw new Error(error);
  if (draft.mode === "incremental") return { mode: "incremental" };
  return {
    mode: "incremental_with_lookback",
    lookback: {
      from_partition: draft.fromPartition,
      to_partition: draft.toPartition
    }
  };
}

export function setLogSyncActivity(
  current: LogSyncActivities,
  sourceIds: number[],
  activity: LogSyncActivity
): LogSyncActivities {
  const next = { ...current };
  sourceIds.forEach((sourceId) => {
    next[sourceId] = activity;
  });
  return next;
}

export function clearLogSyncActivity(
  current: LogSyncActivities,
  sourceIds: number[]
): LogSyncActivities {
  const next = { ...current };
  sourceIds.forEach((sourceId) => delete next[sourceId]);
  return next;
}

function isIsoDate(value: string): boolean {
  if (!ISO_PARTITION_DATE.test(value)) return false;
  const parsed = new Date(`${value}T00:00:00Z`);
  return !Number.isNaN(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value;
}
