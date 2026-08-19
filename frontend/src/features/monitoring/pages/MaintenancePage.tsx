import { useEffect, useMemo, useState } from "react";
import type { EChartsOption } from "echarts";
import type { MonitoringRecord, MonitoringReport } from "../../../shared/api/domainTypes";
import { LineageFormatIcon } from "../../lineage/components/LineageFormatIcon";
import { formatTimestampForDisplay } from "../../../shared/time";
import { formatMaintenanceLag, maintenanceFormatIconKind, maintenanceTableHealthClass, maintenanceTableHealthLabel } from "../maintenancePresentation";
import type { MonitoringFilters } from "../monitoringFilters";
import type { TableSort } from "../MonitoringCharts";import { CompactNumberValue, DataTable, DetailMetric, HealthStripCard, ReportChart, ReportPanel, TableDateTimeValue, TablePager, baseChartOption, bottomAnchoredValueXAxis, formatBytes, formatBytesShort, formatCompact, formatNumber, formatPercent, formatSeconds, horizontalBarDataZoom, monitoringTimezone, normalizeTrendBucketKey, num, reportChartPalette, reportChartGrid, resolveTrendBucketKeys } from "../components/monitoringPrimitives";

const MAINTENANCE_STATUSES = ["succeeded", "failed", "skipped", "running", "pending", "unknown"] as const;
type EfficiencyScaleMode = "linear" | "log";

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
  onInspect
}: {
  report: MonitoringReport;
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
}) {
  const kpis = report.maintenance.kpis ?? {};
  const timezoneName = monitoringTimezone(report);
  const tableRows = rows;
  const outcomeRows = (report.maintenance.table_outcome ?? []) as MonitoringRecord[];
  const efficiencyRows = useMemo(
    () => (report.maintenance.table_efficiency_points ?? []) as Array<Record<string, unknown>>,
    [report.maintenance.table_efficiency_points]
  );
  const [efficiencyScale, setEfficiencyScale] = useState<EfficiencyScaleMode>(() => defaultMaintenanceEfficiencyScale(efficiencyRows));

  useEffect(() => {
    setEfficiencyScale(defaultMaintenanceEfficiencyScale(efficiencyRows));
  }, [efficiencyRows]);

  const health = String(kpis.health_status ?? "no_evidence");
  const bytesReclaimed = Number(kpis.bytes_reclaimed ?? 0);
  const durationSeconds = Number(kpis.duration_seconds ?? 0);
  const coverageRate = Number(kpis.coverage_rate ?? 0);
  const filesRemoved = Number(kpis.files_removed ?? 0);
  const noOpRuntimeShare = Number(kpis.no_op_runtime_share ?? 0);
  const laggedTables = Number(kpis.lagged_tables ?? 0);
  const latestFailedTables = Number(kpis.latest_failed_tables ?? 0);
  const lagWarningDays = Number(kpis.maintenance_lag_warning_days ?? 7);

  return (
    <div className="monitoring-page monitoring-maintenance-report">
      <section className="overview-health-strip monitoring-maintenance-health-strip">
        <HealthStripCard
          label="Maintenance health"
          value={maintenanceHealthLabel(health)}
          detail={
            <span className="health-rate-detail">
              <DetailMetric label="failed" value={<CompactNumberValue value={latestFailedTables} />} tone={latestFailedTables ? "bad" : "neutral"} labelFirst />
              <span className="separator"> · </span>
              <DetailMetric label="missing" value={<CompactNumberValue value={Number(kpis.coverage_missing_tables ?? 0)} />} tone={Number(kpis.coverage_missing_tables ?? 0) ? "amber" : "neutral"} labelFirst />
              <span className="separator"> · </span>
              <DetailMetric label="lagged" value={<CompactNumberValue value={laggedTables} />} tone={laggedTables ? "amber" : "neutral"} labelFirst />
            </span>
          }
          intent={maintenanceHealthIntent(health)}
          accent="intent"
          className="maintenance-kpi maintenance-kpi-health"
          title={`Healthy when active lakehouse tables have maintenance coverage, no latest failed maintenance, and no maintenance lag over ${lagWarningDays} days. No-op maintenance is an optimization signal, not a health rule.`}
        />
        <HealthStripCard
          label="Maintenance runs"
          value={<CompactNumberValue value={Number(kpis.total_maintenance_runs ?? 0)} />}
          detail={
            <span className="health-rate-detail">
              <DetailMetric label="S" value={<CompactNumberValue value={Number(kpis.succeeded_ops ?? 0)} />} tone="good" labelFirst />
              <span className="separator"> · </span>
              <DetailMetric label="F" value={<CompactNumberValue value={Number(kpis.failed_ops ?? 0)} />} tone={Number(kpis.failed_ops ?? 0) ? "bad" : "neutral"} labelFirst />
              <span className="separator"> · </span>
              <DetailMetric label="Skip" value={<CompactNumberValue value={Number(kpis.skipped_ops ?? 0)} />} tone={Number(kpis.skipped_ops ?? 0) ? "amber" : "neutral"} labelFirst />
            </span>
          }
          intent={Number(kpis.failed_ops ?? 0) ? "bad" : Number(kpis.skipped_ops ?? 0) ? "warning" : "neutral"}
          accent="intent"
          className="maintenance-kpi maintenance-kpi-runs"
          title="Maintenance run counts are dataflow run records identified by operation_type=maintenance or compact/cleanup destination operation fallback."
        />
        <HealthStripCard
          label="Maintained tables"
          value={<><CompactNumberValue value={Number(kpis.maintained_tables ?? 0)} /> / <CompactNumberValue value={Number(kpis.active_lakehouse_tables ?? 0)} /></>}
          detail={<DetailMetric label="coverage" value={formatPercent(coverageRate)} tone={coverageRate >= 95 ? "good" : Number(kpis.active_lakehouse_tables ?? 0) ? "amber" : "neutral"} labelFirst />}
          intent={Number(kpis.coverage_missing_tables ?? 0) ? "warning" : Number(kpis.active_lakehouse_tables ?? 0) ? "good" : "neutral"}
          accent="intent"
          className="maintenance-kpi maintenance-kpi-coverage"
          title="Coverage = maintained active lakehouse tables / active lakehouse tables. Active lakehouse table means ETL wrote rows, files, or bytes to Delta/Iceberg-like destinations in current filters."
        />
        <HealthStripCard
          label="Tables with reclaim"
          value={<CompactNumberValue value={Number(kpis.tables_with_reclaim ?? 0)} />}
          detail={<DetailMetric label="no-op runtime" value={formatPercent(noOpRuntimeShare)} tone={noOpRuntimeShare ? "amber" : "neutral"} labelFirst />}
          intent="neutral"
          accent="storage"
          className="maintenance-kpi maintenance-kpi-reclaim-tables"
          title="Tables with reclaim have positive bytes removed or files removed. No-op runtime is the share of successful maintenance duration spent on runs that removed 0 bytes and 0 files."
        />
        <HealthStripCard
          label="Bytes reclaimed"
          value={formatBytes(bytesReclaimed)}
          detail={<DetailMetric label="saved" value={formatBytes(Number(kpis.bytes_saved ?? 0))} tone="blue" labelFirst />}
          intent="neutral"
          accent="storage"
          className="maintenance-kpi maintenance-kpi-bytes"
          title="Observed maintenance storage evidence: sum of destination_bytes_removed. Bytes saved uses destination_bytes_saved when available."
        />
        <HealthStripCard
          label="Files removed"
          value={<CompactNumberValue value={filesRemoved} />}
          detail={<DetailMetric label="avg bytes/file" value={formatBytes(Number(kpis.avg_bytes_per_file_removed ?? 0))} tone="neutral" labelFirst />}
          intent="neutral"
          accent="neutral"
          className="maintenance-kpi maintenance-kpi-files"
          title="Files removed is destination_files_removed. Average bytes per removed file = bytes reclaimed / files removed."
        />
      </section>

      <div className="monitoring-maintenance-content report-layout-table-heavy-3">
        <section className="monitoring-maintenance-primary-grid">
          <ReportPanel
            title="Maintenance status trend"
            className="monitoring-maintenance-status-trend-panel"
            titleTooltip="Stacked maintenance run counts by time bucket. Empty buckets are filled with zero using the current time grain."
            headerAction={<MaintenanceStatusLegend />}
          >
            <ReportChart
              option={maintenanceStatusTrendOption(report, filters, timezoneName)}
              height="100%"
            />
          </ReportPanel>
          <ReportPanel
            title="Storage reclaimed trend"
            className="monitoring-maintenance-reclaim-trend-panel"
            titleTooltip="Bars show observed bytes reclaimed. Line shows files removed. Tooltips separate bytes and files."
            headerAction={<MaintenanceReclaimLegend />}
          >
            <ReportChart
              option={maintenanceReclaimTrendOption(report, filters, timezoneName)}
              height="100%"
            />
          </ReportPanel>
        </section>

        <section className="monitoring-maintenance-secondary-grid">
          <ReportPanel
            title="Reclaim by destination table"
            titleTooltip="One row per destination target, sorted by attention priority, reclaimed bytes, then latest evidence."
          >
            <ReportChart
              option={maintenanceTableOutcomeOption(outcomeRows)}
              height="100%"
              wheelDataZoomStep={1}
            />
          </ReportPanel>
          <ReportPanel
            title="Table efficiency map"
            className="monitoring-maintenance-efficiency-panel"
            titleTooltip="Each point is a destination table. X = bytes reclaimed, Y = total maintenance duration, size = maintenance run count, color = table health. Log scale keeps zero, small, and large reclaim values visible together; tooltips always show raw values."
            headerAction={
              <div className="monitoring-maintenance-efficiency-actions">
                <MaintenanceHealthLegend />
                <MaintenanceEfficiencyScaleToggle value={efficiencyScale} onChange={setEfficiencyScale} />
              </div>
            }
          >
            <ReportChart
              option={maintenanceEfficiencyOption(efficiencyRows, efficiencyScale)}
              height="100%"
            />
          </ReportPanel>
        </section>

        <ReportPanel
          title="Destination table maintenance registry"
          subtitle={<><CompactNumberValue value={totalRows} /> destinations · attention first</>}
          className="monitoring-maintenance-runs-panel"
          titleTooltip="One row per destination table or destination asset. Dataflow maintenance runs are evidence behind the table-level health."
          headerAction={
            <TablePager
              limit={limit}
              offset={offset}
              loadedRows={tableRows.length}
              totalRows={totalRows}
              loading={loading}
              onPageChange={onPageChange}
              onPageSizeChange={onPageSizeChange}
            />
          }
        >
          <MaintenanceRegistryTable rows={tableRows} sort={sort} onSort={onSort} timezoneName={timezoneName} onInspect={onInspect} />
        </ReportPanel>
      </div>
    </div>
  );
}

