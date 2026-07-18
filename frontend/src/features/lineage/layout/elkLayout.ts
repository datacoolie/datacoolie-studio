import type { Edge as FlowEdge } from "@xyflow/react";
import ELK from "elkjs/lib/elk-api.js";
import ElkWorker from "elkjs/lib/elk-worker.min.js?worker";
import type { LineageAssetFlowNode } from "../model/types";
import { recommendedLayerSpacing } from "../model/edgeGeometry";

const ASSET_NODE_HEIGHT = 62;
const positionCache = new Map<string, Map<string, { x: number; y: number }>>();
let elk: InstanceType<typeof ELK> | null = null;

function elkClient() {
  elk ??= new ELK({ workerFactory: () => new ElkWorker() });
  return elk;
}

export function disposeLineageLayoutWorker() {
  elk?.terminateWorker();
  elk = null;
}

export async function layoutLineage(
  nodes: LineageAssetFlowNode[],
  edges: FlowEdge[],
  cacheKey: string
) {
  const cached = positionCache.get(cacheKey);
  if (cached) return cached;

  const layerSpacing = recommendedLayerSpacing(
    edges.flatMap((edge) => (
      edge.data?.relationType === "dataflow" && typeof edge.data.label === "string"
        ? [edge.data.label]
        : []
    ))
  );
  const graphInput = {
    id: "lineage-root",
    layoutOptions: {
      "elk.algorithm": "layered",
      "elk.direction": "RIGHT",
      "elk.edgeRouting": "ORTHOGONAL",
      "elk.spacing.nodeNode": "42",
      "elk.spacing.componentComponent": "64",
      "elk.separateConnectedComponents": "true",
      "elk.layered.compaction.connectedComponents": "true",
      "elk.layered.crossingMinimization.strategy": "LAYER_SWEEP",
      "elk.layered.nodePlacement.strategy": "NETWORK_SIMPLEX",
      "elk.layered.nodePlacement.favorStraightEdges": "true",
      "elk.layered.spacing.nodeNodeBetweenLayers": String(layerSpacing)
    },
    children: nodes.map((node) => ({
      id: node.id,
      width: node.data.nodeWidth,
      height: ASSET_NODE_HEIGHT
    })),
    edges: edges.map((edge) => ({
      id: edge.id,
      sources: [edge.source],
      targets: [edge.target]
    }))
  };
  let graph;
  try {
    graph = await elkClient().layout(graphInput);
  } catch {
    disposeLineageLayoutWorker();
    graph = await elkClient().layout(graphInput);
  }

  const positions = new Map(
    (graph.children ?? []).map((node) => [
      node.id,
      { x: node.x ?? 0, y: node.y ?? 0 }
    ])
  );
  positionCache.set(cacheKey, positions);
  if (positionCache.size > 30) {
    positionCache.delete(positionCache.keys().next().value!);
  }
  return positions;
}
