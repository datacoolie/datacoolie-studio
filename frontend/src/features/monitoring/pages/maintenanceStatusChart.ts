import type { EChartsOption } from "echarts";
import {
  baseChartOption,
  bottomAnchoredValueXAxis,
  fixedHorizontalAxisLabel,
  fixedHorizontalBarGrid,
  formatCompact,
  formatTooltipNumber,
  horizontalBarDataZoom,
  horizontalBarSeriesSizing,
  metricNumber,
  metricValue,
  reportChartPalette,
} from "../components/monitoringPresentation";
import { chartMetric, chartTooltip, firstChartParam } from "../components/chartTooltip";

const MAINTENANCE_STATUSES = [
  { key: "succeeded", label: "Succeeded", color: reportChartPalette.success, detail: "Latest maintenance succeeded with a non-dry-run job and a successful destination phase. No-change successes count." },
  { key: "failed", label: "Failed", color: reportChartPalette.failed, detail: "Latest maintenance run failed. Earlier successful maintenance does not change this latest outcome." },
  { key: "skipped", label: "Skipped", color: reportChartPalette.skipped, detail: "Latest maintenance run was skipped." },
  { key: "running", label: "Running", color: reportChartPalette.running, detail: "Latest maintenance run is recorded as running." },
  { key: "pending", label: "Pending", color: reportChartPalette.pending, detail: "Latest maintenance run is recorded as pending." },
  { key: "dry_run", label: "Dry run", color: reportChartPalette.unknown, detail: "Latest maintenance belongs to an explicit dry-run job; it does not confirm actual maintenance." },
  { key: "unknown", label: "Unknown", color: reportChartPalette.unknown, detail: "Latest maintenance has an unknown outcome, or a reported success lacks the evidence required to confirm maintenance." },
  { key: "not_recorded", label: "No maintenance recorded", color: reportChartPalette.unknown, detail: "No maintenance is recorded in the current filters. This does not establish a storage issue or an overdue task." },
] as const;

export function maintenanceStatusOption(rows: Array<Record<string, unknown>>): EChartsOption {
  const byStatus = new Map(rows.map((row) => [String(row.status), row]));
  const visible = MAINTENANCE_STATUSES.flatMap((status) => {
    const row = byStatus.get(status.key);
    return row && metricNumber(metricValue(row, "count")) > 0 ? [{ ...status, row }] : [];
  });
  if (!visible.length) return baseChartOption({
    graphic: { type: "text", left: "center", top: "middle", style: { text: "No lakehouse destinations\nin the current filters.", fill: reportChartPalette.muted, fontSize: 12, fontWeight: 600, align: "center" } },
  });
  const zoom = horizontalBarDataZoom(visible.length);
  return baseChartOption({
    tooltip: {
      trigger: "item",
      formatter: chartTooltip((params) => {
        const item = visible[firstChartParam(params)?.dataIndex ?? 0];
        if (!item) return { body: "" };
        const count = metricValue(item.row, "count");
        const population = metricValue(item.row, "population_count");
        const percentage = (metricNumber(count) / metricNumber(population) * 100).toFixed(1);
        return {
          title: item.label,
          metrics: [chartMetric("Destinations", `${formatTooltipNumber(count)} / ${formatTooltipNumber(population)} (${percentage}%)`, item.key === "failed" ? "failed" : item.key === "succeeded" ? "success" : "neutral")],
          note: `${item.detail} Each Delta/Iceberg destination is counted once, by its latest maintenance within the current filters.`,
        };
      }),
    },
    grid: fixedHorizontalBarGrid(132, Boolean(zoom), { top: 8, right: zoom ? 24 : 10 }),
    xAxis: { ...bottomAnchoredValueXAxis(), min: 0, minInterval: 1, max: (value: { max: number }) => Math.max(1, Math.ceil(value.max * 1.16)) },
    yAxis: { type: "category", inverse: true, data: visible.map((item) => item.label), axisTick: { show: false }, axisLabel: fixedHorizontalAxisLabel(132, { interval: 0, margin: 8 }) },
    dataZoom: zoom,
    series: [{
      name: "Destinations",
      type: "bar",
      ...horizontalBarSeriesSizing(visible.length),
      label: { show: true, position: "right", color: reportChartPalette.text, fontSize: 10, formatter: (params: { dataIndex?: number }) => formatCompact(metricValue(visible[params.dataIndex ?? 0]?.row ?? {}, "count")) },
      labelLayout: { hideOverlap: true },
      data: visible.map((item) => ({ value: metricNumber(metricValue(item.row, "count")), itemStyle: { color: item.color, borderRadius: [0, 3, 3, 0] } })),
    }],
  });
}
