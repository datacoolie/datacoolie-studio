import {
  AlertTriangle,
  CalendarRange,
  CheckCircle2,
  Clock,
  Code2,
  Database,
  FileCheck2,
  FolderOpen,
  HardDrive,
  LoaderCircle,
  Plus,
  RefreshCw,
  Search,
  Settings2,
  TimerReset,
  Trash2,
  XCircle
} from "lucide-react";
import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import type {
  SourceBatchAction,
  SourceBatchEntry,
  SourceBatchResult,
} from "./sourceWorkspaceModel";
import type { LogSyncRequest, SourceDeleteImpact, SourceImportResponse, SourcePath, SourceReadCheckResult, SourceSyncStatus } from "../../shared/api/domainTypes";
import { OperationConfirmationDialog } from "../../shared/components/OperationConfirmationDialog";
import { OperationNotification, type OperationNotice } from "../../shared/components/OperationNotification";
import { toErrorMessage } from "../../shared/lib/errors";
import { sourceKey, type SourceKind } from "../../shared/lib/sources";
import {
  aggregateDeleteImpacts,
  LOG_REFRESH_INTERVALS,
  logRefreshInterval,
  logScheduleLabel,
  summarizeSourceHealth
} from "./sourceWorkspaceModel";
import {
  clearLogSyncActivity,
  DEFAULT_LOG_SYNC_DRAFT,
  setLogSyncActivity,
  toLogSyncRequest,
  validateLogSyncDraft,
  type LogSyncActivities,
  type LogSyncActivity,
  type LogSyncDraft
} from "./logSyncModel";

export type { SourceKind } from "../../shared/lib/sources";

interface SourcesPageProps {
  metadataSources: SourcePath[];
  logPaths: SourcePath[];
  codeArtifacts: SourcePath[];
  busy: boolean;
  selectedEnvironmentId: number | null;
  onImportMetadataSources: (uri: string, label?: string) => Promise<SourceImportResponse | null>;
  onImportDatacoolieProjectSources: (payload: {
    project_uri: string;
    metadata_subpath?: string;
    code_subpath?: string;
    metadata_uri?: string | null;
    code_uri?: string | null;
    include_metadata?: boolean;
    include_code?: boolean;
  }) => Promise<SourceImportResponse | null>;
  onAddLogPath: (uri: string, label?: string, sourceConfig?: Record<string, unknown>) => Promise<void>;
  onAddCodeArtifact: (uri: string, label?: string, sourceConfig?: Record<string, unknown>) => Promise<void>;
  onUpdateSource: (
    kind: SourceKind,
    id: number,
    payload: {
      uri?: string;
      label?: string | null;
      enabled?: boolean;
      source_config?: Record<string, unknown>;
      sync_schedule_enabled?: boolean;
      sync_interval_minutes?: number | null;
    }
  ) => Promise<void>;
  onDeleteSource: (kind: SourceKind, id: number) => Promise<void>;
  onGetDeleteImpact: (kind: SourceKind, id: number) => Promise<SourceDeleteImpact>;
  onValidateSource: (kind: SourceKind, id: number) => Promise<SourceReadCheckResult>;
  onSyncSource: (kind: SourceKind, id: number, logSyncRequest?: LogSyncRequest) => Promise<SourceSyncStatus>;
  onRunSourceBatch: (action: SourceBatchAction, entries: SourceBatchEntry[], logSyncRequest?: LogSyncRequest) => Promise<SourceBatchResult>;
  onRefreshSources: () => Promise<void>;
  syncStatuses: Record<string, SourceSyncStatus>;
}

type SourceOperationNotice = OperationNotice | null;

type SourceEntry = {
  source: SourcePath;
  kind: SourceKind;
};

type SectionBulkAction = SourceBatchAction;
type SectionBulkScope = "configurations" | "logs";

type BulkProgress = {
  scope: SectionBulkScope;
  action: SectionBulkAction;
} | null;

type DeletePrompt = {
  entries: SourceEntry[];
  impacts: SourceDeleteImpact[];
};

type LogSyncPrompt = {
  entries: SourceEntry[];
};

