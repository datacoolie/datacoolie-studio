import type { EChartsOption } from "echarts";
import { useMemo } from "react";
import type { MonitoringRecord, MonitoringReport } from "../../../shared/api/domainTypes";
import type { TableColumn } from "../MonitoringCharts";
import type { MonitoringFilters } from "../monitoringFilters";import { DataTable, DetailMetric, EndpointCell, HealthStripCard, ReportChart, ReportPanel, TablePager, TableDateTimeValue, WatermarkBadge, baseChartOption, fixedHorizontalBarGrid, fixedHorizontalCategoryAxis, formatCompact, formatNumber, formatPercent, monitoringTimezone, horizontalBarDataZoom, horizontalBarSeriesSizing, reportChartPalette, reportChartGrid, resolveTrendBucketKeys, normalizeTrendBucketKey, normalizeTrendGrain, type TableSort } from "../components/monitoringPrimitives";

type FreshnessRow = Record<string, unknown>;

const STALE_THRESHOLD_DAYS = 7;
const WATERMARK_MOVEMENT_LEGEND = [
  ["Initialized", reportChartPalette.teal, "The first watermark value was recorded."],
  ["Advanced", reportChartPalette.success, "The watermark moved forward."],
  ["Unchanged", reportChartPalette.skipped, "The watermark did not move."],
  ["Incomplete", reportChartPalette.blue, "The run has incomplete watermark evidence."],
  ["Invalid", reportChartPalette.failed, "The watermark evidence could not be parsed."],
  ["Unknown", reportChartPalette.unknown, "The movement could not be determined."],
  ["Advanced %", reportChartPalette.blue, "Advanced runs divided by advanced plus unchanged runs."],
] as const;
const FRESHNESS_AGE_LEGEND = [
  ["Current ≤7d", reportChartPalette.unknown],
  ["Stale >7d", reportChartPalette.amber],
] as const;
const WATERMARK_COVERAGE_LEGEND = [
  ["Enabled", reportChartPalette.success],
  ["Not configured", reportChartPalette.unknown],
] as const;

