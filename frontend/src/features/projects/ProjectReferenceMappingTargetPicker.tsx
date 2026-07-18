import { Check, ChevronDown } from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent } from "react";
import {
  filterProjectMappingTargets,
  projectTargetCoverage,
  type ProjectMappingTarget,
  type ProjectReferenceRegistryRow,
} from "./projectReferenceMappingRegistryModel";

interface ProjectReferenceMappingTargetPickerProps {
  row: ProjectReferenceRegistryRow;
  targets: ProjectMappingTarget[];
  selectedTargetId: string | null;
  open: boolean;
  disabled: boolean;
  onOpen: () => void;
  onClose: () => void;
  onTargetChange: (targetId: string) => void;
}

/** Selects a canonical project target without exposing environment asset IDs. */
export function ProjectReferenceMappingTargetPicker({
  row,
  targets,
  selectedTargetId,
  open,
  disabled,
  onOpen,
  onClose,
  onTargetChange,
}: ProjectReferenceMappingTargetPickerProps) {
  const listId = useId();
  const pickerRef = useRef<HTMLDivElement | null>(null);
  const searchRef = useRef<HTMLInputElement | null>(null);
  const [query, setQuery] = useState("");
  const selectedTarget = targets.find((target) => target.id === selectedTargetId) ?? null;
  const matchingTargets = useMemo(
    () => filterProjectMappingTargets(row, targets, { query, connectionName: "" }),
    [query, row, targets],
  );

  useEffect(() => {
    if (!open) return;
    setQuery("");
    const frame = window.requestAnimationFrame(() => searchRef.current?.focus());
    return () => window.cancelAnimationFrame(frame);
  }, [open]);

  useEffect(() => {
    if (!open) return undefined;
    const handlePointerDown = (event: PointerEvent) => {
      if (pickerRef.current?.contains(event.target as Node)) return;
      onClose();
    };
    window.addEventListener("pointerdown", handlePointerDown, true);
    return () => window.removeEventListener("pointerdown", handlePointerDown, true);
  }, [onClose, open]);

  function selectTarget(target: ProjectMappingTarget) {
    onTargetChange(target.id);
    onClose();
  }

  function handleSearchKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Escape") {
      event.preventDefault();
      onClose();
      return;
    }
    if (event.key === "Enter" && matchingTargets.length === 1) {
      event.preventDefault();
      selectTarget(matchingTargets[0]);
    }
  }

  return (
    <div className="reference-mapping-picker reference-mapping-table-picker" ref={pickerRef} onClick={(event) => event.stopPropagation()}>
      <button
        className={selectedTarget || row.observedTargets.length > 1 ? "reference-mapping-picker-button" : "reference-mapping-picker-button is-empty"}
        type="button"
        disabled={disabled}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        onClick={() => open ? onClose() : onOpen()}
      >
        <span>
          {selectedTarget ? (
            <>
              <strong>{selectedTarget.displayName}</strong>
              <small>
                {selectedTarget.connectionName ? <><span className="reference-mapping-target-connection">{selectedTarget.connectionName}</span> · </> : null}
                {selectedTarget.kind} · {selectedTarget.value}
              </small>
            </>
          ) : row.observedTargets.length > 1 ? (
            <>
              <strong>Multiple automatic targets · {row.observedTargets.length}</strong>
              <small>Select a canonical asset to override</small>
            </>
          ) : <span className="reference-mapping-picker-placeholder">Choose canonical asset</span>}
        </span>
        <ChevronDown size={14} aria-hidden="true" />
      </button>

      {open ? (
        <div className="reference-mapping-picker-menu reference-mapping-target-menu" id={listId} role="listbox" aria-label={`Targets for ${row.normalizedValue}`}>
          <label className="reference-mapping-picker-search">
            <input
              ref={searchRef}
              value={query}
              role="combobox"
              aria-label={`Search mapping target for ${row.normalizedValue}`}
              aria-autocomplete="list"
              aria-controls={listId}
              aria-expanded="true"
              placeholder="Search canonical targets"
              disabled={disabled}
              onChange={(event) => setQuery(event.target.value)}
              onKeyDown={handleSearchKeyDown}
            />
          </label>
          <div className="reference-mapping-picker-options">
            {matchingTargets.map((target) => {
              const selected = target.id === selectedTarget?.id;
              const coverage = projectTargetCoverage(row, target);
              const candidate = target.assetIds.some((assetId) => row.candidateAssetIds.includes(assetId));
              const observed = row.observedTargets.find((item) => item.target.id === target.id);
              return (
                <button
                  className={`reference-mapping-picker-option${selected ? " is-selected" : ""}`}
                  key={target.id}
                  type="button"
                  role="option"
                  aria-selected={selected}
                  disabled={disabled}
                  onClick={() => selectTarget(target)}
                >
                  <span>
                    <strong>{target.displayName}</strong>
                    <small><span className="reference-mapping-picker-kind">{target.kind}</span> · {target.value}</small>
                  </span>
                  <em>{observed ? `automatic · ${observed.environmentNames.length} envs · ` : candidate ? "candidate · " : ""}{coverage.total ? `${coverage.available}/${coverage.total}` : "catalog"}</em>
                  {selected ? <Check size={14} aria-label="Selected" /> : null}
                </button>
              );
            })}
            {!matchingTargets.length ? <div className="reference-mapping-picker-empty">No canonical target matches this search.</div> : null}
          </div>
        </div>
      ) : null}
    </div>
  );
}
