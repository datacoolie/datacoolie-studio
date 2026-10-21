import { useEffect, useRef } from "react";
import * as echarts from "echarts/core";
import { BarChart, BoxplotChart, LineChart, ScatterChart } from "echarts/charts";
import {
  AxisPointerComponent,
  DataZoomComponent,
  GraphicComponent,
  GridComponent,
  LegendComponent,
  MarkLineComponent,
  TitleComponent,
  TooltipComponent,
} from "echarts/components";
import { LabelLayout } from "echarts/features";
import { CanvasRenderer } from "echarts/renderers";
import type { EChartsOption } from "echarts";
import { lifecycleStatusPresentations } from "../../shared/statusPresentation";
import { claimHelp, dismissActiveHelp, releaseHelp } from "../../shared/components/helpCoordinator";
import { type ChartTooltipFormatter, type ChartTooltipParams } from "./components/chartTooltip";

echarts.use([
  BarChart,
  BoxplotChart,
  LineChart,
  ScatterChart,
  AxisPointerComponent,
  DataZoomComponent,
  GraphicComponent,
  GridComponent,
  LegendComponent,
  MarkLineComponent,
  TitleComponent,
  TooltipComponent,
  LabelLayout,
  CanvasRenderer,
]);

export function ReportChart({
  option,
  height = 220,
  wheelDataZoomStep,
  onPointClick,
}: {
  option: EChartsOption;
  height?: number | string;
  wheelDataZoomStep?: number;
  onPointClick?: (params: { dataIndex?: number; seriesName?: string }) => void;
}) {
  const chartHostRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<ReturnType<typeof echarts.init> | null>(null);
  const wrapperRef = useRef<HTMLDivElement | null>(null);
  const optionRef = useRef(option);
  const pointClickRef = useRef(onPointClick);
  const chartHelpOwnerRef = useRef<{ close: () => void } | null>(null);
  const chartTooltipSuppressedRef = useRef({ current: false });
  pointClickRef.current = onPointClick;
  const wheelStep = wheelDataZoomStep ? Math.max(1, Math.floor(wheelDataZoomStep)) : 0;
  const consumeWheelForDataZoom = (deltaY: number) => {
    if (!wheelStep) return;
    const chart = chartRef.current;
    if (!chart) return false;
    const current = chart.getOption() as Record<string, any>;
    const dataZoom = Array.isArray(current?.dataZoom) ? dataZoomFromOption(current.dataZoom) : [];
    const targetZoomIndex = dataZoom.findIndex((item: any) => item?.type === "inside");
    if (targetZoomIndex < 0) return false;
    const targetZoom = dataZoom[targetZoomIndex] ?? {};
    const yAxis = Array.isArray(current?.yAxis) ? current.yAxis[0] : current?.yAxis;
    const categories = Array.isArray(yAxis?.data) ? yAxis.data : [];
    const total = categories.length;
    if (total <= 0) return true;
    const rawStart = Number(targetZoom.startValue ?? 0);
    const rawEnd = Number(targetZoom.endValue ?? total - 1);
    const start = Number.isFinite(rawStart) ? Math.max(0, Math.min(total - 1, rawStart)) : 0;
    const end = Number.isFinite(rawEnd) ? Math.max(start, Math.min(total - 1, rawEnd)) : Math.min(total - 1, start + 9);
    const windowSize = Math.max(1, end - start + 1);
    const maxStart = Math.max(0, total - windowSize);
    const direction = deltaY > 0 ? 1 : deltaY < 0 ? -1 : 0;
    if (!direction) return true;
    const nextStart = Math.max(0, Math.min(maxStart, start + direction * wheelStep));
    if (nextStart === start) return true;
    const nextEnd = Math.min(total - 1, nextStart + windowSize - 1);
    chart.dispatchAction({
      type: "dataZoom",
      dataZoomIndex: targetZoomIndex,
      startValue: nextStart,
      endValue: nextEnd,
      animation: { duration: 0 }
    });
    return true;
  };

  useEffect(() => {
    const host = chartHostRef.current;
    if (!host) return;
    const chart = echarts.init(host, undefined, { renderer: "canvas" });
    chartRef.current = chart;
    const suppressed = chartTooltipSuppressedRef.current;
    const hideNativeTooltip = () => {
      chart.dispatchAction({ type: "hideTip" });
      // ECharts keeps an enterable HTML tooltip alive while the pointer is
      // over its body. Hide the owned surface explicitly so Escape wins over
      // that pointer state; the next chart entry lets ECharts show it again.
      document.querySelectorAll<HTMLElement>(".help-chart-surface").forEach((surface) => {
        surface.style.display = "none";
      });
    };
    const helpOwner = {
      close: () => {
        suppressed.current = true;
        hideNativeTooltip();
      }
    };
    chartHelpOwnerRef.current = helpOwner;
    const handleShowTip = () => {
      if (suppressed.current) {
        hideNativeTooltip();
        return;
      }
      if (!claimHelp(helpOwner)) hideNativeTooltip();
    };
    const handleHideTip = () => releaseHelp(helpOwner);
    const handleEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      if (!event.defaultPrevented) dismissActiveHelp(event);
    };
    const resetSuppression = () => { suppressed.current = false; };
    let lastPointerPosition = "";
    const resetAfterPointerMovement = (event: PointerEvent) => {
      const position = `${event.clientX}:${event.clientY}`;
      if (position !== lastPointerPosition) suppressed.current = false;
      lastPointerPosition = position;
    };
    chart.on("showTip", handleShowTip);
    chart.on("hideTip", handleHideTip);
    host.addEventListener("pointerenter", resetSuppression);
    host.addEventListener("pointermove", resetAfterPointerMovement);
    document.addEventListener("keydown", handleEscape, true);
    const handleChartClick = (params: { dataIndex?: number; seriesName?: string }) => {
      pointClickRef.current?.(params);
    };
    chart.on("click", handleChartClick);
    let lastMeasuredWidth = -1;
    let lastMeasuredHeight = -1;
    let resizeFrame: number | null = null;
    const resizeAndFitLabels = () => {
      if (resizeFrame !== null) return;
      resizeFrame = window.requestAnimationFrame(() => {
        resizeFrame = null;
        const width = host.clientWidth;
        const height = host.clientHeight;
        if (width === lastMeasuredWidth && height === lastMeasuredHeight) return;
        lastMeasuredWidth = width;
        lastMeasuredHeight = height;
        chart.resize();
        const sizing = horizontalCategoryLabelSizing(optionRef.current, width);
        if (sizing) chart.setOption(sizing, { lazyUpdate: true });
      });
    };
    const resizeObserver = typeof ResizeObserver === "undefined"
      ? null
      : new ResizeObserver(resizeAndFitLabels);
    resizeObserver?.observe(host);
    const handleWindowResize = resizeAndFitLabels;
    if (!resizeObserver) window.addEventListener("resize", handleWindowResize);
    const animationFrame = window.requestAnimationFrame(resizeAndFitLabels);
    return () => {
      window.cancelAnimationFrame(animationFrame);
      if (resizeFrame !== null) window.cancelAnimationFrame(resizeFrame);
      resizeObserver?.disconnect();
      if (!resizeObserver) window.removeEventListener("resize", handleWindowResize);
      chart.off("click", handleChartClick);
      chart.off("showTip", handleShowTip);
      chart.off("hideTip", handleHideTip);
      host.removeEventListener("pointerenter", resetSuppression);
      host.removeEventListener("pointermove", resetAfterPointerMovement);
      document.removeEventListener("keydown", handleEscape, true);
      releaseHelp(helpOwner);
      chartHelpOwnerRef.current = null;
      suppressed.current = false;
      chartRef.current = null;
      chart.dispose();
    };
  }, []);

  useEffect(() => {
    optionRef.current = option;
    const chart = chartRef.current;
    const host = chartHostRef.current;
    if (!chart || !host) return;
    chartHelpOwnerRef.current?.close();
    const fittedOption = withResponsiveHorizontalCategoryLabels(option, host.clientWidth);
    const lifecycleOption = withChartTooltipLifecycle(fittedOption, chartHelpOwnerRef.current, chartTooltipSuppressedRef.current);
    chart.setOption({
      ...lifecycleOption,
      tooltip: { ...(lifecycleOption.tooltip as Record<string, unknown>), ...sharedReportTooltip(host) },
    }, { notMerge: true, lazyUpdate: true });
  }, [option]);

  useEffect(() => {
    const host = wrapperRef.current;
    if (!host || !wheelStep) return;
    const onWheel = (event: WheelEvent) => {
      if (!consumeWheelForDataZoom(event.deltaY)) return;
      event.preventDefault();
      event.stopPropagation();
    };
    host.addEventListener("wheel", onWheel, { capture: true, passive: false });
    return () => {
      host.removeEventListener("wheel", onWheel, true);
    };
  }, [wheelStep]);

  return (
    <div ref={wrapperRef} className="monitoring-report-chart" style={{ width: "100%", height: "100%", position: "relative" }}>
      <div ref={chartHostRef} style={{ width: "100%", height }} />
    </div>
  );
}

