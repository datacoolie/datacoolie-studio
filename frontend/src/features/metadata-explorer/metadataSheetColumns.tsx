import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { checkboxColumn, createTextColumn, keyColumn, type CellComponent, type Column } from "react-datasheet-grid";
import type { MetadataEditorIssue } from "../../shared/api/domainTypes";
import { MetadataStructuredCell } from "./MetadataStructuredCell";
import { formatCellValue, parseCellText, structuredCellKind, type MetadataSourceOption } from "./metadataSheetOperations";
import type { SheetRow } from "./metadataSheetTypes";

interface SheetColumn {
  key: string;
  name: string;
}

interface BuildSheetColumnsOptions {
  columns: SheetColumn[];
  rows: SheetRow[];
  issues: MetadataEditorIssue[];
  editable: boolean;
  connectionOptions: string[];
  metadataSourceOptions: MetadataSourceOption[];
  columnWidths: Record<string, number>;
  onColumnWidthChange: (columnKey: string, width: number) => void;
}

interface ConnectionColumnData {
  options: string[];
}

interface MetadataTextColumnData {
  columnKey: string;
  readOnly: boolean;
  textColumnData: unknown;
}

interface MetadataSourceColumnData {
  options: MetadataSourceOption[];
}

const FALLBACK_CHARACTER_WIDTH = 7;
const STUDIO_METADATA_SOURCE_COLUMN_KEY = "__metadata_source_name";
let measureRuler: HTMLDivElement | null | undefined;

const metadataTextColumnBase = createTextColumn<unknown>({
  continuousUpdates: false,
  parseUserInput: (value) => parseCellText(value),
  parsePastedValue: (value) => parseCellText(value),
  formatBlurredInput: (value) => formatCellValue(value),
  formatInputOnFocus: (value) => formatCellValue(value),
  formatForCopy: (value) => formatCellValue(value),
  deletedValue: null
});

const MetadataTextCellBase = metadataTextColumnBase.component as CellComponent<unknown, any> | undefined;
const metadataTextCell: CellComponent<unknown, MetadataTextColumnData> = (props) => {
  const structured = Boolean(structuredCellKind(props.columnData.columnKey, props.rowData));
  if (structured) {
    return <MetadataStructuredCell {...props} columnData={{ columnKey: props.columnData.columnKey, readOnly: props.columnData.readOnly }} />;
  }
  return (
    <div className="metadata-text-cell" title={formatCellValue(props.rowData)}>
      {MetadataTextCellBase ? <MetadataTextCellBase {...props} columnData={props.columnData.textColumnData} /> : null}
    </div>
  );
};

function createMetadataTextColumn(columnKey: string, readOnly: boolean): Partial<Column<unknown, MetadataTextColumnData, string>> {
  return {
    ...metadataTextColumnBase,
    component: metadataTextCell,
    columnData: {
      columnKey,
      readOnly,
      textColumnData: metadataTextColumnBase.columnData
    }
  };
}

