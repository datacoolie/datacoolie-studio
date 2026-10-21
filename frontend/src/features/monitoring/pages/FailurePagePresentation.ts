import { monitoringDimensionLabel } from "../monitoringDimensions";
import type { MonitoringFilters } from "../monitoringFilters";
import { num } from "../../../shared/components/DataTable";
import { formatTooltipNumber, type MetricValue } from "../../../shared/lib/numberFormat";
import { baseChartOption, reportChartPalette } from "../ReportChart";
import { FAILURE_PHASES, type FailurePhaseKey } from "../components/monitoringModel";
import { resolveTrendBucketKeys, rowLabel, normalizeTrendGrain } from "../components/monitoringTime";
import {
  horizontalBarDataZoom,
  horizontalBarSeriesSizing,
  fixedHorizontalBarGrid,
  reportChartGrid,
  fixedHorizontalCategoryAxis,
} from "../components/monitoringChartOptions";
import { formatCompact } from "../components/monitoringFormatting";
import { chartMetric, chartTooltip, firstChartParam } from "../components/chartTooltip";

function metricValue(row: Record<string, unknown>, key: string): MetricValue {
  const value = row[key];
  return typeof value === "string" || typeof value === "number" || value === null ? value : undefined;
}

export const FAILURE_PHASE_LABELS: Record<FailurePhaseKey, string> = {
  source: "Source",
  transform: "Transform",
  destination: "Destination",
  overhead: "Overhead",
  unknown: "Unknown"
};

export const monitoringPhaseColors = {
  source: "#2563eb",
  transform: "#7c3aed",
  destination: "#16805c",
  overhead: "#64748b",
  unknown: "#b45309"
} as const;

export const FAILURE_PHASE_COLORS: Record<FailurePhaseKey, string> = {
  ...monitoringPhaseColors
};

export const FAILURE_TREND_COLORS = {
  dataflows: "#c24141",
  jobs: "#7c3aed"
} as const;

export function failureHorizontalBarOption(
  rows: Array<Record<string, string | number>>,
  labelKey: string,
  valueKey: string,
  _seriesName: string
) {
  const visibleRows = rows.filter((row) => num(row, valueKey) > 0);
  const zoomConfig = horizontalBarDataZoom(visibleRows.length);
  const barSizing = horizontalBarSeriesSizing(visibleRows.length);
  return failurePhaseHorizontalBarOption(visibleRows, labelKey, valueKey, zoomConfig, barSizing);
}

export function failurePhaseHorizontalBarOption(
  visibleRows: Array<Record<string, string | number>>,
  labelKey: string,
  valueKey: string,
  zoomConfig: ReturnType<typeof horizontalBarDataZoom>,
  barSizing: ReturnType<typeof horizontalBarSeriesSizing>
) {
  return baseChartOption({
    tooltip: {
      trigger: "item",
      triggerOn: "mousemove",
      axisPointer: { type: "none" },
      formatter: chartTooltip((params) => {
        const point = firstChartParam(params);
        const row = visibleRows[Number(point?.dataIndex ?? 0)] ?? {};
        const phase = failurePhaseFromSeriesName(String(point?.seriesName ?? ""));
        const phaseValue = phase ? failurePhaseMetricValue(row, phase, valueKey) : point?.value;
        return {
          title: monitoringDimensionLabel(row[labelKey]),
          metrics: [
            chartMetric(phase ? FAILURE_PHASE_LABELS[phase] : "Failures", formatTooltipNumber(phaseValue as MetricValue), phase === "unknown" ? "neutral" : "failed"),
            chartMetric("Total failures", formatTooltipNumber(metricValue(row, valueKey)), "failed"),
          ]
        };
      })
    },
    legend: { show: false },
    grid: fixedHorizontalBarGrid(96, Boolean(zoomConfig), { right: zoomConfig ? 14 : 8, top: 4 }),
    xAxis: { type: "value", axisLabel: { fontSize: 10, formatter: (value: number) => formatCompact(value) }, splitLine: { lineStyle: { color: reportChartPalette.grid } } },
    yAxis: fixedHorizontalCategoryAxis(visibleRows.map((row) => monitoringDimensionLabel(row[labelKey])), 96),
    dataZoom: zoomConfig,
    series: FAILURE_PHASES.map((phase) => ({
      name: FAILURE_PHASE_LABELS[phase],
      type: "bar",
      stack: "failures",
      ...barSizing,
      itemStyle: { color: FAILURE_PHASE_COLORS[phase], borderRadius: phase === "unknown" ? [0, 2, 2, 0] : 0 },
      data: visibleRows.map((row) => failurePhaseCount(row, phase, valueKey))
    }))
  });
}

