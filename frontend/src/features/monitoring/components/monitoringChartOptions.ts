import { num } from "../../../shared/components/DataTable";
import { formatTooltipBytes, formatTooltipNumber, isMetricInteger, toApproximateNumber, type MetricValue } from "../../../shared/lib/numberFormat";
import { baseChartOption, reportChartPalette } from "../ReportChart";
import type { MonitoringFilters } from "../monitoringFilters";
import {
  HORIZONTAL_BAR_LABEL_GAP,
  HORIZONTAL_BAR_VISIBLE_ROWS,
  OVERVIEW_STATUSES,
  REPORT_CHART_GRID_BOTTOM,
  REPORT_CHART_X_ZOOM_GRID_BOTTOM
} from "./monitoringModel";
import {
  createEmptyVolumeTrendRow,
  fillMissingTrendDates,
  resolveTrendBucketKeys,
  rowLabel
} from "./monitoringTime";
import { formatCompact, formatBytesShort } from "./monitoringFormatting";
import { rowObservation } from "./monitoringObservation";
import { chartMetric, chartTooltip, firstChartParam } from "./chartTooltip";

const formatNumber = formatTooltipNumber;

export function workloadVolumeTrendOption(
  rowsByDate: Array<Record<string, string | number | null>>,
  bytesByDate: Array<Record<string, string | number | null>>,
  filters: MonitoringFilters,
  dateRange: { min?: string | null; max?: string | null },
  timezoneName: string,
  reportEffectiveGrain?: string,
  showLegend = true
) {
  const mergedByDate = mergeVolumeTrendRows(rowsByDate, bytesByDate);
  const effectiveGrain = String(mergedByDate.find((row) => row.grain)?.grain ?? reportEffectiveGrain ?? filters.grain ?? "day");
  const knownDateKeys = mergedByDate.map((row) => row.bucket || row.date).filter((date) => date && date !== "unknown");
  const dateKeys = resolveTrendBucketKeys(filters, dateRange, timezoneName, knownDateKeys, effectiveGrain);
  const mergedByDateMap = new Map(mergedByDate.map((row) => [row.bucket || row.date, row]));
  const visible = dateKeys.length > 0
    ? dateKeys.map((dateKey) => mergedByDateMap.get(dateKey) ?? createEmptyVolumeTrendRow(dateKey, effectiveGrain))
    : mergedByDate;
  if (!visible.length) return null;
  return baseChartOption({
    tooltip: {
      trigger: "axis",
      confine: true,
      formatter: chartTooltip((params) => {
        const first = firstChartParam(params);
        const index = Number(first?.dataIndex ?? 0);
        const row = visible[index] ?? { date: "" };
        const observationMetric = (label: string, field: string, valueColor: string) => {
          const observation = rowObservation(row, field);
          const coverage = observation.coverage?.replace(/ runs measured$/u, " runs");
          return { ...chartMetric(label, `${formatNumber(observation.value)}${coverage ? `; ${coverage}` : ""}`), valueColor: observation.value != null ? valueColor : undefined };
        };
        return {
          title: row.bucket || row.date || "",
          context: [row.grain ? `Grain: ${row.grain}` : "", timezoneName ? `Timezone: ${timezoneName}` : ""].filter(Boolean).join(" · ") || undefined,
          metrics: [
            observationMetric("Rows read", "rows_read", reportChartPalette.read),
            observationMetric("Rows written", "rows_written", reportChartPalette.written),
            { ...chartMetric("Lakehouse rows written", formatNumber(row.lakehouse_rows_written)), valueColor: row.lakehouse_rows_written != null ? reportChartPalette.written : undefined },
            { ...chartMetric("Lakehouse bytes added", formatTooltipBytes(row.bytes_added)), valueColor: row.bytes_added != null ? reportChartPalette.teal : undefined },
            { ...chartMetric("Lakehouse bytes removed", formatTooltipBytes(row.bytes_removed)), valueColor: row.bytes_removed != null ? reportChartPalette.failed : undefined },
          ]
        };
      })
    },
    legend: showLegend
      ? { top: 0, left: "center", itemWidth: 9, itemHeight: 9, textStyle: { fontSize: 10 } }
      : { show: false },
    grid: reportChartGrid({ left: 46, right: 52, top: showLegend ? 22 : 5, bottom: 5, containLabel: false }),
    xAxis: { type: "category", data: visible.map((row) => row.bucket || row.date), axisLabel: { fontSize: 10, hideOverlap: true }, axisTick: { show: false } },
    yAxis: [
      {
        type: "value",
        name: "rows",
        nameTextStyle: { fontSize: 9, color: reportChartPalette.muted, padding: [0, 0, 0, 26] },
        axisLabel: { fontSize: 10, formatter: (value: number) => formatCompact(value) },
        splitLine: { lineStyle: { color: reportChartPalette.grid } }
      },
      {
        type: "value",
        name: "lakehouse bytes",
        nameTextStyle: { fontSize: 9, color: reportChartPalette.muted, padding: [0, 26, 0, 0] },
        axisLabel: { fontSize: 10, formatter: (value: number) => formatBytesShort(value) },
        splitLine: { show: false }
      }
    ],
    series: [
      { name: "Rows read", type: "bar", itemStyle: { color: reportChartPalette.read, borderRadius: [3, 3, 0, 0] }, data: visible.map((row) => plotMetric(rowObservation(row, "rows_read").value)) },
      { name: "Rows written", type: "bar", itemStyle: { color: reportChartPalette.written, borderRadius: [3, 3, 0, 0] }, data: visible.map((row) => plotMetric(rowObservation(row, "rows_written").value)) },
      {
        name: "Lakehouse bytes added",
        type: "line",
        yAxisIndex: 1,
        connectNulls: false,
        showSymbol: visible.filter((row) => row.bytes_added != null).length === 1,
        showAllSymbol: false,
        symbol: "circle",
        symbolSize: 5,
        clip: false,
        z: 6,
        smooth: 0.2,
        lineStyle: { width: 1.5, color: reportChartPalette.teal },
        itemStyle: { color: "#ffffff", borderColor: reportChartPalette.teal, borderWidth: 1.3 },
        data: visible.map((row) => plotMetric(row.bytes_added))
      },
      {
        name: "Lakehouse bytes removed",
        type: "line",
        yAxisIndex: 1,
        connectNulls: false,
        showSymbol: visible.filter((row) => row.bytes_removed != null).length === 1,
        showAllSymbol: false,
        symbol: "circle",
        symbolSize: 5,
        clip: false,
        z: 6,
        smooth: 0.2,
        lineStyle: { width: 1.5, color: reportChartPalette.failed },
        itemStyle: { color: "#ffffff", borderColor: reportChartPalette.failed, borderWidth: 1.3 },
        data: visible.map((row) => plotMetric(row.bytes_removed))
      }
    ]
  });
}

