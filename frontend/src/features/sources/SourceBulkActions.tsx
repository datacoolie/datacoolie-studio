import { CheckCircle2, RefreshCw, Trash2 } from "lucide-react";

import type { SourceBatchAction } from "./sourceWorkspaceModel";
import { SourceOperationIcon, sourceOperationLabel } from "./SourceOperationFeedback";

export function SourceBulkActions({
  label,
  syncLabel = "Sync all",
  total,
  busy,
  active,
  onValidate,
  onSync,
  onDelete,
}: {
  label: string;
  syncLabel?: string;
  total: number;
  busy: boolean;
  active: SourceBatchAction | null;
  onValidate: () => Promise<void>;
  onSync: () => Promise<void>;
  onDelete: () => Promise<void>;
}) {
  return (
    <div className="source-bulk-actions source-section-bulk-actions" aria-label={`${label} bulk actions`}>
      <button type="button" onClick={() => void onValidate()} disabled={busy || !total} aria-busy={active === "validate"} title={`Validate all ${label.toLowerCase()} sources`}>
        {active === "validate" ? <SourceOperationIcon /> : <CheckCircle2 size={13} />}
        <span>{active === "validate" ? sourceOperationLabel(active) : "Validate all"}</span>
      </button>
      <button type="button" className="source-bulk-primary" onClick={onSync} disabled={busy || !total} aria-busy={active === "sync"} title={`${syncLabel} ${label.toLowerCase()} sources`}>
        {active === "sync" ? <SourceOperationIcon /> : <RefreshCw size={13} />}
        <span>{active === "sync" ? sourceOperationLabel(active) : syncLabel}</span>
      </button>
      <button type="button" className="danger" onClick={() => void onDelete()} disabled={busy || !total} aria-busy={active === "delete"} title={`Delete all ${label.toLowerCase()} sources`}>
        {active === "delete" ? <SourceOperationIcon /> : <Trash2 size={13} />}
        <span>{active === "delete" ? sourceOperationLabel(active) : "Delete all"}</span>
      </button>
    </div>
  );
}
