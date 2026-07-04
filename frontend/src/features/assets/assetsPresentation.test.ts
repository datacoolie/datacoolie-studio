import { describe, expect, it } from "vitest";
import type { AssetInventoryItem } from "../../shared/api/types";
import { assetSearchValues, metadataQueryForAsset, presentAsset } from "./assetsPresentation";

describe("assets presentation", () => {
  const asset: AssetInventoryItem = {
    id: "asset:abc",
    display_name: "orders",
    friendly_name: "orders",
    full_identity: "lake · main.warehouse.sales.orders",
    kind: "table",
    format: "delta",
    connection_name: "lake",
    connection_type: "lakehouse",
    catalog: "main",
    database: "warehouse",
    schema_name: "sales",
    table: "orders",
    path: null,
    query: null,
    python_function: null,
    declaration_status: "declared",
    roles: ["source", "destination"],
    metadata_source_ids: [1],
    metadata_sources: [{ id: 1, uri: "metadata.json" }],
    upstream_count: 1,
    downstream_count: 2,
    input_dataflow_count: 1,
    output_dataflow_count: 2,
    dependency_count: 0,
    issue_count: 0,
    issues: [],
    identifiers: [],
    observations: [],
  };

  it("uses lineage icon presentation while keeping asset identity", () => {
    const presented = presentAsset(asset);
    expect(presented.friendlyName).toBe("orders");
    expect(presented.fullIdentity).toContain("lake");
    expect(presented.iconKind).toBe("delta");
  });

  it("collects searchable values", () => {
    const values = assetSearchValues(asset);
    expect(values).toContain("asset:abc");
    expect(values).toContain("main.warehouse.sales.orders");
    expect(values).toContain("metadata.json");
  });

  it("chooses best metadata query", () => {
    expect(metadataQueryForAsset(asset)).toBe("orders");
  });
});
