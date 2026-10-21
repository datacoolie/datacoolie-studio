import "react-datasheet-grid/dist/style.css";
import "./metadata-explorer.css";

import { DynamicDataSheetGrid, type Column, type DataSheetGridRef } from "react-datasheet-grid";
import { Database } from "lucide-react";
import { useEffect, useLayoutEffect, useMemo, useRef, useState, type MouseEvent as ReactMouseEvent } from "react";
import type {
  MetadataBackup,
  MetadataEditorDocument,
  MetadataEditorIssue,
  MetadataWriteOperation,
} from "../../shared/api/domainTypes";
import { EmptyState } from "../../shared/components/EmptyState";
import { formatCellValue, parseCellText } from "../../shared/metadata/metadataCellValues";
import {
  buildMetadataDataflowRecords,
  findMetadataDataflowRecord,
  isEditableMetadataDataflowRecord,
  type MetadataDataflowSelection,
  updateMetadataDataflowDocument,
} from "../../shared/metadata/metadataDataflowModel";
import type { MetadataNavigationTarget } from "../../shared/metadataNavigation";
import type { LineageDataflowFocusTarget } from "../../shared/lineageNavigation";
import { createStageToneMap, stageToneClass } from "../../shared/stagePresentation";
import { MetadataIssuesDrawer } from "./MetadataIssuesDrawer";
import { MetadataHistoryDrawer } from "./MetadataHistoryDrawer";
import { MetadataMetrics } from "./MetadataMetrics";
import { MetadataSheetContextMenu } from "./MetadataSheetContextMenu";
import { MetadataSheetToolbar } from "./MetadataSheetToolbar";
import { MetadataDataflowDrawer } from "../dataflows/MetadataDataflowDrawer";
import { buildSheetColumns } from "./metadataSheetColumns";
import {
  METADATA_COLUMN_HEADER_LAYOUT,
  metadataHeaderLayoutStyle,
  normalizeMetadataColumnWidth
} from "./metadataColumnHeaderLayout";
import {
  createEmptyMetadataColumnFilters,
  metadataColumnFilterCount,
  metadataColumnFilterMapWithoutKey,
  metadataColumnFilterOptionKeys,
  metadataColumnFilterOptions,
  metadataColumnFilterSelectionIsComplete,
  metadataColumnFilterStateForSheet,
  metadataColumnFilterValueKey,
  type MetadataColumnFilterMap,
  type MetadataColumnFiltersBySheet,
  type MetadataFilterOption
} from "./metadataColumnFilters";
import {
  findMetadataBoundaryCell,
  type MetadataArrowKey
} from "./metadataSheetNavigation";
import {
  canMoveMetadataRow,
  cleanRuntimeRows,
  connectionNameOptions,
  createEmptyRow,
  environmentMetadataSourceOptionsForSheet,
  filterMetadataRows,
  insertAt,
  isStudioMetadataSourceField,
  mergeFilteredRows,
  metadataCellMatches,
  metadataSourceGroupStartIds,
  moveAt,
  normalizeFieldName,
  parseDelimitedRow,
  readClipboard,
  rowFromValues,
  studioRoutingValues,
  synchronizeConnectionNameReferences,
  writeClipboard
} from "./metadataSheetOperations";
import type { SelectionState, SheetKey, SheetRow } from "./metadataSheetTypes";
import { metadataDraftState, metadataServerUpdateConflicts } from "./metadataDraftState";
import { metadataWriteConfirmation } from "../../shared/metadata/metadataSaveConfirmation";
import { MetadataSourceSaveConfirmationDialog } from "../../shared/metadata/MetadataSourceSaveConfirmationDialog";

interface MetadataExplorerProps {
  editorDocument: MetadataEditorDocument | null;
  serverDraft: MetadataEditorDocument | null;
  routeSearch?: string;
  onRouteSearchChange?: (search?: string) => void;
  metadataNavigation?: MetadataNavigationTarget | null;
  onMetadataNavigationConsumed?: () => void;
  onFocusInLineage: (target: LineageDataflowFocusTarget) => void;
  loading: boolean;
  busy: boolean;
  savingDraft: boolean;
  onSaveDraft: (document: MetadataEditorDocument) => Promise<MetadataEditorDocument>;
  onDiscardDraft: () => Promise<void>;
  onPrepareSave: (document: MetadataEditorDocument) => Promise<MetadataWriteOperation>;
  onApplySave: (operation: MetadataWriteOperation) => Promise<MetadataEditorDocument>;
  onAbandonSave: (operation: MetadataWriteOperation) => Promise<MetadataWriteOperation>;
  onListBackups: () => Promise<MetadataBackup[]>;
  onPreviewBackup: (backupId: number) => Promise<MetadataEditorDocument>;
  onRestoreBackup: (backup: MetadataBackup, document: MetadataEditorDocument) => Promise<MetadataEditorDocument>;
  onDeleteBackup: (backupId: number) => Promise<void>;
  onClearBackups: () => Promise<void>;
}

