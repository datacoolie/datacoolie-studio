import { defaultRangeExtractor, useVirtualizer, type Range, type VirtualItem, type Virtualizer } from "@tanstack/react-virtual";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { KeyboardEvent as ReactKeyboardEvent, MouseEvent as ReactMouseEvent, PointerEvent as ReactPointerEvent, ReactNode } from "react";
import { StatusPill } from "./StatusPill";
import { Help, useHelpEnabled } from "./Help";
import { dismissActiveHelp } from "./helpCoordinator";
import { formatTimestampForDisplay, isTimestampFieldName } from "../time";
import { CompactNumberValue } from "./CompactNumberValue";
import { compareMetricValues, formatCompactNumber, type CompactNumberStyle, type MetricValue } from "../lib/numberFormat";

export interface TableColumn<T extends Record<string, unknown>> {
  key: keyof T | string;
  label: string;
  headerTitle?: string;
  render?: (row: T) => ReactNode;
  sortable?: boolean;
  sortKey?: string;
  className?: string;
  width?: number;
  autoFit?: boolean;
  minWidth?: number;
  maxWidth?: number;
  fillPriority?: "normal" | "last";
  measureValue?: (row: T, timezoneName?: string | null) => string | string[];
  /** Opt in to measuring displayed lines with their computed typography and decoration. */
  measureSelectors?: readonly string[];
}

export interface TableSort {
  sortBy: string;
  sortDir: "asc" | "desc";
}

export interface DataTableVirtualizationOptions {
  /** Estimated body-row height in pixels before a rendered row is measured. */
  estimateSize?: number;
  /** Number of rows to keep rendered on each side of the viewport. */
  overscan?: number;
}

export type DataTableRowKey = string | number;

export type DataTableVirtualization = boolean | DataTableVirtualizationOptions;

const useIsomorphicLayoutEffect = typeof window === "undefined" ? useEffect : useLayoutEffect;

