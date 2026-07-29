import type {
  CredentialCapabilities,
  CredentialProfile,
  CredentialProfileDetail,
  JobRecord,
  LatestStatusResponse,
  LocalSourceObservation,
  MonitoringFilterOptionsResponse,
  MonitoringRecord,
  MonitoringRecordsResponse,
  MonitoringPageResponse,
  SourcePath,
  SourcesWorkspace,
  StorageBinding,
  StorageConnectionValidation,
  SystemLogResponse,
} from "./domainTypes";
import type {
  AssetDetailResponse,
  AssetInventoryResponse,
  AssetReferenceDetailResponse,
  AssetReferenceListResponse,
  AssetSourceResponse,
  Environment,
  EnvironmentContext,
  EnvironmentOverview,
  LineageResponse,
  LogSyncRequest,
  MetadataBackup,
  MetadataEditorDocument,
  MetadataEditorWorkspace,
  MetadataResponse,
  ModuleInfo,
  Project,
  ProjectReferenceMapping,
  ProjectReferenceRegistryResponse,
  ProjectSummary,
  ReferenceOccurrenceSourceResponse,
  ReferenceType,
  SourceDeleteImpact,
  SourceImportResponse,
  SourceReadCheckResult,
  SourceSyncStatus,
  StudioCacheFeature,
  StudioCacheMutation,
  StudioCacheScope,
  StudioCacheStatus,
  StudioDiagnostics,
  StudioPathInfo,
  StudioSettings,
  TargetIdentifierKind,
} from "./contractTypes";
import { apiRequestError } from "../lib/errors";

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
    throw apiRequestError(detail, response.status);
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

function environmentSourcePath(
  environmentId: number,
  collection: "metadata-sources" | "log-sources" | "code-artifacts",
  sourceId: number
) {
  return `${API_PREFIX}/environments/${environmentId}/${collection}/${sourceId}`;
}

