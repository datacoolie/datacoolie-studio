import type { AssetInventoryItem, AssetReferenceGroupItem, ProjectReferenceMapping, ReferenceType, TargetIdentifierKind } from "../../shared/api/types";

export interface ReferenceMappingPayload {
  reference_type: ReferenceType;
  reference_value: string;
  target_identifier_kind: TargetIdentifierKind;
  target_value: string;
  target_display_value?: string | null;
  note?: string | null;
}

export interface ReferenceMappingTarget {
  id: string;
  assetId: string;
  assetType: AssetInventoryItem["asset_type"];
  format: string | null;
  connectionName: string;
  displayName: string;
  context: string | null;
  kind: TargetIdentifierKind;
  value: string;
  display: string;
}

export interface ReferenceMappingTargetIdentifier {
  kind: TargetIdentifierKind;
  value: string;
  display: string;
}

export type ReferenceMappingAction = "map" | "edit";

export function referenceMappingAction(
  reference: AssetReferenceGroupItem,
  mappings: ProjectReferenceMapping[] = [],
): ReferenceMappingAction {
  if (findReferenceMapping(reference, mappings) || reference.manual_mapping?.mapping_id) return "edit";
  return "map";
}

export function referenceMappingActionLabel(action: ReferenceMappingAction) {
  if (action === "map") return "Map";
  if (action === "edit") return "Edit";
  return "Map";
}

export function findReferenceMapping(
  reference: Pick<AssetReferenceGroupItem, "reference_type" | "normalized_value">,
  mappings: ProjectReferenceMapping[],
) {
  return mappings.find((mapping) => (
    mapping.reference_type === reference.reference_type
    && mapping.reference_normalized_value === reference.normalized_value
  )) ?? null;
}

export function buildReferenceMappingPayload(
  reference: Pick<AssetReferenceGroupItem, "reference_type" | "normalized_value">,
  target: ReferenceMappingTarget,
  note?: string | null,
): ReferenceMappingPayload {
  return {
    reference_type: reference.reference_type,
    reference_value: reference.normalized_value,
    target_identifier_kind: target.kind,
    target_value: target.value,
    target_display_value: target.display,
    note: note?.trim() || null,
  };
}

export function buildReferenceMappingTargets(assets: AssetInventoryItem[]): ReferenceMappingTarget[] {
  const targets = new Map<string, ReferenceMappingTarget>();
  for (const asset of assets) {
    const identifier = referenceMappingTargetIdentifier(asset);
    if (!identifier) continue;
    const id = referenceMappingTargetKey(identifier.kind, identifier.value);
    if (targets.has(id)) continue;
    const displayName = asset.friendly_name || asset.display_name || identifier.display;
    targets.set(id, {
      id,
      assetId: asset.id,
      assetType: asset.asset_type,
      format: asset.format || null,
      connectionName: asset.connection_name || "unknown connection",
      displayName,
      context: mappingTargetContext(asset, identifier.display, displayName),
      kind: identifier.kind,
      value: identifier.value,
      display: identifier.display,
    });
  }
  return [...targets.values()].sort((left, right) => left.displayName.localeCompare(right.displayName));
}

export function orderReferenceMappingTargets(
  reference: AssetReferenceGroupItem,
  targets: ReferenceMappingTarget[],
) {
  const candidateIds = new Set(reference.candidate_asset_ids);
  return [...targets].sort((left, right) => {
    const leftCandidate = candidateIds.has(left.assetId) ? 0 : 1;
    const rightCandidate = candidateIds.has(right.assetId) ? 0 : 1;
    if (leftCandidate !== rightCandidate) return leftCandidate - rightCandidate;
    return left.displayName.localeCompare(right.displayName);
  });
}

export function filterReferenceMappingTargets(
  reference: AssetReferenceGroupItem,
  targets: ReferenceMappingTarget[],
  options: {
    query: string;
    connectionName: string;
  },
) {
  const needle = options.query.trim().toLowerCase();
  return orderReferenceMappingTargets(reference, targets).filter((target) => {
    if (options.connectionName && target.connectionName !== options.connectionName) return false;
    if (!needle) return true;
    return [
      target.displayName,
      target.context,
      target.connectionName,
      target.assetType,
      target.kind,
      target.value,
    ].join(" ").toLowerCase().includes(needle);
  });
}

export function mappingTargetForAssetId(assetId: string | null | undefined, targets: ReferenceMappingTarget[]) {
  return targets.find((target) => target.assetId === assetId) ?? null;
}

export function mappingTargetForMapping(
  mapping: Pick<ProjectReferenceMapping, "target_identifier_kind" | "target_normalized_value"> | {
    target_identifier_kind?: string | null;
    target_normalized_value?: string | null;
  } | null,
  targets: ReferenceMappingTarget[],
) {
  if (!mapping) return null;
  return targets.find((target) => (
    target.kind === mapping.target_identifier_kind
    && target.value === mapping.target_normalized_value
  )) ?? null;
}

export function referenceMappingTargetIdentifier(asset: AssetInventoryItem): ReferenceMappingTargetIdentifier | null {
  if (asset.mapping_target?.kind && asset.mapping_target.value) {
    return {
      kind: asset.mapping_target.kind as TargetIdentifierKind,
      value: asset.mapping_target.value,
      display: asset.mapping_target.display || asset.mapping_target.value,
    };
  }
  const identifiers = asset.identifiers ?? [];
  const preferred = (asset.asset_type === "api" ? identifiers.find((identifier) => identifierKind(identifier) === "api_endpoint") : null)
    || identifiers.find((identifier) => identifierKind(identifier) === "logical_table")
    || identifiers.find((identifier) => identifierKind(identifier) === "physical_path");
  const kind = identifierKind(preferred);
  const value = stringField(preferred, "normalized_value");
  if (kind && value) {
    return {
      kind,
      value,
      display: stringField(preferred, "display_value") || value,
    };
  }
  return null;
}

function mappingTargetContext(asset: AssetInventoryItem, canonicalDisplay: string, displayName: string) {
  if (asset.asset_type === "table") {
    return [asset.catalog, asset.database].filter(Boolean).join(".") || null;
  }
  const hasAlias = Boolean(asset.table);
  const identity = asset.asset_type === "python_function"
    ? asset.python_function || canonicalDisplay
    : asset.asset_type === "sql_query"
      ? asset.query || canonicalDisplay
      : canonicalDisplay;
  if (hasAlias) return identity;
  return removeTrailingDisplayName(identity, displayName);
}

function removeTrailingDisplayName(identity: string, displayName: string) {
  if (identity === displayName) return null;
  for (const separator of [".", "/"]) {
    const suffix = `${separator}${displayName}`;
    if (identity.endsWith(suffix)) return identity.slice(0, -suffix.length) || null;
  }
  return identity;
}

function identifierKind(identifier: Record<string, unknown> | undefined): TargetIdentifierKind | null {
  const kind = stringField(identifier, "kind");
  return kind === "logical_table" || kind === "physical_path" || kind === "api_endpoint" ? kind : null;
}

function stringField(value: Record<string, unknown> | undefined, key: string) {
  const field = value?.[key];
  return typeof field === "string" && field.trim() ? field.trim() : null;
}

export function referenceMappingTargetKey(kind: TargetIdentifierKind, value: string) {
  return `${kind}\u001f${value}`;
}
