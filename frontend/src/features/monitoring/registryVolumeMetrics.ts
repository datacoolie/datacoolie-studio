import { compareMetricValues, formatTooltipBytes, formatTooltipNumber, type MetricValue } from "../../shared/lib/numberFormat";
import { metricObservation } from "./components/monitoringObservation";

function metric(row: Record<string, unknown>, key: string): MetricValue {
  const value = row[key];
  return typeof value === "string" || typeof value === "number" ? value : null;
}

/** Registry observations use a suffix; averages retain their server completeness gate. */
export function registryVolumeObservation(row: Record<string, unknown>, key: string, average = false) {
  const measured = metric(row, `${key}_measured`);
  const eligible = metric(row, `${key}_eligible`);
  const total = metric(row, average ? `${key}_avg_per_run` : key);
  const observation = metricObservation(total, average ? null : metric(row, `${key}_observed`), measured, eligible);
  return { ...observation, value: average ? total : observation.value, measured, eligible };
}

export function registryVolumeTooltip(row: Record<string, unknown>, key: string, label: string, bytes = false, average = false) {
  const observation = registryVolumeObservation(row, key, average);
  const formatted = bytes ? formatTooltipBytes(observation.value) : formatTooltipNumber(observation.value);
  return `${average ? "Avg " : ""}${label}: ${formatted}${observation.partial && observation.value != null ? " (observed)" : ""}; ${observation.coverage ?? "Coverage unavailable"}${average ? ". Per run recording this metric, including zero; missing is excluded." : ". Total in current filters."}`;
}

export function positiveVolumeMetric(value: MetricValue) {
  return value != null && compareMetricValues(value, 0) > 0;
}

export function registryVolumeSignals(row: Record<string, unknown>) {
  return Array.isArray(row.volume_candidate_signals)
    ? row.volume_candidate_signals.filter((value): value is Record<string, unknown> => value !== null && typeof value === "object" && !Array.isArray(value))
    : [];
}

export function registryVolumeSignalTooltip(row: Record<string, unknown>) {
  const signals = registryVolumeSignals(row);
  const runReasons = Array.isArray(row.volume_candidate_run_reasons) ? row.volume_candidate_run_reasons.join(" · ") : "-";
  return [
    "Dataflow signals compare this dataflow's filtered totals with P95 across filtered dataflows. Rows/files use positive totals; bytes use nonzero absolute net magnitude, including negative net totals. Large volume is context for investigation.",
    ...signals.map((signal) => `${String(signal.label ?? "Signal")}: ${formatTooltipNumber(metric(signal, "value"))} / P95 ${formatTooltipNumber(metric(signal, "threshold"))}; ${formatTooltipNumber(metric(signal, "ratio"))}×`),
    "Byte signals retain their diagnostic net basis: independent observed added minus removed subtotals, which may cover different runs. They compare absolute magnitude. This differs from the paired Net bytes shown in Bytes.",
    `Runs with volume signals: ${formatTooltipNumber(metric(row, "volume_candidate_run_count"))}; ${runReasons}. Run matches use separate run-level P95 thresholds over the filtered runs; they are not counted from the dataflow aggregate matches.`,
    "No detected signal does not establish complete metric coverage.",
  ].join("\n");
}
