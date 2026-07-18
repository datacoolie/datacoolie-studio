import type { TableSort } from "./MonitoringCharts";

export function sortVolumeRows<T extends Record<string, unknown>>(rows: T[], sort?: TableSort): T[] {
  if (!sort) return rows;
  const direction = sort.sortDir === "asc" ? 1 : -1;
  return rows
    .map((row, index) => ({ row, index }))
    .sort((left, right) => {
      const leftValue = left.row[sort.sortBy];
      const rightValue = right.row[sort.sortBy];
      if (leftValue == null && rightValue != null) return 1;
      if (leftValue != null && rightValue == null) return -1;
      const comparison = compareVolumeValues(leftValue, rightValue);
      return comparison === 0 ? left.index - right.index : comparison * direction;
    })
    .map(({ row }) => row);
}

export function alignedVolumeAxisBounds(primaryValues: number[], secondaryValues: number[]) {
  const finitePrimary = primaryValues.filter(Number.isFinite);
  const finiteSecondary = secondaryValues.filter(Number.isFinite);
  let primaryMin = Math.min(0, ...finitePrimary);
  let primaryMax = Math.max(0, ...finitePrimary);
  const secondaryMax = Math.max(1, ...finiteSecondary, 0);

  if (primaryMin === 0 && primaryMax === 0) primaryMax = 1;
  if (primaryMin < 0 && primaryMax === 0) primaryMax = Math.abs(primaryMin) / 9;

  const zeroRatio = primaryMin < 0 ? -primaryMin / (primaryMax - primaryMin) : 0;
  const secondaryMin = zeroRatio > 0 && zeroRatio < 1
    ? -(zeroRatio * secondaryMax) / (1 - zeroRatio)
    : 0;

  return { primaryMin, primaryMax, secondaryMin, secondaryMax };
}

function compareVolumeValues(left: unknown, right: unknown) {
  if (left == null && right == null) return 0;
  if (typeof left === "number" && typeof right === "number") return left - right;
  return String(left).localeCompare(String(right), undefined, { numeric: true, sensitivity: "base" });
}
