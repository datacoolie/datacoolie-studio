import { describe, expect, it } from "vitest";
import type { ProjectReferenceRegistryResponse } from "../../shared/api/domainTypes";
import {
  buildProjectMappingRegistry,
  canCreateProjectMapping,
  canEditProjectMapping,
  filterProjectMappingTargets,
  projectMappingInitialTargetId,
  projectMappingResolutionSummary,
  projectMappingStateLabel,
  projectMappingTargetBusinessKey,
  projectMappingTargetLabel,
  projectTargetCoverage,
} from "./projectReferenceMappingRegistryModel";

const automaticTarget = {
  id: "logical_table\u001fbronze.orders",
  asset_id: "asset:orders",
  asset_ids: ["asset:orders"],
  environment_ids: [1],
  environment_names: ["dev"],
  asset_type: "table" as const,
  format: "delta",
  connection_name: "lake",
  display_name: "orders",
  context: "main.warehouse",
  kind: "logical_table" as const,
  value: "bronze.orders",
  display: "bronze.orders",
};

const response: ProjectReferenceRegistryResponse = {
  project_id: 7,
  mappings: [],
  failures: [],
  targets: [automaticTarget],
  rows: [{
    id: "table_reference\u001fraw.orders",
    reference_type: "table_reference",
    normalized_value: "raw.orders",
    mapping: null,
    resolution: { state: "automatic" },
    environments: [{
      environment_id: 1,
      environment_name: "dev",
      resolution: { state: "automatic" },
      resolved_asset_id: "asset:orders",
      resolved_asset_ids: ["asset:orders"],
      observed_target_ids: [automaticTarget.id],
      candidate_asset_ids: ["asset:orders"],
      manual_mapping_id: null,
      manual_mapping_status: null,
      occurrence_count: 2,
      consumer_count: 1,
    }],
    candidate_asset_ids: ["asset:orders"],
    resolved_asset_ids: ["asset:orders"],
    target: null,
    observed_targets: [{ target: automaticTarget, environment_ids: [1], environment_names: ["dev"] }],
    target_coverage: {
      available_environment_names: [],
      missing_environment_names: ["dev"],
      available: 0,
      total: 1,
    },
  }],
};

describe("project reference registry presentation", () => {
  it("uses the backend-owned state without reclassifying environment snapshots", () => {
    const registry = buildProjectMappingRegistry(response);
    const row = registry.rows[0];

    expect(row.state).toBe("automatic");
    expect(projectMappingStateLabel(row.state)).toBe("Automatic");
    expect(projectMappingResolutionSummary(row)).toBe("1 environment resolved automatically");
    expect(projectMappingTargetLabel(row)).toBe("orders");
    expect(projectMappingInitialTargetId(row)).toBe(automaticTarget.id);
  });

  it("uses Map until a saved mapping exists, then exposes Edit/Clear ownership", () => {
    const automatic = buildProjectMappingRegistry(response).rows[0];
    expect(canCreateProjectMapping(automatic)).toBe(true);
    expect(canEditProjectMapping(automatic)).toBe(false);

    const mapping = {
      id: 41,
      project_id: 7,
      reference_type: "table_reference" as const,
      reference_normalized_value: "raw.orders",
      reference_signature: {},
      target_identifier_kind: "logical_table" as const,
      target_normalized_value: "bronze.orders",
      target_display_value: "bronze.orders",
      note: null,
      created_at: "2026-07-21T00:00:00Z",
      updated_at: "2026-07-21T00:00:00Z",
    };
    const manualResponse: ProjectReferenceRegistryResponse = {
      ...response,
      mappings: [mapping],
      rows: [{
        ...response.rows[0],
        mapping,
        resolution: { state: "manual" },
        target: automaticTarget,
        environments: [{ ...response.rows[0].environments[0], resolution: { state: "manual" }, manual_mapping_id: 41 }],
        target_coverage: {
          available_environment_names: ["dev"],
          missing_environment_names: [],
          available: 1,
          total: 1,
        },
      }],
    };
    const manual = buildProjectMappingRegistry(manualResponse).rows[0];

    expect(manual.state).toBe("manual");
    expect(canCreateProjectMapping(manual)).toBe(false);
    expect(canEditProjectMapping(manual)).toBe(true);
    expect(projectMappingTargetBusinessKey(manual)).toBe("logical_table · bronze.orders");
  });

  it("keeps unresolved reasons as detail while filtering targets by canonical candidates", () => {
    const unresolvedResponse: ProjectReferenceRegistryResponse = {
      ...response,
      rows: [{
        ...response.rows[0],
        resolution: { state: "unresolved", reason: "multiple_matches" },
        environments: [{
          ...response.rows[0].environments[0],
          resolution: { state: "unresolved", reason: "multiple_matches" },
        }],
      }],
    };
    const registry = buildProjectMappingRegistry(unresolvedResponse);
    const row = registry.rows[0];

    expect(row.state).toBe("unresolved");
    expect(projectMappingStateLabel(row.state)).toBe("Unresolved");
    expect(filterProjectMappingTargets(row, registry.targets, { query: "orders", connectionName: "lake" }))
      .toHaveLength(1);
    expect(projectTargetCoverage(row, registry.targets[0])).toEqual({
      availableEnvironmentNames: ["dev"],
      missingEnvironmentNames: [],
      available: 1,
      total: 1,
    });
  });
});
