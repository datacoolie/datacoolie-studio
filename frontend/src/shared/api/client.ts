import type {
  CredentialCapabilities,
  CredentialProfile,
  CredentialProfileDetail,
  MetadataEditorDocument,
  SourcePath,
  StorageBinding,
  StorageConnectionValidation,
} from "./domainTypes";
import type {
  AssetDetailResponseTransport,
  AssetInventoryResponseTransport,
  AssetReferenceDetailResponseTransport,
  AssetReferenceListResponseTransport,
  AssetSourceResponseTransport,
  DatacoolieProjectSourceImportRequest,
  MetadataSourceImportInput,
  Environment,
  EnvironmentContext,
  EnvironmentOverviewResponseTransport,
  LineageResponseTransport,
  LocalSourceObservation,
  LogSyncRequest,
  MetadataBackupResponseTransport,
  MetadataEditorDocumentResponseTransport,
  MetadataEditorWorkspaceResponseTransport,
  MetadataWriteOperationResponseTransport,
  MetadataResponseTransport,
  ModuleInfo,
  MonitoringDataflowRecordsResponseTransport,
  MonitoringDataflowRegistryResponseTransport,
  MonitoringDiagnosticsEvidenceResponseTransport,
  MonitoringEvidenceResponseTransport,
  MonitoringFilterOptionsResponseTransport,
  MonitoringJobRecordsResponseTransport,
  MonitoringLatestStatusResponseTransport,
  MonitoringPageResponseTransport,
  MonitoringSystemLogsResponseTransport,
  Project,
  ProjectReferenceMapping,
  ProjectReferenceRegistryResponseTransport,
  ProjectSummary,
  ReferenceOccurrenceSourceResponseTransport,
  ReferenceType,
  SourceDeleteImpact,
  SourceCreateInput,
  SourceImportResponse,
  SourceRead,
  SourceReadCheckResult,
  SourceSyncStatus,
  SourceUpdate,
  SourcesWorkspaceResponse,
  StudioCacheFeature,
  AnalyticsUpgradeStatusResponse,
  StudioCacheMutationResponse,
  StudioCacheScope,
  StudioCacheStatusResponse,
  StudioDiagnosticsResponse,
  StudioPathInfo,
  StudioSettings,
  SqlReferenceResponse,
  TargetIdentifierKind,
} from "./contractTypes";
import {
  toSourcePath,
  toSourcePaths,
  toSourcesWorkspace,
} from "./sourceAdapters";
import { toStudioCacheMutation, toStudioCacheStatus, toAnalyticsUpgradeStatus } from "./studioCacheAdapters";
import { toStudioDiagnostics } from "./studioDiagnosticsAdapters";
import { toLineageResponse } from "./lineageAdapters";
import { toEnvironmentOverview } from "./environmentAdapters";
import {
  toAssetDetailResponse,
  toAssetInventoryResponse,
  toAssetSourceResponse,
} from "./assetAdapters";
import {
  toAssetReferenceDetailResponse,
  toAssetReferenceListResponse,
  toReferenceOccurrenceSourceResponse,
} from "./assetReferenceAdapters";
import {
  toMetadataBackups,
  toMetadataEditorDocument,
  toMetadataEditorWorkspace,
  toMetadataResponse,
  toMetadataWriteOperation,
} from "./metadataAdapters";
import { toProjectReferenceRegistryResponse } from "./projectReferenceAdapters";
import {
  toLatestStatus,
  toMonitoringDataflowRecords,
  toMonitoringDiagnosticsEvidence,
  toMonitoringEvidence,
  toMonitoringDataflowRegistry,
  toMonitoringFilterOptions,
  toMonitoringJobRecords,
  toMonitoringSystemLogs,
} from "./monitoringAdapters";
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
  if (method !== "GET" || init?.signal) {
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

const MONITORING_PRESERVE_EMPTY_QUERY_KEYS = ["log_session_id"] as const;

function queryString(
  params: Record<string, string | number | undefined>,
  preserveEmptyKeys: readonly string[] = [],
) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    const preserveEmpty = value === ""
      && preserveEmptyKeys.includes(key)
      && params.log_session_scope === "exact";
    if (value !== undefined && (value !== "" || preserveEmpty)) {
      search.set(key, String(value));
    }
  }
  const value = search.toString();
  return value ? `?${value}` : "";
}

