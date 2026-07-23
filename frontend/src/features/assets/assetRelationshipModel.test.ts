import { describe, expect, it } from "vitest";
import type { AssetBrief, AssetDetailResponse, AssetFlow } from "../../shared/api/types";
import { assetRelationshipGroups } from "./assetRelationshipModel";

const upstream = brief("asset:upstream", "raw.orders");
const downstream = brief("asset:downstream", "gold.orders");

describe("assetRelationshipGroups", () => {
  it("groups an upstream asset with the dataflow and dependency that establish it", () => {
    const detail = baseDetail();
    detail.upstream_assets = [{ asset: upstream, relation_flow_count: 1, relation_dependency_count: 1, relation_kinds: ["dataflow", "dependency"] }];
    detail.input_flows = [flow("flow:input", upstream)];
    detail.depends_on = [{
      id: "dependency:input",
      kind: "reads",
      provenance: "sql",
      resolution: { state: "automatic" },
      resolution_method: "canonical_identity",
      reference_id: "reference:orders",
      resolved_asset_id: upstream.id,
      resolved_asset: upstream,
    }];

    const groups = assetRelationshipGroups(detail, "upstream");

    expect(groups).toHaveLength(1);
    expect(groups[0].asset.id).toBe(upstream.id);
    expect(groups[0].via.map((item) => item.kind)).toEqual(["dataflow", "dependency"]);
  });

  it("groups downstream relations by the target asset and preserves reference navigation", () => {
    const detail = baseDetail();
    detail.output_flows = [flow("flow:output", downstream)];
    detail.used_by = [{
      id: "dependency:output",
      kind: "reads",
      provenance: "python",
      resolution: { state: "automatic" },
      resolution_method: "canonical_identity",
      target_asset: downstream,
      reference: { id: "reference:downstream", display_name: "orders", reference_type: "table", resolution: { state: "automatic" } },
    }];

    const groups = assetRelationshipGroups(detail, "downstream");

    expect(groups).toHaveLength(1);
    expect(groups[0].asset.id).toBe(downstream.id);
    expect(groups[0].via).toHaveLength(2);
    expect(groups[0].via[1]).toMatchObject({ kind: "dependency", referenceId: "reference:downstream" });
  });
});

function brief(id: string, name: string): AssetBrief {
  return { id, display_name: name, friendly_name: name, asset_type: "table", connection_name: "lake", attention_count: 0 };
}

function flow(id: string, counterpart: AssetBrief): AssetFlow {
  return { id, dataflow_id: id, name: "load orders", stage: "silver", load_type: "merge", counterpart };
}

function baseDetail(): AssetDetailResponse {
  return {
    asset: {} as AssetDetailResponse["asset"],
    attention_items: [],
    direct_relationships: {} as AssetDetailResponse["direct_relationships"],
    upstream_assets: [],
    downstream_assets: [],
    input_flows: [],
    output_flows: [],
    depends_on: [],
    used_by: [],
  };
}
