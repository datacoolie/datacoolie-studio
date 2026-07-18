import { describe, expect, it } from "vitest";
import { moduleUsesMetadataDataflowEditor, moduleUsesProjectReferenceMappings } from "./environmentModuleData";

describe("environment module data requirements", () => {
  it("loads the editor context with the metadata module", () => {
    expect(moduleUsesMetadataDataflowEditor("metadata")).toBe(true);
  });

  it("defers editor requests for read-oriented modules", () => {
    expect(moduleUsesMetadataDataflowEditor("assets")).toBe(false);
    expect(moduleUsesMetadataDataflowEditor("lineage")).toBe(false);
    expect(moduleUsesMetadataDataflowEditor("sources")).toBe(false);
    expect(moduleUsesMetadataDataflowEditor("monitoring")).toBe(false);
  });

  it("loads project-scoped mappings for every module that renders mapping controls", () => {
    expect(moduleUsesProjectReferenceMappings("projects")).toBe(true);
    expect(moduleUsesProjectReferenceMappings("assets")).toBe(false);
    expect(moduleUsesProjectReferenceMappings("lineage")).toBe(true);
    expect(moduleUsesProjectReferenceMappings("overview")).toBe(false);
    expect(moduleUsesProjectReferenceMappings("metadata")).toBe(false);
  });
});
