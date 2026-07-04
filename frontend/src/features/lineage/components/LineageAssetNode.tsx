import { memo } from "react";
import { Handle, Position, type NodeProps } from "@xyflow/react";
import type { LineageAssetFlowNode } from "../model/types";
import { LineageFormatIcon } from "./LineageFormatIcon";

export const LineageAssetNode = memo(function LineageAssetNode({
  data
}: NodeProps<LineageAssetFlowNode>) {
  return (
    <div className={`lineage-asset-node${data.entityType === "reference" ? " is-reference" : ""}`} title={data.fullIdentity}>
      <Handle className="lineage-handle" type="target" position={Position.Left} />
      <span className="lineage-format-icon-wrap" title={data.badge}>
        <LineageFormatIcon kind={data.iconKind} label={data.badge} size={18} />
      </span>
      <div className="lineage-asset-copy">
        <strong>{data.locator}</strong>
        <span>{data.connection}</span>
      </div>
      {data.declarationStatus === "discovered_only" ? <span className="lineage-node-badge discovered">Discovered</span> : null}
      {data.referenceStatus ? <span className={`lineage-node-badge ${data.referenceStatus}`}>{data.referenceStatus}</span> : null}
      {data.issueCount ? <span className="lineage-issue-count" title="Unresolved input dependencies">{data.issueCount}</span> : null}
      <Handle className="lineage-handle" type="source" position={Position.Right} />
    </div>
  );
});
