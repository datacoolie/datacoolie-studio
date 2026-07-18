import { lazy, Suspense } from "react";
import { MasterDataPage } from "../features/master-data/MasterDataPage";
import { OverviewPage } from "../features/overview/OverviewPage";
import { ProjectDetailPage } from "../features/projects/ProjectDetailPage";
import { ProjectsPage } from "../features/projects/ProjectsPage";
import { SettingsPage } from "../features/settings/SettingsPage";
import { SourcesPage } from "../features/sources/SourcesPage";
import { lineageDataflowFocusSearch } from "../shared/lineageNavigation";
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
      if (route.projectId) {
        return (
          <ProjectDetailPage
            project={workspace.selectedProjectSummary}
            projectId={route.projectId}
            projectName={workspace.selectedProject?.name ?? workspace.selectedProjectSummary?.name ?? null}
            section={route.projectSection ?? "overview"}
            environments={workspace.environments}
            mappings={workspace.projectReferenceMappings}
            busy={workspace.busy}
            routeSearch={route.search}
            onDeleteProject={workspace.deleteProject}
            onSectionChange={router.navigateProjectSection}
            onOpenEnvironment={router.openProjectEnvironment}
            onConfigureSources={router.openEnvironmentSources}
            onCreateEnvironment={workspace.createEnvironment}
            onDeleteEnvironment={workspace.deleteEnvironment}
            onQuickCreateEnvironment={async (projectId, name) => {
              const envId = await workspace.createEnvironment(name, projectId);
              if (envId) router.openEnvironmentSources(projectId, envId);
            }}
            onReloadMappings={workspace.reloadProjectReferenceMappings}
            onCreateMapping={workspace.createProjectReferenceMapping}
            onUpdateMapping={workspace.updateProjectReferenceMapping}
            onDeleteMapping={workspace.deleteProjectReferenceMapping}
          />
        );
      }
      return (
        <ProjectsPage
          projects={workspace.projectSummaries}
          busy={workspace.busy}
          onCreateProject={workspace.createProject}
          onOpenEnvironment={router.openProjectEnvironment}
          onOpenProject={router.openProject}
          onQuickCreateEnvironment={async (projectId, name) => {
            const envId = await workspace.createEnvironment(name, projectId);
            if (envId) router.openEnvironmentSources(projectId, envId);
          }}
        />
      );
    case "settings":
      return (
        <SettingsPage
          settings={settings.settings}
          busy={settings.busy}
          onSaveSettings={settings.saveSettings}
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
          overview={workspace.overview}
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
          onGetDeleteImpact={workspace.getSourceDeleteImpact}
          onValidateSource={workspace.validateSource}
          onSyncSource={workspace.syncSource}
          onRunSourceBatch={workspace.runSourceBatch}
          onRefreshSources={workspace.refreshCurrentEnvironment}
          syncStatuses={workspace.sourceSyncStatuses}
        />
      );
    case "metadata":
      return (
        <MetadataExplorer
          editorDocument={workspace.metadataEditorDocument}
          serverDraft={workspace.metadataEditorDraft}
          routeSearch={route.search}
          metadataNavigation={router.metadataNavigation}
          onMetadataNavigationConsumed={router.clearMetadataNavigation}
          onFocusInLineage={(target) => router.navigate("lineage", lineageDataflowFocusSearch(target))}
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
          environmentId={route.environmentId}
          assets={workspace.assets}
          metadataEditorDocument={workspace.metadataEditorDocument}
          metadataEditorDraft={workspace.metadataEditorDraft}
          onEnsureMetadataEditor={workspace.ensureMetadataEditorContext}
          metadataBusy={workspace.busy}
          onValidateMetadata={workspace.validateMetadataEditorDocument}
          onSaveMetadataDraft={workspace.saveMetadataEditorDraft}
          onSaveMetadata={workspace.saveMetadataEditorDocument}
          loading={workspace.loading}
          routeSearch={route.search}
          onFocusInLineage={(assetId) => {
            router.navigate("lineage", `focusAsset=${encodeURIComponent(assetId)}`);
          }}
          onFocusDataflowInLineage={(target) => router.navigate("lineage", lineageDataflowFocusSearch(target))}
          onOpenMetadata={(target) => {
            router.navigateMetadata(target);
          }}
          projectMappings={workspace.projectReferenceMappings}
          mappingBusy={workspace.busy}
          onCreateReferenceMapping={workspace.createProjectReferenceMapping}
          onUpdateReferenceMapping={workspace.updateProjectReferenceMapping}
          onDeleteReferenceMapping={workspace.deleteProjectReferenceMapping}
          onRefreshReferenceMappings={workspace.reloadProjectReferenceMappings}
        />
      );
    case "lineage":
      return (
        <LineageView
          environmentId={route.environmentId}
          lineage={workspace.lineage}
          latestStatus={workspace.latestStatus}
          onEnsureLatestRuns={workspace.ensureLatestRuns}
          metadataEditorDocument={workspace.metadataEditorDocument}
          metadataEditorDraft={workspace.metadataEditorDraft}
          onEnsureMetadataEditor={workspace.ensureMetadataEditorContext}
          busy={workspace.busy}
          onValidateMetadata={workspace.validateMetadataEditorDocument}
          onSaveMetadataDraft={workspace.saveMetadataEditorDraft}
          onSaveMetadata={workspace.saveMetadataEditorDocument}
          loading={workspace.loading}
          routeSearch={route.search}
          onOpenMetadata={(target) => router.navigateMetadata(target)}
          projectMappings={workspace.projectReferenceMappings}
          onCreateReferenceMapping={workspace.createProjectReferenceMapping}
          onUpdateReferenceMapping={workspace.updateProjectReferenceMapping}
          onDeleteReferenceMapping={workspace.deleteProjectReferenceMapping}
          onRefreshReferenceMappings={workspace.refreshCurrentEnvironment}
        />
      );
    case "master-data":
      return <MasterDataPage />;
    case "monitoring":
      return (
        <MonitoringView
          environmentId={route.environmentId}
          sourceCacheVersion={workspace.environmentFreshness?.source_cache_version}
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
