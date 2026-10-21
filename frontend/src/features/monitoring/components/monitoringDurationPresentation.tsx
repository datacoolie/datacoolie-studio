import { monitoringDimensionLabel } from "../monitoringDimensions";
import { formatSeconds, num } from "../../../shared/components/DataTable";
import { ReportChart, baseChartOption, reportChartPalette } from "../ReportChart";
import { fixedHorizontalBarGrid, horizontalBarDataZoom, statusColor } from "./monitoringChartOptions";
import { chartDurationMetric, chartMetric, chartTooltip, firstChartParam } from "./chartTooltip";
import { formatTooltipNumber, toApproximateNumber, type MetricValue } from "../../../shared/lib/numberFormat";

export function DurationDistributionBoxPlot({
  rows,
  labelKey,
  emptyText,
  entityKind = "dataflow"
}: {
  rows: Array<Record<string, unknown>>;
  labelKey: string;
  emptyText: string;
  entityKind?: "dataflow" | "job";
}) {
  const visible = rows.filter((row) => num(row, "count") > 0);
  if (!visible.length) return <div className="table-empty">{emptyText}</div>;
  return (
    <div className="monitoring-job-chart-fill">
      <ReportChart option={durationDistributionBoxOption(visible, labelKey, entityKind)} height="100%" wheelDataZoomStep={1} />
    </div>
  );
}

