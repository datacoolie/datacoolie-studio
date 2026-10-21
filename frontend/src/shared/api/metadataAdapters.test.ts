import { describe, expect, it } from "vitest";
import {
  toMetadataBackup,
  toMetadataEditorDocument,
  toMetadataEditorWorkspace,
  toMetadataResponse,
} from "./metadataAdapters";
import type {
  MetadataBackupResponseTransport,
  MetadataEditorDocumentResponseTransport,
  MetadataEditorWorkspaceResponseTransport,
  MetadataResponseTransport,
} from "./contractTypes";

const documentTransport = {
  source: {
    source_id: 10,
    environment_id: 7,
    uri: "metadata/orders.json",
    format: "json",
    revision: { checksum: "v1" },
  },
  sheets: {
    dataflows: {
      columns: [{ key: "name", name: "Name" }],
      rows: [{ name: "orders" }],
    },
  },
  issues: [{ severity: "warning", sheet: "dataflows", row_index: 0, column: "name", message: "check" }],
  metadata_document: { dataflows: [] },
  metadata_documents: { "metadata/orders.json": { dataflows: [] } },
  sql_files: [{ source_id: 10, query: "orders.sql", content: "SELECT 1" }],
} as unknown as MetadataEditorDocumentResponseTransport;

describe("metadata transport adapters", () => {
  it("maps metadata dataflows from extensible records", () => {
    const response = toMetadataResponse({
      summary: { dataflows: 1 },
      sources: [{ uri: "metadata/orders.json" }],
      connections: [{ name: "lake" }],
      dataflows: [{
        metadata_source_id: 10,
        metadata_source_uri: "metadata/orders.json",
        name: "orders",
        source: { connection_name: "lake", table: "orders" },
        destination: { connection_name: "warehouse", table: "orders" },
      }],
      schema_hints: [],
      errors: [],
    } as unknown as MetadataResponseTransport);

    expect(response.dataflows[0]).toMatchObject({
      metadata_source_id: 10,
      name: "orders",
      source: { connection_name: "lake", table: "orders" },
      destination: { connection_name: "warehouse", table: "orders" },
    });
  });

  it("normalizes editor sheets, issues, and pending SQL operations", () => {
    const document = toMetadataEditorDocument(documentTransport);

    expect(document.source.revision).toEqual({ checksum: "v1" });
    expect(document.sheets.dataflows.rows).toEqual([{ name: "orders" }]);
    expect(document.issues[0]).toEqual({
      severity: "warning",
      sheet: "dataflows",
      row_index: 0,
      column: "name",
      message: "check",
    });
    expect(document.sql_files).toEqual([
      expect.objectContaining({ source_id: 10, query: "orders.sql", content: "SELECT 1" }),
    ]);
  });

  it("adapts workspace responses to the same document view model", () => {
    const workspace = toMetadataEditorWorkspace({
      schema_version: "metadata-editor-workspace.v1",
      environment_id: 7,
      metadata_catalog_version: "catalog-1",
      document: documentTransport,
      draft: null,
    } as unknown as MetadataEditorWorkspaceResponseTransport);
    expect(workspace.document.sheets.dataflows.columns).toEqual([{ key: "name", name: "Name" }]);
    expect(workspace.draft).toBeNull();
  });

  it("keeps backup revisions nullable at the UI boundary", () => {
    const backup = toMetadataBackup({
      id: 42,
      project_id: 3,
      environment_id: 7,
      source_id: 10,
      source_uri: "metadata/orders.json",
      backup_path: ".backups/orders.json",
      source_revision: null,
      saved_revision: { checksum: "v1" },
      created_at: "2026-07-18T00:00:00Z",
    } as unknown as MetadataBackupResponseTransport);

    expect(backup).toMatchObject({
      id: 42,
      source_revision: null,
      saved_revision: { checksum: "v1" },
    });
  });
});
