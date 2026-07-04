import {
  CheckCircle2,
  Clock,
  Code2,
  Database,
  FileCheck2,
  FolderOpen,
  HardDrive,
  Plus,
  RefreshCw,
  Search,
  Settings2,
  Trash2,
  XCircle
} from "lucide-react";
import { FormEvent, useMemo, useState } from "react";
import type { SourceImportResponse, SourcePath, SourceReadCheckResult, SourceSyncStatus } from "../../shared/api/types";
import { sourceKey, type SourceKind } from "../../shared/lib/sources";

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
  onDeleteSources: (kind: SourceKind, ids: number[]) => Promise<void>;
  onValidateSource: (kind: SourceKind, id: number) => Promise<SourceReadCheckResult>;
  onSyncSource: (kind: SourceKind, id: number) => Promise<SourceSyncStatus>;
  syncStatuses: Record<string, SourceSyncStatus>;
}

type ImportNotice = {
  title: string;
  detail: string;
  errors: string[];
} | null;

type SourceEntry = {
  source: SourcePath;
  kind: SourceKind;
};

type SectionBulkAction = "check" | "sync" | "delete";
type SectionBulkScope = "configurations" | "logs";

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
  onDeleteSources,
  onValidateSource,
  onSyncSource,
  syncStatuses
}: SourcesPageProps) {
  const [notice, setNotice] = useState<ImportNotice>(null);
  const [sectionBulkBusy, setSectionBulkBusy] = useState<`${SectionBulkScope}:${SectionBulkAction}` | null>(null);
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
  const enabledCount = allSources.filter(({ source }) => source.enabled).length;
  const readableCount = allSources.filter(
    ({ source }) => source.latest_validation?.status === "ok" || source.latest_validation?.status === "warning"
  ).length;
  const syncedCount = allSources.filter(({ source, kind }) => syncStatuses[sourceKey(kind, source.id)]?.status === "ok").length;
  const disabled = !selectedEnvironmentId;
  const sectionActionBusy = busy || sectionBulkBusy !== null;

  async function runSectionCheck(scope: SectionBulkScope, entries: SourceEntry[]) {
    if (!entries.length) return;
    setSectionBulkBusy(`${scope}:check`);
    try {
      for (const entry of entries) {
        await onValidateSource(entry.kind, entry.source.id);
      }
    } finally {
      setSectionBulkBusy(null);
    }
  }

  async function runSectionSync(scope: SectionBulkScope, entries: SourceEntry[]) {
    if (!entries.length) return;
    setSectionBulkBusy(`${scope}:sync`);
    try {
      for (const entry of entries) {
        await onSyncSource(entry.kind, entry.source.id);
      }
    } finally {
      setSectionBulkBusy(null);
    }
  }

  async function runSectionDelete(scope: SectionBulkScope, entries: SourceEntry[]) {
    if (!entries.length) return;
    setSectionBulkBusy(`${scope}:delete`);
    try {
      for (const kind of ["metadata", "code", "logs"] as SourceKind[]) {
        const ids = entries.filter((entry) => entry.kind === kind).map((entry) => entry.source.id);
        if (ids.length) await onDeleteSources(kind, ids);
      }
    } finally {
      setSectionBulkBusy(null);
    }
  }

  function activeSectionAction(scope: SectionBulkScope): SectionBulkAction | null {
    if (!sectionBulkBusy?.startsWith(`${scope}:`)) return null;
    return sectionBulkBusy.split(":")[1] as SectionBulkAction;
  }

  return (
    <div className="sources-page">
      <section className="sources-overview table-panel">
        <SourceOverviewMetric icon={<HardDrive size={17} />} label="Sources" value={allSources.length} detail={`${enabledCount} enabled`} />
        <SourceOverviewMetric icon={<FileCheck2 size={17} />} label="Read checks" value={readableCount} detail={`${allSources.length - readableCount} need check`} />
        <SourceOverviewMetric icon={<RefreshCw size={17} />} label="Cache sync" value={syncedCount} detail={`${allSources.length - syncedCount} not current`} />
      </section>

      {notice ? (
        <section className={`source-import-notice table-panel ${notice.errors.length ? "has-errors" : ""}`}>
          <div>
            <strong>{notice.title}</strong>
            <span>{notice.detail}</span>
          </div>
          {notice.errors.length ? (
            <ul>
              {notice.errors.map((error) => (
                <li key={error}>{error}</li>
              ))}
            </ul>
          ) : null}
        </section>
      ) : null}

      <div className="view-stack sources-stack">
        <section className="table-panel source-panel source-project-panel">
          <div className="panel-toolbar source-panel-toolbar">
            <div className="section-heading">
              <FolderOpen size={18} />
              <div>
                <h2>Metadata</h2>
                <span>Metadata and source code.</span>
              </div>
            </div>
            <div className="source-section-toolbar">
              <div className="source-section-counts">
                <span>{configurationSources.length} configured</span>
                <strong>{metadataSources.length} metadata · {codeArtifacts.length} code</strong>
              </div>
              <SourceSectionBulkActions
                label="Metadata"
                total={configurationSources.length}
                busy={sectionActionBusy}
                active={activeSectionAction("configurations")}
                onCheck={() => runSectionCheck("configurations", configurationSources)}
                onSync={() => runSectionSync("configurations", configurationSources)}
                onDelete={() => runSectionDelete("configurations", configurationSources)}
              />
            </div>
          </div>
          <div className="source-project-layout">
            <DatacoolieProjectForm
              busy={busy}
              disabled={disabled}
              onImportMetadataSources={async (uri, label) => {
                const result = await onImportMetadataSources(uri, label);
                setNotice(importNotice(result, "Metadata scan complete"));
              }}
              onImportDatacoolieProjectSources={async (payload) => {
                const result = await onImportDatacoolieProjectSources(payload);
                setNotice(importNotice(result, "Project scan complete"));
              }}
              onAddCodeArtifact={async (uri, label, sourceConfig) => {
                await onAddCodeArtifact(uri, label, sourceConfig);
                setNotice({ title: "Source code added", detail: "1 code artifact configured.", errors: [] });
              }}
            />
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
                onDelete={onDeleteSource}
                onValidate={onValidateSource}
                onSync={onSyncSource}
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
                onDelete={onDeleteSource}
                onValidate={onValidateSource}
                onSync={onSyncSource}
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
                onCheck={() => runSectionCheck("logs", logSources)}
                onSync={() => runSectionSync("logs", logSources)}
                onDelete={() => runSectionDelete("logs", logSources)}
              />
            </div>
          </div>
          <div className="source-logs-layout">
            <LogSourceForm
              busy={busy}
              disabled={disabled}
              onAddLogPath={async (uri, label, sourceConfig) => {
                await onAddLogPath(uri, label, sourceConfig);
                setNotice({ title: "Log source added", detail: "1 log source configured.", errors: [] });
              }}
            />
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
              onDelete={onDeleteSource}
              onValidate={onValidateSource}
              onSync={onSyncSource}
              syncStatuses={syncStatuses}
            />
          </div>
        </section>
      </div>
    </div>
  );
}

