import type { Edge as FlowEdge, Node as FlowNode } from "@xyflow/react";
import type {
  LineageAsset,
  LineageDataflow,
  LineageDependency,
  LineageReference
} from "../../../shared/api/types";
import type { AssetIconKind } from "./presentation";

export type TraceDirection = "upstream" | "both" | "downstream";
export type LineageEntity = LineageAsset | LineageReference;
export type LineageRelation = LineageDataflow | LineageDependency;

export type LineageFocus =
  { kind: "asset" | "reference" | "dataflow" | "dependency"; id: string };

export type LineageSelection = LineageFocus | null;

export interface LineageFilters {
  connections: string[];
  stages: string[];
  formats: string[];
  resolutions: string[];
}

export interface VisibleLineage {
  entities: LineageEntity[];
  dataflows: LineageDataflow[];
  dependencies: LineageDependency[];
  focusNodeIds: Set<string>;
  focusEdgeIds: Set<string>;
  issueCountByAsset: Map<string, number>;
  filtersActive: boolean;
  traceActive: boolean;
}

export interface LineageSearchResult {
  id: string;
  kind: "asset" | "reference" | "dataflow" | "dependency";
  title: string;
  subtitle: string;
  identity: string;
  searchText: string;
}

export interface LineageAssetNodeData extends Record<string, unknown> {
  entityType: "asset" | "reference";
  locator: string;
  connection: string;
  badge: string;
  iconKind: AssetIconKind;
  fullIdentity: string;
  focused: boolean;
  issueCount: number;
  declarationStatus?: "declared" | "discovered_only";
  referenceStatus?: "ambiguous" | "unresolved";
  nodeWidth: number;
}

export interface LineageDataflowEdgeData extends Record<string, unknown> {
  relationType: "dataflow";
  label: string;
  status: string;
  labelSegment: "source" | "target" | "longest";
}

export interface LineageDependencyEdgeData extends Record<string, unknown> {
  relationType: "dependency";
  resolutionStatus: string;
}

export type LineageAssetFlowNode = FlowNode<LineageAssetNodeData, "lineageAsset">;
export type LineageFlowEdge = FlowEdge<LineageDataflowEdgeData | LineageDependencyEdgeData>;

export interface LineageFlow {
  nodes: LineageAssetFlowNode[];
  edges: LineageFlowEdge[];
}
