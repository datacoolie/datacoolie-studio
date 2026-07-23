import { MarkerType, Position } from "@xyflow/react";
import type {
  LatestStatusResponse,
  LineageAsset,
  LineageDependency,
  LineageReference
} from "../../../shared/api/types";
import { assetIconKind, presentLineageAsset, referenceTypeAssetType } from "./presentation";
import type {
  LineageAssetFlowNode,
  LineageFlow,
  LineageFlowEdge,
  LineageSelection,
  LineageSelectionState,
  VisibleLineage
} from "./types";
import { isLineageAsset } from "./types";

const NODE_WIDTH = 232;

export function buildLineageFlow(
  visible: VisibleLineage,
  latestStatus: LatestStatusResponse | null,
  statusOverlay: boolean,
  selection: LineageSelection,
  hoveredEdgeId: string | null
): LineageFlow {
  const selectionContext = buildSelectionContext(visible, selection);
  const nodes: LineageAssetFlowNode[] = visible.entities.map((entity) => {
    const focused = visible.focusNodeIds.has(entity.id);
    const selectionState = nodeSelectionState(entity.id, selectionContext);
    const presentation = isLineageAsset(entity)
      ? presentLineageAsset(entity)
      : presentReference(entity);
    return {
      id: entity.id,
      type: "lineageAsset",
      position: { x: 0, y: 0 },
      targetPosition: Position.Left,
      sourcePosition: Position.Right,
      className: [
        isLineageAsset(entity) ? "flow-asset" : "flow-reference",
        focused ? "flow-asset-focused" : "",
        `flow-selection-${selectionState}`
      ].filter(Boolean).join(" "),
      style: { width: NODE_WIDTH },
      selected: selectionState === "selected",
      ariaLabel: `${presentation.connection}, ${presentation.locator}, ${presentation.badge}`,
      data: {
        entityType: entity.entity_type,
        locator: presentation.locator,
        connection: presentation.connection,
        badge: presentation.badge,
        iconKind: presentation.iconKind,
        assetType: isLineageAsset(entity) ? entity.asset_type : undefined,
        referenceType: isLineageAsset(entity) ? undefined : entity.reference_type,
        fullIdentity: presentation.fullIdentity,
        focused,
        issueCount: visible.issueCountByAsset.get(entity.id) ?? 0,
        declarationStatus: isLineageAsset(entity) ? entity.declaration_status : undefined,
        referenceStatus: isLineageAsset(entity) ? undefined : entity.resolution.state,
        nodeWidth: NODE_WIDTH,
        selectionState
      }
    };
  });

  const fanOut = countBy(visible.dataflows.map((item) => item.source_asset_id));
  const fanIn = countBy(visible.dataflows.map((item) => item.destination_asset_id));
  const dataflowRelationKeys = new Set(visible.dataflows.map((item) => relationKey(item.source_asset_id, item.destination_asset_id)));
  const dataflowEdges: LineageFlowEdge[] = visible.dataflows.map((dataflow) => {
    const selectionState = edgeSelectionState(dataflow.id, selectionContext);
    const selected = selectionState === "selected";
    const hovered = hoveredEdgeId === dataflow.id;
    const focused = visible.focusEdgeIds.has(dataflow.id);
    const status = normalizeStatus(latestRun(latestStatus, dataflow.dataflow_id, dataflow.name)?.status);
    const color = selectionEdgeColor(selectionState)
      ?? (statusOverlay ? edgeColor(status) : "var(--lineage-edge-neutral)");
    return {
      id: dataflow.id,
      source: dataflow.source_asset_id,
      target: dataflow.destination_asset_id,
      type: "lineageDataflow",
      markerEnd: { type: MarkerType.ArrowClosed, color },
      selected,
      ariaLabel: `${dataflow.name}${statusOverlay && status !== "unknown" ? `, ${status}` : ""}`,
      zIndex: selected ? 24 : selectionState === "input" || selectionState === "output" || selectionState === "both"
        ? 18 : hovered ? 14 : focused ? 10 : 1,
      style: {
        stroke: color,
        strokeWidth: selected ? 3.2 : isRelatedSelection(selectionState) ? 2.4 : hovered || focused ? 2.4 : 1.6,
        opacity: 1
      },
      data: {
        relationType: "dataflow",
        label: dataflow.name,
        status: statusOverlay ? status : "unknown",
        selectionState,
        labelSegment: (fanOut.get(dataflow.source_asset_id) ?? 0) > 1
          && (fanIn.get(dataflow.destination_asset_id) ?? 0) > 1 ? "longest"
          : (fanOut.get(dataflow.source_asset_id) ?? 0) > 1 ? "target"
            : (fanIn.get(dataflow.destination_asset_id) ?? 0) > 1 ? "source"
              : "longest"
      }
    };
  });

  const dependencyRouteLanes = dependencyRouteLaneById(visible.dependencies, dataflowRelationKeys);
  const dependencyEdges: LineageFlowEdge[] = visible.dependencies.map((dependency) => {
    const selectionState = edgeSelectionState(dependency.id, selectionContext);
    const selected = selectionState === "selected";
    const hovered = hoveredEdgeId === dependency.id;
    const focused = visible.focusEdgeIds.has(dependency.id);
    const color = selectionEdgeColor(selectionState) ?? dependencyColor();
    return {
      id: dependency.id,
      source: dependency.resolved_asset_id || dependency.reference_id,
      target: dependency.target_asset_id,
      type: "lineageDependency",
      markerEnd: {
        type: MarkerType.ArrowClosed,
        color
      },
      selected,
      ariaLabel: `${dependency.provenance} dependency, ${dependency.resolution.state}`,
      zIndex: selected ? 23 : isRelatedSelection(selectionState) ? 17 : hovered ? 13 : focused ? 9 : 0,
      style: {
        stroke: color,
        strokeDasharray: "6 5",
        strokeLinecap: "round",
        strokeWidth: selected ? 3 : isRelatedSelection(selectionState) ? 2.3 : hovered || focused ? 2.2 : 1.4,
        opacity: dependency.resolution.state === "automatic" ? 0.82 : 1
      },
      data: {
        relationType: "dependency",
        resolutionStatus: dependency.resolution.state,
        routeLane: dependencyRouteLanes.get(dependency.id) ?? 0,
        selectionState,
      }
    };
  });

  return { nodes, edges: [...dependencyEdges, ...dataflowEdges] };
}