const connectionCell: CellComponent<unknown, ConnectionColumnData> = ({ rowData, setRowData, focus, disabled, columnData, stopEditing }) => {
  const ref = useRef<HTMLInputElement>(null);
  const cellRef = useRef<HTMLDivElement>(null);
  const value = formatCellValue(rowData);
  const [draftValue, setDraftValue] = useState(value);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    setDraftValue(value);
  }, [value]);

  useLayoutEffect(() => {
    if (focus) {
      setDraftValue(value);
      setOpen(true);
      ref.current?.focus();
      ref.current?.select();
    } else {
      setOpen(false);
    }
  }, [focus, value]);

  const visibleOptions = useMemo(() => {
    const query = draftValue.trim().toLowerCase();
    const options = query && query !== value.toLowerCase()
      ? columnData.options.filter((option) => option.toLowerCase().includes(query))
      : columnData.options;
    return options.slice(0, 80);
  }, [columnData.options, draftValue, value]);

  function commit(nextValue = draftValue) {
    setRowData(parseCellText(nextValue));
    setDraftValue(nextValue);
    setOpen(false);
  }

  function cancel() {
    setDraftValue(value);
    setOpen(false);
    stopEditing({ nextRow: false });
  }

  return (
    <div ref={cellRef} className="metadata-connection-cell">
      <input
        ref={ref}
        className="metadata-connection-input"
        disabled={disabled}
        title={value}
        value={draftValue}
        onBlur={(event) => {
          if (cellRef.current?.contains(event.relatedTarget as Node | null)) return;
          if (focus) commit();
        }}
        onChange={(event) => {
          setDraftValue(event.target.value);
          setOpen(true);
        }}
        onFocus={() => {
          if (!disabled) setOpen(true);
        }}
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            event.preventDefault();
            event.stopPropagation();
            cancel();
          } else if (event.key === "Enter" || event.key === "Tab") {
            commit();
          }
        }}
      />
      {focus && open && !disabled ? (
        <div className="metadata-connection-options" role="listbox">
          {visibleOptions.length ? visibleOptions.map((option) => (
            <button
              key={option}
              type="button"
              role="option"
              onMouseDown={(event) => {
                event.preventDefault();
                commit(option);
                stopEditing({ nextRow: false });
              }}
            >
              {option}
            </button>
          )) : (
            <span>No matching connection</span>
          )}
        </div>
      ) : null}
    </div>
  );
};

function createConnectionColumn(options: string[]): Partial<Column<unknown, ConnectionColumnData, string>> {
  return {
    component: connectionCell,
    columnData: { options },
    copyValue: ({ rowData }) => formatCellValue(rowData),
    pasteValue: ({ value }) => parseCellText(value),
    deleteValue: () => null,
    isCellEmpty: ({ rowData }) => !formatCellValue(rowData)
  };
}

