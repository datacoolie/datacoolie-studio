import { compareMetricValues, formatExactNumber, formatTooltipNumber, toApproximateNumber, type MetricValue } from "../../../shared/lib/numberFormat";
import { formatCompact } from "./monitoringFormatting";

/** Keep observed totals visible and identify incomplete measurement coverage. */
export function metricObservation(total: MetricValue, observed: MetricValue, measured: MetricValue, eligible: MetricValue) {
  const hasTotal = total != null;
  const partial = !hasTotal || (measured != null && eligible != null && compareMetricValues(measured, eligible) < 0);
  const hasObservation = (toApproximateNumber(measured) ?? 0) > 0;
  return {
    value: hasTotal ? total : (hasObservation ? observed : null),
    partial,
    coverage: measured == null || eligible == null ? null : `${formatExactNumber(measured)} / ${formatExactNumber(eligible)} runs measured`,
    compactCoverage: measured == null || eligible == null ? null : `${formatCompact(measured)} / ${formatCompact(eligible)} runs measured`,
  };
}

export function rowObservation(row: Record<string, unknown>, field: string, totalField = field) {
  const metric = (key: string): MetricValue => {
    const value = row[key];
    return typeof value === "number" || typeof value === "string" ? value : null;
  };
  return metricObservation(metric(totalField), metric(`observed_${field}`), metric(`${field}_measured`), metric(`${field}_eligible`));
}

export function observationTooltip(label: string, row: Record<string, unknown>, field: string) {
  const observation = rowObservation(row, field);
  return `${label}${observation.partial && observation.value != null ? " (observed subtotal)" : ""}: ${formatTooltipNumber(observation.value)}${observation.coverage ? `; ${observation.coverage}` : ""}`;
}

/** Missing coordinates cannot represent a measured zero on an efficiency map. */
export function scatterObservations<T>(rows: T[], xValue: (row: T) => MetricValue, yValue: (row: T) => MetricValue) {
  const points = rows.flatMap((row) => {
    const x = toApproximateNumber(xValue(row));
    const y = toApproximateNumber(yValue(row));
    return x === null || y === null ? [] : [{ row, x, y }];
  });
  const missing = rows.length - points.length;
  return { points,
    coverage: `${points.length} / ${rows.length} points measured${missing ? `; ${missing} missing measurements` : ""}`,
    compactCoverage: `${formatCompact(points.length)} / ${formatCompact(rows.length)} point${missing ? ` · ${formatCompact(missing)} missing` : ""}`,
  };
}
