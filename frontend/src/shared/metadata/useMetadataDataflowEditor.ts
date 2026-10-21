import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type {
  MetadataEditorDocument,
  MetadataWriteOperation,
  PendingSqlFileOperation,
} from "../api/domainTypes";
import {
  buildMetadataDataflowRecords,
  findMetadataDataflowRecord,
  isEditableMetadataDataflowRecord,
  type MetadataDataflowRecord,
  type MetadataDataflowSelection,
  updateMetadataDataflowDocument,
} from "./metadataDataflowModel";
import {
  metadataWriteConfirmation,
  type MetadataSaveConfirmation,
} from "./metadataSaveConfirmation";

export interface MetadataDataflowEditorOptions {
  environmentId: number;
  sourceDocument: MetadataEditorDocument | null;
  draftDocument: MetadataEditorDocument | null;
  selection: MetadataDataflowSelection | null;
  onEnsureEditor: () => Promise<void>;
  onSaveDraft: (document: MetadataEditorDocument) => Promise<MetadataEditorDocument>;
  onPrepareSave: (document: MetadataEditorDocument) => Promise<MetadataWriteOperation>;
  onApplySave: (operation: MetadataWriteOperation) => Promise<MetadataEditorDocument>;
  onAbandonSave: (operation: MetadataWriteOperation) => Promise<MetadataWriteOperation>;
  onSaved?: () => void;
}

export interface MetadataDataflowEditorController {
  activeDocument: MetadataEditorDocument | null;
  records: MetadataDataflowRecord[];
  selectedRecord: MetadataDataflowRecord | null;
  selectedEditable: boolean;
  pendingDocument: MetadataEditorDocument | null;
  pendingOperation: MetadataWriteOperation | null;
  setPendingDocument: (document: MetadataEditorDocument | null) => void;
  saveConfirmation: MetadataSaveConfirmation | null;
  saveDraft: (nextRow: Record<string, unknown>, sqlOperation?: PendingSqlFileOperation | null) => Promise<MetadataEditorDocument | undefined>;
  save: (nextRow: Record<string, unknown>, sqlOperation?: PendingSqlFileOperation | null) => Promise<void>;
  confirmSave: () => Promise<MetadataEditorDocument | undefined>;
  cancelSave: () => Promise<void>;
  loading: boolean;
  loadError: string | null;
  retryEditor: () => Promise<void>;
}

/**
 * Coordinates the shared metadata-dataflow edit lifecycle for drawers opened
 * from any feature. Feature components remain responsible for navigation and
 * presentation; this controller owns document projection and save semantics.
 */
