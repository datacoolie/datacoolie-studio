import { Boxes, FilterX, Search } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Icon } from "@iconify/react";
import type { AssetInventoryItem, AssetInventoryResponse, AssetReferenceGroupItem, MetadataEditorDocument } from "../../shared/api/domainTypes";
import { EmptyState } from "../../shared/components/EmptyState";
import type { MetadataNavigationTarget } from "../../shared/metadataNavigation";
import type { LineageDataflowFocusTarget } from "../../shared/lineageNavigation";
import { toErrorMessage } from "../../shared/lib/errors";
import { connectionStageFamily } from "../../shared/connectionOrder";
import { DataTable, formatNumber, type TableColumn, type TableSort } from "../monitoring/MonitoringCharts";
import { LineageFormatIcon } from "../lineage/components/LineageFormatIcon";
import { assetIconKind, assetTypeIconId, assetTypeTone, referenceTypeAssetType } from "../lineage/model/presentation";
import { MetadataDataflowDrawer } from "../dataflows/MetadataDataflowDrawer";
import {
  buildMetadataDataflowRecords,
  findMetadataDataflowRecord,
  isEditableMetadataDataflowRecord,
  type MetadataDataflowSelection,
  updateMetadataDataflowRow,
} from "../dataflows/metadataDataflowModel";
import { metadataSaveConfirmation } from "../metadata-explorer/metadataSaveConfirmation";
import { MetadataSourceSaveConfirmationDialog } from "../metadata-explorer/MetadataSourceSaveConfirmationDialog";
import { metadataQueryForAsset, presentAsset, presentReference, referenceConsumerTypeSummary, referenceContextLine, referenceProvenanceDescription, referenceProvenanceLabel, referenceProvenanceTone, referenceResolutionPresentation } from "./assetsPresentation";
import { orderAssetsByConnection, orderReferencesByAction, RECOMMENDED_ASSET_SORT_KEY, RECOMMENDED_SORT, startsConnectionGroup, startsReferenceResolutionGroup } from "./assetsOrdering";
import { AssetsDrawer } from "./AssetsDrawer";
import { referenceMappingAction, referenceMappingActionLabel, type ReferenceMappingPayload } from "../reference-mappings/referenceMappingModel";
import { ReferenceMappingClearAction } from "../reference-mappings/ReferenceMappingClearAction";
import { ReferenceDrawer } from "./ReferenceDrawer";
import { useAssetsResources } from "./assetsQueries";

interface AssetsViewProps {
  environmentId: number;
  metadataEditorDocument: MetadataEditorDocument | null;
  metadataEditorDraft: MetadataEditorDocument | null;
  onEnsureMetadataEditor: () => Promise<void>;
  metadataBusy: boolean;
  onValidateMetadata: (document: MetadataEditorDocument) => Promise<MetadataEditorDocument>;
  onSaveMetadataDraft: (document: MetadataEditorDocument) => Promise<MetadataEditorDocument>;
  onSaveMetadata: (document: MetadataEditorDocument) => Promise<MetadataEditorDocument>;
  routeSearch?: string;
  onFocusInLineage: (assetId: string) => void;
  onFocusDataflowInLineage: (target: LineageDataflowFocusTarget) => void;
  onOpenMetadata: (target: MetadataNavigationTarget) => void;
  mappingBusy?: boolean;
  onCreateReferenceMapping: (payload: ReferenceMappingPayload) => Promise<unknown>;
  onUpdateReferenceMapping: (mappingId: number, payload: ReferenceMappingPayload) => Promise<unknown>;
  onDeleteReferenceMapping: (mappingId: number) => Promise<unknown>;
}

type AssetsTab = "inventory" | "references";
type AssetRow = AssetInventoryItem & Record<string, unknown>;
type ReferenceRow = AssetReferenceGroupItem & Record<string, unknown>;

interface AssetFilters {
  connection: string;
  format: string;
  assetType: string;
  role: string;
  attentionState: string;
  scope: string;
}

interface ReferenceFilters {
  referenceType: string;
  provenance: string;
  resolutionState: string;
  attentionState: string;
}

const EMPTY_ASSET_FILTERS: AssetFilters = {
  connection: "",
  format: "",
  assetType: "",
  role: "",
  attentionState: "",
  scope: "",
};

const EMPTY_REFERENCE_FILTERS: ReferenceFilters = {
  referenceType: "",
  provenance: "",
  resolutionState: "",
  attentionState: "",
};

const ASSETS_DRAWER_SEARCH_KEYS = [
  "assetId",
  "referenceId",
  "dataflowSourceId",
  "dataflowId",
  "dataflowName",
] as const;

const ASSETS_DRAWER_HISTORY_KEY = "datacoolieAssetsDrawer";
const NEEDS_MAPPING_FILTER = "unresolved";

type AssetsDrawerHistoryState =
  | { kind: "asset"; assetTrail: string[]; depth: number }
  | { kind: "reference"; referenceId: string; mode?: "details" | "mapping"; depth: number }
  | { kind: "dataflow"; assetTrail: string[]; dataflow: MetadataDataflowSelection; depth: number };

function dataflowSelectionFromSearch(params: URLSearchParams): MetadataDataflowSelection | null {
  const dataflowId = params.get("dataflowId");
  const name = params.get("dataflowName");
  const sourceId = params.get("dataflowSourceId");
  if (!dataflowId && !name && !sourceId) return null;
  const parsedSourceId = sourceId ? Number(sourceId) : null;
  return {
    metadataSourceId: Number.isFinite(parsedSourceId) && parsedSourceId ? parsedSourceId : null,
    dataflowId,
    name,
  };
}

function assetsDrawerHistoryFromState(state: unknown): AssetsDrawerHistoryState | null {
  if (!state || typeof state !== "object") return null;
  const value = (state as Record<string, unknown>)[ASSETS_DRAWER_HISTORY_KEY];
  if (!value || typeof value !== "object") return null;
  const record = value as Record<string, unknown>;
  const depth = positiveInteger(record.depth) ?? 1;
  if (record.kind === "asset") {
    const assetTrail = stringArray(record.assetTrail);
    return assetTrail.length ? { kind: "asset", assetTrail, depth } : null;
  }
  if (record.kind === "reference") {
    const referenceId = textOrNull(record.referenceId);
    const mode = record.mode === "mapping" ? "mapping" : "details";
    return referenceId ? { kind: "reference", referenceId, mode, depth } : null;
  }
  if (record.kind === "dataflow") {
    const assetTrail = stringArray(record.assetTrail);
    const dataflow = dataflowSelectionFromHistory(record.dataflow);
    return dataflow ? { kind: "dataflow", assetTrail, dataflow, depth } : null;
  }
  return null;
}

function pushAssetsDrawerHistory(drawer: AssetsDrawerHistoryState) {
  const nextState = {
    ...historyStateObject(),
    [ASSETS_DRAWER_HISTORY_KEY]: drawer,
  };
  window.history.pushState(nextState, "", currentHistoryUrl());
}

function clearLegacyAssetsDrawerSearchParams() {
  const url = new URL(window.location.href);
  let changed = false;
  for (const key of ASSETS_DRAWER_SEARCH_KEYS) {
    if (!url.searchParams.has(key)) continue;
    url.searchParams.delete(key);
    changed = true;
  }
  if (!changed) return;
  const next = `${url.pathname}${url.search}${url.hash}`;
  window.history.replaceState(historyStateObject(), "", next);
}

function currentHistoryUrl() {
  return `${window.location.pathname}${window.location.search}${window.location.hash}`;
}

function historyStateObject() {
  return window.history.state && typeof window.history.state === "object"
    ? { ...(window.history.state as Record<string, unknown>) }
    : {};
}

function dataflowSelectionFromHistory(value: unknown): MetadataDataflowSelection | null {
  if (!value || typeof value !== "object") return null;
  const record = value as Record<string, unknown>;
  const metadataSourceId = positiveInteger(record.metadataSourceId);
  const dataflowId = textOrNull(record.dataflowId);
  const name = textOrNull(record.name);
  if (!metadataSourceId && !dataflowId && !name) return null;
  return { metadataSourceId: metadataSourceId ?? null, dataflowId, name };
}

