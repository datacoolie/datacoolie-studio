import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { AppShell } from "./app/AppShell";
import { ModuleView } from "./app/ModuleView";
import { environmentDefaultModule, isModuleKeyEnabled, monitoringDefaultPage } from "./app/moduleRegistry";
import { useStudioModules } from "./app/useStudioModules";
import { useStudioRouter } from "./app/useStudioRouter";
import { useStudioWorkspace } from "./app/useStudioWorkspace";
import { useStudioDiagnostics } from "./features/settings/hooks/useStudioDiagnostics";
import { useStudioSettings } from "./features/settings/hooks/useStudioSettings";
import { environmentQueryKeys, useEnvironmentContextQuery } from "./features/environments/environmentQueries";
import { sourceCheckIntervalMs } from "./app/environmentHeaderResource";

export function App() {
  const router = useStudioRouter();
  const queryClient = useQueryClient();
  const modules = useStudioModules();
  const diagnostics = useStudioDiagnostics(router.route.module === "settings");
  const settings = useStudioSettings({
    onSaved: () => {
      const environmentId = router.route.environmentId;
      if (!environmentId) return undefined;
      return queryClient.invalidateQueries({ queryKey: ["environments", environmentId] });
    }
  });
  const environmentContext = useEnvironmentContextQuery(
    router.route.environmentId,
    sourceCheckIntervalMs(settings.settings?.source_check_interval_seconds),
    router.route.module,
  );
  const workspace = useStudioWorkspace(router, {
    onEnvironmentChanged: async (environmentId) => {
      await queryClient.invalidateQueries({ queryKey: environmentQueryKeys.context(environmentId) });
    },
  });

  // Redirect away from disabled capability modules reached directly via URL.
  useEffect(() => {
    if (modules.loading) return;
    if (isModuleKeyEnabled(router.route.module, modules.enabledCapabilities)) return;
    router.setStudioRoute({ ...router.route, module: environmentDefaultModule, monitoringPage: monitoringDefaultPage }, true);
  }, [router, modules.loading, modules.enabledCapabilities]);

  const error = workspace.error
    ?? (environmentContext.data && router.route.projectId !== environmentContext.data.environment.project_id
      ? "Environment does not belong to the project in this URL."
      : null)
    ?? (environmentContext.error instanceof Error ? environmentContext.error.message : null)
    ?? settings.error
    ?? (router.route.module === "settings" ? diagnostics.error : null)
    ?? modules.error;

  return (
    <AppShell
      activeModule={router.route.module}
      activeScope={router.activeScope}
      project={environmentContext.data?.project ?? workspace.selectedProject ?? workspace.selectedProjectSummary}
      environment={environmentContext.data?.environment
        ?? workspace.environments.find((item) => item.id === router.route.environmentId)
        ?? null}
      sidebarCollapsed={router.sidebarCollapsed}
      enabledCapabilities={modules.enabledCapabilities}
      metadataSourceCount={environmentContext.data?.source_counts.metadata ?? 0}
      logPathCount={environmentContext.data?.source_counts.logs ?? 0}
      freshness={environmentContext.data?.freshness ?? null}
      error={error}
      onNavigate={router.navigate}
      onToggleSidebar={router.toggleSidebar}
      onProjectSelect={router.selectProject}
      onOpenProject={router.openProject}
    >
      <ModuleView
        router={router}
        workspace={workspace}
        settings={settings}
        diagnostics={diagnostics}
        modules={modules}
        environmentContext={environmentContext.data}
      />
    </AppShell>
  );
}