export function FreshnessPage({
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
  onInspect?: (row: FreshnessRow) => void;
}) {
  const kpis = report.freshness.kpis;
  const timezoneName = monitoringTimezone(report);
  const registryRows = rows;
  const watermarkTrendRows = fillMissingWatermarkMovementTrendRows(
    report.freshness.watermark_movement_by_date ?? [],
    filters,
    report.summary.date_range,
    timezoneName,
    String(report.summary.effective_grain ?? filters.grain ?? "day")
  );
  const observedDataflows = Number(kpis.observed_dataflows ?? 0);
  const staleDataflows = Number(kpis.stale_dataflows ?? kpis.stale_candidates ?? 0);
  const advancedRuns = Number(kpis.watermark_advanced_runs ?? 0);
  const initializedRuns = Number(kpis.watermark_initialized_runs ?? 0);
  const unchangedRuns = Number(kpis.watermark_unchanged_runs ?? 0);
  const skippedStreakDataflows = Number(kpis.skipped_streak_dataflows ?? 0);
  const skippedStreakThreshold = Number(kpis.skipped_streak_threshold ?? 3);
  const staleThresholdDays = STALE_THRESHOLD_DAYS;
  const successfulRuns = Number(kpis.successful_runs ?? kpis.latest_successful_runs ?? 0);
  const failedRuns = Number(kpis.failed_runs ?? 0);
  const skippedRuns = Number(kpis.skipped_runs ?? kpis.skipped_no_new_data ?? 0);
  const latestStatusIssueDataflows = Number(kpis.latest_status_issue_dataflows ?? kpis.latest_failed_or_active_dataflows ?? 0);
  const latestWatermarkInvalidDataflows = Number(kpis.latest_watermark_invalid_dataflows ?? 0);
  const latestWatermarkIncompleteDataflows = Number(kpis.latest_watermark_incomplete_dataflows ?? 0);
  const latestWatermarkIssueDataflows = Number(kpis.latest_watermark_issue_dataflows ?? (latestWatermarkInvalidDataflows + latestWatermarkIncompleteDataflows));
  const healthIntent = !observedDataflows
    ? "bad"
    : staleDataflows || latestStatusIssueDataflows || latestWatermarkIssueDataflows
      ? "warning"
      : "good";
  const healthLabel = healthIntent === "bad" ? "No ETL evidence" : healthIntent === "warning" ? "Needs review" : "Current";
  const healthTitle = [
    "Freshness health:",
    "- No ETL evidence: no dataflow runs in current filters.",
    `- Needs review: stale > 0, run issue > 0, or wm issue > 0.`,
    `- Stale: latest succeeded/skipped run is older than ${staleThresholdDays} days.`,
    "- Run issue: latest run is not succeeded or skipped.",
    "- Wm issue: latest watermark is invalid or incomplete.",
    "- Consecutive skipped runs are diagnostic only; they do not change this health status."
  ].join("\n");
  return (
    <div className="monitoring-page monitoring-freshness-report">
      <section className="overview-health-strip monitoring-freshness-health-strip">
        <HealthStripCard
          label="Freshness health"
          value={healthLabel}
          detail={
            <span className="health-rate-detail">
              <DetailMetric label="stale" value={formatNumber(staleDataflows)} tone={staleDataflows ? "warning" : "neutral"} />
              <span className="separator"> · </span>
              <DetailMetric label="run issue" value={formatNumber(latestStatusIssueDataflows)} tone={latestStatusIssueDataflows ? "warning" : "neutral"} />
              <span className="separator"> · </span>
              <DetailMetric label="wm issue" value={formatNumber(latestWatermarkIssueDataflows)} tone={latestWatermarkIssueDataflows ? "warning" : "neutral"} />
            </span>
          }
          intent={healthIntent}
          accent="intent"
          title={healthTitle}
        />
        <HealthStripCard
          label="Observed dataflows"
          value={formatNumber(observedDataflows)}
          detail={
            <span className="health-rate-detail freshness-observed-runs-detail">
              <span className="health-detail-label">etl runs S/F/Skip</span>
              <span className="health-detail-value">
                <b className="detail-value-good">{formatNumber(successfulRuns)}</b>
                <span className="separator"> / </span>
                <b className="detail-value-bad">{formatNumber(failedRuns)}</b>
                <span className="separator"> / </span>
                <b className="detail-value-warning">{formatNumber(skippedRuns)}</b>
              </span>
            </span>
          }
          title="Observed dataflows = distinct dataflow_id values in ETL logs. S/F/Skip = succeeded, failed, skipped runs in current filters."
        />
        <HealthStripCard
          label="Stale dataflows"
          value={formatNumber(staleDataflows)}
          detail={<DetailMetric label="of dataflows" value={formatPercent(Number(kpis.stale_dataflow_rate ?? 0))} tone={staleDataflows ? "warning" : "neutral"} />}
          intent={staleDataflows ? "warning" : "neutral"}
          accent="intent"
          title={`Stale = latest succeeded/skipped run is older than ${staleThresholdDays} days. Counted by dataflow_id.`}
        />
        <HealthStripCard
          label="Check age"
          value={`P95 ${formatAgeSeconds(Number(kpis.p95_age_seconds ?? 0))}`}
          detail={
            <span className="health-rate-detail">
              <DetailMetric label="min" value={formatAgeSeconds(Number(kpis.min_age_seconds ?? 0))} tone="neutral" labelFirst />
              <span className="separator"> · </span>
              <DetailMetric label="P50" value={formatAgeSeconds(Number(kpis.p50_age_seconds ?? 0))} tone="blue" labelFirst />
              <span className="separator"> · </span>
              <DetailMetric label="max" value={formatAgeSeconds(Number(kpis.max_age_seconds ?? 0))} tone={Number(kpis.max_age_days ?? 0) > STALE_THRESHOLD_DAYS ? "warning" : "neutral"} labelFirst />
            </span>
          }
          intent={Number(kpis.p95_age_days ?? 0) > STALE_THRESHOLD_DAYS ? "warning" : "neutral"}
          accent="intent"
          title="Age is measured from the latest succeeded/skipped ETL run per dataflow."
        />
        <HealthStripCard
          label="Watermark coverage"
          value={formatPercent(Number(kpis.watermark_coverage_rate ?? 0))}
          detail={<DetailMetric label="enabled flows" value={formatNumber(Number(kpis.watermark_enabled_dataflows ?? 0))} tone="blue" />}
          intent={Number(kpis.watermark_coverage_rate ?? 0) > 0 ? "good" : "neutral"}
          accent={Number(kpis.watermark_coverage_rate ?? 0) > 0 ? "source" : "neutral"}
          title="Watermark coverage = dataflows with watermark config or watermark values / observed dataflows."
        />
        <HealthStripCard
          label="Watermark movement"
          value={`${formatPercent(Number(kpis.watermark_advanced_rate ?? 0))} advanced`}
          className="freshness-watermark-movement-card"
          detail={
            <span className="health-rate-detail">
              <DetailMetric label="adv" value={formatNumber(advancedRuns)} tone={advancedRuns ? "good" : "neutral"} />
              <span className="separator"> · </span>
              <DetailMetric label="unch" value={formatNumber(unchangedRuns)} tone={unchangedRuns ? "warning" : "neutral"} />
              <span className="separator"> · </span>
              <DetailMetric label="init" value={formatNumber(initializedRuns)} tone={initializedRuns ? "blue" : "neutral"} />
            </span>
          }
          intent={latestWatermarkIssueDataflows ? "warning" : "neutral"}
          accent="intent"
          title="Advanced rate and adv/unch/init count watermark-enabled ETL runs in the current filters. Advanced changed, unchanged did not change, and initialized has the first watermark. Freshness health evaluates the latest watermark state per dataflow."
        />
      </section>

      <div className="monitoring-freshness-content">
        <section className="monitoring-freshness-top-grid">
          <ReportPanel
            title="Oldest / stale dataflows"
            subtitle="latest check age"
            titleTooltip="Ranks dataflows by age of latest succeeded/skipped ETL run. Stale candidates are older than the configured threshold."
            headerAction={<FreshnessChartLegend label="Freshness age legend" items={FRESHNESS_AGE_LEGEND} />}
          >
            <ReportChart option={freshnessAgeByDataflowOption(report.freshness.age_by_dataflow ?? [])} height="100%" wheelDataZoomStep={1} />
          </ReportPanel>
          <ReportPanel
            title="Watermark movement trend"
            subtitle={`${report.summary.effective_grain ?? "day"} grain`}
            titleTooltip="Counts watermark-enabled ETL runs by committed movement state: advanced, initialized, unchanged, incomplete, invalid, and unknown. This is diagnostic history across the filter; Freshness health only uses the latest watermark state per dataflow."
            headerAction={<WatermarkMovementLegend />}
          >
            <ReportChart option={watermarkMovementTrendOption(watermarkTrendRows)} height="100%" />
          </ReportPanel>
        </section>

        <section className="monitoring-freshness-middle-grid">
          <ReportPanel
            title="Freshness age distribution"
            subtitle="dataflows by age band"
            titleTooltip="Buckets dataflow-level latest succeeded/skipped ETL age. Unknown means dataflow has no parsable latest check timestamp."
          >
            <ReportChart option={freshnessAgeDistributionOption(report.freshness.age_distribution ?? [])} height="100%" />
          </ReportPanel>
          <ReportPanel
            title="Watermark coverage by stage"
            titleTooltip="Stage-level dataflow coverage. A dataflow is enabled when at least one ETL row for that stage has watermark columns or watermark values. Not configured is neutral: freshness still uses latest succeeded/skipped run."
            headerAction={<FreshnessChartLegend label="Watermark coverage legend" items={WATERMARK_COVERAGE_LEGEND} />}
          >
            <ReportChart option={watermarkCoverageByStageOption(report.freshness.watermark_coverage_by_stage ?? [])} height="100%" wheelDataZoomStep={1} />
          </ReportPanel>
          <ReportPanel
            title="Consecutive skipped"
            subtitle="dataflows by streak"
            titleTooltip={`Groups dataflows by their current consecutive skipped-run streak. The diagnostic threshold is ${skippedStreakThreshold}+ runs. Skipped is not a failure and is not used by Freshness health; it usually means no new incremental data was available.`}
          >
            <ReportChart option={skippedStreakDistributionOption(report.freshness.skipped_streak_distribution ?? [])} height="100%" />
          </ReportPanel>
          <ReportPanel
            title="Watermark adjustments"
            subtitle="changed read boundary"
            titleTooltip="Adjustment is separate from watermark movement. Adjusted means source_watermark_effective differs from source_watermark_before."
          >
            <ReportChart option={watermarkAdjustmentOption(watermarkTrendRows)} height="100%" />
          </ReportPanel>
        </section>

        <ReportPanel
          title="Dataflow freshness registry"
          subtitle={`${formatNumber(totalRows)} dataflows`}
          titleTooltip="Dataflow-level registry used for investigation. Click a row to inspect latest master data, freshness evidence, and the related dataflow runs."
          className="monitoring-freshness-registry-panel"
          headerAction={
            <TablePager
              limit={limit}
              offset={offset}
              loadedRows={registryRows.length}
              totalRows={totalRows}
              loading={loading}
              onPageChange={onPageChange}
              onPageSizeChange={onPageSizeChange}
            />
          }
        >
          <DataflowFreshnessTable
            rows={registryRows}
            offset={0}
            limit={limit}
            sort={sort}
            onSort={onSort}
            onInspect={onInspect}
            timezoneName={timezoneName}
          />
        </ReportPanel>
      </div>
    </div>
  );
}

