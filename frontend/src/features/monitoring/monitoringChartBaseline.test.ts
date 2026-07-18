import { describe, expect, it } from "vitest";
import { DEFAULT_MONITORING_FILTERS, type MonitoringFilters } from "./monitoringFilters";
import {
  childFanoutDistributionOption,
  dataflowStatusTrendOption,
  failureTrendOption,
  jobStatusTrendOption,
  workloadVolumeTrendOption,
} from "./monitoringShared";

function expectBottomAnchored(option: unknown) {
  const grid = (option as { grid?: { bottom?: number; containLabel?: boolean } } | null)?.grid;
  expect(grid?.bottom).toBe(5);
  expect(grid?.containLabel).toBe(false);
}

describe("Monitoring chart bottom baseline", () => {
  const filters: MonitoringFilters = { ...DEFAULT_MONITORING_FILTERS, range: "custom", grain: "day", startTime: "2026-07-15", endTime: "2026-07-15" };
  const dateRange = { min: "2026-07-15", max: "2026-07-15" };
  const trendRows = [{ date: "2026-07-15", bucket: "2026-07-15", succeeded: 1, failed: 0, skipped: 0, running: 0, pending: 0 }];

  it("bottom-anchors shared job and dataflow status trends", () => {
    expectBottomAnchored(jobStatusTrendOption(trendRows, filters, dateRange, "UTC", "day"));
    expectBottomAnchored(dataflowStatusTrendOption(trendRows, filters, dateRange, "UTC", "day"));
  });

  it("bottom-anchors Overview input/output workload", () => {
    expectBottomAnchored(workloadVolumeTrendOption(
      [{ date: "2026-07-15", bucket: "2026-07-15", rows_read: 10, est_rows_written: 9 }],
      [{ date: "2026-07-15", bucket: "2026-07-15", bytes_added: 100, bytes_removed: 0 }],
      filters,
      dateRange,
      "UTC",
      "day",
    ));
  });

  it("can move the workload legend out of the chart canvas", () => {
    const option = workloadVolumeTrendOption(
      [{ date: "2026-07-15", bucket: "2026-07-15", rows_read: 10, est_rows_written: 9 }],
      [{ date: "2026-07-15", bucket: "2026-07-15", bytes_added: 100, bytes_removed: 0 }],
      filters,
      dateRange,
      "UTC",
      "day",
      false
    );
    if (!option) throw new Error("Expected workload volume trend option");
    expect(option.legend).toEqual({ show: false });
    expect((option.grid as { top?: number }).top).toBe(5);
  });

  it("bottom-anchors Failure trend", () => {
    expectBottomAnchored(failureTrendOption(
      [{ date: "2026-07-15", bucket: "2026-07-15", failed_jobs: 1, failed_dataflows: 2 }],
      filters,
      dateRange,
      "UTC",
      "day",
    ));
  });

  it("bottom-anchors Job fan-out when its horizontal zoom is not needed", () => {
    const option = childFanoutDistributionOption([
      { bin_label: "1", bin_start: 1, bin_end: 1, jobs: 2 },
    ]);
    expect((option.grid as { bottom?: number; containLabel?: boolean }).bottom).toBe(0);
    expect((option.grid as { bottom?: number; containLabel?: boolean }).containLabel).toBe(false);
  });
});
