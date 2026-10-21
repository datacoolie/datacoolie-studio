import { Help } from "../../../shared/components/Help";
import { useEffect, useMemo, useState } from "react";
import type { EChartsOption } from "echarts";
import type { MonitoringRecord, MonitoringReport } from "../../../shared/api/domainTypes";
import type { MonitoringFilters } from "../monitoringFilters";
import type { TableSort } from "../../../shared/components/DataTable";
import {
  CompactNumberValue,
  HealthStripCard,
  ReportChart,
  ReportPanel,
  TablePager,
  baseChartOption,
  bottomAnchoredValueXAxis,
  fixedHorizontalBarGrid,
  fixedHorizontalAxisLabel,
  formatBytesShort,
  formatCompact,
  formatTooltipBytes,
  formatTooltipNumber,
  formatNumber,
  formatPercent,
  formatSeconds,
  horizontalBarDataZoom,
  humanize,
  metricNumber,
  metricValue,
  monitoringTimezone,
  normalizeTrendBucketKey,
  num,
  reportChartPalette,
  reportChartGrid,
  resolveTrendBucketKeys,
  toApproximateNumber,
} from "../components/monitoringPresentation";
import { scatterObservations } from "../components/monitoringObservation";
import { compareMetricValues } from "../../../shared/lib/numberFormat";
import { MaintenanceRegistryTable } from "./MaintenanceRegistryTable";
import { maintenanceStatusOption } from "./maintenanceStatusChart";
import { destinationHelpContent, presentDestination, presentDestinationRows } from "../destinationPresentation";
import { chartDurationMetric, chartMetric, chartTooltip, firstChartParam } from "../components/chartTooltip";

const DESTINATION_STATUSES = ["succeeded", "failed", "skipped", "running", "pending", "unknown"] as const;
type EfficiencyScaleMode = "linear" | "log";
export type DestinationMetric = "rows" | "files" | "bytes";
type DestinationEffect = "added" | "removed";
type TrendMetric = "files" | "bytes";
type DestinationReport = MonitoringReport["maintenance"] & Record<string, unknown>;
type MetricDisplayState = {
  value: ReturnType<typeof metricValue>;
  observed: boolean;
  measured: ReturnType<typeof metricValue>;
  denominator: ReturnType<typeof metricValue>;
};

