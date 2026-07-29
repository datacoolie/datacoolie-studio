import { beforeEach, describe, expect, it, vi } from "vitest";
import type { SourcePath } from "../shared/api/domainTypes";

vi.mock("../shared/api/client", () => ({
  api: {
    getSourcesWorkspace: vi.fn(),
  },
}));

import { api } from "../shared/api/client";
import { fetchEnvironmentSources } from "./environmentSourcesResource";

const source = (id: number): SourcePath => ({
  id,
  environment_id: 7,
  uri: `file:///source-${id}`,
  enabled: true,
  sync_schedule_enabled: false,
  created_at: "2026-07-17T00:00:00Z",
});

describe("fetchEnvironmentSources", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("loads all source lists only when the Sources module requests them", async () => {
    const metadataSources = [source(1)];
    const logPaths = [source(2)];
    const codeArtifacts = [source(3)];
    vi.mocked(api.getSourcesWorkspace).mockResolvedValue({
      schema_version: "sources-workspace.v1",
      environment_id: 7,
      metadata_sources: metadataSources,
      log_sources: logPaths,
      code_artifacts: codeArtifacts,
      statuses: [],
      earliest_cloud_due_at: null,
      dependency_version: "test-version",
    });

    await expect(fetchEnvironmentSources(7)).resolves.toEqual({
      metadataSources,
      logPaths,
      codeArtifacts,
      statuses: {},
    });
    expect(api.getSourcesWorkspace).toHaveBeenCalledWith(7);
  });
});
