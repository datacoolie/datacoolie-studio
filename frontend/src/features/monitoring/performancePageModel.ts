import type { TableSort } from "./MonitoringCharts";

export type PerformanceEfficiencyScope = "etl" | "maintenance" | "all";

export function sortPerformanceRows<T extends Record<string, unknown>>(rows: T[], sort?: TableSort): T[] {
  if (!sort) return rows;
  const direction = sort.sortDir === "asc" ? 1 : -1;
  return rows
    .map((row, index) => ({ row, index }))
    .sort((left, right) => {
      const leftValue = left.row[sort.sortBy];
      const rightValue = right.row[sort.sortBy];
      if (leftValue == null && rightValue != null) return 1;
      if (leftValue != null && rightValue == null) return -1;
      const comparison = comparePerformanceValues(leftValue, rightValue);
      return comparison === 0 ? left.index - right.index : comparison * direction;
    })
    .map(({ row }) => row);
}

export function filterPerformanceEfficiencyRows<T extends Record<string, unknown>>(
  rows: T[],
  scope: PerformanceEfficiencyScope
): T[] {
  if (scope === "all") return rows;
  return rows.filter((row) => {
    const operationType = String(row.operation_type ?? "unknown").trim().toLowerCase();
    return scope === "maintenance" ? operationType === "maintenance" : operationType !== "maintenance";
  });
}

export function defaultPerformanceEfficiencyScope(rows: Array<Record<string, unknown>>): PerformanceEfficiencyScope {
  const hasPipelineRuns = rows.some((row) => String(row.operation_type ?? "").toLowerCase() !== "maintenance");
  const hasMaintenanceRuns = rows.some((row) => String(row.operation_type ?? "").toLowerCase() === "maintenance");
  if (hasPipelineRuns && hasMaintenanceRuns) return "etl";
  return hasMaintenanceRuns ? "maintenance" : "all";
}

export function performancePressureIntent(ratio: number, p95: number): "neutral" | "good" | "warning" | "bad" {
  if (!ratio || !p95) return "neutral";
  if (ratio >= 10 && p95 >= 60) return "bad";
  if (ratio >= 5 && p95 >= 30) return "warning";
  if (ratio >= 5) return "neutral";
  return "good";
}

function comparePerformanceValues(left: unknown, right: unknown) {
  if (left == null && right == null) return 0;
  if (typeof left === "number" && typeof right === "number") return left - right;
  return String(left).localeCompare(String(right), undefined, { numeric: true, sensitivity: "base" });
}
