import { describe, expect, it } from "vitest";
import { DEFAULT_MONITORING_FILTERS } from "./monitoringFilters";
import { failureCategoryPhaseMatrixOption, failureHorizontalBarOption, failureTrendOption } from "./monitoringShared";

type ChartSeries = {
  name: string;
  stack?: string;
  data: number[];
  itemStyle?: { color?: string };
  lineStyle?: { color?: string };
  areaStyle?: { color?: string };
};

function chartSeries(option: unknown) {
  return ((option as { series?: ChartSeries[] }).series ?? []) as ChartSeries[];
}

function yAxisLabels(option: unknown) {
  return ((option as { yAxis?: { data?: string[] } }).yAxis?.data ?? []) as string[];
}

describe("Monitoring failure chart semantics", () => {
  it("keeps dataflow on the Y axis and uses phase series as the legend", () => {
    const option = failureHorizontalBarOption(
      [{ dataflow_name: "orders", error_count: 5, source: 2, transform: 1, destination: 1, overhead: 1, unknown: 0 }],
      "dataflow_name",
      "error_count",
      "Failed runs"
    );
    const series = chartSeries(option);

    expect(yAxisLabels(option)).toEqual(["orders"]);
    expect(series.map((item) => item.name)).toEqual(["Source", "Transform", "Destination", "Overhead", "Unknown"]);
    expect(series.every((item) => item.stack === "failures")).toBe(true);
    expect(series.map((item) => item.data)).toEqual([[2], [1], [1], [1], [0]]);
  });

  it("keeps category on the Y axis and assigns unattributed totals to Unknown", () => {
    const option = failureCategoryPhaseMatrixOption([
      { category: "Other", total: 3, source: 0, transform: 0, destination: 0, overhead: 0, unknown: 0 }
    ]);
    const series = chartSeries(option);
    const overhead = series.find((item) => item.name === "Overhead");
    const unknown = series.find((item) => item.name === "Unknown");

    expect(yAxisLabels(option)).toEqual(["Other"]);
    expect(unknown?.data).toEqual([3]);
    expect(unknown?.itemStyle?.color).not.toBe(overhead?.itemStyle?.color);
  });

  it("uses distinct entity-level colors for dataflow and job failure trends", () => {
    const option = failureTrendOption(
      [{ date: "2026-07-15", failed_dataflows: 4, failed_jobs: 2 }],
      DEFAULT_MONITORING_FILTERS,
      { min: "2026-07-15", max: "2026-07-15" },
      "UTC",
      "day"
    );
    const series = chartSeries(option);
    const dataflows = series.find((item) => item.name === "Dataflows");
    const jobs = series.find((item) => item.name === "Jobs");

    expect(dataflows?.lineStyle?.color).toBe("#c24141");
    expect(dataflows?.itemStyle?.color).toBe("#c24141");
    expect(dataflows?.areaStyle?.color).toBe("rgba(194, 65, 65, 0.08)");
    expect(jobs?.lineStyle?.color).toBe("#7c3aed");
    expect(jobs?.itemStyle?.color).toBe("#7c3aed");
    expect(jobs?.areaStyle).toBeUndefined();
  });
});