function DataflowFreshnessTable({
  rows,
  offset,
  limit,
  sort,
  onSort,
  onInspect,
  timezoneName
}: {
  rows: FreshnessRow[];
  offset: number;
  limit: number;
  sort: TableSort;
  onSort: (sort: TableSort) => void;
  onInspect?: (row: FreshnessRow) => void;
  timezoneName?: string | null;
}) {
  const columns = useMemo<TableColumn<FreshnessRow>[]>(() => [
    { key: "dataflow_name", label: "Dataflow", sortable: true, minWidth: 150, fillPriority: "normal", className: "freshness-registry-cell-single-line", render: (row) => <CompactDataflowCell row={row} /> },
    { key: "stage", label: "Stage", sortable: true, minWidth: 90, maxWidth: 150, fillPriority: "normal", className: "freshness-registry-cell-single-line" },
    { key: "source_name", label: "Source", sortable: true, minWidth: 150, maxWidth: 240, fillPriority: "normal", render: (row) => <EndpointCell row={row as MonitoringRecord} direction="source" /> },
    { key: "destination_name", label: "Destination", sortable: true, minWidth: 150, maxWidth: 240, fillPriority: "normal", render: (row) => <EndpointCell row={row as MonitoringRecord} direction="destination" /> },
    { key: "destination_load_type", label: "Load", sortable: true, autoFit: true, minWidth: 58, maxWidth: 112, className: "freshness-registry-cell-single-line", render: (row) => String(row.destination_load_type ?? "-"), measureValue: (row) => String(row.destination_load_type ?? "-") },
    { key: "latest_freshness_at", label: "Latest check", sortable: true, autoFit: true, minWidth: 132, maxWidth: 178, render: (row) => <TableDateTimeValue value={row.latest_freshness_at} timezoneName={timezoneName} /> },
    { key: "age_days", label: "Age", sortable: true, autoFit: true, minWidth: 54, maxWidth: 76, className: "freshness-registry-cell-single-line", render: (row) => <AgeCell row={row} /> },
    { key: "last_statuses", label: "Recent runs", sortable: true, sortKey: "latest_run_status", autoFit: true, minWidth: 88, maxWidth: 104, className: "freshness-registry-cell-single-line", render: (row) => <LastStatusCell row={row} timezoneName={timezoneName} /> },
    { key: "movement_state", label: "Watermark", sortable: true, autoFit: true, minWidth: 98, maxWidth: 138, className: "freshness-registry-cell-single-line", render: (row) => <WatermarkBadge value={row.movement_state} effective={row.source_watermark_effective} />, measureValue: (row) => String(row.movement_state ?? "Not configured") },
    { key: "latest_success_watermark", label: "Latest watermark", sortable: true, minWidth: 160, fillPriority: "last", render: (row) => <LatestWatermarkCell row={row} /> }
  ], [timezoneName]);

  return (
    <DataTable<FreshnessRow>
      rows={rows}
      columns={columns}
      maxRows={limit}
      offset={offset}
      onRowClick={onInspect}
      sort={sort}
      onSort={onSort}
      timezoneName={timezoneName}
      className="monitoring-freshness-table"
    />
  );
}