export function MaintenancePage({
  report,
  filters,
  rows,
  totalRows,
  loading,
  sort,
  onSort,
  limit,
  offset,
  onPageChange,
  onPageSizeChange,
  onInspect,
  timezoneName: timezoneNameProp,
  reportState,
  scrollResetKey,
  tablePending,
  tableError,
}: {
  report?: MonitoringReport | null;
  filters: MonitoringFilters;
  rows: MonitoringRecord[];
  totalRows: number;
  loading: boolean;
  sort: TableSort;
  onSort: (sort: TableSort) => void;
  limit: number;
  offset: number;
  onPageChange: (offset: number) => void;
  onPageSizeChange: (limit: number) => void;
  onInspect?: (row: MonitoringRecord) => void;
  timezoneName?: string | null;
  reportState?: { loading: boolean; error: string | null; retry: () => void };
  scrollResetKey?: string;
  tablePending?: boolean;
  tableError?: string | null;
}) {
  const maintenance = (report?.maintenance ?? {}) as DestinationReport;
  const kpis = (maintenance.kpis ?? {}) as Record<string, unknown>;
  const timezoneName = timezoneNameProp ?? (report ? monitoringTimezone(report) : null);
  const tableRows = rows;
  const outcomeRows = useMemo(() => rowsFrom(maintenance.table_outcome), [maintenance.table_outcome]);
  const efficiencyRows = useMemo(() => rowsFrom(maintenance.table_efficiency_points), [maintenance.table_efficiency_points]);
  const maintenanceStatusRows = useMemo(() => rowsFrom(maintenance.maintenance_status_counts), [maintenance.maintenance_status_counts]);
  const maintenanceStatusChart = useMemo(() => maintenanceStatusOption(maintenanceStatusRows), [maintenanceStatusRows]);
  const [trendMetric, setTrendMetric] = useState<TrendMetric>(() => defaultTrendMetric(maintenance));
  const [outcomeMetric, setOutcomeMetric] = useState<DestinationMetric>(() => defaultDestinationMetric(outcomeRows, false));
  const [outcomeEffect, setOutcomeEffect] = useState<DestinationEffect>(() => defaultDestinationEffect(outcomeRows, defaultDestinationMetric(outcomeRows, false), false));
  const [efficiencyMetric, setEfficiencyMetric] = useState<DestinationMetric>(() => defaultDestinationMetric(efficiencyRows, true));
  const [efficiencyEffect, setEfficiencyEffect] = useState<DestinationEffect>(() => defaultDestinationEffect(efficiencyRows, defaultDestinationMetric(efficiencyRows, true), true));
  const [efficiencyScale, setEfficiencyScale] = useState<EfficiencyScaleMode>("linear");
  const efficiencyObservations = useMemo(() => destinationEfficiencyObservations(efficiencyRows, efficiencyMetric, efficiencyEffect), [efficiencyRows, efficiencyMetric, efficiencyEffect]);

  useEffect(() => { setTrendMetric(defaultTrendMetric(maintenance)); }, [maintenance.reclaim_by_date, maintenance.bytes_reclaimed_by_date]);
  useEffect(() => { const metric = defaultDestinationMetric(outcomeRows, false); setOutcomeMetric(metric); setOutcomeEffect(defaultDestinationEffect(outcomeRows, metric, false)); }, [outcomeRows]);
  useEffect(() => { const metric = defaultDestinationMetric(efficiencyRows, true); setEfficiencyMetric(metric); setEfficiencyEffect(defaultDestinationEffect(efficiencyRows, metric, true)); }, [efficiencyRows]);
  useEffect(() => { setEfficiencyScale(defaultEfficiencyScale(efficiencyRows, efficiencyMetric, efficiencyEffect)); }, [efficiencyRows, efficiencyMetric, efficiencyEffect]);

  if (!report) {
    return <div className="monitoring-page monitoring-maintenance-report"><div className="table-empty" role="status">{reportState?.error ?? (reportState?.loading === false ? "No destination report" : "Loading destination report…")}</div></div>;
  }

  const destinationCount = firstMetric(kpis, "destination_count");
  const resolvedDestinations = firstMetric(kpis, "resolved_destinations");
  const unresolvedDestinations = firstMetric(kpis, "unresolved_binding_destinations");
  const conflictingDestinations = firstMetric(kpis, "conflicting_destinations");
  const activeDestinations = firstMetric(kpis, "active_destinations");
  const totalDestinationNumber = toApproximateNumber(destinationCount);
  const activeDestinationNumber = toApproximateNumber(activeDestinations);
  const destinationsWithChangesRate = totalDestinationNumber !== null && totalDestinationNumber > 0 && activeDestinationNumber !== null
    ? `${(activeDestinationNumber / totalDestinationNumber * 100).toFixed(1)}%`
    : "-";
  const destinationsWithFailures = firstMetric(kpis, "destinations_with_failures");
  const failedDestinationNumber = toApproximateNumber(destinationsWithFailures);
  const destinationsWithFailuresRate = totalDestinationNumber !== null && totalDestinationNumber > 0 && failedDestinationNumber !== null
    ? `${(failedDestinationNumber / totalDestinationNumber * 100).toFixed(1)}%`
    : "-";
  const operationCount = firstMetric(kpis, "operation_count");
  const operationTypes = rowsFrom(maintenance.operation_type_counts).map((row) => ({
    label: row.operation_type === "etl" ? "ETL" : humanize(String(row.operation_type ?? "unknown")),
    count: metricValue(row, "count"),
  }));
  const operationDetail = operationTypes.length ? operationTypes.map(({ label, count }, index) => <span key={label}>{index ? " · " : ""}{label} <b>{formatCompact(count)}</b></span>) : "-";
  const operationTooltip = ["Operations by type in the current filters.", ...operationTypes.map(({ label, count }) => `${label}: ${formatTooltipNumber(count)}`)].join("\n");
  const rowsWritten = metricDisplay(kpis, "workload_rows_written");
  const filesWritten = metricDisplay(kpis, "workload_files_added");
  const bytesWritten = metricDisplay(kpis, "workload_bytes_added");
  const filesRemoved = metricDisplay(kpis, "workload_files_removed");
  const bytesRemoved = metricDisplay(kpis, "workload_bytes_removed");
  const avgRowsWritten = firstMetric(kpis, "workload_rows_written_avg_per_run");
  const writtenIsPartial = [rowsWritten, filesWritten, filesRemoved, bytesWritten, bytesRemoved].some((state) => state.observed);
  const filesTooltip = [
    "Files added / removed in the current filters (in that order).",
    `Added: ${formatTooltipNumber(filesWritten.value)}; coverage: ${formatTooltipNumber(filesWritten.measured)} / ${formatTooltipNumber(filesWritten.denominator)} runs.`,
    `Removed: ${formatTooltipNumber(filesRemoved.value)}; coverage: ${formatTooltipNumber(filesRemoved.measured)} / ${formatTooltipNumber(filesRemoved.denominator)} runs.`,
    "Reported root metrics, including all operation types. Missing measurements are excluded; recorded zero values are included.",
  ].join("\n");
  const bytesTooltip = [
    "Bytes added / removed in the current filters (in that order).",
    `Added: ${formatTooltipBytes(bytesWritten.value)}; coverage: ${formatTooltipNumber(bytesWritten.measured)} / ${formatTooltipNumber(bytesWritten.denominator)} runs.`,
    `Removed: ${formatTooltipBytes(bytesRemoved.value)}; coverage: ${formatTooltipNumber(bytesRemoved.measured)} / ${formatTooltipNumber(bytesRemoved.denominator)} runs.`,
    "Reported root metrics, including all operation types. Missing measurements are excluded; recorded zero values are included.",
  ].join("\n");
  const writtenTooltip = [
    "Reported destination write workload in the current filters, including all operation types.",
    writtenIsPartial ? "Observed subtotals: metric coverage is incomplete." : "Totals cover the recorded measurements in the current filters.",
    "Line 2: total rows written - average rows written per measured run.",
    "Line 3: files added / removed; bytes added / removed.",
    `Rows total: ${formatTooltipNumber(rowsWritten.value)}; avg: ${formatTooltipNumber(avgRowsWritten)} per measured run.`,
    `Rows coverage: ${formatTooltipNumber(rowsWritten.measured)} / ${formatTooltipNumber(rowsWritten.denominator)} runs.`,
    filesTooltip,
    bytesTooltip,
    "Rows average divides by runs recording rows_written, including zero. Missing measurements are excluded.",
  ].join("\n");
  const lakehouseDestinations = firstMetric(kpis, "maintenance_lakehouse_destinations");
  const successfullyMaintained = firstMetric(kpis, "maintenance_successful_destinations");
  const maintenanceCoverage = toApproximateNumber(firstMetric(kpis, "maintenance_coverage_rate"));
  const maintenanceTooltip = [
    "Share of filtered Delta/Iceberg destinations with at least one successful maintenance.",
    "Requires a non-dry-run job and a successful destination phase; no-change successes count.",
    `Lakehouse destinations: ${formatTooltipNumber(lakehouseDestinations)}.`,
    `With a maintenance attempt: ${formatTooltipNumber(firstMetric(kpis, "maintenance_attempted_destinations"))}.`,
    `With successful maintenance: ${formatTooltipNumber(successfullyMaintained)}.`,
    `No maintenance recorded: ${formatTooltipNumber(firstMetric(kpis, "maintenance_unrecorded_destinations"))}.`,
    "Each destination is counted once. Only recorded Delta/Iceberg formats are included; unknown formats are excluded.",
    "All counts honor the current filters. No recorded maintenance does not establish a storage issue.",
  ].join("\n");
  const failuresTone = metricNumber(destinationsWithFailures) > 0 ? "bad" : "neutral";
  const reportError = reportState?.error;

  return (
    <div className="monitoring-page monitoring-maintenance-report">
      <section className="overview-health-strip monitoring-maintenance-health-strip monitoring-destination-kpi-strip">
        <HealthStripCard
          label="Destinations"
          value={<CompactNumberValue value={destinationCount} />}
          detail={<span className="health-rate-detail">
            <Help as="span" title="Resolved: enough address and connection information to identify the destination."><b className="destination-identity-count is-resolved">{formatTooltipNumber(resolvedDestinations)}</b> resolved</Help>
            <span className="separator"> · </span>
            <Help as="span" title="Unresolved: destination recorded, but its address or connection binding is incomplete or weak."><b className="destination-identity-count is-unresolved">{formatTooltipNumber(unresolvedDestinations)}</b> unresolved</Help>
            <span className="separator"> · </span>
            <Help as="span" title="Conflicts: logs and metadata disagree, or multiple destination bindings match."><b className="destination-identity-count is-conflict">{formatTooltipNumber(conflictingDestinations)}</b> conflicts</Help>
          </span>}
          title={"Destinations recorded in the current filters.\nResolved: destination identity is sufficiently bound.\nUnresolved: address or connection binding is incomplete or weak.\nConflicts: logs and metadata disagree, or multiple bindings match."}
          className="maintenance-kpi maintenance-kpi-destinations"
        />
        <HealthStripCard
          label="Destinations with changes"
          value={<CompactNumberValue value={activeDestinations} />}
          detail={<Help as="span" title="Share of destinations with recorded changes in the current filters."><b>{destinationsWithChangesRate}</b> of destinations</Help>}
          title={"Destinations with at least one recorded change in the current filters.\nRequires a non-dry-run job and a successful destination phase.\nAt least one rows written/inserted/updated/deleted, files added/removed, or bytes added/removed value must be greater than zero.\nIncludes ETL and maintenance; each destination is counted once."}
          accent="storage"
          className="maintenance-kpi maintenance-kpi-active-destinations"
        />
        <HealthStripCard
          label="Destinations with failures"
          value={<CompactNumberValue value={destinationsWithFailures} />}
          detail={<Help as="span" title="Share of destinations with at least one destination phase failure in the current filters."><b>{destinationsWithFailuresRate}</b> of destinations</Help>}
          intent={failuresTone}
          title="Destinations with at least one failed destination phase in the current filters. A later success does not erase historical failures."
          className="maintenance-kpi maintenance-kpi-failed-destinations"
        />
        <HealthStripCard
          label="Operations"
          value={<CompactNumberValue value={operationCount} />}
          detail={<Help as="span" title={operationTooltip}>{operationDetail}</Help>}
          title={operationTooltip}
          className="maintenance-kpi maintenance-kpi-operations"
        />
        <HealthStripCard
          label="Written"
          value={<Help as="span" className="destination-written-kpi-total" title={writtenTooltip}><b>{formatCompact(rowsWritten.value)}</b> r <span className="separator">–</span> Avg <b>{formatCompact(avgRowsWritten)}</b> r</Help>}
          detail={<span className="destination-written-kpi-effects"><Help as="span" title={filesTooltip}>Files <b>{formatCompact(filesWritten.value)}</b> / <b>{formatCompact(filesRemoved.value)}</b></Help> <span className="separator">·</span> <Help as="span" title={bytesTooltip}>Bytes <b>{writtenBytesLabel(bytesWritten.value)}</b> / <b>{writtenBytesLabel(bytesRemoved.value)}</b></Help></span>}
          accent="storage"
          title={writtenTooltip}
          className="maintenance-kpi maintenance-kpi-written"
        />
        <HealthStripCard
          label="Maintenance coverage"
          value={maintenanceCoverage === null ? "—" : `${maintenanceCoverage.toFixed(1)}%`}
          detail={<Help as="span" title={maintenanceTooltip}><b>{formatTooltipNumber(successfullyMaintained)}</b> / <b>{formatTooltipNumber(lakehouseDestinations)}</b> lakehouse</Help>}
          title={maintenanceTooltip}
          className="maintenance-kpi maintenance-kpi-coverage"
        />
      </section>

      {reportError ? <div className="monitoring-report-error" role="alert">{reportError}</div> : null}

      <div className="monitoring-maintenance-content monitoring-equal-rows-3">
        <section className="monitoring-maintenance-primary-grid">
          <ReportPanel
            title="Destination activity"
            className="monitoring-maintenance-status-trend-panel"
            titleTooltip="All filtered destination operations by time bucket. Lifecycle status counts are kept separate."
            headerAction={<DestinationStatusLegend />}
          >
            <ReportChart option={destinationStatusTrendOption(report, filters, timezoneName ?? "UTC")} height="100%" />
          </ReportPanel>
          <ReportPanel
            title="Added / removed trend"
            className="monitoring-maintenance-reclaim-trend-panel"
            titleTooltip="Filtered recorded destination effects. Select files or bytes; added and removed values remain distinct."
            headerAction={<DestinationMetricToggle value={trendMetric} onChange={setTrendMetric} label="Trend metric" metrics={["files", "bytes"]} />}
          >
            <ReportChart option={destinationChangeTrendOption(report, filters, timezoneName ?? "UTC", trendMetric)} height="100%" />
          </ReportPanel>
        </section>

        <section className="monitoring-maintenance-secondary-grid">
          <ReportPanel
            title="Top destinations"
            className="monitoring-maintenance-top-destinations-panel"
            titleTooltip="Metric candidates are ranked before the chart limit. Files and bytes include added and removed recorded effects."
            headerAction={<div className="monitoring-destination-chart-actions"><DestinationMetricToggle value={outcomeMetric} onChange={(metric) => { setOutcomeMetric(metric); if (metric === "rows") setOutcomeEffect("added"); }} label="Top destination metric" metrics={["rows", "files", "bytes"]} /><DestinationEffectToggle metric={outcomeMetric} value={outcomeEffect} onChange={setOutcomeEffect} label="Top destination effect" /></div>}
          >
            <ReportChart option={destinationOutcomeOption(outcomeRows, outcomeMetric, outcomeEffect)} height="100%" wheelDataZoomStep={1} />
          </ReportPanel>
          <ReportPanel
            title="Maintenance status"
            subtitle={<><CompactNumberValue value={lakehouseDestinations} /> lakehouse</>}
            className="monitoring-maintenance-status-panel"
            titleTooltip={"Unique Delta/Iceberg destinations by their latest maintenance within the current filters.\nCoverage KPI counts any successful maintenance in the period; this chart shows the latest outcome.\nDry-run and unconfirmed successes remain separate. No maintenance recorded does not establish a storage issue."}
          >
            <ReportChart option={maintenanceStatusChart} height="100%" wheelDataZoomStep={1} />
          </ReportPanel>
          <ReportPanel
            title="Destination efficiency"
            subtitle={<Help as="span" title={`Eligible operations with paired ${efficiencyLabel(efficiencyMetric, efficiencyEffect)} and root runtime evidence. Up to 800 destinations.`}>{efficiencyObservations.points.length} destinations</Help>}
            className="monitoring-maintenance-efficiency-panel"
            titleTooltip="Each point pairs an authoritative root effect with root operation runtime. Final destination phase runtime remains a separate detail."
            headerAction={<div className="monitoring-maintenance-efficiency-actions"><DestinationMetricToggle value={efficiencyMetric} onChange={(metric) => { setEfficiencyMetric(metric); if (metric === "rows") setEfficiencyEffect("added"); }} label="Efficiency metric" metrics={["rows", "files", "bytes"]} /><DestinationEffectToggle metric={efficiencyMetric} value={efficiencyEffect} onChange={setEfficiencyEffect} label="Efficiency effect" /><EfficiencyScaleToggle value={efficiencyScale} onChange={setEfficiencyScale} /></div>}
          >
            <ReportChart option={maintenanceEfficiencyOption(efficiencyRows, efficiencyScale, efficiencyMetric, efficiencyEffect)} height="100%" />
          </ReportPanel>
        </section>

        <ReportPanel
          title="Destination registry"
          subtitle={<><CompactNumberValue value={totalRows} /> destinations{sort.sortBy === "attention_priority" && sort.sortDir === "desc" ? " · signals first" : ""}</>}
          className="monitoring-maintenance-runs-panel"
          titleTooltip="One row per physical destination object. Lifecycle, effects and maintenance evidence follow the current filters."
          headerAction={<TablePager limit={limit} offset={offset} loadedRows={tableRows.length} totalRows={totalRows} loading={loading || Boolean(tablePending)} onPageChange={onPageChange} onPageSizeChange={onPageSizeChange} />}
        >
          {tableError ? <div className="monitoring-report-error" role="alert">{tableError}</div> : null}
          {tablePending && !tableRows.length ? <div className="table-empty" role="status">Loading destination registry…</div> : <MaintenanceRegistryTable rows={tableRows} sort={sort} onSort={onSort} timezoneName={timezoneName} onInspect={onInspect} scrollResetKey={scrollResetKey ?? `${offset}:${sort.sortBy}:${sort.sortDir}`} />}
        </ReportPanel>
      </div>
    </div>
  );
}