export function DataTable<T extends Record<string, unknown>>({
  rows,
  columns,
  maxRows = 12,
  offset = 0,
  onRowClick,
  sort,
  onSort,
  rowClassName,
  timezoneName,
  fixedLayout = true,
  compactNumbers = false,
  compactNumberStyle = "suffix",
  className,
  sortMode = "client",
  getRowKey,
  virtualization,
  scrollResetKey
}: {
  rows: T[];
  columns: TableColumn<T>[];
  maxRows?: number;
  offset?: number;
  onRowClick?: (row: T) => void;
  sort?: TableSort;
  onSort?: (sort: TableSort) => void;
  rowClassName?: (row: T, index: number, visibleRows: T[]) => string | undefined;
  timezoneName?: string | null;
  fixedLayout?: boolean;
  compactNumbers?: boolean;
  compactNumberStyle?: CompactNumberStyle;
  className?: string;
  /** Use this for a response whose order is authoritative on the server. */
  sortMode?: "client" | "server";
  /** Stable identity for row reuse, focus retention, and virtualizer measurements. */
  getRowKey?: (row: T, index: number) => DataTableRowKey;
  /** Virtualize the fetched page while retaining native table markup. */
  virtualization?: DataTableVirtualization;
  /** Change this when pagination, sort, or filter scope should return to row zero. */
  scrollResetKey?: DataTableRowKey | null;
}) {
  const [internalSort, setInternalSort] = useState<TableSort | undefined>(undefined);
  const helpEnabled = useHelpEnabled();
  const activeSort = sort ?? internalSort;
  useEffect(() => { if (helpEnabled) dismissActiveHelp(); }, [helpEnabled, rows, offset, activeSort, scrollResetKey]);
  const sortedRows = useMemo(
    () => (sortMode === "server" ? rows : sortRows(rows, columns, activeSort)),
    [rows, columns, activeSort, sortMode]
  );
  const visible = useMemo(
    () => sortedRows.slice(offset, offset + maxRows),
    [sortedRows, offset, maxRows]
  );
  const virtualizationOptions = typeof virtualization === "object" ? virtualization : undefined;
  const virtualizationEnabled = virtualization === true || virtualizationOptions !== undefined;
  const autoWidths = useMemo(
    () => calculateAutoFitWidths(visible, columns, timezoneName, compactNumbers, compactNumberStyle, !virtualizationEnabled),
    [visible, columns, timezoneName, compactNumbers, compactNumberStyle, virtualizationEnabled]
  );
  const [columnWidths, setColumnWidths] = useState<Record<string, number>>({});
  const [renderedAutoWidths, setRenderedAutoWidths] = useState<Record<string, number>>({});
  const tableContainerRef = useRef<HTMLDivElement | null>(null);
  const [scrollElement, setScrollElement] = useState<HTMLDivElement | null>(null);
  const [containerWidth, setContainerWidth] = useState(0);
  const resizingColumnRef = useRef<string | null>(null);
  const resizeCleanupRef = useRef<(() => void) | null>(null);
  const [headerElement, setHeaderElement] = useState<HTMLTableSectionElement | null>(null);
  const [headerHeight, setHeaderHeight] = useState(0);
  const [densityEstimate, setDensityEstimate] = useState(42);
  const rowRefs = useRef(new Map<DataTableRowKey, HTMLTableRowElement>());
  const rowRefCallbacks = useRef(new Map<DataTableRowKey, { renderedVirtually: boolean; callback: (element: HTMLTableRowElement | null) => void }>());
  const virtualizerRef = useRef<Virtualizer<HTMLDivElement, HTMLTableRowElement> | null>(null);
  const pendingFocusKeyRef = useRef<DataTableRowKey | null>(null);
  const pendingFocusPlacementRef = useRef<"row" | "last">("row");
  const [pendingFocusKey, setPendingFocusKey] = useState<DataTableRowKey | null>(null);
  const focusedRowKeyRef = useRef<DataTableRowKey | null>(null);
  const [focusedRowKey, setFocusedRowKey] = useState<DataTableRowKey | null>(null);
  const anchorRef = useRef<{ key: DataTableRowKey; offset: number } | null>(null);
  const previousVisibleRef = useRef(visible);
  const previousScrollResetKeyRef = useRef<DataTableRowKey | null | undefined>(scrollResetKey);
  const initializedScrollResetRef = useRef(false);
  const estimateSizeOverride = virtualizationOptions?.estimateSize;
  const estimateSize = Math.max(1, estimateSizeOverride ?? densityEstimate);
  const overscan = Math.max(0, virtualizationOptions?.overscan ?? 5);
  const getRowKeyValue = useCallback(
    (row: T, index: number): DataTableRowKey => getRowKey?.(row, index) ?? index,
    [getRowKey]
  );
  const visibleRowKey = useCallback(
    (index: number) => getRowKeyValue(visible[index], offset + index),
    [getRowKeyValue, offset, visible]
  );
  const focusedIndex = useMemo(() => {
    if (focusedRowKey === null) return -1;
    return visible.findIndex((row, index) => getRowKeyValue(row, offset + index) === focusedRowKey);
  }, [focusedRowKey, getRowKeyValue, offset, visible]);
  const rangeExtractor = useCallback((range: Range) => {
    const indexes = defaultRangeExtractor(range);
    if (focusedIndex >= 0 && !indexes.includes(focusedIndex)) indexes.push(focusedIndex);
    return indexes.sort((left, right) => left - right);
  }, [focusedIndex]);
  const captureAnchor = useCallback((instance: Virtualizer<HTMLDivElement, HTMLTableRowElement>) => {
    if (!virtualizationEnabled) return;
    const element = instance.scrollElement;
    if (!element) return;
    const scrollOffset = instance.scrollOffset ?? element.scrollTop;
    const first = instance.getVirtualItems().find((item) => item.end > scrollOffset + headerHeight);
    if (!first || !visible[first.index]) return;
    anchorRef.current = {
      key: visibleRowKey(first.index),
      offset: scrollOffset - (first.start - headerHeight)
    };
  }, [headerHeight, virtualizationEnabled, visible, visibleRowKey]);
  const virtualizer = useVirtualizer<HTMLDivElement, HTMLTableRowElement>({
    count: virtualizationEnabled ? visible.length : 0,
    getScrollElement: () => scrollElement,
    estimateSize: () => estimateSize,
    overscan,
    enabled: virtualizationEnabled,
    scrollMargin: headerHeight,
    scrollPaddingStart: headerHeight,
    // Keep a user who is at the end of the fetched page at the end when the
    // first visible rows are corrected from the estimate to their measured
    // height. Without this, the page can grow by a few pixels after the
    // browser has already clamped scrollTop to the old max.
    anchorTo: "end",
    getItemKey: visibleRowKey,
    rangeExtractor,
    onChange: captureAnchor
  });
  virtualizerRef.current = virtualizer;
  const virtualItems = virtualizationEnabled && scrollElement ? virtualizer.getVirtualItems() : [];
  const renderedVirtually = virtualizationEnabled && scrollElement !== null;
  const initialVirtualItems = useMemo<VirtualItem[]>(() => {
    if (!virtualizationEnabled) return [];
    // The scroll element is assigned after the first client render. Keep that
    // first body bounded to the shared short-table viewport plus overscan;
    // TanStack Virtual takes over once its element observer has a rect.
    const initialCount = Math.min(visible.length, Math.ceil(344 / estimateSize) + overscan * 2);
    return Array.from({ length: initialCount }, (_, index) => ({
      index,
      key: visibleRowKey(index),
      start: headerHeight + index * estimateSize,
      size: estimateSize,
      end: headerHeight + (index + 1) * estimateSize,
      lane: 0
    }));
  }, [estimateSize, headerHeight, overscan, virtualizationEnabled, visible.length, visibleRowKey]);
  const hasVirtualRange = renderedVirtually && virtualItems.length > 0;
  const virtualBodyItems = hasVirtualRange ? virtualItems : initialVirtualItems;
  const setScrollElementRef = useCallback((element: HTMLDivElement | null) => {
    tableContainerRef.current = element;
    if (virtualizationEnabled) setScrollElement(element);
  }, [virtualizationEnabled]);
  const setTableHeaderRef = useCallback((element: HTMLTableSectionElement | null) => {
    setHeaderElement(virtualizationEnabled ? element : null);
  }, [virtualizationEnabled]);
  const registerRowRef = useCallback((key: DataTableRowKey, element: HTMLTableRowElement | null) => {
    if (element) {
      rowRefs.current.set(key, element);
    } else {
      rowRefs.current.delete(key);
    }
  }, []);
  const getRowRef = useCallback((key: DataTableRowKey) => {
    const cached = rowRefCallbacks.current.get(key);
    if (cached?.renderedVirtually === renderedVirtually) return cached.callback;
    const callback = (element: HTMLTableRowElement | null) => {
      registerRowRef(key, element);
      if (renderedVirtually) virtualizerRef.current?.measureElement(element);
    };
    rowRefCallbacks.current.set(key, { renderedVirtually, callback });
    return callback;
  }, [registerRowRef, renderedVirtually]);
  const updateContainerWidth = useCallback(() => {
    const element = tableContainerRef.current;
    if (!element) return;
    const nextWidth = normalizeTableContainerWidth(measureElementContentWidth(element));
    setContainerWidth((current) => (current === nextWidth ? current : nextWidth));
  }, []);
  const updateDensityEstimate = useCallback(() => {
    const element = tableContainerRef.current;
    if (!element || !virtualizationEnabled || estimateSizeOverride !== undefined) return;
    const tokenValue = Number.parseFloat(window.getComputedStyle(element).getPropertyValue("--monitoring-table-row-height"));
    if (Number.isFinite(tokenValue) && tokenValue > 0) {
      setDensityEstimate((current) => (current === tokenValue ? current : tokenValue));
    }
  }, [estimateSizeOverride, virtualizationEnabled]);
  const baseWidths = useMemo(
    () => calculateBaseWidths(columns, { ...autoWidths, ...renderedAutoWidths }, columnWidths),
    [columns, autoWidths, renderedAutoWidths, columnWidths]
  );
  const displayedWidths = useMemo(
    () => distributeTableWidth(columns, baseWidths, columnWidths, containerWidth),
    [columns, baseWidths, columnWidths, containerWidth]
  );
  const tableWidth = columns.reduce((sum, column) => {
    const key = String(column.key);
    return sum + (displayedWidths[key] ?? 0);
  }, 0);

  useIsomorphicLayoutEffect(() => {
    const element = tableContainerRef.current;
    if (!element || virtualizationEnabled || !columns.some(column => column.measureSelectors?.length)) {
      setRenderedAutoWidths(current => Object.keys(current).length ? {} : current);
      return;
    }
    const measure = () => {
      const next = calculateRenderedAutoFitWidths(element, columns);
      setRenderedAutoWidths(current => sameWidths(current, next) ? current : next);
    };
    measure();
    document.fonts?.addEventListener("loadingdone", measure);
    return () => document.fonts?.removeEventListener("loadingdone", measure);
  }, [columns, visible, timezoneName, className, virtualizationEnabled]);

  useEffect(() => {
    const element = tableContainerRef.current;
    if (!element) return;
    updateContainerWidth();
    updateDensityEstimate();
    const observer = new ResizeObserver(() => {
      updateContainerWidth();
      updateDensityEstimate();
    });
    observer.observe(element);
    return () => {
      observer.disconnect();
    };
  }, [scrollElement, updateContainerWidth, updateDensityEstimate, visible.length > 0]);

  const rowLayoutSignature = JSON.stringify([displayedWidths, estimateSize]);
  useEffect(() => {
    if (!virtualizationEnabled || !scrollElement) return;
    let frame: number | null = null;
    const remeasure = () => {
      if (frame !== null) return;
      frame = window.requestAnimationFrame(() => {
        frame = null;
        const instance = virtualizerRef.current;
        if (!instance) return;
        const anchor = anchorRef.current;
        const atEnd = scrollElement.scrollTop > 0 &&
          scrollElement.scrollHeight - scrollElement.clientHeight - scrollElement.scrollTop < 2;
        // Cached sizes for rows outside the viewport also belong to the old
        // column/font geometry. Clear them before measuring the mounted rows.
        instance.measure();
        for (const element of rowRefs.current.values()) instance.measureElement(element);
        if (atEnd) {
          instance.scrollToIndex(instance.options.count - 1, { align: "end" });
        } else if (anchor) {
          const index = instance.options.getItemKey;
          const anchorIndex = Array.from({ length: instance.options.count }, (_, item) => item)
            .find((item) => index(item) === anchor.key);
          if (anchorIndex !== undefined) {
            const target = instance.getOffsetForIndex(anchorIndex, "start")?.[0];
            if (target !== undefined) instance.scrollToOffset(Math.max(0, target + anchor.offset));
          }
        }
      });
    };
    remeasure();
    window.addEventListener("resize", remeasure);
    window.visualViewport?.addEventListener("resize", remeasure);
    document.fonts?.addEventListener("loadingdone", remeasure);
    return () => {
      if (frame !== null) window.cancelAnimationFrame(frame);
      window.removeEventListener("resize", remeasure);
      window.visualViewport?.removeEventListener("resize", remeasure);
      document.fonts?.removeEventListener("loadingdone", remeasure);
    };
  }, [rowLayoutSignature, scrollElement, virtualizationEnabled]);

  useEffect(() => {
    // A native vertical scrollbar changes clientWidth without necessarily
    // changing the observed border box. Recheck only on the empty/non-empty
    // transition; row scrolling itself must not trigger layout reads.
    updateContainerWidth();
  }, [updateContainerWidth, visible.length > 0, virtualizationEnabled]);

  useEffect(() => {
    if (!virtualizationEnabled || !headerElement) {
      setHeaderHeight((current) => (current === 0 ? current : 0));
      return;
    }
    const measure = () => {
      const nextHeight = Math.ceil(headerElement.getBoundingClientRect().height);
      setHeaderHeight((current) => (current === nextHeight ? current : nextHeight));
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(headerElement);
    return () => observer.disconnect();
  }, [headerElement, virtualizationEnabled]);

  useEffect(() => {
    if (!virtualizationEnabled) {
      rowRefs.current.clear();
      rowRefCallbacks.current.clear();
    }
  }, [virtualizationEnabled]);

  useIsomorphicLayoutEffect(() => {
    // A server page can replace all of the rows while the table component
    // stays mounted. React invokes the old ref callbacks as rows unmount, but
    // callbacks for rows that were never mounted (or that were retained by a
    // previous virtual range) would otherwise grow for every page/refresh.
    const activeKeys = new Set(
      visible.map((row, index) => getRowKeyValue(row, offset + index))
    );
    for (const key of rowRefs.current.keys()) {
      if (!activeKeys.has(key)) rowRefs.current.delete(key);
    }
    for (const key of rowRefCallbacks.current.keys()) {
      if (!activeKeys.has(key)) rowRefCallbacks.current.delete(key);
    }
  }, [getRowKeyValue, offset, visible]);

  useEffect(() => {
    focusedRowKeyRef.current = focusedRowKey;
    if (
      focusedRowKey !== null &&
      !visible.some((row, index) => getRowKeyValue(row, offset + index) === focusedRowKey)
    ) {
      setFocusedRowKey(null);
    }
  }, [focusedRowKey, getRowKeyValue, offset, visible]);

  useIsomorphicLayoutEffect(() => {
    const resetChanged = initializedScrollResetRef.current &&
      !Object.is(previousScrollResetKeyRef.current, scrollResetKey);
    if (resetChanged) {
      anchorRef.current = null;
      pendingFocusKeyRef.current = null;
      pendingFocusPlacementRef.current = "row";
      setPendingFocusKey(null);
      setFocusedRowKey(null);
      if (virtualizationEnabled) virtualizer.scrollToOffset(0, { behavior: "auto" });
    } else if (virtualizationEnabled && previousVisibleRef.current !== visible && anchorRef.current) {
      const anchor = anchorRef.current;
      const index = visible.findIndex((row, rowIndex) => getRowKeyValue(row, offset + rowIndex) === anchor.key);
      if (index >= 0) {
        const target = virtualizer.getOffsetForIndex(index, "start")?.[0];
        if (target !== undefined) {
          virtualizer.scrollToOffset(Math.max(0, target + anchor.offset), { behavior: "auto" });
        }
      }
    }
    previousVisibleRef.current = visible;
    previousScrollResetKeyRef.current = scrollResetKey;
    initializedScrollResetRef.current = true;
  }, [getRowKeyValue, offset, scrollResetKey, visible, virtualizationEnabled, virtualizer]);

  useIsomorphicLayoutEffect(() => {
    const key = pendingFocusKey;
    if (key === null || typeof window === "undefined") return;
    const frame = window.requestAnimationFrame(() => {
      const element = rowRefs.current.get(key);
      if (!element) return;
      const tabbables = pendingFocusPlacementRef.current === "last"
        ? getTableRowTabbables(element)
        : [];
      (tabbables[tabbables.length - 1] ?? element).focus();
      pendingFocusKeyRef.current = null;
      pendingFocusPlacementRef.current = "row";
      setPendingFocusKey(null);
    });
    return () => window.cancelAnimationFrame(frame);
  }, [pendingFocusKey, virtualItems]);

  useEffect(() => () => {
    resizeCleanupRef.current?.();
    resizeCleanupRef.current = null;
  }, []);

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

    resizeCleanupRef.current?.();

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
      if (resizeCleanupRef.current === cleanup) resizeCleanupRef.current = null;
    }

    function cleanup() {
      resizingColumnRef.current = null;
      document.body.classList.remove("is-resizing-column");
      window.removeEventListener("pointermove", handlePointerMove);
      window.removeEventListener("pointerup", handlePointerUp);
    }

    document.body.classList.add("is-resizing-column");
    window.addEventListener("pointermove", handlePointerMove);
    window.addEventListener("pointerup", handlePointerUp);
    resizeCleanupRef.current = cleanup;
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

  function handleRowFocus(key: DataTableRowKey) {
    if (!virtualizationEnabled) return;
    focusedRowKeyRef.current = key;
    setFocusedRowKey(key);
  }

  function handleRowKeyDown(event: ReactKeyboardEvent<HTMLTableRowElement>, row: T, index: number) {
    if (event.key === "Enter" || event.key === " ") {
      if (!onRowClick) return;
      if (event.target !== event.currentTarget) return;
      event.preventDefault();
      onRowClick(row);
      return;
    }
    if (event.key !== "Tab" || !onRowClick || !renderedVirtually) return;
    const target = event.target instanceof Element ? event.target : null;
    const tabbables = getTableRowTabbables(event.currentTarget);
    const targetTabbable = target?.closest<HTMLElement>(TABLE_ROW_FOCUSABLE_SELECTOR);
    const targetIndex = targetTabbable ? tabbables.indexOf(targetTabbable) : -1;
    if (target === event.currentTarget && !event.shiftKey && tabbables.length) return;
    if (event.shiftKey && targetIndex === 0) {
      // The row itself is the focus stop immediately before its first child.
      event.preventDefault();
      event.currentTarget.focus();
      return;
    }
    if (targetIndex >= 0 && !event.shiftKey && targetIndex < tabbables.length - 1) return;
    if (targetIndex >= 0 && event.shiftKey && targetIndex > 0) return;
    const nextIndex = index + (event.shiftKey ? -1 : 1);
    if (nextIndex < 0 || nextIndex >= visible.length) return;
    event.preventDefault();
    const nextKey = visibleRowKey(nextIndex);
    pendingFocusKeyRef.current = nextKey;
    pendingFocusPlacementRef.current = event.shiftKey ? "last" : "row";
    setPendingFocusKey(nextKey);
    handleRowFocus(nextKey);
    virtualizer.scrollToIndex(nextIndex, { align: "auto", behavior: "auto" });
  }

  function renderCell(row: T, column: TableColumn<T>) {
    const key = String(column.key);
    const width = displayedWidths[key];
    const widthStyle = width ? { width: `${width}px` } : undefined;
    const cellContent = column.render
      ? column.render(row)
      : compactNumbers && typeof row[key] === "number"
        ? <CompactNumberValue value={row[key] as number} compactStyle={compactNumberStyle} />
        : display(row, key, timezoneName);
    return (
      <td key={key} className={column.className} style={widthStyle}>
        {cellContent}
      </td>
    );
  }

  function renderDataRow(row: T, index: number, item?: Pick<VirtualItem, "key">) {
    const rowKey = visibleRowKey(index);
    const rowIndex = offset + index;
    return (
      <tr
        key={item?.key ?? rowKey}
        ref={virtualizationEnabled ? getRowRef(rowKey) : undefined}
        className={[onRowClick ? "clickable-row" : "", virtualizationEnabled ? "data-table-virtual-row" : "", rowClassName?.(row, index, visible) || ""].filter(Boolean).join(" ") || undefined}
        tabIndex={onRowClick ? 0 : undefined}
        aria-rowindex={virtualizationEnabled ? rowIndex + 2 : undefined}
        data-index={renderedVirtually ? index : undefined}
        data-row-index={virtualizationEnabled ? rowIndex : undefined}
        data-row-key={virtualizationEnabled ? String(rowKey) : undefined}
        onFocus={virtualizationEnabled ? () => handleRowFocus(rowKey) : undefined}
        onClick={onRowClick ? () => onRowClick(row) : undefined}
        onKeyDown={onRowClick ? (event) => handleRowKeyDown(event, row, index) : undefined}
      >
        {columns.map((column) => renderCell(row, column))}
      </tr>
    );
  }

  function renderSpacer(height: number, key: string) {
    if (height <= 0) return null;
    return (
      <tr
        key={key}
        className="data-table-virtual-spacer"
        aria-hidden="true"
        style={{ height: `${height}px` }}
      >
        <td colSpan={columns.length} aria-hidden="true" style={{ height: `${height}px` }} />
      </tr>
    );
  }

  const bodyRows = virtualizationEnabled
    ? (() => {
        const result: ReactNode[] = [];
        let previousEnd = 0;
        for (const item of virtualBodyItems) {
          const row = visible[item.index];
          if (!row) continue;
          const rowStart = item.start - headerHeight;
          result.push(renderSpacer(rowStart - previousEnd, `gap-${item.index}`));
          result.push(renderDataRow(row, item.index, item));
          previousEnd = item.end - headerHeight;
        }
        const totalSize = hasVirtualRange
          ? virtualizer.getTotalSize()
          : Math.max(virtualizer.getTotalSize(), visible.length * estimateSize);
        result.push(renderSpacer(totalSize - previousEnd, "bottom"));
        return result;
      })()
    : visible.map((row, index) => renderDataRow(row, index));

  if (!visible.length && !virtualizationEnabled) return <div className="table-empty">No records</div>;

  const tableClassName = `table-scroll short monitoring-data-table${virtualizationEnabled ? " is-virtualized" : ""}${className ? ` ${className}` : ""}`;

  return (
    <div ref={setScrollElementRef} className={tableClassName}>
      {visible.length ? (
        <table
          className={fixedLayout ? "data-table-fixed" : undefined}
          aria-rowcount={virtualizationEnabled ? sortedRows.length + 1 : undefined}
          style={tableWidth ? { width: `${tableWidth}px`, minWidth: `${tableWidth}px` } : undefined}
        >
          <colgroup>
            {columns.map((column) => {
              const key = String(column.key);
              const width = displayedWidths[key];
              return <col key={key} style={width ? { width: `${width}px` } : undefined} />;
            })}
          </colgroup>
          <thead ref={virtualizationEnabled ? setTableHeaderRef : undefined}>
            <tr aria-rowindex={virtualizationEnabled ? 1 : undefined}>
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
                        {helpEnabled ? <Help title={column.headerTitle}>{column.label}</Help> : <span title={column.headerTitle}>{column.label}</span>}
                        <span aria-hidden="true">{active ? (activeSort?.sortDir === "desc" ? "↓" : "↑") : "↕"}</span>
                      </button>
                    ) : (
                      helpEnabled ? <Help className="table-static-header" title={column.headerTitle}>{column.label}</Help> : <span className="table-static-header" title={column.headerTitle}>{column.label}</span>
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
          <tbody>{bodyRows}</tbody>
        </table>
      ) : (
        <div className="table-empty">No records</div>
      )}
    </div>
  );
}

