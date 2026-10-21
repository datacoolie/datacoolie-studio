import type { components } from "./generated/schema";

type Schemas = components["schemas"];

export type DatacoolieProjectSourceImportRequest =
  Schemas["DatacoolieProjectSourceImportRequest"];
export type DatacoolieProjectSourceImportInput = Omit<
  DatacoolieProjectSourceImportRequest,
  "enabled" | "code_discovery_mode"
> &
  Partial<Pick<DatacoolieProjectSourceImportRequest, "enabled" | "code_discovery_mode">>;
export type MetadataSourceImportRequest = Schemas["MetadataSourceImportRequest"];
export type MetadataSourceImportInput = Omit<MetadataSourceImportRequest, "enabled"> &
  Partial<Pick<MetadataSourceImportRequest, "enabled">>;
export type SourceCreate = Schemas["SourceCreate"];
export type SourceCreateInput = Omit<SourceCreate, "enabled"> &
  Partial<Pick<SourceCreate, "enabled">>;
export type SourceUpdate = Schemas["SourceUpdate"];

export type SourceRead =
  | Schemas["MetadataSourceRead"]
  | Schemas["SqlSourceRead"]
  | Schemas["LogSourceRead"]
  | Schemas["CodeArtifactRead"];
export type MetadataSourceReadTransport = Schemas["MetadataSourceRead"];
export type SourcesWorkspaceResponse = Schemas["SourcesWorkspaceResponse"];

export type Environment = Schemas["EnvironmentRead"];
export type EnvironmentContext = Schemas["EnvironmentContextResponse"];
export type EnvironmentOverviewResponseTransport = Schemas["EnvironmentOverviewResponse"];
export type AssetDetailResponseTransport = Schemas["AssetDetailResponse"];
export type AssetInventoryResponseTransport = Schemas["AssetInventoryResponse"];
export type AssetReferenceDetailResponseTransport = Schemas["AssetReferenceDetailResponse"];
export type AssetReferenceListResponseTransport = Schemas["AssetReferenceListResponse"];
export type AssetSourceResponseTransport = Schemas["AssetSourceResponse"];
export type ReferenceOccurrenceSourceResponseTransport = Schemas["ReferenceOccurrenceSourceResponse"];
export type LineageResponseTransport = Schemas["LineageResponse"];
export type MetadataBackupResponseTransport = Schemas["MetadataBackupResponse"];
export type MetadataEditorDocumentResponseTransport = Schemas["MetadataEditorDocumentResponse"];
export type MetadataEditorWorkspaceResponseTransport = Schemas["MetadataEditorWorkspaceResponse"];
export type MetadataWriteOperationResponseTransport = Schemas["MetadataWriteOperationResponse"];
export type MetadataResponseTransport = Schemas["MetadataResponse"];
export type MetadataWriteItemResponseTransport = Schemas["MetadataWriteItemResponse"];
export type WriteSafety = NonNullable<MetadataWriteItemResponseTransport["write_safety"]>;
export type SqlReferenceResponse = Schemas["SqlReferenceResponse"];
export type ProjectReferenceMapping = Schemas["ProjectReferenceMappingRead"];
export type ProjectReferenceRegistryResponseTransport = Schemas["ProjectReferenceRegistryResponse"];
export type Project = Schemas["ProjectRead"];
export type ProjectEnvironmentSummary = Schemas["ProjectEnvironmentSummaryResponse"];
export type ProjectSummary = Schemas["ProjectSummaryResponse"];
export type SourceValidationResult = Schemas["SourceValidationResponse"];
export type SourceReadCheckResult = Schemas["SourceValidationResponse"];
export type SourceDeleteImpactItem = Schemas["SourceDeleteImpactItem"];
export type SourceDeleteImpact = Schemas["SourceDeleteImpactResponse"];
export type SourceImportResponse = Schemas["SourceImportResponse"];
export type LogSyncRequest = Schemas["LogSyncRequest"];
export type LocalSourceObservation = Schemas["LocalSourceObservationResponse"];
export type SourceSyncStatus = Schemas["SourceSyncStatusResponse"];
export type StudioDiagnosticsResponse = Schemas["StudioDiagnosticsResponse"];
export type StudioPathInfo = Schemas["StudioPathInfo"];
export type StudioCacheMutationResponse = Schemas["StudioCacheMutationResponse"];
export type StudioCacheStatusResponse = Schemas["StudioCacheStatusResponse"];
export type AnalyticsUpgradeStatusResponse = Schemas["AnalyticsUpgradeStatusResponse"];
export type StudioSettings = Schemas["StudioSettingsResponse"];
export type ModuleInfo = Schemas["ModuleInfo"];
export type MonitoringPageResponseTransport = Schemas["MonitoringPageResponse"];
export type MonitoringJobRecordTransport = Schemas["MonitoringJobRecordResponse"];
export type MonitoringDataflowRecordTransport = Schemas["MonitoringDataflowRecordResponse"];
export type MonitoringNamedCountTransport = Schemas["MonitoringNamedCountResponse"];
export type MonitoringDataflowRecordsResponseTransport = Schemas["MonitoringDataflowRecordsResponse"];
export type MonitoringEvidenceResponseTransport = Schemas["MonitoringEvidenceResponse"];
export type MonitoringDiagnosticsEvidenceResponseTransport = Schemas["MonitoringDiagnosticsEvidenceResponse"];
export type MonitoringDataflowRegistryResponseTransport = Schemas["MonitoringDataflowRegistryResponse"];
export type MonitoringRegistryRowTransport = Schemas["MonitoringRegistryRowResponse"];
export type MonitoringRegistryRunReferenceTransport = Schemas["MonitoringRegistryRunReferenceResponse"];
export type MonitoringRegistryProfileSnapshotTransport = Schemas["MonitoringRegistryProfileSnapshotResponse"];
export type MonitoringFilterOptionsResponseTransport = Schemas["MonitoringFilterOptionsResponse"];
export type MonitoringJobRecordsResponseTransport = Schemas["MonitoringJobRecordsResponse"];
export type MonitoringLatestStatusResponseTransport = Schemas["MonitoringLatestStatusResponse"];
export type MonitoringSystemLogsResponseTransport = Schemas["MonitoringSystemLogsResponse"];

export type ReferenceType = Schemas["ProjectReferenceMappingCreate"]["reference_type"];
export type TargetIdentifierKind =
  Schemas["ProjectReferenceMappingCreate"]["target_identifier_kind"];
export type StudioCacheScope = Schemas["StudioCacheClearRequest"]["scope"];
export type StudioCacheFeature =
  NonNullable<Schemas["StudioCacheClearRequest"]["features"]>[number];
