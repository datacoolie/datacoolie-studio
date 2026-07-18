import { describe, expect, it } from "vitest";
import type { AssetInventoryItem, AssetReferenceGroupItem, ProjectReferenceMapping } from "../../shared/api/types";
import {
  buildReferenceMappingPayload,
  buildReferenceMappingTargets,
  filterReferenceMappingTargets,
  findReferenceMapping,
  referenceMappingAction,
  type ReferenceMappingTarget,
} from "./referenceMappingModel";

const reference = {
  id: "reference:customer",
  reference_type: "table_reference",
  normalized_value: "silver.customer",
  display_name: "customer",
  group_status: "unresolved",
  candidate_asset_ids: [],
  occurrence_ids: ["occurrence:1"],
  dependency_count: 1,
  consumer_asset_ids: ["asset:consumer"],
  manual_mapping: null,
} as unknown as AssetReferenceGroupItem;

const mapping = {
  id: 41,
  reference_type: "table_reference",
  reference_normalized_value: "silver.customer",
  target_identifier_kind: "logical_table",
  target_normalized_value: "catalog.database.silver.customer",
  target_display_value: "catalog.database.silver.customer",
  note: null,
} as unknown as ProjectReferenceMapping;

const target: ReferenceMappingTarget = {
  id: "logical_table:customer",
  assetId: "asset:customer",
  assetType: "table",
  format: "delta",
  connectionName: "lakehouse",
  displayName: "silver.customer",
  context: "catalog.database",
  kind: "logical_table",
  value: "catalog.database.silver.customer",
  display: "catalog.database.silver.customer",
};

const pathTarget: ReferenceMappingTarget = {
  ...target,
  id: "physical_path:customer",
  assetId: "asset:customer-path",
  assetType: "path",
  connectionName: "landing",
  kind: "physical_path",
  value: "/landing/customer",
  display: "/landing/customer",
};

describe("reference mapping model", () => {
  it("uses a broad canonical mapping and excludes provenance from its identity", () => {
    expect(findReferenceMapping(reference, [mapping])).toBe(mapping);
    expect(buildReferenceMappingPayload(reference, target, "shared mapping")).toEqual({
      reference_type: "table_reference",
      reference_value: "silver.customer",
      target_identifier_kind: "logical_table",
      target_value: "catalog.database.silver.customer",
      target_display_value: "catalog.database.silver.customer",
      note: "shared mapping",
    });
  });

  it("prioritizes repair and edit actions before resolution status", () => {
    expect(referenceMappingAction({ ...reference, group_status: "mapping_target_missing" }, [mapping])).toBe("repair");
    expect(referenceMappingAction({ ...reference, group_status: "unresolved" }, [mapping])).toBe("edit");
    expect(referenceMappingAction({ ...reference, group_status: "resolved_single" }, [])).toBe("edit");
  });

  it("filters the target catalog by connection without changing its canonical target kinds", () => {
    const filtered = filterReferenceMappingTargets(reference, [target, pathTarget], {
      query: "",
      connectionName: "landing",
    });
    expect(filtered).toEqual([pathTarget]);
    expect(filterReferenceMappingTargets(reference, [target, pathTarget], {
      query: "customer",
      connectionName: "",
    })).toEqual([target, pathTarget]);
  });

  it("removes table identity already shown on line one but preserves canonical identifiers for aliases", () => {
    const targets = buildReferenceMappingTargets([
      {
        id: "asset:table",
        asset_type: "table",
        friendly_name: "silver.customer",
        display_name: "silver.customer",
        full_identity: "lakehouse · main.warehouse.silver.customer",
        connection_name: "lakehouse",
        catalog: "main",
        database: "warehouse",
        schema_name: "silver",
        table: "customer",
        identifiers: [{ kind: "logical_table", normalized_value: "main.warehouse.silver.customer", display_value: "main.warehouse.silver.customer" }],
      },
      {
        id: "asset:api",
        asset_type: "api",
        friendly_name: "customer service",
        display_name: "customer service",
        full_identity: "gateway · /api/v1/customers",
        connection_name: "gateway",
        table: "customer service",
        identifiers: [{ kind: "api_endpoint", normalized_value: "/api/v1/customers", display_value: "/api/v1/customers" }],
      },
    ] as unknown as AssetInventoryItem[]);

    expect(targets.find((item) => item.assetId === "asset:table")?.context).toBe("main.warehouse");
    expect(targets.find((item) => item.assetId === "asset:api")?.context).toBe("/api/v1/customers");
  });
});
