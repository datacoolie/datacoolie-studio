import { compareNumber } from "lossless-json";

const compactNumberFormatter = new Intl.NumberFormat("en-US", {
  notation: "compact",
  maximumFractionDigits: 1
});

const exactNumberFormatter = new Intl.NumberFormat("en-US", {
  useGrouping: true,
  maximumFractionDigits: 20
});

const ageNumberFormatter = new Intl.NumberFormat("en-US", {
  useGrouping: true,
  maximumFractionDigits: 1
});

/** Values transported by Monitoring integer/count/byte fields. */
export type MetricValue = number | string | bigint | null | undefined;
export type CompactNumberStyle = "suffix" | "infix";

export type PresentNumberOptions = {
  prefix?: string;
  suffix?: string;
  compactStyle?: CompactNumberStyle;
};

const INTEGER_TEXT = /^-?\d+$/u;
const NUMBER_TEXT = /^-?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/u;

function normalizedNumber(value: number | null | undefined) {
  if (typeof value !== "number" || !Number.isFinite(value)) return null;
  return Object.is(value, -0) ? 0 : value;
}

function metricText(value: MetricValue): string | null {
  if (typeof value === "bigint") return value.toString();
  if (typeof value === "number") {
    const normalized = normalizedNumber(value);
    return normalized === null ? null : String(normalized);
  }
  if (typeof value !== "string") return null;
  const text = value.trim();
  return text && NUMBER_TEXT.test(text) ? text : null;
}

function withAffixes(value: string, options: PresentNumberOptions = {}) {
  return `${options.prefix ?? ""}${value}${options.suffix ?? ""}`;
}

function formatIntegerText(value: string) {
  const negative = value.startsWith("-");
  const digits = (negative ? value.slice(1) : value).replace(/^0+(?=\d)/u, "");
  const grouped = digits.replace(/\B(?=(\d{3})+(?!\d))/gu, ",");
  return `${negative && digits !== "0" ? "-" : ""}${grouped}`;
}

function formatDecimalText(value: string) {
  if (INTEGER_TEXT.test(value)) return formatIntegerText(value);
  if (!NUMBER_TEXT.test(value)) return "-";
  // Keep authored decimal/exponent text intact. Metric DTOs are integer
  // strings, but this path is also used for exact decimal evidence.
  if (/[eE]/u.test(value)) return value;
  const negative = value.startsWith("-");
  const unsigned = negative ? value.slice(1) : value;
  const [whole, fraction] = unsigned.split(".", 2);
  const grouped = formatIntegerText(whole || "0");
  return `${negative && grouped !== "0" ? "-" : ""}${grouped}${fraction === undefined ? "" : `.${fraction}`}`;
}

function approximateMetricNumber(value: MetricValue): number | null {
  const text = metricText(value);
  if (text === null) return null;
  const parsed = Number(text);
  if (Number.isFinite(parsed)) return Object.is(parsed, -0) ? 0 : parsed;
  // Keep plotting possible for an integer outside Number's finite range. The
  // returned value is deliberately approximate; tooltips use exact formatting.
  if (!INTEGER_TEXT.test(text)) return null;
  const negative = text.startsWith("-");
  const digits = (negative ? text.slice(1) : text).replace(/^0+(?=\d)/u, "");
  const leading = Number(digits.slice(0, 15));
  const approximate = leading * 10 ** Math.max(0, digits.length - 15);
  return (negative ? -1 : 1) * approximate;
}

/** Convert a decimal integer to a plotting number, explicitly approximate. */
export function toApproximateNumber(value: MetricValue): number | null {
  const result = approximateMetricNumber(value);
  return result === null || !Number.isFinite(result) ? null : result;
}

export function formatCompactNumber(value: MetricValue, compactStyle: CompactNumberStyle = "suffix") {
  const normalized = approximateMetricNumber(value);
  if (normalized === null) return "-";
  const display = compactNumberFormatter.format(normalized).replace(/K/gu, "k");
  // Reorder the rounded compact value, preserving Intl unit rollover and signs.
  return compactStyle === "infix" ? display.replace(/\.([0-9]+)([kMBT])$/u, "$2$1") : display;
}

