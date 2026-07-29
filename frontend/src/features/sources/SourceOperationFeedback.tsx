import { LoaderCircle } from "lucide-react";

import type { SourceBatchAction } from "./sourceWorkspaceModel";

const LABELS: Record<SourceBatchAction, string> = {
  validate: "Validating",
  sync: "Syncing",
  delete: "Deleting",
};

export function sourceOperationLabel(action: SourceBatchAction) {
  return LABELS[action];
}

export function sourceOperationStatusSlot(
  action: SourceBatchAction,
): "read" | "cache" | "all" {
  if (action === "validate") return "read";
  if (action === "sync") return "cache";
  return "all";
}

export function SourceOperationIcon({ size = 13 }: { size?: number }) {
  return (
    <LoaderCircle
      size={size}
      className="source-operation-icon"
      aria-hidden="true"
    />
  );
}

export function SourceOperationPill({
  action,
}: {
  action: SourceBatchAction;
}) {
  const label = sourceOperationLabel(action);
  return (
    <span
      className={`source-operation-pill is-${action}`}
      role="status"
      aria-live="polite"
    >
      <SourceOperationIcon size={11} />
      <span>{label}…</span>
    </span>
  );
}