export function jobStatusTrendOption(
  rows: Array<Record<string, string | number | null>>,
  filters: MonitoringFilters,
  dateRange: { min?: string | null; max?: string | null },
  timezoneName: string,
  effectiveGrain?: string
) {
  return statusTrendOption(rows, "jobs", filters, dateRange, timezoneName, effectiveGrain);
}

export function dataflowStatusTrendOption(
  rows: Array<Record<string, string | number | null>>,
  filters: MonitoringFilters,
  dateRange: { min?: string | null; max?: string | null },
  timezoneName: string,
  effectiveGrain?: string
) {
  return statusTrendOption(rows, "dataflows", filters, dateRange, timezoneName, effectiveGrain);
}

export function statusTrendOption(
  rows: Array<Record<string, string | number | null>>,
  runLabel: "jobs" | "dataflows",
  filters: MonitoringFilters,
  dateRange: { min?: string | null; max?: string | null },
  timezoneName: string,
  reportEffectiveGrain?: string
) {
  const visible = fillMissingTrendDates(rows, filters, dateRange, timezoneName, reportEffectiveGrain);
  const successRatePoints = visible.map((row) => trendLineRate(row, "success_rate", "succeeded"));
  const barSeries = OVERVIEW_STATUSES.map((status) => ({
    name: status,
    type: "bar" as const,
    stack: "runs",
    emphasis: { focus: "series" as const },
    itemStyle: { color: statusColor(status) },
    data: visible.map((row) => plotMetric(metricAt(row, status))),
    yAxisIndex: 0
  }));
  return baseChartOption({
    tooltip: {
      trigger: "axis",
      confine: true,
      formatter: chartTooltip((params) => {
        const first = firstChartParam(params);
        const index = Number(first?.dataIndex ?? 0);
        const row = visible[index] ?? {};
        const succeeded = metricAt(row, "succeeded");
        const failed = metricAt(row, "failed");
        const skipped = metricAt(row, "skipped");
        const running = metricAt(row, "running");
        const pending = metricAt(row, "pending");
        const unknown = metricAt(row, "unknown");
        const explicitTotal = metricAt(row, "total");
        const total = explicitTotal === undefined ? sumMetricValues([succeeded, failed, skipped, running, pending, unknown]) : explicitTotal;
        const totalApprox = toApproximateNumber(total);
        const successRate = trendRate(row, "success_rate", "succeeded");
        const failureRate = trendRate(row, "failure_rate", "failed");
        const rateLabel = successRate === null ? "N/A" : `${formatTooltipNumber(successRate)}%`;
        const failureLabel = failureRate === null ? "N/A" : `${formatTooltipNumber(failureRate)}%`;
        const bucketLabel = String(row.bucket ?? row.date ?? "");
        const grainLabel = row.grain ? `Grain: ${row.grain}` : "";
        const timezoneLabel = timezoneName ? `Timezone: ${timezoneName}` : "";
        const noRunLabel = totalApprox === 0 ? `No ${runLabel} ran in this bucket` : "";
        return {
          title: bucketLabel,
          context: [grainLabel, timezoneLabel].filter(Boolean).join(" · ") || undefined,
          metrics: [
            chartMetric(`Total ${runLabel}`, formatNumber(total)),
            chartMetric("Succeeded", formatNumber(succeeded), "success"),
            chartMetric("Failed", formatNumber(failed), "failed"),
            chartMetric("Skipped", formatNumber(skipped), "warning"),
            chartMetric("Running", formatNumber(running), "info"),
            chartMetric("Pending", formatNumber(pending), "info"),
            chartMetric("Unknown", formatNumber(unknown)),
            chartMetric("Success rate", rateLabel, "success"),
            chartMetric("Failure rate", failureLabel, "failed"),
          ],
          note: noRunLabel || undefined,
        };
      })
    },
    legend: { show: false },
    grid: reportChartGrid({ left: 36, right: 40, top: 5, bottom: 5, containLabel: false }),
    xAxis: { type: "category", data: visible.map((row) => rowLabel(row)), axisLabel: { fontSize: 10, hideOverlap: true }, axisTick: { show: false } },
    yAxis: [
      { type: "value", axisLabel: { fontSize: 10, formatter: (value: number) => formatCompact(value) }, splitLine: { lineStyle: { color: reportChartPalette.grid } } },
      { type: "value", min: 0, max: 100, axisLabel: { fontSize: 10, formatter: (value: number) => `${value}%` }, splitLine: { show: false } }
    ],
    series: [
      ...barSeries,
      {
        name: "success rate",
        type: "line" as const,
        yAxisIndex: 1,
        connectNulls: false,
        showSymbol: false,
        showAllSymbol: false,
        symbol: "circle",
        symbolSize: 5,
        clip: false,
        z: 6,
        smooth: 0.2,
        lineStyle: { width: 1.6, color: reportChartPalette.blue },
        itemStyle: { color: "#ffffff", borderColor: reportChartPalette.blue, borderWidth: 1.5 },
        data: successRatePoints
      }
    ]
  });
}

