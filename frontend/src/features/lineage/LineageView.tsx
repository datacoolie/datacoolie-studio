import { Activity, Check, ChevronDown, FilterX, GitBranch, LocateFixed, X } from "lucide-react";
import { lazy, Suspense, useEffect, useMemo, useRef, useState, type MouseEvent } from "react";
import type { AssetInventoryItem, LineageDataflow, LineageResponse, MetadataEditorDocument, MonitoringRecord } from "../../shared/api/domainTypes";
import { EmptyState } from "../../shared/components/EmptyState";
import type { MetadataNavigationTarget } from "../../shared/metadataNavigation";
import { lineageDataflowFocusFromSearch, type LineageDataflowFocusTarget } from "../../shared/lineageNavigation";
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
import { LineageCanvas } from "./components/LineageCanvas";
import { LineageDetailsDrawer } from "./components/LineageDetailsDrawer";
import { LineageSearch } from "./components/LineageSearch";
import { disposeLineageLayoutWorker } from "./layout/elkLayout";
import {
  createLineageGraphIndex,
  findLineageDataflowByMetadataIdentity,
  lineageFilterOptions,
  searchLineage,
  selectVisibleLineage
} from "./model/graphIndex";
import { presentLineageAsset } from "./model/presentation";
import { isLineageAsset, type LineageFilters, type LineageFocus, type LineageSearchResult, type LineageSelection, type TraceDirection } from "./model/types";
import { toggleFilterValue } from "./model/filterSelection";
import type { ReferenceMappingPayload } from "../reference-mappings/referenceMappingModel";
import { useLineageLatestStatus } from "./lineageQueries";

const MonitoringDataflowRunDrawer = lazy(() => import("../monitoring/MonitoringDataflowRunDrawer").then((module) => ({ default: module.MonitoringDataflowRunDrawer })));

interface LineageViewProps {
  environmentId: number;
  lineage: LineageResponse;
  onRefreshLineage: () => Promise<unknown>;
  metadataEditorDocument: MetadataEditorDocument | null;
  metadataEditorDraft: MetadataEditorDocument | null;
  onEnsureMetadataEditor: () => Promise<void>;
  busy: boolean;
  onValidateMetadata: (document: MetadataEditorDocument) => Promise<MetadataEditorDocument>;
  onSaveMetadataDraft: (document: MetadataEditorDocument) => Promise<MetadataEditorDocument>;
  onSaveMetadata: (document: MetadataEditorDocument) => Promise<MetadataEditorDocument>;
  timezoneName?: string | null;
  routeSearch?: string;
  onOpenMetadata: (target: MetadataNavigationTarget) => void;
  onCreateReferenceMapping: (payload: ReferenceMappingPayload) => Promise<unknown>;
  onUpdateReferenceMapping: (mappingId: number, payload: ReferenceMappingPayload) => Promise<unknown>;
  onDeleteReferenceMapping: (mappingId: number) => Promise<unknown>;
}

const EMPTY_FILTERS: LineageFilters = { connections: [], stages: [], formats: [], resolutions: [] };
const LINEAGE_DATAFLOW_HISTORY_KEY = "datacoolieLineageDataflowDrawer";
const LINEAGE_MONITORING_DRAWER_HISTORY_KEY = "datacoolieLineageMonitoringDrawer";

function hasLineageMonitoringDrawerHistory(state: unknown) {
  if (!state || typeof state !== "object") return false;
  return (state as Record<string, unknown>)[LINEAGE_MONITORING_DRAWER_HISTORY_KEY] === true;
}

