import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { createElement } from "react";
import { AgeCell, FreshnessPage, freshnessSavedWatermarkDisplayValue } from "./pages/FreshnessPage";
import { normalizeMonitoringPage } from "./monitoringQueries";
import type { MonitoringPageResponseTransport } from "../../shared/api/contractTypes";
import { DEFAULT_MONITORING_FILTERS } from "./monitoringFilters";
import {
  fillMissingWatermarkMovementTrendRows,
  freshnessAgeByDataflowOption,
  freshnessAgeDistributionOption,
  readBoundaryAdjustmentChartRows,
  readBoundaryAdjustmentOption,
  skippedStreakDistributionOption,
  watermarkCoverageByStageOption,
  watermarkMovementTrendOption,
} from "./pages/FreshnessPage";
import { chartTooltipContent } from "./components/chartTooltip";

type ChartDatum = number | { value: number; itemStyle?: { color?: string } };
type ChartSeries = { data?: ChartDatum[] };

function firstSeries(option: unknown) {
  return ((option as { series?: ChartSeries[] }).series ?? [])[0];
}

describe("Monitoring freshness chart semantics", () => {
  it.each([0, 1])("only shows a zero Check age when dated evidence exists (%s)", (evidenceCount) => {
    const report = normalizeMonitoringPage({ page: "freshness", schema_version: "monitoring-page.v9",
      summary: {}, freshness: { kpis: { observed_dataflows: 1, freshness_runs: 1,
        dataflows_with_freshness_evidence: evidenceCount, p95_age_seconds: 0, min_age_seconds: 0,
        p50_age_seconds: 0, max_age_seconds: 0 } } } as unknown as MonitoringPageResponseTransport);
    const html = renderToStaticMarkup(createElement(FreshnessPage, {
      report, filters: DEFAULT_MONITORING_FILTERS, rows: [], totalRows: 0, loading: false,
      sort: { sortBy: "latest_freshness_at", sortDir: "desc" }, onSort: () => undefined,
      limit: 100, offset: 0, onPageChange: () => undefined, onPageSizeChange: () => undefined,
    }));
    expect(html).toContain(evidenceCount ? "P95 0s" : "P95 -");
    if (!evidenceCount) expect(html).not.toContain("P95 0s");
  });
  it("renders unknown table ages separately from a real zero", () => {
    expect(renderToStaticMarkup(createElement(AgeCell, { row: { age_seconds: null, age_days: null } })))
      .toContain('>-</span>');
    expect(renderToStaticMarkup(createElement(AgeCell, { row: { age_seconds: 0, age_days: 0 } })))
      .toContain('>0s</span>');
  });
  it("formats saved watermark objects at readable wrap points", () => {
    expect(freshnessSavedWatermarkDisplayValue({
      latest_success_watermark: '{"event_date":"2024-01-03","partition":"orders"}',
    })).toBe("event_date: 2024-01-03 · partition: orders");
    expect(freshnessSavedWatermarkDisplayValue({ latest_success_watermark: "2024-01-03" }))
      .toBe("2024-01-03");
  });

  it("distinguishes the successful freshness basis from a later failed run in the tooltip", () => {
    const option = freshnessAgeByDataflowOption([{ dataflow_name: "orders", age_days: 1,
      latest_freshness_status: "succeeded", latest_run_status: "failed" }]) as {
        tooltip?: { formatter?: (params: unknown) => string } };
    const tooltip = option.tooltip?.formatter?.({ dataIndex: 0 });
    expect(tooltip).toContain("Latest check status: succeeded");
    expect(tooltip).toContain("Latest run status: failed");
  });
  it("fills missing dates in the watermark timeline and leaves missing rates unknown", () => {
    const rows = fillMissingWatermarkMovementTrendRows(
      [
        { date: "2026-06-18T07:00:00+07:00", bucket: "2026-06-18T07:00:00+07:00", grain: "day", advanced: 2, watermark_enabled_runs: 2, advanced_rate: 100 },
        { date: "2026-06-20T07:00:00+07:00", bucket: "2026-06-20T07:00:00+07:00", grain: "day", unchanged: 1, watermark_enabled_runs: 1, advanced_rate: 0 },
      ],
      {
        ...DEFAULT_MONITORING_FILTERS,
        range: "custom",
        grain: "day",
        startTime: "2026-06-18T00:00:00Z",
        endTime: "2026-06-20T23:59:59Z",
      },
      { min: "2026-06-18", max: "2026-06-20" },
      "Asia/Saigon",
      "day",
    );

    expect(rows.map((row) => row.bucket)).toEqual(["2026-06-18", "2026-06-19", "2026-06-20", "2026-06-21"]);
    expect(rows[1]).toMatchObject({ advanced: 0, unchanged: 0, watermark_enabled_runs: 0, advanced_rate: null, not_attempted: 0, save_error: 0 });

    const option = watermarkMovementTrendOption(rows) as { series?: Array<{ name?: string; data?: unknown[] }> };
    expect(option.series?.find((series) => series.name === "Advanced")?.data).toEqual([2, 0, 0, 0]);
    expect(option.series?.find((series) => series.name === "Advanced %")?.data).toEqual([100, null, 0, null]);
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
      { bucket: "≤1d", dataflows: 1 },
      { bucket: "1–3d", dataflows: 2 },
      { bucket: "3–7d", dataflows: 3 },
      { bucket: "7–30d", dataflows: 4 },
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
      { date: "2026-07-15", initialized: 1, advanced: 2, unchanged: 3, total: 6, watermark_rate_runs: 6, advanced_rate: 33.33 },
    ]) as { legend?: { show?: boolean } };

    expect(option.legend?.show).toBe(false);
  });

  it("colors movement tooltip values to their series and leaves rollups neutral", () => {
    const row = {
      date: "2026-07-15", initialized: 1, advanced: 2, changed: 3, empty: 4,
      unchanged: 5, unknown: 6, watermark_enabled_runs: 21, watermark_rate_runs: 24,
      advanced_rate: 50,
    };
    const option = watermarkMovementTrendOption([row]) as {
      series?: Array<{ name?: string; itemStyle?: { color?: string }; lineStyle?: { color?: string } }>;
      tooltip?: { formatter?: (params: unknown) => string };
    };
    const params = [
      ...["Initialized", "Advanced", "Changed", "Empty", "Unchanged", "Unknown", "Advanced %"]
        .map((seriesName) => ({ dataIndex: 0, seriesName })),
    ];
    const content = chartTooltipContent(option.tooltip?.formatter)?.(params);
    const metric = (label: string) => content?.metrics?.find((entry) => entry.label === label);
    const seriesColor = (name: string) => {
      const series = option.series?.find((entry) => entry.name === name);
      return name === "Advanced %" ? series?.lineStyle?.color : series?.itemStyle?.color;
    };

    for (const name of ["Initialized", "Advanced", "Changed", "Empty", "Unchanged", "Unknown"]) {
      expect(metric(name)?.valueColor).toBe(seriesColor(name));
    }
    expect(metric("Advanced rate")?.valueColor).toBe(seriesColor("Advanced %"));
    expect(metric("Configured watermark runs")?.valueColor).toBeUndefined();
    expect(metric("Total ETL runs")?.valueColor).toBeUndefined();
  });

  it("shows the advanced rate only once in the movement tooltip", () => {
    const option = watermarkMovementTrendOption([
      { date: "2026-07-15", advanced: 2, unchanged: 1, watermark_enabled_runs: 3, advanced_rate: 66.67 },
    ]) as { tooltip?: { formatter?: (params: unknown) => string } };
    const tooltip = option.tooltip?.formatter?.([
      { dataIndex: 0, seriesName: "Advanced", value: 2, marker: "" },
      { dataIndex: 0, seriesName: "Advanced %", value: 66.67, marker: "" },
    ]);

    expect(tooltip).toContain("Advanced rate: 66.67%");
    expect(tooltip).not.toContain("Advanced %:");
  });

  it("shows the total ETL denominator and renders Empty movement", () => {
    const option = watermarkMovementTrendOption([
      { date: "2026-07-15", advanced: 1, unchanged: 1, changed: 1, empty: 1, watermark_enabled_runs: 4, watermark_rate_runs: 4, advanced_rate: 25 },
    ]) as { series?: Array<{ name?: string; data?: unknown[] }> ; tooltip?: { formatter?: (params: unknown) => string } };
    const tooltip = option.tooltip?.formatter?.([
      { dataIndex: 0, seriesName: "Empty", value: 1, marker: "" },
      { dataIndex: 0, seriesName: "Advanced %", value: 25, marker: "" },
    ]) ?? "";

    expect(option.series?.find((series) => series.name === "Empty")?.data).toEqual([1]);
    expect(tooltip).toContain("Empty: 1");
    expect(tooltip).toContain("Total ETL runs: 4");
    expect(tooltip).toContain("Advanced rate: 25% (1 advanced / 4 ETL runs)");
  });

  it("does not claim zero ETL runs when the advanced rate is missing", () => {
    const option = watermarkMovementTrendOption([
      { date: "2026-07-15", advanced: 1, advanced_rate: null },
    ]) as { tooltip?: { formatter?: (params: unknown) => string } };
    const tooltip = option.tooltip?.formatter?.([{ dataIndex: 0, seriesName: "Advanced", marker: "" }]) ?? "";

    expect(tooltip).toContain("unk (ETL run count unavailable; rate unavailable)");
    expect(tooltip).not.toContain("0 ETL runs");
  });

  it("keeps movement tooltip focused while preserving exact counts", () => {
    const option = watermarkMovementTrendOption([
      {
        date: "2026-07-15",
        advanced: "9007199254740993",
        unknown: null,
        watermark_enabled_runs: "9007199254740993",
        not_requested: "12",
        not_attempted: "0",
        no_candidate: "1",
        save_error: "9007199254740993",
        advanced_rate: null,
      },
    ]) as { series?: Array<{ name?: string; data?: unknown[] }> ; tooltip?: { formatter?: (params: unknown) => string } };

    expect(option.series?.find((series) => series.name === "Advanced")?.data?.[0]).toBeGreaterThan(9e15);
    expect(option.series?.find((series) => series.name === "Unknown")?.data?.[0]).toBeNull();
    expect(option.series?.find((series) => series.name === "Advanced %")?.data?.[0]).toBeNull();
    const tooltip = option.tooltip?.formatter?.([{ dataIndex: 0, seriesName: "Advanced", marker: "" }]) ?? "";
    expect(tooltip).toContain("Advanced: 9,007,199,254,740,993");
    expect(tooltip).not.toContain("Not requested");
    expect(tooltip).not.toContain("Save error");
  });

  it("does not call unknown Freshness evidence current", () => {
    const report = normalizeMonitoringPage({
      page: "freshness",
      schema_version: "monitoring-page.v11",
      summary: {},
      freshness: {
        kpis: {
          observed_dataflows: null,
          latest_watermark_unknown_dataflows: "1",
          watermark_unknown_runs: "1",
          watermark_save_error_runs: "1",
        },
      },
    } as unknown as MonitoringPageResponseTransport);
    const html = renderToStaticMarkup(createElement(FreshnessPage, {
      report, filters: DEFAULT_MONITORING_FILTERS, rows: [], totalRows: 0, loading: false,
      sort: { sortBy: "latest_freshness_at", sortDir: "desc" }, onSort: () => undefined,
      limit: 100, offset: 0, onPageChange: () => undefined, onPageSizeChange: () => undefined,
    }));
    expect(html).toContain("Evidence unknown");
    expect(html).toContain("Unknown 1");
    expect(html).toContain("save error 1");
    expect(html).not.toContain("wm issue");
  });

  it.each([
    ["freshness age distribution", freshnessAgeDistributionOption([{ bucket: "≤1d", dataflows: 130 }])],
  ])("lets ECharts auto-scale the %s Y-axis", (_name, option) => {
    const yAxis = (option as { yAxis?: { max?: unknown } }).yAxis;

    expect(yAxis?.max).toBeUndefined();
  });

  it("keeps the coverage legend out of the chart plot area", () => {
    const option = watermarkCoverageByStageOption([
      { stage: "bronze", observed_dataflows: 2, watermark_enabled_dataflows: 1, not_configured_dataflows: 1, coverage_rate: 50 },
    ]) as { legend?: { show?: boolean } };

    expect(option.legend?.show).toBe(false);
  });

  it("includes the coverage denominator in the stage tooltip", () => {
    const option = watermarkCoverageByStageOption([
      { stage: "bronze", observed_dataflows: 12, watermark_enabled_dataflows: 3, not_configured_dataflows: 9, coverage_rate: 25 },
    ]) as { tooltip?: { formatter?: (params: unknown) => string } };
    const tooltip = option.tooltip?.formatter?.({ dataIndex: 0 }) ?? "";

    expect(tooltip).toContain("Observed dataflows: 12");
    expect(tooltip).toContain("Enabled dataflows: 3");
    expect(tooltip).toContain("Coverage: 25%");
  });

  it("renders only adjusted dataflows and keeps other states out of the bars", () => {
    const option = readBoundaryAdjustmentOption([
      { dataflow_name: "adjusted-flow", adjustment_state: "adjusted", adjustment_adjusted_runs: 2 },
      { dataflow_name: "more-adjusted-flow", adjustment_state: "adjusted", adjustment_adjusted_runs: 4 },
      { dataflow_name: "same-flow", adjustment_state: "unchanged", adjustment_unchanged_runs: 3 },
      { dataflow_name: "unknown-flow", adjustment_state: "unknown", adjustment_unknown_runs: 1 },
      { dataflow_name: "full-flow", adjustment_state: "not_applied" },
    ]) as { yAxis?: { data?: string[] }; series?: Array<{ name?: string; data?: unknown[] }> };

    expect(option.yAxis?.data).toEqual(["more-adjusted-flow", "adjusted-flow"]);
    expect(option.series).toHaveLength(1);
    expect(option.series?.find((series) => series.name === "Adjusted")?.data).toEqual([4, 2]);
  });

  it("keeps the read-boundary tooltip aligned with the adjusted-only visual", () => {
    const option = readBoundaryAdjustmentOption([
      { dataflow_name: "orders", source_name: "orders_db", adjustment_state: "adjusted", adjustment_adjusted_runs: 2 },
    ]) as { tooltip?: { formatter?: (params: unknown) => string } };
    const tooltip = option.tooltip?.formatter?.({ dataIndex: 0 }) ?? "";

    expect(tooltip).toContain("Source: orders_db");
    expect(tooltip).toContain("Adjusted runs: 2");
    expect(tooltip).not.toContain("Unchanged");
    expect(tooltip).not.toContain("Unknown");
    expect(tooltip).not.toContain("Input lower");
  });

  it("shows an explicit empty state when every entity is not applicable", () => {
    const option = readBoundaryAdjustmentOption([
      { dataflow_name: "full-flow", adjustment_state: "not_applied" },
    ]) as { title?: { text?: string } };
    expect(option.title?.text).toBe("No adjusted read-boundary evidence.");
  });

  it("keeps chart indexes aligned after omitting not-applied-only entities", () => {
    const rows = [
      { dataflow_id: "only-not-applied", dataflow_name: "only-not-applied", adjustment_state: "not_applied", adjustment_not_applied_runs: 4 },
      { dataflow_id: "adjusted", dataflow_name: "adjusted", adjustment_state: "adjusted", adjustment_adjusted_runs: 2 },
      { dataflow_id: "mixed", dataflow_name: "mixed", adjustment_state: "not_applied", adjustment_adjusted_runs: 1, adjustment_not_applied_runs: 3, adjustment_comparable_runs: 1 },
    ];

    expect(readBoundaryAdjustmentChartRows(rows).map((row) => row.dataflow_id)).toEqual(["adjusted", "mixed"]);
  });

  it("maps the canonical SQL aggregate fields into non-zero chart data", () => {
    const age = freshnessAgeDistributionOption([{ bucket: "≤1d", dataflows: 130 }]);
    const coverage = watermarkCoverageByStageOption([{
      stage: "bronze",
      observed_dataflows: 12,
      watermark_enabled_dataflows: 3,
      not_configured_dataflows: 9,
      coverage_rate: 25,
    }]);
    const streak = skippedStreakDistributionOption([{ bucket: "2–3", dataflows: 14 }]);

    expect((firstSeries(age)?.data?.[0] as { value: number }).value).toBe(130);
    expect(((coverage as { series?: ChartSeries[] }).series ?? []).map((series) => series.data?.[0])).toEqual([25, 75]);
    expect((streak as { xAxis?: { data?: string[] } }).xAxis?.data).toEqual(["2–3"]);
    expect(firstSeries(streak)?.data).toEqual([14]);
  });

  it.each([
    ["movement trend", watermarkMovementTrendOption([{ date: "2026-07-15", watermark_enabled_runs: 1, advanced: 1, advanced_rate: 100 }])],
    ["age distribution", freshnessAgeDistributionOption([{ bucket: "1-3d", dataflows: 1 }])],
    ["consecutive skipped", skippedStreakDistributionOption([{ bucket: ">7", dataflows: 1 }])],
  ])("keeps the %s X-axis five pixels from the panel bottom", (_name, option) => {
    const grid = (option as { grid?: { bottom?: number; containLabel?: boolean } }).grid;

    expect(grid?.bottom).toBe(5);
    expect(grid?.containLabel).toBe(false);
  });

  it.each([
    ["oldest dataflows", freshnessAgeByDataflowOption(Array.from({ length: 9 }, (_, index) => ({ dataflow_name: `flow_${index}`, age_days: index + 1 })))],
    ["watermark coverage", watermarkCoverageByStageOption(Array.from({ length: 9 }, (_, index) => ({ stage: `stage_${index}`, observed_dataflows: 2, watermark_enabled_dataflows: 1, not_configured_dataflows: 1, coverage_rate: 50 })))],
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
