import type {
  Environment as EnvironmentTransport,
  LocalSourceObservation as LocalSourceObservationTransport,
  MonitoringDataflowRecordTransport,
  MonitoringDataflowRegistryResponseTransport,
  MonitoringRegistryRowTransport,
  MonitoringRegistryRunReferenceTransport,
  MonitoringRegistryProfileSnapshotTransport,
  MonitoringJobRecordTransport,
  MonitoringNamedCountTransport,
  MonitoringPageResponseTransport,
  Project as ProjectTransport,
  ProjectEnvironmentSummary as ProjectEnvironmentSummaryTransport,
  ProjectReferenceMapping as ProjectReferenceMappingTransport,
  ProjectSummary as ProjectSummaryTransport,
  ReferenceType as ReferenceTypeTransport,
  SourceSyncStatus as SourceSyncStatusTransport,
  SourceValidationResult as SourceValidationResultTransport,
  TargetIdentifierKind as TargetIdentifierKindTransport,
  WriteSafety,
} from "./contractTypes";

/** Monitoring integer/count/byte metrics remain decimal strings at the API boundary. */
export type MonitoringInteger = string | number | null;
export type MonitoringMetric = string | number | null;
export type MonitoringSessionScope = "all" | "null" | "exact";
export type MonitoringRegistryRecord = MonitoringRegistryRowTransport;
export type MonitoringRegistryRunReference = MonitoringRegistryRunReferenceTransport;
export type MonitoringRegistryProfileSnapshot = MonitoringRegistryProfileSnapshotTransport;
export type MonitoringRegistryResponse = MonitoringRecordsResponse<MonitoringRegistryRecord> & {
  section: MonitoringDataflowRegistryResponseTransport["section"];
};

export interface MonitoringLogAdmissionFile {
  source_id?: number | null;
  uri?: string | null;
  admitted: MonitoringInteger;
  excluded: MonitoringInteger;
  versions?: Record<string, MonitoringInteger>;
  locations?: Array<{ line?: number | null; version?: string | null }>;
}

export interface MonitoringLogAdmission {
  admitted: MonitoringInteger;
  excluded: MonitoringInteger;
  unsupported_only?: boolean;
  files: MonitoringLogAdmissionFile[];
}

export interface MonitoringV6Metrics {
  [key: string]: MonitoringInteger;
}

export interface MonitoringV6RecordEvidence {
  metrics?: MonitoringV6Metrics | null;
  read_request?: Record<string, unknown> | null;
  attempts?: unknown[] | null;
  diagnostic_issues?: unknown[] | null;
  [key: string]: unknown;
}

export type { WriteSafety } from "./contractTypes";

export type TargetIdentifierKind = TargetIdentifierKindTransport;
export type ReferenceType = ReferenceTypeTransport;
export type ResolutionState = "automatic" | "manual" | "unresolved";
export type ResolutionReason = "no_match" | "multiple_matches" | "out_of_scope" | "conflicting_targets" | "incomplete" | "target_missing";

export interface ReferenceResolution {
  state: ResolutionState;
  reason?: ResolutionReason | null;
}

export type Project = ProjectTransport;
export type ProjectEnvironmentSummary = ProjectEnvironmentSummaryTransport;
export type ProjectSummary = ProjectSummaryTransport;
export type ProjectReferenceMapping = ProjectReferenceMappingTransport;

export interface ProjectReferenceRegistryTarget {
  id: string;
  asset_id: string;
  asset_ids: string[];
  environment_ids: number[];
  environment_names: string[];
  asset_type: AssetInventoryItem["asset_type"];
  format?: string | null;
  connection_name: string;
  display_name: string;
  context?: string | null;
  kind: TargetIdentifierKind;
  value: string;
  display: string;
}

export interface ProjectReferenceRegistryEnvironment {
  environment_id: number;
  environment_name: string;
  resolution: ReferenceResolution;
  resolved_asset_id?: string | null;
  resolved_asset_ids: string[];
  observed_target_ids: string[];
  candidate_asset_ids: string[];
  manual_mapping_id?: number | null;
  manual_mapping_status?: string | null;
  occurrence_count: number;
  consumer_count: number;
}

export interface ProjectReferenceRegistryObservedTarget {
  target: ProjectReferenceRegistryTarget;
  environment_ids: number[];
  environment_names: string[];
}

export interface ProjectReferenceRegistryTargetCoverage {
  available_environment_names: string[];
  missing_environment_names: string[];
  available: number;
  total: number;
}

export interface ProjectReferenceRegistryRow {
  id: string;
  reference_type: ReferenceType;
  normalized_value: string;
  mapping?: ProjectReferenceMapping | null;
  resolution: ReferenceResolution;
  environments: ProjectReferenceRegistryEnvironment[];
  candidate_asset_ids: string[];
  resolved_asset_ids: string[];
  target?: ProjectReferenceRegistryTarget | null;
  observed_targets: ProjectReferenceRegistryObservedTarget[];
  target_coverage: ProjectReferenceRegistryTargetCoverage;
}

export interface ProjectReferenceRegistryFailure {
  environment_id: number;
  environment_name: string;
  message: string;
}

export interface ProjectReferenceRegistryResponse {
  project_id: number;
  mappings: ProjectReferenceMapping[];
  rows: ProjectReferenceRegistryRow[];
  targets: ProjectReferenceRegistryTarget[];
  failures: ProjectReferenceRegistryFailure[];
}

export interface StudioPathInfo {
  backend: string;
  path: string;
  exists: boolean;
  size_bytes?: number | null;
  maintenance_supported: boolean;
}

