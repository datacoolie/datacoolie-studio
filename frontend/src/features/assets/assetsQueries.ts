import { keepPreviousData, queryOptions, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { useCallback } from "react";
import { api } from "../../shared/api/client";
import { environmentQueryKeys } from "../environments/environmentQueries";

export type AssetQueryParameters = Record<string, string | number | undefined>;

const assetsQueryStaleTime = Number.POSITIVE_INFINITY;

export const assetQueryKeys = {
  inventory: (environmentId: number, parameters: AssetQueryParameters = {}) => (
    [...environmentQueryKeys.assets(environmentId), "inventory", normalizedParameters(parameters)] as const
  ),
  references: (environmentId: number, parameters: AssetQueryParameters = {}) => (
    [...environmentQueryKeys.assets(environmentId), "references", normalizedParameters(parameters)] as const
  ),
  assetDetail: (environmentId: number, assetId: string) => (
    [...environmentQueryKeys.assets(environmentId), "asset-detail", assetId] as const
  ),
  assetSource: (environmentId: number, assetId: string) => (
    [...environmentQueryKeys.assets(environmentId), "asset-source", assetId] as const
  ),
  referenceDetail: (environmentId: number, referenceId: string) => (
    [...environmentQueryKeys.assets(environmentId), "reference-detail", referenceId] as const
  ),
};

export function assetInventoryOptions(environmentId: number, parameters: AssetQueryParameters = {}) {
  const normalized = normalizedParameters(parameters);
  return queryOptions({
    queryKey: assetQueryKeys.inventory(environmentId, normalized),
    queryFn: () => api.getAssets(environmentId, normalized),
    staleTime: assetsQueryStaleTime,
  });
}

export function assetReferencesOptions(environmentId: number, parameters: AssetQueryParameters = {}) {
  const normalized = normalizedParameters(parameters);
  return queryOptions({
    queryKey: assetQueryKeys.references(environmentId, normalized),
    queryFn: () => api.getAssetReferences(environmentId, normalized),
    staleTime: assetsQueryStaleTime,
  });
}

export function assetDetailOptions(environmentId: number, assetId: string) {
  return queryOptions({
    queryKey: assetQueryKeys.assetDetail(environmentId, assetId),
    queryFn: () => api.getAsset(environmentId, assetId),
    staleTime: assetsQueryStaleTime,
  });
}

export function assetSourceOptions(environmentId: number, assetId: string) {
  return queryOptions({
    queryKey: assetQueryKeys.assetSource(environmentId, assetId),
    queryFn: async () => (await api.getAssetSource(environmentId, assetId)).definition,
    staleTime: assetsQueryStaleTime,
  });
}

export function referenceDetailOptions(environmentId: number, referenceId: string) {
  return queryOptions({
    queryKey: assetQueryKeys.referenceDetail(environmentId, referenceId),
    queryFn: () => api.getAssetReference(environmentId, referenceId),
    staleTime: assetsQueryStaleTime,
  });
}

export function fetchAssetInventory(
  queryClient: QueryClient,
  environmentId: number,
  parameters: AssetQueryParameters = {},
) {
  return queryClient.fetchQuery(assetInventoryOptions(environmentId, parameters));
}

export function useAssetsResources(options: {
  environmentId: number;
  activeTab: "inventory" | "references";
  inventoryParameters: AssetQueryParameters;
  referenceParameters: AssetQueryParameters;
  selectedAssetId: string | null;
  selectedReferenceId: string | null;
}) {
  const queryClient = useQueryClient();
  const hasInventoryParameters = hasParameters(options.inventoryParameters);
  const baseInventory = useQuery(assetInventoryOptions(options.environmentId));
  const filteredInventory = useQuery({
    ...assetInventoryOptions(options.environmentId, options.inventoryParameters),
    enabled: options.activeTab === "inventory" && hasInventoryParameters,
    placeholderData: keepPreviousData,
  });
  const references = useQuery({
    ...assetReferencesOptions(options.environmentId, options.referenceParameters),
    enabled: options.activeTab === "references",
    placeholderData: keepPreviousData,
  });
  const assetDetail = useQuery({
    ...assetDetailOptions(options.environmentId, options.selectedAssetId ?? ""),
    enabled: Boolean(options.selectedAssetId),
  });
  const referenceDetail = useQuery({
    ...referenceDetailOptions(options.environmentId, options.selectedReferenceId ?? ""),
    enabled: Boolean(options.selectedReferenceId),
  });
  const loadAssetSource = useCallback(
    (assetId: string) => api.getAssetSource(options.environmentId, assetId),
    [options.environmentId],
  );
  const loadOccurrenceSource = useCallback(
    (occurrenceId: string) => api.getReferenceOccurrenceSource(options.environmentId, occurrenceId),
    [options.environmentId],
  );
  const searchAssets = useCallback(
    (parameters: AssetQueryParameters) => fetchAssetInventory(queryClient, options.environmentId, parameters),
    [options.environmentId, queryClient],
  );
  const refreshAfterMapping = useCallback(() => queryClient.invalidateQueries({
    queryKey: environmentQueryKeys.assets(options.environmentId),
  }), [options.environmentId, queryClient]);

  const inventoryPage = hasInventoryParameters
    ? filteredInventory.data ?? baseInventory.data
    : baseInventory.data;
  const inventoryQuery = hasInventoryParameters ? filteredInventory : baseInventory;

  return {
    baseInventory: baseInventory.data ?? null,
    inventoryPage: inventoryPage ?? null,
    referencesPage: references.data ?? null,
    assetDetail: assetDetail.data ?? null,
    referenceDetail: referenceDetail.data ?? null,
    inventoryLoading: inventoryQuery.isFetching,
    referencesLoading: references.isFetching || referenceDetail.isFetching,
    assetDetailLoading: assetDetail.isFetching,
    inventoryError: inventoryQuery.error,
    referencesError: references.error ?? referenceDetail.error,
    assetDetailError: assetDetail.error,
    retryInventory: () => inventoryQuery.refetch(),
    loadAssetSource,
    loadOccurrenceSource,
    searchAssets,
    refreshAfterMapping,
  };
}

export function normalizedParameters(parameters: AssetQueryParameters) {
  return Object.fromEntries(
    Object.entries(parameters)
      .filter(([, value]) => value !== undefined && value !== "")
      .sort(([left], [right]) => left.localeCompare(right)),
  ) as Record<string, string | number>;
}

function hasParameters(parameters: AssetQueryParameters) {
  return Object.keys(normalizedParameters(parameters)).length > 0;
}
