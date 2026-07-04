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
  environments: ProjectEnvironmentSummary[];
}

export interface StudioPathInfo {
  path: string;
  exists: boolean;
  size_bytes?: number | null;
}

export interface StudioAnalyticsCacheInfo {
  scope: "studio";
  path: string;
  exists: boolean;
  size_bytes?: number | null;
  dataflow_row_count: number;
  job_row_count: number;
  filter_value_count: number;
  cached_source_count: number;
  active_source_count: number;
  orphan_source_count: number;
  orphan_source_ids: number[];
}

export interface StudioStorageInfo {
  workspace_database: StudioPathInfo;
  analytics_cache: StudioAnalyticsCacheInfo;
}

export interface StudioSettings {
  timezone: string;
  timezone_source: "configured" | "server_default";
  updated_at?: string | null;
  storage: StudioStorageInfo;
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
  created_at: string;
  latest_validation?: SourceValidationResult | null;
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
  latest_job?: SourceSyncJob | null;
}

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

export interface FreshnessSourceItem {
  source_id: number;
  source_kind: "metadata" | "logs" | string;
  label?: string | null;
  uri: string;
  status: string;
  source_modified_at?: string | null;
  cache_synced_at?: string | null;
  cache_source_modified_at?: string | null;
  revision?: Record<string, unknown> | null;
  cache_revision?: Record<string, unknown> | null;
  message: string;
}

export interface FreshnessGroup {
  status: string;
  max_source_modified_at?: string | null;
  cache_synced_at?: string | null;
  count: number;
}

export interface EnvironmentFreshness {
  environment_id: number;
  status: string;
  message: string;
  max_source_modified_at?: string | null;
  metadata_source_count: number;
  etl_log_path_count: number;
  metadata: FreshnessGroup;
  etl_logs: FreshnessGroup;
  items: FreshnessSourceItem[];
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
  label: string;
  kind: "table" | "path" | "sql_query" | "python_function" | "api" | "unresolved";
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
  observations?: Array<Record<string, unknown>>;
  identifiers?: Array<Record<string, unknown>>;
}

export interface LineageReference {
  id: string;
  kind: "table_reference" | "path_reference" | "dynamic_expression" | "unknown";
  display_name: string;
  resolution_status: "ambiguous" | "unresolved";
  raw_value: string;
  provenance: "sql" | "python" | "python_sql";
  target_asset_id: string;
  candidate_asset_ids: string[];
  reason_code: string;
  observations: Array<Record<string, unknown>>;
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
  source: {
    entity_type: "asset" | "reference";
    id: string;
  };
  target_asset_id: string;
  kind: "reads" | "uses";
  provenance: "sql" | "python" | "python_sql";
  resolution_status: "resolved" | "discovered_only" | "ambiguous" | "unresolved";
  resolution_method: string;
  observations: Array<Record<string, unknown>>;
}

export interface LineageDiagnostic {
  severity: "info" | "warning" | "error";
  code: string;
  message: string;
  asset_id?: string;
  dataflow_id?: string;
  metadata_source_id?: number;
  details?: Record<string, unknown>;
}

export interface LineageSummary {
  assets: number;
  references: number;
  dataflows: number;
  dependencies: number;
  stitched_assets: number;
  declared_assets: number;
  discovered_only_assets: number;
  resolved_dependencies: number;
  discovered_only_dependencies: number;
  ambiguous_dependencies: number;
  unresolved_dependencies: number;
  diagnostics: number;
}

export interface LineageResponse {
  schema_version: "lineage.v2";
  summary: LineageSummary;
  assets: LineageAsset[];
  references: LineageReference[];
  dataflows: LineageDataflow[];
  dependencies: LineageDependency[];
  diagnostics: LineageDiagnostic[];
}

export interface AssetSummary {
  assets: number;
  declared: number;
  discovered_only: number;
  stitched: number;
  with_issues: number;
}

export interface AssetMetadataSource {
  id: number;
  uri: string;
}

export interface AssetIssue {
  severity: "info" | "warning" | "error";
  code: string;
  message: string;
  dataflow_id?: string | null;
  metadata_source_id?: number | null;
  reference_id?: string | null;
  details: Record<string, unknown>;
}

