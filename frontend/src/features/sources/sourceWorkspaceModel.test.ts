import { describe, expect, it } from "vitest";
import type { SourcePath, SourceSyncStatus } from "../../shared/api/types";
import { sourceKey } from "../../shared/lib/sources";
import {
  aggregateDeleteImpacts,
  logRefreshInterval,
  logScheduleLabel,
  sourceDeletionWarning,
  sourceDisplayName,
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
