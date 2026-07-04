import { CheckCircle2, Code2, Database, FolderOpen, Plus } from "lucide-react";
import type { ProjectSummary } from "../../shared/api/types";
import { EmptyState } from "../../shared/components/EmptyState";
import { orderedEnvironmentNamesWithMissing } from "../../shared/environmentOrder";

interface ProjectOverviewPageProps {
  project: ProjectSummary | null;
  busy: boolean;
  onOpenEnvironment: (projectId: number, environmentId: number) => void;
  onOpenEnvironments: (projectId: number) => void;
  onQuickCreateEnvironment: (projectId: number, name: string) => Promise<void>;
}

export function ProjectOverviewPage({ project, busy, onOpenEnvironment, onOpenEnvironments, onQuickCreateEnvironment }: ProjectOverviewPageProps) {
  if (!project) {
    return <EmptyState title={busy ? "Loading project" : "Project not found"} />;
  }

  const totalCode = project.environments.reduce((s, e) => s + (e.code_artifact_count ?? 0), 0);

  return (
    <div className="view-stack">
      {/* Compact KPI strip */}
      <div className="proj-kpi-strip">
        <div className="proj-kpi-item">
          <CheckCircle2 size={16} />
          <div><strong>{project.environment_count}</strong><span>Environments</span></div>
        </div>
        <div className="proj-kpi-item">
          <Database size={16} />
          <div><strong>{project.metadata_source_count}</strong><span>Sources</span></div>
        </div>
        <div className="proj-kpi-item">
          <FolderOpen size={16} />
          <div><strong>{project.etl_log_path_count}</strong><span>Log paths</span></div>
        </div>
        <div className="proj-kpi-item">
          <Code2 size={16} />
          <div><strong>{totalCode}</strong><span>Code</span></div>
        </div>
      </div>

      {/* Environments panel */}
      <section className="table-panel project-overview-panel">
        <div className="panel-toolbar">
          <button className="panel-title-btn" onClick={() => onOpenEnvironments(project.id)}>
            <h2>Environments</h2>
            <span>{project.environment_count} configured</span>
          </button>
          <button className="icon-action" onClick={() => onOpenEnvironments(project.id)}>
            <Plus size={14} />
            <span>Add environment</span>
          </button>
        </div>
        <div className="proj-env-grid">
          {orderedEnvironmentNamesWithMissing(project.environments).map((name) => {
            const env = project.environments.find((item) => item.name === name);
            if (env) {
              const hasMetadata = env.metadata_source_count > 0;
              const hasLogs = env.etl_log_path_count > 0;
              const status: "ready" | "partial" | "empty" =
                hasMetadata && hasLogs ? "ready"
                : (hasMetadata || hasLogs || (env.code_artifact_count ?? 0) > 0) ? "partial"
                : "empty";
              return (
                <button key={name} className={`proj-env-card proj-env-card-${status}`} onClick={() => onOpenEnvironment(project.id, env.id)}>
                  <div className="proj-env-card-header">
                    <span className="proj-env-card-name">{name}</span>
                    {status === "ready" && <span className="proj-env-badge proj-env-badge-ready"><CheckCircle2 size={10} />ready</span>}
                    {status === "partial" && <span className="proj-env-badge proj-env-badge-partial">partial</span>}
                    {status === "empty" && <span className="proj-env-badge proj-env-badge-empty">empty</span>}
                  </div>
                  <div className="proj-env-card-stats">
                    <span><Database size={11} />{env.metadata_source_count} src</span>
                    <span><FolderOpen size={11} />{env.etl_log_path_count} log</span>
                    <span><Code2 size={11} />{env.code_artifact_count ?? 0} code</span>
                  </div>
                  <div className="proj-env-card-cta">Open workspace →</div>
                </button>
              );
            }
            return (
              <button key={name} className="proj-env-card proj-env-card-missing" disabled={busy} onClick={() => onQuickCreateEnvironment(project.id, name)} title={`Create ${name} environment`}>
                <div className="proj-env-card-header">
                  <span className="proj-env-card-name">{name}</span>
                  <span className="proj-env-badge proj-env-badge-missing">not created</span>
                </div>
                <div className="proj-env-card-cta muted">+ Create &amp; configure →</div>
              </button>
            );
          })}
        </div>
      </section>
    </div>
  );
}
