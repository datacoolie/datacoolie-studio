import { useMemo, useState } from "react";
import type { TableSort } from "../../shared/components/DataTable";
import { useDebouncedValue } from "../../shared/hooks/useDebouncedValue";
import type { AssetQueryParameters } from "./assetsQueries";
import { RECOMMENDED_ASSET_SORT_KEY, RECOMMENDED_SORT } from "./assetsOrdering";

export interface AssetFilters {
  connection: string;
  format: string;
  assetType: string;
  role: string;
  attentionState: string;
  scope: string;
}

export const EMPTY_ASSET_FILTERS: AssetFilters = {
  connection: "",
  format: "",
  assetType: "",
  role: "",
  attentionState: "",
  scope: "",
};

export function useAssetsInventoryController({ onActivateTab }: {
  onActivateTab: () => void;
}) {
  const [query, setQuery] = useState("");
  const [filters, setFilters] = useState<AssetFilters>(EMPTY_ASSET_FILTERS);
  const [sort, setSort] = useState<TableSort>(RECOMMENDED_SORT);
  const debouncedQuery = useDebouncedValue(query, 200);
  const parameters = useMemo<AssetQueryParameters>(() => ({
    q: debouncedQuery.trim() || undefined,
    connection: filters.connection || undefined,
    format: filters.format || undefined,
    asset_type: filters.assetType || undefined,
    role: filters.role || undefined,
    attention_state: filters.attentionState || undefined,
    scope: filters.scope || undefined,
    sort_by: sort.sortBy === "display_name" || sort.sortBy === RECOMMENDED_ASSET_SORT_KEY ? undefined : sort.sortBy,
    sort_dir: sort.sortDir === "asc" ? undefined : sort.sortDir,
  }), [debouncedQuery, filters, sort]);

  function reset() {
    setQuery("");
    setFilters(EMPTY_ASSET_FILTERS);
    setSort(RECOMMENDED_SORT);
  }

  function handleSort(nextSort: TableSort) {
    setSort((current) => current.sortBy === RECOMMENDED_ASSET_SORT_KEY
      ? { ...nextSort, sortDir: "asc" }
      : nextSort);
  }

  function showAll() {
    onActivateTab();
    reset();
  }

  function showAttention() {
    onActivateTab();
    setQuery("");
    setFilters({ ...EMPTY_ASSET_FILTERS, attentionState: "with_attention" });
    setSort(RECOMMENDED_SORT);
  }

  return {
    query,
    setQuery,
    filters,
    setFilters,
    sort,
    setSort,
    parameters,
    reset,
    handleSort,
    showAll,
    showAttention,
  };
}
