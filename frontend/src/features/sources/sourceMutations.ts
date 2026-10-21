import type { Dispatch, MutableRefObject, SetStateAction } from "react";

import type { QueryClient } from "@tanstack/react-query";
import type { StorageBinding } from "../../shared/api/domainTypes";
import type {
  LogSyncRequest,
  SourceDeleteImpact,
  SourceImportResponse,
  SourceReadCheckResult,
  SourceSyncStatus,
} from "../../shared/api/contractTypes";
import { api } from "../../shared/api/client";
import { environmentQueryKeys } from "../environments/environmentQueries";
import type { EnvironmentSourcesData } from "./environmentSourcesResource";
import { toErrorMessage } from "../../shared/lib/errors";
import { sourceKey, type SourceKind } from "../../shared/lib/sources";
import type { DatacoolieProjectSourceImportInput, DatacoolieProjectSourceImportRequest } from "../../shared/api/contractTypes";
import type { ModuleKey } from "../../app/moduleRegistry";
import {
  beginSourceOperations,
  finishSourceOperations,
  type SourceBatchAction,
  type SourceBatchEntry,
  type SourceBatchResult,
  type SourceOperations,
} from "./sourceWorkspaceModel";

type SourceUpdatePayload = Parameters<typeof api.updateMetadataSource>[2];

type SourceOperationAdapter = {
  update: (environmentId: number, sourceId: number, payload: SourceUpdatePayload) => Promise<unknown>;
  remove: (environmentId: number, sourceId: number) => Promise<void>;
  deleteImpact: (environmentId: number, sourceId: number) => Promise<SourceDeleteImpact>;
  validate: (environmentId: number, sourceId: number) => Promise<SourceReadCheckResult>;
  refresh: (
    environmentId: number,
    sourceId: number,
    logSyncRequest?: LogSyncRequest,
  ) => Promise<SourceSyncStatus>;
  retry: (environmentId: number, sourceId: number) => Promise<SourceSyncStatus>;
};

const sourceOperationAdapters: Record<SourceKind, SourceOperationAdapter> = {
  metadata: {
    update: (environmentId, sourceId, payload) =>
      api.updateMetadataSource(environmentId, sourceId, payload),
    remove: (environmentId, sourceId) => api.deleteMetadataSource(environmentId, sourceId),
    deleteImpact: (environmentId, sourceId) =>
      api.getMetadataSourceDeleteImpact(environmentId, sourceId),
    validate: (environmentId, sourceId) => api.validateMetadataSource(environmentId, sourceId),
    refresh: (environmentId, sourceId) => api.refreshMetadataSource(environmentId, sourceId),
    retry: (environmentId, sourceId) =>
      api.retryMetadataSourceObservation(environmentId, sourceId),
  },
  sql: {
    update: (environmentId, sourceId, payload) =>
      api.updateSqlSource(environmentId, sourceId, payload),
    remove: (environmentId, sourceId) => api.deleteSqlSource(environmentId, sourceId),
    deleteImpact: (environmentId, sourceId) =>
      api.getSqlSourceDeleteImpact(environmentId, sourceId),
    validate: (environmentId, sourceId) => api.validateSqlSource(environmentId, sourceId),
    refresh: (environmentId, sourceId) => api.refreshSqlSource(environmentId, sourceId),
    retry: (environmentId, sourceId) =>
      api.retrySqlSourceObservation(environmentId, sourceId),
  },
  logs: {
    update: (environmentId, sourceId, payload) =>
      api.updateLogSource(environmentId, sourceId, payload),
    remove: (environmentId, sourceId) => api.deleteLogSource(environmentId, sourceId),
    deleteImpact: (environmentId, sourceId) =>
      api.getLogSourceDeleteImpact(environmentId, sourceId),
    validate: (environmentId, sourceId) => api.validateLogSource(environmentId, sourceId),
    refresh: (environmentId, sourceId, logSyncRequest) =>
      api.refreshLogSource(environmentId, sourceId, logSyncRequest ?? { mode: "incremental" }),
    retry: (environmentId, sourceId) =>
      api.retryLogSourceObservation(environmentId, sourceId),
  },
  code: {
    update: (environmentId, sourceId, payload) =>
      api.updateCodeArtifact(environmentId, sourceId, payload),
    remove: (environmentId, sourceId) => api.deleteCodeArtifact(environmentId, sourceId),
    deleteImpact: (environmentId, sourceId) =>
      api.getCodeArtifactDeleteImpact(environmentId, sourceId),
    validate: (environmentId, sourceId) => api.validateCodeArtifact(environmentId, sourceId),
    refresh: (environmentId, sourceId) => api.refreshCodeArtifact(environmentId, sourceId),
    retry: (environmentId, sourceId) =>
      api.retryCodeArtifactObservation(environmentId, sourceId),
  },
};