export interface StudioAnalyticsCacheInfo {
  scope: "studio";
  path: string;
  exists: boolean;
  size_bytes?: number | null;
  schema_version?: number | null;
  generation?: number | null;
  build_state: "ready" | "building" | "rebuild_required";
  published_at?: string | null;
  dataflow_row_count: number;
  job_row_count: number;
  filter_value_count: number;
  cached_source_count: number;
  active_source_count: number;
  orphan_source_count: number;
  orphan_source_ids: number[];
  resource_limits: Record<string, string | number>;
}

export interface StudioDiagnostics {
  workspace_database: StudioPathInfo;
  analytics_cache: StudioAnalyticsCacheInfo;
}

export type StudioCacheScope = "read_models" | "analytics" | "all_disposable";
export type StudioCacheFeature = "overview" | "assets" | "lineage" | "monitoring";

export interface StudioResultCacheStatus {
  backend: string;
  path: string;
  entries: number;
  payload_bytes: number;
  file_bytes: number;
  limits: Record<string, number>;
  memory: { entries: number; bytes: number };
}

export interface AnalyticsUpgradeStatus {
  state: string;
  operation_kind?: string;
  reason_code?: string | null;
  source_schema_version?: number | null;
  target_schema_version?: number | null;
  source_ids?: number[];
  completed_source_ids?: number[];
  attempt_count?: number;
  episode_attempt_count?: number;
  error_code?: string | null;
  error_message?: string | null;
  next_retry_at?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  updated_at?: string | null;
  duration_seconds?: number | null;
  source_progress?: AnalyticsUpgradeSourceProgress[];
}

export interface AnalyticsUpgradeSourceProgress {
  source_id: number;
  project_id?: number | null;
  project_name?: string | null;
  environment_id?: number | null;
  environment_name?: string | null;
  source_kind?: string | null;
  label?: string | null;
  uri?: string | null;
  is_registered?: boolean | null;
  status: string;
  message?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  duration_seconds?: number | null;
}

export interface StudioCacheStatus {
  result_cache: StudioResultCacheStatus;
  analytics_cache: {
    backend: string;
    path: string;
    exists: boolean;
    file_bytes: number;
    schema_version?: number | null;
    generation?: number | null;
    build_state: string;
    published_at?: string | null;
    dataflow_rows: number;
    job_rows: number;
    filter_values: number;
    upgrade?: AnalyticsUpgradeStatus | null;
  };
  sync_job_retention?: Record<string, unknown> | null;
}

export interface StudioCacheMutation {
  scope: string;
  environment_id?: number | null;
  features: string[];
  read_models?: Record<string, unknown> | null;
  analytics?: Record<string, unknown> | null;
  analytics_dependent_read_models?: Record<string, unknown> | null;
}

export interface StudioSettings {
  timezone: string;
  timezone_source: "configured" | "server_default";
  timezone_offset_minutes: number;
  source_check_mode: "fixed" | "adaptive";
  source_check_interval_seconds: number;
  source_check_max_interval_seconds: number;
  updated_at?: string | null;
}

export type ModuleStatus = "available" | "coming_soon";

export interface ModuleInfo {
  key: string;
  name: string;
  description: string;
  group: string;
  status: ModuleStatus;
  togglable: boolean;
  default_enabled: boolean;
  pages: string[];
  enabled: boolean;
}

export type Environment = EnvironmentTransport;

export interface SourcePath {
  id: number;
  environment_id: number;
  uri: string;
  label?: string | null;
  enabled: boolean;
  sync_schedule_enabled: boolean;
  sync_interval_minutes?: number | null;
  last_scheduled_sync_at?: string | null;
  source_config?: Record<string, unknown> | null;
  storage?: StorageBinding;
  configured_location?: ConfiguredSourceLocation | null;
  created_at: string;
  latest_validation?: SourceValidationResult | null;
}

export interface ConfiguredSourceLocation {
  registration_id: number;
  purpose: "project" | "metadata" | "sql" | "code" | "logs";
  input_uri: string;
  canonical_uri: string;
  input_locations: Record<string, string | string[]>;
  canonical_locations: Record<string, string | string[]>;
}

export type StorageProvider = "local" | "s3" | "minio" | "adls" | "onelake" | "gcs" | "dbfs";
export type StorageAuthMode = "none" | "ambient" | "anonymous" | "credential_profile";

export interface StorageBinding {
  provider: StorageProvider;
  auth_mode: StorageAuthMode;
  credential_profile_id?: string | null;
  options: Record<string, unknown>;
}

export interface CredentialProfile {
  id: string;
  name: string;
  provider: Exclude<StorageProvider, "local">;
  auth_type: string;
  secret_state: "present" | "missing" | "unavailable";
  masked_summary: Record<string, unknown>;
  version: number;
  reference_count: number;
  created_at: string;
  updated_at: string;
}

export interface CredentialProfileDetail extends CredentialProfile {
  config: Record<string, unknown>;
}

export interface CredentialCapabilities {
  providers: Record<string, string[]>;
  secret_store_available: boolean;
  secret_store_backend: string;
  remediation?: string | null;
}

export interface StorageConnectionValidation {
  status: "ok" | "error";
  provider: string;
  canonical_uri?: string | null;
  object_type?: "file" | "directory" | null;
  objects_scanned: number;
  provider_revision?: string | null;
  metadata_write_back_supported: boolean;
  message: string;
  error?: Record<string, unknown> | null;
}

export type SourceValidationResult = SourceValidationResultTransport;
export type LocalSourceObservation = LocalSourceObservationTransport;

