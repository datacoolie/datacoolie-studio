import { describe, expect, it } from "vitest";
import type { Environment, Project, ProjectSummary } from "../shared/api/domainTypes";
import {
  addEnvironmentToProject,
  addProjectSummary,
  changeProjectReferenceMappingCount,
  removeEnvironmentFromProject,
  renameEnvironmentInProject,
  renameProjectSummary,
} from "./projectSummaryMutations";

const timestamp = "2026-07-18T00:00:00Z";
const project: Project = { id: 2, name: "Beta", description: null, created_at: timestamp, updated_at: timestamp };
const environment: Environment = { id: 3, project_id: 2, name: "dev", created_at: timestamp, updated_at: timestamp };

describe("Project summary mutations", () => {
  it("adds a created Project without a summary reload", () => {
    expect(addProjectSummary([], project)).toEqual([{
      ...project,
      environment_count: 0,
      metadata_source_count: 0,
      etl_log_path_count: 0,
      reference_mapping_count: 0,
      environments: [],
    }]);
  });

  it("adds and removes an Environment while reconciling aggregate counts", () => {
    const initial: ProjectSummary[] = [{
      ...project,
      environment_count: 0,
      metadata_source_count: 0,
      etl_log_path_count: 0,
      reference_mapping_count: 0,
      environments: [],
    }];
    const added = addEnvironmentToProject(initial, project.id, environment);
    expect(added[0].environment_count).toBe(1);
    expect(added[0].environments[0]).toMatchObject({ id: 3, name: "dev", metadata_source_count: 0 });

    const withCounts: ProjectSummary[] = [{
      ...added[0],
      metadata_source_count: 2,
      etl_log_path_count: 1,
      environments: [{ ...added[0].environments[0], metadata_source_count: 2, etl_log_path_count: 1 }],
    }];
    expect(removeEnvironmentFromProject(withCounts, project.id, environment.id)[0]).toMatchObject({
      environment_count: 0,
      metadata_source_count: 0,
      etl_log_path_count: 0,
      environments: [],
    });
  });

  it("changes mapping counts only for the owning Project and never below zero", () => {
    const initial = addProjectSummary([], project);
    expect(changeProjectReferenceMappingCount(initial, project.id, 1)[0].reference_mapping_count).toBe(1);
    expect(changeProjectReferenceMappingCount(initial, project.id, -1)[0].reference_mapping_count).toBe(0);
  });

  it("replaces and re-sorts renamed Projects and Environments", () => {
    const alpha: Project = { ...project, id: 1, name: "Alpha" };
    const summaries = [
      addProjectSummary([], alpha)[0],
      {
        ...addProjectSummary([], project)[0],
        environment_count: 2,
        environments: [
          {
            ...environment,
            id: 4,
            name: "prod",
            metadata_source_count: 0,
            etl_log_path_count: 0,
            code_artifact_count: 0,
          },
          {
            ...environment,
            metadata_source_count: 0,
            etl_log_path_count: 0,
            code_artifact_count: 0,
          },
        ],
      },
    ];

    const renamedProjects = renameProjectSummary(
      summaries,
      { ...project, name: "00 Beta", updated_at: "2026-07-29T01:00:00Z" },
    );
    expect(renamedProjects.map((item) => item.id)).toEqual([2, 1]);
    expect(renamedProjects[0]).toMatchObject({
      name: "00 Beta",
      updated_at: "2026-07-29T01:00:00Z",
      environment_count: 2,
    });

    const renamedEnvironments = renameEnvironmentInProject(
      renamedProjects,
      {
        ...environment,
        id: 4,
        name: "00-prod",
        updated_at: "2026-07-29T02:00:00Z",
      },
    );
    expect(renamedEnvironments[0].environments.map((item) => item.id)).toEqual([4, 3]);
    expect(renamedEnvironments[0].environments[0]).toMatchObject({
      name: "00-prod",
      updated_at: "2026-07-29T02:00:00Z",
    });
    expect(renamedEnvironments[1]).toBe(renamedProjects[1]);
  });
});