export function LineageView({ environmentId, lineage, onRefreshLineage, metadataEditorDocument, metadataEditorDraft, onEnsureMetadataEditor, busy, onValidateMetadata, onSaveMetadataDraft, onSaveMetadata, timezoneName, routeSearch, onOpenMetadata, onCreateReferenceMapping, onUpdateReferenceMapping, onDeleteReferenceMapping }: LineageViewProps) {
  const [query, setQuery] = useState("");
  const [focuses, setFocuses] = useState<LineageFocus[]>([]);
  const [direction, setDirection] = useState<TraceDirection>("both");
  const [filters, setFilters] = useState<LineageFilters>(EMPTY_FILTERS);
  const [openFilter, setOpenFilter] = useState<string | null>(null);
  const [statusOverlay, setStatusOverlay] = useState(false);
  const [showReferences, setShowReferences] = useState(false);
  const [selection, setSelection] = useState<LineageSelection>(null);
  const [selectedMetadataDataflow, setSelectedMetadataDataflow] = useState<MetadataDataflowSelection | null>(null);
  const [selectedMonitoringDataflowRun, setSelectedMonitoringDataflowRun] = useState<MonitoringRecord | null>(null);
  const monitoringDrawerHistoryRef = useRef(false);
  const [pendingMetadataSave, setPendingMetadataSave] = useState<MetadataEditorDocument | null>(null);
  const latestStatusQuery = useLineageLatestStatus(environmentId, statusOverlay || selection?.kind === "dataflow");
  const latestStatus = latestStatusQuery.data ?? null;
  const index = useMemo(() => createLineageGraphIndex(lineage), [lineage]);
  const activeMetadataDocument = metadataEditorDraft ?? metadataEditorDocument;
  const metadataDataflowRecords = useMemo(
    () => selectedMetadataDataflow ? buildMetadataDataflowRecords(activeMetadataDocument) : [],
    [activeMetadataDocument, selectedMetadataDataflow],
  );
  const metadataDataflowIds = useMemo(
    () => new Set(index.dataflows.map((dataflow) => dataflow.id)),
    [index.dataflows],
  );
  const selectedMetadataDataflowRecord = useMemo(
    () => findMetadataDataflowRecord(metadataDataflowRecords, selectedMetadataDataflow),
    [metadataDataflowRecords, selectedMetadataDataflow],
  );
  const selectedMetadataDataflowEditable = isEditableMetadataDataflowRecord(activeMetadataDocument, selectedMetadataDataflowRecord);
  const sourceSaveConfirmation = pendingMetadataSave ? metadataSaveConfirmation(metadataEditorDocument, pendingMetadataSave) : null;

  useEffect(() => () => disposeLineageLayoutWorker(), []);

  useEffect(() => {
    if (!selectedMetadataDataflow || activeMetadataDocument) return;
    void onEnsureMetadataEditor();
    // The workspace callback is intentionally excluded: drawer identity, not
    // parent renders, controls this action-driven resource load.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [environmentId, selectedMetadataDataflow?.metadataSourceId, selectedMetadataDataflow?.rowIndex, selectedMetadataDataflow?.dataflowId, selectedMetadataDataflow?.name, activeMetadataDocument]);
  const options = useMemo(() => lineageFilterOptions(index), [index]);
  const searchResults = useMemo(() => searchLineage(index, query), [index, query]);
  const visible = useMemo(
    () => selectVisibleLineage(index, filters, focuses, direction, showReferences),
    [index, filters, focuses, direction, showReferences]
  );
  const filtersActive = filters.connections.length > 0
    || filters.stages.length > 0
    || filters.formats.length > 0
    || filters.resolutions.length > 0;
  const activeFilterCount = filters.connections.length + filters.stages.length + filters.formats.length + filters.resolutions.length;
  const visibleAssetCount = visible.entities.filter(isLineageAsset).length;
  const visibleUnresolvedCount = visible.dependencies.filter((dependency) => dependency.resolution.state === "unresolved").length;
  const unresolvedCount = focuses.length || filtersActive || showReferences
    ? visibleUnresolvedCount
    : lineage?.summary.unresolved_dependencies ?? 0;
  const traceKey = useMemo(
    () => focuses.length
      ? [direction, ...focuses.map((focus) => `${focus.kind}:${focus.id}`)].join("|")
      : "",
    [direction, focuses]
  );
  const layoutKey = useMemo(
    () => [
      direction,
      ...focuses.map((item) => `${item.kind}:${item.id}`).sort(),
      ...visible.entities.map((entity) => entity.id),
      "--",
      ...visible.dataflows.map((item) => item.id),
      ...visible.dependencies.map((item) => item.id)
    ].join("|"),
    [direction, focuses, visible]
  );

  useEffect(() => {
    if ((selection?.kind === "asset" || selection?.kind === "reference")
      && !visible.entities.some((entity) => entity.id === selection.id)) {
      setSelection(null);
    }
    if (selection?.kind === "dataflow" && !visible.dataflows.some((item) => item.id === selection.id)) {
      setSelection(null);
    }
    if (selection?.kind === "dependency" && !visible.dependencies.some((item) => item.id === selection.id)) {
      setSelection(null);
    }
  }, [selection, visible]);

  useEffect(() => {
    if (selectedMetadataDataflow && !selectedMetadataDataflowRecord) setSelectedMetadataDataflow(null);
  }, [selectedMetadataDataflow, selectedMetadataDataflowRecord]);

  useEffect(() => {
    function handlePopState(event: PopStateEvent) {
      setSelectedMetadataDataflow(metadataDataflowSelectionFromHistory(event.state));
    }
    window.addEventListener("popstate", handlePopState);
    return () => window.removeEventListener("popstate", handlePopState);
  }, []);

  useEffect(() => {
    function handleMonitoringDrawerPopState(event: PopStateEvent) {
      if (!monitoringDrawerHistoryRef.current || hasLineageMonitoringDrawerHistory(event.state)) return;
      monitoringDrawerHistoryRef.current = false;
      setSelectedMonitoringDataflowRun(null);
    }
    window.addEventListener("popstate", handleMonitoringDrawerPopState);
    return () => window.removeEventListener("popstate", handleMonitoringDrawerPopState);
  }, []);

  useEffect(() => {
    const params = new URLSearchParams(routeSearch ?? "");
    const nextQuery = params.get("q");
    if (nextQuery !== null) {
      setQuery(nextQuery);
    }
    const focusDataflow = findLineageDataflowByMetadataIdentity(index, lineageDataflowFocusFromSearch(routeSearch ?? ""));
    if (focusDataflow) {
      setFocuses([{ kind: "dataflow", id: focusDataflow.id }]);
      setDirection("both");
      setSelection({ kind: "dataflow", id: focusDataflow.id });
      return;
    }
    const focusAsset = params.get("focusAsset");
    if (!focusAsset) {
      return;
    }
    const entity = index.entityById.get(focusAsset);
    if (!isLineageAsset(entity)) {
      return;
    }
    setFocuses([{ kind: "asset", id: focusAsset }]);
    setDirection("both");
    setSelection({ kind: "asset", id: focusAsset });
  }, [routeSearch, index]);

  if (!lineage.assets.length) {
    return <EmptyState icon={<GitBranch size={24} />} title="No lineage assets" />;
  }

  function focusSearchResult(result: LineageSearchResult) {
    const nextFocus = { kind: result.kind, id: result.id };
    setFocuses((current) => current.some((item) => item.kind === nextFocus.kind && item.id === nextFocus.id)
      ? current
      : [...current, nextFocus]);
    setQuery("");
    setSelection({ kind: result.kind, id: result.id });
  }

  function openMetadataDataflow(dataflow: LineageDataflow) {
    const nextSelection = dataflowSelection(dataflow);
    const historyState = window.history.state && typeof window.history.state === "object"
      ? { ...(window.history.state as Record<string, unknown>) }
      : {};
    window.history.pushState({ ...historyState, [LINEAGE_DATAFLOW_HISTORY_KEY]: nextSelection }, "", window.location.href);
    setSelectedMetadataDataflow(nextSelection);
  }

  function closeMetadataDataflow() {
    if (metadataDataflowSelectionFromHistory(window.history.state)) {
      window.history.back();
      return;
    }
    setSelectedMetadataDataflow(null);
  }

  function openMonitoringDataflowRun(run: MonitoringRecord) {
    const historyState = window.history.state && typeof window.history.state === "object"
      ? { ...(window.history.state as Record<string, unknown>) }
      : {};
    window.history.pushState({ ...historyState, [LINEAGE_MONITORING_DRAWER_HISTORY_KEY]: true }, "", window.location.href);
    monitoringDrawerHistoryRef.current = true;
    setSelectedMonitoringDataflowRun(run);
  }

  function closeMonitoringDataflowRun() {
    if (monitoringDrawerHistoryRef.current) {
      window.history.back();
      return;
    }
    setSelectedMonitoringDataflowRun(null);
  }

  function focusMetadataDataflow(target: LineageDataflowFocusTarget) {
    const dataflow = findLineageDataflowByMetadataIdentity(index, target);
    if (!dataflow) return;
    setFocuses([{ kind: "dataflow", id: dataflow.id }]);
    setDirection("both");
    setSelection({ kind: "dataflow", id: dataflow.id });
    closeMetadataDataflow();
  }

  function selectedDataflowDocument(nextRow: Record<string, unknown>) {
    if (!activeMetadataDocument || !selectedMetadataDataflowRecord || !selectedMetadataDataflowEditable) return null;
    return updateMetadataDataflowRow(activeMetadataDocument, selectedMetadataDataflowRecord.rowIndex, nextRow);
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
        closeMetadataDataflow();
      }
      return saved;
    } catch {
      return undefined;
    }
  }

  function resetView() {
    setQuery("");
    setFocuses([]);
    setFilters(EMPTY_FILTERS);
    setDirection("both");
    setShowReferences(false);
    setOpenFilter(null);
    setSelection(null);
  }

  function removeFocus(focus: LineageFocus) {
    setFocuses((current) => current.filter((item) => item.kind !== focus.kind || item.id !== focus.id));
    if (selection?.kind === focus.kind && selection.id === focus.id) setSelection(null);
  }

  function focusLabel(focus: LineageFocus) {
    if (focus.kind === "asset" || focus.kind === "reference") {
      const entity = index.entityById.get(focus.id);
      return isLineageAsset(entity)
        ? presentLineageAsset(entity).fullIdentity
        : entity?.display_name || focus.id;
    }
    if (focus.kind === "dataflow") return index.dataflowById.get(focus.id)?.name || focus.id;
    const dependency = index.dependencyById.get(focus.id);
    return dependency ? `${dependency.provenance.replace(/_/g, " ")} ${dependency.kind}` : focus.id;
  }

  const resultHeading = focuses.length ? "Trace lineage" : filtersActive ? "Filtered lineage" : "Full lineage";

  return (
    <div className="lineage-layout">
      <aside className="lineage-sidebar">
        <section className="lineage-filter-panel" aria-labelledby="lineage-filter-title">
          <div className="lineage-panel-heading">
            <div>
              <h3 id="lineage-filter-title">Explore lineage</h3>
              <p>Search by name, connection, path, or canonical identity to open its complete trace.</p>
            </div>
            <span className="lineage-panel-heading-icon is-explore"><LocateFixed size={16} aria-hidden="true" /></span>
          </div>
          <LineageSearch
            query={query}
            results={searchResults}
            onQueryChange={(nextQuery) => {
              setQuery(nextQuery);
            }}
            onSelect={focusSearchResult}
          />
          {focuses.length ? (
            <div className="lineage-focus-chips" aria-label="Focused lineage items">
              {focuses.map((focus) => (
                <button className={`kind-${focus.kind}`} key={`${focus.kind}:${focus.id}`} type="button" title={focus.id} onClick={() => removeFocus(focus)}>
                  <span>{focusLabel(focus)}</span>
                  <X size={12} aria-hidden="true" />
                </button>
              ))}
            </div>
          ) : null}
          <div className="lineage-direction">
            <span className="lineage-filter-section-label">Trace direction</span>
            <div className="segmented-control">
              {(["upstream", "both", "downstream"] as const).map((value) => (
                <button key={value} type="button" className={direction === value ? "active" : ""} onClick={() => setDirection(value)}>
                  {value === "both" ? "Both" : value[0].toUpperCase() + value.slice(1)}
                </button>
              ))}
            </div>
          </div>
          <span className="lineage-filter-section-label">Filters{filtersActive ? <span className="lineage-filter-count">{activeFilterCount}</span> : null}</span>
          <div className="lineage-filter-fields">
            <FilterMultiSelect label="Connection" values={filters.connections} options={options.connections} emptyLabel="All connections" open={openFilter === "connection"} onOpenChange={(open) => setOpenFilter(open ? "connection" : null)} onChange={(connections) => setFilters((current) => ({ ...current, connections }))} />
            <FilterMultiSelect label="Stage" values={filters.stages} options={options.stages} emptyLabel="All stages" open={openFilter === "stage"} onOpenChange={(open) => setOpenFilter(open ? "stage" : null)} onChange={(stages) => setFilters((current) => ({ ...current, stages }))} />
            <FilterMultiSelect label="Format" values={filters.formats} options={options.formats} emptyLabel="All formats" open={openFilter === "format"} onOpenChange={(open) => setOpenFilter(open ? "format" : null)} onChange={(formats) => setFilters((current) => ({ ...current, formats }))} />
            <FilterMultiSelect label="Resolution" values={filters.resolutions} options={options.resolutions} emptyLabel="All resolution states" open={openFilter === "resolution"} onOpenChange={(open) => setOpenFilter(open ? "resolution" : null)} onChange={(resolutions) => setFilters((current) => ({ ...current, resolutions }))} />
          </div>
          <label className={`lineage-status-toggle lineage-reference-toggle${showReferences ? " is-active" : ""}`}>
            <input type="checkbox" checked={showReferences} onChange={(event) => setShowReferences(event.target.checked)} />
            <span>Show unresolved references</span>
          </label>
          <button className="lineage-clear-button" type="button" disabled={!focuses.length && !filtersActive && direction === "both" && !showReferences} onClick={resetView}>
            <FilterX size={14} />
            Reset lineage view
          </button>
        </section>

        <section className="lineage-evidence-panel" aria-labelledby="lineage-evidence-title">
          <div className="lineage-panel-heading">
            <div>
              <h3 id="lineage-evidence-title">Run evidence</h3>
              <p>Latest run status colors dataflow arrows only.</p>
            </div>
            <span className="lineage-panel-heading-icon is-evidence"><Activity size={16} aria-hidden="true" /></span>
          </div>
          <label className={`lineage-status-toggle lineage-run-toggle${statusOverlay ? " is-active" : ""}`}>
            <input type="checkbox" checked={statusOverlay} onChange={(event) => {
              const enabled = event.target.checked;
              setStatusOverlay(enabled);
            }} />
            <span>Color by latest known run</span>
          </label>
          {statusOverlay ? <StatusLegend /> : null}
        </section>
      </aside>

      <section className="lineage-workspace">
        <div className="lineage-result-bar" aria-live="polite">
          <div className="lineage-result-summary">
            <strong>{resultHeading}</strong>
            <span className="lineage-result-stat">· {visibleAssetCount} assets</span>
            <span className="lineage-result-stat">· {visible.dataflows.length} dataflows</span>
            <span className={`lineage-result-stat${unresolvedCount ? " is-attention" : ""}`}>· {unresolvedCount} unresolved</span>
            {focuses.length ? <span className="lineage-result-context">· {direction} trace</span> : null}
          </div>
          <span>{lineage.summary.diagnostics ? `${lineage.summary.diagnostics} diagnostics` : "Resolved lineage graph"}</span>
        </div>
        <div className={`lineage-canvas${selection ? " has-details" : ""}`}>
          <LineageCanvas
            environmentId={environmentId}
            visible={visible}
            latestStatus={latestStatus}
            statusOverlay={statusOverlay}
            selection={selection}
            layoutKey={layoutKey}
            traceKey={traceKey}
            onSelectionChange={setSelection}
            onReset={resetView}
          />
          <LineageDetailsDrawer
            environmentId={environmentId}
            selection={selection}
            index={index}
            latestStatus={latestStatus}
            metadataDataflowIds={metadataDataflowIds}
            mappingAssets={lineage.assets as unknown as AssetInventoryItem[]}
            mappingBusy={busy}
            onCreateReferenceMapping={onCreateReferenceMapping}
            onUpdateReferenceMapping={onUpdateReferenceMapping}
            onDeleteReferenceMapping={onDeleteReferenceMapping}
            onRefreshReferenceMappings={async () => { await onRefreshLineage(); }}
            suspended={Boolean(selectedMetadataDataflowRecord)}
            onOpenDataflowDetails={openMetadataDataflow}
            onOpenMonitoringDataflowRun={openMonitoringDataflowRun}
            onClose={() => setSelection(null)}
            onFocusItem={(focus) => {
              setFocuses((current) => current.some((item) => item.kind === focus.kind && item.id === focus.id)
                ? current
                : [...current, focus]);
            }}
          />
        </div>
      </section>
      {selectedMetadataDataflowRecord ? (
        <MetadataDataflowDrawer
          record={selectedMetadataDataflowRecord}
          editable={selectedMetadataDataflowEditable}
          readOnly={Boolean(activeMetadataDocument?.source.read_only)}
          busy={busy}
          connectionRows={activeMetadataDocument?.sheets.connections?.rows ?? []}
          connectionColumns={activeMetadataDocument?.sheets.connections?.columns ?? []}
          onSave={saveSelectedDataflow}
          onSaveDraft={saveSelectedDataflowDraft}
          onValidate={validateSelectedDataflow}
          onBack={closeMetadataDataflow}
          onClose={closeMetadataDataflow}
          onFocusInLineage={focusMetadataDataflow}
          onOpenMetadata={onOpenMetadata}
        />
      ) : null}
      {selectedMonitoringDataflowRun ? (
        <Suspense fallback={null}>
          <MonitoringDataflowRunDrawer
            key={String(selectedMonitoringDataflowRun.dataflow_run_id ?? "")}
            environmentId={environmentId}
            row={selectedMonitoringDataflowRun}
            timezoneName={timezoneName ?? "UTC"}
            onBack={closeMonitoringDataflowRun}
            onClose={closeMonitoringDataflowRun}
          />
        </Suspense>
      ) : null}
      {pendingMetadataSave && sourceSaveConfirmation ? (
        <MetadataSourceSaveConfirmationDialog
          busy={busy}
          confirmation={sourceSaveConfirmation}
          onCancel={() => setPendingMetadataSave(null)}
          onConfirm={() => void confirmMetadataSave()}
        />
      ) : null}
    </div>
  );
}

