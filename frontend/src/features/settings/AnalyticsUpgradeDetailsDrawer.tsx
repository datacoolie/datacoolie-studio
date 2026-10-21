import { X } from "lucide-react";
import type { AnalyticsUpgradeSourceProgress, AnalyticsUpgradeStatus } from "../../shared/api/domainTypes";
import { Tag, type TagTone } from "../../shared/components/Tag";
import { useDrawerEscape } from "../../shared/hooks/useDrawerEscape";
import { formatAbsoluteTime } from "../../shared/time";

interface AnalyticsUpgradeDetailsDrawerProps {
  upgrade: AnalyticsUpgradeStatus;
  onClose: () => void;
}

export function AnalyticsUpgradeDetailsDrawer({ upgrade, onClose }: AnalyticsUpgradeDetailsDrawerProps) {
  useDrawerEscape(onClose);

  const sourceCount = upgrade.source_ids?.length ?? upgrade.source_progress?.length ?? 0;
  const completedCount = completedSourceCount(upgrade);

  return (
    <div className="metadata-drawer-backdrop" onMouseDown={onClose}>
      <aside
        className="metadata-drawer analytics-upgrade-drawer"
        aria-label="Analytics upgrade details"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="metadata-drawer-header">
          <div>
            <span className="eyebrow">Analytics cache</span>
            <h2>{operationTitle(upgrade)}</h2>
            <small className="settings-drawer-summary">
              Current maintenance attempt and source processing times
            </small>
          </div>
          <button className="icon-action small" type="button" onClick={onClose} aria-label="Close analytics upgrade details">
            <X size={16} />
          </button>
        </header>

        <div className="metadata-drawer-body analytics-upgrade-drawer-body">
          <section className="analytics-upgrade-summary" aria-labelledby="analytics-upgrade-summary-heading">
            <div className="analytics-upgrade-section-heading">
              <h3 id="analytics-upgrade-summary-heading">Attempt summary</h3>
              <Tag tone={statusTone(upgrade.state)}>{upgradeStateLabel(upgrade.state)}</Tag>
            </div>
            <dl className="analytics-upgrade-summary-grid">
              <Detail label="Schema" value={schemaLabel(upgrade)} />
              <Detail label="Reason" value={reasonLabel(upgrade)} />
              <Detail label="Attempt" value={attemptLabel(upgrade)} />
              <Detail label="Progress" value={sourceCount ? `${completedCount} / ${sourceCount} sources` : "No sources"} />
              <Detail label="Duration" value={formatDuration(upgrade.duration_seconds)} />
              <Detail label="Started" value={formatTimestamp(upgrade.started_at)} />
              <Detail label="Completed" value={formatTimestamp(upgrade.completed_at)} />
            </dl>
            {upgrade.error_message ? (
              <div className={`analytics-upgrade-message ${upgrade.state === "completed_with_warnings" ? "is-warning" : "is-error"}`}>
                <strong>{upgrade.error_code ?? "Upgrade error"}</strong>
                <span>{upgrade.error_message}</span>
              </div>
            ) : null}
          </section>

          <section className="analytics-upgrade-sources" aria-labelledby="analytics-upgrade-sources-heading">
            <div className="analytics-upgrade-section-heading">
              <h3 id="analytics-upgrade-sources-heading">Sources</h3>
              <span>{sourceCount} total</span>
            </div>
            {upgrade.source_progress?.length ? (
              <div className="analytics-upgrade-source-list">
                {upgrade.source_progress.map((source) => (
                  <article className="analytics-upgrade-source" key={source.source_id}>
                    <div className="analytics-upgrade-source-header">
                      <div>
                        <strong>{sourceDisplayName(source)}</strong>
                        <small>{sourceContextLabel(source)}</small>
                      </div>
                      <div className="analytics-upgrade-source-status">
                        {sourceIsUnavailable(source) ? <Tag tone="warning">Not registered</Tag> : null}
                        <Tag tone={statusTone(source.status)}>{sourceStatusLabel(source.status)}</Tag>
                      </div>
                    </div>
                    <div className="analytics-upgrade-source-meta">
                      <span>{sourceKindLabel(source.source_kind)}</span>
                      <span>Source ID {source.source_id}</span>
                    </div>
                    {source.uri ? (
                      <p className="analytics-upgrade-source-uri" title={source.uri}>
                        <code>{source.uri}</code>
                      </p>
                    ) : null}
                    {sourceIsUnavailable(source) ? (
                      <p className="analytics-upgrade-source-message">
                        This source is no longer registered in the current workspace. The maintenance attempt can no longer process it.
                      </p>
                    ) : null}
                    <dl className="analytics-upgrade-source-timing">
                      <Detail label="Duration" value={formatDuration(source.duration_seconds)} />
                      <Detail label="Started" value={formatTimestamp(source.started_at)} />
                      <Detail label="Completed" value={formatTimestamp(source.completed_at)} />
                    </dl>
                    {source.message ? <p className="analytics-upgrade-source-message">{source.message}</p> : null}
                  </article>
                ))}
              </div>
            ) : (
              <p className="analytics-upgrade-empty">No per-source execution information is available for this attempt.</p>
            )}
          </section>
        </div>
      </aside>
    </div>
  );
}

