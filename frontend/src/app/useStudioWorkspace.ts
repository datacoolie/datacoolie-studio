import { useEffect, useMemo, useRef, useState } from "react";
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
  AssetInventoryResponse,
  EnvironmentOverview,
  Environment,
  EnvironmentFreshness,
  LatestStatusResponse,
  LineageResponse,
  MetadataBackup,
  MetadataEditorDocument,
  MonitoringReport,
  ProjectReferenceMapping,
  Project,
  ProjectSummary,
  SourceImportResponse,
  SourceDeleteImpact,
  SourcePath,
  SourceReadCheckResult,
  SourceSyncStatus,
  ReferenceType,
  TargetIdentifierKind
} from "../shared/api/types";
import { toErrorMessage } from "../shared/lib/errors";
import { sourceKey, type SourceKind } from "../shared/lib/sources";
import { createProjectReferenceMappingsLoadGuard } from "./projectReferenceMappingsLoadGuard";
import { moduleUsesMetadataDataflowEditor, moduleUsesProjectReferenceMappings } from "./environmentModuleData";
import { subscribeToEnvironmentHeaderRevalidation } from "./environmentHeaderRevalidation";
import { ResourceCache } from "../shared/data/resourceCache";
import { EnvironmentResourceStore, type EnvironmentResourceName } from "./environmentResourceStore";
import {
  fetchEnvironmentHeader,
  sourceCacheVersionChanged,
  sourceCheckIntervalMs,
  structuralCacheVersionChanged,
  type EnvironmentHeaderData
} from "./environmentHeaderResource";
import { fetchEnvironmentSources } from "./environmentSourcesResource";

export type SourceBatchAction = "validate" | "sync" | "delete";

export type SourceBatchEntry = {
  kind: SourceKind;
  id: number;
};

export type SourceBatchResult = {
  total: number;
  succeeded: number;
  warnings: number;
  failed: number;
  errors: string[];
};

export interface StudioWorkspace {
  projects: Project[];
  projectSummaries: ProjectSummary[];
  projectReferenceMappings: ProjectReferenceMapping[];
  environments: Environment[];
  selectedProject: Project | null;
  selectedProjectSummary: ProjectSummary | null;
  metadataSources: SourcePath[];
  logPaths: SourcePath[];
  codeArtifacts: SourcePath[];
  environmentFreshness: EnvironmentFreshness | null;
  sourceSyncStatuses: Record<string, SourceSyncStatus>;
  metadataEditorDocument: MetadataEditorDocument | null;
  metadataEditorDraft: MetadataEditorDocument | null;
  lineage: LineageResponse | null;
  assets: AssetInventoryResponse | null;
  overview: EnvironmentOverview | null;
  monitoringReport: MonitoringReport | null;
  latestStatus: LatestStatusResponse | null;
  loading: boolean;
  busy: boolean;
  error: string | null;
  refreshCurrentEnvironment: () => Promise<void>;
  ensureLatestRuns: () => Promise<void>;
  reloadProjectReferenceMappings: () => Promise<void>;
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
  syncSource: (kind: SourceKind, id: number) => Promise<SourceSyncStatus>;
  runSourceBatch: (action: SourceBatchAction, entries: SourceBatchEntry[]) => Promise<SourceBatchResult>;
  ensureMetadataEditorContext: () => Promise<void>;
  validateMetadataEditorDocument: (document: MetadataEditorDocument) => Promise<MetadataEditorDocument>;
  saveMetadataEditorDraft: (document: MetadataEditorDocument) => Promise<MetadataEditorDocument>;
  discardMetadataEditorDraft: (sourceId: number) => Promise<void>;
  saveMetadataEditorDocument: (document: MetadataEditorDocument) => Promise<MetadataEditorDocument>;
  listMetadataBackups: (sourceId: number) => Promise<MetadataBackup[]>;
  previewMetadataBackup: (backupId: number) => Promise<MetadataEditorDocument>;
  restoreMetadataBackup: (backup: MetadataBackup, document: MetadataEditorDocument) => Promise<MetadataEditorDocument>;
  deleteMetadataBackup: (backupId: number) => Promise<void>;
  clearMetadataBackups: (sourceId: number) => Promise<void>;
}

/**
 * Owns all workspace domain data (projects, environments, sources, metadata,
 * lineage, monitoring) and the mutations that act on it. Route-driven loading
 * is coordinated through the supplied {@link StudioRouter}. Presentation lives
 * entirely in feature components; this hook is the container/data layer.
 */
