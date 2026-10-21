import type {
  AssetDetailResponseTransport,
  AssetInventoryResponseTransport,
  AssetSourceResponseTransport,
} from "./contractTypes";
import {
  asRecord,
  nullableNumber,
  nullableString,
  numberArray,
  numberValue,
  optionalNumber,
  optionalRecord,
  optionalString,
  recordArray,
  stringArray,
  stringValue,
} from "./transportValues";
import type {
  AssetAttention,
  AssetBrief,
  AssetDefinitionDiagnostic,
  AssetDefinitionResponse,
  AssetDependencyReference,
  AssetDependsOnItem,
  AssetDetailResponse,
  AssetDirectRelationships,
  AssetFlow,
  AssetInventoryItem,
  AssetInventoryResponse,
  AssetMetadataSource,
  AssetNeighbor,
  AssetSourceResponse,
  AssetSummary,
  AssetUsedByItem,
  ReferenceResolution,
  TargetIdentifierKind,
} from "./domainTypes";

const ASSET_TYPES = ["table", "path", "sql_query", "python_function", "api", "unresolved"] as const;
const REFERENCE_STATES = ["automatic", "manual", "unresolved"] as const;
const REFERENCE_REASONS = [
  "no_match",
  "multiple_matches",
  "out_of_scope",
  "conflicting_targets",
  "incomplete",
  "target_missing",
] as const;

export function toAssetInventoryResponse(
  response: AssetInventoryResponseTransport,
): AssetInventoryResponse {
  return {
    summary: toAssetSummary(response.summary),
    items: response.items.map(toAssetInventoryItem),
    filter_options: toAssetFilterOptions(response.filter_options),
    catalog_version: response.catalog_version,
  };
}

export function toAssetDetailResponse(
  response: AssetDetailResponseTransport,
): AssetDetailResponse {
  return {
    asset: toAssetInventoryItem(response.asset),
    definition: response.definition === undefined
      ? undefined
      : response.definition === null
        ? null
        : toAssetDefinition(response.definition),
    attention_items: recordArray(response.attention_items).map(toAssetAttention),
    direct_relationships: toAssetDirectRelationships(response.direct_relationships),
    upstream_assets: recordArray(response.upstream_assets)
      .map(toAssetNeighbor)
      .filter((item): item is AssetNeighbor => item !== null),
    downstream_assets: recordArray(response.downstream_assets)
      .map(toAssetNeighbor)
      .filter((item): item is AssetNeighbor => item !== null),
    input_flows: recordArray(response.input_flows)
      .map(toAssetFlow)
      .filter((item): item is AssetFlow => item !== null),
    output_flows: recordArray(response.output_flows)
      .map(toAssetFlow)
      .filter((item): item is AssetFlow => item !== null),
    depends_on: recordArray(response.depends_on)
      .map(toAssetDependsOnItem)
      .filter((item): item is AssetDependsOnItem => item !== null),
    used_by: recordArray(response.used_by)
      .map(toAssetUsedByItem)
      .filter((item): item is AssetUsedByItem => item !== null),
  };
}

export function toAssetSourceResponse(
  response: AssetSourceResponseTransport,
): AssetSourceResponse {
  return {
    definition: toAssetDefinition(response.definition),
    catalog_version: response.catalog_version,
  };
}

export function toAssetInventoryItem(value: unknown): AssetInventoryItem {
  const item = asRecord(value);
  return {
    id: stringValue(item.id),
    display_name: stringValue(item.display_name, stringValue(item.id)),
    friendly_name: stringValue(item.friendly_name, stringValue(item.display_name, stringValue(item.id))),
    full_identity: stringValue(item.full_identity, stringValue(item.display_name, stringValue(item.id))),
    asset_type: toAssetType(item.asset_type),
    format: nullableString(item.format),
    connection_name: nullableString(item.connection_name),
    connection_type: nullableString(item.connection_type),
    catalog: nullableString(item.catalog),
    database: nullableString(item.database),
    schema_name: nullableString(item.schema_name),
    table: nullableString(item.table),
    path: nullableString(item.path),
    query: nullableString(item.query),
    python_function: nullableString(item.python_function),
    roles: stringArray(item.roles),
    metadata_source_ids: numberArray(item.metadata_source_ids),
    metadata_sources: item.metadata_sources === undefined
      ? undefined
      : recordArray(item.metadata_sources).map(toAssetMetadataSource),
    metadata_source_count: numberValue(item.metadata_source_count),
    upstream_count: numberValue(item.upstream_count),
    downstream_count: numberValue(item.downstream_count),
    input_dataflow_count: numberValue(item.input_dataflow_count),
    output_dataflow_count: numberValue(item.output_dataflow_count),
    depends_on_count: numberValue(item.depends_on_count),
    used_by_count: numberValue(item.used_by_count),
    attention_count: numberValue(item.attention_count),
    attention_items: item.attention_items === undefined
      ? undefined
      : recordArray(item.attention_items).map(toAssetAttention),
    identifiers: item.identifiers === undefined ? undefined : recordArray(item.identifiers),
    observations: item.observations === undefined ? undefined : recordArray(item.observations),
    identifier_count: optionalNumber(item.identifier_count),
    observation_count: optionalNumber(item.observation_count),
    mapping_target: toAssetMappingTarget(item.mapping_target),
  };
}

