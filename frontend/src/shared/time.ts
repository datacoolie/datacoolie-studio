const TIMEZONE_SUFFIX = /(Z|[+-]\d{2}:\d{2})$/i;
const DATE_ONLY = /^\d{4}-\d{2}-\d{2}$/;
const EXPLICIT_TIMEZONE_SUFFIX = /(Z|[+-]\d{2}:?\d{2}|\s(?:UTC|GMT)(?:[+-]\d{1,2}(?::?\d{2})?)?)$/i;
const TIMEZONE_CACHE_LIMIT = 32;
const FORMATTER_CACHE_LIMIT = 32;
const timezoneCache = new Map<string, string>();
const dateTimeFormatterCache = new Map<string, Intl.DateTimeFormat>();

export function parseTimestamp(value?: string | null) {
  if (!value) return null;

  let normalized = value.trim();
  if (DATE_ONLY.test(normalized)) {
    normalized = `${normalized}T00:00:00Z`;
  } else if (!TIMEZONE_SUFFIX.test(normalized)) {
    normalized = `${normalized}Z`;
  }

  const timestamp = Date.parse(normalized);
  return Number.isNaN(timestamp) ? null : timestamp;
}

export function formatRelativeTime(value?: string | null, now = Date.now()) {
  const timestamp = parseTimestamp(value);
  if (timestamp === null) return null;

  const elapsedSeconds = Math.max(0, Math.floor((now - timestamp) / 1_000));
  if (elapsedSeconds < 60) return "just now";

  const elapsedMinutes = Math.floor(elapsedSeconds / 60);
  if (elapsedMinutes < 60) return `${formatUnit(elapsedMinutes, "minute")} ago`;

  const elapsedHours = Math.floor(elapsedMinutes / 60);
  if (elapsedHours < 24) return `${formatUnit(elapsedHours, "hour")} ago`;

  const elapsedDays = Math.floor(elapsedHours / 24);
  if (elapsedDays < 7) {
    const remainingHours = elapsedHours % 24;
    const parts = [formatUnit(elapsedDays, "day")];
    if (remainingHours > 0) parts.push(formatUnit(remainingHours, "hour"));
    return `${parts.join(" ")} ago`;
  }

  return `${formatUnit(elapsedDays, "day")} ago`;
}

export function formatAbsoluteTime(value?: string | null, timezoneName?: string | null) {
  const timestamp = parseTimestamp(value);
  if (timestamp === null) return null;

  return formatTimestampInTimezone(timestamp, resolveIntlTimezone(timezoneName));
}

export function isTimestampFieldName(key: string) {
  return /(^|_)(time|timestamp|at)$/iu.test(key) || /_(time|timestamp|at)$/iu.test(key);
}

export function hasExplicitTimezone(value: string) {
  return EXPLICIT_TIMEZONE_SUFFIX.test(value.trim());
}

export function formatTimestampForDisplay(value: unknown, timezoneName?: string | null, fallback = "-") {
  if (value === null || value === undefined || value === "") return fallback;
  const rawValue = String(value).trim();
  if (!rawValue) return fallback;
  if (DATE_ONLY.test(rawValue)) return rawValue;
  if (!hasExplicitTimezone(rawValue)) return tidyTimestamp(rawValue);

  const timestamp = Date.parse(rawValue);
  if (!Number.isFinite(timestamp)) return tidyTimestamp(rawValue);
  return formatTimestampInTimezone(timestamp, resolveIntlTimezone(timezoneName));
}

/**
 * Format event timestamps for the System Log viewer without changing the
 * precision contract used by ordinary Monitoring tables. Valid date-times
 * always show milliseconds; date-only, naive legacy, and invalid values keep
 * the established display behavior and never receive an invented instant.
 */
export function formatTimestampForLogDisplay(value: unknown, timezoneName?: string | null, fallback = "-") {
  if (value === null || value === undefined || value === "") return fallback;
  const rawValue = String(value).trim();
  if (!rawValue) return fallback;
  if (DATE_ONLY.test(rawValue)) return rawValue;

  if (!hasExplicitTimezone(rawValue)) {
    const timestamp = parseTimestamp(rawValue);
    if (timestamp === null || !/T|\s\d{2}:\d{2}/.test(rawValue)) {
      return tidyTimestamp(rawValue);
    }
    return formatNaiveTimestampWithMilliseconds(rawValue);
  }

  const timestamp = Date.parse(rawValue);
  if (!Number.isFinite(timestamp)) return tidyTimestamp(rawValue);
  return formatTimestampInTimezone(timestamp, resolveIntlTimezone(timezoneName), true);
}

