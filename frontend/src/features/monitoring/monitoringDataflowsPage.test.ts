import { renderToStaticMarkup } from "react-dom/server";
import { createElement } from "react";
import { describe, expect, it } from "vitest";
import { chartTooltipContent } from "./components/chartTooltip";
import { durationDistributionBoxOption } from "./components/monitoringDurationPresentation";
import { lifecycleStatusItems, statusColor } from "./components/monitoringChartOptions";
import { TableDateTimeValue } from "./components/monitoringPresentation";
import { EndpointRouteNode, dataflowNameStatusHealthOption } from "./pages/DataflowsPagePresentation";
import { endpointHealthTooltipContent, endpointRoutePresentation } from "./pages/DataflowsPageSupport";

describe("Dataflows page lifecycle status values", () => {
  it("preserves label order and canonical status colors", () => {
    expect(lifecycleStatusItems(2, 3, 5)).toEqual([
      { status: "skipped", value: 2, color: statusColor("skipped") },
      { status: "running", value: 3, color: statusColor("running") },
      { status: "pending", value: 5, color: statusColor("pending") }
    ]);
  });

  it("uses compact durations and legend colors in stage duration tooltips", () => {
    const option = durationDistributionBoxOption([{
      stage: "bronze", count: 3, outlier_count: 1,
      min_duration_seconds: 1, q1_duration_seconds: 2, p50_duration_seconds: 3,
      q3_duration_seconds: 4, max_duration_seconds: 5,
      avg_duration_seconds: 3, p95_duration_seconds: 5,
      succeeded: 2, failed: 1, skipped: 0,
      outliers: [[5, "flow-a", "run-a", "failed", "etl", "duckdb", "file", "local"]]
    }], "stage");
    const formatter = (option.tooltip as { formatter: (params: unknown) => string }).formatter;
    const tooltipContent = chartTooltipContent(formatter);
    const metrics = tooltipContent?.({ dataIndex: 0 }).metrics ?? [];
    const outlierSeries = (option.series as Array<{ name?: string; tooltip?: { formatter?: (params: unknown) => string }; data?: unknown[] }>).find((series) => series.name === "Outliers");
    const outlierTooltip = outlierSeries?.tooltip?.formatter?.({ data: outlierSeries.data?.[0] }) ?? "";

    expect(formatter({ dataIndex: 0 })).not.toContain("seconds");
    expect(metrics.find((metric) => metric.label === "Failed")?.valueColor).toBe(statusColor("failed"));
    expect(outlierTooltip).not.toContain("seconds");
  });

  it("shows Dataflow runs first and colors name-by-status values to the legend", () => {
    const option = dataflowNameStatusHealthOption([{
      dataflow_name: "orders", stage: "bronze", operation_type: "etl", runs: 7, success_rate: 85.71,
      succeeded: 6, failed: 1, skipped: 0, running: 0, pending: 0, unknown: 0,
      avg_duration_seconds: 61, p95_duration_seconds: 90, max_duration_seconds: 100,
      rows_read: 20, rows_written: 15
    }]);
    const formatter = (option.tooltip as { formatter: (params: unknown) => string }).formatter;
    const content = chartTooltipContent(formatter)?.({ dataIndex: 0 });

    expect(content?.metrics?.[0].label).toBe("Dataflow runs");
    expect(content?.metrics?.find((metric) => metric.label === "Failed")?.valueColor).toBe(statusColor("failed"));
    expect(formatter({ dataIndex: 0 })).not.toContain("seconds");
  });

  it("aligns route labels without a format icon and uses a structured, color-coded route tooltip", () => {
    const row = {
      source_name: "lake-source", source_connection_type: "file", source_format: null,
      destination_name: "warehouse", destination_connection_type: "database", destination_format: "delta",
      runs: 5, succeeded: 4, failed: 1, skipped: 0, running: 0, pending: 0, unknown: 0,
      success_rate: 80, avg_duration_seconds: 60, p95_duration_seconds: 120,
      rows_read: 250, rows_written: 200, bytes_added: 1024, bytes_removed: 0
    };
    const endpointMarkup = renderToStaticMarkup(createElement(EndpointRouteNode, { endpoint: { locator: "lake-source", connection: "lake-source", connectionType: "file", format: "" } }));
    const tooltip = endpointHealthTooltipContent(row, endpointRoutePresentation(row, "source"), endpointRoutePresentation(row, "destination"), "file", "delta");

    expect(endpointMarkup).toContain('class="dataflow-route-endpoint-icon" aria-hidden="true"></span>');
    expect(tooltip.metrics[0].label).toBe("Dataflow runs");
    expect(tooltip.metrics.find((metric) => metric.label === "Failed")?.valueColor).toBe(statusColor("failed"));
    expect(JSON.stringify(tooltip)).not.toContain("seconds");
  });

  it("omits redundant hover help for Dataflow run timestamps", () => {
    const markup = renderToStaticMarkup(createElement(TableDateTimeValue, { value: "2026-10-20T12:30:00Z", timezoneName: "UTC", showTooltip: false }));
    expect(markup).not.toContain("aria-description");
  });
});
