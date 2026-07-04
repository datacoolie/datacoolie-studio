import { useCallback, useEffect, useState } from "react";
import { api } from "../../../shared/api/client";
import type { StudioSettings } from "../../../shared/api/types";
import { toErrorMessage } from "../../../shared/lib/errors";

export interface StudioSettingsState {
  settings: StudioSettings | null;
  busy: boolean;
  error: string | null;
  reload: () => Promise<void>;
  saveTimezone: (timezone: string | null) => Promise<void>;
}

/**
 * Loads and mutates Studio-level settings. `onSaved` lets the composition layer
 * react to a saved timezone (e.g. refresh time-sensitive environment views).
 */
export function useStudioSettings(options?: { onSaved?: () => void | Promise<void> }): StudioSettingsState {
  const [settings, setSettings] = useState<StudioSettings | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const onSaved = options?.onSaved;

  const reload = useCallback(async () => {
    setError(null);
    try {
      setSettings(await api.getStudioSettings());
    } catch (err) {
      setError(toErrorMessage(err));
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  const saveTimezone = useCallback(
    async (timezone: string | null) => {
      setBusy(true);
      setError(null);
      try {
        const updated = await api.updateStudioSettings({ timezone });
        setSettings(updated);
        if (onSaved) await onSaved();
      } catch (err) {
        setError(toErrorMessage(err));
      } finally {
        setBusy(false);
      }
    },
    [onSaved]
  );

  return { settings, busy, error, reload, saveTimezone };
}
