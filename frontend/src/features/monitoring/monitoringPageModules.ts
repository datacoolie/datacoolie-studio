import { lazy } from "react";
import type { MonitoringTabKey } from "./monitoringFilters";

const loadOverview = () => import("./pages/MonitoringOverviewPage").then((module) => ({ default: module.MonitoringOverviewPage }));
const loadJobs = () => import("./pages/JobsPage").then((module) => ({ default: module.JobsPage }));
const loadDataflows = () => import("./pages/DataflowsPage").then((module) => ({ default: module.DataflowsPage }));
const loadFailures = () => import("./pages/FailurePage").then((module) => ({ default: module.FailurePage }));
const loadDiagnostics = () => import("./pages/DiagnosticsPage").then((module) => ({ default: module.DiagnosticsPage }));
const loadPerformance = () => import("./pages/PerformancePage").then((module) => ({ default: module.PerformancePage }));
const loadVolume = () => import("./pages/VolumePage").then((module) => ({ default: module.VolumePage }));
const loadMaintenance = () => import("./pages/MaintenancePage").then((module) => ({ default: module.MaintenancePage }));
const loadFreshness = () => import("./pages/FreshnessPage").then((module) => ({ default: module.FreshnessPage }));

const pageLoaders = {
  overview: loadOverview,
  jobs: loadJobs,
  dataflows: loadDataflows,
  failures: loadFailures,
  freshness: loadFreshness,
  performance: loadPerformance,
  volume: loadVolume,
  maintenance: loadMaintenance,
  diagnostics: loadDiagnostics
} satisfies Record<MonitoringTabKey, () => Promise<unknown>>;

export function preloadMonitoringPage(page: MonitoringTabKey) {
  return pageLoaders[page]();
}

export const MonitoringOverviewPage = lazy(loadOverview);
export const JobsPage = lazy(loadJobs);
export const DataflowsPage = lazy(loadDataflows);
export const FailurePage = lazy(loadFailures);
export const DiagnosticsPage = lazy(loadDiagnostics);
export const PerformancePage = lazy(loadPerformance);
export const VolumePage = lazy(loadVolume);
export const MaintenancePage = lazy(loadMaintenance);
export const FreshnessPage = lazy(loadFreshness);
