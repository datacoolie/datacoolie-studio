import {
  CheckCircle2,
  RefreshCw,
  TimerReset,
  Trash2,
  XCircle,
} from "lucide-react";
import type { SourcePath, SourceValidationResult } from "../../shared/api/domainTypes";
import type { SourceSyncStatus } from "../../shared/api/contractTypes";
import type { SourceKind } from "../../shared/lib/sources";
import {
  LOG_REFRESH_INTERVALS,
  logScheduleLabel,
  type SourceOperationAction,
} from "./sourceWorkspaceModel";
import { SourceOperationIcon, SourceOperationPill } from "./SourceOperationFeedback";
import {
  LabeledStatus,
  ValidationBadge,
} from "./SourceStatusPresentation";
import type { LogSyncActivity } from "./logSyncModel";
import type { SourceDeleteHandler, SourceUpdateHandler } from "./sourcePageTypes";

export type SourceCardPrimaryProps = {
  item: SourcePath;
  kind: SourceKind;
  displayName: string;
  typeLabel: string;
  discoveryMissing: boolean;
  discoveryOrigin: string | null;
  componentProvenance: string | null;
  discoveryPythonFiles: number | null;
  validation: SourceValidationResult | null;
  syncStatus: SourceSyncStatus | null;
  busy: boolean;
  working: boolean;
  activeAction: SourceOperationAction | null;
  effectiveSyncActivity?: LogSyncActivity;
  refreshVerb: string;
  scheduleInterval: number;
  timezoneName: string | null;
  onUpdate: SourceUpdateHandler;
  onDelete: SourceDeleteHandler;
  onValidate: (id: number) => Promise<void>;
  onSync: (id: number) => Promise<void>;
};

export function SourceCardPrimary({
  item,
  kind,
  displayName,
  typeLabel,
  discoveryMissing,
  discoveryOrigin,
  componentProvenance,
  discoveryPythonFiles,
  validation,
  syncStatus,
  busy,
  working,
  activeAction,
  effectiveSyncActivity,
  refreshVerb,
  scheduleInterval,
  timezoneName,
  onUpdate,
  onDelete,
  onValidate,
  onSync,
}: SourceCardPrimaryProps) {
  const syncAction = effectiveSyncActivity === "done"
    ? { icon: <CheckCircle2 size={13} />, label: "Done" }
    : effectiveSyncActivity === "error"
      ? { icon: <XCircle size={13} />, label: "Failed" }
      : activeAction === "sync"
        ? { icon: <SourceOperationIcon />, label: `${refreshVerb}ing` }
        : { icon: <RefreshCw size={13} />, label: refreshVerb };

  return (
    <div className="source-card-line source-card-line-primary">
      <div className="source-card-identity-row">
        <strong className="source-card-name" title={displayName}>{displayName}</strong>
        <span className="source-type-chip">{typeLabel}</span>
        {discoveryMissing ? <span className="source-type-chip" title="The last project scan could not find this artifact">Missing</span> : null}
        {discoveryOrigin ? <span className="source-type-chip" title={`Discovered from ${discoveryOrigin}`}>{discoveryOrigin}</span> : null}
        {componentProvenance ? <span className="source-type-chip" title={`Project component provenance: ${componentProvenance}`}>{componentProvenance}</span> : null}
        {discoveryPythonFiles !== null ? <span className="source-type-chip" title="Python files in this code artifact">{discoveryPythonFiles.toLocaleString()} Python files</span> : null}
      </div>
      <div className="source-card-row-actions">
        <LabeledStatus label="Validation">
          {activeAction === "validate" ? (
            <SourceOperationPill action={activeAction} />
          ) : (
            <ValidationBadge
              status={syncStatus?.validation ?? null}
              fallback={validation}
              timezoneName={timezoneName}
            />
          )}
        </LabeledStatus>
        <button
          className={`source-enabled-toggle ${item.enabled ? "is-enabled" : "is-disabled"}`}
          onClick={() => onUpdate(kind, item.id, { enabled: !item.enabled })}
          disabled={busy}
          title={item.enabled ? "Click to disable" : "Click to enable"}
        >
          <span className="source-enabled-dot" />
          {item.enabled ? "Enabled" : "Disabled"}
        </button>
        {kind === "logs" ? (
          <div className="source-log-schedule" title={item.last_scheduled_sync_at ? `Last scheduled refresh: ${item.last_scheduled_sync_at}` : "No scheduled refresh yet"}>
            <button
              type="button"
              className={`source-schedule-toggle ${item.sync_schedule_enabled ? "is-enabled" : ""}`}
              aria-pressed={item.sync_schedule_enabled}
              onClick={() => onUpdate(kind, item.id, {
                sync_schedule_enabled: !item.sync_schedule_enabled,
                sync_interval_minutes: scheduleInterval,
              })}
              disabled={busy}
            >
              <TimerReset size={12} />
              <span>Auto refresh</span>
            </button>
            <select
              aria-label={`Auto refresh interval for ${displayName}`}
              value={scheduleInterval}
              onChange={(event) => onUpdate(kind, item.id, { sync_interval_minutes: Number(event.target.value) })}
              disabled={busy}
            >
              {LOG_REFRESH_INTERVALS.map((minutes) => (
                <option key={minutes} value={minutes}>{minutes === 60 ? "1h" : `${minutes}m`}</option>
              ))}
            </select>
            <span className="source-schedule-state">{logScheduleLabel(item)}</span>
          </div>
        ) : null}
        <div className="source-card-actions">
          <button
            className="source-action-btn"
            onClick={() => onValidate(item.id)}
            disabled={busy || working}
            title={activeAction === "validate" ? "Validation in progress" : "Validate source"}
            aria-label={activeAction === "validate" ? `Validating ${displayName}` : `Validate ${displayName}`}
            aria-busy={activeAction === "validate"}
          >
            {activeAction === "validate" ? <SourceOperationIcon /> : <CheckCircle2 size={13} />}
            <span>{activeAction === "validate" ? "Validating" : "Validate"}</span>
          </button>
          <button
            className={`source-action-btn${effectiveSyncActivity ? ` is-${effectiveSyncActivity}` : ""}`}
            onClick={() => onSync(item.id)}
            disabled={busy || working}
            title={activeAction === "sync" ? `${refreshVerb} in progress` : effectiveSyncActivity === "done" ? `${refreshVerb} completed` : effectiveSyncActivity === "error" ? `${refreshVerb} failed` : kind === "sql" ? "Re-check SQL source" : "Sync cache now"}
            aria-label={activeAction === "sync" ? `${refreshVerb}ing ${displayName}` : kind === "sql" ? `Refresh ${displayName}` : `Sync cache for ${displayName}`}
            aria-busy={activeAction === "sync"}
          >
            {syncAction.icon}
            <span>{syncAction.label}</span>
          </button>
          <button
            className="source-action-btn danger"
            onClick={() => void onDelete(kind, item.id)}
            disabled={busy || working}
            title={activeAction === "delete" ? "Deletion in progress" : "Delete source"}
            aria-label={activeAction === "delete" ? `Deleting ${displayName}` : `Delete ${displayName}`}
            aria-busy={activeAction === "delete"}
          >
            {activeAction === "delete" ? <SourceOperationIcon /> : <Trash2 size={13} />}
          </button>
        </div>
      </div>
    </div>
  );
}
