import {
  Background,
  ControlButton,
  Controls,
  MiniMap,
  ReactFlow,
  type ReactFlowInstance
} from "@xyflow/react";
import { LocateFixed, LoaderCircle } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type MouseEvent } from "react";
import type { LatestStatusResponse } from "../../../shared/api/domainTypes";
import { EmptyState } from "../../../shared/components/EmptyState";
import { useLineageLayout } from "../hooks/useLineageLayout";
import { applyLineageEdgeHover, buildLineageFlow } from "../model/flow";
import type { LineageAssetFlowNode, LineageFlowEdge, LineageSelection, VisibleLineage } from "../model/types";
import {
  focusLineageFitOptions,
  initialLargeGraphFitOptions,
  LINEAGE_FIT_DURATION,
  shouldAutoFitLineage,
  visibleLineageFitOptions,
} from "../model/viewport";
import { LineageAssetNode } from "./LineageAssetNode";
import { LineageDataflowEdge, LineageDependencyEdge } from "./LineageEdges";

const NODE_TYPES = { lineageAsset: LineageAssetNode };
const EDGE_TYPES = {
  lineageDataflow: LineageDataflowEdge,
  lineageDependency: LineageDependencyEdge
};

export function LineageCanvas({
  environmentId,
  visible,
  latestStatus,
  statusOverlay,
  selection,
  layoutKey,
  traceKey,
  onSelectionChange,
  onReset
}: {
  environmentId: number;
  visible: VisibleLineage;
  latestStatus: LatestStatusResponse | null;
  statusOverlay: boolean;
  selection: LineageSelection;
  layoutKey: string;
  traceKey: string;
  onSelectionChange: (selection: LineageSelection) => void;
  onReset: () => void;
}) {
  const [hoveredEdgeId, setHoveredEdgeId] = useState<string | null>(null);
  const [flowInstance, setFlowInstance] = useState<ReactFlowInstance<LineageAssetFlowNode, LineageFlowEdge> | null>(null);
  const flowInstanceRef = useRef<ReactFlowInstance<LineageAssetFlowNode, LineageFlowEdge> | null>(null);
  const hasMountedFlowRef = useRef(false);
  const lastViewportRef = useRef<{ x: number; y: number; zoom: number } | null>(null);
  const lastTraceKeyRef = useRef("");
  const viewportEnvironmentRef = useRef(environmentId);
  const topologyFlow = useMemo(
    () => buildLineageFlow(visible, latestStatus, statusOverlay, selection, null),
    [visible, latestStatus, statusOverlay, selection]
  );
  const flow = useMemo(() => applyLineageEdgeHover(topologyFlow, hoveredEdgeId), [topologyFlow, hoveredEdgeId]);
  const { nodes, ready } = useLineageLayout(topologyFlow, layoutKey);
  const shouldFitView = shouldAutoFitLineage(visible.entities.length) && !hasMountedFlowRef.current;

  useEffect(() => {
    if (viewportEnvironmentRef.current === environmentId) return;
    viewportEnvironmentRef.current = environmentId;
    hasMountedFlowRef.current = false;
    flowInstanceRef.current = null;
    setFlowInstance(null);
    lastViewportRef.current = null;
    lastTraceKeyRef.current = "";
  }, [environmentId]);

  useEffect(() => {
    if (visible.entities.length) return;
    hasMountedFlowRef.current = false;
    flowInstanceRef.current = null;
    setFlowInstance(null);
    lastViewportRef.current = null;
    lastTraceKeyRef.current = "";
  }, [visible.entities.length]);

  function zoomHundredPercent() {
    if (!flowInstance) return;
    const viewport = flowInstance.getViewport();
    lastViewportRef.current = { ...viewport, zoom: 1 };
    void flowInstance.setViewport({ ...viewport, zoom: 1 }, { duration: 220 });
  }

  function fitTrace(duration = LINEAGE_FIT_DURATION) {
    const instance = flowInstanceRef.current;
    if (!instance || !visible.traceActive || !nodes.length) return;
    void instance.fitView({ ...focusLineageFitOptions(), nodes, duration }).then(() => {
      if (flowInstanceRef.current === instance) lastViewportRef.current = instance.getViewport();
    });
  }

  function handleFlowInit(instance: ReactFlowInstance<LineageAssetFlowNode, LineageFlowEdge>) {
    const firstMount = !hasMountedFlowRef.current;
    const traceChanged = traceKey !== lastTraceKeyRef.current;
    hasMountedFlowRef.current = true;
    flowInstanceRef.current = instance;
    setFlowInstance(instance);
    lastViewportRef.current = instance.getViewport();
    lastTraceKeyRef.current = traceKey;

    if (firstMount && !shouldAutoFitLineage(visible.entities.length)) {
      void instance.fitView({
        ...(visible.traceActive ? focusLineageFitOptions() : initialLargeGraphFitOptions()),
        nodes: visible.traceActive ? nodes : undefined,
        duration: 0
      }).then(() => {
        if (flowInstanceRef.current === instance) lastViewportRef.current = instance.getViewport();
      });
      return;
    }

    if (!firstMount && traceChanged) {
      if (visible.traceActive) {
        void instance.fitView({ ...focusLineageFitOptions(), nodes, duration: LINEAGE_FIT_DURATION }).then(() => {
          if (flowInstanceRef.current === instance) lastViewportRef.current = instance.getViewport();
        });
      } else {
        void instance.fitView({ ...visibleLineageFitOptions(), duration: LINEAGE_FIT_DURATION }).then(() => {
          if (flowInstanceRef.current === instance) lastViewportRef.current = instance.getViewport();
        });
      }
    }
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

  function selectDataflowLabel(event: MouseEvent<HTMLDivElement>) {
    const edgeId = dataflowLabelEdgeId(event.target);
    if (!edgeId) return;
    event.preventDefault();
    event.stopPropagation();
    onSelectionChange({ kind: "dataflow", id: edgeId });
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
    <div
      className="lineage-flow-host"
      onKeyDown={selectFromKeyboard}
      onClickCapture={selectDataflowLabel}
      onPointerDownCapture={(event) => {
        if (dataflowLabelEdgeId(event.target)) event.stopPropagation();
      }}
    >
      <ReactFlow
        nodes={nodes}
        edges={flow.edges}
        nodeTypes={NODE_TYPES}
        edgeTypes={EDGE_TYPES}
        fitView={shouldFitView}
        fitViewOptions={{ ...visibleLineageFitOptions() }}
        defaultViewport={lastViewportRef.current ?? { x: 36, y: 36, zoom: 0.42 }}
        proOptions={{ hideAttribution: true }}
        minZoom={0.2}
        maxZoom={1.6}
        onInit={handleFlowInit}
        onMove={(_, viewport) => {
          lastViewportRef.current = viewport;
        }}
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
        <Controls
          showInteractive={false}
          fitViewOptions={{ ...visibleLineageFitOptions(), duration: LINEAGE_FIT_DURATION }}
        >
          {visible.traceActive ? (
            <ControlButton title="Fit focused trace" aria-label="Fit focused trace" onClick={() => fitTrace()}>
              <LocateFixed size={13} aria-hidden="true" />
            </ControlButton>
          ) : null}
          <ControlButton title="Zoom to 100%" aria-label="Zoom to 100%" onClick={zoomHundredPercent}>
            <span className="lineage-zoom-actual">100%</span>
          </ControlButton>
        </Controls>
      </ReactFlow>
    </div>
  );
}

function dataflowLabelEdgeId(target: EventTarget | null) {
  if (!(target instanceof Element)) return null;
  return target.closest<HTMLElement>(".lineage-edge-label[data-edge-id]")?.dataset.edgeId ?? null;
}
