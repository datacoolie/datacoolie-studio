import type { JobRecord, MonitoringMetric, MonitoringReport } from "../../../shared/api/domainTypes";
import type { MonitoringFilters } from "../monitoringFilters";import { ChildFanoutDistributionPanel, JobDurationByOperationBoxPlot, JobRunsTable, JobStageHealthPanel, JobWorkloadEfficiencyScatter, WorkloadEfficiencyLegend } from "./JobsPageSupport";
import { CompactNumberValue, DetailMetric, DurationHeadline, HealthStripCard, LifecycleStatusValues, ReportChart, ReportPanel, StatusHealthLegend, StatusTrendLegend, type TableSort, TablePager, WindowPairDetail, durationIntent, durationPercentilesDetail, durationStatsTitle, formatPercent, formatTimestampForDisplay, jobStatusTrendOption, metricNumber, metricValue, monitoringTimezone, successRateIntent } from "../components/monitoringPresentation";

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
  const jobDurationStats = (report.operations.job_duration_stats ?? {}) as Record<string, MonitoringMetric>;
  const latestFailedJob = report.operations.latest_failed_job;
  const latestFailedTime = latestFailedJob ? String(latestFailedJob.end_time ?? latestFailedJob.start_time ?? "") : "";
  const durationByOperationRows = report.operations.job_duration_by_operation_types ?? [];
  const jobTrendRows = report.operations.jobs_by_date_status ?? [];
  const workloadEfficiencyRows = report.operations.job_workload_efficiency ?? [];
  const childFanoutRows = report.operations.job_child_fanout_distribution ?? [];
  const last24Window = (report.operations.windows?.last_24_hours ?? {}) as Record<string, unknown>;
  const last7Window = (report.operations.windows?.last_7_days ?? {}) as Record<string, unknown>;
  const metricOr = (row: Record<string, MonitoringMetric>, key: string, fallback: MonitoringMetric | undefined): MonitoringMetric | undefined => row[key] === undefined ? fallback : row[key];
  const jobAvgDuration = metricOr(jobDurationStats, "avg_duration_seconds", kpis.avg_duration_seconds);
  const jobP50Duration = metricOr(jobDurationStats, "p50_duration_seconds", jobAvgDuration);
  const jobP95Duration = metricOr(jobDurationStats, "p95_duration_seconds", kpis.p95_duration_seconds);
  const jobP99Duration = metricOr(jobDurationStats, "p99_duration_seconds", undefined);
  const jobExecutableRuns = metricNumber(kpis.total_succeeded) + metricNumber(kpis.total_failures);
  const jobRateIntent = successRateIntent(metricNumber(kpis.job_success_rate), metricNumber(kpis.job_failure_rate), jobExecutableRuns);
  const childMismatchCount = report.reconciliation.mismatch_count;
  const jobStageRows = report.operations.job_status_by_stage ?? [];
  const timezoneName = monitoringTimezone(report);
  return (
    <div className="monitoring-page monitoring-job-page monitoring-job-report">
      <section className="overview-health-strip monitoring-job-health-strip">
        <HealthStripCard
          label="Job runs"
          value={<CompactNumberValue value={metricValue(kpis, "total_jobs")} />}
          detail={
            <WindowPairDetail
              firstLabel="24h"
              firstValue={<CompactNumberValue value={metricValue(last24Window, "job_runs")} />}
              firstTone="headline"
              secondLabel="7d"
              secondValue={<CompactNumberValue value={metricValue(last7Window, "job_runs")} />}
              secondTone="headline"
            />
          }
          title="Job runs in the rolling last 24 hours and last 7 * 24 hours. Executable jobs = succeeded + failed. Skipped jobs are not counted in execution rate."
        />
        <HealthStripCard
          label="Execution rate"
          value={`${formatPercent(metricValue(kpis, "job_success_rate"))} success`}
          detail={
            <WindowPairDetail
              firstLabel="24h"
              firstValue={`${formatPercent(metricValue(last24Window, "job_success_rate"))}`}
              firstTone={metricNumber(metricValue(last24Window, "job_success_rate")) > 0 ? "good" : "warning"}
              secondLabel="7d"
              secondValue={`${formatPercent(metricValue(last7Window, "job_success_rate"))}`}
              secondTone={metricNumber(metricValue(last7Window, "job_success_rate")) > 0 ? "good" : "warning"}
            />
          }
          intent={jobRateIntent}
          accent="intent"
          title="Job execution success rate = succeeded / (succeeded + failed). Skipped is excluded. Windows use rolling last 24 hours and last 7 * 24 hours."
          className="overview-health-card-rate"
        />
        <HealthStripCard
          label="Failed jobs"
          value={<CompactNumberValue value={metricValue(kpis, "total_failures")} />}
          detail={
            <WindowPairDetail
              firstLabel="24h"
              firstValue={<CompactNumberValue value={metricValue(last24Window, "job_failed")} />}
              firstTone={metricNumber(metricValue(last24Window, "job_failed")) > 0 ? "bad" : "neutral"}
              secondLabel="7d"
              secondValue={<CompactNumberValue value={metricValue(last7Window, "job_failed")} />}
              secondTone={metricNumber(metricValue(last7Window, "job_failed")) > 0 ? "bad" : "neutral"}
            />
          }
          intent={metricNumber(metricValue(last24Window, "job_failed")) || metricNumber(metricValue(last7Window, "job_failed")) || metricNumber(metricValue(kpis, "total_failures")) ? "bad" : "neutral"}
          accent="intent"
          title={latestFailedTime ? `Latest failed job time: ${formatTimestampForDisplay(latestFailedTime, timezoneName)}` : "No failed jobs in current filters."}
        />
        <HealthStripCard
          label="Child flows: Skipped / Running / Pending"
          value={
            <LifecycleStatusValues
              running={kpis.total_running}
              pending={kpis.total_pending}
              skipped={metricValue(kpis, "total_skipped")}
            />
          }
          detail={
            <WindowPairDetail
              firstLabel="24h"
              firstValue={
                <LifecycleStatusValues
              running={metricValue(last24Window, "job_running")}
              pending={metricValue(last24Window, "job_pending")}
              skipped={metricValue(last24Window, "job_skipped")}
                />
              }
              firstTone="neutral"
              secondLabel="7d"
              secondValue={
                <LifecycleStatusValues
              running={metricValue(last7Window, "job_running")}
              pending={metricValue(last7Window, "job_pending")}
              skipped={metricValue(last7Window, "job_skipped")}
                />
              }
              secondTone="neutral"
            />
          }
          intent={kpis.total_running == null || kpis.total_pending == null
            ? "neutral"
            : (metricNumber(kpis.total_running) || metricNumber(kpis.total_pending)) ? "warning" : "neutral"}
          accent="intent"
          title="Child dataflow totals from job log fields: total_running / total_pending / total_skipped. These are not recalculated from dataflow logs. A dash means the job log did not report that total; it is not an observed zero."
        />
        <HealthStripCard
          label="Job duration"
          value={<DurationHeadline avgSeconds={jobAvgDuration} p50Seconds={jobP50Duration} />}
          detail={durationPercentilesDetail({
            q3_duration_seconds: metricOr(jobDurationStats, "p75_duration_seconds", metricOr(jobDurationStats, "q3_duration_seconds", undefined)),
            p95_duration_seconds: jobP95Duration,
            p99_duration_seconds: jobP99Duration
          })}
          intent={durationIntent(jobDurationStats, metricNumber(kpis.avg_duration_seconds), metricNumber(kpis.p95_duration_seconds))}
          accent="intent"
          title={durationStatsTitle("Job duration", jobDurationStats)}
          className="overview-health-card-duration"
        />
        <HealthStripCard
          label="Dataflow impact"
          value={<><CompactNumberValue value={metricValue(dataflowKpis, "failed")} /> failed / <CompactNumberValue value={metricValue(dataflowKpis, "total_dataflows")} /></>}
          detail={
            <DetailMetric
              label="reconcile mismatch checks"
              value={<CompactNumberValue value={childMismatchCount} />}
              tone={metricNumber(childMismatchCount) ? "warning" : "neutral"}
            />
          }
          intent={metricNumber(dataflowKpis.failed) ? "bad" : metricNumber(childMismatchCount) ? "warning" : "neutral"}
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
            titleTooltip="Every operation type with positive duration is included, including maintenance and destination compact/cleanup operations. Each point is a job + operation group: X is dataflow runs, Y is total duration, and color is operation type. Pipeline point size uses rows read/second; maintenance point size uses bytes processed (added + removed)/second. Sizes are normalized within each unit, so compare only points using the same metric. For responsiveness, the chart shows the 500 longest groups plus one representative group per operation type."
            headerAction={<WorkloadEfficiencyLegend rows={workloadEfficiencyRows} />}
          >
            <JobWorkloadEfficiencyScatter rows={workloadEfficiencyRows} />
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
