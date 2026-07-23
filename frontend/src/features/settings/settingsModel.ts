import type { StudioSettings } from "../../shared/api/types";
import type { StudioSettingsChanges } from "./hooks/useStudioSettings";

export interface StudioSettingsDraft {
  timezoneInput: string;
  sourceCheckIntervalInput: string;
  useServerDefaultTimezone: boolean;
}

export function createStudioSettingsDraft(settings: StudioSettings): StudioSettingsDraft {
  return {
    timezoneInput: settings.timezone,
    sourceCheckIntervalInput: String(settings.source_check_interval_seconds),
    useServerDefaultTimezone: settings.timezone_source === "server_default",
  };
}

export function isStudioSettingsDraftValid(draft: StudioSettingsDraft): boolean {
  const interval = Number(draft.sourceCheckIntervalInput);
  return (draft.useServerDefaultTimezone || Boolean(draft.timezoneInput.trim()))
    && Number.isInteger(interval)
    && interval >= 5
    && interval <= 3600;
}

export function buildStudioSettingsPatch(
  settings: StudioSettings,
  draft: StudioSettingsDraft,
): StudioSettingsChanges {
  if (!isStudioSettingsDraftValid(draft)) return {};

  const changes: StudioSettingsChanges = {};
  const timezone = draft.timezoneInput.trim();
  if (draft.useServerDefaultTimezone) {
    if (settings.timezone_source === "configured") changes.timezone = null;
  } else if (settings.timezone_source !== "configured" || timezone !== settings.timezone) {
    changes.timezone = timezone;
  }

  const interval = Number(draft.sourceCheckIntervalInput);
  if (interval !== settings.source_check_interval_seconds) {
    changes.source_check_interval_seconds = interval;
  }
  return changes;
}
