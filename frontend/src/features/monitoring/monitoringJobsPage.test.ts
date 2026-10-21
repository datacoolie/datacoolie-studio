import { describe, expect, it } from "vitest";import { durationDistributionBoxOption } from "./components/monitoringDurationPresentation";import { chartTooltipContent } from "./components/chartTooltip";import { childFanoutDistributionOption, jobWorkloadEfficiencyOption } from "./pages/JobsPagePresentation";import { lifecycleStatusItems, operationColor, statusColor } from "./components/monitoringChartOptions";

describe("Jobs page chart layout", () => {
  it("shows pipeline and maintenance operation types with unit-specific rate tooltips", () => {
    const option = jobWorkloadEfficiencyOption([
      { job_id: "job-etl", operation_type: "etl", child_dataflow_count: 2, duration_seconds: 120, workload_size: 30, workload_size_metric: "rows_per_second" },
      { job_id: "job-compact", operation_type: "compact", child_dataflow_count: 1, duration_seconds: 60, workload_size: 12, workload_size_metric: "bytes_per_second", bytes_added: 900, bytes_removed: 300 }
    ]);
    const series = option.series as Array<{ name?: string }>;
    const formatter = (option as any).tooltip.formatter as (params: unknown) => string;
    const content = chartTooltipContent(formatter);

    expect(series.map((item) => item.name)).toEqual(["compact", "etl"]);
    const tooltip = formatter({ data: { row: { job_id: "job-compact", operation_type: "compact", duration_seconds: 60, workload_size: 12, workload_size_metric: "bytes_per_second", bytes_added: 900, bytes_removed: 300 } } });
    const tooltipMetrics = content?.({ data: { row: { job_id: "job-compact", operation_type: "compact", duration_seconds: 60, workload_size: 12, workload_size_metric: "bytes_per_second", bytes_added: 900, bytes_removed: 300 } } }).metrics;
    expect(tooltip).toContain("Workload rate (bytes/s): 12");
    expect(tooltip).toContain("Total duration: 1m");
    expect(tooltip).not.toContain("seconds");
    expect(tooltipMetrics?.find((metric) => metric.label === "Dataflow operation")?.valueColor).toBe(operationColor("compact", 0));
  });

  it("anchors workload efficiency to the bottom of its chart area", () => {
    const option = jobWorkloadEfficiencyOption([
      { job_id: "job-1", operation_type: "etl", child_dataflow_count: 2, duration_seconds: 12, workload_size: 100 }
    ]);

    expect((option.grid as { bottom?: number }).bottom).toBe(0);
  });

  it("anchors fan-out to the bottom when no zoom control is needed", () => {
    const option = childFanoutDistributionOption([
      { bin_label: "1-2", bin_start: 1, bin_end: 2, jobs: 3, succeeded: 3, failed: 0, skipped: 0, running: 0, pending: 0 }
    ]);

    expect((option.grid as { bottom?: number; top?: number; containLabel?: boolean }).bottom).toBe(0);
    expect((option.grid as { bottom?: number; top?: number; containLabel?: boolean }).containLabel).toBe(false);
    expect((option.grid as { bottom?: number; top?: number }).top).toBeGreaterThanOrEqual(16);
    expect((option.yAxis as { max?: unknown }).max).toBeUndefined();
  });

  it("uses the canonical trend colors for lifecycle KPI values", () => {
    expect(lifecycleStatusItems(7, 3, 5)).toEqual([
      { status: "skipped", value: 7, color: statusColor("skipped") },
      { status: "running", value: 3, color: statusColor("running") },
      { status: "pending", value: 5, color: statusColor("pending") }
    ]);
  });

  it("keeps an unreported lifecycle total distinguishable from zero", () => {
    expect(lifecycleStatusItems(7, null, 5)).toEqual([
      { status: "skipped", value: 7, color: statusColor("skipped") },
      { status: "running", value: null, color: statusColor("running") },
      { status: "pending", value: 5, color: statusColor("pending") }
    ]);
  });

  it("shows runtime context without job identity or operation in duration outlier tooltips", () => {
    const option = durationDistributionBoxOption([
      {
        operation_type: "etl, maintenance",
        count: 5,
        outliers: [[100, "job-1", "job-1", "failed", "etl, maintenance", "duckdb", "file", "local"]]
      }
    ], "operation_type", "job");
    const outlierSeries = (option.series as Array<{ name?: string; tooltip?: { formatter?: (params: unknown) => string }; data?: unknown[] }>)
      .find((series) => series.name === "Outliers");
    const tooltip = outlierSeries?.tooltip?.formatter?.({ data: outlierSeries.data?.[0] }) ?? "";

    expect(tooltip).toContain("Runtime: duckdb / file / local");
    expect(tooltip).not.toContain("Job ID:");
    expect(tooltip).not.toContain("Operation:");
    expect(tooltip).not.toContain("Job:");
    expect(tooltip).not.toContain("etl, maintenance");
    expect(tooltip).toContain("Duration: 1.67m");
    expect(tooltip).not.toContain("seconds");
  });
  it("uses compact durations for job operation distribution metrics", () => {
    const option = durationDistributionBoxOption([{
      operation_type: "etl", count: 2, min_duration_seconds: 0,
      q1_duration_seconds: 1.23456, p50_duration_seconds: 60,
      q3_duration_seconds: 90, max_duration_seconds: 120,
      avg_duration_seconds: 75, p95_duration_seconds: null,
    }], "operation_type", "job");
    const tooltip = (option as any).tooltip.formatter({ dataIndex: 0 });
    expect(tooltip).toContain("Min: 0s");
    expect(tooltip).toContain("Q1: 1.23s");
    expect(tooltip).toContain("Median: 1m");
    expect(tooltip).toContain("Avg / P95: 1.25m / Unavailable");
    expect(tooltip).not.toContain("seconds");
  });
});