export function useMetadataDataflowEditor({
  environmentId,
  sourceDocument,
  draftDocument,
  selection,
  onEnsureEditor,
  onSaveDraft,
  onPrepareSave,
  onApplySave,
  onAbandonSave,
  onSaved,
}: MetadataDataflowEditorOptions): MetadataDataflowEditorController {
  const activeDocument = draftDocument ?? sourceDocument;
  const [pendingDocument, setPendingDocument] = useState<MetadataEditorDocument | null>(null);
  const [pendingOperation, setPendingOperation] = useState<MetadataWriteOperation | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loadedSelection, setLoadedSelection] = useState<string | null>(null);
  const selectionKey = selection ? JSON.stringify([environmentId, selection]) : null;
  const activeSelection = useRef(selectionKey);
  const generation = useRef(0);
  if (activeSelection.current !== selectionKey) { activeSelection.current = selectionKey; generation.current++; }
  const records = useMemo(
    () => selection ? buildMetadataDataflowRecords(activeDocument) : [],
    [activeDocument, selection],
  );
  const selectedRecord = useMemo(
    () => findMetadataDataflowRecord(records, selection),
    [records, selection],
  );
  const ready = selectionKey !== null && loadedSelection === selectionKey && !loading && !loadError;
  const selectedEditable = ready && isEditableMetadataDataflowRecord(activeDocument, selectedRecord);
  const saveConfirmation = useMemo(
    () => pendingOperation ? metadataWriteConfirmation(pendingOperation) : null,
    [pendingOperation],
  );

  useEffect(() => {
    if (!selectionKey) { setLoadedSelection(null); return; }
    let current = true;
    setLoading(true);
    setLoadError(null);
    void onEnsureEditor().then(() => {
      if (current) setLoadedSelection(selectionKey);
    }).catch(reason => {
      if (current) setLoadError(reason instanceof Error ? reason.message : String(reason));
    }).finally(() => { if (current) setLoading(false); });
    return () => { current = false; };
    // Only drawer identity triggers demand reads; cached content is still observed.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectionKey]);

  async function retryEditor() {
    const requestedGeneration = ++generation.current;
    const requestedKey = selectionKey;
    const current = () => generation.current === requestedGeneration && activeSelection.current === requestedKey;
    setLoading(true);
    setLoadError(null);
    try { await onEnsureEditor(); if (current()) setLoadedSelection(requestedKey); }
    catch (reason) { if (current()) setLoadError(reason instanceof Error ? reason.message : String(reason)); }
    finally { if (current()) setLoading(false); }
  }

  const selectedDocument = useCallback((
    nextRow: Record<string, unknown>,
    sqlOperation?: PendingSqlFileOperation | null,
  ) => {
    if (!activeDocument || !selectedRecord || !selectedEditable) return null;
    return updateMetadataDataflowDocument(activeDocument, selectedRecord.rowIndex, nextRow, sqlOperation);
  }, [activeDocument, selectedEditable, selectedRecord]);

  const saveDraft = useCallback(async (
    nextRow: Record<string, unknown>,
    sqlOperation?: PendingSqlFileOperation | null,
  ) => {
    const nextDocument = selectedDocument(nextRow, sqlOperation);
    if (!nextDocument) return undefined;
    try {
      return await onSaveDraft(nextDocument);
    } catch {
      return undefined;
    }
  }, [onSaveDraft, selectedDocument]);

  const save = useCallback(async (
    nextRow: Record<string, unknown>,
    sqlOperation?: PendingSqlFileOperation | null,
  ) => {
    const nextDocument = selectedDocument(nextRow, sqlOperation);
    if (!nextDocument) return;
    try {
      const operation = await onPrepareSave(nextDocument);
      setPendingDocument(nextDocument);
      setPendingOperation(operation);
    } catch {
      setPendingDocument(null);
      setPendingOperation(null);
    }
  }, [onPrepareSave, selectedDocument]);

  const confirmSave = useCallback(async () => {
    if (!pendingDocument) return undefined;
    if (!pendingOperation) return undefined;
    try {
      const saved = await onApplySave(pendingOperation);
      if (saved) {
        setPendingDocument(null);
        setPendingOperation(null);
        onSaved?.();
      }
      return saved;
    } catch (error) {
      const operation = (error as { operation?: MetadataWriteOperation }).operation;
      if (operation) setPendingOperation(operation);
      return undefined;
    }
  }, [onApplySave, onSaved, pendingDocument, pendingOperation]);

  const cancelSave = useCallback(async () => {
    if (pendingOperation && pendingOperation.state !== "succeeded") {
      try {
        await onAbandonSave(pendingOperation);
      } catch {
        return;
      }
    }
    setPendingDocument(null);
    setPendingOperation(null);
  }, [onAbandonSave, pendingOperation]);

  return {
    activeDocument,
    records,
    selectedRecord: ready ? selectedRecord : null,
    selectedEditable,
    pendingDocument,
    pendingOperation,
    setPendingDocument,
    saveConfirmation,
    saveDraft,
    save,
    confirmSave,
    cancelSave,
    loading: Boolean(selectionKey && !ready && !loadError) || loading,
    loadError,
    retryEditor,
  };
}
