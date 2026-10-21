import { assetFriendlyAlias, assetLocatorDetail, type AssetNamingInput } from "../../shared/assetNaming";
import { canonicalFormat } from "../../shared/formatPresentation";
import type { HelpContent } from "../../shared/components/helpContent";

export type DestinationPresentationOrigin = "captured" | "metadata" | "unknown";

export const DESTINATION_WORKLOAD_SCOPE = "Read is source rows consumed by runs writing to this destination; Written is reported write workload, not committed physical effects";

export type DestinationLayoutField = "format" | "load_type" | "partition_columns" | "partition_expressions";

export interface DestinationLayoutPresentation {
  format: string | null;
  load_type: string | null;
  partition_columns: string[] | null;
  partition_expressions?: string[] | null;
  observation_time: string | null;
  dataflow_run_id: string | null;
  origins: Partial<Record<DestinationLayoutField, DestinationPresentationOrigin>>;
  metadata_sources: Array<Record<string, unknown>>;
  field_metadata_sources: Partial<Record<DestinationLayoutField, Array<Record<string, unknown>>>>;
  metadata_revision: string | null;
  conflicts?: unknown;
  invalid_fields: string[];
  errors: Array<{ field: string | null; code: string | null }>;
}

export interface DestinationPresentation {
  id: string;
  name: string;
  connection: string;
  format: string;
  context: string;
  subtitle: string;
  locator: string;
  locatorLabel: "Table" | "Storage path" | "Endpoint" | "Location";
  path: string;
  fullIdentity: string;
  shortId: string;
}

/** Adapt already projected fields; never load Assets inventory or log JSON. */
export function presentDestination(row: Record<string, unknown>): DestinationPresentation {
  const id = text(row, "target");
  const path = text(row, "destination_path");
  const fullTable = text(row, "destination_full_table");
  const recordedTable = text(row, "destination_table");
  const qualified = splitQualifiedTable(fullTable);
  const table = unquoteIdentifier(recordedTable) || qualified.at(-1) || "";
  const namespace = qualified.slice(0, -1).join(".");
  const connection = text(row, "destination_name", "destination_connection_name") || "Unknown connection";
  const format = text(row, "destination_format", "format", "destination_connection_type") || "unknown";
  const api = text(row, "destination_connection_type").toLowerCase() === "api" || format.toLowerCase() === "api";
  const locator = fullTable || recordedTable || path || text(row, "target_display") || "Unknown destination";
  const locatorLabel = api ? "Endpoint" : fullTable || recordedTable ? "Table" : path ? "Storage path" : "Location";
  const asset: AssetNamingInput = {
    id, asset_type: api ? "api" : table ? "table" : path ? "path" : "unresolved",
    display_name: api ? path || locator : locator,
    full_identity: [connection, locator].join(" · "),
    table: api ? null : table,
    path,
    catalog: unquoteIdentifier(text(row, "destination_catalog")),
    database: unquoteIdentifier(text(row, "destination_database")),
    schema_name: unquoteIdentifier(text(row, "destination_schema")),
  };
  const name = assetFriendlyAlias(asset, locator, { qualifyTableWithSchema: true });
  // Full table already contains the namespace. Avoid repeating typed parts.
  if (namespace) { asset.catalog = null; asset.database = null; asset.schema_name = namespace; }
  const context = assetLocatorDetail(asset, name)
    || (path && table && !api ? assetLocatorDetail({ ...asset, asset_type: "path", table: null }, name) : "");
  const subtitle = [connection, context || format].filter(Boolean).join(" · ");
  return { id, name, connection, format, context, subtitle, locator, locatorLabel, path,
    fullIdentity: [connection, locator, path !== locator ? path : ""].filter(Boolean).join(" · "),
    shortId: compactDestinationId(id) };
}