export function horizontalBarDataZoom(categoryCount: number, visibleRows = HORIZONTAL_BAR_VISIBLE_ROWS) {
  if (categoryCount <= visibleRows) return undefined;
  return [
    { type: "inside" as const, orient: "vertical" as const, yAxisIndex: [0], startValue: 0, endValue: visibleRows - 1, filterMode: "none" as const, zoomLock: true, moveOnMouseWheel: true, zoomOnMouseWheel: false, moveOnMouseMove: false },
    { type: "slider" as const, orient: "vertical" as const, yAxisIndex: [0], startValue: 0, endValue: visibleRows - 1, filterMode: "none" as const, zoomLock: true, width: 8, right: 2, showDataShadow: false, showDetail: false, brushSelect: false, handleSize: 0, moveHandleSize: 0, textStyle: { fontSize: 0 }, borderColor: "rgba(17, 24, 39, 0.12)", backgroundColor: "rgba(17, 24, 39, 0.06)", fillerColor: "rgba(99, 102, 241, 0.18)" }
  ];
}

export function horizontalBarSeriesSizing(categoryCount: number, visibleRows = HORIZONTAL_BAR_VISIBLE_ROWS) {
  if (categoryCount > visibleRows) return { barWidth: 14, barCategoryGap: "42%" };
  return { barMaxWidth: 22, barCategoryGap: categoryCount <= 4 ? "32%" : "38%" };
}

