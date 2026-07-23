import { beforeEach, describe, expect, it, vi } from "vitest";
import type { SourcePath } from "../shared/api/domainTypes";

vi.mock("../shared/api/client", () => ({
  api: {
    listMetadataSources: vi.fn(),
    listLogSources: vi.fn(),
    listCodeArtifacts: vi.fn(),
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
    vi.mocked(api.listMetadataSources).mockResolvedValue(metadataSources);
    vi.mocked(api.listLogSources).mockResolvedValue(logPaths);
    vi.mocked(api.listCodeArtifacts).mockResolvedValue(codeArtifacts);

    await expect(fetchEnvironmentSources(7)).resolves.toEqual({ metadataSources, logPaths, codeArtifacts });
    expect(api.listMetadataSources).toHaveBeenCalledWith(7);
    expect(api.listLogSources).toHaveBeenCalledWith(7);
    expect(api.listCodeArtifacts).toHaveBeenCalledWith(7);
  });
});