function Detail({ label, value }: { label: string; value: string }) {
  return <div><dt>{label}</dt><dd>{value}</dd></div>;
}

export function formatDuration(seconds?: number | null): string {
  if (seconds == null || !Number.isFinite(seconds)) return "—";
  const rounded = Math.max(0, Math.round(seconds));
  const hours = Math.floor(rounded / 3600);
  const minutes = Math.floor((rounded % 3600) / 60);
  const remainingSeconds = rounded % 60;
  if (hours) return `${hours}h ${minutes}m ${remainingSeconds}s`;
  if (minutes) return `${minutes}m ${remainingSeconds}s`;
  return `${remainingSeconds}s`;
}

export function completedSourceCount(upgrade: AnalyticsUpgradeStatus): number {
  const completedIds = new Set(upgrade.completed_source_ids ?? []);
  for (const source of upgrade.source_progress ?? []) {
    if (["completed", "succeeded"].includes(source.status)) {
      completedIds.add(source.source_id);
    }
  }
  return completedIds.size;
}

function formatTimestamp(value?: string | null): string {
  return formatAbsoluteTime(value) ?? "—";
}

function sourceDisplayName(source: AnalyticsUpgradeSourceProgress): string {
  return source.label || source.uri || `Source ${source.source_id}`;
}

function sourceContextLabel(source: AnalyticsUpgradeSourceProgress): string {
  const context = [source.project_name, source.environment_name].filter(Boolean).join(" / ");
  if (context) return context;
  return sourceIsUnavailable(source)
    ? "Project / environment no longer available"
    : "Project / environment unavailable";
}

function sourceIsUnavailable(source: AnalyticsUpgradeSourceProgress): boolean {
  return source.is_registered === false
    || (source.is_registered == null && !source.label && !source.uri);
}

function sourceKindLabel(sourceKind?: string | null): string {
  const labels: Record<string, string> = {
    logs: "Logs source",
    metadata: "Metadata source",
    code: "Code source",
  };
  return labels[sourceKind ?? ""] ?? sourceKind ?? "Source";
}

function schemaLabel(upgrade: AnalyticsUpgradeStatus): string {
  const source = upgrade.source_schema_version == null ? "unknown" : `v${upgrade.source_schema_version}`;
  const target = upgrade.target_schema_version == null ? "unknown" : `v${upgrade.target_schema_version}`;
  if (upgrade.operation_kind === "source_repair" && source === target) return `Schema ${source}`;
  return `${source} → ${target}`;
}

function operationTitle(upgrade: AnalyticsUpgradeStatus): string {
  switch (upgrade.operation_kind) {
    case "schema_upgrade":
      return "Upgrade analytics schema";
    case "source_repair":
      return "Repair source cache";
    case "cache_rebuild":
    case "scope_reconcile":
      return "Rebuild analytics cache";
    case "initialize":
      return "Initialize analytics cache";
    default:
      return "Upgrade details";
  }
}

function reasonLabel(upgrade: AnalyticsUpgradeStatus): string {
  const labels: Record<string, string> = {
    missing_database: "Cache database missing",
    missing_source_coverage: "Source cache missing",
    schema_mismatch: "Schema migration required",
    cache_schema_incompatible: "Cache schema incompatible",
    cache_not_ready: "Cache not ready",
    cache_unavailable: "Cache unavailable",
    source_scope_changed: "Source scope changed",
  };
  return labels[upgrade.reason_code ?? ""] ?? upgrade.reason_code ?? "Maintenance requested";
}

function attemptLabel(upgrade: AnalyticsUpgradeStatus): string {
  const total = upgrade.attempt_count ?? 0;
  const episode = upgrade.episode_attempt_count;
  return episode && episode !== total ? `${episode} (total ${total})` : String(total);
}

function upgradeStateLabel(state: string): string {
  if (state === "repairing") return "Repairing source cache";
  if (state === "staging") return "Staging analytics candidate";
  if (state === "finalizing") return "Finalizing analytics candidate";
  if (state === "completed_with_warnings") return "Current with warnings";
  return state.replace(/_/gu, " ");
}

function sourceStatusLabel(status: string): string {
  if (status === "succeeded") return "Completed";
  return upgradeStateLabel(status);
}

function statusTone(status: string): TagTone {
  if (["succeeded", "completed", "current", "not_required"].includes(status)) return "success";
  if (status === "completed_with_warnings") return "warning";
  if (status === "failed") return "danger";
  if (["pending", "queued"].includes(status)) return "neutral";
  return "info";
}