export function applyLineageEdgeHover(flow: LineageFlow, hoveredEdgeId: string | null): LineageFlow {
  if (!hoveredEdgeId) return flow;
  return {
    nodes: flow.nodes,
    edges: flow.edges.map((edge) => edge.id !== hoveredEdgeId ? edge : {
      ...edge,
      zIndex: Math.max(edge.zIndex ?? 0, edge.data?.relationType === "dependency" ? 13 : 14),
      style: {
        ...edge.style,
        strokeWidth: Math.max(Number(edge.style?.strokeWidth ?? 0), edge.data?.relationType === "dependency" ? 2.2 : 2.4),
      },
    }),
  };
}

function presentReference(reference: LineageReference) {
  const referenceObjectType = referenceTypeAssetType(reference.reference_type);
  return {
    locator: reference.display_name,
    connection: referenceSubtitle(reference),
    badge: reference.resolution.state.toUpperCase(),
    iconKind: assetIconKind(referenceObjectType),
    fullIdentity: reference.normalized_value
  };
}

function referenceSubtitle(reference: LineageReference) {
  const referenceType = reference.reference_type.replace(/_/gu, " ");
  const provenances = Array.from(new Set(reference.provenances)).map(referenceProvenanceLabel);
  return provenances.length ? `${referenceType} · ${provenances.join(" + ")}` : referenceType;
}