function CompactDataflowCell({ row }: { row: FreshnessRow }) {
  const name = String(row.dataflow_name ?? row.dataflow_id ?? "unknown");
  return (
    <span className="monitor-stack-cell" title={name}>
      <strong>{name}</strong>
    </span>
  );
}

function LatestWatermarkCell({ row }: { row: FreshnessRow }) {
  const watermark = firstFreshnessValue(row, ["latest_success_watermark"]);
  const value = hasFreshnessValue(watermark) ? String(watermark) : "-";
  return (
    <span className="monitor-stack-cell freshness-latest-watermark" title={value}>
      <span>{value}</span>
    </span>
  );
}

function AgeCell({ row }: { row: FreshnessRow }) {
  const ageDays = Number(row.age_days ?? 0);
  const ageSeconds = Number(row.age_seconds ?? ageDays * 86400);
  const tone = Number.isFinite(ageDays) && ageDays > STALE_THRESHOLD_DAYS ? "is-warning" : "";
  return <span className={`freshness-age-value ${tone}`}>{Number.isFinite(ageSeconds) ? formatAgeSeconds(ageSeconds) : "-"}</span>;
}

function LastStatusCell({ row, timezoneName }: { row: FreshnessRow; timezoneName?: string | null }) {
  const statuses = Array.isArray(row.last_statuses) ? row.last_statuses.slice(0, 5) : [];
  if (!statuses.length) return <span className="monitor-muted">-</span>;
  return (
    <span className="freshness-status-history" aria-label="Last five statuses">
      {statuses.map((item, index) => {
        const record: Record<string, unknown> = item && typeof item === "object" ? item as Record<string, unknown> : { status: item };
        const status = String(record.status ?? "unknown").toLowerCase();
        const timeValue = record.time ? String(record.time) : "";
        const title = [
          status,
          timeValue && timezoneName ? formatStatusHistoryTime(timeValue, timezoneName) : timeValue,
          record.dataflow_run_id ? `run ${record.dataflow_run_id}` : "",
        ].filter(Boolean).join("\n");
        return <i key={`${status}-${index}`} className={`status-bg-${status}`} title={title || status} />;
      })}
    </span>
  );
}

function WatermarkMovementLegend() {
  return <FreshnessChartLegend label="Watermark movement legend" items={WATERMARK_MOVEMENT_LEGEND} />;
}

