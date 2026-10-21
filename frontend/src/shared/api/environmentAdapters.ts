import type { EnvironmentOverviewResponseTransport } from "./contractTypes";
import {
  asRecord,
  optionalDecimalString,
  nullableString,
  numberValue,
  recordArray,
} from "./transportValues";
import type { EnvironmentOverview, LineageSummary } from "./domainTypes";

export function toEnvironmentOverview(
  response: EnvironmentOverviewResponseTransport,
): EnvironmentOverview {
  const metadata = response.metadata;
  const monitoring = response.monitoring;
  const failedJobWindows = asRecord(monitoring.failed_job_windows);
  const dateRange = asRecord(monitoring.date_range);
  return {
    schema_version: response.schema_version,
    sources: response.sources,
    metadata: {
      connections: metadata.connections,
      enabled_connections: metadata.enabled_connections,
      dataflows: metadata.dataflows,
      enabled_dataflows: metadata.enabled_dataflows,
      schema_hints: metadata.schema_hints,
      enabled_schema_hints: metadata.enabled_schema_hints,
      stages: metadata.stages.map((item) => ({ name: item.name, count: item.count })),
      load_types: metadata.load_types.map((item) => ({ name: item.name, count: item.count })),
      errors: recordArray(metadata.errors),
    },
    lineage: toLineageSummary(response.lineage),
    monitoring: {
      job_records: overviewMetric(monitoring.job_records),
      total_failures: overviewMetric(monitoring.total_failures),
      dataflow_success_rate: monitoring.dataflow_success_rate,
      failed_job_windows: {
        last7: overviewMetric(failedJobWindows.last7),
        last30: overviewMetric(failedJobWindows.last30),
        last365: overviewMetric(failedJobWindows.last365),
      },
      active_engines: overviewMetric(monitoring.active_engines),
      latest_log_at: nullableString(monitoring.latest_log_at),
      date_range: {
        min: nullableString(dateRange.min),
        max: nullableString(dateRange.max),
      },
      errors: recordArray(monitoring.errors),
    },
    cache: {
      state: response.cache.state,
      computed_at: response.cache.computed_at,
    },
  };
}

function overviewMetric(value: unknown) {
  if (value === null) return null;
  return optionalDecimalString(value) ?? "0";
}

function toLineageSummary(value: unknown): LineageSummary & { error_count: number } {
  const summary = asRecord(value);
  return {
    assets: numberValue(summary.assets),
    references: numberValue(summary.references),
    dataflows: numberValue(summary.dataflows),
    dependencies: numberValue(summary.dependencies),
    stitched_assets: numberValue(summary.stitched_assets),
    declared_assets: numberValue(summary.declared_assets),
    automatic_references: numberValue(summary.automatic_references),
    manual_references: numberValue(summary.manual_references),
    unresolved_references: numberValue(summary.unresolved_references),
    automatic_dependencies: numberValue(summary.automatic_dependencies),
    manual_dependencies: numberValue(summary.manual_dependencies),
    unresolved_dependencies: numberValue(summary.unresolved_dependencies),
    diagnostics: numberValue(summary.diagnostics),
    error_count: numberValue(summary.error_count),
  };
}
