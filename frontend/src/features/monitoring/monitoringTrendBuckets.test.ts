import { describe, expect, it } from "vitest";
import { DEFAULT_MONITORING_FILTERS } from "./monitoringFilters";
import { monitoringTrendBucketTestUtils } from "./MonitoringPages";

describe("monitoring trend buckets", () => {
  it("fills every hour for a 3 day range when hour grain is selected", () => {
    const keys = monitoringTrendBucketTestUtils.resolveTrendBucketKeys(
      { ...DEFAULT_MONITORING_FILTERS, range: "3d", grain: "hour" },
      { min: null, max: null },
      "Asia/Saigon",
      [],
      "hour"
    );

    expect(keys).toHaveLength(3 * 24);
    expect(keys.every((key) => /\d{4}-\d{2}-\d{2} \d{2}:00/.test(key))).toBe(true);
  });

  it("fills 24 hourly buckets for the rolling 24h range", () => {
    const keys = monitoringTrendBucketTestUtils.resolveTrendBucketKeys(
      { ...DEFAULT_MONITORING_FILTERS, range: "24h", grain: "hour" },
      { min: null, max: null },
      "Asia/Saigon",
      [],
      "hour"
    );

    expect(keys).toHaveLength(24);
    expect(new Set(keys).size).toBe(24);
    expect(keys.every((key) => /\d{4}-\d{2}-\d{2} \d{2}:00/.test(key))).toBe(true);
  });

  it("uses hourly buckets for auto grain up to 3 days", () => {
    const keys = monitoringTrendBucketTestUtils.resolveTrendBucketKeys(
      { ...DEFAULT_MONITORING_FILTERS, range: "3d", grain: "auto" },
      { min: null, max: null },
      "Asia/Saigon",
      [],
      "auto"
    );

    expect(keys).toHaveLength(3 * 24);
    expect(keys.every((key) => /\d{4}-\d{2}-\d{2} \d{2}:00/.test(key))).toBe(true);
  });

  it("uses daily buckets for auto grain above 3 days", () => {
    const keys = monitoringTrendBucketTestUtils.resolveTrendBucketKeys(
      { ...DEFAULT_MONITORING_FILTERS, range: "7d", grain: "auto" },
      { min: null, max: null },
      "Asia/Saigon",
      [],
      "auto"
    );

    expect(keys).toHaveLength(7);
    expect(keys.every((key) => /^\d{4}-\d{2}-\d{2}$/.test(key))).toBe(true);
  });

  it("uses daily buckets for auto grain up to 90 days", () => {
    const keys = monitoringTrendBucketTestUtils.resolveTrendBucketKeys(
      { ...DEFAULT_MONITORING_FILTERS, range: "90d", grain: "auto" },
      { min: null, max: null },
      "Asia/Saigon",
      [],
      "auto"
    );

    expect(keys).toHaveLength(90);
    expect(keys.every((key) => /^\d{4}-\d{2}-\d{2}$/.test(key))).toBe(true);
  });

  it("keeps backend weekly grain for all time instead of forcing month", () => {
    const keys = monitoringTrendBucketTestUtils.resolveTrendBucketKeys(
      { ...DEFAULT_MONITORING_FILTERS, range: "all", grain: "week" },
      { min: "2026-03-01", max: "2026-06-18" },
      "Asia/Ho_Chi_Minh",
      ["2026-W09", "2026-W10", "2026-W11"],
      "week"
    );

    expect(keys.length).toBeGreaterThan(3);
    expect(keys[0]).toBe("2026-W09");
    expect(keys.every((key) => /^\d{4}-W\d{2}$/.test(key))).toBe(true);
  });

  it("fills missing failure trend buckets with zero values", () => {
    const rows = monitoringTrendBucketTestUtils.fillMissingFailureTrendDates(
      [
        { date: "2026-06-20", bucket: "2026-06-20", grain: "day", failed_jobs: 1, failed_dataflows: 3 }
      ],
      { ...DEFAULT_MONITORING_FILTERS, range: "custom", grain: "day", startTime: "2026-06-16", endTime: "2026-06-22" },
      { min: "2026-06-16", max: "2026-06-22" },
      "Asia/Saigon",
      "day"
    );

    expect(rows).toHaveLength(7);
    expect(rows.map((row) => row.bucket)).toContain("2026-06-20");
    expect(rows.find((row) => row.bucket === "2026-06-20")?.failed_dataflows).toBe(3);
    expect(rows.filter((row) => row.bucket !== "2026-06-20").every((row) => row.failed_jobs === 0 && row.failed_dataflows === 0)).toBe(true);
  });

  it("uses estimated rows written for the Overview workload series", () => {
    const option = monitoringTrendBucketTestUtils.workloadVolumeTrendOption(
      [
        {
          date: "2026-06-20",
          bucket: "2026-06-20",
          grain: "day",
          rows_read: 120,
          rows_written: 40,
          est_rows_written: 110,
          rows_output: 75,
          rows_output_estimated: 35
        }
      ],
      [],
      { ...DEFAULT_MONITORING_FILTERS, range: "custom", grain: "day", startTime: "2026-06-20", endTime: "2026-06-20" },
      { min: "2026-06-20", max: "2026-06-20" },
      "Asia/Saigon",
      "day"
    );
    const series = (option?.series ?? []) as Array<{ name?: string; data?: number[] }>;
    const estimatedSeries = series.find((item) => item.name === "Est rows written");

    expect(estimatedSeries?.data).toEqual([110]);
    expect(series.some((item) => item.name === "Rows output")).toBe(false);
  });
});