export function failurePhaseCount(row: Record<string, string | number>, phase: FailurePhaseKey, totalKey: string) {
  if (phase !== "unknown") return num(row, phase);
  const explicitUnknown = num(row, "unknown");
  if (explicitUnknown > 0) return explicitUnknown;
  const attributed = num(row, "source") + num(row, "transform") + num(row, "destination") + num(row, "overhead");
  return Math.max(0, num(row, totalKey) - attributed);
}

function failurePhaseMetricValue(row: Record<string, unknown>, phase: FailurePhaseKey, totalKey: string): MetricValue {
  if (phase !== "unknown") return metricValue(row, phase);
  if (num(row as Record<string, string | number>, "unknown") > 0) return metricValue(row, "unknown");
  return failurePhaseCount(row as Record<string, string | number>, phase, totalKey);
}

export function failurePhaseFromSeriesName(seriesName: string): FailurePhaseKey | undefined {
  return FAILURE_PHASES.find((phase) => FAILURE_PHASE_LABELS[phase] === seriesName);
}

export function failureTrendOption(
  rows: Array<Record<string, string | number | null>>,
  filters: MonitoringFilters,
  dateRange: { min?: string | null; max?: string | null },
  timezoneName: string,
  reportEffectiveGrain?: string
) {
  const visible = fillMissingFailureTrendDates(rows, filters, dateRange, timezoneName, reportEffectiveGrain);
  const dataflowColor = FAILURE_TREND_COLORS.dataflows;
  const jobColor = FAILURE_TREND_COLORS.jobs;
  return baseChartOption({
    tooltip: {
      trigger: "axis",
      formatter: chartTooltip((params) => {
        const points = Array.isArray(params) ? params : [params];
        const first = points[0] ?? {};
        const index = Number(first?.dataIndex ?? 0);
        const row = visible[index] ?? {};
        return {
          title: String(row.bucket ?? row.date ?? ""),
          context: [row.grain ? `Grain: ${row.grain}` : "", timezoneName ? `Timezone: ${timezoneName}` : ""].filter(Boolean).join(" · ") || undefined,
          metrics: points.map((point) => {
            const seriesName = String(point.seriesName ?? "Failures");
            const value = seriesName.toLowerCase().includes("job")
              ? metricValue(row, "failed_jobs")
              : metricValue(row, "failed_dataflows") ?? metricValue(row, "failed");
            return chartMetric(seriesName, formatTooltipNumber(value), "failed");
          }),
        };
      })
    },
    grid: reportChartGrid({ left: 42, right: 42, top: 5, bottom: 5, containLabel: false }),
    xAxis: {
      type: "category",
      data: visible.map((row) => String(row.date ?? row.bucket ?? "unknown")),
      axisTick: { show: false },
      axisLabel: { fontSize: 10, color: reportChartPalette.muted }
    },
    yAxis: [
      {
        type: "value",
        name: "dataflows",
        nameTextStyle: { fontSize: 9, color: reportChartPalette.muted, padding: [0, 0, 0, 24] },
        axisLabel: { fontSize: 10, formatter: (value: number) => formatCompact(value) },
        splitLine: { lineStyle: { color: reportChartPalette.grid } }
      },
      {
        type: "value",
        name: "jobs",
        nameTextStyle: { fontSize: 9, color: reportChartPalette.muted, padding: [0, 24, 0, 0] },
        axisLabel: { fontSize: 10, formatter: (value: number) => formatCompact(value) },
        splitLine: { show: false }
      }
    ],
    legend: { show: false },
    series: [
      {
        name: "Dataflows",
        type: "line",
        smooth: true,
        showSymbol: false,
        symbolSize: 5,
        lineStyle: { color: dataflowColor, width: 2 },
        itemStyle: { color: dataflowColor },
        areaStyle: { color: "rgba(194, 65, 65, 0.08)" },
        yAxisIndex: 0,
        data: visible.map((row) => num(row, "failed_dataflows") || num(row, "failed"))
      },
      {
        name: "Jobs",
        type: "line",
        smooth: true,
        showSymbol: false,
        symbolSize: 5,
        lineStyle: { color: jobColor, width: 2 },
        itemStyle: { color: jobColor },
        yAxisIndex: 1,
        data: visible.map((row) => num(row, "failed_jobs"))
      }
    ]
  });
}

