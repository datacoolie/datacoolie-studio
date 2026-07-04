import { useEffect, useRef } from "react";
import ReactECharts from "echarts-for-react";
import type { EChartsOption } from "echarts";

export function ReportChart({
  option,
  height = 220,
  wheelDataZoomStep
}: {
  option: EChartsOption;
  height?: number | string;
  wheelDataZoomStep?: number;
}) {
  const chartRef = useRef<ReactECharts | null>(null);
  const wrapperRef = useRef<HTMLDivElement | null>(null);
  const wheelStep = wheelDataZoomStep ? Math.max(1, Math.floor(wheelDataZoomStep)) : 0;
  const consumeWheelForDataZoom = (deltaY: number) => {
    if (!wheelStep) return;
    const chart = chartRef.current?.getEchartsInstance();
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
    <div ref={wrapperRef} style={{ width: "100%", height: "100%" }}>
      <ReactECharts
        ref={chartRef}
        option={option}
        notMerge
        lazyUpdate
        style={{ width: "100%", height }}
        opts={{ renderer: "canvas" }}
      />
    </div>
  );
}

function dataZoomFromOption(dataZoom: unknown) {
  return Array.isArray(dataZoom) ? dataZoom : [];
}

export const reportChartPalette = {
  success: "#2f8f72",
  failed: "#c94a4f",
  skipped: "#d89b42",
  running: "#4e78bb",
  pending: "#8a6fd1",
  unknown: "#8b95a5",
  teal: "#155e59",
  blue: "#3d6fa8",
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
      fontFamily: "Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
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

export function sharedReportTooltip() {
  return {
    confine: false,
    appendToBody: true,
    backgroundColor: "rgba(255,255,255,0.98)",
    borderColor: "#dde3eb",
    extraCssText: "z-index: 2147483647; box-shadow: 0 12px 30px rgba(15, 23, 42, 0.16); max-width: min(520px, calc(100vw - 24px)); white-space: normal;",
    textStyle: { color: reportChartPalette.text },
    position: (point: [number, number], _params: unknown, _dom: unknown, _rect: unknown, size: { contentSize: number[]; viewSize: number[] }) => {
      const [x, y] = point;
      const [tooltipWidth, tooltipHeight] = size.contentSize;
      const [viewWidth, viewHeight] = size.viewSize;
      const margin = 12;
      const gap = 16;
      const left = x + tooltipWidth + gap + margin <= viewWidth
        ? x + gap
        : Math.max(margin, x - tooltipWidth - gap);
      const top = Math.max(margin, Math.min(viewHeight - tooltipHeight - margin, y - tooltipHeight / 2));
      return [left, top];
    }
  };
}
