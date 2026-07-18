import { ChevronDown, Settings, X } from "lucide-react";
import { FormEvent, KeyboardEvent, useEffect, useMemo, useRef, useState } from "react";
import type { ModuleInfo, StudioSettings } from "../../shared/api/types";
import { Tag } from "../../shared/components/Tag";
import { Toggle } from "../../shared/components/Toggle";
import { useDrawerEscape } from "../../shared/hooks/useDrawerEscape";

interface SettingsPageProps {
  settings: StudioSettings | null;
  busy: boolean;
  onSaveSettings: (changes: { timezone: string | null; source_check_interval_seconds: number }) => Promise<void>;
  modules: ModuleInfo[];
  modulesBusyKey: string | null;
  onToggleModule: (key: string, enabled: boolean) => Promise<void>;
}

interface TimezoneOption {
  name: string;
  offsetLabel: string;
  shortOffsetLabel: string;
  offsetMinutes: number | null;
  searchText: string;
}

const MAX_VISIBLE_TIMEZONE_OPTIONS = 250;
const FALLBACK_TIMEZONE_NAMES = [
  "UTC",
  "Etc/UTC",
  "Pacific/Honolulu",
  "America/Anchorage",
  "America/Los_Angeles",
  "America/Phoenix",
  "America/Denver",
  "America/Chicago",
  "America/New_York",
  "America/Toronto",
  "America/Mexico_City",
  "America/Bogota",
  "America/Lima",
  "America/Santiago",
  "America/Sao_Paulo",
  "America/Argentina/Buenos_Aires",
  "Europe/Dublin",
  "Europe/London",
  "Europe/Paris",
  "Europe/Berlin",
  "Europe/Madrid",
  "Europe/Rome",
  "Europe/Warsaw",
  "Europe/Athens",
  "Europe/Bucharest",
  "Europe/Istanbul",
  "Europe/Moscow",
  "Africa/Cairo",
  "Africa/Johannesburg",
  "Asia/Jerusalem",
  "Asia/Dubai",
  "Asia/Karachi",
  "Asia/Kolkata",
  "Asia/Kathmandu",
  "Asia/Dhaka",
  "Asia/Bangkok",
  "Asia/Ho_Chi_Minh",
  "Asia/Jakarta",
  "Asia/Shanghai",
  "Asia/Hong_Kong",
  "Asia/Taipei",
  "Asia/Singapore",
  "Asia/Kuala_Lumpur",
  "Asia/Manila",
  "Asia/Seoul",
  "Asia/Tokyo",
  "Australia/Perth",
  "Australia/Adelaide",
  "Australia/Sydney",
  "Pacific/Auckland"
] as const;

