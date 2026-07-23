import { describe, expect, it } from "vitest";
import { cacheInvalidationBranches, matchesDerivedQuery } from "./cacheInvalidation";

describe("cache invalidation matrix", () => {
  it("invalidates only overview and monitoring after analytics clear", () => {
    const branches = cacheInvalidationBranches("analytics");
    expect(branches).toEqual(["overview", "monitoring"]);
    expect(matchesDerivedQuery(["environments", 7, "monitoring", "report"], branches)).toBe(true);
    expect(matchesDerivedQuery(["environments", 7, "overview"], branches)).toBe(true);
    expect(matchesDerivedQuery(["environments", 7, "assets"], branches)).toBe(false);
    expect(matchesDerivedQuery(["environments", 7, "metadata", "workspace"], branches)).toBe(false);
  });

  it("maps feature-scoped read-model clears without touching metadata", () => {
    const branches = cacheInvalidationBranches("read_models", ["assets", "lineage"]);
    expect(matchesDerivedQuery(["environments", 4, "assets"], branches, 4)).toBe(true);
    expect(matchesDerivedQuery(["environments", 4, "lineage"], branches, 4)).toBe(true);
    expect(matchesDerivedQuery(["environments", 4, "overview"], branches, 4)).toBe(false);
    expect(matchesDerivedQuery(["environments", 5, "assets"], branches, 4)).toBe(false);
  });
});
