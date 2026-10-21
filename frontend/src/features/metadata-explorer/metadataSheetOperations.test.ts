import { describe, expect, it } from "vitest";
import {
  canMoveMetadataRow,
  filterMetadataRows,
  metadataSourceGroupStartIds,
  sameMetadataSortBucket,
  synchronizeConnectionNameReferences
} from "./metadataSheetOperations";
import type { MetadataEditorDocument } from "../../shared/api/domainTypes";

describe("metadata row grouping and filtering", () => {
  const rows = [
    { name: "silver flow", stage: " Silver2 ", __metadata_source_id: 2 },
    { name: "silver flow copy", stage: "silver2", __metadata_source_id: 2 },
    { name: "silver ten", stage: "silver10", __metadata_source_id: 2 },
    { name: "bronze flow", stage: "bronze", __metadata_source_id: 1 }
  ];

  it("filters an exact normalized stage independently of text search", () => {
    const filtered = filterMetadataRows("dataflows", rows, [{ key: "name" }], "", "silver2");

    expect(filtered.map((row) => row.name)).toEqual(["silver flow", "silver flow copy"]);
    expect(filtered[0].__rowIndex).toBe(0);
  });

  it("marks the first visible row of each source block", () => {
    const runtimeRows = filterMetadataRows("dataflows", rows, [{ key: "name" }], "");
    expect([...metadataSourceGroupStartIds(runtimeRows)]).toEqual(["dataflows-0", "dataflows-3"]);
  });

  it("keeps source row indexes when target and column filters narrow the grid", () => {
    const filtered = filterMetadataRows(
      "dataflows",
      [
        { dataflow_id: "flow-a", stage: "silver", region: "America" },
        { dataflow_id: "flow-b", stage: "gold", region: "Japan" },
        { dataflow_id: "flow-c", stage: "gold", region: "America" }
      ],
      [{ key: "stage" }, { key: "region" }],
      "",
      null,
      { stage: ["metadata-filter:value:string:gold"], region: ["metadata-filter:value:string:Japan"] },
      undefined,
      ["flow-b", "flow-c"]
    );

    expect(filtered.map((row) => row.dataflow_id)).toEqual(["flow-b"]);
    expect(filtered[0].__rowIndex).toBe(1);
  });

  it("allows movement only within the same canonical bucket", () => {
    expect(canMoveMetadataRow("dataflows", rows, 0, 1)).toBe(true);
    expect(canMoveMetadataRow("dataflows", rows, 1, 1)).toBe(false);
    expect(sameMetadataSortBucket("connections", rows[0], rows[1])).toBe(true);
    expect(sameMetadataSortBucket("dataflows", rows[0], rows[1])).toBe(true);
    expect(sameMetadataSortBucket("dataflows", rows[1], rows[2])).toBe(false);
    expect(sameMetadataSortBucket(
      "schema_hints",
      { __metadata_source_id: 2, connection_name: "c", schema_name: "s", table_name: "t", ordinal_position: null },
      { __metadata_source_id: 2, connection_name: "c", schema_name: "s", table_name: "t", ordinal_position: "unknown" }
    )).toBe(true);
  });
});

describe("connection rename synchronization", () => {
  it("updates connection references in dataflows and schema hints by stable connection id", () => {
    const document = metadataDocument({
      connections: [
        { connection_id: "conn-silver", name: "silver" },
        { connection_id: "conn-gold", name: "gold" }
      ],
      dataflows: [{ source_connection_name: "silver", destination_connection_name: "gold" }],
      schema_hints: [{ connection_name: "silver", table_name: "orders" }]
    });

    const next = synchronizeConnectionNameReferences(document, {
      columns: [{ key: "connection_id", name: "connection_id" }, { key: "name", name: "name" }],
      rows: [
        { connection_id: "conn-silver", name: "silver_curated" },
        { connection_id: "conn-gold", name: "gold" }
      ]
    });

    expect(next.sheets.dataflows.rows[0]).toMatchObject({
      source_connection_name: "silver_curated",
      destination_connection_name: "gold"
    });
    expect(next.sheets.schema_hints.rows[0].connection_name).toBe("silver_curated");
  });

  it("leaves unrelated references unchanged when the connection name is unchanged", () => {
    const document = metadataDocument({
      connections: [{ connection_id: "conn-silver", name: "silver" }],
      dataflows: [{ source_connection_name: "silver", name: "flow" }],
      schema_hints: [{ connection_name: "external", table_name: "orders" }]
    });

    const next = synchronizeConnectionNameReferences(document, {
      columns: [],
      rows: [{ connection_id: "conn-silver", name: "silver" }]
    });

    expect(next.sheets.dataflows.rows[0]).toEqual(document.sheets.dataflows.rows[0]);
    expect(next.sheets.schema_hints.rows[0]).toEqual(document.sheets.schema_hints.rows[0]);
  });
});

function metadataDocument(rows: {
  connections: Array<Record<string, unknown>>;
  dataflows: Array<Record<string, unknown>>;
  schema_hints: Array<Record<string, unknown>>;
}): MetadataEditorDocument {
  return {
    source: { source_id: 1, environment_id: 1, uri: "metadata.json", format: "json", revision: {} },
    sheets: {
      connections: { columns: [], rows: rows.connections },
      dataflows: { columns: [], rows: rows.dataflows },
      schema_hints: { columns: [], rows: rows.schema_hints }
    },
    issues: []
  };
}