export function toAssetBrief(value: unknown): AssetBrief | null {
  const asset = asRecord(value);
  const id = optionalString(asset.id);
  if (!id) return null;
  const displayName = stringValue(asset.display_name, stringValue(asset.friendly_name, id));
  return {
    id,
    display_name: displayName,
    friendly_name: stringValue(asset.friendly_name, displayName),
    full_identity: optionalString(asset.full_identity),
    asset_type: stringValue(asset.asset_type, "unresolved"),
    connection_name: nullableString(asset.connection_name),
    format: nullableString(asset.format),
    attention_count: numberValue(asset.attention_count),
  };
}

export function toAssetAttention(value: unknown): AssetAttention {
  const attention = asRecord(value);
  return {
    severity: attention.severity === "warning" || attention.severity === "error"
      ? attention.severity
      : "info",
    code: stringValue(attention.code),
    message: stringValue(attention.message),
    source_type: stringValue(attention.source_type),
    subject_type: stringValue(attention.subject_type, "asset"),
    dataflow_id: nullableString(attention.dataflow_id),
    metadata_source_id: nullableNumber(attention.metadata_source_id),
    reference_id: nullableString(attention.reference_id),
    reference_occurrence_id: nullableString(attention.reference_occurrence_id),
    details: optionalRecord(attention.details) ?? {},
  };
}

export function toAssetMappingTarget(
  value: unknown,
): { kind: TargetIdentifierKind | string; value: string; display: string } | null {
  if (value === undefined || value === null) return null;
  const target = asRecord(value);
  if (Object.keys(target).length === 0) return null;
  return {
    kind: stringValue(target.kind),
    value: stringValue(target.value),
    display: stringValue(target.display, stringValue(target.value)),
  };
}

export function toReferenceResolution(value: unknown): ReferenceResolution {
  const resolution = asRecord(value);
  return {
    state: typeof resolution.state === "string" && REFERENCE_STATES.includes(resolution.state as typeof REFERENCE_STATES[number])
      ? resolution.state as ReferenceResolution["state"]
      : "unresolved",
    reason: resolution.reason === null || resolution.reason === undefined
      ? null
      : typeof resolution.reason === "string" && REFERENCE_REASONS.includes(resolution.reason as typeof REFERENCE_REASONS[number])
        ? resolution.reason as NonNullable<ReferenceResolution["reason"]>
        : null,
  };
}

export function toAssetMetadataSource(value: unknown): AssetMetadataSource {
  const source = asRecord(value);
  return { id: numberValue(source.id), uri: stringValue(source.uri) };
}

function toAssetSummary(value: unknown): AssetSummary {
  const summary = asRecord(value);
  return {
    assets: numberValue(summary.assets),
    references: numberValue(summary.references),
    manual_mappings: numberValue(summary.manual_mappings),
    visible: numberValue(summary.visible),
    asset_attention: numberValue(summary.asset_attention),
    with_attention: numberValue(summary.with_attention),
    automatic_references: numberValue(summary.automatic_references),
    manual_references: numberValue(summary.manual_references),
    unresolved_references: numberValue(summary.unresolved_references),
  };
}

function toAssetFilterOptions(value: unknown) {
  const options = asRecord(value);
  return {
    connections: stringArray(options.connections),
    formats: stringArray(options.formats),
    asset_types: stringArray(options.asset_types),
    roles: stringArray(options.roles),
    attention_states: stringArray(options.attention_states),
  };
}

function toAssetDefinition(value: unknown): AssetDefinitionResponse {
  const definition = asRecord(value);
  return {
    ...definition,
    kind: stringValue(definition.kind, "unresolved"),
    language: nullableString(definition.language),
    status: stringValue(definition.status, "unavailable"),
    title: nullableString(definition.title),
    raw: nullableString(definition.raw),
    formatted: nullableString(definition.formatted),
    source: nullableString(definition.source),
    function_path: nullableString(definition.function_path),
    module_name: nullableString(definition.module_name),
    relative_path: nullableString(definition.relative_path),
    line_count: optionalNumber(definition.line_count),
    diagnostics: recordArray(definition.diagnostics).map(toAssetDefinitionDiagnostic),
    matches: definition.matches === undefined ? undefined : recordArray(definition.matches),
  };
}

