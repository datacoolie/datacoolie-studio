import { formatExactNumber } from "../../shared/lib/numberFormat";

export function isMeaningfulConfigValue(value: unknown) {
  return value !== null && value !== undefined && value !== "";
}

export function formatConfigValue(value: unknown, key: string) {
  if (typeof value === "boolean") return value ? "true" : "false";
  if (key === "retention_hours" && typeof value === "number") return `${formatExactNumber(value)}h`;
  return String(value);
}
