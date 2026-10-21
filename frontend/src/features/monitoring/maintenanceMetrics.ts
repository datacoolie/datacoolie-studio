import { formatSeconds } from "../../shared/components/DataTable";
import { formatBytes, formatCompact, formatTooltipNumber, metricValue, toApproximateNumber, type MetricValue } from "./components/monitoringPresentation";

export function maintenanceCoverage(row: Record<string, unknown>, measuredKey: string, eligibleKey = "run_count") {
  const measured = metricValue(row, measuredKey);
  const eligible = metricValue(row, eligibleKey);
  return `${formatTooltipNumber(measured)} / ${formatTooltipNumber(eligible)} runs`;
}

export function maintenancePartial(row: Record<string, unknown>, measuredKey: string, eligibleKey = "run_count") {
  const measured = toApproximateNumber(metricValue(row, measuredKey));
  const eligible = toApproximateNumber(metricValue(row, eligibleKey));
  return measured !== null && eligible !== null && measured < eligible;
}

export function maintenanceMetricText(row: Record<string, unknown>, field: string, bytes = false) {
  const value = maintenanceValue(row, field);
  const suffix = value != null && maintenancePartial(row, `${field}_measured_runs`) ? "*" : "";
  return `${bytes ? formatBytes(value) : formatCompact(value)}${suffix}`;
}

export function maintenanceValue(row: Record<string, unknown>, field: string) {
  const total = metricValue(row, field);
  return total != null ? total : (toApproximateNumber(metricValue(row, `${field}_measured_runs`)) ?? 0) > 0
    ? metricValue(row, `${field}_observed`) : null;
}

export function maintenanceSeconds(value: MetricValue) {
  const number = toApproximateNumber(value);
  if (number === null) return "—";
  if (number === 0) return "0s";
  return number > 0 && number < 1 ? `${Number((number * 1000).toPrecision(3))}ms` : formatSeconds(number);
}

export function maintenanceNoChangesText(row: Record<string, unknown>) {
  return (toApproximateNumber(metricValue(row, "comparable_runs")) ?? 0) > 0
    ? formatCompact(metricValue(row, "no_op_runs")) : "—";
}
