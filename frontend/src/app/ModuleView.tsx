import { lazy, Suspense, useEffect } from "react";
import { MasterDataPage } from "../features/master-data/MasterDataPage";
import { SourcesPage } from "../features/sources/SourcesPage";
import { useEnvironmentOverviewQuery } from "../features/environments/environmentQueries";
import { useLineageGraph } from "../features/lineage/lineageQueries";
import { MonitoringRoute } from "../features/monitoring/MonitoringRoute";
import { lineageDataflowFocusSearch } from "../shared/lineageNavigation";
import type { StudioDiagnosticsState } from "../features/settings/hooks/useStudioDiagnostics";
import type { StudioSettingsState } from "../features/settings/hooks/useStudioSettings";
import { EmptyState } from "../shared/components/EmptyState";
import { monitoringDefaultPage } from "./moduleRegistry";
import type { StudioModulesState } from "./useStudioModules";
import type { StudioRouter } from "./useStudioRouter";
import type { StudioWorkspace } from "./useStudioWorkspace";
import type { EnvironmentContext } from "../shared/api/domainTypes";
import { toErrorMessage } from "../shared/lib/errors";
import { useNotificationCenter } from "../shared/notifications/NotificationCenter";

// Heavy, graph/chart-heavy pages are code-split so the initial bundle stays small.
const MetadataExplorer = lazy(() =>
  import("../features/metadata-explorer/MetadataExplorer").then((module) => ({ default: module.MetadataExplorer }))
);
const LineageView = lazy(() =>
  import("../features/lineage/LineageView").then((module) => ({ default: module.LineageView }))
);
const AssetsView = lazy(() =>
  import("../features/assets/AssetsView").then((module) => ({ default: module.AssetsView }))
);
const SettingsPage = lazy(() =>
  import("../features/settings/SettingsPage").then((module) => ({ default: module.SettingsPage }))
);
const ProjectsPage = lazy(() =>
  import("../features/projects/ProjectsPage").then((module) => ({ default: module.ProjectsPage }))
);
const OverviewPage = lazy(() =>
  import("../features/overview/OverviewPage").then((module) => ({ default: module.OverviewPage }))
);
const ProjectDetailPage = lazy(() =>
  import("../features/projects/ProjectDetailPage").then((module) => ({ default: module.ProjectDetailPage }))
);

interface ModuleViewProps {
  router: StudioRouter;
  workspace: StudioWorkspace;
  settings: StudioSettingsState;
  diagnostics: StudioDiagnosticsState;
  modules: StudioModulesState;
  environmentContext?: EnvironmentContext | null;
}

/** Resolves the active route module to its feature page. */
export function ModuleView(props: ModuleViewProps) {
  return (
    <Suspense fallback={<EmptyState title="Loading…" />}>
      <ResolveModule {...props} />
    </Suspense>
  );
}

