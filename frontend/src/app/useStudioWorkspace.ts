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
} from "../shared/api/types";
import { toErrorMessage } from "../shared/lib/errors";
import { sourceKey, type SourceKind } from "../shared/lib/sources";
import { projectRouteResources } from "./projectRouteResources";
import { addEnvironmentToProject, addProjectSummary, changeProjectReferenceMappingCount, removeEnvironmentFromProject } from "./projectSummaryMutations";
import { ResourceCache } from "../shared/data/resourceCache";
import { fetchEnvironmentSources } from "./environmentSourcesResource";
import { environmentQueryKeys } from "../features/environments/environmentQueries";
import { useEnvironmentMetadataEditor } from "../features/metadata-explorer/metadataEditorQueries";

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

  async function createProject(name: string) {
    setBusy(true);
    setError(null);
    try {
      const project = await api.createProject({ name });
      setProjects((current) => [...current, project].sort((a, b) => a.name.localeCompare(b.name)));
      updateProjectSummaries((current) => addProjectSummary(current, project));
      setStudioRoute({ projectId: project.id, environmentId: null, module: "projects", projectSection: projectDefaultSection });
    } catch (err) {
      setError(toErrorMessage(err));
      throw err;
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
      updateProjectSummaries((current) => current.filter((project) => project.id !== projectId));
      setEnvironments([]);
      clearEnvironmentSources();
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
    const projectId = route.projectId;
    setBusy(true);
    setError(null);
    try {
      const mapping = await api.createProjectReferenceMapping(projectId, payload);
      if (route.environmentId) await options?.onEnvironmentChanged?.(route.environmentId);
      updateProjectSummaries((current) => changeProjectReferenceMappingCount(current, projectId, 1));
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
    const projectId = route.projectId;
    setBusy(true);
    setError(null);
    try {
      const mapping = await api.updateProjectReferenceMapping(projectId, mappingId, payload);
      if (route.environmentId) await options?.onEnvironmentChanged?.(route.environmentId);
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
    const projectId = route.projectId;
    setBusy(true);
    setError(null);
    try {
      await api.deleteProjectReferenceMapping(projectId, mappingId);
      if (route.environmentId) await options?.onEnvironmentChanged?.(route.environmentId);
      updateProjectSummaries((current) => changeProjectReferenceMappingCount(current, projectId, -1));
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
      updateProjectSummaries((current) => addEnvironmentToProject(current, pid, environment));
      setEnvironments((current) => [...current.filter((item) => item.id !== environment.id), environment]
        .sort((left, right) => left.name.localeCompare(right.name)));
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
      queryClient.removeQueries({ queryKey: ["environments", environmentId] });
      updateProjectSummaries((current) => removeEnvironmentFromProject(current, pid, environmentId));
      setEnvironments((current) => current.filter((environment) => environment.id !== environmentId));
    } catch (err) {
      setError(toErrorMessage(err));
      throw err;
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
      invalidateProjectSummaries();
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
      invalidateProjectSummaries();
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
      invalidateProjectSummaries();
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
      invalidateProjectSummaries();
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
      invalidateProjectSummaries();
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
      invalidateProjectSummaries();
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
      invalidateProjectSummaries();
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

  async function syncSource(kind: SourceKind, id: number, logSyncRequest?: LogSyncRequest): Promise<SourceSyncStatus> {
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
            ? await api.refreshLogSource(environmentId, id, logSyncRequest ?? { mode: "incremental" })
            : await api.refreshCodeArtifact(environmentId, id);
      if (activeEnvironmentIdRef.current === environmentId) {
        setSourceSyncStatuses((current) => ({ ...current, [sourceKey(kind, id)]: result }));
        await refreshEnvironment(environmentId, route.module, { forceHeader: true, forceModule: true });
      }
      return result;
    } catch (err) {
      const message = toErrorMessage(err);
      if (activeEnvironmentIdRef.current === environmentId) setError(message);
      const result: SourceSyncStatus = {
        source_id: id,
        source_kind: kind,
        status: "error",
        message,
        error: { message },
        checked_at: new Date().toISOString(),
        latest_job: null
      };
      if (activeEnvironmentIdRef.current === environmentId) {
        setSourceSyncStatuses((current) => ({ ...current, [sourceKey(kind, id)]: result }));
      }
      return result;
    }
  }

  async function runSourceBatch(action: SourceBatchAction, entries: SourceBatchEntry[], logSyncRequest?: LogSyncRequest): Promise<SourceBatchResult> {
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
                ? await api.refreshLogSource(environmentId, entry.id, logSyncRequest ?? { mode: "incremental" })
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

      if (action === "delete") invalidateProjectSummaries();
      await refreshEnvironment(environmentId, "sources", { forceHeader: true, forceModule: true });
    } finally {
      setBusy(false);
    }

    if (result.failed) setError(`${result.failed} source ${result.failed === 1 ? "action" : "actions"} failed`);
    return result;
  }

  async function getSourceDeleteImpact(kind: SourceKind, id: number): Promise<SourceDeleteImpact> {
    const environmentId = route.environmentId;
    if (!environmentId) throw new Error("Select an environment before viewing source delete impact");
    if (kind === "metadata") return api.getMetadataSourceDeleteImpact(environmentId, id);
    if (kind === "logs") return api.getLogSourceDeleteImpact(environmentId, id);
    return api.getCodeArtifactDeleteImpact(environmentId, id);
  }

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
