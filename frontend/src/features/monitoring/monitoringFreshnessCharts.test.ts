import { describe, expect, it } from "vitest";
import { DEFAULT_MONITORING_FILTERS } from "./monitoringFilters";
import {
  fillMissingWatermarkMovementTrendRows,
  freshnessAgeByDataflowOption,
  freshnessAgeDistributionOption,
  skippedStreakDistributionOption,
  watermarkAdjustmentOption,
  watermarkCoverageByStageOption,
  watermarkMovementTrendOption,
} from "./pages/FreshnessPage";

type ChartDatum = number | { value: number; itemStyle?: { color?: string } };
type ChartSeries = { data?: ChartDatum[] };

function firstSeries(option: unknown) {
  return ((option as { series?: ChartSeries[] }).series ?? [])[0];
}

describe("Monitoring freshness chart semantics", () => {
  it("fills missing dates in the watermark timeline and renders missing rates as zero", () => {
    const rows = fillMissingWatermarkMovementTrendRows(
      [
        { date: "2026-06-18", bucket: "2026-06-18", advanced: 2, total: 2, advanced_rate: 100 },
        { date: "2026-06-20", bucket: "2026-06-20", unchanged: 1, total: 1, advanced_rate: 0 },
      ],
      {
        ...DEFAULT_MONITORING_FILTERS,
        range: "custom",
        grain: "day",
        startTime: "2026-06-18T00:00:00Z",
        endTime: "2026-06-20T23:59:59Z",
      },
      { min: "2026-06-18", max: "2026-06-20" },
      "UTC",
      "day",
    );

    expect(rows.map((row) => row.bucket)).toEqual(["2026-06-18", "2026-06-19", "2026-06-20"]);
    expect(rows[1]).toMatchObject({ advanced: 0, unchanged: 0, adjusted: 0, total: 0, advanced_rate: 0 });

    const option = watermarkMovementTrendOption(rows) as { series?: Array<{ name?: string; data?: unknown[] }> };
    expect(option.series?.find((series) => series.name === "Advanced %")?.data).toEqual([100, 0, 0]);
  });

  it("colors age by the fixed seven-day threshold instead of run status", () => {
    const option = freshnessAgeByDataflowOption([
      { dataflow_name: "current_failed", age_days: 7, latest_freshness_status: "failed" },
      { dataflow_name: "stale_succeeded", age_days: 8, latest_freshness_status: "succeeded" },
    ]);
    const data = firstSeries(option)?.data as Array<{ value: number; itemStyle: { color: string } }>;

    expect(data.map((item) => item.value)).toEqual([8, 7]);
    expect(data.map((item) => item.itemStyle.color)).toEqual(["#c77d2f", "#8b95a5"]);
  });

  it("uses distinct age-band colors and reserves warning colors for stale bands", () => {
    const option = freshnessAgeDistributionOption([
      { bucket: "<24h", dataflows: 1 },
      { bucket: "1-3d", dataflows: 2 },
      { bucket: "3-7d", dataflows: 3 },
      { bucket: "7-30d", dataflows: 4 },
      { bucket: ">30d", dataflows: 5 },
      { bucket: "Unknown", dataflows: 6 },
    ]);
    const data = firstSeries(option)?.data as Array<{ itemStyle: { color: string } }>;

    expect(data.map((item) => item.itemStyle.color)).toEqual([
      "#155e59",
      "#3d6fa8",
      "#8a6fd1",
      "#c77d2f",
      "#c94a4f",
      "#8b95a5",
    ]);
  });

  it("keeps the movement legend out of the chart plot area", () => {
    const option = watermarkMovementTrendOption([
      { date: "2026-07-15", initialized: 1, advanced: 2, unchanged: 3, total: 6, advanced_rate: 40 },
    ]) as { legend?: { show?: boolean } };

    expect(option.legend?.show).toBe(false);
  });

  it("keeps the coverage legend out of the chart plot area", () => {
    const option = watermarkCoverageByStageOption([
      { stage: "bronze", total: 2, enabled: 1, coverage_rate: 50 },
    ]) as { legend?: { show?: boolean } };

    expect(option.legend?.show).toBe(false);
  });

  it.each([
    ["movement trend", watermarkMovementTrendOption([{ date: "2026-07-15", total: 1, advanced: 1, advanced_rate: 100 }])],
    ["age distribution", freshnessAgeDistributionOption([{ bucket: "1-3d", dataflows: 1 }])],
    ["consecutive skipped", skippedStreakDistributionOption([{ bucket: ">7", dataflows: 1 }])],
    ["watermark adjustments", watermarkAdjustmentOption([{ date: "2026-07-15", adjusted: 1, total: 1 }])],
  ])("keeps the %s X-axis five pixels from the panel bottom", (_name, option) => {
    const grid = (option as { grid?: { bottom?: number; containLabel?: boolean } }).grid;

    expect(grid?.bottom).toBe(5);
    expect(grid?.containLabel).toBe(false);
  });

  it.each([
    ["oldest dataflows", freshnessAgeByDataflowOption(Array.from({ length: 9 }, (_, index) => ({ dataflow_name: `flow_${index}`, age_days: index + 1 })))],
    ["watermark coverage", watermarkCoverageByStageOption(Array.from({ length: 9 }, (_, index) => ({ stage: `stage_${index}`, total: 2, enabled: 1, coverage_rate: 50 })))],
  ])("locks the %s vertical navigator to the shared horizontal-bar scroll pattern", (_name, option) => {
    const dataZoom = (option as { dataZoom?: Array<Record<string, unknown>> }).dataZoom ?? [];
    const slider = dataZoom.find((item) => item.type === "slider");
    const inside = dataZoom.find((item) => item.type === "inside");

    expect(slider).toMatchObject({
      orient: "vertical",
      zoomLock: true,
      handleSize: 0,
      moveHandleSize: 0,
      showDataShadow: false,
      brushSelect: false,
    });
    expect(inside).toMatchObject({
      orient: "vertical",
      zoomLock: true,
      moveOnMouseWheel: true,
      zoomOnMouseWheel: false,
    });
  });
});