function rowsFrom(value: unknown): Array<Record<string, unknown>> {
  return Array.isArray(value) ? value.filter((row): row is Record<string, unknown> => Boolean(row) && typeof row === "object") : [];
}

function firstMetric(row: Record<string, unknown>, ...keys: string[]) {
  for (const key of keys) {
    const value = metricValue(row, key);
    if (value !== undefined && value !== null && value !== "") return value;
  }
  return null;
}

function metricDisplay(row: Record<string, unknown>, key: string): MetricDisplayState {
  const exact = metricValue(row, key);
  const observedValue = metricValue(row, `${key}_observed`);
  const measured = metricValue(row, `${key}_measured_runs`);
  const denominator = metricValue(row, "operation_count");
  const value = exact ?? observedValue;
  const observed = value != null && (exact == null
    || (measured != null && denominator != null && compareMetricValues(measured, denominator) < 0));
  return { value, observed, measured, denominator };
}

function writtenBytesLabel(value: ReturnType<typeof metricValue>) {
  const label = formatBytesShort(value);
  return label === "0" ? "0 B" : label.replace(/([A-Z]+)$/u, " $1");
}

function firstText(row: Record<string, unknown>, ...keys: string[]) {
  for (const key of keys) {
    const value = row[key];
    if (value !== undefined && value !== null && String(value).trim() !== "") return String(value);
  }
  return "";
}

