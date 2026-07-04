import "react-datasheet-grid/dist/style.css";

import { DynamicDataSheetGrid, type Column, type DataSheetGridRef } from "react-datasheet-grid";
import { Database } from "lucide-react";
import { useEffect, useLayoutEffect, useMemo, useRef, useState, type MouseEvent as ReactMouseEvent } from "react";
import type { MetadataBackup, MetadataEditorDocument, MetadataEditorIssue, MetadataResponse } from "../../shared/api/types";
import { EmptyState } from "../../shared/components/EmptyState";
import { MetadataIssuesDrawer } from "./MetadataIssuesDrawer";
import { MetadataHistoryDrawer } from "./MetadataHistoryDrawer";
import { MetadataMetrics } from "./MetadataMetrics";
import { MetadataSheetContextMenu } from "./MetadataSheetContextMenu";
import { MetadataSheetToolbar } from "./MetadataSheetToolbar";
import { buildSheetColumns } from "./metadataSheetColumns";
import {
  findMetadataBoundaryCell,
  type MetadataArrowKey
} from "./metadataSheetNavigation";
import {
  cleanRuntimeRows,
  connectionNameOptions,
  createEmptyRow,
  environmentMetadataSourceOptionsForSheet,
  filterMetadataRows,
  formatCellValue,
  insertAt,
  isStudioMetadataSourceField,
  mergeFilteredRows,
  metadataCellMatches,
  moveAt,
  normalizeFieldName,
  parseCellText,
  parseDelimitedRow,
  readClipboard,
  rowFromValues,
  studioRoutingValues,
  writeClipboard
} from "./metadataSheetOperations";
import type { SelectionState, SheetKey, SheetRow } from "./metadataSheetTypes";

interface MetadataExplorerProps {
  metadata: MetadataResponse | null;
  editorDocument: MetadataEditorDocument | null;
  serverDraft: MetadataEditorDocument | null;
  routeSearch?: string;
  loading: boolean;
  busy: boolean;
  onValidate: (document: MetadataEditorDocument) => Promise<MetadataEditorDocument>;
  onSaveDraft: (document: MetadataEditorDocument) => Promise<MetadataEditorDocument>;
  onDiscardDraft: (sourceId: number) => Promise<void>;
  onSave: (document: MetadataEditorDocument) => Promise<MetadataEditorDocument>;
  onListBackups: (sourceId: number) => Promise<MetadataBackup[]>;
  onPreviewBackup: (backupId: number) => Promise<MetadataEditorDocument>;
  onRestoreBackup: (backup: MetadataBackup, document: MetadataEditorDocument) => Promise<MetadataEditorDocument>;
  onDeleteBackup: (backupId: number) => Promise<void>;
  onClearBackups: (sourceId: number) => Promise<void>;
}

const sheetKeys = ["connections", "dataflows", "schema_hints"] satisfies SheetKey[];
const metadataRowHeight = 31;
const metadataHeaderRowHeight = 38;
const metadataScrollbarAllowance = 18;