export const api = {
  listCredentialProfiles: () =>
    request<CredentialProfile[]>(`${API_PREFIX}/credential-profiles`),
  getCredentialProfile: (profileId: string) =>
    request<CredentialProfileDetail>(`${API_PREFIX}/credential-profiles/${profileId}`),
  getCredentialCapabilities: () =>
    request<CredentialCapabilities>(`${API_PREFIX}/credential-profiles/capabilities`),
  createCredentialProfile: (payload: {
    name: string;
    provider: Exclude<StorageBinding["provider"], "local">;
    auth_type: string;
    config: Record<string, unknown>;
    secret?: Record<string, unknown>;
  }) =>
    request<CredentialProfile>(`${API_PREFIX}/credential-profiles`, {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  updateCredentialProfile: (
    profileId: string,
    payload: { name?: string; config?: Record<string, unknown>; secret?: Record<string, unknown> }
  ) =>
    request<CredentialProfile>(`${API_PREFIX}/credential-profiles/${profileId}`, {
      method: "PATCH",
      body: JSON.stringify(payload)
    }),
  deleteCredentialProfile: (profileId: string) =>
    request<void>(`${API_PREFIX}/credential-profiles/${profileId}`, { method: "DELETE" }),
  validateStorageConnection: (payload: {
    uri: string;
    storage?: StorageBinding;
    source_config?: Record<string, unknown>;
  }) =>
    request<StorageConnectionValidation>(`${API_PREFIX}/storage-connections/validate`, {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  getStudioSettings: () => request<StudioSettings>(`${API_PREFIX}/studio/settings`),
  getStudioDiagnostics: () => request<StudioDiagnostics>(`${API_PREFIX}/studio/diagnostics`),
  compactWorkspaceDatabase: () => request<StudioPathInfo>(`${API_PREFIX}/studio/workspace-database/compact`, {
    method: "POST",
    body: JSON.stringify({ confirm: true }),
  }),
  getStudioCache: () => request<StudioCacheStatus>(`${API_PREFIX}/studio/cache`),
  clearStudioCache: (payload: {
    scope: StudioCacheScope;
    environment_id?: number;
    features?: StudioCacheFeature[];
  }) => request<StudioCacheMutation>(`${API_PREFIX}/studio/cache/clear`, {
    method: "POST",
    body: JSON.stringify({ ...payload, confirm: true }),
  }),
  compactStudioCache: () => request<StudioCacheMutation>(`${API_PREFIX}/studio/cache/compact`, {
    method: "POST",
    body: JSON.stringify({ confirm: true }),
  }),
  updateStudioSettings: (payload: {
    timezone?: string | null;
    source_check_mode?: "fixed" | "adaptive";
    source_check_interval_seconds?: number;
    source_check_max_interval_seconds?: number;
  }) =>
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
  deleteProject: (projectId: number) =>
    request<void>(`${API_PREFIX}/projects/${projectId}`, { method: "DELETE" }),
  getProjectReferenceRegistry: (projectId: number) =>
    request<ProjectReferenceRegistryResponse>(`${API_PREFIX}/projects/${projectId}/reference-registry`),
  createProjectReferenceMapping: (
    projectId: number,
    payload: {
      reference_type: ReferenceType;
      reference_value: string;
      target_identifier_kind: TargetIdentifierKind;
      target_value: string;
      target_display_value?: string | null;
      note?: string | null;
    }
  ) =>
    request<ProjectReferenceMapping>(`${API_PREFIX}/projects/${projectId}/reference-mappings`, {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  updateProjectReferenceMapping: (
    projectId: number,
    mappingId: number,
    payload: {
      reference_type?: ReferenceType;
      reference_value?: string | null;
      target_identifier_kind?: TargetIdentifierKind;
      target_value?: string | null;
      target_display_value?: string | null;
      note?: string | null;
    }
  ) =>
    request<ProjectReferenceMapping>(
      `${API_PREFIX}/projects/${projectId}/reference-mappings/${mappingId}`,
      { method: "PATCH", body: JSON.stringify(payload) }
    ),
  deleteProjectReferenceMapping: (projectId: number, mappingId: number) =>
    request<void>(`${API_PREFIX}/projects/${projectId}/reference-mappings/${mappingId}`, { method: "DELETE" }),
  listEnvironments: (projectId: number) => request<Environment[]>(`${API_PREFIX}/projects/${projectId}/environments`),
  createEnvironment: (projectId: number, payload: { name: string }) =>
    request<Environment>(`${API_PREFIX}/projects/${projectId}/environments`, { method: "POST", body: JSON.stringify(payload) }),
  deleteEnvironment: (environmentId: number) =>
    request<void>(`${API_PREFIX}/environments/${environmentId}`, { method: "DELETE" }),
  observeLocalSources: (environmentId: number) =>
    request<LocalSourceObservation>(
      `${API_PREFIX}/environments/${environmentId}/sources/observe-local`,
      { method: "POST" },
    ),
  getSourcesWorkspace: (environmentId: number) =>
    request<SourcesWorkspace>(
      `${API_PREFIX}/environments/${environmentId}/sources/workspace`,
    ),
  getEnvironmentContext: (environmentId: number) =>
    request<EnvironmentContext>(`${API_PREFIX}/environments/${environmentId}/context`),
  listMetadataSources: (environmentId: number) =>
    request<SourcePath[]>(`${API_PREFIX}/environments/${environmentId}/metadata-sources`),
  addMetadataSource: (environmentId: number, payload: { uri: string; label?: string; enabled?: boolean; source_config?: Record<string, unknown>; storage?: StorageBinding }) =>
    request<SourcePath>(`${API_PREFIX}/environments/${environmentId}/metadata-sources`, {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  importMetadataSources: (environmentId: number, payload: { uri: string; label?: string; enabled?: boolean; storage?: StorageBinding }) =>
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
      storage?: StorageBinding;
    }
  ) =>
    request<SourceImportResponse>(`${API_PREFIX}/environments/${environmentId}/datacoolie-project-sources`, {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  updateMetadataSource: (environmentId: number, sourceId: number, payload: { uri?: string; label?: string | null; enabled?: boolean; source_config?: Record<string, unknown>; storage?: StorageBinding; sync_schedule_enabled?: boolean; sync_interval_minutes?: number | null }) =>
    request<SourcePath>(environmentSourcePath(environmentId, "metadata-sources", sourceId), {
      method: "PATCH",
      body: JSON.stringify(payload)
    }),
  deleteMetadataSource: (environmentId: number, sourceId: number) =>
    request<void>(environmentSourcePath(environmentId, "metadata-sources", sourceId), {
      method: "DELETE"
    }),
  getMetadataSourceDeleteImpact: (environmentId: number, sourceId: number) =>
    request<SourceDeleteImpact>(`${environmentSourcePath(environmentId, "metadata-sources", sourceId)}/delete-impact`),
  validateMetadataSource: (environmentId: number, sourceId: number) =>
    request<SourceReadCheckResult>(`${environmentSourcePath(environmentId, "metadata-sources", sourceId)}/validate`, {
      method: "POST"
    }),
  refreshMetadataSource: (environmentId: number, sourceId: number) =>
    request<SourceSyncStatus>(`${environmentSourcePath(environmentId, "metadata-sources", sourceId)}/refresh`, {
      method: "POST"
    }),
  listLogSources: (environmentId: number) => request<SourcePath[]>(`${API_PREFIX}/environments/${environmentId}/log-sources`),
  addLogSource: (
    environmentId: number,
    payload: { uri: string; label?: string; enabled?: boolean; source_config?: Record<string, unknown>; storage?: StorageBinding }
  ) =>
    request<SourcePath>(`${API_PREFIX}/environments/${environmentId}/log-sources`, {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  updateLogSource: (
    environmentId: number,
    pathId: number,
    payload: {
      uri?: string;
      label?: string | null;
      enabled?: boolean;
      source_config?: Record<string, unknown>;
      storage?: StorageBinding;
      sync_schedule_enabled?: boolean;
      sync_interval_minutes?: number | null;
    }
  ) =>
    request<SourcePath>(environmentSourcePath(environmentId, "log-sources", pathId), {
      method: "PATCH",
      body: JSON.stringify(payload)
    }),
  deleteLogSource: (environmentId: number, pathId: number) =>
    request<void>(environmentSourcePath(environmentId, "log-sources", pathId), {
      method: "DELETE"
    }),
  getLogSourceDeleteImpact: (environmentId: number, pathId: number) =>
    request<SourceDeleteImpact>(`${environmentSourcePath(environmentId, "log-sources", pathId)}/delete-impact`),
  validateLogSource: (environmentId: number, pathId: number) =>
    request<SourceReadCheckResult>(`${environmentSourcePath(environmentId, "log-sources", pathId)}/validate`, {
      method: "POST"
    }),
  refreshLogSource: (environmentId: number, pathId: number, payload: LogSyncRequest) =>
    request<SourceSyncStatus>(`${environmentSourcePath(environmentId, "log-sources", pathId)}/refresh`, {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  listCodeArtifacts: (environmentId: number) =>
    request<SourcePath[]>(`${API_PREFIX}/environments/${environmentId}/code-artifacts`),
  addCodeArtifact: (
    environmentId: number,
    payload: { uri: string; label?: string; enabled?: boolean; source_config?: Record<string, unknown>; storage?: StorageBinding }
  ) =>
    request<SourcePath>(`${API_PREFIX}/environments/${environmentId}/code-artifacts`, {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  updateCodeArtifact: (
    environmentId: number,
    sourceId: number,
    payload: {
      uri?: string;
      label?: string | null;
      enabled?: boolean;
      source_config?: Record<string, unknown>;
      storage?: StorageBinding;
      sync_schedule_enabled?: boolean;
      sync_interval_minutes?: number | null;
    }
  ) =>
    request<SourcePath>(environmentSourcePath(environmentId, "code-artifacts", sourceId), {
      method: "PATCH",
      body: JSON.stringify(payload)
    }),
  deleteCodeArtifact: (environmentId: number, sourceId: number) =>
    request<void>(environmentSourcePath(environmentId, "code-artifacts", sourceId), { method: "DELETE" }),
  getCodeArtifactDeleteImpact: (environmentId: number, sourceId: number) =>
    request<SourceDeleteImpact>(`${environmentSourcePath(environmentId, "code-artifacts", sourceId)}/delete-impact`),
  validateCodeArtifact: (environmentId: number, sourceId: number) =>
    request<SourceReadCheckResult>(`${environmentSourcePath(environmentId, "code-artifacts", sourceId)}/validate`, {
      method: "POST"
    }),
  refreshCodeArtifact: (environmentId: number, sourceId: number) =>
    request<SourceSyncStatus>(`${environmentSourcePath(environmentId, "code-artifacts", sourceId)}/refresh`, {
      method: "POST"
    }),
  getMetadata: (environmentId: number) => request<MetadataResponse>(`${API_PREFIX}/environments/${environmentId}/metadata`),
  getEnvironmentMetadataEditorWorkspace: (environmentId: number) =>
    request<MetadataEditorWorkspace>(`${API_PREFIX}/environments/${environmentId}/metadata-editor-workspace`),
  validateEnvironmentMetadataEditorDocument: (environmentId: number, payload: MetadataEditorDocument) =>
    request<{ status: string; summary: Record<string, unknown>; issues: MetadataEditorDocument["issues"] }>(
      `${API_PREFIX}/environments/${environmentId}/metadata-editor-document/validate`,
      { method: "POST", body: JSON.stringify(payload) }
    ),
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
    request<MetadataEditorWorkspace>(`${API_PREFIX}/environments/${environmentId}/metadata-editor-document`, {
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
    request<MetadataEditorWorkspace>(`${API_PREFIX}/metadata-backups/${backupId}/restore`, {
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
  getEnvironmentOverview: (environmentId: number) => request<EnvironmentOverview>(`${API_PREFIX}/environments/${environmentId}/overview`),
  getAssets: (environmentId: number, params: Record<string, string | number | undefined> = {}) =>
      request<AssetInventoryResponse>(`${API_PREFIX}/environments/${environmentId}/assets${queryString(params)}`),
  getAssetReferences: (environmentId: number, params: Record<string, string | number | undefined> = {}) =>
      request<AssetReferenceListResponse>(`${API_PREFIX}/environments/${environmentId}/asset-references${queryString(params)}`),
  getAssetReference: (environmentId: number, referenceId: string) =>
    request<AssetReferenceDetailResponse>(
      `${API_PREFIX}/environments/${environmentId}/asset-references/${encodeURIComponent(referenceId)}`,
    ),
    getAsset: (environmentId: number, assetId: string) =>
      request<AssetDetailResponse>(`${API_PREFIX}/environments/${environmentId}/assets/${encodeURIComponent(assetId)}`),
    getAssetSource: (environmentId: number, assetId: string) =>
      request<AssetSourceResponse>(`${API_PREFIX}/environments/${environmentId}/assets/${encodeURIComponent(assetId)}/source`),
  getReferenceOccurrenceSource: (environmentId: number, occurrenceId: string) =>
    request<ReferenceOccurrenceSourceResponse>(
      `${API_PREFIX}/environments/${environmentId}/reference-occurrences/${encodeURIComponent(occurrenceId)}/source`,
    ),
  getMonitoringPage: (environmentId: number, page: string, params: Record<string, string | number | undefined> = {}) =>
    request<MonitoringPageResponse>(`${API_PREFIX}/environments/${environmentId}/monitoring/pages/${page}${queryString(params)}`),
  getMonitoringPageEvidence: (environmentId: number, page: string, params: Record<string, string | number | undefined>) =>
    request<MonitoringRecordsResponse<MonitoringRecord>>(
      `${API_PREFIX}/environments/${environmentId}/monitoring/pages/${page}/evidence${queryString(params)}`
    ),
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
