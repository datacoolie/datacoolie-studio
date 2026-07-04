import { useEffect, useMemo, useRef, useState } from "react";
import type { MouseEvent as ReactMouseEvent, PointerEvent as ReactPointerEvent, ReactNode } from "react";
import { StatusPill } from "../../shared/components/StatusPill";
import { formatTimestampForDisplay, isTimestampFieldName } from "../../shared/time";

export interface MetricItem {
  label: string;
  value: ReactNode;
  subtext?: ReactNode;
  tooltip?: string;
  intent?: "neutral" | "bad" | "good" | "warning";
}

export interface TableColumn<T extends Record<string, unknown>> {
  key: keyof T | string;
  label: string;
  render?: (row: T) => ReactNode;
  sortable?: boolean;
  sortKey?: string;
  className?: string;
  width?: number;
  autoFit?: boolean;
  minWidth?: number;
  maxWidth?: number;
  fillPriority?: "normal" | "last";
}

export interface TableSort {
  sortBy: string;
  sortDir: "asc" | "desc";
}

export function MetricGrid({ items }: { items: MetricItem[] }) {
  return (
    <div className="metric-row monitoring-metrics">
      {items.map((item) => (
        <div key={item.label} className={`metric-card metric-${item.intent ?? "neutral"}`} title={item.tooltip}>
          <span>{item.label}</span>
          <strong>{item.value}</strong>
          {item.subtext ? <small>{item.subtext}</small> : null}
        </div>
      ))}
    </div>
  );
}

export function BarList({
  rows,
  labelKey = "name",
  valueKey = "count",
  valueLabel,
  maxRows = 12
}: {
  rows: Record<string, unknown>[];
  labelKey?: string;
  valueKey?: string;
  valueLabel?: (value: number) => string;
  maxRows?: number;
}) {
  const visible = rows.slice(0, maxRows);
  const maxValue = Math.max(1, ...visible.map((row) => num(row, valueKey)));
  if (!visible.length) return <div className="table-empty">No records</div>;
  return (
    <div className="bar-list">
      {visible.map((row, index) => {
        const value = num(row, valueKey);
        const label = text(row, labelKey);
        return (
          <div key={`${label}-${index}`} className="bar-row">
            <span title={label}>{label}</span>
            <div className="bar-track">
              <div style={{ width: `${Math.max(3, (value / maxValue) * 100)}%` }} />
            </div>
            <strong>{valueLabel ? valueLabel(value) : formatNumber(value)}</strong>
          </div>
        );
      })}
    </div>
  );
}

export function StackedStatusBars({ rows, labelKey = "date" }: { rows: Record<string, unknown>[]; labelKey?: string }) {
  const statuses = ["succeeded", "failed", "skipped", "running", "pending", "unknown"];
  if (!rows.length) return <div className="table-empty">No records</div>;
  return (
    <div className="stacked-list">
      {rows.map((row, index) => {
        const total = statuses.reduce((sum, status) => sum + num(row, status), 0);
        return (
          <div key={`${text(row, labelKey)}-${index}`} className="stacked-row">
            <span>{text(row, labelKey)}</span>
            <div className="stacked-track">
              {statuses.map((status) => {
                const value = num(row, status);
                if (!value) return null;
                return (
                  <div
                    key={status}
                    className={`stacked-segment status-bg-${status}`}
                    title={`${status}: ${value}`}
                    style={{ width: `${(value / Math.max(1, total)) * 100}%` }}
                  />
                );
              })}
            </div>
            <strong>{formatNumber(total)}</strong>
          </div>
        );
      })}
    </div>
  );
}

