import type { StudioRoute } from "./routes";

export interface ProjectRouteResources {
  projectSummaries: boolean;
  projects: boolean;
  environments: boolean;
}

const noProjectResources: ProjectRouteResources = {
  projectSummaries: false,
  projects: false,
  environments: false,
};

export function projectRouteResources(route: StudioRoute): ProjectRouteResources {
  if (route.module === "settings") return noProjectResources;

  if (route.module === "projects") {
    return {
      projectSummaries: true,
      projects: false,
      environments: false,
    };
  }

  return {
    projectSummaries: false,
    projects: false,
    environments: false,
  };
}
