import { ChevronRight } from "lucide-react";
import type { EnvironmentContext } from "../shared/api/domainTypes";
import { RelativeTime } from "../shared/components/RelativeTime";
import type { ModuleKey, ModuleScope } from "./moduleRegistry";

interface ContextBarProps {
  activeModule: ModuleKey;
  scope: ModuleScope;
  project: { id: number; name: string } | null;
  environment: { id: number; name: string } | null;
  metadataSourceCount: number;
  logPathCount: number;
  freshness: EnvironmentContext["freshness"] | null;
  onProjectSelect: (projectId: number | null) => void;
  onOpenProject: (projectId: number) => void;
}

export function ContextBar({
  activeModule,
  scope,
  project,
  environment,
  metadataSourceCount,
  logPathCount,
  freshness,
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
        <div className="context-status">
          {freshness ? (
            <>
              <FreshnessPill
                label="Metadata"
                count={metadataSourceCount}
                countLabel="sources"
                modifiedAt={freshness.metadata.max_source_modified_at}
                status={freshness.metadata.status}
              />
              <FreshnessPill
                label="Logs"
                count={logPathCount}
                countLabel="paths"
                modifiedAt={freshness.etl_logs.max_source_modified_at}
                status={freshness.etl_logs.status}
              />
            </>
          ) : (
            <>
              <span>{metadataSourceCount} metadata sources</span>
              <span>{logPathCount} log paths</span>
            </>
          )}
        </div>
      ) : null}
    </header>
  );
}

function FreshnessPill({
  label,
  count,
  countLabel,
  modifiedAt,
  status
}: {
  label: string;
  count: number;
  countLabel: string;
  modifiedAt?: string | null;
  status: string;
}) {
  return (
    <strong className={`freshness-chip freshness-${status}`} title={freshnessDescription(label, status)}>
      <span className="freshness-label">{label}</span>
      <span className="freshness-count">
        {count} {countLabel}
      </span>
      <RelativeTime
        className="freshness-time"
        value={modifiedAt}
        fallback={modifiedAt ? "Modified unknown" : "No source modified time"}
        titlePrefix="Source modified"
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

function freshnessDescription(label: string, status: string) {
  return {
    current: `${label} source and Studio cache are aligned.`,
    not_cached: `${label} source has changes that are not synced into Studio yet.`,
    missing: `${label} source cannot be found at its configured path.`,
    sync_failed: `${label} source could not be synchronized into Studio.`,
    unknown: `${label} cache state is not available.`
  }[status] ?? `${label} cache state is not available.`;
}
