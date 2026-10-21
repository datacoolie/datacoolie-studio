import {
  AlertTriangle,
  CalendarRange,
  FileCheck2,
  RefreshCw,
  Trash2,
} from "lucide-react";
import { useState } from "react";

import type { LogSyncRequest } from "../../shared/api/contractTypes";
import { OperationConfirmationDialog } from "../../shared/components/OperationConfirmationDialog";
import { aggregateDeleteImpacts } from "./sourceWorkspaceModel";
import {
  DEFAULT_LOG_SYNC_DRAFT,
  toLogSyncRequest,
  validateLogSyncDraft,
  type LogSyncDraft,
} from "./logSyncModel";
import type { SourceDeletePrompt } from "./sourcePageTypes";
import type { SourceKind } from "../../shared/lib/sources";

export function LogSyncDialog({
  sourceCount,
  onCancel,
  onConfirm,
}: {
  sourceCount: number;
  onCancel: () => void;
  onConfirm: (request: LogSyncRequest) => void;
}) {
  const [draft, setDraft] = useState<LogSyncDraft>(DEFAULT_LOG_SYNC_DRAFT);
  const validationError = validateLogSyncDraft(draft);
  const sourceLabel = sourceCount === 1 ? "this log source" : `${sourceCount} log sources`;

  return (
    <OperationConfirmationDialog
      confirmDisabled={Boolean(validationError)}
      confirmIcon={<RefreshCw size={14} />}
      confirmLabel={sourceCount === 1 ? "Sync logs" : `Sync ${sourceCount} sources`}
      description={`Choose how Datacoolie Studio discovers files for ${sourceLabel}.`}
      icon={<CalendarRange size={18} />}
      onCancel={onCancel}
      onConfirm={() => onConfirm(toLogSyncRequest(draft))}
      title="Sync log data"
    >
      <div className="source-log-sync-options" role="radiogroup" aria-label="Log sync mode">
        <label className={draft.mode === "incremental" ? "is-selected" : ""}>
          <input
            type="radio"
            name="log-sync-mode"
            checked={draft.mode === "incremental"}
            onChange={() => setDraft((current) => ({ ...current, mode: "incremental" }))}
          />
          <span>
            <strong>Incremental</strong>
            <small>Load new or updated files from the current partition checkpoint.</small>
          </span>
        </label>
        <label className={draft.mode === "incremental_with_lookback" ? "is-selected" : ""}>
          <input
            type="radio"
            name="log-sync-mode"
            checked={draft.mode === "incremental_with_lookback"}
            onChange={() => setDraft((current) => ({ ...current, mode: "incremental_with_lookback" }))}
          />
          <span>
            <strong>Incremental + lookback</strong>
            <small>Recheck an older date range, then continue from the current checkpoint.</small>
          </span>
        </label>
      </div>
      {draft.mode === "incremental_with_lookback" ? (
        <div className="source-log-sync-range">
          <label>
            From
            <input
              type="date"
              value={draft.fromPartition}
              onChange={(event) => setDraft((current) => ({ ...current, fromPartition: event.target.value }))}
            />
          </label>
          <label>
            To
            <input
              type="date"
              value={draft.toPartition}
              onChange={(event) => setDraft((current) => ({ ...current, toPartition: event.target.value }))}
            />
          </label>
          {validationError ? <p className="source-log-sync-error" role="alert">{validationError}</p> : null}
        </div>
      ) : null}
    </OperationConfirmationDialog>
  );
}

export function SourceDeleteDialog({
  prompt,
  onCancel,
  onConfirm,
}: {
  prompt: SourceDeletePrompt;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const impacts = aggregateDeleteImpacts(prompt.impacts);
  const count = prompt.entries.length;
  const singleKind = prompt.entries.every((entry) => entry.kind === prompt.entries[0]?.kind)
    ? prompt.entries[0]?.kind
    : null;
  const sourceLabel = count === 1 && singleKind
    ? `${sourceKindLabel(singleKind)} source`
    : count > 1 && singleKind
      ? `${count} ${sourceKindLabel(singleKind)} sources`
      : `${count} selected sources`;

  return (
    <OperationConfirmationDialog
      confirmIcon={<Trash2 size={14} />}
      confirmLabel={count === 1 ? "Delete source" : `Delete ${count} sources`}
      description={`This removes ${count === 1 ? "its" : "their"} configuration and the related Studio-owned data listed below.`}
      icon={<AlertTriangle size={18} />}
      onCancel={onCancel}
      onConfirm={onConfirm}
      tone="danger"
      title={`Delete ${sourceLabel}?`}
    >
      {impacts.length ? (
        <ul>
          {impacts.map((item) => (
            <li key={`${item.kind}:${item.label}`} className={item.severity === "warning" ? "is-warning" : ""}>
              <strong>{item.count.toLocaleString()}</strong>
              <span>{item.label}</span>
            </li>
          ))}
        </ul>
      ) : <p className="source-delete-empty-impact">No cached Studio data is currently associated with the selected source.</p>}
      <div className="operation-confirmation-note tone-success">
        <FileCheck2 size={15} />
        <span><strong>Original source files will not be deleted.</strong> Only configuration and data stored by Datacoolie Studio are removed.</span>
      </div>
    </OperationConfirmationDialog>
  );
}

function sourceKindLabel(kind: SourceKind) {
  if (kind === "metadata") return "metadata";
  if (kind === "sql") return "SQL";
  if (kind === "code") return "code";
  return "log";
}