/** Format a metric without converting a decimal-string integer through JS Number. */
export function formatExactNumber(value: MetricValue) {
  const text = metricText(value);
  if (text === null) return "-";
  if (INTEGER_TEXT.test(text)) return formatIntegerText(text);
  if (typeof value === "number") {
    const normalized = normalizedNumber(value);
    return normalized === null ? "-" : exactNumberFormatter.format(normalized);
  }
  return formatDecimalText(text);
}

export function formatAgeNumber(value: number | null | undefined) {
  const normalized = normalizedNumber(value);
  if (normalized === null) return "-";
  return ageNumberFormatter.format(normalized);
}

/** Tooltip decimals use grouping and at most two digits; integer strings stay lossless. */
export function formatTooltipNumber(value: MetricValue, fractionDigits: 1 | 2 = 2): string {
  const text = metricText(value);
  if (text === null) return "-";
  if (INTEGER_TEXT.test(text)) return formatIntegerText(text);
  const negative = text.startsWith("-");
  const [mantissa, exponentText] = (negative ? text.slice(1) : text).split(/[eE]/u);
  const [whole = "0", fraction = ""] = mantissa.split(".");
  const exponent = Number(exponentText ?? 0);
  if (!Number.isSafeInteger(exponent) || Math.abs(exponent) > 1000) return formatExactNumber(value);
  const digits = whole + fraction;
  const point = whole.length + exponent;
  const expanded = point <= 0 ? `0.${"0".repeat(-point)}${digits}`
    : point >= digits.length ? `${digits}${"0".repeat(point - digits.length)}`
    : `${digits.slice(0, point)}.${digits.slice(point)}`;
  const [integer, decimals = ""] = expanded.split(".");
  const kept = decimals.padEnd(fractionDigits, "0").slice(0, fractionDigits);
  const rounded = BigInt(integer + kept) + (Number(decimals[fractionDigits] ?? 0) >= 5 ? 1n : 0n);
  const padded = rounded.toString().padStart(fractionDigits + 1, "0");
  const tail = padded.slice(-fractionDigits).replace(/0+$/u, "");
  return `${negative && rounded !== 0n ? "-" : ""}${formatIntegerText(padded.slice(0, -fractionDigits))}${tail ? `.${tail}` : ""}`;
}

export function presentNumber(value: MetricValue, options: PresentNumberOptions = {}) {
  return {
    display: withAffixes(formatCompactNumber(value, options.compactStyle), options),
    exact: withAffixes(formatExactNumber(value), options)
  };
}

export function formatExactBytes(value: MetricValue) {
  const exact = formatExactNumber(value);
  return exact === "-" ? exact : `${exact} B`;
}

export function formatTooltipBytes(value: MetricValue) {
  const formatted = formatTooltipNumber(value);
  return formatted === "-" ? formatted : `${formatted} B`;
}

export function isMetricInteger(value: MetricValue): value is number | string | bigint {
  if (typeof value === "bigint") return true;
  if (typeof value === "number") return Number.isSafeInteger(value);
  return typeof value === "string" && INTEGER_TEXT.test(value.trim());
}

/** Compare transported metrics without narrowing large integer strings. */
export function compareMetricValues(left: MetricValue, right: MetricValue): number {
  const leftText = metricText(left);
  const rightText = metricText(right);
  if (leftText === null && rightText === null) return 0;
  if (leftText === null) return -1;
  if (rightText === null) return 1;
  if (INTEGER_TEXT.test(leftText) && INTEGER_TEXT.test(rightText)) {
    const a = BigInt(leftText);
    const b = BigInt(rightText);
    return a < b ? -1 : a > b ? 1 : 0;
  }
  try {
    return compareNumber(leftText, rightText);
  } catch {
    return leftText.localeCompare(rightText);
  }
}

export function metricDifference(left: MetricValue, right: MetricValue): string | null {
  if (left == null || right == null || !isMetricInteger(left) || !isMetricInteger(right)) return null;
  const difference = BigInt(String(left)) - BigInt(String(right));
  return difference.toString();
}
