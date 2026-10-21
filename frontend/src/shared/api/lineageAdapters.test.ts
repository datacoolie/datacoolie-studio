import { describe, expect, it } from "vitest";

import type { LineageResponseTransport } from "./contractTypes";
import { toLineageResponse } from "./lineageAdapters";

function lineageResponse(): LineageResponseTransport {
  return {
    schema_version: "lineage.v4",
    summary: {
      assets: 1,
      references: 1,
      dataflows: 1,
      dependencies: 1,
      stitched_assets: 1,
      declared_assets: 1,
      automatic_references: 1,
      manual_references: 0,
      unresolved_references: 0,
      automatic_dependencies: 1,
      manual_dependencies: 0,
      unresolved_dependencies: 0,
      diagnostics: 0,
    },
    assets: [{
      id: "asset:orders",
      entity_type: "asset",
      asset_type: "table",
      declaration_status: "declared",
      display_name: "Orders",
      label: "Orders",
      mapping_target: { kind: "logical_table", value: "orders", display: "orders" },
    }],
    references: [{
      id: "reference:orders",
      entity_type: "reference",
      reference_type: "table_reference",
      display_name: "orders",
      normalized_value: "orders",
      resolution: { state: "automatic" },
      occurrence_count: 0,
      dependency_count: 0,
      manual_mapping: { mapping_id: 7, target_normalized_value: "orders" },
    }],
    dataflows: [{
      id: "flow:orders",
      dataflow_id: "orders",
      name: "Orders",
      source_asset_id: "asset:raw-orders",
      destination_asset_id: "asset:orders",
      metadata_source_id: 3,
      metadata_source_uri: "./metadata.json",
    }],
    dependencies: [{
      id: "dependency:orders",
      target_asset_id: "asset:raw-orders",
      consumer_asset_id: "asset:orders",
      kind: "reads",
      provenance: "sql",
      resolution: { state: "automatic" },
      resolution_method: "exact",
      reference_id: "reference:orders",
      reference_occurrence_id: "occurrence:orders",
    }],
  };
}

describe("lineage API adapters", () => {
  it("normalizes generated graph collections into the lineage view model", () => {
    const lineage = toLineageResponse(lineageResponse());

    expect(lineage.assets[0]).toMatchObject({
      metadata_source_ids: [],
      roles: [],
      observations: [],
      identifiers: [],
      mapping_target: { kind: "logical_table", value: "orders", display: "orders" },
    });
    expect(lineage.references[0]).toMatchObject({
      resolved_asset_ids: [],
      candidate_asset_ids: [],
      occurrence_count: 0,
      consumer_asset_ids: [],
      provenances: [],
      manual_mapping: { mapping_id: 7, target_normalized_value: "orders" },
    });
  });

  it("does not expose incomplete manual mapping records to the UI", () => {
    const response = lineageResponse();
    response.references[0].manual_mapping = { target_normalized_value: "orders" };

    expect(toLineageResponse(response).references[0].manual_mapping).toBeNull();
  });
});
