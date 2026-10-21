import { ChevronRight } from "lucide-react";
import type { EnvironmentContext } from "../shared/api/domainTypes";
import { RelativeTime } from "../shared/components/RelativeTime";
import { NotificationCenter } from "../shared/notifications/NotificationCenter";
import { formatAbsoluteTime } from "../shared/time";
import type { ModuleKey, ModuleScope } from "./moduleRegistry";

interface ContextBarProps {
  activeModule: ModuleKey;
  scope: ModuleScope;
  project: { id: number; name: string } | null;
  environment: { id: number; name: string } | null;
  metadataSourceCount: number;
  logSourceCount: number;
  freshness: EnvironmentContext["freshness"] | null;
  timezoneName: string | null;
  onProjectSelect: (projectId: number | null) => void;
  onOpenProject: (projectId: number) => void;
}

export function ContextBar({
  activeModule,
  scope,
  project,
  environment,
  metadataSourceCount,
  logSourceCount,
  freshness,
  timezoneName,
  onProjectSelect,
  onOpenProject
}: ContextBarProps) {
  if (scope === "global" && !project) {
    return (
      <header className="context-bar context-bar-global">
        <div>
          <span className="eyebrow">Studio</span>
          <h2>{activeModule === "settings" ? "Settings" : "Projects"}</h2>
        </div>
        <NotificationCenter />
      </header>
    );
  }

  return (
    <header className="context-bar">
      <nav className="context-trail" aria-label="Current location">
        <button className="context-trail-link" type="button" onClick={() => onProjectSelect(null)}>Projects</button>
        {project ? (
          <>
            <ChevronRight className="context-trail-separator" size={14} aria-hidden="true" />
            <button className="context-trail-project" type="button" onClick={() => onOpenProject(project.id)}>{project.name}</button>
          </>
        ) : null}
        {environment ? (
          <>
            <ChevronRight className="context-trail-separator" size={14} aria-hidden="true" />
            <span className="context-trail-current" aria-current="page">{environment.name}</span>
          </>
        ) : null}
        {scope === "environment" ? <span className="context-trail-module">{activeModule}</span> : null}
      </nav>
      {scope === "environment" ? (
        <div className="context-bar-actions">
          <div className="context-status">
            {freshness ? (
              <>
                <FreshnessPill
                  label="Metadata"
                  count={metadataSourceCount}
                  countLabel="sources"
                  modifiedAt={freshness.metadata.max_source_modified_at}
                  status={freshness.metadata.status}
                  pendingSyncCount={freshness.metadata.pending_sync_count ?? 0}
                  timezoneName={timezoneName}
                />
                <FreshnessPill
                  label="Logs"
                  count={logSourceCount}
                  countLabel="sources"
                  modifiedAt={freshness.logs.max_source_modified_at}
                  status={freshness.logs.status}
                  pendingSyncCount={freshness.logs.pending_sync_count ?? 0}
                  timezoneName={timezoneName}
                />
              </>
            ) : (
              <>
                <span>{metadataSourceCount} metadata sources</span>
                <span>{logSourceCount} log sources</span>
              </>
            )}
          </div>
          <NotificationCenter />
        </div>
      ) : <NotificationCenter />}
    </header>
  );
}

function FreshnessPill({
  label,
  count,
  countLabel,
  modifiedAt,
  status,
  pendingSyncCount,
  timezoneName
}: {
  label: string;
  count: number;
  countLabel: string;
  modifiedAt?: string | null;
  status: string;
  pendingSyncCount: number;
  timezoneName: string | null;
}) {
  return (
    <strong
      className={`freshness-chip freshness-${status}`}
      title={freshnessDescription(label, status, modifiedAt, count, countLabel, pendingSyncCount, timezoneName)}
    >
      <span className="freshness-label">{label}</span>
      <span className="freshness-count">
        {count} {countLabel}
      </span>
      <RelativeTime
        className="freshness-time"
        value={modifiedAt}
        fallback={modifiedAt ? "Modified unknown" : "No source modified time"}
        titlePrefix="Source modified"
        timezoneName={timezoneName}
      />
      <em className={`freshness-status freshness-status-${status}`}>{freshnessLabel(status)}</em>
    </strong>
  );
}

function freshnessLabel(status: string) {
  return {
    current: "Current",
    not_cached: "Not synced",
    missing: "Missing",
    sync_failed: "Sync failed",
    unknown: "Unknown"
  }[status] ?? "Unknown";
}

function freshnessDescription(
  label: string,
  status: string,
  modifiedAt: string | null | undefined,
  count: number,
  countLabel: string,
  pendingSyncCount: number,
  timezoneName: string | null
) {
  const absolute = formatAbsoluteTime(modifiedAt, timezoneName);
  const sourceClause = absolute ? `Source modified: ${absolute}` : "Source modified time unavailable";
  const cacheClause = pendingSyncCount > 0
    ? `Cache: ${pendingSyncCount} of ${count} ${countLabel} not synced`
    : status === "current"
      ? "Cache: aligned"
      : `Cache: ${freshnessLabel(status)}`;
  return `${label} · ${sourceClause} · ${cacheClause}`;
}
