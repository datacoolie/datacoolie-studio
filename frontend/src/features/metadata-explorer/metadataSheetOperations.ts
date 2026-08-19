import type { MetadataEditorDocument, MetadataEditorSheet } from "../../shared/api/domainTypes";
import { normalizeStage, stageFilterMatches } from "../../shared/stagePresentation";
import type { SheetKey, SheetRow } from "./metadataSheetTypes";

export const STUDIO_METADATA_SOURCE_FIELDS = [
  "__metadata_source_id",
  "__metadata_source_name",
  "__metadata_source_uri",
  "__metadata_source_kind"
] as const;

export function filterMetadataRows(
  sheetKey: SheetKey,
  rows: Array<Record<string, unknown>>,
  columns: Array<{ key: string }>,
  query: string,
  stageFilter?: string | null
): SheetRow[] {
  const needle = query.trim().toLocaleLowerCase();
  const normalizedStageFilter = normalizeStage(stageFilter);
  return rows
    .map<SheetRow>((row, index) => ({ ...row, __rowId: `${sheetKey}-${index}`, __rowIndex: index }))
    .filter((row) => !normalizedStageFilter || stageFilterMatches(row.stage, normalizedStageFilter))
    .filter((row) => !needle || columns.some((column) => metadataCellMatches(row[column.key], needle)));
}

export function metadataSourceIdentity(row: Record<string, unknown>) {
  const sourceId = Number(row.__metadata_source_id);
  if (Number.isFinite(sourceId) && sourceId > 0) return `id:${sourceId}`;
  const uri = formatCellValue(row.__metadata_source_uri).trim().toLowerCase();
  if (uri) return `uri:${uri}`;
  return `name:${formatCellValue(row.__metadata_source_name).trim().toLowerCase()}`;
}

export function metadataSourceGroupStartIds(rows: SheetRow[]) {
  const starts = new Set<string>();
  let previousSource = "";
  rows.forEach((row, index) => {
    const source = metadataSourceIdentity(row);
    if (index === 0 || source !== previousSource) starts.add(row.__rowId);
    previousSource = source;
  });
  return starts;
}

export function sameMetadataSortBucket(sheetKey: SheetKey, left: Record<string, unknown>, right: Record<string, unknown>) {
  if (metadataSourceIdentity(left) !== metadataSourceIdentity(right)) return false;
  if (sheetKey === "connections") return true;
  if (sheetKey === "dataflows") return normalizeStage(left.stage) === normalizeStage(right.stage);
  return normalizeText(left.connection_name) === normalizeText(right.connection_name)
    && normalizeText(left.schema_name) === normalizeText(right.schema_name)
    && normalizeText(left.table_name) === normalizeText(right.table_name)
    && ordinalIdentity(left.ordinal_position) === ordinalIdentity(right.ordinal_position);
}

export function canMoveMetadataRow(sheetKey: SheetKey, rows: Array<Record<string, unknown>>, rowIndex: number | null, offset: -1 | 1) {
  if (rowIndex == null) return false;
  const targetIndex = rowIndex + offset;
  if (rowIndex < 0 || rowIndex >= rows.length || targetIndex < 0 || targetIndex >= rows.length) return false;
  return sameMetadataSortBucket(sheetKey, rows[rowIndex], rows[targetIndex]);
}

export function mergeFilteredRows(sourceRows: Array<Record<string, unknown>>, filteredRows: SheetRow[]) {
  const nextRows = [...sourceRows];
  for (const filteredRow of filteredRows) {
    const { __rowId, __rowIndex, ...cleanRow } = filteredRow;
    if (__rowIndex >= 0 && __rowIndex < nextRows.length) {
      nextRows[__rowIndex] = cleanRow;
    }
  }
  return nextRows;
}

export function metadataCellMatches(value: unknown, query: string) {
  const needle = query.trim().toLocaleLowerCase();
  return Boolean(needle) && searchableCellValue(value).toLocaleLowerCase().includes(needle);
}

