import { MarkerType, Position } from "@xyflow/react";
import type {
  LatestStatusResponse,
  LineageAsset,
  LineageReference
} from "../../../shared/api/types";
import { presentLineageAsset } from "./presentation";
import type {
  LineageAssetFlowNode,
  LineageFlow,
  LineageFlowEdge,
  VisibleLineage
} from "./types";

const NODE_WIDTH = 232;

export function buildLineageFlow(
  visible: VisibleLineage,
  latestStatus: LatestStatusResponse | null,
  statusOverlay: boolean,
  selectedId: string | null,
  hoveredEdgeId: string | null
): LineageFlow {
  const nodes: LineageAssetFlowNode[] = visible.entities.map((entity) => {
    const focused = visible.focusNodeIds.has(entity.id);
    const presentation = isAsset(entity)
      ? presentLineageAsset(entity)
      : presentReference(entity);
    return {
      id: entity.id,
      type: "lineageAsset",
      position: { x: 0, y: 0 },
      targetPosition: Position.Left,
      sourcePosition: Position.Right,
      className: `${isAsset(entity) ? "flow-asset" : "flow-reference"}${focused ? " flow-asset-focused" : ""}`,
      style: { width: NODE_WIDTH },
      selected: selectedId === entity.id,
      ariaLabel: `${presentation.connection}, ${presentation.locator}, ${presentation.badge}`,
      data: {
        entityType: isAsset(entity) ? "asset" : "reference",
        locator: presentation.locator,
        connection: presentation.connection,
        badge: presentation.badge,
        iconKind: presentation.iconKind,
        fullIdentity: presentation.fullIdentity,
        focused,
        issueCount: visible.issueCountByAsset.get(entity.id) ?? 0,
        declarationStatus: isAsset(entity) ? entity.declaration_status : undefined,
        referenceStatus: isAsset(entity) ? undefined : entity.resolution_status,
        nodeWidth: NODE_WIDTH
      }
    };
  });

  const fanOut = countBy(visible.dataflows.map((item) => item.source_asset_id));
  const fanIn = countBy(visible.dataflows.map((item) => item.destination_asset_id));
  const dataflowEdges: LineageFlowEdge[] = visible.dataflows.map((dataflow) => {
    const selected = selectedId === dataflow.id;
    const hovered = hoveredEdgeId === dataflow.id;
    const focused = visible.focusEdgeIds.has(dataflow.id);
    const status = normalizeStatus(latestRun(latestStatus, dataflow.dataflow_id, dataflow.name)?.status);
    const color = statusOverlay ? edgeColor(status) : "var(--lineage-edge-neutral)";
    return {
      id: dataflow.id,
      source: dataflow.source_asset_id,
      target: dataflow.destination_asset_id,
      type: "lineageDataflow",
      markerEnd: { type: MarkerType.ArrowClosed, color },
      selected,
      ariaLabel: `${dataflow.name}${statusOverlay && status !== "unknown" ? `, ${status}` : ""}`,
      zIndex: selected || hovered ? 20 : focused ? 10 : 1,
      style: {
        stroke: color,
        strokeWidth: selected ? 3 : hovered || focused ? 2.4 : 1.6,
        opacity: 1
      },
      data: {
        relationType: "dataflow",
        label: dataflow.name,
        status: statusOverlay ? status : "unknown",
        labelSegment: (fanOut.get(dataflow.source_asset_id) ?? 0) > 1
          && (fanIn.get(dataflow.destination_asset_id) ?? 0) > 1 ? "longest"
          : (fanOut.get(dataflow.source_asset_id) ?? 0) > 1 ? "target"
            : (fanIn.get(dataflow.destination_asset_id) ?? 0) > 1 ? "source"
              : "longest"
      }
    };
  });

  const dependencyEdges: LineageFlowEdge[] = visible.dependencies.map((dependency) => {
    const selected = selectedId === dependency.id;
    const hovered = hoveredEdgeId === dependency.id;
    const focused = visible.focusEdgeIds.has(dependency.id);
    return {
      id: dependency.id,
      source: dependency.source.id,
      target: dependency.target_asset_id,
      type: "lineageDependency",
      markerEnd: {
        type: MarkerType.ArrowClosed,
        color: dependencyColor(dependency.resolution_status)
      },
      selected,
      ariaLabel: `${dependency.provenance} dependency, ${dependency.resolution_status}`,
      zIndex: selected || hovered ? 18 : focused ? 9 : 0,
      style: {
        stroke: dependencyColor(dependency.resolution_status),
        strokeDasharray: "6 5",
        strokeWidth: selected ? 2.8 : hovered || focused ? 2.2 : 1.4,
        opacity: dependency.resolution_status === "resolved" ? 0.82 : 1
      },
      data: {
        relationType: "dependency",
        resolutionStatus: dependency.resolution_status
      }
    };
  });

  return { nodes, edges: [...dependencyEdges, ...dataflowEdges] };
}

function presentReference(reference: LineageReference) {
  return {
    locator: reference.display_name,
    connection: `${reference.provenance} dependency`,
    badge: reference.resolution_status.toUpperCase(),
    iconKind: reference.provenance === "sql" ? "sql" as const : "code" as const,
    fullIdentity: reference.raw_value
  };
}

function isAsset(entity: LineageAsset | LineageReference): entity is LineageAsset {
  return "declaration_status" in entity;
}

function countBy(values: string[]) {
  const counts = new Map<string, number>();
  for (const value of values) counts.set(value, (counts.get(value) ?? 0) + 1);
  return counts;
}

function edgeColor(status: string) {
  if (status === "failed") return "var(--lineage-edge-failed)";
  if (status === "succeeded") return "var(--lineage-edge-succeeded)";
  if (status === "skipped" || status === "warning") return "var(--lineage-edge-warning)";
  return "var(--lineage-edge-neutral)";
}

function dependencyColor(status: string) {
  if (status === "ambiguous") return "var(--lineage-dependency-ambiguous)";
  if (status === "unresolved") return "var(--lineage-dependency-unresolved)";
  return "var(--lineage-dependency)";
}

function normalizeStatus(value: unknown) {
  return typeof value === "string" && value ? value.toLowerCase() : "unknown";
}

export function latestRun(
  latestStatus: LatestStatusResponse | null,
  dataflowId: string,
  dataflowName: string
) {
  if (!latestStatus) return null;
  if (latestStatus.latest_by_id || latestStatus.latest_by_name) {
    return latestStatus.latest_by_id?.[dataflowId]
      ?? latestStatus.latest_by_name?.[dataflowName]
      ?? null;
  }
  return latestStatus.latest[dataflowName] ?? latestStatus.latest[dataflowId] ?? null;
}
