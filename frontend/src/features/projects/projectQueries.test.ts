import { QueryClient } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../../shared/api/client";
import type { ProjectSummary } from "../../shared/api/domainTypes";
import { projectQueryKeys, projectSummariesOptions } from "./projectQueries";

const summaries: ProjectSummary[] = [
  {
    id: 1,
    name: "Analytics",
    description: null,
    created_at: "2026-09-19T00:00:00Z",
    updated_at: "2026-09-19T00:00:00Z",
    environment_count: 0,
    metadata_source_count: 0,
    log_source_count: 0,
    reference_mapping_count: 0,
    environments: [],
  },
];

afterEach(() => vi.restoreAllMocks());

describe("Project summary query ownership", () => {
  it("uses one stable feature-owned cache identity", () => {
    expect(projectQueryKeys.summaries()).toEqual(["projects", "summaries"]);
    expect(projectSummariesOptions().queryKey).toEqual(projectQueryKeys.summaries());
  });

  it("deduplicates the directory read and refetches after invalidation", async () => {
    const queryClient = new QueryClient();
    const request = vi.spyOn(api, "listProjectSummaries").mockResolvedValue(summaries);

    await queryClient.ensureQueryData(projectSummariesOptions());
    await queryClient.ensureQueryData(projectSummariesOptions());
    await queryClient.invalidateQueries({ queryKey: projectQueryKeys.summaries() });
    await queryClient.fetchQuery(projectSummariesOptions());

    expect(request).toHaveBeenCalledTimes(2);
  });
});