function SourceSectionBulkActions({
  label,
  total,
  busy,
  active,
  onCheck,
  onSync,
  onDelete
}: {
  label: string;
  total: number;
  busy: boolean;
  active: SectionBulkAction | null;
  onCheck: () => Promise<void>;
  onSync: () => Promise<void>;
  onDelete: () => Promise<void>;
}) {
  return (
    <div className="source-bulk-actions source-section-bulk-actions" aria-label={`${label} bulk actions`}>
      <button type="button" onClick={onCheck} disabled={busy || !total} title={`Check all ${label.toLowerCase()} sources`}>
        <CheckCircle2 size={13} />
        <span>{active === "check" ? "Checking" : "Check all"}</span>
      </button>
      <button type="button" onClick={onSync} disabled={busy || !total} title={`Sync all ${label.toLowerCase()} sources`}>
        <RefreshCw size={13} className={active === "sync" ? "spin" : undefined} />
        <span>{active === "sync" ? "Syncing" : "Sync all"}</span>
      </button>
      <button type="button" className="danger" onClick={onDelete} disabled={busy || !total} title={`Delete all ${label.toLowerCase()} sources`}>
        <Trash2 size={13} />
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
  onImportMetadataSources: (uri: string, label?: string) => Promise<void>;
  onImportDatacoolieProjectSources: (payload: {
    project_uri: string;
    metadata_subpath?: string;
    code_subpath?: string;
    metadata_uri?: string | null;
    code_uri?: string | null;
    include_metadata?: boolean;
    include_code?: boolean;
  }) => Promise<void>;
  onAddCodeArtifact: SourcesPageProps["onAddCodeArtifact"];
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
    if (manualKind === "metadata") {
      await onImportMetadataSources(manualUri.trim(), manualLabel.trim() || undefined);
    } else {
      await onAddCodeArtifact(manualUri.trim(), manualLabel.trim() || undefined, {
        artifact_type: artifactType,
        module_roots: moduleRoots
          .split(",")
          .map((value) => value.trim())
          .filter(Boolean),
        ...(modulePrefix.trim() ? { module_prefix: modulePrefix.trim() } : {})
      });
    }
    setManualUri("");
    setManualLabel("");
  }

  return (
    <div className="source-config-card">
      <div className="source-config-tabs" role="tablist" aria-label="Project source mode">
        <button type="button" className={mode === "project" ? "active" : ""} onClick={() => setMode("project")}>
          <FolderOpen size={14} />
          Project path
        </button>
        <button type="button" className={mode === "manual" ? "active" : ""} onClick={() => setMode("manual")}>
          <Settings2 size={14} />
          Manual path
        </button>
      </div>

      {mode === "project" ? (
        <form className="source-add-form source-project-form" onSubmit={submitProject}>
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
        <form className="source-add-form source-project-form" onSubmit={submitManual}>
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
  onAddLogPath: SourcesPageProps["onAddLogPath"];
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
      await onAddLogPath(baseUri.trim(), label.trim() || undefined, {
        mode: "base_log_path",
        base_log_uri: baseUri.trim()
      });
      setBaseUri("");
    } else {
      if (!etlUri.trim() && !systemUri.trim()) return;
      const primaryUri = etlUri.trim() || systemUri.trim();
      await onAddLogPath(primaryUri, label.trim() || undefined, {
        mode: "separate_paths",
        etl_logs_uri: etlUri.trim() || undefined,
        system_logs_uri: systemUri.trim() || undefined
      });
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
  syncStatuses
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
  syncStatuses: Record<string, SourceSyncStatus>;
}) {
  const [validation, setValidation] = useState<Record<number, SourceReadCheckResult>>({});
  const [syncStatus, setSyncStatus] = useState<Record<number, SourceSyncStatus>>({});
  const [syncing, setSyncing] = useState<Record<number, boolean>>({});
  const enabled = items.filter((item) => item.enabled).length;

  async function validate(id: number) {
    const result = await onValidate(kind, id);
    setValidation((current) => ({ ...current, [id]: result }));
  }

  async function sync(id: number) {
    setSyncing((current) => ({ ...current, [id]: true }));
    setSyncStatus((current) => ({
      ...current,
      [id]: {
        source_id: id,
        source_kind: kind,
        status: "running",
        message: "Sync in progress",
        checked_at: new Date().toISOString(),
        latest_job: null
      }
    }));
    try {
      const result = await onSync(kind, id);
      setSyncStatus((current) => ({ ...current, [id]: result }));
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
            syncing={Boolean(syncing[item.id])}
            validation={validation[item.id] ?? item.latest_validation ?? null}
            syncStatus={syncStatus[item.id] ?? syncStatuses[sourceKey(kind, item.id)] ?? null}
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

function SourceOverviewMetric({ icon, label, value, detail }: { icon: React.ReactNode; label: string; value: number; detail: string }) {
  return (
    <div className="sources-overview-metric">
      <span className="sources-overview-icon">{icon}</span>
      <div>
        <strong>{value}</strong>
        <span>{label}</span>
      </div>
      <em>{detail}</em>
    </div>
  );
}

function SourceCard({
  item,
  kind,
  busy,
  syncing,
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
  syncing: boolean;
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

  return (
    <article className="source-card">
      <div className="source-card-main">
        <div className="source-card-line source-card-line-primary">
          <strong className="source-card-name" title={displayName}>{displayName}</strong>
          <span className="source-type-chip">{typeLabel}</span>
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
        <div className="source-card-line source-card-line-secondary">
          <div className="source-card-status">
            <LabeledStatus label="Read">
              <ReadCheckBadge validation={validation} />
            </LabeledStatus>
            <LabeledStatus label="Cache">
              <SyncBadge status={syncStatus} />
            </LabeledStatus>
          </div>
        </div>
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
        <div className="source-card-actions">
          <button className="source-action-btn" onClick={() => onValidate(item.id)} disabled={busy} title="Check readability" aria-label="Check readability">
            <CheckCircle2 size={13} />
            <span>Check</span>
          </button>
          <button className="source-action-btn" onClick={() => onSync(item.id)} disabled={syncing} title="Sync cache now" aria-label="Sync cache now">
            <RefreshCw size={13} className={syncing ? "spin" : undefined} />
            <span>Sync</span>
          </button>
          <button className="source-action-btn danger" onClick={() => onDelete(kind, item.id)} disabled={busy} title="Remove source" aria-label="Remove source">
            <Trash2 size={13} />
          </button>
        </div>
      </div>
    </article>
  );
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
      <span className="source-status-pill muted" title="Not yet checked">
        <Clock size={11} />
        <span>not checked</span>
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

function importNotice(result: SourceImportResponse | null, title: string): ImportNotice {
  if (!result) return null;
  const created = result.summary.created ?? result.created.length;
  const existing = result.summary.existing ?? result.existing.length;
  const metadata = result.summary.metadata_sources ?? 0;
  const code = result.summary.code_artifacts ?? 0;
  const errors = result.errors.map((item) => String(item.message ?? item.uri ?? JSON.stringify(item)));
  return {
    title,
    detail: `${created} created · ${existing} reused · ${metadata} metadata · ${code} code`,
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