function dataZoomFromOption(dataZoom: unknown) {
  return Array.isArray(dataZoom) ? dataZoom : [];
}

export const reportChartPalette = {
  success: lifecycleStatusPresentations.succeeded.chartColor,
  failed: lifecycleStatusPresentations.failed.chartColor,
  skipped: lifecycleStatusPresentations.skipped.chartColor,
  running: lifecycleStatusPresentations.running.chartColor,
  pending: lifecycleStatusPresentations.pending.chartColor,
  unknown: "#8b95a5",
  teal: "#155e59",
  blue: "#3d6fa8",
  written: "#16805c",
  read: "#2563eb",
  amber: "#c77d2f",
  grid: "#e8edf3",
  text: "#303846",
  muted: "#6d7584"
};

export function baseChartOption(option: EChartsOption): EChartsOption {
  const optionRecord = option as EChartsOption & { tooltip?: Record<string, unknown> };
  return {
    color: [
      reportChartPalette.success,
      reportChartPalette.failed,
      reportChartPalette.skipped,
      reportChartPalette.running,
      reportChartPalette.pending,
      reportChartPalette.blue,
      reportChartPalette.amber
    ],
    textStyle: {
      color: reportChartPalette.text,
      fontFamily: "IBM Plex Sans, Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif",
      fontSize: 13,
      fontWeight: 400
    },
    grid: {
      left: 8,
      right: 10,
      top: 12,
      bottom: 2,
      containLabel: true
    },
    ...option,
    tooltip: {
      ...(optionRecord.tooltip ?? {}),
      ...sharedReportTooltip()
    }
  };
}