/** Preserve every physical key when names/context coincide in the visible set. */
export function presentDestinationRows(rows: readonly Record<string, unknown>[]) {
  const values = rows.map(presentDestination);
  const names = new Map<string, Set<string>>();
  const subtitles = new Map<string, Set<string>>();
  for (const value of values) {
    const pair = JSON.stringify([value.name, value.subtitle]);
    const nameIds = names.get(value.name) ?? new Set<string>();
    nameIds.add(value.id); names.set(value.name, nameIds);
    const contextIds = subtitles.get(pair) ?? new Set<string>();
    contextIds.add(value.id); subtitles.set(pair, contextIds);
  }
  return values.map(value => {
    const sameContext = (subtitles.get(JSON.stringify([value.name, value.subtitle]))?.size ?? 0) > 1;
    const peers = subtitles.get(JSON.stringify([value.name, value.subtitle])) ?? new Set<string>();
    let shortId = value.shortId;
    if (sameContext) {
      let width = 6;
      while ([...peers].some(id => id !== value.id && compactDestinationId(id, width) === shortId)) {
        width += 2; shortId = compactDestinationId(value.id, width);
      }
    }
    const subtitle = sameContext ? `${shortId} · ${value.subtitle}` : value.subtitle;
    const chartLabel = (names.get(value.name)?.size ?? 0) > 1 ? `${value.name} · ${subtitle}` : value.name;
    return { ...value, shortId, subtitle, chartLabel };
  });
}

export function destinationTooltip(value: DestinationPresentation) {
  return [`Asset: ${value.name}`, `Connection: ${value.connection}`, `${value.locatorLabel}: ${value.locator}`,
    value.path && value.path !== value.locator ? `Storage path: ${value.path}` : "", `Format: ${value.format}`]
    .filter(Boolean).join("\n");
}

export function destinationHelpContent(value: DestinationPresentation): HelpContent {
  const locatorWrap = value.locatorLabel === "Storage path" ? "path" : value.locator.length > 36 ? "multiline" : undefined;
  return {
    title: `Asset: ${value.name}`,
    context: `Connection: ${value.connection}`,
    metrics: [
      { label: value.locatorLabel, value: value.locator, ...(locatorWrap ? { wrap: locatorWrap } : {}) },
      ...(value.path && value.path !== value.locator ? [{ label: "Storage path", value: value.path, wrap: "path" as const }] : []),
      { label: "Format", value: value.format },
    ],
  };
}

/**
 * Read the bounded layout projection attached by Monitoring/Assets. The flat
 * fields are retained as a captured-evidence fallback for older responses.
 * This helper deliberately never reads or displays destination configure.
 */