function MaintenanceStatusLegend() {
  return <MaintenanceChartLegend label="Maintenance status legend" items={MAINTENANCE_STATUSES.map((status) => [statusLabel(status), statusColor(status)] as const)} />;
}

function MaintenanceReclaimLegend() {
  return <MaintenanceChartLegend label="Storage reclaimed legend" items={[
    ["Bytes reclaimed", reportChartPalette.teal],
    ["Files removed", reportChartPalette.blue],
  ]} />;
}

function MaintenanceHealthLegend() {
  return <MaintenanceChartLegend label="Table health legend" items={[
    ["Healthy", maintenanceTableHealthColor("healthy")],
    ["Warning", maintenanceTableHealthColor("warning")],
    ["Issues", maintenanceTableHealthColor("has_issues")],
    ["No evidence", maintenanceTableHealthColor("no_evidence")],
  ]} />;
}

function MaintenanceChartLegend({ label, items }: { label: string; items: ReadonlyArray<readonly [string, string]> }) {
  return (
    <div className="monitoring-maintenance-chart-legend" aria-label={label}>
      {items.map(([itemLabel, color]) => (
        <span key={itemLabel}><i style={{ backgroundColor: color }} aria-hidden="true" />{itemLabel}</span>
      ))}
    </div>
  );
}