function environmentSourcePath(
  environmentId: number,
  collection: "metadata-sources" | "sql-sources" | "log-sources" | "code-artifacts",
  sourceId: number
) {
  return `${API_PREFIX}/environments/${environmentId}/${collection}/${sourceId}`;
}

async function sourceReadRequest(path: string, init?: RequestInit): Promise<SourcePath> {
  return toSourcePath(await request<SourceRead>(path, init));
}

async function sourceListRequest(path: string, init?: RequestInit): Promise<SourcePath[]> {
  return toSourcePaths(await request<SourceRead[]>(path, init));
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
  getStudioDiagnostics: async () =>
    toStudioDiagnostics(
      await request<StudioDiagnosticsResponse>(`${API_PREFIX}/studio/diagnostics`),
    ),
  compactWorkspaceDatabase: () => request<StudioPathInfo>(`${API_PREFIX}/studio/workspace-database/compact`, {
    method: "POST",
    body: JSON.stringify({ confirm: true }),
  }),
  getStudioCache: async () =>
    toStudioCacheStatus(
      await request<StudioCacheStatusResponse>(`${API_PREFIX}/studio/cache`),
    ),
  retryAnalyticsUpgrade: async () =>
    toAnalyticsUpgradeStatus(
      await request<AnalyticsUpgradeStatusResponse>(
        `${API_PREFIX}/studio/cache/analytics-upgrade/retry`,
        { method: "POST", body: JSON.stringify({}) },
      ),
    ),
  clearStudioCache: async (payload: {
    scope: StudioCacheScope;
    environment_id?: number;
    features?: StudioCacheFeature[];
  }) =>
    toStudioCacheMutation(
      await request<StudioCacheMutationResponse>(`${API_PREFIX}/studio/cache/clear`, {
        method: "POST",
        body: JSON.stringify({ ...payload, confirm: true }),
      }),
    ),
  compactStudioCache: async () =>
    toStudioCacheMutation(
      await request<StudioCacheMutationResponse>(`${API_PREFIX}/studio/cache/compact`, {
        method: "POST",
        body: JSON.stringify({ confirm: true }),
      }),
    ),
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
  renameProject: (projectId: number, payload: { name: string }) =>
    request<Project>(`${API_PREFIX}/projects/${projectId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  deleteProject: (projectId: number) =>
    request<void>(`${API_PREFIX}/projects/${projectId}`, { method: "DELETE" }),
  getProjectReferenceRegistry: async (projectId: number, signal?: AbortSignal) =>
    toProjectReferenceRegistryResponse(
      await request<ProjectReferenceRegistryResponseTransport>(
        `${API_PREFIX}/projects/${projectId}/reference-registry`,
        { signal },
      ),
    ),
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
  renameEnvironment: (environmentId: number, payload: { name: string }) =>
    request<Environment>(`${API_PREFIX}/environments/${environmentId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  deleteEnvironment: (environmentId: number) =>
    request<void>(`${API_PREFIX}/environments/${environmentId}`, { method: "DELETE" }),
  observeLocalSources: (environmentId: number) =>
    request<LocalSourceObservation>(
      `${API_PREFIX}/environments/${environmentId}/sources/observe-local`,
      { method: "POST" },
    ),
  getSourcesWorkspace: async (
    environmentId: number,
    signal?: AbortSignal,
    view: "full" | "logs-summary" = "full",
  ) =>
    toSourcesWorkspace(
      await request<SourcesWorkspaceResponse>(
        `${API_PREFIX}/environments/${environmentId}/sources/workspace${view === "logs-summary" ? "?view=logs-summary" : ""}`,
        { signal },
      ),
    ),
  getEnvironmentContext: (environmentId: number) =>
    request<EnvironmentContext>(`${API_PREFIX}/environments/${environmentId}/context`),
  listMetadataSources: (environmentId: number) =>
    sourceListRequest(`${API_PREFIX}/environments/${environmentId}/metadata-sources`),
  addMetadataSource: (environmentId: number, payload: SourceCreateInput) =>
    sourceReadRequest(`${API_PREFIX}/environments/${environmentId}/metadata-sources`, {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  importMetadataSources: (environmentId: number, payload: MetadataSourceImportInput) =>
    request<SourceImportResponse>(`${API_PREFIX}/environments/${environmentId}/metadata-sources/import`, {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  listSqlSources: (environmentId: number) =>
    sourceListRequest(`${API_PREFIX}/environments/${environmentId}/sql-sources`),
  addSqlSource: (
    environmentId: number,
    payload: SourceCreateInput
  ) =>
    sourceReadRequest(`${API_PREFIX}/environments/${environmentId}/sql-sources`, {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  updateSqlSource: (
    environmentId: number,
    sourceId: number,
    payload: SourceUpdate
  ) =>
    sourceReadRequest(environmentSourcePath(environmentId, "sql-sources", sourceId), {
      method: "PATCH",
      body: JSON.stringify(payload)
    }),
  deleteSqlSource: (environmentId: number, sourceId: number) =>
    request<void>(environmentSourcePath(environmentId, "sql-sources", sourceId), {
      method: "DELETE"
    }),
  getSqlSourceDeleteImpact: (environmentId: number, sourceId: number) =>
    request<SourceDeleteImpact>(`${environmentSourcePath(environmentId, "sql-sources", sourceId)}/delete-impact`),
  validateSqlSource: (environmentId: number, sourceId: number) =>
    request<SourceReadCheckResult>(`${environmentSourcePath(environmentId, "sql-sources", sourceId)}/validate`, {
      method: "POST"
    }),
  refreshSqlSource: (environmentId: number, sourceId: number) =>
    request<SourceSyncStatus>(`${environmentSourcePath(environmentId, "sql-sources", sourceId)}/refresh`, {
      method: "POST"
    }),
  retrySqlSourceObservation: (environmentId: number, sourceId: number) =>
    request<SourceSyncStatus>(`${environmentSourcePath(environmentId, "sql-sources", sourceId)}/retry-observation`, {
      method: "POST"
    }),
  importDatacoolieProjectSources: (
    environmentId: number,
    payload: DatacoolieProjectSourceImportRequest
  ) =>
    request<SourceImportResponse>(`${API_PREFIX}/environments/${environmentId}/datacoolie-project-sources`, {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  updateMetadataSource: (environmentId: number, sourceId: number, payload: SourceUpdate) =>
    sourceReadRequest(environmentSourcePath(environmentId, "metadata-sources", sourceId), {
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
  retryMetadataSourceObservation: (environmentId: number, sourceId: number) =>
    request<SourceSyncStatus>(`${environmentSourcePath(environmentId, "metadata-sources", sourceId)}/retry-observation`, {
      method: "POST"
    }),
  listLogSources: (environmentId: number) => sourceListRequest(`${API_PREFIX}/environments/${environmentId}/log-sources`),
  addLogSource: (
    environmentId: number,
    payload: SourceCreateInput
  ) =>
    sourceReadRequest(`${API_PREFIX}/environments/${environmentId}/log-sources`, {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  updateLogSource: (
    environmentId: number,
    pathId: number,
    payload: SourceUpdate
  ) =>
    sourceReadRequest(environmentSourcePath(environmentId, "log-sources", pathId), {
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
  retryLogSourceObservation: (environmentId: number, pathId: number) =>
    request<SourceSyncStatus>(`${environmentSourcePath(environmentId, "log-sources", pathId)}/retry-observation`, {
      method: "POST"
    }),
  listCodeArtifacts: (environmentId: number) =>
    sourceListRequest(`${API_PREFIX}/environments/${environmentId}/code-artifacts`),
  addCodeArtifact: (
    environmentId: number,
    payload: SourceCreateInput
  ) =>
    sourceReadRequest(`${API_PREFIX}/environments/${environmentId}/code-artifacts`, {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  updateCodeArtifact: (
    environmentId: number,
    sourceId: number,
    payload: SourceUpdate
  ) =>
    sourceReadRequest(environmentSourcePath(environmentId, "code-artifacts", sourceId), {
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
  retryCodeArtifactObservation: (environmentId: number, sourceId: number) =>
    request<SourceSyncStatus>(`${environmentSourcePath(environmentId, "code-artifacts", sourceId)}/retry-observation`, {
      method: "POST"
    }),
  getMetadata: async (environmentId: number) =>
    toMetadataResponse(
      await request<MetadataResponseTransport>(`${API_PREFIX}/environments/${environmentId}/metadata`),
    ),
  getEnvironmentMetadataEditorWorkspace: async (environmentId: number, signal?: AbortSignal) =>
    toMetadataEditorWorkspace(
      await request<MetadataEditorWorkspaceResponseTransport>(
        `${API_PREFIX}/environments/${environmentId}/metadata-editor-workspace`,
        { signal },
      ),
    ),
  saveEnvironmentMetadataEditorDraft: async (environmentId: number, payload: MetadataEditorDocument) =>
    toMetadataEditorDocument(
      await request<MetadataEditorDocumentResponseTransport>(
        `${API_PREFIX}/environments/${environmentId}/metadata-editor-document/draft`,
        {
          method: "PUT",
          body: JSON.stringify(payload),
        },
      ),
    ),
  readSqlReference: (environmentId: number, sourceId: number, query: string) =>
    request<SqlReferenceResponse>(`${API_PREFIX}/environments/${environmentId}/sql-reference`, {
      method: "POST",
      body: JSON.stringify({ source_id: sourceId, query })
    }),
  discardEnvironmentMetadataEditorDraft: (environmentId: number) =>
    request<void>(`${API_PREFIX}/environments/${environmentId}/metadata-editor-document/draft`, {
      method: "DELETE"
    }),
  prepareMetadataEditorWrite: async (environmentId: number, payload: MetadataEditorDocument) =>
    toMetadataWriteOperation(
      await request<MetadataWriteOperationResponseTransport>(
        `${API_PREFIX}/environments/${environmentId}/metadata-write-operations/prepare`,
        {
          method: "POST",
          body: JSON.stringify({
            expected_revision: payload.source.revision,
            editor_document: payload,
          }),
        },
      ),
    ),
  getMetadataEditorWrite: async (environmentId: number, operationKey: string) =>
    toMetadataWriteOperation(
      await request<MetadataWriteOperationResponseTransport>(
        `${API_PREFIX}/environments/${environmentId}/metadata-write-operations/${operationKey}`,
      ),
    ),
  applyMetadataEditorWriteOperation: async (environmentId: number, operationKey: string) =>
    toMetadataWriteOperation(
      await request<MetadataWriteOperationResponseTransport>(
        `${API_PREFIX}/environments/${environmentId}/metadata-write-operations/${operationKey}/apply`,
        {
          method: "POST",
          body: JSON.stringify({ confirm_apply: true }),
        },
      ),
    ),
  retryMetadataEditorWrite: async (environmentId: number, operationKey: string) =>
    toMetadataWriteOperation(
      await request<MetadataWriteOperationResponseTransport>(
        `${API_PREFIX}/environments/${environmentId}/metadata-write-operations/${operationKey}/retry`,
        { method: "POST", body: JSON.stringify({ confirm_apply: true }) },
      ),
    ),
  abandonMetadataEditorWrite: async (environmentId: number, operationKey: string) =>
    toMetadataWriteOperation(
      await request<MetadataWriteOperationResponseTransport>(
        `${API_PREFIX}/environments/${environmentId}/metadata-write-operations/${operationKey}/abandon`,
        { method: "POST", body: JSON.stringify({ confirm_abandon: true }) },
      ),
    ),
  listEnvironmentMetadataBackups: async (environmentId: number, signal?: AbortSignal) =>
    toMetadataBackups(
      await request<MetadataBackupResponseTransport[]>(
        `${API_PREFIX}/environments/${environmentId}/metadata-backups`,
        { signal },
      ),
    ),
  deleteEnvironmentMetadataBackups: (environmentId: number) =>
    request<void>(`${API_PREFIX}/environments/${environmentId}/metadata-backups`, {
      method: "DELETE"
    }),
  getMetadataBackupDocument: async (environmentId: number, backupId: number, signal?: AbortSignal) =>
    toMetadataEditorDocument(
      await request<MetadataEditorDocumentResponseTransport>(
        `${API_PREFIX}/environments/${environmentId}/metadata-backups/${backupId}/editor-document`,
        { signal },
      ),
    ),
  restoreMetadataBackup: async (environmentId: number, backupId: number, expectedRevision: Record<string, unknown>) =>
    toMetadataEditorWorkspace(
      await request<MetadataEditorWorkspaceResponseTransport>(
        `${API_PREFIX}/environments/${environmentId}/metadata-backups/${backupId}/restore`,
        {
          method: "POST",
          body: JSON.stringify({
            expected_revision: expectedRevision,
            confirm_restore: true,
          }),
        },
      ),
    ),
  deleteMetadataBackup: (environmentId: number, backupId: number) =>
    request<void>(`${API_PREFIX}/environments/${environmentId}/metadata-backups/${backupId}`, {
      method: "DELETE"
    }),
  getLineage: async (environmentId: number, includeDraft = false, signal?: AbortSignal) =>
    toLineageResponse(
      await request<LineageResponseTransport>(
        `${API_PREFIX}/environments/${environmentId}/lineage${includeDraft ? "?draft=true" : ""}`,
        { signal },
      ),
    ),
  getEnvironmentOverview: async (environmentId: number, signal?: AbortSignal) =>
    toEnvironmentOverview(
      await request<EnvironmentOverviewResponseTransport>(
        `${API_PREFIX}/environments/${environmentId}/overview`,
        { signal },
      ),
    ),
  getAssets: async (environmentId: number, params: Record<string, string | number | undefined> = {}, signal?: AbortSignal) =>
    toAssetInventoryResponse(
      await request<AssetInventoryResponseTransport>(
        `${API_PREFIX}/environments/${environmentId}/assets${queryString(params)}`,
        { signal },
      ),
    ),
  getAssetReferences: async (environmentId: number, params: Record<string, string | number | undefined> = {}, signal?: AbortSignal) =>
    toAssetReferenceListResponse(
      await request<AssetReferenceListResponseTransport>(
        `${API_PREFIX}/environments/${environmentId}/asset-references${queryString(params)}`,
        { signal },
      ),
    ),
  getAssetReference: async (environmentId: number, referenceId: string, signal?: AbortSignal) =>
    toAssetReferenceDetailResponse(
      await request<AssetReferenceDetailResponseTransport>(
        `${API_PREFIX}/environments/${environmentId}/asset-references/${encodeURIComponent(referenceId)}`,
        { signal },
      ),
    ),
  getAsset: async (environmentId: number, assetId: string, signal?: AbortSignal) =>
    toAssetDetailResponse(
      await request<AssetDetailResponseTransport>(
        `${API_PREFIX}/environments/${environmentId}/assets/${encodeURIComponent(assetId)}`,
        { signal },
      ),
    ),
  getAssetSource: async (environmentId: number, assetId: string, signal?: AbortSignal) =>
    toAssetSourceResponse(
      await request<AssetSourceResponseTransport>(
        `${API_PREFIX}/environments/${environmentId}/assets/${encodeURIComponent(assetId)}/source`,
        { signal },
      ),
    ),
  getReferenceOccurrenceSource: async (environmentId: number, occurrenceId: string, signal?: AbortSignal) =>
    toReferenceOccurrenceSourceResponse(
      await request<ReferenceOccurrenceSourceResponseTransport>(
        `${API_PREFIX}/environments/${environmentId}/reference-occurrences/${encodeURIComponent(occurrenceId)}/source`,
        { signal },
      ),
    ),
  getMonitoringPage: (environmentId: number, page: string, params: Record<string, string | number | undefined> = {}, signal?: AbortSignal) =>
    request<MonitoringPageResponseTransport>(`${API_PREFIX}/environments/${environmentId}/monitoring/pages/${page}${queryString(params, MONITORING_PRESERVE_EMPTY_QUERY_KEYS)}`, { signal }),
  getMonitoringPageEvidence: async (environmentId: number, page: string, params: Record<string, string | number | undefined>, signal?: AbortSignal) =>
    toMonitoringEvidence(
      await request<MonitoringEvidenceResponseTransport>(
        `${API_PREFIX}/environments/${environmentId}/monitoring/pages/${page}/evidence${queryString(params, MONITORING_PRESERVE_EMPTY_QUERY_KEYS)}`,
        { signal },
      ),
    ),
  getMonitoringDiagnosticsEvidence: async (environmentId: number, params: Record<string, string | number | undefined>, signal?: AbortSignal) =>
    toMonitoringDiagnosticsEvidence(
      await request<MonitoringDiagnosticsEvidenceResponseTransport>(
        `${API_PREFIX}/environments/${environmentId}/monitoring/pages/diagnostics/evidence${queryString(params, MONITORING_PRESERVE_EMPTY_QUERY_KEYS)}`,
        { signal },
      ),
    ),
  getMonitoringDataflowRegistry: async (environmentId: number, params: Record<string, string | number | undefined>, signal?: AbortSignal) =>
    toMonitoringDataflowRegistry(
      await request<MonitoringDataflowRegistryResponseTransport>(
        `${API_PREFIX}/environments/${environmentId}/monitoring/dataflow-registry${queryString(params, MONITORING_PRESERVE_EMPTY_QUERY_KEYS)}`,
        { signal },
      ),
    ),
  getMonitoringDataflowRegistryDetail: async (environmentId: number, dataflowId: string, params: Record<string, string | number | undefined> = {}, signal?: AbortSignal) =>
    toMonitoringDataflowRegistry(
      await request<MonitoringDataflowRegistryResponseTransport>(
        `${API_PREFIX}/environments/${environmentId}/monitoring/dataflow-registry/detail${queryString({ ...params, dataflowId }, MONITORING_PRESERVE_EMPTY_QUERY_KEYS)}`,
        { signal },
      ),
    ),
  getMonitoringFilterOptions: async (environmentId: number, params: Record<string, string | number | undefined>, signal?: AbortSignal) =>
    toMonitoringFilterOptions(
      await request<MonitoringFilterOptionsResponseTransport>(
        `${API_PREFIX}/environments/${environmentId}/monitoring/filter-options${queryString(params)}`,
        { signal },
      ),
    ),
  getMonitoringJobs: async (environmentId: number, params: Record<string, string | number | undefined>, signal?: AbortSignal) =>
    toMonitoringJobRecords(
      await request<MonitoringJobRecordsResponseTransport>(
        `${API_PREFIX}/environments/${environmentId}/monitoring/jobs${queryString(params, MONITORING_PRESERVE_EMPTY_QUERY_KEYS)}`,
        { signal },
      ),
    ),
  getMonitoringDataflows: async (environmentId: number, params: Record<string, string | number | undefined>, signal?: AbortSignal) =>
    toMonitoringDataflowRecords(
      await request<MonitoringDataflowRecordsResponseTransport>(
        `${API_PREFIX}/environments/${environmentId}/monitoring/dataflows${queryString(params, MONITORING_PRESERVE_EMPTY_QUERY_KEYS)}`,
        { signal },
      ),
    ),
  getMonitoringSystemLogs: async (environmentId: number, params: Record<string, string | number | undefined>, signal?: AbortSignal) =>
    toMonitoringSystemLogs(
      await request<MonitoringSystemLogsResponseTransport>(
        `${API_PREFIX}/environments/${environmentId}/monitoring/system-logs${queryString(params, MONITORING_PRESERVE_EMPTY_QUERY_KEYS)}`,
        { signal },
      ),
    ),
  monitoringSystemLogDownloadUrl: (environmentId: number, params: Record<string, string | number | undefined>) =>
    `${API_BASE}${API_PREFIX}/environments/${environmentId}/monitoring/system-logs/download${queryString(params, MONITORING_PRESERVE_EMPTY_QUERY_KEYS)}`,
  getLatestStatus: async (environmentId: number, signal?: AbortSignal) =>
    toLatestStatus(
      await request<MonitoringLatestStatusResponseTransport>(
        `${API_PREFIX}/environments/${environmentId}/monitoring/latest-status`,
        { signal },
      ),
    )
};
