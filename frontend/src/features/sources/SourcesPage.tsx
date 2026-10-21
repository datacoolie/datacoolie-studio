import {
  Code2,
  Database,
  FileCheck2,
  FolderOpen,
  HardDrive,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type {
  SourceBatchAction,
  SourceBatchEntry,
  SourceBatchResult,
  SourceOperations,
} from "./sourceWorkspaceModel";
import type {
  SourcePath,
  StorageBinding,
} from "../../shared/api/domainTypes";
import type {
  LogSyncRequest,
  SourceDeleteImpact,
  SourceImportResponse,
  SourceReadCheckResult,
  SourceSyncStatus,
} from "../../shared/api/contractTypes";
import type { DatacoolieProjectSourceImportInput } from "../../shared/api/contractTypes";
import { OperationNotification } from "../../shared/components/OperationNotification";
import { useNotificationCenter } from "../../shared/notifications/NotificationCenter";
import { toErrorMessage } from "../../shared/lib/errors";
import { sourceKey, type SourceKind } from "../../shared/lib/sources";
import {
  isSourceSyncRunning,
  sourceBackendOperation,
  sourceOperationFor,
  summarizeSourceHealth
} from "./sourceWorkspaceModel";
import { SourceOperationIcon } from "./SourceOperationFeedback";
import {
  clearLogSyncActivity,
  setLogSyncActivity,
  type LogSyncActivities,
  type LogSyncActivity,
} from "./logSyncModel";
import { DatacoolieProjectForm } from "./DatacoolieProjectForm";
import { LogSourceForm } from "./LogSourceForm";
import { SourceGroup } from "./SourceGroup";
import { SourceBulkActions } from "./SourceBulkActions";
import { LogSyncDialog, SourceDeleteDialog } from "./SourceDialogs";
import { sourceStatusDetailMessage } from "./sourceStatusModel";
import {
  batchNotice,
  importNotice,
  logSyncModeLabel,
  logSyncNotice,
  operationFailureNotice,
  toBatchEntries,
  type SourceOperationNotice,
} from "./sourceNotices";
import type {
  SourceDeletePrompt,
  SourceEntry,
  SourceLogSyncPrompt,
} from "./sourcePageTypes";

export type { SourceKind } from "../../shared/lib/sources";

interface SourcesPageProps {
  metadataSources: SourcePath[];
  sqlSources: SourcePath[];
  logBasePaths: SourcePath[];
  codeArtifacts: SourcePath[];
  busy: boolean;
  selectedEnvironmentId: number | null;
  onImportMetadataSources: (uri: string, label?: string, storage?: StorageBinding) => Promise<SourceImportResponse | null>;
  onAddSqlSource: (uri: string, label?: string, storage?: StorageBinding) => Promise<void>;
  onImportDatacoolieProjectSources: (payload: DatacoolieProjectSourceImportInput) => Promise<SourceImportResponse | null>;
  onAddLogBasePath: (uri: string, label?: string, sourceConfig?: Record<string, unknown>, storage?: StorageBinding) => Promise<void>;
  onAddCodeArtifact: (uri: string, label?: string, sourceConfig?: Record<string, unknown>, storage?: StorageBinding) => Promise<void>;
  onUpdateSource: (
    kind: SourceKind,
    id: number,
    payload: {
      uri?: string;
      label?: string | null;
      enabled?: boolean;
      source_config?: Record<string, unknown>;
      storage?: StorageBinding;
      sync_schedule_enabled?: boolean;
      sync_interval_minutes?: number | null;
    }
  ) => Promise<void>;
  onDeleteSource: (kind: SourceKind, id: number) => Promise<void>;
  onGetDeleteImpact: (kind: SourceKind, id: number) => Promise<SourceDeleteImpact>;
  onValidateSource: (kind: SourceKind, id: number) => Promise<SourceReadCheckResult>;
  onSyncSource: (kind: SourceKind, id: number, logSyncRequest?: LogSyncRequest) => Promise<SourceSyncStatus>;
  onRetrySourceObservation: (kind: SourceKind, id: number) => Promise<SourceSyncStatus>;
  onRunSourceBatch: (action: SourceBatchAction, entries: SourceBatchEntry[], logSyncRequest?: LogSyncRequest) => Promise<SourceBatchResult>;
  syncStatuses: Record<string, SourceSyncStatus>;
  sourceOperations: SourceOperations;
  timezoneName: string | null;
}

type SectionBulkAction = SourceBatchAction;
type SectionBulkScope = "configurations" | "logs";

export function SourcesPage({
  metadataSources,
  sqlSources,
  logBasePaths,
  codeArtifacts,
  busy,
  selectedEnvironmentId,
  onImportMetadataSources,
  onAddSqlSource,
  onImportDatacoolieProjectSources,
  onAddLogBasePath,
  onAddCodeArtifact,
  onUpdateSource,
  onGetDeleteImpact,
  onValidateSource,
  onSyncSource,
  onRetrySourceObservation,
  onRunSourceBatch,
  syncStatuses,
  sourceOperations,
  timezoneName,
}: SourcesPageProps) {
  const { publish } = useNotificationCenter();
  const [notice, setNoticeState] = useState<SourceOperationNotice>(null);
  const setNotice = useCallback((next: SourceOperationNotice) => {
    setNoticeState(next);
    if (!next) return;
    publish({
      tone: next.tone,
      title: next.title,
      location: "Sources",
      message: next.detail,
      errors: next.errors,
    });
  }, [publish]);
  const validateSourceWithNotice = useCallback(async (kind: SourceKind, id: number) => {
    const result = await onValidateSource(kind, id);
    if (result.status === "error" || result.status === "warning") {
      setNotice({
        tone: result.status === "error" ? "error" : "warning",
        title: result.status === "error" ? "Source validation failed" : "Source validation completed with warnings",
        detail: result.message || "Review the source validation result.",
        errors: result.errors.map((error) => sourceStatusDetailMessage(error) ?? JSON.stringify(error)),
      });
    }
    return result;
  }, [onValidateSource, setNotice]);
  const [deletePrompt, setDeletePrompt] = useState<SourceDeletePrompt | null>(null);
  const [logSyncPrompt, setLogSyncPrompt] = useState<SourceLogSyncPrompt | null>(null);
  const [logSyncActivities, setLogSyncActivities] = useState<LogSyncActivities>({});
  const logSyncTimers = useRef<Record<number, number>>({});
  const selectedEnvironmentIdRef = useRef(selectedEnvironmentId);
  selectedEnvironmentIdRef.current = selectedEnvironmentId;
  const [loadingDeleteImpact, setLoadingDeleteImpact] = useState(false);
  const configurationSources = useMemo(
    () => [
      ...metadataSources.map((source) => ({ source, kind: "metadata" as const })),
      ...sqlSources.map((source) => ({ source, kind: "sql" as const })),
      ...codeArtifacts.map((source) => ({ source, kind: "code" as const }))
    ],
    [metadataSources, sqlSources, codeArtifacts]
  );
  const logSources = useMemo(() => logBasePaths.map((source) => ({ source, kind: "logs" as const })), [logBasePaths]);
  const allSources = useMemo(
    () => [...configurationSources, ...logSources],
    [configurationSources, logSources]
  );
  const sourceHealth = useMemo(() => summarizeSourceHealth(allSources, syncStatuses), [allSources, syncStatuses]);
  const disabled = !selectedEnvironmentId;

  useEffect(() => {
    if (!deletePrompt) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setDeletePrompt(null);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [deletePrompt]);

  useEffect(() => {
    setNotice(null);
    setLogSyncPrompt(null);
    setLogSyncActivities({});
    Object.values(logSyncTimers.current).forEach((timer) => window.clearTimeout(timer));
    logSyncTimers.current = {};
  }, [selectedEnvironmentId]);

  useEffect(() => () => {
    Object.values(logSyncTimers.current).forEach((timer) => window.clearTimeout(timer));
  }, []);

  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(null), 7_500);
    return () => window.clearTimeout(timer);
  }, [notice]);

  async function requestDelete(entries: SourceEntry[]) {
    if (!entries.length) return;
    setLoadingDeleteImpact(true);
    try {
      const impacts = await Promise.all(entries.map((entry) => onGetDeleteImpact(entry.kind, entry.source.id)));
      setDeletePrompt({ entries, impacts });
    } finally {
      setLoadingDeleteImpact(false);
    }
  }

  async function requestSourceDelete(kind: SourceKind, id: number) {
    const entry = allSources.find((item) => item.kind === kind && item.source.id === id);
    if (entry) await requestDelete([entry]);
  }

  async function confirmDelete() {
    if (!deletePrompt) return;
    const entries = deletePrompt.entries;
    setDeletePrompt(null);
    const result = await onRunSourceBatch("delete", toBatchEntries(entries));
    setNotice(batchNotice("delete", result));
  }

  async function runSectionValidate(_scope: SectionBulkScope, entries: SourceEntry[]) {
    if (!entries.length) return;
    const result = await onRunSourceBatch("validate", toBatchEntries(entries));
    setNotice(batchNotice("validate", result));
  }

  async function runSectionSync(scope: SectionBulkScope, entries: SourceEntry[]) {
    if (!entries.length) return;
    if (scope === "logs") {
      setLogSyncPrompt({ entries });
      return;
    }
    const result = await onRunSourceBatch("sync", toBatchEntries(entries));
    setNotice(batchNotice("sync", result));
  }

  function setBackgroundLogSyncState(sourceIds: number[], activity: LogSyncActivity) {
    sourceIds.forEach((sourceId) => {
      const existingTimer = logSyncTimers.current[sourceId];
      if (existingTimer) window.clearTimeout(existingTimer);
    });
    setLogSyncActivities((current) => setLogSyncActivity(current, sourceIds, activity));
  }

  function finishBackgroundLogSync(sourceIds: number[], activity: Exclude<LogSyncActivity, "syncing">) {
    setBackgroundLogSyncState(sourceIds, activity);
    sourceIds.forEach((sourceId) => {
      logSyncTimers.current[sourceId] = window.setTimeout(() => {
        setLogSyncActivities((current) => clearLogSyncActivity(current, [sourceId]));
        delete logSyncTimers.current[sourceId];
      }, 4_000);
    });
  }

  function confirmLogSync(request: LogSyncRequest) {
    if (!logSyncPrompt) return;
    const entries = logSyncPrompt.entries;
    const sourceIds = entries.map((entry) => entry.source.id);
    setLogSyncPrompt(null);
    void runLogSyncInBackground(entries, sourceIds, request, selectedEnvironmentId);
  }

  async function runLogSyncInBackground(
    entries: SourceEntry[],
    sourceIds: number[],
    request: LogSyncRequest,
    environmentId: number | null
  ) {
    const isCurrentEnvironment = () => selectedEnvironmentIdRef.current === environmentId;
    try {
      if (entries.length === 1) {
        const entry = entries[0];
        const result = await onSyncSource("logs", entry.source.id, request);
        if (!isCurrentEnvironment()) return;
        setNotice(logSyncNotice(result, request));
        finishBackgroundLogSync(sourceIds, result.status === "error" ? "error" : "done");
      } else {
        const result = await onRunSourceBatch("sync", toBatchEntries(entries), request);
        if (!isCurrentEnvironment()) return;
        setNotice(batchNotice("sync", result, logSyncModeLabel(request)));
        finishBackgroundLogSync(sourceIds, result.failed ? "error" : "done");
      }
    } catch (error) {
      if (!isCurrentEnvironment()) return;
      const message = toErrorMessage(error);
      finishBackgroundLogSync(sourceIds, "error");
      setNotice(operationFailureNotice("Log sync failed", message));
    }
  }

  async function runSectionDelete(_scope: SectionBulkScope, entries: SourceEntry[]) {
    if (!entries.length) return;
    await requestDelete(entries);
  }

  function activeSectionAction(entries: SourceEntry[]): SectionBulkAction | null {
    const actions = new Set<SectionBulkAction>();
    for (const entry of entries) {
      const operation = sourceOperationFor(
        sourceOperations,
        selectedEnvironmentId,
        entry.kind,
        entry.source.id,
      );
      if (operation && operation.action !== "retry") {
        actions.add(operation.action);
      }
      const backendStatus = syncStatuses[sourceKey(entry.kind, entry.source.id)];
      if (isSourceSyncRunning(backendStatus)) {
        actions.add(sourceBackendOperation(backendStatus) ?? "sync");
      }
    }
    return actions.size === 1 ? [...actions][0] : null;
  }

  function sectionHasWorkingSource(entries: SourceEntry[]) {
    return entries.some((entry) =>
      Boolean(sourceOperationFor(
        sourceOperations,
        selectedEnvironmentId,
        entry.kind,
        entry.source.id,
      ))
      || isSourceSyncRunning(syncStatuses[sourceKey(entry.kind, entry.source.id)])
    );
  }

  const configurationAction = activeSectionAction(configurationSources);
  const logsAction = activeSectionAction(logSources);
  const configurationWorking = sectionHasWorkingSource(configurationSources);
  const logsWorking = sectionHasWorkingSource(logSources);

  return (
    <div className="sources-page">
      <section className="sources-workspace-bar table-panel">
        <div className="sources-workspace-summary">
          <HardDrive size={16} />
          <div>
            <strong>{allSources.length} configured sources</strong>
            <span>{metadataSources.length} metadata · {sqlSources.length} SQL · {codeArtifacts.length} code · {logSources.length} logs</span>
          </div>
        </div>
        <div className="sources-health-line" aria-label={`${sourceHealth.enabled} enabled, ${sourceHealth.pendingSync} pending sync, ${sourceHealth.issues} with issues`}>
          <span><strong>{sourceHealth.enabled}</strong> enabled</span>
          <span><strong>{sourceHealth.pendingSync}</strong> pending sync</span>
          <span><strong>{sourceHealth.issues}</strong> with issues</span>
        </div>
      </section>

      <div className="view-stack sources-stack">
        <section className="table-panel source-panel source-project-panel">
          <div className="panel-toolbar source-panel-toolbar">
            <div className="section-heading">
              <FolderOpen size={18} />
              <div>
                <h2>Metadata, SQL &amp; code</h2>
                <span>Metadata, SQL roots, and source code.</span>
              </div>
            </div>
            <div className="source-section-toolbar">
              <div className="source-section-counts">
                <span>{configurationSources.length} configured</span>
                <strong>{metadataSources.length} metadata · {sqlSources.length} SQL · {codeArtifacts.length} code</strong>
              </div>
              <SourceBulkActions
                label="Metadata, SQL and code"
                total={configurationSources.length}
                busy={busy || configurationWorking}
                active={configurationAction}
                syncLabel="Sync / refresh all"
                onValidate={() => runSectionValidate("configurations", configurationSources)}
                onSync={() => runSectionSync("configurations", configurationSources)}
                onDelete={() => runSectionDelete("configurations", configurationSources)}
              />
            </div>
          </div>
          <div className="source-section-layout">
          <aside className="source-config-sidebar" aria-label="Add metadata, SQL, or source code">
            <DatacoolieProjectForm
              busy={busy}
              disabled={disabled}
              onImportMetadataSources={async (uri, label, storage) => {
                try {
                  const result = await onImportMetadataSources(uri, label, storage);
                  setNotice(importNotice(result, "Metadata scan complete"));
                  return Boolean(result && (!result.errors.length || result.created.length || result.existing.length));
                } catch (error) {
                  setNotice(operationFailureNotice("Metadata scan could not be completed", toErrorMessage(error)));
                  return false;
                }
              }}
              onImportDatacoolieProjectSources={async (payload) => {
                try {
                  const result = await onImportDatacoolieProjectSources(payload);
                  setNotice(importNotice(result, "Project scan complete"));
                  return Boolean(result && (!result.errors.length || result.created.length || result.existing.length));
                } catch (error) {
                  setNotice(operationFailureNotice("Project scan could not be completed", toErrorMessage(error)));
                  return false;
                }
              }}
              onAddSqlSource={async (uri, label, storage) => {
                try {
                  await onAddSqlSource(uri, label, storage);
                  setNotice({ tone: "success", title: "SQL source added", detail: "1 SQL source configured.", errors: [] });
                  return true;
                } catch (error) {
                  setNotice(operationFailureNotice("SQL source was not added", toErrorMessage(error)));
                  return false;
                }
              }}
              onAddCodeArtifact={async (uri, label, sourceConfig, storage) => {
                try {
                  await onAddCodeArtifact(uri, label, sourceConfig, storage);
                  setNotice({ tone: "success", title: "Source code added", detail: "1 code artifact configured.", errors: [] });
                  return true;
                } catch (error) {
                  setNotice(operationFailureNotice("Source code was not added", toErrorMessage(error)));
                  return false;
                }
              }}
            />
          </aside>
          <div className="source-project-lists">
              <SourceGroup
                title="Metadata sources"
                description="Files discovered from metadata path or project scan."
                icon={<Database size={16} />}
                kind="metadata"
                items={metadataSources}
                emptyTitle="No metadata source yet"
                emptyDetail="Scan a project or add a metadata file/folder."
                busy={busy}
                onUpdate={onUpdateSource}
                onDelete={requestSourceDelete}
                onValidate={validateSourceWithNotice}
                onSync={onSyncSource}
                onRetryObservation={onRetrySourceObservation}
                syncStatuses={syncStatuses}
                sourceOperations={sourceOperations}
                environmentId={selectedEnvironmentId}
                timezoneName={timezoneName}
              />
              <SourceGroup
                title="SQL sources"
                description="SQL files and roots used by metadata query references."
                icon={<Database size={16} />}
                kind="sql"
                items={sqlSources}
                emptyTitle="No SQL source yet"
                emptyDetail="Add a SQL file or folder, or scan a DataCoolie project."
                busy={busy}
                onUpdate={onUpdateSource}
                onDelete={requestSourceDelete}
                onValidate={validateSourceWithNotice}
                onSync={onSyncSource}
                onRetryObservation={onRetrySourceObservation}
                syncStatuses={syncStatuses}
                sourceOperations={sourceOperations}
                environmentId={selectedEnvironmentId}
                timezoneName={timezoneName}
              />
              <SourceGroup
                title="Source code"
                description="Python files used by function and dependency analysis."
                icon={<Code2 size={16} />}
                kind="code"
                items={codeArtifacts}
                emptyTitle="No source code artifact yet"
                emptyDetail="Project scan uses the functions folder by default."
                busy={busy}
                onUpdate={onUpdateSource}
                onDelete={requestSourceDelete}
                onValidate={validateSourceWithNotice}
                onSync={onSyncSource}
                onRetryObservation={onRetrySourceObservation}
                syncStatuses={syncStatuses}
                sourceOperations={sourceOperations}
                environmentId={selectedEnvironmentId}
                timezoneName={timezoneName}
              />
          </div>
          </div>
        </section>

        <section className="table-panel source-panel source-logs-panel">
          <div className="panel-toolbar source-panel-toolbar">
            <div className="section-heading">
              <HardDrive size={18} />
              <div>
                <h2>Logs</h2>
                <span>Execution and system logs.</span>
              </div>
            </div>
            <div className="source-section-toolbar">
              <div className="source-section-counts">
                <span>{logBasePaths.length} configured</span>
                <strong>{logBasePaths.filter((item) => item.enabled).length}/{logBasePaths.length} enabled</strong>
              </div>
              <SourceBulkActions
                label="Logs"
                total={logSources.length}
                busy={busy || logsWorking}
                active={logsAction}
                onValidate={() => runSectionValidate("logs", logSources)}
                onSync={() => runSectionSync("logs", logSources)}
                onDelete={() => runSectionDelete("logs", logSources)}
              />
            </div>
          </div>
          <div className="source-section-layout">
          <aside className="source-config-sidebar" aria-label="Add log source">
            <LogSourceForm
              busy={busy}
              disabled={disabled}
              onAddLogBasePath={async (uri, label, sourceConfig, storage) => {
                try {
                  await onAddLogBasePath(uri, label, sourceConfig, storage);
                  setNotice({ tone: "success", title: "Log source added", detail: "1 log source configured.", errors: [] });
                  return true;
                } catch (error) {
                  setNotice(operationFailureNotice("Log source was not added", toErrorMessage(error)));
                  return false;
                }
              }}
            />
          </aside>
          <div className="source-logs-inventory">
            <SourceGroup
              title="Log sources"
              description="Log base path, or separate execution/system log base paths."
              icon={<FileCheck2 size={16} />}
              kind="logs"
              items={logBasePaths}
              emptyTitle="No log source yet"
              emptyDetail="Add a log base folder or separate execution/system log base folders."
              busy={busy}
              onUpdate={onUpdateSource}
              onDelete={requestSourceDelete}
              onValidate={validateSourceWithNotice}
              onSync={onSyncSource}
              onRetryObservation={onRetrySourceObservation}
              onRequestLogSync={(id) => {
                const source = logBasePaths.find((item) => item.id === id);
                if (source) setLogSyncPrompt({ entries: [{ source, kind: "logs" }] });
              }}
              syncStatuses={syncStatuses}
              sourceOperations={sourceOperations}
              environmentId={selectedEnvironmentId}
              timezoneName={timezoneName}
              externalSyncActivities={logSyncActivities}
            />
          </div>
          </div>
        </section>
      </div>
      {deletePrompt ? (
        <SourceDeleteDialog
          prompt={deletePrompt}
          onCancel={() => setDeletePrompt(null)}
          onConfirm={() => void confirmDelete()}
        />
      ) : null}
      {logSyncPrompt ? (
        <LogSyncDialog
          sourceCount={logSyncPrompt.entries.length}
          onCancel={() => setLogSyncPrompt(null)}
          onConfirm={confirmLogSync}
        />
      ) : null}
      {loadingDeleteImpact ? (
        <div className="source-delete-impact-loading" role="status">
          <SourceOperationIcon />
          <span>Reviewing cached data…</span>
        </div>
      ) : null}
      {notice ? <OperationNotification notice={notice} onClose={() => setNotice(null)} /> : null}
    </div>
  );
}
