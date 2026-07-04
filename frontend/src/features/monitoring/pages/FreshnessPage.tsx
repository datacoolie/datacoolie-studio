import type { EChartsOption } from "echarts";
import { useEffect, useState } from "react";
import type { MonitoringRecord, MonitoringReport } from "../../../shared/api/types";
import {
  DataTable,
  DetailMetric,
  EndpointCell,
  HealthStripCard,
  ReportChart,
  ReportPanel,
  TablePager,
  TableDateTimeValue,
  WatermarkBadge,
  baseChartOption,
  fixedHorizontalBarGrid,
  fixedHorizontalCategoryAxis,
  formatCompact,
  formatNumber,
  formatPercent,
  monitoringTimezone,
  reportChartPalette,
  reportChartGrid,
  type TableSort
} from "../monitoringShared";

type FreshnessRow = Record<string, unknown>;

export function FreshnessPage({
  report,
  onInspect
}: {
  report: MonitoringReport;
  onInspect?: (row: FreshnessRow) => void;
}) {
  const kpis = report.freshness.kpis;
  const timezoneName = monitoringTimezone(report);
  const registryRows = report.freshness.dataflow_registry ?? [];
  const [registrySort, setRegistrySort] = useState<TableSort>({ sortBy: "latest_freshness_at", sortDir: "desc" });
  const [registryOffset, setRegistryOffset] = useState(0);
  const [registryLimit, setRegistryLimit] = useState(100);
  useEffect(() => {
    if (registryOffset >= registryRows.length) setRegistryOffset(0);
  }, [registryOffset, registryRows.length]);
  const observedDataflows = Number(kpis.observed_dataflows ?? 0);
  const staleDataflows = Number(kpis.stale_dataflows ?? kpis.stale_candidates ?? 0);
  const advancedRuns = Number(kpis.watermark_advanced_runs ?? 0);
  const initializedRuns = Number(kpis.watermark_initialized_runs ?? 0);
  const unchangedRuns = Number(kpis.watermark_unchanged_runs ?? 0);
  const skippedStreakDataflows = Number(kpis.skipped_streak_dataflows ?? 0);
  const skippedStreakThreshold = Number(kpis.skipped_streak_threshold ?? 3);
  const staleThresholdDays = Number(kpis.stale_threshold_days ?? 7);
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
              <DetailMetric label="max" value={formatAgeSeconds(Number(kpis.max_age_seconds ?? 0))} tone={Number(kpis.max_age_days ?? 0) > 7 ? "warning" : "neutral"} labelFirst />
            </span>
          }
          intent={Number(kpis.p95_age_days ?? 0) > 7 ? "warning" : "neutral"}
          title="Age is measured from the latest succeeded/skipped ETL run per dataflow."
        />
        <HealthStripCard
          label="Watermark coverage"
          value={formatPercent(Number(kpis.watermark_coverage_rate ?? 0))}
          detail={<DetailMetric label="enabled flows" value={formatNumber(Number(kpis.watermark_enabled_dataflows ?? 0))} tone="blue" />}
          intent={Number(kpis.watermark_coverage_rate ?? 0) > 0 ? "good" : "neutral"}
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
          title="Movement = latest watermark change evidence. Advanced changed, unchanged did not change, initialized has first watermark."
        />
      </section>

      <div className="monitoring-freshness-content">
        <section className="monitoring-freshness-top-grid">
          <ReportPanel
            title="Oldest / stale dataflows"
            subtitle="latest check age"
            titleTooltip="Ranks dataflows by age of latest succeeded/skipped ETL run. Stale candidates are older than the configured threshold."
          >
            <ReportChart option={freshnessAgeByDataflowOption(report.freshness.dataflow_registry ?? [])} height="100%" wheelDataZoomStep={1} />
          </ReportPanel>
          <ReportPanel
            title="Watermark movement trend"
            subtitle={`${report.summary.effective_grain ?? "day"} grain`}
            titleTooltip="Counts watermark-enabled ETL runs by committed movement state: advanced, initialized, unchanged, incomplete, invalid, and unknown. This is diagnostic history across the filter; Freshness health only uses the latest watermark state per dataflow."
          >
            <ReportChart option={watermarkMovementTrendOption(report.freshness.watermark_movement_by_date ?? [])} height="100%" />
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
            subtitle="enabled vs not configured by stage"
            titleTooltip="Stage-level dataflow coverage. A dataflow is enabled when at least one ETL row for that stage has watermark columns or watermark values. Not configured is neutral: freshness still uses latest succeeded/skipped run."
          >
            <ReportChart option={watermarkCoverageByStageOption(report.freshness.watermark_coverage_by_stage ?? [])} height="100%" wheelDataZoomStep={1} />
          </ReportPanel>
          <ReportPanel
            title="Consecutive skipped"
            subtitle="dataflows by streak"
            titleTooltip={`Counts dataflows whose latest ${skippedStreakThreshold} ETL runs are all skipped. Skipped is not a failure and is not used by Freshness health; it usually means no new incremental data was available.`}
          >
            <ReportChart option={skippedStreakDistributionOption(report.freshness.skipped_streak_distribution ?? [])} height="100%" />
          </ReportPanel>
          <ReportPanel
            title="Watermark adjustment"
            subtitle="effective read-boundary signal"
            titleTooltip="Adjustment is separate from watermark movement. Adjusted means source_watermark_effective differs from source_watermark_before."
          >
            <ReportChart option={watermarkAdjustmentOption(report.freshness.watermark_movement_by_date ?? [])} height="100%" />
          </ReportPanel>
        </section>

        <ReportPanel
          title="Dataflow freshness registry"
          subtitle={`${formatNumber(registryRows.length)} dataflows`}
          titleTooltip="Dataflow-level registry used for investigation. Click a row to inspect latest master data, freshness evidence, and the related dataflow runs."
          className="monitoring-freshness-registry-panel"
          headerAction={
            <TablePager
              limit={registryLimit}
              offset={registryOffset}
              loadedRows={Math.min(registryLimit, Math.max(0, registryRows.length - registryOffset))}
              totalRows={registryRows.length}
              loading={false}
              onPageChange={setRegistryOffset}
              onPageSizeChange={(nextLimit) => {
                setRegistryLimit(nextLimit);
                setRegistryOffset(0);
              }}
            />
          }
        >
          <DataflowFreshnessTable
            rows={registryRows}
            offset={registryOffset}
            limit={registryLimit}
            sort={registrySort}
            onSort={(nextSort) => {
              setRegistrySort(nextSort);
              setRegistryOffset(0);
            }}
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
  return (
    <DataTable<FreshnessRow>
      rows={rows}
      columns={[
        { key: "dataflow_name", label: "Dataflow", sortable: true, width: 160, render: (row) => <CompactDataflowCell row={row} /> },
        { key: "stage", label: "Stage", sortable: true, autoFit: true, minWidth: 64, maxWidth: 140 },
        { key: "source_name", label: "Source", sortable: true, width: 160, render: (row) => <EndpointCell row={row as MonitoringRecord} direction="source" /> },
        { key: "destination_name", label: "Destination", sortable: true, width: 160, render: (row) => <EndpointCell row={row as MonitoringRecord} direction="destination" /> },
        { key: "destination_load_type", label: "Load", sortable: true, width: 120, render: (row) => String(row.destination_load_type ?? "-") },
        { key: "latest_freshness_at", label: "Latest check", sortable: true, autoFit: true, minWidth: 132, maxWidth: 178, render: (row) => <TableDateTimeValue value={row.latest_freshness_at} timezoneName={timezoneName} /> },
        { key: "age_days", label: "Age", sortable: true, autoFit: true, minWidth: 54, maxWidth: 76, render: (row) => <AgeCell row={row} /> },
        { key: "last_statuses", label: "Last status", sortable: true, sortKey: "latest_run_status", autoFit: true, minWidth: 82, maxWidth: 98, render: (row) => <LastStatusCell row={row} timezoneName={timezoneName} /> },
        { key: "movement_state", label: "Watermark", sortable: true, width: 130, render: (row) => <WatermarkBadge value={row.movement_state} effective={row.source_watermark_effective} /> },
        { key: "latest_success_watermark", label: "Latest watermark", sortable: true, minWidth: 160, fillPriority: "last", render: (row) => <LatestWatermarkCell row={row} /> }
      ]}
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
  const tone = Number.isFinite(ageDays) && ageDays > 7 ? "is-warning" : "";
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

function freshnessAgeByDataflowOption(rows: FreshnessRow[]): EChartsOption {
  const visible = [...rows]
    .filter((row) => row.age_days !== null && row.age_days !== undefined && Number.isFinite(Number(row.age_days)))
    .sort((a, b) => Number(b.age_days ?? 0) - Number(a.age_days ?? 0));
  const zoomConfig = horizontalDataZoom(visible.length);
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
          `Target: ${String(row.target ?? "unknown")}`,
          `Latest check: ${String(row.latest_freshness_at ?? "-")}`,
          `Status: ${String(row.latest_freshness_status ?? "-")}`
        ].join("<br/>");
      }
    },
    grid: fixedHorizontalBarGrid(132, Boolean(zoomConfig), { right: zoomConfig ? 16 : 8, top: 6 }),
    xAxis: { type: "value", axisLabel: { fontSize: 10, formatter: (value: number) => `${formatCompact(value)}d` }, splitLine: { lineStyle: { color: reportChartPalette.grid } } },
    yAxis: fixedHorizontalCategoryAxis(visible.map((row) => String(row.dataflow_name ?? row.dataflow_id ?? "unknown")), 132),
    dataZoom: zoomConfig,
    series: [{
      name: "Age days",
      type: "bar",
      barMaxWidth: visible.length <= 8 ? 16 : 12,
      label: optionalValueLabel("right", (value) => `${formatCompact(value)}d`),
      labelLayout: { hideOverlap: true },
      itemStyle: { borderRadius: 2 },
      data: visible.map((row) => ({
        value: Number(row.age_days ?? 0),
        itemStyle: { color: freshnessStatusColor(String(row.latest_freshness_status ?? row.status ?? "unknown")) }
      }))
    }]
  });
}

