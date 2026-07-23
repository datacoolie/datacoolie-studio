import { useCallback, useEffect, useState } from "react";
import { api } from "../../../shared/api/client";
import type { StudioSettings } from "../../../shared/api/types";
import { toErrorMessage } from "../../../shared/lib/errors";

export interface StudioSettingsState {
  settings: StudioSettings | null;
  loading: boolean;
  saving: boolean;
  error: string | null;
  reload: () => Promise<void>;
  saveSettings: (changes: StudioSettingsChanges) => Promise<StudioSettings>;
}

export interface StudioSettingsChanges {
  timezone?: string | null;
  source_check_interval_seconds?: number;
}

/**
 * Loads and mutates Studio-level settings. `onSaved` lets the composition layer
 * react to a saved timezone (e.g. refresh time-sensitive environment views).
 */
export function useStudioSettings(options?: { onSaved?: () => void | Promise<void> }): StudioSettingsState {
  const [settings, setSettings] = useState<StudioSettings | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const onSaved = options?.onSaved;

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setSettings(await api.getStudioSettings());
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

  const saveSettings = useCallback(async (changes: StudioSettingsChanges) => {
    setSaving(true);
    setError(null);
    try {
      const updated = await api.updateStudioSettings(changes);
      setSettings(updated);
      if (onSaved) await onSaved();
      return updated;
    } catch (err) {
      setError(toErrorMessage(err));
      throw err;
    } finally {
      setSaving(false);
    }
  }, [onSaved]);

  return { settings, loading, saving, error, reload, saveSettings };
}
