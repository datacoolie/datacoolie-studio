import {
  BaseEdge,
  EdgeLabelRenderer,
  getSmoothStepPath,
  type EdgeProps
} from "@xyflow/react";
import type { LineageFlowEdge } from "../model/types";
import {
  EDGE_LABEL_MAX_WIDTH,
  labelAnchorX,
  labelTransform,
  resolveLabelPlacement
} from "../model/edgeGeometry";

export function LineageDataflowEdge(props: EdgeProps<LineageFlowEdge>) {
  const {
    id,
    sourceX,
    sourceY,
    targetX,
    targetY,
    markerEnd,
    style,
    data
  } = props;
  const centerX = sourceX + (targetX - sourceX) / 2;
  const edgePath = `M ${sourceX} ${sourceY} H ${centerX} V ${targetY} H ${targetX}`;
  const sourceSegmentLength = Math.abs(centerX - sourceX);
  const targetSegmentLength = Math.abs(targetX - centerX);
  const labelPlacement = resolveLabelPlacement(
    data?.relationType === "dataflow" ? data.labelSegment : "longest",
    sourceSegmentLength,
    targetSegmentLength
  );
  const labelX = labelPlacement.alignment === "center"
    ? centerX
    : labelAnchorX(labelPlacement.alignment, sourceX, targetX);
  const segmentY = labelPlacement.alignment === "center"
    ? sourceY + (targetY - sourceY) / 2
    : labelPlacement.segment === "source" ? sourceY : targetY;

  return (
    <>
      <BaseEdge id={id} path={edgePath} markerEnd={markerEnd} style={style} />
      {data?.relationType === "dataflow" ? (
        <EdgeLabelRenderer>
          <div
            className={`lineage-edge-label${data.status !== "unknown" ? ` status-${data.status}` : ""}`}
            style={{
              maxWidth: EDGE_LABEL_MAX_WIDTH,
              transform: labelTransform(
                labelPlacement.alignment,
                labelX,
                segmentY - 5
              )
            }}
            title={data.label}
          >
            {data.label}
          </div>
        </EdgeLabelRenderer>
      ) : null}
    </>
  );
}

export function LineageDependencyEdge(props: EdgeProps<LineageFlowEdge>) {
  const [edgePath] = getSmoothStepPath({
    sourceX: props.sourceX,
    sourceY: props.sourceY,
    sourcePosition: props.sourcePosition,
    targetX: props.targetX,
    targetY: props.targetY,
    targetPosition: props.targetPosition,
    borderRadius: 6,
    offset: 22
  });
  return (
    <BaseEdge
      id={props.id}
      path={edgePath}
      markerEnd={props.markerEnd}
      style={props.style}
    />
  );
}