function freshnessStatusColor(status: string) {
  const normalized = status.toLowerCase();
  if (normalized === "succeeded") return reportChartPalette.success;
  if (normalized === "failed") return reportChartPalette.failed;
  if (normalized === "skipped") return reportChartPalette.skipped;
  if (normalized === "running") return reportChartPalette.running;
  if (normalized === "pending") return reportChartPalette.pending;
  return reportChartPalette.unknown;
}

function watermarkMovementTrendOption(rows: FreshnessRow[]): EChartsOption {
  if (!rows.length) return emptyChartOption("No watermark movement evidence.");
  const maxTotal = Math.max(1, ...rows.map((row) => Number(row.total ?? 0)));
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
          ...points.map((point) => {
            const value = Number(point.value ?? 0);
            const formatted = point.seriesName === "Advanced %" ? formatPercent(value) : formatNumber(value);
            return `${point.marker}${point.seriesName}: ${formatted}`;
          }),
          `Adjusted effective boundary: ${formatNumber(Number(row.adjusted ?? 0))}`,
          `Advanced rate: ${row.advanced_rate === null || row.advanced_rate === undefined ? "N/A" : formatPercent(Number(row.advanced_rate))}`
        ].filter(Boolean).join("<br/>");
      }
    },
    legend: { top: 0, left: "center", itemWidth: 8, itemHeight: 8, textStyle: { fontSize: 10, color: reportChartPalette.muted } },
    grid: reportChartGrid({ left: 8, right: 12, top: 22 }),
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
          (row) => Number(row.total ?? 0),
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

