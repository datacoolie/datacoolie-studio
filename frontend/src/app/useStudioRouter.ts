import { useCallback, useEffect, useMemo, useState } from "react";
import {
  defaultModule,
  environmentDefaultModule,
  moduleByKey,
  monitoringDefaultPage,
  projectDefaultSection,
  type ModuleKey,
  type ModuleScope,
  type MonitoringPageKey,
  type ProjectSectionKey
} from "./moduleRegistry";
import { parseRoute, pushRoute, replaceRoute, type StudioRoute } from "./routes";
import type { MetadataNavigationTarget } from "../shared/metadataNavigation";

const MOBILE_BREAKPOINT_QUERY = "(max-width: 620px)";

export interface StudioRouter {
  route: StudioRoute;
  activeModule: ReturnType<typeof moduleByKey>;
  activeScope: ModuleScope;
  sidebarCollapsed: boolean;
  metadataNavigation: MetadataNavigationTarget | null;
  setRoute: (next: StudioRoute) => void;
  setStudioRoute: (next: StudioRoute, replace?: boolean) => void;
  navigate: (module: ModuleKey, search?: string) => void;
  navigateMetadata: (target: MetadataNavigationTarget) => void;
  clearMetadataNavigation: () => void;
  navigateMonitoringPage: (page: MonitoringPageKey) => void;
  navigateProjectSection: (section: ProjectSectionKey) => void;
  selectProject: (projectId: number | null) => void;
  selectEnvironment: (environmentId: number | null) => void;
  openProject: (projectId: number) => void;
  openProjectEnvironment: (projectId: number, environmentId: number) => void;
  openProjectEnvironments: (projectId: number) => void;
  openProjectReferenceMappings: (projectId: number) => void;
  openEnvironmentSources: (projectId: number, environmentId: number) => void;
  toggleSidebar: () => void;
}

/**
 * Owns URL/route state, browser history sync, and all navigation/selection
 * intents. Holds no domain data so it can be consumed by the workspace data
 * layer and the shell independently.
 */
