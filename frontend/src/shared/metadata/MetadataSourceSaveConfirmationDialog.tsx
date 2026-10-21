import { AlertTriangle, Loader2, Save } from "lucide-react";
import { OperationConfirmationDialog } from "../components/OperationConfirmationDialog";
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
      confirmLabel={confirmation.refreshOnly ? (busy ? "Refreshing…" : "Refresh workspace") : (busy ? "Saving…" : "Save to source")}
      description={confirmation.description}
      icon={<AlertTriangle size={18} />}
      onCancel={onCancel}
      onConfirm={onConfirm}
      tone="warning"
      title={confirmation.title}
    >
      {!confirmation.refreshOnly ? <><div className="metadata-save-summary" aria-label="Source mutation summary">
        <span>Metadata files: {formatImpactSummary(confirmation.impacts)}</span>
        <span>SQL files: {formatImpactSummary(confirmation.sqlImpacts)}</span>
        <span>Source deletions: 0</span>
      </div>
      <ul className="metadata-save-impact-list">
        {confirmation.impacts.map((impact) => (
          <li key={impact.key}>
            <strong>{impactStatusLabel(impact.status, impact.action)}</strong>
            <span>{impact.label}<small>{impact.sheets.join(", ")}</small></span>
          </li>
        ))}
        {confirmation.sqlImpacts.map((impact) => (
          <li key={impact.key}>
            <strong>{impactStatusLabel(impact.status, impact.action)}</strong>
            <span>
              {impact.label}
              <small>
                {impact.sheets.join(", ")}
                {impact.status === "applied" ? " · already published; retry verifies content" : ""}
                {impact.status === "conflict" ? " · changed outside Studio" : ""}
                {impact.status === "failed" ? " · retry remains available" : ""}
              </small>
            </span>
          </li>
        ))}
      </ul>
      </> : null}
      <div className="operation-confirmation-note tone-warning">
        <Save size={15} />
        <span>{!confirmation.refreshOnly ? <strong>Backup protection. </strong> : null}{confirmation.detail}</span>
      </div>
      {confirmation.safetyNotice ? (
        <div className="operation-confirmation-note tone-warning">
          <AlertTriangle size={15} />
          <span><strong>Verified write.</strong> {confirmation.safetyNotice}</span>
        </div>
      ) : null}
    </OperationConfirmationDialog>
  );
}

function formatImpactSummary(
  impacts: MetadataSaveConfirmation["impacts"],
) {
  const counts = {
    create: impacts.filter((impact) => impact.action === "create").length,
    update: impacts.filter((impact) => impact.action === "update").length,
  };
  const parts = [
    counts.create ? `${counts.create} create${counts.create === 1 ? "" : "s"}` : "",
    counts.update ? `${counts.update} update${counts.update === 1 ? "" : "s"}` : "",
  ].filter(Boolean);
  return parts.join(", ") || "0 changes";
}

function impactStatusLabel(
  status: MetadataSaveConfirmation["impacts"][number]["status"],
  action: MetadataSaveConfirmation["impacts"][number]["action"],
) {
  if (status === "applied") return "Already saved";
  if (status === "conflict") return "Conflict";
  if (status === "failed") return "Retry";
  return action === "create" ? "Create" : "Update";
}