export function sharedReportTooltip(chartHost?: HTMLElement) {
  const computedStyle = chartHost && typeof window !== "undefined" ? window.getComputedStyle(chartHost) : null;
  const fontFamily = computedStyle?.getPropertyValue("--font-sans") || "IBM Plex Sans, Inter, ui-sans-serif, system-ui, sans-serif";
  const fontSize = Number.parseFloat(computedStyle?.getPropertyValue("--help-body-size") ?? "") || 13;
  const fontWeight = "normal" as const;
  const textColor = computedStyle?.getPropertyValue("--color-text") || "#162033";
  return {
    className: "help-chart-surface",
    enterable: true,
    confine: false,
    appendToBody: true,
    transitionDuration: 0,
    extraCssText: "z-index: 2147483647; max-height: min(540px, calc(100vh - 24px)); overflow-y: auto; white-space: normal; overflow-wrap: anywhere;",
    textStyle: { color: textColor, fontFamily, fontSize, fontWeight },
    position: (point: [number, number], _params: unknown, _dom: unknown, _rect: unknown, size: { contentSize: number[]; viewSize: number[] }) => {
      // ECharts supplies chart-local coordinates even when the tooltip is in
      // body. Fit against the viewport, then return chart-local coordinates.
      const origin = chartHost?.getBoundingClientRect();
      const originX = origin?.left ?? 0;
      const originY = origin?.top ?? 0;
      const x = originX + point[0];
      const y = originY + point[1];
      const [tooltipWidth, tooltipHeight] = size.contentSize;
      const viewWidth = chartHost ? document.documentElement.clientWidth : size.viewSize[0];
      const viewHeight = chartHost ? document.documentElement.clientHeight : size.viewSize[1];
      const margin = 12;
      const gap = 16;
      const preferredLeft = x + tooltipWidth + gap + margin <= viewWidth
        ? x + gap
        : x - tooltipWidth - gap;
      const left = Math.max(margin, Math.min(viewWidth - tooltipWidth - margin, preferredLeft));
      const top = Math.max(margin, Math.min(viewHeight - tooltipHeight - margin, y - tooltipHeight / 2));
      return [left - originX, top - originY];
    }
  };
}

