import { describe, expect, it } from "vitest";
import type { MetadataEditorDocument } from "../../shared/api/domainTypes";
import { metadataSaveConfirmation, metadataSaveImpacts } from "./metadataSaveConfirmation";

type Row = Record<string, unknown>;

function document(
  scope: "environment" | "source",
  sheets: Partial<Record<"connections" | "dataflows" | "schema_hints", Row[]>>,
  uri = "metadata/environment.json"
): MetadataEditorDocument {
  return {
    source: {
      source_id: scope === "environment" ? 0 : 1,
      environment_id: 2,
      uri,
      format: "json",
      scope,
      revision: {
        sources: [
          { source_id: 1, name: "orders.json", uri: "metadata/orders.json" },
          { source_id: 2, name: "schema_hints.yml", uri: "metadata/schema_hints.yml" },
          { source_id: 3, name: "inventory.json", uri: "metadata/inventory.json" }
        ]
      }
    },
    sheets: Object.fromEntries(
      ["connections", "dataflows", "schema_hints"].map((sheetKey) => [
        sheetKey,
        { columns: [], rows: sheets[sheetKey as keyof typeof sheets] ?? [] }
      ])
    ),
    issues: []
  };
}

function row(sourceId: number | null, name: string, uri: string, values: Row): Row {
  return {
    __metadata_source_id: sourceId,
    __metadata_source_name: name,
    __metadata_source_uri: uri,
    __metadata_source_kind: "metadata",
    ...values
  };
}

describe("metadata save confirmation", () => {
  it("lists only the schema-hints source file that will change", () => {
    const source = document("environment", {
      dataflows: [row(1, "orders.json", "metadata/orders.json", { name: "Orders" })],
      schema_hints: [row(2, "schema_hints.yml", "metadata/schema_hints.yml", { table: "orders", column: "amount", data_type: "decimal" })]
    });
    const pending = document("environment", {
      dataflows: [row(1, "orders.json", "metadata/orders.json", { name: "Orders" })],
      schema_hints: [row(2, "schema_hints.yml", "metadata/schema_hints.yml", { table: "orders", column: "amount", data_type: "decimal(18,2)" })]
    });

    expect(metadataSaveImpacts(source, pending)).toEqual([{
      action: "update",
      key: "id:2",
      label: "metadata/schema_hints.yml",
      sheets: ["Schema hints"]
    }]);
    expect(metadataSaveConfirmation(source, pending).description).toContain("update 1 source file");
  });

  it("lists both source files when a row moves between them", () => {
    const source = document("environment", {
      dataflows: [row(1, "orders.json", "metadata/orders.json", { name: "Orders" })]
    });
    const pending = document("environment", {
      dataflows: [row(3, "inventory.json", "metadata/inventory.json", { name: "Orders" })]
    });

    expect(metadataSaveImpacts(source, pending)).toEqual([
      { action: "update", key: "id:1", label: "metadata/orders.json", sheets: ["Dataflows"] },
      { action: "update", key: "id:3", label: "metadata/inventory.json", sheets: ["Dataflows"] }
    ]);
  });

  it("identifies a typed metadata-source value as a new source file", () => {
    const source = document("environment", { schema_hints: [] });
    const pending = document("environment", {
      schema_hints: [row(null, "schema_hints/new.yml", "", { table: "orders", column: "currency" })]
    });

    expect(metadataSaveImpacts(source, pending)).toEqual([{
      action: "create",
      key: "new:schema_hints/new.yml",
      label: "schema_hints/new.yml",
      sheets: ["Schema hints"]
    }]);
  });

  it("keeps a single-source save scoped to its own file", () => {
    const source = document("source", {
      schema_hints: [row(1, "orders.json", "metadata/orders.json", { table: "orders", column: "amount" })]
    }, "metadata/orders.json");
    const pending = document("source", {
      schema_hints: [row(1, "orders.json", "metadata/orders.json", { table: "orders", column: "total_amount" })]
    }, "metadata/orders.json");

    expect(metadataSaveConfirmation(source, pending).impacts).toEqual([{
      action: "update",
      key: "id:1",
      label: "metadata/orders.json",
      sheets: ["Schema hints"]
    }]);
  });
});