function DestinationStatusLegend() {
  return <DestinationChartLegend label="Destination status legend" items={DESTINATION_STATUSES.map((status) => [statusLabel(status), statusColor(status)] as const)} />;
}

function DestinationChartLegend({ label, items }: { label: string; items: ReadonlyArray<readonly [string, string]> }) {
  return <div className="monitoring-maintenance-chart-legend" aria-label={label}>{items.map(([itemLabel, color]) => <span key={itemLabel}><i style={{ backgroundColor: color }} aria-hidden="true" />{itemLabel}</span>)}</div>;
}

function EfficiencyScaleToggle({ value, onChange }: { value: EfficiencyScaleMode; onChange: (value: EfficiencyScaleMode) => void }) {
  return <div className="segmented-control performance-scale-toggle" role="group" aria-label="Efficiency scale"><button type="button" className={value === "linear" ? "active" : ""} aria-pressed={value === "linear"} onClick={() => onChange("linear")}>Linear</button><button type="button" className={value === "log" ? "active" : ""} aria-pressed={value === "log"} onClick={() => onChange("log")}>Log</button></div>;
}

function DestinationMetricToggle<T extends TrendMetric | DestinationMetric>({ value, onChange, label, metrics }: { value: T; onChange: (value: T) => void; label: string; metrics: readonly T[] }) {
  return <div className="segmented-control performance-scale-toggle" role="group" aria-label={label}>{metrics.map((metric) => <button key={metric} type="button" className={value === metric ? "active" : ""} aria-pressed={value === metric} onClick={() => onChange(metric)}>{metricLabel(metric)}</button>)}</div>;
}

