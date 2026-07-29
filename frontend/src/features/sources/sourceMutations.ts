import type { Dispatch, MutableRefObject, SetStateAction } from "react";

import type { LogSyncRequest, SourceDeleteImpact, SourceImportResponse, SourceReadCheckResult, SourceSyncStatus, StorageBinding } from "../../shared/api/domainTypes";
import { api } from "../../shared/api/client";
import { toErrorMessage } from "../../shared/lib/errors";
import { sourceKey, type SourceKind } from "../../shared/lib/sources";
import type { ModuleKey } from "../../app/moduleRegistry";
import {
  beginSourceOperations,
  finishSourceOperations,
  type SourceBatchAction,
  type SourceBatchEntry,
  type SourceBatchResult,
  type SourceOperations,
} from "./sourceWorkspaceModel";

type SourceMutationContext = {
  environmentId: number | null;
  module: ModuleKey;
  activeEnvironmentIdRef: MutableRefObject<number | null>;
  setBusy: Dispatch<SetStateAction<boolean>>;
  setError: Dispatch<SetStateAction<string | null>>;
  setSourceSyncStatuses: Dispatch<SetStateAction<Record<string, SourceSyncStatus>>>;
  setSourceOperations: Dispatch<SetStateAction<SourceOperations>>;
  invalidateProjectSummaries: () => void;
  refreshEnvironment: (environmentId: number, module: ModuleKey, options: { forceHeader: boolean; forceModule: boolean }) => Promise<void>;
};

export function createEnvironmentSourceMutations(context: SourceMutationContext) {
  const {
    environmentId,
    module,
    activeEnvironmentIdRef,
    setBusy,
    setError,
    setSourceSyncStatuses,
    setSourceOperations,
    invalidateProjectSummaries,
    refreshEnvironment,
  } = context;
  const route = { environmentId, module };

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

  async function importDatacoolieProjectSources(payload: {
    project_uri: string;
    metadata_subpath?: string;
    code_subpath?: string;
    metadata_uri?: string | null;
    code_uri?: string | null;
    include_metadata?: boolean;
    include_code?: boolean;
    storage?: StorageBinding;
  }): Promise<SourceImportResponse | null> {
    if (!route.environmentId) return null;
    setBusy(true);
    setError(null);
    try {
      const result = await api.importDatacoolieProjectSources(route.environmentId, { ...payload, enabled: true });
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

  async function addLogPath(uri: string, label?: string, sourceConfig?: Record<string, unknown>, storage?: StorageBinding) {
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
      if (kind === "metadata") {
        await api.updateMetadataSource(environmentId, id, payload);
      } else if (kind === "logs") {
        await api.updateLogSource(environmentId, id, payload);
      } else {
        await api.updateCodeArtifact(environmentId, id, payload);
      }
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
      if (kind === "metadata") {
        await api.deleteMetadataSource(environmentId, id);
      } else if (kind === "logs") {
        await api.deleteLogSource(environmentId, id);
      } else {
        await api.deleteCodeArtifact(environmentId, id);
      }
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
      return { source_id: id, source_kind: kind, status: "error", message, errors: [{ message }] };
    }
    const entry = { kind, id };
    setSourceOperations((current) =>
      beginSourceOperations(current, environmentId, [entry], "validate")
    );
    setError(null);
    try {
      const result =
        kind === "metadata"
          ? await api.validateMetadataSource(environmentId, id)
          : kind === "logs"
            ? await api.validateLogSource(environmentId, id)
            : await api.validateCodeArtifact(environmentId, id);
      await refreshEnvironment(environmentId, "sources", { forceHeader: true, forceModule: true });
      return result;
    } catch (err) {
      const message = toErrorMessage(err);
      setError(message);
      return { source_id: id, source_kind: kind, status: "error", message, errors: [{ message }] };
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
      return {
        source_id: id,
        source_kind: kind,
        status: "error",
        message,
        error: { message },
        checked_at: new Date().toISOString(),
        latest_job: null
      };
    }
    const entry = { kind, id };
    setSourceOperations((current) =>
      beginSourceOperations(current, environmentId, [entry], "sync")
    );
    setError(null);
    try {
      const result =
        kind === "metadata"
          ? await api.refreshMetadataSource(environmentId, id)
          : kind === "logs"
            ? await api.refreshLogSource(environmentId, id, logSyncRequest ?? { mode: "incremental" })
            : await api.refreshCodeArtifact(environmentId, id);
      if (activeEnvironmentIdRef.current === environmentId) {
        setSourceSyncStatuses((current) => ({ ...current, [sourceKey(kind, id)]: result }));
        await refreshEnvironment(environmentId, route.module, { forceHeader: true, forceModule: true });
      }
      return result;
    } catch (err) {
      const message = toErrorMessage(err);
      if (activeEnvironmentIdRef.current === environmentId) setError(message);
      const result: SourceSyncStatus = {
        source_id: id,
        source_kind: kind,
        status: "error",
        message,
        error: { message },
        checked_at: new Date().toISOString(),
        latest_job: null
      };
      if (activeEnvironmentIdRef.current === environmentId) {
        setSourceSyncStatuses((current) => ({ ...current, [sourceKey(kind, id)]: result }));
      }
      return result;
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
            if (entry.kind === "metadata") await api.deleteMetadataSource(environmentId, entry.id);
            else if (entry.kind === "logs") await api.deleteLogSource(environmentId, entry.id);
            else await api.deleteCodeArtifact(environmentId, entry.id);
            result.succeeded += 1;
            continue;
          }

          const operationResult = action === "validate"
            ? entry.kind === "metadata"
              ? await api.validateMetadataSource(environmentId, entry.id)
              : entry.kind === "logs"
                ? await api.validateLogSource(environmentId, entry.id)
                : await api.validateCodeArtifact(environmentId, entry.id)
            : entry.kind === "metadata"
              ? await api.refreshMetadataSource(environmentId, entry.id)
              : entry.kind === "logs"
                ? await api.refreshLogSource(environmentId, entry.id, logSyncRequest ?? { mode: "incremental" })
                : await api.refreshCodeArtifact(environmentId, entry.id);

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
    if (kind === "metadata") return api.getMetadataSourceDeleteImpact(environmentId, id);
    if (kind === "logs") return api.getLogSourceDeleteImpact(environmentId, id);
    return api.getCodeArtifactDeleteImpact(environmentId, id);
  }

  return {
    addMetadataSource,
    importMetadataSources,
    importDatacoolieProjectSources,
    addLogPath,
    addCodeArtifact,
    updateSource,
    deleteSource,
    validateSource,
    syncSource,
    runSourceBatch,
    getSourceDeleteImpact,
  };
}