export function ScatterPlot({
  points,
  xKey,
  yKey,
  colorKey = "engine_name",
  labelKey = "dataflow_name"
}: {
  points: Record<string, unknown>[];
  xKey: string;
  yKey: string;
  colorKey?: string;
  labelKey?: string;
}) {
  const visible = points.slice(0, 220);
  const maxX = Math.max(1, ...visible.map((point) => num(point, xKey)));
  const maxY = Math.max(1, ...visible.map((point) => num(point, yKey)));
  if (!visible.length) return <div className="table-empty">No records</div>;
  return (
    <div className="scatter-box">
      {visible.map((point, index) => {
        const x = Math.max(2, Math.min(96, (num(point, xKey) / maxX) * 92 + 2));
        const y = 98 - Math.max(2, Math.min(96, (num(point, yKey) / maxY) * 92 + 2));
        const color = text(point, colorKey).includes("Spark") ? "spark" : text(point, colorKey).includes("Polars") ? "polars" : "other";
        return (
          <span
            key={`${text(point, labelKey)}-${index}`}
            className={`scatter-point point-${color}`}
            style={{ left: `${x}%`, top: `${y}%` }}
            title={`${text(point, labelKey)} | ${formatNumber(num(point, xKey))} rows | ${formatSeconds(num(point, yKey))}`}
          />
        );
      })}
      <span className="scatter-axis x-axis">rows</span>
      <span className="scatter-axis y-axis">duration</span>
    </div>
  );
}

