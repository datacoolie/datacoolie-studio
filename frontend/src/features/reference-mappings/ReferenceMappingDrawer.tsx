import type { AssetInventoryItem, AssetReferenceGroupItem, ProjectReferenceMapping } from "../../shared/api/types";
import { ReferenceMappingEditor } from "./ReferenceMappingEditor";
import type { ReferenceMappingPayload } from "./referenceMappingModel";

interface ReferenceMappingDrawerProps {
  reference: AssetReferenceGroupItem;
  assets: AssetInventoryItem[];
  mappings?: ProjectReferenceMapping[];
  busy?: boolean;
  className?: string;
  onCreate: (payload: ReferenceMappingPayload) => Promise<unknown>;
  onUpdate: (mappingId: number, payload: ReferenceMappingPayload) => Promise<unknown>;
  onDelete: (mappingId: number) => Promise<unknown>;
  onRefresh: () => Promise<void>;
  onBack: () => void;
  onSearchTargets?: (query: string, connectionName: string) => Promise<AssetInventoryItem[]>;
}

export function ReferenceMappingDrawer({ className = "", ...props }: ReferenceMappingDrawerProps) {
  return (
    <div className={`reference-mapping-drawer${className ? ` ${className}` : ""}`}>
      <ReferenceMappingEditor {...props} />
    </div>
  );
}