function DestinationEffectToggle({ metric, value, onChange, label }: { metric: DestinationMetric; value: DestinationEffect; onChange: (value: DestinationEffect) => void; label: string }) {
  const options: ReadonlyArray<DestinationEffect> = metric === "rows" ? ["added"] : ["added", "removed"];
  return <Help as="div" className="segmented-control performance-scale-toggle" role="group" aria-label={label} title={metric === "rows" ? "Rows use authoritative rows written effects." : "Select the recorded effect direction used for ranking and paired runtime."}>{options.map((effect) => <button key={effect} type="button" className={value === effect ? "active" : ""} aria-pressed={value === effect} onClick={() => onChange(effect)}>{metric === "rows" ? "Written" : effect === "added" ? "Added" : "Removed"}</button>)}</Help>;
}

function metricLabel(metric: string) {
  return metric === "bytes" ? "Bytes" : metric === "files" ? "Files" : "Rows";
}

function efficiencyLabel(metric: DestinationMetric, effect: DestinationEffect) {
  return metric === "rows" ? "rows written" : `${metricLabel(metric).toLowerCase()} ${effect}`;
}

function destinationStatusTrendOption(report: MonitoringReport, filters: MonitoringFilters, timezoneName: string): EChartsOption {
  const rows = trendRows((report.maintenance as DestinationReport).status_by_date, filters, report, timezoneName, createEmptyStatusRow);
  if (!rows.length) return emptyChartOption("No destination activity in current filters.");
  return baseChartOption({
    tooltip: { trigger: "axis", formatter: chartTooltip((params) => {
      const row = rows[Number(firstChartParam(params)?.dataIndex ?? 0)] ?? {};
      return {
        title: String(row.bucket || row.date || ""),
        context: [row.grain ? `Grain: ${row.grain}` : "", timezoneName ? `Timezone: ${timezoneName}` : ""].filter(Boolean).join(" · ") || undefined,
        metrics: [
          chartMetric("Total", formatNumber(metricValue(row, "total"))),
          ...DESTINATION_STATUSES.map((status) => ({ ...chartMetric(statusLabel(status), formatNumber(metricValue(row, status))), valueColor: statusColor(status) })),
          ...(metricValue(row, "success_rate") == null ? [] : [chartMetric("Success rate", formatPercent(metricValue(row, "success_rate")))]),
        ]
      };
    }) },
    grid: reportChartGrid({ left: 34, right: 10, top: 8, containLabel: false }),
    xAxis: bottomCategoryAxis(rows.map((row) => String(row.bucket || row.date || ""))),
    yAxis: { type: "value", axisLabel: { fontSize: 10, formatter: (value: number) => formatCompact(value) }, splitLine: { lineStyle: { color: reportChartPalette.grid } } },
    series: DESTINATION_STATUSES.map((status) => ({ name: statusLabel(status), type: "bar" as const, stack: "status", itemStyle: { color: statusColor(status), borderRadius: status === "unknown" ? [3, 3, 0, 0] : 0 }, data: rows.map((row) => plotMetric(metricValue(row, status))) }))
  });
}

