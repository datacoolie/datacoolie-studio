import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../shared/api/client";
import type { ModuleInfo } from "../shared/api/domainTypes";
import type { CapabilityKey } from "./moduleRegistry";

export interface StudioModulesState {
  modules: ModuleInfo[];
  enabledCapabilities: ReadonlySet<CapabilityKey>;
  loading: boolean;
  error: string | null;
  busyKey: string | null;
  reload: () => Promise<void>;
  setEnabled: (key: string, enabled: boolean) => Promise<void>;
}

/**
 * Loads the Studio capability-module catalog and exposes the set of enabled
 * capabilities used to gate navigation and routing.
 */
export function useStudioModules(): StudioModulesState {
  const [modules, setModules] = useState<ModuleInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyKey, setBusyKey] = useState<string | null>(null);

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setModules(await api.listModules());
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  const setEnabled = useCallback(async (key: string, enabled: boolean) => {
    setBusyKey(key);
    setError(null);
    try {
      const updated = await api.setModuleEnabled(key, enabled);
      setModules((current) => current.map((module) => (module.key === updated.key ? updated : module)));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      throw err;
    } finally {
      setBusyKey(null);
    }
  }, []);

  const enabledCapabilities = useMemo<ReadonlySet<CapabilityKey>>(
    () => new Set(modules.filter((module) => module.enabled).map((module) => module.key as CapabilityKey)),
    [modules]
  );

  return { modules, enabledCapabilities, loading, error, busyKey, reload, setEnabled };
}
