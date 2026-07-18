import type { AssetIconKind } from "../lineage/model/presentation";
import { formatSeconds } from "./MonitoringCharts";

export type MaintenanceTableHealthTone = "healthy" | "warning" | "issues" | "neutral";

export function maintenanceTableHealthLabel(value: unknown) {
  const normalized = String(value ?? "").trim().toLowerCase();
  if (normalized === "has_issues") return "Has issues";
  if (normalized === "no_evidence") return "No evidence";
  if (normalized === "warning") return "Warning";
  if (normalized === "healthy") return "Healthy";
  if (normalized === "missing") return "Missing";
  return normalized || "Unknown";
}

export function maintenanceTableHealthTone(value: unknown): MaintenanceTableHealthTone {
  const normalized = String(value ?? "").trim().toLowerCase();
  if (normalized === "has_issues") return "issues";
  if (normalized === "warning" || normalized === "missing") return "warning";
  if (normalized === "healthy") return "healthy";
  return "neutral";
}

export function maintenanceTableHealthClass(value: unknown) {
  return `health-${maintenanceTableHealthTone(value)}`;
}

export function maintenanceFormatIconKind(format: unknown): AssetIconKind {
  const normalized = String(format ?? "").trim().toLowerCase();
  if (normalized === "delta" || normalized === "deltalake" || normalized === "delta_lake") return "delta";
  if (normalized === "iceberg" || normalized === "apache_iceberg") return "iceberg";
  if (normalized === "parquet") return "parquet";
  if (normalized === "csv") return "csv";
  if (normalized === "json") return "json";
  if (normalized === "excel" || normalized === "xlsx" || normalized === "xls") return "excel";
  if (normalized === "sql" || normalized.includes("query")) return "sql";
  if (normalized === "python" || normalized.includes("function")) return "python";
  if (normalized === "api" || normalized.includes("rest")) return "api";
  if (normalized === "database" || normalized === "lakehouse") return "database";
  if (normalized === "file") return "file";
  return "table";
}

export function formatMaintenanceLag(seconds: number) {
  if (!Number.isFinite(seconds) || seconds <= 0) return "-";
  if (seconds < 3600) return formatSeconds(seconds);
  if (seconds < 86400) return `${Math.round((seconds / 3600) * 10) / 10}h`;
  return `${Math.round((seconds / 86400) * 10) / 10}d`;
}
