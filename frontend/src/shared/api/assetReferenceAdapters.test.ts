import { describe, expect, it } from "vitest";
import {
  toAssetReferenceDetailResponse,
  toAssetReferenceListResponse,
  toReferenceOccurrenceSourceResponse,
} from "./assetReferenceAdapters";
import type {
  AssetReferenceDetailResponseTransport,
  AssetReferenceListResponseTransport,
  ReferenceOccurrenceSourceResponseTransport,
} from "./contractTypes";

describe("asset reference transport adapters", () => {
  it("normalizes reference groups and nested asset briefs", () => {
    const response = toAssetReferenceListResponse({
      catalog_version: "catalog-1",
      filter_options: { reference_types: ["table_reference"] },
      items: [{
        id: "ref:orders",
        reference_type: "table_reference",
        normalized_value: "lake.orders",
        display_name: "lake.orders",
        resolution: { state: "automatic", reason: null },
        resolved_asset_id: "asset:orders",
        resolved_asset_ids: ["asset:orders"],
        resolved_asset: { id: "asset:orders", display_name: "orders", asset_type: "table" },
        candidate_asset_ids: [],
        candidate_assets: [],
        occurrence_ids: ["occ-1"],
        consumer_asset_ids: [],
        consumer_assets: [],
        dependency_count: 1,
        attention_count: 0,
      }],
    } as unknown as AssetReferenceListResponseTransport);

    expect(response.items[0]).toMatchObject({
      id: "ref:orders",
      resolved_asset: { id: "asset:orders", friendly_name: "orders" },
      occurrence_ids: ["occ-1"],
      provenances: [],
      attention_items: [],
    });
    expect(response.filter_options).toMatchObject({
      reference_types: ["table_reference"],
      provenances: [],
      attention_states: [],
    });
  });

  it("maps occurrence source views and diagnostics", () => {
    const response = toReferenceOccurrenceSourceResponse({
      occurrence_id: "occ-1",
      consumer_asset_id: "asset:consumer",
      views: [{
        id: "query_source",
        label: "query.sql",
        language: "sql",
        content: "SELECT * FROM lake.orders",
        matches: [{
          line: 1,
          column: 15,
          end_line: 1,
          end_column: 26,
          precision: "exact_reference",
        }],
      }],
      diagnostics: [{ severity: "warning", code: "W1", message: "partial" }],
    } as unknown as ReferenceOccurrenceSourceResponseTransport);

    expect(response.views[0].matches[0].precision).toBe("exact_reference");
    expect(response.diagnostics).toEqual([
      { severity: "warning", code: "W1", message: "partial" },
    ]);
  });

  it("defaults missing occurrence collections without changing catalog identity", () => {
    const response = toAssetReferenceDetailResponse({
      catalog_version: "catalog-3",
      reference: {
        id: "ref:missing",
        reference_type: "unknown",
        normalized_value: "missing",
        display_name: "missing",
        resolution: { state: "unresolved" },
        dependency_count: 0,
        attention_count: 0,
      },
    } as unknown as AssetReferenceDetailResponseTransport);

    expect(response.catalog_version).toBe("catalog-3");
    expect(response.occurrences).toEqual([]);
    expect(response.reference.candidate_assets).toEqual([]);
  });
});
