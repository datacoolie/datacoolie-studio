import { describe, expect, it } from "vitest";
import type { ProjectReferenceRegistryResponse } from "../../shared/api/domainTypes";
import { mergeProjectReferenceRegistry } from "./useProjectReferenceRegistry";

function registry(projectId: number, failures: ProjectReferenceRegistryResponse["failures"] = []): ProjectReferenceRegistryResponse {
  return { project_id: projectId, mappings: [], rows: [], targets: [], failures };
}

describe("mergeProjectReferenceRegistry", () => {
  it("treats server-computed rows as authoritative even when an environment failed", () => {
    const previous = registry(1);
    const response = registry(1, [{ environment_id: 2, environment_name: "prod", message: "unavailable" }]);

    expect(mergeProjectReferenceRegistry(previous, response)).toBe(response);
  });

  it("does not merge data across projects", () => {
    const response = registry(2);
    expect(mergeProjectReferenceRegistry(registry(1), response)).toBe(response);
  });
});