export function MetadataExplorer({
  metadata,
  editorDocument,
  serverDraft,
  routeSearch,
  loading,
  busy,
  onValidate,
  onSaveDraft,
  onDiscardDraft,
  onSave,
  onListBackups,
  onPreviewBackup,
  onRestoreBackup,
  onDeleteBackup,
  onClearBackups
}: MetadataExplorerProps) {
  const gridRef = useRef<DataSheetGridRef>(null);
  const gridWrapRef = useRef<HTMLDivElement>(null);
  const resizeFrameRef = useRef<number | null>(null);
  const [gridHeight, setGridHeight] = useState(360);
  const [activeSheet, setActiveSheet] = useState<SheetKey>("dataflows");
  const [query, setQuery] = useState("");
  const [issuesOpen, setIssuesOpen] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [mode, setMode] = useState<"view" | "edit">("view");
  const [draft, setDraft] = useState<MetadataEditorDocument | null>(null);
  const [selection, setSelection] = useState<SelectionState>(null);
  const [pendingFocus, setPendingFocus] = useState<{ sheet: SheetKey; row: number; col: number } | null>(null);
  const [columnWidthsBySheet, setColumnWidthsBySheet] = useState<Record<SheetKey, Record<string, number>>>({
    connections: {},
    dataflows: {},
    schema_hints: {}
  });
  const activeColumnWidths = columnWidthsBySheet[activeSheet];
  const activeDocument = draft ?? editorDocument;
  const hasActiveDocument = Boolean(activeDocument);
  const activeStudioRouting = useMemo(() => studioRoutingValues(activeDocument), [activeDocument]);
  const readOnlyDocument = Boolean(activeDocument?.source.read_only);

  useEffect(() => {
    setDraft(serverDraft ?? editorDocument);
    setMode("view");
  }, [editorDocument, serverDraft]);

  useEffect(() => {
    const params = new URLSearchParams(routeSearch ?? "");
    const requestedSheet = params.get("sheet");
    const requestedQuery = params.get("q") ?? "";
    if (requestedSheet && isSheetKey(requestedSheet)) {
      setActiveSheet(requestedSheet);
    }
    setQuery(requestedQuery);
    gridRef.current?.setSelection(null);
    setSelection(null);
  }, [routeSearch]);

  useLayoutEffect(() => {
    const element = gridWrapRef.current;
    if (!element) return;

    const applyHeight = (nextHeight: number) => {
      if (resizeFrameRef.current !== null) {
        window.cancelAnimationFrame(resizeFrameRef.current);
      }
      resizeFrameRef.current = window.requestAnimationFrame(() => {
        resizeFrameRef.current = null;
        setGridHeight((currentHeight) => (Math.abs(currentHeight - nextHeight) > 2 ? nextHeight : currentHeight));
      });
    };
    const measure = () => {
      applyHeight(Math.max(320, Math.floor(element.getBoundingClientRect().height)));
    };

    const resizeObserver = new ResizeObserver(measure);
    resizeObserver.observe(element);
    measure();

    return () => {
      if (resizeFrameRef.current !== null) {
        window.cancelAnimationFrame(resizeFrameRef.current);
      }
      resizeObserver.disconnect();
    };
  }, [hasActiveDocument]);

  const sheet = activeDocument?.sheets[activeSheet] ?? { columns: [], rows: [] };
  const issues = activeDocument?.issues ?? [];
  const activeSheetIssues = useMemo(
    () => issues.filter((issue) => issue.sheet === activeSheet),
    [activeSheet, issues]
  );
  const editable = mode === "edit";
  const connectionOptions = useMemo(() => connectionNameOptions(activeDocument), [activeDocument]);
  const metadataSourceOptions = useMemo(
    () => environmentMetadataSourceOptionsForSheet(activeDocument, activeSheet),
    [activeDocument, activeSheet]
  );
  const rows = useMemo<SheetRow[]>(
    () => filterMetadataRows(activeSheet, sheet.rows, sheet.columns, query),
    [activeSheet, query, sheet.columns, sheet.rows]
  );
  const showAddRowLine = editable && !query.trim();
  const displayedRows = useMemo<SheetRow[]>(
    () => showAddRowLine
      ? [
          ...rows,
          {
            __rowId: `${activeSheet}-add-row`,
            __rowIndex: sheet.rows.length,
            __isAddRow: true
          }
        ]
      : rows,
    [activeSheet, rows, sheet.rows.length, showAddRowLine]
  );
  const needsVerticalScroll =
    metadataHeaderRowHeight + displayedRows.length * metadataRowHeight + metadataScrollbarAllowance > gridHeight;
  const columns = useMemo<Column<SheetRow>[]>(
    () =>
      buildSheetColumns({
        columns: sheet.columns,
        rows: sheet.rows as SheetRow[],
        issues: activeSheetIssues,
        editable,
        connectionOptions,
        metadataSourceOptions,
        columnWidths: activeColumnWidths,
        onColumnWidthChange: resizeColumn
      }),
    [sheet.columns, sheet.rows, activeSheetIssues, editable, connectionOptions, metadataSourceOptions, activeColumnWidths]
  );
  const metadataGutterColumn = useMemo(() => ({
    component: ({ rowData }: { rowData: SheetRow }) => {
      if (!rowData.__isAddRow) return <>{rowData.__rowIndex + 1}</>;
      return (
        <button
          type="button"
          className="metadata-grid-add-row-gutter-button"
          onClick={(event) => {
            event.preventDefault();
            event.stopPropagation();
          }}
          title="Add row below"
          aria-label="Add row below"
        >
          +
        </button>
      );
    }
  }), [sheet.rows.length]);

  useLayoutEffect(() => {
    if (!pendingFocus || pendingFocus.sheet !== activeSheet) return;
    gridRef.current?.setSelection({
      min: { row: pendingFocus.row, col: pendingFocus.col },
      max: { row: pendingFocus.row, col: pendingFocus.col }
    });
    setPendingFocus(null);
  }, [activeSheet, columns, pendingFocus, rows]);

  useEffect(() => {
    const handleBoundaryNavigation = (event: KeyboardEvent) => {
      if (
        !(event.ctrlKey || event.metaKey)
        || event.altKey
        || event.shiftKey
        || !isMetadataArrowKey(event.key)
        || gridWrapRef.current?.querySelector(".dsg-active-cell-focus")
      ) return;

      const activeCell = gridRef.current?.activeCell;
      if (!activeCell || !rows.length || !sheet.columns.length) return;

      const target = findMetadataBoundaryCell(
        rows,
        sheet.columns.map((column) => column.key),
        activeCell,
        event.key
      );
      event.preventDefault();
      event.stopPropagation();
      gridRef.current?.setActiveCell(target);
    };

    document.addEventListener("keydown", handleBoundaryNavigation, true);
    return () => document.removeEventListener("keydown", handleBoundaryNavigation, true);
  }, [rows, sheet.columns]);

  if (!metadata && !activeDocument && !loading) {
    return <EmptyState icon={<Database size={24} />} title="Add metadata source to inspect metadata" />;
  }

  if (!activeDocument) {
    return <EmptyState icon={<Database size={24} />} title={loading ? "Loading metadata" : "No metadata editor document"} />;
  }

  const dirty = Boolean(serverDraft) || (editorDocument ? JSON.stringify(draft?.sheets) !== JSON.stringify(editorDocument.sheets) : false);
  const enabledDataflows = metadata?.dataflows.filter((dataflow) => dataflow.is_active !== false).length ?? countEnabled(activeDocument.sheets.dataflows?.rows ?? []);
  const selectedRowIndex = selection ? rows[selection.min.row]?.__rowIndex ?? null : null;
  const selectedColumnKey = selection?.min.colId && selection.min.colId !== "__gutter" ? selection.min.colId : null;

  function updateActiveSheet(nextSheet: MetadataEditorDocument["sheets"][string]) {
    if (!activeDocument || mode !== "edit") return;
    setDraft({
      ...activeDocument,
      sheets: {
        ...activeDocument.sheets,
        [activeSheet]: nextSheet
      }
    });
  }

  function updateRows(nextRows: SheetRow[]) {
    const nextDataRows = nextRows.filter((row) => !row.__isAddRow);
    updateActiveSheet({
      ...sheet,
      rows: query.trim() ? mergeFilteredRows(sheet.rows, nextDataRows) : cleanRuntimeRows(nextDataRows)
    });
  }

  function resizeColumn(columnKey: string, width: number) {
    if (activeColumnWidths[columnKey] === width) return;
    setColumnWidthsBySheet((current) => ({
      ...current,
      [activeSheet]: {
        ...current[activeSheet],
        [columnKey]: width
      }
    }));
  }

  function changeActiveSheet(nextSheet: SheetKey) {
    if (nextSheet === activeSheet) return;
    gridRef.current?.setSelection(null);
    setSelection(null);
    setActiveSheet(nextSheet);
    setQuery("");
  }

  function changeMode(nextMode: "view" | "edit") {
    if (nextMode === mode) return;
    const currentSelection = gridRef.current?.selection;
    if (currentSelection) gridRef.current?.setSelection(currentSelection);
    setMode(nextMode);
  }

  function changeQuery(nextQuery: string) {
    if (nextQuery === query) return;
    gridRef.current?.setSelection(null);
    setSelection(null);
    setQuery(nextQuery);
  }

  function startColumnResize(event: ReactMouseEvent<HTMLDivElement>) {
    const addRowButton = event.target instanceof HTMLElement ? event.target.closest<HTMLElement>(".metadata-grid-add-row-gutter-button") : null;
    if (addRowButton) {
      event.preventDefault();
      event.stopPropagation();
      addRow(sheet.rows.length - 1);
      return;
    }

    const target = event.target instanceof HTMLElement ? event.target.closest<HTMLElement>(".metadata-column-resizer") : null;
    const columnKey = target?.dataset.columnKey;
    if (!target || !columnKey || event.detail > 1) return;

    event.preventDefault();
    event.stopPropagation();

    const startX = event.clientX;
    const startWidth = Number(target.dataset.columnWidth) || activeColumnWidths[columnKey] || 140;
    const gridWrap = gridWrapRef.current;
    const gridWrapLeft = gridWrap?.getBoundingClientRect().left ?? 0;
    let nextWidth = startWidth;
    gridWrap?.style.setProperty("--metadata-resize-guide-x", `${event.clientX - gridWrapLeft}px`);
    gridWrap?.classList.add("metadata-column-resize-active");

    const handleMouseMove = (moveEvent: MouseEvent) => {
      nextWidth = Math.max(72, Math.round(startWidth + moveEvent.clientX - startX));
      gridWrap?.style.setProperty("--metadata-resize-guide-x", `${moveEvent.clientX - gridWrapLeft}px`);
    };
    const handleMouseUp = () => {
      document.removeEventListener("mousemove", handleMouseMove);
      document.removeEventListener("mouseup", handleMouseUp);
      document.body.classList.remove("metadata-column-resizing");
      gridWrap?.classList.remove("metadata-column-resize-active");
      gridWrap?.style.removeProperty("--metadata-resize-guide-x");
      if (nextWidth !== startWidth) resizeColumn(columnKey, nextWidth);
    };

    document.body.classList.add("metadata-column-resizing");
    document.addEventListener("mousemove", handleMouseMove);
    document.addEventListener("mouseup", handleMouseUp, { once: true });
  }

  async function validateDraft() {
    if (!activeDocument) return;
    setDraft(await onValidate(activeDocument));
  }

  async function saveDraft() {
    if (!activeDocument) return;
    setDraft(await onSaveDraft(activeDocument));
  }

  async function discardDraft() {
    if (!editorDocument) return;
    await onDiscardDraft(editorDocument.source.source_id);
    setDraft(editorDocument);
    setMode("view");
  }

  async function saveChanges() {
    if (!activeDocument) return;
    const confirmed = window.confirm(
      activeDocument.source.scope === "environment"
        ? "Overwrite metadata sources?\n\nRows will be saved back to their metadata_source files. Backups will be created before saving changed files."
        : `Overwrite metadata source?\n\n${activeDocument.source.uri}\n\nA backup will be created before saving.`
    );
    if (!confirmed) return;
    const saved = await onSave(activeDocument);
    setDraft(saved);
    setMode("view");
  }

  async function restoreBackup(backup: MetadataBackup) {
    if (!activeDocument) return;
    const restored = await onRestoreBackup(backup, activeDocument);
    setDraft(restored);
    setMode("view");
  }

  function addRow(afterIndex?: number | null) {
    const insertIndex = typeof afterIndex === "number" ? afterIndex + 1 : sheet.rows.length;
    updateActiveSheet({
      ...sheet,
      rows: insertAt(sheet.rows, insertIndex, createEmptyRow(sheet.columns, studioRoutingForRow(afterIndex ?? null)))
    });
  }

  function duplicateRow(rowIndex = selectedRowIndex) {
    if (rowIndex == null) return;
    const row = sheet.rows[rowIndex];
    if (!row) return;
    updateActiveSheet({
      ...sheet,
      rows: insertAt(sheet.rows, rowIndex + 1, { ...row })
    });
  }

  function deleteRow(rowIndex = selectedRowIndex) {
    if (rowIndex == null) return;
    updateActiveSheet({
      ...sheet,
      rows: sheet.rows.filter((_, index) => index !== rowIndex)
    });
  }

  function moveRow(rowIndex: number | null, offset: -1 | 1) {
    if (rowIndex == null) return;
    const targetIndex = rowIndex + offset;
    if (targetIndex < 0 || targetIndex >= sheet.rows.length) return;
    updateActiveSheet({
      ...sheet,
      rows: moveAt(sheet.rows, rowIndex, targetIndex)
    });
    gridRef.current?.setActiveCell({ row: targetIndex, col: selection?.min.col ?? 0 });
  }

  function addMetadataField() {
    const name = window.prompt("Metadata field name");
    const key = normalizeFieldName(name);
    if (!key || sheet.columns.some((column) => column.key === key)) return;
    updateActiveSheet({
      columns: [...sheet.columns, { key, name: key }],
      rows: sheet.rows.map((row) => ({ ...row, [key]: null }))
    });
  }

  async function copySelectedRow(rowIndex = selectedRowIndex) {
    if (rowIndex == null) return;
    await writeClipboard(sheet.columns.map((column) => formatCellValue(sheet.rows[rowIndex]?.[column.key])).join("\t"));
  }

  async function pasteRowBelow(rowIndex = selectedRowIndex) {
    if (rowIndex == null) return;
    const text = await readClipboard();
    if (!text) return;
    const values = parseDelimitedRow(text);
    updateActiveSheet({
      ...sheet,
      rows: insertAt(sheet.rows, rowIndex + 1, rowFromValues(sheet.columns, values, studioRoutingForRow(rowIndex)))
    });
  }

  async function copySelectedColumn(columnKey = selectedColumnKey) {
    if (!columnKey) return;
    await writeClipboard(sheet.rows.map((row) => formatCellValue(row[columnKey])).join("\n"));
  }

  async function pasteSelectedColumn(columnKey = selectedColumnKey, rowIndex = selectedRowIndex) {
    if (!columnKey || rowIndex == null || isStudioMetadataSourceField(columnKey)) return;
    const text = await readClipboard();
    if (!text) return;
    const values = text.split(/\r?\n/);
    updateActiveSheet({
      ...sheet,
      rows: sheet.rows.map((row, index) => {
        const valueIndex = index - rowIndex;
        return valueIndex >= 0 && valueIndex < values.length ? { ...row, [columnKey]: parseCellText(values[valueIndex]) } : row;
      })
    });
  }

  function studioRoutingForRow(rowIndex: number | null) {
    const row = rowIndex == null ? null : sheet.rows[rowIndex];
    if (!row) return activeStudioRouting;
    const sourceId = typeof row.__metadata_source_id === "number"
      ? row.__metadata_source_id
      : Number(row.__metadata_source_id);
    return {
      __metadata_source_id: Number.isFinite(sourceId) ? sourceId : activeStudioRouting.__metadata_source_id,
      __metadata_source_name: formatCellValue(row.__metadata_source_name) || activeStudioRouting.__metadata_source_name,
      __metadata_source_uri: formatCellValue(row.__metadata_source_uri) || activeStudioRouting.__metadata_source_uri,
      __metadata_source_kind: formatCellValue(row.__metadata_source_kind) || activeStudioRouting.__metadata_source_kind
    };
  }

  function focusIssue(issue: MetadataEditorIssue) {
    if (!activeDocument || !isSheetKey(issue.sheet)) return;
    setIssuesOpen(false);
    setQuery("");
    const columnIndex = activeDocument.sheets[issue.sheet]?.columns.findIndex((column) => column.key === issue.column) ?? -1;
    setPendingFocus({
      sheet: issue.sheet,
      row: issue.row_index,
      col: Math.max(columnIndex, 0)
    });
    setActiveSheet(issue.sheet);
  }

  return (
    <div className="view-stack metadata-sheet-page">
      <MetadataMetrics
        connections={activeDocument.sheets.connections?.rows.length ?? 0}
        dataflows={activeDocument.sheets.dataflows?.rows.length ?? 0}
        enabledDataflows={enabledDataflows}
        schemaHints={activeDocument.sheets.schema_hints?.rows.length ?? 0}
        issues={issues}
        onIssuesClick={() => setIssuesOpen(true)}
      />

      <section className="table-panel metadata-sheet-panel">
        <MetadataSheetToolbar
          activeSheet={activeSheet}
          busy={busy}
          dirty={dirty}
          filteredRowCount={rows.length}
          totalRowCount={sheet.rows.length}
          mode={mode}
          query={query}
          readOnly={readOnlyDocument}
          sourceFormat={activeDocument.source.format}
          sourceUri={activeDocument.source.uri}
          onActiveSheetChange={changeActiveSheet}
          onDiscard={() => setDraft(serverDraft ?? editorDocument)}
          onDiscardDraft={discardDraft}
          onHistoryOpen={() => setHistoryOpen(true)}
          onModeChange={changeMode}
          onQueryChange={changeQuery}
          onSave={saveChanges}
          onSaveDraft={saveDraft}
          onValidate={validateDraft}
        />

        <div ref={gridWrapRef} className="metadata-grid-wrap metadata-datasheet-wrap" onMouseDownCapture={startColumnResize}>
          <div className="metadata-column-resize-guide" aria-hidden="true" />
          <DynamicDataSheetGrid
            ref={gridRef}
            className={`metadata-datasheet metadata-datasheet-${mode}${needsVerticalScroll ? "" : " metadata-datasheet-no-y-scroll"}`}
            value={displayedRows}
            columns={columns}
            cellClassName={({ rowData, columnId }) => {
              if ((rowData as SheetRow).__isAddRow && columnId !== "__gutter") return "metadata-add-row-cell";
              if (!columnId || !metadataCellMatches((rowData as SheetRow)[columnId], query)) return undefined;
              return "metadata-filter-match";
            }}
            rowClassName={({ rowData }) => (rowData as SheetRow).__isAddRow ? "metadata-add-row-line" : undefined}
            onChange={editable ? updateRows : undefined}
            gutterColumn={metadataGutterColumn}
            rowKey={({ rowData }) => rowData.__rowId}
            rowHeight={metadataRowHeight}
            headerRowHeight={metadataHeaderRowHeight}
            height={gridHeight}
            lockRows={!editable || Boolean(query.trim())}
            disableContextMenu={!editable}
            addRowsComponent={false}
            contextMenuComponent={
              editable
                ? (props) => {
                    const rowIndex = rows[props.cursorIndex.row]?.__rowIndex ?? null;
                    const columnKey = sheet.columns[props.cursorIndex.col]?.key ?? null;
                    return (
                      <MetadataSheetContextMenu
                        {...props}
                        rowIndex={rowIndex}
                        columnKey={columnKey}
                        onAddField={addMetadataField}
                        onAddRowBelow={addRow}
                        onCopyColumn={copySelectedColumn}
                        onCopyRow={copySelectedRow}
                        onDeleteRow={deleteRow}
                        onDuplicateRow={duplicateRow}
                        onMoveRow={moveRow}
                        onPasteColumn={pasteSelectedColumn}
                        onPasteRow={pasteRowBelow}
                      />
                    );
                  }
                : undefined
            }
            onActiveCellChange={({ cell }) => {
              setSelection(cell ? { min: cell, max: cell } : null);
            }}
            onSelectionChange={({ selection }) => {
              setSelection(selection);
            }}
          />
          {!rows.length ? (
            <div className="metadata-grid-empty">
              {query.trim() ? `No rows match "${query.trim()}"` : "No rows"}
            </div>
          ) : null}
        </div>
      </section>

      {issuesOpen ? <MetadataIssuesDrawer issues={issues} onClose={() => setIssuesOpen(false)} onIssueClick={focusIssue} /> : null}
      {historyOpen ? (
        <MetadataHistoryDrawer
          currentDocument={activeDocument}
          dirty={dirty}
          onClear={onClearBackups}
          onClose={() => setHistoryOpen(false)}
          onDelete={onDeleteBackup}
          onList={onListBackups}
          onPreview={onPreviewBackup}
          onRestore={restoreBackup}
        />
      ) : null}
    </div>
  );
}

function countEnabled(rows: Array<Record<string, unknown>>) {
  return rows.filter((row) => row.is_active !== false).length;
}

function isSheetKey(value: string): value is SheetKey {
  return sheetKeys.some((sheet) => sheet === value);
}

function isMetadataArrowKey(key: string): key is MetadataArrowKey {
  return key === "ArrowDown"
    || key === "ArrowLeft"
    || key === "ArrowRight"
    || key === "ArrowUp";
}