export function StatusCell<T extends Record<string, unknown>>({ row, keyName = "status" }: { row: T; keyName?: string }) {
  return <StatusPill status={text(row, keyName)} />;
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

const TABLE_ROW_FOCUSABLE_SELECTOR = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "[contenteditable=\"true\"]",
  "[tabindex]:not([tabindex=\"-1\"]):not([disabled])"
].join(",");

function getTableRowTabbables(row: HTMLTableRowElement) {
  return Array.from(row.querySelectorAll<HTMLElement>(TABLE_ROW_FOCUSABLE_SELECTOR)).filter((element) => {
    if (element.getAttribute("aria-hidden") === "true") return false;
    const style = window.getComputedStyle(element);
    return style.display !== "none" && style.visibility !== "hidden";
  });
}

let tableMeasureContext: CanvasRenderingContext2D | null | undefined;

function normalizeTableContainerWidth(width: number) {
  if (!Number.isFinite(width) || width <= 0) return 0;
  // Column widths are whole pixels. Flooring a fractional content box keeps a
  // fitted table inside its panel instead of creating a sub-pixel scrollbar.
  return Math.floor(width);
}

function measureElementContentWidth(element: HTMLElement) {
  const styles = window.getComputedStyle(element);
  const horizontalBorder =
    Number.parseFloat(styles.borderLeftWidth || "0") +
    Number.parseFloat(styles.borderRightWidth || "0");
  const horizontalPadding =
    Number.parseFloat(styles.paddingLeft || "0") +
    Number.parseFloat(styles.paddingRight || "0");
  return calculateElementContentWidth(
    element.getBoundingClientRect().width,
    element.clientWidth,
    horizontalBorder,
    horizontalPadding
  );
}

