import { AlertTriangle, CheckCircle2, Loader2, Settings, Trash2 } from "lucide-react";
import { useCallback, useState } from "react";
import type { ModuleInfo, StudioDiagnostics, StudioSettings } from "../../shared/api/domainTypes";
import { formatRelativeTime } from "../../shared/time";
import { Tag } from "../../shared/components/Tag";
import { Toggle } from "../../shared/components/Toggle";
import { OperationConfirmationDialog } from "../../shared/components/OperationConfirmationDialog";
import { OperationNotification } from "../../shared/components/OperationNotification";
import { api } from "../../shared/api/client";
import { toErrorMessage } from "../../shared/lib/errors";
import type { StudioSettingsChanges } from "./hooks/useStudioSettings";
import { type StudioCacheAction, useStudioCache } from "./hooks/useStudioCache";
import { StudioConfigurationDrawer } from "./StudioConfigurationDrawer";
import { AnalyticsUpgradeDetailsDrawer } from "./AnalyticsUpgradeDetailsDrawer";
import { CredentialProfilesSection } from "./CredentialProfilesSection";
import type { TimezoneOption } from "./timezoneOptions";
import "./settings.css";

interface SettingsPageProps {
  settings: StudioSettings | null;
  settingsLoading: boolean;
  saving: boolean;
  onSaveSettings: (changes: StudioSettingsChanges) => Promise<StudioSettings>;
  diagnostics: StudioDiagnostics | null;
  diagnosticsLoading: boolean;
  onReloadDiagnostics: () => Promise<void>;
  modules: ModuleInfo[];
  modulesBusyKey: string | null;
  onToggleModule: (key: string, enabled: boolean) => Promise<void>;
}

interface PendingMaintenanceAction {
  action: () => Promise<void>;
  confirmLabel: string;
  description: string;
  title: string;
  tone: "primary" | "warning" | "danger";
}

