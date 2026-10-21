import type {
  SourceRead,
  SourceValidationResult,
  SourceSyncStatus,
  SourcesWorkspaceResponse,
} from "./contractTypes";
import type {
  SourcePath,
  SourcesWorkspace,
} from "./domainTypes";

/**
 * Convert generated source responses into the normalized source view model.
 * The API owns transport shape; this adapter owns UI defaults.
 */
export function toSourcePath(source: SourceRead): SourcePath {
  return {
    id: source.id,
    environment_id: source.environment_id,
    uri: source.uri,
    label: source.label,
    enabled: source.enabled,
    sync_schedule_enabled:
      "sync_schedule_enabled" in source ? source.sync_schedule_enabled : false,
    sync_interval_minutes:
      "sync_interval_minutes" in source ? source.sync_interval_minutes : null,
    last_scheduled_sync_at:
      "last_scheduled_sync_at" in source ? source.last_scheduled_sync_at : null,
    source_config: source.source_config ?? null,
    storage: {
      provider: source.storage.provider,
      auth_mode: source.storage.auth_mode,
      credential_profile_id: source.storage.credential_profile_id,
      options: source.storage.options ?? {},
    },
    configured_location: source.configured_location
      ? {
          registration_id: source.configured_location.registration_id,
          purpose: source.configured_location.purpose,
          input_uri: source.configured_location.input_uri,
          canonical_uri: source.configured_location.canonical_uri,
          input_locations: source.configured_location.input_locations ?? {},
          canonical_locations: source.configured_location.canonical_locations ?? {},
        }
      : null,
    created_at: source.created_at,
    latest_validation: source.latest_validation
      ? toValidationResult(source.latest_validation)
      : null,
  };
}

export function toSourcePaths(sources: SourceRead[]): SourcePath[] {
  return sources.map(toSourcePath);
}

export function toSourcesWorkspace(
  workspace: SourcesWorkspaceResponse,
): SourcesWorkspace {
  return {
    schema_version: workspace.schema_version,
    environment_id: workspace.environment_id,
    metadata_sources: toSourcePaths(workspace.metadata_sources),
    sql_sources: toSourcePaths(workspace.sql_sources),
    log_sources: toSourcePaths(workspace.log_sources),
    code_artifacts: toSourcePaths(workspace.code_artifacts),
    statuses: workspace.statuses as SourceSyncStatus[],
    earliest_cloud_due_at: workspace.earliest_cloud_due_at,
    dependency_version: workspace.dependency_version,
  };
}

function toValidationResult(
  validation: NonNullable<SourceRead["latest_validation"]>,
): SourceValidationResult {
  return {
    source_id: validation.source_id,
    source_kind: validation.source_kind,
    status: validation.status as SourceValidationResult["status"],
    message: validation.message,
    detected_provider: validation.detected_provider,
    detected_format: validation.detected_format,
    metadata_write_back_supported: validation.metadata_write_back_supported,
    record_counts: validation.record_counts,
    records_scanned: validation.records_scanned,
    validated_at: validation.validated_at,
    errors: validation.errors,
  };
}
