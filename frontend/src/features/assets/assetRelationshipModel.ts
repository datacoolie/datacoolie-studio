import type {
  AssetBrief,
  AssetDependsOnItem,
  AssetDetailResponse,
  AssetFlow,
  AssetUsedByItem,
} from "../../shared/api/types";

export type AssetRelationshipDirection = "upstream" | "downstream";

export type AssetRelationshipVia =
  | { kind: "dataflow"; id: string; flow: AssetFlow }
  | {
      kind: "dependency";
      id: string;
      dependencyKind: string;
      provenance: string;
      resolutionMethod: string;
      resolutionStatus: string;
      referenceId: string | null;
    };

export interface AssetRelationshipGroup {
  asset: AssetBrief;
  via: AssetRelationshipVia[];
}

export function assetRelationshipGroups(
  detail: AssetDetailResponse | null,
  direction: AssetRelationshipDirection,
): AssetRelationshipGroup[] {
  if (!detail) return [];

  const neighbors = direction === "upstream" ? detail.upstream_assets : detail.downstream_assets;
  const groups = new Map<string, AssetRelationshipGroup>();
  for (const neighbor of neighbors) {
    groups.set(neighbor.asset.id, { asset: neighbor.asset, via: [] });
  }

  const flows = direction === "upstream" ? detail.input_flows : detail.output_flows;
  for (const flow of flows) {
    relationshipGroup(groups, flow.counterpart).via.push({
      kind: "dataflow",
      id: flow.id || `${flow.dataflow_id}-${flow.counterpart.id}`,
      flow,
    });
  }

  if (direction === "upstream") {
    for (const dependency of detail.depends_on) addUpstreamDependency(groups, dependency);
  } else {
    for (const dependency of detail.used_by) addDownstreamDependency(groups, dependency);
  }

  return [...groups.values()];
}

function addUpstreamDependency(
  groups: Map<string, AssetRelationshipGroup>,
  dependency: AssetDependsOnItem,
) {
  if (!dependency.resolved_asset) return;
  relationshipGroup(groups, dependency.resolved_asset).via.push({
    kind: "dependency",
    id: dependency.id,
    dependencyKind: dependency.kind,
    provenance: dependency.provenance,
    resolutionMethod: dependency.resolution_method,
    resolutionStatus: dependency.resolution_status,
    referenceId: dependency.reference_id || dependency.source_reference?.id || null,
  });
}

function addDownstreamDependency(
  groups: Map<string, AssetRelationshipGroup>,
  dependency: AssetUsedByItem,
) {
  relationshipGroup(groups, dependency.target_asset).via.push({
    kind: "dependency",
    id: dependency.id,
    dependencyKind: dependency.kind,
    provenance: dependency.provenance,
    resolutionMethod: dependency.resolution_method,
    resolutionStatus: dependency.resolution_status,
    referenceId: dependency.reference?.id || null,
  });
}

function relationshipGroup(
  groups: Map<string, AssetRelationshipGroup>,
  asset: AssetBrief,
) {
  const existing = groups.get(asset.id);
  if (existing) return existing;
  const group: AssetRelationshipGroup = { asset, via: [] };
  groups.set(asset.id, group);
  return group;
}
