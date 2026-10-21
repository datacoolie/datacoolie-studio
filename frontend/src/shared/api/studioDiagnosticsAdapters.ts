import type { StudioDiagnosticsResponse } from "./contractTypes";
import type { StudioAnalyticsCacheInfo, StudioDiagnostics } from "./domainTypes";

type ResourceLimitValue = string | number;

/**
 * Convert generated Studio diagnostics transport into the settings view model.
 * The API keeps resource limits extensible; the settings UI consumes scalar values only.
 */
export function toStudioDiagnostics(
  response: StudioDiagnosticsResponse,
): StudioDiagnostics {
  return {
    workspace_database: response.workspace_database,
    analytics_cache: toAnalyticsCacheInfo(response.analytics_cache),
  };
}

function toAnalyticsCacheInfo(
  cache: StudioDiagnosticsResponse["analytics_cache"],
): StudioAnalyticsCacheInfo {
  return {
    scope: cache.scope,
    path: cache.path,
    exists: cache.exists,
    size_bytes: cache.size_bytes,
    schema_version: cache.schema_version,
    generation: cache.generation,
    build_state: cache.build_state,
    published_at: cache.published_at,
    dataflow_row_count: cache.dataflow_row_count,
    job_row_count: cache.job_row_count,
    filter_value_count: cache.filter_value_count,
    cached_source_count: cache.cached_source_count,
    active_source_count: cache.active_source_count,
    orphan_source_count: cache.orphan_source_count,
    orphan_source_ids: cache.orphan_source_ids ?? [],
    resource_limits: toResourceLimits(cache.resource_limits),
  };
}

function toResourceLimits(
  resourceLimits: Record<string, unknown> | undefined,
): Record<string, ResourceLimitValue> {
  return Object.fromEntries(
    Object.entries(resourceLimits ?? {}).filter((entry): entry is [string, ResourceLimitValue] => {
      const value = entry[1];
      return typeof value === "string" || typeof value === "number";
    }),
  );
}
