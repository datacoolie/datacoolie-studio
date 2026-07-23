import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  environmentDefaultModule,
  moduleByKey,
  monitoringDefaultPage,
  projectDefaultSection,
  type ModuleKey
} from "./moduleRegistry";
import type { StudioRouter } from "./useStudioRouter";
import { api } from "../shared/api/client";
import type {
  Environment,
  MetadataBackup,
  MetadataEditorDocument,
  ProjectReferenceMapping,
  Project,
  ProjectSummary,
  SourceImportResponse,
  LogSyncRequest,
  SourceDeleteImpact,
  SourcePath,
  SourceReadCheckResult,
  SourceSyncStatus,
  ReferenceType,
  TargetIdentifierKind
} from "../shared/api/domainTypes";
import { toErrorMessage } from "../shared/lib/errors";
import { sourceKey, type SourceKind } from "../shared/lib/sources";
import { projectRouteResources } from "./projectRouteResources";
import { ResourceCache } from "../shared/data/resourceCache";
import { fetchEnvironmentSources } from "./environmentSourcesResource";
import { environmentQueryKeys } from "../features/environments/environmentQueries";
import { createEnvironmentMutations } from "../features/environments/environmentMutations";
import { useEnvironmentMetadataEditor } from "../features/metadata-explorer/metadataEditorQueries";
import { createProjectMutations } from "../features/projects/projectMutations";
import type {
  SourceBatchAction,
  SourceBatchEntry,
  SourceBatchResult,
} from "../features/sources/sourceWorkspaceModel";
import { createEnvironmentSourceMutations } from "../features/sources/sourceMutations";

export interface StudioWorkspace {
  projects: Project[];
  projectSummaries: ProjectSummary[];
  projectSummariesLoading: boolean;
  projectSummariesLoaded: boolean;
  projectSummariesError: string | null;
  environments: Environment[];
  selectedProject: Project | null;
  selectedProjectSummary: ProjectSummary | null;
  metadataSources: SourcePath[];
  logPaths: SourcePath[];
  codeArtifacts: SourcePath[];
  sourceSyncStatuses: Record<string, SourceSyncStatus>;
  metadataEditorDocument: MetadataEditorDocument | null;
  metadataEditorDraft: MetadataEditorDocument | null;
  loading: boolean;
  busy: boolean;
  error: string | null;
  refreshCurrentEnvironment: () => Promise<void>;
  reloadProjectSummaries: () => Promise<void>;
  createProject: (name: string) => Promise<void>;
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
  deleteEnvironment: (environmentId: number) => Promise<void>;
  addMetadataSource: (uri: string, label?: string) => Promise<void>;
  importMetadataSources: (uri: string, label?: string) => Promise<SourceImportResponse | null>;
  importDatacoolieProjectSources: (payload: {
    project_uri: string;
    metadata_subpath?: string;
    code_subpath?: string;
    metadata_uri?: string | null;
    code_uri?: string | null;
    include_metadata?: boolean;
    include_code?: boolean;
  }) => Promise<SourceImportResponse | null>;
  addLogPath: (uri: string, label?: string, sourceConfig?: Record<string, unknown>) => Promise<void>;
  addCodeArtifact: (uri: string, label?: string, sourceConfig?: Record<string, unknown>) => Promise<void>;
  updateSource: (
    kind: SourceKind,
    id: number,
    payload: {
      uri?: string;
      label?: string | null;
      enabled?: boolean;
      source_config?: Record<string, unknown>;
      sync_schedule_enabled?: boolean;
      sync_interval_minutes?: number | null;
    }
  ) => Promise<void>;
  deleteSource: (kind: SourceKind, id: number) => Promise<void>;
  getSourceDeleteImpact: (kind: SourceKind, id: number) => Promise<SourceDeleteImpact>;
  validateSource: (kind: SourceKind, id: number) => Promise<SourceReadCheckResult>;
  syncSource: (kind: SourceKind, id: number, logSyncRequest?: LogSyncRequest) => Promise<SourceSyncStatus>;
  runSourceBatch: (action: SourceBatchAction, entries: SourceBatchEntry[], logSyncRequest?: LogSyncRequest) => Promise<SourceBatchResult>;
  ensureMetadataEditorContext: () => Promise<void>;
  validateMetadataEditorDocument: (document: MetadataEditorDocument) => Promise<MetadataEditorDocument>;
  saveMetadataEditorDraft: (document: MetadataEditorDocument) => Promise<MetadataEditorDocument>;
  discardMetadataEditorDraft: () => Promise<void>;
  saveMetadataEditorDocument: (document: MetadataEditorDocument) => Promise<MetadataEditorDocument>;
  listMetadataBackups: () => Promise<MetadataBackup[]>;
  previewMetadataBackup: (backupId: number) => Promise<MetadataEditorDocument>;
  restoreMetadataBackup: (backup: MetadataBackup, document: MetadataEditorDocument) => Promise<MetadataEditorDocument>;
  deleteMetadataBackup: (backupId: number) => Promise<void>;
  clearMetadataBackups: () => Promise<void>;
}

