import { describe, expect, it } from "vitest";
import { toEnvironmentOverview } from "./environmentAdapters";
import type { EnvironmentOverviewResponseTransport } from "./contractTypes";

describe("environment overview transport adapter", () => {
  it("normalizes map-shaped monitoring windows for the overview UI", () => {
    const response = toEnvironmentOverview({
      schema_version: "environment-overview.v2",
      sources: {
        metadata: { configured: 1, enabled: 1 },
        logs: { configured: 1, enabled: 1 },
        validation: { errors: 0, warnings: 0 },
      },
      metadata: {
        connections: 1,
        enabled_connections: 1,
        dataflows: 2,
        enabled_dataflows: 2,
        schema_hints: 1,
        enabled_schema_hints: 1,
        stages: [{ name: "silver", count: 2 }],
        load_types: [],
        errors: [],
      },
      lineage: { assets: 2, references: 1, dataflows: 2, dependencies: 1 },
      monitoring: {
        job_records: 2,
        total_failures: 1,
        dataflow_success_rate: 0.5,
        failed_job_windows: { last7: 1 },
        active_engines: 1,
        date_range: {},
        errors: [],
      },
      cache: { state: "hit", computed_at: "2026-09-16T00:00:00Z" },
    } as unknown as EnvironmentOverviewResponseTransport);

    expect(response.monitoring.failed_job_windows).toEqual({ last7: "1", last30: "0", last365: "0" });
    expect(response.lineage).toMatchObject({ assets: 2, references: 1, error_count: 0 });
    expect(response.metadata.stages).toEqual([{ name: "silver", count: 2 }]);
  });

  it("keeps shared monitoring overview counts as decimal strings", () => {
    const response = toEnvironmentOverview({
      schema_version: "environment-overview.v2",
      sources: { metadata: { configured: 0, enabled: 0 }, logs: { configured: 0, enabled: 0 }, validation: { errors: 0, warnings: 0 } },
      metadata: { connections: 0, enabled_connections: 0, dataflows: 0, enabled_dataflows: 0, schema_hints: 0, enabled_schema_hints: 0, stages: [], load_types: [], errors: [] },
      lineage: {},
      monitoring: {
        job_records: "9007199254740993",
        total_failures: "2",
        dataflow_success_rate: 100,
        failed_job_windows: { last7: "9007199254740993" },
        active_engines: "3",
        date_range: {},
        errors: [],
      },
      cache: { state: "hit", computed_at: "2026-09-16T00:00:00Z" },
    } as unknown as EnvironmentOverviewResponseTransport);
    expect(response.monitoring.job_records).toBe("9007199254740993");
    expect(response.monitoring.failed_job_windows.last7).toBe("9007199254740993");
  });
});