export function SettingsPage({ settings, busy, onSaveSettings, modules, modulesBusyKey, onToggleModule }: SettingsPageProps) {
  const [timezoneInput, setTimezoneInput] = useState("");
  const [sourceCheckIntervalInput, setSourceCheckIntervalInput] = useState("30");
  const [configurationDrawerOpen, setConfigurationDrawerOpen] = useState(false);
  const [timezoneMenuOpen, setTimezoneMenuOpen] = useState(false);
  const [useServerDefaultTimezone, setUseServerDefaultTimezone] = useState(false);
  const [activeTimezoneIndex, setActiveTimezoneIndex] = useState(0);
  const timezonePickerRef = useRef<HTMLDivElement | null>(null);
  const timezoneOptions = useMemo(buildTimezoneOptions, []);
  const filteredTimezoneOptions = useMemo(() => {
    const query = normalizeTimezoneSearch(timezoneInput);
    if (!query) return timezoneOptions;
    return timezoneOptions.filter((option) => option.searchText.includes(query));
  }, [timezoneInput, timezoneOptions]);
  const visibleTimezoneOptions = useMemo(
    () => filteredTimezoneOptions.slice(0, MAX_VISIBLE_TIMEZONE_OPTIONS),
    [filteredTimezoneOptions]
  );

  useEffect(() => {
    setTimezoneInput(settings?.timezone ?? "");
  }, [settings?.timezone]);

  useEffect(() => {
    setSourceCheckIntervalInput(String(settings?.source_check_interval_seconds ?? 30));
  }, [settings?.source_check_interval_seconds]);

  useEffect(() => {
    setActiveTimezoneIndex(0);
  }, [timezoneInput]);

  useEffect(() => {
    function closeMenuOnOutsideClick(event: PointerEvent) {
      if (timezonePickerRef.current && !timezonePickerRef.current.contains(event.target as Node)) {
        setTimezoneMenuOpen(false);
      }
    }
    document.addEventListener("pointerdown", closeMenuOnOutsideClick);
    return () => document.removeEventListener("pointerdown", closeMenuOnOutsideClick);
  }, []);

  useDrawerEscape(closeConfigurationDrawer, configurationDrawerOpen);

  async function submitConfiguration(event: FormEvent) {
    event.preventDefault();
    const timezone = timezoneInput.trim();
    const intervalSeconds = Number(sourceCheckIntervalInput);
    if ((!useServerDefaultTimezone && !timezone) || !isValidSourceCheckInterval(intervalSeconds)) return;
    await onSaveSettings({
      timezone: useServerDefaultTimezone ? null : timezone,
      source_check_interval_seconds: intervalSeconds,
    });
    closeConfigurationDrawer();
  }

  function openConfigurationDrawer() {
    setTimezoneInput(settings?.timezone ?? "");
    setSourceCheckIntervalInput(String(settings?.source_check_interval_seconds ?? 30));
    setUseServerDefaultTimezone(settings?.timezone_source === "server_default");
    setTimezoneMenuOpen(false);
    setConfigurationDrawerOpen(true);
  }

  function closeConfigurationDrawer() {
    setTimezoneMenuOpen(false);
    setConfigurationDrawerOpen(false);
  }

  function openTimezoneMenu() {
    if (!timezoneOptions.length || useServerDefaultTimezone) return;
    setTimezoneMenuOpen(true);
  }

  function toggleTimezoneMenu() {
    if (!timezoneOptions.length || useServerDefaultTimezone) return;
    setTimezoneMenuOpen((open) => !open);
  }

  function selectTimezone(option: TimezoneOption) {
    setUseServerDefaultTimezone(false);
    setTimezoneInput(option.name);
    setTimezoneMenuOpen(false);
  }

  function onTimezoneInputKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (useServerDefaultTimezone) return;
    if (event.key === "ArrowDown") {
      event.preventDefault();
      openTimezoneMenu();
      if (!visibleTimezoneOptions.length) return;
      setActiveTimezoneIndex((current) => Math.min(current + 1, visibleTimezoneOptions.length - 1));
      return;
    }
    if (event.key === "ArrowUp") {
      event.preventDefault();
      openTimezoneMenu();
      if (!visibleTimezoneOptions.length) return;
      setActiveTimezoneIndex((current) => Math.max(current - 1, 0));
      return;
    }
    if (event.key === "Enter" && timezoneMenuOpen && visibleTimezoneOptions.length) {
      event.preventDefault();
      const selected = visibleTimezoneOptions[activeTimezoneIndex] ?? visibleTimezoneOptions[0];
      if (selected) selectTimezone(selected);
      return;
    }
  }

  const workspaceDatabase = settings?.storage?.workspace_database;
  const analyticsCache = settings?.storage?.analytics_cache;

  return (
    <div className="settings-layout settings-layout-single">

      <section className="table-panel studio-settings-panel">
        <div className="panel-toolbar">
          <div>
            <h2>Studio configuration</h2>
            <span>Global runtime</span>
          </div>
          <button type="button" className="settings-stat-edit" onClick={openConfigurationDrawer}>Edit configuration</button>
        </div>

        <section className="settings-section" aria-labelledby="configurable-settings-heading">
          <div className="settings-section-heading">
            <h3 id="configurable-settings-heading">Configurable settings</h3>
            <span>Changes apply to this Studio.</span>
          </div>
          <div className="settings-stat-grid">
            <div className="settings-stat-card">
              <p className="settings-stat-label">Timezone</p>
              <dl className="settings-stat-dl">
                <div className="settings-stat-row">
                  <dt>Active</dt>
                  <dd>{settings?.timezone ?? "—"}</dd>
                </div>
                <div className="settings-stat-row">
                  <dt>Source</dt>
                  <dd>{settings?.timezone_source === "configured" ? "Studio override" : "Server default"}</dd>
                </div>
              </dl>
            </div>

            <div className="settings-stat-card">
              <p className="settings-stat-label">Source change check</p>
              <dl className="settings-stat-dl">
                <div className="settings-stat-row">
                  <dt>Check every</dt>
                  <dd>{settings?.source_check_interval_seconds ?? 30} seconds</dd>
                </div>
                <div className="settings-stat-row">
                  <dt>Mode</dt>
                  <dd>Revision-aware</dd>
                </div>
              </dl>
            </div>
          </div>
        </section>

        <section className="settings-section" aria-labelledby="system-information-heading">
          <div className="settings-section-heading">
            <h3 id="system-information-heading">System information</h3>
            <span>Read-only runtime and storage details.</span>
          </div>
          <div className="settings-stat-grid">
            <div className="settings-stat-card">
              <p className="settings-stat-label">API</p>
              <dl className="settings-stat-dl">
                <div className="settings-stat-row">
                  <dt>Prefix</dt>
                  <dd><code>/api/v1</code></dd>
                </div>
              </dl>
            </div>

            <div className="settings-stat-card">
              <p className="settings-stat-label">Workspace database</p>
              <dl className="settings-stat-dl">
                <div className="settings-stat-row">
                  <dt>Path</dt>
                  <dd><code className="settings-path-value">{workspaceDatabase?.path ?? "—"}</code></dd>
                </div>
                <div className="settings-stat-row">
                  <dt>Size</dt>
                  <dd>{formatBytes(workspaceDatabase?.size_bytes)}</dd>
                </div>
              </dl>
            </div>

            <div className="settings-stat-card">
              <p className="settings-stat-label">Analytics cache</p>
              <dl className="settings-stat-dl">
                <div className="settings-stat-row">
                  <dt>Scope</dt>
                  <dd>{analyticsCache?.scope === "studio" ? "Studio-level" : "—"}</dd>
                </div>
                <div className="settings-stat-row">
                  <dt>Path</dt>
                  <dd><code className="settings-path-value">{analyticsCache?.path ?? "—"}</code></dd>
                </div>
                <div className="settings-stat-row">
                  <dt>Size</dt>
                  <dd>{formatBytes(analyticsCache?.size_bytes)}</dd>
                </div>
                <div className="settings-stat-row">
                  <dt>Rows</dt>
                  <dd>
                    {analyticsCache
                      ? `${formatCount(analyticsCache.dataflow_row_count)} df · ${formatCount(analyticsCache.job_row_count)} jobs`
                      : "—"}
                  </dd>
                </div>
                <div className="settings-stat-row">
                  <dt>Sources</dt>
                  <dd>
                    {analyticsCache
                      ? `${formatCount(analyticsCache.active_source_count)} active / ${formatCount(analyticsCache.cached_source_count)} total`
                      : "—"}
                  </dd>
                </div>
                {analyticsCache?.orphan_source_ids?.length ? (
                  <div className="settings-stat-row">
                    <dt>Orphans</dt>
                    <dd><code className="settings-path-value">{analyticsCache.orphan_source_ids.join(", ")}</code></dd>
                  </div>
                ) : null}
              </dl>
            </div>
          </div>
        </section>
      </section>

      <section className="table-panel">
        <div className="panel-toolbar">
          <div>
            <h2>Modules</h2>
            <span>Enable or disable Studio capability modules</span>
          </div>
        </div>
        <div className="module-catalog">
          {modules.length === 0 ? (
            <p className="module-card-desc">No modules registered.</p>
          ) : (
            modules.map((module) => {
              const comingSoon = module.status === "coming_soon";
              return (
                <div key={module.key} className={`module-card${comingSoon ? " module-card-muted" : ""}`}>
                  <div className="module-card-main">
                    <div className="module-card-icon">
                      <Settings size={18} />
                    </div>
                    <div>
                      <div className="module-card-title">
                        {module.name}
                        {comingSoon ? <Tag tone="info">Coming soon</Tag> : module.enabled ? <Tag tone="success">Enabled</Tag> : <Tag tone="neutral">Disabled</Tag>}
                      </div>
                      <p className="module-card-desc">{module.description}</p>
                    </div>
                  </div>
                  <div className="module-card-control">
                    <Toggle
                      checked={module.enabled}
                      label={`Toggle ${module.name} module`}
                      disabled={!module.togglable || modulesBusyKey === module.key}
                      onToggle={(next) => {
                        void onToggleModule(module.key, next);
                      }}
                    />
                  </div>
                </div>
              );
            })
          )}
        </div>
      </section>

      {configurationDrawerOpen ? (
        <div className="metadata-drawer-backdrop" onMouseDown={closeConfigurationDrawer}>
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
                  {settings?.timezone_source === "configured"
                    ? `Current override: ${settings?.timezone ?? "-"}`
                    : `Current default: ${settings?.timezone ?? "-"} (server timezone)`}
                </small>
              </div>
              <button className="icon-action small" type="button" onClick={closeConfigurationDrawer} aria-label="Close Studio configuration">
                <X size={16} />
              </button>
            </header>
            <div className="metadata-drawer-body settings-timezone-drawer-body">
              <form className="settings-timezone-drawer-form" onSubmit={submitConfiguration}>
                <label htmlFor="studio-timezone-input" className="studio-timezone-field">
                  Global timezone (IANA)
                  <div className="studio-timezone-combobox" ref={timezonePickerRef}>
                    <input
                      id="studio-timezone-input"
                      value={timezoneInput}
                      disabled={useServerDefaultTimezone}
                      onFocus={openTimezoneMenu}
                      onClick={openTimezoneMenu}
                      onChange={(event) => {
                        setUseServerDefaultTimezone(false);
                        setTimezoneInput(event.target.value);
                        openTimezoneMenu();
                      }}
                      onKeyDown={onTimezoneInputKeyDown}
                      placeholder="Search timezone (e.g. Asia/Ho_Chi_Minh, UTC+7)"
                      autoComplete="off"
                      role="combobox"
                      aria-expanded={timezoneMenuOpen}
                      aria-controls="studio-timezone-options"
                    />
                    <button
                      type="button"
                      className={`studio-timezone-trigger${timezoneMenuOpen ? " open" : ""}`}
                      onClick={toggleTimezoneMenu}
                      disabled={useServerDefaultTimezone}
                      aria-label={timezoneMenuOpen ? "Close timezone options" : "Open timezone options"}
                    >
                      <ChevronDown size={14} />
                    </button>
                    {timezoneMenuOpen ? (
                      <div className="studio-timezone-menu" id="studio-timezone-options" role="listbox">
                        {visibleTimezoneOptions.length ? (
                          visibleTimezoneOptions.map((option, index) => (
                            <button
                              key={option.name}
                              type="button"
                              role="option"
                              aria-selected={option.name === timezoneInput}
                              className={index === activeTimezoneIndex ? "active" : ""}
                              onMouseDown={(event) => event.preventDefault()}
                              onMouseEnter={() => setActiveTimezoneIndex(index)}
                              onClick={() => selectTimezone(option)}
                              title={`${option.offsetLabel} ${option.name}`}
                            >
                              <span>{option.offsetLabel}</span>
                              <strong>{option.name}</strong>
                            </button>
                          ))
                        ) : (
                          <div className="studio-timezone-empty">No timezone found</div>
                        )}
                        {filteredTimezoneOptions.length > visibleTimezoneOptions.length ? (
                          <div className="studio-timezone-empty">
                            Showing first {visibleTimezoneOptions.length} of {filteredTimezoneOptions.length} matches
                          </div>
                        ) : null}
                      </div>
                    ) : null}
                  </div>
                  {useServerDefaultTimezone ? (
                    <span className="settings-field-hint">This Studio will follow the server timezone when saved.</span>
                  ) : null}
                </label>
                <label htmlFor="source-check-interval-input" className="studio-timezone-field">
                  Source change check (seconds)
                  <input
                    id="source-check-interval-input"
                    type="number"
                    min="5"
                    max="3600"
                    step="1"
                    value={sourceCheckIntervalInput}
                    onChange={(event) => setSourceCheckIntervalInput(event.target.value)}
                  />
                </label>
                <div className="settings-timezone-drawer-actions">
                  <button
                    type="submit"
                    disabled={busy || (!useServerDefaultTimezone && !timezoneInput.trim()) || !isValidSourceCheckInterval(Number(sourceCheckIntervalInput))}
                  >
                    Save changes
                  </button>
                  <button
                    type="button"
                    className="text-action"
                    onClick={() => {
                      setUseServerDefaultTimezone((value) => !value);
                      setTimezoneMenuOpen(false);
                    }}
                    disabled={busy}
                  >
                    {useServerDefaultTimezone ? "Set Studio override" : "Use server default"}
                  </button>
                  <button type="button" className="text-action" onClick={closeConfigurationDrawer} disabled={busy}>
                    Cancel
                  </button>
                </div>
              </form>
            </div>
          </aside>
        </div>
      ) : null}
    </div>
  );
}