function searchableCellValue(value: unknown) {
  if (value == null) return "";
  if (Array.isArray(value) || typeof value === "object") {
    try {
      return JSON.stringify(value);
    } catch {
      return String(value);
    }
  }
  return String(value);
}

function normalizeText(value: unknown) {
  return formatCellValue(value).trim().toLowerCase();
}

function ordinalIdentity(value: unknown) {
  const text = formatCellValue(value).trim();
  if (!text) return "last";
  const numeric = Number(text);
  return Number.isFinite(numeric) ? `number:${numeric}` : "last";
}

export function cleanRuntimeRows(rows: SheetRow[]) {
  return rows.map(({ __rowId, __rowIndex, ...row }) => row);
}

export function createEmptyRow(columns: Array<{ key: string }>, routing = studioRoutingValues(null)) {
  return hydrateStudioRouting(
    Object.fromEntries(columns.map((column) => [column.key, column.key === "is_active" ? true : null])),
    routing
  );
}

export function insertAt<T>(items: T[], index: number, item: T) {
  return [...items.slice(0, index), item, ...items.slice(index)];
}

export function moveAt<T>(items: T[], fromIndex: number, toIndex: number) {
  const next = [...items];
  const [item] = next.splice(fromIndex, 1);
  next.splice(toIndex, 0, item);
  return next;
}

export function rowFromValues(columns: Array<{ key: string }>, values: string[], routing = studioRoutingValues(null)) {
  return hydrateStudioRouting(
    Object.fromEntries(
      columns.map((column, index) => [
        column.key,
        isStudioMetadataSourceField(column.key) ? null : parseCellText(values[index] ?? "")
      ])
    ),
    routing
  );
}

export function parseDelimitedRow(text: string) {
  return text.replace(/\r?\n$/, "").split("\t");
}

export function normalizeFieldName(value: string | null) {
  return value?.trim().replace(/\s+/g, "_") ?? "";
}

export async function writeClipboard(value: string) {
  if (!navigator.clipboard?.writeText) return;
  await navigator.clipboard.writeText(value);
}

export async function readClipboard() {
  if (!navigator.clipboard?.readText) return "";
  return navigator.clipboard.readText();
}

export function parseCellText(value: string) {
  const normalized = value.trim();
  if (!normalized) return null;
  if (normalized.toLowerCase() === "true") return true;
  if (normalized.toLowerCase() === "false") return false;
  return value;
}

export function formatCellValue(value: unknown) {
  if (value == null) return "";
  if (Array.isArray(value) || typeof value === "object") return JSON.stringify(value);
  return String(value);
}

const structuredArrayFields = new Set([
  "additional_columns",
  "deduplicate_columns",
  "latest_data_columns",
  "merge_keys",
  "partition_columns",
  "schema_hints",
  "source_watermark_columns",
  "transform_drop_columns",
  "transform_hash_columns",
  "transform_masking_rules",
  "transform_select_columns",
  "transform_value_rules"
]);

const structuredObjectFields = new Set([
  "transform_rename_columns"
]);

const structuredSqlFields = new Set([
  "source_query",
  "source_filter_expression",
  "transform_filter_expression"
]);

export type StructuredCellKind = "array" | "object" | "sql";

export function structuredCellKind(columnKey: string, value: unknown): StructuredCellKind | null {
  if (structuredSqlFields.has(columnKey)) return "sql";
  const parsed = parseStructuredValue(value);
  if (parsed) return Array.isArray(parsed) ? "array" : "object";
  if (structuredArrayFields.has(columnKey)) return "array";
  if (structuredObjectFields.has(columnKey)) return "object";
  if (columnKey === "configure" || columnKey.endsWith("_configure")) return "object";
  return null;
}