function freshnessAgeDistributionOption(rows: FreshnessRow[]): EChartsOption {
  if (!rows.length) return emptyChartOption("No age distribution evidence.");
  const maxValue = Math.max(1, ...rows.map((row) => Number(row.dataflows ?? row.targets ?? 0)));
  return baseChartOption({
    tooltip: {
      trigger: "item",
      formatter: (params: any) => `${params?.name}: ${formatNumber(Number(params?.value ?? 0))} dataflows`
    },
    grid: reportChartGrid({ left: 8, right: 8, top: 18 }),
    xAxis: { type: "category", data: rows.map((row) => String(row.bucket ?? "unknown")), axisTick: { show: false }, axisLabel: { fontSize: 10, color: reportChartPalette.muted } },
    yAxis: { type: "value", max: chartLabelHeadroomMax(maxValue), axisLabel: { fontSize: 10, formatter: (value: number) => formatCompact(value) }, splitLine: { lineStyle: { color: reportChartPalette.grid } } },
    series: [{
      name: "Dataflows",
      type: "bar",
      barMaxWidth: 34,
      label: optionalValueLabel("top", (value) => formatCompact(value)),
      labelLayout: { hideOverlap: true },
      itemStyle: { color: reportChartPalette.blue, borderRadius: 2 },
      data: rows.map((row) => Number(row.dataflows ?? row.targets ?? 0))
    }]
  });
}

