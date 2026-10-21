import type { JobRecord, MonitoringRecord, MonitoringReport } from "../../../shared/api/domainTypes";
import type { CSSProperties, PointerEvent as ReactPointerEvent, ReactNode } from "react";
import type { MonitoringFilters, MonitoringTabKey } from "../monitoringFilters";
import type { TableSort } from "../../../shared/components/DataTable";

export type {
  JobRecord,
  MonitoringRecord,
  MonitoringReport,
  CSSProperties,
  ReactPointerEvent,
  ReactNode,
  MonitoringFilters,
  MonitoringTabKey,
  TableSort
};

export const OVERVIEW_STATUSES = ["succeeded", "failed", "skipped", "running", "pending", "unknown"] as const;
export const PHASE_KEYS = ["source", "transform", "destination", "overhead"] as const;
export const FAILURE_PHASES = ["source", "transform", "destination", "overhead", "unknown"] as const;
export const HORIZONTAL_BAR_VISIBLE_ROWS = 8;
export const HORIZONTAL_BAR_LABEL_GAP = 16;
export const REPORT_CHART_GRID_BOTTOM = 6;
export const REPORT_CHART_X_ZOOM_GRID_BOTTOM = 30;
export const RUN_TABLE_PAGE_SIZES = [50, 100, 200] as const;

export type HealthIntent = "neutral" | "bad" | "good" | "warning";
export type HealthCardAccent = HealthIntent | "source" | "transform" | "destination" | "storage" | "overhead" | "intent";
export type PhaseKey = typeof PHASE_KEYS[number];
export type FailurePhaseKey = typeof FAILURE_PHASES[number];