function calculateElementContentWidth(
  borderBoxWidth: number,
  clientWidth: number,
  horizontalBorder = 0,
  horizontalPadding = 0
) {
  const fractionalPaddingBoxWidth = Math.max(0, borderBoxWidth - horizontalBorder);
  // clientWidth excludes a native vertical scrollbar, while the fractional
  // padding box prevents sub-pixel overflow when no scrollbar is present.
  return Math.max(0, Math.min(fractionalPaddingBoxWidth, clientWidth) - horizontalPadding);
}

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
    return false;
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
    .filter((column) => column.fillPriority === "last" || column.fillPriority === "normal" || isLongTextFillColumn(column))
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
  return /(?:^|[_\s-])(?:error|message|issue|detail|description|query|json|payload|sql|text)(?:$|[_\s-])/u.test(`${key} ${label}`);
}

function sameWidths(left: Record<string, number>, right: Record<string, number>) {
  return Object.keys(left).length === Object.keys(right).length
    && Object.entries(right).every(([key, width]) => left[key] === width);
}

function calculateRenderedAutoFitWidths<T extends Record<string, unknown>>(element: HTMLElement, columns: TableColumn<T>[]) {
  const context = getTableMeasureContext();
  if (!context) return {};
  const result: Record<string, number> = {};
  const styles = new WeakMap<Element, CSSStyleDeclaration>();
  const styleFor = (node: Element) => {
    const cached = styles.get(node);
    if (cached) return cached;
    const style = window.getComputedStyle(node);
    styles.set(node, style);
    return style;
  };
  const pixels = (value: string) => Number.parseFloat(value) || 0;
  const decoration = (node: Element, cell: Element) => {
    let width = 0;
    for (let current: Element | null = node; current; current = current.parentElement) {
      const style = styleFor(current);
      width += pixels(style.paddingLeft) + pixels(style.paddingRight)
        + pixels(style.borderLeftWidth) + pixels(style.borderRightWidth);
      if (current === cell) break;
      width += pixels(style.marginLeft) + pixels(style.marginRight);
    }
    return width;
  };
  const textWidth = (node: Element) => {
    const style = styleFor(node);
    const raw = node.textContent ?? "";
    const text = style.textTransform === "uppercase" ? raw.toUpperCase()
      : style.textTransform === "lowercase" ? raw.toLowerCase() : raw;
    context.font = `${style.fontStyle} ${style.fontWeight} ${style.fontSize} ${style.fontFamily}`;
    return context.measureText(text).width
      + Math.max(0, Array.from(text).length - 1) * pixels(style.letterSpacing);
  };
  const headers = element.querySelectorAll("thead tr:first-child th");
  const rows = Array.from(element.querySelectorAll("tbody tr:not(.data-table-virtual-spacer)")).slice(0, 250);
  context.save();
  try {
    columns.forEach((column, index) => {
      if (!column.measureSelectors?.length || column.autoFit === false || (column.width !== undefined && column.autoFit !== true)) return;
      const header = headers[index];
      const label = header?.querySelector(".table-sort-button > span:first-child, .table-static-header");
      if (!header || !label) return;
      const glyph = header.querySelector(".table-sort-button > span:last-child");
      const button = header.querySelector(".table-sort-button");
      let width = textWidth(label) + decoration(label, header)
        + (column.sortable && glyph ? textWidth(glyph) + pixels(button ? styleFor(button).columnGap : "0") : 0);
      // Reserve the resize handle and rounding independently of content font size.
      const headerWidth = width + 4;
      let complete = rows.length > 0;
      for (const row of rows) {
        const cell = row.children[index];
        if (!cell) { complete = false; break; }
        for (const selector of column.measureSelectors) {
          const line = cell.querySelector(selector);
          if (!line) { complete = false; break; }
          width = Math.max(width, textWidth(line) + decoration(line, cell));
        }
        if (!complete) break;
      }
      // Missing selectors leave this column on its existing string estimate.
      if (complete) result[String(column.key)] = Math.max(column.minWidth ?? 48,
        Math.min(column.maxWidth ?? column.width ?? 180, Math.ceil(Math.max(headerWidth, width + 2))));
    });
  } finally {
    context.restore();
  }
  return result;
}

