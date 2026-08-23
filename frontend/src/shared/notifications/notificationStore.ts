export type NotificationTone = "success" | "warning" | "error";

export const NOTIFICATION_STORAGE_KEY = "datacoolie-studio.notifications.v1";
export const NOTIFICATION_CHANNEL_NAME = "datacoolie-studio.notifications";
export const NOTIFICATION_TTL_MS = 24 * 60 * 60 * 1_000;
export const NOTIFICATION_MAX_RECORDS = 100;
export const NOTIFICATION_DEDUPE_WINDOW_MS = 60 * 1_000;
const MAX_TITLE_LENGTH = 240;
const MAX_LOCATION_LENGTH = 160;
const MAX_MESSAGE_LENGTH = 2_000;

export interface NotificationRecord {
  id: string;
  tone: NotificationTone;
  title: string;
  location: string;
  message: string;
  occurredAt: number;
  expiresAt: number;
  readAt: number | null;
  dedupeKey?: string;
}

export interface NotificationInput {
  tone: NotificationTone;
  title: string;
  location: string;
  message: string;
  errors?: string[];
  occurredAt?: number;
  dedupeKey?: string;
}

export type NotificationChannelEvent =
  | { type: "upsert"; sourceId: string; record: NotificationRecord }
  | { type: "mark-read"; sourceId: string; id: string; readAt: number }
  | { type: "mark-all-read"; sourceId: string; readAt: number }
  | { type: "clear-read"; sourceId: string }
  | { type: "clear-all"; sourceId: string };

export interface PersistedNotificationState {
  version: 1;
  notifications: NotificationRecord[];
}

export function createNotificationRecord(input: NotificationInput, now = Date.now()): NotificationRecord {
  const occurredAt = finiteTimestamp(input.occurredAt) ? Number(input.occurredAt) : now;
  const message = sanitizeNotificationText(
    [input.message, ...(input.errors ?? [])]
      .map((value) => sanitizeNotificationText(value, MAX_MESSAGE_LENGTH))
      .filter(Boolean)
      .join("\n"),
    MAX_MESSAGE_LENGTH,
  );

  return {
    id: makeNotificationId(),
    tone: input.tone,
    title: sanitizeNotificationText(input.title, MAX_TITLE_LENGTH) || "Studio notification",
    location: sanitizeNotificationText(input.location, MAX_LOCATION_LENGTH) || "Studio",
    message: message || "No additional message.",
    occurredAt,
    expiresAt: occurredAt + NOTIFICATION_TTL_MS,
    readAt: null,
    ...(input.dedupeKey ? { dedupeKey: sanitizeNotificationText(input.dedupeKey, MAX_LOCATION_LENGTH) } : {}),
  };
}

export function pruneNotifications(records: NotificationRecord[], now = Date.now()): NotificationRecord[] {
  return records
    .filter((record) => isNotificationRecord(record) && record.expiresAt > now)
    .sort((left, right) => right.occurredAt - left.occurredAt)
    .slice(0, NOTIFICATION_MAX_RECORDS);
}

export function addNotificationRecord(
  records: NotificationRecord[],
  record: NotificationRecord,
  now = Date.now(),
): { records: NotificationRecord[]; added: boolean } {
  const current = pruneNotifications(records, now);
  if (record.dedupeKey) {
    const duplicate = current.find(
      (item) => item.dedupeKey === record.dedupeKey
        && Math.abs(item.occurredAt - record.occurredAt) <= NOTIFICATION_DEDUPE_WINDOW_MS,
    );
    if (duplicate) return { records: current, added: false };
  }

  return {
    records: pruneNotifications([record, ...current], now),
    added: true,
  };
}

export function mergeNotificationRecords(
  left: NotificationRecord[],
  right: NotificationRecord[],
  now = Date.now(),
): NotificationRecord[] {
  const merged = new Map<string, NotificationRecord>();
  [...left, ...right].forEach((record) => {
    if (!isNotificationRecord(record)) return;
    const existing = merged.get(record.id);
    if (!existing) {
      merged.set(record.id, record);
      return;
    }
    merged.set(record.id, {
      ...existing,
      ...record,
      readAt: maxNullableTimestamp(existing.readAt, record.readAt),
    });
  });
  return pruneNotifications(Array.from(merged.values()), now);
}

