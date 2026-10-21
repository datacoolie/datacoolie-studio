import type {
  AssetReferenceDetailResponseTransport,
  AssetReferenceListResponseTransport,
  ReferenceOccurrenceSourceResponseTransport,
} from "./contractTypes";
import {
  toAssetAttention,
  toAssetBrief,
  toAssetMappingTarget,
  toReferenceResolution,
} from "./assetAdapters";
import {
  asRecord,
  nullableNumber,
  nullableString,
  numberValue,
  optionalNumber,
  optionalString,
  recordArray,
  stringArray,
  stringValue,
} from "./transportValues";
import type {
  AssetManualMappingInfo,
  AssetReferenceDetailResponse,
  AssetReferenceFilterOptions,
  AssetReferenceGroupItem,
  AssetReferenceListResponse,
  AssetReferenceOccurrenceItem,
  ReferenceOccurrenceSourceResponse,
  ReferenceSourceDiagnostic,
  ReferenceSourceMatch,
  ReferenceSourceView,
  ReferenceType,
  SourceLocation,
} from "./domainTypes";

const REFERENCE_TYPES = [
  "table_reference",
  "path_reference",
  "api_endpoint_reference",
  "unknown",
] as const;
const SOURCE_VIEW_IDS = ["query_source", "consumer_source", "evaluated_sql"] as const;
const SOURCE_LANGUAGES = ["sql", "python"] as const;
export function toAssetReferenceListResponse(
  response: AssetReferenceListResponseTransport,
): AssetReferenceListResponse {
  return {
    items: response.items.map(toAssetReferenceGroup),
    filter_options: toAssetReferenceFilterOptions(response.filter_options),
    catalog_version: response.catalog_version,
  };
}

export function toAssetReferenceDetailResponse(
  response: AssetReferenceDetailResponseTransport,
): AssetReferenceDetailResponse {
  return {
    reference: toAssetReferenceGroup(response.reference),
    occurrences: recordArray(response.occurrences).map(toAssetReferenceOccurrence),
    catalog_version: response.catalog_version,
  };
}

export function toReferenceOccurrenceSourceResponse(
  response: ReferenceOccurrenceSourceResponseTransport,
): ReferenceOccurrenceSourceResponse {
  return {
    occurrence_id: response.occurrence_id,
    consumer_asset_id: response.consumer_asset_id,
    views: recordArray(response.views)
      .map(toReferenceSourceView)
      .filter((view): view is ReferenceSourceView => view !== null),
    diagnostics: recordArray(response.diagnostics).map(toReferenceSourceDiagnostic),
  };
}

export function toAssetReferenceGroup(value: unknown): AssetReferenceGroupItem {
  const reference = asRecord(value);
  return {
    id: stringValue(reference.id),
    reference_type: toReferenceType(reference.reference_type),
    normalized_value: stringValue(reference.normalized_value),
    display_name: stringValue(reference.display_name, stringValue(reference.normalized_value)),
    resolution: toReferenceResolution(reference.resolution),
    resolved_asset_id: nullableString(reference.resolved_asset_id),
    resolved_asset_ids: stringArray(reference.resolved_asset_ids),
    resolved_asset: toOptionalAsset(reference.resolved_asset),
    candidate_asset_ids: stringArray(reference.candidate_asset_ids),
    candidate_assets: toAssetBriefs(reference.candidate_assets),
    occurrence_ids: stringArray(reference.occurrence_ids),
    consumer_asset_ids: stringArray(reference.consumer_asset_ids),
    consumer_assets: toAssetBriefs(reference.consumer_assets),
    provenances: stringArray(reference.provenances),
    resolution_methods: reference.resolution_methods === undefined
      ? undefined
      : stringArray(reference.resolution_methods),
    occurrence_count: optionalNumber(reference.occurrence_count),
    dependency_count: numberValue(reference.dependency_count),
    dataflow_ids: stringArray(reference.dataflow_ids),
    attention_count: numberValue(reference.attention_count),
    attention_items: recordArray(reference.attention_items).map(toAssetAttention),
    observations: recordArray(reference.observations),
    manual_mapping: toManualMapping(reference.manual_mapping),
  };
}

export function toAssetReferenceOccurrence(value: unknown): AssetReferenceOccurrenceItem {
  const occurrence = asRecord(value);
  return {
    id: stringValue(occurrence.id),
    reference_id: stringValue(occurrence.reference_id),
    reference_type: toReferenceType(occurrence.reference_type),
    raw_value: stringValue(occurrence.raw_value),
    normalized_value: stringValue(occurrence.normalized_value),
    context_scope: nullableString(occurrence.context_scope),
    context_scope_source: toContextScopeSource(occurrence.context_scope_source),
    addressing_mode: nullableString(occurrence.addressing_mode),
    qualification_level: nullableString(occurrence.qualification_level),
    source_location: toSourceLocation(occurrence.source_location),
    display_name: stringValue(occurrence.display_name, stringValue(occurrence.raw_value)),
    provenance: nullableString(occurrence.provenance),
    consumer_asset_id: nullableString(occurrence.consumer_asset_id),
    consumer_asset: toOptionalAsset(occurrence.consumer_asset),
    connection_name: nullableString(occurrence.connection_name),
    resolution: toReferenceResolution(occurrence.resolution),
    resolution_method: nullableString(occurrence.resolution_method),
    resolved_asset_id: nullableString(occurrence.resolved_asset_id),
    resolved_asset: toOptionalAsset(occurrence.resolved_asset),
    candidate_asset_ids: stringArray(occurrence.candidate_asset_ids),
    candidate_assets: toAssetBriefs(occurrence.candidate_assets),
    dependency_count: numberValue(occurrence.dependency_count),
    dataflow_ids: stringArray(occurrence.dataflow_ids),
    attention_count: numberValue(occurrence.attention_count),
    attention_items: recordArray(occurrence.attention_items).map(toAssetAttention),
    observations: recordArray(occurrence.observations),
    manual_mapping: toManualMapping(occurrence.manual_mapping),
  };
}

