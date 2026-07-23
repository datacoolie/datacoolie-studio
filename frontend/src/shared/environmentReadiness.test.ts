import { describe, expect, it } from "vitest";
import type { ProjectEnvironmentSummary } from "./api/domainTypes";
import { environmentReadiness, environmentReadinessLabel, projectReadinessSummary } from "./environmentReadiness";

function environment(metadataSources: number, logPaths: number, codeArtifacts = 0): ProjectEnvironmentSummary {
  return {
    id: 1,
    name: "dev",
    metadata_source_count: metadataSources,
    etl_log_path_count: logPaths,
    code_artifact_count: codeArtifacts,
    created_at: "2026-07-12T00:00:00Z",
    updated_at: "2026-07-12T00:00:00Z",
  };
}

describe("environment readiness", () => {
  it("treats metadata as the only required readiness prerequisite", () => {
    expect(environmentReadiness(environment(1, 0, 1))).toBe("ready");
    expect(environmentReadinessLabel(environmentReadiness(environment(1, 0)))).toBe("Ready");
  });

  it("requires metadata even when log paths or code artifacts exist", () => {
    expect(environmentReadiness(environment(0, 1))).toBe("needs-metadata");
    expect(environmentReadiness(environment(0, 0, 2))).toBe("needs-metadata");
  });

  it("summarizes only existing environments", () => {
    expect(projectReadinessSummary([environment(1, 0), environment(0, 1)])).toEqual({
      total: 2,
      ready: 1,
      needsMetadata: 1,
    });
  });
});