export interface SourcesWorkspace {
  schema_version: "sources-workspace.v1";
  environment_id: number;
  metadata_sources: SourcePath[];
  sql_sources: SourcePath[];
  log_sources: SourcePath[];
  code_artifacts: SourcePath[];
  statuses: SourceSyncStatusTransport[];
  earliest_cloud_due_at?: string | null;
  dependency_version: string;
}

export type LogSyncRequest =
  | { mode: "incremental" }
  | {
      mode: "incremental_with_lookback";
      lookback: {
        from_partition: string;
        to_partition: string;
      };
    };

export interface SystemLogFile {
  source_id: number;
  file_uri: string;
  file_kind: string;
  log_session_id?: string | null;
  row_count: MonitoringInteger;
  log_timestamp?: string | null;
  event_min_timestamp?: string | null;
  event_max_timestamp?: string | null;
  run_date?: string | null;
}

export interface SystemLogResponse {
  records: Array<Record<string, unknown>>;
  total: number;
  files: SystemLogFile[];
  errors: Array<Record<string, unknown>>;
}

export interface FreshnessGroup {
  status: string;
  max_source_modified_at?: string | null;
  cache_synced_at?: string | null;
  count: number;
  pending_sync_count?: number;
}

export interface EnvironmentContext {
  schema_version: "environment-context.v1";
  project: { id: number; name: string };
  environment: { id: number; project_id: number; name: string };
  source_counts: { metadata: number; logs: number; code: number };
  freshness: {
    status: string;
    message: string;
    max_source_modified_at?: string | null;
    metadata: FreshnessGroup;
    logs: FreshnessGroup;
  };
  versions: {
    source_registry: string;
    metadata_catalog: string;
    code_catalog: string;
    operations: string;
    reference_mappings: string;
  };
  checked_at: string;
}

export interface SourceDeleteImpactItem {
  kind: string;
  label: string;
  count: number;
  severity: "info" | "warning" | string;
}

export interface SourceDeleteImpact {
  source_id: number;
  source_kind: string;
  source_uri: string;
  mode: "hard_delete" | string;
  metadata_file_deleted: boolean;
  has_impact: boolean;
  impacts: SourceDeleteImpactItem[];
  summary: string;
}

export interface Endpoint {
  connection_name?: string | null;
  connection_type?: string | null;
  format?: string | null;
  catalog?: string | null;
  database?: string | null;
  schema_name?: string | null;
  table?: string | null;
  path?: string | null;
  query?: string | null;
  python_function?: string | null;
  load_type?: string | null;
}

export interface Dataflow {
  metadata_source_id: number;
  metadata_source_uri: string;
  dataflow_id?: string | null;
  name: string;
  description?: string | null;
  stage?: string | null;
  processing_mode?: string | null;
  is_active?: boolean;
  load_type?: string | null;
  source?: Endpoint;
  destination?: Endpoint;
  source_asset_id?: string;
  destination_asset_id?: string;
}

export interface MetadataResponse {
  summary: Record<string, unknown>;
  sources: Array<Record<string, unknown>>;
  connections: Array<Record<string, unknown>>;
  dataflows: Dataflow[];
  schema_hints: Array<Record<string, unknown>>;
  errors: Array<Record<string, unknown>>;
}

export interface EnvironmentOverview {
  schema_version: "environment-overview.v2";
  sources: {
    metadata: { configured: number; enabled: number };
    logs: { configured: number; enabled: number };
    validation: { errors: number; warnings: number };
  };
  metadata: {
    connections: number;
    enabled_connections: number;
    dataflows: number;
    enabled_dataflows: number;
    schema_hints: number;
    enabled_schema_hints: number;
    stages: Array<{ name: string; count: number }>;
    load_types: Array<{ name: string; count: number }>;
    errors: Array<Record<string, unknown>>;
  };
  lineage: LineageSummary & { error_count: number };
  monitoring: {
    job_records: MonitoringInteger;
    total_failures: MonitoringInteger;
    dataflow_success_rate: number;
    failed_job_windows: { last7: MonitoringInteger; last30: MonitoringInteger; last365: MonitoringInteger };
    active_engines: MonitoringInteger;
    latest_log_at: string | null;
    date_range: { min?: string | null; max?: string | null };
    errors: Array<Record<string, unknown>>;
  };
  cache: { state: "hit" | "miss"; computed_at: string };
}

export interface MetadataEditorColumn {
  key: string;
  name: string;
}

export interface MetadataEditorSheet {
  columns: MetadataEditorColumn[];
  rows: Array<Record<string, unknown>>;
}

export interface MetadataEditorIssue {
  severity: "error" | "warning" | string;
  sheet: string;
  row_index: number;
  column: string;
  message: string;
}

export interface PendingSqlFileOperation {
  source_id: number;
  query: string;
  content: string;
  base_revision?: Record<string, unknown> | null;
  base_kind?: "sql_root" | "project_root" | string;
  relative_path?: string;
  base_uri?: string | null;
  config_revision?: Record<string, unknown> | null;
  publish_status?: "pending" | "applied";
  published_revision?: Record<string, unknown> | null;
  published_content_hash?: string | null;
}

export interface MetadataEditorDocument {
  source: {
    source_id: number;
    environment_id: number;
    project_id?: number | null;
    uri: string;
    name?: string;
    format: string;
    scope?: "source" | "environment" | string;
    read_only?: boolean;
    revision: Record<string, unknown>;
  };
  sheets: Record<string, MetadataEditorSheet>;
  issues: MetadataEditorIssue[];
  metadata_document?: Record<string, unknown>;
  metadata_documents?: Record<string, Record<string, unknown>>;
  sql_files?: PendingSqlFileOperation[];
}