function referenceProvenanceLabel(provenance: LineageReference["provenances"][number]) {
  if (provenance === "sql") return "SQL";
  if (provenance === "python") return "Python";
  return "Python SQL";
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

function dependencyColor() {
  return "var(--lineage-edge-neutral)";
}

function selectionEdgeColor(selectionState: LineageSelectionState) {
  if (selectionState === "selected") return "var(--lineage-selection-selected)";
  if (selectionState === "input") return "var(--lineage-selection-input)";
  if (selectionState === "output") return "var(--lineage-selection-output)";
  if (selectionState === "both") return "var(--lineage-selection-both)";
  return null;
}

function isRelatedSelection(selectionState: LineageSelectionState) {
  return selectionState === "input" || selectionState === "output" || selectionState === "both";
}

interface SelectionContext {
  active: boolean;
  selectedNodeId: string | null;
  selectedEdgeId: string | null;
  inputNodeIds: Set<string>;
  outputNodeIds: Set<string>;
  inputEdgeIds: Set<string>;
  outputEdgeIds: Set<string>;
}

function buildSelectionContext(visible: VisibleLineage, selection: LineageSelection): SelectionContext {
  const context: SelectionContext = {
    active: Boolean(selection),
    selectedNodeId: selection?.kind === "asset" || selection?.kind === "reference" ? selection.id : null,
    selectedEdgeId: selection?.kind === "dataflow" || selection?.kind === "dependency" ? selection.id : null,
    inputNodeIds: new Set(),
    outputNodeIds: new Set(),
    inputEdgeIds: new Set(),
    outputEdgeIds: new Set()
  };
  if (!selection) return context;

  const relations = [
    ...visible.dataflows.map((item) => ({ id: item.id, kind: "dataflow" as const, source: item.source_asset_id, target: item.destination_asset_id })),
    ...visible.dependencies.map((item) => ({
      id: item.id,
      kind: "dependency" as const,
      source: item.resolved_asset_id || item.reference_id,
      target: item.target_asset_id
    }))
  ];

  if (context.selectedNodeId) {
    for (const relation of relations) {
      if (relation.target === context.selectedNodeId) {
        context.inputNodeIds.add(relation.source);
        context.inputEdgeIds.add(relation.id);
      }
      if (relation.source === context.selectedNodeId) {
        context.outputNodeIds.add(relation.target);
        context.outputEdgeIds.add(relation.id);
      }
    }
    return context;
  }

  const selectedRelation = relations.find((relation) => relation.kind === selection.kind && relation.id === selection.id);
  if (selectedRelation) {
    context.inputNodeIds.add(selectedRelation.source);
    context.outputNodeIds.add(selectedRelation.target);
  }
  return context;
}

function nodeSelectionState(id: string, context: SelectionContext): LineageSelectionState {
  if (!context.active) return "none";
  if (context.selectedNodeId === id) return "selected";
  return relatedSelectionState(id, context.inputNodeIds, context.outputNodeIds);
}

function edgeSelectionState(id: string, context: SelectionContext): LineageSelectionState {
  if (!context.active) return "none";
  if (context.selectedEdgeId === id) return "selected";
  return relatedSelectionState(id, context.inputEdgeIds, context.outputEdgeIds);
}

function relatedSelectionState(id: string, inputs: Set<string>, outputs: Set<string>): LineageSelectionState {
  const input = inputs.has(id);
  const output = outputs.has(id);
  if (input && output) return "both";
  if (input) return "input";
  if (output) return "output";
  return "none";
}

function dependencyRouteLaneById(
  dependencies: LineageDependency[],
  dataflowRelationKeys: Set<string>,
) {
  const dependenciesByRelation = new Map<string, LineageDependency[]>();
  for (const dependency of dependencies) {
    const key = relationKey(dependency.resolved_asset_id || dependency.reference_id, dependency.target_asset_id);
    const group = dependenciesByRelation.get(key) ?? [];
    group.push(dependency);
    dependenciesByRelation.set(key, group);
  }
  const lanes = new Map<string, number>();
  for (const [key, group] of dependenciesByRelation) {
    const reserveCenterLane = dataflowRelationKeys.has(key);
    group.sort((left, right) => left.id.localeCompare(right.id)).forEach((dependency, index) => {
      lanes.set(dependency.id, dependencyLane(index, group.length, reserveCenterLane));
    });
  }
  return lanes;
}

function dependencyLane(index: number, count: number, reserveCenterLane: boolean) {
  if (!reserveCenterLane && count === 1) return 0;
  const magnitude = Math.floor(index / 2) + 1;
  return index % 2 === 0 ? magnitude : -magnitude;
}

function relationKey(source: string, target: string) {
  return `${source}\u0000${target}`;
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
  return latestStatus.latest_by_id[dataflowId]
    ?? latestStatus.latest_by_name[dataflowName]
    ?? null;
}