export function parseStructuredValue(value: unknown): Record<string, unknown> | unknown[] | null {
  if (Array.isArray(value)) return value;
  if (value && typeof value === "object") return value as Record<string, unknown>;
  if (typeof value !== "string" || !value.trim()) return null;
  try {
    const parsed: unknown = JSON.parse(value);
    return Array.isArray(parsed) || (parsed !== null && typeof parsed === "object")
      ? parsed as Record<string, unknown> | unknown[]
      : null;
  } catch {
    return null;
  }
}

export function formatPrettyStructuredValue(value: unknown) {
  const parsed = parseStructuredValue(value);
  return parsed ? JSON.stringify(parsed, null, 2) : "";
}

export function connectionNameOptions(document: MetadataEditorDocument | null) {
  const rows = document?.sheets.connections?.rows ?? [];
  return rows.map((row) => formatCellValue(row.name).trim()).filter(Boolean);
}

const connectionReferenceFields = new Set(["connection_name", "source_connection_name", "destination_connection_name"]);

export function synchronizeConnectionNameReferences(document: MetadataEditorDocument, nextConnections: MetadataEditorSheet) {
  const currentConnections = document.sheets.connections?.rows ?? [];
  const renames = connectionNameRenames(currentConnections, nextConnections.rows);
  const sheets: MetadataEditorDocument["sheets"] = {
    ...document.sheets,
    connections: nextConnections
  };
  if (!renames.size) return { ...document, sheets };

  for (const sheetKey of ["dataflows", "schema_hints"] as const) {
    const sheet = sheets[sheetKey];
    if (!sheet) continue;
    sheets[sheetKey] = {
      ...sheet,
      rows: sheet.rows.map((row) => replaceConnectionNameReferences(row, renames))
    };
  }
  return { ...document, sheets };
}

function connectionNameRenames(currentRows: Array<Record<string, unknown>>, nextRows: Array<Record<string, unknown>>) {
  const currentByIdentity = new Map<string, string>();
  currentRows.forEach((row, index) => {
    const name = normalizeConnectionName(row.name);
    if (name) currentByIdentity.set(connectionRowIdentity(row, index), name);
  });

  const renames = new Map<string, string>();
  nextRows.forEach((row, index) => {
    const previousName = currentByIdentity.get(connectionRowIdentity(row, index));
    const nextName = normalizeConnectionName(row.name);
    if (previousName && nextName && previousName !== nextName) renames.set(previousName, nextName);
  });
  return renames;
}

function connectionRowIdentity(row: Record<string, unknown>, index: number) {
  const connectionId = formatCellValue(row.connection_id).trim();
  return connectionId ? `id:${connectionId}` : `index:${index}`;
}

function normalizeConnectionName(value: unknown) {
  return formatCellValue(value).trim();
}

function replaceConnectionNameReferences(row: Record<string, unknown>, renames: Map<string, string>) {
  let nextRow: Record<string, unknown> | null = null;
  for (const [key, value] of Object.entries(row)) {
    if (!connectionReferenceFields.has(key)) continue;
    const replacement = renames.get(normalizeConnectionName(value));
    if (!replacement) continue;
    nextRow ??= { ...row };
    nextRow[key] = replacement;
  }
  return nextRow ?? row;
}

export function studioRoutingValues(document: MetadataEditorDocument | null) {
  const firstSource = environmentMetadataSourceOptions(document)[0];
  if (document?.source.scope === "environment" && firstSource) {
    return {
      __metadata_source_id: firstSource.source_id,
      __metadata_source_name: firstSource.name,
      __metadata_source_uri: firstSource.uri,
      __metadata_source_kind: "metadata"
    };
  }
  return {
    __metadata_source_id: document?.source.source_id ?? null,
    __metadata_source_name: document?.source.name ?? sourceNameFromUri(document?.source.uri ?? ""),
    __metadata_source_uri: document?.source.uri ?? "",
    __metadata_source_kind: "metadata"
  };
}

export interface MetadataSourceOption {
  source_id: number;
  name: string;
  uri: string;
  record_counts: Partial<Record<SheetKey, number>>;
}

