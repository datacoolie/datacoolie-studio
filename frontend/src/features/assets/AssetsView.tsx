import { Boxes, FilterX, Search } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Icon } from "@iconify/react";
import type { AssetInventoryItem, AssetInventoryResponse, AssetReferenceGroupItem, MetadataEditorDocument, MetadataWriteOperation } from "../../shared/api/domainTypes";
import { EmptyState } from "../../shared/components/EmptyState";
import type { MetadataDataflowSelection } from "../../shared/metadata/metadataDataflowModel";
import { useMetadataDataflowEditor } from "../../shared/metadata/useMetadataDataflowEditor";
import { MetadataEditorLoadingDrawer } from "../dataflows/MetadataEditorLoadingDrawer";
import type { MetadataNavigationTarget } from "../../shared/metadataNavigation";
import type { LineageDataflowFocusTarget } from "../../shared/lineageNavigation";
import { toErrorMessage } from "../../shared/lib/errors";
import { connectionStageFamily } from "../../shared/connectionOrder";
import { DataTable, formatNumber, type TableColumn } from "../../shared/components/DataTable";
import { LineageFormatIcon } from "../../shared/components/FormatIcon";
import { assetIconKind, assetTypeIconId, assetTypeTone, referenceTypeAssetType } from "../../shared/assetPresentation";
import { assetFriendlyAlias, assetLocatorDetail } from "../../shared/assetNaming";
import { MetadataDataflowDrawer } from "../dataflows/MetadataDataflowDrawer";
import { MetadataSourceSaveConfirmationDialog } from "../../shared/metadata/MetadataSourceSaveConfirmationDialog";
import { presentAsset, presentReference, referenceConsumerTypeSummary, referenceContextLine, referenceProvenanceDescription, referenceProvenanceLabel, referenceProvenanceTone, referenceResolutionPresentation } from "./assetsPresentation";
import { orderAssetsByConnection, orderReferencesByAction, RECOMMENDED_ASSET_SORT_KEY, startsConnectionGroup, startsReferenceResolutionGroup } from "./assetsOrdering";
import { AssetsDrawer } from "./AssetsDrawer";
import { referenceMappingAction, referenceMappingActionLabel, type ReferenceMappingPayload } from "../reference-mappings/referenceMappingModel";
import { ReferenceMappingClearAction } from "../reference-mappings/ReferenceMappingClearAction";
import { ReferenceDrawer } from "./ReferenceDrawer";
import { useAssetsResources } from "./assetsQueries";
import { useAssetsInventoryController } from "./useAssetsInventoryController";
import { NEEDS_MAPPING_FILTER, useAssetsReferenceController } from "./useAssetsReferenceController";

interface AssetsViewProps {
  environmentId: number;
  metadataEditorDocument: MetadataEditorDocument | null;
  metadataEditorDraft: MetadataEditorDocument | null;
  onEnsureMetadataEditor: () => Promise<void>;
  metadataBusy: boolean;
  onSaveMetadataDraft: (document: MetadataEditorDocument) => Promise<MetadataEditorDocument>;
  onPrepareMetadataWrite: (document: MetadataEditorDocument) => Promise<MetadataWriteOperation>;
  onApplyMetadataWrite: (operation: MetadataWriteOperation) => Promise<MetadataEditorDocument>;
  onAbandonMetadataWrite: (operation: MetadataWriteOperation) => Promise<MetadataWriteOperation>;
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

const ASSETS_DRAWER_SEARCH_KEYS = [
  "assetId",
  "referenceId",
  "dataflowSourceId",
  "dataflowId",
  "dataflowName",
] as const;

const ASSETS_DRAWER_HISTORY_KEY = "datacoolieAssetsDrawer";

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
  onSaveMetadataDraft,
  onPrepareMetadataWrite,
  onApplyMetadataWrite,
  onAbandonMetadataWrite,
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
  const inventoryController = useAssetsInventoryController({
    onActivateTab: () => setActiveTab("inventory"),
  });
  const referenceController = useAssetsReferenceController({
    onActivateTab: () => setActiveTab("references"),
  });
  const {
    query: assetQuery,
    setQuery: setAssetQuery,
    filters: assetFilters,
    setFilters: setAssetFilters,
    sort: assetSort,
    parameters: inventoryParameters,
    reset: resetAssetFilters,
    handleSort: handleAssetSort,
    showAll: showAllAssets,
    showAttention: showAttentionAssets,
  } = inventoryController;
  const {
    query: referenceQuery,
    setQuery: setReferenceQuery,
    filters: referenceFilters,
    setFilters: setReferenceFilters,
    sort: referenceSort,
    parameters: referenceParameters,
    selectedId: selectedReferenceId,
    setSelectedId: setSelectedReferenceId,
    drawerMode: referenceDrawerMode,
    setDrawerMode: setReferenceDrawerMode,
    clearId: clearReferenceId,
    setClearId: setClearReferenceId,
    actionError: referenceActionError,
    reset: resetReferenceFilters,
    handleSort: handleReferenceSort,
    showNeedingMapping: showReferencesNeedingMapping,
    showAttention: showAttentionReferences,
    clearMapping: clearReferenceMapping,
  } = referenceController;
  const [selectedAssetTrail, setSelectedAssetTrail] = useState<string[]>([]);
  const [selectedDataflow, setSelectedDataflow] = useState<MetadataDataflowSelection | null>(null);
  const selectedAssetId = selectedAssetTrail.length ? selectedAssetTrail[selectedAssetTrail.length - 1] : null;
  const resources = useAssetsResources({
    environmentId,
    activeTab,
    inventoryParameters,
    referenceParameters,
    selectedAssetId,
    selectedReferenceId,
  });

