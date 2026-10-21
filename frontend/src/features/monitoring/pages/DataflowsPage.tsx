import type { MonitoringMetric, MonitoringRecord, MonitoringReport } from "../../../shared/api/domainTypes";
import type { MonitoringFilters } from "../monitoringFilters";import { DataflowEndpointHealthPanel, DataflowNameStatusHealthPanel, DataflowRunsTable } from "./DataflowsPageSupport";
import { DurationDistributionBoxPlot } from "../components/monitoringDurationPresentation";
import { ObservedNumberValue } from "../components/monitoringPresentation";
import { rowObservation } from "../components/monitoringObservation";
import { CompactBytesValue, CompactNumberValue, LifecycleStatusValues, DetailMetric, DurationHeadline, HealthStripCard, ReportChart, ReportPanel, RuntimePhaseContribution, RuntimePhaseLegend, StatusHealthLegend, StatusTrendLegend, type TableSort, TablePager, WindowPairDetail, dataflowStatusTrendOption, durationIntent, durationPercentilesDetail, durationStatsTitle, formatPercent, metricNumber, metricValue, monitoringTimezone, runtimePhaseContributionTooltip, successRateIntent } from "../components/monitoringPresentation";

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
  const durationStats = (report.operations.dataflow_duration_stats ?? {}) as Record<string, MonitoringMetric>;
  const timezoneName = monitoringTimezone(report);
  const last24Window = (report.operations.windows?.last_24_hours ?? {}) as Record<string, unknown>;
  const last7Window = (report.operations.windows?.last_7_days ?? {}) as Record<string, unknown>;
  const metricOr = (row: Record<string, MonitoringMetric>, key: string, fallback: MonitoringMetric | undefined): MonitoringMetric | undefined => row[key] === undefined ? fallback : row[key];
  const avgDuration = metricOr(durationStats, "avg_duration_seconds", kpis.avg_duration_seconds);
  const p50Duration = metricOr(durationStats, "p50_duration_seconds", avgDuration);
  const executableRuns = metricNumber(kpis.succeeded) + metricNumber(kpis.failed);
  const rateIntent = successRateIntent(metricNumber(kpis.success_rate), metricNumber(kpis.failure_rate), executableRuns);
  const durationHealthIntent = durationIntent(durationStats, metricNumber(kpis.avg_duration_seconds), metricNumber(kpis.p95_duration_seconds));
  const phaseStageRows = report.operations.phase_health_by_stage ?? [];
  const endpointRows = report.operations.dataflow_endpoint_health ?? [];
  const dataflowNameStatusRows = report.operations.dataflow_name_status_health ?? [];
  return (
    <div className="monitoring-page monitoring-dataflow-report">
      <section className="overview-health-strip monitoring-dataflow-health-strip">
        <HealthStripCard
          label="Dataflow runs"
          value={<CompactNumberValue value={metricValue(kpis, "total_dataflows")} />}
          detail={
            <WindowPairDetail
              firstLabel="24h"
              firstValue={<CompactNumberValue value={metricValue(last24Window, "dataflow_runs")} />}
              firstTone="headline"
              secondLabel="7d"
              secondValue={<CompactNumberValue value={metricValue(last7Window, "dataflow_runs")} />}
              secondTone="headline"
            />
          }
          intent="neutral"
          title="Total dataflow run records in the current filters."
        />
        <HealthStripCard
          label="Execution rate"
          value={`${formatPercent(metricValue(kpis, "success_rate"))} success`}
          detail={
            <WindowPairDetail
              firstLabel="24h"
              firstValue={formatPercent(metricValue(last24Window, "dataflow_success_rate"))}
              firstTone={rateIntent}
              secondLabel="7d"
              secondValue={formatPercent(metricValue(last7Window, "dataflow_success_rate"))}
              secondTone={rateIntent}
            />
          }
          intent={rateIntent}
          accent="intent"
          title="Execution success rate = succeeded / (succeeded + failed). Skipped runs are not counted as failed."
        />
        <HealthStripCard
          label="Failed runs"
          value={<CompactNumberValue value={metricValue(kpis, "failed")} />}
          detail={
            <WindowPairDetail
              firstLabel="24h"
              firstValue={<CompactNumberValue value={metricValue(last24Window, "dataflow_failed")} />}
              firstTone={metricNumber(metricValue(last24Window, "dataflow_failed")) ? "bad" : "neutral"}
              secondLabel="7d"
              secondValue={<CompactNumberValue value={metricValue(last7Window, "dataflow_failed")} />}
              secondTone={metricNumber(metricValue(last7Window, "dataflow_failed")) ? "bad" : "neutral"}
            />
          }
          intent={metricNumber(metricValue(kpis, "failed")) ? "bad" : "neutral"}
          accent="intent"
          title="Failed dataflow run records in the current filters."
        />
        <HealthStripCard
          label="Skipped / Running / Pending"
          value={
            <LifecycleStatusValues
              skipped={metricValue(kpis, "skipped")}
              running={metricValue(kpis, "running")}
              pending={metricValue(kpis, "pending")}
            />
          }
          detail={
            <WindowPairDetail
              firstLabel="24h"
              firstValue={
                <LifecycleStatusValues
                  skipped={metricValue(last24Window, "dataflow_skipped")}
                  running={metricValue(last24Window, "dataflow_running")}
                  pending={metricValue(last24Window, "dataflow_pending")}
                />
              }
              firstTone="neutral"
              secondLabel="7d"
              secondValue={
                <LifecycleStatusValues
                  skipped={metricValue(last7Window, "dataflow_skipped")}
                  running={metricValue(last7Window, "dataflow_running")}
                  pending={metricValue(last7Window, "dataflow_pending")}
                />
              }
              secondTone="neutral"
            />
          }
          intent={metricNumber(metricValue(kpis, "running")) || metricNumber(metricValue(kpis, "pending")) ? "warning" : "neutral"}
          accent="intent"
          title="Skipped means no new data or no processing needed. It is tracked separately from failed."
        />
        <HealthStripCard
          label="Dataflow duration"
          value={<DurationHeadline avgSeconds={avgDuration} p50Seconds={p50Duration} />}
          detail={durationPercentilesDetail(durationStats)}
          intent={durationHealthIntent}
          accent="intent"
          title={durationStatsTitle("Dataflow duration", durationStats)}
          className="overview-health-card-duration"
        />
        <HealthStripCard
          label="Workload"
          value={<><ObservedNumberValue row={volumeKpis} field="rows_read" totalField="total_rows_read" /> / <ObservedNumberValue row={volumeKpis} field="rows_written" totalField="total_rows_written" /></>}
          detail={<DetailMetric label="net lakehouse bytes" value={<CompactBytesValue value={metricValue(volumeKpis, "net_bytes_change")} />} tone="blue" />}
          intent="neutral"
          title={[
            "Rows read / rows written. Byte metrics are lakehouse destination signals where available.",
            `Read: ${rowObservation(volumeKpis, "rows_read").coverage ?? "Measurement coverage unavailable"}`,
            `Written: ${rowObservation(volumeKpis, "rows_written").coverage ?? "Measurement coverage unavailable"}`
          ].join("\n")}
        />
      </section>

      <div className="monitoring-dataflow-content report-layout-table-heavy-3">
        <section className="monitoring-dataflow-primary-grid">
          <ReportPanel
            title="Dataflow status trend"
            titleTooltip="Dataflow run status over the selected time range. Success rate line uses succeeded / (succeeded + failed)."
            headerAction={<StatusTrendLegend />}
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
            titleTooltip={runtimePhaseContributionTooltip("stage")}
            headerAction={<RuntimePhaseLegend />}
          >
            <RuntimePhaseContribution
              rows={phaseStageRows}
              labelKey="stage"
              emptyText="No stage phase duration signals in current filters."
              showLegend={false}
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
            title="Dataflow x status"
            className="monitoring-dataflow-name-status-panel"
            titleTooltip="Groups dataflow runs by name and splits run counts by status. Sort prioritizes failed, active, slow, and high-volume dataflows."
            headerAction={<StatusHealthLegend />}
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