const sheetKeys = ["connections", "dataflows", "schema_hints"] satisfies SheetKey[];
const metadataRowHeight = 31;
const metadataHeaderRowHeight = 38;
const metadataScrollbarAllowance = 18;

export function MetadataExplorer({
  editorDocument,
  serverDraft,
  routeSearch,
  onRouteSearchChange,
  metadataNavigation,
  onMetadataNavigationConsumed,
  onFocusInLineage,
  loading,
  busy,
  savingDraft,
  onSaveDraft,
  onDiscardDraft,
  onPrepareSave,
  onApplySave,
  onAbandonSave,
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
  const [stageFilter, setStageFilter] = useState<string | null>(null);
  const [targetDataflowIds, setTargetDataflowIds] = useState<string[]>([]);
  const [issuesOpen, setIssuesOpen] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [mode, setMode] = useState<"view" | "edit">("view");
  const [draft, setDraft] = useState<MetadataEditorDocument | null>(null);
  const [sourceBaseline, setSourceBaseline] = useState<MetadataEditorDocument | null>(editorDocument);
  const [serverConflict, setServerConflict] = useState(false);
  const [pendingSourceSave, setPendingSourceSave] = useState<{
    document: MetadataEditorDocument;
    operation: MetadataWriteOperation;
  } | null>(null);
  const [selection, setSelection] = useState<SelectionState>(null);
  const [selectedDataflow, setSelectedDataflow] = useState<MetadataDataflowSelection | null>(null);
  const [dataflowDrawerStack, setDataflowDrawerStack] = useState<MetadataDataflowSelection[]>([]);
  const selectedDataflowRef = useRef(selectedDataflow);
  const dataflowDrawerStackRef = useRef(dataflowDrawerStack);
  const dataflowDrawerHistoryDepthRef = useRef(0);
  const suppressNextDataflowDrawerPopRef = useRef(false);
  const preserveColumnFiltersForNextRouteRef = useRef(false);
  const environmentIdentityRef = useRef<string | null>(null);
  const serverBaseRef = useRef({ document: editorDocument, draft: serverDraft });
  const workingDocumentRef = useRef<MetadataEditorDocument | null>(draft);
  const acceptingServerUpdateRef = useRef(false);
  const [pendingFocus, setPendingFocus] = useState<{ sheet: SheetKey; row: number; col: number } | null>(null);
  const [columnWidthsBySheet, setColumnWidthsBySheet] = useState<Record<SheetKey, Record<string, number>>>({
    connections: {},
    dataflows: {},
    schema_hints: {}
  });
  const [columnFiltersBySheet, setColumnFiltersBySheet] = useState<MetadataColumnFiltersBySheet>(createEmptyMetadataColumnFilters);
  const [activeFilterColumnKey, setActiveFilterColumnKey] = useState<string | null>(null);
  const activeColumnWidths = columnWidthsBySheet[activeSheet];
  const activeDocument = draft ?? editorDocument;
  workingDocumentRef.current = draft;
  const hasActiveDocument = Boolean(activeDocument);
  const stageToneMap = useMemo(
    () => createStageToneMap(activeDocument?.sheets.dataflows?.rows.map((row) => row.stage) ?? []),
    [activeDocument]
  );
  const activeStudioRouting = useMemo(() => studioRoutingValues(activeDocument), [activeDocument]);
  const readOnlyDocument = Boolean(activeDocument?.source.read_only);
  const draftState = useMemo(
    () => metadataDraftState(sourceBaseline, serverDraft, activeDocument),
    [activeDocument, serverDraft, sourceBaseline],
  );
  const environmentIdentity = editorDocument
    ? `${editorDocument.source.environment_id}:${editorDocument.source.source_id}`
    : null;

  useEffect(() => {
    const previousEnvironmentIdentity = environmentIdentityRef.current;
    const environmentChanged = previousEnvironmentIdentity !== null && previousEnvironmentIdentity !== environmentIdentity;
    environmentIdentityRef.current = environmentIdentity;

    if (activeFilterColumnKey) {
      const availableColumnKeys = new Set(activeDocument?.sheets[activeSheet]?.columns.map((column) => column.key) ?? []);
      if (!availableColumnKeys.has(activeFilterColumnKey)) setActiveFilterColumnKey(null);
    }
    setColumnFiltersBySheet((current) => {
      if (environmentChanged) return createEmptyMetadataColumnFilters();
      const nextFiltersBySheet = createEmptyMetadataColumnFilters();
      let changed = false;
      for (const sheetKey of sheetKeys) {
        const availableColumnKeys = new Set(activeDocument?.sheets[sheetKey]?.columns.map((column) => column.key) ?? []);
        const currentFilters = current[sheetKey];
        const nextFilters = Object.fromEntries(
          Object.entries(currentFilters).filter(([columnKey]) => availableColumnKeys.has(columnKey))
        );
        nextFiltersBySheet[sheetKey] = nextFilters;
        if (Object.keys(nextFilters).length !== Object.keys(currentFilters).length) changed = true;
      }
      return changed ? nextFiltersBySheet : current;
    });
  }, [activeDocument, activeFilterColumnKey, activeSheet, environmentIdentity]);

  useEffect(() => {
    const previous = serverBaseRef.current;
    const serverUpdateConflicts = metadataServerUpdateConflicts(
      previous.document,
      previous.draft,
      workingDocumentRef.current,
      editorDocument,
      serverDraft
    );
    const documentChanged = previous.document !== editorDocument;
    serverBaseRef.current = { document: editorDocument, draft: serverDraft };
    setSourceBaseline(editorDocument);
    if (serverUpdateConflicts && !acceptingServerUpdateRef.current) {
      setServerConflict(true);
      return;
    }
    setDraft(serverDraft ?? editorDocument);
    setServerConflict(false);
    if (documentChanged) setMode("view");
  }, [editorDocument, serverDraft]);

  useEffect(() => {
    const params = new URLSearchParams(routeSearch ?? "");
    const requestedSheet = params.get("sheet");
    const requestedQuery = params.get("q") ?? "";
    const requestedStage = params.get("stage")?.trim() ?? "";
    const preserveColumnFilters = preserveColumnFiltersForNextRouteRef.current;
    preserveColumnFiltersForNextRouteRef.current = false;
    if (requestedSheet && isSheetKey(requestedSheet)) {
      setActiveSheet(requestedSheet);
    }
    if (requestedStage) setActiveSheet("dataflows");
    if (!preserveColumnFilters && (requestedQuery || requestedStage)) {
      const filterSheet = requestedStage ? "dataflows" : requestedSheet && isSheetKey(requestedSheet) ? requestedSheet : "dataflows";
      setColumnFiltersBySheet((current) => ({ ...current, [filterSheet]: {} }));
    }
    setStageFilter(requestedStage || null);
    setQuery(requestedQuery);
    setTargetDataflowIds([]);
    setActiveFilterColumnKey(null);
    gridRef.current?.setSelection(null);
    setSelection(null);
  }, [routeSearch]);

  useEffect(() => {
    if (!metadataNavigation) return;
    setActiveSheet("dataflows");
    setTargetDataflowIds(metadataNavigation.dataflowIds);
    setStageFilter(null);
    setQuery(metadataNavigation.dataflowIds.length ? "" : metadataNavigation.fallbackQuery || "");
    setColumnFiltersBySheet((current) => ({ ...current, dataflows: {} }));
    setActiveFilterColumnKey(null);
    gridRef.current?.setSelection(null);
    setSelection(null);
    onMetadataNavigationConsumed?.();
  }, [metadataNavigation, onMetadataNavigationConsumed]);

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
  const editable = mode === "edit" && !savingDraft;
  const connectionOptions = useMemo(() => connectionNameOptions(activeDocument), [activeDocument]);
  const metadataSourceOptions = useMemo(
    () => environmentMetadataSourceOptionsForSheet(activeDocument, activeSheet),
    [activeDocument, activeSheet]
  );
  const activeColumnFilters = columnFiltersBySheet[activeSheet];
  const manualStageFilter = activeColumnFilters.stage;
  const effectiveStageFilter = manualStageFilter ? null : stageFilter;
  const rows = useMemo<SheetRow[]>(
    () => filterMetadataRows(
      activeSheet,
      sheet.rows,
      sheet.columns,
      query,
      effectiveStageFilter,
      activeColumnFilters,
      undefined,
      targetDataflowIds
    ),
    [activeColumnFilters, activeSheet, effectiveStageFilter, query, sheet.columns, sheet.rows, targetDataflowIds]
  );
  const activeColumnFilterCount = metadataColumnFilterCount(activeColumnFilters);
  const hasActiveRowSubsetFilter = Boolean(query.trim() || stageFilter || targetDataflowIds.length || activeColumnFilterCount);
  const activeFilterRows = useMemo<SheetRow[]>(
    () => activeFilterColumnKey
      ? filterMetadataRows(
          activeSheet,
          sheet.rows,
          sheet.columns,
          query,
          activeFilterColumnKey === "stage" ? null : effectiveStageFilter,
          activeColumnFilters,
          activeFilterColumnKey,
          targetDataflowIds
        )
      : [],
    [activeColumnFilters, activeFilterColumnKey, activeSheet, effectiveStageFilter, query, sheet.columns, sheet.rows, targetDataflowIds]
  );
  const activeFilterOptions = useMemo(
    () => activeFilterColumnKey ? metadataColumnFilterOptions(activeFilterRows, activeFilterColumnKey) : { options: [] as MetadataFilterOption[], totalCount: 0 },
    [activeFilterColumnKey, activeFilterRows]
  );
  const activeFilterSelectedKeys = useMemo(() => {
    if (!activeFilterColumnKey) return [];
    const selectedKeys = metadataColumnFilterStateForSheet(columnFiltersBySheet, activeSheet, activeFilterColumnKey);
    if (selectedKeys) return [...selectedKeys];
    if (activeFilterColumnKey === "stage" && stageFilter) return [metadataColumnFilterValueKey(stageFilter, activeFilterColumnKey)];
    return metadataColumnFilterOptionKeys(activeFilterOptions.options);
  }, [activeFilterColumnKey, activeFilterOptions.options, activeSheet, columnFiltersBySheet, stageFilter]);
  const sourceGroupStartIds = useMemo(() => metadataSourceGroupStartIds(rows), [rows]);
  const showAddRowLine = editable && !hasActiveRowSubsetFilter;
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
  const dataflowRecords = useMemo(
    () => selectedDataflow ? buildMetadataDataflowRecords(activeDocument) : [],
    [activeDocument, selectedDataflow]
  );
  const selectedDataflowRecord = useMemo(
    () => findMetadataDataflowRecord(dataflowRecords, selectedDataflow),
    [dataflowRecords, selectedDataflow]
  );

  function popDataflowDrawerState() {
    const stack = dataflowDrawerStackRef.current;
    const previous = stack[stack.length - 1];
    const nextStack = stack.slice(0, -1);
    setSelectedDataflow(previous ?? null);
    setDataflowDrawerStack(nextStack);
    selectedDataflowRef.current = previous ?? null;
    dataflowDrawerStackRef.current = nextStack;
    dataflowDrawerHistoryDepthRef.current = Math.max(0, dataflowDrawerHistoryDepthRef.current - 1);
  }

  function pushDataflowDrawerHistory() {
    window.history.pushState({ datacoolieMetadataDataflowDrawer: true }, "", window.location.href);
    dataflowDrawerHistoryDepthRef.current += 1;
  }

  function openDataflowDrawer(next: MetadataDataflowSelection) {
    pushDataflowDrawerHistory();
    setSelectedDataflow(next);
    setDataflowDrawerStack([]);
    selectedDataflowRef.current = next;
    dataflowDrawerStackRef.current = [];
  }

  function pushDataflowDrawer(next: MetadataDataflowSelection) {
    pushDataflowDrawerHistory();
    const current = selectedDataflowRef.current;
    if (current) {
      const nextStack = [...dataflowDrawerStackRef.current, current];
      setDataflowDrawerStack(nextStack);
      dataflowDrawerStackRef.current = nextStack;
    }
    setSelectedDataflow(next);
    selectedDataflowRef.current = next;
  }

  function popDataflowDrawer() {
    if (dataflowDrawerHistoryDepthRef.current > 0) {
      window.history.back();
      return;
    }
    popDataflowDrawerState();
  }

  function closeDataflowDrawer() {
    setSelectedDataflow(null);
    setDataflowDrawerStack([]);
    selectedDataflowRef.current = null;
    dataflowDrawerStackRef.current = [];
    if (dataflowDrawerHistoryDepthRef.current > 0) {
      suppressNextDataflowDrawerPopRef.current = true;
      window.history.go(-dataflowDrawerHistoryDepthRef.current);
      dataflowDrawerHistoryDepthRef.current = 0;
    }
  }

  function clearEditorSelectionAfterDocumentChange() {
    gridRef.current?.setSelection(null);
    setSelection(null);
    if (selectedDataflowRef.current || dataflowDrawerHistoryDepthRef.current > 0) closeDataflowDrawer();
  }

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
        onColumnWidthChange: resizeColumn,
        activeFilterColumnKey,
        activeFilterOptions: activeFilterOptions.options,
        activeFilterSelectedKeys,
        isColumnFilterActive: (columnKey) => columnKey in activeColumnFilters
          || (columnKey === "stage" && Boolean(stageFilter) && !("stage" in activeColumnFilters)),
        onColumnFilterOpen: openColumnFilter,
        onColumnFilterApply: applyColumnFilter,
        onColumnFilterClear: clearColumnFilter,
        onColumnFilterCancel: closeColumnFilter
      }),
    [
      sheet.columns,
      sheet.rows,
      activeSheetIssues,
      editable,
      connectionOptions,
      metadataSourceOptions,
      activeColumnWidths,
      activeColumnFilters,
      activeFilterColumnKey,
      activeFilterOptions,
      activeFilterSelectedKeys,
      stageFilter
    ]
  );
  const metadataGutterColumn = useMemo(() => ({
    component: ({ rowData }: { rowData: SheetRow }) => {
      if (!rowData.__isAddRow) {
        if (activeSheet === "dataflows") {
          return (
            <button
              type="button"
              className="metadata-grid-row-number-button"
              title="Double-click to open dataflow details"
              onDoubleClick={(event) => {
                event.preventDefault();
                event.stopPropagation();
                openDataflowDrawer({
                  metadataSourceId: Number(rowData.__metadata_source_id),
                  rowIndex: rowData.__rowIndex,
                  dataflowId: formatCellValue(rowData.dataflow_id),
                  name: formatCellValue(rowData.name),
                });
              }}
            >
              {rowData.__rowIndex + 1}
            </button>
          );
        }
        return <>{rowData.__rowIndex + 1}</>;
      }
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
  }), [activeSheet, sheet.rows.length]);

  useEffect(() => {
    if (activeSheet !== "dataflows") closeDataflowDrawer();
  }, [activeSheet]);

  useEffect(() => {
    if (selectedDataflow && !selectedDataflowRecord) closeDataflowDrawer();
  }, [selectedDataflow, selectedDataflowRecord]);

  useEffect(() => {
    selectedDataflowRef.current = selectedDataflow;
    dataflowDrawerStackRef.current = dataflowDrawerStack;
  }, [dataflowDrawerStack, selectedDataflow]);

  useEffect(() => {
    function handlePopState() {
      if (suppressNextDataflowDrawerPopRef.current) {
        suppressNextDataflowDrawerPopRef.current = false;
        dataflowDrawerHistoryDepthRef.current = 0;
        return;
      }
      if (!selectedDataflowRef.current || dataflowDrawerHistoryDepthRef.current <= 0) return;
      popDataflowDrawerState();
    }

    window.addEventListener("popstate", handlePopState);
    return () => window.removeEventListener("popstate", handlePopState);
  }, []);

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

  if (!activeDocument && !loading) {
    return <EmptyState icon={<Database size={24} />} title="Add metadata source to inspect metadata" />;
  }

  if (!activeDocument) {
    return <EmptyState icon={<Database size={24} />} title={loading ? "Loading metadata" : "No metadata editor document"} />;
  }

  const dirty = draftState.hasLocalChanges;
  const sourceSaveConfirmation = pendingSourceSave
    ? metadataWriteConfirmation(pendingSourceSave.operation)
    : null;
  const enabledDataflows = countEnabled(activeDocument.sheets.dataflows?.rows ?? []);
  const selectedRowIndex = selection ? rows[selection.min.row]?.__rowIndex ?? null : null;
  const selectedColumnKey = selection?.min.colId && selection.min.colId !== "__gutter" ? selection.min.colId : null;

  function updateActiveSheet(nextSheet: MetadataEditorDocument["sheets"][string]) {
    if (!activeDocument || mode !== "edit") return;
    if (activeSheet === "connections") {
      setDraft(synchronizeConnectionNameReferences(activeDocument, nextSheet));
      return;
    }
    setDraft({
      ...activeDocument,
      sheets: { ...activeDocument.sheets, [activeSheet]: nextSheet }
    });
  }

  function updateRows(nextRows: SheetRow[]) {
    const nextDataRows = nextRows.filter((row) => !row.__isAddRow);
    updateActiveSheet({
      ...sheet,
      rows: hasActiveRowSubsetFilter ? mergeFilteredRows(sheet.rows, nextDataRows) : cleanRuntimeRows(nextDataRows)
    });
  }

  function openColumnFilter(columnKey: string) {
    setActiveFilterColumnKey((current) => current === columnKey ? null : columnKey);
  }

  function closeColumnFilter() {
    setActiveFilterColumnKey(null);
  }

  function clearColumnFilter(columnKey: string) {
    setColumnFiltersBySheet((current) => ({
      ...current,
      [activeSheet]: metadataColumnFilterMapWithoutKey(current[activeSheet], columnKey)
    }));
    setActiveFilterColumnKey(null);
    if (columnKey === "stage" && stageFilter) {
      preserveColumnFiltersForNextRouteRef.current = true;
      clearStageFilter();
    }
  }

  function applyColumnFilter(columnKey: string, selectedKeys: string[]) {
    const normalizedKeys = [...new Set(selectedKeys)];
    const isComplete = metadataColumnFilterSelectionIsComplete(normalizedKeys, activeFilterOptions.options);
    setColumnFiltersBySheet((current) => {
      const currentFilters = current[activeSheet];
      const nextFilters: MetadataColumnFilterMap = isComplete
        ? metadataColumnFilterMapWithoutKey(currentFilters, columnKey)
        : { ...currentFilters, [columnKey]: normalizedKeys };
      return { ...current, [activeSheet]: nextFilters };
    });
    setActiveFilterColumnKey(null);
    if (columnKey === "stage" && stageFilter) {
      preserveColumnFiltersForNextRouteRef.current = true;
      clearStageFilter();
    }
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
    setTargetDataflowIds([]);
    setActiveFilterColumnKey(null);
    clearStageFilter(nextSheet);
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
    setTargetDataflowIds([]);
    setActiveFilterColumnKey(null);
    setQuery(nextQuery);
  }

  function clearStageFilter(nextSheet?: SheetKey) {
    if (onRouteSearchChange) preserveColumnFiltersForNextRouteRef.current = true;
    setStageFilter(null);
    const params = new URLSearchParams(routeSearch ?? "");
    params.delete("stage");
    if (nextSheet) {
      params.set("sheet", nextSheet);
      params.delete("q");
    }
    const nextSearch = params.toString();
    onRouteSearchChange?.(nextSearch ? `?${nextSearch}` : undefined);
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
    const startRenderedWidth = Number(target.dataset.columnWidth)
      || normalizeMetadataColumnWidth(activeColumnWidths[columnKey] ?? 140);
    const gridWrap = gridWrapRef.current;
    const gridWrapLeft = gridWrap?.getBoundingClientRect().left ?? 0;
    let nextRenderedWidth = startRenderedWidth;
    gridWrap?.style.setProperty("--metadata-resize-guide-x", `${event.clientX - gridWrapLeft}px`);
    gridWrap?.classList.add("metadata-column-resize-active");

    const handleMouseMove = (moveEvent: MouseEvent) => {
      nextRenderedWidth = Math.max(
        METADATA_COLUMN_HEADER_LAYOUT.minimumColumnWidth,
        Math.round(startRenderedWidth + moveEvent.clientX - startX)
      );
      gridWrap?.style.setProperty("--metadata-resize-guide-x", `${moveEvent.clientX - gridWrapLeft}px`);
    };
    const handleMouseUp = () => {
      document.removeEventListener("mousemove", handleMouseMove);
      document.removeEventListener("mouseup", handleMouseUp);
      document.body.classList.remove("metadata-column-resizing");
      gridWrap?.classList.remove("metadata-column-resize-active");
      gridWrap?.style.removeProperty("--metadata-resize-guide-x");
      if (nextRenderedWidth !== startRenderedWidth) resizeColumn(columnKey, nextRenderedWidth);
    };

    document.body.classList.add("metadata-column-resizing");
    document.addEventListener("mousemove", handleMouseMove);
    document.addEventListener("mouseup", handleMouseUp, { once: true });
  }

  async function saveDraft(document = activeDocument) {
    if (!document) return;
    acceptingServerUpdateRef.current = true;
    try {
      const saved = await onSaveDraft(document);
      setDraft(saved);
      setServerConflict(false);
      clearEditorSelectionAfterDocumentChange();
      return saved;
    } catch {
      return undefined;
    } finally {
      acceptingServerUpdateRef.current = false;
    }
  }

  async function discardDraft() {
    if (!editorDocument) return;
    acceptingServerUpdateRef.current = true;
    try {
      await onDiscardDraft();
      setDraft(editorDocument);
      setServerConflict(false);
      clearEditorSelectionAfterDocumentChange();
    } catch {
      return;
    } finally {
      acceptingServerUpdateRef.current = false;
    }
  }

  async function requestSourceSave(document = activeDocument) {
    if (!document) return;
    try {
      const operation = await onPrepareSave(document);
      setPendingSourceSave({ document, operation });
    } catch {
      setServerConflict(true);
    }
  }

  async function confirmSourceSave() {
    if (!pendingSourceSave) return;
    acceptingServerUpdateRef.current = true;
    try {
      const saved = await onApplySave(pendingSourceSave.operation);
      setSourceBaseline(saved);
      setDraft(saved);
      setMode("view");
      setServerConflict(false);
      setPendingSourceSave(null);
      closeDataflowDrawer();
    } catch (reason) {
      const operation = (reason as { operation?: MetadataWriteOperation }).operation;
      if (operation) {
        setPendingSourceSave((current) => current ? { ...current, operation } : current);
      }
    } finally {
      acceptingServerUpdateRef.current = false;
    }
  }

  async function cancelSourceSave() {
    if (!pendingSourceSave) return;
    try {
      await onAbandonSave(pendingSourceSave.operation);
      setPendingSourceSave(null);
    } catch {
      return;
    }
  }

  async function restoreBackup(backup: MetadataBackup) {
    if (!activeDocument) return;
    acceptingServerUpdateRef.current = true;
    try {
      const restored = await onRestoreBackup(backup, activeDocument);
      setSourceBaseline(restored);
      setDraft(restored);
      setMode("view");
      setServerConflict(false);
      clearEditorSelectionAfterDocumentChange();
    } finally {
      acceptingServerUpdateRef.current = false;
    }
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
    if (hasActiveRowSubsetFilter || !canMoveMetadataRow(activeSheet, sheet.rows, rowIndex, offset)) return;
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
      __metadata_source_kind: formatCellValue(row.__metadata_source_kind) || activeStudioRouting.__metadata_source_kind,
      __metadata_source_read_only: row.__metadata_source_read_only === true
        ? true
        : activeStudioRouting.__metadata_source_read_only
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
      {serverConflict ? (
        <div className="metadata-editor-conflict" role="alert">
          Metadata changed on the server while this editor has unsaved changes. Your local edits are preserved; discard them or reload before saving.
        </div>
      ) : null}
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
          savingDraft={savingDraft}
          hasLocalChanges={draftState.hasLocalChanges}
          hasSourceChanges={draftState.hasSourceChanges}
          hasStoredDraft={draftState.hasStoredDraft}
          filteredRowCount={rows.length}
          totalRowCount={sheet.rows.length}
          activeColumnFilterCount={activeColumnFilterCount}
          mode={mode}
          query={query}
          readOnly={readOnlyDocument}
          sourceFormat={activeDocument.source.format}
          sourceUri={activeDocument.source.uri}
          onActiveSheetChange={changeActiveSheet}
          onDiscard={() => {
            setDraft(serverDraft ?? editorDocument);
            setServerConflict(false);
          }}
          onDiscardDraft={discardDraft}
          onHistoryOpen={() => setHistoryOpen(true)}
          onModeChange={changeMode}
          onQueryChange={changeQuery}
          stageFilter={stageFilter}
          stageToneMap={stageToneMap}
          onStageFilterClear={clearStageFilter}
          onClearColumnFilters={() => {
            setColumnFiltersBySheet((current) => ({ ...current, [activeSheet]: {} }));
            setActiveFilterColumnKey(null);
          }}
          onSave={() => void requestSourceSave()}
          onSaveDraft={saveDraft}
        />

        <div
          ref={gridWrapRef}
          className="metadata-grid-wrap metadata-datasheet-wrap"
          style={metadataHeaderLayoutStyle}
          onMouseDownCapture={startColumnResize}
        >
          <div className="metadata-column-resize-guide" aria-hidden="true" />
          <DynamicDataSheetGrid
            ref={gridRef}
            className={`metadata-datasheet metadata-datasheet-${mode}${needsVerticalScroll ? "" : " metadata-datasheet-no-y-scroll"}`}
            value={displayedRows}
            columns={columns}
            cellClassName={({ rowData, columnId }) => {
              if ((rowData as SheetRow).__isAddRow && columnId !== "__gutter") return "metadata-add-row-cell";
              const runtimeRow = rowData as SheetRow;
              const cellHasIssue = Boolean(columnId) && activeSheetIssues.some(
                (issue) => issue.row_index === runtimeRow.__rowIndex && issue.column === columnId
              );
              const classNames: string[] = [];
              if (activeSheet === "dataflows" && !cellHasIssue) classNames.push(stageToneClass(runtimeRow.stage, stageToneMap));
              if (!cellHasIssue && columnId && metadataCellMatches(runtimeRow[columnId], query)) classNames.push("metadata-filter-match");
              return classNames.length ? classNames.join(" ") : undefined;
            }}
            rowClassName={({ rowData }) => {
              const runtimeRow = rowData as SheetRow;
              const classNames: string[] = [];
              if (runtimeRow.__isAddRow) classNames.push("metadata-add-row-line");
              if (sourceGroupStartIds.has(runtimeRow.__rowId)) classNames.push("metadata-source-group-start");
              return classNames.length ? classNames.join(" ") : undefined;
            }}
            onChange={editable ? updateRows : undefined}
            gutterColumn={metadataGutterColumn}
            rowKey={({ rowData }) => rowData.__rowId}
            rowHeight={metadataRowHeight}
            headerRowHeight={metadataHeaderRowHeight}
            height={gridHeight}
            lockRows={!editable || hasActiveRowSubsetFilter}
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
                        canMoveUp={Boolean(
                          rowIndex != null
                          && !hasActiveRowSubsetFilter
                          && canMoveMetadataRow(activeSheet, sheet.rows, rowIndex, -1)
                        )}
                        canMoveDown={Boolean(
                          rowIndex != null
                          && !hasActiveRowSubsetFilter
                          && canMoveMetadataRow(activeSheet, sheet.rows, rowIndex, 1)
                        )}
                        movementDisabledReason={hasActiveRowSubsetFilter ? "Clear the active filter before moving rows" : "Rows move only within the same source/order group"}
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
              {stageFilter ? `No dataflows match stage "${stageFilter}"` : query.trim() ? `No rows match "${query.trim()}"` : activeColumnFilterCount ? "No rows match the active column filters" : "No rows"}
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
      {pendingSourceSave && sourceSaveConfirmation ? (
        <MetadataSourceSaveConfirmationDialog
          busy={busy}
          confirmation={sourceSaveConfirmation}
          onCancel={() => void cancelSourceSave()}
          onConfirm={() => void confirmSourceSave()}
        />
      ) : null}
      {selectedDataflowRecord ? (
        <MetadataDataflowDrawer
          record={selectedDataflowRecord}
          environmentId={activeDocument.source.environment_id}
          pendingSqlFiles={activeDocument.sql_files ?? []}
          sectionOrder="source-destination-transform"
          editable={isEditableMetadataDataflowRecord(activeDocument, selectedDataflowRecord)}
          readOnly={readOnlyDocument || Boolean(selectedDataflowRecord.readOnly)}
          busy={busy}
          connectionRows={activeDocument?.sheets.connections?.rows ?? []}
          connectionColumns={activeDocument?.sheets.connections?.columns ?? []}
          relatedDataflows={dataflowRecords}
                        onSave={(nextRow, sqlOperation) => void requestSourceSave(
            activeDocument ? updateMetadataDataflowDocument(activeDocument, selectedDataflowRecord.rowIndex, nextRow, sqlOperation) : null,
          )}
          onSaveDraft={(nextRow, sqlOperation) => saveDraft(
            activeDocument ? updateMetadataDataflowDocument(activeDocument, selectedDataflowRecord.rowIndex, nextRow, sqlOperation) : null,
          )}
          onBack={dataflowDrawerStack.length ? popDataflowDrawer : undefined}
          onSelectRelatedDataflow={(related) => pushDataflowDrawer({
            metadataSourceId: related.metadataSourceId,
            rowIndex: related.rowIndex,
            dataflowId: related.dataflowId,
            name: related.name,
          })}
          onFocusInLineage={onFocusInLineage}
          onClose={closeDataflowDrawer}
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
