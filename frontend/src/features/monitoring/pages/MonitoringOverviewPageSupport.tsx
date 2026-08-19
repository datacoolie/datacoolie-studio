import type { JobRecord, MonitoringRecord, MonitoringReport } from "../../../shared/api/domainTypes";
import { useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties, PointerEvent as ReactPointerEvent, ReactNode } from "react";
import { createPortal } from "react-dom";
import type { MonitoringFilters, MonitoringTabKey } from "../monitoringFilters";
import {
  BarList,
  DataTable,
  MetricGrid,
  Panel,
  ScatterPlot,
  StatusCell,
  type TableSort,
  formatBytes,
  formatNumber,
  formatSeconds,
  num
} from "../MonitoringCharts";
import { ReportChart, baseChartOption, reportChartPalette } from "../ReportChart";
import { formatTimestampForDisplay } from "../../../shared/time";
import { LineageFormatIcon } from "../../lineage/components/LineageFormatIcon";
import { assetIconKind } from "../../lineage/model/presentation";
import { CompactNumberValue, DetailMetric, MonitoringChartLegend, OVERVIEW_STATUSES, bottomAnchoredValueXAxis, fixedHorizontalBarGrid, fixedHorizontalCategoryAxis, formatCompact, formatPercent, horizontalBarDataZoom, horizontalBarSeriesSizing, statusColor, workloadVolumeTrendOption } from "../components/monitoringPrimitives";

export function FailureBreakdownDetail({ jobFailed, flowFailed }: { jobFailed: number; flowFailed: number }) {
  return (
    <span className="health-failure-detail">
      <DetailMetric label="job failed" value={<CompactNumberValue value={jobFailed} />} tone="bad" />
      <span className="separator"> · </span>
      <DetailMetric label="flow failed" value={<CompactNumberValue value={flowFailed} />} tone="bad" />
    </span>
  );
}

export function RateBreakdownDetail({ failedRate, windowFailedRate }: { failedRate: number; windowFailedRate: number }) {
  return (
    <span className="health-rate-detail">
      <DetailMetric label="failed" value={formatPercent(failedRate)} tone="bad" />
      <span className="separator"> · </span>
      <DetailMetric label="7d failed" value={formatPercent(windowFailedRate)} tone="bad" />
    </span>
  );
}

export function healthReasonSummary(reasons: string[]) {
  const items = reasons.filter((reason) => String(reason || "").trim().length > 0);
  if (!items.length) {
    return {
      primary: "No immediate monitoring issues detected.",
      additionalCount: 0
    };
  }
  return {
    primary: items[0],
    additionalCount: Math.max(0, items.length - 1)
  };
}

export function healthReasonsTooltip(reasons: string[]) {
  const items = reasons.filter((reason) => String(reason || "").trim().length > 0);
  if (!items.length) return "No immediate monitoring issues detected.";
  return items.map((reason, index) => `${index + 1}. ${reason}`).join("\n");
}

export function attentionQueueRuleTooltip() {
  return [
    "Value source:",
    "- Signals roll up actionable current-filter health evidence from Jobs, Dataflows, Failures, Performance, Maintenance, Freshness, and Diagnostics.",
    "Display condition:",
    "- Recent failed jobs/dataflows: last 3d => bad, else last 7d => warning.",
    "- Repeated failure: top failing dataflow has >= 3 failed runs.",
    "- No log evidence: warning when no monitoring logs are found in current filters.",
    "- Stale logs: warning when latest log age > 7 days.",
    "- Performance pressure uses the same P95/P50 thresholds as Performance; optimization candidates and a slow stage can also produce signals.",
    "- Maintenance coverage/lag, freshness, runtime context, active runs, linkage, cache, and reconciliation evidence reuse their page metrics.",
    "- High volume alone is not treated as an issue without anomaly evidence.",
    "- Queue is deduplicated, then ranked by severity and impact; it shows up to 8 signals."
  ].join("\n");
}

