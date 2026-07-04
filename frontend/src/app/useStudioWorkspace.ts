import { useEffect, useMemo, useRef, useState } from "react";
import {
  environmentDefaultModule,
  moduleByKey,
  monitoringDefaultPage,
  projectDefaultModule,
  type ModuleKey
} from "./moduleRegistry";
import type { StudioRouter } from "./useStudioRouter";
import { api } from "../shared/api/client";
import type {
  AssetsResponse,
  Environment,
  EnvironmentFreshness,
  LatestStatusResponse,
  LineageResponse,
  MetadataBackup,
  MetadataEditorDocument,
  MetadataResponse,
  MonitoringReport,
  Project,
  ProjectSummary,
  SourceImportResponse,
  SourcePath,
  SourceReadCheckResult,
  SourceSyncStatus
} from "../shared/api/types";
import { toErrorMessage } from "../shared/lib/errors";
import { sourceKey, type SourceKind } from "../shared/lib/sources";

export interface StudioWorkspace {
  projects: Project[];
  projectSummaries: ProjectSummary[];
  environments: Environment[];
  selectedProject: Project | null;
  selectedProjectSummary: ProjectSummary | null;
  metadataSources: SourcePath[];
  logPaths: SourcePath[];
  codeArtifacts: SourcePath[];
  environmentFreshness: EnvironmentFreshness | null;
  sourceSyncStatuses: Record<string, SourceSyncStatus>;
  metadata: MetadataResponse | null;
  metadataEditorDocument: MetadataEditorDocument | null;
  metadataEditorDraft: MetadataEditorDocument | null;
  lineage: LineageResponse | null;
  assets: AssetsResponse | null;
  monitoringReport: MonitoringReport | null;
  latestStatus: LatestStatusResponse | null;
  loading: boolean;
  busy: boolean;
  error: string | null;
  refreshCurrentEnvironment: () => Promise<void>;
  createProject: (name: string) => Promise<void>;
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
  deleteSources: (kind: SourceKind, ids: number[]) => Promise<void>;
  validateSource: (kind: SourceKind, id: number) => Promise<SourceReadCheckResult>;
  syncSource: (kind: SourceKind, id: number) => Promise<SourceSyncStatus>;
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
export function useStudioWorkspace(router: StudioRouter): StudioWorkspace {
  const { route, activeScope, setStudioRoute } = router;

  const [projects, setProjects] = useState<Project[]>([]);
  const [projectSummaries, setProjectSummaries] = useState<ProjectSummary[]>([]);
  const [environments, setEnvironments] = useState<Environment[]>([]);
  const [metadataSources, setMetadataSources] = useState<SourcePath[]>([]);
  const [logPaths, setLogPaths] = useState<SourcePath[]>([]);
  const [codeArtifacts, setCodeArtifacts] = useState<SourcePath[]>([]);
  const [environmentFreshness, setEnvironmentFreshness] = useState<EnvironmentFreshness | null>(null);
  const [sourceSyncStatuses, setSourceSyncStatuses] = useState<Record<string, SourceSyncStatus>>({});
  const [metadata, setMetadata] = useState<MetadataResponse | null>(null);
  const [metadataEditorDocument, setMetadataEditorDocument] = useState<MetadataEditorDocument | null>(null);
  const [metadataEditorDraft, setMetadataEditorDraft] = useState<MetadataEditorDocument | null>(null);
  const [lineage, setLineage] = useState<LineageResponse | null>(null);
  const [assets, setAssets] = useState<AssetsResponse | null>(null);
  const [monitoringReport, setMonitoringReport] = useState<MonitoringReport | null>(null);
  const [latestStatus, setLatestStatus] = useState<LatestStatusResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const lastLoadedEnvironmentId = useRef<number | null>(null);

  const selectedProject = useMemo(
    () => projects.find((project) => project.id === route.projectId) ?? null,
    [projects, route.projectId]
  );
  const selectedProjectSummary = useMemo(
    () => projectSummaries.find((project) => project.id === route.projectId) ?? null,
    [projectSummaries, route.projectId]
  );

  function clearEnvironmentData() {
    setMetadata(null);
    setMetadataEditorDocument(null);
    setMetadataEditorDraft(null);
    setLineage(null);
    setAssets(null);
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
      if (items[0]) setStudioRoute({ projectId: items[0].id, environmentId: null, module: projectDefaultModule }, true);
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
        setStudioRoute({ projectId, environmentId: null, module: "environments" }, replace);
      }
    } catch (err) {
      setError(toErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function refreshEnvironment(environmentId = route.environmentId, module: ModuleKey = route.module) {
    if (!environmentId) return;
    setLoading(true);
    setError(null);
    try {
      const [sources, paths, artifacts, freshness] = await Promise.all([
        api.listMetadataSources(environmentId),
        api.listLogSources(environmentId),
        api.listCodeArtifacts(environmentId),
        api.getEnvironmentFreshness(environmentId)
      ]);
      setMetadataSources(sources);
      setLogPaths(paths);
      setCodeArtifacts(artifacts);
      setEnvironmentFreshness(freshness);

      if (module === "settings" || module === "sources") {
        if (module === "sources") {
          await loadSourceSyncStatuses(sources, paths, artifacts);
        }
        return;
      }

      if (module === "metadata") {
        const [metadataData, statusData, editorDocument, editorDraft] = await Promise.all([
          api.getMetadata(environmentId),
          api.getLatestStatus(environmentId),
          api.getEnvironmentMetadataEditorDocument(environmentId),
          api.getEnvironmentMetadataEditorDraft(environmentId)
        ]);
        setMetadata(metadataData);
        setLatestStatus(statusData);
        setMetadataEditorDocument(editorDocument);
        setMetadataEditorDraft(editorDraft);
        return;
      }

      if (module === "lineage") {
        const [lineageData, statusData] = await Promise.all([
          api.getLineage(environmentId),
          api.getLatestStatus(environmentId)
        ]);
        setLineage(lineageData);
        setLatestStatus(statusData);
        return;
      }

      if (module === "assets") {
        setAssets(await api.getAssets(environmentId));
        return;
      }

      if (module === "monitoring") {
        return;
      }

      const [metadataData, lineageData, monitoringData] = await Promise.all([
        api.getMetadata(environmentId),
        api.getLineage(environmentId),
        api.getMonitoringReport(environmentId)
      ]);
      setMetadata(metadataData);
      setLineage(lineageData);
      setMonitoringReport(monitoringData);
    } catch (err) {
      setError(toErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }

  async function loadSourceSyncStatuses(
    sources = metadataSources,
    paths = logPaths,
    artifacts = codeArtifacts
  ) {
    const entries = await Promise.all([
      ...sources.map(async (source): Promise<[string, SourceSyncStatus]> => [
        sourceKey("metadata", source.id),
        await api.getMetadataSourceSyncStatus(source.id)
      ]),
      ...paths.map(async (path): Promise<[string, SourceSyncStatus]> => [
        sourceKey("logs", path.id),
        await api.getLogSourceSyncStatus(path.id)
      ]),
      ...artifacts.map(async (artifact): Promise<[string, SourceSyncStatus]> => [
        sourceKey("code", artifact.id),
        await api.getCodeArtifactSyncStatus(artifact.id)
      ])
    ]);
    setSourceSyncStatuses(Object.fromEntries(entries));
  }

  async function loadEnvironmentFreshness(environmentId = route.environmentId) {
    if (!environmentId) return;
    setEnvironmentFreshness(await api.getEnvironmentFreshness(environmentId));
  }

  async function createProject(name: string) {
    setBusy(true);
    setError(null);
    try {
      const project = await api.createProject({ name });
      setProjects((current) => [...current, project].sort((a, b) => a.name.localeCompare(b.name)));
      await loadProjectSummaries();
      setStudioRoute({ projectId: project.id, environmentId: null, module: projectDefaultModule });
    } catch (err) {
      setError(toErrorMessage(err));
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
      await refreshEnvironment(route.environmentId);
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
      await refreshEnvironment(route.environmentId, "sources");
      return result;
    } catch (err) {
      setError(toErrorMessage(err));
      return null;
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
      await refreshEnvironment(route.environmentId, "sources");
      return result;
    } catch (err) {
      setError(toErrorMessage(err));
      return null;
    } finally {
      setBusy(false);
    }
  }

  async function addLogPath(uri: string, label?: string, sourceConfig?: Record<string, unknown>) {
    if (!route.environmentId) return;
    setBusy(true);
    setError(null);
    try {
      await api.addLogSource(route.environmentId, { uri, label, enabled: true, source_config: sourceConfig });
      await refreshEnvironment(route.environmentId);
    } catch (err) {
      setError(toErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function addCodeArtifact(uri: string, label?: string, sourceConfig?: Record<string, unknown>) {
    if (!route.environmentId) return;
    setBusy(true);
    setError(null);
    try {
      await api.addCodeArtifact(route.environmentId, { uri, label, enabled: true, source_config: sourceConfig });
      await refreshEnvironment(route.environmentId);
    } catch (err) {
      setError(toErrorMessage(err));
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
    setBusy(true);
    setError(null);
    try {
      if (kind === "metadata") {
        await api.updateMetadataSource(id, payload);
      } else if (kind === "logs") {
        await api.updateLogSource(id, payload);
      } else {
        await api.updateCodeArtifact(id, payload);
      }
      await loadProjectSummaries();
      await refreshEnvironment(route.environmentId, route.module);
    } catch (err) {
      setError(toErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function deleteSource(kind: SourceKind, id: number) {
    setBusy(true);
    setError(null);
    try {
      if (kind === "metadata") {
        const impact = await api.getMetadataSourceDeleteImpact(id);
        if (impact.has_impact && !window.confirm(metadataDeleteImpactMessage(impact))) {
          return;
        }
        await api.deleteMetadataSource(id);
      } else if (kind === "logs") {
        await api.deleteLogSource(id);
      } else {
        await api.deleteCodeArtifact(id);
      }
      await loadProjectSummaries();
      await refreshEnvironment(route.environmentId, route.module);
    } catch (err) {
      setError(toErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function deleteSources(kind: SourceKind, ids: number[]) {
    const uniqueIds = Array.from(new Set(ids));
    if (!uniqueIds.length) return;
    setBusy(true);
    setError(null);
    try {
      if (kind === "metadata") {
        const impacts = await Promise.all(uniqueIds.map((id) => api.getMetadataSourceDeleteImpact(id)));
        if (!window.confirm(metadataBulkDeleteImpactMessage(impacts))) return;
        await Promise.all(uniqueIds.map((id) => api.deleteMetadataSource(id)));
      } else if (kind === "logs") {
        if (!window.confirm(`Remove ${uniqueIds.length} log source${uniqueIds.length === 1 ? "" : "s"}?`)) return;
        await Promise.all(uniqueIds.map((id) => api.deleteLogSource(id)));
      } else {
        if (!window.confirm(`Remove ${uniqueIds.length} code artifact${uniqueIds.length === 1 ? "" : "s"}?`)) return;
        await Promise.all(uniqueIds.map((id) => api.deleteCodeArtifact(id)));
      }
      await loadProjectSummaries();
      await refreshEnvironment(route.environmentId, route.module);
    } catch (err) {
      setError(toErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function validateSource(kind: SourceKind, id: number): Promise<SourceReadCheckResult> {
    setBusy(true);
    setError(null);
    try {
      const result =
        kind === "metadata"
          ? await api.validateMetadataSource(id)
          : kind === "logs"
            ? await api.validateLogSource(id)
            : await api.validateCodeArtifact(id);
      await refreshEnvironment(route.environmentId, "sources");
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
    setError(null);
    try {
      const result =
        kind === "metadata"
          ? await api.refreshMetadataSource(id)
          : kind === "logs"
            ? await api.refreshLogSource(id)
            : await api.refreshCodeArtifact(id);
      setSourceSyncStatuses((current) => ({ ...current, [sourceKey(kind, id)]: result }));
      await loadEnvironmentFreshness();
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

  async function validateMetadataEditorDocument(document: MetadataEditorDocument) {
    setBusy(true);
    setError(null);
    try {
      if (!route.environmentId) return document;
      const validation = await api.validateEnvironmentMetadataEditorDocument(route.environmentId, document);
      return { ...document, issues: validation.issues };
    } catch (err) {
      setError(toErrorMessage(err));
      return document;
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
      setMetadataEditorDraft(nextDocument);
      return nextDocument;
    } catch (err) {
      setError(toErrorMessage(err));
      return document;
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
      setMetadataEditorDraft(null);
      setMetadataEditorDocument(await api.getEnvironmentMetadataEditorDocument(route.environmentId));
    } catch (err) {
      setError(toErrorMessage(err));
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
      setMetadataEditorDocument(savedDocument);
      setMetadataEditorDraft(null);
      if (route.environmentId) {
        setMetadataSources(await api.listMetadataSources(route.environmentId));
        await loadProjectSummaries();
        setMetadata(await api.getMetadata(route.environmentId));
        await loadEnvironmentFreshness(route.environmentId);
      }
      return savedDocument;
    } catch (err) {
      setError(toErrorMessage(err));
      return document;
    } finally {
      setBusy(false);
    }
  }

  async function listMetadataBackups(_sourceId: number) {
    if (!route.environmentId) return [];
    return api.listEnvironmentMetadataBackups(route.environmentId);
  }

  async function previewMetadataBackup(backupId: number) {
    return api.getMetadataBackupDocument(backupId);
  }

  async function restoreMetadataBackup(backup: MetadataBackup, document: MetadataEditorDocument) {
    setBusy(true);
    setError(null);
    try {
      await api.restoreMetadataBackup(backup.id, sourceRevisionForBackup(document, backup));
      const nextDocument = route.environmentId
        ? await api.getEnvironmentMetadataEditorDocument(route.environmentId)
        : document;
      setMetadataEditorDocument(nextDocument);
      setMetadataEditorDraft(null);
      if (route.environmentId) {
        setMetadata(await api.getMetadata(route.environmentId));
        await loadEnvironmentFreshness(route.environmentId);
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

  // Reload summaries when viewing project-scoped overviews.
  useEffect(() => {
    if (route.module === "projects" || route.module === "project-overview" || route.module === "environments") {
      void loadProjectSummaries();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [route.module]);

  // Load environments when the selected project changes.
  useEffect(() => {
    if (route.projectId) {
      void loadEnvironments(route.projectId);
    } else {
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

  return {
    projects,
    projectSummaries,
    environments,
    selectedProject,
    selectedProjectSummary,
    metadataSources,
    logPaths,
    codeArtifacts,
    environmentFreshness,
    sourceSyncStatuses,
    metadata,
    metadataEditorDocument,
    metadataEditorDraft,
    lineage,
    assets,
    monitoringReport,
    latestStatus,
    loading,
    busy,
    error,
    refreshCurrentEnvironment: () => refreshEnvironment(route.environmentId, route.module),
    createProject,
    createEnvironment,
    deleteEnvironment,
    addMetadataSource,
    importMetadataSources,
    importDatacoolieProjectSources,
    addLogPath,
    addCodeArtifact,
    updateSource,
    deleteSource,
    deleteSources,
    validateSource,
    syncSource,
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

function metadataDeleteImpactMessage(impact: Awaited<ReturnType<typeof api.getMetadataSourceDeleteImpact>>) {
  const lines = impact.impacts.map((item) => `- ${item.count} ${item.label}`);
  return [
    "Remove this metadata source?",
    "",
    "This will also remove:",
    ...lines,
    "",
    "The metadata file itself will not be deleted:",
    impact.source_uri
  ].join("\n");
}

function metadataBulkDeleteImpactMessage(impacts: Awaited<ReturnType<typeof api.getMetadataSourceDeleteImpact>>[]) {
  const impacted = impacts.filter((impact) => impact.has_impact);
  const counts = new Map<string, number>();
  for (const impact of impacted) {
    for (const item of impact.impacts) {
      counts.set(item.label, (counts.get(item.label) ?? 0) + item.count);
    }
  }
  const lines = Array.from(counts.entries()).map(([label, count]) => `- ${count} ${label}`);
  return [
    `Remove ${impacts.length} metadata source${impacts.length === 1 ? "" : "s"}?`,
    "",
    ...(lines.length ? ["This will also remove related Studio data:", ...lines, ""] : ["No related Studio data was found.", ""]),
    "Metadata files themselves will not be deleted."
  ].join("\n");
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
