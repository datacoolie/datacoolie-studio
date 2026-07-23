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
  if (left === right || left.sheets === right.sheets) return false;
  const sheetNames = new Set([...Object.keys(left.sheets), ...Object.keys(right.sheets)]);
  for (const sheetName of sheetNames) {
    const leftSheet = left.sheets[sheetName];
    const rightSheet = right.sheets[sheetName];
    if (leftSheet === rightSheet) continue;
    if (JSON.stringify(leftSheet) !== JSON.stringify(rightSheet)) return true;
  }
  return false;
}
