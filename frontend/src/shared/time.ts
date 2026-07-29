const TIMEZONE_SUFFIX = /(Z|[+-]\d{2}:\d{2})$/i;
const DATE_ONLY = /^\d{4}-\d{2}-\d{2}$/;
const EXPLICIT_TIMEZONE_SUFFIX = /(Z|[+-]\d{2}:?\d{2}|\s(?:UTC|GMT)(?:[+-]\d{1,2}(?::?\d{2})?)?)$/i;

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

export function resolveIntlTimezone(timezoneName?: string | null) {
  const candidate = timezoneName?.trim() || "UTC";
  try {
    new Intl.DateTimeFormat("en-US", { timeZone: candidate }).format();
    return candidate;
  } catch {
    const browserTimezone = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
    try {
      new Intl.DateTimeFormat("en-US", { timeZone: browserTimezone }).format();
      return browserTimezone;
    } catch {
      return "UTC";
    }
  }
}

export function elapsedWholeDays(value?: string | null, now = Date.now()) {
  const timestamp = parseTimestamp(value);
  if (timestamp === null) return null;
  return Math.max(0, Math.floor((now - timestamp) / 86_400_000));
}

function formatTimestampInTimezone(timestamp: number, timezoneName: string) {
  const formatter = new Intl.DateTimeFormat("en-US", {
    timeZone: timezoneName,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
    hourCycle: "h23",
    timeZoneName: "short"
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
  return `${year}-${month}-${day} ${hour}:${minute}:${second}${timezone ? ` ${timezone}` : ""}`.trim();
}

function tidyTimestamp(value: string) {
  return value
    .replace("T", " ")
    .replace(/\.\d+/, "")
    .trim();
}

function formatUnit(value: number, unit: string) {
  return `${value} ${unit}${value === 1 ? "" : "s"}`;
}