export function SettingsPage({
  settings,
  settingsLoading,
  saving,
  onSaveSettings,
  diagnostics,
  diagnosticsLoading,
  onReloadDiagnostics,
  modules,
  modulesBusyKey,
  onToggleModule,
}: SettingsPageProps) {
  const [configurationDrawerOpen, setConfigurationDrawerOpen] = useState(false);
  const [upgradeDetailsOpen, setUpgradeDetailsOpen] = useState(false);
  const [timezoneOptions, setTimezoneOptions] = useState<TimezoneOption[] | null>(null);
  const [pendingMaintenanceAction, setPendingMaintenanceAction] = useState<PendingMaintenanceAction | null>(null);
  const [workspaceMaintenanceBusy, setWorkspaceMaintenanceBusy] = useState(false);
  const [workspaceMaintenanceNotice, setWorkspaceMaintenanceNotice] = useState<{ tone: "success" | "error"; title: string; detail: string } | null>(null);
  const cache = useStudioCache(onReloadDiagnostics);
  const closeConfigurationDrawer = useCallback(() => setConfigurationDrawerOpen(false), []);
  const workspaceDatabase = diagnostics?.workspace_database;
  const analyticsCache = cache.status?.analytics_cache;
  const analyticsUpgrade = analyticsCache?.upgrade;
  const upgradeActive = analyticsUpgrade
    ? ["pending", "building", "validating", "publishing"].includes(analyticsUpgrade.state)
    : false;
  const upgradeFailed = analyticsUpgrade?.state === "failed";
  const upgradeUpToDate = analyticsUpgrade
    ? ["succeeded", "current", "not_required"].includes(analyticsUpgrade.state)
    : false;
  const upgradeProgress = analyticsUpgrade?.source_ids?.length
    ? ` · ${analyticsUpgrade.completed_source_ids?.length ?? 0}/${analyticsUpgrade.source_ids.length}`
    : "";
  const upgradeVersionText = analyticsUpgrade?.target_schema_version != null
    ? ` · v${analyticsUpgrade.target_schema_version}`
    : "";
  const lastUpgradeAt = analyticsUpgrade?.completed_at ?? analyticsUpgrade?.updated_at ?? null;
  const resultCache = cache.status?.result_cache;

  const maintenanceBusy = cache.busyAction !== null || workspaceMaintenanceBusy;

  const compactWorkspaceDatabase = useCallback(async () => {
    setWorkspaceMaintenanceBusy(true);
    setWorkspaceMaintenanceNotice(null);
    try {
      await api.compactWorkspaceDatabase();
      await onReloadDiagnostics();
      setWorkspaceMaintenanceNotice({ tone: "success", title: "Database compacted", detail: "Workspace database maintenance completed." });
    } catch (error) {
      setWorkspaceMaintenanceNotice({ tone: "error", title: "Database maintenance failed", detail: toErrorMessage(error) });
      throw error;
    } finally {
      setWorkspaceMaintenanceBusy(false);
    }
  }, [onReloadDiagnostics]);

  const confirmPendingMaintenanceAction = useCallback(() => {
    if (!pendingMaintenanceAction) return;
    setWorkspaceMaintenanceNotice(null);
    cache.dismissFeedback();
    void pendingMaintenanceAction.action()
      .catch(() => undefined)
      .finally(() => setPendingMaintenanceAction(null));
  }, [cache, pendingMaintenanceAction]);

  return (
    <div className="settings-layout settings-layout-single">
      <section className="table-panel studio-settings-panel">
        <div className="panel-toolbar">
          <div>
            <h2>Studio configuration</h2>
            <span>Global runtime</span>
          </div>
        </div>

        <section className="settings-section" aria-labelledby="configurable-settings-heading">
          <div className="settings-section-heading">
            <div className="settings-section-heading-copy">
              <h3 id="configurable-settings-heading">Configurable settings</h3>
              <span>Changes apply to this Studio.</span>
            </div>
            <button
              type="button"
              className="settings-section-action"
              onClick={() => setConfigurationDrawerOpen(true)}
              disabled={settingsLoading || !settings}
            >{settingsLoading ? "Loading…" : "Edit configuration"}</button>
          </div>
          <div className="settings-stat-grid">
            <div className="settings-stat-card">
              <p className="settings-stat-label">Timezone</p>
              <dl className="settings-stat-dl">
                <StatRow label="Active" value={settings?.timezone ?? loadingValue(settingsLoading)} />
                <StatRow
                  label="Source"
                  value={settings
                    ? (settings.timezone_source === "configured" ? "Studio override" : "Server default")
                    : loadingValue(settingsLoading)}
                />
              </dl>
            </div>
            <div className="settings-stat-card">
              <p className="settings-stat-label">Source change observation</p>
              <dl className="settings-stat-dl">
                <StatRow
                  label={settings?.source_check_mode === "adaptive" ? "Active interval" : "Check every"}
                  value={settings ? `${settings.source_check_interval_seconds} seconds` : loadingValue(settingsLoading)}
                />
                <StatRow
                  label="Mode"
                  value={settings
                    ? (settings.source_check_mode === "adaptive" ? "Adaptive" : "Fixed")
                    : loadingValue(settingsLoading)}
                />
                {settings?.source_check_mode === "adaptive" ? (
                  <StatRow label="Idle interval" value={`${settings.source_check_max_interval_seconds} seconds`} />
                ) : null}
                <StatRow label="Local metadata/code" value="On navigation / foreground" />
                <StatRow label="Log sources" value="Observed periodically; synced by schedule" />
                <StatRow label="Failure policy" value="Pause after 3 consecutive failures" />
              </dl>
            </div>
          </div>
        </section>

        <CredentialProfilesSection />

        <section className="settings-section" aria-labelledby="system-information-heading">
          <div className="settings-section-heading">
            <div className="settings-section-heading-copy">
              <h3 id="system-information-heading">System information</h3>
              <span>Runtime and core storage details.</span>
            </div>
          </div>
          <div className="settings-stat-grid">
            <div className="settings-stat-card">
              <div className="settings-stat-card-header"><p className="settings-stat-label">API</p></div>
              <dl className="settings-stat-dl"><StatRow label="Prefix" value="/api/v1" code /></dl>
            </div>
            <div className="settings-stat-card">
              <div className="settings-stat-card-header"><p className="settings-stat-label">Workspace database</p></div>
              <dl className="settings-stat-dl">
                <StatRow label="Backend" value={workspaceDatabase?.backend ?? loadingValue(diagnosticsLoading)} />
                <StatRow label={workspaceDatabase?.backend === "sqlite" ? "Path" : "Database"} value={workspaceDatabase?.path ?? loadingValue(diagnosticsLoading)} code />
                <StatRow label="Size" value={workspaceDatabase ? formatBytes(workspaceDatabase.size_bytes) : loadingValue(diagnosticsLoading)} />
              </dl>
              {workspaceDatabase?.maintenance_supported ? (
                <details className="settings-cache-maintenance">
                  <summary>Maintenance</summary>
                  <div className="settings-cache-maintenance-actions">
                    <button
                      type="button"
                      disabled={maintenanceBusy}
                      onClick={() => setPendingMaintenanceAction({
                        action: compactWorkspaceDatabase,
                        confirmLabel: "Compact database",
                        description: "SQLite will reclaim unused file space. Core data is preserved and writes may be briefly blocked.",
                        title: "Compact workspace database?",
                        tone: "primary",
                      })}
                    >Compact database</button>
                  </div>
                </details>
              ) : null}
            </div>
          </div>
        </section>

        <section className="settings-section" aria-labelledby="cache-storage-heading">
          <div className="settings-section-heading">
            <div className="settings-section-heading-copy">
              <h3 id="cache-storage-heading">Disposable cache storage</h3>
              <span>Current Metadata and Code materializations remain protected in the workspace database.</span>
            </div>
            <button
              type="button"
              className="settings-section-action settings-section-action-danger"
              disabled={maintenanceBusy}
              onClick={() => setPendingMaintenanceAction({
                action: () => cache.clear("all_disposable"),
                confirmLabel: "Clear all caches",
                description: "All disposable result and analytics caches will rebuild on demand. Core Studio state and current source materializations remain protected.",
                title: "Clear all disposable caches?",
                tone: "danger",
              })}
            >Clear all caches</button>
          </div>
          <div className="settings-stat-grid">
            <div className="settings-stat-card">
              <div className="settings-stat-card-header">
                <p className="settings-stat-label">Result cache</p>
                <button
                  type="button"
                  className="settings-section-action"
                  disabled={maintenanceBusy}
                  onClick={() => setPendingMaintenanceAction({
                    action: () => cache.clear("read_models"),
                    confirmLabel: "Clear result cache",
                    description: "Derived results will rebuild on demand. Metadata, Code, drafts, backups, settings, and sync history remain protected.",
                    title: "Clear result cache?",
                    tone: "warning",
                  })}
                >Clear</button>
              </div>
              <dl className="settings-stat-dl">
                <StatRow label="Backend" value={resultCache?.backend ?? loadingValue(cache.loading)} />
                <StatRow label="Path" value={resultCache?.path ?? loadingValue(cache.loading)} code />
                <StatRow label="Entries" value={resultCache ? formatCount(resultCache.entries) : loadingValue(cache.loading)} />
                <StatRow label="Payload" value={resultCache ? formatBytes(resultCache.payload_bytes) : loadingValue(cache.loading)} />
                <StatRow label="File" value={resultCache ? formatBytes(resultCache.file_bytes) : loadingValue(cache.loading)} />
              </dl>
              <details className="settings-cache-maintenance">
                <summary>Maintenance</summary>
                <div className="settings-cache-maintenance-actions">
                  <button
                    type="button"
                    disabled={maintenanceBusy}
                    onClick={() => setPendingMaintenanceAction({
                      action: cache.compact,
                      confirmLabel: "Optimize cache",
                      description: "Older result variants will be removed until the configured limits are met, then the result-cache SQLite file will reclaim unused space. Cache access may be briefly blocked.",
                      title: "Optimize result cache?",
                      tone: "primary",
                    })}
                  >Optimize result cache</button>
                </div>
              </details>
            </div>
            <div className="settings-stat-card">
              <div className="settings-stat-card-header">
                <p className="settings-stat-label">Analytics cache</p>
                {upgradeActive ? (
                  <span className="analytics-upgrade-indicator" role="status" aria-live="polite">
                    <Loader2 size={13} className="is-spinning" />
                    <span>{analyticsUpgradeLabel(analyticsUpgrade!.state)}{upgradeProgress}</span>
                  </span>
                ) : upgradeUpToDate ? (
                  <span className="analytics-upgrade-indicator is-current" role="status">
                    <CheckCircle2 size={13} />
                    <span>Up to date{upgradeVersionText}</span>
                  </span>
                ) : null}
                {analyticsUpgrade?.state === "failed" ? (
                  <button
                    type="button"
                    className="settings-section-action"
                    disabled={maintenanceBusy}
                    onClick={() => { void cache.retryUpgrade().catch(() => undefined); }}
                  >{cache.busyAction === "analytics-upgrade:retry" ? "Retrying…" : "Retry upgrade"}</button>
                ) : null}
                {analyticsUpgrade ? (
                  <button
                    type="button"
                    className="settings-section-action"
                    onClick={() => setUpgradeDetailsOpen(true)}
                  >View details</button>
                ) : null}
                <button
                  type="button"
                  className="settings-section-action"
                  disabled={maintenanceBusy}
                  onClick={() => setPendingMaintenanceAction({
                    action: () => cache.clear("analytics"),
                    confirmLabel: "Clear analytics cache",
                    description: "Parsed analytics and dependent Monitoring/Overview results will rebuild from configured log sources.",
                    title: "Clear analytics cache?",
                    tone: "warning",
                  })}
                >Clear</button>
              </div>
              <dl className="settings-stat-dl">
                <StatRow label="Backend" value={analyticsCache?.backend ?? loadingValue(cache.loading)} />
                <StatRow label="Path" value={analyticsCache?.path ?? loadingValue(cache.loading)} code />
                <StatRow label="File" value={analyticsCache ? formatBytes(analyticsCache.file_bytes) : loadingValue(cache.loading)} />
                {analyticsUpgrade?.target_schema_version != null ? (
                  <StatRow label="Schema version" value={`v${analyticsUpgrade.target_schema_version}`} />
                ) : null}
                {upgradeActive ? (
                  <>
                    <StatRow label="Upgrade" value={analyticsUpgradeLabel(analyticsUpgrade!.state)} />
                    {analyticsUpgrade?.source_ids?.length ? (
                      <StatRow
                        label="Progress"
                        value={`${analyticsUpgrade.completed_source_ids?.length ?? 0} / ${analyticsUpgrade.source_ids.length} sources`}
                      />
                    ) : null}
                    {analyticsUpgrade?.started_at ? (
                      <StatRow label="Started" value={formatRelativeTime(analyticsUpgrade.started_at) ?? "—"} />
                    ) : null}
                  </>
                ) : upgradeFailed ? (
                  <>
                    <StatRow label="Upgrade" value={analyticsUpgradeLabel("failed")} />
                    {analyticsUpgrade?.error_message ? (
                      <StatRow label="Upgrade error" value={analyticsUpgrade.error_message} />
                    ) : null}
                    <StatRow label="Last attempt" value={formatRelativeTime(lastUpgradeAt) ?? "—"} />
                    {analyticsUpgrade?.next_retry_at ? (
                      <StatRow label="Next retry" value={formatRelativeTime(analyticsUpgrade.next_retry_at) ?? "—"} />
                    ) : null}
                  </>
                ) : upgradeUpToDate ? (
                  <StatRow label="Last upgraded" value={lastUpgradeAt ? (formatRelativeTime(lastUpgradeAt) ?? "—") : "—"} />
                ) : null}
                <StatRow
                  label="Rows"
                  value={analyticsCache
                    ? `${formatCount(analyticsCache.dataflow_rows)} df · ${formatCount(analyticsCache.job_rows)} jobs`
                    : loadingValue(cache.loading)}
                />
              </dl>
            </div>
          </div>
        </section>
      </section>

      <section className="table-panel">
        <div className="panel-toolbar"><div><h2>Modules</h2><span>Enable or disable Studio capability modules</span></div></div>
        <div className="module-catalog">
          {modules.length === 0 ? <p className="module-card-desc">No modules registered.</p> : modules.map((module) => {
            const comingSoon = module.status === "coming_soon";
            return (
              <div key={module.key} className={`module-card${comingSoon ? " module-card-muted" : ""}`}>
                <div className="module-card-main">
                  <div className="module-card-icon"><Settings size={18} /></div>
                  <div>
                    <div className="module-card-title">
                      {module.name}
                      {comingSoon
                        ? <Tag tone="info">Coming soon</Tag>
                        : module.enabled ? <Tag tone="success">Enabled</Tag> : <Tag tone="neutral">Disabled</Tag>}
                    </div>
                    <p className="module-card-desc">{module.description}</p>
                  </div>
                </div>
                <div className="module-card-control">
                  <Toggle
                    checked={module.enabled}
                    label={`Toggle ${module.name} module`}
                    disabled={!module.togglable || modulesBusyKey === module.key}
                    onToggle={(next) => { void onToggleModule(module.key, next).catch(() => undefined); }}
                  />
                </div>
              </div>
            );
          })}
        </div>
      </section>

      {configurationDrawerOpen && settings ? (
        <StudioConfigurationDrawer
          settings={settings}
          saving={saving}
          timezoneOptions={timezoneOptions}
          onTimezoneOptionsLoaded={setTimezoneOptions}
          onSave={onSaveSettings}
          onClose={closeConfigurationDrawer}
        />
      ) : null}
      {upgradeDetailsOpen && analyticsUpgrade ? (
        <AnalyticsUpgradeDetailsDrawer
          upgrade={analyticsUpgrade}
          onClose={() => setUpgradeDetailsOpen(false)}
        />
      ) : null}
      {pendingMaintenanceAction ? (
        <OperationConfirmationDialog
          confirmIcon={pendingMaintenanceAction.tone === "danger" ? <Trash2 size={14} /> : undefined}
          confirmLabel={pendingMaintenanceAction.confirmLabel}
          description={pendingMaintenanceAction.description}
          icon={<AlertTriangle size={18} />}
          busy={maintenanceBusy}
          onCancel={() => setPendingMaintenanceAction(null)}
          onConfirm={confirmPendingMaintenanceAction}
          title={pendingMaintenanceAction.title}
          tone={pendingMaintenanceAction.tone}
        />
      ) : null}
      {workspaceMaintenanceNotice ? (
        <OperationNotification notice={workspaceMaintenanceNotice} onClose={() => setWorkspaceMaintenanceNotice(null)} />
      ) : cache.error ? (
        <OperationNotification
          notice={{ tone: "error", title: "Cache unavailable", detail: cache.error }}
          onClose={cache.dismissFeedback}
        />
      ) : cache.lastAction ? (
        <OperationNotification
          notice={{ tone: "success", title: "Cache updated", detail: cacheMutationMessage(cache.lastAction) }}
          onClose={cache.dismissFeedback}
        />
      ) : null}
    </div>
  );
}