export function SourcesPage({
  metadataSources,
  logPaths,
  codeArtifacts,
  busy,
  selectedEnvironmentId,
  onImportMetadataSources,
  onImportDatacoolieProjectSources,
  onAddLogPath,
  onAddCodeArtifact,
  onUpdateSource,
  onDeleteSource,
  onGetDeleteImpact,
  onValidateSource,
  onSyncSource,
  onRunSourceBatch,
  onRefreshSources,
  syncStatuses
}: SourcesPageProps) {
  const [notice, setNotice] = useState<SourceOperationNotice>(null);
  const [bulkProgress, setBulkProgress] = useState<BulkProgress>(null);
  const [deletePrompt, setDeletePrompt] = useState<DeletePrompt | null>(null);
  const [logSyncPrompt, setLogSyncPrompt] = useState<LogSyncPrompt | null>(null);
  const [logSyncActivities, setLogSyncActivities] = useState<LogSyncActivities>({});
  const logSyncTimers = useRef<Record<number, number>>({});
  const selectedEnvironmentIdRef = useRef(selectedEnvironmentId);
  selectedEnvironmentIdRef.current = selectedEnvironmentId;
  const [loadingDeleteImpact, setLoadingDeleteImpact] = useState(false);
  const configurationSources = useMemo(
    () => [
      ...metadataSources.map((source) => ({ source, kind: "metadata" as const })),
      ...codeArtifacts.map((source) => ({ source, kind: "code" as const }))
    ],
    [metadataSources, codeArtifacts]
  );
  const logSources = useMemo(() => logPaths.map((source) => ({ source, kind: "logs" as const })), [logPaths]);
  const allSources = useMemo(
    () => [...configurationSources, ...logSources],
    [configurationSources, logSources]
  );
  const sourceHealth = useMemo(() => summarizeSourceHealth(allSources, syncStatuses), [allSources, syncStatuses]);
  const disabled = !selectedEnvironmentId;
  const sectionActionBusy = busy || bulkProgress !== null;

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
    setBulkProgress(null);
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
    const scope = entries.every((entry) => entry.kind === "logs") ? "logs" : "configurations";
    setDeletePrompt(null);
    setBulkProgress({ scope, action: "delete" });
    try {
      const result = await onRunSourceBatch("delete", toBatchEntries(entries));
      setNotice(batchNotice("delete", result));
    } finally {
      setBulkProgress(null);
    }
  }

  async function runSectionValidate(scope: SectionBulkScope, entries: SourceEntry[]) {
    if (!entries.length) return;
    try {
      setBulkProgress({ scope, action: "validate" });
      const result = await onRunSourceBatch("validate", toBatchEntries(entries));
      setNotice(batchNotice("validate", result));
    } finally {
      setBulkProgress(null);
    }
  }

  async function runSectionSync(scope: SectionBulkScope, entries: SourceEntry[]) {
    if (!entries.length) return;
    if (scope === "logs") {
      setLogSyncPrompt({ entries });
      return;
    }
    try {
      setBulkProgress({ scope, action: "sync" });
      const result = await onRunSourceBatch("sync", toBatchEntries(entries));
      setNotice(batchNotice("sync", result));
    } finally {
      setBulkProgress(null);
    }
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
    setBackgroundLogSyncState(sourceIds, "syncing");
    if (entries.length > 1) setBulkProgress({ scope: "logs", action: "sync" });
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
    } finally {
      if (isCurrentEnvironment()) setBulkProgress(null);
    }
  }

  async function runSectionDelete(_scope: SectionBulkScope, entries: SourceEntry[]) {
    if (!entries.length) return;
    await requestDelete(entries);
  }

  function activeSectionAction(scope: SectionBulkScope): SectionBulkAction | null {
    return bulkProgress?.scope === scope ? bulkProgress.action : null;
  }

  return (
    <div className="sources-page">
      <section className="sources-workspace-bar table-panel">
        <div className="sources-workspace-summary">
          <HardDrive size={16} />
          <div>
            <strong>{allSources.length} configured sources</strong>
            <span>{configurationSources.length} metadata &amp; code · {logSources.length} logs</span>
          </div>
        </div>
        <div className="sources-health-line" aria-label={`${sourceHealth.enabled} enabled, ${sourceHealth.readable} readable, ${sourceHealth.current} current`}>
          <span><strong>{sourceHealth.enabled}</strong> enabled</span>
          <span><strong>{sourceHealth.readable}</strong> readable</span>
          <span><strong>{sourceHealth.current}</strong> current</span>
        </div>
      </section>

      <div className="view-stack sources-stack">
        <section className="table-panel source-panel source-project-panel">
          <div className="panel-toolbar source-panel-toolbar">
            <div className="section-heading">
              <FolderOpen size={18} />
              <div>
                <h2>Metadata &amp; code</h2>
                <span>Metadata and source code.</span>
              </div>
            </div>
            <div className="source-section-toolbar">
              <div className="source-section-counts">
                <span>{configurationSources.length} configured</span>
                <strong>{metadataSources.length} metadata · {codeArtifacts.length} code</strong>
              </div>
              <SourceSectionBulkActions
                label="Metadata and code"
                total={configurationSources.length}
                busy={sectionActionBusy}
                active={activeSectionAction("configurations")}
                onValidate={() => runSectionValidate("configurations", configurationSources)}
                onSync={() => runSectionSync("configurations", configurationSources)}
                onDelete={() => runSectionDelete("configurations", configurationSources)}
              />
            </div>
          </div>
          <div className="source-section-layout">
          <aside className="source-config-sidebar" aria-label="Add metadata or source code">
            <DatacoolieProjectForm
              busy={busy}
              disabled={disabled}
              onImportMetadataSources={async (uri, label) => {
                try {
                  const result = await onImportMetadataSources(uri, label);
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
              onAddCodeArtifact={async (uri, label, sourceConfig) => {
                try {
                  await onAddCodeArtifact(uri, label, sourceConfig);
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
                onValidate={onValidateSource}
                onSync={onSyncSource}
                onRefresh={onRefreshSources}
                syncStatuses={syncStatuses}
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
                onValidate={onValidateSource}
                onSync={onSyncSource}
                onRefresh={onRefreshSources}
                syncStatuses={syncStatuses}
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
                <span>ETL and system logs.</span>
              </div>
            </div>
            <div className="source-section-toolbar">
              <div className="source-section-counts">
                <span>{logPaths.length} configured</span>
                <strong>{logPaths.filter((item) => item.enabled).length}/{logPaths.length} enabled</strong>
              </div>
              <SourceSectionBulkActions
                label="Logs"
                total={logSources.length}
                busy={sectionActionBusy}
                active={activeSectionAction("logs")}
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
              onAddLogPath={async (uri, label, sourceConfig) => {
                try {
                  await onAddLogPath(uri, label, sourceConfig);
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
              description="Base logs path, or separate ETL/system paths."
              icon={<FileCheck2 size={16} />}
              kind="logs"
              items={logPaths}
              emptyTitle="No log source yet"
              emptyDetail="Add a base logs folder or separate ETL/system folders."
              busy={busy}
              onUpdate={onUpdateSource}
              onDelete={requestSourceDelete}
              onValidate={onValidateSource}
              onSync={onSyncSource}
              onRequestLogSync={(id) => {
                const source = logPaths.find((item) => item.id === id);
                if (source) setLogSyncPrompt({ entries: [{ source, kind: "logs" }] });
              }}
              onRefresh={onRefreshSources}
              syncStatuses={syncStatuses}
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
      {loadingDeleteImpact ? <div className="source-delete-impact-loading" role="status">Reviewing cached data…</div> : null}
      {notice ? <OperationNotification notice={notice} onClose={() => setNotice(null)} /> : null}
    </div>
  );
}

function SourceSectionBulkActions({
  label,
  total,
  busy,
  active,
  onValidate,
  onSync,
  onDelete
}: {
  label: string;
  total: number;
  busy: boolean;
  active: SectionBulkAction | null;
  onValidate: () => Promise<void>;
  onSync: () => Promise<void>;
  onDelete: () => Promise<void>;
}) {
  return (
    <div className="source-bulk-actions source-section-bulk-actions" aria-label={`${label} bulk actions`}>
      <button type="button" onClick={() => void onValidate()} disabled={busy || !total} aria-busy={active === "validate"} title={`Validate all ${label.toLowerCase()} sources`}>
        {active === "validate" ? <LoaderCircle size={13} className="spin" /> : <CheckCircle2 size={13} />}
        <span>{active === "validate" ? "Validating" : "Validate all"}</span>
      </button>
      <button type="button" className="source-bulk-primary" onClick={onSync} disabled={busy || !total} aria-busy={active === "sync"} title={`Sync all ${label.toLowerCase()} sources`}>
        {active === "sync" ? <LoaderCircle size={13} className="spin" /> : <RefreshCw size={13} />}
        <span>{active === "sync" ? "Syncing" : "Sync all"}</span>
      </button>
      <button type="button" className="danger" onClick={() => void onDelete()} disabled={busy || !total} aria-busy={active === "delete"} title={`Delete all ${label.toLowerCase()} sources`}>
        {active === "delete" ? <LoaderCircle size={13} className="spin" /> : <Trash2 size={13} />}
        <span>{active === "delete" ? "Deleting" : "Delete all"}</span>
      </button>
    </div>
  );
}

function DatacoolieProjectForm({
  busy,
  disabled,
  onImportMetadataSources,
  onImportDatacoolieProjectSources,
  onAddCodeArtifact
}: {
  busy: boolean;
  disabled: boolean;
  onImportMetadataSources: (uri: string, label?: string) => Promise<boolean>;
  onImportDatacoolieProjectSources: (payload: {
    project_uri: string;
    metadata_subpath?: string;
    code_subpath?: string;
    metadata_uri?: string | null;
    code_uri?: string | null;
    include_metadata?: boolean;
    include_code?: boolean;
  }) => Promise<boolean>;
  onAddCodeArtifact: (uri: string, label?: string, sourceConfig?: Record<string, unknown>) => Promise<boolean>;
}) {
  const [mode, setMode] = useState<"project" | "manual">("project");
  const [projectUri, setProjectUri] = useState("");
  const [metadataSubpath, setMetadataSubpath] = useState("metadata");
  const [codeSubpath, setCodeSubpath] = useState("functions");
  const [includeMetadata, setIncludeMetadata] = useState(true);
  const [includeCode, setIncludeCode] = useState(true);
  const [manualKind, setManualKind] = useState<"metadata" | "code">("metadata");
  const [manualUri, setManualUri] = useState("");
  const [manualLabel, setManualLabel] = useState("");
  const [artifactType, setArtifactType] = useState("directory");
  const [moduleRoots, setModuleRoots] = useState("");
  const [modulePrefix, setModulePrefix] = useState("");

  async function submitProject(event: FormEvent) {
    event.preventDefault();
    if (!projectUri.trim() || (!includeMetadata && !includeCode)) return;
    await onImportDatacoolieProjectSources({
      project_uri: projectUri.trim(),
      metadata_subpath: metadataSubpath.trim() || "metadata",
      code_subpath: codeSubpath.trim() || "functions",
      include_metadata: includeMetadata,
      include_code: includeCode
    });
  }

  async function submitManual(event: FormEvent) {
    event.preventDefault();
    if (!manualUri.trim()) return;
    const added = manualKind === "metadata"
      ? await onImportMetadataSources(manualUri.trim(), manualLabel.trim() || undefined)
      : await onAddCodeArtifact(manualUri.trim(), manualLabel.trim() || undefined, {
          artifact_type: artifactType,
          module_roots: moduleRoots
            .split(",")
            .map((value) => value.trim())
            .filter(Boolean),
          ...(modulePrefix.trim() ? { module_prefix: modulePrefix.trim() } : {})
        });
    if (!added) return;
    setManualUri("");
    setManualLabel("");
  }

  return (
    <div className="source-config-card">
      <div className="source-config-tabs" role="tablist" aria-label="Project source mode">
        <button id="project-source-project-tab" type="button" role="tab" aria-selected={mode === "project"} aria-controls="project-source-project-panel" tabIndex={mode === "project" ? 0 : -1} className={mode === "project" ? "active" : ""} onClick={() => setMode("project")}>
          <FolderOpen size={14} />
          Project path
        </button>
        <button id="project-source-manual-tab" type="button" role="tab" aria-selected={mode === "manual"} aria-controls="project-source-manual-panel" tabIndex={mode === "manual" ? 0 : -1} className={mode === "manual" ? "active" : ""} onClick={() => setMode("manual")}>
          <Settings2 size={14} />
          Manual path
        </button>
      </div>

      {mode === "project" ? (
        <form id="project-source-project-panel" role="tabpanel" aria-labelledby="project-source-project-tab" className="source-add-form source-project-form" onSubmit={submitProject}>
          <div className="source-add-heading">
            <strong>Scan project</strong>
            <span>Defaults: metadata and functions.</span>
          </div>
          <label className="source-field-wide">
            Project path
            <input value={projectUri} onChange={(event) => setProjectUri(event.target.value)} placeholder="D:\GitHub\my_dcws" disabled={busy || disabled} />
          </label>
          <div className="source-form-row two">
            <label>
              Metadata folder
              <input value={metadataSubpath} onChange={(event) => setMetadataSubpath(event.target.value)} disabled={busy || disabled || !includeMetadata} />
            </label>
            <label>
              Source code folder
              <input value={codeSubpath} onChange={(event) => setCodeSubpath(event.target.value)} disabled={busy || disabled || !includeCode} />
            </label>
          </div>
          <div className="source-checkbox-row">
            <label>
              <input type="checkbox" checked={includeMetadata} onChange={(event) => setIncludeMetadata(event.target.checked)} disabled={busy || disabled} />
              Metadata
            </label>
            <label>
              <input type="checkbox" checked={includeCode} onChange={(event) => setIncludeCode(event.target.checked)} disabled={busy || disabled} />
              Source code
            </label>
          </div>
          <button type="submit" disabled={busy || disabled || !projectUri.trim() || (!includeMetadata && !includeCode)}>
            <Search size={16} />
            <span>Scan project</span>
          </button>
        </form>
      ) : (
        <form id="project-source-manual-panel" role="tabpanel" aria-labelledby="project-source-manual-tab" className="source-add-form source-project-form" onSubmit={submitManual}>
          <div className="source-add-heading">
            <strong>Add path</strong>
            <span>Metadata can be a file or folder.</span>
          </div>
          <label>
            Source type
            <select value={manualKind} onChange={(event) => setManualKind(event.target.value as "metadata" | "code")} disabled={busy || disabled}>
              <option value="metadata">Metadata</option>
              <option value="code">Source code</option>
            </select>
          </label>
          <label className="source-field-wide">
            Path
            <input
              value={manualUri}
              onChange={(event) => setManualUri(event.target.value)}
              placeholder={manualKind === "metadata" ? "metadata file or folder" : "directory, ZIP, wheel, or installed package"}
              disabled={busy || disabled}
            />
          </label>
          <label>
            Label
            <input value={manualLabel} onChange={(event) => setManualLabel(event.target.value)} placeholder="Optional label" disabled={busy || disabled} />
          </label>
          {manualKind === "code" ? (
            <div className="source-form-row two">
              <label>
                Artifact type
                <select value={artifactType} onChange={(event) => setArtifactType(event.target.value)} disabled={busy || disabled}>
                  <option value="directory">Directory</option>
                  <option value="zip">ZIP archive</option>
                  <option value="wheel">Python wheel</option>
                  <option value="installed_distribution">Installed distribution</option>
                </select>
              </label>
              <label>
                Module prefix
                <input value={modulePrefix} onChange={(event) => setModulePrefix(event.target.value)} placeholder="functions" disabled={busy || disabled} />
              </label>
              <label className="source-field-wide">
                Module roots
                <input value={moduleRoots} onChange={(event) => setModuleRoots(event.target.value)} placeholder="src, python (optional)" disabled={busy || disabled} />
              </label>
            </div>
          ) : null}
          <button type="submit" disabled={busy || disabled || !manualUri.trim()}>
            <Plus size={16} />
            <span>Add path</span>
          </button>
        </form>
      )}
    </div>
  );
}

function LogSourceForm({
  busy,
  disabled,
  onAddLogPath
}: {
  busy: boolean;
  disabled: boolean;
  onAddLogPath: (uri: string, label?: string, sourceConfig?: Record<string, unknown>) => Promise<boolean>;
}) {
  const [mode, setMode] = useState<"base_log_path" | "separate_paths">("base_log_path");
  const [baseUri, setBaseUri] = useState("");
  const [etlUri, setEtlUri] = useState("");
  const [systemUri, setSystemUri] = useState("");
  const [label, setLabel] = useState("");

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (mode === "base_log_path") {
      if (!baseUri.trim()) return;
      const added = await onAddLogPath(baseUri.trim(), label.trim() || undefined, {
        mode: "base_log_path",
        base_log_uri: baseUri.trim()
      });
      if (!added) return;
      setBaseUri("");
    } else {
      if (!etlUri.trim() && !systemUri.trim()) return;
      const primaryUri = etlUri.trim() || systemUri.trim();
      const added = await onAddLogPath(primaryUri, label.trim() || undefined, {
        mode: "separate_paths",
        etl_logs_uri: etlUri.trim() || undefined,
        system_logs_uri: systemUri.trim() || undefined
      });
      if (!added) return;
      setEtlUri("");
      setSystemUri("");
    }
    setLabel("");
  }

  return (
    <div className="source-config-card">
      <form className="source-add-form source-log-form" onSubmit={submit}>
        <div className="source-add-heading">
          <strong>Add log source</strong>
          <span>Base path or separate ETL/system folders.</span>
        </div>
        <label>
          Mode
          <select value={mode} onChange={(event) => setMode(event.target.value as "base_log_path" | "separate_paths")} disabled={busy || disabled}>
            <option value="base_log_path">Base log path</option>
            <option value="separate_paths">Separate ETL/system paths</option>
          </select>
        </label>
        {mode === "base_log_path" ? (
          <label className="source-field-wide">
            Base log path
            <input value={baseUri} onChange={(event) => setBaseUri(event.target.value)} placeholder="logs folder" disabled={busy || disabled} />
          </label>
        ) : (
          <div className="source-form-row two">
            <label>
              ETL logs path
              <input value={etlUri} onChange={(event) => setEtlUri(event.target.value)} placeholder="etl_logs folder" disabled={busy || disabled} />
            </label>
            <label>
              System logs path
              <input value={systemUri} onChange={(event) => setSystemUri(event.target.value)} placeholder="system_logs folder" disabled={busy || disabled} />
            </label>
          </div>
        )}
        <label>
          Label
          <input value={label} onChange={(event) => setLabel(event.target.value)} placeholder="Optional label" disabled={busy || disabled} />
        </label>
        <button type="submit" disabled={busy || disabled || (mode === "base_log_path" ? !baseUri.trim() : !etlUri.trim() && !systemUri.trim())}>
          <Plus size={16} />
          <span>Add log source</span>
        </button>
      </form>
    </div>
  );
}

function SourceGroup({
  title,
  description,
  icon,
  kind,
  items,
  emptyTitle,
  emptyDetail,
  busy,
  onUpdate,
  onDelete,
  onValidate,
  onSync,
  onRequestLogSync,
  onRefresh,
  syncStatuses,
  externalSyncActivities = {}
}: {
  title: string;
  description: string;
  icon: React.ReactNode;
  kind: SourceKind;
  items: SourcePath[];
  emptyTitle: string;
  emptyDetail: string;
  busy: boolean;
  onUpdate: SourcesPageProps["onUpdateSource"];
  onDelete: SourcesPageProps["onDeleteSource"];
  onValidate: SourcesPageProps["onValidateSource"];
  onSync: SourcesPageProps["onSyncSource"];
  onRequestLogSync?: (id: number) => void;
  onRefresh: SourcesPageProps["onRefreshSources"];
  syncStatuses: Record<string, SourceSyncStatus>;
  externalSyncActivities?: LogSyncActivities;
}) {
  const [syncStatus, setSyncStatus] = useState<Record<number, SourceSyncStatus>>({});
  const [syncing, setSyncing] = useState<Record<number, boolean>>({});
  const enabled = items.filter((item) => item.enabled).length;

  async function validate(id: number) {
    await onValidate(kind, id);
  }

  async function sync(id: number) {
    if (kind === "logs" && onRequestLogSync) {
      onRequestLogSync(id);
      return;
    }
    setSyncing((current) => ({ ...current, [id]: true }));
    setSyncStatus((current) => ({
      ...current,
      [id]: runningSourceSyncStatus(kind, id)
    }));
    try {
      const result = await onSync(kind, id);
      setSyncStatus((current) => ({ ...current, [id]: result }));
      await onRefresh();
    } finally {
      setSyncing((current) => ({ ...current, [id]: false }));
    }
  }

  return (
    <section className="source-group">
      <div className="source-group-header">
        <div className="source-group-title">
          {icon}
          <div>
            <strong>{title}</strong>
            <span>{description}</span>
          </div>
        </div>
        <div className="source-group-toolbar">
          <em>{enabled}/{items.length} enabled</em>
        </div>
      </div>
      <div className="source-card-list">
        {items.map((item) => (
          <SourceCard
            key={item.id}
            item={item}
            kind={kind}
            busy={busy}
            syncActivity={syncing[item.id] ? "syncing" : externalSyncActivities[item.id]}
            validation={item.latest_validation ?? null}
            syncStatus={externalSyncActivities[item.id] === "syncing"
              ? runningSourceSyncStatus(kind, item.id)
              : syncStatus[item.id] ?? syncStatuses[sourceKey(kind, item.id)] ?? null}
            onUpdate={onUpdate}
            onDelete={onDelete}
            onValidate={validate}
            onSync={sync}
          />
        ))}
        {!items.length ? (
          <div className="source-card-empty">
            <Clock size={18} />
            <div>
              <strong>{emptyTitle}</strong>
              <span>{emptyDetail}</span>
            </div>
          </div>
        ) : null}
      </div>
    </section>
  );
}

function SourceCard({
  item,
  kind,
  busy,
  syncActivity,
  validation,
  syncStatus,
  onUpdate,
  onDelete,
  onValidate,
  onSync
}: {
  item: SourcePath;
  kind: SourceKind;
  busy: boolean;
  syncActivity?: LogSyncActivity;
  validation: SourceReadCheckResult | null;
  syncStatus: SourceSyncStatus | null;
  onUpdate: SourcesPageProps["onUpdateSource"];
  onDelete: SourcesPageProps["onDeleteSource"];
  onValidate: (id: number) => Promise<void>;
  onSync: (id: number) => Promise<void>;
}) {
  const secondaryPath = kind === "logs" && typeof item.source_config?.system_logs_uri === "string" ? item.source_config.system_logs_uri : null;
  const etlPath = kind === "logs" && typeof item.source_config?.etl_logs_uri === "string" ? item.source_config.etl_logs_uri : null;
  const primaryPathLabel = kind === "logs" && item.source_config?.mode === "separate_paths" ? "ETL" : "Path";
  const modeLabel =
    kind === "code" && typeof item.source_config?.artifact_type === "string"
      ? item.source_config.artifact_type.replace(/_/g, " ")
      : kind === "logs" && typeof item.source_config?.mode === "string"
        ? item.source_config.mode === "separate_paths"
          ? "separate paths"
          : "base path"
        : kind === "metadata" && typeof item.source_config?.discovery_mode === "string"
          ? String(item.source_config.discovery_mode).replace(/_/g, " ")
          : null;
  const primaryPath = etlPath || item.uri;
  const displayName = item.label || basename(item.uri);
  const typeLabel = sourceTypeLabel(kind, item, modeLabel);
  const scheduleInterval = logRefreshInterval(item);
  const syncing = syncActivity === "syncing";
  const syncAction = syncActivity === "done"
    ? { icon: <CheckCircle2 size={13} />, label: "Done" }
    : syncActivity === "error"
      ? { icon: <XCircle size={13} />, label: "Failed" }
      : syncing
        ? { icon: <RefreshCw size={13} className="spin" />, label: "Sync" }
        : { icon: <RefreshCw size={13} />, label: "Sync" };

  return (
    <article className="source-card">
      <div className="source-card-line source-card-line-primary">
        <div className="source-card-identity-row">
          <strong className="source-card-name" title={displayName}>{displayName}</strong>
          <span className="source-type-chip">{typeLabel}</span>
        </div>
        <div className="source-card-row-actions">
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
                  sync_interval_minutes: scheduleInterval
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
            <button className="source-action-btn" onClick={() => onValidate(item.id)} disabled={busy} title="Validate source" aria-label={`Validate ${displayName}`}>
              <CheckCircle2 size={13} />
              <span>Validate</span>
            </button>
            <button
              className={`source-action-btn${syncActivity ? ` is-${syncActivity}` : ""}`}
              onClick={() => onSync(item.id)}
              disabled={busy || syncing}
              title={syncing ? "Sync in progress" : syncActivity === "done" ? "Sync completed" : syncActivity === "error" ? "Sync failed" : "Sync cache now"}
              aria-label={`Sync cache for ${displayName}`}
              aria-busy={syncing}
            >
              {syncAction.icon}
              <span>{syncAction.label}</span>
            </button>
            <button
              className="source-action-btn danger"
              onClick={() => void onDelete(kind, item.id)}
              disabled={busy}
              title="Delete source"
              aria-label={`Delete ${displayName}`}
            >
              <Trash2 size={13} />
            </button>
          </div>
        </div>
      </div>
      <div className="source-card-line source-card-line-secondary">
        <div className="source-card-path-list">
          <div className="source-card-path-inline" title={primaryPath}>
            <span>{primaryPathLabel}</span>
            <code>{primaryPath}</code>
          </div>
          {secondaryPath ? (
            <div className="source-card-path-inline" title={secondaryPath}>
              <span>System</span>
              <code>{secondaryPath}</code>
            </div>
          ) : null}
        </div>
        <div className="source-card-status">
          <LabeledStatus label="Read">
            <ReadCheckBadge validation={validation} />
          </LabeledStatus>
          <LabeledStatus label="Cache">
            <SyncBadge status={syncStatus} />
          </LabeledStatus>
        </div>
      </div>
    </article>
  );
}

function LogSyncDialog({
  sourceCount,
  onCancel,
  onConfirm
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

function SourceDeleteDialog({
  prompt,
  onCancel,
  onConfirm
}: {
  prompt: DeletePrompt;
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
  if (kind === "code") return "code";
  return "log";
}

function sourceTypeLabel(kind: SourceKind, item: SourcePath, modeLabel: string | null) {
  if (kind === "metadata") return metadataSourceTypeLabel(item);
  if (kind === "logs") {
    return item.source_config?.mode === "separate_paths" ? "separate log paths" : "base log path";
  }
  return modeLabel ? `${modeLabel} source` : "source code path";
}

function metadataSourceTypeLabel(item: SourcePath) {
  const marker = `${item.label ?? ""}/${item.uri}`.replace(/\\/g, "/").toLowerCase();
  if (marker.includes("schema_hints") || marker.includes("schema-hints")) return "schema hints path";
  if (marker.includes("connections")) return "connection path";
  if (marker.includes("dataflows")) return "dataflow path";
  return "metadata path";
}

function LabeledStatus({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="source-labeled-status">
      <span>{label}</span>
      {children}
    </div>
  );
}

function ReadCheckBadge({ validation }: { validation: SourceReadCheckResult | null }) {
  if (!validation) {
    return (
      <span className="source-status-pill muted" title="Not yet validated">
        <Clock size={11} />
        <span>not validated</span>
      </span>
    );
  }
  const ok = validation.status === "ok" || validation.status === "warning";
  const summary = compactValidationSummary(validation);
  return (
    <span className={`source-status-pill ${ok ? "ok" : "error"}`} title={readCheckTooltip(validation)}>
      {ok ? <CheckCircle2 size={11} /> : <XCircle size={11} />}
      <span>{ok ? "readable" : "not readable"}</span>
      {validation.validated_at ? <em>{relativeTime(validation.validated_at)}</em> : null}
      {summary ? <em className="source-status-detail">· {summary}</em> : null}
    </span>
  );
}

function SyncBadge({ status }: { status: SourceSyncStatus | null }) {
  if (!status || status.status === "unknown") {
    return (
      <span className="source-status-pill muted" title="Not synced">
        <Clock size={11} />
        <span>not synced</span>
      </span>
    );
  }
  const ok = status.status === "ok";
  const failed = status.status === "error";
  const running = status.status === "running";
  return (
    <span className={`source-status-pill ${ok ? "ok" : failed ? "error" : running ? "warn" : "muted"}`} title={syncTooltip(status)}>
      {ok ? <CheckCircle2 size={11} /> : failed ? <XCircle size={11} /> : <RefreshCw size={11} className={running ? "spin" : undefined} />}
      <span>{ok ? "synced" : running ? "syncing..." : status.status}</span>
      {status.checked_at ? <em>{relativeTime(status.checked_at)}</em> : null}
    </span>
  );
}

function runningSourceSyncStatus(kind: SourceKind, sourceId: number): SourceSyncStatus {
  return {
    source_id: sourceId,
    source_kind: kind,
    status: "running",
    message: "Sync in progress",
    checked_at: new Date().toISOString(),
    latest_job: null
  };
}

function toBatchEntries(entries: SourceEntry[]): SourceBatchEntry[] {
  return entries.map((entry) => ({ kind: entry.kind, id: entry.source.id }));
}

function operationFailureNotice(title: string, detail = "Review the error and retry the action."): SourceOperationNotice {
  return {
    tone: "error",
    title,
    detail,
    errors: []
  };
}

function batchNotice(action: SourceBatchAction, result: SourceBatchResult, context?: string): SourceOperationNotice {
  const actionLabel = action === "validate" ? "Validation" : action === "sync" ? "Sync" : "Deletion";
  const completed = result.succeeded + result.warnings;
  const parts = [
    `${result.succeeded} ${action === "validate" ? "validated" : action === "sync" ? "synced" : "deleted"}`,
    result.warnings ? `${result.warnings} completed with warning${result.warnings === 1 ? "" : "s"}` : "",
    result.failed ? `${result.failed} failed` : ""
  ].filter(Boolean);
  return {
    tone: result.failed === result.total ? "error" : result.failed || result.warnings ? "warning" : "success",
    title: result.failed ? `${actionLabel} completed with issues` : `${actionLabel} complete`,
    detail: `${context ? `${context} · ` : ""}${completed}/${result.total} sources processed · ${parts.join(" · ")}`,
    errors: result.errors
  };
}

function logSyncNotice(status: SourceSyncStatus, request: LogSyncRequest): SourceOperationNotice {
  const result = status.latest_job?.result ?? {};
  const counts = [
    countSummary(result, "candidate_files", "candidate files"),
    countSummary(result, "replaced_files", "replaced"),
    countSummary(result, "inserted_rows", "rows"),
    countSummary(result, "unchanged_files", "unchanged")
  ].filter(Boolean);
  return {
    tone: status.status === "error" ? "error" : status.status === "warning" ? "warning" : "success",
    title: status.status === "error" ? "Log sync failed" : "Log sync complete",
    detail: [logSyncModeLabel(request), ...counts, status.message].filter(Boolean).join(" · "),
    errors: status.status === "error" ? [status.message] : []
  };
}

function logSyncModeLabel(request: LogSyncRequest) {
  return request.mode === "incremental" ? "Incremental" : "Incremental + lookback";
}

function countSummary(result: Record<string, unknown>, key: string, label: string) {
  const value = result[key];
  return typeof value === "number" ? `${value.toLocaleString()} ${label}` : "";
}

function importNotice(result: SourceImportResponse | null, title: string): SourceOperationNotice {
  if (!result) return operationFailureNotice(`${title.replace(" complete", "")} could not be completed`);
  const created = result.summary.created ?? result.created.length;
  const existing = result.summary.existing ?? result.existing.length;
  const metadata = result.summary.metadata_sources ?? 0;
  const code = result.summary.code_artifacts ?? 0;
  const autoSynced = result.summary.auto_synced ?? 0;
  const errors = result.errors.map((item) => String(item.message ?? item.uri ?? JSON.stringify(item)));
  return {
    tone: errors.length ? created || existing ? "warning" : "error" : "success",
    title,
    detail: `${created} created · ${existing} reused · ${metadata} metadata · ${code} code${autoSynced ? ` · ${autoSynced} validated & synced` : ""}`,
    errors
  };
}

function compactValidationSummary(validation: SourceReadCheckResult) {
  const counts = validation.record_counts ?? {};
  if (validation.source_kind === "metadata") {
    return validation.detected_format ? `${validation.detected_format} file` : "metadata file";
  }
  if (validation.source_kind === "logs") {
    const jobs = counts.job_jsonl_files;
    const dataflows = counts.dataflow_parquet_files;
    const systems = counts.system_jsonl_files;
    if (typeof jobs === "number" || typeof dataflows === "number" || typeof systems === "number") {
      return `${jobs ?? 0} job, ${dataflows ?? 0} run, ${systems ?? 0} system files`;
    }
  }
  if (validation.source_kind === "code") {
    return `${counts.python_files ?? 0} Python files, ${counts.modules ?? 0} modules`;
  }
  return validation.message;
}

function syncTooltip(status: SourceSyncStatus) {
  const revision = status.revision ?? {};
  return [
    status.message,
    status.latest_job?.status ? `job: ${status.latest_job.status}` : "",
    typeof revision.size === "number" ? `size: ${revision.size}` : "",
    typeof revision.file_count === "number" ? `files: ${revision.file_count}` : ""
  ]
    .filter(Boolean)
    .join(" · ");
}

function readCheckTooltip(validation: SourceReadCheckResult) {
  return [validation.message, validation.detected_provider, validation.detected_format, formatCounts(validation.record_counts)].filter(Boolean).join(" · ");
}

function formatCounts(counts?: Record<string, number>) {
  if (!counts || !Object.keys(counts).length) return "";
  return Object.entries(counts)
    .map(([key, value]) => `${key.replace(/_/g, " ")}: ${value}`)
    .join(", ");
}

function relativeTime(value: string) {
  const time = new Date(value).getTime();
  if (Number.isNaN(time)) return value;
  const seconds = Math.max(0, Math.floor((Date.now() - time) / 1000));
  if (seconds < 60) return "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d ago`;
  return new Date(value).toLocaleDateString();
}

function basename(uri: string) {
  const normalized = uri.replace(/\\/g, "/").replace(/\/$/, "");
  return normalized.split("/").pop() || uri;
}
