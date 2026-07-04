import type { AssetInventoryItem, LineageAsset } from "../../shared/api/types";
import { lineageNodeSearchValues, presentLineageAsset } from "../lineage/model/presentation";

export function presentAsset(asset: AssetInventoryItem) {
  const lineagePresentation = presentLineageAsset(asLineageAsset(asset));
  return {
    ...lineagePresentation,
    friendlyName: asset.friendly_name || lineagePresentation.locator,
    fullIdentity: asset.full_identity || lineagePresentation.fullIdentity,
  };
}

export function assetSearchValues(asset: AssetInventoryItem): string[] {
  const lineageAsset = asLineageAsset(asset);
  const qualifiedTable = [asset.catalog, asset.database, asset.schema_name, asset.table].filter(Boolean).join(".");
  const values = [
    ...lineageNodeSearchValues(lineageAsset),
    asset.friendly_name,
    asset.full_identity,
    qualifiedTable,
    asset.connection_type,
    asset.declaration_status,
    ...asset.roles,
    ...asset.metadata_sources.map((source) => source.uri),
  ];
  return Array.from(new Set(values.filter((value): value is string => Boolean(value))));
}

export function metadataQueryForAsset(asset: AssetInventoryItem): string {
  return asset.table
    || pathBasename(asset.path)
    || asset.connection_name
    || asset.friendly_name
    || asset.display_name
    || asset.id;
}

function asLineageAsset(asset: AssetInventoryItem): LineageAsset {
  return {
    id: asset.id,
    label: asset.display_name,
    kind: asset.kind,
    display_name: asset.display_name,
    declaration_status: asset.declaration_status,
    connection_name: asset.connection_name ?? undefined,
    connection_type: asset.connection_type ?? undefined,
    format: asset.format ?? undefined,
    catalog: asset.catalog ?? undefined,
    database: asset.database ?? undefined,
    schema_name: asset.schema_name ?? undefined,
    path: asset.path ?? undefined,
    table: asset.table ?? undefined,
    query: asset.query ?? undefined,
    python_function: asset.python_function ?? undefined,
    metadata_source_ids: asset.metadata_source_ids,
    roles: asset.roles,
    observations: asset.observations,
    identifiers: asset.identifiers,
  };
}

function pathBasename(path: string | null | undefined) {
  if (!path) return null;
  const normalized = path.replace(/\\/g, "/").replace(/\/+$/u, "");
  if (!normalized) return null;
  const parts = normalized.split("/").filter(Boolean);
  return parts.at(-1) || normalized;
}