export interface AssetFilterOptions {
  connections: string[];
  formats: string[];
  kinds: string[];
  roles: string[];
  declaration_statuses: Array<"declared" | "discovered_only" | string>;
  issue_states: string[];
}

export interface AssetInventoryItem {
  id: string;
  display_name: string;
  friendly_name: string;
  full_identity: string;
  kind: "table" | "path" | "sql_query" | "python_function" | "api" | "unresolved";
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
  declaration_status: "declared" | "discovered_only";
  roles: string[];
  metadata_source_ids: number[];
  metadata_sources: AssetMetadataSource[];
  upstream_count: number;
  downstream_count: number;
  input_dataflow_count: number;
  output_dataflow_count: number;
  dependency_count: number;
  issue_count: number;
  issues: AssetIssue[];
  identifiers: Array<Record<string, unknown>>;
  observations: Array<Record<string, unknown>>;
}

export interface AssetsResponse {
  summary: AssetSummary;
  assets: AssetInventoryItem[];
  filter_options: AssetFilterOptions;
  diagnostics: LineageDiagnostic[];
}

export interface AssetDetailResponse {
  asset: AssetInventoryItem;
  diagnostics: AssetIssue[];
}

export interface MonitoringRecord {
  dataflow_name?: string | null;
  dataflow_id?: string | null;
  stage?: string | null;
  status?: string | null;
  duration_seconds?: number | null;
  started_at?: string | null;
  completed_at?: string | null;
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
    dataflow_duration_by_operation_type?: Array<Record<string, unknown>>;
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
    latest_queue?: Array<Record<string, string | number | null>>;
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
    workload_efficiency_points?: Array<Record<string, string | number | null>>;
    slowest_dataflow_profiles?: Array<Record<string, string | number | null>>;
    runtime_context_profiles?: Array<Record<string, string | number | null>>;
    investigation_queue?: MonitoringRecord[];
    duration_breakdown: Array<Record<string, string | number>>;
    duration_vs_rows: Array<Record<string, string | number>>;
    slowest_dataflows: MonitoringRecord[];
    slowest_dataflows_by_p95?: Array<Record<string, string | number | null>>;
    overview_p95_duration_seconds?: number;
    duration_by_stage: Array<Record<string, string | number>>;
    engine_stage_matrix: Array<Record<string, string | number>>;
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
    investigation_queue?: MonitoringRecord[];
  };
  maintenance: {
    kpis: Record<string, string | number | null>;
    status_by_date?: Array<Record<string, string | number | null>>;
    reclaim_by_date?: Array<Record<string, string | number | null>>;
    operation_health?: Array<Record<string, string | number | null>>;
    table_registry?: Array<Record<string, string | number | boolean | null>>;
    table_attention?: Array<Record<string, string | number | boolean | null>>;
    table_efficiency_points?: Array<Record<string, string | number | boolean | null>>;
    table_outcome?: Array<Record<string, string | number | null>>;
    efficiency_points?: Array<Record<string, string | number | null>>;
    investigation_queue?: MonitoringRecord[];
    bytes_reclaimed_by_table: Array<Record<string, string | number>>;
    format_comparison: Array<Record<string, string | number>>;
    per_table: Array<Record<string, string | number | null>>;
    duration_vs_files_removed: Array<Record<string, string | number>>;
    bytes_reclaimed_by_date: Array<Record<string, string | number>>;
  };
  freshness: {
    kpis: Record<string, number>;
    latest_freshness_by_dataflow: Array<Record<string, string | number | null>>;
    watermark_movement: Array<Record<string, string | number | null>>;
    watermark_movement_by_date?: Array<Record<string, string | number | null>>;
    age_distribution?: Array<Record<string, string | number | null>>;
    watermark_coverage_by_stage?: Array<Record<string, string | number | null>>;
    skipped_streak_distribution?: Array<Record<string, string | number | null>>;
    dataflow_registry?: Array<Record<string, string | number | null>>;
    stale_candidates: Array<Record<string, string | number | null>>;
    skipped_patterns: Array<Record<string, string | number | null>>;
  };
  errors: Array<Record<string, unknown>>;
}

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
  latest: Record<string, MonitoringRecord>;
  latest_by_id?: Record<string, MonitoringRecord>;
  latest_by_name?: Record<string, MonitoringRecord>;
  ambiguous_names?: string[];
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
