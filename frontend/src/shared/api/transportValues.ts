export type JsonRecord = Record<string, unknown>;

/** Decimal strings are the wire representation for Monitoring counts/bytes. */
export type DecimalString = string;
export type SessionScope = "all" | "null" | "exact";

export function asRecord(value: unknown): JsonRecord {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as JsonRecord
    : {};
}

export function recordArray(value: unknown): JsonRecord[] {
  return Array.isArray(value)
    ? value.filter((item): item is JsonRecord => (
      item !== null && typeof item === "object" && !Array.isArray(item)
    ))
    : [];
}

export function recordMap(value: unknown): Record<string, JsonRecord> {
  return Object.fromEntries(
    Object.entries(asRecord(value)).map(([key, item]) => [key, asRecord(item)]),
  );
}

export function stringValue(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

export function optionalString(value: unknown): string | undefined {
  return typeof value === "string" ? value : undefined;
}

export function nullableString(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

export function numberValue(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

export function decimalString(value: unknown, fallback = "0"): DecimalString {
  if (typeof value === "string" && /^-?\d+$/u.test(value.trim())) return value.trim();
  if (typeof value === "bigint") return value.toString();
  if (typeof value === "number" && Number.isSafeInteger(value)) return String(value);
  return fallback;
}

export function optionalDecimalString(value: unknown): DecimalString | undefined {
  if (value === undefined) return undefined;
  if (typeof value === "string" && /^-?\d+$/u.test(value.trim())) return value.trim();
  if (typeof value === "bigint") return value.toString();
  if (typeof value === "number" && Number.isSafeInteger(value)) return String(value);
  return undefined;
}

export function nullableDecimalString(value: unknown): DecimalString | null {
  if (value === null) return null;
  return optionalDecimalString(value) ?? null;
}

export function optionalNumber(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

export function nullableNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

export function optionalBoolean(value: unknown): boolean | undefined {
  return typeof value === "boolean" ? value : undefined;
}

export function booleanValue(value: unknown, fallback = false): boolean {
  return typeof value === "boolean" ? value : fallback;
}

export function nullableBoolean(value: unknown): boolean | null {
  return typeof value === "boolean" ? value : null;
}

export function stringArray(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];
}

export function numberArray(value: unknown): number[] {
  return Array.isArray(value)
    ? value.filter((item): item is number => (
      typeof item === "number" && Number.isFinite(item)
    ))
    : [];
}

export function optionalRecord(value: unknown): JsonRecord | undefined {
  if (value === undefined) return undefined;
  return asRecord(value);
}

export function nullableRecord(value: unknown): JsonRecord | null | undefined {
  if (value === undefined) return undefined;
  if (value === null) return null;
  return asRecord(value);
}
