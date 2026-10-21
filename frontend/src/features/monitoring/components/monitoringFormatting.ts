import { formatCompactNumber, toApproximateNumber, type MetricValue } from "../../../shared/lib/numberFormat";
import type { HealthIntent } from "./monitoringModel";

export function formatCompact(value: MetricValue) {
  return formatCompactNumber(value, "infix");
}

export function formatBytesShort(value: MetricValue) {
  const approximate = toApproximateNumber(value);
  if (approximate === null) return "-";
  if (approximate === 0) return "0";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let size = Math.abs(approximate);
  let unitIndex = 0;
  while (size >= 1024 && unitIndex < units.length - 1) {
    size /= 1024;
    unitIndex += 1;
  }
  const sign = approximate < 0 ? "-" : "";
  const digits = size >= 100 || unitIndex === 0 ? 0 : size >= 10 ? 1 : 2;
  return `${sign}${size.toFixed(digits)}${units[unitIndex]}`;
}

export function formatPercent(value: MetricValue) {
  const approximate = toApproximateNumber(value);
  return approximate === null ? "-" : `${Math.round(approximate * 100) / 100}%`;
}

export function formatPhasePercent(value: number) {
  return `${Math.round(value * 10) / 10}%`;
}

export function shortIdentifier(value: unknown) {
  const text = value === null || value === undefined || value === "" ? "unknown" : String(value);
  if (text.length <= 18) return text;
  return `${text.slice(0, 8)}...${text.slice(-6)}`;
}

export function formatSecondsSingleDecimal(value: number) {
  if (!Number.isFinite(value)) return "-";
  const formatter = new Intl.NumberFormat("en-US", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  if (value < 60) return `${formatter.format(value)}s`;
  return `${formatter.format(value / 60)}m`;
}

export function successRateIntent(successRate: number, failureRate: number, executableRuns: number): HealthIntent {
  if (!Number.isFinite(executableRuns) || executableRuns <= 0) return "neutral";
  if (failureRate >= 5 || successRate < 95) return "bad";
  if (failureRate >= 1 || successRate < 99) return "warning";
  return "good";
}

export function durationIntent(stats: Record<string, MetricValue>, fallbackAvg: number, fallbackP95: number): HealthIntent {
  const avg = toApproximateNumber(stats.avg_duration_seconds) ?? fallbackAvg ?? 0;
  const p95 = toApproximateNumber(stats.p95_duration_seconds) ?? fallbackP95 ?? 0;
  if (!Number.isFinite(avg) || !Number.isFinite(p95) || p95 <= 0) return "neutral";
  if (p95 >= Math.max(1200, avg * 3)) return "bad";
  if (p95 >= Math.max(600, avg * 2)) return "warning";
  return "good";
}

export function cleanDisplayValue(value: unknown) {
  if (value === null || value === undefined) return null;
  const textValue = String(value).trim();
  return textValue ? textValue : null;
}

export function compactQualifiedTable(value: string | null) {
  if (!value) return null;
  const parts = value.replace(/`/g, "").split(".").filter(Boolean);
  return parts.at(-1) || value;
}

export function pythonFunctionTail(value: string | null) {
  if (!value) return null;
  return value.split(".").filter(Boolean).at(-1) || value;
}

export function pathBasename(value: string | null) {
  if (!value) return null;
  const parts = value.split(/[\\/]+/).filter((part) => part && part !== ".");
  return parts.at(-1) || value;
}

export function humanize(value: string) {
  return value.replace(/_/g, " ").replace(/\b\w/g, (char) => char.toUpperCase());
}

export function optionalNum(row: Record<string, unknown>, field: string) {
  const value = row[field];
  if (value === null || value === undefined || value === "") return null;
  if (Array.isArray(value) && !value.length) return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}
