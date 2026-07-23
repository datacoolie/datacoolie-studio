import type { ProjectEnvironmentSummary, ProjectSummary } from "../../shared/api/domainTypes";
import { environmentReadiness } from "../../shared/environmentReadiness";

export function workspaceTotals(projects: ProjectSummary[]) {
  return projects.reduce((totals, project) => ({
    projects: totals.projects + 1,
    environments: totals.environments + project.environment_count,
    metadataSources: totals.metadataSources + project.metadata_source_count,
  }), { projects: 0, environments: 0, metadataSources: 0 });
}

export function filterAndSortProjects(projects: ProjectSummary[], query: string) {
  const sorted = [...projects].sort((left, right) =>
    left.name.localeCompare(right.name, undefined, { numeric: true, sensitivity: "base" })
  );
  const needle = query.trim().toLowerCase();
  return needle ? sorted.filter((project) => projectSearchText(project).includes(needle)) : sorted;
}

export function environmentsByName(environments: ProjectEnvironmentSummary[]) {
  return new Map(environments.map((environment) => [environment.name, environment]));
}

export function projectReadiness(project: ProjectSummary) {
  const statuses = project.environments.map(environmentReadiness);
  const ready = statuses.filter((status) => status === "ready").length;
  const needsMetadata = statuses.length - ready;
  if (!statuses.length) return { tone: "empty", label: "No environments" } as const;
  if (ready === statuses.length) return { tone: "ready", label: "All ready" } as const;
  if (ready) return { tone: "needs-metadata", label: `${ready}/${statuses.length} ready` } as const;
  return {
    tone: "needs-metadata",
    label: `${needsMetadata} ${needsMetadata === 1 ? "environment needs" : "environments need"} metadata`,
  } as const;
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