function destinationChangeTrendOption(report: MonitoringReport, filters: MonitoringFilters, timezoneName: string, metric: TrendMetric): EChartsOption {
  const section = report.maintenance as DestinationReport;
  const rows = trendRows(section.reclaim_by_date ?? section.bytes_reclaimed_by_date, filters, report, timezoneName, createEmptyChangeRow);
  if (!rows.length) return emptyChartOption(`No recorded ${metric} changes in current filters.`);
  const addedLabel = `${metricLabel(metric)} added`;
  const removedLabel = `${metricLabel(metric)} removed`;
  return baseChartOption({
    tooltip: { trigger: "axis", formatter: chartTooltip((params) => {
      const row = rows[Number(firstChartParam(params)?.dataIndex ?? 0)] ?? {};
      return {
        title: String(row.bucket || row.date || ""),
        context: row.grain ? `Grain: ${row.grain}` : undefined,
        metrics: [
          { ...chartMetric("Added", formatMetric(metric, trendMetricValue(row, metric, "added"))), valueColor: reportChartPalette.teal },
          { ...chartMetric("Removed", formatMetric(metric, trendMetricValue(row, metric, "removed"))), valueColor: reportChartPalette.blue },
          chartMetric("Runs", formatTooltipNumber(firstMetric(row, "runs", "operation_count"))),
        ]
      };
    }) },
    grid: reportChartGrid({ left: metric === "bytes" ? 48 : 38, right: 10, top: 8, containLabel: false }),
    xAxis: bottomCategoryAxis(rows.map((row) => String(row.bucket || row.date || ""))),
    yAxis: [{ type: "value", min: 0, axisLabel: { fontSize: 10, formatter: (value: number) => metric === "bytes" ? formatBytesShort(value) : formatCompact(value) }, splitLine: { lineStyle: { color: reportChartPalette.grid } } }, { type: "value", show: false, min: 0 }],
    series: [
      { name: addedLabel, type: "bar", itemStyle: { color: reportChartPalette.teal, borderRadius: [3, 3, 0, 0] }, data: rows.map((row) => plotMetric(trendMetricValue(row, metric, "added"))) },
      { ...lineSeries(removedLabel, rows.map((row) => plotMetric(trendMetricValue(row, metric, "removed"))), reportChartPalette.blue, 0), name: removedLabel }
    ]
  });
}

function destinationOutcomeOption(rows: Array<Record<string, unknown>>, metric: DestinationMetric, effect: DestinationEffect): EChartsOption {
  const visible = rows.filter((row) => metricNumber(destinationMetricValue(row, metric, effect)) > 0).sort((left, right) => compareMetricValues(destinationMetricValue(right, metric, effect), destinationMetricValue(left, metric, effect)) || String(left.target_display ?? "").localeCompare(String(right.target_display ?? ""))).slice(0, 200);
  if (!visible.length) return emptyChartOption(`No recorded positive ${efficiencyLabel(metric, effect)} changes.`);
  const names = presentDestinationRows(visible);
  const namesById = new Map(names.map(value => [value.id, value]));
  const dataZoom = horizontalBarDataZoom(visible.length);
  return baseChartOption({
    tooltip: { trigger: "item", formatter: chartTooltip((params) => {
      const index = Number(firstChartParam(params)?.dataIndex ?? 0);
      const row = visible[index] ?? {};
      const destination = names[index] ?? presentDestination(row);
      const content = destinationHelpContent(destination);
      return {
        ...content,
        metrics: [
          ...(content.metrics ?? []),
          chartMetric(`${metricLabel(metric)} ${metric === "rows" ? "written" : effect}`, formatMetric(metric, destinationMetricValue(row, metric, effect))),
          chartMetric("Runs", formatTooltipNumber(firstMetric(row, "run_count"))),
          chartMetric("Latest status", firstText(row, "latest_status") || "unknown"),
        ]
      };
    }) },
    grid: fixedHorizontalBarGrid(132, Boolean(dataZoom), { right: dataZoom ? 24 : 10, top: 8 }),
    xAxis: bottomAnchoredValueXAxis({ formatter: (value) => metric === "bytes" ? formatBytesShort(value) : formatCompact(value) }),
    yAxis: { type: "category", data: names.map(value => value.id), inverse: true, axisTick: { show: false }, axisLabel: fixedHorizontalAxisLabel(132, { margin: 8, fontSize: 10, interval: 0, formatter: (id: string) => namesById.get(id)?.name ?? id }) },
    dataZoom,
    series: [{ name: efficiencyLabel(metric, effect), type: "bar", barMaxWidth: 18, itemStyle: { color: effect === "removed" ? reportChartPalette.blue : reportChartPalette.teal, borderRadius: [0, 3, 3, 0] }, label: { show: true, position: "right", color: reportChartPalette.text, fontSize: 9, formatter: (params: any) => formatMetric(metric, params?.value) }, data: visible.map((row) => plotMetric(destinationMetricValue(row, metric, effect))) }]
  });
}

