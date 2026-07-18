import type { MetadataEditorDocument } from "../../shared/api/types";

export type MetadataDraftState = {
  hasLocalChanges: boolean;
  hasSourceChanges: boolean;
  hasStoredDraft: boolean;
};

export function metadataDraftState(
  sourceDocument: MetadataEditorDocument | null,
  serverDraft: MetadataEditorDocument | null,
  activeDocument: MetadataEditorDocument | null
): MetadataDraftState {
  const draftBase = serverDraft ?? sourceDocument;
  const hasStoredDraft = Boolean(serverDraft);
  const hasLocalChanges = Boolean(activeDocument && draftBase && metadataSheetsDiffer(activeDocument, draftBase));
  const hasSourceChanges = Boolean(activeDocument && sourceDocument && metadataSheetsDiffer(activeDocument, sourceDocument));

  return {
    hasLocalChanges,
    hasSourceChanges,
    hasStoredDraft
  };
}

export function metadataSheetsDiffer(left: MetadataEditorDocument, right: MetadataEditorDocument) {
  return JSON.stringify(left.sheets) !== JSON.stringify(right.sheets);
}