const studioMetadataSourceCell: CellComponent<SheetRow, MetadataSourceColumnData> = ({ rowData, setRowData, focus, disabled, columnData, stopEditing }) => {
  const ref = useRef<HTMLInputElement>(null);
  const cellRef = useRef<HTMLDivElement>(null);
  const sourceName = formatCellValue(rowData.__metadata_source_name) || "metadata_source";
  const sourceUri = formatCellValue(rowData.__metadata_source_uri);
  const title = sourceUri ? `${sourceName}\n${sourceUri}` : sourceName;
  const [draftValue, setDraftValue] = useState(sourceName);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    setDraftValue(sourceName);
  }, [sourceName]);

  useLayoutEffect(() => {
    if (focus) {
      setDraftValue(sourceName);
      setOpen(true);
      ref.current?.focus();
      ref.current?.select();
    } else {
      setOpen(false);
    }
  }, [focus, sourceName]);

  const visibleOptions = useMemo(() => {
    const query = draftValue.trim().toLowerCase();
    const options = query && query !== sourceName.toLowerCase()
      ? columnData.options.filter((option) => sourceOptionMatches(option, query))
      : columnData.options;
    return options.slice(0, 80);
  }, [columnData.options, draftValue, sourceName]);

  function commit(nextValue = draftValue) {
    const value = nextValue.trim();
    const option = findExactSourceOption(columnData.options, value);
    setRowData({
      ...rowData,
      __metadata_source_id: option?.source_id ?? null,
      __metadata_source_name: option?.name ?? value,
      __metadata_source_uri: option?.uri ?? "",
      __metadata_source_kind: "metadata"
    });
    setDraftValue(option?.name ?? value);
    setOpen(false);
  }

  function cancel() {
    setDraftValue(sourceName);
    setOpen(false);
    stopEditing({ nextRow: false });
  }

  if (focus && !disabled) {
    return (
      <div ref={cellRef} className="metadata-connection-cell metadata-source-routing-editor">
        <input
          ref={ref}
          className="metadata-connection-input"
          title={title}
          value={draftValue}
          onBlur={(event) => {
            if (cellRef.current?.contains(event.relatedTarget as Node | null)) return;
            commit();
          }}
          onChange={(event) => {
            setDraftValue(event.target.value);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          onKeyDown={(event) => {
            if (event.key === "Escape") {
              event.preventDefault();
              event.stopPropagation();
              cancel();
            } else if (event.key === "Enter" || event.key === "Tab") {
              commit();
            }
          }}
        />
        {open ? (
          <div className="metadata-connection-options" role="listbox">
            {visibleOptions.length ? visibleOptions.map((option) => (
              <button
                key={option.source_id}
                type="button"
                role="option"
                title={option.uri}
                onMouseDown={(event) => {
                  event.preventDefault();
                  commit(option.name);
                  stopEditing({ nextRow: false });
                }}
              >
                <strong>{option.name}</strong>
                <span>{shortSourceUri(option.uri)}</span>
              </button>
            )) : (
              <span>Create "{draftValue.trim() || "new metadata source"}" on save</span>
            )}
          </div>
        ) : null}
      </div>
    );
  }

  return (
    <div className="metadata-studio-source-cell" title={title}>
      <span>{sourceName}</span>
    </div>
  );
};

function createStudioMetadataSourceColumn(columnKey: string, options: MetadataSourceOption[]): Partial<Column<SheetRow, MetadataSourceColumnData, string>> {
  return {
    id: columnKey,
    component: studioMetadataSourceCell,
    columnData: { options },
    copyValue: ({ rowData }) => formatCellValue(rowData.__metadata_source_name),
    pasteValue: ({ rowData, value }) => {
      const sourceName = parseCellText(value);
      if (!sourceName) return rowData;
      return {
        ...rowData,
        __metadata_source_id: null,
        __metadata_source_name: formatCellValue(sourceName),
        __metadata_source_uri: "",
        __metadata_source_kind: "metadata"
      };
    },
    deleteValue: ({ rowData }) => ({
      ...rowData,
      __metadata_source_id: null,
      __metadata_source_name: "",
      __metadata_source_uri: "",
      __metadata_source_kind: "metadata"
    }),
    isCellEmpty: ({ rowData }) => !formatCellValue(rowData.__metadata_source_name)
  };
}

export function buildSheetColumns({ columns, rows, issues, editable, connectionOptions, metadataSourceOptions, columnWidths, onColumnWidthChange }: BuildSheetColumnsOptions): Column<SheetRow>[] {
  return columns.map((column) => {
    const studioRoutingColumn = isStudioMetadataSourceColumn(column.key);
    const baseColumn: Partial<Column<SheetRow, any, string>> = studioRoutingColumn
      ? createStudioMetadataSourceColumn(column.key, metadataSourceOptions)
      : keyColumn(column.key as keyof SheetRow, resolveBaseColumn(column.key, connectionOptions, !editable)) as Partial<Column<SheetRow, any, string>>;
    const basis = columnWidths[column.key] ?? initialColumnWidth(column, rows);
    return {
      ...baseColumn,
      title: (
        <ResizableHeader
          columnKey={column.key}
          title={column.name}
          width={basis}
          autoWidth={autoFitColumnWidth(column, rows)}
          onColumnWidthChange={onColumnWidthChange}
        />
      ),
      minWidth: 72,
      basis,
      grow: 0,
      shrink: 0,
      headerClassName: metadataHeaderClassName(column.key),
      disabled: !editable && !columnHasStructuredValues(column.key, rows) && !isConnectionReferenceColumn(column.key),
      cellClassName: ({ rowData }) => metadataCellClassName({ issues, rowData, columnKey: column.key, connectionOptions })
    };
  });
}

interface ResizableHeaderProps {
  columnKey: string;
  title: string;
  width: number;
  autoWidth: number;
  onColumnWidthChange: (columnKey: string, width: number) => void;
}

function ResizableHeader({ columnKey, title, width, autoWidth, onColumnWidthChange }: ResizableHeaderProps) {
  return (
    <span className="metadata-resizable-header" title={title}>
      <span>{title}</span>
      <button
        className="metadata-column-resizer"
        type="button"
        aria-label={`Resize ${title}`}
        data-column-key={columnKey}
        data-column-width={width}
        data-column-auto-width={autoWidth}
        onMouseDown={(event) => {
          if (event.detail > 1) event.stopPropagation();
        }}
        onClick={(event) => event.stopPropagation()}
        onDoubleClick={(event) => {
          event.preventDefault();
          event.stopPropagation();
          onColumnWidthChange(columnKey, autoWidth);
        }}
      />
    </span>
  );
}

function resolveBaseColumn(key: string, connectionOptions: string[], readOnly: boolean) {
  if (key === "is_active") return checkboxColumn as Partial<Column<SheetRow[keyof SheetRow], any, string>>;
  if (!readOnly && isConnectionReferenceColumn(key)) return createConnectionColumn(connectionOptions) as Partial<Column<SheetRow[keyof SheetRow], any, string>>;
  return createMetadataTextColumn(key, readOnly) as Partial<Column<SheetRow[keyof SheetRow], any, string>>;
}

function columnHasStructuredValues(key: string, rows: SheetRow[]) {
  return Boolean(structuredCellKind(key, null)) || rows.some((row) => Boolean(structuredCellKind(key, row[key])));
}

function isStudioMetadataSourceColumn(key: string) {
  return key === STUDIO_METADATA_SOURCE_COLUMN_KEY;
}

function sourceOptionMatches(option: MetadataSourceOption, query: string) {
  const normalized = query.trim().toLowerCase();
  return option.name.toLowerCase().includes(normalized)
    || option.uri.toLowerCase().includes(normalized)
    || shortSourceUri(option.uri).toLowerCase().includes(normalized);
}

function findExactSourceOption(options: MetadataSourceOption[], value: string) {
  const normalized = value.trim().toLowerCase();
  return options.find((option) =>
    option.name.toLowerCase() === normalized
    || option.uri.toLowerCase() === normalized
    || shortSourceUri(option.uri).toLowerCase() === normalized
  );
}

function shortSourceUri(value: string) {
  return value.replace(/\\/g, "/").split("/").filter(Boolean).slice(-2).join("/");
}

function isConnectionReferenceColumn(key: string) {
  return key === "connection_name" || key.endsWith("_connection_name");
}

function hasIssue(issues: MetadataEditorIssue[], row: SheetRow, columnKey: string) {
  return issues.some((issue) => issue.row_index === row.__rowIndex && issue.column === columnKey);
}

function metadataCellClassName({
  issues,
  rowData,
  columnKey,
  connectionOptions
}: {
  issues: MetadataEditorIssue[];
  rowData: SheetRow;
  columnKey: string;
  connectionOptions: string[];
}) {
  const classNames: string[] = [];
  if (isStudioMetadataSourceColumn(columnKey)) classNames.push("metadata-cell-studio-routing");
  if (hasIssue(issues, rowData, columnKey)) classNames.push("metadata-cell-issue");
  if (isConnectionReferenceColumn(columnKey)) {
    const value = formatCellValue(rowData[columnKey]);
    if (value && !connectionOptions.includes(value)) classNames.push("metadata-cell-warning");
  }
  return classNames.length ? classNames.join(" ") : undefined;
}

const preferredInitialWidthColumns = new Set([
  "connection_id",
  "name",
  "connection_type",
  "format",
  "catalog",
  "database",
  "configure",
  "secret_ref",
  "secrets_ref",
  "dataflow_id",
  "stage",
  "source_connection_name",
  "source_schema_name",
  "source_table",
  "source_python_function",
  "source_watermark_columns",
  "destination_connection_name",
  "destination_schema_name",
  "destination_table",
  "destination_load_type",
  "destination_merge_keys",
  "connection_name",
  "schema_name",
  "table_name",
  "column_name",
  "data_type"
]);

function initialPresetWidth(key: string) {
  const normalizedKey = normalizeColumnKeyPrefix(key);
  if (normalizedKey.includes("configure") || normalizedKey.includes("query") || normalizedKey.includes("filter_expression") || normalizedKey.includes("schema_hints")) return 240;
  if (normalizedKey.includes("connection") || normalizedKey.includes("destination") || normalizedKey.includes("source") || normalizedKey.includes("transform")) return 190;
  if (normalizedKey === "description") return 220;
  if (normalizedKey === "name" || normalizedKey.endsWith("_id")) return 180;
  if (normalizedKey === "stage") return 150;
  if (normalizedKey === "is_active") return 110;
  return 140;
}

function initialColumnWidth(column: SheetColumn, rows: SheetRow[]) {
  const normalizedKey = normalizeColumnKeyPrefix(column.key);
  if (isStudioMetadataSourceColumn(normalizedKey)) return measuredColumnWidth(column, rows);
  if (!usesPreferredInitialWidth(column.key)) return measuredHeaderWidth(column);
  return Math.min(initialPresetWidth(normalizedKey), measuredColumnWidth(column, rows));
}

function autoFitColumnWidth(column: SheetColumn, rows: SheetRow[]) {
  return Math.min(480, measuredColumnWidth(column, rows));
}

function measuredColumnWidth(column: SheetColumn, rows: SheetRow[]) {
  const headerWidth = measuredHeaderWidth(column);
  const cellWidth = rows.reduce(
    (current, row) => Math.max(current, measureCellWidth(formatCellValue(row[column.key]))),
    0
  );
  return Math.max(72, Math.ceil(Math.max(headerWidth, cellWidth)));
}

function measuredHeaderWidth(column: SheetColumn) {
  return Math.max(72, Math.ceil(measureNativeTextWidth(column.name, "metadata-autofit-header-ruler")));
}

function usesPreferredInitialWidth(key: string) {
  const normalizedKey = normalizeColumnKeyPrefix(key);
  if (isStudioMetadataSourceColumn(normalizedKey)) return false;
  return preferredInitialWidthColumns.has(normalizedKey);
}

function metadataHeaderClassName(key: string) {
  const normalizedKey = normalizeColumnKeyPrefix(key);
  if (isStudioMetadataSourceColumn(normalizedKey)) return "metadata-header-studio";
  if (normalizedKey.startsWith("source_")) return "metadata-header-source";
  if (normalizedKey.startsWith("transform_")) return "metadata-header-transform";
  if (normalizedKey.startsWith("destination_")) return "metadata-header-destination";
  return undefined;
}

function normalizeColumnKeyPrefix(key: string) {
  if (key.startsWith("src_")) return `source_${key.slice(4)}`;
  if (key.startsWith("tran_")) return `transform_${key.slice(5)}`;
  if (key.startsWith("dest_")) return `destination_${key.slice(5)}`;
  return key;
}

function measureCellWidth(value: string) {
  return measureNativeTextWidth(value, "metadata-autofit-cell-ruler");
}

function measureNativeTextWidth(value: string, className: string) {
  if (!value) return 0;
  const ruler = getMeasureRuler();
  if (!ruler) return value.length * FALLBACK_CHARACTER_WIDTH;
  ruler.className = className;
  ruler.textContent = value;
  return ruler.getBoundingClientRect().width;
}

function getMeasureRuler() {
  if (measureRuler !== undefined) return measureRuler;
  if (typeof document === "undefined") {
    measureRuler = null;
    return measureRuler;
  }
  measureRuler = document.createElement("div");
  measureRuler.setAttribute("aria-hidden", "true");
  document.body.appendChild(measureRuler);
  return measureRuler;
}
