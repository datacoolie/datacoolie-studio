import { QueryClient } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../../shared/api/client";
import type {
  MetadataBackup,
  MetadataEditorDocument,
  MetadataEditorWorkspace,
} from "../../shared/api/domainTypes";
import {
  metadataBackupDocumentOptions,
  metadataBackupsOptions,
  metadataEditorWorkspaceOptions,
  sourceRevisionForBackup,
} from "./metadataEditorQueries";

function document(revision: Record<string, unknown> = {}): MetadataEditorDocument {
  return {
    source: {
      source_id: 10,
      environment_id: 7,
      uri: "metadata/orders.json",
      format: "json",
      revision,
    },
    sheets: {},
    issues: [],
  };
}

function workspace(): MetadataEditorWorkspace {
  return {
    schema_version: "metadata-editor-workspace.v1",
    environment_id: 7,
    metadata_catalog_version: "metadata-7",
    document: document(),
    draft: null,
  };
}

const backup: MetadataBackup = {
  id: 42,
  project_id: 3,
  environment_id: 7,
  source_id: 10,
  source_uri: "metadata/orders.json",
  backup_path: ".backups/orders.json",
  created_at: "2026-07-18T00:00:00Z",
};

afterEach(() => vi.restoreAllMocks());

describe("metadata editor query ownership", () => {
  it("reuses the aggregate workspace while its query is fresh", async () => {
    const queryClient = new QueryClient();
    const request = vi
      .spyOn(api, "getEnvironmentMetadataEditorWorkspace")
      .mockResolvedValue(workspace());

    await queryClient.ensureQueryData(metadataEditorWorkspaceOptions(7));
    await queryClient.ensureQueryData(metadataEditorWorkspaceOptions(7));

    expect(request).toHaveBeenCalledTimes(1);
    expect(request).toHaveBeenCalledWith(7);
  });

  it("keeps backup lists and previews in separate stable caches", () => {
    expect(metadataBackupsOptions(7).queryKey).toEqual([
      "environments", 7, "metadata", "backups",
    ]);
    expect(metadataBackupsOptions(8).queryKey).not.toEqual(metadataBackupsOptions(7).queryKey);
    expect(metadataBackupDocumentOptions(42).queryKey).toEqual([
      "metadata-backups", 42, "document",
    ]);
  });
});

describe("sourceRevisionForBackup", () => {
  it("selects the revision owned by the backup source", () => {
    const revision = {
      sources: [
        { source_id: 9, revision: { checksum: "other" } },
        { source_id: 10, revision: { checksum: "target" } },
      ],
    };

    expect(sourceRevisionForBackup(document(revision), backup)).toEqual({ checksum: "target" });
  });

  it("falls back to the document revision for legacy single-source documents", () => {
    const revision = { checksum: "single" };
    expect(sourceRevisionForBackup(document(revision), backup)).toBe(revision);
  });
});