function FreshnessChartLegend({
  label,
  items,
}: {
  label: string;
  items: ReadonlyArray<readonly [string, string, string?]>;
}) {
  return (
    <div className="freshness-chart-legend" aria-label={label}>
      {items.map(([itemLabel, color, description]) => (
        <span key={itemLabel} title={description}>
          <i style={{ backgroundColor: color }} aria-hidden="true" />
          {itemLabel}
        </span>
      ))}
    </div>
  );
}

function firstFreshnessValue(row: FreshnessRow, keys: string[]) {
  for (const key of keys) {
    if (hasFreshnessValue(row[key])) return row[key];
  }
  return null;
}

function hasFreshnessValue(value: unknown) {
  if (Array.isArray(value)) return value.length > 0;
  return value !== null && value !== undefined && value !== "";
}

function formatStatusHistoryTime(value: string, timezoneName: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "short",
    timeStyle: "medium",
    timeZone: timezoneName,
  }).format(date);
}

export function freshnessAgeByDataflowOption(rows: FreshnessRow[]): EChartsOption {
  const visible = [...rows]
    .filter((row) => row.age_days !== null && row.age_days !== undefined && Number.isFinite(Number(row.age_days)))
    .sort((a, b) => Number(b.age_days ?? 0) - Number(a.age_days ?? 0));
  const zoomConfig = horizontalBarDataZoom(visible.length);
  const barSizing = horizontalBarSeriesSizing(visible.length);
  if (!visible.length) return emptyChartOption("No dataflow freshness evidence.");
  return baseChartOption({
    tooltip: {
      trigger: "item",
      triggerOn: "mousemove",
      appendToBody: true,
      axisPointer: { type: "none" },
      formatter: (params: any) => {
        const row = visible[Number(params?.dataIndex ?? 0)] ?? {};
        return [
          `<strong>${String(row.dataflow_name ?? row.dataflow_id ?? "unknown")}</strong>`,
          `Age: ${formatAgeSeconds(Number(row.age_seconds ?? Number(row.age_days ?? 0) * 86400))}`,
          `Freshness: ${Number(row.age_days ?? 0) > STALE_THRESHOLD_DAYS ? "Stale" : "Current"}`,
          `Target: ${String(row.target ?? "unknown")}`,
          `Latest check: ${String(row.latest_freshness_at ?? "-")}`,
          `Latest run status: ${String(row.latest_freshness_status ?? "-")}`
        ].join("<br/>");
      }
    },
    grid: fixedHorizontalBarGrid(132, Boolean(zoomConfig), { top: 6 }),
    xAxis: { type: "value", axisLabel: { fontSize: 10, formatter: (value: number) => `${formatCompact(value)}d` }, splitLine: { lineStyle: { color: reportChartPalette.grid } } },
    yAxis: fixedHorizontalCategoryAxis(
      visible.map((row) => String(row.dataflow_name ?? row.dataflow_id ?? "unknown")),
      132,
      { axisLine: { show: true, lineStyle: { color: "#d9e1ea", width: 1 } } }
    ),
    dataZoom: zoomConfig,
    series: [{
      name: "Age days",
      type: "bar",
      ...barSizing,
      label: optionalValueLabel("right", (value) => `${formatCompact(value)}d`),
      labelLayout: { hideOverlap: true },
      itemStyle: { borderRadius: 2 },
      data: visible.map((row) => ({
        value: Number(row.age_days ?? 0),
        itemStyle: { color: freshnessAgeColor(Number(row.age_days ?? 0)) }
      }))
    }]
  });
}

function freshnessAgeColor(ageDays: number) {
  return ageDays > STALE_THRESHOLD_DAYS ? reportChartPalette.amber : reportChartPalette.unknown;
}

