import { CheckCircle2, Code2, Database, FolderOpen, Layers3, Plus, PlusCircle } from "lucide-react";
import { FormEvent, useMemo, useState } from "react";
import type { ProjectSummary } from "../../shared/api/types";
import { orderedEnvironmentNamesWithMissing } from "../../shared/environmentOrder";

interface ProjectsPageProps {
  projects: ProjectSummary[];
  busy: boolean;
  onCreateProject: (name: string) => Promise<void>;
  onOpenEnvironment: (projectId: number, environmentId: number) => void;
  onOpenProject: (projectId: number) => void;
  onOpenProjectEnvironments: (projectId: number) => void;
  onQuickCreateEnvironment: (projectId: number, name: string) => Promise<void>;
}

export function ProjectsPage({
  projects,
  busy,
  onCreateProject,
  onOpenEnvironment,
  onOpenProject,
  onOpenProjectEnvironments,
  onQuickCreateEnvironment
}: ProjectsPageProps) {
  const [projectName, setProjectName] = useState("");
  const totals = useMemo(
    () => ({
      projects: projects.length,
      environments: projects.reduce((sum, project) => sum + project.environment_count, 0),
      metadataSources: projects.reduce((sum, project) => sum + project.metadata_source_count, 0),
      logPaths: projects.reduce((sum, project) => sum + project.etl_log_path_count, 0)
    }),
    [projects]
  );

  async function submitProject(event: FormEvent) {
    event.preventDefault();
    if (!projectName.trim()) return;
    await onCreateProject(projectName.trim());
    setProjectName("");
  }

  return (
    <div className="view-stack">
      <div className="projects-kpi-strip">
        <KpiTile label="Projects" value={totals.projects} icon={<Layers3 size={18} />} />
        <KpiTile label="Environments" value={totals.environments} icon={<CheckCircle2 size={18} />} />
        <KpiTile label="Metadata sources" value={totals.metadataSources} icon={<Database size={18} />} />
        <KpiTile label="Log paths" value={totals.logPaths} icon={<FolderOpen size={18} />} />
      </div>

      <section className="table-panel projects-panel">
        <div className="panel-toolbar">
          <h2>Projects</h2>
          <span>{projects.length} configured</span>
        </div>

        <div className="projects-add-row">
          <form className="settings-form projects-create-form" onSubmit={submitProject}>
            <label>
              Project name
              <input value={projectName} onChange={(event) => setProjectName(event.target.value)} placeholder="New project name" />
            </label>
            <button type="submit" disabled={busy || !projectName.trim()}>
              <Plus size={15} />
              <span>Create project</span>
            </button>
          </form>
        </div>

        <div className="project-card-list">
          {projects.map((project) => (
            <article key={project.id} className="project-card">
              <div className="project-card-header">
                <button className="project-card-title-btn" onClick={() => onOpenProject(project.id)}>
                  <strong><Layers3 size={14} />{project.name}</strong>
                  {project.description ? <span>{project.description}</span> : null}
                </button>
                <div className="project-card-header-actions">
                  <button className="text-action" onClick={() => onOpenProject(project.id)}>Overview</button>
                  <button className="text-action" onClick={() => onOpenProjectEnvironments(project.id)}>Environments</button>
                </div>
              </div>

              <div className="project-card-stats">
                <span><Database size={13} />{project.metadata_source_count} metadata sources</span>
                <span><FolderOpen size={13} />{project.etl_log_path_count} log paths</span>
                <span><Layers3 size={13} />{project.environment_count} environments</span>
              </div>

              <div className="project-card-envs">
                <span className="project-card-envs-label">Environments</span>
                <div className="project-env-tiles">
                  {orderedEnvironmentNamesWithMissing(project.environments).map((name) => {
                    const env = project.environments.find((item) => item.name === name);
                    return env ? (() => {
                      const hasMetadata = env.metadata_source_count > 0;
                      const hasLogs = env.etl_log_path_count > 0;
                      const status = hasMetadata && hasLogs ? "ready"
                        : (hasMetadata || hasLogs || env.code_artifact_count > 0) ? "partial"
                        : "empty";
                      return (
                        <button key={name} className={`project-env-tile ready`} onClick={() => onOpenEnvironment(project.id, env.id)}>
                          <div className="project-env-tile-header">
                            <span className="project-env-tile-name">{name}</span>
                            {status === "ready" && <span className="project-env-tile-ready"><CheckCircle2 size={11} />ready</span>}
                            {status === "partial" && <span className="project-env-tile-partial">partial</span>}
                            {status === "empty" && <span className="project-env-tile-missing">empty</span>}
                          </div>
                          <div className="project-env-tile-stats-row">
                            <span title="Metadata sources"><Database size={10} />{env.metadata_source_count} src</span>
                            <span title="Log paths"><FolderOpen size={10} />{env.etl_log_path_count} log</span>
                            <span title="Code artifacts"><Code2 size={10} />{env.code_artifact_count} code</span>
                          </div>
                        </button>
                      );
                    })() : (
                      <button key={name} className="project-env-tile missing" disabled={busy} onClick={() => onQuickCreateEnvironment(project.id, name)} title={`Create ${name} environment`}>
                        <div className="project-env-tile-header">
                          <span className="project-env-tile-name">{name}</span>
                          <span className="project-env-tile-missing">not created</span>
                        </div>
                        <div className="project-env-tile-stats-row muted">
                          <span><PlusCircle size={10} />click to create</span>
                        </div>
                      </button>
                    );
                  })}
                </div>
              </div>
            </article>
          ))}
          {!projects.length ? (
            <div className="project-card-empty">
              <Layers3 size={20} />
              <div>
                <strong>No projects yet</strong>
                <span>Create your first project using the form above.</span>
              </div>
            </div>
          ) : null}
        </div>
      </section>
    </div>
  );
}

function KpiTile({ label, value, icon }: { label: string; value: number; icon: React.ReactNode }) {
  return (
    <div className="projects-kpi-tile">
      <span className="projects-kpi-icon">{icon}</span>
      <div>
        <strong>{value}</strong>
        <span>{label}</span>
      </div>
    </div>
  );
}