export function OperationHealthPanel({
  jobRows,
  dataflowRows
}: {
  jobRows: Array<Record<string, string | number>>;
  dataflowRows: Array<Record<string, string | number>>;
}) {
  const jobOperations = sortOperationRows(filterOperationRows(jobRows));
  const dataflowOperations = sortOperationRows(filterOperationRows(dataflowRows));
  if (!jobOperations.length && !dataflowOperations.length) {
    return <div className="table-empty">No operation signals in current filters.</div>;
  }
  return (
    <div className="overview-operation-health">
      <ReportChart option={operationHealthCombinedBarOption(jobOperations, dataflowOperations)} height="100%" />
    </div>
  );
}

export function operationHealthCombinedBarOption(
  jobRows: Array<Record<string, string | number>>,
  dataflowRows: Array<Record<string, string | number>>
) {
  const hasJobRows = jobRows.length > 0;
  const hasDataflowRows = dataflowRows.length > 0;
  const jobLabels = jobRows.map((row) => String(row.operation_type ?? "unknown"));
  const dataflowLabels = dataflowRows.map((row) => String(row.operation_type ?? "unknown"));
  const hasBothGroups = hasJobRows && hasDataflowRows;
  const jobTop = hasBothGroups ? 14 : 8;
  const jobHeight = hasBothGroups ? "43%" : "86%";
  const dataflowTop = hasJobRows ? "54%" : 8;
  const dataflowHeight = hasBothGroups ? "43%" : "86%";
  const grids = [];
  const xAxes = [];
  const yAxes = [];
  const graphics = [];
  if (hasJobRows) {
    grids.push(fixedHorizontalBarGrid(76, false, { right: 14, top: jobTop, height: jobHeight }));
    xAxes.push(operationHealthValueAxis(0, "bottom"));
    yAxes.push(operationHealthCategoryAxis(jobLabels, 0));
    graphics.push(operationHealthGroupLabel("Job runs", hasBothGroups ? 0 : 0));
  }
  if (hasDataflowRows) {
    grids.push(fixedHorizontalBarGrid(76, false, { right: 14, top: dataflowTop, height: dataflowHeight }));
    xAxes.push(operationHealthValueAxis(hasJobRows ? 1 : 0, "bottom"));
    yAxes.push(operationHealthCategoryAxis(dataflowLabels, hasJobRows ? 1 : 0));
    graphics.push(operationHealthGroupLabel("Dataflow runs", hasJobRows ? "51.5%" : 0));
  }
  if (hasBothGroups) graphics.push(operationHealthDivider());
  const jobAxisIndex = hasJobRows ? 0 : -1;
  const dataflowAxisIndex = hasJobRows && hasDataflowRows ? 1 : hasDataflowRows ? 0 : -1;
  return baseChartOption({
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "shadow" },
      confine: false,
      appendToBody: true,
      extraCssText: "max-width: 260px; white-space: normal; line-height: 1.35; z-index: 9999;",
      formatter: (params: any) => {
        const first = Array.isArray(params) ? params[0] : params;
        const seriesName = String(first?.seriesName ?? "");
        const isDataflow = seriesName.startsWith("Dataflow ");
        const title = isDataflow ? "Dataflow runs" : "Job runs";
        const sourceRows = isDataflow ? dataflowRows : jobRows;
        const row = sourceRows[Number(first?.dataIndex ?? 0)] ?? {};
        return [
          `<strong>${title}</strong>`,
          ...operationHealthTooltip(row).split("\n")
        ].join("<br/>");
      }
    },
    legend: { show: false },
    grid: grids,
    xAxis: xAxes,
    yAxis: yAxes,
    graphic: graphics,
    series: [
      ...(hasJobRows
        ? OVERVIEW_STATUSES.map((status) => operationHealthStatusSeries("Job", status, jobRows, jobAxisIndex))
        : []),
      ...(hasDataflowRows
        ? OVERVIEW_STATUSES.map((status) => operationHealthStatusSeries("Dataflow", status, dataflowRows, dataflowAxisIndex))
        : [])
    ]
  });
}