function withChartTooltipLifecycle(
  option: EChartsOption,
  owner: { close: () => void } | null,
  suppressed: { current: boolean },
): EChartsOption {
  if (!owner) return option;
  const record = option as unknown as Record<string, any>;
  const nextTooltip = wrapChartTooltip(record.tooltip, owner, suppressed);
  const nextSeries = Array.isArray(record.series)
    ? record.series.map((series: unknown) => {
        if (!series || typeof series !== "object") return series;
        const value = series as Record<string, any>;
        return value.tooltip ? { ...value, tooltip: wrapChartTooltip(value.tooltip, owner, suppressed) } : value;
      })
    : record.series;
  return { ...option, tooltip: nextTooltip, series: nextSeries } as EChartsOption;
}

function wrapChartTooltip(
  tooltip: unknown,
  owner: { close: () => void },
  suppressed: { current: boolean },
) {
  if (!tooltip || typeof tooltip !== "object") return tooltip;
  const record = tooltip as Record<string, any>;
  if (typeof record.formatter !== "function") return tooltip;
  const original = record.formatter as ChartTooltipFormatter;
  const formatter = ((params: ChartTooltipParams | ChartTooltipParams[]) => {
    if (suppressed.current) return "";
    return claimHelp(owner) ? original(params) : "";
  }) as ChartTooltipFormatter;
  formatter.content = original.content;
  return { ...record, formatter };
}

// Let horizontal record labels fit naturally, capped at 35% of the chart width.
const HORIZONTAL_CATEGORY_LABEL_MAX_RATIO = 0.35;

function withResponsiveHorizontalCategoryLabels(option: EChartsOption, chartWidth: number): EChartsOption {
  const sizing = horizontalCategoryLabelSizing(option, chartWidth);
  return sizing ? { ...option, ...sizing } : option;
}

