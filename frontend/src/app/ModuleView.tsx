import { lazy, Suspense } from "react";
import { EnvironmentsPage } from "../features/environments/EnvironmentsPage";
import { MasterDataPage } from "../features/master-data/MasterDataPage";
import { OverviewPage } from "../features/overview/OverviewPage";
import { ProjectOverviewPage } from "../features/project-overview/ProjectOverviewPage";
import { ProjectsPage } from "../features/projects/ProjectsPage";
import { SettingsPage } from "../features/settings/SettingsPage";
import { SourcesPage } from "../features/sources/SourcesPage";
import type { StudioSettingsState } from "../features/settings/hooks/useStudioSettings";
import { EmptyState } from "../shared/components/EmptyState";
import { monitoringDefaultPage } from "./moduleRegistry";
import type { StudioModulesState } from "./useStudioModules";
import type { StudioRouter } from "./useStudioRouter";
import type { StudioWorkspace } from "./useStudioWorkspace";

// Heavy, graph/chart-heavy pages are code-split so the initial bundle stays small.
const MetadataExplorer = lazy(() =>
  import("../features/metadata-explorer/MetadataExplorer").then((module) => ({ default: module.MetadataExplorer }))
);
const LineageView = lazy(() =>
  import("../features/lineage/LineageView").then((module) => ({ default: module.LineageView }))
);
const MonitoringView = lazy(() =>
  import("../features/monitoring/MonitoringView").then((module) => ({ default: module.MonitoringView }))
);
const AssetsView = lazy(() =>
  import("../features/assets/AssetsView").then((module) => ({ default: module.AssetsView }))
);

interface ModuleViewProps {
  router: StudioRouter;
  workspace: StudioWorkspace;
  settings: StudioSettingsState;
  modules: StudioModulesState;
}

/** Resolves the active route module to its feature page. */
export function ModuleView(props: ModuleViewProps) {
  return (
    <Suspense fallback={<EmptyState title="Loading…" />}>
      <ResolveModule {...props} />
    </Suspense>
  );
}

function ResolveModule({ router, workspace, settings, modules }: ModuleViewProps) {
  const { route } = router;

  switch (route.module) {
    case "projects":
      return (
        <ProjectsPage
          projects={workspace.projectSummaries}
          busy={workspace.busy}
          onCreateProject={workspace.createProject}
          onOpenEnvironment={router.openProjectEnvironment}
          onOpenProject={router.openProject}
          onOpenProjectEnvironments={router.openProjectEnvironments}
          onQuickCreateEnvironment={async (projectId, name) => {
            const envId = await workspace.createEnvironment(name, projectId);
            if (envId) router.openEnvironmentSources(projectId, envId);
          }}
        />
      );
    case "project-overview":
      return (
        <ProjectOverviewPage
          project={workspace.selectedProjectSummary}
          busy={workspace.busy}
          onOpenEnvironment={router.openProjectEnvironment}
          onOpenEnvironments={router.openProjectEnvironments}
          onQuickCreateEnvironment={async (projectId, name) => {
            const envId = await workspace.createEnvironment(name, projectId);
            if (envId) router.openEnvironmentSources(projectId, envId);
          }}
        />
      );
    case "environments":
      return (
        <EnvironmentsPage
          project={workspace.selectedProjectSummary}
          busy={workspace.busy}
          onCreateEnvironment={workspace.createEnvironment}
          onDeleteEnvironment={workspace.deleteEnvironment}
          onOpenEnvironment={router.openProjectEnvironment}
          onConfigureSources={router.openEnvironmentSources}
        />
      );
    case "settings":
      return (
        <SettingsPage
          settings={settings.settings}
          busy={settings.busy}
          onSaveTimezone={settings.saveTimezone}
          onReload={settings.reload}
          modules={modules.modules}
          modulesBusyKey={modules.busyKey}
          onToggleModule={modules.setEnabled}
        />
      );
    default:
      break;
  }

  if (!route.environmentId) {
    return <EmptyState title="Select an environment" />;
  }

  switch (route.module) {
    case "overview":
      return (
        <OverviewPage
          metadata={workspace.metadata}
          lineage={workspace.lineage}
          monitoringReport={workspace.monitoringReport}
          metadataSources={workspace.metadataSources}
          logPaths={workspace.logPaths}
          loading={workspace.loading}
          onNavigate={router.navigate}
        />
      );
    case "sources":
      return (
        <SourcesPage
          metadataSources={workspace.metadataSources}
          logPaths={workspace.logPaths}
          codeArtifacts={workspace.codeArtifacts}
          busy={workspace.busy || workspace.loading}
          selectedEnvironmentId={route.environmentId}
          onImportMetadataSources={workspace.importMetadataSources}
          onImportDatacoolieProjectSources={workspace.importDatacoolieProjectSources}
          onAddLogPath={workspace.addLogPath}
          onAddCodeArtifact={workspace.addCodeArtifact}
          onUpdateSource={workspace.updateSource}
          onDeleteSource={workspace.deleteSource}
          onDeleteSources={workspace.deleteSources}
          onValidateSource={workspace.validateSource}
          onSyncSource={workspace.syncSource}
          syncStatuses={workspace.sourceSyncStatuses}
        />
      );
    case "metadata":
      return (
        <MetadataExplorer
          metadata={workspace.metadata}
          editorDocument={workspace.metadataEditorDocument}
          serverDraft={workspace.metadataEditorDraft}
          routeSearch={route.search}
          loading={workspace.loading}
          busy={workspace.busy}
          onValidate={workspace.validateMetadataEditorDocument}
          onSaveDraft={workspace.saveMetadataEditorDraft}
          onDiscardDraft={workspace.discardMetadataEditorDraft}
          onSave={workspace.saveMetadataEditorDocument}
          onListBackups={workspace.listMetadataBackups}
          onPreviewBackup={workspace.previewMetadataBackup}
          onRestoreBackup={workspace.restoreMetadataBackup}
          onDeleteBackup={workspace.deleteMetadataBackup}
          onClearBackups={workspace.clearMetadataBackups}
        />
      );
    case "assets":
      return (
        <AssetsView
          assets={workspace.assets}
          loading={workspace.loading}
          routeSearch={route.search}
          onFocusInLineage={(assetId) => {
            router.navigate("lineage", `focusAsset=${encodeURIComponent(assetId)}`);
          }}
          onOpenMetadata={(query) => {
            router.navigate("metadata", `sheet=dataflows&q=${encodeURIComponent(query)}`);
          }}
        />
      );
    case "lineage":
      return (
        <LineageView
          lineage={workspace.lineage}
          latestStatus={workspace.latestStatus}
          loading={workspace.loading}
          routeSearch={route.search}
        />
      );
    case "master-data":
      return <MasterDataPage />;
    case "monitoring":
      return (
        <MonitoringView
          environmentId={route.environmentId}
          report={workspace.monitoringReport}
          loading={workspace.loading}
          activePage={route.monitoringPage ?? monitoringDefaultPage}
          onPageChange={router.navigateMonitoringPage}
        />
      );
    default:
      return <EmptyState title="Unknown module" />;
  }
}