function positiveInteger(value: unknown) {
  const number = Number(value);
  return Number.isInteger(number) && number > 0 ? number : null;
}

function stringArray(value: unknown) {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string" && item.length > 0) : [];
}

function textOrNull(value: unknown) {
  return typeof value === "string" && value.length ? value : null;
}

export function AssetsView({
  environmentId,
  metadataEditorDocument,
  metadataEditorDraft,
  onEnsureMetadataEditor,
  metadataBusy,
  onValidateMetadata,
  onSaveMetadataDraft,
  onSaveMetadata,
  routeSearch,
  onFocusInLineage,
  onFocusDataflowInLineage,
  onOpenMetadata,
  mappingBusy,
  onCreateReferenceMapping,
  onUpdateReferenceMapping,
  onDeleteReferenceMapping,
}: AssetsViewProps) {
  const [activeTab, setActiveTab] = useState<AssetsTab>("inventory");
  const [assetQuery, setAssetQuery] = useState("");
  const [referenceQuery, setReferenceQuery] = useState("");
  const [assetFilters, setAssetFilters] = useState<AssetFilters>(EMPTY_ASSET_FILTERS);
  const [referenceFilters, setReferenceFilters] = useState<ReferenceFilters>(EMPTY_REFERENCE_FILTERS);
  const [assetSort, setAssetSort] = useState<TableSort>(RECOMMENDED_SORT);
  const [referenceSort, setReferenceSort] = useState<TableSort>(RECOMMENDED_SORT);
  const [selectedAssetTrail, setSelectedAssetTrail] = useState<string[]>([]);
  const [selectedReferenceId, setSelectedReferenceId] = useState<string | null>(null);
  const [referenceDrawerMode, setReferenceDrawerMode] = useState<"details" | "mapping">("details");
  const [clearReferenceId, setClearReferenceId] = useState<string | null>(null);
  const [referenceActionError, setReferenceActionError] = useState<{ referenceId: string; message: string } | null>(null);
  const [selectedDataflow, setSelectedDataflow] = useState<MetadataDataflowSelection | null>(null);
  const [pendingMetadataSave, setPendingMetadataSave] = useState<MetadataEditorDocument | null>(null);
  const selectedAssetId = selectedAssetTrail.length ? selectedAssetTrail[selectedAssetTrail.length - 1] : null;
  const debouncedAssetQuery = useDebouncedValue(assetQuery, 200);
  const debouncedReferenceQuery = useDebouncedValue(referenceQuery, 200);
  const inventoryParameters = useMemo(() => ({
    q: debouncedAssetQuery.trim() || undefined,
    connection: assetFilters.connection || undefined,
    format: assetFilters.format || undefined,
    asset_type: assetFilters.assetType || undefined,
    role: assetFilters.role || undefined,
    attention_state: assetFilters.attentionState || undefined,
    scope: assetFilters.scope || undefined,
    sort_by: assetSort.sortBy === "display_name" || assetSort.sortBy === RECOMMENDED_ASSET_SORT_KEY ? undefined : assetSort.sortBy,
    sort_dir: assetSort.sortDir === "asc" ? undefined : assetSort.sortDir,
  }), [assetFilters, assetSort, debouncedAssetQuery]);
  const referenceParameters = useMemo(() => ({
    q: debouncedReferenceQuery.trim() || undefined,
    reference_type: referenceFilters.referenceType || undefined,
    provenance: referenceFilters.provenance || undefined,
    resolution_state: referenceFilters.resolutionState || undefined,
    attention_state: referenceFilters.attentionState || undefined,
    sort_by: referenceSort.sortBy === "display_name" || referenceSort.sortBy === RECOMMENDED_ASSET_SORT_KEY ? undefined : referenceSort.sortBy,
    sort_dir: referenceSort.sortDir === "asc" ? undefined : referenceSort.sortDir,
  }), [debouncedReferenceQuery, referenceFilters, referenceSort]);
  const resources = useAssetsResources({
    environmentId,
    activeTab,
    inventoryParameters,
    referenceParameters,
    selectedAssetId,
    selectedReferenceId,
  });

  useEffect(() => {
    const historyDrawer = assetsDrawerHistoryFromState(window.history.state);
    if (historyDrawer) {
      applyAssetsDrawerHistoryState(historyDrawer);
      clearLegacyAssetsDrawerSearchParams();
      return;
    }
    const params = new URLSearchParams(routeSearch ?? "");
    const requestedTab = params.get("tab");
    if (requestedTab === "references" || requestedTab === "inventory") setActiveTab(requestedTab);
    const requestedQuery = params.get("q");
    if (requestedQuery !== null) {
      if (requestedTab === "references") setReferenceQuery(requestedQuery);
      else setAssetQuery(requestedQuery);
    }
    const requestedAssetId = params.get("assetId");
    if (requestedAssetId) {
      setActiveTab("inventory");
      setSelectedAssetTrail([requestedAssetId]);
      setSelectedReferenceId(null);
      setReferenceDrawerMode("details");
      setSelectedDataflow(dataflowSelectionFromSearch(params));
      clearLegacyAssetsDrawerSearchParams();
      return;
    }
    const requestedReferenceId = params.get("referenceId");
    if (requestedReferenceId) {
      setActiveTab("references");
      setSelectedReferenceId(requestedReferenceId);
      setReferenceDrawerMode("details");
      setSelectedAssetTrail([]);
      setSelectedDataflow(null);
      clearLegacyAssetsDrawerSearchParams();
      return;
    }
    setSelectedAssetTrail([]);
    setSelectedReferenceId(null);
    setReferenceDrawerMode("details");
    setSelectedDataflow(null);
  }, [routeSearch]);

  useEffect(() => {
    function handlePopState(event: PopStateEvent) {
      applyAssetsDrawerHistoryState(assetsDrawerHistoryFromState(event.state));
    }
    window.addEventListener("popstate", handlePopState);
    return () => window.removeEventListener("popstate", handlePopState);
  }, []);

  const assets = resources.baseInventory;
  const assetPage = resources.inventoryPage;
  const referencePage = resources.referencesPage;
  const assetDetail = resources.assetDetail;
  const referenceDetail = resources.referenceDetail;
  const inventory = assetPage?.items ?? assets?.items ?? [];
  const references = referencePage?.items ?? [];
  const referenceOccurrences = referenceDetail?.occurrences ?? [];
  const referenceById = useMemo(() => new Map(references.map((item) => [item.id, item])), [references]);
  const selectedAsset = useMemo(
    () => inventory.find((asset) => asset.id === selectedAssetId) ?? null,
    [inventory, selectedAssetId],
  );
  const selectedReference = selectedReferenceId
    ? (referenceDetail?.reference.id === selectedReferenceId ? referenceDetail.reference : referenceById.get(selectedReferenceId) ?? null)
    : null;
  const drawerAsset = assetDetail?.asset ?? selectedAsset;
  const activeMetadataDocument = metadataEditorDraft ?? metadataEditorDocument;
  const dataflowRecords = useMemo(
    () => buildMetadataDataflowRecords(activeMetadataDocument),
    [activeMetadataDocument],
  );
  const selectedDataflowRecord = useMemo(
    () => findMetadataDataflowRecord(dataflowRecords, selectedDataflow),
    [dataflowRecords, selectedDataflow],
  );
  const selectedDataflowEditable = isEditableMetadataDataflowRecord(activeMetadataDocument, selectedDataflowRecord);
  const sourceSaveConfirmation = pendingMetadataSave ? metadataSaveConfirmation(metadataEditorDocument, pendingMetadataSave) : null;
  const searchMappingTargets = useCallback(async (query: string, connectionName: string) => (
    await resources.searchAssets({
      q: query.trim() || undefined,
      connection: connectionName || undefined,
    })
  ).items, [resources.searchAssets]);

  useEffect(() => {
    if (!selectedDataflow || activeMetadataDocument) return;
    void onEnsureMetadataEditor();
    // The workspace callback is intentionally excluded: drawer identity, not
    // parent renders, controls this action-driven resource load.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [environmentId, selectedDataflow?.metadataSourceId, selectedDataflow?.rowIndex, selectedDataflow?.dataflowId, selectedDataflow?.name, activeMetadataDocument]);

  function selectedDataflowDocument(nextRow: Record<string, unknown>) {
    if (!activeMetadataDocument || !selectedDataflowRecord || !selectedDataflowEditable) return null;
    return updateMetadataDataflowRow(activeMetadataDocument, selectedDataflowRecord.rowIndex, nextRow);
  }

  async function validateSelectedDataflow(nextRow: Record<string, unknown>) {
    const nextDocument = selectedDataflowDocument(nextRow);
    if (!nextDocument) return;
    try {
      return await onValidateMetadata(nextDocument);
    } catch {
      return undefined;
    }
  }

  async function saveSelectedDataflowDraft(nextRow: Record<string, unknown>) {
    const nextDocument = selectedDataflowDocument(nextRow);
    if (!nextDocument) return;
    try {
      return await onSaveMetadataDraft(nextDocument);
    } catch {
      return undefined;
    }
  }

  function saveSelectedDataflow(nextRow: Record<string, unknown>) {
    const nextDocument = selectedDataflowDocument(nextRow);
    if (!nextDocument) return;
    setPendingMetadataSave(nextDocument);
  }

  async function confirmMetadataSave() {
    if (!pendingMetadataSave) return;
    try {
      const saved = await onSaveMetadata(pendingMetadataSave);
      if (saved) {
        setPendingMetadataSave(null);
        closeDataflowDrawer();
      }
      return saved;
    } catch {
      return undefined;
    }
  }

  const assetMetrics = useMemo(() => calculateAssetMetrics(inventory, references, assets?.summary), [inventory, references, assets?.summary]);
  // Pages already reflect the server query. Retain the previous page while a
  // debounced request is in flight instead of locally hiding stale rows.
  const filteredAssets = useMemo(
    () => (assetSort.sortBy === RECOMMENDED_ASSET_SORT_KEY ? orderAssetsByConnection(inventory) : inventory) as AssetRow[],
    [assetSort.sortBy, inventory],
  );
  const filteredReferences = useMemo(
    () => (referenceSort.sortBy === RECOMMENDED_ASSET_SORT_KEY ? orderReferencesByAction(references) : references) as ReferenceRow[],
    [referenceSort.sortBy, references],
  );
  const attentionMappingLabels = useMemo(() => {
    const labels: Record<string, string> = {};
    for (const reference of references) {
      const label = referenceMappingActionLabel(referenceMappingAction(reference));
      if (label) labels[reference.id] = label;
    }
    return labels;
  }, [references]);

  const filterOptions = useMemo(() => ({
    assets: {
      connections: sortTextOptions(assets?.filter_options.connections ?? []),
      formats: sortTextOptions(assets?.filter_options.formats ?? []),
      assetTypes: sortTextOptions(assets?.filter_options.asset_types ?? []),
      roles: sortTextOptions(assets?.filter_options.roles ?? []),
      attentionStates: sortTextOptions(assets?.filter_options.attention_states ?? []),
    },
    references: {
      referenceTypes: sortTextOptions(referencePage?.filter_options.reference_types ?? []),
      provenances: sortTextOptions(referencePage?.filter_options.provenances ?? []),
      resolutionStates: sortTextOptions(referencePage?.filter_options.resolution_states ?? []),
      attentionStates: sortTextOptions(referencePage?.filter_options.attention_states ?? []),
    },
  }), [assets, referencePage]);

  const assetColumns = useMemo<TableColumn<AssetRow>[]>(() => [
    {
      key: "asset",
      label: "Asset",
      sortable: true,
      sortKey: "display_name",
      minWidth: 320,
      fillPriority: "last",
      render: (asset) => <AssetCell asset={asset} />,
    },
    {
      key: "asset_type",
      label: "Type",
      sortable: true,
      autoFit: true,
      minWidth: 112,
      maxWidth: 156,
      render: (asset) => <AssetTypeCell asset={asset} />,
    },
    {
      key: "connection_name",
      label: "Connection",
      sortable: true,
      autoFit: true,
      minWidth: 180,
      maxWidth: 260,
      render: (asset) => <ConnectionCell asset={asset} />,
    },
    {
      key: "roles",
      label: "Usage",
      autoFit: true,
      minWidth: 96,
      maxWidth: 150,
      render: (asset) => <UsageCell asset={asset} />,
    },
    {
      key: "lineage",
      label: "Lineage",
      sortable: true,
      sortKey: "downstream_count",
      autoFit: true,
      minWidth: 120,
      maxWidth: 188,
      render: (asset) => <LineageCell asset={asset} />,
    },
    {
      key: "depends_on_count",
      label: "Depends On",
      autoFit: true,
      minWidth: 118,
      maxWidth: 150,
      render: (asset) => <DependsOnCell asset={asset} />,
    },
    {
      key: "used_by_count",
      label: "Used By",
      autoFit: true,
      minWidth: 132,
      maxWidth: 166,
      render: (asset) => <UsedByCell asset={asset} />,
    },
    {
      key: "attention_count",
      label: "Attention",
      sortable: true,
      autoFit: true,
      minWidth: 90,
      maxWidth: 110,
      render: (asset) => <AttentionCountCell count={asset.attention_count} />,
    },
    {
      key: "metadata_sources",
      label: "Provenance",
      autoFit: true,
      minWidth: 118,
      maxWidth: 150,
      render: (asset) => <ProvenanceCell asset={asset} />,
    },
  ], []);

  const referenceColumns = useMemo<TableColumn<ReferenceRow>[]>(() => [
    {
      key: "reference",
      label: "Reference",
      sortable: true,
      sortKey: "display_name",
      minWidth: 260,
      maxWidth: 340,
      fillPriority: "last",
      render: (reference) => <ReferenceCell reference={reference} />,
    },
    {
      key: "resolution_state",
      label: "Resolution",
      sortable: true,
      width: 140,
      render: (reference) => <ReferenceResolutionCell reference={reference} />,
    },
    {
      key: "resolved_asset_id",
      label: "Target",
      width: 256,
      render: (reference) => <ReferenceTargetCell reference={reference} />,
    },
    {
      key: "consumer_asset_ids",
      label: "Used By",
      autoFit: true,
      minWidth: 150,
      maxWidth: 210,
      render: (reference) => <ReferenceConsumersCell reference={reference} />,
    },
    {
      key: "provenance",
      label: "Detected in",
      autoFit: true,
      minWidth: 104,
      maxWidth: 140,
      render: (reference) => <ReferenceSourceCell reference={reference} />,
    },
    {
      key: "action",
      label: "Action",
      width: 184,
      render: (reference) => {
        const action = referenceMappingAction(reference);
        const label = referenceMappingActionLabel(action);
        if (!label) return <span className="assets-empty-inline">-</span>;
        const isManualMapping = Boolean(reference.manual_mapping?.mapping_id);
        const showClearError = referenceActionError?.referenceId === reference.id ? referenceActionError.message : null;
        return (
          <div className="assets-reference-actions" onClick={(event) => event.stopPropagation()}>
            <button
              className={`text-action compact assets-reference-action-button ${action === "edit" ? "reference-mapping-action-edit" : "reference-mapping-action-map"}`}
              type="button"
              disabled={mappingBusy}
              onClick={(event) => {
                event.stopPropagation();
                openReferenceMapping(reference);
              }}
            >
              {label}
            </button>
            {isManualMapping ? (
              <ReferenceMappingClearAction
                confirming={clearReferenceId === reference.id}
                disabled={Boolean(mappingBusy)}
                onClear={() => void clearReferenceMapping(reference)}
                onDismiss={() => setClearReferenceId(null)}
              />
            ) : null}
            {showClearError ? <small className="reference-mapping-action-error" role="alert">{showClearError}</small> : null}
          </div>
        );
      },
    },
  ], [clearReferenceId, mappingBusy, referenceActionError]);

  if (!assets && resources.inventoryError) {
    return (
      <EmptyState
        icon={<Boxes size={24} />}
        title="Unable to load assets"
        detail={toErrorMessage(resources.inventoryError)}
        action={<button type="button" onClick={() => void resources.retryInventory()}>Retry</button>}
      />
    );
  }
  if (!assets) {
    return <EmptyState icon={<Boxes size={24} />} title="Loading assets" />;
  }

  function resetAssetFilters() {
    setAssetQuery("");
    setAssetFilters(EMPTY_ASSET_FILTERS);
    setAssetSort(RECOMMENDED_SORT);
  }

  function resetReferenceFilters() {
    setReferenceQuery("");
    setReferenceFilters(EMPTY_REFERENCE_FILTERS);
    setReferenceSort(RECOMMENDED_SORT);
  }

  function handleAssetSort(nextSort: TableSort) {
    setAssetSort((current) => current.sortBy === RECOMMENDED_ASSET_SORT_KEY ? { ...nextSort, sortDir: "asc" } : nextSort);
  }

  function handleReferenceSort(nextSort: TableSort) {
    setReferenceSort((current) => current.sortBy === RECOMMENDED_ASSET_SORT_KEY ? { ...nextSort, sortDir: "asc" } : nextSort);
  }

  async function refreshAfterReferenceMapping() {
    await resources.refreshAfterMapping();
  }

  async function clearReferenceMapping(reference: AssetReferenceGroupItem) {
    const mappingId = reference.manual_mapping?.mapping_id;
    if (!mappingId) return;
    if (clearReferenceId !== reference.id) {
      setClearReferenceId(reference.id);
      setReferenceActionError(null);
      return;
    }
    setReferenceActionError(null);
    try {
      await onDeleteReferenceMapping(mappingId);
      await refreshAfterReferenceMapping();
      setClearReferenceId(null);
    } catch (error) {
      setReferenceActionError({
        referenceId: reference.id,
        message: toErrorMessage(error) || "Mapping could not be cleared.",
      });
    }
  }

  function showAllAssets() {
    setActiveTab("inventory");
    setAssetQuery("");
    setAssetFilters(EMPTY_ASSET_FILTERS);
    setAssetSort(RECOMMENDED_SORT);
  }

  function showAttentionAssets() {
    setActiveTab("inventory");
    setAssetQuery("");
    setAssetFilters({ ...EMPTY_ASSET_FILTERS, attentionState: "with_attention" });
    setAssetSort(RECOMMENDED_SORT);
  }

  function showReferencesNeedingMapping() {
    setActiveTab("references");
    setReferenceQuery("");
    setReferenceFilters({ ...EMPTY_REFERENCE_FILTERS, resolutionState: NEEDS_MAPPING_FILTER });
    setReferenceSort(RECOMMENDED_SORT);
  }

  function showAttentionItems() {
    if (activeTab === "references") {
      setReferenceQuery("");
      setReferenceFilters({ ...EMPTY_REFERENCE_FILTERS, attentionState: "with_attention" });
      setReferenceSort(RECOMMENDED_SORT);
      return;
    }
    showAttentionAssets();
  }

  function applyAssetsDrawerHistoryState(drawer: AssetsDrawerHistoryState | null) {
    if (!drawer) {
      clearDrawerSelection();
      return;
    }
    if (drawer.kind === "asset") {
      setActiveTab("inventory");
      setSelectedAssetTrail(drawer.assetTrail);
      setSelectedReferenceId(null);
      setReferenceDrawerMode("details");
      setSelectedDataflow(null);
      return;
    }
    if (drawer.kind === "reference") {
      setActiveTab("references");
      setSelectedAssetTrail([]);
      setSelectedReferenceId(drawer.referenceId);
      setReferenceDrawerMode(drawer.mode ?? "details");
      setSelectedDataflow(null);
      return;
    }
    setActiveTab("inventory");
    setSelectedAssetTrail(drawer.assetTrail);
    setSelectedReferenceId(null);
    setReferenceDrawerMode("details");
    setSelectedDataflow(drawer.dataflow);
  }

  function clearDrawerSelection() {
    setSelectedAssetTrail([]);
    setSelectedReferenceId(null);
    setReferenceDrawerMode("details");
    setSelectedDataflow(null);
  }

  function closeDrawerStack() {
    const drawer = assetsDrawerHistoryFromState(window.history.state);
    clearDrawerSelection();
    if (drawer?.depth) {
      window.history.go(-drawer.depth);
    }
  }

  function openAssetDrawer(assetId: string) {
    setActiveTab("inventory");
    setSelectedReferenceId(null);
    setReferenceDrawerMode("details");
    setSelectedDataflow(null);
    setSelectedAssetTrail([assetId]);
    pushAssetsDrawerHistory({ kind: "asset", assetTrail: [assetId], depth: 1 });
  }

  function openReferenceDrawer(referenceId: string) {
    setActiveTab("references");
    setSelectedAssetTrail([]);
    setSelectedDataflow(null);
    setSelectedReferenceId(referenceId);
    setReferenceDrawerMode("details");
    pushAssetsDrawerHistory({ kind: "reference", referenceId, mode: "details", depth: 1 });
  }

  function openReferenceDrawerFromAsset(referenceId: string) {
    setSelectedDataflow(null);
    setSelectedReferenceId(referenceId);
    setReferenceDrawerMode("details");
    const currentDepth = assetsDrawerHistoryFromState(window.history.state)?.depth ?? Math.max(1, selectedAssetTrail.length);
    pushAssetsDrawerHistory({ kind: "reference", referenceId, mode: "details", depth: currentDepth + 1 });
  }

  function openReferenceMapping(reference: AssetReferenceGroupItem) {
    const current = assetsDrawerHistoryFromState(window.history.state);
    const isCurrentReference = current?.kind === "reference" && current.referenceId === reference.id;
    const detailDepth = isCurrentReference ? current.depth : 1;
    setActiveTab("references");
    setSelectedAssetTrail([]);
    setSelectedDataflow(null);
    setSelectedReferenceId(reference.id);
    setReferenceDrawerMode("mapping");
    if (!isCurrentReference) {
      pushAssetsDrawerHistory({ kind: "reference", referenceId: reference.id, mode: "details", depth: detailDepth });
    }
    pushAssetsDrawerHistory({ kind: "reference", referenceId: reference.id, mode: "mapping", depth: detailDepth + 1 });
  }

  function openReferenceMappingFromAsset(referenceId: string) {
    const reference = referenceById.get(referenceId);
    if (!reference || !referenceMappingActionLabel(referenceMappingAction(reference))) return;
    const currentDepth = assetsDrawerHistoryFromState(window.history.state)?.depth ?? Math.max(1, selectedAssetTrail.length);
    setActiveTab("references");
    setSelectedAssetTrail([]);
    setSelectedDataflow(null);
    setSelectedReferenceId(reference.id);
    setReferenceDrawerMode("mapping");
    pushAssetsDrawerHistory({ kind: "reference", referenceId: reference.id, mode: "details", depth: currentDepth + 1 });
    pushAssetsDrawerHistory({ kind: "reference", referenceId: reference.id, mode: "mapping", depth: currentDepth + 2 });
  }

  function openRelatedAssetDrawer(assetId: string) {
    const nextTrail = selectedAssetTrail.length && selectedAssetTrail[selectedAssetTrail.length - 1] !== assetId
      ? [...selectedAssetTrail, assetId]
      : selectedAssetTrail.length
        ? selectedAssetTrail
        : [assetId];
    if (selectedAssetTrail[selectedAssetTrail.length - 1] === assetId) return;
    setSelectedDataflow(null);
    setSelectedReferenceId(null);
    setReferenceDrawerMode("details");
    setSelectedAssetTrail(nextTrail);
    const currentDepth = assetsDrawerHistoryFromState(window.history.state)?.depth ?? Math.max(0, selectedAssetTrail.length);
    pushAssetsDrawerHistory({ kind: "asset", assetTrail: nextTrail, depth: currentDepth + 1 });
  }

  function openDataflowDrawer(flow: { metadata_source_id?: number | null; dataflow_id?: string | null; name?: string | null }) {
    const selection = {
      metadataSourceId: flow.metadata_source_id,
      dataflowId: flow.dataflow_id,
      name: flow.name,
    };
    setSelectedReferenceId(null);
    setSelectedDataflow(selection);
    const currentDepth = assetsDrawerHistoryFromState(window.history.state)?.depth ?? Math.max(0, selectedAssetTrail.length);
    pushAssetsDrawerHistory({
      kind: "dataflow",
      assetTrail: selectedAssetTrail,
      dataflow: selection,
      depth: currentDepth + 1,
    });
  }

  function closeAssetDrawer() {
    closeDrawerStack();
  }

  function closeReferenceDrawer() {
    closeDrawerStack();
  }

  function closeDataflowDrawer() {
    closeDrawerStack();
  }

  function backFromDataflowDrawer() {
    window.history.back();
  }

  function backFromRelatedAssetDrawer() {
    if ((assetsDrawerHistoryFromState(window.history.state)?.depth ?? 0) > 1) {
      window.history.back();
    }
  }

  return (
    <div className="view-stack assets-view">
      <section className="table-panel assets-panel">
        <div className="panel-toolbar compact assets-toolbar">
          <div className="assets-toolbar-main">
            <div className="assets-title-block">
              <h2>Assets</h2>
              <p>Inventory assets and SQL/Python reference evidence.</p>
            </div>
            <div className="assets-metric-strip" aria-label="Asset inventory metrics">
              <MetricChip label="Assets" value={assetMetrics.assets} active={activeTab === "inventory"} onClick={showAllAssets} />
              <MetricChip label="References" value={assetMetrics.references} active={activeTab === "references"} onClick={() => setActiveTab("references")} />
              <MetricChip label="Needs mapping" value={assetMetrics.referencesNeedingMapping} active={activeTab === "references" && referenceFilters.resolutionState === NEEDS_MAPPING_FILTER} tone={assetMetrics.referencesNeedingMapping > 0 ? "warning" : "neutral"} onClick={showReferencesNeedingMapping} />
              <MetricChip label="Attention" value={activeTab === "references" ? assetMetrics.referenceAttention : assetMetrics.assetAttention} active={(activeTab === "inventory" && assetFilters.attentionState === "with_attention") || (activeTab === "references" && referenceFilters.attentionState === "with_attention")} tone={(activeTab === "references" ? assetMetrics.referenceAttention : assetMetrics.assetAttention) > 0 ? "warning" : "neutral"} onClick={showAttentionItems} />
              <MetricChip label="Visible" tone="view" value={activeTab === "inventory" ? filteredAssets.length : filteredReferences.length} />
            </div>
          </div>
        </div>

        <div className="assets-tabs" role="tablist" aria-label="Assets module views">
          <button type="button" className={activeTab === "inventory" ? "is-active" : ""} onClick={() => setActiveTab("inventory")}>Inventory</button>
          <button type="button" className={activeTab === "references" ? "is-active" : ""} onClick={() => setActiveTab("references")}>References</button>
        </div>

        {activeTab === "inventory" ? (
          <>
            <div className="assets-controls">
              <div className="assets-controls-row assets-filter-row assets-filter-row-inventory">
                <label className="search-box assets-search">
                  <Search size={14} />
                  <input value={assetQuery} onChange={(event) => setAssetQuery(event.target.value)} placeholder="Search asset id, table, path, connection, or format" />
                </label>
                <select value={assetFilters.connection} onChange={(event) => setAssetFilters((current) => ({ ...current, connection: event.target.value }))}>
                  <option value="">All connections</option>
                  {filterOptions.assets.connections.map((value) => <option key={value} value={value}>{value}</option>)}
                </select>
                <select value={assetFilters.format} onChange={(event) => setAssetFilters((current) => ({ ...current, format: event.target.value }))}>
                  <option value="">All formats</option>
                  {filterOptions.assets.formats.map((value) => <option key={value} value={value}>{value}</option>)}
                </select>
                <select value={assetFilters.assetType} onChange={(event) => setAssetFilters((current) => ({ ...current, assetType: event.target.value }))}>
                  <option value="">All types</option>
                  {filterOptions.assets.assetTypes.map((value) => <option key={value} value={value}>{compactHumanize(value)}</option>)}
                </select>
                <select value={assetFilters.role} onChange={(event) => setAssetFilters((current) => ({ ...current, role: event.target.value }))}>
                  <option value="">All roles</option>
                  {filterOptions.assets.roles.map((value) => <option key={value} value={value}>{value}</option>)}
                </select>
                <select value={assetFilters.attentionState} onChange={(event) => setAssetFilters((current) => ({ ...current, attentionState: event.target.value }))}>
                  <option value="">All attention states</option>
                  {filterOptions.assets.attentionStates.map((value) => <option key={value} value={value}>{humanize(value)}</option>)}
                </select>
                <button className="icon-action assets-reset-button" type="button" onClick={resetAssetFilters}>
                  <FilterX size={14} />
                  Reset
                </button>
              </div>
            </div>
            {filteredAssets.length ? (
              <DataTable<AssetRow>
                className="assets-table"
                columns={assetColumns}
                fixedLayout
                maxRows={Math.max(filteredAssets.length, 12)}
                onRowClick={(asset) => openAssetDrawer(asset.id)}
                rows={filteredAssets}
                sort={assetSort}
                onSort={handleAssetSort}
                rowClassName={(asset, index, rows) => [
                  connectionStageFamily(asset.connection_name) ? `assets-stage-${connectionStageFamily(asset.connection_name)}` : undefined,
                  assetSort.sortBy === RECOMMENDED_ASSET_SORT_KEY && startsConnectionGroup(asset, rows[index - 1])
                    ? "assets-connection-group-start"
                    : undefined,
                ].filter(Boolean).join(" ") || undefined}
              />
            ) : (
              <div className="table-empty">No assets match the current filters.</div>
            )}
            {resources.inventoryError ? <div className="table-empty">{toErrorMessage(resources.inventoryError)}</div> : null}
            {resources.inventoryLoading ? <div className="assets-page-status">Updating assets...</div> : null}
          </>
        ) : (
          <>
            <div className="assets-controls">
              <div className="assets-controls-row assets-filter-row assets-filter-row-references">
                <label className="search-box assets-search">
                  <Search size={14} />
                  <input value={referenceQuery} onChange={(event) => setReferenceQuery(event.target.value)} placeholder="Search reference, consumer, resolved asset, or method" />
                </label>
                <select value={referenceFilters.referenceType} onChange={(event) => setReferenceFilters((current) => ({ ...current, referenceType: event.target.value }))}>
                  <option value="">All types</option>
                  {filterOptions.references.referenceTypes.map((value) => <option key={value} value={value}>{compactHumanize(value)}</option>)}
                </select>
                <select value={referenceFilters.provenance} onChange={(event) => setReferenceFilters((current) => ({ ...current, provenance: event.target.value }))}>
                  <option value="">All sources</option>
                  {filterOptions.references.provenances.map((value) => <option key={value} value={value}>{compactHumanize(value)}</option>)}
                </select>
                <select value={referenceFilters.resolutionState} onChange={(event) => setReferenceFilters((current) => ({ ...current, resolutionState: event.target.value }))}>
                  <option value="">All resolutions</option>
                  {filterOptions.references.resolutionStates.map((value) => <option key={value} value={value}>{humanize(value)}</option>)}
                </select>
                <select value={referenceFilters.attentionState} onChange={(event) => setReferenceFilters((current) => ({ ...current, attentionState: event.target.value }))}>
                  <option value="">All attention states</option>
                  {filterOptions.references.attentionStates.map((value) => <option key={value} value={value}>{humanize(value)}</option>)}
                </select>
                <button className="icon-action assets-reset-button" type="button" onClick={resetReferenceFilters}>
                  <FilterX size={14} />
                  Reset
                </button>
              </div>
            </div>
            {filteredReferences.length ? (
              <DataTable<ReferenceRow>
                className="assets-table"
                columns={referenceColumns}
                fixedLayout
                maxRows={Math.max(filteredReferences.length, 12)}
                onRowClick={(reference) => openReferenceDrawer(reference.id)}
                rows={filteredReferences}
                sort={referenceSort}
                onSort={handleReferenceSort}
                rowClassName={referenceSort.sortBy === RECOMMENDED_ASSET_SORT_KEY
                  ? (reference, index, rows) => startsReferenceResolutionGroup(reference, rows[index - 1]) ? "assets-reference-resolution-group-start" : undefined
                  : undefined}
              />
            ) : (
              <div className="table-empty">No references match the current filters.</div>
            )}
            {resources.referencesError ? <div className="table-empty">{toErrorMessage(resources.referencesError)}</div> : null}
            {resources.referencesLoading ? <div className="assets-page-status">Updating references...</div> : null}
          </>
        )}
      </section>

      {drawerAsset && selectedAssetId && !selectedDataflowRecord && !selectedReference ? (
        <AssetsDrawer
          asset={drawerAsset}
          detail={assetDetail}
          loading={resources.assetDetailLoading}
          error={resources.assetDetailError ? toErrorMessage(resources.assetDetailError) : null}
          canGoBack={Boolean((assetsDrawerHistoryFromState(window.history.state)?.depth ?? 0) > 1)}
          onBack={backFromRelatedAssetDrawer}
          onClose={closeAssetDrawer}
          onSelectDataflow={openDataflowDrawer}
          onSelectReference={openReferenceDrawerFromAsset}
          attentionMappingLabels={attentionMappingLabels}
          onOpenReferenceMapping={openReferenceMappingFromAsset}
          onSelectRelatedAsset={openRelatedAssetDrawer}
          onFocusInLineage={onFocusInLineage}
          onOpenMetadata={onOpenMetadata}
          onLoadDefinition={async () => (await resources.loadAssetSource(selectedAssetId)).definition}
          onNavigateAway={clearDrawerSelection}
        />
      ) : null}

      {selectedDataflowRecord ? (
        <MetadataDataflowDrawer
          record={selectedDataflowRecord}
          editable={selectedDataflowEditable}
          readOnly={Boolean(activeMetadataDocument?.source.read_only)}
          busy={metadataBusy}
          connectionRows={activeMetadataDocument?.sheets.connections?.rows ?? []}
          connectionColumns={activeMetadataDocument?.sheets.connections?.columns ?? []}
          onSave={saveSelectedDataflow}
          onSaveDraft={saveSelectedDataflowDraft}
          onValidate={validateSelectedDataflow}
          onBack={backFromDataflowDrawer}
          onClose={closeDataflowDrawer}
          onFocusInLineage={(target) => {
            onFocusDataflowInLineage(target);
            clearDrawerSelection();
          }}
          onOpenMetadata={(target) => {
            onOpenMetadata(target);
            clearDrawerSelection();
          }}
        />
      ) : null}
      {pendingMetadataSave && sourceSaveConfirmation ? (
        <MetadataSourceSaveConfirmationDialog
          busy={metadataBusy}
          confirmation={sourceSaveConfirmation}
          onCancel={() => setPendingMetadataSave(null)}
          onConfirm={() => void confirmMetadataSave()}
        />
      ) : null}

      {selectedReference ? (
        <ReferenceDrawer
          environmentId={environmentId}
          reference={selectedReference}
          occurrences={referenceOccurrences.filter((item) => item.reference_id === selectedReference.id)}
          mappingMode={referenceDrawerMode === "mapping"}
          assets={inventory}
          mappingBusy={mappingBusy}
          canGoBack={Boolean(assetsDrawerHistoryFromState(window.history.state)?.depth && (assetsDrawerHistoryFromState(window.history.state)?.depth ?? 0) > 1)}
          onBack={() => window.history.back()}
          onClose={closeReferenceDrawer}
          onSelectAsset={openRelatedAssetDrawer}
          onOpenMetadata={onOpenMetadata}
          onOpenReferenceMapping={openReferenceMapping}
          onCreateReferenceMapping={onCreateReferenceMapping}
          onUpdateReferenceMapping={onUpdateReferenceMapping}
          onDeleteReferenceMapping={onDeleteReferenceMapping}
          onRefreshReferenceMappings={refreshAfterReferenceMapping}
          onSearchMappingTargets={searchMappingTargets}
          onLoadOccurrenceSource={(_, occurrenceId) => resources.loadOccurrenceSource(occurrenceId)}
          onNavigateAway={clearDrawerSelection}
        />
      ) : null}
    </div>
  );
}

function MetricChip({ label, value, tone = "neutral", active = false, onClick }: {
  label: string;
  value: number | string;
  tone?: "neutral" | "warning" | "view";
  active?: boolean;
  onClick?: () => void;
}) {
  const className = `assets-metric-chip tone-${tone}${active ? " is-active" : ""}`;
  const body = (
    <>
      <span>{label}</span>
      <strong>{typeof value === "number" ? formatNumber(value) : value}</strong>
    </>
  );
  if (!onClick) return <div className={className}>{body}</div>;
  return <button className={className} type="button" onClick={onClick}>{body}</button>;
}

function AssetCell({ asset }: { asset: AssetInventoryItem }) {
  const presentation = presentAsset(asset);
  const title = assetFriendlyAlias(asset, presentation.friendlyName);
  const subtitle = assetLocatorDetail(asset, title);
  const tooltip = [presentation.fullIdentity, `id: ${asset.id}`].filter(Boolean).join("\n");
  return (
    <span className="assets-asset-cell">
      <span className={`assets-asset-icon ${assetToneClass(asset.asset_type)}`}>
        <LineageFormatIcon kind={presentation.iconKind} label={presentation.badge} size={18} />
      </span>
      <span className="assets-asset-copy">
        <strong title={tooltip}>{title}</strong>
        {subtitle ? <small title={tooltip}>{subtitle}</small> : null}
      </span>
    </span>
  );
}

function ReferenceCell({ reference }: { reference: AssetReferenceGroupItem }) {
  const presentation = presentReference(reference);
  const referenceObjectType = referenceTypeAssetType(reference.reference_type);
  const subtitle = referenceContextLine(reference);
  return (
    <span className="assets-asset-cell">
      <span className={`assets-asset-icon ${assetToneClass(referenceObjectType)}`}>
        <Icon icon={assetTypeIconId(referenceObjectType)} width={18} height={18} aria-label={compactHumanize(reference.reference_type)} />
      </span>
      <span className="assets-asset-copy">
        <strong>{presentation.friendlyName}</strong>
        <small title={reference.normalized_value}>{subtitle || presentation.subtitle || reference.normalized_value}</small>
      </span>
    </span>
  );
}

function ConnectionCell({ asset }: { asset: AssetInventoryItem }) {
  const details = [asset.connection_type, asset.format].filter(Boolean).map(humanize).join(" · ");
  return (
    <span className="assets-connection-cell">
      <strong>{asset.connection_name || "-"}</strong>
      <small>{details || "-"}</small>
    </span>
  );
}

function ReferenceAssetCell({ asset, fallback }: { asset?: { friendly_name?: string; display_name?: string; full_identity?: string; connection_name?: string | null } | null; fallback?: string | null }) {
  const title = asset?.full_identity || asset?.display_name || fallback || "-";
  return (
    <span className="assets-connection-cell" title={title}>
      <strong>{asset?.friendly_name || asset?.display_name || fallback || "-"}</strong>
      <small>{asset?.full_identity || asset?.connection_name || "-"}</small>
    </span>
  );
}

function ReferenceTargetAssetCell({ asset, fallback }: {
  asset?: { asset_type?: string; connection_name?: string | null; display_name?: string; format?: string | null; friendly_name?: string; full_identity?: string } | null;
  fallback?: string | null;
}) {
  if (!asset) return <ReferenceAssetCell fallback={fallback} />;
  const title = asset.full_identity || asset.display_name || fallback || "-";
  const iconKind = assetIconKind(asset.format || asset.asset_type || "file");
  return (
    <span className="assets-asset-cell" title={title}>
      <span className={`assets-asset-icon ${assetToneClass(asset.asset_type)}`}>
        <LineageFormatIcon kind={iconKind} label={compactHumanize(asset.asset_type)} size={16} />
      </span>
      <span className="assets-asset-copy">
        <strong>{asset.friendly_name || asset.display_name || fallback || "-"}</strong>
        <small>{asset.full_identity || asset.connection_name || "-"}</small>
      </span>
    </span>
  );
}

function ReferenceSourceCell({ reference }: { reference: AssetReferenceGroupItem }) {
  if (!reference.provenances.length) {
    return <span className="assets-source-empty" title="No provenance recorded">-</span>;
  }
  const description = reference.provenances.map(referenceProvenanceDescription).join("; ");
  return (
    <span className="assets-source-list" title={description} aria-label={description}>
      {reference.provenances.map((source) => (
        <span
          className={`assets-source-token assets-source-tone-${referenceProvenanceTone(source)}`}
          key={source}
          title={referenceProvenanceDescription(source)}
        >
          {referenceProvenanceLabel(source)}
        </span>
      ))}
    </span>
  );
}

function ReferenceConsumersCell({ reference }: { reference: AssetReferenceGroupItem }) {
  const count = reference.consumer_asset_ids.length;
  const first = reference.consumer_assets[0] ?? null;
  if (!count) return <span className="assets-empty-inline">-</span>;
  if (count === 1) return <ReferenceAssetCell asset={first} fallback="1 consumer" />;
  const typeSummary = referenceConsumerTypeSummary(reference);
  return (
    <span className="assets-connection-cell" title={reference.consumer_assets.map((asset) => asset.full_identity || asset.display_name || asset.id).join("\n")}>
      <strong>{formatNumber(count)} consumers</strong>
      <small>{typeSummary || "multiple assets"}</small>
    </span>
  );
}

function ReferenceResolutionCell({ reference }: { reference: AssetReferenceGroupItem }) {
  const presentation = referenceResolutionPresentation(reference);
  const affected = reference.attention_count > 0 ? ` · ${formatNumber(reference.attention_count)} affected` : "";
  return (
    <span className="assets-lineage-cell">
      <span className={`assets-status-chip status-${presentation.state}`}>{presentation.label}</span>
      <small>{presentation.detail}{referenceNeedsMapping(reference) ? affected : ""}</small>
    </span>
  );
}

function ReferenceTargetCell({ reference }: { reference: AssetReferenceGroupItem }) {
  if (reference.resolved_asset || reference.resolved_asset_id) {
    return <ReferenceTargetAssetCell asset={reference.resolved_asset} fallback={reference.resolved_asset_id || "-"} />;
  }
  if (reference.candidate_asset_ids.length) {
    const first = reference.candidate_assets[0] ?? null;
    return (
      <span className="assets-connection-cell" title={reference.candidate_assets.map((asset) => asset.full_identity || asset.display_name || asset.id).join("\n")}>
        <strong>{formatNumber(reference.candidate_asset_ids.length)} candidates</strong>
        <small>{first?.friendly_name || first?.display_name || "mapping needed"}</small>
      </span>
    );
  }
  return <span className="assets-empty-inline">-</span>;
}

function AssetTypeCell({ asset }: { asset: AssetInventoryItem }) {
  return (
    <span className={`assets-type-cell ${assetToneClass(asset.asset_type)}`}>
      <span className="assets-type-icon" aria-hidden="true">
        <Icon icon={assetTypeIconId(asset.asset_type)} width={14} height={14} />
      </span>
      <span>{typeLabel(asset.asset_type)}</span>
    </span>
  );
}

function UsageCell({ asset }: { asset: AssetInventoryItem }) {
  const roles = asset.roles.length ? asset.roles.join(" + ") : "-";
  return <span className="assets-pill" title={asset.roles.join(", ")}>{roles}</span>;
}

function LineageCell({ asset }: { asset: AssetInventoryItem }) {
  return (
    <span className="assets-lineage-cell" title="Up/down assets and dataflows that read/write this asset">
      <strong>{asset.upstream_count} up · {asset.downstream_count} down</strong>
      <small>{asset.output_dataflow_count} read by · {asset.input_dataflow_count} written by</small>
    </span>
  );
}

function DependsOnCell({ asset }: { asset: AssetInventoryItem }) {
  return (
    <span className="assets-lineage-cell" title="Reference dependencies this asset reads or uses">
      <strong>{formatNumber(asset.depends_on_count)}</strong>
      <small>dependencies</small>
    </span>
  );
}

function UsedByCell({ asset }: { asset: AssetInventoryItem }) {
  return (
    <span className="assets-lineage-cell" title="Assets that use this asset through resolved SQL/Python references">
      <strong>{formatNumber(asset.used_by_count)}</strong>
      <small>consumers</small>
    </span>
  );
}

function ProvenanceCell({ asset }: { asset: AssetInventoryItem }) {
  const sourceCount = asset.metadata_source_count ?? asset.metadata_source_ids.length;
  const identifierCount = asset.identifier_count ?? asset.identifiers?.length ?? 0;
  const observationCount = asset.observation_count ?? asset.observations?.length ?? 0;
  const title = [
    `${formatNumber(sourceCount)} metadata source${sourceCount === 1 ? "" : "s"}`,
    `${formatNumber(identifierCount)} identifier${identifierCount === 1 ? "" : "s"}`,
    `${formatNumber(observationCount)} observation${observationCount === 1 ? "" : "s"}`,
  ].join(" · ");
  return (
    <span className="assets-evidence-cell" title={title}>
      <strong>{formatNumber(sourceCount)} source{sourceCount === 1 ? "" : "s"}</strong>
      <small>{formatNumber(identifierCount)} ids · {formatNumber(observationCount)} obs</small>
    </span>
  );
}

function AttentionCountCell({ count }: { count: number }) {
  return (
    <span className={count > 0 ? "assets-attention-count has-attention" : "assets-attention-count"}>
      {count > 0 ? `${formatNumber(count)} open` : "clean"}
    </span>
  );
}

function humanize(value: string | null | undefined) {
  if (!value) return "-";
  return value.replace(/_/g, " ");
}

function compactHumanize(value: string | null | undefined) {
  return humanize(value)
    .replace(/\bpython sql\b/giu, "py_sql")
    .replace(/\bpython function\b/giu, "py_function")
    .replace(/\bsql query\b/giu, "sql_query")
    .replace(/\bpython\b/giu, "py")
    .replace(/\s+/gu, "_");
}

function typeLabel(value: string | null | undefined) {
  return compactHumanize(value).toLocaleLowerCase();
}

function assetToneClass(assetType: string | null | undefined) {
  return `asset-tone-${assetTypeTone(assetType || "default")}`;
}

function sortTextOptions(values: string[]) {
  return [...values].sort((left, right) => left.localeCompare(right, undefined, { sensitivity: "base" }));
}

function calculateAssetMetrics(assets: AssetInventoryItem[], references: AssetReferenceGroupItem[], summary?: AssetInventoryResponse["summary"]) {
  return {
    assets: summary?.assets ?? assets.length,
    references: summary?.references ?? references.length,
    referencesNeedingMapping: summary?.unresolved_references
      ?? references.filter(referenceNeedsMapping).length,
    assetAttention: summary?.asset_attention ?? assets.filter((asset) => asset.attention_count > 0).length,
    referenceAttention: references.filter((reference) => reference.attention_count > 0).length,
  };
}

function referenceNeedsMapping(reference: AssetReferenceGroupItem) {
  return reference.resolution.state === "unresolved";
}

function assetFriendlyAlias(asset: AssetInventoryItem, fallback: string) {
  if (asset.friendly_name?.trim()) return asset.friendly_name.trim();
  if (asset.asset_type === "sql_query" || asset.query) return sqlQueryAlias(asset, fallback);
  if (asset.asset_type === "python_function" || asset.python_function) return pythonFunctionAlias(asset, fallback);
  if (asset.asset_type === "api") {
    const endpointAlias = apiEndpointAlias(asset);
    if (endpointAlias) return endpointAlias;
  }
  if (asset.asset_type === "table" && asset.table) return asset.table;
  if (asset.asset_type === "path" && asset.path) return compactPath(asset.path);
  if (asset.table) return asset.table;
  if (asset.path) return compactPath(asset.path);
  return asset.friendly_name || asset.display_name || fallback || asset.id;
}

function assetLocatorDetail(asset: AssetInventoryItem, alias: string) {
  const hasAlias = usesFriendlyAlias(asset, alias);
  if (asset.asset_type === "python_function" || asset.python_function) return functionContext(asset, alias, hasAlias);
  if (asset.asset_type === "sql_query" || asset.query) return asset.query ? sqlQueryContext(asset.query) : "";
  if (asset.asset_type === "api") {
    const endpoint = apiEndpointLabel(asset);
    if (endpoint) return hasAlias ? endpoint : apiEndpointContext(endpoint, alias);
  }
  if (asset.asset_type === "table" && asset.table) return tableContext(asset);
  if (asset.asset_type === "path" && asset.path) return pathContext(asset.path, alias);
  if (asset.table) return tableContext(asset);
  if (asset.path) return pathContext(asset.path, alias);
  return nonDuplicateText(asset.display_name || asset.full_identity || "", alias);
}

function tableContext(asset: AssetInventoryItem) {
  return [asset.catalog, asset.database, asset.schema_name].filter(Boolean).join(".");
}

function apiEndpointAlias(asset: AssetInventoryItem) {
  const endpoint = apiEndpointLabel(asset);
  if (!endpoint) return null;
  const withoutQuery = endpoint.split("?", 1)[0] || endpoint;
  const parts = withoutQuery.split("/").filter(Boolean);
  return parts.at(-1) || withoutQuery.replace(/^(GET|POST|PUT|PATCH|DELETE)\s+/iu, "") || null;
}

function apiEndpointLabel(asset: AssetInventoryItem) {
  if (asset.asset_type !== "api") return null;
  const identifiers = asset.identifiers ?? [];
  const identifier = identifiers.find((item) => String(item.kind || "") === "api_endpoint") ?? identifiers[0];
  const value = stringRecordValue(identifier, "display_value") || stringRecordValue(identifier, "normalized_value") || asset.display_name;
  const match = value.match(/\b(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+(\S+)/iu);
  if (match) return `${match[1].toUpperCase()} ${compactUrlPath(match[2])}`;
  return value || null;
}

function sqlQueryAlias(asset: AssetInventoryItem, fallback: string) {
  const candidates = [asset.friendly_name, asset.display_name, fallback]
    .map((value) => value?.trim())
    .filter((value): value is string => Boolean(value));
  return candidates.find((value) => value.toLocaleLowerCase() !== "sql query") || "SQL query";
}

function pythonFunctionAlias(asset: AssetInventoryItem, fallback: string) {
  const locator = functionLocator(asset);
  if (locator) return locator.split(".").filter(Boolean).at(-1) || locator;
  return asset.friendly_name || asset.display_name || fallback || asset.id;
}

function sqlQueryContext(query: string) {
  const tables = sqlTables(query);
  if (tables.length === 1) return `from ${tables[0]}`;
  if (tables.length > 1) return `${tables[0]} + ${tables.length - 1} table${tables.length === 2 ? "" : "s"}`;
  return compactSql(query);
}

function sqlTables(query: string | null | undefined) {
  if (!query) return [];
  const tables = Array.from(query.matchAll(/\b(?:from|join)\s+([`"[\]\w.-]+)/giu))
    .map((match) => cleanSqlIdentifier(match[1]))
    .filter((value): value is string => Boolean(value));
  return Array.from(new Set(tables));
}

function firstSqlTable(query: string | null | undefined) {
  if (!query) return null;
  const match = query.match(/\b(?:from|join)\s+([`"[\]\w.-]+)/iu);
  return cleanSqlIdentifier(match?.[1]);
}

function cleanSqlIdentifier(value: string | undefined) {
  return value?.replace(/^[`"[]|[`"\]]$/gu, "") || null;
}

function compactSql(query: string) {
  const table = firstSqlTable(query);
  if (table) return `from ${table}`;
  return normalizeWhitespace(query).slice(0, 80) || "sql query";
}

function compactPath(path: string) {
  const normalized = normalizeSlashes(path).replace(/\/+$/u, "");
  const parts = normalized.split("/").filter(Boolean);
  if (parts.length >= 3) return parts.slice(-3).join("/");
  return normalized || path;
}

function pathContext(path: string, alias: string) {
  const normalized = normalizeSlashes(path).replace(/\/+$/u, "");
  const normalizedAlias = normalizeSlashes(alias).replace(/\/+$/u, "");
  if (!normalizedAlias) return compactPath(normalized);
  if (normalized === normalizedAlias) return "";
  if (!normalized.endsWith(normalizedAlias)) return nonDuplicateText(compactPath(normalized), alias);
  const context = normalized.slice(0, -normalizedAlias.length).replace(/\/+$/u, "");
  return context ? compactPath(context) : "";
}

function apiEndpointContext(endpoint: string, alias: string) {
  const match = endpoint.match(/\b(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+(\S+)/iu);
  if (!match) return nonDuplicateText(endpoint, alias);
  const method = match[1].toUpperCase();
  const endpointPath = match[2].split("?", 1)[0]?.replace(/\/+$/u, "") || "";
  const parts = endpointPath.split("/").filter(Boolean);
  if (!parts.length) return method;
  const parentPath = `/${parts.slice(0, -1).join("/")}`.replace(/\/$/u, "");
  return parentPath ? `${method} ${parentPath}` : method;
}

function functionContext(asset: AssetInventoryItem, alias: string, showFull = false) {
  const functionName = functionLocator(asset);
  if (!functionName) return "python function";
  if (showFull) return nonDuplicateText(functionName, alias) || "python function";
  const parts = functionName.split(".").filter(Boolean);
  if (parts.length <= 1) return "python function";
  const parent = parts.slice(0, -1).join(".");
  return nonDuplicateText(parent, alias) || "python function";
}

function functionLocator(asset: AssetInventoryItem) {
  const candidates = [
    asset.python_function,
    asset.display_name,
    asset.full_identity.split(" · ").at(-1),
  ].filter((value): value is string => Boolean(value?.trim()));
  return candidates.find((value) => value.split(".").filter(Boolean).length > 1) || candidates[0] || null;
}

function nonDuplicateText(value: string, alias: string) {
  const normalizedValue = value.trim();
  if (!normalizedValue) return "";
  const normalizedAlias = alias.trim();
  if (!normalizedAlias) return normalizedValue;
  return normalizedValue.toLocaleLowerCase() === normalizedAlias.toLocaleLowerCase() ? "" : normalizedValue;
}

function usesFriendlyAlias(asset: AssetInventoryItem, title: string) {
  const friendlyName = asset.friendly_name?.trim();
  if (!friendlyName || friendlyName !== title.trim()) return false;
  const expectedLeaf = technicalLeaf(asset);
  return Boolean(expectedLeaf && friendlyName.toLocaleLowerCase() !== expectedLeaf.toLocaleLowerCase());
}

function technicalLeaf(asset: AssetInventoryItem) {
  if (asset.asset_type === "python_function" || asset.python_function) {
    const locator = functionLocator(asset);
    return locator?.split(".").filter(Boolean).at(-1) || locator || null;
  }
  if (asset.asset_type === "sql_query" || asset.query) return "SQL query";
  if (asset.asset_type === "api") return apiEndpointAlias(asset);
  if (asset.asset_type === "table" && asset.table) return asset.table;
  if (asset.asset_type === "path" && asset.path) return compactPath(asset.path);
  if (asset.table) return asset.table;
  if (asset.path) return compactPath(asset.path);
  return null;
}

function normalizeWhitespace(value: string) {
  return value.replace(/\s+/gu, " ").trim();
}

function compactUrlPath(value: string) {
  try {
    const url = new URL(value);
    return `${url.pathname || "/"}${url.search || ""}`;
  } catch {
    return value;
  }
}

function normalizeSlashes(value: string) {
  return value.replace(/\\/g, "/");
}

function stringRecordValue(value: unknown, key: string) {
  if (!value || typeof value !== "object") return null;
  const field = (value as Record<string, unknown>)[key];
  return typeof field === "string" && field.trim() ? field.trim() : null;
}

function useDebouncedValue<T>(value: T, delayMs: number) {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), delayMs);
    return () => window.clearTimeout(timer);
  }, [delayMs, value]);
  return debounced;
}
