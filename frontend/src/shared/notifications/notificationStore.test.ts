import { describe, expect, it } from "vitest";
import {
  addNotificationRecord,
  applyNotificationEvent,
  createNotificationRecord,
  isNotificationRecord,
  NOTIFICATION_MAX_RECORDS,
  NOTIFICATION_TTL_MS,
  parsePersistedNotifications,
  pruneNotifications,
  serializeNotifications,
  type NotificationRecord,
} from "./notificationStore";

const NOW = Date.parse("2026-08-21T10:00:00.000Z");

function record(id: string, overrides: Partial<NotificationRecord> = {}): NotificationRecord {
  return {
    id,
    tone: "success",
    title: "Completed",
    location: "Sources",
    message: "Operation completed.",
    occurredAt: NOW,
    expiresAt: NOW + NOTIFICATION_TTL_MS,
    readAt: null,
    ...overrides,
  };
}

describe("notificationStore", () => {
  it("creates a bounded, redacted record with a 24-hour expiry", () => {
    const created = createNotificationRecord({
      tone: "error",
      title: "Validation failed",
      location: "Sources",
      message: "client_secret=super-secret&sig=abc123",
      errors: ["Access denied"],
    }, NOW);

    expect(created.tone).toBe("error");
    expect(created.expiresAt).toBe(NOW + NOTIFICATION_TTL_MS);
    expect(created.message).toContain("client_secret=[redacted]");
    expect(created.message).toContain("sig=[redacted]");
    expect(created.message).toContain("Access denied");
  });

  it("prunes expired records and caps the retained list", () => {
    const records = Array.from({ length: NOTIFICATION_MAX_RECORDS + 2 }, (_, index) => record(String(index), {
      occurredAt: NOW + index,
      expiresAt: NOW + NOTIFICATION_TTL_MS + index,
    }));
    records.push(record("expired", { expiresAt: NOW - 1 }));

    const pruned = pruneNotifications(records, NOW);

    expect(pruned).toHaveLength(NOTIFICATION_MAX_RECORDS);
    expect(pruned.some((item) => item.id === "expired")).toBe(false);
    expect(pruned[0].id).toBe(String(NOTIFICATION_MAX_RECORDS + 1));
  });

  it("deduplicates records with the same short-lived key", () => {
    const first = record("first", { dedupeKey: "monitoring:1" });
    const second = record("second", { dedupeKey: "monitoring:1", occurredAt: NOW + 10_000, expiresAt: NOW + NOTIFICATION_TTL_MS + 10_000 });

    const result = addNotificationRecord([first], second, NOW + 10_000);

    expect(result.added).toBe(false);
    expect(result.records.map((item) => item.id)).toEqual(["first"]);
  });

  it("round-trips valid storage and ignores malformed payloads", () => {
    const serialized = serializeNotifications([record("one")], NOW);

    expect(parsePersistedNotifications(serialized, NOW)).toHaveLength(1);
    expect(parsePersistedNotifications("not-json", NOW)).toEqual([]);
    expect(parsePersistedNotifications(JSON.stringify({ version: 2, notifications: [record("one")] }), NOW)).toEqual([]);
  });

  it("applies read and clear events without losing other records", () => {
    const records = [record("one"), record("two")];
    const read = applyNotificationEvent(records, { type: "mark-read", sourceId: "tab-b", id: "one", readAt: NOW + 1 }, NOW);
    expect(read.find((item) => item.id === "one")?.readAt).toBe(NOW + 1);

    const cleared = applyNotificationEvent(read, { type: "clear-read", sourceId: "tab-b" }, NOW);
    expect(cleared.map((item) => item.id)).toEqual(["two"]);
  });

  it("deduplicates an upsert received from another tab", () => {
    const existing = record("first", { dedupeKey: "monitoring:1" });
    const incoming = record("second", { dedupeKey: "monitoring:1", occurredAt: NOW + 5_000, expiresAt: NOW + NOTIFICATION_TTL_MS + 5_000 });

    const next = applyNotificationEvent([existing], {
      type: "upsert",
      sourceId: "tab-b",
      record: incoming,
    }, NOW + 5_000);

    expect(next.map((item) => item.id)).toEqual(["first"]);
  });

  it("accepts only complete notification records at the channel boundary", () => {
    expect(isNotificationRecord({ type: "upsert" })).toBe(false);
    expect(isNotificationRecord(record("valid"))).toBe(true);
  });
});