export function fillMissingFailureTrendDates(
  rows: Array<Record<string, string | number | null>>,
  filters: MonitoringFilters,
  dateRange: { min?: string | null; max?: string | null },
  timezoneName: string,
  reportEffectiveGrain?: string
) {
  const effectiveGrain = String(rows.find((row) => row.grain)?.grain ?? reportEffectiveGrain ?? filters.grain ?? "day");
  const known = rows.filter((row) => rowLabel(row) !== "unknown");
  const rowByDate = new Map<string, Record<string, string | number | null>>();
  for (const row of known) {
    const dateKey = rowLabel(row);
    if (!dateKey) continue;
    rowByDate.set(dateKey, row);
  }
  const keys = resolveTrendBucketKeys(filters, dateRange, timezoneName, Array.from(rowByDate.keys()), effectiveGrain);
  if (!keys.length) return known;
  return keys.map((dateKey) => rowByDate.get(dateKey) ?? createEmptyFailureTrendRow(dateKey, effectiveGrain));
}

export function createEmptyFailureTrendRow(dateKey: string, grain: string): Record<string, string | number> {
  return {
    date: dateKey,
    bucket: dateKey,
    grain: normalizeTrendGrain(grain),
    failed_jobs: 0,
    failed_dataflows: 0,
    failed: 0
  };
}

export function failureCategoryPhaseMatrixOption(rows: Array<Record<string, string | number>>) {
  const visibleRows = rows.filter((row) => num(row, "total") > 0);
  const zoomConfig = horizontalBarDataZoom(visibleRows.length);
  const barSizing = horizontalBarSeriesSizing(visibleRows.length);
  return baseChartOption({
    tooltip: {
      trigger: "item",
      triggerOn: "mousemove",
      axisPointer: { type: "none" },
      formatter: chartTooltip((params) => {
        const point = firstChartParam(params);
        const row = visibleRows[Number(point?.dataIndex ?? 0)] ?? {};
        const phase = failurePhaseFromSeriesName(String(point?.seriesName ?? "")) ?? "unknown";
        return {
          title: String(row.category ?? "unknown"),
          metrics: [
            chartMetric(FAILURE_PHASE_LABELS[phase], formatTooltipNumber(failurePhaseMetricValue(row, phase, "total")), "failed"),
            chartMetric("Total failures", formatTooltipNumber(metricValue(row, "total")), "failed"),
          ]
        };
      })
    },
    legend: { show: false },
    grid: fixedHorizontalBarGrid(106, Boolean(zoomConfig), { right: zoomConfig ? 14 : 8, top: 4 }),
    xAxis: { type: "value", axisLabel: { fontSize: 10, formatter: (value: number) => formatCompact(value) }, splitLine: { lineStyle: { color: reportChartPalette.grid } } },
    yAxis: fixedHorizontalCategoryAxis(visibleRows.map((row) => String(row.category ?? "unknown")), 106),
    dataZoom: zoomConfig,
    series: FAILURE_PHASES.map((phase) => ({
      name: FAILURE_PHASE_LABELS[phase],
      type: "bar",
      stack: "failures",
      ...barSizing,
      itemStyle: { color: FAILURE_PHASE_COLORS[phase], borderRadius: phase === "unknown" ? [0, 2, 2, 0] : 0 },
      data: visibleRows.map((row) => failurePhaseCount(row, phase, "total"))
    }))
  });
}