export function presentDestinationLayout(row: Record<string, unknown>): DestinationLayoutPresentation {
  const rawLayout = record(row.layout) ?? record(row.destination_layout);
  const hasLayoutField = (field: string) => Boolean(rawLayout && Object.prototype.hasOwnProperty.call(rawLayout, field));
  const rawValue = (field: string, ...fallbackKeys: string[]) => {
    if (hasLayoutField(field)) return rawLayout?.[field];
    for (const key of fallbackKeys) {
      if (Object.prototype.hasOwnProperty.call(row, key)) return row[key];
    }
    return null;
  };

  const rawFormat = rawValue("format", "destination_format", "format");
  const rawLoadType = rawValue("load_type", "destination_load_type");
  const rawPartitions = rawValue("partition_columns", "destination_partition_columns");
  const rawExpressions = rawValue("partition_expressions", "destination_partition_expressions");
  const format = textValue(rawFormat);
  const loadType = textValue(rawLoadType);
  const partitionColumns = parseStringList(rawPartitions);
  const partitionExpressions = rawExpressions === null || rawExpressions === undefined
    ? null
    : parseStringList(rawExpressions);
  const metadataSources = normalizeMetadataSources(rawValue("metadata_sources", "layout_metadata_sources"));
  const fieldMetadataSources = normalizeFieldMetadataSources(rawValue("field_metadata_sources", "layout_field_metadata_sources"));
  const invalidFields = parseStringList(rawValue("invalid_fields", "layout_invalid_fields")) ?? [];
  const errors = normalizeLayoutErrors(rawValue("errors", "layout_errors"));
  const rawOrigins = record(rawLayout?.origins) ?? record(row.layout_origins);
  const origins: Partial<Record<DestinationLayoutField, DestinationPresentationOrigin>> = {};
  for (const field of ["format", "load_type", "partition_columns", "partition_expressions"] as const) {
    const explicit = rawOrigins?.[field];
    if (isPresentationOrigin(explicit)) origins[field] = explicit;
    else if (fieldValuePresent(field, rawValue(field))) {
      origins[field] = rawLayout && metadataSources.length > 0 ? "metadata" : "captured";
    } else {
      origins[field] = "unknown";
    }
  }

  const observationTime = textValue(
    rawLayout?.observation_time ?? row.layout_observation_time ?? (!rawLayout ? row.latest_time : null),
  );
  const dataflowRunId = textValue(rawLayout?.dataflow_run_id ?? row.layout_dataflow_run_id ?? (!rawLayout ? row.dataflow_run_id : null));
  const metadataRevision = textValue(rawLayout?.metadata_revision ?? row.layout_metadata_revision);
  const conflicts = rawLayout && Object.prototype.hasOwnProperty.call(rawLayout, "conflicts")
    ? rawLayout.conflicts
    : row.layout_conflicts ?? null;

  return {
    format,
    load_type: loadType,
    partition_columns: partitionColumns,
    ...(partitionExpressions !== null ? { partition_expressions: partitionExpressions } : {}),
    observation_time: observationTime,
    dataflow_run_id: dataflowRunId,
    origins,
    metadata_sources: metadataSources,
    field_metadata_sources: fieldMetadataSources,
    metadata_revision: metadataRevision,
    conflicts,
    invalid_fields: invalidFields,
    errors,
  };
}

/**
 * Return bounded provenance for one layout field.  This is intentionally a
 * tooltip sized projection: source identifiers and revisions are useful for
 * explaining a metadata fallback, while the rest of a metadata record is not
 * part of the monitoring presentation contract.
 */
export function destinationLayoutFieldTooltip(
  layout: DestinationLayoutPresentation,
  field: DestinationLayoutField,
): string | null {
  const origin = layout.origins[field] ?? "unknown";
  const fieldSources = layout.field_metadata_sources[field] ?? [];
  const sources = fieldSources.length ? fieldSources : origin === "metadata" ? layout.metadata_sources : [];
  if (origin !== "metadata" && !fieldSources.length) return null;

  const lines = [`Origin: ${origin}`];
  const sourceLabels = sources.map(metadataSourceIdentity).filter((value): value is string => Boolean(value));
  if (sourceLabels.length) lines.push(`Metadata source: ${[...new Set(sourceLabels)].join(", ")}`);
  else if (origin === "metadata") lines.push("Metadata source: unavailable");

  const revisions = sources.map(metadataSourceRevision).filter((value): value is string => Boolean(value));
  if (layout.metadata_revision) revisions.push(layout.metadata_revision);
  const uniqueRevisions = [...new Set(revisions)];
  if (uniqueRevisions.length) lines.push(`Metadata revision: ${uniqueRevisions.join(", ")}`);
  return lines.join("\n");
}

export function destinationLayoutInvalidReason(layout: DestinationLayoutPresentation, field?: DestinationLayoutField): string | null {
  if (layout.errors.some(error => error.code === "invalid_metadata_value")) {
    const errors = layout.errors.filter(error => error.code === "invalid_metadata_value" || error.code === "invalid_captured_value");
    const relevant = errors.filter(error => !field || error.field === field);
    return relevant.length ? relevant.map(error => `Invalid ${error.code === "invalid_metadata_value" ? "published metadata" : "captured"} ${error.field?.replace(/_/gu, " ") || "layout"} configuration`).join("\n") : null;
  }
  const invalidFields = [
    ...layout.invalid_fields,
    ...layout.errors.filter(error => error.code === "invalid_captured_value" && error.field).map(error => error.field as string),
  ].map(value => value.toLowerCase().replace(/^destination_/u, ""));
  const matches = (name: string) => invalidFields.some(value => value === name || value.includes(name));
  if (field === "partition_columns" || field === "partition_expressions") {
    return matches("partition") ? "Invalid captured partition configuration" : null;
  }
  if (field === "format") return matches("format") ? "Invalid captured format" : null;
  if (field === "load_type") return matches("load") ? "Invalid captured load type" : null;
  if (matches("partition")) return "Invalid captured partition configuration";
  if (matches("format")) return "Invalid captured format";
  if (matches("load")) return "Invalid captured load type";
  return invalidFields.length || layout.errors.some(error => error.code === "invalid_captured_value")
    ? "Invalid captured layout value"
    : null;
}