export interface MetadataEditorWorkspace {
  schema_version: "metadata-editor-workspace.v1";
  environment_id: number;
  metadata_catalog_version: string;
  document: MetadataEditorDocument;
  draft: MetadataEditorDocument | null;
}

export interface MetadataBackup {
  id: number;
  project_id: number;
  environment_id: number;
  source_id: number;
  source_uri: string;
  backup_path?: string | null;
  backup_ref?: string | null;
  backup_hash?: string | null;
  backup_size?: number | null;
  source_revision?: Record<string, unknown> | null;
  saved_revision?: Record<string, unknown> | null;
  created_at: string;
}

export interface MetadataWriteItem {
  id: number;
  order: number;
  kind: string;
  state: string;
  source_id?: number | null;
  source_name?: string | null;
  source_uri: string;
  target_uri: string;
  action: string;
  label: string;
  sheets: string[];
  provider?: string | null;
  write_safety?: WriteSafety | null;
  error?: Record<string, unknown> | null;
}

export interface MetadataWriteOperation {
  operation_key: string;
  environment_id: number;
  state: string;
  created_at: string;
  updated_at: string;
  completed_at?: string | null;
  expires_at: string;
  error?: Record<string, unknown> | null;
  summary: Record<string, number>;
  items: MetadataWriteItem[];
}

export interface LineageAsset {
  id: string;
  entity_type: "asset";
  label: string;
  asset_type: "table" | "path" | "sql_query" | "python_function" | "api" | "unresolved";
  display_name: string;
  declaration_status: "declared" | "discovered_only";
  display_label?: string;
  endpoint_locator?: string;
  endpoint_kind?: string;
  identity_type?: string;
  connection_name?: string | null;
  connection_type?: string | null;
  format?: string | null;
  catalog?: string | null;
  database?: string | null;
  schema_name?: string | null;
  path?: string | null;
  table?: string | null;
  query?: string | null;
  python_function?: string | null;
  metadata_source_ids?: number[];
  roles?: string[];
  mapping_target?: {
    kind: TargetIdentifierKind | string;
    value: string;
    display: string;
  } | null;
  observations?: Array<Record<string, unknown>>;
  identifiers?: Array<Record<string, unknown>>;
}

export interface LineageReference {
  id: string;
  entity_type: "reference";
  reference_type: ReferenceType;
  display_name: string;
  normalized_value: string;
  resolution: ReferenceResolution;
  resolved_asset_id?: string | null;
  resolved_asset_ids: string[];
  candidate_asset_ids: string[];
  occurrence_count: number;
  consumer_asset_ids: string[];
  provenances: Array<"sql" | "python" | "python_sql">;
  dependency_count: number;
  manual_mapping?: AssetManualMappingInfo | null;
}

export interface SourceLocation {
  module?: string | null;
  path?: string | null;
  function_path?: string | null;
  line?: number | null;
  column?: number | null;
  end_line?: number | null;
  end_column?: number | null;
  coordinate_space?: "query_source" | "function_source" | null;
}

export interface LineageDataflow {
  id: string;
  dataflow_id: string;
  name: string;
  source_asset_id: string;
  destination_asset_id: string;
  stage?: string | null;
  load_type?: string | null;
  metadata_source_id: number;
  metadata_source_uri: string;
}

export interface LineageDependency {
  id: string;
  target_asset_id: string;
  consumer_asset_id: string;
  kind: "reads" | "uses";
  provenance: "sql" | "python" | "python_sql";
  resolution: ReferenceResolution;
  resolution_method: string;
  reference_id: string;
  reference_occurrence_id: string;
  resolved_asset_id?: string | null;
  addressing_mode?: string | null;
  qualification_level?: string | null;
}

export interface LineageSummary {
  assets: number;
  references: number;
  dataflows: number;
  dependencies: number;
  stitched_assets: number;
  declared_assets: number;
  automatic_references: number;
  manual_references: number;
  unresolved_references: number;
  automatic_dependencies: number;
  manual_dependencies: number;
  unresolved_dependencies: number;
  diagnostics: number;
}

export interface LineageResponse {
  schema_version: "lineage.v4";
  summary: LineageSummary;
  assets: LineageAsset[];
  references: LineageReference[];
  dataflows: LineageDataflow[];
  dependencies: LineageDependency[];
}

export interface AssetSummary {
  assets: number;
  references: number;
  manual_mappings: number;
  visible: number;
  asset_attention: number;
  with_attention: number;
  automatic_references: number;
  manual_references: number;
  unresolved_references: number;
}

export interface AssetMetadataSource {
  id: number;
  uri: string;
}

export interface AssetAttention {
  severity: "info" | "warning" | "error";
  code: string;
  message: string;
  source_type: string;
  subject_type: string;
  dataflow_id?: string | null;
  metadata_source_id?: number | null;
  reference_id?: string | null;
  reference_occurrence_id?: string | null;
  details: Record<string, unknown>;
}

export interface AssetFilterOptions {
  connections: string[];
  formats: string[];
  asset_types: string[];
  roles: string[];
  attention_states: string[];
}

export interface AssetReferenceFilterOptions {
  connections?: string[];
  reference_types: Array<ReferenceType | string>;
  provenances: Array<"sql" | "python" | "python_sql" | string>;
  resolution_states?: Array<ResolutionState | string>;
  resolution_methods?: string[];
  attention_states: string[];
}

export interface AssetManualMappingInfo {
  mapping_id: number;
  status?: string;
  note?: string | null;
  target_identifier_kind?: string | null;
  target_normalized_value?: string | null;
}