export function operationHealthValueAxis(gridIndex: number, position: "top" | "bottom") {
  return {
    type: "value" as const,
    gridIndex,
    position,
    axisLabel: { fontSize: 9, margin: 3, formatter: (value: number) => formatCompact(value) },
    axisTick: { show: false },
    axisLine: { lineStyle: { color: reportChartPalette.grid } },
    splitLine: { lineStyle: { color: reportChartPalette.grid } }
  };
}

export function operationHealthCategoryAxis(labels: string[], gridIndex: number) {
  return {
    type: "category" as const,
    gridIndex,
    data: labels,
    inverse: true,
    boundaryGap: false,
    axisTick: { show: false },
    axisLabel: {
      fontSize: 10,
      color: reportChartPalette.muted,
      overflow: "truncate" as const,
      width: 76,
      margin: 4
    }
  };
}

export function operationHealthStatusSeries(
  group: "Job" | "Dataflow",
  status: string,
  rows: Array<Record<string, string | number>>,
  axisIndex: number
) {
  return {
    name: `${group} ${status}`,
    type: "bar" as const,
    stack: `${group}-runs`,
    barWidth: 16,
    emphasis: { focus: "series" as const },
    itemStyle: { color: statusColor(status), borderRadius: 2 },
    xAxisIndex: axisIndex,
    yAxisIndex: axisIndex,
    data: rows.map((row) => num(row, status))
  };
}

export function operationHealthGroupLabel(text: string, top: number | string) {
  return {
    type: "text" as const,
    left: 0,
    top,
    style: {
      text,
      fill: reportChartPalette.muted,
      font: "800 10px Inter, ui-sans-serif, system-ui",
      align: "left",
      backgroundColor: "#ffffff",
      padding: [1, 4, 1, 0],
      textTransform: "uppercase"
    },
    silent: true
  };
}

export function operationHealthDivider() {
  return {
    type: "rect" as const,
    left: 0,
    right: 0,
    top: "50%",
    shape: { width: 520, height: 1 },
    style: {
      fill: reportChartPalette.grid
    },
    silent: true
  };
}

export function WorkloadVolumeLegend() {
  return <MonitoringChartLegend label="Input and output workload legend" items={[
    ["Rows read", reportChartPalette.read],
    ["Est rows written", reportChartPalette.written],
    ["Bytes added", reportChartPalette.teal],
    ["Bytes removed", reportChartPalette.failed]
  ]} />;
}

export function WorkloadVolumeContextPanel({
  report,
  filters,
  dateRange,
  timezoneName,
  effectiveGrain
}: {
  report: MonitoringReport;
  filters: MonitoringFilters;
  dateRange: { min?: string | null; max?: string | null };
  timezoneName: string;
  effectiveGrain?: string;
}) {
  const trendOption = workloadVolumeTrendOption(
    report.volume.rows_by_date ?? [],
    report.volume.bytes_by_date ?? [],
    filters,
    dateRange,
    timezoneName,
    effectiveGrain,
    false
  );
  return (
    <div className="overview-workload-context">
      {trendOption ? (
        <div className="overview-workload-trend">
          <ReportChart option={trendOption} height="100%" />
        </div>
      ) : (
        <div className="table-empty">No workload volume trend in current filters.</div>
      )}
    </div>
  );
}

export function sortOperationRows(rows: Array<Record<string, string | number>>) {
  return rows
    .slice()
    .sort((left, right) => {
      const failed = num(right, "failed") - num(left, "failed");
      if (failed !== 0) return failed;
      const active = num(right, "running") + num(right, "pending") - (num(left, "running") + num(left, "pending"));
      if (active !== 0) return active;
      const total = operationTotal(right) - operationTotal(left);
      if (total !== 0) return total;
      return String(left.operation_type ?? "unknown").localeCompare(String(right.operation_type ?? "unknown"));
    });
}