/** Distinct known captured formats in the active filter, normalized for display. */
export function destinationFormatVariants(row: Record<string, unknown>): string[] {
  const raw = row.format_variants;
  const values = raw === undefined || raw === null
    ? (textValue(row.destination_format) ? [row.destination_format] : [])
    : parseStringList(raw) ?? [];
  const variants = new Map<string, string>();
  for (const value of values) {
    const normalized = normalizeFormat(value);
    if (normalized && !variants.has(normalized)) variants.set(normalized, normalized);
  }
  return [...variants.values()];
}

export function destinationFormatUnknownRuns(row: Record<string, unknown>): number | null {
  const value = row.format_unknown_runs;
  if (value === null || value === undefined || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : null;
}

export function destinationFormatLabel(value: unknown): string {
  const normalized = normalizeFormat(value);
  return normalized || "Unknown";
}

export function destinationLatestFormat(row: Record<string, unknown>): string | null {
  const layout = presentDestinationLayout(row);
  return normalizeFormat(layout.format) || null;
}

export type MaintenanceEvidenceState = "recorded" | "not_recorded" | "not_applicable" | "unknown";

export function maintenanceEvidenceState(row: Record<string, unknown>): MaintenanceEvidenceState {
  const explicit = textValue(row.maintenance_evidence_state)?.toLowerCase();
  if (explicit === "recorded" || explicit === "not_recorded" || explicit === "not_applicable" || explicit === "unknown") return explicit;

  const attempts = numericValue(row.maintenance_run_count);
  if (attempts !== null && attempts > 0) return "recorded";
  if (attempts === 0) return "not_recorded";
  return "unknown";
}

export function maintenanceEvidenceLabel(state: MaintenanceEvidenceState): string {
  if (state === "recorded") return "Recorded";
  if (state === "not_recorded") return "No maintenance recorded in filter";
  if (state === "not_applicable") return "N/A";
  return "Unknown";
}

export function isLakehouseFormat(value: unknown): boolean {
  const normalized = normalizeFormat(value);
  return normalized === "delta" || normalized === "iceberg";
}

function compactDestinationId(id: string, width = 6) {
  const value = id.replace(/^asset:/u, "");
  return value.length > width * 2 ? `${value.slice(0, width)}…${value.slice(-width)}` : value;
}

function text(row: Record<string, unknown>, ...keys: string[]) {
  for (const key of keys) {
    const value = row[key];
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return "";
}

function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null;
}

function textValue(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const result = value.trim();
  return result || null;
}

function numericValue(value: unknown): number | null {
  if (typeof value !== "number" && typeof value !== "string") return null;
  if (typeof value === "string" && !value.trim()) return null;
  const result = Number(value);
  return Number.isFinite(result) ? result : null;
}

function normalizeFormat(value: unknown): string {
  return canonicalFormat(value);
}

function isPresentationOrigin(value: unknown): value is DestinationPresentationOrigin {
  return value === "captured" || value === "metadata" || value === "unknown";
}

function fieldValuePresent(field: DestinationLayoutField, value: unknown) {
  if (field === "partition_columns" || field === "partition_expressions") return Array.isArray(value) ? true : textValue(value) !== null;
  return textValue(value) !== null;
}

function parseStringList(value: unknown): string[] | null {
  if (Array.isArray(value)) return value.filter(item => typeof item === "string").map(item => item.trim()).filter(Boolean);
  if (typeof value !== "string" || !value.trim()) return null;
  const trimmed = value.trim();
  if (trimmed.startsWith("[") && trimmed.endsWith("]")) {
    try {
      const parsed: unknown = JSON.parse(trimmed);
      if (Array.isArray(parsed)) return parsed.filter(item => typeof item === "string").map(item => item.trim()).filter(Boolean);
    } catch {
      return null;
    }
  }
  return [trimmed];
}

function normalizeMetadataSources(value: unknown): Array<Record<string, unknown>> {
  const values = Array.isArray(value) ? value : value === null || value === undefined ? [] : [value];
  return values.flatMap(item => {
    if (typeof item === "string" && item.trim()) return [{ source: item.trim() }];
    if (typeof item === "number" && Number.isFinite(item)) return [{ source_id: item }];
    const source = record(item);
    if (!source) return [];
    const result: Record<string, unknown> = {};
    for (const key of ["source_id", "source", "source_uri", "uri", "revision", "metadata_revision"]) {
      const normalized = metadataSourceValue(source[key]);
      if (normalized !== null) result[key] = normalized;
    }
    return Object.keys(result).length ? [result] : [];
  });
}

function normalizeFieldMetadataSources(value: unknown): Partial<Record<DestinationLayoutField, Array<Record<string, unknown>>>> {
  const sourceMap = record(value);
  if (!sourceMap) return {};
  const result: Partial<Record<DestinationLayoutField, Array<Record<string, unknown>>>> = {};
  for (const field of ["format", "load_type", "partition_columns", "partition_expressions"] as const) {
    const sources = normalizeMetadataSources(sourceMap[field]);
    if (sources.length) result[field] = sources;
  }
  return result;
}

function metadataSourceIdentity(source: Record<string, unknown>): string | null {
  return metadataSourceText(source.source_id)
    ?? metadataSourceText(source.source)
    ?? metadataSourceText(source.source_uri)
    ?? metadataSourceText(source.uri)
    ?? null;
}

function metadataSourceRevision(source: Record<string, unknown>): string | null {
  return metadataSourceText(source.revision) ?? metadataSourceText(source.metadata_revision) ?? null;
}

function metadataSourceText(value: unknown): string | null {
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  if (typeof value !== "string") return null;
  const result = value.trim();
  return result ? result.slice(0, 160) : null;
}

function metadataSourceValue(value: unknown): string | number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value !== "string") return null;
  const result = value.trim();
  return result ? result.slice(0, 160) : null;
}