function watermarkCoverageByStageOption(rows: FreshnessRow[]): EChartsOption {
  const visible = [...rows]
    .filter((row) => Number(row.total ?? 0) > 0)
    .sort((left, right) => String(left.stage ?? "unknown").localeCompare(String(right.stage ?? "unknown")));
  const zoomConfig = horizontalDataZoom(visible.length);
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
          `Enabled dataflows: ${formatNumber(Number(row.enabled ?? 0))}`,
          `Not configured dataflows: ${formatNumber(notConfiguredDataflows(row))}`,
          `Coverage: ${formatPercent(Number(row.coverage_rate ?? 0))}`
        ].join("<br/>");
      }
    },
    legend: { top: 0, left: "center", itemWidth: 8, itemHeight: 8, textStyle: { fontSize: 10, color: reportChartPalette.muted } },
    grid: fixedHorizontalBarGrid(96, Boolean(zoomConfig), { right: zoomConfig ? 16 : 8, top: 22 }),
    xAxis: { type: "value", max: 100, axisLabel: { fontSize: 10, formatter: (value: number) => `${value}%` }, splitLine: { lineStyle: { color: reportChartPalette.grid } } },
    yAxis: fixedHorizontalCategoryAxis(visible.map((row) => String(row.stage ?? "unknown")), 96),
    dataZoom: zoomConfig,
    series: [
      {
        name: "Enabled",
        type: "bar",
        stack: "coverage",
        barMaxWidth: visible.length <= 8 ? 16 : 12,
        label: optionalInsideShareLabel(visible, () => 100, (value) => `${formatCompact(value)}%`),
        labelLayout: { hideOverlap: true },
        itemStyle: { color: reportChartPalette.success, borderRadius: [2, 0, 0, 2] },
        data: visible.map((row) => Number(row.coverage_rate ?? 0))
      },
      {
        name: "Not configured",
        type: "bar",
        stack: "coverage",
        barMaxWidth: visible.length <= 8 ? 16 : 12,
        label: optionalInsideShareLabel(visible, () => 100, (value) => `${formatCompact(value)}%`),
        labelLayout: { hideOverlap: true },
        itemStyle: { color: reportChartPalette.unknown, borderRadius: [0, 2, 2, 0] },
        data: visible.map((row) => Math.max(0, 100 - Number(row.coverage_rate ?? 0)))
      }
    ]
  });
}

function skippedStreakDistributionOption(rows: FreshnessRow[]): EChartsOption {
  if (!rows.length) return emptyChartOption("No consecutive skipped dataflow streaks.");
  const maxValue = Math.max(1, ...rows.map((row) => Number(row.dataflows ?? row.targets ?? 0)));
  return baseChartOption({
    tooltip: {
      trigger: "item",
      formatter: (params: any) => `${params?.name}: ${formatNumber(Number(params?.value ?? 0))} dataflows`
    },
    grid: reportChartGrid({ left: 8, right: 8, top: 18 }),
    xAxis: { type: "category", data: rows.map((row) => String(row.bucket ?? "unknown")), axisTick: { show: false }, axisLabel: { fontSize: 10, color: reportChartPalette.muted } },
    yAxis: { type: "value", max: chartLabelHeadroomMax(maxValue), axisLabel: { fontSize: 10, formatter: (value: number) => formatCompact(value) }, splitLine: { lineStyle: { color: reportChartPalette.grid } } },
    series: [{
      name: "Dataflows",
      type: "bar",
      barMaxWidth: 34,
      label: optionalValueLabel("top", (value) => formatCompact(value)),
      labelLayout: { hideOverlap: true },
      itemStyle: { color: reportChartPalette.skipped, borderRadius: 2 },
      data: rows.map((row) => Number(row.dataflows ?? row.targets ?? 0))
    }]
  });
}

function watermarkAdjustmentOption(rows: FreshnessRow[]): EChartsOption {
  if (!rows.length) return emptyChartOption("No watermark adjustment evidence.");
  const maxValue = Math.max(1, ...rows.map((row) => Number(row.adjusted ?? 0)));
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
          `Watermark-enabled runs: ${formatNumber(Number(row.total ?? 0))}`
        ].join("<br/>");
      }
    },
    grid: reportChartGrid({ left: 8, right: 12, top: 18 }),
    xAxis: { type: "category", data: rows.map((row) => String(row.date ?? row.bucket ?? "unknown")), axisTick: { show: false }, axisLabel: { fontSize: 10, color: reportChartPalette.muted, hideOverlap: true } },
    yAxis: { type: "value", max: chartLabelHeadroomMax(maxValue), axisLabel: { fontSize: 10, formatter: (value: number) => formatCompact(value) }, splitLine: { lineStyle: { color: reportChartPalette.grid } } },
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

function horizontalDataZoom(rowCount: number) {
  if (rowCount <= 8) return undefined;
  return [
    { type: "slider", yAxisIndex: 0, width: 8, right: 2, startValue: 0, endValue: 7, showDetail: false, brushSelect: false },
    { type: "inside", yAxisIndex: 0, startValue: 0, endValue: 7, zoomOnMouseWheel: false, moveOnMouseWheel: true, moveOnMouseMove: false }
  ];
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
  return Number(row.not_configured ?? row.missing ?? 0);
}
