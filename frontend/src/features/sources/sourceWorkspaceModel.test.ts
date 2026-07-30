import { describe, expect, it } from "vitest";
import type { SourcePath, SourceSyncStatus } from "../../shared/api/domainTypes";
import { sourceKey } from "../../shared/lib/sources";
import {
  aggregateDeleteImpacts,
  beginSourceOperations,
  finishSourceOperations,
  hasRunningSourceSync,
  isSourceSyncRunning,
  logRefreshInterval,
  logScheduleLabel,
  sourceDeletionWarning,
  sourceDisplayName,
  sourceDisplayPath,
  sourceSyncStatusPollInterval,
  sourceOperationFor,
  shouldStartLocalObservation,
  SOURCE_IDLE_STATUS_POLL_MIN_MS,
  SOURCE_SYNC_STATUS_POLL_INTERVAL_MS,
  summarizeSourceHealth,
  type SourceWorkspaceEntry
} from "./sourceWorkspaceModel";

function source(id: number, overrides: Partial<SourcePath> = {}): SourcePath {
  return {
    id,
    environment_id: 2,
    uri: `D:\\workspace\\source-${id}.json`,
    label: null,
    enabled: true,
    source_config: {},
    latest_validation: null,
    ...overrides
  } as SourcePath;
}

describe("source workspace model", () => {
  it("coalesces duplicate Local foreground observations", () => {
    expect(shouldStartLocalObservation("hidden", undefined, 1_000)).toBe(false);
    expect(shouldStartLocalObservation("visible", undefined, 1_000)).toBe(true);
    expect(shouldStartLocalObservation(
      "visible",
      { inFlight: true, lastStartedAt: 1_000 },
      5_000,
    )).toBe(false);
    expect(shouldStartLocalObservation(
      "visible",
      { inFlight: false, lastStartedAt: 1_000 },
      1_749,
    )).toBe(false);
    expect(shouldStartLocalObservation(
      "visible",
      { inFlight: false, lastStartedAt: 1_000 },
      10_999,
    )).toBe(false);
    expect(shouldStartLocalObservation(
      "visible",
      { inFlight: false, lastStartedAt: 1_000 },
      11_000,
    )).toBe(true);
  });

  it("recognizes a persisted running job even when the last revision is still healthy", () => {
    const status = {
      source_id: 1,
      source_kind: "metadata",
      status: "ok",
      message: "Source revision recorded",
      latest_job: { status: "running" }
    } as SourceSyncStatus;

    expect(isSourceSyncRunning(status)).toBe(true);
    expect(hasRunningSourceSync({ [sourceKey("metadata", 1)]: status })).toBe(true);
    expect(sourceSyncStatusPollInterval({ [sourceKey("metadata", 1)]: status }))
      .toBe(SOURCE_SYNC_STATUS_POLL_INTERVAL_MS);
    expect(isSourceSyncRunning({ ...status, latest_job: { ...status.latest_job!, status: "succeeded" } })).toBe(false);
    expect(sourceSyncStatusPollInterval({
      [sourceKey("metadata", 1)]: {
        ...status,
        latest_job: { ...status.latest_job!, status: "succeeded" }
      }
    })).toBe(false);
  });

  it("polls persisted initialization phases even before a sync job starts", () => {
    const status = {
      source_id: 1,
      source_kind: "metadata",
      status: "running",
      message: "Waiting to validate source",
      active_operation: "validate",
      latest_job: { status: "queued" }
    } as SourceSyncStatus;

    expect(isSourceSyncRunning(status)).toBe(true);
    expect(sourceSyncStatusPollInterval({ [sourceKey("metadata", 1)]: status }))
      .toBe(SOURCE_SYNC_STATUS_POLL_INTERVAL_MS);
  });

  it("uses persisted next due time for idle DB-only status polling", () => {
    const status = {
      source_id: 1,
      source_kind: "metadata",
      status: "ok",
      message: "Observed",
      next_check_at: "2026-07-28T10:01:00Z",
      latest_job: null,
    } as SourceSyncStatus;

    expect(sourceSyncStatusPollInterval(
      { [sourceKey("metadata", 1)]: status },
      Date.parse("2026-07-28T10:00:00Z"),
    )).toBe(62_000);
    expect(sourceSyncStatusPollInterval(
      {
        [sourceKey("metadata", 1)]: {
          ...status,
          next_check_at: "2026-07-28T10:00:01Z",
        },
      },
      Date.parse("2026-07-28T10:00:00Z"),
    )).toBe(SOURCE_IDLE_STATUS_POLL_MIN_MS);
  });

  it("does not poll a hard-paused source with no next check", () => {
    const status = {
      source_id: 1,
      source_kind: "metadata",
      status: "error",
      message: "Source checks paused",
      next_check_at: null,
      observation_state: "paused",
      observation_failure_count: 3,
      latest_job: null,
    } as SourceSyncStatus;

    expect(sourceSyncStatusPollInterval({
      [sourceKey("metadata", 1)]: status,
    })).toBe(false);
  });

  it("tracks source operations by environment and preserves unrelated work", () => {
    const running = beginSourceOperations(
      {},
      7,
      [{ kind: "metadata", id: 1 }, { kind: "logs", id: 2 }],
      "validate",
      "2026-07-28T10:00:00Z",
    );
    const anotherEnvironment = beginSourceOperations(
      running,
      8,
      [{ kind: "metadata", id: 1 }],
      "sync",
      "2026-07-28T10:01:00Z",
    );

    expect(sourceOperationFor(anotherEnvironment, 7, "metadata", 1)?.action)
      .toBe("validate");
    expect(sourceOperationFor(anotherEnvironment, 8, "metadata", 1)?.action)
      .toBe("sync");

    const remaining = finishSourceOperations(
      anotherEnvironment,
      7,
      [{ kind: "metadata", id: 1 }],
    );
    expect(sourceOperationFor(remaining, 7, "metadata", 1)).toBeUndefined();
    expect(sourceOperationFor(remaining, 7, "logs", 2)?.action).toBe("validate");
    expect(sourceOperationFor(remaining, 8, "metadata", 1)?.action).toBe("sync");
  });

  it("summarizes enabled, readable, and current health independently", () => {
    const entries: SourceWorkspaceEntry[] = [
      { kind: "metadata", source: source(1, { latest_validation: { status: "ok" } as SourcePath["latest_validation"] }) },
      { kind: "code", source: source(2, { enabled: false, latest_validation: { status: "warning" } as SourcePath["latest_validation"] }) },
      { kind: "logs", source: source(3, { latest_validation: { status: "error" } as SourcePath["latest_validation"] }) }
    ];
    const syncStatuses = {
      [sourceKey("metadata", 1)]: { status: "ok" },
      [sourceKey("code", 2)]: { status: "error" },
      [sourceKey("logs", 3)]: { status: "ok" }
    } as Record<string, SourceSyncStatus>;

    expect(summarizeSourceHealth(entries, syncStatuses)).toEqual({ enabled: 2, readable: 2, current: 2 });
  });

  it("prefers the label and otherwise uses the final path segment", () => {
    expect(sourceDisplayName(source(1, { label: "Orders metadata" }))).toBe("Orders metadata");
    expect(sourceDisplayName(source(2, { uri: "D:\\workspace\\metadata\\dataflows.json" }))).toBe("dataflows.json");
  });

  it("shows the user-entered path and falls back to the canonical source path", () => {
    expect(sourceDisplayPath(source(1, {
      uri: "abfs://test@account.dfs.core.windows.net/metadata/assets.json",
      configured_location: {
        registration_id: 7,
        purpose: "metadata",
        input_uri: "https://account.dfs.core.windows.net/test/metadata/assets.json",
        canonical_uri: "abfs://test@account.dfs.core.windows.net/metadata/assets.json",
        input_locations: {},
        canonical_locations: {}
      }
    }), "metadata")).toBe("https://account.dfs.core.windows.net/test/metadata/assets.json");
    expect(sourceDisplayPath(source(2, { uri: "s3://bucket/metadata.json" }), "metadata"))
      .toBe("s3://bucket/metadata.json");
  });

  it("rebases project sources onto the user-entered metadata and code roots", () => {
    const configured_location = {
      registration_id: 34,
      purpose: "project" as const,
      input_uri: "abfss://test@datateamtest01.dfs.core.windows.net/",
      canonical_uri: "abfs://test@datateamtest01.dfs.core.windows.net",
      input_locations: {
        metadata_uri: "abfss://test@datateamtest01.dfs.core.windows.net/metadata",
        code_uri: "abfss://test@datateamtest01.dfs.core.windows.net/functions"
      },
      canonical_locations: {
        metadata_uri: "abfs://test@datateamtest01.dfs.core.windows.net/metadata",
        code_uri: "abfs://test@datateamtest01.dfs.core.windows.net/functions"
      }
    };
    expect(sourceDisplayPath(source(3, {
      uri: "abfs://test@datateamtest01.dfs.core.windows.net/metadata/aws_use_cases.json",
      configured_location
    }), "metadata")).toBe(
      "abfss://test@datateamtest01.dfs.core.windows.net/metadata/aws_use_cases.json"
    );
    expect(sourceDisplayPath(source(4, {
      uri: "abfs://test@datateamtest01.dfs.core.windows.net/functions",
      configured_location
    }), "code")).toBe(
      "abfss://test@datateamtest01.dfs.core.windows.net/functions"
    );
  });

  it("warns that deleting log configuration removes cache but not original files", () => {
    expect(sourceDeletionWarning("logs", "Runtime logs")).toContain("Cached log data and sync history");
    expect(sourceDeletionWarning("logs", "Runtime logs")).toContain("Original log files will not be deleted");
    expect(sourceDeletionWarning("logs", "Runtime logs", 3)).toContain("3 log sources");
  });

  it("uses a one-minute default while keeping auto refresh off", () => {
    const item = source(4, { sync_schedule_enabled: false, sync_interval_minutes: null });
    expect(logRefreshInterval(item)).toBe(1);
    expect(logScheduleLabel(item)).toBe("Off");
    expect(logScheduleLabel({ ...item, sync_schedule_enabled: true }, Date.UTC(2026, 0, 1))).toBe("Starts within 1 min");
  });

  it("aggregates delete-impact counts for bulk confirmation", () => {
    const base = {
      source_kind: "logs",
      source_uri: "logs",
      mode: "hard_delete",
      metadata_file_deleted: false,
      has_impact: true,
      summary: "impact"
    } as const;
    const combined = aggregateDeleteImpacts([
      { ...base, source_id: 1, impacts: [{ kind: "manifest", label: "indexed log files", count: 2, severity: "warning" }] },
      { ...base, source_id: 2, impacts: [{ kind: "manifest", label: "indexed log files", count: 3, severity: "warning" }] }
    ]);
    expect(combined).toEqual([{ kind: "manifest", label: "indexed log files", count: 5, severity: "warning" }]);
  });
});