function isValidSourceCheckInterval(value: number) {
  return Number.isInteger(value) && value >= 5 && value <= 3600;
}

function formatCount(value: number | null | undefined): string {
  if (!Number.isFinite(value)) return "0";
  return new Intl.NumberFormat("en-US").format(Number(value));
}

function formatBytes(value: number | null | undefined): string {
  if (!Number.isFinite(value) || Number(value) < 0) return "-";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let current = Number(value);
  let unitIndex = 0;
  while (current >= 1024 && unitIndex < units.length - 1) {
    current /= 1024;
    unitIndex += 1;
  }
  const precision = unitIndex === 0 ? 0 : current >= 100 ? 0 : current >= 10 ? 1 : 2;
  return `${current.toFixed(precision)} ${units[unitIndex]}`;
}

function buildTimezoneOptions(): TimezoneOption[] {
  const intlApi = Intl as typeof Intl & { supportedValuesOf?: (key: string) => string[] };
  try {
    const timezoneNames = resolveTimezoneNames(intlApi);
    const now = new Date();
    return timezoneNames
      .map((name) => {
        const offsetMinutes = timezoneOffsetMinutes(name, now);
        const offsetLabel = formatUtcOffset(offsetMinutes, true);
        const shortOffsetLabel = formatUtcOffset(offsetMinutes, false);
        return {
          name,
          offsetLabel,
          shortOffsetLabel,
          offsetMinutes,
          searchText: normalizeTimezoneSearch(`${name} ${offsetLabel} ${shortOffsetLabel}`)
        };
      })
      .sort((left, right) => {
        const leftOffset = left.offsetMinutes ?? Number.POSITIVE_INFINITY;
        const rightOffset = right.offsetMinutes ?? Number.POSITIVE_INFINITY;
        if (leftOffset !== rightOffset) return leftOffset - rightOffset;
        return left.name.localeCompare(right.name);
      });
  } catch {
    return [];
  }
}

