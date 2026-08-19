import type { JobRecord, MonitoringReport } from "../../../shared/api/domainTypes";
import type { MonitoringFilters } from "../monitoringFilters";import { ChildFanoutDistributionPanel, JobDurationByOperationBoxPlot, JobRunsTable, JobStageHealthPanel, JobWorkloadEfficiencyScatter, WorkloadEfficiencyLegend } from "./JobsPageSupport";
import { CompactNumberValue, DetailMetric, DurationHeadline, HealthStripCard, LifecycleStatusValues, ReportChart, ReportPanel, StatusHealthLegend, StatusTrendLegend, type TableSort, TablePager, WindowPairDetail, durationIntent, durationPercentilesDetail, durationStatsTitle, formatPercent, formatTimestampForDisplay, jobStatusTrendOption, monitoringTimezone, successRateIntent } from "../components/monitoringPrimitives";

export function JobsPage({
  report,
  filters,
  rows,
  totalRows,
  loading,
  filtered,
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
  rows: JobRecord[];
  totalRows: number;
  loading: boolean;
  filtered: boolean;
  sort?: TableSort;
  onSort?: (sort: TableSort) => void;
  limit: number;
  offset: number;
  onPageChange: (offset: number) => void;
  onPageSizeChange: (limit: number) => void;
  onInspect?: (row: JobRecord) => void;
}) {
  const kpis = report.operations.kpis;
  const dataflowKpis = report.operations.dataflow_kpis;
  const jobDurationStats = (report.operations.job_duration_stats ?? {}) as Record<string, number>;
  const latestFailedJob = report.operations.latest_failed_job;
  const latestFailedTime = latestFailedJob ? String(latestFailedJob.end_time ?? latestFailedJob.start_time ?? "") : "";
  const durationByOperationRows = report.operations.job_duration_by_operation_types ?? [];
  const jobTrendRows = report.operations.jobs_by_date_status ?? [];
  const workloadEfficiencyRows = report.operations.job_workload_efficiency ?? [];
  const childFanoutRows = report.operations.job_child_fanout_distribution ?? [];
  const last24Window = (report.operations.windows?.last_24_hours ?? {}) as Record<string, number>;
  const last7Window = (report.operations.windows?.last_7_days ?? {}) as Record<string, number>;
  const jobP50Duration = jobDurationStats.p50_duration_seconds ?? jobDurationStats.avg_duration_seconds ?? kpis.avg_duration_seconds ?? 0;
  const jobP95Duration = jobDurationStats.p95_duration_seconds ?? kpis.p95_duration_seconds ?? 0;
  const jobP99Duration = jobDurationStats.p99_duration_seconds ?? 0;
  const jobExecutableRuns = (kpis.total_succeeded ?? 0) + (kpis.total_failures ?? 0);
  const jobRateIntent = successRateIntent(kpis.job_success_rate ?? 0, kpis.job_failure_rate ?? 0, jobExecutableRuns);
  const childMismatchCount = report.reconciliation.mismatch_count ?? 0;
  const jobStageRows = report.operations.job_status_by_stage ?? [];
  const timezoneName = monitoringTimezone(report);
  return (
    <div className="monitoring-page monitoring-job-page monitoring-job-report">
      <section className="overview-health-strip monitoring-job-health-strip">
        <HealthStripCard
          label="Job runs"
          value={<CompactNumberValue value={kpis.total_jobs ?? 0} />}
          detail={
            <WindowPairDetail
              firstLabel="24h"
              firstValue={<CompactNumberValue value={last24Window.job_runs ?? 0} />}
              firstTone="headline"
              secondLabel="7d"
              secondValue={<CompactNumberValue value={last7Window.job_runs ?? 0} />}
              secondTone="headline"
            />
          }
          title="Job runs in the rolling last 24 hours and last 7 * 24 hours. Executable jobs = succeeded + failed. Skipped jobs are not counted in execution rate."
        />
        <HealthStripCard
          label="Execution rate"
          value={`${formatPercent(kpis.job_success_rate ?? 0)} success`}
          detail={
            <WindowPairDetail
              firstLabel="24h"
              firstValue={`${formatPercent(last24Window.job_success_rate ?? 0)}`}
              firstTone={Number(last24Window.job_success_rate ?? 0) > 0 ? "good" : "warning"}
              secondLabel="7d"
              secondValue={`${formatPercent(last7Window.job_success_rate ?? 0)}`}
              secondTone={Number(last7Window.job_success_rate ?? 0) > 0 ? "good" : "warning"}
            />
          }
          intent={jobRateIntent}
          accent="intent"
          title="Job execution success rate = succeeded / (succeeded + failed). Skipped is excluded. Windows use rolling last 24 hours and last 7 * 24 hours."
          className="overview-health-card-rate"
        />
        <HealthStripCard
          label="Failed jobs"
          value={<CompactNumberValue value={kpis.total_failures ?? 0} />}
          detail={
            <WindowPairDetail
              firstLabel="24h"
              firstValue={<CompactNumberValue value={last24Window.job_failed ?? 0} />}
              firstTone={Number(last24Window.job_failed ?? 0) > 0 ? "bad" : "neutral"}
              secondLabel="7d"
              secondValue={<CompactNumberValue value={last7Window.job_failed ?? 0} />}
              secondTone={Number(last7Window.job_failed ?? 0) > 0 ? "bad" : "neutral"}
            />
          }
          intent={(last24Window.job_failed ?? 0) || (last7Window.job_failed ?? 0) || (kpis.total_failures ?? 0) ? "bad" : "neutral"}
          accent="intent"
          title={latestFailedTime ? `Latest failed job time: ${formatTimestampForDisplay(latestFailedTime, timezoneName)}` : "No failed jobs in current filters."}
        />
        <HealthStripCard
          label="Skipped / Running / Pending"
          value={
            <LifecycleStatusValues
              running={kpis.total_running ?? 0}
              pending={kpis.total_pending ?? 0}
              skipped={kpis.total_skipped ?? 0}
            />
          }
          detail={
            <WindowPairDetail
              firstLabel="24h"
              firstValue={
                <LifecycleStatusValues
                  running={last24Window.job_running ?? 0}
                  pending={last24Window.job_pending ?? 0}
                  skipped={last24Window.job_skipped ?? 0}
                />
              }
              firstTone="neutral"
              secondLabel="7d"
              secondValue={
                <LifecycleStatusValues
                  running={last7Window.job_running ?? 0}
                  pending={last7Window.job_pending ?? 0}
                  skipped={last7Window.job_skipped ?? 0}
                />
              }
              secondTone="neutral"
            />
          }
          intent={(kpis.total_running ?? 0) || (kpis.total_pending ?? 0) ? "warning" : "neutral"}
          accent="intent"
          title="Child dataflow totals from job log fields: total_running / total_pending / total_skipped. These are not recalculated from dataflow logs."
        />
        <HealthStripCard
          label="Job duration"
          value={<DurationHeadline avgSeconds={jobDurationStats.avg_duration_seconds ?? kpis.avg_duration_seconds ?? 0} p50Seconds={jobP50Duration} />}
          detail={durationPercentilesDetail({
            q3_duration_seconds: jobDurationStats.p75_duration_seconds ?? jobDurationStats.q3_duration_seconds ?? 0,
            p95_duration_seconds: jobP95Duration,
            p99_duration_seconds: jobP99Duration
          })}
          intent={durationIntent(jobDurationStats, kpis.avg_duration_seconds ?? 0, kpis.p95_duration_seconds ?? 0)}
          accent="intent"
          title={durationStatsTitle("Job duration", jobDurationStats)}
          className="overview-health-card-duration"
        />
        <HealthStripCard
          label="Dataflow impact"
          value={<><CompactNumberValue value={dataflowKpis.failed ?? 0} /> failed / <CompactNumberValue value={dataflowKpis.total_dataflows ?? 0} /></>}
          detail={
            <DetailMetric
              label="reconcile mismatch checks"
              value={<CompactNumberValue value={childMismatchCount} />}
              tone={childMismatchCount ? "warning" : "neutral"}
            />
          }
          intent={(dataflowKpis.failed ?? 0) ? "bad" : childMismatchCount ? "warning" : "neutral"}
          accent="intent"
          title="Dataflow impact is calculated from dataflow logs linked by job_id. Reconciliation mismatch checks compare job-log totals (total_dataflows, total_failed, total_skipped, total_succeeded) with child dataflow rollups by job_id."
        />
      </section>

      <div className="monitoring-job-content report-layout-table-heavy-3">
        <section className="monitoring-job-primary-grid">
          <ReportPanel
            title="Job status trend by date"
            titleTooltip="Job run status over the selected time range. Success rate line uses succeeded / (succeeded + failed)."
            headerAction={<StatusTrendLegend />}
          >
            <div className="monitoring-job-chart-fill">
              <ReportChart
                option={jobStatusTrendOption(jobTrendRows, filters, report.summary.date_range, timezoneName, report.summary.effective_grain ?? undefined)}
                height="100%"
              />
            </div>
          </ReportPanel>
          <ReportPanel
            title="Job operation duration"
            subtitle="job operation_types"
            titleTooltip="Box plot of job duration grouped by the exact operation_types bundle from each job log record. Array values stay together and display as value_1, value_2. It shows min, Q1, median, Q3, and max duration for completed job runs. Tooltip includes average, P95, count, and status mix."
          >
            <JobDurationByOperationBoxPlot rows={durationByOperationRows} />
          </ReportPanel>
        </section>
        <section className="monitoring-job-secondary-grid">
          <ReportPanel
            title="Job x stage health"
            titleTooltip="Counts distinct job_id values that touched each dataflow stage, then splits those jobs by job-log status. Skipped is not counted as failed."
            headerAction={<StatusHealthLegend />}
          >
            <JobStageHealthPanel rows={jobStageRows} />
          </ReportPanel>
          <ReportPanel
            title="Workload efficiency"
            titleTooltip="Each point is a job_id + dataflow operation_type group. X is total dataflow runs, Y is total dataflow duration, color is operation_type, and point size is rows read divided by duration."
            headerAction={<WorkloadEfficiencyLegend rows={workloadEfficiencyRows} />}
          >
            <JobWorkloadEfficiencyScatter rows={workloadEfficiencyRows} onInspect={onInspect} />
          </ReportPanel>
          <ReportPanel
            title="Job fan-out distribution"
            subtitle="job count by total_dataflows"
            titleTooltip="Histogram from job log totals. Each job_id contributes 1 count. X is total_dataflows from the job log, Y is count of job_id values in each bin."
          >
            <ChildFanoutDistributionPanel rows={childFanoutRows} />
          </ReportPanel>
        </section>
        <ReportPanel
          title={filtered ? "Filtered job runs" : "Job runs"}
          className="monitoring-job-runs-panel"
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
          <JobRunsTable rows={rows} sort={sort} onSort={onSort} onInspect={onInspect} timezoneName={timezoneName} />
        </ReportPanel>
      </div>
    </div>
  );
}
