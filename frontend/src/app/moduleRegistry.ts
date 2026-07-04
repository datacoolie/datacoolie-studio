import {
  Activity,
  Boxes,
  Database,
  FolderKanban,
  GitBranch,
  Home,
  Layers3,
  Settings,
  SlidersHorizontal,
  TableProperties,
  type LucideIcon
} from "lucide-react";

export type ModuleKey =
  | "projects"
  | "project-overview"
  | "environments"
  | "overview"
  | "sources"
  | "metadata"
  | "assets"
  | "lineage"
  | "monitoring"
  | "master-data"
  | "settings";

/**
 * A capability module is a feature package that can be enabled or disabled at
 * the Studio level. Navigation entries that belong to a capability are only
 * shown when that capability is enabled. Entries without a capability are core
 * platform navigation and are always available.
 */
export type CapabilityKey = "metadata" | "master-data";

export type MonitoringPageKey =
  | "overview"
  | "jobs"
  | "dataflows"
  | "failures"
  | "diagnostics"
  | "performance"
  | "volume"
  | "maintenance"
  | "freshness";

export type ModuleGroup = "Projects" | "Environment" | "Studio";
export type ModuleScope = "global" | "project" | "environment";

export interface StudioModule {
  key: ModuleKey;
  label: string;
  path: ModuleKey;
  icon: LucideIcon;
  group: ModuleGroup;
  scope: ModuleScope;
  requiresEnvironment: boolean;
  /** Capability that gates this entry. Undefined = always-available core. */
  capability?: CapabilityKey;
}

export const modules: StudioModule[] = [
  { key: "projects", label: "All projects", path: "projects", icon: FolderKanban, group: "Projects", scope: "global", requiresEnvironment: false },
  { key: "project-overview", label: "Project overview", path: "project-overview", icon: Home, group: "Projects", scope: "project", requiresEnvironment: false },
  { key: "environments", label: "Environments", path: "environments", icon: Layers3, group: "Projects", scope: "project", requiresEnvironment: false },
  { key: "overview", label: "Overview", path: "overview", icon: Home, group: "Environment", scope: "environment", requiresEnvironment: true },
  { key: "metadata", label: "Metadata", path: "metadata", icon: Database, group: "Environment", scope: "environment", requiresEnvironment: true, capability: "metadata" },
  { key: "assets", label: "Assets", path: "assets", icon: TableProperties, group: "Environment", scope: "environment", requiresEnvironment: true, capability: "metadata" },
  { key: "lineage", label: "Lineage", path: "lineage", icon: GitBranch, group: "Environment", scope: "environment", requiresEnvironment: true, capability: "metadata" },
  { key: "monitoring", label: "Monitoring", path: "monitoring", icon: Activity, group: "Environment", scope: "environment", requiresEnvironment: true, capability: "metadata" },
  { key: "master-data", label: "Master data", path: "master-data", icon: Boxes, group: "Environment", scope: "environment", requiresEnvironment: true, capability: "master-data" },
  { key: "sources", label: "Sources", path: "sources", icon: SlidersHorizontal, group: "Environment", scope: "environment", requiresEnvironment: true },
  { key: "settings", label: "Settings", path: "settings", icon: Settings, group: "Studio", scope: "global", requiresEnvironment: false }
];

export const defaultModule: ModuleKey = "projects";
export const environmentDefaultModule: ModuleKey = "overview";
export const projectDefaultModule: ModuleKey = "project-overview";
export const monitoringDefaultPage: MonitoringPageKey = "overview";
export const monitoringPages: MonitoringPageKey[] = [
  "overview",
  "jobs",
  "dataflows",
  "failures",
  "diagnostics",
  "performance",
  "volume",
  "maintenance",
  "freshness"
];
export const moduleGroups: ModuleGroup[] = ["Projects", "Environment", "Studio"];

export function isModuleKey(value: string | undefined): value is ModuleKey {
  return modules.some((module) => module.key === value);
}

export function moduleByKey(key: ModuleKey) {
  return modules.find((module) => module.key === key);
}

/**
 * Whether a navigation entry should be shown given the set of enabled
 * capability modules. Core entries (no capability) are always visible.
 */
export function isModuleEnabled(module: StudioModule, enabledCapabilities: ReadonlySet<CapabilityKey>): boolean {
  return module.capability === undefined || enabledCapabilities.has(module.capability);
}

export function isModuleKeyEnabled(key: ModuleKey, enabledCapabilities: ReadonlySet<CapabilityKey>): boolean {
  const module = moduleByKey(key);
  return module ? isModuleEnabled(module, enabledCapabilities) : false;
}

export function isMonitoringPageKey(value: string | undefined): value is MonitoringPageKey {
  return monitoringPages.some((page) => page === value);
}