function resolveTimezoneNames(intlApi: typeof Intl & { supportedValuesOf?: (key: string) => string[] }): string[] {
  const names = new Set<string>(FALLBACK_TIMEZONE_NAMES);
  const localTimezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  if (localTimezone) names.add(localTimezone);
  if (typeof intlApi.supportedValuesOf === "function") {
    try {
      for (const timezoneName of intlApi.supportedValuesOf("timeZone")) {
        names.add(timezoneName);
      }
    } catch {
      // Keep fallback list only when supportedValuesOf throws.
    }
  }
  return Array.from(names);
}

function timezoneOffsetMinutes(timezoneName: string, reference: Date): number | null {
  try {
    const parts = new Intl.DateTimeFormat("en-US", {
      timeZone: timezoneName,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false
    }).formatToParts(reference);
    const values = Object.fromEntries(
      parts.filter((part) => part.type !== "literal").map((part) => [part.type, Number(part.value)])
    ) as Record<string, number>;
    if (
      !Number.isFinite(values.year) ||
      !Number.isFinite(values.month) ||
      !Number.isFinite(values.day) ||
      !Number.isFinite(values.hour) ||
      !Number.isFinite(values.minute) ||
      !Number.isFinite(values.second)
    ) {
      return null;
    }
    const asUtc = Date.UTC(
      values.year,
      values.month - 1,
      values.day,
      values.hour,
      values.minute,
      values.second
    );
    return Math.round((asUtc - reference.getTime()) / 60000);
  } catch {
    return null;
  }
}

function formatUtcOffset(offsetMinutes: number | null, padHours: boolean): string {
  if (offsetMinutes === null) return "UTC";
  const sign = offsetMinutes >= 0 ? "+" : "-";
  const absoluteMinutes = Math.abs(offsetMinutes);
  const rawHours = Math.floor(absoluteMinutes / 60);
  const hours = padHours ? String(rawHours).padStart(2, "0") : String(rawHours);
  const minutes = absoluteMinutes % 60;
  if (minutes === 0 && !padHours) return `UTC${sign}${hours}`;
  return `UTC${sign}${hours}:${String(minutes).padStart(2, "0")}`;
}

function normalizeTimezoneSearch(value: string): string {
  return value.toLowerCase().replace(/\s+/g, "").trim();
}
