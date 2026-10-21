import { AlertTriangle, RefreshCw } from "lucide-react";

import type { SourcePath } from "../../shared/api/domainTypes";
import type { SourceSyncStatus } from "../../shared/api/contractTypes";
import type { SourceKind } from "../../shared/lib/sources";
import { SourceOperationIcon, SourceOperationPill } from "./SourceOperationFeedback";
import {
  CheckBadge,
  LabeledStatus,
  StatusPill,
  SyncBadge,
} from "./SourceStatusPresentation";
import { SourcePathCopyButton } from "./SourcePathCopyButton";
import { sourceStatusDetailMessage } from "./sourceStatusModel";
import type { SourceRetryObservationHandler } from "./sourcePageTypes";
import type { SourceOperationAction } from "./sourceWorkspaceModel";

export type SourceCardSecondaryProps = {
  item: SourcePath;
  kind: SourceKind;
  primaryPath: string;
  primaryPathLabel: string;
  secondaryPath: string | null;
  syncStatus: SourceSyncStatus | null;
  activeAction: SourceOperationAction | null;
  busy: boolean;
  timezoneName: string | null;
  onRetryObservation: SourceRetryObservationHandler;
};

export function SourceCardSecondary({
  item,
  kind,
  primaryPath,
  primaryPathLabel,
  secondaryPath,
  syncStatus,
  activeAction,
  busy,
  timezoneName,
  onRetryObservation,
}: SourceCardSecondaryProps) {
  const pauseError = sourceStatusDetailMessage(syncStatus?.observation?.error);

  return (
    <>
      <div className="source-card-line source-card-line-secondary">
        <div className="source-card-path-list">
          <div className="source-card-path-inline" title={primaryPath}>
            <span>{primaryPathLabel}</span>
            <code>{primaryPath}</code>
            <SourcePathCopyButton value={primaryPath} label={primaryPathLabel} />
          </div>
          {secondaryPath ? (
            <div className="source-card-path-inline" title={secondaryPath}>
              <span>System log base</span>
              <code>{secondaryPath}</code>
              <SourcePathCopyButton value={secondaryPath} label="System log base path" />
            </div>
          ) : null}
        </div>
        <div className="source-card-status">
          {activeAction === "delete" ? (
            <LabeledStatus label="Status">
              <SourceOperationPill action={activeAction} />
            </LabeledStatus>
          ) : kind === "sql" ? (
            <LabeledStatus label="Mode">
              <StatusPill
                view={{
                  label: "Live read",
                  tone: "ok",
                  icon: "check",
                  tooltip: "SQL files are read directly from this source; Studio does not create a SQL cache.",
                }}
                timezoneName={timezoneName}
              />
            </LabeledStatus>
          ) : (
            <>
              <LabeledStatus label="Check">
                {activeAction === "retry" ? <SourceOperationPill action={activeAction} /> : <CheckBadge status={syncStatus} timezoneName={timezoneName} />}
              </LabeledStatus>
              <LabeledStatus label="Sync">
                {activeAction === "sync" ? <SourceOperationPill action={activeAction} /> : <SyncBadge status={syncStatus} timezoneName={timezoneName} />}
              </LabeledStatus>
            </>
          )}
        </div>
      </div>
      {syncStatus?.observation?.state === "paused" ? (
        <div className="source-observation-pause" role="status">
          <AlertTriangle size={16} aria-hidden="true" />
          <div className="source-observation-pause-copy">
            <strong>Source checks paused</strong>
            <span>
              {syncStatus.observation.failure_count} consecutive automatic checks failed.
              Successful Validate or Sync also restores automatic checks.
            </span>
            {pauseError ? <span className="source-observation-pause-error">Last error: {pauseError}</span> : null}
          </div>
          <button
            type="button"
            className="source-action-btn source-observation-retry"
            onClick={() => void onRetryObservation(kind, item.id)}
            disabled={busy || Boolean(activeAction) || !item.enabled}
            aria-busy={activeAction === "retry"}
          >
            {activeAction === "retry" ? <SourceOperationIcon /> : <RefreshCw size={13} />}
            <span>{activeAction === "retry" ? "Retrying..." : "Retry now"}</span>
          </button>
        </div>
      ) : null}
    </>
  );
}