function MaintenanceEfficiencyScaleToggle({
  value,
  onChange
}: {
  value: EfficiencyScaleMode;
  onChange: (value: EfficiencyScaleMode) => void;
}) {
  return (
    <div
      className="segmented-control performance-scale-toggle"
      role="group"
      aria-label="Table efficiency scale"
      title="Linear shows raw axis spacing. Log keeps zero, small, and very large reclaimed values visible together; tooltip values remain raw."
    >
      <button
        type="button"
        className={value === "linear" ? "active" : ""}
        aria-pressed={value === "linear"}
        onClick={() => onChange("linear")}
      >
        Linear
      </button>
      <button
        type="button"
        className={value === "log" ? "active" : ""}
        aria-pressed={value === "log"}
        onClick={() => onChange("log")}
      >
        Log
      </button>
    </div>
  );
}

function MaintenanceRegistryTable({
  rows,
  sort,
  onSort,
  timezoneName,
  onInspect
}: {
  rows: MonitoringRecord[];
  sort: TableSort;
  onSort: (sort: TableSort) => void;
  timezoneName?: string | null;
  onInspect?: (row: MonitoringRecord) => void;
}) {
  return (
    <DataTable<MonitoringRecord>
      rows={rows}
      compactNumbers
      columns={[
        { key: "target", label: "Destination table", sortable: true, minWidth: 180, fillPriority: "last", render: (row) => <MaintenanceTargetCell row={row} /> },
        { key: "table_health", label: "Health", sortable: true, autoFit: true, minWidth: 86, maxWidth: 112, render: (row) => <MaintenanceTableHealthCell row={row} />, measureValue: (row) => maintenanceTableHealthLabel(String(row.table_health ?? row.status ?? "unknown")) },
        { key: "latest_maintenance_time", label: "Latest maintenance", sortable: true, autoFit: true, minWidth: 144, maxWidth: 190, render: (row) => <TableDateTimeValue value={row.latest_maintenance_time} timezoneName={timezoneName} />, measureValue: (row, activeTimezone) => formatTimestampForDisplay(row.latest_maintenance_time, activeTimezone, "-") },
        { key: "latest_etl_write_time", label: "Latest ETL", sortable: true, autoFit: true, minWidth: 144, maxWidth: 190, render: (row) => <TableDateTimeValue value={row.latest_etl_write_time} timezoneName={timezoneName} />, measureValue: (row, activeTimezone) => formatTimestampForDisplay(row.latest_etl_write_time, activeTimezone, "-") },
        { key: "maintenance_lag_seconds", label: "Lag", sortable: true, autoFit: true, minWidth: 64, maxWidth: 88, render: (row) => <MaintenanceLagCell row={row} />, measureValue: (row) => formatMaintenanceLag(num(row, "maintenance_lag_seconds")) },
        { key: "bytes_reclaimed", label: "Reclaim", sortable: true, autoFit: true, minWidth: 82, maxWidth: 124, render: (row) => <MaintenanceReclaimCell row={row} />, measureValue: (row) => maintenanceReclaimLines(row) },
        { key: "bytes_reclaimed_per_second", label: "Efficiency", sortable: true, autoFit: true, minWidth: 86, maxWidth: 132, render: (row) => <MaintenanceEfficiencyCell row={row} />, measureValue: (row) => maintenanceEfficiencyLines(row) },
        { key: "no_op_runs", label: "No-op", sortable: true, autoFit: true, minWidth: 68, maxWidth: 108, render: (row) => <MaintenanceNoOpCell row={row} />, measureValue: (row) => maintenanceNoOpLines(row) },
        { key: "run_count", label: "Runs", sortable: true, autoFit: true, minWidth: 72, maxWidth: 108, render: (row) => <MaintenanceRunsCell row={row} />, measureValue: (row) => maintenanceRunLines(row) },
        { key: "attention_reason", label: "Attention", minWidth: 140, fillPriority: "last", render: (row) => <MaintenanceReasonCell row={row} /> }
      ]}
      maxRows={rows.length}
      sort={sort}
      onSort={onSort}
      onRowClick={onInspect}
      timezoneName={timezoneName}
      className="monitoring-maintenance-table"
      fixedLayout
    />
  );
}

