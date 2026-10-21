import type { MonitoringPageKey } from "../../app/moduleRegistry";
import type { MonitoringSessionScope } from "../../shared/api/domainTypes";

export type MonitoringRange = "24h" | "3d" | "7d" | "30d" | "90d" | "custom" | "all";
export type MonitoringGrain = "auto" | "hour" | "day" | "week" | "month";

export type MonitoringTabKey = MonitoringPageKey;

export interface MonitoringFilters {
  range: MonitoringRange;
  grain: MonitoringGrain;
  startTime: string;
  endTime: string;
  status: string;
  stage: string;
  connection: string;
  engine: string;
  provider: string;
  sourceType: string;
  destinationType: string;
  loadType: string;
  operationType: string;
  search: string;
  investigateKind: string;
  investigateValue: string;
  investigateLogSessionScope?: MonitoringSessionScope;
  investigateLogSessionId?: string | null;
  investigateSourceId?: number;
}

export interface FilterOption {
  value: string;
  label: string;
  count?: number;
}

export const DEFAULT_MONITORING_FILTERS: MonitoringFilters = {
  range: "30d",
  grain: "auto",
  startTime: "",
  endTime: "",
  status: "all",
  stage: "all",
  connection: "all",
  engine: "all",
  provider: "all",
  sourceType: "all",
  destinationType: "all",
  loadType: "all",
  operationType: "all",
  search: "",
  investigateKind: "",
  investigateLogSessionScope: "all",
  investigateLogSessionId: "",
  investigateValue: ""
};

const FILTER_KEYS = [...Object.keys(DEFAULT_MONITORING_FILTERS), "investigateSourceId"] as Array<keyof MonitoringFilters>;

export function filtersFromSearch(search: string): MonitoringFilters {
  const params = new URLSearchParams(search);
  const filters = { ...DEFAULT_MONITORING_FILTERS };
  for (const key of FILTER_KEYS) {
    const value = params.get(key);
    if (value !== null) {
      if (key === "range") {
        filters.range = isMonitoringRange(value) ? value : DEFAULT_MONITORING_FILTERS.range;
      } else if (key === "grain") {
        filters.grain = isMonitoringGrain(value) ? value : DEFAULT_MONITORING_FILTERS.grain;
      } else if (key === "investigateLogSessionScope") {
        filters.investigateLogSessionScope = isMonitoringSessionScope(value) ? value : DEFAULT_MONITORING_FILTERS.investigateLogSessionScope;
      } else if (key === "investigateSourceId") {
        const sourceId = Number(value);
        if (Number.isInteger(sourceId) && sourceId > 0) filters.investigateSourceId = sourceId;
      } else {
        filters[key] = value as never;
      }
    }
  }
  return filters;
}

export function filtersToSearch(filters: MonitoringFilters, search = "") {
  const params = new URLSearchParams(search);
  for (const key of FILTER_KEYS) {
    const value = filters[key];
    if ((key === "startTime" || key === "endTime") && filters.range !== "custom") {
      params.delete(key);
    } else if (key === "investigateLogSessionId" && filters.investigateLogSessionScope === "exact" && value !== undefined && value !== null) {
      // An exact empty session ID is meaningful and must survive URL round trips.
      params.set(key, String(value));
    } else if (value && value !== DEFAULT_MONITORING_FILTERS[key]) {
      params.set(key, String(value));
    } else {
      params.delete(key);
    }
  }
  const value = params.toString();
  return value ? `?${value}` : "";
}

export function hasActiveFilters(filters: MonitoringFilters) {
  return FILTER_KEYS.some((key) => {
    if ((key === "startTime" || key === "endTime") && filters.range !== "custom") return false;
    return filters[key] !== DEFAULT_MONITORING_FILTERS[key];
  });
}

function isMonitoringRange(value: string): value is MonitoringRange {
  return value === "24h" || value === "3d" || value === "7d" || value === "30d" || value === "90d" || value === "custom" || value === "all";
}

function isMonitoringGrain(value: string): value is MonitoringGrain {
  return value === "auto" || value === "hour" || value === "day" || value === "week" || value === "month";
}

function isMonitoringSessionScope(value: string): value is MonitoringSessionScope {
  return value === "all" || value === "null" || value === "exact";
}
