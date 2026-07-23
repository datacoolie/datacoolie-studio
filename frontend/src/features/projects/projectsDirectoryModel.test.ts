import { describe, expect, it } from "vitest";
import type { ProjectEnvironmentSummary, ProjectSummary } from "../../shared/api/domainTypes";
import { environmentsByName, filterAndSortProjects, projectReadiness, workspaceTotals } from "./projectsDirectoryModel";

const timestamp = "2026-07-18T00:00:00Z";

function environment(overrides: Partial<ProjectEnvironmentSummary> = {}): ProjectEnvironmentSummary {
  return {
    id: 1,
    name: "dev",
    metadata_source_count: 1,
    etl_log_path_count: 0,
    code_artifact_count: 0,
    created_at: timestamp,
    updated_at: timestamp,
    ...overrides,
  };
}

function project(overrides: Partial<ProjectSummary> = {}): ProjectSummary {
  return {
    id: 1,
    name: "Project 2",
    description: null,
    environment_count: 1,
    metadata_source_count: 1,
    etl_log_path_count: 0,
    reference_mapping_count: 0,
    environments: [environment()],
    created_at: timestamp,
    updated_at: timestamp,
    ...overrides,
  };
}

describe("Projects directory model", () => {
  it("calculates workspace totals", () => {
    expect(workspaceTotals([
      project(),
      project({ id: 2, environment_count: 3, metadata_source_count: 4 }),
    ])).toEqual({ projects: 2, environments: 4, metadataSources: 5 });
  });

  it("sorts naturally and searches names, descriptions, and environments", () => {
    const projects = [
      project({ id: 10, name: "Project 10", description: "Finance" }),
      project({ id: 2, name: "Project 2", environments: [environment({ name: "production" })] }),
    ];

    expect(filterAndSortProjects(projects, "").map((item) => item.id)).toEqual([2, 10]);
    expect(filterAndSortProjects(projects, "finance").map((item) => item.id)).toEqual([10]);
    expect(filterAndSortProjects(projects, "PRODUCTION").map((item) => item.id)).toEqual([2]);
  });

  it("derives readiness and indexes environments once", () => {
    const dev = environment({ id: 1, name: "dev" });
    const prod = environment({ id: 2, name: "prod", metadata_source_count: 0 });
    const item = project({ environments: [dev, prod], environment_count: 2 });

    expect(projectReadiness(item)).toEqual({ tone: "needs-metadata", label: "1/2 ready" });
    expect(environmentsByName(item.environments).get("prod")).toBe(prod);
  });
});
