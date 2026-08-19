import { describe, expect, it } from "vitest";
import type { AssetInventoryItem, AssetReferenceGroupItem } from "../../shared/api/domainTypes";
import { orderAssetsByConnection, orderReferencesByAction, startsConnectionGroup, startsReferenceResolutionGroup } from "./assetsOrdering";

function asset(id: string, connection_name: string, display_name: string): AssetInventoryItem {
  return {
    id,
    connection_name,
    display_name,
    friendly_name: display_name,
    full_identity: `${connection_name}.${display_name}`,
    asset_type: "table",
    roles: [],
    metadata_source_ids: [],
    upstream_count: 0,
    downstream_count: 0,
    input_dataflow_count: 0,
    output_dataflow_count: 0,
    depends_on_count: 0,
    used_by_count: 0,
    attention_count: 0,
    identifier_count: 0,
    observation_count: 0,
    metadata_source_count: 0,
  };
}

function reference(id: string, state: "unresolved" | "manual" | "automatic", attention_count = 0): AssetReferenceGroupItem {
  return {
    id,
    reference_type: "table_reference",
    normalized_value: id,
    display_name: id,
    resolution: { state, reason: state === "unresolved" ? "no_match" : null },
    resolved_asset_ids: [],
    candidate_asset_ids: [],
    candidate_assets: [],
    occurrence_ids: [],
    consumer_asset_ids: [],
    consumer_assets: [],
    provenances: [],
    dependency_count: 0,
    dataflow_ids: [],
    attention_count,
    attention_items: [],
    observations: [],
  };
}

describe("assetsOrdering", () => {
  it("orders Inventory by connection family, prefix convention, and name", () => {
    const rows = orderAssetsByConnection([
      asset("3", "silver_delta", "Orders"),
      asset("2", "bronze_parquet", "Orders"),
      asset("4", "analytics", "Orders"),
      asset("1", "wwi_sqlserver", "Customers"),
    ]);
    expect(rows.map((row) => `${row.connection_name}:${row.display_name}`)).toEqual([
      "bronze_parquet:Orders",
      "silver_delta:Orders",
      "analytics:Orders",
      "wwi_sqlserver:Customers",
    ]);
  });

  it("orders References by actionability before name", () => {
    const rows = orderReferencesByAction([
      reference("automatic", "automatic"),
      reference("manual", "manual"),
      reference("unresolved-z", "unresolved"),
      reference("unresolved-a", "unresolved", 1),
    ]);
    expect(rows.map((row) => row.id)).toEqual(["unresolved-a", "unresolved-z", "manual", "automatic"]);
  });

  it("marks only the first row of each recommended group", () => {
    const assets = orderAssetsByConnection([asset("2", "bronze_parquet", "B"), asset("1", "bronze_parquet", "A"), asset("3", "silver_delta", "A")]);
    expect(assets.map((row, index) => startsConnectionGroup(row, assets[index - 1]))).toEqual([true, false, true]);

    const references = orderReferencesByAction([reference("manual", "manual"), reference("unresolved", "unresolved"), reference("automatic", "automatic")]);
    expect(references.map((row, index) => startsReferenceResolutionGroup(row, references[index - 1]))).toEqual([true, true, true]);
  });
});
