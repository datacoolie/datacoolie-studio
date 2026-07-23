import { QueryClient } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../../shared/api/client";
import type {
  AssetDetailResponse,
  AssetInventoryResponse,
  AssetReferenceDetailResponse,
  AssetReferenceListResponse,
  AssetSourceResponse,
} from "../../shared/api/domainTypes";
import {
  assetDetailOptions,
  assetInventoryOptions,
  assetQueryKeys,
  assetReferencesOptions,
  assetSourceOptions,
  fetchAssetInventory,
  normalizedParameters,
  referenceDetailOptions,
} from "./assetsQueries";

const inventory = {
  summary: {
    assets: 0,
    references: 0,
    manual_mappings: 0,
    visible: 0,
    asset_attention: 0,
    with_attention: 0,
    automatic_references: 0,
    manual_references: 0,
    unresolved_references: 0,
  },
  items: [],
  filter_options: { connections: [], formats: [], asset_types: [], roles: [], attention_states: [] },
  catalog_version: "catalog-1",
} as AssetInventoryResponse;

const references = {
  items: [],
  filter_options: { reference_types: [], provenances: [], attention_states: [] },
  catalog_version: "catalog-1",
} as AssetReferenceListResponse;

const assetDetail = { asset: { id: "asset-1" } } as unknown as AssetDetailResponse;
const assetSource = { definition: { kind: "sql_query", status: "available", raw: "SELECT 1" }, catalog_version: "catalog-1" } as AssetSourceResponse;
const referenceDetail = { reference: { id: "reference-1" }, occurrences: [] } as unknown as AssetReferenceDetailResponse;

afterEach(() => vi.restoreAllMocks());

describe("Assets query ownership", () => {
  it("normalizes parameters once for both the cache key and request", () => {
    expect(normalizedParameters({
      sort_dir: undefined,
      q: "orders",
      connection: "",
      asset_type: "table",
    })).toEqual({ asset_type: "table", q: "orders" });
    expect(assetInventoryOptions(7, { connection: "", q: undefined }).queryKey).toEqual([
      "environments", 7, "assets", "inventory", {},
    ]);
  });

  it("separates environments, resource families, parameters, and detail identities", () => {
    expect(assetQueryKeys.inventory(7)).not.toEqual(assetQueryKeys.inventory(8));
    expect(assetQueryKeys.inventory(7)).not.toEqual(assetQueryKeys.references(7));
    expect(assetQueryKeys.inventory(7, { q: "orders" })).not.toEqual(assetQueryKeys.inventory(7));
    expect(assetQueryKeys.assetDetail(7, "same-id")).not.toEqual(assetQueryKeys.referenceDetail(7, "same-id"));
    expect(assetQueryKeys.assetDetail(7, "same-id")).not.toEqual(assetQueryKeys.assetSource(7, "same-id"));
  });

  it("reuses fresh inventory and reference pages across tab unmounts", async () => {
    const queryClient = new QueryClient();
    const inventoryRequest = vi.spyOn(api, "getAssets").mockResolvedValue(inventory);
    const referenceRequest = vi.spyOn(api, "getAssetReferences").mockResolvedValue(references);

    await queryClient.ensureQueryData(assetInventoryOptions(7, { q: "orders" }));
    await queryClient.ensureQueryData(assetInventoryOptions(7, { q: "orders" }));
    await queryClient.ensureQueryData(assetReferencesOptions(7, { resolution_state: "unresolved" }));
    await queryClient.ensureQueryData(assetReferencesOptions(7, { resolution_state: "unresolved" }));

    expect(inventoryRequest).toHaveBeenCalledTimes(1);
    expect(inventoryRequest).toHaveBeenCalledWith(7, { q: "orders" });
    expect(referenceRequest).toHaveBeenCalledTimes(1);
    expect(referenceRequest).toHaveBeenCalledWith(7, { resolution_state: "unresolved" });
  });

  it("deduplicates asset and reference detail reads without sharing their cache entries", async () => {
    const queryClient = new QueryClient();
    const assetRequest = vi.spyOn(api, "getAsset").mockResolvedValue(assetDetail);
    const referenceRequest = vi.spyOn(api, "getAssetReference").mockResolvedValue(referenceDetail);

    await queryClient.ensureQueryData(assetDetailOptions(7, "asset-1"));
    await queryClient.ensureQueryData(assetDetailOptions(7, "asset-1"));
    await queryClient.ensureQueryData(referenceDetailOptions(7, "reference-1"));
    await queryClient.ensureQueryData(referenceDetailOptions(7, "reference-1"));

    expect(assetRequest).toHaveBeenCalledTimes(1);
    expect(referenceRequest).toHaveBeenCalledTimes(1);
  });

  it("loads source content through its dedicated query without loading asset detail", async () => {
    const queryClient = new QueryClient();
    const detailRequest = vi.spyOn(api, "getAsset").mockResolvedValue(assetDetail);
    const sourceRequest = vi.spyOn(api, "getAssetSource").mockResolvedValue(assetSource);

    await expect(queryClient.ensureQueryData(assetSourceOptions(7, "asset-1"))).resolves.toEqual(assetSource.definition);

    expect(sourceRequest).toHaveBeenCalledWith(7, "asset-1");
    expect(detailRequest).not.toHaveBeenCalled();
  });

  it("refetches an invalidated mapping-target inventory instead of returning stale targets", async () => {
    const queryClient = new QueryClient();
    const inventoryRequest = vi.spyOn(api, "getAssets").mockResolvedValue(inventory);

    await fetchAssetInventory(queryClient, 7, { q: "orders" });
    await queryClient.invalidateQueries({ queryKey: assetQueryKeys.inventory(7, { q: "orders" }) });
    await fetchAssetInventory(queryClient, 7, { q: "orders" });

    expect(inventoryRequest).toHaveBeenCalledTimes(2);
  });
});
