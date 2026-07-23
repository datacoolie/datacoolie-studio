import {
  defaultModule,
  environmentDefaultModule,
  isModuleKey,
  isMonitoringPageKey,
  moduleByKey,
  monitoringDefaultPage,
  projectDefaultSection,
  type ModuleKey,
  type MonitoringPageKey,
  type ProjectSectionKey
} from "./moduleRegistry";

export interface StudioRoute {
  projectId: number | null;
  environmentId: number | null;
  module: ModuleKey;
  projectSection?: ProjectSectionKey;
  monitoringPage?: MonitoringPageKey;
  search?: string;
}

const routePattern = /^\/projects\/(\d+)\/environments\/(\d+)\/([a-z-]+)(?:\/([a-z-]+))?\/?$/;
const projectPattern = /^\/projects\/(\d+)\/?$/;
const projectSectionPattern = /^\/projects\/(\d+)\/([a-z-]+)\/?$/;
const projectsPattern = /^\/projects\/?$/;
const settingsPattern = /^\/settings\/?$/;

export function parseRoute(pathname = defaultPathname(), search = defaultSearch()): StudioRoute {
  if (projectsPattern.test(pathname)) {
    return { projectId: null, environmentId: null, module: "projects", monitoringPage: monitoringDefaultPage, search };
  }
  if (settingsPattern.test(pathname)) {
    return { projectId: null, environmentId: null, module: "settings", monitoringPage: monitoringDefaultPage, search };
  }
  const projectMatch = projectPattern.exec(pathname);
  if (projectMatch) {
    return {
      projectId: Number(projectMatch[1]),
      environmentId: null,
      module: "projects",
      projectSection: projectDefaultSection,
      monitoringPage: monitoringDefaultPage,
      search
    };
  }
  const projectSectionMatch = projectSectionPattern.exec(pathname);
  if (projectSectionMatch) {
    const section = projectSection(projectSectionMatch[2]);
    return {
      projectId: Number(projectSectionMatch[1]),
      environmentId: null,
      module: "projects",
      projectSection: section,
      monitoringPage: monitoringDefaultPage,
      search,
    };
  }
  const match = routePattern.exec(pathname);
  if (match) {
    const module = isModuleKey(match[3]) && moduleByKey(match[3])?.scope === "environment" ? match[3] : environmentDefaultModule;
    const monitoringPage = module === "monitoring" && isMonitoringPageKey(match[4]) ? match[4] : monitoringDefaultPage;
    return {
      projectId: Number(match[1]),
      environmentId: Number(match[2]),
      module,
      monitoringPage,
      search
    };
  }
  return { projectId: null, environmentId: null, module: defaultModule, monitoringPage: monitoringDefaultPage, search };
}

export function modulePath(projectId: number, environmentId: number, module: ModuleKey, monitoringPage = monitoringDefaultPage) {
  if (module === "monitoring") {
    return `/projects/${projectId}/environments/${environmentId}/monitoring/${monitoringPage}`;
  }
  return `/projects/${projectId}/environments/${environmentId}/${module}`;
}

export function pushRoute(next: StudioRoute) {
  const path = routePath(next);
  if (!path) return;
  if (`${window.location.pathname}${window.location.search}` !== path) {
    window.history.pushState({}, "", path);
  }
}

export function replaceRoute(next: StudioRoute) {
  const path = routePath(next);
  if (!path) return;
  if (`${window.location.pathname}${window.location.search}` !== path) {
    window.history.replaceState({}, "", path);
  }
}

function routePath(next: StudioRoute) {
  const search = normalizeSearch(next.search);
  if (next.projectId && next.environmentId) {
    return `${modulePath(next.projectId, next.environmentId, next.module, next.monitoringPage ?? monitoringDefaultPage)}${search}`;
  }
  if (next.projectId && next.module === "projects") {
    if (next.projectSection === "environments") {
      return `/projects/${next.projectId}/environments${search}`;
    }
    if (next.projectSection === "reference-mappings") {
      return `/projects/${next.projectId}/reference-mappings${search}`;
    }
    return `/projects/${next.projectId}${search}`;
  }
  if (next.module === "projects") {
    return `/projects${search}`;
  }
  if (next.module === "settings") {
    return `/settings${search}`;
  }
  return null;
}

function normalizeSearch(search: string | undefined) {
  if (!search) return "";
  return search.startsWith("?") ? search : `?${search}`;
}

function projectSection(value: string | undefined): ProjectSectionKey {
  if (value === "environments" || value === "reference-mappings") return value;
  return projectDefaultSection;
}

function defaultPathname() {
  return typeof window === "undefined" ? "/projects" : window.location.pathname;
}

function defaultSearch() {
  return typeof window === "undefined" ? "" : window.location.search;
}