export function DataTable<T extends Record<string, unknown>>({
  rows,
  columns,
  maxRows = 12,
  offset = 0,
  onRowClick,
  sort,
  onSort,
  timezoneName,
  fixedLayout = true,
  className
}: {
  rows: T[];
  columns: TableColumn<T>[];
  maxRows?: number;
  offset?: number;
  onRowClick?: (row: T) => void;
  sort?: TableSort;
  onSort?: (sort: TableSort) => void;
  timezoneName?: string | null;
  fixedLayout?: boolean;
  className?: string;
}) {
  const [internalSort, setInternalSort] = useState<TableSort | undefined>(undefined);
  const activeSort = sort ?? internalSort;
  const sortedRows = useMemo(
    () => sortRows(rows, columns, activeSort),
    [rows, columns, activeSort]
  );
  const visible = sortedRows.slice(offset, offset + maxRows);
  const autoWidths = useMemo(
    () => calculateAutoFitWidths(visible, columns, timezoneName),
    [visible, columns, timezoneName]
  );
  const [columnWidths, setColumnWidths] = useState<Record<string, number>>({});
  const tableContainerRef = useRef<HTMLDivElement | null>(null);
  const [containerWidth, setContainerWidth] = useState(0);
  const resizingColumnRef = useRef<string | null>(null);
  const baseWidths = useMemo(
    () => calculateBaseWidths(columns, autoWidths, columnWidths),
    [columns, autoWidths, columnWidths]
  );
  const displayedWidths = useMemo(
    () => distributeTableWidth(columns, baseWidths, columnWidths, containerWidth),
    [columns, baseWidths, columnWidths, containerWidth]
  );
  const tableWidth = columns.reduce((sum, column) => {
    const key = String(column.key);
    return sum + (displayedWidths[key] ?? 0);
  }, 0);

  useEffect(() => {
    const element = tableContainerRef.current;
    if (!element) return;

    function measure() {
      setContainerWidth(element?.clientWidth ?? 0);
    }

    measure();
    const animationFrame = window.requestAnimationFrame(measure);
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => {
      window.cancelAnimationFrame(animationFrame);
      observer.disconnect();
    };
  }, []);

  useEffect(() => {
    const element = tableContainerRef.current;
    if (!element) return;
    const animationFrame = window.requestAnimationFrame(() => {
      setContainerWidth(element.clientWidth);
    });
    return () => window.cancelAnimationFrame(animationFrame);
  }, [rows, columns.length, tableWidth]);

  if (!visible.length) return <div className="table-empty">No records</div>;

  function handleSort(nextSort: TableSort) {
    if (onSort) {
      onSort(nextSort);
      return;
    }
    setInternalSort(nextSort);
  }

  function startColumnResize(event: ReactPointerEvent<HTMLSpanElement>, key: string) {
    event.preventDefault();
    event.stopPropagation();
    const headerCell = event.currentTarget.closest("th");
    if (!headerCell) return;
    const startX = event.clientX;
    const startWidth = headerCell.getBoundingClientRect().width;
    resizingColumnRef.current = key;

    function handlePointerMove(pointerEvent: PointerEvent) {
      if (resizingColumnRef.current !== key) return;
      const nextWidth = Math.max(32, Math.round(startWidth + pointerEvent.clientX - startX));
      setColumnWidths((current) => ({ ...current, [key]: nextWidth }));
    }

    function handlePointerUp() {
      resizingColumnRef.current = null;
      document.body.classList.remove("is-resizing-column");
      window.removeEventListener("pointermove", handlePointerMove);
      window.removeEventListener("pointerup", handlePointerUp);
    }

    document.body.classList.add("is-resizing-column");
    window.addEventListener("pointermove", handlePointerMove);
    window.addEventListener("pointerup", handlePointerUp);
  }

  function resetColumnWidth(event: ReactMouseEvent<HTMLSpanElement>, key: string) {
    event.preventDefault();
    event.stopPropagation();
    setColumnWidths((current) => {
      const next = { ...current };
      delete next[key];
      return next;
    });
  }

  return (
    <div ref={tableContainerRef} className={`table-scroll short monitoring-data-table${className ? ` ${className}` : ""}`}>
      <table
        className={fixedLayout ? "data-table-fixed" : undefined}
        style={tableWidth ? { width: `${tableWidth}px`, minWidth: `${tableWidth}px` } : undefined}
      >
        <colgroup>
          {columns.map((column) => {
            const key = String(column.key);
            const width = displayedWidths[key];
            return <col key={key} style={width ? { width: `${width}px` } : undefined} />;
          })}
        </colgroup>
        <thead>
          <tr>
            {columns.map((column) => {
              const sortKey = column.sortKey ?? String(column.key);
              const active = activeSort?.sortBy === sortKey;
              const key = String(column.key);
              const width = displayedWidths[key];
              const widthStyle = width ? { width: `${width}px` } : undefined;
              return (
                <th key={key} className={column.className} style={widthStyle}>
                  {column.sortable ? (
                    <button
                      className={`table-sort-button${active ? " active" : ""}`}
                      type="button"
                      onClick={() => handleSort({ sortBy: sortKey, sortDir: active && activeSort?.sortDir === "desc" ? "asc" : "desc" })}
                    >
                      <span>{column.label}</span>
                      <span aria-hidden="true">{active ? (activeSort?.sortDir === "desc" ? "↓" : "↑") : "↕"}</span>
                    </button>
                  ) : (
                    column.label
                  )}
                  <span
                    className="table-column-resizer"
                    aria-hidden="true"
                    onPointerDown={(event) => startColumnResize(event, key)}
                    onDoubleClick={(event) => resetColumnWidth(event, key)}
                  />
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {visible.map((row, index) => (
            <tr
              key={index}
              className={onRowClick ? "clickable-row" : undefined}
              tabIndex={onRowClick ? 0 : undefined}
              onClick={onRowClick ? () => onRowClick(row) : undefined}
              onKeyDown={
                onRowClick
                  ? (event) => {
                      if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault();
                        onRowClick(row);
                      }
                    }
                  : undefined
              }
            >
              {columns.map((column) => {
                const key = String(column.key);
                const width = displayedWidths[key];
                const widthStyle = width ? { width: `${width}px` } : undefined;
                return (
                  <td key={key} className={column.className} style={widthStyle}>
                    {column.render ? column.render(row) : display(row, key, timezoneName)}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function StatusCell<T extends Record<string, unknown>>({ row, keyName = "status" }: { row: T; keyName?: string }) {
  return <StatusPill status={text(row, keyName)} />;
}

export function Panel({ title, subtitle, children }: { title: string; subtitle?: string; children: ReactNode }) {
  return (
    <section className="table-panel">
      <div className="panel-toolbar compact">
        <div>
          <h2>{title}</h2>
          {subtitle ? <span>{subtitle}</span> : null}
        </div>
      </div>
      {children}
    </section>
  );
}

export function display(row: Record<string, unknown>, key: string, timezoneName?: string | null) {
  const value = row[key];
  if (typeof value === "number") return formatNumber(value);
  if (value === null || value === undefined || value === "") return "-";
  if (typeof value === "string" && timezoneName && isTimestampFieldName(key)) return formatTimestampForDisplay(value, timezoneName);
  return String(value);
}

export function text(row: Record<string, unknown>, key: string) {
  const value = row[key];
  if (value === null || value === undefined || value === "") return "unknown";
  return String(value);
}

export function num(row: Record<string, unknown>, key: string) {
  const value = row[key];
  return typeof value === "number" && Number.isFinite(value) ? value : Number(value) || 0;
}

export function formatSeconds(value: number) {
  if (!Number.isFinite(value)) return "-";
  if (value < 60) return `${value.toFixed(2)}s`;
  return `${(value / 60).toFixed(2)}m`;
}

export function formatBytes(value: number) {
  if (!Number.isFinite(value)) return "-";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let size = Math.abs(value);
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024;
    unit += 1;
  }
  const sign = value < 0 ? "-" : "";
  return `${sign}${size.toFixed(unit ? 1 : 0)} ${units[unit]}`;
}

export function formatNumber(value: number) {
  return new Intl.NumberFormat("en-US", { maximumFractionDigits: value < 10 ? 2 : 0 }).format(value);
}

let tableMeasureContext: CanvasRenderingContext2D | null | undefined;

function calculateBaseWidths<T extends Record<string, unknown>>(
  columns: TableColumn<T>[],
  autoWidths: Record<string, number>,
  columnWidths: Record<string, number>
) {
  return Object.fromEntries(
    columns.map((column) => {
      const key = String(column.key);
      return [key, columnWidths[key] ?? autoWidths[key] ?? column.width ?? column.minWidth ?? 96];
    })
  ) as Record<string, number>;
}

function distributeTableWidth<T extends Record<string, unknown>>(
  columns: TableColumn<T>[],
  baseWidths: Record<string, number>,
  columnWidths: Record<string, number>,
  containerWidth: number
) {
  const total = columns.reduce((sum, column) => sum + (baseWidths[String(column.key)] ?? 0), 0);
  if (!containerWidth || total >= containerWidth) return baseWidths;

  const manualKeys = new Set(Object.keys(columnWidths));
  const fillColumns = columns.filter((column) => {
    if (manualKeys.has(String(column.key))) return false;
    if (column.fillPriority === "normal") return true;
    if (isLongTextFillColumn(column)) return true;
    return column.width === undefined;
  });
  if (!fillColumns.length) return baseWidths;

  const next = { ...baseWidths };
  let remaining = Math.floor(containerWidth - total);
  const longTextColumns = fillColumns.filter(isLongTextFillColumn);
  const normalColumns = fillColumns.filter((column) => !isLongTextFillColumn(column));
  remaining = distributeWidthToColumns(next, longTextColumns, remaining);
  remaining = distributeWidthToColumns(next, normalColumns, remaining);
  if (remaining > 0 && longTextColumns.length) {
    remaining = distributeWidthToColumns(next, longTextColumns, remaining, { ignoreMaxWidth: true });
  }
  return shrinkTableWidthToContainer(next, columns, columnWidths, containerWidth);
}

function distributeWidthToColumns<T extends Record<string, unknown>>(
  widths: Record<string, number>,
  columns: TableColumn<T>[],
  spareWidth: number,
  options: { ignoreMaxWidth?: boolean } = {}
) {
  let remaining = spareWidth;
  let candidates = columns;
  while (remaining > 0 && candidates.length) {
    const perColumn = Math.max(1, Math.floor(remaining / candidates.length));
    let used = 0;
    const nextCandidates = [];
    for (const column of candidates) {
      const key = String(column.key);
      const currentWidth = widths[key] ?? 0;
      const maxWidth = options.ignoreMaxWidth ? Number.POSITIVE_INFINITY : column.maxWidth ?? Number.POSITIVE_INFINITY;
      const extra = Math.min(perColumn, Math.max(0, maxWidth - currentWidth));
      if (extra > 0) {
        widths[key] = currentWidth + extra;
        used += extra;
      }
      if ((widths[key] ?? 0) < maxWidth) nextCandidates.push(column);
    }
    if (!used) break;
    remaining -= used;
    candidates = nextCandidates;
  }
  return remaining;
}

function shrinkTableWidthToContainer<T extends Record<string, unknown>>(
  widths: Record<string, number>,
  columns: TableColumn<T>[],
  columnWidths: Record<string, number>,
  containerWidth: number
) {
  let overflow = columns.reduce((sum, column) => sum + (widths[String(column.key)] ?? 0), 0) - containerWidth;
  if (overflow <= 0) return widths;

  const manualKeys = new Set(Object.keys(columnWidths));
  const candidates = columns
    .filter((column) => !manualKeys.has(String(column.key)))
    .filter((column) => column.fillPriority === "last" || column.fillPriority === "normal" || isLongTextFillColumn(column) || column.width === undefined)
    .sort((left, right) => Number(isLongTextFillColumn(right)) - Number(isLongTextFillColumn(left)));
  const next = { ...widths };
  for (const column of candidates) {
    if (overflow <= 0) break;
    const key = String(column.key);
    const currentWidth = next[key] ?? 0;
    const minWidth = column.minWidth ?? 32;
    const shrink = Math.min(overflow, Math.max(0, currentWidth - minWidth));
    if (shrink <= 0) continue;
    next[key] = currentWidth - shrink;
    overflow -= shrink;
  }
  return next;
}

function isLongTextFillColumn<T extends Record<string, unknown>>(column: TableColumn<T>) {
  if (column.fillPriority === "last") return true;
  if (column.fillPriority === "normal") return false;
  const key = String(column.key).toLowerCase();
  const label = column.label.toLowerCase();
  return /error|message|issue|detail|description|query|json|payload|sql|text/u.test(`${key} ${label}`);
}

function calculateAutoFitWidths<T extends Record<string, unknown>>(
  rows: T[],
  columns: TableColumn<T>[],
  timezoneName?: string | null
) {
  const autoColumns = columns.filter((column) => column.autoFit === true || (column.autoFit !== false && column.width === undefined));
  if (!autoColumns.length) return {};
  const context = getTableMeasureContext();
  const result: Record<string, number> = {};

  for (const column of autoColumns) {
    const key = String(column.key);
    const headerWidth = measureTextWidth(context, column.label) + 38;
    if (isLongTextFillColumn(column)) {
      const preferredWidth = column.width ?? column.minWidth ?? headerWidth;
      result[key] = Math.max(column.minWidth ?? 96, Math.min(column.maxWidth ?? Number.POSITIVE_INFINITY, preferredWidth));
      continue;
    }
    const sampleWidths = rows.slice(0, 250).map((row) => {
      const value = column.render ? renderedText(row, column) : display(row, key, timezoneName);
      return measureTextWidth(context, value) + 26;
    });
    const measured = Math.ceil(Math.max(headerWidth, ...sampleWidths, column.minWidth ?? 0));
    result[key] = Math.max(column.minWidth ?? 48, Math.min(column.maxWidth ?? column.width ?? 180, measured));
  }

  return result;
}

function sortRows<T extends Record<string, unknown>>(rows: T[], columns: TableColumn<T>[], sort?: TableSort) {
  if (!sort) return rows;
  const column = columns.find((item) => (item.sortKey ?? String(item.key)) === sort.sortBy);
  if (!column) return rows;
  const key = String(column.key);
  const direction = sort.sortDir === "desc" ? -1 : 1;
  return [...rows].sort((left, right) => compareTableValues(tableSortValue(left, key), tableSortValue(right, key)) * direction);
}

function tableSortValue(row: Record<string, unknown>, key: string) {
  const value = row[key];
  if (value === null || value === undefined || value === "") return "";
  if (typeof value === "number") return value;
  const text = String(value);
  const parsed = Number(text);
  if (text.trim() && Number.isFinite(parsed)) return parsed;
  const time = Date.parse(text);
  if (Number.isFinite(time) && /\d{4}-\d{2}-\d{2}/.test(text)) return time;
  return text.toLowerCase();
}

function compareTableValues(left: string | number, right: string | number) {
  if (typeof left === "number" && typeof right === "number") return left - right;
  return String(left).localeCompare(String(right), undefined, { numeric: true, sensitivity: "base" });
}

function getTableMeasureContext() {
  if (tableMeasureContext !== undefined) return tableMeasureContext;
  if (typeof document === "undefined") {
    tableMeasureContext = null;
    return tableMeasureContext;
  }
  const canvas = document.createElement("canvas");
  tableMeasureContext = canvas.getContext("2d");
  if (tableMeasureContext) tableMeasureContext.font = "12px Inter, ui-sans-serif, system-ui, sans-serif";
  return tableMeasureContext;
}

function measureTextWidth(context: CanvasRenderingContext2D | null | undefined, value: string) {
  if (!context) return Math.min(180, Math.max(48, value.length * 7));
  return context.measureText(value).width;
}

function renderedText<T extends Record<string, unknown>>(row: T, column: TableColumn<T>) {
  const rendered = column.render?.(row);
  if (rendered === null || rendered === undefined || rendered === false) return "-";
  if (typeof rendered === "string" || typeof rendered === "number") return String(rendered);
  const key = String(column.key);
  return display(row, key);
}
