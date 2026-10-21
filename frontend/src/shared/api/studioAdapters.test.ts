import { describe, expect, it } from "vitest";

import type {
  StudioCacheMutationResponse,
  StudioCacheStatusResponse,
  StudioDiagnosticsResponse,
} from "./contractTypes";
import { toStudioCacheMutation, toStudioCacheStatus } from "./studioCacheAdapters";
import { toStudioDiagnostics } from "./studioDiagnosticsAdapters";

function diagnosticsResponse(
  overrides: Partial<StudioDiagnosticsResponse["analytics_cache"]> = {},
): StudioDiagnosticsResponse {
  return {
    workspace_database: {
      backend: "sqlite",
      path: "./studio.db",
      exists: true,
      maintenance_supported: true,
    },
    analytics_cache: {
      scope: "studio",
      path: "./analytics.duckdb",
      exists: true,
      active_source_count: 2,
      build_state: "ready",
      cached_source_count: 2,
      dataflow_row_count: 4,
      filter_value_count: 3,
      job_row_count: 5,
      orphan_source_count: 0,
      ...overrides,
    },
  };
}

function cacheStatusResponse(): StudioCacheStatusResponse {
  return {
    result_cache: {
      backend: "sqlite",
      path: "./result-cache.db",
      entries: 4,
      payload_bytes: 128,
      file_bytes: 256,
      limits: { max_memory_entries: 10 },
      memory: { entries: 2, bytes: 64 },
    },
    analytics_cache: {
      backend: "duckdb",
      path: "./analytics.duckdb",
      exists: true,
      file_bytes: 512,
      build_state: "ready",
      dataflow_rows: 8,
      job_rows: 9,
      filter_values: 3,
      upgrade: {
        state: "completed_with_warnings",
        operation_kind: "schema_upgrade",
        attempt_count: 2,
        episode_attempt_count: 1,
        source_ids: [7],
        completed_source_ids: [7],
        source_progress: [{ source_id: 7, status: "succeeded" }],
      },
    },
    sync_job_retention: { retained: 4 },
  };
}

describe("Studio API adapters", () => {
  it("normalizes extensible diagnostics fields for the settings view", () => {
    const diagnostics = toStudioDiagnostics(
      diagnosticsResponse({
        resource_limits: {
          duckdb_threads: 4,
          duckdb_memory_limit: "1GB",
          ignored_object: { value: true },
        },
      }),
    );

    expect(diagnostics.analytics_cache.resource_limits).toEqual({
      duckdb_threads: 4,
      duckdb_memory_limit: "1GB",
    });
    expect(diagnostics.analytics_cache.orphan_source_ids).toEqual([]);
  });

  it("preserves diagnostics transport values used by settings", () => {
    const diagnostics = toStudioDiagnostics(
      diagnosticsResponse({
        size_bytes: 128,
        schema_version: 7,
        generation: 3,
        orphan_source_count: 1,
        orphan_source_ids: [99],
      }),
    );

    expect(diagnostics.workspace_database.backend).toBe("sqlite");
    expect(diagnostics.analytics_cache).toMatchObject({
      size_bytes: 128,
      schema_version: 7,
      generation: 3,
      orphan_source_count: 1,
      orphan_source_ids: [99],
    });
  });

  it("projects dynamic cache payloads into stable settings fields", () => {
    const status = toStudioCacheStatus(cacheStatusResponse());

    expect(status.result_cache).toEqual({
      backend: "sqlite",
      path: "./result-cache.db",
      entries: 4,
      payload_bytes: 128,
      file_bytes: 256,
      limits: { max_memory_entries: 10 },
      memory: { entries: 2, bytes: 64 },
    });
    expect(status.analytics_cache.upgrade).toMatchObject({
      state: "completed_with_warnings",
      source_progress: [{ source_id: 7, status: "succeeded" }],
    });
  });

  it("drops malformed dynamic upgrade details instead of leaking them to the UI", () => {
    const response = cacheStatusResponse();
    response.analytics_cache = {
      upgrade: { state: 42 },
    };

    expect(toStudioCacheStatus(response).analytics_cache.upgrade).toBeNull();
  });

  it("normalizes mutation defaults from the generated response", () => {
    const response: StudioCacheMutationResponse = { scope: "analytics" };

    expect(toStudioCacheMutation(response)).toEqual({
      scope: "analytics",
      environment_id: undefined,
      features: [],
      read_models: undefined,
      analytics: undefined,
      analytics_dependent_read_models: undefined,
    });
  });
});
