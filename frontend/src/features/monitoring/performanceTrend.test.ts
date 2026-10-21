import { describe, expect, it } from "vitest";
import { DEFAULT_MONITORING_FILTERS } from "./monitoringFilters";
import { performancePageTestUtils } from "./pages/PerformancePage";
import { chartTooltipContent } from "./components/chartTooltip";
import { monitoringPhaseColors } from "./pages/FailurePagePresentation";

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
    expect(series.find((item) => item.name === "Runs with signals")?.data).toEqual([0, 1, 0]);
    expect(series.find((item) => item.name === "P50")?.lineStyle?.color).toBe("#2563eb");
    expect(series.find((item) => item.name === "P95")?.lineStyle?.color).toBe("#7c3aed");
  });

  it("colors performance trend tooltip values to match their legend series", () => {
    const option = performancePageTestUtils.performanceTrendOption(
      [{ bucket: "2026-06-17", date: "2026-06-17", grain: "day", run_count: 2, p50_duration_seconds: 5, p95_duration_seconds: 9, candidate_count: 1 }],
      { ...DEFAULT_MONITORING_FILTERS, range: "custom", grain: "day", startTime: "2026-06-17", endTime: "2026-06-17" },
      { min: "2026-06-17", max: "2026-06-17" },
      "UTC",
      "day",
    ) as { tooltip?: { formatter?: (params: unknown) => string }; series?: Array<{ name?: string; itemStyle?: { color?: string }; lineStyle?: { color?: string } }> };
    const content = chartTooltipContent(option.tooltip?.formatter)?.([{ dataIndex: 0, seriesName: "P50" }]);
    const metric = (label: string) => content?.metrics?.find((entry) => entry.label === label);
    const series = (name: string) => option.series?.find((entry) => entry.name === name);

    expect(metric("P50")?.valueColor).toBe(series("P50")?.lineStyle?.color);
    expect(metric("P95")?.valueColor).toBe(series("P95")?.lineStyle?.color);
    expect(metric("Runs with performance signals")?.valueColor).toBe(series("Runs with signals")?.itemStyle?.color);
    expect(metric("Runs")?.valueColor).toBeUndefined();
  });

  it("colors bottleneck values by phase and keeps maintenance tooltips on maintenance metrics", () => {
    const maintenance = performancePageTestUtils.workloadEfficiencyOption([{
      dataflow_name: "compact_orders", dataflow_run_id: "maint-1", stage: "silver", operation_type: "maintenance",
      duration_seconds: 20, maintenance_bytes_processed: 2048, maintenance_files_processed: 4,
      rows_read_per_second: 100, performance_bottleneck_phase: "destination", performance_candidate_priority: 1,
    }], "linear", "maintenance") as { tooltip?: { formatter?: (params: unknown) => string } };
    const maintenanceContent = chartTooltipContent(maintenance.tooltip?.formatter)?.({ dataIndex: 0 });
    const maintenanceMetric = (label: string) => maintenanceContent?.metrics?.find((entry) => entry.label === label);

    expect(maintenanceMetric("Bottleneck")?.valueColor).toBe(monitoringPhaseColors.destination);
    expect(maintenanceMetric("Bytes processed")?.value).toContain("2,048 B");
    expect(maintenanceMetric("Files processed")).toBeDefined();
    expect(maintenanceMetric("Rows/s")).toBeUndefined();

    const slowest = performancePageTestUtils.slowestDataflowProfilesOption([{
      dataflow_name: "compact_orders", p95_duration_seconds: 20, performance_bottleneck_phase: "destination",
    }]) as { tooltip?: { formatter?: (params: unknown) => string } };
    const slowestContent = chartTooltipContent(slowest.tooltip?.formatter)?.({ dataIndex: 0 });
    expect(slowestContent?.metrics?.find((entry) => entry.label === "Bottleneck")?.valueColor)
      .toBe(monitoringPhaseColors.destination);
  });

  it("normalizes timestamp buckets before joining them to the visible date range", () => {
    const option = performancePageTestUtils.performanceTrendOption(
      [{
        bucket: "2026-06-17T00:00:00Z",
        date: "2026-06-17T00:00:00Z",
        grain: "day",
        run_count: 2,
        p50_duration_seconds: 5,
        p95_duration_seconds: 9,
        candidate_count: 1
      }],
      {
        ...DEFAULT_MONITORING_FILTERS,
        range: "custom",
        grain: "day",
        startTime: "2026-06-16",
        endTime: "2026-06-18"
      },
      { min: "2026-06-16", max: "2026-06-18" },
      "UTC",
      "day"
    );
    const series = (option.series ?? []) as Array<{ name?: string; data?: Array<number | null> }>;

    expect(series.find((item) => item.name === "P50")?.data).toEqual([0, 5, 0]);
    expect(series.find((item) => item.name === "P95")?.data).toEqual([0, 9, 0]);
    expect(series.find((item) => item.name === "Runs with signals")?.data).toEqual([0, 1, 0]);
  });
});