export function watermarkMovementTrendOption(rows: FreshnessRow[]): EChartsOption {
  if (!rows.length) return emptyChartOption("No watermark movement evidence.");
  const maxTotal = Math.max(1, ...rows.map(watermarkEnabledRuns));
  const statuses = [
    ["initialized", "Initialized", reportChartPalette.teal],
    ["advanced", "Advanced", reportChartPalette.success],
    ["unchanged", "Unchanged", reportChartPalette.skipped],
    ["incomplete", "Incomplete", reportChartPalette.blue],
    ["invalid", "Invalid", reportChartPalette.failed],
    ["unknown", "Unknown", reportChartPalette.unknown]
  ] as const;
  return baseChartOption({
    tooltip: {
      trigger: "axis",
      confine: true,
      formatter: (params: any) => {
        const points = Array.isArray(params) ? params : [params];
        const row = rows[Number(points[0]?.dataIndex ?? 0)] ?? {};
        return [
          `<strong>${String(row.bucket ?? row.date ?? "unknown")}</strong>`,
          row.grain ? `Grain: ${row.grain}` : "",
          ...points
            .filter((point) => point.seriesName !== "Advanced %")
            .map((point) => `${point.marker}${point.seriesName}: ${formatNumber(Number(point.value ?? 0))}`),
          `Adjusted effective boundary: ${formatNumber(Number(row.adjusted ?? 0))}`,
          `Advanced rate: ${row.advanced_rate === null || row.advanced_rate === undefined ? "N/A" : formatPercent(Number(row.advanced_rate))}`
        ].filter(Boolean).join("<br/>");
      }
    },
    legend: { show: false },
    grid: reportChartGrid({ left: 36, right: 40, top: 6, bottom: 5, containLabel: false }),
    xAxis: { type: "category", data: rows.map((row) => String(row.date ?? row.bucket ?? "unknown")), axisTick: { show: false }, axisLabel: { fontSize: 10, color: reportChartPalette.muted, hideOverlap: true } },
    yAxis: [
      { type: "value", axisLabel: { fontSize: 10, formatter: (value: number) => formatCompact(value) }, splitLine: { lineStyle: { color: reportChartPalette.grid } } },
      { type: "value", min: 0, max: 100, axisLabel: { fontSize: 10, formatter: (value: number) => `${value}%` }, splitLine: { show: false } }
    ],
    series: [
      ...statuses.map(([key, label, color]) => ({
        name: label,
        type: "bar" as const,
        stack: "movement",
        barMaxWidth: 28,
        label: optionalInsideShareLabel(
          rows,
          watermarkEnabledRuns,
          (value) => formatCompact(value),
          { maxTotal }
        ),
        labelLayout: { hideOverlap: true },
        itemStyle: { color, borderRadius: key === "unknown" ? [2, 2, 0, 0] : 0 },
        data: rows.map((row) => Number(row[key] ?? 0))
      })),
      {
        name: "Advanced %",
        type: "line" as const,
        yAxisIndex: 1,
        connectNulls: true,
        showSymbol: false,
        showAllSymbol: false,
        symbol: "circle",
        symbolSize: 5,
        clip: false,
        z: 6,
        smooth: 0.2,
        color: reportChartPalette.blue,
        lineStyle: { width: 1.6, color: reportChartPalette.blue },
        itemStyle: { color: "#ffffff", borderColor: reportChartPalette.blue, borderWidth: 1.5 },
        data: rows.map((row) => Number(row.advanced_rate ?? 0))
      }
    ]
  });
}

export function fillMissingWatermarkMovementTrendRows(
  rows: FreshnessRow[],
  filters: MonitoringFilters,
  dateRange: { min?: string | null; max?: string | null },
  timezoneName: string,
  reportEffectiveGrain = "day"
) {
  const effectiveGrain = String(rows.find((row) => row.grain)?.grain ?? reportEffectiveGrain ?? filters.grain ?? "day");
  const knownRows = rows.flatMap((row) => {
    const key = normalizeTrendBucketKey(row.bucket ?? row.date, effectiveGrain, timezoneName);
    return key ? [{ ...row, date: key, bucket: key }] : [];
  });
  const rowByKey = new Map(knownRows.map((row) => [String(row.bucket), row]));
  const bucketKeys = resolveTrendBucketKeys(
    filters,
    dateRange,
    timezoneName,
    Array.from(rowByKey.keys()),
    effectiveGrain
  );
  if (!bucketKeys.length) return knownRows;
  return bucketKeys.map((bucket) => ({
    ...createEmptyWatermarkMovementTrendRow(bucket, effectiveGrain),
    ...(rowByKey.get(bucket) ?? {})
  }));
}

function createEmptyWatermarkMovementTrendRow(bucket: string, grain: string): FreshnessRow {
  return {
    date: bucket,
    bucket,
    grain: normalizeTrendGrain(grain),
    initialized: 0,
    advanced: 0,
    unchanged: 0,
    incomplete: 0,
    invalid: 0,
    unknown: 0,
    adjusted: 0,
    watermark_enabled_runs: 0,
    advanced_rate: 0
  };
}

export function freshnessAgeDistributionOption(rows: FreshnessRow[]): EChartsOption {
  if (!rows.length) return emptyChartOption("No age distribution evidence.");
  return baseChartOption({
    tooltip: {
      trigger: "item",
      formatter: (params: any) => `${params?.name}: ${formatNumber(Number(params?.value ?? 0))} dataflows`
    },
    grid: reportChartGrid({ left: 34, right: 8, top: 18, bottom: 5, containLabel: false }),
    xAxis: { type: "category", data: rows.map((row) => String(row.bucket ?? "unknown")), axisTick: { show: false }, axisLabel: { fontSize: 10, color: reportChartPalette.muted } },
    yAxis: { type: "value", axisLabel: { fontSize: 10, formatter: (value: number) => formatCompact(value) }, splitLine: { lineStyle: { color: reportChartPalette.grid } } },
    series: [{
      name: "Dataflows",
      type: "bar",
      barMaxWidth: 34,
      label: optionalValueLabel("top", (value) => formatCompact(value)),
      labelLayout: { hideOverlap: true },
      itemStyle: { borderRadius: 2 },
      data: rows.map((row) => ({
        value: Number(row.dataflows ?? 0),
        itemStyle: { color: freshnessAgeBucketColor(String(row.bucket ?? "unknown")) }
      }))
    }]
  });
}

