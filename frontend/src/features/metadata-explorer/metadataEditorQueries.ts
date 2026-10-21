import {
  queryOptions,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../../shared/api/client";
import type {
  MetadataBackup,
  MetadataEditorDocument,
  MetadataEditorWorkspace,
  MetadataWriteOperation,
} from "../../shared/api/domainTypes";
import { shouldRetryQueryError } from "../../shared/lib/errors";
import { MetadataPublicationError } from "../../shared/metadata/publicationOutcome";
import { environmentQueryKeys } from "../environments/environmentQueries";

const metadataQueryStaleTime = Number.POSITIVE_INFINITY;

export function metadataEditorWorkspaceOptions(environmentId: number) {
  return queryOptions({
    queryKey: environmentQueryKeys.metadataWorkspace(environmentId),
    queryFn: ({ signal }) => api.getEnvironmentMetadataEditorWorkspace(environmentId, signal),
    staleTime: metadataQueryStaleTime,
    retry: shouldRetryQueryError,
  });
}

export function metadataBackupsOptions(environmentId: number) {
  return queryOptions({
    queryKey: environmentQueryKeys.metadataBackups(environmentId),
    queryFn: ({ signal }) => api.listEnvironmentMetadataBackups(environmentId, signal),
    staleTime: metadataQueryStaleTime,
  });
}

export function metadataBackupDocumentOptions(environmentId: number, backupId: number) {
  return queryOptions({
    queryKey: environmentQueryKeys.metadataBackupDocument(environmentId, backupId),
    queryFn: ({ signal }) => api.getMetadataBackupDocument(environmentId, backupId, signal),
    staleTime: metadataQueryStaleTime,
  });
}

type MetadataEditorResourceOptions = {
  enabled: boolean;
  onCatalogChanged?: (mayChangeSources: boolean, environmentId: number) => Promise<void> | void;
};

export function useEnvironmentMetadataEditor(
  environmentId: number | null,
  options: MetadataEditorResourceOptions,
) {
  const queryClient = useQueryClient();
  const [pending, setPending] = useState<Record<number, number>>({});
  const [followUpError, setFollowUpError] = useState<{ id: number; error: Error } | null>(null);
  const [actionError, setActionError] = useState<{ id: number; error: unknown } | null>(null);
  async function lifecycle<T>(id: number, action: () => Promise<T>): Promise<T> {
    setPending(current => ({ ...current, [id]: (current[id] ?? 0) + 1 }));
    setFollowUpError(null);
    setActionError(null);
    try {
      return await action();
    } catch (error) {
      setActionError({ id, error });
      throw error;
    } finally {
      setPending(current => ({ ...current, [id]: Math.max(0, (current[id] ?? 1) - 1) }));
    }
  }
  async function cancelWorkspace(id: number) {
    await queryClient.cancelQueries({ queryKey: environmentQueryKeys.metadataWorkspace(id), exact: true });
  }
  const workspaceQuery = useQuery<MetadataEditorWorkspace>({
    queryKey: environmentQueryKeys.metadataWorkspace(environmentId ?? 0),
    queryFn: ({ signal }) => api.getEnvironmentMetadataEditorWorkspace(environmentId!, signal),
    staleTime: metadataQueryStaleTime,
    enabled: environmentId !== null && options.enabled,
    retry: shouldRetryQueryError,
  });

  const saveDraftMutation = useMutation({
    mutationFn: ({ id, document }: { id: number; document: MetadataEditorDocument }) =>
      api.saveEnvironmentMetadataEditorDraft(id, document),
  });
  const discardDraftMutation = useMutation({
    mutationFn: (id: number) => api.discardEnvironmentMetadataEditorDraft(id),
  });
  const prepareWriteMutation = useMutation({
    mutationFn: ({ id, document }: { id: number; document: MetadataEditorDocument }) =>
      api.prepareMetadataEditorWrite(id, document),
  });
  const applyWriteMutation = useMutation({
    mutationFn: ({ id, operation }: { id: number; operation: MetadataWriteOperation }) =>
      api.applyMetadataEditorWriteOperation(id, operation.operation_key),
  });
  const abandonWriteMutation = useMutation({
    mutationFn: ({ id, operationKey }: { id: number; operationKey: string }) =>
      api.abandonMetadataEditorWrite(id, operationKey),
  });
  const restoreMutation = useMutation({
    mutationFn: ({ id, backup, document }: { id: number; backup: MetadataBackup; document: MetadataEditorDocument }) =>
      api.restoreMetadataBackup(id, backup.id, sourceRevisionForBackup(document, backup)),
  });
  const deleteBackupMutation = useMutation({
    mutationFn: ({ id, backupId }: { id: number; backupId: number }) =>
      api.deleteMetadataBackup(id, backupId),
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
    return queryClient.fetchQuery(metadataEditorWorkspaceOptions(id));
  }

  async function saveDraft(document: MetadataEditorDocument) {
    const id = requireEnvironmentId();
    return lifecycle(id, async () => {
      await cancelWorkspace(id);
      const draft = await saveDraftMutation.mutateAsync({ id, document });
      await cancelWorkspace(id);
      queryClient.setQueryData<MetadataEditorWorkspace>(
        environmentQueryKeys.metadataWorkspace(id),
        (current) => current ? { ...current, draft } : current,
      );
      await queryClient.invalidateQueries({
        queryKey: environmentQueryKeys.lineage(id),
        refetchType: "active",
    });
    return draft;
    });
  }

  async function discardDraft() {
    const id = requireEnvironmentId();
    return lifecycle(id, async () => {
      await cancelWorkspace(id);
      await discardDraftMutation.mutateAsync(id);
      await cancelWorkspace(id);
      queryClient.setQueryData<MetadataEditorWorkspace>(
        environmentQueryKeys.metadataWorkspace(id),
        (current) => current ? { ...current, draft: null } : current,
      );
      await queryClient.invalidateQueries({
        queryKey: environmentQueryKeys.lineage(id),
        refetchType: "active",
    });
    });
  }

  async function prepareDocumentWrite(document: MetadataEditorDocument) {
    const id = requireEnvironmentId();
    return prepareWriteMutation.mutateAsync({ id, document });
  }

  async function applyPreparedDocumentWrite(operation: MetadataWriteOperation) {
    const id = requireEnvironmentId();
    applyWriteMutation.reset();
    return lifecycle(id, async () => {
      await cancelWorkspace(id);
      let applied = operation;
      if (operation.state !== "succeeded") {
        try {
          applied = await applyWriteMutation.mutateAsync({ id, operation });
        } catch (reason) {
          throw new MetadataPublicationError("Publication response unavailable. Retry the same operation to recover its result.", operation, "publication", reason);
        }
      }
      if (applied.state === "succeeded" || applied.items.some(item => item.state === "applied")) {
        await markPublicationStale(id, true);
      }
      if (applied.state !== "succeeded") {
        throw new MetadataPublicationError("Metadata write is incomplete; retry or abandon the operation", applied, "publication");
      }
      await cancelWorkspace(id);
      await queryClient.invalidateQueries({ queryKey: environmentQueryKeys.metadataWorkspace(id), exact: true, refetchType: "none" });
      let workspace: MetadataEditorWorkspace;
      try {
        workspace = await queryClient.fetchQuery(metadataEditorWorkspaceOptions(id));
      } catch (reason) {
        throw new MetadataPublicationError("Source saved. Refresh the workspace to load the published document.", applied, "workspace-refresh", reason);
      }
      queryClient.setQueryData(environmentQueryKeys.metadataWorkspace(id), workspace);
      await refreshHeader(id, true);
      return workspace.document;
    });
  }

  async function abandonDocumentWrite(operation: MetadataWriteOperation) {
    const id = requireEnvironmentId();
    if (operation.state === "succeeded") return operation;
    return abandonWriteMutation.mutateAsync({
      id,
      operationKey: operation.operation_key,
    });
  }

  async function listBackups() {
    const id = requireEnvironmentId();
    return queryClient.fetchQuery(metadataBackupsOptions(id));
  }

  async function previewBackup(backupId: number) {
    const id = requireEnvironmentId();
    return queryClient.ensureQueryData(metadataBackupDocumentOptions(id, backupId));
  }

  async function restoreBackup(backup: MetadataBackup, document: MetadataEditorDocument) {
    const id = requireEnvironmentId();
    return lifecycle(id, async () => {
      await cancelWorkspace(id);
      const workspace = await restoreMutation.mutateAsync({ id, backup, document });
      await cancelWorkspace(id);
      queryClient.setQueryData(environmentQueryKeys.metadataWorkspace(id), workspace);
      await markPublicationStale(id, false);
      await refreshHeader(id, false);
      return workspace.document;
    });
  }

  async function deleteBackup(backupId: number) {
    const id = requireEnvironmentId();
    await deleteBackupMutation.mutateAsync({ id, backupId });
    await queryClient.cancelQueries({ queryKey: environmentQueryKeys.metadataBackups(id), exact: true });
    await queryClient.cancelQueries({ queryKey: environmentQueryKeys.metadataBackupDocument(id, backupId), exact: true });
    queryClient.setQueryData<MetadataBackup[]>(
      environmentQueryKeys.metadataBackups(id),
      (current) => current?.filter((backup) => backup.id !== backupId) ?? current,
    );
    queryClient.removeQueries({
      queryKey: environmentQueryKeys.metadataBackupDocument(id, backupId),
      exact: true,
    });
  }

  async function clearBackups() {
    const id = requireEnvironmentId();
    await clearBackupsMutation.mutateAsync(id);
    await queryClient.cancelQueries({ queryKey: environmentQueryKeys.metadataBackups(id), exact: true });
    const previewKey = ["metadata", id, "backups"];
    await queryClient.cancelQueries({ queryKey: previewKey });
    queryClient.setQueryData(environmentQueryKeys.metadataBackups(id), []);
    queryClient.removeQueries({ queryKey: previewKey });
  }

  async function markBackupsStale(id: number) {
    await queryClient.cancelQueries({ queryKey: environmentQueryKeys.metadataBackups(id), exact: true });
    await queryClient.invalidateQueries({
      queryKey: environmentQueryKeys.metadataBackups(id),
      exact: true,
      refetchType: "none",
    });
  }

  async function markPublicationStale(id: number, mayChangeSources: boolean) {
    const dependents: (readonly unknown[])[] = [environmentQueryKeys.context(id), environmentQueryKeys.assets(id), environmentQueryKeys.lineage(id), environmentQueryKeys.overview(id)];
    if (mayChangeSources) dependents.push(environmentQueryKeys.sources(id));
    await Promise.all([
      markBackupsStale(id),
      ...dependents.map(queryKey => queryClient.invalidateQueries({ queryKey, refetchType: "none" })),
      queryClient.invalidateQueries({ queryKey: ["projects", "summaries"], refetchType: "none" }),
    ]);
  }

  async function refreshHeader(id: number, mayChangeSources: boolean) {
    try {
      await options.onCatalogChanged?.(mayChangeSources, id);
    } catch (reason) {
      setFollowUpError({ id, error: new Error(`Source saved; workspace updated. Header refresh failed: ${reason instanceof Error ? reason.message : String(reason)}`) });
    }
  }

  const mutations = [
    saveDraftMutation,
    discardDraftMutation,
    prepareWriteMutation,
    applyWriteMutation,
    abandonWriteMutation,
    restoreMutation,
    deleteBackupMutation,
    clearBackupsMutation,
  ];
  const activeMutations = mutations.filter(mutation => {
    const variables = mutation.variables;
    return typeof variables === "number" ? variables === environmentId : variables?.id === environmentId;
  });
  const mutationError = activeMutations.find((mutation) => mutation.error)?.error ?? null;

  return {
    workspace: workspaceQuery.data ?? null,
    loading: workspaceQuery.isFetching,
    busy: (pending[environmentId ?? 0] ?? 0) > 0 || activeMutations.some((mutation) => mutation.isPending),
    savingDraft: saveDraftMutation.isPending,
    error: (actionError?.id === environmentId ? actionError.error : null) ?? workspaceQuery.error ?? mutationError,
    warning: followUpError?.id === environmentId ? followUpError.error.message : null,
    ensureContext,
    retryWorkspace: workspaceQuery.refetch,
    saveDraft,
    discardDraft,
    prepareDocumentWrite,
    applyPreparedDocumentWrite,
    abandonDocumentWrite,
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
