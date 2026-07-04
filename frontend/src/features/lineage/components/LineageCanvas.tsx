import {
  Background,
  ControlButton,
  Controls,
  MiniMap,
  ReactFlow,
  type ReactFlowInstance
} from "@xyflow/react";
import { LoaderCircle } from "lucide-react";
import { useMemo, useState, type KeyboardEvent } from "react";
import type { LatestStatusResponse } from "../../../shared/api/types";
import { EmptyState } from "../../../shared/components/EmptyState";
import { useLineageLayout } from "../hooks/useLineageLayout";
import { buildLineageFlow } from "../model/flow";
import type { LineageSelection, VisibleLineage } from "../model/types";
import { LineageAssetNode } from "./LineageAssetNode";
import { LineageDataflowEdge, LineageDependencyEdge } from "./LineageEdges";

const NODE_TYPES = { lineageAsset: LineageAssetNode };
const EDGE_TYPES = {
  lineageDataflow: LineageDataflowEdge,
  lineageDependency: LineageDependencyEdge
};

export function LineageCanvas({
  visible,
  latestStatus,
  statusOverlay,
  selection,
  layoutKey,
  onSelectionChange,
  onReset
}: {
  visible: VisibleLineage;
  latestStatus: LatestStatusResponse | null;
  statusOverlay: boolean;
  selection: LineageSelection;
  layoutKey: string;
  onSelectionChange: (selection: LineageSelection) => void;
  onReset: () => void;
}) {
  const [hoveredEdgeId, setHoveredEdgeId] = useState<string | null>(null);
  const [flowInstance, setFlowInstance] = useState<ReactFlowInstance | null>(null);
  const selectedId = selection?.id ?? null;
  const flow = useMemo(
    () => buildLineageFlow(visible, latestStatus, statusOverlay, selectedId, hoveredEdgeId),
    [visible, latestStatus, statusOverlay, selectedId, hoveredEdgeId]
  );
  const { nodes, ready } = useLineageLayout(flow, layoutKey);
  const shouldFitView = visible.entities.length <= 60;

  function zoomHundredPercent() {
    if (!flowInstance) return;
    const viewport = flowInstance.getViewport();
    void flowInstance.setViewport({ ...viewport, zoom: 1 }, { duration: 220 });
  }

  function selectFromKeyboard(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key !== "Enter" && event.key !== " ") return;
    const target = event.target as HTMLElement;
    const edgeElement = target.closest<SVGGElement>(".react-flow__edge[data-id]");
    if (edgeElement?.dataset.id) {
      event.preventDefault();
      const edge = flow.edges.find((item) => item.id === edgeElement.dataset.id);
      const kind = edge?.data?.relationType === "dependency" ? "dependency" : "dataflow";
      onSelectionChange({ kind, id: edgeElement.dataset.id });
      return;
    }
    const nodeElement = target.closest<HTMLDivElement>(".react-flow__node[data-id]");
    if (nodeElement?.dataset.id) {
      event.preventDefault();
      const node = flow.nodes.find((item) => item.id === nodeElement.dataset.id);
      onSelectionChange({
        kind: node?.data.entityType === "reference" ? "reference" : "asset",
        id: nodeElement.dataset.id
      });
    }
  }

  if (!visible.entities.length) {
    return (
      <EmptyState
        title="No lineage matches this focus"
        action={<button className="lineage-reset-button" type="button" onClick={onReset}>Reset lineage view</button>}
      />
    );
  }

  if (!ready) {
    return (
      <div className="lineage-layout-loading" aria-live="polite">
        <LoaderCircle size={18} className="spin" />
        <span>Arranging lineage</span>
      </div>
    );
  }

  return (
    <div className="lineage-flow-host" onKeyDown={selectFromKeyboard}>
      <ReactFlow
        key={layoutKey}
        nodes={nodes}
        edges={flow.edges}
        nodeTypes={NODE_TYPES}
        edgeTypes={EDGE_TYPES}
        fitView={shouldFitView}
        fitViewOptions={{ padding: 0.18, minZoom: 0.34, maxZoom: 1 }}
        defaultViewport={{ x: 36, y: 36, zoom: 0.42 }}
        proOptions={{ hideAttribution: true }}
        minZoom={0.2}
        maxZoom={1.6}
        onInit={setFlowInstance}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable
        onlyRenderVisibleElements
        onNodeClick={(_, node) => onSelectionChange({
          kind: node.data.entityType === "reference" ? "reference" : "asset",
          id: node.id
        })}
        onEdgeClick={(_, edge) => {
          onSelectionChange({
            kind: edge.data?.relationType === "dependency" ? "dependency" : "dataflow",
            id: edge.id
          });
        }}
        onEdgeMouseEnter={(_, edge) => setHoveredEdgeId(edge.id)}
        onEdgeMouseLeave={() => setHoveredEdgeId(null)}
        onPaneClick={() => onSelectionChange(null)}
      >
        <Background gap={20} color="#e2e6ec" />
        {visible.entities.length > 30 ? <MiniMap pannable zoomable nodeStrokeWidth={2} /> : null}
        <Controls showInteractive={false}>
          <ControlButton title="Zoom to 100%" aria-label="Zoom to 100%" onClick={zoomHundredPercent}>
            <span className="lineage-zoom-actual">100%</span>
          </ControlButton>
        </Controls>
      </ReactFlow>
    </div>
  );
}