export interface AssetInventoryItem {
  id: string;
  display_name: string;
  friendly_name: string;
  full_identity: string;
  asset_type: "table" | "path" | "sql_query" | "python_function" | "api" | "unresolved";
  format?: string | null;
  connection_name?: string | null;
  connection_type?: string | null;
  catalog?: string | null;
  database?: string | null;
  schema_name?: string | null;
  table?: string | null;
  path?: string | null;
  query?: string | null;
  python_function?: string | null;
  roles: string[];
  metadata_source_ids: number[];
  metadata_sources?: AssetMetadataSource[];
  metadata_source_count?: number;
  upstream_count: number;
  downstream_count: number;
  input_dataflow_count: number;
  output_dataflow_count: number;
  depends_on_count: number;
  used_by_count: number;
  attention_count: number;
  attention_items?: AssetAttention[];
  identifiers?: Array<Record<string, unknown>>;
  observations?: Array<Record<string, unknown>>;
  identifier_count?: number;
  observation_count?: number;
  mapping_target?: {
    kind: TargetIdentifierKind | string;
    value: string;
    display: string;
  } | null;
}

export interface AssetReferenceGroupItem {
  id: string;
  reference_type: ReferenceType;
  normalized_value: string;
  display_name: string;
  resolution: ReferenceResolution;
  resolved_asset_id?: string | null;
  resolved_asset_ids: string[];
  resolved_asset?: AssetBrief | null;
  candidate_asset_ids: string[];
  candidate_assets: AssetBrief[];
  occurrence_ids: string[];
  consumer_asset_ids: string[];
  consumer_assets: AssetBrief[];
  provenances: Array<"sql" | "python" | "python_sql" | string>;
  resolution_methods?: string[];
  occurrence_count?: number;
  dependency_count: number;
  dataflow_ids: string[];
  attention_count: number;
  attention_items: AssetAttention[];
  observations: Array<Record<string, unknown>>;
  manual_mapping?: AssetManualMappingInfo | null;
}

export interface AssetReferenceOccurrenceItem {
  id: string;
  reference_id: string;
  reference_type: ReferenceType;
  raw_value: string;
  normalized_value: string;
  context_scope?: string | null;
  context_scope_source?: "detected" | "metadata_context" | null;
  addressing_mode?: string | null;
  qualification_level?: string | null;
  source_location?: SourceLocation | null;
  display_name: string;
  provenance?: "sql" | "python" | "python_sql" | string | null;
  consumer_asset_id?: string | null;
  consumer_asset?: AssetBrief | null;
  connection_name?: string | null;
  resolution: ReferenceResolution;
  resolution_method?: string | null;
  resolved_asset_id?: string | null;
  resolved_asset?: AssetBrief | null;
  candidate_asset_ids: string[];
  candidate_assets: AssetBrief[];
  dependency_count: number;
  dataflow_ids: string[];
  attention_count: number;
  attention_items: AssetAttention[];
  observations: Array<Record<string, unknown>>;
  manual_mapping?: AssetManualMappingInfo | null;
}

export interface AssetInventoryResponse {
  summary: AssetSummary;
  items: AssetInventoryItem[];
  filter_options: AssetFilterOptions;
  catalog_version: string;
}

export interface AssetReferenceListResponse {
  items: AssetReferenceGroupItem[];
  filter_options: AssetReferenceFilterOptions;
  catalog_version: string;
}

export interface AssetReferenceDetailResponse {
  reference: AssetReferenceGroupItem;
  occurrences: AssetReferenceOccurrenceItem[];
  catalog_version: string;
}

export interface AssetDirectRelationships {
  upstream_assets: number;
  downstream_assets: number;
  input_flows: number;
  output_flows: number;
  depends_on_count: number;
  depends_on_total?: number;
  depends_on_mapped_count?: number;
  depends_on_unmapped_count?: number;
  depends_on_asset_count?: number;
  depends_on_reference_count?: number;
  used_by_count: number;
  used_by_total?: number;
  position: "entry" | "transit" | "exit" | "isolated" | string;
}

export interface AssetBrief {
  id: string;
  display_name: string;
  friendly_name: string;
  full_identity?: string;
  asset_type: AssetInventoryItem["asset_type"] | string;
  connection_name?: string | null;
  format?: string | null;
  attention_count: number;
}

export interface AssetNeighbor {
  asset: AssetBrief;
  relation_flow_count: number;
  relation_dependency_count: number;
  relation_kinds: string[];
}

export interface AssetFlow {
  id: string;
  dataflow_id: string;
  name: string;
  stage?: string | null;
  load_type?: string | null;
  metadata_source_id?: number | null;
  metadata_source_uri?: string | null;
  source_asset_id?: string | null;
  destination_asset_id?: string | null;
  counterpart: AssetBrief;
}

export interface AssetDependencyReference {
  id: string;
  display_name: string;
  reference_type: string;
  resolution: ReferenceResolution;
  raw_value?: string | null;
  provenance?: string | null;
}

export interface AssetDependsOnItem {
  id: string;
  kind: string;
  provenance: string;
  resolution: ReferenceResolution;
  resolution_method: string;
  reference_id?: string | null;
  resolved_asset_id?: string | null;
  resolved_asset?: AssetBrief | null;
  source_reference?: AssetDependencyReference | null;
}

export interface AssetUsedByItem {
  id: string;
  kind: string;
  provenance: string;
  resolution: ReferenceResolution;
  resolution_method: string;
  target_asset: AssetBrief;
  reference?: AssetDependencyReference | null;
}

