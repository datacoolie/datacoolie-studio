import { describe, expect, it } from "vitest";
import { DEFAULT_MONITORING_FILTERS } from "./monitoringFilters";
import { performancePageTestUtils } from "./pages/PerformancePage";

describe("performance trend", () => {
  it("renders missing duration buckets as zero instead of line gaps", () => {
    const option = performancePageTestUtils.performanceTrendOption(
      [
        {
          bucket: "2026-06-17",
          date: "2026-06-17",
          grain: "day",
          run_count: 2,
          p50_duration_seconds: 5,
          p95_duration_seconds: 9,
          candidate_count: 1
        }
      ],
      {
        ...DEFAULT_MONITORING_FILTERS,
        range: "custom",
        grain: "day",
        startTime: "2026-06-16",
        endTime: "2026-06-18"
      },
      { min: "2026-06-16", max: "2026-06-18" },
      "Asia/Saigon",
      "day"
    );
    const series = (option.series ?? []) as Array<{
      name?: string;
      data?: Array<number | null>;
      lineStyle?: { color?: string };
    }>;

    expect(series.find((item) => item.name === "P50")?.data).toEqual([0, 5, 0]);
    expect(series.find((item) => item.name === "P95")?.data).toEqual([0, 9, 0]);
    expect(series.find((item) => item.name === "Candidates")?.data).toEqual([0, 1, 0]);
    expect(series.find((item) => item.name === "P50")?.lineStyle?.color).toBe("#2563eb");
    expect(series.find((item) => item.name === "P95")?.lineStyle?.color).toBe("#7c3aed");
  });
});