function toAssetDefinitionDiagnostic(value: unknown): AssetDefinitionDiagnostic {
  const diagnostic = asRecord(value);
  return {
    severity: stringValue(diagnostic.severity, "info"),
    code: stringValue(diagnostic.code),
    message: stringValue(diagnostic.message),
    details: optionalRecord(diagnostic.details),
  };
}

function toAssetDirectRelationships(value: unknown): AssetDirectRelationships {
  const relationships = asRecord(value);
  return {
    upstream_assets: numberValue(relationships.upstream_assets),
    downstream_assets: numberValue(relationships.downstream_assets),
    input_flows: numberValue(relationships.input_flows),
    output_flows: numberValue(relationships.output_flows),
    depends_on_count: numberValue(relationships.depends_on_count),
    depends_on_total: optionalNumber(relationships.depends_on_total),
    depends_on_mapped_count: optionalNumber(relationships.depends_on_mapped_count),
    depends_on_unmapped_count: optionalNumber(relationships.depends_on_unmapped_count),
    depends_on_asset_count: optionalNumber(relationships.depends_on_asset_count),
    depends_on_reference_count: optionalNumber(relationships.depends_on_reference_count),
    used_by_count: numberValue(relationships.used_by_count),
    used_by_total: optionalNumber(relationships.used_by_total),
    position: stringValue(relationships.position, "isolated"),
  };
}

function toAssetNeighbor(value: unknown): AssetNeighbor | null {
  const neighbor = asRecord(value);
  const asset = toAssetBrief(neighbor.asset);
  if (!asset) return null;
  return {
    asset,
    relation_flow_count: numberValue(neighbor.relation_flow_count),
    relation_dependency_count: numberValue(neighbor.relation_dependency_count),
    relation_kinds: stringArray(neighbor.relation_kinds),
  };
}

function toAssetFlow(value: unknown): AssetFlow | null {
  const flow = asRecord(value);
  const counterpart = toAssetBrief(flow.counterpart);
  if (!counterpart) return null;
  return {
    id: stringValue(flow.id),
    dataflow_id: stringValue(flow.dataflow_id),
    name: stringValue(flow.name),
    stage: nullableString(flow.stage),
    load_type: nullableString(flow.load_type),
    metadata_source_id: nullableNumber(flow.metadata_source_id),
    metadata_source_uri: nullableString(flow.metadata_source_uri),
    source_asset_id: nullableString(flow.source_asset_id),
    destination_asset_id: nullableString(flow.destination_asset_id),
    counterpart,
  };
}

function toAssetDependsOnItem(value: unknown): AssetDependsOnItem | null {
  const item = asRecord(value);
  const resolvedAsset = item.resolved_asset === undefined
    ? undefined
    : item.resolved_asset === null
      ? null
      : toAssetBrief(item.resolved_asset);
  return {
    id: stringValue(item.id),
    kind: stringValue(item.kind),
    provenance: stringValue(item.provenance),
    resolution: toReferenceResolution(item.resolution),
    resolution_method: stringValue(item.resolution_method),
    reference_id: nullableString(item.reference_id),
    resolved_asset_id: nullableString(item.resolved_asset_id),
    resolved_asset: resolvedAsset,
    source_reference: toAssetDependencyReference(item.source_reference),
  };
}

function toAssetUsedByItem(value: unknown): AssetUsedByItem | null {
  const item = asRecord(value);
  const targetAsset = toAssetBrief(item.target_asset);
  if (!targetAsset) return null;
  return {
    id: stringValue(item.id),
    kind: stringValue(item.kind),
    provenance: stringValue(item.provenance),
    resolution: toReferenceResolution(item.resolution),
    resolution_method: stringValue(item.resolution_method),
    target_asset: targetAsset,
    reference: toAssetDependencyReference(item.reference),
  };
}

function toAssetDependencyReference(value: unknown): AssetDependencyReference | null {
  if (value === undefined || value === null) return null;
  const reference = asRecord(value);
  return {
    id: stringValue(reference.id),
    display_name: stringValue(reference.display_name, stringValue(reference.raw_value)),
    reference_type: stringValue(reference.reference_type),
    resolution: toReferenceResolution(reference.resolution),
    raw_value: nullableString(reference.raw_value),
    provenance: nullableString(reference.provenance),
  };
}

function toAssetType(value: unknown): AssetInventoryItem["asset_type"] {
  return typeof value === "string" && ASSET_TYPES.includes(value as typeof ASSET_TYPES[number])
    ? value as AssetInventoryItem["asset_type"]
    : "unresolved";
}
