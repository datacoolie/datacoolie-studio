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

export type PresentNumberOptions = {
  prefix?: string;
  suffix?: string;
};

function normalizedNumber(value: number | null | undefined) {
  if (typeof value !== "number" || !Number.isFinite(value)) return null;
  return Object.is(value, -0) ? 0 : value;
}

function withAffixes(value: string, options: PresentNumberOptions = {}) {
  return `${options.prefix ?? ""}${value}${options.suffix ?? ""}`;
}

export function formatCompactNumber(value: number | null | undefined) {
  const normalized = normalizedNumber(value);
  if (normalized === null) return "-";
  return compactNumberFormatter.format(normalized).replace(/K/gu, "k");
}

export function formatExactNumber(value: number | null | undefined) {
  const normalized = normalizedNumber(value);
  if (normalized === null) return "-";
  return exactNumberFormatter.format(normalized);
}

export function formatAgeNumber(value: number | null | undefined) {
  const normalized = normalizedNumber(value);
  if (normalized === null) return "-";
  return ageNumberFormatter.format(normalized);
}

export function presentNumber(value: number | null | undefined, options: PresentNumberOptions = {}) {
  return {
    display: withAffixes(formatCompactNumber(value), options),
    exact: withAffixes(formatExactNumber(value), options)
  };
}

export function formatExactBytes(value: number | null | undefined) {
  const exact = formatExactNumber(value);
  return exact === "-" ? exact : `${exact} B`;
}