export function maintenanceEfficiencyOption(rows: Array<Record<string, unknown>>, scaleMode: EfficiencyScaleMode, metric: DestinationMetric = "bytes", effect: DestinationEffect = "added"): EChartsOption {
  const observations = destinationEfficiencyObservations(rows, metric, effect);
  const visible = observations.points.map((point) => point.row);
  if (!visible.length) return emptyChartOption(`No paired ${efficiencyLabel(metric, effect)} / runtime evidence.`);
  const fields = efficiencyFields(metric, effect);
  const names = presentDestinationRows(visible);
  return baseChartOption({
    grid: reportChartGrid({ left: 42, right: 8, top: 8, bottom: 18, containLabel: false }),
    tooltip: { trigger: "item", formatter: chartTooltip((params) => {
      const index = Number(firstChartParam(params)?.dataIndex ?? 0);
      const row = visible[index] ?? {};
      const destination = names[index] ?? presentDestination(row);
      const content = destinationHelpContent(destination);
      const latestStatus = firstText(row, "latest_status").toLowerCase() || "unknown";
      return {
        ...content,
        metrics: [
          ...(content.metrics ?? []),
          { ...chartMetric("Point color (latest status)", statusLabel(latestStatus)), valueColor: destinationSignalColor(latestStatus) },
          chartMetric(`Paired ${efficiencyLabel(metric, effect)}`, formatMetric(metric, firstMetric(row, ...fields.effectKeys))),
          chartMetric("Root runtime", chartDurationMetric(firstMetric(row, ...fields.runtimeKeys), { compactOnly: true })),
          chartMetric("Paired runs", formatTooltipNumber(firstMetric(row, ...fields.runKeys))),
        ],
        note: firstText(row, "attention_reason") ? `Signal: ${firstText(row, "attention_reason")}` : undefined,
      };
    }) },
    xAxis: { type: "value", scale: true, axisLabel: { fontSize: 10, formatter: (value: number) => metric === "bytes" ? formatBytesShort(rawAxisValue(value, scaleMode)) : formatCompact(rawAxisValue(value, scaleMode)) }, splitLine: { lineStyle: { color: reportChartPalette.grid } } },
    yAxis: { type: "value", scale: true, name: "Root runtime", nameTextStyle: { fontSize: 10 }, axisLabel: { fontSize: 10, formatter: (value: number) => formatSeconds(rawAxisValue(value, scaleMode)) }, splitLine: { lineStyle: { color: reportChartPalette.grid } } },
    series: [{ name: `${efficiencyLabel(metric, effect)} / root runtime`, type: "scatter", symbolSize: (_value: unknown, params: any) => Math.max(6, Math.min(20, 6 + Math.sqrt(numFromKeys(visible[Number(params?.dataIndex ?? 0)] ?? {}, fields.runKeys)))), itemStyle: { color: (params: any) => destinationSignalColor(String(visible[Number(params?.dataIndex ?? 0)]?.latest_status ?? "unknown")), opacity: 0.78, borderColor: "#ffffff", borderWidth: 1 }, data: observations.points.map((point) => [axisValue(point.x, scaleMode), axisValue(point.y, scaleMode)]) }]
  });
}

function destinationEfficiencyObservations(rows: Array<Record<string, unknown>>, metric: DestinationMetric, effect: DestinationEffect = "added") {
  const fields = efficiencyFields(metric, effect);
  const eligible = rows.filter((row) => firstMetric(row, ...fields.effectKeys) != null && numFromKeys(row, fields.runtimeKeys) > 0).sort((left, right) => compareMetricValues(firstMetric(right, ...fields.effectKeys), firstMetric(left, ...fields.effectKeys)) || String(left.target ?? "").localeCompare(String(right.target ?? ""))).slice(0, 800);
  return scatterObservations(eligible, (row) => firstMetric(row, ...fields.effectKeys), (row) => firstMetric(row, ...fields.runtimeKeys));
}

function efficiencyFields(metric: DestinationMetric, effect: DestinationEffect = "added") {
  if (metric === "rows") return { effectKeys: ["paired_rows_written"] as string[], runtimeKeys: ["paired_rows_runtime"] as string[], runKeys: ["paired_rows_runs"] as string[] };
  if (metric === "files") {
    return effect === "removed"
      ? { effectKeys: ["paired_files_removed"] as string[], runtimeKeys: ["paired_files_removed_runtime"] as string[], runKeys: ["paired_files_removed_runs"] as string[] }
      : { effectKeys: ["paired_files_added"] as string[], runtimeKeys: ["paired_files_runtime"] as string[], runKeys: ["paired_files_runs"] as string[] };
  }
  return effect === "removed"
    ? { effectKeys: ["paired_bytes_removed"] as string[], runtimeKeys: ["paired_bytes_removed_runtime"] as string[], runKeys: ["paired_bytes_removed_runs"] as string[] }
    : { effectKeys: ["paired_bytes_added"] as string[], runtimeKeys: ["paired_bytes_runtime"] as string[], runKeys: ["paired_bytes_runs"] as string[] };
}

function defaultDestinationMetric(rows: Array<Record<string, unknown>>, efficiency: boolean): DestinationMetric {
  const candidates: DestinationMetric[] = ["rows", "files", "bytes"];
  return candidates.find((metric) => rows.some((row) => {
    if (metric === "rows") return metricNumber(destinationMetricValue(row, metric, "added")) > 0;
    if (efficiency) return metricNumber(firstMetric(row, ...efficiencyFields(metric, "added").effectKeys)) > 0 || metricNumber(firstMetric(row, ...efficiencyFields(metric, "removed").effectKeys)) > 0;
    return metricNumber(effectPart(row, metric, "added")) > 0 || metricNumber(effectPart(row, metric, "removed")) > 0;
  })) ?? "rows";
}

function defaultDestinationEffect(rows: Array<Record<string, unknown>>, metric: DestinationMetric, efficiency: boolean): DestinationEffect {
  if (metric === "rows") return "added";
  if (efficiency) {
    if (rows.some((row) => metricNumber(firstMetric(row, ...efficiencyFields(metric, "added").effectKeys)) > 0)) return "added";
    if (rows.some((row) => metricNumber(firstMetric(row, ...efficiencyFields(metric, "removed").effectKeys)) > 0)) return "removed";
    return "added";
  }
  if (rows.some((row) => metricNumber(effectPart(row, metric, "added")) > 0)) return "added";
  if (rows.some((row) => metricNumber(effectPart(row, metric, "removed")) > 0)) return "removed";
  return "added";
}

function defaultTrendMetric(section: DestinationReport): TrendMetric {
  const rows = rowsFrom(section.reclaim_by_date ?? section.bytes_reclaimed_by_date);
  return rows.some((row) => metricNumber(trendMetricValue(row, "bytes", "added")) > 0 || metricNumber(trendMetricValue(row, "bytes", "removed")) > 0) ? "bytes" : "files";
}