function MaintenanceTargetCell({ row }: { row: MonitoringRecord }) {
  const targetIdentity = String(row.target ?? row.table ?? row.destination_table ?? row.destination_path ?? "unknown");
  const target = String(row.target_display ?? targetIdentity);
  const format = String(row.destination_format ?? row.format ?? row.destination_connection_type ?? "table");
  const meta = [row.destination_name ?? row.destination_connection_name, row.destination_connection_type].filter(Boolean).join(" · ");
  return (
    <span className="monitor-endpoint-cell" title={`Target: ${targetIdentity}\n${meta}`}>
      <span className="monitor-endpoint-icon">
        <LineageFormatIcon kind={maintenanceFormatIconKind(format)} label={format} size={18} />
      </span>
      <span className="monitor-endpoint-text">
        <strong>{target}</strong>
        <small>{meta || "destination asset"}</small>
      </span>
    </span>
  );
}

function MaintenanceTableHealthCell({ row }: { row: MonitoringRecord }) {
  const health = String(row.table_health ?? row.status ?? "unknown");
  const reason = String(row.attention_reason ?? "");
  return (
    <span className={`maintenance-table-health-chip ${maintenanceTableHealthClass(health)}`} title={reason || health}>
      {maintenanceTableHealthLabel(health)}
    </span>
  );
}

function MaintenanceReclaimCell({ row }: { row: MonitoringRecord }) {
  const bytes = num(row, "bytes_reclaimed") || num(row, "maintenance_bytes_reclaimed") || num(row, "destination_bytes_removed");
  const files = num(row, "files_removed") || num(row, "maintenance_files_removed") || num(row, "destination_files_removed");
  return (
    <span className="monitor-stack-cell maintenance-reclaim-cell" title={`Bytes reclaimed: ${formatBytes(bytes)}\nFiles removed: ${formatNumber(files)}`}>
      <strong>{formatBytes(bytes)}</strong>
      <small><CompactNumberValue value={files} /> files</small>
    </span>
  );
}

function maintenanceReclaimLines(row: MonitoringRecord): [string, string] {
  const bytes = num(row, "bytes_reclaimed") || num(row, "maintenance_bytes_reclaimed") || num(row, "destination_bytes_removed");
  const files = num(row, "files_removed") || num(row, "maintenance_files_removed") || num(row, "destination_files_removed");
  return [formatBytes(bytes), `${formatCompact(files)} files`];
}

function MaintenanceEfficiencyCell({ row }: { row: MonitoringRecord }) {
  const [rateLabel, durationLabel] = maintenanceEfficiencyLines(row);
  return (
    <span className="monitor-stack-cell" title={`Bytes/sec: ${rateLabel}\nDuration: ${durationLabel}`}>
      <strong>{rateLabel}</strong>
      <small>{durationLabel}</small>
    </span>
  );
}

function maintenanceEfficiencyLines(row: MonitoringRecord): [string, string] {
  const rate = num(row, "bytes_reclaimed_per_second") || num(row, "maintenance_bytes_per_second");
  return [`${formatBytes(rate)}/s`, formatSeconds(num(row, "duration_seconds"))];
}

function MaintenanceNoOpCell({ row }: { row: MonitoringRecord }) {
  const runs = num(row, "no_op_runs");
  return (
    <span className={`monitor-stack-cell maintenance-no-op-cell${runs > 0 ? " has-no-op" : ""}`} title={`No-op runs: ${formatNumber(runs)}\nNo-op duration: ${formatSeconds(num(row, "no_op_duration_seconds"))}\nDefinition: succeeded maintenance runs with 0 bytes removed and 0 files removed.`}>
      <strong><CompactNumberValue value={runs} /></strong>
      <small>{formatSeconds(num(row, "no_op_duration_seconds"))}</small>
    </span>
  );
}