function sourceSyncFailureStatus(
  sourceId: number,
  sourceKind: SourceKind,
  message: string,
): SourceSyncStatus {
  const now = new Date().toISOString();
  const error = { message };
  return {
    source_id: sourceId,
    source_kind: sourceKind,
    status: "error",
    message,
    validation: {
      state: "not_validated",
      completed_at: null,
      summary: null,
      error: null,
    },
    observation: {
      state: "error",
      checked_at: now,
      pending_changes: null,
      next_check_at: null,
      failure_count: 0,
      error,
    },
    sync_execution: {
      state: "failed",
      trigger: "manual",
      job_id: null,
      started_at: now,
      completed_at: now,
      last_successful_at: null,
      summary: message,
      error,
      result: { status: "error", message, error },
    },
  };
}

type SourceMutationContext = {
  environmentId: number | null;
  module: ModuleKey;
  activeEnvironmentIdRef: MutableRefObject<number | null>;
  queryClient: QueryClient;
  setBusy: Dispatch<SetStateAction<boolean>>;
  setError: Dispatch<SetStateAction<string | null>>;
  setSourceOperations: Dispatch<SetStateAction<SourceOperations>>;
  invalidateProjectSummaries: () => void;
  refreshEnvironment: (environmentId: number, module: ModuleKey, options: { forceHeader: boolean; forceModule: boolean }) => Promise<void>;
};

