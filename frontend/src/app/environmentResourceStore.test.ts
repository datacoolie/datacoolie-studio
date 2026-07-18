import { describe, expect, it, vi } from "vitest";
import { ENVIRONMENT_RESOURCE_NAMES, EnvironmentResourceStore } from "./environmentResourceStore";

describe("EnvironmentResourceStore", () => {
  it("retains module reads until explicit invalidation", async () => {
    let now = 1_000;
    const store = new EnvironmentResourceStore(() => now);
    const fetcher = vi.fn(async () => ({ revision: 1 }));

    await store.load(1, "lineage", fetcher);
    now += 3_600_000;
    await store.load(1, "lineage", fetcher);

    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("invalidates every resource for one environment without touching another", async () => {
    const store = new EnvironmentResourceStore();
    const fetchEnvironmentOne = vi.fn(async () => ({ environment: 1 }));
    const fetchEnvironmentTwo = vi.fn(async () => ({ environment: 2 }));
    const options = {};
    await store.load(1, "assets", fetchEnvironmentOne, options);
    await store.load(2, "assets", fetchEnvironmentTwo, options);

    store.invalidateEnvironment(1);
    await store.load(1, "assets", fetchEnvironmentOne, options);
    await store.load(2, "assets", fetchEnvironmentTwo, options);

    expect(fetchEnvironmentOne).toHaveBeenCalledTimes(2);
    expect(fetchEnvironmentTwo).toHaveBeenCalledTimes(1);
  });

  it("caches the narrow Overview read model as its own resource", async () => {
    let now = 1_000;
    const store = new EnvironmentResourceStore(() => now);
    const fetcher = vi.fn(async () => ({ schema_version: "environment-overview.v1" }));

    await store.load(1, "overview", fetcher);
    now += 1_000;
    await store.load(1, "overview", fetcher);

    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("keeps Sources data in its own cache resource", async () => {
    let now = 1_000;
    const store = new EnvironmentResourceStore(() => now);
    const fetcher = vi.fn(async () => ({ metadataSources: [], logPaths: [], codeArtifacts: [] }));

    await store.load(1, "sources", fetcher);
    now += 1_000;
    await store.load(1, "sources", fetcher);

    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("does not allocate the retired generic Metadata response as a module resource", () => {
    expect(ENVIRONMENT_RESOURCE_NAMES).not.toContain("metadata");
  });
});
