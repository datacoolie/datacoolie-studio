import { ArrowUpRight, CheckCircle2, Code2, Database, FolderOpen, Layers3, Plus, Search } from "lucide-react";
import { FormEvent, useMemo, useState } from "react";
import type { ProjectEnvironmentSummary, ProjectSummary } from "../../shared/api/types";
import { orderedEnvironmentNamesWithMissing } from "../../shared/environmentOrder";
import { environmentReadiness, environmentReadinessLabel } from "../../shared/environmentReadiness";

interface ProjectsPageProps {
  projects: ProjectSummary[];
  busy: boolean;
  onCreateProject: (name: string) => Promise<void>;
  onOpenEnvironment: (projectId: number, environmentId: number) => void;
  onOpenProject: (projectId: number) => void;
  onQuickCreateEnvironment: (projectId: number, name: string) => Promise<void>;
}

export function ProjectsPage({
  projects,
  busy,
  onCreateProject,
  onOpenEnvironment,
  onOpenProject,
  onQuickCreateEnvironment
}: ProjectsPageProps) {
  const [projectName, setProjectName] = useState("");
  const [query, setQuery] = useState("");
  const totals = useMemo(() => workspaceTotals(projects), [projects]);
  const sortedProjects = useMemo(
    () => [...projects].sort((left, right) => left.name.localeCompare(right.name, undefined, { numeric: true, sensitivity: "base" })),
    [projects]
  );
  const filteredProjects = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return needle ? sortedProjects.filter((project) => projectSearchText(project).includes(needle)) : sortedProjects;
  }, [query, sortedProjects]);

  async function submitProject(event: FormEvent) {
    event.preventDefault();
    if (!projectName.trim()) return;
    await onCreateProject(projectName.trim());
    setProjectName("");
  }

  const summary = query.trim()
    ? `${filteredProjects.length} matching · ${totals.projects} projects in workspace`
    : `${totals.projects} projects · ${totals.environments} environments · ${totals.metadataSources} metadata sources`;

  return (
    <div className="view-stack projects-directory">
      <section className="table-panel projects-panel projects-directory-panel" aria-labelledby="projects-heading">
        <div className="panel-toolbar projects-directory-toolbar">
          <div className="projects-directory-heading">
            <h2 id="projects-heading">Projects</h2>
            <span>{summary}</span>
          </div>
          <form className="projects-directory-create-form" onSubmit={submitProject}>
            <label htmlFor="new-project-name">
              <span>New project</span>
              <input
                id="new-project-name"
                value={projectName}
                onChange={(event) => setProjectName(event.target.value)}
                placeholder="Project name"
              />
            </label>
            <button type="submit" disabled={busy || !projectName.trim()}>
              <Plus size={15} />
              <span>Create project</span>
            </button>
          </form>
        </div>

        <div className="projects-directory-search">
          <Search size={15} aria-hidden="true" />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search projects, descriptions, environments"
            aria-label="Search projects"
          />
        </div>

        <div className="project-directory-list">
          {filteredProjects.map((project) => (
            <ProjectDirectoryRow
              key={project.id}
              project={project}
              busy={busy}
              onOpenEnvironment={onOpenEnvironment}
              onOpenProject={onOpenProject}
              onQuickCreateEnvironment={onQuickCreateEnvironment}
            />
          ))}
          {!projects.length ? <EmptyProjectsState /> : null}
          {projects.length && !filteredProjects.length ? <NoSearchMatchesState /> : null}
        </div>
      </section>
    </div>
  );
}

