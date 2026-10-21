import { describe, expect, it } from "vitest";
import {
  toAssetDetailResponse,
  toAssetInventoryResponse,
  toAssetSourceResponse,
} from "./assetAdapters";
import type {
  AssetDetailResponseTransport,
  AssetInventoryResponseTransport,
  AssetSourceResponseTransport,
} from "./contractTypes";

describe("asset transport adapters", () => {
  it("normalizes optional inventory collections and filter values", () => {
    const response = toAssetInventoryResponse({
      summary: { assets: 1 },
      items: [{
        id: "asset:orders",
        display_name: "orders",
        friendly_name: "Orders",
        full_identity: "lake.orders",
        asset_type: "table",
        attention_count: 0,
        depends_on_count: 0,
        downstream_count: 0,
        input_dataflow_count: 0,
        output_dataflow_count: 0,
        upstream_count: 0,
        used_by_count: 0,
        metadata_source_count: 1,
        identifier_count: 0,
        observation_count: 0,
      }],
      filter_options: { connections: ["lake"], asset_types: ["table"] },
      catalog_version: "catalog-1",
    } as unknown as AssetInventoryResponseTransport);

    expect(response.items[0]).toMatchObject({
      id: "asset:orders",
      roles: [],
      metadata_source_ids: [],
      attention_count: 0,
    });
    expect(response.filter_options).toEqual({
      connections: ["lake"],
      formats: [],
      asset_types: ["table"],
      roles: [],
      attention_states: [],
    });
  });

  it("projects untyped detail relationships into stable UI records", () => {
    const response = toAssetDetailResponse({
      asset: {
        id: "asset:orders",
        display_name: "orders",
        friendly_name: "Orders",
        full_identity: "lake.orders",
        asset_type: "table",
        attention_count: 0,
      },
      attention_items: [],
      direct_relationships: { position: "entry", upstream_assets: 0 },
      upstream_assets: [],
      downstream_assets: [{
        asset: { id: "asset:customers", display_name: "customers", asset_type: "table" },
        relation_flow_count: 1,
      }],
      input_flows: [],
      output_flows: [{
        id: "flow-1",
        dataflow_id: "df-1",
        name: "orders_to_customers",
        counterpart: { id: "asset:customers", display_name: "customers", asset_type: "table" },
      }],
      depends_on: [],
      used_by: [],
    } as unknown as AssetDetailResponseTransport);

    expect(response.direct_relationships.position).toBe("entry");
    expect(response.downstream_assets[0].asset.friendly_name).toBe("customers");
    expect(response.output_flows[0].counterpart.id).toBe("asset:customers");
  });

  it("keeps source definition fields at the asset boundary", () => {
    const response = toAssetSourceResponse({
      catalog_version: "catalog-2",
      definition: {
        kind: "sql_query",
        status: "available",
        line_count: 2,
        raw: "SELECT 1",
        diagnostics: [{ severity: "warning", code: "W1", message: "check" }],
      },
    } as unknown as AssetSourceResponseTransport);

    expect(response).toEqual({
      catalog_version: "catalog-2",
      definition: expect.objectContaining({
        kind: "sql_query",
        status: "available",
        line_count: 2,
        raw: "SELECT 1",
      }),
    });
    expect(response.definition.diagnostics).toEqual([
      { severity: "warning", code: "W1", message: "check", details: undefined },
    ]);
  });
});
