import { useCallback, useEffect, useMemo, useState } from "react";
import {
  defaultModule,
  environmentDefaultModule,
  moduleByKey,
  monitoringDefaultPage,
  projectDefaultModule,
  type ModuleKey,
  type ModuleScope,
  type MonitoringPageKey
} from "./moduleRegistry";
import { parseRoute, pushRoute, replaceRoute, type StudioRoute } from "./routes";

const MOBILE_BREAKPOINT_QUERY = "(max-width: 620px)";

export interface StudioRouter {
  route: StudioRoute;
  activeModule: ReturnType<typeof moduleByKey>;
  activeScope: ModuleScope;
  sidebarCollapsed: boolean;
  setRoute: (next: StudioRoute) => void;
  setStudioRoute: (next: StudioRoute, replace?: boolean) => void;
  navigate: (module: ModuleKey, search?: string) => void;
  navigateMonitoringPage: (page: MonitoringPageKey) => void;
  selectProject: (projectId: number | null) => void;
  selectEnvironment: (environmentId: number | null) => void;
  openProject: (projectId: number) => void;
  openProjectEnvironment: (projectId: number, environmentId: number) => void;
  openProjectEnvironments: (projectId: number) => void;
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
      const target = moduleByKey(module);
      if (!target || target.scope === "global") {
        setStudioRoute({ projectId: null, environmentId: null, module: target?.key ?? "projects", search });
        return;
      }
      if (target.scope === "project") {
        if (!route.projectId) return;
        setStudioRoute({ projectId: route.projectId, environmentId: null, module, search });
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

  const selectProject = useCallback(
    (projectId: number | null) => {
      if (!projectId) {
        setRoute({ projectId: null, environmentId: null, module: defaultModule });
        window.history.pushState({}, "", "/projects");
        return;
      }
      setStudioRoute({ projectId, environmentId: null, module: projectDefaultModule });
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
    (projectId: number) => setStudioRoute({ projectId, environmentId: null, module: projectDefaultModule }),
    [setStudioRoute]
  );

  const openProjectEnvironment = useCallback(
    (projectId: number, environmentId: number) => setStudioRoute({ projectId, environmentId, module: "overview" }),
    [setStudioRoute]
  );

  const openProjectEnvironments = useCallback(
    (projectId: number) => setStudioRoute({ projectId, environmentId: null, module: "environments" }),
    [setStudioRoute]
  );

  const openEnvironmentSources = useCallback(
    (projectId: number, environmentId: number) => setStudioRoute({ projectId, environmentId, module: "sources" }),
    [setStudioRoute]
  );

  const toggleSidebar = useCallback(() => {
    const isMobile = window.matchMedia(MOBILE_BREAKPOINT_QUERY).matches;
    if (!isMobile) {
      setSidebarCollapsed(false);
      return;
    }
    setSidebarCollapsed((current) => !current);
  }, []);

  return useMemo(
    () => ({
      route,
      activeModule,
      activeScope,
      sidebarCollapsed,
      setRoute,
      setStudioRoute,
      navigate,
      navigateMonitoringPage,
      selectProject,
      selectEnvironment,
      openProject,
      openProjectEnvironment,
      openProjectEnvironments,
      openEnvironmentSources,
      toggleSidebar
    }),
    [
      route,
      activeModule,
      activeScope,
      sidebarCollapsed,
      setStudioRoute,
      navigate,
      navigateMonitoringPage,
      selectProject,
      selectEnvironment,
      openProject,
      openProjectEnvironment,
      openProjectEnvironments,
      openEnvironmentSources,
      toggleSidebar
    ]
  );
}
