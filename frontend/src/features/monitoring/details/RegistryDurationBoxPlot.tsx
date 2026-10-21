import { ReportChart, baseChartOption, reportChartPalette } from "../ReportChart";
import { formatSeconds } from "../../../shared/components/DataTable";
import { Help } from "../../../shared/components/Help";
import { chartDurationMetric, chartMetric, chartTooltip } from "../components/chartTooltip";

export function RegistryDurationBoxPlot({ row }: { row: Record<string, unknown> }) {
  const count = Number(row.performance_duration_measured ?? 0);
  const eligible = Number(row.performance_timing_eligible ?? 0);
  const fields = ["min", "q1", "p50", "q3", "max"] as const;
  const values = fields.map((field) => row[`performance_${field}_duration_seconds`]);
  const available = count > 0 && values.every((value) => value != null && Number.isFinite(Number(value)));
  const durationContent = {
    title: "Duration distribution in the current filters",
    metrics: [
      chartMetric("Measured durations", `${count} / ${eligible} succeeded or failed runs`),
      ...fields.map((field, index) => chartMetric(field === "p50" ? "Median" : field.toUpperCase(), available ? chartDurationMetric(values[index]) : "Unavailable")),
      chartMetric("Avg / P95", `${chartDurationMetric(row.performance_avg_duration_seconds)} / ${chartDurationMetric(row.performance_p95_duration_seconds)}`),
    ],
    note: "Whiskers show min and max; the box spans Q1 to Q3.",
  };
  return (
    <div className="monitoring-registry-duration-distribution">
      <h5><Help as="span" title="Duration distribution" content={durationContent}>Duration distribution</Help></h5>
      <small>{count} / {eligible} runs measured</small>
      {available ? <ReportChart height={90} option={baseChartOption({
        animation: false,
        tooltip: { trigger: "item", formatter: chartTooltip(() => durationContent) },
        grid: { left: 4, right: 12, top: 4, bottom: 22 },
        xAxis: { type: "value", min: 0, splitNumber: 3, axisLabel: { fontSize: 10, color: reportChartPalette.muted, formatter: (value: number) => formatSeconds(value), hideOverlap: true }, splitLine: { lineStyle: { color: reportChartPalette.grid } } },
        yAxis: { type: "category", data: ["Duration"], show: false },
        series: [{ name: "Duration", type: "boxplot", boxWidth: [16, 24], itemStyle: { color: "rgba(61, 111, 168, 0.18)", borderColor: reportChartPalette.blue, borderWidth: 1.4 }, data: [values.map(Number)] }],
      })} /> : <span>Duration unavailable</span>}
    </div>
  );
}
