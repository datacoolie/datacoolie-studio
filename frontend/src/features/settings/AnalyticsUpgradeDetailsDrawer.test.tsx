import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import {
  AnalyticsUpgradeDetailsDrawer,
  completedSourceCount,
  formatDuration,
} from "./AnalyticsUpgradeDetailsDrawer";

describe("AnalyticsUpgradeDetailsDrawer", () => {
  it("renders attempt and per-source timing details", () => {
    const html = renderToStaticMarkup(
      <AnalyticsUpgradeDetailsDrawer
        onClose={vi.fn()}
        upgrade={{
          state: "building",
          source_schema_version: 2,
          target_schema_version: 3,
          source_ids: [7, 8],
          completed_source_ids: [],
          attempt_count: 2,
          started_at: "2026-08-11T03:00:00Z",
          duration_seconds: 75,
          source_progress: [
            {
              source_id: 7,
              label: "Production logs",
              status: "succeeded",
              started_at: "2026-08-11T03:00:00Z",
              completed_at: "2026-08-11T03:00:42Z",
              duration_seconds: 42,
              message: "Analytics rows rebuilt.",
            },
            { source_id: 8, label: "Archive", status: "pending" },
          ],
        }}
      />,
    );

    expect(html).toContain("Upgrade details");
    expect(html).toContain("1 / 2 sources");
    expect(html).toContain("1m 15s");
    expect(html).toContain("Production logs");
    expect(html).toContain("42s");
    expect(html).toContain("Analytics rows rebuilt.");
  });

  it("labels same-version source repair separately from schema upgrade", () => {
    const html = renderToStaticMarkup(
      <AnalyticsUpgradeDetailsDrawer
        onClose={vi.fn()}
        upgrade={{
          state: "repairing",
          operation_kind: "source_repair",
          reason_code: "missing_source_coverage",
          source_schema_version: 10,
          target_schema_version: 10,
          source_ids: [118],
          episode_attempt_count: 1,
          attempt_count: 70,
          source_progress: [{ source_id: 118, status: "running" }],
        }}
      />,
    );

    expect(html).toContain("Repair source cache");
    expect(html).toContain("Schema v10");
    expect(html).toContain("Source cache missing");
    expect(html).toContain("1 (total 70)");
    expect(html).not.toContain("v10 → v10");
  });

  it("keeps partial completion visibly distinct from a successful upgrade", () => {
    const html = renderToStaticMarkup(
      <AnalyticsUpgradeDetailsDrawer
        onClose={vi.fn()}
        upgrade={{
          state: "completed_with_warnings",
          operation_kind: "schema_upgrade",
          reason_code: "schema_mismatch",
          source_schema_version: 9,
          target_schema_version: 10,
          source_ids: [7, 8],
          completed_source_ids: [7],
          error_code: "source_replay_partial",
          error_message: "1 Log source could not be published.",
        }}
      />,
    );

    expect(html).toContain("Current with warnings");
    expect(html).toContain("analytics-upgrade-message is-warning");
    expect(html).not.toContain("analytics-upgrade-message is-error");
  });

  it("counts completed source jobs when the upgrade checkpoint is still stale", () => {
    expect(completedSourceCount({
      state: "building",
      completed_source_ids: [],
      source_progress: [
        { source_id: 7, status: "succeeded" },
        { source_id: 8, status: "pending" },
      ],
    })).toBe(1);
  });

  it("formats long and unavailable durations", () => {
    expect(formatDuration(3661)).toBe("1h 1m 1s");
    expect(formatDuration(null)).toBe("—");
  });
});