export function applyNotificationEvent(
  records: NotificationRecord[],
  event: NotificationChannelEvent,
  now = Date.now(),
): NotificationRecord[] {
  const current = pruneNotifications(records, now);
  if (event.type === "upsert") {
    if (current.some((record) => record.id === event.record.id)) {
      return mergeNotificationRecords(current, [event.record], now);
    }
    return addNotificationRecord(current, event.record, now).records;
  }
  if (event.type === "mark-read") {
    return pruneNotifications(
      current.map((record) => record.id === event.id ? { ...record, readAt: event.readAt } : record),
      now,
    );
  }
  if (event.type === "mark-all-read") {
    return pruneNotifications(current.map((record) => ({ ...record, readAt: event.readAt })), now);
  }
  if (event.type === "clear-read") {
    return pruneNotifications(current.filter((record) => record.readAt === null), now);
  }
  return [];
}

export function parsePersistedNotifications(raw: string | null, now = Date.now()): NotificationRecord[] {
  if (!raw) return [];
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!isPersistedNotificationState(parsed)) return [];
    return pruneNotifications(parsed.notifications.filter(isNotificationRecord), now);
  } catch {
    return [];
  }
}

export function serializeNotifications(
  records: NotificationRecord[],
  now = Date.now(),
): string {
  const state: PersistedNotificationState = {
    version: 1,
    notifications: pruneNotifications(records, now),
  };
  return JSON.stringify(state);
}

export function unreadNotificationCount(records: NotificationRecord[], now = Date.now()): number {
  return pruneNotifications(records, now).filter((record) => record.readAt === null).length;
}

export function notificationListsEqual(left: NotificationRecord[], right: NotificationRecord[]): boolean {
  if (left.length !== right.length) return false;
  return left.every((record, index) => {
    const other = right[index];
    return record.id === other.id
      && record.tone === other.tone
      && record.title === other.title
      && record.location === other.location
      && record.message === other.message
      && record.occurredAt === other.occurredAt
      && record.expiresAt === other.expiresAt
      && record.readAt === other.readAt
      && record.dedupeKey === other.dedupeKey;
  });
}

function isPersistedNotificationState(value: unknown): value is PersistedNotificationState {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Partial<PersistedNotificationState>;
  return candidate.version === 1
    && Array.isArray(candidate.notifications);
}

export function isNotificationRecord(value: unknown): value is NotificationRecord {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Partial<NotificationRecord>;
  return typeof candidate.id === "string"
    && (candidate.tone === "success" || candidate.tone === "warning" || candidate.tone === "error")
    && typeof candidate.title === "string"
    && typeof candidate.location === "string"
    && typeof candidate.message === "string"
    && finiteTimestamp(candidate.occurredAt)
    && finiteTimestamp(candidate.expiresAt)
    && (candidate.readAt === null || finiteTimestamp(candidate.readAt))
    && (candidate.dedupeKey === undefined || typeof candidate.dedupeKey === "string");
}

function finiteTimestamp(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function maxNullableTimestamp(left: number | null, right: number | null): number | null {
  if (left === null) return right;
  if (right === null) return left;
  return Math.max(left, right);
}

function sanitizeNotificationText(value: unknown, maxLength: number): string {
  const text = String(value ?? "")
    .replace(/((?:access[-_ ]?token|refresh[-_ ]?token|session[-_ ]?token|client[-_ ]?secret|password|sas[-_ ]?token|account[-_ ]?key|shared[-_ ]?access[-_ ]?signature)\s*[:=]\s*)[^\s,;&]+/giu, "$1[redacted]")
    .replace(/((?:["']?)(?:access[-_ ]?token|refresh[-_ ]?token|session[-_ ]?token|client[-_ ]?secret|password|sas[-_ ]?token|account[-_ ]?key|shared[-_ ]?access[-_ ]?signature)(?:["']?\s*:\s*["']?))[^"',}\s]+["']?/giu, "$1[redacted]")
    .replace(/([?&](?:sig|token|signature)=)[^&\s]+/giu, "$1[redacted]")
    .replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/gu, "")
    .trim();
  return text.length > maxLength ? `${text.slice(0, maxLength - 1)}…` : text;
}

function makeNotificationId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}
