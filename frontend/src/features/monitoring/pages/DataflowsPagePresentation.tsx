import { Help } from "../../../shared/components/Help";
import { monitoringDimensionLabel, monitoringRawDimensionLabel } from "../monitoringDimensions";

import { LineageFormatIcon } from "../../../shared/components/FormatIcon";
import { num } from "../../../shared/components/DataTable";
import { assetIconKind } from "../../../shared/formatPresentation";
import { monitoringConnectionStageClass } from "../monitoringStagePresentation";
import { baseChartOption, reportChartPalette } from "../ReportChart";
import { OVERVIEW_STATUSES } from "../components/monitoringModel";
import {
  horizontalBarDataZoom,
  horizontalBarSeriesSizing,
  fixedHorizontalBarGrid,
  fixedHorizontalAxisLabel,
  statusColor,
} from "../components/monitoringChartOptions";
import { formatCompact, formatPercent } from "../components/monitoringFormatting";
import { formatTooltipNumber, type MetricValue } from "../../../shared/lib/numberFormat";
import { chartDurationMetric, chartMetric, chartTooltip, firstChartParam } from "../components/chartTooltip";

function metricValue(row: Record<string, unknown>, key: string): MetricValue {
  const value = row[key];
  return typeof value === "string" || typeof value === "number" || value === null ? value : undefined;
}

export function dataflowNameStatusHealthOption(rows: Array<Record<string, string | number | null>>) {
  const labels = rows.map((row) => String(row.dataflow_name ?? "unknown"));
  const zoomConfig = horizontalBarDataZoom(labels.length);
  const barSizing = horizontalBarSeriesSizing(labels.length);
  return baseChartOption({
    tooltip: {
      trigger: "item",
      triggerOn: "mousemove",
      confine: false,
      appendToBody: true,
      formatter: chartTooltip((params) => {
        const row = rows[Number(firstChartParam(params)?.dataIndex ?? 0)] ?? {};
        return {
          title: String(row.dataflow_name ?? "unknown"),
          context: `${monitoringDimensionLabel(row.stage)} · ${monitoringDimensionLabel(row.operation_type)}`,
          metrics: [
            chartMetric("Dataflow runs", formatTooltipNumber(metricValue(row, "runs"))),
            chartMetric("Success rate", `${formatPercent(metricValue(row, "success_rate"))} success`),
            { ...chartMetric("Succeeded", formatTooltipNumber(metricValue(row, "succeeded"))), valueColor: statusColor("succeeded") },
            { ...chartMetric("Failed", formatTooltipNumber(metricValue(row, "failed"))), valueColor: statusColor("failed") },
            { ...chartMetric("Skipped", formatTooltipNumber(metricValue(row, "skipped"))), valueColor: statusColor("skipped") },
            { ...chartMetric("Running", formatTooltipNumber(metricValue(row, "running"))), valueColor: statusColor("running") },
            { ...chartMetric("Pending", formatTooltipNumber(metricValue(row, "pending"))), valueColor: statusColor("pending") },
            { ...chartMetric("Unknown", formatTooltipNumber(metricValue(row, "unknown"))), valueColor: statusColor("unknown") },
            chartMetric("AVG / P95 / Max", `${chartDurationMetric(row.avg_duration_seconds, { compactOnly: true })} · ${chartDurationMetric(row.p95_duration_seconds, { compactOnly: true })} · ${chartDurationMetric(row.max_duration_seconds, { compactOnly: true })}`),
            chartMetric("Rows read / written", `${formatTooltipNumber(metricValue(row, "rows_read"))} / ${formatTooltipNumber(metricValue(row, "rows_written"))}`),
          ],
          note: `${monitoringRawDimensionLabel(row.source_name)} -> ${monitoringRawDimensionLabel(row.destination_name)}`,
        };
      })
    },
    legend: { show: false },
    grid: fixedHorizontalBarGrid(128, Boolean(zoomConfig), { right: zoomConfig ? 24 : 10, top: 4 }),
    xAxis: {
      type: "value",
      min: 0,
      minInterval: 1,
      axisLabel: { fontSize: 9, margin: 3, formatter: (value: number) => formatCompact(value) },
      axisTick: { show: false },
      axisLine: { lineStyle: { color: reportChartPalette.grid } },
      splitLine: { lineStyle: { color: reportChartPalette.grid } }
    },
    yAxis: {
      type: "category",
      data: labels,
      inverse: true,
      axisTick: { show: false },
      axisLine: {
        show: true,
        lineStyle: { color: "#d9e1ea", width: 1 }
      },
      axisLabel: fixedHorizontalAxisLabel(116)
    },
    dataZoom: zoomConfig,
    series: OVERVIEW_STATUSES.map((status) => ({
      name: status,
      type: "bar" as const,
      stack: "dataflow-name-status",
      ...barSizing,
      emphasis: { focus: "series" as const },
      itemStyle: { color: statusColor(status), borderRadius: 2 },
      label: {
        show: true,
        position: "inside" as const,
        color: "#ffffff",
        fontSize: 9,
        formatter: (params: any) => {
          const value = Number(params?.value ?? 0);
          return value > 0 ? formatCompact(value) : "";
        }
      },
      data: rows.map((row) => num(row, status))
    }))
  });
}

export function EndpointRouteNode({ endpoint }: { endpoint: { locator: string; connection: string; connectionType: string; format: string } }) {
  const stageClass = monitoringConnectionStageClass(endpoint.connection);
  return (
    <Help as="span" className={["dataflow-route-endpoint", "is-connection-only", stageClass].filter(Boolean).join(" ")} title={`${endpoint.connection}\n${endpoint.connectionType}${endpoint.format ? `\n${endpoint.format}` : ""}`}>
      <span className="dataflow-route-endpoint-icon" aria-hidden="true">
        {endpoint.format ? <LineageFormatIcon kind={assetIconKind(endpoint.format)} label={endpoint.format} size={18} /> : null}
      </span>
      <span className="dataflow-route-endpoint-text">
        <strong>{endpoint.connection}</strong>
        <small>{endpoint.connectionType}</small>
      </span>
    </Help>
  );
}