export interface AssetDefinitionDiagnostic {
  severity: "info" | "warning" | "error" | string;
  code: string;
  message: string;
  details?: Record<string, unknown>;
}

export interface AssetDefinitionResponse {
  kind: "sql_query" | "python_function" | "api" | "path" | "unresolved" | string;
  language?: string | null;
  status: "available" | "unavailable" | "ambiguous" | "empty" | string;
  title?: string | null;
  raw?: string | null;
  formatted?: string | null;
  source?: string | null;
  function_path?: string | null;
  module_name?: string | null;
  relative_path?: string | null;
  line_count?: number;
  diagnostics?: AssetDefinitionDiagnostic[];
  matches?: Array<Record<string, unknown>>;
  [key: string]: unknown;
}

export interface AssetSourceResponse {
  definition: AssetDefinitionResponse;
  catalog_version: string;
}

export interface AssetDetailResponse {
  asset: AssetInventoryItem;
  definition?: AssetDefinitionResponse | null;
  attention_items: AssetAttention[];
  direct_relationships: AssetDirectRelationships;
  upstream_assets: AssetNeighbor[];
  downstream_assets: AssetNeighbor[];
  input_flows: AssetFlow[];
  output_flows: AssetFlow[];
  depends_on: AssetDependsOnItem[];
  used_by: AssetUsedByItem[];
}

export interface ReferenceSourceMatch {
  line: number;
  column: number;
  end_line: number;
  end_column: number;
  precision: "exact_reference" | "detection_expression" | "location_only";
}

export interface ReferenceSourceView {
  id: "query_source" | "consumer_source" | "evaluated_sql";
  label: string;
  language: "sql" | "python";
  content: string;
  path?: string | null;
  function_path?: string | null;
  module_name?: string | null;
  matches: ReferenceSourceMatch[];
}

export interface ReferenceSourceDiagnostic {
  severity: "info" | "warning" | "error";
  code: string;
  message: string;
}

export interface ReferenceOccurrenceSourceResponse {
  occurrence_id: string;
  consumer_asset_id: string;
  views: ReferenceSourceView[];
  diagnostics: ReferenceSourceDiagnostic[];
}

export type MonitoringRecord = MonitoringDataflowRecordTransport & MonitoringV6RecordEvidence & {
  started_at?: string | null;
  completed_at?: string | null;
  raw_record_json?: string | null;
  /** Assets-owned destination identity metadata (additive across Monitoring versions). */
  identity_version?: string | null;
  identity_status?: "resolved" | "unresolved" | "conflict" | string | null;
  identity_reason?: string | null;
  identity_provenance?: Record<string, "captured" | "metadata" | "derived" | string> | null;
  identity_metadata_sources?: Array<{ source_id?: number | null; revision?: string | null; [key: string]: unknown }> | null;
  read_boundary_adjustment_state?: string | null;
  read_boundary_adjustment_basis?: string | null;
  read_boundary_adjustment_reason?: string | null;
  read_boundary_adjustment_input_json?: string | null;
  read_boundary_adjustment_effective_json?: string | null;
};

export type MonitoringFailureActivity = MonitoringRecord & {
  activity_type?: "failed_run" | "recovered_attempt";
  activity_time?: string | null;
  activity_time_source?: "attempt_end_time" | "attempt_start_time" | "dataflow_run_time" | string | null;
  activity_context?: string | null;
  activity_message?: string | null;
  failure_time?: string | null;
  failure_phase?: string | null;
  failure_category?: string | null;
  failure_message?: string | null;
  attempt_number?: MonitoringInteger | null;
  attempt_message?: string | null;
};

export type JobRecord = MonitoringJobRecordTransport & {
  raw_record_json?: string | null;
};

export type NamedCount = Omit<MonitoringNamedCountTransport, "count"> & {
  count: MonitoringInteger;
};

