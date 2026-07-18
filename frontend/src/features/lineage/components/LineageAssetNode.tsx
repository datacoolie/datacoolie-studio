import { memo } from "react";
import { Handle, Position, type NodeProps } from "@xyflow/react";
import { Icon } from "@iconify/react";
import type { LineageAssetFlowNode } from "../model/types";
import { assetTypeIconId, assetTypeTone, referenceTypeAssetType } from "../model/presentation";
import { LineageEntityIcon } from "./LineageEntityIcon";

export const LineageAssetNode = memo(function LineageAssetNode({
  data
}: NodeProps<LineageAssetFlowNode>) {
  const referenceObjectType = data.referenceType ? referenceTypeAssetType(data.referenceType) : null;
  return (
    <div className={`lineage-asset-node${data.entityType === "reference" ? " is-reference" : ""}`} title={data.fullIdentity}>
      <Handle className="lineage-handle" type="target" position={Position.Left} />
      <span className={`lineage-format-icon-wrap${referenceObjectType ? ` is-reference asset-tone-${assetTypeTone(referenceObjectType)}` : ""}`} title={referenceObjectType ? data.referenceType?.replace(/_/g, " ") : data.badge}>
        <LineageEntityIcon iconKind={data.iconKind} badge={data.badge} referenceType={data.referenceType} size={18} />
      </span>
      <div className="lineage-asset-copy">
        <strong>{data.locator}</strong>
        <span>{data.connection}</span>
      </div>
      {data.declarationStatus === "discovered_only" ? <span className="lineage-node-badge discovered">Discovered</span> : null}
      {data.referenceStatus ? <span className={`lineage-node-badge is-reference-status ${data.referenceStatus}`}>{data.referenceStatus.replace(/_/g, " ")}</span> : null}
      {data.issueCount ? <span className="lineage-issue-count" title={`${data.issueCount} input reference${data.issueCount === 1 ? "" : "s"} requiring attention`}>{data.issueCount}</span> : null}
      {data.assetType ? (
        <span className="lineage-node-type-icon" title={data.assetType.replace(/_/g, " ")}>
          <Icon icon={assetTypeIconId(data.assetType)} width={12} height={12} />
        </span>
      ) : null}
      <Handle className="lineage-handle" type="source" position={Position.Right} />
    </div>
  );
});
