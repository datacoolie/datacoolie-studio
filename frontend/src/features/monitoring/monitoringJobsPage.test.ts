import { describe, expect, it } from "vitest";
import { childFanoutDistributionOption, jobWorkloadEfficiencyOption } from "./monitoringShared";

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
});
