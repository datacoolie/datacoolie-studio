export type TargetIdentifierKind = "logical_table" | "physical_path" | "api_endpoint";
export type ReferenceType = "table_reference" | "path_reference" | "api_endpoint_reference" | "unknown";
export type ResolutionState = "automatic" | "manual" | "unresolved";
export type ResolutionReason = "no_match" | "multiple_matches" | "conflicting_targets" | "incomplete" | "target_missing";

export interface ReferenceResolution {
  state: ResolutionState;
  reason?: ResolutionReason | null;
}

export interface Project {
  id: number;
  name: string;
  description?: string | null;
  created_at: string;
  updated_at: string;
}

export interface ProjectEnvironmentSummary {
  id: number;
  name: string;
  metadata_source_count: number;
  etl_log_path_count: number;
  code_artifact_count: number;
  created_at: string;
  updated_at: string;
}

export interface ProjectSummary extends Project {
  environment_count: number;
  metadata_source_count: number;
  etl_log_path_count: number;
  reference_mapping_count: number;
  environments: ProjectEnvironmentSummary[];
}

export interface ProjectReferenceMapping {
  id: number;
  project_id: number;
  reference_type: ReferenceType;
  reference_normalized_value: string;
  reference_signature: Record<string, unknown>;
  target_identifier_kind: TargetIdentifierKind;
  target_normalized_value: string;
  target_display_value: string;
  note?: string | null;
  created_at: string;
  updated_at: string;
}

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
  build_state: "ready" | "rebuild_required";
  published_at?: string | null;
  dataflow_row_count: number;
  job_row_count: number;
  filter_value_count: number;
  cached_source_count: number;
  active_source_count: number;
  orphan_source_count: number;
  orphan_source_ids: number[];
}

export interface StudioDiagnostics {
  workspace_database: StudioPathInfo;
  analytics_cache: StudioAnalyticsCacheInfo;
}

export type StudioCacheScope = "read_models" | "analytics" | "all_disposable";
export type StudioCacheFeature = "overview" | "assets" | "lineage" | "monitoring";

export interface StudioResultCacheStatus {
  backend: "sqlite";
  path: string;
  entries: number;
  payload_bytes: number;
  file_bytes: number;
  limits: Record<string, number>;
  memory: { entries: number; bytes: number };
}