export function fixedHorizontalBarGrid(labelWidth: number, hasZoom: boolean, overrides: Record<string, string | number | boolean> = {}) {
  return { left: Math.min(labelWidth, 132) + HORIZONTAL_BAR_LABEL_GAP, right: hasZoom ? 18 : 10, bottom: REPORT_CHART_GRID_BOTTOM, containLabel: false, ...overrides };
}

export function reportChartGrid(overrides: Record<string, string | number | boolean> = {}) {
  return { left: 8, right: 10, top: 12, bottom: REPORT_CHART_GRID_BOTTOM, containLabel: true, ...overrides };
}

export function reportXAxisZoomGrid(hasZoom: boolean, overrides: Record<string, string | number | boolean> = {}) {
  return { ...reportChartGrid(overrides), bottom: hasZoom ? REPORT_CHART_X_ZOOM_GRID_BOTTOM : REPORT_CHART_GRID_BOTTOM };
}

export function fixedHorizontalCategoryAxis(labels: string[], labelWidth: number, overrides: Record<string, unknown> = {}) {
  return { type: "category" as const, data: labels, inverse: true, axisTick: { show: false }, axisLabel: fixedHorizontalAxisLabel(labelWidth), ...overrides };
}

export function fixedHorizontalAxisLabel(labelWidth: number, overrides: Record<string, unknown> = {}) {
  return { show: true, align: "right" as const, fontSize: 10, color: reportChartPalette.muted, margin: 4, ...overrides, overflow: "truncate" as const, width: Math.min(labelWidth, 132) };
}

export function xCategoryDataZoom(categoryCount: number, visibleColumns = 12) {
  if (categoryCount <= visibleColumns) return undefined;
  return [
    { type: "inside" as const, xAxisIndex: [0], startValue: 0, endValue: visibleColumns - 1, filterMode: "filter" as const, zoomLock: true, moveOnMouseWheel: true, zoomOnMouseWheel: false, moveOnMouseMove: false },
    { type: "slider" as const, xAxisIndex: [0], startValue: 0, endValue: visibleColumns - 1, filterMode: "filter" as const, zoomLock: true, height: 8, bottom: 2, showDataShadow: false, showDetail: false, brushSelect: false, handleSize: 0, moveHandleSize: 0, textStyle: { fontSize: 0 }, borderColor: "rgba(17, 24, 39, 0.12)", backgroundColor: "rgba(17, 24, 39, 0.06)", fillerColor: "rgba(99, 102, 241, 0.18)" }
  ];
}

export function bottomAnchoredValueXAxis(axisLabelOverrides?: { formatter?: (value: number) => string }) {
  return { type: "value" as const, position: "bottom" as const, axisLine: { onZero: false }, axisLabel: { fontSize: 10, formatter: (value: number) => formatCompact(value), ...(axisLabelOverrides ?? {}) }, splitLine: { lineStyle: { color: reportChartPalette.grid } } };
}

export function operationColor(operationType: string, index: number) {
  const palette = ["#2f7d7b", "#3d6fa8", "#8a6fd1", "#b87933", "#6f8f3a", "#a85f73", "#5c7f99", "#7a6a3a"];
  let hash = index;
  for (let i = 0; i < operationType.length; i += 1) hash = (hash * 31 + operationType.charCodeAt(i)) % palette.length;
  return palette[Math.abs(hash) % palette.length];
}

export function statusColor(status: string) {
  if (status === "succeeded") return reportChartPalette.success;
  if (status === "failed") return reportChartPalette.failed;
  if (status === "skipped") return reportChartPalette.skipped;
  if (status === "running") return reportChartPalette.running;
  if (status === "pending") return reportChartPalette.pending;
  return reportChartPalette.unknown;
}

export function lifecycleStatusItems(
  skipped: number | null | undefined,
  running: number | null | undefined,
  pending: number | null | undefined,
) {
  return ([
    ["skipped", skipped],
    ["running", running],
    ["pending", pending]
  ] as const).map(([status, value]) => ({ status, value, color: statusColor(status) }));
}