export function environmentMetadataSourceOptions(document: MetadataEditorDocument | null): MetadataSourceOption[] {
  const sources = document?.source.revision?.sources;
  if (!Array.isArray(sources)) return [];
  const countsBySource = metadataRowCountsBySource(document);
  return sources
    .map((item) => {
      if (!item || typeof item !== "object") return null;
      const source = item as Record<string, unknown>;
      const sourceId = Number(source.source_id);
      const uri = formatCellValue(source.uri);
      const name = formatCellValue(source.name) || sourceNameFromUri(uri);
      if (!Number.isFinite(sourceId) || sourceId <= 0 || !name) return null;
      return {
        source_id: sourceId,
        name,
        uri,
        record_counts: {
          ...metadataRecordCounts(source.record_counts),
          ...(countsBySource.get(sourceId) ?? {})
        }
      };
    })
    .filter((item): item is MetadataSourceOption => Boolean(item));
}

export function environmentMetadataSourceOptionsForSheet(document: MetadataEditorDocument | null, sheetKey: SheetKey): MetadataSourceOption[] {
  return environmentMetadataSourceOptions(document).filter((option) => metadataSourceMatchesSheet(option, sheetKey));
}

export function hydrateStudioRouting<T extends Record<string, unknown>>(row: T, routing: Record<string, unknown>) {
  return {
    ...row,
    ...Object.fromEntries(STUDIO_METADATA_SOURCE_FIELDS.map((field) => [field, routing[field] ?? null]))
  };
}

export function isStudioMetadataSourceField(key: string) {
  return STUDIO_METADATA_SOURCE_FIELDS.some((field) => field === key);
}

function sourceNameFromUri(uri: string) {
  if (!uri) return "";
  return uri.replace(/\\/g, "/").split("/").filter(Boolean).pop() ?? uri;
}

function metadataRowCountsBySource(document: MetadataEditorDocument | null) {
  const result = new Map<number, Partial<Record<SheetKey, number>>>();
  for (const sheetKey of ["connections", "dataflows", "schema_hints"] satisfies SheetKey[]) {
    for (const row of document?.sheets[sheetKey]?.rows ?? []) {
      const sourceId = Number(row.__metadata_source_id);
      if (!Number.isFinite(sourceId) || sourceId <= 0) continue;
      const counts = result.get(sourceId) ?? {};
      counts[sheetKey] = (counts[sheetKey] ?? 0) + 1;
      result.set(sourceId, counts);
    }
  }
  return result;
}

function metadataRecordCounts(value: unknown): Partial<Record<SheetKey, number>> {
  if (!value || typeof value !== "object") return {};
  const record = value as Record<string, unknown>;
  return {
    connections: numericRecordCount(record.connections),
    dataflows: numericRecordCount(record.dataflows),
    schema_hints: numericRecordCount(record.schema_hints)
  };
}

function numericRecordCount(value: unknown) {
  const count = Number(value);
  return Number.isFinite(count) && count > 0 ? count : undefined;
}

function metadataSourceMatchesSheet(option: MetadataSourceOption, sheetKey: SheetKey) {
  const counts = option.record_counts ?? {};
  const positiveSheets = (Object.keys(counts) as SheetKey[]).filter((key) => (counts[key] ?? 0) > 0);
  if (positiveSheets.length) {
    return (counts[sheetKey] ?? 0) > 0;
  }

  const normalized = `${option.name}/${option.uri}`.replace(/\\/g, "/").toLowerCase();
  if (/(^|\/)metadata(\.|\/|$)/.test(normalized)) return true;
  if (sheetKey === "connections") return /(^|\/)connections?(\.|\/|_|-|$)/.test(normalized);
  if (sheetKey === "dataflows") return /(^|\/)dataflows?(\.|\/|_|-|$)/.test(normalized);
  return /(^|\/)schema[_-]?hints?(\.|\/|_|-|$)/.test(normalized);
}