function StatRow({ label, value, code = false }: { label: string; value: string; code?: boolean }) {
  return <div className="settings-stat-row"><dt>{label}</dt><dd>{code ? <code className="settings-path-value">{value}</code> : value}</dd></div>;
}

function loadingValue(loading: boolean): string {
  return loading ? "Loading…" : "—";
}

function analyticsUpgradeLabel(state: string): string {
  const labels: Record<string, string> = {
    pending: "Waiting to upgrade",
    building: "Rebuilding from Log sources",
    validating: "Validating candidate",
    publishing: "Publishing atomically",
    succeeded: "Current",
    current: "Current",
    failed: "Retry scheduled",
    not_required: "Not required",
  };
  return labels[state] ?? state;
}

function cacheMutationMessage(action: StudioCacheAction): string {
  if (action.type === "clear") {
    if (action.scope === "read_models") return "Result cache cleared.";
    if (action.scope === "analytics") return "Analytics cache cleared.";
    return "All disposable caches cleared.";
  }

  const prune = asRecord(action.result.read_models?.prune);
  const compact = asRecord(action.result.read_models);
  const deletedEntries = asNumber(prune?.deleted_entries) ?? 0;
  const beforeBytes = asNumber(compact?.file_bytes_before);
  const afterBytes = asNumber(compact?.file_bytes_after);
  const entryLabel = deletedEntries === 1 ? "entry" : "entries";
  if (beforeBytes !== null && afterBytes !== null) {
    const fileSummary = afterBytes < beforeBytes
      ? `file reduced from ${formatBytes(beforeBytes)} to ${formatBytes(afterBytes)}`
      : afterBytes === beforeBytes
        ? `file remains ${formatBytes(afterBytes)}`
        : `file changed from ${formatBytes(beforeBytes)} to ${formatBytes(afterBytes)}`;
    return `Result cache optimized. Removed ${formatCount(deletedEntries)} ${entryLabel}; ${fileSummary}.`;
  }
  return `Result cache optimized. Removed ${formatCount(deletedEntries)} ${entryLabel}.`;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function asNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function formatCount(value: number | null | undefined): string {
  if (!Number.isFinite(value)) return "0";
  return new Intl.NumberFormat("en-US").format(Number(value));
}

function formatBytes(value: number | null | undefined): string {
  if (!Number.isFinite(value) || Number(value) < 0) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let current = Number(value);
  let unitIndex = 0;
  while (current >= 1024 && unitIndex < units.length - 1) {
    current /= 1024;
    unitIndex += 1;
  }
  const precision = unitIndex === 0 ? 0 : current >= 100 ? 0 : current >= 10 ? 1 : 2;
  return `${current.toFixed(precision)} ${units[unitIndex]}`;
}