function maintenanceNoOpLines(row: MonitoringRecord): [string, string] {
  return [formatCompact(num(row, "no_op_runs")), formatSeconds(num(row, "no_op_duration_seconds"))];
}

function MaintenanceLagCell({ row }: { row: MonitoringRecord }) {
  const lag = num(row, "maintenance_lag_seconds");
  const warning = Boolean(row.maintenance_lag_warning);
  const thresholdDays = Number(row.maintenance_lag_warning_days ?? 7);
  return (
    <span
      className={warning ? "performance-reason performance-reason-warning" : ""}
      title={`Lag = latest ETL write time minus latest maintenance time. Warning only when lag is greater than ${thresholdDays} days.`}
    >
      {formatMaintenanceLag(lag)}
    </span>
  );
}

function MaintenanceRunsCell({ row }: { row: MonitoringRecord }) {
  const runCount = num(row, "run_count");
  const succeeded = num(row, "succeeded");
  const failed = num(row, "failed");
  const skipped = num(row, "skipped");
  return (
    <span className="monitor-stack-cell" title={`Runs: ${formatNumber(num(row, "run_count"))}\nSucceeded / failed / skipped: ${formatNumber(num(row, "succeeded"))} / ${formatNumber(num(row, "failed"))} / ${formatNumber(num(row, "skipped"))}\nRunning / pending / unknown: ${formatNumber(num(row, "running"))} / ${formatNumber(num(row, "pending"))} / ${formatNumber(num(row, "unknown"))}`}>
      <strong><CompactNumberValue value={runCount} /></strong>
      <small>
        <span style={{ color: reportChartPalette.success }}><CompactNumberValue value={succeeded} /></span>
        {" / "}
        <span style={{ color: failed ? reportChartPalette.failed : undefined }}><CompactNumberValue value={failed} /></span>
        {" / "}
        <span style={{ color: skipped ? reportChartPalette.skipped : undefined }}><CompactNumberValue value={skipped} /></span>
      </small>
    </span>
  );
}

function maintenanceRunLines(row: MonitoringRecord): [string, string] {
  return [
    formatCompact(num(row, "run_count")),
    `${formatCompact(num(row, "succeeded"))} / ${formatCompact(num(row, "failed"))} / ${formatCompact(num(row, "skipped"))}`,
  ];
}

function MaintenanceReasonCell({ row }: { row: MonitoringRecord }) {
  const reason = String(row.attention_reason ?? row.maintenance_candidate_reason ?? "");
  const health = String(row.table_health ?? "");
  const priority = num(row, "attention_priority");
  if (!reason || (health === "healthy" && priority <= 0)) return <span className="performance-reason" title="No maintenance attention rule matched.">-</span>;
  return <span className={`performance-reason ${maintenanceReasonClass(row)}`} title={reason}>{reason}</span>;
}

function maintenanceStatusTrendOption(report: MonitoringReport, filters: MonitoringFilters, timezoneName: string): EChartsOption {
  const visible = maintenanceTrendRows(report.maintenance.status_by_date ?? [], filters, report, timezoneName, createEmptyMaintenanceStatusRow);
  if (!visible.length) return emptyChartOption("No maintenance status trend in current filters.");
  return baseChartOption({
    tooltip: {
      trigger: "axis",
      formatter: (params: any) => {
        const first = Array.isArray(params) ? params[0] : params;
        const row = visible[Number(first?.dataIndex ?? 0)] ?? {};
        return [
          `<strong>${row.bucket || row.date || ""}</strong>`,
          row.grain ? `Grain: ${row.grain}` : "",
          timezoneName ? `Timezone: ${timezoneName}` : "",
          `Total: ${formatNumber(num(row, "total"))}`,
          `Succeeded: ${formatNumber(num(row, "succeeded"))}`,
          `Failed: ${formatNumber(num(row, "failed"))}`,
          `Skipped: ${formatNumber(num(row, "skipped"))}`,
          `Running / pending / unknown: ${formatNumber(num(row, "running"))} / ${formatNumber(num(row, "pending"))} / ${formatNumber(num(row, "unknown"))}`,
          `Success rate: ${formatPercent(num(row, "success_rate"))}`
        ].filter(Boolean).join("<br/>");
      }
    },
    grid: reportChartGrid({ left: 34, right: 10, top: 8, containLabel: false }),
    xAxis: maintenanceBottomCategoryXAxis(visible.map((row) => String(row.bucket || row.date || ""))),
    yAxis: { type: "value", axisLabel: { fontSize: 10, formatter: (value: number) => formatCompact(value) }, splitLine: { lineStyle: { color: reportChartPalette.grid } } },
    series: MAINTENANCE_STATUSES.map((status) => ({
      name: statusLabel(status),
      type: "bar" as const,
      stack: "status",
      itemStyle: { color: statusColor(status), borderRadius: status === "unknown" ? [3, 3, 0, 0] : 0 },
      data: visible.map((row) => num(row, status))
    }))
  });
}