/**
 * Owns workspace mutations plus the remaining Project, Environment-source,
 * and Monitoring compatibility state. Feature server reads are query-owned. Route-driven loading
 * is coordinated through the supplied {@link StudioRouter}. Presentation lives
 * entirely in feature components; this hook is the container/data layer.
 */
export function useStudioWorkspace(
  router: StudioRouter,
  options?: {
    onEnvironmentChanged?: (environmentId: number) => Promise<void> | void;
  }
): StudioWorkspace {
  const { route, activeScope, setStudioRoute } = router;
  const queryClient = useQueryClient();
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectSummaries, setProjectSummaries] = useState<ProjectSummary[]>([]);
  const [projectSummariesLoading, setProjectSummariesLoading] = useState(false);
  const [projectSummariesLoaded, setProjectSummariesLoaded] = useState(false);
  const [projectSummariesError, setProjectSummariesError] = useState<string | null>(null);
  const [environments, setEnvironments] = useState<Environment[]>([]);
  const [metadataSources, setMetadataSources] = useState<SourcePath[]>([]);
  const [logPaths, setLogPaths] = useState<SourcePath[]>([]);
  const [codeArtifacts, setCodeArtifacts] = useState<SourcePath[]>([]);
  const [sourceSyncStatuses, setSourceSyncStatuses] = useState<Record<string, SourceSyncStatus>>({});
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const activeEnvironmentIdRef = useRef<number | null>(route.environmentId);
  const environmentModuleLoadGeneration = useRef(0);
  const projectSummariesCache = useRef(new ResourceCache<"directory", ProjectSummary[]>());
  const projectSummariesGeneration = useRef(0);

  activeEnvironmentIdRef.current = route.environmentId;

  const selectedProject = useMemo(
    () => projects.find((project) => project.id === route.projectId) ?? null,
    [projects, route.projectId]
  );
  const selectedProjectSummary = useMemo(
    () => projectSummaries.find((project) => project.id === route.projectId) ?? null,
    [projectSummaries, route.projectId]
  );

  const sourcesQuery = useQuery({
    queryKey: route.environmentId ? environmentQueryKeys.sources(route.environmentId) : ["environments", "no-sources"],
    queryFn: async () => {
      const environmentId = route.environmentId!;
      const sources = await fetchEnvironmentSources(environmentId);
      const statuses = await fetchSourceSyncStatuses(
        environmentId,
        sources.metadataSources,
        sources.logPaths,
        sources.codeArtifacts,
      );
      return { ...sources, statuses };
    },
    enabled: route.environmentId !== null && route.module === "sources",
    staleTime: Number.POSITIVE_INFINITY,
  });
  const metadataEditor = useEnvironmentMetadataEditor(route.environmentId, {
    enabled: route.module === "metadata",
    onCatalogChanged: async (mayChangeSources) => {
      if (mayChangeSources) invalidateProjectSummaries();
      await refreshEnvironmentHeader(route.environmentId);
    },
  });
  // The source lists are derived solely from the query so the displayed data can
  // never desync from the cache. Clearing here (instead of in a separate
  // environment-change effect) avoids a race that could wipe freshly loaded data.
  useEffect(() => {
    const data = sourcesQuery.data;
    if (data) {
      setMetadataSources(data.metadataSources);
      setLogPaths(data.logPaths);
      setCodeArtifacts(data.codeArtifacts);
      setSourceSyncStatuses(data.statuses);
    } else {
      setMetadataSources([]);
      setLogPaths([]);
      setCodeArtifacts([]);
      setSourceSyncStatuses({});
    }
  }, [sourcesQuery.data]);

  function clearEnvironmentSources() {
    setMetadataSources([]);
    setLogPaths([]);
    setCodeArtifacts([]);
    setSourceSyncStatuses({});
  }

  async function loadProjects(preferredId?: number) {
    setBusy(true);
    setError(null);
    try {
      const items = await api.listProjects();
      setProjects(items);
      const projectId = preferredId ?? route.projectId;
      if (projectId && items.some((project) => project.id === projectId)) return;
      if (activeScope === "global") return;
      if (items[0]) {
        setStudioRoute({ projectId: items[0].id, environmentId: null, module: "projects", projectSection: projectDefaultSection }, true);
      }
    } catch (err) {
      setError(toErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  function applyProjectSummaries(items: ProjectSummary[]) {
    setProjectSummaries(items);
    setProjects(items.map(({ id, name, description, created_at, updated_at }) => ({
      id,
      name,
      description,
      created_at,
      updated_at,
    })));
    setProjectSummariesLoaded(true);
    setProjectSummariesError(null);
  }

  function invalidateProjectSummaries() {
    projectSummariesGeneration.current += 1;
    projectSummariesCache.current.invalidate("directory");
  }

  function updateProjectSummaries(updater: (items: ProjectSummary[]) => ProjectSummary[]) {
    invalidateProjectSummaries();
    setProjectSummaries(updater);
    setProjectSummariesLoaded(true);
    setProjectSummariesLoading(false);
    setProjectSummariesError(null);
  }

  async function loadProjectSummaries(force = false) {
    const generation = projectSummariesGeneration.current;
    setProjectSummariesLoading(true);
    setProjectSummariesError(null);
    setError(null);
    try {
      const result = await projectSummariesCache.current.load(
        "directory",
        api.listProjectSummaries,
        { ttlMs: Number.MAX_SAFE_INTEGER, force }
      );
      if (result.current && generation === projectSummariesGeneration.current) {
        applyProjectSummaries(result.data);
      }
    } catch (err) {
      if (generation !== projectSummariesGeneration.current) return;
      const message = toErrorMessage(err);
      setProjectSummariesError(message);
    } finally {
      if (generation === projectSummariesGeneration.current) setProjectSummariesLoading(false);
    }
  }

  async function loadEnvironments(projectId: number, preferredId?: number, replace = false) {
    setBusy(true);
    setError(null);
    try {
      const items = await api.listEnvironments(projectId);
      setEnvironments(items);
      const environmentId = preferredId ?? (route.projectId === projectId ? route.environmentId : null);
      const nextEnvironmentId = environmentId && items.some((env) => env.id === environmentId) ? environmentId : (items[0]?.id ?? null);
      const currentScope = moduleByKey(route.module)?.scope ?? "global";
      if (currentScope !== "environment") {
        return;
      }
      if (nextEnvironmentId) {
        setStudioRoute(
          {
            projectId,
            environmentId: nextEnvironmentId,
            module: route.module || environmentDefaultModule,
            monitoringPage: route.module === "monitoring" ? (route.monitoringPage ?? monitoringDefaultPage) : monitoringDefaultPage
          },
          replace
        );
      } else {
        setStudioRoute({ projectId, environmentId: null, module: "projects", projectSection: "environments" }, replace);
      }
    } catch (err) {
      setError(toErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  function isCurrentEnvironmentLoad(environmentId: number, generation: number) {
    return activeEnvironmentIdRef.current === environmentId
      && environmentModuleLoadGeneration.current === generation;
  }

  async function loadEnvironmentModule(
    environmentId: number,
    module: ModuleKey,
    generation: number,
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
        loadEnvironmentModule(environmentId, module, generation, Boolean(refreshOptions?.forceModule)),
        refreshOptions?.forceHeader ? options?.onEnvironmentChanged?.(environmentId) : undefined,
      ]);
    } catch (err) {
      if (isCurrentEnvironmentLoad(environmentId, generation)) setError(toErrorMessage(err));
    } finally {
      if (isCurrentEnvironmentLoad(environmentId, generation)) setLoading(false);
    }
  }

  async function fetchSourceSyncStatuses(environmentId: number, sources: SourcePath[], paths: SourcePath[], artifacts: SourcePath[]) {
    const entries = await Promise.all([
      ...sources.map(async (source): Promise<[string, SourceSyncStatus]> => [
        sourceKey("metadata", source.id),
        await api.getMetadataSourceSyncStatus(environmentId, source.id)
      ]),
      ...paths.map(async (path): Promise<[string, SourceSyncStatus]> => [
        sourceKey("logs", path.id),
        await api.getLogSourceSyncStatus(environmentId, path.id)
      ]),
      ...artifacts.map(async (artifact): Promise<[string, SourceSyncStatus]> => [
        sourceKey("code", artifact.id),
        await api.getCodeArtifactSyncStatus(environmentId, artifact.id)
      ])
    ]);
    return Object.fromEntries(entries);
  }

  async function refreshEnvironmentHeader(environmentId = route.environmentId) {
    if (!environmentId) return;
    await options?.onEnvironmentChanged?.(environmentId);
  }

  async function refreshEnvironmentAfterHeaderMutation(
    environmentId = route.environmentId,
    module: ModuleKey = route.module
  ) {
    if (!environmentId) return;
    await refreshEnvironment(environmentId, module, { forceHeader: true, forceModule: true });
  }

  const {
    createProject,
    deleteProject,
    createProjectReferenceMapping,
    updateProjectReferenceMapping,
    deleteProjectReferenceMapping,
  } = createProjectMutations({
    projectId: route.projectId,
    environmentId: route.environmentId,
    setStudioRoute,
    setProjects,
    setEnvironments,
    setBusy,
    setError,
    updateProjectSummaries,
    clearEnvironmentSources,
    onEnvironmentChanged: options?.onEnvironmentChanged,
  });

  const { createEnvironment, deleteEnvironment } = createEnvironmentMutations({
    projectId: route.projectId,
    queryClient,
    setEnvironments,
    setBusy,
    setError,
    updateProjectSummaries,
  });

  const {
    addMetadataSource,
    importMetadataSources,
    importDatacoolieProjectSources,
    addLogPath,
    addCodeArtifact,
    updateSource,
    deleteSource,
    validateSource,
    syncSource,
    runSourceBatch,
    getSourceDeleteImpact,
  } = createEnvironmentSourceMutations({
    environmentId: route.environmentId,
    module: route.module,
    activeEnvironmentIdRef,
    setBusy,
    setError,
    setSourceSyncStatuses,
    invalidateProjectSummaries,
    refreshEnvironment,
  });

  const routeResources = projectRouteResources(route);

  // Load project identity only for routes that consume project context.
  useEffect(() => {
    if (!routeResources.projects) return;
    void loadProjects();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [routeResources.projects]);

  // Project summaries are specific to the project hub/detail experience.
  useEffect(() => {
    if (!routeResources.projectSummaries) return;
    void loadProjectSummaries();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [routeResources.projectSummaries]);

  // Load environments when the selected project changes.
  useEffect(() => {
    if (routeResources.environments && route.projectId) {
      void loadEnvironments(route.projectId);
    } else {
      setEnvironments([]);
    }
    if (!route.projectId) {
      clearEnvironmentSources();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [route.projectId, routeResources.environments]);

  // Load environment-scoped data when the environment or module changes. Source
  // list state is cleared/repopulated by the sources query effect above, keyed on
  // the active environment, so it must not be cleared here (that race left the
  // Sources tab blank until a full reload).
  useEffect(() => {
    if (!route.environmentId) return;
    void refreshEnvironment(route.environmentId, route.module);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [route.environmentId, route.module]);

  const activeQueryError = [sourcesQuery]
    .find((query) => query.error)?.error;

  return {
    projects,
    projectSummaries,
    projectSummariesLoading,
    projectSummariesLoaded,
    projectSummariesError,
    environments,
    selectedProject,
    selectedProjectSummary,
    metadataSources,
    logPaths,
    codeArtifacts,
    sourceSyncStatuses,
    metadataEditorDocument: metadataEditor.workspace?.document ?? null,
    metadataEditorDraft: metadataEditor.workspace?.draft ?? null,
    loading: loading || sourcesQuery.isFetching || metadataEditor.loading,
    busy: busy || metadataEditor.busy,
    error: error
      ?? (activeQueryError ? toErrorMessage(activeQueryError) : null)
      ?? (metadataEditor.error ? toErrorMessage(metadataEditor.error) : null),
    refreshCurrentEnvironment: () => refreshEnvironment(route.environmentId, route.module, { forceHeader: true, forceModule: true }),
    reloadProjectSummaries: () => loadProjectSummaries(true),
    createProject,
    deleteProject,
    createProjectReferenceMapping,
    updateProjectReferenceMapping,
    deleteProjectReferenceMapping,
    createEnvironment,
    deleteEnvironment,
    addMetadataSource,
    importMetadataSources,
    importDatacoolieProjectSources,
    addLogPath,
    addCodeArtifact,
    updateSource,
    deleteSource,
    getSourceDeleteImpact,
    validateSource,
    syncSource,
    runSourceBatch,
    ensureMetadataEditorContext: async () => { await metadataEditor.ensureContext(); },
    validateMetadataEditorDocument: metadataEditor.validateDocument,
    saveMetadataEditorDraft: metadataEditor.saveDraft,
    discardMetadataEditorDraft: metadataEditor.discardDraft,
    saveMetadataEditorDocument: metadataEditor.saveDocument,
    listMetadataBackups: metadataEditor.listBackups,
    previewMetadataBackup: metadataEditor.previewBackup,
    restoreMetadataBackup: metadataEditor.restoreBackup,
    deleteMetadataBackup: metadataEditor.deleteBackup,
    clearMetadataBackups: metadataEditor.clearBackups
  };
}
