import { describe, expect, it } from "vitest";

import type { MetadataSourceReadTransport } from "./contractTypes";
import { toSourcePath, toSourcesWorkspace } from "./sourceAdapters";

type MetadataSourceRead = MetadataSourceReadTransport;

function metadataSource(
  overrides: Partial<MetadataSourceRead> = {},
): MetadataSourceRead {
  return {
    id: 7,
    environment_id: 3,
    uri: "./metadata.json",
    enabled: true,
    created_at: "2026-09-16T00:00:00Z",
    storage: {
      provider: "local",
      auth_mode: "none",
    },
    ...overrides,
  };
}

describe("source API adapters", () => {
  it("normalizes source-specific transport fields into the shared view model", () => {
    const source = toSourcePath(
      metadataSource({
        storage: {
          provider: "local",
          auth_mode: "none",
          options: undefined,
        },
        latest_validation: {
          source_id: 7,
          source_kind: "metadata",
          status: "warning",
          message: "Metadata contains warnings",
          record_counts: {},
          records_scanned: 2,
          errors: [],
        },
      }),
    );

    expect(source.sync_schedule_enabled).toBe(false);
    expect(source.sync_interval_minutes).toBeNull();
    expect(source.storage?.options).toEqual({});
    expect(source.latest_validation?.status).toBe("warning");
  });

  it("adapts every source collection in the workspace response", () => {
    const source = metadataSource();
    const workspace = toSourcesWorkspace({
      schema_version: "sources-workspace.v1",
      environment_id: 3,
      metadata_sources: [source],
      sql_sources: [],
      log_sources: [],
      code_artifacts: [],
      statuses: [],
      dependency_version: "v1",
    });

    expect(workspace.metadata_sources).toHaveLength(1);
    expect(workspace.metadata_sources[0].id).toBe(7);
    expect(workspace.sql_sources).toEqual([]);
  });
});