function calculateAutoFitWidths<T extends Record<string, unknown>>(
  rows: T[],
  columns: TableColumn<T>[],
  timezoneName?: string | null,
  compactNumbers = false,
  compactNumberStyle: CompactNumberStyle = "suffix",
  measureRenderedCells = true
) {
  const autoColumns = columns.filter((column) => column.autoFit === true || (column.autoFit !== false && column.width === undefined));
  if (!autoColumns.length) return {};
  const context = getTableMeasureContext();
  const result: Record<string, number> = {};

  for (const column of autoColumns) {
    const key = String(column.key);
    const headerWidth = measureTextWidth(context, column.label) + (column.sortable ? 38 : 26);
    if (isLongTextFillColumn(column)) {
      const preferredWidth = column.width ?? column.minWidth ?? headerWidth;
      result[key] = Math.max(column.minWidth ?? 96, Math.min(column.maxWidth ?? Number.POSITIVE_INFINITY, preferredWidth));
      continue;
    }
    const sampleWidths = rows.slice(0, 250).map((row) => {
      const measuredValues = column.measureValue?.(row, timezoneName);
      const values = Array.isArray(measuredValues)
        ? measuredValues
        : [measuredValues ?? (measureRenderedCells && column.render
          ? renderedText(row, column)
          : autoFitDisplay(row, key, timezoneName, compactNumbers, compactNumberStyle))];
      return Math.max(...values.map((value) => measureTextWidth(context, value || "-") + 26));
    });
    const measured = Math.ceil(Math.max(headerWidth, ...sampleWidths, column.minWidth ?? 0));
    result[key] = Math.max(column.minWidth ?? 48, Math.min(column.maxWidth ?? column.width ?? 180, measured));
  }

  return result;
}

