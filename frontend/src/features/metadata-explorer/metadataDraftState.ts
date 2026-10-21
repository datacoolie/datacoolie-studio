import type { MetadataEditorDocument } from "../../shared/api/domainTypes";

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

export function metadataServerUpdateConflicts(
  previousSourceDocument: MetadataEditorDocument | null,
  previousServerDraft: MetadataEditorDocument | null,
  workingDocument: MetadataEditorDocument | null,
  incomingSourceDocument: MetadataEditorDocument | null,
  incomingServerDraft: MetadataEditorDocument | null
) {
  const previousBase = previousServerDraft ?? previousSourceDocument;
  const incomingBase = incomingServerDraft ?? incomingSourceDocument;
  const hasLocalChanges = Boolean(
    workingDocument
    && previousBase
    && metadataSheetsDiffer(workingDocument, previousBase)
  );
  if (!hasLocalChanges || !workingDocument) return false;
  return !incomingBase || metadataSheetsDiffer(workingDocument, incomingBase);
}

export function metadataSheetsDiffer(left: MetadataEditorDocument, right: MetadataEditorDocument) {
  if (left === right) return false;
  if (left.sheets !== right.sheets) {
    const sheetNames = new Set([...Object.keys(left.sheets), ...Object.keys(right.sheets)]);
    for (const sheetName of sheetNames) {
      const leftSheet = left.sheets[sheetName];
      const rightSheet = right.sheets[sheetName];
      if (leftSheet === rightSheet) continue;
      if (JSON.stringify(leftSheet) !== JSON.stringify(rightSheet)) return true;
    }
  }
  // SQL file content is intentionally kept outside the metadata sheets so it
  // never leaks into source metadata serialization.  It is still part of the
  // editor document's draft/source boundary: a SQL-only draft must keep
  // "Save to source" available after the draft has been persisted.
  return JSON.stringify(left.sql_files ?? []) !== JSON.stringify(right.sql_files ?? []);
}
