import type { SheetKey } from "./metadataSheetTypes";

export type MetadataColumnFilterMap = Record<string, readonly string[]>;

export type MetadataColumnFiltersBySheet = Record<SheetKey, MetadataColumnFilterMap>;

export interface MetadataFilterOption {
  key: string;
  label: string;
  count: number;
  isBlank: boolean;
}

export interface MetadataFilterOptions {
  options: MetadataFilterOption[];
  totalCount: number;
}

export const METADATA_FILTER_OPTION_RENDER_LIMIT = 300;
export const METADATA_FILTER_BLANK_KEY = "metadata-filter:blank";

export function createEmptyMetadataColumnFilters(): MetadataColumnFiltersBySheet {
  return {
    connections: {},
    dataflows: {},
    schema_hints: {}
  };
}

export function metadataColumnFilterMatches(
  row: Record<string, unknown>,
  filters: MetadataColumnFilterMap,
  excludedColumnKey?: string
) {
  return Object.entries(filters).every(([columnKey, selectedKeys]) => {
    if (columnKey === excludedColumnKey) return true;
    if (!selectedKeys.length) return false;
    return selectedKeys.includes(metadataColumnFilterValueKey(row[columnKey], columnKey));
  });
}

export function metadataColumnFilterCount(filters: MetadataColumnFilterMap) {
  return Object.keys(filters).length;
}

export function metadataColumnFilterValueKey(value: unknown, columnKey?: string) {
  if (isBlankFilterValue(value)) return METADATA_FILTER_BLANK_KEY;
  const displayValue = formatMetadataFilterValue(value);
  const canonicalValue = columnKey === "stage" ? displayValue.trim().toLocaleLowerCase() : displayValue;
  const valueType = Array.isArray(value) ? "array" : typeof value === "object" ? "object" : typeof value;
  return `metadata-filter:value:${valueType}:${canonicalValue}`;
}

export function metadataColumnFilterValueLabel(value: unknown, columnKey?: string) {
  if (isBlankFilterValue(value)) return "Blanks";
  if (typeof value === "boolean") return value ? "True" : "False";
  const label = formatMetadataFilterValue(value);
  return columnKey === "stage" ? label.trim() : label;
}

export function metadataColumnFilterOptions(rows: Array<Record<string, unknown>>, columnKey: string): MetadataFilterOptions {
  const optionsByKey = new Map<string, MetadataFilterOption>();
  for (const row of rows) {
    const value = row[columnKey];
    const key = metadataColumnFilterValueKey(value, columnKey);
    const current = optionsByKey.get(key);
    if (current) {
      current.count += 1;
      continue;
    }
    optionsByKey.set(key, {
      key,
      label: metadataColumnFilterValueLabel(value, columnKey),
      count: 1,
      isBlank: key === METADATA_FILTER_BLANK_KEY
    });
  }

  const options = [...optionsByKey.values()].sort((left, right) => {
    if (left.isBlank !== right.isBlank) return left.isBlank ? -1 : 1;
    return left.label.localeCompare(right.label, undefined, { numeric: true, sensitivity: "base" });
  });
  return { options, totalCount: options.length };
}

export function metadataColumnFilterOptionKeys(options: MetadataFilterOption[]) {
  return options.map((option) => option.key);
}

function isBlankFilterValue(value: unknown) {
  return value == null || formatMetadataFilterValue(value).trim() === "";
}

function formatMetadataFilterValue(value: unknown) {
  if (value == null) return "";
  if (Array.isArray(value) || typeof value === "object") return stableStringify(value);
  return String(value);
}

function stableStringify(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map((item) => stableStringify(item)).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value as Record<string, unknown>).sort().map((key) => `${JSON.stringify(key)}:${stableStringify((value as Record<string, unknown>)[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

export function metadataFilterOptionsForRender(options: MetadataFilterOption[], search: string) {
  const needle = search.trim().toLocaleLowerCase();
  const matchingOptions = needle
    ? options.filter((option) => option.label.toLocaleLowerCase().includes(needle))
    : options;
  return {
    matchingOptions,
    visibleOptions: matchingOptions.slice(0, METADATA_FILTER_OPTION_RENDER_LIMIT),
    truncated: matchingOptions.length > METADATA_FILTER_OPTION_RENDER_LIMIT
  };
}

export function metadataColumnFilterSelectionIsComplete(selectedKeys: readonly string[], options: MetadataFilterOption[]) {
  if (!options.length) return false;
  const selected = new Set(selectedKeys);
  return options.every((option) => selected.has(option.key));
}

export function metadataColumnFilterMapWithoutKey(filters: MetadataColumnFilterMap, columnKey: string): MetadataColumnFilterMap {
  if (!(columnKey in filters)) return filters;
  const next = { ...filters };
  delete next[columnKey];
  return next;
}

export function metadataColumnFilterStateForSheet(
  filtersBySheet: MetadataColumnFiltersBySheet,
  sheetKey: SheetKey,
  columnKey: string
) {
  return filtersBySheet[sheetKey][columnKey] ?? null;
}