export function resolveIntlTimezone(timezoneName?: string | null) {
  const candidate = timezoneName?.trim() || "UTC";
  const cached = timezoneCache.get(candidate);
  if (cached) return cached;

  let resolvedTimezone: string;
  try {
    new Intl.DateTimeFormat("en-US", { timeZone: candidate }).format();
    resolvedTimezone = candidate;
  } catch {
    const browserTimezone = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
    try {
      new Intl.DateTimeFormat("en-US", { timeZone: browserTimezone }).format();
      resolvedTimezone = browserTimezone;
    } catch {
      resolvedTimezone = "UTC";
    }
  }

  return setBoundedCacheValue(timezoneCache, candidate, resolvedTimezone, TIMEZONE_CACHE_LIMIT);
}

export function elapsedWholeDays(value?: string | null, now = Date.now()) {
  const timestamp = parseTimestamp(value);
  if (timestamp === null) return null;
  return Math.max(0, Math.floor((now - timestamp) / 86_400_000));
}

/**
 * Preserve the compact locale timestamp used in status-history tooltips while
 * reusing the formatter for each resolved timezone.
 */
export function formatTimestampForStatusHistory(value: string, timezoneName: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const timezone = resolveIntlTimezone(timezoneName);
  return getCachedDateTimeFormatter(
    undefined,
    timezone,
    "status-history",
    { dateStyle: "short", timeStyle: "medium", timeZone: timezone },
  ).format(date);
}

function formatTimestampInTimezone(timestamp: number, timezoneName: string, includeMilliseconds = false) {
  const formatter = getCachedDateTimeFormatter("en-US", timezoneName, "display", {
    timeZone: timezoneName,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
    hourCycle: "h23",
    timeZoneName: "short",
  });
  const parts = formatter.formatToParts(new Date(timestamp));
  const part = (type: Intl.DateTimeFormatPartTypes) => parts.find((item) => item.type === type)?.value ?? "";
  const year = part("year");
  const month = part("month");
  const day = part("day");
  const hour = part("hour");
  const minute = part("minute");
  const second = part("second");
  const timezone = part("timeZoneName");
  const milliseconds = includeMilliseconds ? `.${String(new Date(timestamp).getUTCMilliseconds()).padStart(3, "0")}` : "";
  return `${year}-${month}-${day} ${hour}:${minute}:${second}${milliseconds}${timezone ? ` ${timezone}` : ""}`.trim();
}

function formatNaiveTimestampWithMilliseconds(value: string) {
  const tidy = value.replace("T", " ").trim();
  const fraction = tidy.match(/\.(\d+)$/)?.[1] ?? "";
  const base = tidy.replace(/\.\d+$/, "");
  return `${base}.${fraction.padEnd(3, "0").slice(0, 3)}`;
}

function tidyTimestamp(value: string) {
  return value
    .replace("T", " ")
    .replace(/\.\d+/, "")
    .trim();
}

function getCachedDateTimeFormatter(
  locale: string | undefined,
  timezoneName: string,
  formatKey: string,
  options: Intl.DateTimeFormatOptions,
) {
  const key = `${locale ?? "default"}|${timezoneName}|${formatKey}`;
  const cached = dateTimeFormatterCache.get(key);
  if (cached) return cached;
  return setBoundedCacheValue(
    dateTimeFormatterCache,
    key,
    new Intl.DateTimeFormat(locale, options),
    FORMATTER_CACHE_LIMIT,
  );
}

function setBoundedCacheValue<T>(cache: Map<string, T>, key: string, value: T, limit: number) {
  if (!cache.has(key) && cache.size >= limit) {
    const oldestKey = cache.keys().next().value;
    if (oldestKey !== undefined) cache.delete(oldestKey);
  }
  cache.set(key, value);
  return value;
}

function formatUnit(value: number, unit: string) {
  return `${value} ${unit}${value === 1 ? "" : "s"}`;
}
