import { describe, expect, it } from "vitest";
import {
  childFanoutDistributionOption,
  durationDistributionBoxOption,
  lifecycleStatusItems,
  jobWorkloadEfficiencyOption,
  statusColor
} from "./monitoringShared";

describe("Jobs page chart layout", () => {
  it("anchors workload efficiency to the bottom of its chart area", () => {
    const option = jobWorkloadEfficiencyOption([
      { job_id: "job-1", operation_type: "etl", child_dataflow_count: 2, duration_seconds: 12, workload_size: 100 }
    ]);

    expect((option.grid as { bottom?: number }).bottom).toBe(0);
  });

  it("anchors fan-out to the bottom when no zoom control is needed", () => {
    const option = childFanoutDistributionOption([
      { bin_label: "1-2", bin_start: 1, bin_end: 2, jobs: 3, succeeded: 3, failed: 0, skipped: 0, running: 0, pending: 0 }
    ]);

    expect((option.grid as { bottom?: number; top?: number; containLabel?: boolean }).bottom).toBe(0);
    expect((option.grid as { bottom?: number; top?: number; containLabel?: boolean }).containLabel).toBe(false);
    expect((option.grid as { bottom?: number; top?: number }).top).toBeGreaterThanOrEqual(16);
    expect((option.yAxis as { max?: unknown }).max).toBeUndefined();
  });

  it("uses the canonical trend colors for lifecycle KPI values", () => {
    expect(lifecycleStatusItems(7, 3, 5)).toEqual([
      { status: "skipped", value: 7, color: statusColor("skipped") },
      { status: "running", value: 3, color: statusColor("running") },
      { status: "pending", value: 5, color: statusColor("pending") }
    ]);
  });

  it("shows runtime context without job identity or operation in duration outlier tooltips", () => {
    const option = durationDistributionBoxOption([
      {
        operation_type: "etl, maintenance",
        count: 5,
        outliers: [[100, "job-1", "job-1", "failed", "etl, maintenance", "duckdb", "file", "local"]]
      }
    ], "operation_type", "job");
    const outlierSeries = (option.series as Array<{ name?: string; tooltip?: { formatter?: (params: unknown) => string }; data?: unknown[] }>)
      .find((series) => series.name === "Outliers");
    const tooltip = outlierSeries?.tooltip?.formatter?.({ data: outlierSeries.data?.[0] }) ?? "";

    expect(tooltip).toContain("Runtime: duckdb / file / local");
    expect(tooltip).not.toContain("Job ID:");
    expect(tooltip).not.toContain("Operation:");
    expect(tooltip).not.toContain("Job:");
    expect(tooltip).not.toContain("etl, maintenance");
  });
});
