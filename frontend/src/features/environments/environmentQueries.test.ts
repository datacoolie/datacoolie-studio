import { describe, expect, it } from "vitest";
import type { EnvironmentContext, MetadataEditorWorkspace } from "../../shared/api/domainTypes";
import { environmentInvalidationTargets, metadataWorkspaceSatisfiesCatalogVersion } from "./environmentQueries";

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
      .toEqual(["metadata", "assets", "lineage", "overview"]);
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
