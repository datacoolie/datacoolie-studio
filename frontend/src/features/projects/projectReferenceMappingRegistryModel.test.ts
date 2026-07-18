import { describe, expect, it } from "vitest";
import type { AssetInventoryItem, AssetReferenceGroupItem, Environment, ProjectReferenceMapping } from "../../shared/api/types";
import {
  buildProjectMappingRegistry,
  buildProjectMappingTargets,
  canCreateProjectMapping,
  canEditProjectMapping,
  projectEnvironmentResolutionLabel,
  projectMappingInitialTargetId,
  projectMappingResolutionSummary,
  projectMappingStateLabel,
  projectMappingTargetBusinessKey,
  projectMappingTargetLabel,
  type ProjectAssetsSnapshot,
} from "./projectReferenceMappingRegistryModel";

const mapping = {
  id: 7,
  project_id: 1,
  reference_type: "table_reference",
  reference_normalized_value: "raw.orders",
  reference_signature: {},
  target_identifier_kind: "logical_table",
  target_normalized_value: "bronze.orders",
  target_display_value: "bronze.orders",
  note: "project fallback",
  created_at: "2026-07-11T00:00:00Z",
  updated_at: "2026-07-11T00:00:00Z",
} as ProjectReferenceMapping;

function environment(id: number, name: string) {
  return { id, name } as Environment;
}

function asset(id: string, identifier: string, name = identifier) {
  return {
    id,
    asset_type: "table",
    friendly_name: name,
    display_name: name,
    full_identity: `warehouse · ${identifier}`,
    connection_name: "warehouse",
    catalog: "main",
    database: "warehouse",
    schema_name: identifier.split(".")[0],
    table: identifier.split(".")[1],
    identifiers: [{ kind: "logical_table", normalized_value: identifier, display_value: identifier }],
  } as unknown as AssetInventoryItem;
}

function reference(
  normalizedValue: string,
  groupStatus: AssetReferenceGroupItem["group_status"],
  options: Partial<AssetReferenceGroupItem> = {},
) {
  return {
    id: `reference:${normalizedValue}`,
    reference_type: "table_reference",
    normalized_value: normalizedValue,
    display_name: normalizedValue,
    group_status: groupStatus,
    resolved_asset_id: null,
    resolved_asset_ids: [],
    candidate_asset_ids: [],
    occurrence_ids: ["occurrence:1"],
    dependency_count: 1,
    consumer_asset_ids: ["asset:consumer"],
    manual_mapping: null,
    ...options,
  } as unknown as AssetReferenceGroupItem;
}

function snapshot(env: Environment, assets: AssetInventoryItem[], referenceGroups: AssetReferenceGroupItem[]): ProjectAssetsSnapshot {
  return { environment: env, assets, referenceGroups };
}

