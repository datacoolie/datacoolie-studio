import { describe, expect, it } from "vitest";
import { maintenancePageTestUtils } from "./pages/MaintenancePage";

const report = {
  summary: {
    timezone: "UTC",
    effective_grain: "day",
    date_range: { min: "2026-07-20", max: "2026-07-22" },
  },
  maintenance: {
    status_by_date: [{
      bucket: "2026-07-21T00:00:00Z",
      grain: "day",
      succeeded: 2,
      failed: 1,
      skipped: 0,
      running: 0,
      pending: 0,
      unknown: 0,
      total: 3,
      success_rate: 66.67,
    }],
    reclaim_by_date: [{
      bucket: "2026-07-21T00:00:00Z",
      grain: "day",
      bytes_reclaimed: 4096,
      bytes_saved: 1024,
      files_removed: 2,
      runs: 1,
    }],
  },
} as any;

const filters = {
  range: "custom",
  grain: "day",
  startTime: "2026-07-20T00:00:00Z",
  endTime: "2026-07-22T23:59:59Z",
} as any;

describe("maintenance trends", () => {
  it("joins timestamp status buckets to normalized visible dates", () => {
    const option = maintenancePageTestUtils.maintenanceStatusTrendOption(report, filters, "UTC") as any;
    expect(option.xAxis.data).toEqual(["2026-07-20", "2026-07-21", "2026-07-22"]);
    expect(option.series[0].data).toEqual([0, 2, 0]);
    expect(option.series[1].data).toEqual([0, 1, 0]);
  });

  it("joins timestamp reclaim buckets without replacing evidence with zero", () => {
    const option = maintenancePageTestUtils.maintenanceReclaimTrendOption(report, filters, "UTC") as any;
    expect(option.series[0].data).toEqual([0, 4096, 0]);
    expect(option.series[1].data).toEqual([0, 2, 0]);
  });
});
