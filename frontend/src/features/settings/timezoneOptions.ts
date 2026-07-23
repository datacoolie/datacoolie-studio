export interface TimezoneOption {
  name: string;
  offsetLabel: string;
  offsetMinutes: number | null;
  searchText: string;
}

export const MAX_VISIBLE_TIMEZONE_OPTIONS = 50;

const FALLBACK_TIMEZONE_NAMES = [
  "UTC", "Etc/UTC", "Pacific/Honolulu", "America/Anchorage", "America/Los_Angeles",
  "America/Phoenix", "America/Denver", "America/Chicago", "America/New_York", "America/Toronto",
  "America/Mexico_City", "America/Bogota", "America/Lima", "America/Santiago", "America/Sao_Paulo",
  "America/Argentina/Buenos_Aires", "Europe/Dublin", "Europe/London", "Europe/Paris", "Europe/Berlin",
  "Europe/Madrid", "Europe/Rome", "Europe/Warsaw", "Europe/Athens", "Europe/Bucharest",
  "Europe/Istanbul", "Europe/Moscow", "Africa/Cairo", "Africa/Johannesburg", "Asia/Jerusalem",
  "Asia/Dubai", "Asia/Karachi", "Asia/Kolkata", "Asia/Kathmandu", "Asia/Dhaka", "Asia/Bangkok",
  "Asia/Ho_Chi_Minh", "Asia/Jakarta", "Asia/Shanghai", "Asia/Hong_Kong", "Asia/Taipei",
  "Asia/Singapore", "Asia/Kuala_Lumpur", "Asia/Manila", "Asia/Seoul", "Asia/Tokyo",
  "Australia/Perth", "Australia/Adelaide", "Australia/Sydney", "Pacific/Auckland",
] as const;

export function buildTimezoneOptions(): TimezoneOption[] {
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
          offsetMinutes,
          searchText: normalizeTimezoneSearch(`${name} ${offsetLabel} ${shortOffsetLabel}`),
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

export function matchTimezoneOptions(
  options: TimezoneOption[],
  input: string,
  selectedName?: string,
  selectedOffsetMinutes?: number,
) {
  const query = normalizeTimezoneSearch(input);
  const matches = query ? options.filter((option) => option.searchText.includes(query)) : options;
  let startIndex = 0;
  let focusedIndex = matches.findIndex((option) => option.name === selectedName);
  if (!query && selectedName && matches.length > MAX_VISIBLE_TIMEZONE_OPTIONS) {
    if (focusedIndex < 0 && Number.isFinite(selectedOffsetMinutes)) {
      const sameOffsetIndexes = matches.flatMap((option, index) => (
        option.offsetMinutes === selectedOffsetMinutes ? [index] : []
      ));
      focusedIndex = sameOffsetIndexes[Math.floor(sameOffsetIndexes.length / 2)] ?? -1;
    }
    if (focusedIndex >= 0) {
      const centeredStart = focusedIndex - Math.floor(MAX_VISIBLE_TIMEZONE_OPTIONS / 2);
      startIndex = Math.max(0, centeredStart);
    }
  }
  return {
    total: matches.length,
    visible: matches.slice(startIndex, startIndex + MAX_VISIBLE_TIMEZONE_OPTIONS),
    focusedIndex: focusedIndex >= 0 ? focusedIndex - startIndex : 0,
  };
}

function resolveTimezoneNames(intlApi: typeof Intl & { supportedValuesOf?: (key: string) => string[] }): string[] {
  const names = new Set<string>(FALLBACK_TIMEZONE_NAMES);
  const localTimezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  if (localTimezone) names.add(localTimezone);
  if (typeof intlApi.supportedValuesOf === "function") {
    try {
      for (const timezoneName of intlApi.supportedValuesOf("timeZone")) names.add(timezoneName);
    } catch {
      // Keep the portable fallback list when the runtime rejects the request.
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
      hour12: false,
    }).formatToParts(reference);
    const values = Object.fromEntries(
      parts.filter((part) => part.type !== "literal").map((part) => [part.type, Number(part.value)]),
    ) as Record<string, number>;
    if ([values.year, values.month, values.day, values.hour, values.minute, values.second]
      .some((value) => !Number.isFinite(value))) return null;
    const asUtc = Date.UTC(
      values.year, values.month - 1, values.day, values.hour, values.minute, values.second,
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
