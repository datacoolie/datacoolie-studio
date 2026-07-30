import { describe, expect, it } from "vitest";
import type { MetadataEditorDocument } from "../../shared/api/domainTypes";
import {
  metadataDraftState,
  metadataServerUpdateConflicts
} from "./metadataDraftState";

function document(description = "source"): MetadataEditorDocument {
  return {
    source: {
      source_id: 1,
      environment_id: 2,
      uri: "metadata/orders.json",
      format: "json",
      revision: {}
    },
    sheets: {
      dataflows: {
        columns: [{ key: "description", name: "description" }],
        rows: [{ description }]
      }
    },
    issues: []
  };
}

describe("metadataDraftState", () => {
  it("allows a persisted draft that differs from the source to be saved without a local change", () => {
    expect(metadataDraftState(document(), document("saved draft"), document("saved draft"))).toEqual({
      hasLocalChanges: false,
      hasSourceChanges: true,
      hasStoredDraft: true
    });
  });

  it("separates local editor changes from a persisted draft", () => {
    expect(metadataDraftState(document(), document("saved draft"), document("edited again"))).toEqual({
      hasLocalChanges: true,
      hasSourceChanges: true,
      hasStoredDraft: true
    });
  });

  it("does not report a draft difference when its sheets match the source", () => {
    expect(metadataDraftState(document(), document(), document())).toEqual({
      hasLocalChanges: false,
      hasSourceChanges: false,
      hasStoredDraft: true
    });
  });
});

describe("metadataServerUpdateConflicts", () => {
  it("accepts the stored draft returned for the current local edits", () => {
    const source = document();
    const localDraft = document("edited locally");

    expect(metadataServerUpdateConflicts(
      source,
      null,
      localDraft,
      source,
      document("edited locally")
    )).toBe(false);
  });

  it("accepts the source document returned after saving the current local edits", () => {
    const source = document();
    const localDraft = document("edited locally");

    expect(metadataServerUpdateConflicts(
      source,
      null,
      localDraft,
      document("edited locally"),
      null
    )).toBe(false);
  });

  it("preserves local edits when the incoming server content is different", () => {
    expect(metadataServerUpdateConflicts(
      document(),
      null,
      document("edited locally"),
      document("changed elsewhere"),
      null
    )).toBe(true);
  });
});