export function useStudioRouter(): StudioRouter {
  const [route, setRoute] = useState<StudioRoute>(() => parseRoute());
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => window.matchMedia(MOBILE_BREAKPOINT_QUERY).matches);
  const [metadataNavigation, setMetadataNavigation] = useState<MetadataNavigationTarget | null>(null);

  const activeModule = moduleByKey(route.module);
  const activeScope = activeModule?.scope ?? "global";

  useEffect(() => {
    function handlePopState() {
      setRoute(parseRoute());
    }
    window.addEventListener("popstate", handlePopState);
    return () => window.removeEventListener("popstate", handlePopState);
  }, []);

  useEffect(() => {
    const mediaQuery = window.matchMedia(MOBILE_BREAKPOINT_QUERY);
    const syncSidebarMode = () => setSidebarCollapsed(mediaQuery.matches);
    syncSidebarMode();
    mediaQuery.addEventListener("change", syncSidebarMode);
    return () => mediaQuery.removeEventListener("change", syncSidebarMode);
  }, []);

  const setStudioRoute = useCallback((next: StudioRoute, replace = false) => {
    setRoute(next);
    if (replace) {
      replaceRoute(next);
    } else {
      pushRoute(next);
    }
  }, []);

  const navigate = useCallback(
    (module: ModuleKey, search?: string) => {
      if (module !== "metadata") setMetadataNavigation(null);
      const target = moduleByKey(module);
      if (!target || target.scope === "global") {
        setStudioRoute({ projectId: null, environmentId: null, module: target?.key ?? "projects", search });
        return;
      }
      if (!route.projectId || !route.environmentId) return;
      setStudioRoute({
        projectId: route.projectId,
        environmentId: route.environmentId,
        module,
        monitoringPage:
          module === "monitoring" && route.module === "monitoring"
            ? route.monitoringPage ?? monitoringDefaultPage
            : monitoringDefaultPage,
        search
      });
    },
    [route, setStudioRoute]
  );

  const navigateMonitoringPage = useCallback(
    (page: MonitoringPageKey) => {
      if (!route.projectId || !route.environmentId) return;
      setStudioRoute({ ...route, module: "monitoring", monitoringPage: page });
    },
    [route, setStudioRoute]
  );

  const navigateMetadata = useCallback(
    (target: MetadataNavigationTarget) => {
      if (!route.projectId || !route.environmentId) return;
      setMetadataNavigation(target);
      setStudioRoute({
        projectId: route.projectId,
        environmentId: route.environmentId,
        module: "metadata",
        monitoringPage: monitoringDefaultPage,
      });
    },
    [route.environmentId, route.projectId, setStudioRoute],
  );

  const clearMetadataNavigation = useCallback(() => setMetadataNavigation(null), []);

  const navigateProjectSection = useCallback(
    (section: ProjectSectionKey) => {
      if (!route.projectId) return;
      setStudioRoute({ projectId: route.projectId, environmentId: null, module: "projects", projectSection: section });
    },
    [route.projectId, setStudioRoute]
  );

  const selectProject = useCallback(
    (projectId: number | null) => {
      if (!projectId) {
        setRoute({ projectId: null, environmentId: null, module: defaultModule });
        window.history.pushState({}, "", "/projects");
        return;
      }
      setStudioRoute({ projectId, environmentId: null, module: "projects", projectSection: projectDefaultSection });
    },
    [setStudioRoute]
  );

  const selectEnvironment = useCallback(
    (environmentId: number | null) => {
      if (!route.projectId || !environmentId) return;
      const module = activeScope === "environment" ? route.module : environmentDefaultModule;
      setStudioRoute({ projectId: route.projectId, environmentId, module });
    },
    [route.projectId, route.module, activeScope, setStudioRoute]
  );

  const openProject = useCallback(
    (projectId: number) => setStudioRoute({ projectId, environmentId: null, module: "projects", projectSection: projectDefaultSection }),
    [setStudioRoute]
  );

  const openProjectEnvironment = useCallback(
    (projectId: number, environmentId: number) => setStudioRoute({ projectId, environmentId, module: "overview" }),
    [setStudioRoute]
  );

  const openProjectEnvironments = useCallback(
    (projectId: number) => setStudioRoute({ projectId, environmentId: null, module: "projects", projectSection: "environments" }),
    [setStudioRoute]
  );

  const openProjectReferenceMappings = useCallback(
    (projectId: number) => setStudioRoute({ projectId, environmentId: null, module: "projects", projectSection: "reference-mappings" }),
    [setStudioRoute]
  );

  const openEnvironmentSources = useCallback(
    (projectId: number, environmentId: number) => setStudioRoute({ projectId, environmentId, module: "sources" }),
    [setStudioRoute]
  );

  const toggleSidebar = useCallback(() => {
    setSidebarCollapsed((current) => !current);
  }, []);

  return useMemo(
    () => ({
      route,
      activeModule,
      activeScope,
      sidebarCollapsed,
      metadataNavigation,
      setRoute,
      setStudioRoute,
      navigate,
      navigateMetadata,
      clearMetadataNavigation,
      navigateMonitoringPage,
      navigateProjectSection,
      selectProject,
      selectEnvironment,
      openProject,
      openProjectEnvironment,
      openProjectEnvironments,
      openProjectReferenceMappings,
      openEnvironmentSources,
      toggleSidebar
    }),
    [
      route,
      activeModule,
      activeScope,
      sidebarCollapsed,
      metadataNavigation,
      setStudioRoute,
      navigate,
      navigateMetadata,
      clearMetadataNavigation,
      navigateMonitoringPage,
      navigateProjectSection,
      selectProject,
      selectEnvironment,
      openProject,
      openProjectEnvironment,
      openProjectEnvironments,
      openProjectReferenceMappings,
      openEnvironmentSources,
      toggleSidebar
    ]
  );
}