export function filterOperationRows(rows: Array<Record<string, string | number>>) {
  return rows.filter((row) => {
    const operation = String(row.operation_type ?? "unknown").trim();
    return operation !== "" && operation !== "not_available";
  });
}

export function operationTotal(row: Record<string, unknown>) {
  return num(row, "count") || OVERVIEW_STATUSES.reduce((sum, status) => sum + num(row, status), 0);
}

export function operationHealthTooltip(row: Record<string, unknown>) {
  return [
    String(row.operation_type ?? "unknown"),
    `Total: ${formatNumber(operationTotal(row))}`,
    `Succeeded: ${formatNumber(num(row, "succeeded"))}`,
    `Failed: ${formatNumber(num(row, "failed"))}`,
    `Skipped: ${formatNumber(num(row, "skipped"))}`,
    `Running: ${formatNumber(num(row, "running"))}`,
    `Pending: ${formatNumber(num(row, "pending"))}`
  ].join("\n");
}

export function failureCategoryOption(rows: Array<Record<string, string | number>>) {
  const visible = rows
    .slice()
    .sort((left, right) => {
      const byCount = num(right, "count") - num(left, "count");
      if (byCount !== 0) return byCount;
      return String(left.category ?? "unknown").localeCompare(String(right.category ?? "unknown"));
    });
  const zoomConfig = horizontalBarDataZoom(visible.length);
  const hasZoom = Boolean(zoomConfig);
  const barSizing = horizontalBarSeriesSizing(visible.length);
  return baseChartOption({
    animation: false,
    animationDurationUpdate: 0,
    grid: fixedHorizontalBarGrid(110, hasZoom, { top: 12 }),
    tooltip: { trigger: "item", triggerOn: "mousemove", confine: true, axisPointer: { type: "none" } },
    xAxis: bottomAnchoredValueXAxis(),
    yAxis: fixedHorizontalCategoryAxis(visible.map((row) => String(row.category ?? "unknown")), 110),
    dataZoom: zoomConfig,
    series: [
      {
        name: "Failures",
        type: "bar",
        ...barSizing,
        itemStyle: { color: reportChartPalette.failed, borderRadius: [0, 3, 3, 0] },
        data: visible.map((row) => num(row, "count"))
      }
    ]
  });
}

export function resolveAttentionTarget(target: string): MonitoringTabKey {
  if (target === "jobs") return "jobs";
  if (target === "dataflows") return "dataflows";
  if (target === "failures") return "failures";
  if (target === "performance") return "performance";
  if (target === "maintenance") return "maintenance";
  if (target === "freshness") return "freshness";
  if (target === "diagnostics" || target === "sources") return "diagnostics";
  return "overview";
}

export type RuntimeContextSortKey =
  | "engine_name"
  | "metadata_provider_name"
  | "platform_name"
  | "jobs"
  | "success_rate"
  | "avg_duration_seconds"
  | "p95_duration_seconds";

export const RUNTIME_CONTEXT_AUTO_COLUMNS = [
  "Engine",
  "Provider",
  "Platform",
  "Jobs",
  "Success",
  "AVG / P95"
];

export const RUNTIME_CONTEXT_COLUMN_GAP = 8;

export const RUNTIME_CONTEXT_MIN_COLUMN_WIDTHS = [64, 68, 64, 46, 54, 66];

export const RUNTIME_CONTEXT_MAX_COLUMN_WIDTHS = [190, 190, 150, 86, 82, 112];

