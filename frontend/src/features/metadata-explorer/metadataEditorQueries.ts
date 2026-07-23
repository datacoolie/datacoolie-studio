import {
  queryOptions,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { api } from "../../shared/api/client";
import type {
  MetadataBackup,
  MetadataEditorDocument,
  MetadataEditorWorkspace,
} from "../../shared/api/domainTypes";
import { environmentQueryKeys } from "../environments/environmentQueries";

const metadataQueryStaleTime = Number.POSITIVE_INFINITY;

export function metadataEditorWorkspaceOptions(environmentId: number) {
  return queryOptions({
    queryKey: environmentQueryKeys.metadataWorkspace(environmentId),
    queryFn: () => api.getEnvironmentMetadataEditorWorkspace(environmentId),
    staleTime: metadataQueryStaleTime,
  });
}

export function metadataBackupsOptions(environmentId: number) {
  return queryOptions({
    queryKey: environmentQueryKeys.metadataBackups(environmentId),
    queryFn: () => api.listEnvironmentMetadataBackups(environmentId),
    staleTime: metadataQueryStaleTime,
  });
}

export function metadataBackupDocumentOptions(backupId: number) {
  return queryOptions({
    queryKey: environmentQueryKeys.metadataBackupDocument(backupId),
    queryFn: () => api.getMetadataBackupDocument(backupId),
    staleTime: metadataQueryStaleTime,
  });
}

type MetadataEditorResourceOptions = {
  enabled: boolean;
  onCatalogChanged?: (mayChangeSources: boolean) => Promise<void> | void;
};

export function useEnvironmentMetadataEditor(
  environmentId: number | null,
  options: MetadataEditorResourceOptions,
) {
  const queryClient = useQueryClient();
  const workspaceQuery = useQuery<MetadataEditorWorkspace>({
    queryKey: environmentQueryKeys.metadataWorkspace(environmentId ?? 0),
    queryFn: () => api.getEnvironmentMetadataEditorWorkspace(environmentId!),
    staleTime: metadataQueryStaleTime,
    enabled: environmentId !== null && options.enabled,
  });

  const validateMutation = useMutation({
    mutationFn: ({ id, document }: { id: number; document: MetadataEditorDocument }) =>
      api.validateEnvironmentMetadataEditorDocument(id, document),
  });
  const saveDraftMutation = useMutation({
    mutationFn: ({ id, document }: { id: number; document: MetadataEditorDocument }) =>
      api.saveEnvironmentMetadataEditorDraft(id, document),
  });
  const discardDraftMutation = useMutation({
    mutationFn: (id: number) => api.discardEnvironmentMetadataEditorDraft(id),
  });
  const saveDocumentMutation = useMutation({
    mutationFn: ({ id, document }: { id: number; document: MetadataEditorDocument }) =>
      api.saveEnvironmentMetadataEditorDocument(id, document),
  });
  const restoreMutation = useMutation({
    mutationFn: ({ backup, document }: { backup: MetadataBackup; document: MetadataEditorDocument }) =>
      api.restoreMetadataBackup(backup.id, sourceRevisionForBackup(document, backup)),
  });
  const deleteBackupMutation = useMutation({
    mutationFn: (backupId: number) => api.deleteMetadataBackup(backupId),
  });
  const clearBackupsMutation = useMutation({
    mutationFn: (id: number) => api.deleteEnvironmentMetadataBackups(id),
  });

  function requireEnvironmentId() {
    if (!environmentId) throw new Error("Select an environment before editing Metadata");
    return environmentId;
  }

  async function ensureContext() {
    const id = requireEnvironmentId();
    return queryClient.ensureQueryData(metadataEditorWorkspaceOptions(id));
  }

  async function validateDocument(document: MetadataEditorDocument) {
    const id = requireEnvironmentId();
    const validation = await validateMutation.mutateAsync({ id, document });
    return { ...document, issues: validation.issues };
  }

  async function saveDraft(document: MetadataEditorDocument) {
    const id = requireEnvironmentId();
    const draft = await saveDraftMutation.mutateAsync({ id, document });
    queryClient.setQueryData<MetadataEditorWorkspace>(
      environmentQueryKeys.metadataWorkspace(id),
      (current) => current ? { ...current, draft } : current,
    );
    return draft;
  }

  async function discardDraft() {
    const id = requireEnvironmentId();
    await discardDraftMutation.mutateAsync(id);
    queryClient.setQueryData<MetadataEditorWorkspace>(
      environmentQueryKeys.metadataWorkspace(id),
      (current) => current ? { ...current, draft: null } : current,
    );
  }

  async function saveDocument(document: MetadataEditorDocument) {
    const id = requireEnvironmentId();
    const workspace = await saveDocumentMutation.mutateAsync({ id, document });
    queryClient.setQueryData(environmentQueryKeys.metadataWorkspace(id), workspace);
    await markBackupsStale(id);
    await options.onCatalogChanged?.(true);
    return workspace.document;
  }

  async function listBackups() {
    const id = requireEnvironmentId();
    return queryClient.ensureQueryData(metadataBackupsOptions(id));
  }

  async function previewBackup(backupId: number) {
    return queryClient.ensureQueryData(metadataBackupDocumentOptions(backupId));
  }

  async function restoreBackup(backup: MetadataBackup, document: MetadataEditorDocument) {
    const id = requireEnvironmentId();
    const workspace = await restoreMutation.mutateAsync({ backup, document });
    queryClient.setQueryData(environmentQueryKeys.metadataWorkspace(id), workspace);
    await markBackupsStale(id);
    await options.onCatalogChanged?.(false);
    return workspace.document;
  }

  async function deleteBackup(backupId: number) {
    const id = requireEnvironmentId();
    await deleteBackupMutation.mutateAsync(backupId);
    queryClient.setQueryData<MetadataBackup[]>(
      environmentQueryKeys.metadataBackups(id),
      (current) => current?.filter((backup) => backup.id !== backupId) ?? current,
    );
    queryClient.removeQueries({
      queryKey: environmentQueryKeys.metadataBackupDocument(backupId),
      exact: true,
    });
  }

  async function clearBackups() {
    const id = requireEnvironmentId();
    const cached = queryClient.getQueryData<MetadataBackup[]>(
      environmentQueryKeys.metadataBackups(id),
    ) ?? [];
    await clearBackupsMutation.mutateAsync(id);
    queryClient.setQueryData(environmentQueryKeys.metadataBackups(id), []);
    for (const backup of cached) {
      queryClient.removeQueries({
        queryKey: environmentQueryKeys.metadataBackupDocument(backup.id),
        exact: true,
      });
    }
  }

  async function markBackupsStale(id: number) {
    await queryClient.invalidateQueries({
      queryKey: environmentQueryKeys.metadataBackups(id),
      exact: true,
      refetchType: "none",
    });
  }

  const mutations = [
    validateMutation,
    saveDraftMutation,
    discardDraftMutation,
    saveDocumentMutation,
    restoreMutation,
    deleteBackupMutation,
    clearBackupsMutation,
  ];
  const mutationError = mutations.find((mutation) => mutation.error)?.error ?? null;

  return {
    workspace: workspaceQuery.data ?? null,
    loading: workspaceQuery.isFetching,
    busy: mutations.some((mutation) => mutation.isPending),
    error: workspaceQuery.error ?? mutationError,
    ensureContext,
    validateDocument,
    saveDraft,
    discardDraft,
    saveDocument,
    listBackups,
    previewBackup,
    restoreBackup,
    deleteBackup,
    clearBackups,
  };
}

export function sourceRevisionForBackup(
  document: MetadataEditorDocument,
  backup: MetadataBackup,
) {
  const revision = document.source.revision;
  const sources = Array.isArray(revision.sources) ? revision.sources : [];
  for (const item of sources) {
    if (!item || typeof item !== "object") continue;
    const source = item as Record<string, unknown>;
    if (Number(source.source_id) !== backup.source_id) continue;
    const sourceRevision = source.revision;
    return sourceRevision && typeof sourceRevision === "object"
      ? sourceRevision as Record<string, unknown>
      : source;
  }
  return revision;
}