function toAssetReferenceFilterOptions(value: unknown): AssetReferenceFilterOptions {
  const options = asRecord(value);
  return {
    connections: options.connections === undefined ? undefined : stringArray(options.connections),
    reference_types: stringArray(options.reference_types),
    provenances: stringArray(options.provenances),
    resolution_states: options.resolution_states === undefined
      ? undefined
      : stringArray(options.resolution_states),
    resolution_methods: options.resolution_methods === undefined
      ? undefined
      : stringArray(options.resolution_methods),
    attention_states: stringArray(options.attention_states),
  };
}

function toAssetBriefs(value: unknown) {
  return recordArray(value)
    .map(toAssetBrief)
    .filter((asset): asset is NonNullable<ReturnType<typeof toAssetBrief>> => asset !== null);
}

function toManualMapping(value: unknown): AssetManualMappingInfo | null {
  if (value === undefined || value === null) return null;
  const mapping = asRecord(value);
  const mappingId = optionalNumber(mapping.mapping_id);
  if (mappingId === undefined) return null;
  const target = toAssetMappingTarget(mapping.target);
  return {
    mapping_id: mappingId,
    status: optionalString(mapping.status),
    note: nullableString(mapping.note),
    target_identifier_kind: nullableString(mapping.target_identifier_kind) ?? target?.kind ?? null,
    target_normalized_value: nullableString(mapping.target_normalized_value) ?? target?.value ?? null,
  };
}

function toOptionalAsset(value: unknown) {
  if (value === undefined) return undefined;
  if (value === null) return null;
  return toAssetBrief(value);
}

function toSourceLocation(value: unknown): SourceLocation | null {
  if (value === undefined || value === null) return null;
  const location = asRecord(value);
  return {
    module: nullableString(location.module),
    path: nullableString(location.path),
    function_path: nullableString(location.function_path),
    line: nullableNumber(location.line),
    column: nullableNumber(location.column),
    end_line: nullableNumber(location.end_line),
    end_column: nullableNumber(location.end_column),
    coordinate_space: location.coordinate_space === "query_source" || location.coordinate_space === "function_source"
      ? location.coordinate_space
      : null,
  };
}

function toReferenceSourceView(value: unknown): ReferenceSourceView | null {
  const view = asRecord(value);
  if (typeof view.id !== "string" || typeof view.label !== "string" || typeof view.content !== "string") {
    return null;
  }
  return {
    id: SOURCE_VIEW_IDS.includes(view.id as typeof SOURCE_VIEW_IDS[number])
      ? view.id as ReferenceSourceView["id"]
      : "query_source",
    label: view.label,
    language: SOURCE_LANGUAGES.includes(view.language as typeof SOURCE_LANGUAGES[number])
      ? view.language as ReferenceSourceView["language"]
      : "sql",
    content: view.content,
    path: nullableString(view.path),
    function_path: nullableString(view.function_path),
    module_name: nullableString(view.module_name),
    matches: recordArray(view.matches).map(toReferenceSourceMatch),
  };
}

function toReferenceSourceMatch(value: unknown): ReferenceSourceMatch {
  const match = asRecord(value);
  const precisions = ["exact_reference", "detection_expression", "location_only"] as const;
  return {
    line: numberValue(match.line),
    column: numberValue(match.column),
    end_line: numberValue(match.end_line),
    end_column: numberValue(match.end_column),
    precision: precisions.includes(match.precision as typeof precisions[number])
      ? match.precision as ReferenceSourceMatch["precision"]
      : "location_only",
  };
}

function toReferenceSourceDiagnostic(value: unknown): ReferenceSourceDiagnostic {
  const diagnostic = asRecord(value);
  return {
    severity: diagnostic.severity === "warning" || diagnostic.severity === "error"
      ? diagnostic.severity
      : "info",
    code: stringValue(diagnostic.code),
    message: stringValue(diagnostic.message),
  };
}

function toReferenceType(value: unknown): ReferenceType {
  return REFERENCE_TYPES.includes(value as typeof REFERENCE_TYPES[number])
    ? value as ReferenceType
    : "unknown";
}

function toContextScopeSource(value: unknown): "detected" | "metadata_context" | null {
  return value === "detected" || value === "metadata_context" ? value : null;
}