export function durationDistributionBoxOption(
  rows: Array<Record<string, unknown>>,
  labelKey: string,
  entityKind: "dataflow" | "job" = "dataflow"
) {
  const visibleRows = 8;
  const zoomConfig = horizontalBarDataZoom(rows.length, visibleRows);
  const outlierPoints = rows.flatMap((row, rowIndex) => durationOutlierPoints(row, rowIndex));
  const durationMetric = (value: unknown) => chartDurationMetric(value, { compactOnly: true });
  return baseChartOption({
    tooltip: {
      trigger: "item",
      confine: true,
      formatter: chartTooltip((params) => {
        const row = rows[Number(firstChartParam(params)?.dataIndex ?? 0)] ?? {};
        const label = monitoringDimensionLabel(row[labelKey] ?? row.group);
        const operationMix = row.operation_mix ? `Operation mix: ${row.operation_mix}` : "";
        return {
          title: label,
          metrics: [
            chartMetric(`${entityKind === "job" ? "Job" : "Dataflow"} runs`, formatTooltipNumber(row.count as MetricValue)),
            chartMetric("Outliers", formatTooltipNumber(row.outlier_count as MetricValue), "warning"),
            chartMetric("Min", durationMetric(row.min_duration_seconds)),
            chartMetric("Q1", durationMetric(row.q1_duration_seconds)),
            chartMetric("Median", durationMetric(row.p50_duration_seconds)),
            chartMetric("Q3", durationMetric(row.q3_duration_seconds)),
            chartMetric("Max", durationMetric(row.max_duration_seconds)),
            chartMetric("Avg / P95", `${durationMetric(row.avg_duration_seconds)} / ${durationMetric(row.p95_duration_seconds)}`),
            { ...chartMetric("Succeeded", formatTooltipNumber(row.succeeded as MetricValue), "success"), valueColor: statusColor("succeeded") },
            { ...chartMetric("Failed", formatTooltipNumber(row.failed as MetricValue), "failed"), valueColor: statusColor("failed") },
            { ...chartMetric("Skipped", formatTooltipNumber(row.skipped as MetricValue), "warning"), valueColor: statusColor("skipped") },
          ],
          note: operationMix || undefined,
        };
      })
    },
    grid: fixedHorizontalBarGrid(96, Boolean(zoomConfig), { left: 112, right: zoomConfig ? 24 : 10, top: 6 }),
    dataZoom: zoomConfig,
    xAxis: {
      type: "value",
      axisTick: { show: false },
      axisLabel: { fontSize: 10, formatter: (value: number) => formatSeconds(value) },
      splitLine: { lineStyle: { color: reportChartPalette.grid } }
    },
    yAxis: {
      type: "category",
      data: rows.map((row) => monitoringDimensionLabel(row[labelKey] ?? row.group)),
      inverse: true,
      axisTick: { show: false },
      axisLine: {
        show: true,
        lineStyle: { color: "#d9e1ea", width: 1 }
      },
      axisLabel: {
        align: "right" as const,
        fontSize: 10,
        width: 102,
        overflow: "truncate" as const,
        margin: 8,
        color: reportChartPalette.muted
      }
    },
    series: [
      {
        name: "Duration",
        type: "boxplot" as const,
        itemStyle: {
          color: "rgba(61, 111, 168, 0.18)",
          borderColor: reportChartPalette.blue,
          borderWidth: 1.4
        },
        data: rows.map((row) => [
          num(row, "whisker_min_duration_seconds"),
          num(row, "q1_duration_seconds"),
          num(row, "p50_duration_seconds"),
          num(row, "q3_duration_seconds"),
          num(row, "whisker_max_duration_seconds")
        ])
      },
      {
        name: "Outliers",
        type: "scatter" as const,
        symbolSize: 5,
        itemStyle: {
          color: reportChartPalette.amber,
          borderColor: "#ffffff",
          borderWidth: 1
        },
        tooltip: {
          formatter: chartTooltip((params) => {
            const point = firstChartParam(params);
            const pointData = point.data && typeof point.data === "object" ? point.data as Record<string, unknown> : {};
            const meta = pointData.meta && typeof pointData.meta === "object" ? pointData.meta as Record<string, unknown> : {};
            if (entityKind === "job") {
              const runtime = [meta.engine_name, meta.metadata_provider_name, meta.platform_name]
                .map(monitoringDimensionLabel)
                .join(" / ");
              return {
                title: `Outlier ${meta.pointIndex === undefined ? "" : `#${Number(meta.pointIndex) + 1}`}`.trim(),
                metrics: [
                  chartMetric("Duration", durationMetric(meta.duration_seconds)),
                  { ...chartMetric("Status", meta.status ?? "-", meta.status === "failed" ? "failed" : meta.status === "succeeded" ? "success" : "neutral"), valueColor: statusColor(String(meta.status ?? "unknown")) },
                  chartMetric("Runtime", runtime || "-"),
                ],
              };
            }
            return {
              title: monitoringDimensionLabel(meta.stage ?? meta.group),
              metrics: [
                chartMetric("Duration", durationMetric(meta.duration_seconds)),
                chartMetric("Dataflow", meta.dataflow_name ?? "unknown"),
                chartMetric("Run", meta.dataflow_run_id ?? "-"),
                { ...chartMetric("Status", meta.status ?? "-", meta.status === "failed" ? "failed" : meta.status === "succeeded" ? "success" : "neutral"), valueColor: statusColor(String(meta.status ?? "unknown")) },
                chartMetric("Operation", monitoringDimensionLabel(meta.operation_type)),
              ],
            };
          })
        },
        data: outlierPoints as any
      }
    ]
  });
}

export function durationOutlierPoints(row: Record<string, unknown>, rowIndex: number) {
  const rawOutliers = Array.isArray(row.outliers) ? row.outliers : [];
  const stage = String(row.stage ?? row.group ?? row.operation_type ?? "unknown");
  return rawOutliers
    .map((item, outlierIndex) => {
      const normalized = Array.isArray(item) ? {
        duration_seconds: item[0],
        dataflow_name: item[1],
        dataflow_run_id: item[2],
        status: item[3],
        operation_type: item[4],
        engine_name: item[5],
        metadata_provider_name: item[6],
        platform_name: item[7],
      } : item && typeof item === "object" ? item as Record<string, unknown> : null;
      return normalized ? { item: normalized, outlierIndex } : null;
    })
    .filter((entry): entry is { item: Record<string, unknown>; outlierIndex: number } => Boolean(entry))
    .map(({ item, outlierIndex }) => ({
      value: [toApproximateNumber(item.duration_seconds as MetricValue), rowIndex] as [number | null, number],
      meta: {
        ...item,
        stage,
        group: String(row.group ?? stage),
        pointIndex: outlierIndex,
        chartPointId: `${stage}:${rowIndex}:${outlierIndex}`,
      }
    }));
}
