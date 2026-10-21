export const CANONICAL_MESSAGE_FIELDS = [
  "message",
] as const;

export const LEGACY_FAILURE_MESSAGE_FIELDS = [
  "error_message",
  "error_preview",
  "failure_message",
  "source_error_message",
  "transform_error_message",
  "destination_error_message",
  "error_messages",
] as const;

type CanonicalMessageField = (typeof CANONICAL_MESSAGE_FIELDS)[number];
type LegacyMessageField = (typeof LEGACY_FAILURE_MESSAGE_FIELDS)[number];

export interface MonitoringMessageState {
  value: string | null;
  source: "canonical" | "legacy" | null;
  field: CanonicalMessageField | LegacyMessageField | null;
  canonicalPresent: boolean;
}

/**
 * Resolve the text shown for a run while retaining the distinction between an
 * omitted canonical message and an explicitly supplied null or empty value.
 * The latter must not resurrect a stale derived error from an older projection.
 */
export function monitoringMessageState(row: Record<string, unknown>): MonitoringMessageState {
  if (isCanonicalFieldPresent(row, "message")) {
    return { value: messageText(row.message), source: "canonical", field: "message", canonicalPresent: true };
  }
  if (hasOwn(row, "status")) {
    const value = String(row.status ?? "").toLowerCase() === "failed" ? messageText(row.error_message) : null;
    return { value, source: value === null ? null : "legacy", field: value === null ? null : "error_message", canonicalPresent: false };
  }

  for (const field of LEGACY_FAILURE_MESSAGE_FIELDS) {
    if (!hasOwn(row, field)) continue;
    const value = messageText(row[field]);
    if (value !== null) {
      return { value, source: "legacy", field, canonicalPresent: false };
    }
  }

  return { value: null, source: null, field: null, canonicalPresent: false };
}

export function effectiveMonitoringMessage(row: Record<string, unknown>) {
  return monitoringMessageState(row).value;
}

function isCanonicalFieldPresent(row: Record<string, unknown>, field: CanonicalMessageField) {
  const presenceField = "message_present";
  if (hasOwn(row, presenceField)) return !isExplicitFalse(row[presenceField]);
  return hasOwn(row, field);
}

function isExplicitFalse(value: unknown) {
  return value === false || value === 0 || (typeof value === "string" && ["false", "0"].includes(value.trim().toLowerCase()));
}

function messageText(value: unknown) {
  if (value === null || value === undefined) return null;
  const text = typeof value === "string" ? value : String(value);
  return text === "" ? null : text;
}

function hasOwn(row: Record<string, unknown>, field: string): field is string {
  return Object.prototype.hasOwnProperty.call(row, field);
}
