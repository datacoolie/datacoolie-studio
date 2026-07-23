import type { Environment, Project, ProjectEnvironmentSummary, ProjectSummary } from "../shared/api/domainTypes";

export function addProjectSummary(items: ProjectSummary[], project: Project) {
  return [...items, {
    ...project,
    environment_count: 0,
    metadata_source_count: 0,
    etl_log_path_count: 0,
    reference_mapping_count: 0,
    environments: [],
  }].sort(compareProjects);
}

export function addEnvironmentToProject(items: ProjectSummary[], projectId: number, environment: Environment) {
  const summary: ProjectEnvironmentSummary = {
    id: environment.id,
    name: environment.name,
    metadata_source_count: 0,
    etl_log_path_count: 0,
    code_artifact_count: 0,
    created_at: environment.created_at,
    updated_at: environment.updated_at,
  };
  return items.map((project) => project.id === projectId
    ? {
        ...project,
        environment_count: project.environment_count + 1,
        environments: [...project.environments, summary].sort(compareEnvironments),
      }
    : project);
}

export function removeEnvironmentFromProject(items: ProjectSummary[], projectId: number | null, environmentId: number) {
  return items.map((project) => {
    if (project.id !== projectId) return project;
    const removed = project.environments.find((environment) => environment.id === environmentId);
    if (!removed) return project;
    return {
      ...project,
      environment_count: Math.max(0, project.environment_count - 1),
      metadata_source_count: Math.max(0, project.metadata_source_count - removed.metadata_source_count),
      etl_log_path_count: Math.max(0, project.etl_log_path_count - removed.etl_log_path_count),
      environments: project.environments.filter((environment) => environment.id !== environmentId),
    };
  });
}

export function changeProjectReferenceMappingCount(items: ProjectSummary[], projectId: number, delta: number) {
  return items.map((project) => project.id === projectId
    ? { ...project, reference_mapping_count: Math.max(0, project.reference_mapping_count + delta) }
    : project);
}

function compareProjects(left: ProjectSummary, right: ProjectSummary) {
  return left.name.localeCompare(right.name, undefined, { numeric: true, sensitivity: "base" });
}

function compareEnvironments(left: ProjectEnvironmentSummary, right: ProjectEnvironmentSummary) {
  return left.name.localeCompare(right.name, undefined, { numeric: true, sensitivity: "base" });
}