function ProjectDirectoryRow({
  project,
  busy,
  onOpenEnvironment,
  onOpenProject,
  onQuickCreateEnvironment
}: {
  project: ProjectSummary;
  busy: boolean;
  onOpenEnvironment: (projectId: number, environmentId: number) => void;
  onOpenProject: (projectId: number) => void;
  onQuickCreateEnvironment: (projectId: number, name: string) => Promise<void>;
}) {
  const readiness = projectReadiness(project);
  const codeArtifactCount = project.environments.reduce((total, environment) => total + environment.code_artifact_count, 0);

  return (
    <article className="project-directory-row">
      <div className="project-directory-identity">
        <button type="button" className="project-directory-title" onClick={() => onOpenProject(project.id)}>
          <span className="project-directory-icon"><Layers3 size={17} /></span>
          <span className="project-directory-copy">
            <strong>{project.name}</strong>
            {project.description ? <span>{project.description}</span> : null}
          </span>
        </button>
        <span className={`project-directory-readiness is-${readiness.tone}`}>
          {readiness.tone === "ready" ? <CheckCircle2 size={13} /> : <span className="project-status-dot" aria-hidden="true" />}
          {readiness.label}
        </span>
      </div>

      <div className="project-directory-environments">
        <span className="project-directory-label">Environments</span>
        <div className="project-environment-chip-list">
          {orderedEnvironmentNamesWithMissing(project.environments).map((name) => {
            const environment = project.environments.find((item) => item.name === name);
            return environment ? (
              <EnvironmentChip
                key={environment.id}
                environment={environment}
                onOpen={() => onOpenEnvironment(project.id, environment.id)}
              />
            ) : (
              <button
                key={name}
                type="button"
                className="project-environment-chip is-missing"
                disabled={busy}
                onClick={() => onQuickCreateEnvironment(project.id, name)}
                title={`Create ${name} environment`}
              >
                <Plus size={13} />
                <span>Create {name}</span>
              </button>
            );
          })}
        </div>
      </div>

      <div className="project-directory-coverage">
        <span className="project-directory-label">Coverage</span>
        <div>
          <span><Database size={13} />{project.metadata_source_count} sources</span>
          <span><FolderOpen size={13} />{project.etl_log_path_count} logs</span>
          <span><Code2 size={13} />{codeArtifactCount} code</span>
        </div>
      </div>

      <button type="button" className="project-directory-open" onClick={() => onOpenProject(project.id)}>
        <span>Open</span>
        <ArrowUpRight size={15} aria-hidden="true" />
      </button>
    </article>
  );
}

function EnvironmentChip({ environment, onOpen }: { environment: ProjectEnvironmentSummary; onOpen: () => void }) {
  const status = environmentReadiness(environment);
  return (
    <button type="button" className={`project-environment-chip is-${status}`} onClick={onOpen}>
      <span className="project-environment-status" aria-hidden="true" />
      <span className="project-environment-name">{environment.name}</span>
      <span className="project-environment-status-label">{environmentReadinessLabel(status)}</span>
    </button>
  );
}

function EmptyProjectsState() {
  return (
    <div className="project-directory-empty">
      <Layers3 size={20} />
      <div>
        <strong>No projects yet</strong>
        <span>Create a project to start grouping environments.</span>
      </div>
    </div>
  );
}

function NoSearchMatchesState() {
  return (
    <div className="project-directory-empty">
      <Search size={20} />
      <div>
        <strong>No projects match the search</strong>
        <span>Try another name, description, or environment.</span>
      </div>
    </div>
  );
}

function workspaceTotals(projects: ProjectSummary[]) {
  return projects.reduce((totals, project) => ({
    projects: totals.projects + 1,
    environments: totals.environments + project.environment_count,
    metadataSources: totals.metadataSources + project.metadata_source_count,
  }), { projects: 0, environments: 0, metadataSources: 0 });
}

function projectSearchText(project: ProjectSummary) {
  return [
    project.name,
    project.description,
    ...project.environments.map((environment) => environment.name),
  ]
    .filter((value) => value !== null && value !== undefined)
    .join(" ")
    .toLowerCase();
}

function projectReadiness(project: ProjectSummary) {
  const statuses = project.environments.map(environmentReadiness);
  const ready = statuses.filter((status) => status === "ready").length;
  const needsMetadata = statuses.length - ready;
  if (!statuses.length) return { tone: "empty", label: "No environments" };
  if (ready === statuses.length) return { tone: "ready", label: "All ready" };
  if (ready) return { tone: "needs-metadata", label: `${ready}/${statuses.length} ready` };
  return {
    tone: "needs-metadata",
    label: `${needsMetadata} ${needsMetadata === 1 ? "environment needs" : "environments need"} metadata`
  };
}
