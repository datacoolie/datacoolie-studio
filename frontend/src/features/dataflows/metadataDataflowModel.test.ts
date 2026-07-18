import { describe, expect, it } from "vitest";
import type { MetadataEditorDocument, MetadataResponse } from "../../shared/api/types";
import {
  buildMetadataDataflowRecords,
  dataflowFields,
  dataflowTitle,
  dataflowRouteText,
  endpointLabel,
  findMetadataDataflowRecord,
  isEditableMetadataDataflowRecord,
  sourceFields,
  updateMetadataDataflowRow,
} from "./metadataDataflowModel";

describe("metadata dataflow model", () => {
  const document: MetadataEditorDocument = {
    source: {
      source_id: 0,
      environment_id: 1,
      uri: "environment://metadata",
      name: "All metadata sources",
      format: "merged",
      scope: "environment",
      revision: {},
    },
    sheets: {
      connections: { columns: [], rows: [] },
      schema_hints: { columns: [], rows: [] },
      dataflows: {
        columns: [],
        rows: [{
          __metadata_source_id: 10,
          __metadata_source_name: "orders.json",
          __metadata_source_uri: "metadata/dataflows/orders.json",
          dataflow_id: "flow_orders",
          name: "Build Orders",
          stage: "silver",
          source_connection_name: "landing",
          source_schema_name: "raw",
          source_table: "orders",
          destination_connection_name: "lake",
          destination_schema_name: "silver",
          destination_table: "orders",
          destination_load_type: "merge",
          is_active: true,
        }],
      },
    },
    issues: [{
      severity: "warning",
      sheet: "dataflows",
      row_index: 0,
      column: "destination_table",
      message: "example",
    }],
  };

  const metadata: MetadataResponse = {
    summary: {},
    sources: [],
    connections: [],
    schema_hints: [],
    errors: [],
    dataflows: [{
      metadata_source_id: 10,
      metadata_source_uri: "metadata/dataflows/orders.json",
      dataflow_id: "flow_orders",
      name: "Build Orders",
      description: "Normalized description",
      stage: "silver",
      processing_mode: "batch",
      load_type: "merge",
      source_asset_id: "asset:source",
      destination_asset_id: "asset:destination",
      source: { connection_name: "landing", schema_name: "raw", table: "orders" },
      destination: { connection_name: "lake", schema_name: "silver", table: "orders" },
    }],
  };

  it("builds metadata-root records with normalized enrichment", () => {
    const [record] = buildMetadataDataflowRecords(document, metadata);
    expect(record.name).toBe("Build Orders");
    expect(record.description).toBe("Normalized description");
    expect(record.metadataSourceId).toBe(10);
    expect(record.source.assetId).toBe("asset:source");
    expect(record.destination.assetId).toBe("asset:destination");
    expect(record.issues).toHaveLength(1);
    expect(dataflowRouteText(record)).toBe("landing - raw.orders → lake - silver.orders : merge");
    expect(dataflowTitle(record)).toBe("Build Orders");
  });

  it("builds the editor-backed drawer record without the generic Metadata response", () => {
    const records = buildMetadataDataflowRecords(document);
    const record = findMetadataDataflowRecord(records, { metadataSourceId: 10, dataflowId: "flow_orders" });
    expect(record?.editorBacked).toBe(true);
    expect(record?.name).toBe("Build Orders");
    expect(dataflowRouteText(record!)).toBe("landing - raw.orders → lake - silver.orders : merge");
    expect(isEditableMetadataDataflowRecord(document, record!)).toBe(true);
  });

  it("finds records by stable row identity before fallback values", () => {
    const records = buildMetadataDataflowRecords(document, metadata);
    expect(findMetadataDataflowRecord(records, { metadataSourceId: 10, rowIndex: 0 })?.name).toBe("Build Orders");
    expect(findMetadataDataflowRecord(records, { metadataSourceId: 10, dataflowId: "flow_orders" })?.rowIndex).toBe(0);
    expect(findMetadataDataflowRecord(records, { metadataSourceId: 10, name: "Build Orders" })?.rowIndex).toBe(0);
  });

  it("builds read-only records from normalized metadata when editor rows are unavailable", () => {
    const records = buildMetadataDataflowRecords(null, metadata);
    const record = findMetadataDataflowRecord(records, { metadataSourceId: 10, dataflowId: "flow_orders" });
    expect(record?.name).toBe("Build Orders");
    expect(record?.source.assetId).toBe("asset:source");
    expect(record?.destination.assetId).toBe("asset:destination");
    expect(dataflowRouteText(record!)).toBe("landing - raw.orders → lake - silver.orders : merge");
    expect(isEditableMetadataDataflowRecord(document, record)).toBe(false);
  });

  it("only enables drawer editing for persisted editor rows in writable documents", () => {
    const [record] = buildMetadataDataflowRecords(document, metadata);
    expect(isEditableMetadataDataflowRecord(document, record)).toBe(true);
    expect(isEditableMetadataDataflowRecord({
      ...document,
      source: { ...document.source, read_only: true },
    }, record)).toBe(false);
    expect(isEditableMetadataDataflowRecord(null, record)).toBe(false);
  });

  it("updates only the selected dataflow row", () => {
    const updated = updateMetadataDataflowRow(document, 0, {
      ...document.sheets.dataflows.rows[0],
      dataflow_id: "renamed-id-should-be-ignored",
      description: "edited",
    });
    expect(updated.sheets.dataflows.rows[0].description).toBe("edited");
    expect(updated.sheets.dataflows.rows[0].dataflow_id).toBe("flow_orders");
    expect(document.sheets.dataflows.rows[0].description).toBeUndefined();
  });

  it("keeps the connection context in concise SQL query routes and python endpoints", () => {
    expect(endpointLabel({
      connectionName: "warehouse",
      schemaName: "",
      table: "",
      path: "",
      query: "select * from orders",
      pythonFunction: "",
      configure: null,
      assetId: "",
    })).toBe("warehouse - SQL query");
    expect(endpointLabel({
      connectionName: "runtime",
      schemaName: "",
      table: "",
      path: "",
      query: "",
      pythonFunction: "pipelines.load_orders",
      configure: null,
      assetId: "",
    })).toBe("runtime - pipelines.load_orders");
  });

  it("keeps source fields limited to Data Sheet input columns", () => {
    const records = buildMetadataDataflowRecords(null, metadata);
    const record = records[0];
    const fields = sourceFields(record);
    expect(fields).toHaveLength(8);
    expect(fields.map((field) => field.key)).not.toContain("source_id");
    expect(fields.map((field) => field.key)).not.toContain("source_full_table");
    expect(fields.map((field) => field.key)).not.toContain("source_path");
    expect(fields.filter((field) => field.value !== undefined && field.value !== null && field.value !== ""))
      .toMatchObject([
        { key: "source_connection_name", value: "landing" },
        { key: "source_schema_name", value: "raw" },
        { key: "source_table", value: "orders" },
      ]);
  });

  it("keeps the drawer field order aligned with the data sheet columns", () => {
    const orderedDocument: MetadataEditorDocument = {
      ...document,
      sheets: {
        ...document.sheets,
        dataflows: {
          ...document.sheets.dataflows,
          columns: [
            { key: "name", name: "name" },
            { key: "dataflow_id", name: "dataflow_id" },
            { key: "source_table", name: "source_table" },
            { key: "source_connection_name", name: "source_connection_name" },
            { key: "source_query", name: "source_query" },
          ],
        },
      },
    };
    const [record] = buildMetadataDataflowRecords(orderedDocument, metadata);
    expect(dataflowFields(record).map((field) => field.key).slice(0, 2)).toEqual(["name", "dataflow_id"]);
    const sourceFieldKeys = sourceFields(record).map((field) => field.key);
    expect(sourceFieldKeys.slice(0, 3)).toEqual([
      "source_table",
      "source_connection_name",
      "source_query",
    ]);
    expect(sourceFieldKeys).toContain("source_schema_name");
  });

  it("places workspace id directly after dataflow id regardless of sheet order", () => {
    const [record] = buildMetadataDataflowRecords(document, metadata);
    expect(dataflowFields(record).map((field) => field.key).slice(0, 2)).toEqual([
      "dataflow_id",
      "workspace_id",
    ]);
  });
});
