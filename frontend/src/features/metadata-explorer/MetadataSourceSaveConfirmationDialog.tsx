import { AlertTriangle, Loader2, Save } from "lucide-react";
import { OperationConfirmationDialog } from "../../shared/components/OperationConfirmationDialog";
import type { MetadataSaveConfirmation } from "./metadataSaveConfirmation";

interface MetadataSourceSaveConfirmationDialogProps {
  busy: boolean;
  confirmation: MetadataSaveConfirmation;
  onCancel: () => void;
  onConfirm: () => void;
}

export function MetadataSourceSaveConfirmationDialog({
  busy,
  confirmation,
  onCancel,
  onConfirm
}: MetadataSourceSaveConfirmationDialogProps) {
  return (
    <OperationConfirmationDialog
      busy={busy}
      confirmIcon={busy ? <Loader2 className="is-spinning" size={14} /> : <Save size={14} />}
      confirmLabel={busy ? "Saving…" : "Save"}
      description={confirmation.description}
      icon={<AlertTriangle size={18} />}
      onCancel={onCancel}
      onConfirm={onConfirm}
      tone="warning"
      title={confirmation.title}
    >
      <ul className="metadata-save-impact-list">
        {confirmation.impacts.map((impact) => (
          <li key={impact.key}>
            <strong>{impact.action === "create" ? "Create" : "Update"}</strong>
            <span>{impact.label}<small>{impact.sheets.join(", ")}</small></span>
          </li>
        ))}
      </ul>
      <div className="operation-confirmation-note tone-warning">
        <Save size={15} />
        <span><strong>Backup protection.</strong> {confirmation.detail}</span>
      </div>
    </OperationConfirmationDialog>
  );
}
