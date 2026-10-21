import { CompactNumberValue, formatPhasePercent, metricValue } from "../components/monitoringPresentation";
import { formatSeconds, num } from "../../../shared/components/DataTable";
import { GroupedDetailCard, firstValue, hasValue } from "./detailPrimitives";

export function PerformanceDetailSections({ row }: { row: Record<string, unknown> }) {
  const candidateReason = firstValue(row, ["performance_candidate_reason", "candidate_reason"]);
  return (
    <section className="monitoring-detail-section monitoring-performance-evidence-section">
      <h3>Performance evidence</h3>
      <div className="monitoring-dataflow-detail-grid monitoring-performance-evidence-grid">
        <GroupedDetailCard
          title="Optimization signal"
          rows={[
            ["Primary reason", candidateReason],
            ["Rule", row.performance_candidate_code],
            ["Priority", row.performance_candidate_priority],
          ]}
          showEmpty
        />
        <GroupedDetailCard
          title="Efficiency"
          rows={[
            ["Rows processed", <CompactNumberValue value={metricValue(row, "performance_rows_processed")} />],
            ["Rows / second", <CompactNumberValue value={metricValue(row, "performance_rows_per_second")} suffix="/s" />],
            ["Overhead share", performanceRatioLabel(row.performance_overhead_ratio)],
            ["Dominant share", performanceRatioLabel(row.performance_dominant_phase_ratio)],
          ]}
          showEmpty
        />
        <GroupedDetailCard
          title="Phase cost"
          rows={[
            ["Bottleneck", row.performance_bottleneck_phase],
            ["Source", formatSeconds(num(row, "source_duration_seconds"))],
            ["Transform", formatSeconds(num(row, "transform_duration_seconds"))],
            ["Destination", formatSeconds(num(row, "destination_duration_seconds"))],
            ["Overhead", formatSeconds(num(row, "overhead_duration_seconds"))],
          ]}
          showEmpty
        />
      </div>
    </section>
  );
}

export function performanceRatioLabel(value: unknown) {
  if (!hasValue(value)) return "-";
  const ratio = Number(value);
  return Number.isFinite(ratio) ? formatPhasePercent(ratio * 100) : "-";
}
