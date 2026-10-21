import { useEffect, useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { type ModuleKey } from "./moduleRegistry";
import type { StudioRouter } from "./useStudioRouter";
import type {
  DatacoolieProjectSourceImportInput,
  LogSyncRequest,
  SourceDeleteImpact,
  SourceImportResponse,
  SourceReadCheckResult,
  SourceSyncStatus,
} from "../shared/api/contractTypes";
import type {
  MetadataBackup,
  MetadataEditorDocument,
  MetadataWriteOperation,
  ProjectReferenceMapping,
  ProjectSummary,
  SourcePath,
  StorageBinding,
  ReferenceType,
  TargetIdentifierKind
} from "../shared/api/domainTypes";
import { toErrorMessage } from "../shared/lib/errors";
import type { SourceKind } from "../shared/lib/sources";
import { environmentQueryKeys } from "../features/environments/environmentQueries";
import { createEnvironmentMutations } from "../features/environments/environmentMutations";
import { useEnvironmentMetadataEditor } from "../features/metadata-explorer/metadataEditorQueries";
import { createProjectMutations } from "../features/projects/projectMutations";
import { projectQueryKeys, useProjectSummariesQuery } from "../features/projects/projectQueries";
import type {
  SourceBatchAction,
  SourceBatchEntry,
  SourceBatchResult,
  SourceOperations,
} from "../features/sources/sourceWorkspaceModel";
import { createEnvironmentSourceMutations } from "../features/sources/sourceMutations";
import { useEnvironmentSourcesQuery } from "../features/sources/sourceQueries";
import { useLocalSourceObservation } from "../features/sources/useLocalSourceObservation";

export interface StudioWorkspace {
  projectSummaries: ProjectSummary[];
  projectSummariesLoading: boolean;
  projectSummariesLoaded: boolean;
  projectSummariesError: string | null;
  selectedProjectSummary: ProjectSummary | null;
  metadataSources: SourcePath[];
  sqlSources: SourcePath[];
  logBasePaths: SourcePath[];
  codeArtifacts: SourcePath[];
  sourceSyncStatuses: Record<string, SourceSyncStatus>;
  sourceOperations: SourceOperations;
  metadataEditorDocument: MetadataEditorDocument | null;
  metadataEditorDraft: MetadataEditorDocument | null;
  metadataEditorSavingDraft: boolean;
  loading: boolean;
  busy: boolean;
  error: string | null;
  retryMetadataEditor: () => Promise<unknown>;
  refreshCurrentEnvironment: () => Promise<void>;
  reloadProjectSummaries: () => Promise<void>;
  createProject: (name: string) => Promise<void>;
  renameProject: (projectId: number, name: string) => Promise<void>;
  deleteProject: (projectId: number) => Promise<void>;
  createProjectReferenceMapping: (payload: {
    reference_type: ReferenceType;
    reference_value: string;
    target_identifier_kind: TargetIdentifierKind;
    target_value: string;
    target_display_value?: string | null;
    note?: string | null;
  }) => Promise<ProjectReferenceMapping>;
  updateProjectReferenceMapping: (mappingId: number, payload: {
    reference_type?: ReferenceType;
    reference_value?: string | null;
    target_identifier_kind?: TargetIdentifierKind;
    target_value?: string | null;
    target_display_value?: string | null;
    note?: string | null;
  }) => Promise<ProjectReferenceMapping>;
  deleteProjectReferenceMapping: (mappingId: number) => Promise<void>;
  createEnvironment: (name: string, projectIdOverride?: number) => Promise<number>;
  renameEnvironment: (environmentId: number, name: string) => Promise<void>;
  deleteEnvironment: (environmentId: number) => Promise<void>;
  addMetadataSource: (uri: string, label?: string) => Promise<void>;
  importMetadataSources: (uri: string, label?: string, storage?: StorageBinding) => Promise<SourceImportResponse | null>;
  addSqlSource: (uri: string, label?: string, storage?: StorageBinding) => Promise<void>;
  importDatacoolieProjectSources: (payload: DatacoolieProjectSourceImportInput) => Promise<SourceImportResponse | null>;
  addLogBasePath: (uri: string, label?: string, sourceConfig?: Record<string, unknown>, storage?: StorageBinding) => Promise<void>;
  addCodeArtifact: (uri: string, label?: string, sourceConfig?: Record<string, unknown>, storage?: StorageBinding) => Promise<void>;
  updateSource: (
    kind: SourceKind,
    id: number,
    payload: {
      uri?: string;
      label?: string | null;
      enabled?: boolean;
      source_config?: Record<string, unknown>;
      storage?: StorageBinding;
      sync_schedule_enabled?: boolean;
      sync_interval_minutes?: number | null;
    }
  ) => Promise<void>;
  deleteSource: (kind: SourceKind, id: number) => Promise<void>;
  getSourceDeleteImpact: (kind: SourceKind, id: number) => Promise<SourceDeleteImpact>;
  validateSource: (kind: SourceKind, id: number) => Promise<SourceReadCheckResult>;
  syncSource: (kind: SourceKind, id: number, logSyncRequest?: LogSyncRequest) => Promise<SourceSyncStatus>;
  retrySourceObservation: (kind: SourceKind, id: number) => Promise<SourceSyncStatus>;
  runSourceBatch: (action: SourceBatchAction, entries: SourceBatchEntry[], logSyncRequest?: LogSyncRequest) => Promise<SourceBatchResult>;
  ensureMetadataEditorContext: () => Promise<void>;
  metadataWarning: string | null;
  saveMetadataEditorDraft: (document: MetadataEditorDocument) => Promise<MetadataEditorDocument>;
  discardMetadataEditorDraft: () => Promise<void>;
  prepareMetadataEditorDocument: (document: MetadataEditorDocument) => Promise<MetadataWriteOperation>;
  applyMetadataEditorWrite: (operation: MetadataWriteOperation) => Promise<MetadataEditorDocument>;
  abandonMetadataEditorWrite: (operation: MetadataWriteOperation) => Promise<MetadataWriteOperation>;
  listMetadataBackups: () => Promise<MetadataBackup[]>;
  previewMetadataBackup: (backupId: number) => Promise<MetadataEditorDocument>;
  restoreMetadataBackup: (backup: MetadataBackup, document: MetadataEditorDocument) => Promise<MetadataEditorDocument>;
  deleteMetadataBackup: (backupId: number) => Promise<void>;
  clearMetadataBackups: () => Promise<void>;
}

/**
 * Owns workspace mutations plus Environment-source,
 * and Monitoring operation state. Feature server reads are query-owned. Route-driven loading
 * is coordinated through the supplied {@link StudioRouter}. Presentation lives
 * entirely in feature components; this hook is the container/data layer.
 */
export function useStudioWorkspace(
  router: StudioRouter,
  options?: {
    onEnvironmentChanged?: (environmentId: number) => Promise<void> | void;
  }
): StudioWorkspace {
  const { route, setStudioRoute } = router;
  const queryClient = useQueryClient();
  const [sourceOperations, setSourceOperations] = useState<SourceOperations>({});
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const activeEnvironmentIdRef = useRef<number | null>(route.environmentId);
  const activeProjectIdRef = useRef<number | null>(route.projectId);
  const environmentModuleLoadGeneration = useRef(0);

  activeEnvironmentIdRef.current = route.environmentId;
  activeProjectIdRef.current = route.projectId;
  useEffect(() => { setBusy(false); setError(null); }, [route.projectId]);

  const projectSummariesQuery = useProjectSummariesQuery(route.module === "projects");
  const projectSummaries = projectSummariesQuery.data ?? [];
  const selectedProjectSummary = useMemo(
    () => projectSummaries.find((project) => project.id === route.projectId) ?? null,
    [projectSummaries, route.projectId]
  );

  function invalidateProjectSummaries() {
    void queryClient.invalidateQueries({ queryKey: projectQueryKeys.summaries() });
  }

  const sourcesQuery = useEnvironmentSourcesQuery({
    environmentId: route.environmentId,
    enabled: route.module === "sources",
  });
  useLocalSourceObservation({
    environmentId: route.environmentId,
    activationKey: route.module,
    isActive: (environmentId) => activeEnvironmentIdRef.current === environmentId,
    onEnvironmentChanged: options?.onEnvironmentChanged,
    onError: setError,
  });
  const metadataEditor = useEnvironmentMetadataEditor(route.environmentId, {
    enabled: route.module === "metadata",
    onCatalogChanged: async (mayChangeSources, environmentId) => {
      if (mayChangeSources) invalidateProjectSummaries();
      await refreshEnvironmentHeader(environmentId);
    },
  });

  function clearEnvironmentSources() {
    if (route.environmentId !== null) {
      queryClient.removeQueries({
        queryKey: environmentQueryKeys.sources(route.environmentId),
      });
    }
  }

  function isCurrentEnvironmentLoad(environmentId: number, generation: number) {
    return activeEnvironmentIdRef.current === environmentId
      && environmentModuleLoadGeneration.current === generation;
  }

  async function loadEnvironmentModule(
    environmentId: number,
    module: ModuleKey,
    force = false
  ) {
    // Source mutations force a module refresh; refetch the Sources list so the UI
    // reflects the change without a full page reload.
    if (module === "sources" && force) {
      await queryClient.invalidateQueries({ queryKey: environmentQueryKeys.sources(environmentId) });
    }
  }

  async function refreshEnvironment(
    environmentId = route.environmentId,
    module: ModuleKey = route.module,
    refreshOptions?: { forceHeader?: boolean; forceModule?: boolean }
  ) {
    if (!environmentId) return;
    const generation = ++environmentModuleLoadGeneration.current;
    setLoading(true);
    setError(null);
    try {
      await Promise.all([
        loadEnvironmentModule(environmentId, module, Boolean(refreshOptions?.forceModule)),
        refreshOptions?.forceHeader ? options?.onEnvironmentChanged?.(environmentId) : undefined,
      ]);
    } catch (err) {
      if (isCurrentEnvironmentLoad(environmentId, generation)) setError(toErrorMessage(err));
    } finally {
      if (isCurrentEnvironmentLoad(environmentId, generation)) setLoading(false);
    }
  }

  async function refreshEnvironmentHeader(environmentId = route.environmentId) {
    if (!environmentId) return;
    await options?.onEnvironmentChanged?.(environmentId);
  }

  const {
    createProject,
    renameProject,
    deleteProject,
    createProjectReferenceMapping,
    updateProjectReferenceMapping,
    deleteProjectReferenceMapping,
  } = createProjectMutations({
    projectId: route.projectId,
    environmentId: route.environmentId,
    queryClient,
    setStudioRoute,
    setBusy,
    setError,
    clearEnvironmentSources,
    onEnvironmentChanged: options?.onEnvironmentChanged,
    isActiveProject: projectId => activeProjectIdRef.current === projectId,
  });

  const { createEnvironment, renameEnvironment, deleteEnvironment } = createEnvironmentMutations({
    projectId: route.projectId,
    queryClient,
    setBusy,
    setError,
  });

  const {
    addMetadataSource,
    importMetadataSources,
    addSqlSource,
    importDatacoolieProjectSources,
    addLogBasePath,
    addCodeArtifact,
    updateSource,
    deleteSource,
    validateSource,
    syncSource,
    retrySourceObservation,
    runSourceBatch,
    getSourceDeleteImpact,
  } = createEnvironmentSourceMutations({
    environmentId: route.environmentId,
    module: route.module,
    activeEnvironmentIdRef,
    queryClient,
    setBusy,
    setError,
    setSourceOperations,
    invalidateProjectSummaries,
    refreshEnvironment,
  });

  // Load environment-scoped data when the environment or module changes. Source
  // server state is owned by the Sources query and is never copied into this
  // container's local state.
  useEffect(() => {
    if (!route.environmentId) return;
    void refreshEnvironment(route.environmentId, route.module);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [route.environmentId, route.module]);

  const activeQueryError = [sourcesQuery]
    .find((query) => query.error)?.error;

  return {
    projectSummaries,
    projectSummariesLoading: projectSummariesQuery.isPending,
    projectSummariesLoaded: projectSummariesQuery.data !== undefined,
    projectSummariesError: projectSummariesQuery.error
      ? toErrorMessage(projectSummariesQuery.error)
      : null,
    selectedProjectSummary,
    metadataSources: sourcesQuery.data?.metadataSources ?? [],
    sqlSources: sourcesQuery.data?.sqlSources ?? [],
    logBasePaths: sourcesQuery.data?.logBasePaths ?? [],
    codeArtifacts: sourcesQuery.data?.codeArtifacts ?? [],
    sourceSyncStatuses: sourcesQuery.data?.statuses ?? {},
    sourceOperations,
    metadataEditorDocument: metadataEditor.workspace?.document ?? null,
    metadataEditorDraft: metadataEditor.workspace?.draft ?? null,
    metadataEditorSavingDraft: metadataEditor.savingDraft,
    loading: loading || (route.module === "sources" && sourcesQuery.isPending) || metadataEditor.loading,
    busy: busy || metadataEditor.busy,
    error: error
      ?? (activeQueryError ? toErrorMessage(activeQueryError) : null)
      ?? (metadataEditor.error ? toErrorMessage(metadataEditor.error) : null),
    retryMetadataEditor: async () => { await metadataEditor.retryWorkspace(); },
    refreshCurrentEnvironment: () => refreshEnvironment(route.environmentId, route.module, { forceHeader: true, forceModule: true }),
    reloadProjectSummaries: async () => { await projectSummariesQuery.refetch(); },
    createProject,
    renameProject,
    deleteProject,
    createProjectReferenceMapping,
    updateProjectReferenceMapping,
    deleteProjectReferenceMapping,
    createEnvironment,
    renameEnvironment,
    deleteEnvironment,
    addMetadataSource,
    importMetadataSources,
    addSqlSource,
    importDatacoolieProjectSources,
    addLogBasePath,
    addCodeArtifact,
    updateSource,
    deleteSource,
    getSourceDeleteImpact,
    validateSource,
    syncSource,
    retrySourceObservation,
    runSourceBatch,
    ensureMetadataEditorContext: async () => { await metadataEditor.ensureContext(); },
    metadataWarning: metadataEditor.warning,
    saveMetadataEditorDraft: metadataEditor.saveDraft,
    discardMetadataEditorDraft: metadataEditor.discardDraft,
    prepareMetadataEditorDocument: metadataEditor.prepareDocumentWrite,
    applyMetadataEditorWrite: metadataEditor.applyPreparedDocumentWrite,
    abandonMetadataEditorWrite: metadataEditor.abandonDocumentWrite,
    listMetadataBackups: metadataEditor.listBackups,
    previewMetadataBackup: metadataEditor.previewBackup,
    restoreMetadataBackup: metadataEditor.restoreBackup,
    deleteMetadataBackup: metadataEditor.deleteBackup,
    clearMetadataBackups: metadataEditor.clearBackups
  };
}
