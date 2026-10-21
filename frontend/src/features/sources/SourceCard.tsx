import type {
  SourcePath,
  SourceValidationResult,
} from "../../shared/api/domainTypes";
import type { SourceSyncStatus } from "../../shared/api/contractTypes";
import { displayStorageUri } from "../../shared/lib/storage";
import { type SourceKind } from "../../shared/lib/sources";
import {
  logRefreshInterval,
  sourceBackendOperation,
  sourceDisplayPath,
  type SourceOperation,
} from "./sourceWorkspaceModel";
import { sourceComponentProvenanceLabel } from "./sourceSetupModel";
import { SourceCardPrimary } from "./SourceCardPrimary";
import { SourceCardSecondary } from "./SourceCardSecondary";
import {
  discoveredPythonFileCount,
  sourceBasename,
  sourceTypeLabel,
  validatedPythonFileCount,
} from "./sourceCardModel";
import type {
  SourceDeleteHandler,
  SourceRetryObservationHandler,
  SourceUpdateHandler,
} from "./sourcePageTypes";
import type { LogSyncActivity } from "./logSyncModel";

export type SourceCardProps = {
  item: SourcePath;
  kind: SourceKind;
  busy: boolean;
  operation?: SourceOperation;
  syncActivity?: LogSyncActivity;
  validation: SourceValidationResult | null;
  syncStatus: SourceSyncStatus | null;
  onUpdate: SourceUpdateHandler;
  onDelete: SourceDeleteHandler;
  onValidate: (id: number) => Promise<void>;
  onSync: (id: number) => Promise<void>;
  onRetryObservation: SourceRetryObservationHandler;
  timezoneName: string | null;
};

export function SourceCard({
  item,
  kind,
  busy,
  operation,
  syncActivity,
  validation,
  syncStatus,
  onUpdate,
  onDelete,
  onValidate,
  onSync,
  onRetryObservation,
  timezoneName,
}: SourceCardProps) {
  const secondaryPath = kind === "logs" && typeof item.source_config?.system_logs_uri === "string"
    ? displayStorageUri(item.source_config.system_logs_uri)
    : null;
  const executionPath = kind === "logs"
    ? typeof item.source_config?.execution_logs_uri === "string"
      ? item.source_config.execution_logs_uri
      : null
    : null;
  const primaryPathLabel = kind === "logs"
    ? item.source_config?.mode === "separate_paths" ? "Execution log base" : "Log base path"
    : kind === "sql" ? "SQL path" : "Path";
  const modeLabel =
    kind === "code" && typeof item.source_config?.artifact_type === "string"
      ? item.source_config.artifact_type.replace(/_/g, " ")
      : kind === "logs" && typeof item.source_config?.mode === "string"
        ? item.source_config.mode === "separate_paths"
          ? "separate log base paths"
          : "log base path"
        : kind === "metadata" && typeof item.source_config?.discovery_mode === "string"
          ? String(item.source_config.discovery_mode).replace(/_/g, " ")
          : null;
  const discoveryOrigin = kind === "code" && typeof item.source_config?.discovery_origin === "string"
    ? item.source_config.discovery_origin.replace(/^pyproject\./, "").replace(/\./g, " ")
    : null;
  const componentProvenance = kind === "logs"
    ? null
    : sourceComponentProvenanceLabel(kind, item.source_config);
  const discoveryMissing = kind === "code" && item.source_config?.discovery_state === "missing";
  const discoveryPythonFiles = kind === "code"
    ? validatedPythonFileCount(validation) ?? discoveredPythonFileCount(item)
    : null;
  const primaryPath = kind === "logs"
    ? displayStorageUri(executionPath || item.uri)
    : sourceDisplayPath(item, kind);
  const displayName = item.label || sourceBasename(displayStorageUri(item.uri));
  const typeLabel = sourceTypeLabel(kind, item, modeLabel);
  const scheduleInterval = logRefreshInterval(item);
  const backendOperation = sourceBackendOperation(syncStatus);
  const backendSyncing = backendOperation === "sync";
  const activeAction = operation?.action ?? backendOperation;
  const working = activeAction !== null;
  const effectiveSyncActivity = backendSyncing ? "syncing" : syncActivity;
  const refreshVerb = kind === "sql" ? "Refresh" : "Sync";
  return (
    <article
      className={`source-card${item.enabled ? "" : " is-disabled"}${working ? ` is-working is-${activeAction}` : ""}`}
      aria-busy={working}
    >
      <SourceCardPrimary
        item={item}
        kind={kind}
        displayName={displayName}
        typeLabel={typeLabel}
        discoveryMissing={discoveryMissing}
        discoveryOrigin={discoveryOrigin}
        componentProvenance={componentProvenance}
        discoveryPythonFiles={discoveryPythonFiles}
        validation={validation}
        syncStatus={syncStatus}
        busy={busy}
        working={working}
        activeAction={activeAction}
        effectiveSyncActivity={effectiveSyncActivity}
        refreshVerb={refreshVerb}
        scheduleInterval={scheduleInterval}
        timezoneName={timezoneName}
        onUpdate={onUpdate}
        onDelete={onDelete}
        onValidate={onValidate}
        onSync={onSync}
      />
      <SourceCardSecondary
        item={item}
        kind={kind}
        primaryPath={primaryPath}
        primaryPathLabel={primaryPathLabel}
        secondaryPath={secondaryPath}
        syncStatus={syncStatus}
        activeAction={activeAction}
        busy={busy}
        timezoneName={timezoneName}
        onRetryObservation={onRetryObservation}
      />
    </article>
  );
}