function autoFitDisplay(row: Record<string, unknown>, key: string, timezoneName: string | null | undefined, compactNumbers: boolean, compactNumberStyle: CompactNumberStyle) {
  const value = row[key];
  if (compactNumbers && typeof value === "number") return formatCompactNumber(value, compactNumberStyle);
  return display(row, key, timezoneName);
}

function renderedText<T extends Record<string, unknown>>(row: T, column: TableColumn<T>) {
  const rendered = column.render?.(row);
  if (rendered === null || rendered === undefined || rendered === false) return "-";
  if (typeof rendered === "string" || typeof rendered === "number") return String(rendered);
  const key = String(column.key);
  return display(row, key);
}

function sortRows<T extends Record<string, unknown>>(rows: T[], columns: TableColumn<T>[], sort?: TableSort) {
  if (!sort) return rows;
  const column = columns.find((item) => (item.sortKey ?? String(item.key)) === sort.sortBy);
  if (!column) return rows;
  const key = column.sortKey ?? String(column.key);
  const direction = sort.sortDir === "desc" ? -1 : 1;
  return [...rows].sort((left, right) => compareTableValues(tableSortValue(left, key), tableSortValue(right, key)) * direction);
}

function tableSortValue(row: Record<string, unknown>, key: string) {
  const value = row[key];
  if (value === null || value === undefined || value === "") return "";
  if (typeof value === "number") return value;
  const text = String(value);
  // Keep transported decimal strings as strings. Converting a count such as
  // 9007199254740993 through Number changes its sort position and loses the
  // value before the cell renderer can present it exactly.
  if (isNumericMetricText(text)) return text.trim();
  const time = Date.parse(text);
  if (Number.isFinite(time) && /\d{4}-\d{2}-\d{2}/.test(text)) return time;
  return text.toLowerCase();
}

function compareTableValues(left: string | number, right: string | number) {
  if (isNumericMetricValue(left) && isNumericMetricValue(right)) {
    return compareMetricValues(left as MetricValue, right as MetricValue);
  }
  return String(left).localeCompare(String(right), undefined, { numeric: true, sensitivity: "base" });
}

const NUMERIC_METRIC_TEXT = /^-?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/u;

function isNumericMetricText(value: string) {
  return NUMERIC_METRIC_TEXT.test(value.trim());
}

function isNumericMetricValue(value: string | number): boolean {
  return typeof value === "number" || isNumericMetricText(value);
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

export const monitoringTableTestUtils = {
  calculateElementContentWidth,
  calculateAutoFitWidths,
  distributeTableWidth,
  normalizeTableContainerWidth,
  sortRows
};