export interface StudioCacheStatus {
  result_cache: StudioResultCacheStatus;
  analytics_cache: {
    backend: "duckdb";
    path: string;
    exists: boolean;
    file_bytes: number;
    schema_version?: number | null;
    generation?: number | null;
    build_state: "ready" | "rebuild_required";
    published_at?: string | null;
    dataflow_rows: number;
    job_rows: number;
    filter_values: number;
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

export interface Environment {
  id: number;
  project_id: number;
  name: string;
  created_at: string;
  updated_at: string;
}

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
  purpose: "project" | "metadata" | "code" | "logs";
  input_uri: string;
  canonical_uri: string;
  input_locations: Record<string, string>;
  canonical_locations: Record<string, string>;
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

export interface SourceImportItem {
  status: "created" | "existing";
  id: number;
  source_kind: string;
  uri: string;
  label?: string | null;
  record_counts: Record<string, number>;
  source_config: Record<string, unknown>;
}

export interface SourceImportResponse {
  created: SourceImportItem[];
  existing: SourceImportItem[];
  errors: Array<Record<string, unknown>>;
  summary: Record<string, number>;
}

export interface SourceValidationResult {
  source_id: number;
  source_kind: "metadata" | "logs" | string;
  status: "ok" | "warning" | "error";
  message: string;
  detected_provider?: string | null;
  detected_format?: string | null;
  record_counts?: Record<string, number>;
  records_scanned?: number;
  validated_at?: string | null;
  errors: Array<Record<string, unknown>>;
}

export type SourceReadCheckResult = SourceValidationResult;

export interface SourceSyncJob {
  id: number;
  environment_id: number;
  source_id: number;
  source_kind: "metadata" | "logs" | string;
  job_type: string;
  status: "running" | "succeeded" | "failed" | string;
  message?: string | null;
  result?: Record<string, unknown> | null;
  started_at: string;
  completed_at?: string | null;
}

export interface SourceSyncStatus {
  source_id: number;
  source_kind: "metadata" | "logs" | string;
  status: "ok" | "warning" | "error" | "unknown" | string;
  message: string;
  revision?: Record<string, unknown> | null;
  error?: Record<string, unknown> | null;
  checked_at?: string | null;
  last_observed_at?: string | null;
  next_check_at?: string | null;
  pending_changes?: boolean | null;
  observation_state: "active" | "retrying" | "paused";
  observation_failure_count: number;
  observation_paused_at?: string | null;
  active_operation?: "validate" | "sync" | null;
  latest_job?: SourceSyncJob | null;
}

export interface SourceObservationOutcome {
  source_id: number;
  source_kind: string;
  outcome: "changed" | "unchanged" | "error" | "skipped";
  pending_changes?: boolean | null;
  error?: Record<string, unknown> | null;
  started_at: string;
  completed_at: string;
  status: SourceSyncStatus;
}

export interface LocalSourceObservation {
  environment_id: number;
  total: number;
  observed: number;
  changed: number;
  skipped: number;
  failed: number;
  observed_at: string;
  outcomes: SourceObservationOutcome[];
}

export interface SourcesWorkspace {
  schema_version: "sources-workspace.v1";
  environment_id: number;
  metadata_sources: SourcePath[];
  log_sources: SourcePath[];
  code_artifacts: SourcePath[];
  statuses: SourceSyncStatus[];
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
  row_count: number;
  log_timestamp?: string | null;
  run_date?: string | null;
}

export interface SystemLogResponse {
  records: Array<Record<string, unknown>>;
  total: number;
  files: SystemLogFile[];
  errors: Array<Record<string, string>>;
}

export interface FreshnessGroup {
  status: string;
  max_source_modified_at?: string | null;
  cache_synced_at?: string | null;
  count: number;
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
    etl_logs: FreshnessGroup;
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
    job_records: number;
    total_failures: number;
    dataflow_success_rate: number;
    failed_job_windows: { last7: number; last30: number; last365: number };
    active_engines: number;
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
  backup_path: string;
  source_revision?: Record<string, unknown> | null;
  saved_revision?: Record<string, unknown> | null;
  created_at: string;
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

export interface MonitoringRecord {
  dataflow_name?: string | null;
  dataflow_id?: string | null;
  stage?: string | null;
  status?: string | null;
  duration_seconds?: number | null;
  started_at?: string | null;
  completed_at?: string | null;
  failure_category?: string | null;
  failure_tags?: string[];
  failure_rule_id?: string | null;
  [key: string]: unknown;
}

export interface JobRecord {
  job_id?: string | null;
  engine_name?: string | null;
  metadata_provider_name?: string | null;
  status?: string | null;
  duration_seconds?: number | null;
  error_message?: string | null;
  total_dataflows?: number | null;
  total_rows_read?: number | null;
  total_rows_written?: number | null;
  total_bytes_added?: number | null;
  total_bytes_removed?: number | null;
  start_time?: string | null;
  end_time?: string | null;
  [key: string]: unknown;
}

export interface NamedCount extends Record<string, unknown> {
  name?: string;
  status?: string;
  category?: string;
  count: number;
}

export interface MonitoringReport {
  summary: {
    dataflow_records: number;
    job_records: number;
    date_range: { min?: string | null; max?: string | null };
    latest_log_at?: string | null;
    latest_job_log_at?: string | null;
    latest_dataflow_log_at?: string | null;
    timezone?: string | null;
    timezone_source?: "configured" | "server_default" | string;
    requested_grain?: string | null;
    effective_grain?: string | null;
    active_engines: number;
    active_metadata_providers: number;
    log_paths: number;
  };
  health: {
    status: string;
    label: string;
    reasons: string[];
    latest_log_at?: string | null;
    latest_job_log_at?: string | null;
    latest_dataflow_log_at?: string | null;
    latest_log_age_days?: number | null;
    failed_jobs_last_3_days?: number;
    failed_jobs_last_7_days?: number;
    failed_dataflows_last_3_days?: number;
    failed_dataflows_last_7_days?: number;
    maintenance_failed_last_7_days?: number;
    maintenance_failed_last_14_days?: number;
    maintenance_skipped_last_7_days?: number;
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
    mismatch_count: number;
    checks: Array<Record<string, string | number>>;
  };
  diagnostics: {
    kpis: Record<string, string | number | null>;
    record_evidence_by_date?: Array<Record<string, string | number | null>>;
    job_linkage_summary?: Array<Record<string, string | number | null>>;
    reconciliation_by_metric?: Array<Record<string, string | number | null>>;
    field_completeness?: Array<Record<string, string | number | null>>;
    source_coverage?: Array<Record<string, string | number | boolean | null>>;
    investigation_queue?: Array<Record<string, unknown>>;
    job_id_evidence: Array<Record<string, string | number | null>>;
    read_errors: Array<Record<string, unknown>>;
  };
  metric_definitions: Record<string, { label: string; formula: string }>;
  operations: {
    kpis: Record<string, number>;
    windows?: Record<string, Record<string, number>>;
    job_duration_stats?: Record<string, number>;
    job_status_distribution: Array<{ status: string; count: number }>;
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
    dataflow_kpis: Record<string, number>;
    dataflow_duration_stats?: Record<string, number>;
    dataflow_runs_by_operation_type?: Array<Record<string, string | number>>;
    dataflow_runs_by_destination_operation_type?: Array<Record<string, string | number>>;
    phase_health?: Array<Record<string, string | number>>;
    phase_health_by_stage?: Array<Record<string, string | number>>;
    dataflow_endpoint_health?: Array<Record<string, string | number | null>>;
    dataflow_name_status_health?: Array<Record<string, string | number | null>>;
    dataflow_watermark_summary?: Array<Record<string, string | number | null>>;
    job_status_by_stage?: Array<Record<string, string | number>>;
    dataflow_status_by_stage?: Array<Record<string, string | number>>;
    status_by_stage: Array<Record<string, string | number>>;
  };
  failures: {
    kpis?: Record<string, string | number | null>;
    latest_queue?: MonitoringRecord[];
    repeated_signatures?: Array<Record<string, string | number | null>>;
    failure_by_phase?: NamedCount[];
    failure_category_phase_matrix?: Array<Record<string, string | number>>;
    endpoint_impact?: Array<Record<string, string | number | null>>;
    failed_by_stage: NamedCount[];
    failed_by_source_connection_type: NamedCount[];
    top_failing_dataflows: Array<Record<string, string | number | null>>;
    error_categories: Array<{ category: string; count: number }>;
    failure_trend_by_date: Array<{ date: string; failed: number }>;
    failed_records: MonitoringRecord[];
  };
  performance: {
    kpis?: Record<string, string | number | null>;
    duration_distribution_by_stage?: Array<Record<string, unknown>>;
    phase_contribution_by_stage_operation?: Array<Record<string, string | number>>;
    workload_efficiency_points?: Array<Record<string, string | number | null> | Array<string | number | null>>;
    slowest_dataflow_profiles?: Array<Record<string, string | number | null>>;
    runtime_context_profiles?: Array<Record<string, string | number | null>>;
    performance_trend?: Array<Record<string, string | number | null>>;
  };
  volume: {
    kpis: Record<string, number>;
    rows_by_date: Array<Record<string, string | number | null>>;
    bytes_by_date: Array<Record<string, string | number | null>>;
    volume_by_load_type: Array<Record<string, string | number>>;
    volume_by_workload_type?: Array<Record<string, string | number | null>>;
    route_volume?: Array<Record<string, string | number | null>>;
    top_dataflows_by_rows_read?: Array<Record<string, string | number | null>>;
    top_dataflows_by_est_rows_written?: Array<Record<string, string | number | null>>;
    top_dataflows_by_rows_written: Array<Record<string, string | number>>;
    top_dataflows_by_bytes_added?: Array<Record<string, string | number>>;
    top_dataflows_by_net_bytes?: Array<Record<string, string | number | null>>;
  };
  maintenance: {
    kpis: Record<string, string | number | null>;
    status_by_date?: Array<Record<string, string | number | null>>;
    reclaim_by_date?: Array<Record<string, string | number | null>>;
    table_efficiency_points?: Array<Record<string, string | number | boolean | null>>;
    table_outcome?: Array<Record<string, string | number | null>>;
    format_comparison: Array<Record<string, string | number>>;
    bytes_reclaimed_by_date: Array<Record<string, string | number>>;
  };
  freshness: {
    kpis: Record<string, number>;
    age_by_dataflow?: Array<Record<string, string | number | null>>;
    watermark_movement_by_date?: Array<Record<string, string | number | null>>;
    age_distribution?: Array<Record<string, string | number | null>>;
    watermark_coverage_by_stage?: Array<Record<string, string | number | null>>;
    skipped_streak_distribution?: Array<Record<string, string | number | null>>;
  };
  errors: Array<Record<string, unknown>>;
}

export type MonitoringPageResponse = {
  schema_version: "monitoring-page.v9";
  page: "environment-overview" | "overview" | "jobs" | "dataflows" | "failures" | "diagnostics" | "performance" | "volume" | "maintenance" | "freshness";
  summary: MonitoringReport["summary"];
} & Partial<Omit<MonitoringReport, "summary" | "metric_definitions">>;

export interface MonitoringFilterOption {
  value: string;
  label: string;
  count?: number;
}

export interface MonitoringFilterOptionsResponse {
  options: Record<string, MonitoringFilterOption[]>;
  summary: {
    source: string;
    fields: number;
  };
  errors: Array<Record<string, unknown>>;
}

export interface MonitoringOverview {
  summary: {
    records?: number;
    succeeded?: number;
    failed?: number;
    skipped?: number;
    success_rate?: number;
    log_paths?: number;
  };
  failed_dataflows: MonitoringRecord[];
  slowest_dataflows: MonitoringRecord[];
  duration_by_stage: Array<{
    stage: string;
    count: number;
    avg_duration_seconds: number;
    max_duration_seconds: number;
  }>;
  status_by_stage: Array<Record<string, string | number>>;
  errors: Array<Record<string, unknown>>;
}

export interface LatestStatusResponse {
    latest_by_id: Record<string, MonitoringRecord>;
    latest_by_name: Record<string, MonitoringRecord>;
    ambiguous_names: string[];
    errors: Array<Record<string, unknown>>;
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
  };
}
