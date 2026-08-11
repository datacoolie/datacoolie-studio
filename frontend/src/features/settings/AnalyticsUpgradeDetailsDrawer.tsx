import { X } from "lucide-react";
import type { AnalyticsUpgradeStatus } from "../../shared/api/domainTypes";
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
            <h2>Upgrade details</h2>
            <small className="settings-drawer-summary">
              Current attempt and source processing times
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
              <Detail label="Attempt" value={String(upgrade.attempt_count ?? 0)} />
              <Detail label="Progress" value={sourceCount ? `${completedCount} / ${sourceCount} sources` : "No sources"} />
              <Detail label="Duration" value={formatDuration(upgrade.duration_seconds)} />
              <Detail label="Started" value={formatTimestamp(upgrade.started_at)} />
              <Detail label="Completed" value={formatTimestamp(upgrade.completed_at)} />
            </dl>
            {upgrade.error_message ? (
              <div className="analytics-upgrade-message is-error">
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
                        <strong>{source.label || `Source ${source.source_id}`}</strong>
                        <small>Source ID {source.source_id}</small>
                      </div>
                      <Tag tone={statusTone(source.status)}>{sourceStatusLabel(source.status)}</Tag>
                    </div>
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

function schemaLabel(upgrade: AnalyticsUpgradeStatus): string {
  const source = upgrade.source_schema_version == null ? "unknown" : `v${upgrade.source_schema_version}`;
  const target = upgrade.target_schema_version == null ? "unknown" : `v${upgrade.target_schema_version}`;
  return `${source} → ${target}`;
}

function upgradeStateLabel(state: string): string {
  return state.replace(/_/gu, " ");
}

function sourceStatusLabel(status: string): string {
  if (status === "succeeded") return "Completed";
  return upgradeStateLabel(status);
}

function statusTone(status: string): TagTone {
  if (["succeeded", "completed", "current", "not_required"].includes(status)) return "success";
  if (status === "failed") return "danger";
  if (["pending", "queued"].includes(status)) return "neutral";
  return "info";
}
