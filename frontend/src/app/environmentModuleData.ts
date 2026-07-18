import type { ModuleKey } from "./moduleRegistry";

export function moduleUsesMetadataDataflowEditor(module: ModuleKey) {
  return module === "metadata";
}

export function moduleUsesProjectReferenceMappings(module: ModuleKey) {
  return module === "projects" || module === "lineage";
}