function freshnessAgeBucketColor(bucket: string) {
  const normalized = bucket.trim().toLowerCase();
  if (normalized === "≤1d") return reportChartPalette.teal;
  if (normalized === "1–3d") return reportChartPalette.blue;
  if (normalized === "3–7d") return reportChartPalette.pending;
  if (normalized === "7–30d") return reportChartPalette.amber;
  if (normalized === ">30d") return reportChartPalette.failed;
  return reportChartPalette.unknown;
}

export function watermarkCoverageByStageOption(rows: FreshnessRow[]): EChartsOption {
  const visible = [...rows]
    .filter((row) => Number(row.observed_dataflows ?? 0) > 0)
    .sort((left, right) => String(left.stage ?? "unknown").localeCompare(String(right.stage ?? "unknown")));
  const zoomConfig = horizontalBarDataZoom(visible.length);
  const barSizing = horizontalBarSeriesSizing(visible.length);
  if (!visible.length) return emptyChartOption("No watermark coverage evidence.");
  return baseChartOption({
    tooltip: {
      trigger: "item",
      triggerOn: "mousemove",
      appendToBody: true,
      axisPointer: { type: "none" },
      formatter: (params: any) => {
        const row = visible[Number(params?.dataIndex ?? 0)] ?? {};
        return [
          `<strong>${String(row.stage ?? "unknown")}</strong>`,
          `Enabled dataflows: ${formatNumber(Number(row.watermark_enabled_dataflows ?? 0))}`,
          `Not configured dataflows: ${formatNumber(notConfiguredDataflows(row))}`,
          `Coverage: ${formatPercent(Number(row.coverage_rate ?? 0))}`
        ].join("<br/>");
      }
    },
    legend: { show: false },
    grid: fixedHorizontalBarGrid(96, Boolean(zoomConfig), { top: 6 }),
    xAxis: { type: "value", max: 100, axisLabel: { fontSize: 10, formatter: (value: number) => `${value}%` }, splitLine: { lineStyle: { color: reportChartPalette.grid } } },
    yAxis: fixedHorizontalCategoryAxis(
      visible.map((row) => String(row.stage ?? "unknown")),
      96,
      { axisLine: { show: true, lineStyle: { color: "#d9e1ea", width: 1 } } }
    ),
    dataZoom: zoomConfig,
    series: [
      {
        name: "Enabled",
        type: "bar",
        stack: "coverage",
        ...barSizing,
        label: optionalInsideShareLabel(visible, () => 100, (value) => `${formatCompact(value)}%`),
        labelLayout: { hideOverlap: true },
        itemStyle: { color: reportChartPalette.success, borderRadius: [2, 0, 0, 2] },
        data: visible.map((row) => Number(row.coverage_rate ?? 0))
      },
      {
        name: "Not configured",
        type: "bar",
        stack: "coverage",
        ...barSizing,
        label: optionalInsideShareLabel(visible, () => 100, (value) => `${formatCompact(value)}%`),
        labelLayout: { hideOverlap: true },
        itemStyle: { color: reportChartPalette.unknown, borderRadius: [0, 2, 2, 0] },
        data: visible.map((row) => Math.max(0, 100 - Number(row.coverage_rate ?? 0)))
      }
    ]
  });
}

export function skippedStreakDistributionOption(rows: FreshnessRow[]): EChartsOption {
  if (!rows.length) return emptyChartOption("No consecutive skipped dataflow streaks.");
  const maxValue = Math.max(1, ...rows.map((row) => Number(row.dataflows ?? 0)));
  return baseChartOption({
    tooltip: {
      trigger: "item",
      formatter: (params: any) => `${params?.name}: ${formatNumber(Number(params?.value ?? 0))} dataflows`
    },
    grid: reportChartGrid({ left: 30, right: 8, top: 18, bottom: 5, containLabel: false }),
    xAxis: { type: "category", data: rows.map((row) => String(row.bucket ?? "unknown")), axisTick: { show: false }, axisLabel: { fontSize: 10, color: reportChartPalette.muted } },
    yAxis: { type: "value", max: chartLabelHeadroomMax(maxValue), axisLabel: { fontSize: 10, formatter: (value: number) => formatCompact(value) }, splitLine: { lineStyle: { color: reportChartPalette.grid } } },
    series: [{
      name: "Dataflows",
      type: "bar",
      barMaxWidth: 34,
      label: optionalValueLabel("top", (value) => formatCompact(value)),
      labelLayout: { hideOverlap: true },
      itemStyle: { color: reportChartPalette.skipped, borderRadius: 2 },
      data: rows.map((row) => Number(row.dataflows ?? 0))
    }]
  });
}

