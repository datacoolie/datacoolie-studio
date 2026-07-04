import { useEffect } from "react";
import { AppShell } from "./app/AppShell";
import { ModuleView } from "./app/ModuleView";
import { environmentDefaultModule, isModuleKeyEnabled, monitoringDefaultPage } from "./app/moduleRegistry";
import { useStudioModules } from "./app/useStudioModules";
import { useStudioRouter } from "./app/useStudioRouter";
import { useStudioWorkspace } from "./app/useStudioWorkspace";
import { useStudioSettings } from "./features/settings/hooks/useStudioSettings";

export function App() {
  const router = useStudioRouter();
  const workspace = useStudioWorkspace(router);
  const modules = useStudioModules();
  const settings = useStudioSettings({
    onSaved: () => {
      const { environmentId, module } = router.route;
      if (environmentId && (module === "monitoring" || module === "overview")) {
        return workspace.refreshCurrentEnvironment();
      }
      return undefined;
    }
  });

  // Redirect away from disabled capability modules reached directly via URL.
  useEffect(() => {
    if (modules.loading) return;
    if (isModuleKeyEnabled(router.route.module, modules.enabledCapabilities)) return;
    router.setStudioRoute({ ...router.route, module: environmentDefaultModule, monitoringPage: monitoringDefaultPage }, true);
  }, [router, modules.loading, modules.enabledCapabilities]);

  const error = workspace.error ?? settings.error ?? modules.error;

  return (
    <AppShell
      activeModule={router.route.module}
      activeScope={router.activeScope}
      projects={workspace.projects}
      environments={workspace.environments}
      selectedProjectId={router.route.projectId}
      selectedEnvironmentId={router.route.environmentId}
      sidebarCollapsed={router.sidebarCollapsed}
      enabledCapabilities={modules.enabledCapabilities}
      metadataSourceCount={workspace.metadataSources.length}
      logPathCount={workspace.logPaths.length}
      freshness={workspace.environmentFreshness}
      error={error}
      onNavigate={router.navigate}
      onToggleSidebar={router.toggleSidebar}
      onProjectSelect={router.selectProject}
      onEnvironmentSelect={router.selectEnvironment}
    >
      <ModuleView router={router} workspace={workspace} settings={settings} modules={modules} />
    </AppShell>
  );
}
