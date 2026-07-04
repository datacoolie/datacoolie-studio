import type {
  Environment,
  EnvironmentFreshness,
  JobRecord,
  AssetDetailResponse,
  AssetsResponse,
  LatestStatusResponse,
  LineageResponse,
  MetadataBackup,
  MetadataEditorDocument,
  MetadataResponse,
  MonitoringFilterOptionsResponse,
  MonitoringRecord,
  MonitoringRecordsResponse,
  MonitoringReport,
  Project,
  ProjectSummary,
  SourceReadCheckResult,
  SourceDeleteImpact,
  SourceImportResponse,
  SourcePath,
  SourceSyncStatus,
  StudioSettings,
  ModuleInfo,
  SystemLogResponse,
} from "./types";

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "";
const API_PREFIX = "/api/v1";
const inFlightGetRequests = new Map<string, Promise<unknown>>();

async function fetchRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    ...init
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `Request failed: ${response.status}`);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return response.json() as Promise<T>;
}

function request<T>(path: string, init?: RequestInit): Promise<T> {
  const method = String(init?.method ?? "GET").toUpperCase();
  if (method !== "GET") {
    return fetchRequest<T>(path, init);
  }
  const existing = inFlightGetRequests.get(path);
  if (existing) return existing as Promise<T>;
  const pending = fetchRequest<T>(path, init).finally(() => {
    inFlightGetRequests.delete(path);
  });
  inFlightGetRequests.set(path, pending);
  return pending;
}

function queryString(params: Record<string, string | number | undefined>) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") {
      search.set(key, String(value));
    }
  }
  const value = search.toString();
  return value ? `?${value}` : "";
}