export interface MonitoringReport {
  summary: {
    dataflow_records: MonitoringInteger;
    job_records: MonitoringInteger;
    date_range: { min?: string | null; max?: string | null };
    latest_log_at?: string | null;
    latest_job_log_at?: string | null;
    latest_dataflow_log_at?: string | null;
    timezone?: string | null;
    timezone_source?: "configured" | "server_default" | string | null;
    requested_grain?: string | null;
    effective_grain?: string | null;
    active_engines: MonitoringInteger;
    active_metadata_providers: MonitoringInteger;
    log_base_paths: MonitoringInteger;
    identity_version?: string | null;
  };
  log_admission?: MonitoringLogAdmission | null;
  health: {
    status: string;
    label: string;
    reasons: string[];
    latest_log_at?: string | null;
    latest_job_log_at?: string | null;
    latest_dataflow_log_at?: string | null;
    latest_log_age_days?: number | null;
    failed_jobs_last_3_days?: MonitoringInteger;
    failed_jobs_last_7_days?: MonitoringInteger;
    failed_dataflows_last_3_days?: MonitoringInteger;
    failed_dataflows_last_7_days?: MonitoringInteger;
    maintenance_failed_last_7_days?: MonitoringInteger;
    maintenance_failed_last_14_days?: MonitoringInteger;
    maintenance_skipped_last_7_days?: MonitoringInteger;
  };
  attention: Array<{
    severity: "good" | "info" | "warning" | "bad" | string;
    code: string;
    title: string;
    detail: string;
    target: string;
    evidence?: Record<string, unknown>;
  }>;
  coverage: Record<string, string | number | null>;
  reconciliation: {
    status: string;
    scope_coverage?: string | null;
    mismatch_count: MonitoringInteger;
    checks: Array<Record<string, string | number | null>>;
    summary?: {
      total_checks_count: MonitoringInteger;
      total_jobs_count: MonitoringInteger;
      comparable_checks_count: MonitoringInteger;
      comparable_jobs_count: MonitoringInteger;
      mismatch_checks_count: MonitoringInteger;
      mismatch_jobs_count: MonitoringInteger;
      unassessed_checks_count: MonitoringInteger;
      unassessed_jobs_count: MonitoringInteger;
      scope_coverage?: string | null;
      unassessed_reason?: string | null;
      [key: string]: unknown;
    } | null;
  };
  diagnostics: {
    kpis: Record<string, string | number | null>;
    assessment?: {
      status: string;
      reasons: Array<{ code: string; severity: string; count: MonitoringInteger; scope_coverage?: string | null }>;
      coverage?: string | null;
      [key: string]: unknown;
    } | null;
    registry_summary?: {
      total_issues: MonitoringInteger;
      loaded_issues: MonitoringInteger;
      limit: number;
      truncated: boolean;
      [key: string]: unknown;
    } | null;
    diagnostic_capture?: {
      summary: Record<string, string | number | boolean | null>;
      by_reason: Array<Record<string, string | number | null>>;
      evidence: Array<Record<string, string | number | null>>;
    } | null;
    record_evidence_by_date?: Array<Record<string, string | number | null>>;
    job_linkage_summary?: Array<Record<string, string | number | null>>;
    reconciliation_by_metric?: Array<Record<string, string | number | null>>;
    field_completeness?: Array<Record<string, string | number | boolean | null>>;
    source_coverage?: Array<Record<string, string | number | boolean | null>>;
    investigation_queue?: Array<Record<string, unknown>>;
    finding_groups?: DiagnosticsFindingGroup[];
    findings_summary?: DiagnosticsFindingsSummary | null;
    scope_notices?: DiagnosticsScopeNotice[];
    job_id_evidence: Array<Record<string, string | number | null>>;
    read_errors: Array<Record<string, unknown>>;
  };
  operations: {
    kpis: Record<string, MonitoringMetric>;
    windows?: Record<string, Record<string, MonitoringMetric>>;
    job_duration_stats?: Record<string, MonitoringMetric>;
    job_status_distribution: Array<{ status: string; count: MonitoringInteger }>;
    job_runs_by_operation_type?: Array<Record<string, string | number>>;
    job_runs_by_dataflow_operation_type?: Array<Record<string, string | number>>;
    job_duration_by_operation_types?: Array<Record<string, unknown>>;
    dataflow_duration_by_stage?: Array<Record<string, unknown>>;
    job_workload_efficiency?: Array<Record<string, string | number | null>>;
    job_child_fanout_distribution?: Array<Record<string, string | number | null>>;
    jobs_by_date_status: Array<Record<string, string | number | null>>;
    latest_failed_job?: JobRecord | null;
    slowest_jobs?: JobRecord[];
    jobs_by_engine_provider?: Array<Record<string, string | number>>;
    jobs_by_child_impact?: Array<Record<string, string | number | null>>;
    job_attention?: Array<Record<string, string | number | null>>;
    dataflows_by_date_status: Array<Record<string, string | number | null>>;
    failed_jobs: JobRecord[];
    dataflow_kpis: Record<string, MonitoringMetric>;
    dataflow_duration_stats?: Record<string, MonitoringMetric>;
    dataflow_runs_by_operation_type?: Array<Record<string, string | number>>;
    dataflow_runs_by_destination_operation_type?: Array<Record<string, string | number>>;
    phase_health?: Array<Record<string, string | number | null>>;
    phase_health_by_stage?: Array<Record<string, string | number | null>>;
    dataflow_endpoint_health?: Array<Record<string, string | number | null>>;
    dataflow_name_status_health?: Array<Record<string, string | number | null>>;
    dataflow_watermark_summary?: Array<Record<string, string | number | null>>;
    job_status_by_stage?: Array<Record<string, string | number>>;
    dataflow_status_by_stage?: Array<Record<string, string | number>>;
    status_by_stage: Array<Record<string, string | number>>;
  };
  failures: {
    kpis?: Record<string, string | number | null>;
    latest_queue?: MonitoringFailureActivity[];
    repeated_signatures?: Array<Record<string, string | number | null>>;
    failure_by_phase?: NamedCount[];
    failure_category_phase_matrix?: Array<Record<string, string | number>>;
    endpoint_impact?: Array<Record<string, string | number | null>>;
    failed_by_stage: NamedCount[];
    failed_by_source_connection_type: NamedCount[];
    top_failing_dataflows: Array<Record<string, string | number | null>>;
    error_categories: Array<{ category: string; count: MonitoringInteger }>;
    failure_trend_by_date: Array<{ date: string; failed: MonitoringInteger }>;
    failed_records: MonitoringRecord[];
    recovered_attempts?: Array<Record<string, string | number | null>>;
  };
  performance: {
    kpis?: Record<string, string | number | null>;
    duration_distribution_by_stage?: Array<Record<string, unknown>>;
    phase_contribution_by_stage_operation?: Array<Record<string, string | number | null>>;
    workload_efficiency_points?: Array<Record<string, string | number | null> | Array<string | number | null>>;
    slowest_dataflow_profiles?: Array<Record<string, string | number | null>>;
    runtime_context_profiles?: Array<Record<string, string | number | null>>;
    performance_trend?: Array<Record<string, string | number | null>>;
  };
  volume: {
    kpis: Record<string, MonitoringMetric>;
    rows_by_date: Array<Record<string, string | number | null>>;
    bytes_by_date: Array<Record<string, string | number | null>>;
    volume_by_load_type: Array<Record<string, string | number>>;
    volume_by_workload_type?: Array<Record<string, string | number | null>>;
    route_volume?: Array<Record<string, string | number | null>>;
    top_dataflows_by_rows_read?: Array<Record<string, string | number | null>>;
    top_dataflows_by_rows_written: Array<Record<string, string | number | null>>;
    top_dataflows_by_lakehouse_rows_written?: Array<Record<string, string | number | null>>;
    top_dataflows_by_bytes_added?: Array<Record<string, string | number>>;
    top_dataflows_by_net_bytes?: Array<Record<string, string | number | null>>;
  };
  maintenance: {
    kpis: Record<string, string | number | null>;
    operation_type_counts?: Array<Record<string, string | number | null>>;
    maintenance_status_counts?: Array<Record<string, string | number | null>>;
    status_by_date?: Array<Record<string, string | number | null>>;
    reclaim_by_date?: Array<Record<string, string | number | null>>;
    table_efficiency_points?: Array<Record<string, string | number | boolean | null>>;
    table_outcome?: Array<Record<string, string | number | null>>;
    format_comparison: Array<Record<string, string | number>>;
    bytes_reclaimed_by_date: Array<Record<string, string | number>>;
  };
  freshness: {
    kpis: Record<string, MonitoringMetric>;
    age_by_dataflow?: Array<Record<string, string | number | null>>;
    watermark_movement_by_date?: Array<Record<string, string | number | null>>;
    age_distribution?: Array<Record<string, string | number | null>>;
    watermark_coverage_by_stage?: Array<Record<string, string | number | null>>;
    skipped_streak_distribution?: Array<Record<string, string | number | null>>;
    read_boundary_adjustment_by_dataflow?: Array<Record<string, string | number | null>>;
  };
  errors: Array<Record<string, unknown>>;
}

