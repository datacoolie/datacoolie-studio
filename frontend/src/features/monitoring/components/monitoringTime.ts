import type { MonitoringFilters } from "../monitoringFilters";
import { resolveIntlTimezone } from "../../../shared/time";

export const DATE_GRAINS = ["hour", "day", "week", "month"] as const;

export function fillMissingTrendDates(
  rows: Array<Record<string, string | number | null>>,
  filters: MonitoringFilters,
  dateRange: { min?: string | null; max?: string | null },
  timezoneName: string,
  reportEffectiveGrain?: string
) {
  const effectiveGrain = String(rows.find((row) => row.grain)?.grain ?? reportEffectiveGrain ?? filters.grain ?? "day");
  const known = rows.filter((row) => rowLabel(row) !== "unknown");
  if (!known.length) {
    const keys = resolveTrendBucketKeys(filters, dateRange, timezoneName, [], effectiveGrain);
    return keys.map((dateKey) => createEmptyTrendRow(dateKey, effectiveGrain));
  }
  const rowByDate = new Map<string, Record<string, string | number | null>>();
  for (const row of known) {
    const dateKey = normalizeTrendBucketKey(rowLabel(row), effectiveGrain, timezoneName);
    if (!dateKey) continue;
    rowByDate.set(dateKey, { ...row, bucket: dateKey, date: dateKey });
  }
  const keys = resolveTrendBucketKeys(filters, dateRange, timezoneName, Array.from(rowByDate.keys()), effectiveGrain);
  if (!keys.length) return known;
  return keys.map((dateKey) => rowByDate.get(dateKey) ?? createEmptyTrendRow(dateKey, effectiveGrain));
}

export function createEmptyTrendRow(dateKey: string, grain: string): Record<string, string | number> {
  return {
    date: dateKey,
    bucket: dateKey,
    grain: normalizeTrendGrain(grain),
    succeeded: 0,
    failed: 0,
    skipped: 0,
    running: 0,
    pending: 0,
    unknown: 0,
    total: 0,
    executable_total: 0
  };
}

export function createEmptyVolumeTrendRow(dateKey: string, grain: string) {
  return {
    date: dateKey,
    bucket: dateKey,
    grain: normalizeTrendGrain(grain),
    rows_read: 0,
    rows_written: 0,
    lakehouse_rows_written: 0,
    dataflow_runs: 0,
    bytes_added: 0,
    bytes_removed: 0,
    bytes_saved: 0,
    files_added: 0,
    files_removed: 0,
    net_bytes: 0
  };
}

export function resolveTrendBucketKeys(
  filters: MonitoringFilters,
  dateRange: { min?: string | null; max?: string | null },
  timezoneName: string,
  knownDateKeys: string[],
  grainValue: string
) {
  const normalizedGrain = normalizeTrendGrain(grainValue);
  const grain = normalizedGrain === "auto" ? resolveClientTrendGrain(normalizedGrain, filters) : normalizedGrain;
  if (grain === "hour") return resolveTrendHourKeys(filters, dateRange, timezoneName, knownDateKeys);
  if (grain === "week") return resolveTrendWeekKeys(filters, dateRange, timezoneName, knownDateKeys);
  if (grain === "month") return resolveTrendMonthKeys(filters, dateRange, timezoneName, knownDateKeys);
  return resolveTrendDateKeys(filters, dateRange, timezoneName, knownDateKeys);
}

export function normalizeTrendBucketKey(value: unknown, grainValue: string, timezoneName: string) {
  const rawValue = String(value ?? "").trim();
  if (!rawValue) return "";
  const grain = normalizeTrendGrain(grainValue);
  if (grain === "hour") {
    if (/^\d{4}-\d{2}-\d{2} \d{2}:00$/u.test(rawValue)) return rawValue;
    const timestamp = Date.parse(rawValue);
    return Number.isFinite(timestamp) ? timezoneHourKey(timestamp, timezoneName) : "";
  }
  if (grain === "week" && /^\d{4}-W\d{2}$/u.test(rawValue)) return rawValue;
  if (grain === "month" && /^\d{4}-\d{2}$/u.test(rawValue)) return rawValue;
  const dateKey = normalizeToDateKey(rawValue, timezoneName);
  if (!dateKey) return "";
  if (grain === "week") return weekKeyFromDateKey(dateKey);
  if (grain === "month") return dateKey.slice(0, 7);
  return dateKey;
}