export function watermarkAdjustmentOption(rows: FreshnessRow[]): EChartsOption {
  if (!rows.length) return emptyChartOption("No watermark adjustment evidence.");
  return baseChartOption({
    tooltip: {
      trigger: "axis",
      appendToBody: true,
      formatter: (params: any) => {
        const points = Array.isArray(params) ? params : [params];
        const row = rows[Number(points[0]?.dataIndex ?? 0)] ?? {};
        return [
          `<strong>${String(row.bucket ?? row.date ?? "unknown")}</strong>`,
          `Adjusted effective boundary: ${formatNumber(Number(row.adjusted ?? 0))}`,
          `Watermark-enabled runs: ${formatNumber(watermarkEnabledRuns(row))}`
        ].join("<br/>");
      }
    },
    grid: reportChartGrid({ left: 30, right: 12, top: 18, bottom: 5, containLabel: false }),
    xAxis: { type: "category", data: rows.map((row) => String(row.date ?? row.bucket ?? "unknown")), axisTick: { show: false }, axisLabel: { fontSize: 10, color: reportChartPalette.muted, hideOverlap: true } },
    yAxis: { type: "value", axisLabel: { fontSize: 10, formatter: (value: number) => formatCompact(value) }, splitLine: { lineStyle: { color: reportChartPalette.grid } } },
    series: [{
      name: "Adjusted",
      type: "line",
      smooth: 0.2,
      showSymbol: false,
      showAllSymbol: false,
      symbol: "circle",
      symbolSize: 5,
      lineStyle: { width: 1.6, color: reportChartPalette.blue },
      itemStyle: { color: "#ffffff", borderColor: reportChartPalette.blue, borderWidth: 1.5 },
      areaStyle: { color: "rgba(59, 130, 246, 0.16)" },
      data: rows.map((row) => Number(row.adjusted ?? 0))
    }]
  });
}

function optionalValueLabel(
  position: "top" | "right",
  formatter: (value: number) => string
) {
  return {
    show: true,
    position,
    color: reportChartPalette.text,
    fontSize: 10,
    formatter: (params: { value?: unknown }) => {
      const value = Number(params.value ?? 0);
      if (!Number.isFinite(value) || value <= 0) return "";
      return formatter(value);
    }
  };
}

function optionalInsideShareLabel(
  rows: FreshnessRow[],
  totalForRow: (row: FreshnessRow) => number,
  formatter: (value: number) => string,
  options: { minShare?: number; minAxisShare?: number; maxTotal?: number } = {}
) {
  const minShare = options.minShare ?? 15;
  const minAxisShare = options.minAxisShare ?? 7;
  const maxTotal = Math.max(1, options.maxTotal ?? Math.max(1, ...rows.map(totalForRow)));
  return {
    show: true,
    position: "inside" as const,
    color: "#ffffff",
    fontSize: 10,
    formatter: (params: { value?: unknown; dataIndex?: number }) => {
      const value = Number(params.value ?? 0);
      const row = rows[Number(params.dataIndex ?? 0)] ?? {};
      const total = totalForRow(row);
      if (!Number.isFinite(value) || value <= 0 || !Number.isFinite(total) || total <= 0) return "";
      if ((value / total) * 100 < minShare) return "";
      if ((value / maxTotal) * 100 < minAxisShare) return "";
      return formatter(value);
    }
  };
}

function chartLabelHeadroomMax(maxValue: number) {
  return Math.ceil(Math.max(1, maxValue) * 1.18);
}

function emptyChartOption(message: string): EChartsOption {
  return baseChartOption({
    title: { text: message, left: "center", top: "middle", textStyle: { color: reportChartPalette.muted, fontSize: 12, fontWeight: 500 } },
    xAxis: { show: false },
    yAxis: { show: false },
    series: []
  });
}

function formatAgeSeconds(value: number) {
  if (!Number.isFinite(value)) return "-";
  if (value < 60) return `${Math.max(0, Math.round(value))}s`;
  const minutes = value / 60;
  if (minutes < 60) return `${formatNumber(minutes)}m`;
  const hours = minutes / 60;
  if (hours < 24) return `${formatNumber(hours)}h`;
  return `${formatNumber(value / 86400)}d`;
}

function notConfiguredDataflows(row: FreshnessRow) {
  return Number(row.not_configured_dataflows ?? 0);
}

function watermarkEnabledRuns(row: FreshnessRow) {
  return Number(row.watermark_enabled_runs ?? 0);
}