export function EngineProviderHealth({ rows }: { rows: Array<Record<string, string | number>> }) {
  const matrixRef = useRef<HTMLDivElement | null>(null);
  const [sort, setSort] = useState<{ key: RuntimeContextSortKey; dir: "asc" | "desc" }>({ key: "jobs", dir: "desc" });
  const [columnWidths, setColumnWidths] = useState<Array<number | null>>(() => RUNTIME_CONTEXT_AUTO_COLUMNS.map(() => null));
  const [matrixWidth, setMatrixWidth] = useState(0);
  const visible = useMemo(() => sortRuntimeContextRows(rows, sort.key, sort.dir), [rows, sort.key, sort.dir]);
  const autoColumnWidths = useMemo(() => buildRuntimeContextAutoWidths(visible), [visible]);
  const fittedColumnWidths = useMemo(
    () => fitRuntimeContextColumnWidths(columnWidths.map((width, index) => width ?? autoColumnWidths[index]), matrixWidth),
    [autoColumnWidths, columnWidths, matrixWidth]
  );
  const gridTemplateColumns = useMemo(
    () => fittedColumnWidths.map((width) => `${width}px`).join(" "),
    [fittedColumnWidths]
  );
  const gridStyle: CSSProperties = { gridTemplateColumns };
  useEffect(() => {
    const element = matrixRef.current;
    if (!element || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(([entry]) => {
      setMatrixWidth(Math.floor(entry.contentRect.width));
    });
    observer.observe(element);
    setMatrixWidth(Math.floor(element.getBoundingClientRect().width));
    return () => observer.disconnect();
  }, []);
  if (!visible.length) return <div className="table-empty">No runtime context in current filters.</div>;
  function toggleSort(key: RuntimeContextSortKey) {
    setSort((current) => ({ key, dir: current.key === key && current.dir === "desc" ? "asc" : "desc" }));
  }
  function startColumnResize(event: ReactPointerEvent<HTMLSpanElement>, index: number) {
    event.preventDefault();
    event.stopPropagation();
    const headerCell = event.currentTarget.parentElement;
    if (!headerCell) return;
    const startX = event.clientX;
    const startWidth = headerCell.getBoundingClientRect().width;
    const minWidth = RUNTIME_CONTEXT_MIN_COLUMN_WIDTHS[index] ?? 52;
    const handleMove = (moveEvent: PointerEvent) => {
      const nextWidth = Math.max(minWidth, Math.round(startWidth + moveEvent.clientX - startX));
      setColumnWidths((current) => current.map((width, widthIndex) => (widthIndex === index ? nextWidth : width)));
    };
    const handleUp = () => {
      window.removeEventListener("pointermove", handleMove);
      window.removeEventListener("pointerup", handleUp);
    };
    window.addEventListener("pointermove", handleMove);
    window.addEventListener("pointerup", handleUp);
  }
  function resetColumnWidth(index: number) {
    setColumnWidths((current) => current.map((width, widthIndex) => (widthIndex === index ? null : width)));
  }
  return (
    <div className="monitoring-compact-matrix" ref={matrixRef}>
      <div className="monitoring-compact-matrix-header" style={gridStyle}>
        <RuntimeContextHeaderCell
          label={`Engine${sortIndicator(sort, "engine_name")}`}
          title="Sort by engine"
          onSort={() => toggleSort("engine_name")}
          onResize={(event) => startColumnResize(event, 0)}
          onReset={() => resetColumnWidth(0)}
        />
        <RuntimeContextHeaderCell
          label={`Provider${sortIndicator(sort, "metadata_provider_name")}`}
          title="Sort by provider"
          onSort={() => toggleSort("metadata_provider_name")}
          onResize={(event) => startColumnResize(event, 1)}
          onReset={() => resetColumnWidth(1)}
        />
        <RuntimeContextHeaderCell
          label={`Platform${sortIndicator(sort, "platform_name")}`}
          title="Sort by platform"
          onSort={() => toggleSort("platform_name")}
          onResize={(event) => startColumnResize(event, 2)}
          onReset={() => resetColumnWidth(2)}
        />
        <RuntimeContextHeaderCell
          label={`Jobs${sortIndicator(sort, "jobs")}`}
          title="Sort by job runs"
          onSort={() => toggleSort("jobs")}
          onResize={(event) => startColumnResize(event, 3)}
          onReset={() => resetColumnWidth(3)}
        />
        <RuntimeContextHeaderCell
          label={`Success${sortIndicator(sort, "success_rate")}`}
          title="Sort by success rate"
          onSort={() => toggleSort("success_rate")}
          onResize={(event) => startColumnResize(event, 4)}
          onReset={() => resetColumnWidth(4)}
        />
        <RuntimeContextHeaderCell
          label={`AVG / P95${sortIndicator(sort, "avg_duration_seconds")}`}
          title="Sort by average duration. P95 is the second value in each row."
          onSort={() => toggleSort("avg_duration_seconds")}
          onResize={(event) => startColumnResize(event, 5)}
          onReset={() => resetColumnWidth(5)}
        />
      </div>
      {visible.map((row, index) => {
        const failed = num(row, "failed");
        const successRate = num(row, "success_rate");
        return (
          <div key={`${row.engine_name}-${row.metadata_provider_name}-${index}`} className="monitoring-compact-matrix-row" style={gridStyle}>
            <span className="monitoring-stack-cell" title={`${row.engine_name ?? "unknown"} / ${row.metadata_provider_name ?? "unknown"} / ${row.platform_name ?? "unknown"}`}>
              <strong>{row.engine_name ?? "unknown"}</strong>
            </span>
            <span title={String(row.metadata_provider_name ?? "unknown")}>{row.metadata_provider_name ?? "unknown"}</span>
            <span title={String(row.platform_name ?? "unknown")}>{row.platform_name ?? "unknown"}</span>
            <span>
              <CompactNumberValue value={num(row, "jobs")} />
              <small><span className="status-bad"><CompactNumberValue value={failed} /></span> failed</small>
            </span>
            <span className={successRate < 95 ? "status-bad" : "status-good"}>{formatPercent(successRate)}</span>
            <span>
              <strong>{formatSeconds(num(row, "avg_duration_seconds"))}</strong>
              <small>P95 {formatSeconds(num(row, "p95_duration_seconds"))}</small>
            </span>
          </div>
        );
      })}
    </div>
  );
}

export function RuntimeContextHeaderCell({
  label,
  title,
  onSort,
  onResize,
  onReset
}: {
  label: string;
  title: string;
  onSort: () => void;
  onResize: (event: ReactPointerEvent<HTMLSpanElement>) => void;
  onReset: () => void;
}) {
  return (
    <span className="monitoring-resizable-header-cell">
      <button type="button" onClick={onSort} title={title}>
        {label}
      </button>
      <span
        className="monitoring-column-resizer"
        role="separator"
        aria-orientation="vertical"
        title="Drag to resize. Double click to auto width."
        onPointerDown={onResize}
        onDoubleClick={(event) => {
          event.preventDefault();
          event.stopPropagation();
          onReset();
        }}
      />
    </span>
  );
}

export function buildRuntimeContextAutoWidths(rows: Array<Record<string, string | number>>) {
  const columnValues = RUNTIME_CONTEXT_AUTO_COLUMNS.map((label) => [label]);
  rows.forEach((row) => {
    const failed = num(row, "failed");
    columnValues[0].push(String(row.engine_name ?? "unknown"));
    columnValues[1].push(String(row.metadata_provider_name ?? "unknown"));
    columnValues[2].push(String(row.platform_name ?? "unknown"));
    columnValues[3].push(formatCompact(num(row, "jobs")), failed ? `${formatCompact(failed)} failed` : "0 failed");
    columnValues[4].push(formatPercent(num(row, "success_rate")));
    columnValues[5].push(formatSeconds(num(row, "avg_duration_seconds")), `P95 ${formatSeconds(num(row, "p95_duration_seconds"))}`);
  });
  return columnValues.map((values, index) => {
    const maxTextWidth = values.reduce((maxWidth, value) => Math.max(maxWidth, measureRuntimeContextText(value)), 0);
    const padding = index < 3 ? 22 : 16;
    return clampNumber(
      Math.ceil(maxTextWidth + padding),
      RUNTIME_CONTEXT_MIN_COLUMN_WIDTHS[index] ?? 52,
      RUNTIME_CONTEXT_MAX_COLUMN_WIDTHS[index] ?? 220
    );
  });
}

export function fitRuntimeContextColumnWidths(widths: number[], availableWidth: number) {
  if (!availableWidth) return widths;
  const gapWidth = RUNTIME_CONTEXT_COLUMN_GAP * Math.max(0, widths.length - 1);
  const targetWidth = Math.max(0, availableWidth - gapWidth);
  const totalWidth = widths.reduce((sum, width) => sum + width, 0);
  if (totalWidth <= targetWidth) return widths;

  const minimums = RUNTIME_CONTEXT_MIN_COLUMN_WIDTHS;
  const minTotalWidth = minimums.reduce((sum, width) => sum + width, 0);
  if (targetWidth <= minTotalWidth) return minimums;

  const shrinkableTotal = widths.reduce((sum, width, index) => sum + Math.max(0, width - minimums[index]), 0);
  if (!shrinkableTotal) return widths;
  const shrinkNeeded = totalWidth - targetWidth;

  return widths.map((width, index) => {
    const minWidth = minimums[index];
    const shrinkable = Math.max(0, width - minWidth);
    const shrink = (shrinkable / shrinkableTotal) * shrinkNeeded;
    return Math.max(minWidth, Math.floor(width - shrink));
  });
}

export let runtimeContextMeasureCanvas: HTMLCanvasElement | null = null;

export function measureRuntimeContextText(value: string) {
  if (typeof document === "undefined") {
    return value.length * 7;
  }
  runtimeContextMeasureCanvas ??= document.createElement("canvas");
  const context = runtimeContextMeasureCanvas.getContext("2d");
  if (!context) return value.length * 7;
  context.font = "750 12px Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif";
  return context.measureText(value).width;
}

export function clampNumber(value: number, min: number, max: number) {
  return Math.max(min, Math.min(max, value));
}

export function sortRuntimeContextRows(
  rows: Array<Record<string, string | number>>,
  key: RuntimeContextSortKey,
  dir: "asc" | "desc"
) {
  const multiplier = dir === "asc" ? 1 : -1;
  return rows.slice().sort((left, right) => {
    const leftValue = runtimeContextSortValue(left, key);
    const rightValue = runtimeContextSortValue(right, key);
    if (typeof leftValue === "number" && typeof rightValue === "number") {
      const delta = leftValue - rightValue;
      if (delta !== 0) return delta * multiplier;
    } else {
      const delta = String(leftValue).localeCompare(String(rightValue));
      if (delta !== 0) return delta * multiplier;
    }
    const failed = num(right, "failed") - num(left, "failed");
    if (failed !== 0) return failed;
    return String(left.engine_name ?? "").localeCompare(String(right.engine_name ?? ""));
  });
}

export function runtimeContextSortValue(row: Record<string, string | number>, key: RuntimeContextSortKey) {
  if (key === "jobs") return num(row, "jobs");
  if (key === "success_rate") return num(row, "success_rate");
  if (key === "avg_duration_seconds") return num(row, "avg_duration_seconds");
  if (key === "p95_duration_seconds") return num(row, "p95_duration_seconds");
  return String(row[key] ?? "unknown");
}

export function sortIndicator(sort: { key: RuntimeContextSortKey; dir: "asc" | "desc" }, key: RuntimeContextSortKey) {
  return sort.key === key ? (sort.dir === "asc" ? " ↑" : " ↓") : "";
}