function ResolveModule({ router, workspace, settings, diagnostics, modules, environmentContext }: ModuleViewProps) {
  const { route } = router;
  const { publish } = useNotificationCenter();
  useEffect(() => {
    if (!workspace.metadataWarning || !route.environmentId) return;
    publish({ tone: "warning", title: "Source saved; refresh needs attention", location: route.module, message: workspace.metadataWarning, dedupeKey: `environment:${route.environmentId}:metadata-refresh:${workspace.metadataWarning}` });
  }, [publish, route.environmentId, route.module, workspace.metadataWarning]);
  const metadataWarning = route.module === "metadata"
    ? errorListMessage(workspace.metadataEditorDocument?.source.revision?.errors)
    : null;

  useEffect(() => {
    if (route.module !== "metadata" || !route.environmentId || (!workspace.error && !metadataWarning)) return;
    publish({
      tone: "error",
      title: workspace.error ? "Metadata could not load" : "Metadata source read failed",
      location: "Metadata",
      message: workspace.error ?? metadataWarning ?? "Metadata source read failed",
      dedupeKey: `environment:${route.environmentId}:metadata:${workspace.error ?? metadataWarning}`,
    });
  }, [metadataWarning, publish, route.environmentId, route.module, workspace.error]);

  switch (route.module) {
    case "projects":
      if (route.projectId) {
        if (workspace.projectSummariesLoading && !workspace.selectedProjectSummary) {
          return <EmptyState title="Loading project…" />;
        }
        if (workspace.projectSummariesError && !workspace.selectedProjectSummary) {
          return (
            <EmptyState
              title="Unable to load project"
              detail={workspace.projectSummariesError}
              action={<button type="button" onClick={() => void workspace.reloadProjectSummaries()}>Retry</button>}
            />
          );
        }
        return (
          <ProjectDetailPage
            project={workspace.selectedProjectSummary}
            projectId={route.projectId}
            projectName={workspace.selectedProjectSummary?.name ?? null}
            section={route.projectSection ?? "overview"}
            busy={workspace.busy}
            routeSearch={route.search}
            onRenameProject={workspace.renameProject}
            onDeleteProject={workspace.deleteProject}
            onSectionChange={router.navigateProjectSection}
            onOpenEnvironment={router.openProjectEnvironment}
            onConfigureSources={router.openEnvironmentSources}
            onCreateEnvironment={workspace.createEnvironment}
            onRenameEnvironment={workspace.renameEnvironment}
            onDeleteEnvironment={workspace.deleteEnvironment}
            onQuickCreateEnvironment={async (projectId, name) => {
              const envId = await workspace.createEnvironment(name, projectId);
              if (envId) router.openEnvironmentSources(projectId, envId);
            }}
            onCreateMapping={workspace.createProjectReferenceMapping}
            onUpdateMapping={workspace.updateProjectReferenceMapping}
            onDeleteMapping={workspace.deleteProjectReferenceMapping}
          />
        );
      }
      return (
        <ProjectsPage
          projects={workspace.projectSummaries}
          loading={workspace.projectSummariesLoading}
          loaded={workspace.projectSummariesLoaded}
          loadError={workspace.projectSummariesError}
          busy={workspace.busy}
          onRetry={workspace.reloadProjectSummaries}
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
          settingsLoading={settings.loading}
          saving={settings.saving}
          onSaveSettings={settings.saveSettings}
          diagnostics={diagnostics.diagnostics}
          diagnosticsLoading={diagnostics.loading}
          onReloadDiagnostics={diagnostics.reload}
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
  if (environmentContext && route.projectId !== environmentContext.environment.project_id) {
    return (
      <EmptyState
        title="Environment route mismatch"
        detail="This environment does not belong to the project in the current URL."
      />
    );
  }

  switch (route.module) {
    case "overview":
      return <EnvironmentOverviewRoute environmentId={route.environmentId} onNavigate={router.navigate} timezoneName={settings.settings?.timezone ?? null} />;
    case "sources":
      return (
        <SourcesPage
          metadataSources={workspace.metadataSources}
          sqlSources={workspace.sqlSources}
          logBasePaths={workspace.logBasePaths}
          codeArtifacts={workspace.codeArtifacts}
          busy={workspace.busy || workspace.loading}
          selectedEnvironmentId={route.environmentId}
          onImportMetadataSources={workspace.importMetadataSources}
          onAddSqlSource={workspace.addSqlSource}
          onImportDatacoolieProjectSources={workspace.importDatacoolieProjectSources}
          onAddLogBasePath={workspace.addLogBasePath}
          onAddCodeArtifact={workspace.addCodeArtifact}
          onUpdateSource={workspace.updateSource}
          onDeleteSource={workspace.deleteSource}
          onGetDeleteImpact={workspace.getSourceDeleteImpact}
          onValidateSource={workspace.validateSource}
          onSyncSource={workspace.syncSource}
          onRetrySourceObservation={workspace.retrySourceObservation}
          onRunSourceBatch={workspace.runSourceBatch}
          syncStatuses={workspace.sourceSyncStatuses}
          sourceOperations={workspace.sourceOperations}
          timezoneName={settings.settings?.timezone ?? null}
        />
      );
    case "metadata":
      if (workspace.error && !workspace.metadataEditorDocument) {
        return (
          <EmptyState
            title="Unable to load metadata"
            detail={workspace.error}
            action={<button type="button" onClick={() => void workspace.retryMetadataEditor()}>Retry</button>}
          />
        );
      }
      return (
        <MetadataExplorer
          key={route.environmentId}
          editorDocument={workspace.metadataEditorDocument}
          serverDraft={workspace.metadataEditorDraft}
          routeSearch={route.search}
          onRouteSearchChange={(search) => router.navigate("metadata", search)}
          metadataNavigation={router.metadataNavigation}
          onMetadataNavigationConsumed={router.clearMetadataNavigation}
          onFocusInLineage={(target) => router.navigate("lineage", lineageDataflowFocusSearch(target))}
          loading={workspace.loading}
          busy={workspace.busy}
          savingDraft={workspace.metadataEditorSavingDraft}
          onSaveDraft={workspace.saveMetadataEditorDraft}
          onDiscardDraft={workspace.discardMetadataEditorDraft}
          onPrepareSave={workspace.prepareMetadataEditorDocument}
          onApplySave={workspace.applyMetadataEditorWrite}
          onAbandonSave={workspace.abandonMetadataEditorWrite}
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
          key={route.environmentId}
          environmentId={route.environmentId}
          metadataEditorDocument={workspace.metadataEditorDocument}
          metadataEditorDraft={workspace.metadataEditorDraft}
          onEnsureMetadataEditor={workspace.ensureMetadataEditorContext}
          metadataBusy={workspace.busy}
          onSaveMetadataDraft={workspace.saveMetadataEditorDraft}
          onPrepareMetadataWrite={workspace.prepareMetadataEditorDocument}
          onApplyMetadataWrite={workspace.applyMetadataEditorWrite}
          onAbandonMetadataWrite={workspace.abandonMetadataEditorWrite}
          routeSearch={route.search}
          onFocusInLineage={(assetId) => {
            router.navigate("lineage", `focusAsset=${encodeURIComponent(assetId)}`);
          }}
          onFocusDataflowInLineage={(target) => router.navigate("lineage", lineageDataflowFocusSearch(target))}
          onOpenMetadata={(target) => {
            router.navigateMetadata(target);
          }}
          mappingBusy={workspace.busy}
          onCreateReferenceMapping={workspace.createProjectReferenceMapping}
          onUpdateReferenceMapping={workspace.updateProjectReferenceMapping}
          onDeleteReferenceMapping={workspace.deleteProjectReferenceMapping}
        />
      );
    case "lineage":
      return (
        <EnvironmentLineageRoute
          environmentId={route.environmentId}
          workspace={workspace}
          routeSearch={route.search}
          timezoneName={settings.settings?.timezone ?? "UTC"}
          onOpenMetadata={router.navigateMetadata}
        />
      );
    case "master-data":
      return <MasterDataPage />;
    case "monitoring":
      return (
        <MonitoringRoute
          environmentId={route.environmentId}
          timezoneName={settings.settings?.timezone ?? null}
          timezoneRevision={settings.settings?.updated_at ?? null}
          identityRevision={environmentContext
            ? JSON.stringify([
              environmentContext.versions.metadata_catalog,
              environmentContext.versions.operations,
            ])
            : null}
          timezoneLoading={settings.loading}
          timezoneError={settings.error}
          activePage={route.monitoringPage ?? monitoringDefaultPage}
          onPageChange={router.navigateMonitoringPage}
          routeSearch={route.search}
          onSearchChange={router.replaceSearch}
          onOpenSources={() => router.navigate("sources")}
        />
      );
    default:
      return <EmptyState title="Unknown module" />;
  }
}

function EnvironmentLineageRoute({ environmentId, workspace, routeSearch, timezoneName, onOpenMetadata }: {
  environmentId: number;
  workspace: StudioWorkspace;
  routeSearch?: string;
  timezoneName: string | null;
  onOpenMetadata: StudioRouter["navigateMetadata"];
}) {
  const draftPreview = workspace.metadataEditorDraft !== null;
  const graph = useLineageGraph(environmentId, draftPreview);
  if (graph.isError && !graph.data) {
    return <EmptyState title="Unable to load lineage" detail={graph.error instanceof Error ? graph.error.message : "Lineage request failed"} action={<button type="button" onClick={() => void graph.refetch()}>Retry</button>} />;
  }
  if (!graph.data) return <EmptyState title="Loading lineage…" />;
  return (
    <LineageView
      key={environmentId}
      environmentId={environmentId}
      lineage={graph.data}
      draftPreview={draftPreview}
      onRefreshLineage={graph.refetch}
      metadataEditorDocument={workspace.metadataEditorDocument}
      metadataEditorDraft={workspace.metadataEditorDraft}
      onEnsureMetadataEditor={workspace.ensureMetadataEditorContext}
      busy={workspace.busy}
      onSaveMetadataDraft={workspace.saveMetadataEditorDraft}
      onPrepareMetadataWrite={workspace.prepareMetadataEditorDocument}
      onApplyMetadataWrite={workspace.applyMetadataEditorWrite}
      onAbandonMetadataWrite={workspace.abandonMetadataEditorWrite}
      timezoneName={timezoneName}
      routeSearch={routeSearch}
      onOpenMetadata={onOpenMetadata}
      onCreateReferenceMapping={workspace.createProjectReferenceMapping}
      onUpdateReferenceMapping={workspace.updateProjectReferenceMapping}
      onDeleteReferenceMapping={workspace.deleteProjectReferenceMapping}
    />
  );
}

function EnvironmentOverviewRoute({
  environmentId,
  onNavigate,
  timezoneName,
}: {
  environmentId: number;
  onNavigate: StudioRouter["navigate"];
  timezoneName: string | null;
}) {
  const overview = useEnvironmentOverviewQuery(environmentId);
  const { publish } = useNotificationCenter();
  const errorMessage = overview.error ? toErrorMessage(overview.error) : null;
  const sourceWarningMessage = errorListMessage(overview.data?.metadata.errors);

  useEffect(() => {
    if (!overview.isError || overview.data || !errorMessage) return;
    publish({
      tone: "error",
      title: "Overview could not load",
      location: "Overview",
      message: errorMessage,
      dedupeKey: `environment:${environmentId}:overview:${errorMessage}`,
    });
  }, [environmentId, errorMessage, overview.data, overview.isError, publish]);

  useEffect(() => {
    if (!overview.data || !sourceWarningMessage) return;
    publish({
      tone: "error",
      title: "Overview loaded with source errors",
      location: "Overview",
      message: sourceWarningMessage,
      dedupeKey: `environment:${environmentId}:overview:sources:${sourceWarningMessage}`,
    });
  }, [environmentId, overview.data, publish, sourceWarningMessage]);

  if (overview.isError && !overview.data) {
    return (
      <EmptyState
        title="Unable to load overview"
        detail={errorMessage ?? "Overview request failed"}
        action={<button type="button" onClick={() => void overview.refetch()}>Retry</button>}
      />
    );
  }
  if (!overview.data) return <EmptyState title="Loading overview…" />;
  return <OverviewPage overview={overview.data} onNavigate={onNavigate} timezoneName={timezoneName} />;
}

function errorListMessage(value: unknown): string | null {
  if (!Array.isArray(value)) return null;
  const messages = value
    .map((item) => {
      if (item && typeof item === "object" && "message" in item) {
        const message = (item as { message?: unknown }).message;
        return typeof message === "string" ? message : null;
      }
      return typeof item === "string" ? item : null;
    })
    .filter((message): message is string => Boolean(message));
  return messages.length ? messages.join("\n") : null;
}