function maintenanceReclaimTrendOption(report: MonitoringReport, filters: MonitoringFilters, timezoneName: string): EChartsOption {
  const visible = maintenanceTrendRows(report.maintenance.reclaim_by_date ?? report.maintenance.bytes_reclaimed_by_date ?? [], filters, report, timezoneName, createEmptyMaintenanceReclaimRow);
  if (!visible.length) return emptyChartOption("No maintenance reclaim trend in current filters.");
  return baseChartOption({
    tooltip: {
      trigger: "axis",
      formatter: (params: any) => {
        const first = Array.isArray(params) ? params[0] : params;
        const row = visible[Number(first?.dataIndex ?? 0)] ?? {};
        return [
          `<strong>${row.bucket || row.date || ""}</strong>`,
          row.grain ? `Grain: ${row.grain}` : "",
          timezoneName ? `Timezone: ${timezoneName}` : "",
          `Bytes reclaimed: ${formatBytes(num(row, "bytes_reclaimed"))}`,
          `Bytes saved: ${formatBytes(num(row, "bytes_saved"))}`,
          `Files removed: ${formatNumber(num(row, "files_removed"))}`,
          `Runs: ${formatNumber(num(row, "runs"))}`
        ].filter(Boolean).join("<br/>");
      }
    },
    grid: reportChartGrid({ left: 42, right: 42, top: 8, containLabel: false }),
    xAxis: maintenanceBottomCategoryXAxis(visible.map((row) => String(row.bucket || row.date || ""))),
    yAxis: [
      { type: "value", min: 0, axisLabel: { fontSize: 10, formatter: (value: number) => formatBytesShort(value) }, splitLine: { lineStyle: { color: reportChartPalette.grid } } },
      { type: "value", min: 0, axisLabel: { fontSize: 10, formatter: (value: number) => formatCompact(value) }, splitLine: { show: false } }
    ],
    series: [
      { name: "Bytes reclaimed", type: "bar", itemStyle: { color: reportChartPalette.teal, borderRadius: [3, 3, 0, 0] }, data: visible.map((row) => num(row, "bytes_reclaimed")) },
      lineSeries("Files removed", visible.map((row) => num(row, "files_removed")), reportChartPalette.blue, 1)
    ]
  });
}

function maintenanceBottomCategoryXAxis(data: string[]) {
  return {
    type: "category" as const,
    position: "bottom" as const,
    data,
    axisLine: { onZero: false },
    axisLabel: { fontSize: 10, hideOverlap: true },
    axisTick: { show: false },
  };
}

function maintenanceEfficiencyOption(rows: Array<Record<string, unknown>>, scaleMode: EfficiencyScaleMode): EChartsOption {
  const visible = rows.slice(0, 800);
  if (!visible.length) return emptyChartOption("No table maintenance efficiency signals.");
  return baseChartOption({
    grid: reportChartGrid({ left: 42, right: 8, top: 8, containLabel: false }),
    tooltip: {
      trigger: "item",
      formatter: (params: any) => {
        const row = visible[Number(params?.dataIndex ?? 0)] ?? {};
        return [
          `<strong>${row.target_display ?? row.target ?? row.table ?? "unknown"}</strong>`,
          `Health: ${maintenanceTableHealthLabel(String(row.table_health ?? "unknown"))}`,
          `Format/status: ${row.destination_format ?? row.format ?? "-"} · ${row.latest_status ?? row.status ?? "-"}`,
          `Runs: ${formatNumber(num(row, "run_count"))}`,
          `Duration: ${formatSeconds(num(row, "duration_seconds"))}`,
          `Bytes reclaimed: ${formatBytes(num(row, "bytes_reclaimed") || num(row, "bytes_removed"))}`,
          `Files removed: ${formatNumber(num(row, "files_removed"))}`,
          `Efficiency: ${formatBytes(num(row, "bytes_reclaimed_per_second"))}/s`,
          row.attention_reason ? `Reason: ${row.attention_reason}` : ""
        ].filter(Boolean).join("<br/>");
      }
    },
    xAxis: {
      type: "value",
      scale: true,
      axisLabel: { fontSize: 10, formatter: (value: number) => formatBytesShort(rawMaintenanceAxisValue(value, scaleMode)) },
      splitLine: { lineStyle: { color: reportChartPalette.grid } }
    },
    yAxis: {
      type: "value",
      scale: true,
      axisLabel: { fontSize: 10, formatter: (value: number) => formatSeconds(rawMaintenanceAxisValue(value, scaleMode)) },
      splitLine: { lineStyle: { color: reportChartPalette.grid } }
    },
    series: [
      {
        name: "Maintenance runs",
        type: "scatter",
        symbolSize: (_value: unknown, params: any) => {
          const row = visible[Number(params?.dataIndex ?? 0)] ?? {};
          return Math.max(6, Math.min(20, 6 + Math.sqrt(num(row, "run_count"))));
        },
        itemStyle: {
          color: (params: any) => maintenanceTableHealthColor(String(visible[Number(params?.dataIndex ?? 0)]?.table_health ?? "unknown")),
          opacity: 0.78,
          borderColor: "#ffffff",
          borderWidth: 1
        },
        data: visible.map((row) => [
          maintenanceAxisValue(num(row, "bytes_reclaimed") || num(row, "bytes_removed"), scaleMode),
          maintenanceAxisValue(num(row, "duration_seconds"), scaleMode)
        ])
      }
    ]
  });
}