export function createEnvironmentSourceMutations(context: SourceMutationContext) {
  const {
    environmentId,
    module,
    activeEnvironmentIdRef,
    queryClient,
    setBusy,
    setError,
    setSourceOperations,
    invalidateProjectSummaries,
    refreshEnvironment,
  } = context;
  const route = { environmentId, module };

  function setSourceStatus(environmentId: number, status: SourceSyncStatus) {
    queryClient.setQueryData<EnvironmentSourcesData>(
      environmentQueryKeys.sources(environmentId),
      (current) => current
        ? {
            ...current,
            statuses: {
              ...current.statuses,
              [sourceKey(status.source_kind as SourceKind, status.source_id)]: status,
            },
          }
        : current,
    );
  }

  async function refreshEnvironmentAfterHeaderMutation(
    targetEnvironmentId = route.environmentId,
    targetModule: ModuleKey = route.module
  ) {
    if (!targetEnvironmentId) return;
    await refreshEnvironment(targetEnvironmentId, targetModule, { forceHeader: true, forceModule: true });
  }

  async function addMetadataSource(uri: string, label?: string) {
    if (!route.environmentId) return;
    setBusy(true);
    setError(null);
    try {
      await api.addMetadataSource(route.environmentId, { uri, label, enabled: true });
      invalidateProjectSummaries();
      await refreshEnvironmentAfterHeaderMutation(route.environmentId);
    } catch (err) {
      setError(toErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function importMetadataSources(uri: string, label?: string, storage?: StorageBinding): Promise<SourceImportResponse | null> {
    if (!route.environmentId) return null;
    setBusy(true);
    setError(null);
    try {
      const result = await api.importMetadataSources(route.environmentId, { uri, label, enabled: true, storage });
      invalidateProjectSummaries();
      await refreshEnvironmentAfterHeaderMutation(route.environmentId, "sources");
      return result;
    } catch (err) {
      setError(toErrorMessage(err));
      throw err;
    } finally {
      setBusy(false);
    }
  }

  async function addSqlSource(uri: string, label?: string, storage?: StorageBinding) {
    if (!route.environmentId) throw new Error("Select an environment before adding an SQL source");
    setBusy(true);
    setError(null);
    try {
      await api.addSqlSource(route.environmentId, { uri, label, enabled: true, storage });
      invalidateProjectSummaries();
      await refreshEnvironmentAfterHeaderMutation(route.environmentId, "sources");
    } catch (err) {
      setError(toErrorMessage(err));
      throw err;
    } finally {
      setBusy(false);
    }
  }

  async function importDatacoolieProjectSources(
    payload: DatacoolieProjectSourceImportInput,
  ): Promise<SourceImportResponse | null> {
    if (!route.environmentId) return null;
    setBusy(true);
    setError(null);
    try {
      const requestPayload: DatacoolieProjectSourceImportRequest = {
        ...payload,
        code_discovery_mode: payload.code_discovery_mode ?? "auto",
        enabled: payload.enabled ?? true,
      };
      const result = await api.importDatacoolieProjectSources(route.environmentId, requestPayload);
      invalidateProjectSummaries();
      await refreshEnvironmentAfterHeaderMutation(route.environmentId, "sources");
      return result;
    } catch (err) {
      setError(toErrorMessage(err));
      throw err;
    } finally {
      setBusy(false);
    }
  }

  async function addLogBasePath(uri: string, label?: string, sourceConfig?: Record<string, unknown>, storage?: StorageBinding) {
    if (!route.environmentId) throw new Error("Select an environment before adding a log source");
    setBusy(true);
    setError(null);
    try {
      await api.addLogSource(route.environmentId, { uri, label, enabled: true, source_config: sourceConfig, storage });
      invalidateProjectSummaries();
      await refreshEnvironmentAfterHeaderMutation(route.environmentId);
    } catch (err) {
      setError(toErrorMessage(err));
      throw err;
    } finally {
      setBusy(false);
    }
  }

  async function addCodeArtifact(uri: string, label?: string, sourceConfig?: Record<string, unknown>, storage?: StorageBinding) {
    if (!route.environmentId) throw new Error("Select an environment before adding source code");
    setBusy(true);
    setError(null);
    try {
      await api.addCodeArtifact(route.environmentId, { uri, label, enabled: true, source_config: sourceConfig, storage });
      invalidateProjectSummaries();
      await refreshEnvironmentAfterHeaderMutation(route.environmentId);
    } catch (err) {
      setError(toErrorMessage(err));
      throw err;
    } finally {
      setBusy(false);
    }
  }

  async function updateSource(
    kind: SourceKind,
    id: number,
    payload: {
      uri?: string;
      label?: string | null;
      enabled?: boolean;
      source_config?: Record<string, unknown>;
      storage?: StorageBinding;
      sync_schedule_enabled?: boolean;
      sync_interval_minutes?: number | null;
    }
  ) {
    const environmentId = route.environmentId;
    if (!environmentId) return;
    setBusy(true);
    setError(null);
    try {
      await sourceOperationAdapters[kind].update(environmentId, id, payload);
      invalidateProjectSummaries();
      await refreshEnvironmentAfterHeaderMutation(environmentId, route.module);
    } catch (err) {
      setError(toErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function deleteSource(kind: SourceKind, id: number) {
    const environmentId = route.environmentId;
    if (!environmentId) return;
    const entry = { kind, id };
    setSourceOperations((current) =>
      beginSourceOperations(current, environmentId, [entry], "delete")
    );
    setError(null);
    try {
      await sourceOperationAdapters[kind].remove(environmentId, id);
      invalidateProjectSummaries();
      await refreshEnvironmentAfterHeaderMutation(environmentId, route.module);
    } catch (err) {
      setError(toErrorMessage(err));
    } finally {
      setSourceOperations((current) =>
        finishSourceOperations(current, environmentId, [entry])
      );
    }
  }

  async function validateSource(kind: SourceKind, id: number): Promise<SourceReadCheckResult> {
    const environmentId = route.environmentId;
    if (!environmentId) {
      const message = "Select an environment before validating a source";
      setError(message);
      return {
        source_id: id,
        source_kind: kind,
        status: "error",
        message,
        record_counts: {},
        records_scanned: 0,
        errors: [{ message }],
      };
    }
    const entry = { kind, id };
    setSourceOperations((current) =>
      beginSourceOperations(current, environmentId, [entry], "validate")
    );
    setError(null);
    try {
      const result = await sourceOperationAdapters[kind].validate(environmentId, id);
      await refreshEnvironment(environmentId, "sources", { forceHeader: true, forceModule: true });
      if (result.status === "error" && activeEnvironmentIdRef.current === environmentId) {
        setError(result.message || "Source validation failed");
      }
      return result;
    } catch (err) {
      const message = toErrorMessage(err);
      setError(message);
      return {
        source_id: id,
        source_kind: kind,
        status: "error",
        message,
        record_counts: {},
        records_scanned: 0,
        errors: [{ message }],
      };
    } finally {
      setSourceOperations((current) =>
        finishSourceOperations(current, environmentId, [entry])
      );
    }
  }

  async function syncSource(kind: SourceKind, id: number, logSyncRequest?: LogSyncRequest): Promise<SourceSyncStatus> {
    const environmentId = route.environmentId;
    if (!environmentId) {
      const message = "Select an environment before syncing a source";
      setError(message);
      return sourceSyncFailureStatus(id, kind, message);
    }
    const entry = { kind, id };
    setSourceOperations((current) =>
      beginSourceOperations(current, environmentId, [entry], "sync")
    );
    setError(null);
    try {
      const result = await sourceOperationAdapters[kind].refresh(
        environmentId,
        id,
        logSyncRequest,
      );
      if (activeEnvironmentIdRef.current === environmentId) {
        setSourceStatus(environmentId, result);
        await refreshEnvironment(environmentId, route.module, { forceHeader: true, forceModule: true });
      }
      return result;
    } catch (err) {
      const message = toErrorMessage(err);
      if (activeEnvironmentIdRef.current === environmentId) setError(message);
      const result = sourceSyncFailureStatus(id, kind, message);
      if (activeEnvironmentIdRef.current === environmentId) {
        setSourceStatus(environmentId, result);
      }
      return result;
    } finally {
      setSourceOperations((current) =>
        finishSourceOperations(current, environmentId, [entry])
      );
    }
  }

  async function retrySourceObservation(kind: SourceKind, id: number): Promise<SourceSyncStatus> {
    const environmentId = route.environmentId;
    if (!environmentId) {
      throw new Error("Select an environment before retrying source checks");
    }
    const entry = { kind, id };
    setSourceOperations((current) =>
      beginSourceOperations(current, environmentId, [entry], "retry")
    );
    setError(null);
    try {
      const result = await sourceOperationAdapters[kind].retry(environmentId, id);
      if (activeEnvironmentIdRef.current === environmentId) {
        setSourceStatus(environmentId, result);
        await refreshEnvironment(
          environmentId,
          "sources",
          { forceHeader: true, forceModule: true },
        );
      }
      return result;
    } catch (err) {
      const message = toErrorMessage(err);
      if (activeEnvironmentIdRef.current === environmentId) setError(message);
      throw err;
    } finally {
      setSourceOperations((current) =>
        finishSourceOperations(current, environmentId, [entry])
      );
    }
  }

  async function runSourceBatch(action: SourceBatchAction, entries: SourceBatchEntry[], logSyncRequest?: LogSyncRequest): Promise<SourceBatchResult> {
    const uniqueEntries = Array.from(
      new Map(entries.map((entry) => [`${entry.kind}:${entry.id}`, entry])).values()
    );
    const result: SourceBatchResult = {
      total: uniqueEntries.length,
      succeeded: 0,
      warnings: 0,
      failed: 0,
      errors: []
    };
    const environmentId = route.environmentId;
    if (!environmentId) {
      const message = "Select an environment before running a source action";
      setError(message);
      return { ...result, failed: result.total, errors: result.total ? [message] : [] };
    }
    if (!uniqueEntries.length) return result;

    setSourceOperations((current) =>
      beginSourceOperations(current, environmentId, uniqueEntries, action)
    );
    setError(null);
    try {
      for (const entry of uniqueEntries) {
        try {
          if (action === "delete") {
            await sourceOperationAdapters[entry.kind].remove(environmentId, entry.id);
            result.succeeded += 1;
            continue;
          }

          const operationResult = action === "validate"
            ? await sourceOperationAdapters[entry.kind].validate(environmentId, entry.id)
            : await sourceOperationAdapters[entry.kind].refresh(
                environmentId,
                entry.id,
                logSyncRequest,
              );

          if (operationResult.status === "error") {
            result.failed += 1;
            result.errors.push(`${entry.kind} source #${entry.id}: ${operationResult.message}`);
          } else if (operationResult.status === "warning" || operationResult.status === "running" || operationResult.status === "unknown") {
            result.warnings += 1;
          } else {
            result.succeeded += 1;
          }
        } catch (err) {
          result.failed += 1;
          result.errors.push(`${entry.kind} source #${entry.id}: ${toErrorMessage(err)}`);
        }
      }
      if (action === "delete") invalidateProjectSummaries();
      await refreshEnvironment(environmentId, "sources", { forceHeader: true, forceModule: true });
    } catch (err) {
      setError(toErrorMessage(err));
    } finally {
      setSourceOperations((current) =>
        finishSourceOperations(current, environmentId, uniqueEntries)
      );
    }

    if (result.failed) setError(`${result.failed} source ${result.failed === 1 ? "action" : "actions"} failed`);
    return result;
  }

  async function getSourceDeleteImpact(kind: SourceKind, id: number): Promise<SourceDeleteImpact> {
    const environmentId = route.environmentId;
    if (!environmentId) throw new Error("Select an environment before viewing source delete impact");
    return sourceOperationAdapters[kind].deleteImpact(environmentId, id);
  }

  return {
    addMetadataSource,
    importMetadataSources,
    addSqlSource,
    importDatacoolieProjectSources,
    addLogBasePath,
    addCodeArtifact,
    updateSource,
    deleteSource,
    validateSource,
    syncSource,
    retrySourceObservation,
    runSourceBatch,
    getSourceDeleteImpact,
  };
}