function defaultEfficiencyScale(rows: Array<Record<string, unknown>>, metric: DestinationMetric, effect: DestinationEffect): EfficiencyScaleMode {
  const values = destinationEfficiencyObservations(rows, metric, effect).points.map((point) => point.x).filter((value) => value > 0);
  if (values.length < 2) return "linear";
  const sorted = values.slice().sort((left, right) => left - right);
  const median = sorted[Math.floor(sorted.length / 2)] ?? 0;
  return median > 0 && Math.max(...values) / median >= 1000 ? "log" : "linear";
}

function trendRows(rowsValue: unknown, filters: MonitoringFilters, report: MonitoringReport, timezoneName: string, createEmpty: (bucket: string, grain: string) => Record<string, unknown>) {
  const rows = rowsFrom(rowsValue);
  const effectiveGrain = String(rows.find((row) => row.grain)?.grain ?? report.summary.effective_grain ?? filters.grain ?? "day");
  const normalizedRows = rows.map((row) => { const bucket = normalizeTrendBucketKey(row.bucket ?? row.date, effectiveGrain, timezoneName); return { ...row, bucket, date: bucket }; });
  const knownKeys = normalizedRows.map((row) => String(row.bucket ?? "")).filter((key) => key && key !== "unknown");
  const dateKeys = resolveTrendBucketKeys(filters, report.summary.date_range, timezoneName, knownKeys, effectiveGrain);
  const byKey = new Map(normalizedRows.map((row) => [String(row.bucket), row]));
  return (dateKeys.length ? dateKeys.map((key) => ({ ...createEmpty(key, effectiveGrain), ...(byKey.get(key) ?? {}) })) : normalizedRows) as Array<Record<string, unknown>>;
}

function createEmptyStatusRow(bucket: string, grain: string) {
  return { bucket, date: bucket, grain, succeeded: 0, failed: 0, skipped: 0, running: 0, pending: 0, unknown: 0, total: 0, success_rate: null };
}

function createEmptyChangeRow(bucket: string, grain: string) {
  return { bucket, date: bucket, grain, files_added: 0, files_removed: 0, bytes_added: 0, bytes_removed: 0, bytes_reclaimed: 0, runs: 0 };
}

function destinationMetricValue(row: Record<string, unknown>, metric: DestinationMetric, effect: DestinationEffect = "added") {
  return effectPart(row, metric, effect);
}

function trendMetricValue(row: Record<string, unknown>, metric: TrendMetric, part: "added" | "removed") {
  return effectPart(row, metric, part);
}

function effectPart(row: Record<string, unknown>, metric: "rows" | "files" | "bytes", part: "added" | "removed") {
  if (metric === "rows") return firstMetric(row, "rows_written", "rows_written_observed");
  if (metric === "files") return firstMetric(row, part === "added" ? "files_added" : "files_removed", part === "added" ? "files_added_observed" : "files_removed_observed");
  return firstMetric(row, part === "added" ? "bytes_added" : "bytes_removed", part === "added" ? "bytes_added_observed" : "bytes_removed_observed");
}

function numFromKeys(row: Record<string, unknown>, keys: string[]) {
  for (const key of keys) {
    const value = num(row, key);
    if (value > 0) return value;
  }
  return 0;
}

function formatMetric(metric: string, value: ReturnType<typeof metricValue>) {
  return metric === "bytes" ? formatTooltipBytes(value) : formatTooltipNumber(value);
}

function axisValue(value: number, scaleMode: EfficiencyScaleMode) { return scaleMode === "linear" ? value : Math.log10(Math.max(0, value) + 1); }
function rawAxisValue(value: number, scaleMode: EfficiencyScaleMode) { return scaleMode === "linear" ? value : Math.max(0, Math.pow(10, value) - 1); }
function plotMetric(value: ReturnType<typeof metricValue>) { return value == null || value === "" ? null : toApproximateNumber(value); }

function bottomCategoryAxis(data: string[]) {
  return { type: "category" as const, position: "bottom" as const, data, axisLine: { onZero: false }, axisLabel: { fontSize: 10, hideOverlap: true }, axisTick: { show: false } };
}

function lineSeries(name: string, data: Array<number | null>, color: string, yAxisIndex = 0) {
  return { name, type: "line" as const, yAxisIndex, connectNulls: false, showSymbol: false, showAllSymbol: false, symbol: "circle", symbolSize: 5, clip: false, z: 6, smooth: 0.2, lineStyle: { width: 1.5, color }, itemStyle: { color: "#ffffff", borderColor: color, borderWidth: 1.3 }, data };
}

function statusLabel(status: string) { return status === "succeeded" ? "Succeeded" : status === "failed" ? "Failed" : status === "skipped" ? "Skipped" : status === "running" ? "Running" : status === "pending" ? "Pending" : "Unknown"; }
function statusColor(status: string) { return status === "succeeded" ? reportChartPalette.success : status === "failed" ? reportChartPalette.failed : status === "skipped" ? reportChartPalette.skipped : status === "running" ? reportChartPalette.running : status === "pending" ? reportChartPalette.pending : reportChartPalette.unknown; }
function destinationSignalColor(status: string) { return statusColor(status.toLowerCase()); }

function emptyChartOption(message: string): EChartsOption {
  return baseChartOption({ graphic: { type: "text", left: "center", top: "middle", style: { text: message, fill: reportChartPalette.muted, fontSize: 12, fontWeight: 600 } } });
}

export const maintenancePageTestUtils = {
  destinationChangeTrendOption,
  destinationOutcomeOption,
  maintenanceReclaimTrendOption,
  maintenanceStatusTrendOption: destinationStatusTrendOption,
};

function maintenanceReclaimTrendOption(report: MonitoringReport, filters: MonitoringFilters, timezoneName: string, metric: TrendMetric = "files") {
  return destinationChangeTrendOption(report, filters, timezoneName, metric);
}