function defaultMaintenanceEfficiencyScale(rows: Array<Record<string, unknown>>): EfficiencyScaleMode {
  const values = rows
    .map((row) => num(row, "bytes_reclaimed") || num(row, "bytes_removed"))
    .filter((value) => value > 0);
  if (values.length < 2) return "linear";
  const p50 = percentileFromNumbers(values, 0.5);
  const max = Math.max(...values);
  return p50 > 0 && max / p50 >= 1000 ? "log" : "linear";
}

function maintenanceAxisValue(value: number, scaleMode: EfficiencyScaleMode) {
  if (scaleMode === "linear") return value;
  return Math.log10(Math.max(0, value) + 1);
}

function rawMaintenanceAxisValue(value: number, scaleMode: EfficiencyScaleMode) {
  if (scaleMode === "linear") return value;
  return Math.max(0, Math.pow(10, value) - 1);
}

function percentileFromNumbers(values: number[], percentile: number) {
  if (!values.length) return 0;
  const sorted = values.slice().sort((left, right) => left - right);
  const index = (sorted.length - 1) * percentile;
  const lower = Math.floor(index);
  const upper = Math.ceil(index);
  if (lower === upper) return sorted[lower] ?? 0;
  const weight = index - lower;
  return (sorted[lower] ?? 0) * (1 - weight) + (sorted[upper] ?? 0) * weight;
}

function maintenanceTableOutcomeOption(rows: Array<Record<string, unknown>>): EChartsOption {
  const visible = rows.slice().sort((left, right) => {
    const priority = num(right, "attention_priority") - num(left, "attention_priority");
    if (priority !== 0) return priority;
    const reclaim = num(right, "bytes_reclaimed") - num(left, "bytes_reclaimed");
    if (reclaim !== 0) return reclaim;
    return String(left.target ?? "").localeCompare(String(right.target ?? ""));
  });
  if (!visible.length) return emptyChartOption("No destination table outcomes.");
  const labels = visible.map((row) => String(row.target_display ?? row.target ?? row.table ?? row.destination_table ?? "unknown"));
  const dataZoom = horizontalBarDataZoom(visible.length);
  return baseChartOption({
    tooltip: {
      trigger: "item",
      formatter: (params: any) => {
        const row = visible[Number(params?.dataIndex ?? 0)] ?? {};
        return [
          `<strong>${labels[Number(params?.dataIndex ?? 0)] ?? "unknown"}</strong>`,
          `Runs: ${formatNumber(num(row, "run_count") || num(row, "count"))}`,
          `Health: ${maintenanceTableHealthLabel(String(row.table_health ?? "unknown"))}`,
          `Latest status: ${row.latest_status ?? row.status ?? "-"}`,
          `Format: ${row.format ?? row.destination_format ?? "-"}`,
          `Bytes reclaimed: ${formatBytes(num(row, "bytes_reclaimed") || num(row, "bytes_removed"))}`,
          `Files removed: ${formatNumber(num(row, "files_removed"))}`,
          `Duration: ${formatSeconds(num(row, "duration_seconds"))}`,
          `No-op runs: ${formatNumber(num(row, "no_op_runs"))}`,
          `No-op duration: ${formatSeconds(num(row, "no_op_duration_seconds"))}`,
          row.attention_reason ? `Reason: ${row.attention_reason}` : ""
        ].join("<br/>");
      }
    },
    grid: reportChartGrid({ left: 236, right: dataZoom ? 24 : 10, top: 8, containLabel: false }),
    xAxis: bottomAnchoredValueXAxis({ formatter: (value) => formatBytesShort(value) }),
    yAxis: {
      type: "category",
      data: labels,
      inverse: true,
      axisTick: { show: false },
      axisLabel: { align: "right", fontSize: 10, width: 222, overflow: "truncate", margin: 8, color: reportChartPalette.muted }
    },
    dataZoom,
    series: [
      {
        name: "Bytes reclaimed",
        type: "bar",
        barMaxWidth: 18,
        itemStyle: {
          color: (params: any) => {
            const row = visible[Number(params?.dataIndex ?? 0)] ?? {};
            if (num(row, "bytes_reclaimed") || num(row, "bytes_removed")) return reportChartPalette.teal;
            return maintenanceTableHealthColor(String(row.table_health ?? "unknown"));
          },
          borderRadius: [0, 3, 3, 0]
        },
        label: {
          show: true,
          position: "right",
          color: reportChartPalette.text,
          fontSize: 9,
          formatter: (params: any) => {
            const value = Number(params?.value ?? 0);
            return value > 0 ? formatBytesShort(value) : "";
          }
        },
        data: visible.map((row) => num(row, "bytes_reclaimed") || num(row, "bytes_removed"))
      }
    ]
  });
}