  const {
    activeDocument: activeMetadataDocument,
    selectedRecord: selectedDataflowRecord,
    selectedEditable: selectedDataflowEditable,
    pendingDocument: pendingMetadataSave,
    cancelSave: cancelMetadataSave,
    saveConfirmation: sourceSaveConfirmation,
    saveDraft: saveSelectedDataflowDraft,
    save: saveSelectedDataflow,
    confirmSave: confirmMetadataSave,
    loading: editorLoading,
    loadError: editorLoadError,
    retryEditor,
  } = useMetadataDataflowEditor({
    environmentId,
    sourceDocument: metadataEditorDocument,
    draftDocument: metadataEditorDraft,
    selection: selectedDataflow,
    onEnsureEditor: onEnsureMetadataEditor,
    onSaveDraft: onSaveMetadataDraft,
    onPrepareSave: onPrepareMetadataWrite,
    onApplySave: onApplyMetadataWrite,
    onAbandonSave: onAbandonMetadataWrite,
    onSaved: closeDataflowDrawer,
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
  const searchMappingTargets = useCallback(async (query: string, connectionName: string) => (
    await resources.searchAssets({
      q: query.trim() || undefined,
      connection: connectionName || undefined,
    })
  ).items, [resources.searchAssets]);

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
                onClear={() => void clearReferenceMapping(reference, onDeleteReferenceMapping, resources.refreshAfterMapping)}
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

  async function refreshAfterReferenceMapping() {
    await resources.refreshAfterMapping();
  }

  function showAttentionItems() {
    if (activeTab === "references") return showAttentionReferences();
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

      {drawerAsset && selectedAssetId && !selectedDataflow && !selectedReference ? (
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

      {selectedDataflow && !selectedDataflowRecord ? <MetadataEditorLoadingDrawer loading={editorLoading} error={editorLoadError ?? (!editorLoading ? "The selected dataflow is no longer available." : null)} onRetry={retryEditor} onClose={closeDataflowDrawer} /> : null}
      {selectedDataflowRecord ? (
        <MetadataDataflowDrawer
          record={selectedDataflowRecord}
          environmentId={environmentId}
          pendingSqlFiles={activeMetadataDocument?.sql_files ?? []}
          editable={selectedDataflowEditable}
          readOnly={Boolean(activeMetadataDocument?.source.read_only) || Boolean(selectedDataflowRecord?.readOnly)}
          busy={metadataBusy}
          connectionRows={activeMetadataDocument?.sheets.connections?.rows ?? []}
          connectionColumns={activeMetadataDocument?.sheets.connections?.columns ?? []}
          onSave={saveSelectedDataflow}
          onSaveDraft={saveSelectedDataflowDraft}
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
          onCancel={() => void cancelMetadataSave()}
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