export function stageTotal(row: Record<string, string | number>) {
  return OVERVIEW_STATUSES.reduce((total, status) => total + num(row, status), 0);
}

export function trendRate(row: Record<string, string | number | null>, key: "success_rate" | "failure_rate", basis: "succeeded" | "failed") {
  const explicit = row[key];
  if (explicit !== undefined) return explicit === null ? null : toApproximateNumber(explicit);
  const succeeded = num(row, "succeeded");
  const failed = num(row, "failed");
  const executable = Number(row.executable_total ?? succeeded + failed);
  if (executable <= 0) return null;
  const numerator = basis === "succeeded" ? succeeded : failed;
  return Math.round((numerator / executable) * 10000) / 100;
}

export function trendLineRate(row: Record<string, string | number | null>, key: "success_rate" | "failure_rate", basis: "succeeded" | "failed") {
  const total = toApproximateNumber(metricAt(row, "total"));
  if (total !== null && total === 0) return 0;
  return trendRate(row, key, basis);
}

export function mergeVolumeTrendRows(
  rowsByDate: Array<Record<string, string | number | null>>,
  bytesByDate: Array<Record<string, string | number | null>> = []
) {
  type VolumeTrendBucket = Record<string, MetricValue> & {
    date: string;
    bucket: string;
    grain?: string;
    rows_read: MetricValue;
    rows_written: MetricValue;
    lakehouse_rows_written: MetricValue;
    dataflow_runs: MetricValue;
    bytes_added: MetricValue;
    bytes_removed: MetricValue;
    bytes_saved: MetricValue;
  };
  const buckets = new Map<string, VolumeTrendBucket>();
  const ensureBucket = (date: string) => {
    const existing = buckets.get(date);
    if (existing) return existing;
    const bucket: VolumeTrendBucket = { date, bucket: date, rows_read: null, rows_written: null, lakehouse_rows_written: null, dataflow_runs: null, bytes_added: null, bytes_removed: null, bytes_saved: null };
    buckets.set(date, bucket);
    return bucket;
  };
  for (const row of rowsByDate) {
    const date = rowLabel(row);
    if (date === "unknown") continue;
    const bucket = ensureBucket(date);
    Object.assign(bucket, row, { date, bucket: date });
    bucket.grain = String(row.grain ?? bucket.grain ?? "");
    bucket.rows_read = metricField(row, "rows_read");
    bucket.rows_written = metricField(row, "rows_written");
    bucket.lakehouse_rows_written = metricField(row, "lakehouse_rows_written");
    bucket.dataflow_runs = metricField(row, "dataflow_runs");
  }
  for (const row of bytesByDate) {
    const date = rowLabel(row);
    if (date === "unknown") continue;
    const bucket = ensureBucket(date);
    Object.assign(bucket, row, { date, bucket: date });
    bucket.grain = String(row.grain ?? bucket.grain ?? "");
    bucket.bytes_added = metricField(row, "bytes_added");
    bucket.bytes_removed = metricField(row, "bytes_removed");
    bucket.bytes_saved = metricField(row, "bytes_saved");
  }
  return Array.from(buckets.values()).sort((left, right) => left.date.localeCompare(right.date));
}

function metricField(row: Record<string, string | number | null>, key: string): MetricValue {
  const value = row[key];
  return typeof value === "string" || typeof value === "number" || value === null ? value : null;
}

function metricAt(row: Record<string, string | number | null>, key: string): MetricValue {
  return metricField(row, key);
}

function sumMetricValues(values: MetricValue[]): MetricValue {
  if (values.some((value) => value === null || value === undefined || value === "")) return null;
  if (values.every((value) => isMetricInteger(value))) {
    return values.reduce<bigint>((sum, value) => sum + BigInt(String(value)), 0n).toString();
  }
  const numbers = values.map((value) => toApproximateNumber(value));
  return numbers.every((value) => value !== null) ? numbers.reduce((sum, value) => sum + (value ?? 0), 0) : null;
}

/** Chart geometry uses a finite plotting number; tooltips retain the source metric text. */
function plotMetric(value: MetricValue) {
  return value === null || value === undefined || value === "" ? null : toApproximateNumber(value);
}
