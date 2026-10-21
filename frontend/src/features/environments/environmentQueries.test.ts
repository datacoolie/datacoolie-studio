import { describe, expect, it, vi } from "vitest";
import { QueryClient } from "@tanstack/react-query";
import type { EnvironmentContext, MetadataEditorWorkspace } from "../../shared/api/domainTypes";
import { environmentInvalidationTargets, environmentQueryKeys, installEnvironmentVersionReconciliation, metadataWorkspaceSatisfiesCatalogVersion } from "./environmentQueries";

const base: EnvironmentContext["versions"] = {
  source_registry: "source-1",
  metadata_catalog: "metadata-1",
  code_catalog: "code-1",
  operations: "operations-1",
  reference_mappings: "mappings-1",
};

describe("environmentInvalidationTargets", () => {
  it("invalidates only source-owned reads for a source registry change", () => {
    expect(environmentInvalidationTargets(base, { ...base, source_registry: "source-2" }))
      .toEqual(["sources", "overview"]);
  });

  it("invalidates structural consumers for a metadata catalog change", () => {
    expect(environmentInvalidationTargets(base, { ...base, metadata_catalog: "metadata-2" }))
      .toEqual(["metadata", "assets", "lineage", "monitoring", "overview"]);
  });

  it("keeps operations changes away from structural resources", () => {
    expect(environmentInvalidationTargets(base, { ...base, operations: "operations-2" }))
      .toEqual(["monitoring", "overview"]);
  });

  it("invalidates mapping consumers without touching sources or metadata", () => {
    expect(environmentInvalidationTargets(base, { ...base, reference_mappings: "mappings-2" }))
      .toEqual(["assets", "lineage", "overview"]);
  });
});

describe("metadataWorkspaceSatisfiesCatalogVersion", () => {
  const workspace = { metadata_catalog_version: "metadata-2" } as MetadataEditorWorkspace;

  it("keeps an authoritative mutation response fresh", () => {
    expect(metadataWorkspaceSatisfiesCatalogVersion(workspace, "metadata-2")).toBe(true);
  });

  it("requires a refetch for an external catalog version", () => {
    expect(metadataWorkspaceSatisfiesCatalogVersion(workspace, "metadata-3")).toBe(false);
    expect(metadataWorkspaceSatisfiesCatalogVersion(undefined, "metadata-2")).toBe(false);
  });
});

describe("installEnvironmentVersionReconciliation", () => {
  it("does not cancel active reads when the first context version is published", async () => {
    const queryClient = new QueryClient();
    const cancelQueries = vi.spyOn(queryClient, "cancelQueries");
    const invalidateQueries = vi.spyOn(queryClient, "invalidateQueries");
    const dispose = installEnvironmentVersionReconciliation(queryClient);

    queryClient.setQueryData(environmentQueryKeys.context(1), {
      environment: { id: 1 },
      versions: base,
    } as EnvironmentContext);
    await Promise.resolve();

    expect(cancelQueries).not.toHaveBeenCalled();
    expect(invalidateQueries).toHaveBeenCalledWith(expect.objectContaining({ refetchType: "none" }));
    dispose();
    queryClient.clear();
  });
});