export function resolveTrendDateKeys(
  filters: MonitoringFilters,
  dateRange: { min?: string | null; max?: string | null },
  timezoneName: string,
  knownDateKeys: string[]
) {
  const range = filters.range;
  const today = timezoneDateKey(Date.now(), timezoneName);
  const minKnown = knownDateKeys.length ? knownDateKeys.slice().sort()[0] : "";
  const maxKnown = knownDateKeys.length ? knownDateKeys.slice().sort()[knownDateKeys.length - 1] : "";
  if (range === "24h" || range === "3d" || range === "7d" || range === "30d" || range === "90d") {
    const days = range === "24h" ? 1 : range === "3d" ? 3 : range === "7d" ? 7 : range === "90d" ? 90 : 30;
    if (!today) return [];
    const start = addDaysToDateKey(today, -(days - 1));
    return enumerateDateKeys(start, today);
  }
  if (range === "custom") {
    let start = normalizeToDateKey(filters.startTime, timezoneName) || normalizeToDateKey(dateRange.min ?? "", timezoneName) || minKnown;
    let end = normalizeToDateKey(filters.endTime, timezoneName) || normalizeToDateKey(dateRange.max ?? "", timezoneName) || maxKnown || today;
    if (!start) start = end;
    if (!end) end = start;
    return enumerateDateKeys(start, end);
  }
  if (range === "all") {
    let start = normalizeToDateKey(dateRange.min ?? "", timezoneName) || minKnown;
    let end = today || normalizeToDateKey(dateRange.max ?? "", timezoneName) || maxKnown;
    if (!start) start = end;
    if (!end) end = start;
    return enumerateDateKeys(start, end);
  }
  return enumerateDateKeys(minKnown, maxKnown);
}

export function resolveTrendHourKeys(
  filters: MonitoringFilters,
  dateRange: { min?: string | null; max?: string | null },
  timezoneName: string,
  knownDateKeys: string[]
) {
  const range = filters.range;
  const now = Date.now();
  const minKnown = knownDateKeys.length ? knownDateKeys.slice().sort()[0] : "";
  const maxKnown = knownDateKeys.length ? knownDateKeys.slice().sort()[knownDateKeys.length - 1] : "";
  if (range === "24h" || range === "3d" || range === "7d" || range === "30d" || range === "90d") {
    const hours = range === "24h" ? 24 : range === "3d" ? 3 * 24 : range === "7d" ? 7 * 24 : range === "90d" ? 90 * 24 : 30 * 24;
    const endHour = floorToHour(now);
    const startHour = endHour - (hours - 1) * 3600 * 1000;
    return enumerateHourKeys(startHour, endHour, timezoneName);
  }
  if (range === "custom") {
    const start = parseFilterTime(filters.startTime) ?? parseFilterTime(dateRange.min ?? "") ?? parseHourKey(minKnown);
    const end = parseFilterTime(filters.endTime) ?? parseFilterTime(dateRange.max ?? "") ?? parseHourKey(maxKnown) ?? start;
    return enumerateHourKeys(start ?? end ?? now, end ?? start ?? now, timezoneName);
  }
  if (range === "all") return knownDateKeys.slice().sort();
  return knownDateKeys.slice().sort();
}

