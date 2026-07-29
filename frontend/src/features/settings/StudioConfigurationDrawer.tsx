import { ChevronDown, Search, X } from "lucide-react";
import { type FormEvent, type KeyboardEvent, useEffect, useMemo, useRef, useState } from "react";
import type { StudioSettings } from "../../shared/api/domainTypes";
import { useDrawerEscape } from "../../shared/hooks/useDrawerEscape";
import type { StudioSettingsChanges } from "./hooks/useStudioSettings";
import {
  buildStudioSettingsPatch,
  createStudioSettingsDraft,
  isStudioSettingsDraftValid,
  type StudioSettingsDraft,
} from "./settingsModel";
import { buildTimezoneOptions, matchTimezoneOptions, type TimezoneOption } from "./timezoneOptions";

interface StudioConfigurationDrawerProps {
  settings: StudioSettings;
  saving: boolean;
  timezoneOptions: TimezoneOption[] | null;
  onTimezoneOptionsLoaded: (options: TimezoneOption[]) => void;
  onSave: (changes: StudioSettingsChanges) => Promise<StudioSettings>;
  onClose: () => void;
}

export function StudioConfigurationDrawer({
  settings,
  saving,
  timezoneOptions,
  onTimezoneOptionsLoaded,
  onSave,
  onClose,
}: StudioConfigurationDrawerProps) {
  const [draft, setDraft] = useState<StudioSettingsDraft>(() => createStudioSettingsDraft(settings));
  const [timezoneMenuOpen, setTimezoneMenuOpen] = useState(false);
  const [timezoneSearch, setTimezoneSearch] = useState("");
  const [activeTimezoneIndex, setActiveTimezoneIndex] = useState(0);
  const timezonePickerRef = useRef<HTMLDivElement | null>(null);
  const timezoneSearchRef = useRef<HTMLInputElement | null>(null);
  const selectedTimezoneRef = useRef<HTMLButtonElement | null>(null);
  const matches = useMemo(
    () => matchTimezoneOptions(
      timezoneOptions ?? [],
      timezoneSearch,
      draft.timezoneInput,
      settings.timezone_offset_minutes,
    ),
    [draft.timezoneInput, settings.timezone_offset_minutes, timezoneOptions, timezoneSearch],
  );
  const changes = useMemo(() => buildStudioSettingsPatch(settings, draft), [draft, settings]);
  const valid = isStudioSettingsDraftValid(draft);
  const hasChanges = Object.keys(changes).length > 0;

  useEffect(() => {
    if (timezoneOptions !== null) return;
    let cancelled = false;
    const timeout = window.setTimeout(() => {
      const options = buildTimezoneOptions();
      if (!cancelled) onTimezoneOptionsLoaded(options);
    }, 0);
    return () => {
      cancelled = true;
      window.clearTimeout(timeout);
    };
  }, [onTimezoneOptionsLoaded, timezoneOptions]);

  useEffect(() => {
    if (!timezoneMenuOpen) return;
    function closeMenuOnOutsideClick(event: PointerEvent) {
      if (timezonePickerRef.current && !timezonePickerRef.current.contains(event.target as Node)) {
        setTimezoneMenuOpen(false);
      }
    }
    document.addEventListener("pointerdown", closeMenuOnOutsideClick);
    return () => document.removeEventListener("pointerdown", closeMenuOnOutsideClick);
  }, [timezoneMenuOpen]);

  useEffect(() => {
    if (!timezoneMenuOpen) return;
    setActiveTimezoneIndex(matches.focusedIndex);
    const animationFrame = window.requestAnimationFrame(() => {
      timezoneSearchRef.current?.focus();
      selectedTimezoneRef.current?.scrollIntoView({ block: "center" });
    });
    return () => window.cancelAnimationFrame(animationFrame);
  }, [draft.timezoneInput, matches.visible, timezoneMenuOpen]);

  useDrawerEscape(onClose, true);

  function updateDraft(changes: Partial<StudioSettingsDraft>) {
    setDraft((current) => ({ ...current, ...changes }));
  }

  function openTimezoneMenu() {
    setTimezoneSearch("");
    setTimezoneMenuOpen(true);
  }

  function selectTimezone(option: TimezoneOption) {
    updateDraft({ timezoneInput: option.name, useServerDefaultTimezone: false });
    setTimezoneSearch("");
    setActiveTimezoneIndex(0);
    setTimezoneMenuOpen(false);
  }

  function onTimezoneSearchKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      if (!matches.visible.length) return;
      setActiveTimezoneIndex((current) => Math.min(current + 1, matches.visible.length - 1));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      if (!matches.visible.length) return;
      setActiveTimezoneIndex((current) => Math.max(current - 1, 0));
    } else if (event.key === "Enter") {
      event.preventDefault();
      const selected = matches.visible[activeTimezoneIndex] ?? matches.visible[0];
      if (selected) selectTimezone(selected);
    } else if (event.key === "Escape") {
      event.preventDefault();
      setTimezoneMenuOpen(false);
    }
  }

  async function submitConfiguration(event: FormEvent) {
    event.preventDefault();
    if (!valid || !hasChanges) return;
    try {
      await onSave(changes);
      onClose();
    } catch {
      // The settings resource owns the visible error; keep this draft open.
    }
  }

  return (
    <div className="metadata-drawer-backdrop" onMouseDown={onClose}>
      <aside
        className="metadata-drawer settings-timezone-drawer"
        aria-label="Studio configuration"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="metadata-drawer-header">
          <div>
            <span className="eyebrow">Configuration scope</span>
            <h2>Studio configuration</h2>
            <small className="settings-drawer-summary">
              {settings.timezone_source === "configured"
                ? `Current override: ${settings.timezone}`
                : `Current default: ${settings.timezone} (server timezone)`}
            </small>
          </div>
          <button className="icon-action small" type="button" onClick={onClose} aria-label="Close Studio configuration">
            <X size={16} />
          </button>
        </header>
        <div className="metadata-drawer-body settings-timezone-drawer-body">
          <form className="settings-timezone-drawer-form" onSubmit={submitConfiguration}>
            <div className="studio-timezone-field">
              <span id="studio-timezone-label">Global timezone (IANA)</span>
              <div className="studio-timezone-combobox" ref={timezonePickerRef}>
                <button
                  id="studio-timezone-select"
                  type="button"
                  className={`studio-timezone-select${timezoneMenuOpen ? " open" : ""}`}
                  onClick={() => timezoneMenuOpen ? setTimezoneMenuOpen(false) : openTimezoneMenu()}
                  aria-labelledby="studio-timezone-label studio-timezone-select"
                  aria-haspopup="listbox"
                  aria-expanded={timezoneMenuOpen}
                  aria-controls="studio-timezone-options"
                >
                  <span><strong>{draft.timezoneInput}</strong><small>{draft.useServerDefaultTimezone ? "Server default" : "Studio override"}</small></span>
                  <ChevronDown size={14} />
                </button>
                {timezoneMenuOpen ? (
                  <div className="studio-timezone-dropdown">
                    <div className="studio-timezone-search">
                      <Search size={14} aria-hidden="true" />
                      <input
                        ref={timezoneSearchRef}
                        value={timezoneSearch}
                        onChange={(event) => setTimezoneSearch(event.target.value)}
                        onKeyDown={onTimezoneSearchKeyDown}
                        placeholder="Search timezone or UTC offset"
                        aria-label="Search timezones"
                        autoComplete="off"
                      />
                    </div>
                    <div className="studio-timezone-menu" id="studio-timezone-options" role="listbox">
                      {matches.visible.length ? matches.visible.map((option, index) => (
                        <button
                          ref={index === matches.focusedIndex ? selectedTimezoneRef : null}
                          key={option.name}
                          type="button"
                          role="option"
                          aria-selected={option.name === draft.timezoneInput}
                          className={index === activeTimezoneIndex ? "active" : ""}
                          onMouseDown={(event) => event.preventDefault()}
                          onMouseEnter={() => setActiveTimezoneIndex(index)}
                          onClick={() => selectTimezone(option)}
                          title={`${option.offsetLabel} ${option.name}`}
                        >
                          <span>{option.offsetLabel}</span>
                          <strong>{option.name}</strong>
                        </button>
                      )) : (
                        <div className="studio-timezone-empty">
                          {timezoneOptions === null ? "Loading timezones…" : "No timezone found"}
                        </div>
                      )}
                    </div>
                    {matches.total > matches.visible.length ? (
                      <div className="studio-timezone-empty">
                        Showing {matches.visible.length} of {matches.total} timezones — search to narrow the list
                      </div>
                    ) : null}
                  </div>
                ) : null}
              </div>
              {draft.useServerDefaultTimezone ? (
                <span className="settings-field-hint">This Studio will follow the server timezone when saved.</span>
              ) : null}
            </div>
            <label htmlFor="source-check-mode-input" className="studio-timezone-field">
              Source observation mode
              <select
                id="source-check-mode-input"
                value={draft.sourceCheckMode}
                onChange={(event) => updateDraft({
                  sourceCheckMode: event.target.value as StudioSettingsDraft["sourceCheckMode"],
                })}
              >
                <option value="adaptive">Adaptive — slow down periodic checks while unchanged</option>
                <option value="fixed">Fixed — always use the configured interval</option>
              </select>
            </label>
            <label htmlFor="source-check-interval-input" className="studio-timezone-field">
              {draft.sourceCheckMode === "adaptive" ? "Active interval (seconds)" : "Check interval (seconds)"}
              <input
                id="source-check-interval-input"
                type="number"
                min="5"
                max="3600"
                step="1"
                value={draft.sourceCheckIntervalInput}
                onChange={(event) => updateDraft({ sourceCheckIntervalInput: event.target.value })}
              />
            </label>
            {draft.sourceCheckMode === "adaptive" ? (
              <label htmlFor="source-check-max-interval-input" className="studio-timezone-field">
                Idle interval (seconds)
                <input
                  id="source-check-max-interval-input"
                  type="number"
                  min={draft.sourceCheckIntervalInput || "5"}
                  max="3600"
                  step="1"
                  value={draft.sourceCheckMaxIntervalInput}
                  onChange={(event) => updateDraft({ sourceCheckMaxIntervalInput: event.target.value })}
                />
                <span className="settings-field-hint">
                  Periodic cadence: {draft.sourceCheckIntervalInput || "—"}s → {
                    Number.isFinite(Number(draft.sourceCheckIntervalInput))
                      ? Math.min(
                        Number(draft.sourceCheckMaxIntervalInput) || 3600,
                        Math.max(60, Number(draft.sourceCheckIntervalInput) * 2),
                      )
                      : "—"
                  }s → {draft.sourceCheckMaxIntervalInput || "—"}s while unchanged.
                </span>
              </label>
            ) : null}
            <span className="settings-field-hint">
              Cloud metadata/code changes sync automatically. Local metadata/code are checked on navigation or foreground. Log sources are observed periodically and sync only through manual action or their schedule.
            </span>
            <div className="settings-timezone-drawer-actions">
              <button type="submit" disabled={saving || !valid || !hasChanges}>
                {saving ? "Saving…" : "Save changes"}
              </button>
              <button
                type="button"
                className="text-action"
                onClick={() => {
                  updateDraft({ useServerDefaultTimezone: !draft.useServerDefaultTimezone });
                  setTimezoneMenuOpen(false);
                }}
                disabled={saving}
              >
                {draft.useServerDefaultTimezone ? "Set Studio override" : "Use server default"}
              </button>
              <button type="button" className="text-action" onClick={onClose} disabled={saving}>Cancel</button>
            </div>
          </form>
        </div>
      </aside>
    </div>
  );
}
