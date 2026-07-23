import { useCallback, useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "../../../shared/api/client";
import type {
  StudioCacheFeature,
  StudioCacheScope,
  StudioCacheStatus,
} from "../../../shared/api/types";
import { toErrorMessage } from "../../../shared/lib/errors";
import { cacheInvalidationBranches, matchesDerivedQuery } from "../cacheInvalidation";

export interface StudioCacheState {
  status: StudioCacheStatus | null;
  loading: boolean;
  busyAction: string | null;
  error: string | null;
  lastAction: string | null;
  reload: () => Promise<void>;
  clear: (scope: StudioCacheScope, features?: StudioCacheFeature[]) => Promise<void>;
  prune: () => Promise<void>;
  compact: () => Promise<void>;
  dismissFeedback: () => void;
}

export function useStudioCache(onChanged?: () => void | Promise<void>): StudioCacheState {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<StudioCacheStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [lastAction, setLastAction] = useState<string | null>(null);

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setStatus(await api.getStudioCache());
    } catch (err) {
      setError(toErrorMessage(err));
      throw err;
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void reload().catch(() => undefined);
  }, [reload]);

  const refreshDiagnostics = useCallback(async () => {
    const tasks: Array<Promise<unknown>> = [reload()];
    if (onChanged) tasks.push(Promise.resolve(onChanged()));
    await Promise.all(tasks);
  }, [onChanged, reload]);

  const clear = useCallback(async (
    scope: StudioCacheScope,
    features: StudioCacheFeature[] = [],
  ) => {
    setBusyAction(`clear:${scope}`);
    setError(null);
    setLastAction(null);
    try {
      await api.clearStudioCache({ scope, features });
      const branches = cacheInvalidationBranches(scope, features);
      await queryClient.invalidateQueries({
        predicate: (query) => matchesDerivedQuery(query.queryKey, branches),
        refetchType: "active",
      });
      await refreshDiagnostics();
      setLastAction(`clear:${scope}`);
    } catch (err) {
      setError(toErrorMessage(err));
      throw err;
    } finally {
      setBusyAction(null);
    }
  }, [queryClient, refreshDiagnostics]);

  const prune = useCallback(async () => {
    setBusyAction("prune");
    setError(null);
    setLastAction(null);
    try {
      await api.pruneStudioCache();
      await refreshDiagnostics();
      setLastAction("prune");
    } catch (err) {
      setError(toErrorMessage(err));
      throw err;
    } finally {
      setBusyAction(null);
    }
  }, [refreshDiagnostics]);

  const compact = useCallback(async () => {
    setBusyAction("compact");
    setError(null);
    setLastAction(null);
    try {
      await api.compactStudioCache();
      await refreshDiagnostics();
      setLastAction("compact");
    } catch (err) {
      setError(toErrorMessage(err));
      throw err;
    } finally {
      setBusyAction(null);
    }
  }, [refreshDiagnostics]);

  const dismissFeedback = useCallback(() => {
    setError(null);
    setLastAction(null);
  }, []);

  return { status, loading, busyAction, error, lastAction, reload, clear, prune, compact, dismissFeedback };
}
