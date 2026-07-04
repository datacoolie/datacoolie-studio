import type { MonitoringRecord, MonitoringReport } from "../../../shared/api/types";
import type { MonitoringFilters } from "../monitoringFilters";
import {
  DataflowEndpointHealthPanel,
  DataflowNameStatusHealthPanel,
  DataflowRunsTable,
  DetailMetric,
  DurationDistributionBoxPlot,
  DurationHeadline,
  HealthStripCard,
  ReportChart,
  ReportPanel,
  RuntimePhaseContribution,
  type TableSort,
  TablePager,
  WindowPairDetail,
  dataflowStatusTrendOption,
  durationIntent,
  durationPercentilesDetail,
  durationStatsTitle,
  formatBytes,
  formatNumber,
  formatPercent,
  monitoringTimezone,
  successRateIntent
} from "../monitoringShared";

export function DataflowsPage({
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
  sort?: TableSort;
  onSort?: (sort: TableSort) => void;
  limit: number;
  offset: number;
  onPageChange: (offset: number) => void;
  onPageSizeChange: (limit: number) => void;
  onInspect?: (row: MonitoringRecord) => void;
}) {
  const kpis = report.operations.dataflow_kpis;
  const volumeKpis = report.volume.kpis;
  const durationStats = (report.operations.dataflow_duration_stats ?? {}) as Record<string, number>;
  const timezoneName = monitoringTimezone(report);
  const last24Window = (report.operations.windows?.last_24_hours ?? {}) as Record<string, number>;
  const last7Window = (report.operations.windows?.last_7_days ?? {}) as Record<string, number>;
  const avgDuration = durationStats.avg_duration_seconds ?? kpis.avg_duration_seconds ?? 0;
  const p50Duration = durationStats.p50_duration_seconds ?? avgDuration;
  const executableRuns = (kpis.succeeded ?? 0) + (kpis.failed ?? 0);
  const rateIntent = successRateIntent(kpis.success_rate ?? 0, kpis.failure_rate ?? 0, executableRuns);
  const durationHealthIntent = durationIntent(durationStats, kpis.avg_duration_seconds ?? 0, kpis.p95_duration_seconds ?? 0);
  const phaseStageRows = report.operations.phase_health_by_stage ?? [];
  const endpointRows = report.operations.dataflow_endpoint_health ?? [];
  const dataflowNameStatusRows = report.operations.dataflow_name_status_health ?? [];
  return (
    <div className="monitoring-page monitoring-dataflow-report">
      <section className="overview-health-strip monitoring-dataflow-health-strip">
        <HealthStripCard
          label="Dataflow runs"
          value={formatNumber(kpis.total_dataflows ?? 0)}
          detail={
            <WindowPairDetail
              firstLabel="24h"
              firstValue={formatNumber(last24Window.dataflow_runs ?? 0)}
              secondLabel="7d"
              secondValue={formatNumber(last7Window.dataflow_runs ?? 0)}
            />
          }
          intent="neutral"
          title="Total dataflow run records in the current filters."
        />
        <HealthStripCard
          label="Execution rate"
          value={`${formatPercent(kpis.success_rate ?? 0)} success`}
          detail={
            <WindowPairDetail
              firstLabel="24h"
              firstValue={formatPercent(last24Window.dataflow_success_rate ?? 0)}
              firstTone={successRateIntent(last24Window.dataflow_success_rate ?? 0, last24Window.dataflow_failure_rate ?? 0, (last24Window.dataflow_succeeded ?? 0) + (last24Window.dataflow_failed ?? 0))}
              secondLabel="7d"
              secondValue={formatPercent(last7Window.dataflow_success_rate ?? 0)}
              secondTone={successRateIntent(last7Window.dataflow_success_rate ?? 0, last7Window.dataflow_failure_rate ?? 0, (last7Window.dataflow_succeeded ?? 0) + (last7Window.dataflow_failed ?? 0))}
            />
          }
          intent={rateIntent}
          title="Execution success rate = succeeded / (succeeded + failed). Skipped runs are not counted as failed."
        />
        <HealthStripCard
          label="Failed runs"
          value={formatNumber(kpis.failed ?? 0)}
          detail={
            <WindowPairDetail
              firstLabel="24h"
              firstValue={formatNumber(last24Window.dataflow_failed ?? 0)}
              firstTone={(last24Window.dataflow_failed ?? 0) ? "bad" : "neutral"}
              secondLabel="7d"
              secondValue={formatNumber(last7Window.dataflow_failed ?? 0)}
              secondTone={(last7Window.dataflow_failed ?? 0) ? "bad" : "neutral"}
            />
          }
          intent={(kpis.failed ?? 0) ? "bad" : "neutral"}
          title="Failed dataflow run records in the current filters."
        />
        <HealthStripCard
          label="Skipped / Running / Pending"
          value={`${formatNumber(kpis.skipped ?? 0)} / ${formatNumber(kpis.running ?? 0)} / ${formatNumber(kpis.pending ?? 0)}`}
          detail={
            <WindowPairDetail
              firstLabel="24h"
              firstValue={`${formatNumber(last24Window.dataflow_skipped ?? 0)} / ${formatNumber(last24Window.dataflow_running ?? 0)} / ${formatNumber(last24Window.dataflow_pending ?? 0)}`}
              firstTone={(last24Window.dataflow_skipped ?? 0) ? "warning" : "neutral"}
              secondLabel="7d"
              secondValue={`${formatNumber(last7Window.dataflow_skipped ?? 0)} / ${formatNumber(last7Window.dataflow_running ?? 0)} / ${formatNumber(last7Window.dataflow_pending ?? 0)}`}
              secondTone={(last7Window.dataflow_skipped ?? 0) ? "warning" : "neutral"}
            />
          }
          intent={(kpis.running ?? 0) || (kpis.pending ?? 0) ? "warning" : "neutral"}
          title="Skipped means no new data or no processing needed. It is tracked separately from failed."
        />
        <HealthStripCard
          label="Dataflow duration"
          value={<DurationHeadline avgSeconds={avgDuration} p50Seconds={p50Duration} />}
          detail={durationPercentilesDetail(durationStats)}
          intent={durationHealthIntent}
          title={durationStatsTitle("Dataflow duration", durationStats)}
          className="overview-health-card-duration"
        />
        <HealthStripCard
          label="Workload"
          value={`${formatNumber(volumeKpis.total_rows_read ?? 0)} / ${formatNumber(volumeKpis.total_rows_written ?? 0)}`}
          detail={
            <span className="health-rate-detail">
              <DetailMetric label="net lakehouse bytes" value={formatBytes(volumeKpis.net_bytes_change ?? 0)} tone="blue" />
            </span>
          }
          intent="neutral"
          title="Rows read / rows written. Byte metrics are lakehouse destination signals where available."
        />
      </section>

      <div className="monitoring-dataflow-content report-layout-table-heavy-3">
        <section className="monitoring-dataflow-primary-grid">
          <ReportPanel
            title="Dataflow status trend"
            subtitle="runs by status and success rate"
            titleTooltip="Dataflow run status over the selected time range. Success rate line uses succeeded / (succeeded + failed)."
          >
            <div className="monitoring-job-chart-fill">
              <ReportChart
                option={dataflowStatusTrendOption(report.operations.dataflows_by_date_status ?? [], filters, report.summary.date_range, timezoneName, report.summary.effective_grain ?? undefined)}
                height="100%"
              />
            </div>
          </ReportPanel>
          <ReportPanel
            title="Stage duration distribution"
            subtitle="dataflows grouped by stage"
            titleTooltip="Box plot of dataflow runs grouped by stage. Stage is a dataflow category, not the report grain. The visual shows min, Q1, median, Q3, max, outliers, average, P95, status mix, and operation mix."
          >
            <DurationDistributionBoxPlot
              rows={report.operations.dataflow_duration_by_stage ?? []}
              labelKey="stage"
              emptyText="No dataflow stage duration data in current filters."
            />
          </ReportPanel>
        </section>
        <section className="monitoring-dataflow-secondary-grid">
          <ReportPanel
            title="Stage phase contribution"
            subtitle="source, transform, destination"
            titleTooltip="Shows source, transform, and destination runtime contribution for dataflow runs grouped by stage. Stage is used as a diagnosis category; the underlying grain remains dataflow runs."
          >
            <RuntimePhaseContribution
              rows={phaseStageRows}
              labelKey="stage"
              emptyText="No stage phase duration signals in current filters."
            />
          </ReportPanel>
          <ReportPanel
            title="Source to destination health"
            subtitle="source to destination"
            titleTooltip="Groups dataflow runs by source and destination connection pair. It helps identify source-to-target paths with failures, slow P95 duration, or high run volume."
          >
            <DataflowEndpointHealthPanel rows={endpointRows} />
          </ReportPanel>
          <ReportPanel
            title="Dataflow name x status health"
            subtitle="runs by dataflow name"
            titleTooltip="Groups dataflow runs by dataflow_name and splits them by status. Sort prioritizes failed, active, slow, and high-volume dataflows."
          >
            <DataflowNameStatusHealthPanel rows={dataflowNameStatusRows} />
          </ReportPanel>
        </section>
        <ReportPanel
          title="Dataflow runs"
          className="monitoring-dataflow-runs-panel"
          headerAction={
            <TablePager
              limit={limit}
              offset={offset}
              loadedRows={rows.length}
              totalRows={totalRows}
              loading={loading}
              onPageChange={onPageChange}
              onPageSizeChange={onPageSizeChange}
            />
          }
        >
          <DataflowRunsTable rows={rows} sort={sort} onSort={onSort} onInspect={onInspect} timezoneName={timezoneName} />
        </ReportPanel>
      </div>
    </div>
  );
}