describe("project asset mapping registry model", () => {
  it("uses the same concise resolution labels in the table and drawer", () => {
    expect(projectMappingStateLabel("manual")).toBe("Manual");
    expect(projectMappingStateLabel("needs_mapping")).toBe("Needs mapping");
    expect(projectMappingStateLabel("automatic")).toBe("Automatic");
  });

  it("keeps every affected environment when a mapping target is missing in only one of them", () => {
    const dev = environment(1, "dev");
    const test = environment(2, "test");
    const registry = buildProjectMappingRegistry([
      snapshot(dev, [asset("asset:dev-orders", "bronze.orders")], [reference("raw.orders", "resolved_single", {
        resolved_asset_id: "asset:dev-orders",
        resolved_asset_ids: ["asset:dev-orders"],
        manual_mapping: { mapping_id: mapping.id, status: "applied" },
      })]),
      snapshot(test, [], [reference("raw.orders", "mapping_target_missing", {
        manual_mapping: { mapping_id: mapping.id, status: "target_missing" },
      })]),
    ], [mapping]);

    const row = registry.rows[0];
    expect(row.state).toBe("missing_target");
    expect(row.targetCoverage).toMatchObject({
      available: 1,
      total: 2,
      availableEnvironmentNames: ["dev"],
      missingEnvironmentNames: ["test"],
    });
    expect(projectEnvironmentResolutionLabel(row.environments[0], row.mapping)).toBe("Manual");
    expect(projectEnvironmentResolutionLabel(row.environments[1], row.mapping)).toBe("Target missing");
  });

  it("does not let the first environment hide a later unresolved occurrence", () => {
    const registry = buildProjectMappingRegistry([
      snapshot(environment(1, "dev"), [asset("asset:dev-orders", "bronze.orders")], [reference("raw.orders", "resolved_single", {
        resolved_asset_id: "asset:dev-orders",
        resolved_asset_ids: ["asset:dev-orders"],
      })]),
      snapshot(environment(2, "test"), [], [reference("raw.orders", "unresolved")]),
    ], []);

    const row = registry.rows[0];
    expect(row.state).toBe("needs_mapping");
    expect(row.action).toBe("map");
    expect(projectMappingResolutionSummary(row)).toBe("Automatic 1 · Needs mapping 1");
  });

  it("keeps a saved rule visible when no loaded environment currently observes its reference", () => {
    const registry = buildProjectMappingRegistry([
      snapshot(environment(1, "dev"), [asset("asset:dev-orders", "bronze.orders")], []),
    ], [mapping]);

    const row = registry.rows[0];
    expect(row.state).toBe("stored_only");
    expect(row.action).toBe("edit");
    expect(row.targetCoverage).toMatchObject({ available: 0, total: 0 });
  });

  it("keeps mixed or partial effective resolution in review even when a saved mapping is active", () => {
    const registry = buildProjectMappingRegistry([
      snapshot(environment(1, "dev"), [asset("asset:dev-orders", "bronze.orders")], [reference("raw.orders", "resolved_mixed", {
        resolved_asset_ids: ["asset:dev-orders", "asset:another-orders"],
        manual_mapping: { mapping_id: mapping.id, status: "applied" },
      })]),
      snapshot(environment(2, "test"), [asset("asset:test-orders", "bronze.orders")], [reference("raw.orders", "partially_resolved", {
        resolved_asset_id: "asset:test-orders",
        resolved_asset_ids: ["asset:test-orders"],
        manual_mapping: { mapping_id: mapping.id, status: "applied" },
      })]),
    ], [mapping]);

    const row = registry.rows[0];
    expect(row.state).toBe("review");
    expect(row.action).toBe("edit");
    expect(projectEnvironmentResolutionLabel(row.environments[0], row.mapping)).toBe("Review");
    expect(projectEnvironmentResolutionLabel(row.environments[1], row.mapping)).toBe("Review");
    expect(projectMappingResolutionSummary(row)).toBe("2 environments need review");
  });

  it("does not include a saved mapping from another project when a scope is supplied", () => {
    const registry = buildProjectMappingRegistry([], [{ ...mapping, project_id: 2 }], 1);

    expect(registry.rows).toEqual([]);
  });

  it("groups target availability by canonical target identity across environments", () => {
    const targets = buildProjectMappingTargets([
      snapshot(environment(1, "dev"), [asset("asset:dev-orders", "bronze.orders")], []),
      snapshot(environment(2, "test"), [asset("asset:test-orders", "bronze.orders")], []),
    ]);

    expect(targets).toHaveLength(1);
    expect(targets[0]).toMatchObject({
      value: "bronze.orders",
      environmentNames: ["dev", "test"],
      assetIds: ["asset:dev-orders", "asset:test-orders"],
    });
  });

  it("orders manual active rows before attention and automatic rows", () => {
    const manualMapping = {
      ...mapping,
      reference_normalized_value: "raw.manual",
      target_normalized_value: "bronze.manual",
      target_display_value: "bronze.manual",
    };
    const registry = buildProjectMappingRegistry([
      snapshot(environment(1, "dev"), [asset("asset:manual", "bronze.manual")], [
        reference("raw.auto", "resolved_single"),
        reference("raw.needs", "unresolved"),
        reference("raw.manual", "resolved_single", {
          resolved_asset_id: "asset:manual",
          resolved_asset_ids: ["asset:manual"],
          manual_mapping: { mapping_id: manualMapping.id, status: "applied" },
        }),
      ]),
    ], [manualMapping]);

    expect(registry.rows.map((row) => row.normalizedValue)).toEqual(["raw.manual", "raw.needs", "raw.auto"]);
  });

  it("permits explicit overrides for automatic and mixed resolutions", () => {
    expect(canCreateProjectMapping({ mapping: null, state: "needs_mapping", action: "map" })).toBe(true);
    expect(canCreateProjectMapping({ mapping: null, state: "automatic", action: "edit" })).toBe(true);
    expect(canCreateProjectMapping({ mapping: null, state: "review", action: "edit" })).toBe(true);
    expect(canEditProjectMapping({ mapping, state: "manual" })).toBe(true);
    expect(canEditProjectMapping({ mapping, state: "review" })).toBe(true);
    expect(projectMappingTargetBusinessKey({ mapping })).toBe("logical_table · bronze.orders");
  });

  it("projects one observed automatic canonical target across environments", () => {
    const registry = buildProjectMappingRegistry([
      snapshot(environment(1, "dev"), [asset("asset:dev-orders", "bronze.orders", "orders")], [
        reference("raw.orders", "resolved_single", {
          resolved_asset_id: "asset:dev-orders",
          resolved_asset_ids: ["asset:dev-orders"],
        }),
      ]),
      snapshot(environment(2, "test"), [asset("asset:test-orders", "bronze.orders", "orders")], [
        reference("raw.orders", "resolved_single", {
          resolved_asset_id: "asset:test-orders",
          resolved_asset_ids: ["asset:test-orders"],
        }),
      ]),
    ], []);

    const row = registry.rows[0];
    expect(row.observedTargets).toHaveLength(1);
    expect(row.observedTargets[0]).toMatchObject({
      environmentNames: ["dev", "test"],
      target: { value: "bronze.orders", displayName: "orders" },
    });
    expect(projectMappingInitialTargetId(row)).toBe(row.observedTargets[0].target.id);
    expect(projectMappingTargetLabel(row)).toBe("orders");
  });

  it("keeps different automatic targets explicit and does not preselect one", () => {
    const registry = buildProjectMappingRegistry([
      snapshot(environment(1, "dev"), [asset("asset:dev-orders", "bronze.orders")], [
        reference("orders", "resolved_single", {
          resolved_asset_id: "asset:dev-orders",
          resolved_asset_ids: ["asset:dev-orders"],
        }),
      ]),
      snapshot(environment(2, "test"), [asset("asset:test-orders", "silver.orders")], [
        reference("orders", "resolved_single", {
          resolved_asset_id: "asset:test-orders",
          resolved_asset_ids: ["asset:test-orders"],
        }),
      ]),
    ], []);

    const row = registry.rows[0];
    expect(row.observedTargets.map((item) => item.target.value)).toEqual(["bronze.orders", "silver.orders"]);
    expect(projectMappingInitialTargetId(row)).toBeNull();
    expect(projectMappingTargetLabel(row)).toBe("Multiple automatic targets · 2");
  });
});
