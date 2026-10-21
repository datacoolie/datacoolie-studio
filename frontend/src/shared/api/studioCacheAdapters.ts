import type {
  AnalyticsUpgradeStatusResponse,
  StudioCacheMutationResponse,
  StudioCacheStatusResponse,
} from "./contractTypes";
import type {
  AnalyticsUpgradeSourceProgress,
  AnalyticsUpgradeStatus,
  StudioCacheMutation,
  StudioCacheStatus,
  StudioResultCacheStatus,
} from "./domainTypes";
import {
  asRecord,
  booleanValue,
  nullableBoolean,
  nullableNumber,
  nullableString,
  numberArray,
  numberValue,
  stringValue,
  type JsonRecord,
} from "./transportValues";

/**
 * Project the extensible Studio cache transport into the settings view model.
 * Cache status is intentionally map-shaped in the API because the stores expose
 * backend-specific diagnostics; normalization keeps that variability out of React.
 */
export function toStudioCacheStatus(
  response: StudioCacheStatusResponse,
): StudioCacheStatus {
  const analyticsCache = asRecord(response.analytics_cache);
  return {
    result_cache: toStudioResultCacheStatus(response.result_cache),
    analytics_cache: {
      backend: stringValue(analyticsCache.backend, "duckdb"),
      path: stringValue(analyticsCache.path),
      exists: booleanValue(analyticsCache.exists),
      file_bytes: numberValue(analyticsCache.file_bytes),
      schema_version: nullableNumber(analyticsCache.schema_version),
      generation: nullableNumber(analyticsCache.generation),
      build_state: stringValue(analyticsCache.build_state, "rebuild_required"),
      published_at: nullableString(analyticsCache.published_at),
      dataflow_rows: numberValue(analyticsCache.dataflow_rows),
      job_rows: numberValue(analyticsCache.job_rows),
      filter_values: numberValue(analyticsCache.filter_values),
      upgrade: toAnalyticsUpgradeStatusValue(analyticsCache.upgrade),
    },
    sync_job_retention: response.sync_job_retention ?? null,
  };
}

export function toStudioCacheMutation(
  response: StudioCacheMutationResponse,
): StudioCacheMutation {
  return {
    scope: response.scope,
    environment_id: response.environment_id,
    features: response.features ?? [],
    read_models: response.read_models,
    analytics: response.analytics,
    analytics_dependent_read_models: response.analytics_dependent_read_models,
  };
}

export function toAnalyticsUpgradeStatus(
  response: AnalyticsUpgradeStatusResponse,
): AnalyticsUpgradeStatus {
  return projectAnalyticsUpgradeStatus(response);
}

function toStudioResultCacheStatus(value: unknown): StudioResultCacheStatus {
  const cache = asRecord(value);
  const memory = asRecord(cache.memory);
  return {
    backend: stringValue(cache.backend, "sqlite"),
    path: stringValue(cache.path),
    entries: numberValue(cache.entries),
    payload_bytes: numberValue(cache.payload_bytes),
    file_bytes: numberValue(cache.file_bytes),
    limits: toNumberRecord(cache.limits),
    memory: {
      entries: numberValue(memory.entries),
      bytes: numberValue(memory.bytes),
    },
  };
}

function toAnalyticsUpgradeStatusValue(value: unknown): AnalyticsUpgradeStatus | null {
  const record = asRecord(value);
  if (typeof record.state !== "string") return null;
  return projectAnalyticsUpgradeStatus(record);
}

function projectAnalyticsUpgradeStatus(
  response: AnalyticsUpgradeStatusResponse | JsonRecord,
): AnalyticsUpgradeStatus {
  const record = asRecord(response);
  return {
    state: stringValue(record.state),
    operation_kind: stringValue(record.operation_kind),
    reason_code: nullableString(record.reason_code),
    source_schema_version: nullableNumber(record.source_schema_version),
    target_schema_version: nullableNumber(record.target_schema_version),
    source_ids: numberArray(record.source_ids),
    completed_source_ids: numberArray(record.completed_source_ids),
    attempt_count: numberValue(record.attempt_count),
    episode_attempt_count: numberValue(record.episode_attempt_count),
    error_code: nullableString(record.error_code),
    error_message: nullableString(record.error_message),
    next_retry_at: nullableString(record.next_retry_at),
    started_at: nullableString(record.started_at),
    completed_at: nullableString(record.completed_at),
    updated_at: nullableString(record.updated_at),
    duration_seconds: nullableNumber(record.duration_seconds),
    source_progress: Array.isArray(record.source_progress)
      ? record.source_progress
          .map(toAnalyticsUpgradeSourceProgress)
          .filter((source): source is AnalyticsUpgradeSourceProgress => source !== null)
      : [],
  };
}

function toAnalyticsUpgradeSourceProgress(
  value: unknown,
): AnalyticsUpgradeSourceProgress | null {
  const record = asRecord(value);
  if (typeof record.source_id !== "number" || typeof record.status !== "string") {
    return null;
  }
  return {
    source_id: record.source_id,
    project_id: nullableNumber(record.project_id),
    project_name: nullableString(record.project_name),
    environment_id: nullableNumber(record.environment_id),
    environment_name: nullableString(record.environment_name),
    source_kind: nullableString(record.source_kind),
    label: nullableString(record.label),
    uri: nullableString(record.uri),
    is_registered: nullableBoolean(record.is_registered),
    status: record.status,
    message: nullableString(record.message),
    started_at: nullableString(record.started_at),
    completed_at: nullableString(record.completed_at),
    duration_seconds: nullableNumber(record.duration_seconds),
  };
}

function toNumberRecord(value: unknown): Record<string, number> {
  return Object.fromEntries(
    Object.entries(asRecord(value)).filter((entry): entry is [string, number] => {
      const item = entry[1];
      return typeof item === "number" && Number.isFinite(item);
    }),
  );
}
