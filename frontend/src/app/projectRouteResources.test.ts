import { describe, expect, it } from "vitest";
import type { StudioRoute } from "./routes";
import { projectRouteResources } from "./projectRouteResources";

function resources(route: Partial<StudioRoute>) {
  return projectRouteResources({
    projectId: null,
    environmentId: null,
    module: "projects",
    ...route,
  });
}

describe("projectRouteResources", () => {
  it("loads only summaries for the Projects directory", () => {
    expect(resources({})).toEqual({
      projectSummaries: true,
      projects: false,
      environments: false,
    });
  });

  it("loads the exact resources for each Project section", () => {
    expect(resources({ projectId: 1, projectSection: "overview" })).toEqual({
      projectSummaries: true,
      projects: false,
      environments: false,
    });
    expect(resources({ projectId: 1, projectSection: "environments" })).toEqual({
      projectSummaries: true,
      projects: false,
      environments: false,
    });
    expect(resources({ projectId: 1, projectSection: "reference-mappings" })).toEqual({
      projectSummaries: true,
      projects: false,
      environments: false,
    });
  });

  it("leaves Environment identity to the shared context query", () => {
    expect(resources({ projectId: 1, environmentId: 2, module: "sources" })).toEqual({
      projectSummaries: false,
      projects: false,
      environments: false,
    });
  });

  it("loads no Project resources for Settings", () => {
    expect(resources({ module: "settings" })).toEqual({
      projectSummaries: false,
      projects: false,
      environments: false,
    });
  });
});