function horizontalCategoryLabelSizing(option: EChartsOption, chartWidth: number): EChartsOption | null {
  if (!Number.isFinite(chartWidth) || chartWidth <= 0) return null;

  const optionRecord = option as unknown as Record<string, any>;
  const yAxisValue = optionRecord.yAxis;
  const gridValue = optionRecord.grid;
  const yAxisWasArray = Array.isArray(yAxisValue);
  const gridWasArray = Array.isArray(gridValue);
  const yAxes = asOptionArray(yAxisValue);
  const grids = asOptionArray(gridValue);
  if (!yAxes.length || !grids.length) return null;

  const textStyle = asOptionRecord(optionRecord.textStyle);
  const maxLabelWidth = Math.max(1, Math.floor(chartWidth * HORIZONTAL_CATEGORY_LABEL_MAX_RATIO));
  const nextLeftByGrid = new Map<number, number>();
  let changed = false;

  const nextYAxes = yAxes.map((axis) => {
    const axisLabel = asOptionRecord(axis?.axisLabel);
    const labels = Array.isArray(axis?.data)
      ? axis.data.map((value: unknown, index: number) => formattedCategoryLabelText(value, index, axisLabel)).filter(Boolean)
      : [];
    const currentWidth = Number(axisLabel.width);
    if (axis?.type !== "category" || !labels.length || axisLabel.overflow !== "truncate" || !Number.isFinite(currentWidth)) {
      return axis;
    }

    const measuredWidth = measureCategoryLabels(labels, axisLabel, textStyle);
    // zrender's truncation layout reserves 1px inside the supplied width.
    const labelWidth = Math.max(1, Math.min(maxLabelWidth, Math.ceil(measuredWidth) + 1));
    const gridIndex = Number(axis.gridIndex ?? 0);
    const grid = grids[gridIndex];
    if (!grid) return axis;

    const gridLeft = Number(grid.left);
    const leftGap = Number.isFinite(gridLeft) ? Math.max(0, gridLeft - currentWidth) : 8;
    nextLeftByGrid.set(gridIndex, Math.max(nextLeftByGrid.get(gridIndex) ?? 0, labelWidth + leftGap));
    changed ||= labelWidth !== currentWidth;
    return { ...axis, axisLabel: { ...axisLabel, width: labelWidth } };
  });

  const nextGrids = grids.map((grid, index) => {
    const left = nextLeftByGrid.get(index);
    if (left === undefined) return grid;
    changed ||= Number(grid.left) !== left;
    return { ...grid, left };
  });

  if (!changed) return null;
  return {
    yAxis: yAxisWasArray ? nextYAxes : nextYAxes[0],
    grid: gridWasArray ? nextGrids : nextGrids[0]
  } as EChartsOption;
}

function asOptionArray(value: unknown): Array<Record<string, any>> {
  if (Array.isArray(value)) return value.map(asOptionRecord);
  const record = asOptionRecord(value);
  return Object.keys(record).length ? [record] : [];
}

function asOptionRecord(value: unknown): Record<string, any> {
  return value !== null && typeof value === "object" ? value as Record<string, any> : {};
}

function categoryLabelText(value: unknown): string {
  if (typeof value === "string" || typeof value === "number") return String(value);
  const record = asOptionRecord(value);
  return record.value === undefined || record.value === null ? "" : String(record.value);
}

function formattedCategoryLabelText(value: unknown, index: number, axisLabel: Record<string, any>): string {
  const label = categoryLabelText(value);
  if (typeof axisLabel.formatter === "function") return String(axisLabel.formatter(label, index) ?? "");
  if (typeof axisLabel.formatter === "string") return axisLabel.formatter.replace(/\{value\}/gu, label);
  return label;
}

function measureCategoryLabels(
  labels: string[],
  axisLabel: Record<string, any>,
  textStyle: Record<string, any>
): number {
  const fontSizeValue = Number(axisLabel.fontSize ?? textStyle.fontSize ?? 12);
  const fontSize = Number.isFinite(fontSizeValue) ? fontSizeValue : 12;
  const fontWeight = axisLabel.fontWeight ?? textStyle.fontWeight ?? "normal";
  const fontFamily = axisLabel.fontFamily ?? textStyle.fontFamily ?? "sans-serif";
  const lines = labels.flatMap(label => label.split("\n"));
  if (typeof document !== "undefined") {
    const context = document.createElement("canvas").getContext("2d");
    if (context) {
      context.font = `${fontWeight} ${fontSize}px ${fontFamily}`;
      return Math.max(0, ...lines.map((label) => context.measureText(label).width));
    }
  }
  return Math.max(0, ...lines.map((label) => label.length * fontSize * 0.6));
}
