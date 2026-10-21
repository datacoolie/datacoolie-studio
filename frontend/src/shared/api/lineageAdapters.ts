import type { LineageResponseTransport } from "./contractTypes";
import type {
  AssetManualMappingInfo,
  LineageAsset,
  LineageReference,
  LineageResponse,
} from "./domainTypes";

type LineageAssetTransport = LineageResponseTransport["assets"][number];
type LineageReferenceTransport = LineageResponseTransport["references"][number];

/**
 * Convert the generated lineage graph transport into the graph view model.
 * The view model owns collection defaults and the small mapping projection used by drawers.
 */
export function toLineageResponse(
  response: LineageResponseTransport,
): LineageResponse {
  return {
    schema_version: response.schema_version,
    summary: response.summary,
    assets: response.assets.map(toLineageAsset),
    references: response.references.map(toLineageReference),
    dataflows: response.dataflows,
    dependencies: response.dependencies,
  };
}

function toLineageAsset(asset: LineageAssetTransport): LineageAsset {
  return {
    ...asset,
    display_label: stringValue(asset.display_label),
    endpoint_locator: stringValue(asset.endpoint_locator),
    endpoint_kind: stringValue(asset.endpoint_kind),
    identity_type: stringValue(asset.identity_type),
    metadata_source_ids: asset.metadata_source_ids ?? [],
    roles: asset.roles ?? [],
    mapping_target: asset.mapping_target
      ? {
          kind: asset.mapping_target.kind ?? "",
          value: asset.mapping_target.value ?? "",
          display: asset.mapping_target.display ?? "",
        }
      : null,
    observations: [],
    identifiers: [],
  };
}

function toLineageReference(reference: LineageReferenceTransport): LineageReference {
  return {
    ...reference,
    resolved_asset_ids: reference.resolved_asset_ids ?? [],
    candidate_asset_ids: reference.candidate_asset_ids ?? [],
    occurrence_count: reference.occurrence_count ?? 0,
    consumer_asset_ids: reference.consumer_asset_ids ?? [],
    provenances: reference.provenances ?? [],
    manual_mapping: toManualMapping(reference.manual_mapping),
  };
}

function toManualMapping(
  mapping: LineageReferenceTransport["manual_mapping"],
): AssetManualMappingInfo | null {
  if (!mapping || typeof mapping.mapping_id !== "number") return null;
  return {
    mapping_id: mapping.mapping_id,
    status: stringValue(mapping.status),
    note: nullableString(mapping.note),
    target_identifier_kind: nullableString(mapping.target_identifier_kind),
    target_normalized_value: nullableString(mapping.target_normalized_value),
  };
}

function stringValue(value: unknown): string | undefined {
  return typeof value === "string" ? value : undefined;
}

function nullableString(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}
