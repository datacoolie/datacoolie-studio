import { describe, expect, it } from "vitest";
import { chartDurationMetric, chartMetric, chartTooltip } from "./chartTooltip";
import { durationDistributionBoxOption, durationOutlierPoints } from "./monitoringDurationPresentation";
import { dataflowNameStatusHealthOption } from "../pages/DataflowsPagePresentation";


describe("chart tooltip contract", () => {
  it("escapes builder-owned content while retaining exact accessible text", () => {
    const formatter = chartTooltip(() => ({
      title: "run <alpha>",
      context: "  source\t→\tdestination  ",
      metrics: [chartMetric("Rows", "9,007,199,254,740,993")],
    }));

    const html = formatter({ dataIndex: 0 });

    expect(html).toContain("run &lt;alpha&gt;");
    expect(html).not.toContain("<alpha>");
    expect(html).toContain("aria-label=\"run &lt;alpha&gt;\n  source\t→\tdestination  \nRows: 9,007,199,254,740,993\"");
  });

  it("keeps duplicate outlier coordinates as separate chart points", () => {
    const points = durationOutlierPoints({
      stage: "transform",
      outliers: [
        { duration_seconds: 10, status: "failed", dataflow_run_id: "run-a" },
        { duration_seconds: 10, status: "failed", dataflow_run_id: "run-b" },
      ],
    }, 2);

    expect(points).toHaveLength(2);
    expect(points[0]?.value).toEqual([10, 2]);
    expect(points[1]?.value).toEqual([10, 2]);
    expect(points[0]?.meta.chartPointId).not.toBe(points[1]?.meta.chartPointId);
  });

  it("keeps duration help exact and distinguishes unavailable raw values", () => {
    expect(chartDurationMetric("9007199254740993")).toBe("150,119,987,579,016.53m (9,007,199,254,740,993 seconds)");
    expect(chartDurationMetric(null)).toBe("Unavailable");
    const points = durationOutlierPoints({ stage: "transform", outliers: [{ duration_seconds: null }] }, 0);
    expect(points[0]?.value).toEqual([null, 0]);
    expect((points[0]?.meta as Record<string, unknown>).duration_seconds).toBeNull();
  });

  it("uses source duration fields in box plot inspection instead of geometry values", () => {
    const option = durationDistributionBoxOption([{
      stage: "transform",
      count: "9007199254740993",
      outlier_count: 1,
      min_duration_seconds: "0.125",
      q1_duration_seconds: "1.25",
      p50_duration_seconds: "2.5",
      q3_duration_seconds: "3.75",
      max_duration_seconds: "4.5",
      avg_duration_seconds: "2.25",
      p95_duration_seconds: "4.25",
      succeeded: "9007199254740993",
      failed: 0,
      skipped: 0,
      outliers: [],
    }], "stage");
    const formatter = (option as any).tooltip.formatter as (params: unknown) => string;
    const html = formatter({ dataIndex: 0 });
    expect(html).toContain("Dataflow runs: 9,007,199,254,740,993");
    expect(html).toContain("Min: 0.13s");
    expect(html).toContain("Avg / P95: 2.25s / 4.25s");
  });

  it("keeps dataflow status and duration source values exact in chart help", () => {
    const option = dataflowNameStatusHealthOption([{
      dataflow_name: "orders",
      stage: "silver",
      operation_type: "copy",
      runs: "9007199254740993",
      success_rate: "99.5",
      succeeded: "9007199254740993",
      failed: "9007199254740995",
      skipped: 0,
      running: 0,
      pending: 0,
      unknown: 0,
      avg_duration_seconds: "1.25",
      p95_duration_seconds: "2.5",
      max_duration_seconds: "3.75",
      rows_read: null,
      rows_written: null,
    }]);
    const formatter = (option as any).tooltip.formatter as (params: unknown) => string;
    const html = formatter({ dataIndex: 0 });
    expect(html).toContain("Dataflow runs: 9,007,199,254,740,993");
    expect(html).toContain("Succeeded: 9,007,199,254,740,993");
    expect(html).toContain("Failed: 9,007,199,254,740,995");
    expect(html).toContain("AVG / P95 / Max: 1.25s · 2.5s · 3.75s");
  });

});
