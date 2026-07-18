import { History, Save, Search, Upload, X } from "lucide-react";
import { useEffect, useRef } from "react";
import type { SheetKey } from "./metadataSheetTypes";

interface MetadataSheetToolbarProps {
  activeSheet: SheetKey;
  busy: boolean;
  hasLocalChanges: boolean;
  hasSourceChanges: boolean;
  hasStoredDraft: boolean;
  filteredRowCount: number;
  totalRowCount: number;
  mode: "view" | "edit";
  query: string;
  readOnly?: boolean;
  sourceFormat: string;
  sourceUri: string;
  onActiveSheetChange: (sheet: SheetKey) => void;
  onDiscard: () => void;
  onDiscardDraft: () => void;
  onHistoryOpen: () => void;
  onModeChange: (mode: "view" | "edit") => void;
  onQueryChange: (query: string) => void;
  onSave: () => void;
  onSaveDraft: () => void;
  onValidate: () => void;
}

const sheets = [
  { key: "connections", label: "Connections" },
  { key: "dataflows", label: "Dataflows" },
  { key: "schema_hints", label: "Schema hints" }
] satisfies Array<{ key: SheetKey; label: string }>;

export function MetadataSheetToolbar({
  activeSheet,
  busy,
  hasLocalChanges,
  hasSourceChanges,
  hasStoredDraft,
  filteredRowCount,
  totalRowCount,
  mode,
  query,
  readOnly = false,
  sourceFormat,
  sourceUri,
  onActiveSheetChange,
  onDiscard,
  onDiscardDraft,
  onHistoryOpen,
  onModeChange,
  onQueryChange,
  onSave,
  onSaveDraft,
  onValidate
}: MetadataSheetToolbarProps) {
  const findInputRef = useRef<HTMLInputElement>(null);
  const hasQuery = Boolean(query.trim());

  useEffect(() => {
    const focusFind = (event: KeyboardEvent) => {
      if (!(event.ctrlKey || event.metaKey) || event.key.toLowerCase() !== "f") return;
      const activeElement = document.activeElement;
      if (activeElement instanceof HTMLElement && activeElement.closest(".metadata-json-editor")) return;
      event.preventDefault();
      findInputRef.current?.focus();
      findInputRef.current?.select();
    };

    document.addEventListener("keydown", focusFind, true);
    return () => document.removeEventListener("keydown", focusFind, true);
  }, []);

  return (
    <div className="panel-toolbar metadata-sheet-toolbar">
      <div className="metadata-sheet-title">
        <h2>{sheetTitle(activeSheet)}</h2>
        <span title={sourceUri}>
          {sourceFormat === "merged" ? "Merged sources" : `${sourceFormat.toUpperCase()} · ${shortPath(sourceUri)}`}
          {readOnly ? " · save disabled" : ""}
        </span>
      </div>
      <div className={`metadata-sheet-controls${mode === "edit" ? " edit-mode" : ""}`}>
        <div className="metadata-sheet-switcher">
          <nav className="tabs metadata-tabs" aria-label="Metadata sheets">
            {sheets.map((item) => (
              <button key={item.key} className={activeSheet === item.key ? "active" : ""} type="button" onClick={() => onActiveSheetChange(item.key)}>
                {item.label}
              </button>
            ))}
          </nav>
          <div className="segmented-control" aria-label="Metadata mode">
            <button className={mode === "view" ? "active" : ""} type="button" onClick={() => onModeChange("view")}>
              View
            </button>
            <button className={mode === "edit" ? "active" : ""} type="button" onClick={() => onModeChange("edit")}>
              Edit
            </button>
          </div>
        </div>
        <div className="metadata-sheet-actions">
          {mode === "edit" ? (
            <div className="metadata-edit-actions">
              {!readOnly ? (
                <>
                  <button className="text-action primary" type="button" onClick={() => onSave()} disabled={!hasSourceChanges || busy}>
                    <Save size={14} />
                    Save to source
                  </button>
                  <button className="text-action" type="button" onClick={() => onSaveDraft()} disabled={!hasLocalChanges || busy}>
                    Save draft
                  </button>
                </>
              ) : null}
              <button className="text-action" type="button" onClick={() => onValidate()} disabled={busy}>
                Validate
              </button>
              <button className="text-action" type="button" onClick={onDiscard} disabled={!hasLocalChanges || busy}>
                Discard
              </button>
              {!readOnly ? (
                <button className="text-action" type="button" onClick={onDiscardDraft} disabled={!hasStoredDraft || busy}>
                  Clear draft
                </button>
              ) : null}
            </div>
          ) : null}
          {mode === "view" && !readOnly && hasSourceChanges ? (
            <button className="text-action primary metadata-save-source-action" type="button" onClick={() => onSave()} disabled={busy}>
              <Upload size={14} />
              Save to source
            </button>
          ) : null}
          {!readOnly ? (
            <button className="text-action metadata-history-action" type="button" onClick={onHistoryOpen} disabled={busy}>
              <History size={14} />
              History
            </button>
          ) : null}
          <div className={`metadata-find${hasQuery ? " active" : ""}`}>
            <label className="search-box metadata-find-input">
              <Search size={16} />
              <input
                ref={findInputRef}
                value={query}
                onChange={(event) => onQueryChange(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Escape") {
                    if (hasQuery) onQueryChange("");
                    else event.currentTarget.blur();
                  }
                }}
                placeholder={`Filter ${sheetLabel(activeSheet)}`}
                aria-label={`Filter ${sheetLabel(activeSheet)}`}
                title="Filter rows (Ctrl+F)"
              />
            </label>
            {hasQuery ? (
              <>
                <span className={`metadata-find-count${filteredRowCount ? "" : " empty"}`} aria-live="polite">
                  {filteredRowCount} of {totalRowCount} rows
                </span>
                <button type="button" onClick={() => onQueryChange("")} aria-label="Clear filter" title="Clear filter (Esc)">
                  <X size={15} />
                </button>
              </>
            ) : null}
          </div>
        </div>
      </div>
    </div>
  );
}

function sheetTitle(sheet: SheetKey) {
  const item = sheets.find((candidate) => candidate.key === sheet);
  return item?.label ?? "Metadata";
}

function shortPath(value: string) {
  return value.replace(/\\/g, "/").split("/").filter(Boolean).slice(-2).join("/");
}

function sheetLabel(sheet: SheetKey) {
  if (sheet === "schema_hints") return "schema hints";
  return sheet;
}
