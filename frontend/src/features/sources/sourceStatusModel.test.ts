import { describe, expect, it } from "vitest";

import type { SourceValidationResult } from "../../shared/api/domainTypes";
import type { SourceSyncStatus } from "../../shared/api/contractTypes";
import {
  checkTooltip,
  sourceStatusDetailMessage,
  syncExecutionTooltip,
  validationStateFromResult,
} from "./sourceStatusModel";

describe("source status model", () => {
  it("normalizes status error messages without inventing fallback text", () => {
    expect(sourceStatusDetailMessage({ message: " access denied " })).toBe("access denied");
    expect(sourceStatusDetailMessage({ message: "  " })).toBeNull();
    expect(sourceStatusDetailMessage({ code: "storage_access_failed" })).toBeNull();
    expect(sourceStatusDetailMessage(null)).toBeNull();
  });

  it("derives validation state from the canonical validation result", () => {
    expect(validationStateFromResult(null)).toBe("not_validated");
    expect(validationStateFromResult({ status: "ok" } as SourceValidationResult)).toBe("ready");
    expect(validationStateFromResult({ status: "warning" } as SourceValidationResult)).toBe("warning");
    expect(validationStateFromResult({ status: "error" } as SourceValidationResult)).toBe("invalid");
  });

  it("builds check and sync details from their nested status objects", () => {
    const observation = {
      state: "changed",
      checked_at: "2026-09-16T10:00:00Z",
      next_check_at: "2026-09-16T10:15:00Z",
      pending_changes: true,
      failure_count: 1,
      error: { message: "access denied" },
    } as SourceSyncStatus["observation"];
    const execution = {
      state: "failed",
      trigger: "manual",
      completed_at: "2026-09-16T10:01:00Z",
      last_successful_at: "2026-09-16T09:00:00Z",
      summary: null,
      error: { message: "sync failed" },
    } as SourceSyncStatus["sync_execution"];

    expect(checkTooltip(observation, observation.checked_at, "UTC"))
      .toContain("Changes since last sync");
    expect(checkTooltip(observation, observation.checked_at, "UTC"))
      .toContain("Error: access denied");
    expect(syncExecutionTooltip(execution, execution.completed_at, "UTC"))
      .toContain("Error: sync failed");
    expect(syncExecutionTooltip(execution, execution.completed_at, "UTC"))
      .toContain("Last success");
  });
});