function maintenanceTrendRows(
  rows: Array<Record<string, string | number | null>>,
  filters: MonitoringFilters,
  report: MonitoringReport,
  timezoneName: string,
  createEmpty: (bucket: string, grain: string) => Record<string, string | number | null>
): Array<Record<string, string | number | null>> {
  const effectiveGrain = String(rows.find((row) => row.grain)?.grain ?? report.summary.effective_grain ?? filters.grain ?? "day");
  const normalizedRows = rows.map((row) => {
    const bucket = normalizeTrendBucketKey(row.bucket ?? row.date, effectiveGrain, timezoneName);
    return { ...row, bucket, date: bucket };
  });
  const knownDateKeys = normalizedRows.map((row) => String(row.bucket ?? "")).filter((date) => date && date !== "unknown");
  const dateKeys = resolveTrendBucketKeys(filters, report.summary.date_range, timezoneName, knownDateKeys, effectiveGrain);
  const byKey = new Map(normalizedRows.map((row) => [String(row.bucket), row]));
  const visible = dateKeys.length > 0
    ? dateKeys.map((dateKey) => ({ ...createEmpty(dateKey, effectiveGrain), ...(byKey.get(dateKey) ?? {}) }))
    : normalizedRows;
  return visible.map((row) => ({
    ...row,
    total: num(row, "total") || MAINTENANCE_STATUSES.reduce((sum, status) => sum + num(row, status), 0),
    bytes_reclaimed: num(row, "bytes_reclaimed"),
    bytes_saved: num(row, "bytes_saved"),
    files_removed: num(row, "files_removed"),
    runs: num(row, "runs")
  })) as Array<Record<string, string | number | null>>;
}

function createEmptyMaintenanceStatusRow(bucket: string, grain: string) {
  return {
    date: bucket,
    bucket,
    grain,
    succeeded: 0,
    failed: 0,
    skipped: 0,
    running: 0,
    pending: 0,
    unknown: 0,
    total: 0,
    success_rate: 0
  };
}

function createEmptyMaintenanceReclaimRow(bucket: string, grain: string) {
  return {
    date: bucket,
    bucket,
    grain,
    bytes_reclaimed: 0,
    bytes_saved: 0,
    files_removed: 0,
    runs: 0
  };
}

function maintenanceHealthLabel(value: string) {
  if (value === "has_issues") return "Has issues";
  if (value === "no_evidence") return "No evidence";
  if (value === "warning") return "Warning";
  if (value === "healthy") return "Healthy";
  return value || "-";
}

function maintenanceHealthIntent(value: string): "neutral" | "bad" | "good" | "warning" {
  if (value === "has_issues") return "bad";
  if (value === "warning") return "warning";
  if (value === "healthy") return "good";
  return "neutral";
}

function maintenanceTableHealthColor(value: string) {
  if (value === "has_issues") return reportChartPalette.failed;
  if (value === "warning" || value === "missing") return reportChartPalette.amber;
  if (value === "healthy") return reportChartPalette.success;
  return reportChartPalette.unknown;
}

function maintenanceReasonClass(row: MonitoringRecord) {
  const health = String(row.table_health ?? "");
  if (health === "has_issues") return "performance-reason-bad";
  if (health === "warning") return "performance-reason-warning";
  const kind = String(row.maintenance_candidate_kind ?? "");
  if (kind === "failed") return "performance-reason-bad";
  if (kind === "skipped" || kind === "no_op" || kind === "high_duration") return "performance-reason-warning";
  return "";
}

function statusLabel(status: string) {
  if (status === "succeeded") return "Succeeded";
  if (status === "failed") return "Failed";
  if (status === "skipped") return "Skipped";
  if (status === "running") return "Running";
  if (status === "pending") return "Pending";
  return "Unknown";
}

function statusColor(status: string) {
  const normalized = status.toLowerCase();
  if (normalized === "succeeded") return reportChartPalette.success;
  if (normalized === "failed") return reportChartPalette.failed;
  if (normalized === "skipped") return reportChartPalette.skipped;
  if (normalized === "running") return reportChartPalette.running;
  if (normalized === "pending") return reportChartPalette.pending;
  return reportChartPalette.unknown;
}

function lineSeries(name: string, data: number[], color: string, yAxisIndex = 0) {
  return {
    name,
    type: "line" as const,
    yAxisIndex,
    connectNulls: true,
    showSymbol: false,
    showAllSymbol: false,
    symbol: "circle",
    symbolSize: 5,
    clip: false,
    z: 6,
    smooth: 0.2,
    lineStyle: { width: 1.5, color },
    itemStyle: { color: "#ffffff", borderColor: color, borderWidth: 1.3 },
    data
  };
}

function emptyChartOption(message: string): EChartsOption {
  return baseChartOption({
    graphic: {
      type: "text",
      left: "center",
      top: "middle",
      style: { text: message, fill: reportChartPalette.muted, fontSize: 12, fontWeight: 600 }
    }
  });
}

export const maintenancePageTestUtils = {
  maintenanceReclaimTrendOption,
  maintenanceStatusTrendOption,
};
