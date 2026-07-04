import type { Environment, EnvironmentFreshness, Project } from "../shared/api/types";
import { RelativeTime } from "../shared/components/RelativeTime";
import type { ModuleKey, ModuleScope } from "./moduleRegistry";

interface ContextBarProps {
  activeModule: ModuleKey;
  scope: ModuleScope;
  projects: Project[];
  environments: Environment[];
  selectedProjectId: number | null;
  selectedEnvironmentId: number | null;
  metadataSourceCount: number;
  logPathCount: number;
  freshness: EnvironmentFreshness | null;
  onProjectSelect: (projectId: number | null) => void;
  onEnvironmentSelect: (environmentId: number | null) => void;
}

export function ContextBar({
  activeModule,
  scope,
  projects,
  environments,
  selectedProjectId,
  selectedEnvironmentId,
  metadataSourceCount,
  logPathCount,
  freshness,
  onProjectSelect,
  onEnvironmentSelect
}: ContextBarProps) {
  if (scope === "global") {
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
      <div className="context-selectors">
        <label>
          Project
          <select
            value={selectedProjectId ?? ""}
            onChange={(event) => onProjectSelect(event.target.value ? Number(event.target.value) : null)}
          >
            <option value="">Select project</option>
            {projects.map((project) => (
              <option key={project.id} value={project.id}>
                {project.name}
              </option>
            ))}
          </select>
        </label>
        {scope === "environment" ? (
          <label>
            Environment
            <select
              value={selectedEnvironmentId ?? ""}
              onChange={(event) => onEnvironmentSelect(event.target.value ? Number(event.target.value) : null)}
              disabled={!selectedProjectId}
            >
              <option value="">Select environment</option>
              {environments.map((environment) => (
                <option key={environment.id} value={environment.id}>
                  {environment.name}
                </option>
              ))}
            </select>
          </label>
        ) : null}
      </div>

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
    <strong className={`freshness-chip freshness-${status}`}>
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
    source_changed: "Source changed",
    not_cached: "Not cached",
    missing: "Missing",
    sync_failed: "Sync failed",
    unknown: "Unknown"
  }[status] ?? "Unknown";
}
