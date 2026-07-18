import {
  BaseEdge,
  EdgeLabelRenderer,
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
            className={`lineage-edge-label${data.status !== "unknown" ? ` status-${data.status}` : ""} selection-${data.selectionState}`}
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
  const routeLane = props.data?.relationType === "dependency"
    ? props.data.routeLane
    : 0;
  const edgePath = dependencyEdgePath({
    sourceX: props.sourceX,
    sourceY: props.sourceY,
    targetX: props.targetX,
    targetY: props.targetY,
    routeLane
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

interface DependencyPathArgs {
  sourceX: number;
  sourceY: number;
  targetX: number;
  targetY: number;
  routeLane: number;
}

export function dependencyEdgePath({
  sourceX,
  sourceY,
  targetX,
  targetY,
  routeLane
}: DependencyPathArgs) {
  const direction = targetX >= sourceX ? 1 : -1;
  const attachmentOffset = Math.max(-18, Math.min(18, routeLane * 9));
  const availableWidth = Math.abs(targetX - sourceX);
  const channelOffset = Math.min(32 + Math.abs(routeLane) * 8, Math.max(20, availableWidth / 2));
  const channelX = sourceX + direction * channelOffset;
  const laneSourceY = sourceY + attachmentOffset;
  const laneTargetY = targetY + attachmentOffset;

  return `M ${sourceX} ${sourceY} V ${laneSourceY} H ${channelX} V ${laneTargetY} H ${targetX}`;
}
