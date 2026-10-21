import { assetIconKind, type FormatIconKind } from "../../shared/formatPresentation";
import { formatSeconds } from "../../shared/components/DataTable";

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

export function maintenanceFormatIconKind(format: unknown): FormatIconKind {
  return assetIconKind(String(format ?? ""));
}

export function formatMaintenanceLag(seconds: number | null | undefined) {
  if (seconds == null || !Number.isFinite(seconds) || seconds < 0) return "—";
  if (seconds === 0) return "0s";
  if (seconds < 3600) return formatSeconds(seconds);
  if (seconds < 86400) return `${Math.round((seconds / 3600) * 10) / 10}h`;
  return `${Math.round((seconds / 86400) * 10) / 10}d`;
}
