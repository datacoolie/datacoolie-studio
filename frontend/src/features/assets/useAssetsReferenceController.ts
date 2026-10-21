import { useMemo, useState } from "react";
import type { AssetReferenceGroupItem } from "../../shared/api/domainTypes";
import { useDebouncedValue } from "../../shared/hooks/useDebouncedValue";
import { toErrorMessage } from "../../shared/lib/errors";
import type { TableSort } from "../../shared/components/DataTable";
import type { AssetQueryParameters } from "./assetsQueries";
import { RECOMMENDED_ASSET_SORT_KEY, RECOMMENDED_SORT } from "./assetsOrdering";

export interface ReferenceFilters {
  referenceType: string;
  provenance: string;
  resolutionState: string;
  attentionState: string;
}

export const EMPTY_REFERENCE_FILTERS: ReferenceFilters = {
  referenceType: "",
  provenance: "",
  resolutionState: "",
  attentionState: "",
};

export const NEEDS_MAPPING_FILTER = "unresolved";

export function useAssetsReferenceController({ onActivateTab }: {
  onActivateTab: () => void;
}) {
  const [query, setQuery] = useState("");
  const [filters, setFilters] = useState<ReferenceFilters>(EMPTY_REFERENCE_FILTERS);
  const [sort, setSort] = useState<TableSort>(RECOMMENDED_SORT);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [drawerMode, setDrawerMode] = useState<"details" | "mapping">("details");
  const [clearId, setClearId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<{ referenceId: string; message: string } | null>(null);
  const debouncedQuery = useDebouncedValue(query, 200);
  const parameters = useMemo<AssetQueryParameters>(() => ({
    q: debouncedQuery.trim() || undefined,
    reference_type: filters.referenceType || undefined,
    provenance: filters.provenance || undefined,
    resolution_state: filters.resolutionState || undefined,
    attention_state: filters.attentionState || undefined,
    sort_by: sort.sortBy === "display_name" || sort.sortBy === RECOMMENDED_ASSET_SORT_KEY ? undefined : sort.sortBy,
    sort_dir: sort.sortDir === "asc" ? undefined : sort.sortDir,
  }), [debouncedQuery, filters, sort]);

  function reset() {
    setQuery("");
    setFilters(EMPTY_REFERENCE_FILTERS);
    setSort(RECOMMENDED_SORT);
  }

  function handleSort(nextSort: TableSort) {
    setSort((current) => current.sortBy === RECOMMENDED_ASSET_SORT_KEY
      ? { ...nextSort, sortDir: "asc" }
      : nextSort);
  }

  function showNeedingMapping() {
    onActivateTab();
    setQuery("");
    setFilters({ ...EMPTY_REFERENCE_FILTERS, resolutionState: NEEDS_MAPPING_FILTER });
    setSort(RECOMMENDED_SORT);
  }

  function showAttention() {
    onActivateTab();
    setQuery("");
    setFilters({ ...EMPTY_REFERENCE_FILTERS, attentionState: "with_attention" });
    setSort(RECOMMENDED_SORT);
  }

  async function clearMapping(
    reference: AssetReferenceGroupItem,
    onDelete: (mappingId: number) => Promise<unknown>,
    onRefresh: () => Promise<unknown>,
  ) {
    const mappingId = reference.manual_mapping?.mapping_id;
    if (!mappingId) return;
    if (clearId !== reference.id) {
      setClearId(reference.id);
      setActionError(null);
      return;
    }
    setActionError(null);
    try {
      await onDelete(mappingId);
      await onRefresh();
      setClearId(null);
    } catch (error) {
      setActionError({
        referenceId: reference.id,
        message: toErrorMessage(error) || "Mapping could not be cleared.",
      });
    }
  }

  return {
    query,
    setQuery,
    filters,
    setFilters,
    sort,
    setSort,
    parameters,
    selectedId,
    setSelectedId,
    drawerMode,
    setDrawerMode,
    clearId,
    setClearId,
    actionError,
    setActionError,
    reset,
    handleSort,
    showNeedingMapping,
    showAttention,
    clearMapping,
  };
}