export const api = {
  getStudioSettings: () => request<StudioSettings>(`${API_PREFIX}/studio/settings`),
  updateStudioSettings: (payload: { timezone: string | null }) =>
    request<StudioSettings>(`${API_PREFIX}/studio/settings`, {
      method: "PATCH",
      body: JSON.stringify(payload)
    }),
  listModules: () => request<ModuleInfo[]>(`${API_PREFIX}/studio/modules`),
  setModuleEnabled: (key: string, enabled: boolean) =>
    request<ModuleInfo>(`${API_PREFIX}/studio/modules/${key}`, {
      method: "PATCH",
      body: JSON.stringify({ enabled })
    }),
  listProjects: () => request<Project[]>(`${API_PREFIX}/projects`),
  listProjectSummaries: () => request<ProjectSummary[]>(`${API_PREFIX}/projects/summary`),
  createProject: (payload: { name: string; description?: string }) =>
    request<Project>(`${API_PREFIX}/projects`, { method: "POST", body: JSON.stringify(payload) }),
  listEnvironments: (projectId: number) => request<Environment[]>(`${API_PREFIX}/projects/${projectId}/environments`),
  createEnvironment: (projectId: number, payload: { name: string }) =>
    request<Environment>(`${API_PREFIX}/projects/${projectId}/environments`, { method: "POST", body: JSON.stringify(payload) }),
  deleteEnvironment: (environmentId: number) =>
    request<void>(`${API_PREFIX}/environments/${environmentId}`, { method: "DELETE" }),
  getEnvironmentFreshness: (environmentId: number) =>
    request<EnvironmentFreshness>(`${API_PREFIX}/environments/${environmentId}/freshness`),
  listMetadataSources: (environmentId: number) =>
    request<SourcePath[]>(`${API_PREFIX}/environments/${environmentId}/metadata-sources`),
  addMetadataSource: (environmentId: number, payload: { uri: string; label?: string; enabled?: boolean }) =>
    request<SourcePath>(`${API_PREFIX}/environments/${environmentId}/metadata-sources`, {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  importMetadataSources: (environmentId: number, payload: { uri: string; label?: string; enabled?: boolean }) =>
    request<SourceImportResponse>(`${API_PREFIX}/environments/${environmentId}/metadata-sources/import`, {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  importDatacoolieProjectSources: (
    environmentId: number,
    payload: {
      project_uri: string;
      metadata_subpath?: string;
      code_subpath?: string;
      metadata_uri?: string | null;
      code_uri?: string | null;
      include_metadata?: boolean;
      include_code?: boolean;
      enabled?: boolean;
    }
  ) =>
    request<SourceImportResponse>(`${API_PREFIX}/environments/${environmentId}/datacoolie-project-sources`, {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  updateMetadataSource: (sourceId: number, payload: { uri?: string; label?: string | null; enabled?: boolean; sync_schedule_enabled?: boolean; sync_interval_minutes?: number | null }) =>
    request<SourcePath>(`${API_PREFIX}/metadata-sources/${sourceId}`, {
      method: "PATCH",
      body: JSON.stringify(payload)
    }),
  deleteMetadataSource: (sourceId: number) =>
    request<void>(`${API_PREFIX}/metadata-sources/${sourceId}`, {
      method: "DELETE"
    }),
  getMetadataSourceDeleteImpact: (sourceId: number) =>
    request<SourceDeleteImpact>(`${API_PREFIX}/metadata-sources/${sourceId}/delete-impact`),
  validateMetadataSource: (sourceId: number) =>
    request<SourceReadCheckResult>(`${API_PREFIX}/metadata-sources/${sourceId}/validate`, {
      method: "POST"
    }),
  getMetadataSourceSyncStatus: (sourceId: number) =>
    request<SourceSyncStatus>(`${API_PREFIX}/metadata-sources/${sourceId}/sync-status`),
  refreshMetadataSource: (sourceId: number) =>
    request<SourceSyncStatus>(`${API_PREFIX}/metadata-sources/${sourceId}/refresh`, {
      method: "POST"
    }),
  listLogSources: (environmentId: number) => request<SourcePath[]>(`${API_PREFIX}/environments/${environmentId}/log-sources`),
  addLogSource: (
    environmentId: number,
    payload: { uri: string; label?: string; enabled?: boolean; source_config?: Record<string, unknown> }
  ) =>
    request<SourcePath>(`${API_PREFIX}/environments/${environmentId}/log-sources`, {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  updateLogSource: (
    pathId: number,
    payload: {
      uri?: string;
      label?: string | null;
      enabled?: boolean;
      source_config?: Record<string, unknown>;
      sync_schedule_enabled?: boolean;
      sync_interval_minutes?: number | null;
    }
  ) =>
    request<SourcePath>(`${API_PREFIX}/log-sources/${pathId}`, {
      method: "PATCH",
      body: JSON.stringify(payload)
    }),
  deleteLogSource: (pathId: number) =>
    request<void>(`${API_PREFIX}/log-sources/${pathId}`, {
      method: "DELETE"
    }),
  validateLogSource: (pathId: number) =>
    request<SourceReadCheckResult>(`${API_PREFIX}/log-sources/${pathId}/validate`, {
      method: "POST"
    }),
  getLogSourceSyncStatus: (pathId: number) =>
    request<SourceSyncStatus>(`${API_PREFIX}/log-sources/${pathId}/sync-status`),
  refreshLogSource: (pathId: number) =>
    request<SourceSyncStatus>(`${API_PREFIX}/log-sources/${pathId}/refresh`, {
      method: "POST"
    }),
  listCodeArtifacts: (environmentId: number) =>
    request<SourcePath[]>(`${API_PREFIX}/environments/${environmentId}/code-artifacts`),
  addCodeArtifact: (
    environmentId: number,
    payload: { uri: string; label?: string; enabled?: boolean; source_config?: Record<string, unknown> }
  ) =>
    request<SourcePath>(`${API_PREFIX}/environments/${environmentId}/code-artifacts`, {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  updateCodeArtifact: (
    sourceId: number,
    payload: {
      uri?: string;
      label?: string | null;
      enabled?: boolean;
      source_config?: Record<string, unknown>;
      sync_schedule_enabled?: boolean;
      sync_interval_minutes?: number | null;
    }
  ) =>
    request<SourcePath>(`${API_PREFIX}/code-artifacts/${sourceId}`, {
      method: "PATCH",
      body: JSON.stringify(payload)
    }),
  deleteCodeArtifact: (sourceId: number) =>
    request<void>(`${API_PREFIX}/code-artifacts/${sourceId}`, { method: "DELETE" }),
  validateCodeArtifact: (sourceId: number) =>
    request<SourceReadCheckResult>(`${API_PREFIX}/code-artifacts/${sourceId}/validate`, {
      method: "POST"
    }),
  getCodeArtifactSyncStatus: (sourceId: number) =>
    request<SourceSyncStatus>(`${API_PREFIX}/code-artifacts/${sourceId}/sync-status`),
  refreshCodeArtifact: (sourceId: number) =>
    request<SourceSyncStatus>(`${API_PREFIX}/code-artifacts/${sourceId}/refresh`, {
      method: "POST"
    }),
  getMetadata: (environmentId: number) => request<MetadataResponse>(`${API_PREFIX}/environments/${environmentId}/metadata`),
  getEnvironmentMetadataEditorDocument: (environmentId: number) =>
    request<MetadataEditorDocument>(`${API_PREFIX}/environments/${environmentId}/metadata-editor-document`),
  validateEnvironmentMetadataEditorDocument: (environmentId: number, payload: MetadataEditorDocument) =>
    request<{ status: string; summary: Record<string, unknown>; issues: MetadataEditorDocument["issues"] }>(
      `${API_PREFIX}/environments/${environmentId}/metadata-editor-document/validate`,
      { method: "POST", body: JSON.stringify(payload) }
    ),
  getEnvironmentMetadataEditorDraft: (environmentId: number) =>
    request<MetadataEditorDocument | null>(`${API_PREFIX}/environments/${environmentId}/metadata-editor-document/draft`),
  saveEnvironmentMetadataEditorDraft: (environmentId: number, payload: MetadataEditorDocument) =>
    request<MetadataEditorDocument>(`${API_PREFIX}/environments/${environmentId}/metadata-editor-document/draft`, {
      method: "PUT",
      body: JSON.stringify(payload)
    }),
  discardEnvironmentMetadataEditorDraft: (environmentId: number) =>
    request<void>(`${API_PREFIX}/environments/${environmentId}/metadata-editor-document/draft`, {
      method: "DELETE"
    }),
  saveEnvironmentMetadataEditorDocument: (environmentId: number, payload: MetadataEditorDocument) =>
    request<MetadataEditorDocument>(`${API_PREFIX}/environments/${environmentId}/metadata-editor-document`, {
      method: "PUT",
      body: JSON.stringify({
        expected_revision: payload.source.revision,
        editor_document: payload,
        confirm_overwrite: true
      })
    }),
  listEnvironmentMetadataBackups: (environmentId: number) =>
    request<MetadataBackup[]>(`${API_PREFIX}/environments/${environmentId}/metadata-backups`),
  deleteEnvironmentMetadataBackups: (environmentId: number) =>
    request<void>(`${API_PREFIX}/environments/${environmentId}/metadata-backups`, {
      method: "DELETE"
    }),
  getMetadataBackupDocument: (backupId: number) =>
    request<MetadataEditorDocument>(`${API_PREFIX}/metadata-backups/${backupId}/editor-document`),
  restoreMetadataBackup: (backupId: number, expectedRevision: Record<string, unknown>) =>
    request<MetadataEditorDocument>(`${API_PREFIX}/metadata-backups/${backupId}/restore`, {
      method: "POST",
      body: JSON.stringify({
        expected_revision: expectedRevision,
        confirm_restore: true
      })
    }),
  deleteMetadataBackup: (backupId: number) =>
    request<void>(`${API_PREFIX}/metadata-backups/${backupId}`, {
      method: "DELETE"
    }),
  getLineage: (environmentId: number) => request<LineageResponse>(`${API_PREFIX}/environments/${environmentId}/lineage`),
  getAssets: (environmentId: number) => request<AssetsResponse>(`${API_PREFIX}/environments/${environmentId}/assets`),
  getAsset: (environmentId: number, assetId: string) =>
    request<AssetDetailResponse>(`${API_PREFIX}/environments/${environmentId}/assets/${encodeURIComponent(assetId)}`),
  getMonitoringReport: (environmentId: number, params: Record<string, string | number | undefined> = {}) =>
    request<MonitoringReport>(`${API_PREFIX}/environments/${environmentId}/monitoring/overview${queryString(params)}`),
  getMonitoringFilterOptions: (environmentId: number) =>
    request<MonitoringFilterOptionsResponse>(`${API_PREFIX}/environments/${environmentId}/monitoring/filter-options`),
  getMonitoringJobs: (environmentId: number, params: Record<string, string | number | undefined>) =>
    request<MonitoringRecordsResponse<JobRecord>>(`${API_PREFIX}/environments/${environmentId}/monitoring/jobs${queryString(params)}`),
  getMonitoringDataflows: (environmentId: number, params: Record<string, string | number | undefined>) =>
    request<MonitoringRecordsResponse<MonitoringRecord>>(`${API_PREFIX}/environments/${environmentId}/monitoring/dataflows${queryString(params)}`),
  getMonitoringSystemLogs: (environmentId: number, params: Record<string, string | number | undefined>) =>
    request<SystemLogResponse>(`${API_PREFIX}/environments/${environmentId}/monitoring/system-logs${queryString(params)}`),
  getLatestStatus: (environmentId: number) =>
    request<LatestStatusResponse>(`${API_PREFIX}/environments/${environmentId}/monitoring/latest-status`)
};