export type MonitoringPageResponse = MonitoringPageResponseTransport;

export interface MonitoringFilterOption {
  value: string;
  label: string;
  count?: MonitoringInteger;
}

export interface MonitoringFilterOptionsResponse {
  options: Record<string, MonitoringFilterOption[]>;
  summary: {
    source: string;
    fields: number;
  };
  errors: Array<Record<string, unknown>>;
}

export interface LatestStatusResponse {
    latest_by_id: Record<string, MonitoringRecord>;
    latest_by_name: Record<string, MonitoringRecord>;
    ambiguous_names: string[];
    errors: Array<Record<string, unknown>>;
  }

export type DiagnosticsFindingKind = "issue" | "warning" | "limitation" | "notice";
export type DiagnosticsFindingSubject = "job" | "dataflow_record" | "field_group";

/** One structural finding group; counts use the unit named by `subject_kind`. */
export interface DiagnosticsFindingGroup {
  group_key: string;
  kind: DiagnosticsFindingKind;
  rule: string;
  reason: string;
  section: string;
  subject_kind: DiagnosticsFindingSubject;
  affected_objects: string;
  checks?: string | null;
  occurrences?: string | null;
  latest_time?: string | null;
}

export interface DiagnosticsFindingMetric {
  metric: string;
  expected?: string | null;
  observed?: string | null;
  difference?: string | null;
}

/** One affected object with exact nullable identity for drill-in. */
export interface DiagnosticsFindingObject {
  group_key: string;
  rule: string;
  reason: string;
  section: string;
  subject_kind: DiagnosticsFindingSubject;
  source_id?: number | null;
  log_session_id?: string | null;
  job_id?: string | null;
  dataflow_id?: string | null;
  dataflow_run_id?: string | null;
  dataflow_name?: string | null;
  file_uri?: string | null;
  file_kind?: string | null;
  record_line?: string | null;
  latest_time?: string | null;
  checks?: string | null;
  occurrences?: string | null;
  job_status?: string | null;
  job_total_dataflows?: string | null;
  filtered_dataflow_records?: string | null;
  recorded_dataflow_records?: string | null;
  excluded_dataflow_records?: string | null;
  observed_at?: string | null;
  records_dropped?: string | null;
  bytes_dropped?: string | null;
  metrics?: DiagnosticsFindingMetric[];
  field_group?: Record<string, unknown> | null;
}

export interface DiagnosticsFindingsSummary {
  analytics_generation?: string | null;
  as_of?: string | null;
  scope: "full" | "subset" | "none";
  issue_groups: string;
  warning_groups: string;
  limitation_groups: string;
  groups_total: string;
  groups_loaded: string;
  group_limit: number;
  affected_objects: Array<{ kind: DiagnosticsFindingKind; subject_kind: DiagnosticsFindingSubject; count: string }>;
  restrictions: string[];
}

export interface DiagnosticsScopeNotice {
  code: string;
  group_key: string;
  jobs: string;
  checks: string;
  restrictions: string[];
}

export interface DiagnosticsEvidenceResponse {
  mode: "registry" | "detail";
  group: DiagnosticsFindingGroup | null;
  groups: DiagnosticsFindingGroup[];
  objects: DiagnosticsFindingObject[];
  errors: Array<Record<string, unknown>>;
  summary: MonitoringRecordsResponse<Record<string, unknown>>["summary"];
}

export interface MonitoringRecordsResponse<T extends Record<string, unknown>> {
  records: T[];
  errors: Array<Record<string, unknown>>;
  summary: {
    records: number;
    total_records?: number;
    limit: number;
    offset?: number;
    cache?: string;
    analytics_generation?: string | null;
    as_of?: string | null;
    identity_version?: string | null;
  };
}
