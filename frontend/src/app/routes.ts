import {
  defaultModule,
  environmentDefaultModule,
  isModuleKey,
  isMonitoringPageKey,
  moduleByKey,
  monitoringDefaultPage,
  type ModuleKey,
  type MonitoringPageKey
} from "./moduleRegistry";

export interface StudioRoute {
  projectId: number | null;
  environmentId: number | null;
  module: ModuleKey;
  monitoringPage?: MonitoringPageKey;
  search?: string;
}

const routePattern = /^\/projects\/(\d+)\/envs\/(\d+)\/([a-z-]+)(?:\/([a-z-]+))?\/?$/;
const projectPattern = /^\/projects\/(\d+)\/?$/;
const projectEnvironmentsPattern = /^\/projects\/(\d+)\/environments\/?$/;
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
    return { projectId: Number(projectMatch[1]), environmentId: null, module: "project-overview", monitoringPage: monitoringDefaultPage, search };
  }
  const environmentsMatch = projectEnvironmentsPattern.exec(pathname);
  if (environmentsMatch) {
    return { projectId: Number(environmentsMatch[1]), environmentId: null, module: "environments", monitoringPage: monitoringDefaultPage, search };
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
    return `/projects/${projectId}/envs/${environmentId}/monitoring/${monitoringPage}`;
  }
  return `/projects/${projectId}/envs/${environmentId}/${module}`;
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
  if (next.projectId && next.module === "project-overview") {
    return `/projects/${next.projectId}${search}`;
  }
  if (next.projectId && next.module === "environments") {
    return `/projects/${next.projectId}/environments${search}`;
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

function defaultPathname() {
  return typeof window === "undefined" ? "/projects" : window.location.pathname;
}

function defaultSearch() {
  return typeof window === "undefined" ? "" : window.location.search;
}
