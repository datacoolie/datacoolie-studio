import { Activity, Check, ChevronDown, FilterX, GitBranch, LocateFixed, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type MouseEvent } from "react";
import type { LatestStatusResponse, LineageResponse } from "../../shared/api/types";
import { EmptyState } from "../../shared/components/EmptyState";
import { LineageCanvas } from "./components/LineageCanvas";
import { LineageDetailsDrawer } from "./components/LineageDetailsDrawer";
import { LineageSearch } from "./components/LineageSearch";
import {
  createLineageGraphIndex,
  lineageFilterOptions,
  searchLineage,
  selectVisibleLineage
} from "./model/graphIndex";
import { presentLineageAsset } from "./model/presentation";
import type { LineageFilters, LineageFocus, LineageSearchResult, LineageSelection, TraceDirection } from "./model/types";
import { latestRun } from "./model/flow";

interface LineageViewProps {
  lineage: LineageResponse | null;
  latestStatus: LatestStatusResponse | null;
  loading: boolean;
  routeSearch?: string;
}

const EMPTY_FILTERS: LineageFilters = { connections: [], stages: [], formats: [], resolutions: [] };

export function LineageView({ lineage, latestStatus, loading, routeSearch }: LineageViewProps) {
  const [query, setQuery] = useState("");
  const [focuses, setFocuses] = useState<LineageFocus[]>([]);
  const [direction, setDirection] = useState<TraceDirection>("both");
  const [filters, setFilters] = useState<LineageFilters>(EMPTY_FILTERS);
  const [openFilter, setOpenFilter] = useState<string | null>(null);
  const [statusOverlay, setStatusOverlay] = useState(false);
  const [showReferences, setShowReferences] = useState(false);
  const [selection, setSelection] = useState<LineageSelection>(null);
  const index = useMemo(() => createLineageGraphIndex(lineage), [lineage]);
  const options = useMemo(() => lineageFilterOptions(index), [index]);
  const searchResults = useMemo(() => searchLineage(index, query), [index, query]);
  const visible = useMemo(
    () => selectVisibleLineage(index, filters, focuses, direction, showReferences),
    [index, filters, focuses, direction, showReferences]
  );
  const selectedEntity = selection?.kind === "asset" || selection?.kind === "reference"
    ? index.entityById.get(selection.id) ?? null
    : null;
  const selectedDataflow = selection?.kind === "dataflow" ? index.dataflowById.get(selection.id) ?? null : null;
  const selectedDependency = selection?.kind === "dependency" ? index.dependencyById.get(selection.id) ?? null : null;
  const selectedRun = selectedDataflow
    ? latestRun(latestStatus, selectedDataflow.dataflow_id, selectedDataflow.name)
    : null;
  const filtersActive = filters.connections.length > 0
    || filters.stages.length > 0
    || filters.formats.length > 0
    || filters.resolutions.length > 0;
  const visibleAssetCount = visible.entities.filter((entity) => "declaration_status" in entity).length;
  const visibleReferenceCount = visible.entities.length - visibleAssetCount;
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
    const params = new URLSearchParams(routeSearch ?? "");
    const nextQuery = params.get("q");
    if (nextQuery !== null) {
      setQuery(nextQuery);
    }
    const focusAsset = params.get("focusAsset");
    if (!focusAsset) {
      return;
    }
    const entity = index.entityById.get(focusAsset);
    if (!entity || !("declaration_status" in entity)) {
      return;
    }
    setFocuses([{ kind: "asset", id: focusAsset }]);
    setDirection("both");
    setSelection({ kind: "asset", id: focusAsset });
  }, [routeSearch, index]);

  if (!lineage && !loading) {
    return <EmptyState icon={<GitBranch size={24} />} title="Add metadata source to view lineage" />;
  }
  if (!lineage?.assets.length) {
    return <EmptyState icon={<GitBranch size={24} />} title={loading ? "Loading lineage" : "No lineage assets"} />;
  }

  function focusSearchResult(result: LineageSearchResult) {
    const nextFocus = { kind: result.kind, id: result.id };
    setFocuses((current) => current.some((item) => item.kind === nextFocus.kind && item.id === nextFocus.id)
      ? current
      : [...current, nextFocus]);
    setQuery("");
    setSelection({ kind: result.kind, id: result.id });
  }

  function resetView() {
    setQuery("");
    setFocuses([]);
    setFilters(EMPTY_FILTERS);
    setDirection("both");
    setSelection(null);
  }

  function removeFocus(focus: LineageFocus) {
    setFocuses((current) => current.filter((item) => item.kind !== focus.kind || item.id !== focus.id));
    if (selection?.kind === focus.kind && selection.id === focus.id) setSelection(null);
  }

  function focusLabel(focus: LineageFocus) {
    if (focus.kind === "asset" || focus.kind === "reference") {
      const entity = index.entityById.get(focus.id);
      return entity && "declaration_status" in entity
        ? presentLineageAsset(entity).fullIdentity
        : entity?.display_name || focus.id;
    }
    if (focus.kind === "dataflow") return index.dataflowById.get(focus.id)?.name || focus.id;
    const dependency = index.dependencyById.get(focus.id);
    return dependency ? `${dependency.provenance.replace(/_/g, " ")} ${dependency.kind}` : focus.id;
  }

  const relationCount = visible.dataflows.length + visible.dependencies.length;
  const resultTitle = focuses.length
    ? `${visibleAssetCount} assets · ${relationCount} relations in ${focuses.length} trace${focuses.length > 1 ? "s" : ""}`
    : filtersActive
      ? `${visibleAssetCount} assets · ${visible.dataflows.length} filtered dataflows`
      : `Full lineage · ${visibleAssetCount} assets · ${visible.dataflows.length} dataflows`;

  return (
    <div className="lineage-layout">
      <aside className="lineage-sidebar">
        <section className="lineage-filter-panel" aria-labelledby="lineage-filter-title">
          <div className="lineage-panel-heading">
            <div>
              <h3 id="lineage-filter-title">Explore lineage</h3>
              <p>Search by name, connection, path, or canonical identity to open its complete trace.</p>
            </div>
            <LocateFixed size={17} aria-hidden="true" />
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
                <button key={`${focus.kind}:${focus.id}`} type="button" title={focus.id} onClick={() => removeFocus(focus)}>
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
          <span className="lineage-filter-section-label">Filters</span>
          <div className="lineage-filter-fields">
            <FilterMultiSelect label="Connection" values={filters.connections} options={options.connections} emptyLabel="All connections" open={openFilter === "connection"} onOpenChange={(open) => setOpenFilter(open ? "connection" : null)} onChange={(connections) => setFilters((current) => ({ ...current, connections }))} />
            <FilterMultiSelect label="Stage" values={filters.stages} options={options.stages} emptyLabel="All stages" open={openFilter === "stage"} onOpenChange={(open) => setOpenFilter(open ? "stage" : null)} onChange={(stages) => setFilters((current) => ({ ...current, stages }))} />
            <FilterMultiSelect label="Format" values={filters.formats} options={options.formats} emptyLabel="All formats" open={openFilter === "format"} onOpenChange={(open) => setOpenFilter(open ? "format" : null)} onChange={(formats) => setFilters((current) => ({ ...current, formats }))} />
            <FilterMultiSelect label="Resolution" values={filters.resolutions} options={options.resolutions} emptyLabel="All resolution states" open={openFilter === "resolution"} onOpenChange={(open) => setOpenFilter(open ? "resolution" : null)} onChange={(resolutions) => setFilters((current) => ({ ...current, resolutions }))} />
          </div>
          <label className="lineage-status-toggle">
            <input type="checkbox" checked={showReferences} onChange={(event) => setShowReferences(event.target.checked)} />
            <span>Show unresolved references</span>
          </label>
          <button className="lineage-clear-button" type="button" disabled={!focuses.length && !filtersActive && direction === "both"} onClick={resetView}>
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
            <Activity size={17} aria-hidden="true" />
          </div>
          <label className="lineage-status-toggle">
            <input type="checkbox" checked={statusOverlay} onChange={(event) => setStatusOverlay(event.target.checked)} />
            <span>Color by latest known run</span>
          </label>
          {statusOverlay ? <StatusLegend /> : null}
        </section>

        <div className="lineage-summary-compact" aria-label="Visible lineage summary">
          <Metric label="Assets" value={`${visibleAssetCount}/${lineage.summary.assets}`} />
          <Metric label="Dataflows" value={`${visible.dataflows.length}/${lineage.summary.dataflows}`} />
          <Metric label="Unresolved" value={visibleReferenceCount || lineage.summary.references} />
        </div>
      </aside>

      <section className="lineage-workspace">
        <div className="lineage-result-bar" aria-live="polite">
          <div><strong>{resultTitle}</strong>{focuses.length ? <span> · {direction} trace</span> : null}</div>
          <span>{lineage.diagnostics.length ? `${lineage.diagnostics.length} diagnostics` : "Resolved lineage graph"}</span>
        </div>
        <div className={`lineage-canvas${selection ? " has-details" : ""}`}>
          <LineageCanvas
            visible={visible}
            latestStatus={latestStatus}
            statusOverlay={statusOverlay}
            selection={selection}
            layoutKey={layoutKey}
            onSelectionChange={setSelection}
            onReset={resetView}
          />
          <LineageDetailsDrawer
            entity={selectedEntity}
            dataflow={selectedDataflow}
            dependency={selectedDependency}
            run={selectedRun}
            entityById={index.entityById}
            onClose={() => setSelection(null)}
            onFocusItem={(focus) => {
              setFocuses((current) => current.some((item) => item.kind === focus.kind && item.id === focus.id)
                ? current
                : [...current, focus]);
            }}
          />
        </div>
      </section>
    </div>
  );
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
    if (event.ctrlKey || event.metaKey) {
      onChange(selected.has(option)
        ? values.filter((value) => value !== option)
        : [...values, option]);
      return;
    }
    if (allSelected && selected.has(option)) {
      onChange(values.filter((value) => value !== option));
      return;
    }
    onChange([option]);
  }

  return (
    <div className="lineage-filter-multiselect" ref={rootRef}>
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

function Metric({ label, value }: { label: string; value: number | string }) {
  return <div><span>{label}</span><strong>{value}</strong></div>;
}
