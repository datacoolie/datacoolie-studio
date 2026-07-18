import { beforeEach, describe, expect, it, vi } from "vitest";
import type { EnvironmentFreshness } from "../shared/api/types";
import type { EnvironmentHeaderData } from "./environmentHeaderResource";

vi.mock("../shared/api/client", () => ({
  api: { getEnvironmentFreshness: vi.fn() },
}));

import { api } from "../shared/api/client";
import {
  DEFAULT_SOURCE_CHECK_INTERVAL_SECONDS,
  fetchEnvironmentHeader,
  sourceCacheVersionChanged,
  sourceCheckIntervalMs,
  structuralCacheVersionChanged,
} from "./environmentHeaderResource";

describe("sourceCheckIntervalMs", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("uses the configured bounded value", () => {
    expect(sourceCheckIntervalMs(45)).toBe(45_000);
    expect(sourceCheckIntervalMs(5)).toBe(5_000);
    expect(sourceCheckIntervalMs(3600)).toBe(3_600_000);
  });

  it("falls back to the 30 second default", () => {
    expect(sourceCheckIntervalMs(undefined)).toBe(DEFAULT_SOURCE_CHECK_INTERVAL_SECONDS * 1_000);
    expect(sourceCheckIntervalMs(4)).toBe(DEFAULT_SOURCE_CHECK_INTERVAL_SECONDS * 1_000);
    expect(sourceCheckIntervalMs(12.5)).toBe(DEFAULT_SOURCE_CHECK_INTERVAL_SECONDS * 1_000);
  });

  it("loads only the freshness data rendered by the Environment header", async () => {
    const freshness = {
      environment_id: 7,
      status: "fresh",
      message: "Current",
      metadata_source_count: 2,
      etl_log_path_count: 1,
      source_cache_version: "source-cache-v1",
      structural_cache_version: "structural-cache-v1",
      metadata: { status: "fresh", count: 2 },
      etl_logs: { status: "fresh", count: 1 },
      items: [],
    } satisfies EnvironmentFreshness;
    vi.mocked(api.getEnvironmentFreshness).mockResolvedValue(freshness);

    await expect(fetchEnvironmentHeader(7)).resolves.toEqual({ freshness });
    expect(api.getEnvironmentFreshness).toHaveBeenCalledWith(7);
    expect(api.getEnvironmentFreshness).toHaveBeenCalledTimes(1);
  });

  it("detects a materialized source-cache revision change", () => {
    const base = { freshness: { source_cache_version: "v1", structural_cache_version: "s1" } } as EnvironmentHeaderData;
    expect(sourceCacheVersionChanged(base, { freshness: { source_cache_version: "v1", structural_cache_version: "s1" } } as EnvironmentHeaderData)).toBe(false);
    expect(sourceCacheVersionChanged(base, { freshness: { source_cache_version: "v2", structural_cache_version: "s1" } } as EnvironmentHeaderData)).toBe(true);
    expect(structuralCacheVersionChanged(base, { freshness: { source_cache_version: "v1", structural_cache_version: "s2" } } as EnvironmentHeaderData)).toBe(true);
  });
});