export function resolveTrendWeekKeys(
  filters: MonitoringFilters,
  dateRange: { min?: string | null; max?: string | null },
  timezoneName: string,
  knownDateKeys: string[]
) {
  const dateKeys = resolveTrendDateKeys(filters, dateRange, timezoneName, knownDateKeys);
  if (!dateKeys.length) return knownDateKeys.slice().sort();
  return Array.from(new Set(dateKeys.map((dateKey) => weekKeyFromDateKey(dateKey)).filter(Boolean))).sort();
}

export function resolveTrendMonthKeys(
  filters: MonitoringFilters,
  dateRange: { min?: string | null; max?: string | null },
  timezoneName: string,
  knownDateKeys: string[]
) {
  const dateKeys = resolveTrendDateKeys(filters, dateRange, timezoneName, knownDateKeys);
  if (!dateKeys.length) return knownDateKeys.slice().sort();
  return Array.from(new Set(dateKeys.map((dateKey) => dateKey.slice(0, 7)).filter(Boolean))).sort();
}

export function rowLabel(row: Record<string, unknown>) {
  return String(row.bucket ?? row.date ?? "");
}

export function normalizeToDateKey(value: string, timezoneName: string) {
  const trimmed = String(value ?? "").trim();
  if (!trimmed) return "";
  if (/^\d{4}-\d{2}-\d{2}$/.test(trimmed)) return trimmed;
  const timestamp = Date.parse(trimmed);
  if (!Number.isFinite(timestamp)) return "";
  return timezoneDateKey(timestamp, timezoneName);
}

export function timezoneDateKey(timestamp: number, timezoneName: string): string {
  try {
    const formatter = new Intl.DateTimeFormat("en-CA", {
      timeZone: resolveIntlTimezone(timezoneName),
      year: "numeric",
      month: "2-digit",
      day: "2-digit"
    });
    const parts = formatter.formatToParts(new Date(timestamp));
    const values = Object.fromEntries(parts.filter((part) => part.type !== "literal").map((part) => [part.type, part.value])) as Record<string, string>;
    if (!values.year || !values.month || !values.day) return "";
    return `${values.year}-${values.month}-${values.day}`;
  } catch {
    return "";
  }
}

export function timezoneHourKey(timestamp: number, timezoneName: string): string {
  try {
    const formatter = new Intl.DateTimeFormat("en-CA", {
      timeZone: resolveIntlTimezone(timezoneName),
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      hourCycle: "h23"
    });
    const parts = formatter.formatToParts(new Date(timestamp));
    const values = Object.fromEntries(parts.filter((part) => part.type !== "literal").map((part) => [part.type, part.value])) as Record<string, string>;
    if (!values.year || !values.month || !values.day || !values.hour) return "";
    return `${values.year}-${values.month}-${values.day} ${values.hour}:00`;
  } catch {
    return "";
  }
}

export function floorToHour(timestamp: number) {
  const date = new Date(timestamp);
  date.setUTCMinutes(0, 0, 0);
  return date.getTime();
}

export function enumerateHourKeys(startTimestamp: number, endTimestamp: number, timezoneName: string) {
  if (!Number.isFinite(startTimestamp) || !Number.isFinite(endTimestamp)) return [];
  const from = Math.min(floorToHour(startTimestamp), floorToHour(endTimestamp));
  const to = Math.max(floorToHour(startTimestamp), floorToHour(endTimestamp));
  const keys: string[] = [];
  for (let cursor = from; cursor <= to; cursor += 3600 * 1000) {
    const key = timezoneHourKey(cursor, timezoneName);
    if (key && keys[keys.length - 1] !== key) keys.push(key);
  }
  return keys;
}

export function parseFilterTime(value: string | null | undefined) {
  const trimmed = String(value ?? "").trim();
  if (!trimmed) return null;
  const timestamp = Date.parse(trimmed);
  return Number.isFinite(timestamp) ? timestamp : null;
}

export function parseHourKey(value: string) {
  const match = /^(\d{4})-(\d{2})-(\d{2}) (\d{2}):00$/.exec(value);
  if (!match) return null;
  const [, year, month, day, hour] = match;
  return Date.UTC(Number(year), Number(month) - 1, Number(day), Number(hour));
}