export function useStudioWorkspace(
  router: StudioRouter,
  options?: { sourceCheckIntervalSeconds?: number }
): StudioWorkspace {
  const { route, activeScope, setStudioRoute } = router;

  const [projects, setProjects] = useState<Project[]>([]);
  const [projectSummaries, setProjectSummaries] = useState<ProjectSummary[]>([]);
  const [projectReferenceMappings, setProjectReferenceMappings] = useState<ProjectReferenceMapping[]>([]);
  const [environments, setEnvironments] = useState<Environment[]>([]);
  const [metadataSources, setMetadataSources] = useState<SourcePath[]>([]);
  const [logPaths, setLogPaths] = useState<SourcePath[]>([]);
  const [codeArtifacts, setCodeArtifacts] = useState<SourcePath[]>([]);
  const [environmentFreshness, setEnvironmentFreshness] = useState<EnvironmentFreshness | null>(null);
  const [sourceSyncStatuses, setSourceSyncStatuses] = useState<Record<string, SourceSyncStatus>>({});
  const [metadataEditorDocument, setMetadataEditorDocument] = useState<MetadataEditorDocument | null>(null);
  const [metadataEditorDraft, setMetadataEditorDraft] = useState<MetadataEditorDocument | null>(null);
  const [lineage, setLineage] = useState<LineageResponse | null>(null);
  const [assets, setAssets] = useState<AssetInventoryResponse | null>(null);
  const [overview, setOverview] = useState<EnvironmentOverview | null>(null);
  const [monitoringReport, setMonitoringReport] = useState<MonitoringReport | null>(null);
  const [latestStatus, setLatestStatus] = useState<LatestStatusResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const lastLoadedEnvironmentId = useRef<number | null>(null);
  const activeProjectIdRef = useRef<number | null>(route.projectId);
  const activeEnvironmentIdRef = useRef<number | null>(route.environmentId);
  const environmentModuleLoadGeneration = useRef(0);
  const environmentHeaderCache = useRef(new ResourceCache<number, EnvironmentHeaderData>());
  const environmentResourceStore = useRef(new EnvironmentResourceStore());
  const projectReferenceMappingsLoadGuard = useRef(createProjectReferenceMappingsLoadGuard());

  // Keep async mapping responses tied to the project visible in this render.
  // Updating the ref during render closes the gap before the route effects run.
  activeProjectIdRef.current = route.projectId;
  activeEnvironmentIdRef.current = route.environmentId;

  const selectedProject = useMemo(
    () => projects.find((project) => project.id === route.projectId) ?? null,
    [projects, route.projectId]
  );
  const selectedProjectSummary = useMemo(
    () => projectSummaries.find((project) => project.id === route.projectId) ?? null,
    [projectSummaries, route.projectId]
  );

  function clearEnvironmentData() {
    setMetadataEditorDocument(null);
    setMetadataEditorDraft(null);
    setLineage(null);
    setAssets(null);
    setOverview(null);
    setMonitoringReport(null);
    setLatestStatus(null);
    setEnvironmentFreshness(null);
  }

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

  async function loadProjectSummaries() {
    setError(null);
    try {
      setProjectSummaries(await api.listProjectSummaries());
    } catch (err) {
      setError(toErrorMessage(err));
    }
  }

  async function loadProjectReferenceMappings(projectId = route.projectId, showBusy = true) {
    // A mutation started in a prior route render can resume after navigation.
    // It must not start a request for the project that is no longer active.
    if (projectId !== activeProjectIdRef.current) return;

    const request = projectReferenceMappingsLoadGuard.current.begin(projectId, showBusy);
    if (request.projectChanged) {
      setProjectReferenceMappings([]);
    }
    if (!projectId) {
      if (projectReferenceMappingsLoadGuard.current.finish(request)) setBusy(false);
      return;
    }
    if (showBusy) setBusy(true);
    setError(null);
    try {
      const items = await api.listProjectReferenceMappings(projectId);
      if (!projectReferenceMappingsLoadGuard.current.isCurrent(request) || activeProjectIdRef.current !== projectId) return;
      setProjectReferenceMappings(items.filter((mapping) => mapping.project_id === projectId));
    } catch (err) {
      if (!projectReferenceMappingsLoadGuard.current.isCurrent(request) || activeProjectIdRef.current !== projectId) return;
      setError(toErrorMessage(err));
    } finally {
      if (projectReferenceMappingsLoadGuard.current.finish(request)) setBusy(false);
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

  function applyEnvironmentHeader(environmentId: number, header: EnvironmentHeaderData) {
    if (activeEnvironmentIdRef.current !== environmentId) return;
    setEnvironmentFreshness(header.freshness);
  }

  async function loadEnvironmentHeader(environmentId: number, force = false) {
    const cached = environmentHeaderCache.current.peek(environmentId);
    if (cached) applyEnvironmentHeader(environmentId, cached.data);
    const result = await environmentHeaderCache.current.load(
      environmentId,
      () => fetchEnvironmentHeader(environmentId),
      { ttlMs: sourceCheckIntervalMs(options?.sourceCheckIntervalSeconds), force }
    );
    if (result.current) applyEnvironmentHeader(environmentId, result.data);
    const sourceCacheChanged = Boolean(
      cached
      && result.current
      && !result.fromCache
      && sourceCacheVersionChanged(cached.data, result.data)
    );
    const structuralCacheChanged = Boolean(
      cached
      && result.current
      && !result.fromCache
      && structuralCacheVersionChanged(cached.data, result.data)
    );
    if (sourceCacheChanged) invalidateEnvironmentResources(environmentId);
    else if (structuralCacheChanged) environmentResourceStore.current.invalidateStructural(environmentId);
    return result.current ? { data: result.data, sourceCacheChanged, structuralCacheChanged } : null;
  }

  function invalidateEnvironmentHeader(environmentId = route.environmentId) {
    if (environmentId) environmentHeaderCache.current.invalidate(environmentId);
  }

  async function loadEnvironmentResource<T>(
    environmentId: number,
    resource: EnvironmentResourceName,
    fetcher: () => Promise<T>,
    force = false
  ) {
    return environmentResourceStore.current.load(
      environmentId,
      resource,
      fetcher,
      { force }
    );
  }

  function invalidateEnvironmentResources(environmentId = route.environmentId) {
    if (!environmentId) return;
    environmentResourceStore.current.invalidateEnvironment(environmentId);
  }

  function invalidateEnvironmentResource(environmentId: number, resource: EnvironmentResourceName) {
    environmentResourceStore.current.invalidateResource(environmentId, resource);
  }

  function isCurrentEnvironmentLoad(environmentId: number, generation: number) {
    return activeEnvironmentIdRef.current === environmentId
      && environmentModuleLoadGeneration.current === generation;
  }

  function loadMetadataEditorContext(environmentId: number, force = false) {
    return Promise.all([
      loadEnvironmentResource(
        environmentId,
        "editor-document",
        () => api.getEnvironmentMetadataEditorDocument(environmentId),
        force,
      ),
      loadEnvironmentResource(
        environmentId,
        "editor-draft",
        () => api.getEnvironmentMetadataEditorDraft(environmentId),
        force,
      ),
    ]);
  }

  async function loadEnvironmentModule(
    environmentId: number,
    module: ModuleKey,
    generation: number,
    force = false
  ) {
    if (module === "settings") return;
    if (module === "sources") {
      const sources = await loadEnvironmentResource(
        environmentId,
        "sources",
        () => fetchEnvironmentSources(environmentId),
        force,
      );
      if (isCurrentEnvironmentLoad(environmentId, generation)) {
        setMetadataSources(sources.metadataSources);
        setLogPaths(sources.logPaths);
        setCodeArtifacts(sources.codeArtifacts);
      }
      const statuses = await fetchSourceSyncStatuses(
        environmentId,
        sources.metadataSources,
        sources.logPaths,
        sources.codeArtifacts,
      );
      if (!isCurrentEnvironmentLoad(environmentId, generation)) return;
      setSourceSyncStatuses(statuses);
      return;
    }

    if (module === "overview") {
      const overviewData = await loadEnvironmentResource(
        environmentId,
        "overview",
        () => api.getEnvironmentOverview(environmentId),
        force,
      );
      if (isCurrentEnvironmentLoad(environmentId, generation)) setOverview(overviewData);
      return;
    }

    if (moduleUsesMetadataDataflowEditor(module)) {
      const editorContext = await loadMetadataEditorContext(environmentId, force);
      if (!isCurrentEnvironmentLoad(environmentId, generation)) return;
      setMetadataEditorDocument(editorContext[0]);
      setMetadataEditorDraft(editorContext[1]);
      return;
    }

    if (module === "lineage") {
      const lineageData = await loadEnvironmentResource(
        environmentId,
        "lineage",
        () => api.getLineage(environmentId),
        force,
      );
      if (!isCurrentEnvironmentLoad(environmentId, generation)) return;
      setLineage(lineageData);
      return;
    }

    if (module === "assets") {
      const assetsData = await loadEnvironmentResource(
        environmentId,
        "assets",
        () => api.getAssets(environmentId),
        force,
      );
      if (!isCurrentEnvironmentLoad(environmentId, generation)) return;
      setAssets(assetsData);
      return;
    }

    if (module === "monitoring") return;
  }

  async function ensureLatestRuns() {
    const environmentId = route.environmentId;
    if (!environmentId) return;
    const generation = environmentModuleLoadGeneration.current;
    const statusData = await loadEnvironmentResource(
      environmentId,
      "latest-status",
      () => api.getLatestStatus(environmentId),
      false,
    );
    if (isCurrentEnvironmentLoad(environmentId, generation)) setLatestStatus(statusData);
  }

  async function ensureMetadataEditorContext() {
    const environmentId = route.environmentId;
    if (!environmentId) return;
    const [document, draft] = await loadMetadataEditorContext(environmentId);
    if (activeEnvironmentIdRef.current !== environmentId) return;
    setMetadataEditorDocument(document);
    setMetadataEditorDraft(draft);
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
      const header = await loadEnvironmentHeader(environmentId, refreshOptions?.forceHeader ?? false);
      await loadEnvironmentModule(
        environmentId,
        module,
        generation,
        Boolean(refreshOptions?.forceModule || header?.sourceCacheChanged),
      );
    } catch (err) {
      if (isCurrentEnvironmentLoad(environmentId, generation)) setError(toErrorMessage(err));
    } finally {
      if (isCurrentEnvironmentLoad(environmentId, generation)) setLoading(false);
    }
  }

  async function revalidateEnvironmentSourceCache(
    environmentId: number,
    module: ModuleKey,
  ) {
    const header = await loadEnvironmentHeader(environmentId);
    if (!header?.sourceCacheChanged) return;
    const generation = ++environmentModuleLoadGeneration.current;
    setLoading(true);
    try {
      await loadEnvironmentModule(environmentId, module, generation, true);
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
    invalidateEnvironmentHeader(environmentId);
    await loadEnvironmentHeader(environmentId, true);
  }

  async function refreshEnvironmentAfterHeaderMutation(
    environmentId = route.environmentId,
    module: ModuleKey = route.module
  ) {
    if (!environmentId) return;
    invalidateEnvironmentHeader(environmentId);
    invalidateEnvironmentResources(environmentId);
    await refreshEnvironment(environmentId, module, { forceHeader: true });
  }

  async function createProject(name: string) {
    setBusy(true);
    setError(null);
    try {
      const project = await api.createProject({ name });
      setProjects((current) => [...current, project].sort((a, b) => a.name.localeCompare(b.name)));
      await loadProjectSummaries();
      setStudioRoute({ projectId: project.id, environmentId: null, module: "projects", projectSection: projectDefaultSection });
    } catch (err) {
      setError(toErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function deleteProject(projectId: number) {
    setBusy(true);
    setError(null);
    try {
      await api.deleteProject(projectId);
      if (route.projectId === projectId) {
        setStudioRoute({ projectId: null, environmentId: null, module: "projects" });
      }
      setProjects((current) => current.filter((project) => project.id !== projectId));
      setProjectSummaries((current) => current.filter((project) => project.id !== projectId));
      setProjectReferenceMappings([]);
      setEnvironments([]);
      clearEnvironmentSources();
      clearEnvironmentData();
      await loadProjects();
      await loadProjectSummaries();
    } catch (err) {
      setError(toErrorMessage(err));
      throw err;
    } finally {
      setBusy(false);
    }
  }

  async function createProjectReferenceMapping(payload: {
    reference_type: ReferenceType;
    reference_value: string;
    target_identifier_kind: TargetIdentifierKind;
    target_value: string;
    target_display_value?: string | null;
    note?: string | null;
  }) {
    if (!route.projectId) throw new Error("Select a project before creating a mapping.");
    setBusy(true);
    setError(null);
    try {
      const mapping = await api.createProjectReferenceMapping(route.projectId, payload);
      await loadProjectReferenceMappings(route.projectId, false);
      return mapping;
    } catch (err) {
      setError(toErrorMessage(err));
      throw err;
    } finally {
      setBusy(false);
    }
  }

  async function updateProjectReferenceMapping(
    mappingId: number,
    payload: {
      reference_type?: ReferenceType;
      reference_value?: string | null;
      target_identifier_kind?: TargetIdentifierKind;
      target_value?: string | null;
      target_display_value?: string | null;
      note?: string | null;
    }
  ) {
    if (!route.projectId) throw new Error("Select a project before updating a mapping.");
    setBusy(true);
    setError(null);
    try {
      const mapping = await api.updateProjectReferenceMapping(route.projectId, mappingId, payload);
      await loadProjectReferenceMappings(route.projectId, false);
      return mapping;
    } catch (err) {
      setError(toErrorMessage(err));
      throw err;
    } finally {
      setBusy(false);
    }
  }

  async function deleteProjectReferenceMapping(mappingId: number) {
    if (!route.projectId) throw new Error("Select a project before removing a mapping.");
    setBusy(true);
    setError(null);
    try {
      await api.deleteProjectReferenceMapping(route.projectId, mappingId);
      await loadProjectReferenceMappings(route.projectId, false);
    } catch (err) {
      setError(toErrorMessage(err));
      throw err;
    } finally {
      setBusy(false);
    }
  }

  async function createEnvironment(name: string, projectIdOverride?: number): Promise<number> {
    const pid = projectIdOverride ?? route.projectId;
    if (!pid) return 0;
    setBusy(true);
    setError(null);
    try {
      const environment = await api.createEnvironment(pid, { name });
      await loadProjectSummaries();
      await loadEnvironments(pid, environment.id);
      return environment.id;
    } catch (err) {
      setError(toErrorMessage(err));
      return 0;
    } finally {
      setBusy(false);
    }
  }

  async function deleteEnvironment(environmentId: number) {
    const pid = route.projectId;
    setBusy(true);
    setError(null);
    try {
      await api.deleteEnvironment(environmentId);
      environmentHeaderCache.current.invalidate(environmentId);
      invalidateEnvironmentResources(environmentId);
      await loadProjectSummaries();
      if (pid) await loadEnvironments(pid);
    } catch (err) {
      setError(toErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function addMetadataSource(uri: string, label?: string) {
    if (!route.environmentId) return;
    setBusy(true);
    setError(null);
    try {
      await api.addMetadataSource(route.environmentId, { uri, label, enabled: true });
      await refreshEnvironmentAfterHeaderMutation(route.environmentId);
    } catch (err) {
      setError(toErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function importMetadataSources(uri: string, label?: string): Promise<SourceImportResponse | null> {
    if (!route.environmentId) return null;
    setBusy(true);
    setError(null);
    try {
      const result = await api.importMetadataSources(route.environmentId, { uri, label, enabled: true });
      await loadProjectSummaries();
      await refreshEnvironmentAfterHeaderMutation(route.environmentId, "sources");
      return result;
    } catch (err) {
      setError(toErrorMessage(err));
      throw err;
    } finally {
      setBusy(false);
    }
  }

  async function importDatacoolieProjectSources(payload: {
    project_uri: string;
    metadata_subpath?: string;
    code_subpath?: string;
    metadata_uri?: string | null;
    code_uri?: string | null;
    include_metadata?: boolean;
    include_code?: boolean;
  }): Promise<SourceImportResponse | null> {
    if (!route.environmentId) return null;
    setBusy(true);
    setError(null);
    try {
      const result = await api.importDatacoolieProjectSources(route.environmentId, { ...payload, enabled: true });
      await loadProjectSummaries();
      await refreshEnvironmentAfterHeaderMutation(route.environmentId, "sources");
      return result;
    } catch (err) {
      setError(toErrorMessage(err));
      throw err;
    } finally {
      setBusy(false);
    }
  }

  async function addLogPath(uri: string, label?: string, sourceConfig?: Record<string, unknown>) {
    if (!route.environmentId) throw new Error("Select an environment before adding a log source");
    setBusy(true);
    setError(null);
    try {
      await api.addLogSource(route.environmentId, { uri, label, enabled: true, source_config: sourceConfig });
      await refreshEnvironmentAfterHeaderMutation(route.environmentId);
    } catch (err) {
      setError(toErrorMessage(err));
      throw err;
    } finally {
      setBusy(false);
    }
  }

  async function addCodeArtifact(uri: string, label?: string, sourceConfig?: Record<string, unknown>) {
    if (!route.environmentId) throw new Error("Select an environment before adding source code");
    setBusy(true);
    setError(null);
    try {
      await api.addCodeArtifact(route.environmentId, { uri, label, enabled: true, source_config: sourceConfig });
      await refreshEnvironmentAfterHeaderMutation(route.environmentId);
    } catch (err) {
      setError(toErrorMessage(err));
      throw err;
    } finally {
      setBusy(false);
    }
  }

  async function updateSource(
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
  ) {
    const environmentId = route.environmentId;
    if (!environmentId) return;
    setBusy(true);
    setError(null);
    try {
      if (kind === "metadata") {
        await api.updateMetadataSource(environmentId, id, payload);
      } else if (kind === "logs") {
        await api.updateLogSource(environmentId, id, payload);
      } else {
        await api.updateCodeArtifact(environmentId, id, payload);
      }
      await loadProjectSummaries();
      await refreshEnvironmentAfterHeaderMutation(environmentId, route.module);
    } catch (err) {
      setError(toErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function deleteSource(kind: SourceKind, id: number) {
    const environmentId = route.environmentId;
    if (!environmentId) return;
    setBusy(true);
    setError(null);
    try {
      if (kind === "metadata") {
        await api.deleteMetadataSource(environmentId, id);
      } else if (kind === "logs") {
        await api.deleteLogSource(environmentId, id);
      } else {
        await api.deleteCodeArtifact(environmentId, id);
      }
      await loadProjectSummaries();
      await refreshEnvironmentAfterHeaderMutation(environmentId, route.module);
    } catch (err) {
      setError(toErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function validateSource(kind: SourceKind, id: number): Promise<SourceReadCheckResult> {
    const environmentId = route.environmentId;
    if (!environmentId) {
      const message = "Select an environment before validating a source";
      setError(message);
      return { source_id: id, source_kind: kind, status: "error", message, errors: [{ message }] };
    }
    setBusy(true);
    setError(null);
    try {
      const result =
        kind === "metadata"
          ? await api.validateMetadataSource(environmentId, id)
          : kind === "logs"
            ? await api.validateLogSource(environmentId, id)
            : await api.validateCodeArtifact(environmentId, id);
      invalidateEnvironmentResource(environmentId, "sources");
      await refreshEnvironment(environmentId, "sources", { forceHeader: true, forceModule: true });
      return result;
    } catch (err) {
      const message = toErrorMessage(err);
      setError(message);
      return { source_id: id, source_kind: kind, status: "error", message, errors: [{ message }] };
    } finally {
      setBusy(false);
    }
  }

  async function syncSource(kind: SourceKind, id: number): Promise<SourceSyncStatus> {
    const environmentId = route.environmentId;
    if (!environmentId) {
      const message = "Select an environment before syncing a source";
      setError(message);
      return {
        source_id: id,
        source_kind: kind,
        status: "error",
        message,
        error: { message },
        checked_at: new Date().toISOString(),
        latest_job: null
      };
    }
    setError(null);
    try {
      const result =
        kind === "metadata"
          ? await api.refreshMetadataSource(environmentId, id)
          : kind === "logs"
            ? await api.refreshLogSource(environmentId, id)
            : await api.refreshCodeArtifact(environmentId, id);
      setSourceSyncStatuses((current) => ({ ...current, [sourceKey(kind, id)]: result }));
      invalidateEnvironmentResources(environmentId);
      await refreshEnvironment(environmentId, route.module, { forceHeader: true, forceModule: true });
      return result;
    } catch (err) {
      const message = toErrorMessage(err);
      setError(message);
      const result: SourceSyncStatus = {
        source_id: id,
        source_kind: kind,
        status: "error",
        message,
        error: { message },
        checked_at: new Date().toISOString(),
        latest_job: null
      };
      setSourceSyncStatuses((current) => ({ ...current, [sourceKey(kind, id)]: result }));
      return result;
    }
  }

  async function runSourceBatch(action: SourceBatchAction, entries: SourceBatchEntry[]): Promise<SourceBatchResult> {
    const uniqueEntries = Array.from(
      new Map(entries.map((entry) => [`${entry.kind}:${entry.id}`, entry])).values()
    );
    const result: SourceBatchResult = {
      total: uniqueEntries.length,
      succeeded: 0,
      warnings: 0,
      failed: 0,
      errors: []
    };
    const environmentId = route.environmentId;
    if (!environmentId) {
      const message = "Select an environment before running a source action";
      setError(message);
      return { ...result, failed: result.total, errors: result.total ? [message] : [] };
    }
    if (!uniqueEntries.length) return result;

    setBusy(true);
    setError(null);
    try {
      for (const entry of uniqueEntries) {
        try {
          if (action === "delete") {
            if (entry.kind === "metadata") await api.deleteMetadataSource(environmentId, entry.id);
            else if (entry.kind === "logs") await api.deleteLogSource(environmentId, entry.id);
            else await api.deleteCodeArtifact(environmentId, entry.id);
            result.succeeded += 1;
            continue;
          }

          const operationResult = action === "validate"
            ? entry.kind === "metadata"
              ? await api.validateMetadataSource(environmentId, entry.id)
              : entry.kind === "logs"
                ? await api.validateLogSource(environmentId, entry.id)
                : await api.validateCodeArtifact(environmentId, entry.id)
            : entry.kind === "metadata"
              ? await api.refreshMetadataSource(environmentId, entry.id)
              : entry.kind === "logs"
                ? await api.refreshLogSource(environmentId, entry.id)
                : await api.refreshCodeArtifact(environmentId, entry.id);

          if (operationResult.status === "error") {
            result.failed += 1;
            result.errors.push(`${entry.kind} source #${entry.id}: ${operationResult.message}`);
          } else if (operationResult.status === "warning" || operationResult.status === "running" || operationResult.status === "unknown") {
            result.warnings += 1;
          } else {
            result.succeeded += 1;
          }
        } catch (err) {
          result.failed += 1;
          result.errors.push(`${entry.kind} source #${entry.id}: ${toErrorMessage(err)}`);
        }
      }

      if (action === "delete") await loadProjectSummaries();
      if (action === "validate") invalidateEnvironmentResource(environmentId, "sources");
      else invalidateEnvironmentResources(environmentId);
      await refreshEnvironment(environmentId, "sources", { forceHeader: true, forceModule: true });
    } finally {
      setBusy(false);
    }

    if (result.failed) setError(`${result.failed} source ${result.failed === 1 ? "action" : "actions"} failed`);
    return result;
  }

  async function validateMetadataEditorDocument(document: MetadataEditorDocument) {
    setBusy(true);
    setError(null);
    try {
      if (!route.environmentId) return document;
      const validation = await api.validateEnvironmentMetadataEditorDocument(route.environmentId, document);
      return { ...document, issues: validation.issues };
    } catch (err) {
      setError(toErrorMessage(err));
      throw err;
    } finally {
      setBusy(false);
    }
  }

  async function saveMetadataEditorDraft(document: MetadataEditorDocument) {
    setBusy(true);
    setError(null);
    try {
      if (!route.environmentId) return document;
      const nextDocument = await api.saveEnvironmentMetadataEditorDraft(route.environmentId, document);
      invalidateEnvironmentResources(route.environmentId);
      setMetadataEditorDraft(nextDocument);
      return nextDocument;
    } catch (err) {
      setError(toErrorMessage(err));
      throw err;
    } finally {
      setBusy(false);
    }
  }

  async function discardMetadataEditorDraft(_sourceId: number) {
    setBusy(true);
    setError(null);
    try {
      if (!route.environmentId) return;
      await api.discardEnvironmentMetadataEditorDraft(route.environmentId);
      invalidateEnvironmentResources(route.environmentId);
      setMetadataEditorDraft(null);
      setMetadataEditorDocument(await api.getEnvironmentMetadataEditorDocument(route.environmentId));
    } catch (err) {
      setError(toErrorMessage(err));
      throw err;
    } finally {
      setBusy(false);
    }
  }

  async function saveMetadataEditorDocument(document: MetadataEditorDocument) {
    setBusy(true);
    setError(null);
    try {
      if (!route.environmentId) return document;
      const savedDocument = await api.saveEnvironmentMetadataEditorDocument(route.environmentId, document);
      invalidateEnvironmentResources(route.environmentId);
      setMetadataEditorDocument(savedDocument);
      setMetadataEditorDraft(null);
      if (route.environmentId) {
        await loadProjectSummaries();
        await refreshEnvironmentHeader(route.environmentId);
      }
      return savedDocument;
    } catch (err) {
      setError(toErrorMessage(err));
      throw err;
    } finally {
      setBusy(false);
    }
  }

  async function listMetadataBackups(_sourceId: number) {
    if (!route.environmentId) return [];
    return api.listEnvironmentMetadataBackups(route.environmentId);
  }

  async function getSourceDeleteImpact(kind: SourceKind, id: number): Promise<SourceDeleteImpact> {
    const environmentId = route.environmentId;
    if (!environmentId) throw new Error("Select an environment before viewing source delete impact");
    if (kind === "metadata") return api.getMetadataSourceDeleteImpact(environmentId, id);
    if (kind === "logs") return api.getLogSourceDeleteImpact(environmentId, id);
    return api.getCodeArtifactDeleteImpact(environmentId, id);
  }

  async function previewMetadataBackup(backupId: number) {
    return api.getMetadataBackupDocument(backupId);
  }

  async function restoreMetadataBackup(backup: MetadataBackup, document: MetadataEditorDocument) {
    setBusy(true);
    setError(null);
    try {
      await api.restoreMetadataBackup(backup.id, sourceRevisionForBackup(document, backup));
      invalidateEnvironmentResources(route.environmentId);
      const nextDocument = route.environmentId
        ? await api.getEnvironmentMetadataEditorDocument(route.environmentId)
        : document;
      setMetadataEditorDocument(nextDocument);
      setMetadataEditorDraft(null);
      if (route.environmentId) {
        await refreshEnvironmentHeader(route.environmentId);
      }
      return nextDocument;
    } catch (err) {
      setError(toErrorMessage(err));
      throw err;
    } finally {
      setBusy(false);
    }
  }

  async function deleteMetadataBackup(backupId: number) {
    setBusy(true);
    setError(null);
    try {
      await api.deleteMetadataBackup(backupId);
    } catch (err) {
      setError(toErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function clearMetadataBackups(_sourceId: number) {
    setBusy(true);
    setError(null);
    try {
      if (!route.environmentId) return;
      await api.deleteEnvironmentMetadataBackups(route.environmentId);
    } catch (err) {
      setError(toErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  // Initial load.
  useEffect(() => {
    void loadProjects();
    void loadProjectSummaries();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Reload summaries when viewing project hub/detail routes.
  useEffect(() => {
    if (route.module === "projects") {
      void loadProjectSummaries();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [route.module, route.projectSection]);

  useEffect(() => {
    if (moduleUsesProjectReferenceMappings(route.module) && route.projectId) {
      void loadProjectReferenceMappings(route.projectId, false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [route.module, route.projectId]);

  // Load environments when the selected project changes.
  useEffect(() => {
    if (route.projectId) {
      void loadEnvironments(route.projectId);
    } else {
      void loadProjectReferenceMappings(null, false);
      setEnvironments([]);
      clearEnvironmentSources();
      clearEnvironmentData();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [route.projectId]);

  // Load environment-scoped data when the environment or module changes.
  useEffect(() => {
    if (!route.environmentId) {
      lastLoadedEnvironmentId.current = null;
      clearEnvironmentSources();
      clearEnvironmentData();
      return;
    }
    if (lastLoadedEnvironmentId.current !== route.environmentId) {
      lastLoadedEnvironmentId.current = route.environmentId;
      clearEnvironmentSources();
      clearEnvironmentData();
    }
    void refreshEnvironment(route.environmentId, route.module);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [route.environmentId, route.module]);

  // The configured interval is the header's stale threshold, not a polling
  // cadence. Module data remains until its materialized source-cache version changes.
  useEffect(() => {
    if (route.environmentId) void loadEnvironmentHeader(route.environmentId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [options?.sourceCheckIntervalSeconds]);

  useEffect(() => {
    if (!route.environmentId) return;
    const environmentId = route.environmentId;
    const module = route.module;
    return subscribeToEnvironmentHeaderRevalidation(() => {
      void revalidateEnvironmentSourceCache(environmentId, module);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [route.environmentId, route.module, options?.sourceCheckIntervalSeconds]);

  return {
    projects,
    projectSummaries,
    projectReferenceMappings,
    environments,
    selectedProject,
    selectedProjectSummary,
    metadataSources,
    logPaths,
    codeArtifacts,
    environmentFreshness,
    sourceSyncStatuses,
    metadataEditorDocument,
    metadataEditorDraft,
    lineage,
    assets,
    overview,
    monitoringReport,
    latestStatus,
    loading,
    busy,
    error,
    refreshCurrentEnvironment: () => refreshEnvironment(route.environmentId, route.module, { forceHeader: true, forceModule: true }),
    ensureLatestRuns,
    reloadProjectReferenceMappings: () => loadProjectReferenceMappings(route.projectId),
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
    ensureMetadataEditorContext,
    validateMetadataEditorDocument,
    saveMetadataEditorDraft,
    discardMetadataEditorDraft,
    saveMetadataEditorDocument,
    listMetadataBackups,
    previewMetadataBackup,
    restoreMetadataBackup,
    deleteMetadataBackup,
    clearMetadataBackups
  };
}

function sourceRevisionForBackup(document: MetadataEditorDocument, backup: MetadataBackup) {
  const revision = document.source.revision;
  const sources = Array.isArray(revision.sources) ? revision.sources : [];
  for (const item of sources) {
    if (!item || typeof item !== "object") continue;
    const source = item as Record<string, unknown>;
    if (Number(source.source_id) !== backup.source_id) continue;
    const sourceRevision = source.revision;
    return sourceRevision && typeof sourceRevision === "object"
      ? sourceRevision as Record<string, unknown>
      : source;
  }
  return revision;
}
