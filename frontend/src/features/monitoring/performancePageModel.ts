import type { TableSort } from "../../shared/components/DataTable";

export type PerformanceEfficiencyScope = "etl" | "maintenance";

export interface PerformanceEfficiencyCoverage {
  eligible: number;
  measured: number;
}

export function performanceEfficiencyCoverage(
  kpis: Record<string, unknown>,
  scope: PerformanceEfficiencyScope,
): PerformanceEfficiencyCoverage | null {
  const pipeline = readEfficiencyCoverage(kpis, "pipeline");
  const maintenance = readEfficiencyCoverage(kpis, "maintenance");
  if (scope === "etl") return pipeline;
  return maintenance;
}

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
  return rows.filter((row) => {
    const operationType = String(row.operation_type ?? "unknown").trim().toLowerCase();
    return scope === "maintenance" ? operationType === "maintenance" : operationType !== "maintenance";
  });
}

export function defaultPerformanceEfficiencyScope(rows: Array<Record<string, unknown>>): PerformanceEfficiencyScope {
  const hasPipelineRuns = rows.some((row) => String(row.operation_type ?? "").toLowerCase() !== "maintenance");
  const hasMaintenanceRuns = rows.some((row) => String(row.operation_type ?? "").toLowerCase() === "maintenance");
  if (!hasPipelineRuns && hasMaintenanceRuns) return "maintenance";
  return "etl";
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

function readEfficiencyCoverage(kpis: Record<string, unknown>, scope: "pipeline" | "maintenance") {
  const eligible = finiteCount(kpis[`workload_efficiency_${scope}_eligible`]);
  const measured = finiteCount(kpis[`workload_efficiency_${scope}_measured`]);
  return eligible == null || measured == null ? null : { eligible, measured };
}

function finiteCount(value: unknown) {
  if (typeof value !== "number" && typeof value !== "string") return null;
  if (typeof value === "string" && !value.trim()) return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : null;
}
