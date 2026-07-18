import { describe, expect, it } from "vitest";
import { createProjectReferenceMappingsLoadGuard } from "./projectReferenceMappingsLoadGuard";

describe("project asset mappings load guard", () => {
  it("rejects an older project response after navigation", () => {
    const guard = createProjectReferenceMappingsLoadGuard();
    const projectA = guard.begin(1);
    const projectB = guard.begin(2);

    expect(projectA.projectChanged).toBe(true);
    expect(projectB.projectChanged).toBe(true);
    expect(guard.isCurrent(projectA)).toBe(false);
    expect(guard.isCurrent(projectB)).toBe(true);
  });

  it("rejects an older refresh for the same project", () => {
    const guard = createProjectReferenceMappingsLoadGuard();
    const initial = guard.begin(1);
    const refresh = guard.begin(1);

    expect(refresh.projectChanged).toBe(false);
    expect(guard.isCurrent(initial)).toBe(false);
    expect(guard.isCurrent(refresh)).toBe(true);
  });

  it("transfers busy ownership from an Assets load to the project mapping refresh", () => {
    const guard = createProjectReferenceMappingsLoadGuard();
    const assetsLoad = guard.begin(1, true);
    const projectRegistryLoad = guard.begin(1, false);

    expect(assetsLoad.ownsBusy).toBe(true);
    expect(projectRegistryLoad.ownsBusy).toBe(true);
    expect(guard.finish(assetsLoad)).toBe(false);
    expect(guard.finish(projectRegistryLoad)).toBe(true);
  });
});
