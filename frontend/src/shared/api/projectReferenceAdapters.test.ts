import { describe, expect, it } from "vitest";
import { toProjectReferenceRegistryResponse } from "./projectReferenceAdapters";
import type { ProjectReferenceRegistryResponseTransport } from "./contractTypes";

describe("project reference registry transport adapter", () => {
  it("fills optional generated collections before the registry reaches the UI", () => {
    const response = toProjectReferenceRegistryResponse({
      project_id: 3,
      mappings: [],
      targets: [{
        id: "target:orders",
        asset_id: "asset:orders",
        asset_type: "table",
        connection_name: "lake",
        display_name: "orders",
        kind: "logical_table",
        value: "lake.orders",
        display: "lake.orders",
      }],
      rows: [{
        id: "ref:orders",
        reference_type: "table_reference",
        normalized_value: "lake.orders",
        resolution: { state: "automatic" },
        target_coverage: { available: 1, total: 1 },
      }],
      failures: [],
    } as unknown as ProjectReferenceRegistryResponseTransport);

    expect(response.targets[0]).toMatchObject({
      asset_ids: [],
      environment_ids: [],
      environment_names: [],
    });
    expect(response.rows[0]).toMatchObject({
      environments: [],
      candidate_asset_ids: [],
      resolved_asset_ids: [],
      observed_targets: [],
      target_coverage: {
        available_environment_names: [],
        missing_environment_names: [],
      },
    });
  });
});
