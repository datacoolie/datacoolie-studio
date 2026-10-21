import { monitoringDimensionLabel } from "../monitoringDimensions";
import { formatSeconds, num } from "../../../shared/components/DataTable";
import { baseChartOption, reportChartPalette } from "../ReportChart";
import { OVERVIEW_STATUSES, REPORT_CHART_X_ZOOM_GRID_BOTTOM } from "../components/monitoringModel";
import {
  horizontalBarDataZoom,
  horizontalBarSeriesSizing,
  fixedHorizontalBarGrid,
  reportChartGrid,
  reportXAxisZoomGrid,
  fixedHorizontalAxisLabel,
  xCategoryDataZoom,
  operationColor,
  statusColor,
  stageTotal,
} from "../components/monitoringChartOptions";
import { formatCompact, shortIdentifier } from "../components/monitoringFormatting";
import { formatTooltipNumber, type MetricValue } from "../../../shared/lib/numberFormat";
import { chartDurationMetric, chartMetric, chartTooltip, firstChartParam } from "../components/chartTooltip";

function metricValue(row: Record<string, unknown>, key: string): MetricValue {
  const value = row[key];
  return typeof value === "string" || typeof value === "number" || value === null ? value : undefined;
}

export function jobStageHealthOption(rows: Array<Record<string, string | number>>) {
  const labels = rows.map((row) => monitoringDimensionLabel(row.stage));
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
          title: `Stage: ${monitoringDimensionLabel(row.stage)}`,
          metrics: [
            chartMetric("Touched jobs", formatTooltipNumber(metricValue(row, "touched_jobs") ?? stageTotal(row))),
            chartMetric("Succeeded", formatTooltipNumber(metricValue(row, "succeeded")), "success"),
            chartMetric("Failed", formatTooltipNumber(metricValue(row, "failed")), "failed"),
            chartMetric("Skipped", formatTooltipNumber(metricValue(row, "skipped")), "warning"),
            chartMetric("Running", formatTooltipNumber(metricValue(row, "running")), "info"),
            chartMetric("Pending", formatTooltipNumber(metricValue(row, "pending")), "info"),
            chartMetric("Unknown", formatTooltipNumber(metricValue(row, "unknown"))),
          ]
        };
      })
    },
    legend: { show: false },
    grid: fixedHorizontalBarGrid(116, Boolean(zoomConfig), { right: zoomConfig ? 24 : 10, top: 4 }),
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
      axisLabel: fixedHorizontalAxisLabel(104)
    },
    dataZoom: zoomConfig,
    series: OVERVIEW_STATUSES.map((status) => ({
      name: status,
      type: "bar" as const,
      stack: "job-stage-status",
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

export function workloadEfficiencyOperationTypes(rows: Array<Record<string, string | number | null>>) {
  return Array.from(new Set(
    rows
      .filter((row) => num(row, "duration_seconds") > 0)
      .map((row) => String(row.operation_type ?? "unknown"))
  )).sort((left, right) => left.localeCompare(right));
}

export function jobWorkloadEfficiencyOption(
  rows: Array<Record<string, string | number | null>>
) {
  const maxWorkloadByMetric = new Map<string, number>();
  for (const row of rows) {
    const metric = String(row.workload_size_metric ?? "rows_per_second");
    maxWorkloadByMetric.set(metric, Math.max(maxWorkloadByMetric.get(metric) ?? 1, num(row, "workload_size") || 1));
  }
  const maxChildren = Math.max(...rows.map((row) => num(row, "child_dataflow_count") || 0), 1);
  const operationTypes = workloadEfficiencyOperationTypes(rows);
  const operationColors = new Map(operationTypes.map((operationType, operationIndex) => [
    operationType,
    operationColor(operationType, operationIndex)
  ]));
  return baseChartOption({
    tooltip: {
      trigger: "item",
      confine: true,
      formatter: chartTooltip((params) => {
        const point = firstChartParam(params);
        const data = point.data && typeof point.data === "object" ? point.data as Record<string, unknown> : {};
        const row = data.row && typeof data.row === "object" ? data.row as Record<string, unknown> : {};
        const usesBytes = row.workload_size_metric === "bytes_per_second";
        const operationType = String(row.operation_type ?? "unknown");
        return {
          title: shortIdentifier(row.job_id),
          metrics: [
            chartMetric("Job ID", row.job_id ?? "-"),
            chartMetric("Status", row.status ?? "unknown", row.status === "failed" ? "failed" : row.status === "succeeded" ? "success" : "neutral"),
            { ...chartMetric("Dataflow operation", monitoringDimensionLabel(operationType)), valueColor: operationColors.get(operationType) },
            chartMetric("Dataflow runs", formatTooltipNumber(metricValue(row, "child_dataflow_count"))),
            chartMetric("Total duration", chartDurationMetric(row.duration_seconds, { compactOnly: true })),
            chartMetric("Rows read / written", `${formatTooltipNumber(metricValue(row, "rows_read"))} / ${formatTooltipNumber(metricValue(row, "rows_written"))}`),
            chartMetric(usesBytes ? "Workload rate (bytes/s)" : "Workload rate (rows/s)", formatTooltipNumber(metricValue(row, "workload_size"))),
            ...(usesBytes ? [chartMetric("Bytes added / removed", `${formatTooltipNumber(metricValue(row, "bytes_added"))} / ${formatTooltipNumber(metricValue(row, "bytes_removed"))}`)] : []),
            chartMetric("Failed / skipped children", `${formatTooltipNumber(metricValue(row, "failed_child_dataflows"))} / ${formatTooltipNumber(metricValue(row, "skipped_child_dataflows"))}`),
            chartMetric("Runtime", [row.platform_name, row.engine_name, row.metadata_provider_name].map(monitoringDimensionLabel).join(" / ")),
          ],
          note: usesBytes
            ? "Point size is scaled by bytes processed per second; compare with other maintenance operations."
            : "Point size is scaled by rows read per second; compare with other pipeline operations."
        };
      })
    },
    legend: { show: false },
    grid: reportChartGrid({ left: 8, right: 8, top: 4, bottom: 0 }),
    xAxis: {
      type: "value",
      name: "dataflow runs",
      max: Math.ceil(maxChildren * 1.08),
      nameTextStyle: { fontSize: 9, color: reportChartPalette.muted },
      axisLabel: { fontSize: 10, formatter: (value: number) => formatCompact(value) },
      splitLine: { lineStyle: { color: reportChartPalette.grid } }
    },
    yAxis: {
      type: "value",
      name: "duration",
      nameTextStyle: { fontSize: 9, color: reportChartPalette.muted },
      axisLabel: { fontSize: 10, formatter: (value: number) => formatSeconds(value) },
      splitLine: { lineStyle: { color: reportChartPalette.grid } }
    },
    series: operationTypes.map((operationType) => {
      const operationRows = rows.filter((row) => String(row.operation_type ?? "unknown") === operationType);
      return {
        name: monitoringDimensionLabel(operationType),
        type: "scatter" as const,
        data: operationRows.map((row) => ({
          value: [num(row, "child_dataflow_count"), num(row, "duration_seconds"), num(row, "workload_size")],
          row
        })),
        symbolSize: (_value: unknown, params: any) => {
          const row = params?.data?.row ?? {};
          const metric = String(row.workload_size_metric ?? "rows_per_second");
          const workload = num(row, "workload_size") || 1;
          if (!workload || workload <= 1) return 6;
          return Math.max(6, Math.min(24, 6 + Math.sqrt(workload / (maxWorkloadByMetric.get(metric) ?? 1)) * 18));
        },
        itemStyle: {
          color: operationColors.get(operationType),
          opacity: 0.78,
          borderColor: "#ffffff",
          borderWidth: 1
        },
        emphasis: { focus: "self" as const }
      };
    })
  });
}

export function childFanoutDistributionOption(rows: Array<Record<string, string | number | null>>) {
  const zoomConfig = xCategoryDataZoom(rows.length, 16);
  return baseChartOption({
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "shadow" },
      confine: true,
      formatter: chartTooltip((params) => {
        const row = rows[Number(firstChartParam(params)?.dataIndex ?? 0)] ?? {};
        return {
          title: String(row.bin_label ?? "unknown"),
          metrics: [
            chartMetric("Jobs", formatTooltipNumber(metricValue(row, "jobs"))),
            chartMetric("Bin range", `${formatTooltipNumber(metricValue(row, "bin_start"))} to ${formatTooltipNumber(metricValue(row, "bin_end"))} dataflows`),
            chartMetric("Succeeded", formatTooltipNumber(metricValue(row, "succeeded")), "success"),
            chartMetric("Failed", formatTooltipNumber(metricValue(row, "failed")), "failed"),
            chartMetric("Skipped", formatTooltipNumber(metricValue(row, "skipped")), "warning"),
            chartMetric("Running / pending", `${formatTooltipNumber(metricValue(row, "running"))} / ${formatTooltipNumber(metricValue(row, "pending"))}`),
          ]
        };
      })
    },
    legend: { show: false },
    grid: {
      ...reportXAxisZoomGrid(Boolean(zoomConfig), { left: 8, right: 8, top: 16 }),
      bottom: zoomConfig ? REPORT_CHART_X_ZOOM_GRID_BOTTOM : 0,
      containLabel: false
    },
    xAxis: {
      type: "category",
      data: rows.map((row) => String(row.bin_label ?? "")),
      axisTick: { show: false },
      axisLabel: { fontSize: 9, color: reportChartPalette.muted, interval: 0, rotate: rows.length > 12 ? 28 : 0 }
    },
    yAxis: {
      type: "value",
      axisLabel: { fontSize: 10, formatter: (value: number) => formatCompact(value) },
      splitLine: { lineStyle: { color: reportChartPalette.grid } }
    },
    dataZoom: zoomConfig,
    series: [
      {
        name: "Jobs",
        type: "bar" as const,
        barCategoryGap: "0%",
        barGap: "0%",
        itemStyle: { color: reportChartPalette.blue, borderColor: "#ffffff", borderWidth: 0.8 },
        label: {
          show: true,
          position: "top" as const,
          color: reportChartPalette.text,
          fontSize: 9,
          formatter: (params: any) => {
            const value = Number(params?.value ?? 0);
            return value > 0 ? formatCompact(value) : "";
          }
        },
        data: rows.map((row) => num(row, "jobs"))
      }
    ]
  });
}
