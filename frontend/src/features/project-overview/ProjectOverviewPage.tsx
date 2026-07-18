import { ArrowUpRight, CheckCircle2, Code2, Database, FolderOpen, GitBranch, Plus, Settings2 } from "lucide-react";
import type { ProjectEnvironmentSummary, ProjectSummary } from "../../shared/api/types";
import { EmptyState } from "../../shared/components/EmptyState";
import { orderedEnvironmentNamesWithMissing } from "../../shared/environmentOrder";
import {
  environmentReadiness,
  environmentReadinessLabel,
  environmentReadinessReason,
  projectReadinessSummary,
} from "../../shared/environmentReadiness";

interface ProjectOverviewPageProps {
  project: ProjectSummary | null;
  busy: boolean;
  mappingCount: number;
  onOpenEnvironment: (projectId: number, environmentId: number) => void;
  onOpenEnvironments: (projectId: number) => void;
  onConfigureSources: (projectId: number, environmentId: number) => void;
  onOpenReferenceMappings: () => void;
  onQuickCreateEnvironment: (projectId: number, name: string) => Promise<void>;
}

export function ProjectOverviewPage({
  project,
  busy,
  mappingCount,
  onOpenEnvironment,
  onOpenEnvironments,
  onConfigureSources,
  onOpenReferenceMappings,
  onQuickCreateEnvironment
}: ProjectOverviewPageProps) {
  if (!project) {
    return <EmptyState title={busy ? "Loading project" : "Project not found"} />;
  }

  const readiness = projectReadinessSummary(project.environments);
  const suggestedEnvironmentNames = orderedEnvironmentNamesWithMissing(project.environments)
    .filter((name) => !project.environments.some((environment) => environment.name === name));
  const environmentSummary = !readiness.total
    ? "No environments yet"
    : `${readiness.total} active · ${readiness.ready} ready`;
  const mappingSummary = mappingCount
    ? `${mappingCount} saved project ${mappingCount === 1 ? "mapping" : "mappings"} shared across environments`
    : "Add a project mapping when automatic resolution needs correction";

  return (
    <div className="view-stack">
      <div className="project-overview-workspace">
        <section className="table-panel project-overview-panel">
          <div className="panel-toolbar">
            <button className="panel-title-btn" onClick={() => onOpenEnvironments(project.id)}>
              <h2>Environments</h2>
              <span>{environmentSummary}</span>
            </button>
            <button className="icon-action project-overview-add-env" type="button" onClick={() => onOpenEnvironments(project.id)}>
              <Settings2 size={13} />
              <span>Manage environments</span>
            </button>
          </div>

          {project.environments.length ? (
            <div className="proj-env-grid">
              {project.environments.map((environment) => (
                <EnvironmentSetupCard
                  key={environment.id}
                  environment={environment}
                  projectId={project.id}
                  onOpenEnvironment={onOpenEnvironment}
                  onConfigureSources={onConfigureSources}
                />
              ))}
            </div>
          ) : (
            <div className="project-overview-empty">
              <strong>Set up your first environment</strong>
              <span>Add an environment, then connect its metadata source.</span>
            </div>
          )}

          {suggestedEnvironmentNames.length ? (
            <div className="project-suggested-environments">
              <span>Suggested environments</span>
              <div>
                {suggestedEnvironmentNames.map((name) => (
                  <button
                    key={name}
                    type="button"
                    disabled={busy}
                    onClick={() => onQuickCreateEnvironment(project.id, name)}
                  >
                    <Plus size={13} />
                    <span>Add {name}</span>
                  </button>
                ))}
              </div>
            </div>
          ) : null}
        </section>

        <aside className="project-overview-operations" aria-label="Project-level tools">
          <span className="project-overview-operations-label">Project-level tools</span>
          <button className="project-overview-operation" type="button" onClick={onOpenReferenceMappings} aria-label="Open reference mappings">
            <span className="project-overview-operation-icon"><GitBranch size={17} /></span>
            <span className="project-overview-operation-copy">
              <strong>Reference mappings</strong>
              <small>{mappingSummary}</small>
            </span>
            <ArrowUpRight size={15} aria-hidden="true" />
          </button>
        </aside>
      </div>
    </div>
  );
}

function EnvironmentSetupCard({
  environment,
  projectId,
  onOpenEnvironment,
  onConfigureSources,
}: {
  environment: ProjectEnvironmentSummary;
  projectId: number;
  onOpenEnvironment: (projectId: number, environmentId: number) => void;
  onConfigureSources: (projectId: number, environmentId: number) => void;
}) {
  const readiness = environmentReadiness(environment);
  const needsMetadata = readiness === "needs-metadata";

  return (
    <article className={`proj-env-card proj-env-card-${readiness}`}>
      <button
        className="proj-env-card-main"
        type="button"
        onClick={() => needsMetadata
          ? onConfigureSources(projectId, environment.id)
          : onOpenEnvironment(projectId, environment.id)}
      >
        <div className="proj-env-card-header">
          <span className="proj-env-card-name">{environment.name}</span>
          <span className={`proj-env-badge proj-env-badge-${readiness}`}>
            {readiness === "ready" ? <CheckCircle2 size={10} /> : null}
            {environmentReadinessLabel(readiness)}
          </span>
        </div>
        <div className="proj-env-card-stats">
          <span><Database size={11} />{environment.metadata_source_count} sources</span>
          <span><FolderOpen size={11} />{environment.etl_log_path_count} logs</span>
          <span><Code2 size={11} />{environment.code_artifact_count ?? 0} code</span>
        </div>
        {needsMetadata ? <div className="proj-env-card-reason">{environmentReadinessReason(readiness)}</div> : null}
        <div className="proj-env-card-cta">{needsMetadata ? "Add metadata source →" : "Open environment →"}</div>
      </button>
      {!needsMetadata ? (
        <button
          className="proj-env-card-configure"
          type="button"
          onClick={() => onConfigureSources(projectId, environment.id)}
          title={`Manage ${environment.name} sources`}
          aria-label={`Manage ${environment.name} sources`}
        >
          <Settings2 size={13} />
          <span>Manage sources</span>
        </button>
      ) : null}
    </article>
  );
}
