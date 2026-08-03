import type { components } from "./generated/schema";
import type * as Ui from "./domainTypes";

type Schemas = components["schemas"];

type TransportView<Generated, View> = View & Omit<Partial<Generated>, keyof View>;

export type Environment = TransportView<Schemas["EnvironmentRead"], Ui.Environment>;
export type EnvironmentContext = TransportView<Schemas["EnvironmentContextResponse"], Ui.EnvironmentContext>;
export type EnvironmentOverview = TransportView<Schemas["EnvironmentOverviewResponse"], Ui.EnvironmentOverview>;
export type AssetDetailResponse = TransportView<Schemas["AssetDetailResponse"], Ui.AssetDetailResponse>;
export type AssetInventoryResponse = TransportView<Schemas["AssetInventoryResponse"], Ui.AssetInventoryResponse>;
export type AssetReferenceDetailResponse =
  TransportView<Schemas["AssetReferenceDetailResponse"], Ui.AssetReferenceDetailResponse>;
export type AssetReferenceListResponse =
  TransportView<Schemas["AssetReferenceListResponse"], Ui.AssetReferenceListResponse>;
export type AssetSourceResponse = TransportView<Schemas["AssetSourceResponse"], Ui.AssetSourceResponse>;
export type ReferenceOccurrenceSourceResponse =
  TransportView<Schemas["ReferenceOccurrenceSourceResponse"], Ui.ReferenceOccurrenceSourceResponse>;
export type LineageResponse = TransportView<Schemas["LineageResponse"], Ui.LineageResponse>;
export type MetadataBackup = TransportView<Schemas["MetadataBackupResponse"], Ui.MetadataBackup>;
export type MetadataEditorDocument =
  TransportView<Schemas["MetadataEditorDocumentResponse"], Ui.MetadataEditorDocument>;
export type MetadataEditorWorkspace =
  TransportView<Schemas["MetadataEditorWorkspaceResponse"], Ui.MetadataEditorWorkspace>;
export type MetadataResponse = TransportView<Schemas["MetadataResponse"], Ui.MetadataResponse>;
export type ProjectReferenceMapping =
  TransportView<Schemas["ProjectReferenceMappingRead"], Ui.ProjectReferenceMapping>;
export type ProjectReferenceRegistryResponse =
  TransportView<Schemas["ProjectReferenceRegistryResponse"], Ui.ProjectReferenceRegistryResponse>;
export type Project = TransportView<Schemas["ProjectRead"], Ui.Project>;
export type ProjectSummary = TransportView<Schemas["ProjectSummaryResponse"], Ui.ProjectSummary>;
export type SourceReadCheckResult =
  TransportView<Schemas["SourceValidationResponse"], Ui.SourceReadCheckResult>;
export type SourceDeleteImpact =
  TransportView<Schemas["SourceDeleteImpactResponse"], Ui.SourceDeleteImpact>;
export type SourceImportResponse = TransportView<Schemas["SourceImportResponse"], Ui.SourceImportResponse>;
export type LogSyncRequest = Schemas["LogSyncRequest"];
export type SourceSyncStatus = TransportView<Schemas["SourceSyncStatusResponse"], Ui.SourceSyncStatus>;
export type StudioDiagnostics = TransportView<Schemas["StudioDiagnosticsResponse"], Ui.StudioDiagnostics>;
export type StudioPathInfo = TransportView<Schemas["StudioPathInfo"], Ui.StudioPathInfo>;
export type StudioCacheMutation =
  TransportView<Schemas["StudioCacheMutationResponse"], Ui.StudioCacheMutation>;
export type StudioCacheStatus = TransportView<Schemas["StudioCacheStatusResponse"], Ui.StudioCacheStatus>;
export type AnalyticsUpgradeStatus =
  TransportView<Schemas["AnalyticsUpgradeStatusResponse"], Ui.AnalyticsUpgradeStatus>;
export type StudioSettings = TransportView<Schemas["StudioSettingsResponse"], Ui.StudioSettings>;
export type ModuleInfo = TransportView<Schemas["ModuleInfo"], Ui.ModuleInfo>;

export type ReferenceType = Schemas["ProjectReferenceMappingCreate"]["reference_type"];
export type TargetIdentifierKind =
  Schemas["ProjectReferenceMappingCreate"]["target_identifier_kind"];
export type StudioCacheScope = Schemas["StudioCacheClearRequest"]["scope"];
export type StudioCacheFeature =
  NonNullable<Schemas["StudioCacheClearRequest"]["features"]>[number];