export function weekKeyFromDateKey(dateKey: string) {
  const date = dateKeyToUtcDate(dateKey);
  if (!date) return "";
  const day = date.getUTCDay() || 7;
  date.setUTCDate(date.getUTCDate() + 4 - day);
  const isoYear = date.getUTCFullYear();
  const yearStart = new Date(Date.UTC(isoYear, 0, 1));
  const week = Math.ceil(((date.getTime() - yearStart.getTime()) / 86400000 + 1) / 7);
  return `${isoYear}-W${String(week).padStart(2, "0")}`;
}

export function resolveClientTrendGrain(grainValue: string, filters: MonitoringFilters) {
  const requestedGrain = normalizeTrendGrain(grainValue);
  const minimumGrain = minimumClientTrendGrain(filters);
  if (requestedGrain === "auto") return minimumGrain;
  const requestedIndex = DATE_GRAINS.indexOf(requestedGrain);
  const minimumIndex = DATE_GRAINS.indexOf(minimumGrain);
  if (requestedIndex < 0) return minimumGrain;
  return DATE_GRAINS[Math.max(requestedIndex, minimumIndex)];
}

export function normalizeTrendGrain(grain: string) {
  return grain === "hour" || grain === "day" || grain === "week" || grain === "month" || grain === "auto" ? grain : "day";
}

export function minimumClientTrendGrain(filters: MonitoringFilters) {
  if (filters.range === "24h" || filters.range === "3d") return "hour";
  if (filters.range === "7d" || filters.range === "30d" || filters.range === "90d") return "day";
  if (filters.range === "custom") {
    const start = parseFilterTime(filters.startTime);
    const end = parseFilterTime(filters.endTime);
    if (start !== null && end !== null) return minimumClientTrendGrainForSpan(Math.abs(end - start) / 1000);
  }
  if (filters.range === "all") return "month";
  return "day";
}

export function minimumClientTrendGrainForSpan(spanSeconds: number) {
  if (spanSeconds <= 3 * 24 * 3600) return "hour";
  if (spanSeconds <= 90 * 86400) return "day";
  if (spanSeconds <= 365 * 86400) return "week";
  return "month";
}

export function dateKeyToUtcDate(dateKey: string) {
  const [year, month, day] = dateKey.split("-").map((value) => Number(value));
  if (!Number.isFinite(year) || !Number.isFinite(month) || !Number.isFinite(day)) return null;
  return new Date(Date.UTC(year, month - 1, day));
}

export function utcDateToDateKey(date: Date) {
  const year = date.getUTCFullYear();
  const month = String(date.getUTCMonth() + 1).padStart(2, "0");
  const day = String(date.getUTCDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

export function addDaysToDateKey(dateKey: string, offsetDays: number) {
  const date = dateKeyToUtcDate(dateKey);
  if (!date) return "";
  date.setUTCDate(date.getUTCDate() + offsetDays);
  return utcDateToDateKey(date);
}

export function enumerateDateKeys(startDateKey: string, endDateKey: string) {
  const start = dateKeyToUtcDate(startDateKey);
  const end = dateKeyToUtcDate(endDateKey);
  if (!start || !end) return [];
  const from = start.getTime() <= end.getTime() ? start : end;
  const to = start.getTime() <= end.getTime() ? end : start;
  const keys: string[] = [];
  for (const cursor = new Date(from.getTime()); cursor.getTime() <= to.getTime(); cursor.setUTCDate(cursor.getUTCDate() + 1)) {
    keys.push(utcDateToDateKey(cursor));
  }
  return keys;
}

export function phaseLabel(phase: string) {
  if (phase === "source") return "Source";
  if (phase === "transform") return "Transform";
  if (phase === "destination") return "Destination";
  if (phase === "overhead") return "Overhead";
  return phase;
}