function dataflowSelection(dataflow: LineageDataflow): MetadataDataflowSelection {
  return {
    metadataSourceId: dataflow.metadata_source_id,
    dataflowId: dataflow.dataflow_id,
    name: dataflow.name,
  };
}

function metadataDataflowSelectionFromHistory(state: unknown): MetadataDataflowSelection | null {
  if (!state || typeof state !== "object") return null;
  const value = (state as Record<string, unknown>)[LINEAGE_DATAFLOW_HISTORY_KEY];
  if (!value || typeof value !== "object") return null;
  const record = value as Record<string, unknown>;
  const metadataSourceId = typeof record.metadataSourceId === "number" ? record.metadataSourceId : null;
  const dataflowId = typeof record.dataflowId === "string" ? record.dataflowId : null;
  const name = typeof record.name === "string" ? record.name : null;
  return dataflowId || name ? { metadataSourceId, dataflowId, name } : null;
}

function FilterMultiSelect({ label, values, options, emptyLabel, open, onOpenChange, onChange }: {
  label: string;
  values: string[];
  options: string[];
  emptyLabel: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onChange: (value: string[]) => void;
}) {
  const rootRef = useRef<HTMLDivElement | null>(null);
  const selected = new Set(values);
  const allSelected = options.length > 0 && values.length === options.length;
  const buttonLabel = values.length
    ? allSelected ? "All selected" : values.length === 1 ? values[0] : `${values.length} selected`
    : emptyLabel;

  useEffect(() => {
    if (!open) return;
    function closeOnOutsidePointer(event: PointerEvent) {
      if (!rootRef.current?.contains(event.target as Node)) onOpenChange(false);
    }
    document.addEventListener("pointerdown", closeOnOutsidePointer);
    return () => document.removeEventListener("pointerdown", closeOnOutsidePointer);
  }, [open, onOpenChange]);

  function selectAll() {
    onChange(options);
  }

  function selectValue(option: string, event: MouseEvent<HTMLButtonElement>) {
    onChange(toggleFilterValue(values, option, event.ctrlKey || event.metaKey));
  }

  return (
    <div className={`lineage-filter-multiselect${values.length ? " has-value" : ""}`} ref={rootRef}>
      <div className="lineage-filter-label-row">
        <span>{label}</span>
        {values.length ? (
          <button type="button" onClick={() => onChange([])}>
            Clear
          </button>
        ) : null}
      </div>
      <button
        type="button"
        className="lineage-filter-trigger"
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => onOpenChange(!open)}
        onKeyDown={(event) => {
          if (event.key === "Escape") onOpenChange(false);
        }}
      >
        <span>{buttonLabel}</span>
        <ChevronDown size={14} aria-hidden="true" />
      </button>
      {open ? (
        <div className="lineage-filter-menu" role="listbox" aria-label={`${label} filter`}>
          {options.length ? (
            <button
              type="button"
              className={`lineage-filter-menu-action${allSelected ? " selected" : ""}`}
              role="option"
              aria-selected={allSelected}
              onClick={selectAll}
            >
              <span>Select all</span>
              {allSelected ? <Check size={13} aria-hidden="true" /> : null}
            </button>
          ) : null}
          {options.length ? options.map((option) => (
            <button
              key={option}
              type="button"
              role="option"
              aria-selected={selected.has(option)}
              className={selected.has(option) ? "selected" : ""}
              onClick={(event) => selectValue(option, event)}
            >
              <span>{option}</span>
              {selected.has(option) ? <Check size={13} aria-hidden="true" /> : null}
            </button>
          )) : <div className="lineage-filter-empty">No values</div>}
        </div>
      ) : null}
    </div>
  );
}

function StatusLegend() {
  return (
    <div className="lineage-status-legend" aria-label="Latest run status colors">
      <span><i className="lineage-legend-dot status-bg-succeeded" />Succeeded</span>
      <span><i className="lineage-legend-dot status-bg-failed" />Failed</span>
      <span><i className="lineage-legend-dot status-bg-skipped" />Skipped</span>
      <span><i className="lineage-legend-dot status-bg-unknown" />No log</span>
    </div>
  );
}
