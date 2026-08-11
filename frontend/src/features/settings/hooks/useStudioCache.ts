import { useCallback, useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "../../../shared/api/client";
import type {
  StudioCacheFeature,
  StudioCacheMutation,
  StudioCacheScope,
  StudioCacheStatus,
} from "../../../shared/api/domainTypes";
import { toErrorMessage } from "../../../shared/lib/errors";
import { cacheInvalidationBranches, matchesDerivedQuery } from "../cacheInvalidation";

export type StudioCacheAction =
  | { type: "clear"; scope: StudioCacheScope }
  | { type: "compact"; result: StudioCacheMutation };

export interface StudioCacheState {
  status: StudioCacheStatus | null;
  loading: boolean;
  busyAction: string | null;
  error: string | null;
  lastAction: StudioCacheAction | null;
  reload: () => Promise<void>;
  clear: (scope: StudioCacheScope, features?: StudioCacheFeature[]) => Promise<void>;
  compact: () => Promise<void>;
  retryUpgrade: () => Promise<void>;
  dismissFeedback: () => void;
}

export function useStudioCache(onChanged?: () => void | Promise<void>): StudioCacheState {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<StudioCacheStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [lastAction, setLastAction] = useState<StudioCacheAction | null>(null);

  const fetchStatus = useCallback(async (showLoading: boolean) => {
    if (showLoading) setLoading(true);
    setError(null);
    try {
      setStatus(await api.getStudioCache());
    } catch (err) {
      setError(toErrorMessage(err));
      throw err;
    } finally {
      if (showLoading) setLoading(false);
    }
  }, []);

  const reload = useCallback(() => fetchStatus(true), [fetchStatus]);

  useEffect(() => {
    void reload().catch(() => undefined);
  }, [reload]);

  const upgradeState = status?.analytics_cache.upgrade?.state;
  useEffect(() => {
    if (!upgradeState || !["pending", "building", "validating", "publishing"].includes(upgradeState)) {
      return undefined;
    }
    const timer = window.setInterval(() => {
      void fetchStatus(false).catch(() => undefined);
    }, 2_000);
    return () => window.clearInterval(timer);
  }, [fetchStatus, upgradeState]);

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
      setLastAction({ type: "clear", scope });
    } catch (err) {
      setError(toErrorMessage(err));
      throw err;
    } finally {
      setBusyAction(null);
    }
  }, [queryClient, refreshDiagnostics]);

  const compact = useCallback(async () => {
    setBusyAction("compact");
    setError(null);
    setLastAction(null);
    try {
      const result = await api.compactStudioCache();
      await refreshDiagnostics();
      setLastAction({ type: "compact", result });
    } catch (err) {
      setError(toErrorMessage(err));
      throw err;
    } finally {
      setBusyAction(null);
    }
  }, [refreshDiagnostics]);

  const retryUpgrade = useCallback(async () => {
    setBusyAction("analytics-upgrade:retry");
    setError(null);
    setLastAction(null);
    try {
      await api.retryAnalyticsUpgrade();
      await fetchStatus(false);
    } catch (err) {
      setError(toErrorMessage(err));
      throw err;
    } finally {
      setBusyAction(null);
    }
  }, [fetchStatus]);

  const dismissFeedback = useCallback(() => {
    setError(null);
    setLastAction(null);
  }, []);

  return { status, loading, busyAction, error, lastAction, reload, clear, compact, retryUpgrade, dismissFeedback };
}
