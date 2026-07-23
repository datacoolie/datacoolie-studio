import { useCallback, useEffect, useState } from "react";
import { api } from "../../../shared/api/client";
import type { StudioDiagnostics } from "../../../shared/api/domainTypes";
import { toErrorMessage } from "../../../shared/lib/errors";

export interface StudioDiagnosticsState {
  diagnostics: StudioDiagnostics | null;
  loading: boolean;
  error: string | null;
  reload: () => Promise<void>;
}

export function useStudioDiagnostics(enabled: boolean): StudioDiagnosticsState {
  const [diagnostics, setDiagnostics] = useState<StudioDiagnostics | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    if (!enabled) return;
    setLoading(true);
    setError(null);
    try {
      setDiagnostics(await api.getStudioDiagnostics());
    } catch (err) {
      setError(toErrorMessage(err));
      throw err;
    } finally {
      setLoading(false);
    }
  }, [enabled]);

  useEffect(() => {
    if (!enabled) return;
    void reload().catch(() => undefined);
  }, [enabled, reload]);

  return { diagnostics, loading, error, reload };
}
