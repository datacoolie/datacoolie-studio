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
import { reportChartGrid } from "../components/monitoringPrimitives";

export const REPORT_CHART_TIGHT_GRID_BOTTOM = 2;

export function reportTightChartGrid(overrides: Record<string, string | number | boolean> = {}) {
  return {
    ...reportChartGrid(overrides),
    bottom: REPORT_CHART_TIGHT_GRID_BOTTOM
  };
}
