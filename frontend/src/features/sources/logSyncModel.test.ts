import { describe, expect, it } from "vitest";
import {
  clearLogSyncActivity,
  DEFAULT_LOG_SYNC_DRAFT,
  setLogSyncActivity,
  toLogSyncRequest,
  validateLogSyncDraft
} from "./logSyncModel";

describe("log sync request", () => {
  it("uses incremental mode by default", () => {
    expect(toLogSyncRequest(DEFAULT_LOG_SYNC_DRAFT)).toEqual({ mode: "incremental" });
  });

  it("tracks background sync phases independently for each source", () => {
    const syncing = setLogSyncActivity({}, [3, 5], "syncing");
    expect(syncing).toEqual({ 3: "syncing", 5: "syncing" });

    const completed = setLogSyncActivity(syncing, [3], "done");
    expect(completed).toEqual({ 3: "done", 5: "syncing" });
    expect(clearLogSyncActivity(completed, [3])).toEqual({ 5: "syncing" });
  });

  it("builds a normalized lookback payload", () => {
    expect(toLogSyncRequest({
      mode: "incremental_with_lookback",
      fromPartition: "2026-07-01",
      toPartition: "2026-07-21"
    })).toEqual({
      mode: "incremental_with_lookback",
      lookback: { from_partition: "2026-07-01", to_partition: "2026-07-21" }
    });
  });

  it("requires both lookback dates", () => {
    expect(validateLogSyncDraft({
      mode: "incremental_with_lookback",
      fromPartition: "2026-07-01",
      toPartition: ""
    })).toBe("Choose both lookback dates.");
  });

  it("rejects invalid and reversed lookback ranges", () => {
    expect(validateLogSyncDraft({
      mode: "incremental_with_lookback",
      fromPartition: "2026-02-30",
      toPartition: "2026-03-01"
    })).toBe("Lookback dates must use YYYY-MM-DD.");
    expect(validateLogSyncDraft({
      mode: "incremental_with_lookback",
      fromPartition: "2026-07-22",
      toPartition: "2026-07-01"
    })).toBe("From date cannot be after To date.");
  });
});
