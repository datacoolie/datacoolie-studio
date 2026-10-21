import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { DEFAULT_MONITORING_FILTERS } from "./monitoringFilters";
import { StatusHealthLegend } from "./components/monitoringPresentation";
import { OVERVIEW_STATUSES } from "./components/monitoringModel";
import {
  dataflowStatusTrendOption,
  stageTotal,
  trendRate
} from "./components/monitoringChartOptions";
import { jobStageHealthOption } from "./pages/JobsPagePresentation";
import { dataflowNameStatusHealthOption } from "./pages/DataflowsPagePresentation";
import {
  operationHealthCombinedBarOption,
  operationHealthTooltip,
  operationTotal
} from "./pages/MonitoringOverviewPageSupport";

const filters = {
  ...DEFAULT_MONITORING_FILTERS,
  range: "custom" as const,
  grain: "day" as const,
  startTime: "2026-07-15",
  endTime: "2026-07-15"
};
const dateRange = { min: "2026-07-15", max: "2026-07-15" };

describe("unknown monitoring status", () => {
  it("is part of the shared status list and rendered health legend", () => {
    expect(OVERVIEW_STATUSES).toContain("unknown");
    expect(renderToStaticMarkup(createElement(StatusHealthLegend))).toContain(">Unknown</span>");
  });

  it("keeps unknown-only trend and stage/name rows visible", () => {
    const trendOption = dataflowStatusTrendOption(
      [{ date: "2026-07-15", bucket: "2026-07-15", total: 2, unknown: 2 }],
      filters,
      dateRange,
      "UTC",
      "day"
    );
    const trendSeries = trendOption.series as Array<{ name?: string; data?: number[] }>;
    expect(trendSeries.find((series) => series.name === "unknown")?.data).toEqual([2]);

    const stageRow = { stage: "unknown", unknown: 2 };
    const stageOption = jobStageHealthOption([stageRow]);
    const stageSeries = stageOption.series as Array<{ name?: string; data?: number[] }>;
    expect(stageTotal(stageRow)).toBe(2);
    expect((stageOption.yAxis as { data?: string[] }).data).toEqual(["unknown (value)"]);
    expect(stageSeries.find((series) => series.name === "unknown")?.data).toEqual([2]);

    const dataflowOption = dataflowNameStatusHealthOption([{ dataflow_name: "unknown-flow", unknown: 3 }]);
    const dataflowSeries = dataflowOption.series as Array<{ name?: string; data?: number[] }>;
    expect((dataflowOption.yAxis as { data?: string[] }).data).toEqual(["unknown-flow"]);
    expect(dataflowSeries.find((series) => series.name === "unknown")?.data).toEqual([3]);
  });

  it("shows unknown counts in trend, stage, name, and operation tooltips", () => {
    const trendOption = dataflowStatusTrendOption(
      [{ date: "2026-07-15", bucket: "2026-07-15", total: 2, unknown: 2 }],
      filters,
      dateRange,
      "UTC",
      "day"
    );
    const trendTooltip = trendOption.tooltip as { formatter?: (params: unknown) => string };
    expect(trendTooltip.formatter?.([{ dataIndex: 0 }])).toContain("Unknown: 2");

    const stageOption = jobStageHealthOption([{ stage: "unknown", unknown: 2 }]);
    const stageTooltip = stageOption.tooltip as { formatter?: (params: unknown) => string };
    expect(stageTooltip.formatter?.({ dataIndex: 0 })).toContain("Unknown: 2");

    const dataflowOption = dataflowNameStatusHealthOption([{ dataflow_name: "unknown-flow", unknown: 3 }]);
    const dataflowTooltip = dataflowOption.tooltip as { formatter?: (params: unknown) => string };
    expect(dataflowTooltip.formatter?.({ dataIndex: 0 })).toContain("Unknown: 3");

    const operationRow = { operation_type: "unknown-operation", count: 4 };
    expect(operationTotal(operationRow)).toBe(4);
    expect(operationHealthTooltip(operationRow)).toContain("Unknown: 4");
    const operationOption = operationHealthCombinedBarOption([operationRow], []);
    const operationSeries = operationOption.series as Array<{ name?: string; data?: number[] }>;
    expect(operationSeries.find((series) => series.name === "Job unknown")?.data).toEqual([4]);
    const operationTooltip = operationOption.tooltip as { formatter?: (params: unknown) => string };
    expect(operationTooltip.formatter?.({ seriesName: "Job unknown", dataIndex: 0 })).toContain("Unknown: 4");
  });

  it("keeps success and failure rates on the succeeded-plus-failed denominator", () => {
    const row = { succeeded: 1, failed: 1, unknown: 8 };
    expect(trendRate(row, "success_rate", "succeeded")).toBe(50);
    expect(trendRate(row, "failure_rate", "failed")).toBe(50);
  });
});