function normalizeLayoutErrors(value: unknown): Array<{ field: string | null; code: string | null }> {
  if (!Array.isArray(value)) return [];
  return value.flatMap(item => {
    if (!item || typeof item !== "object" || Array.isArray(item)) return [];
    const error = item as Record<string, unknown>;
    const field = textValue(error.field);
    const code = textValue(error.code);
    return field || code ? [{ field, code }] : [];
  });
}

// Read SQL quoting without splitting a dot inside a quoted object name.
function splitQualifiedTable(value: string) {
  const parts: string[] = [];
  let current = "", quote = "";
  for (let index = 0; index < value.length; index += 1) {
    const char = value[index];
    if (quote) {
      current += char;
      if (char === quote) {
        if (value[index + 1] === quote) current += value[++index];
        else quote = "";
      }
    } else if (char === '`' || char === '"' || char === '[') {
      quote = char === '[' ? ']' : char; current += char;
    } else if (char === '.') {
      parts.push(unquoteIdentifier(current)); current = "";
    } else current += char;
  }
  if (quote) return []; // Keep malformed declarations in the exact locator.
  if (current.trim()) parts.push(unquoteIdentifier(current));
  return parts;
}

function unquoteIdentifier(value: string) {
  const trimmed = value.trim();
  const opener = trimmed[0], closer = opener === '[' ? ']' : opener;
  if (!['`', '"', '['].includes(opener) || trimmed.length < 2 || !trimmed.endsWith(closer)) return trimmed;
  return trimmed.slice(1, -1).split(closer + closer).join(closer);
}
