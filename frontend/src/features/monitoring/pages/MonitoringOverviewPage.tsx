import type { MonitoringReport } from "../../../shared/api/domainTypes";
import type { MonitoringFilters, MonitoringTabKey } from "../monitoringFilters";import { DurationHeadline, HealthStripCard, ReportChart, ReportPanel, RuntimePhaseContribution, RuntimePhaseLegend, StatusTrendLegend, dataflowStatusTrendOption, durationIntent, durationPercentilesDetail, durationStatsTitle, failureCategoriesRuleTooltip, formatNumber, formatPercent, jobStatusTrendOption, runtimePhaseContributionTooltip, successRateIntent } from "../components/monitoringPrimitives";
import { EngineProviderHealth, FailureBreakdownDetail, OperationHealthPanel, RateBreakdownDetail, WorkloadVolumeContextPanel, WorkloadVolumeLegend, attentionQueueRuleTooltip, failureCategoryOption, healthReasonSummary, healthReasonsTooltip, resolveAttentionTarget } from "./MonitoringOverviewPageSupport";

function RunCountHeadline({ jobRuns, dataflowRuns }: { jobRuns: number; dataflowRuns: number }) {
  return (
    <>
      {formatNumber(jobRuns)}<span className="overview-run-count-label"> jobs / </span>
      {formatNumber(dataflowRuns)}<span className="overview-run-count-label"> flows</span>
    </>
  );
}

export function MonitoringOverviewPage({
  report,
  filters,
  onNavigate
}: {
  report: MonitoringReport;
  filters: MonitoringFilters;
  onNavigate?: (target: MonitoringTabKey) => void;
}) {
  const kpis = report.operations.kpis;
  const dataflowKpis = report.operations.dataflow_kpis;
  const healthIntent =
    report.health.status === "has_issues" || report.health.status === "bad"
      ? "bad"
      : report.health.status === "warning" || report.health.status === "no_log_evidence"
        ? "warning"
        : "good";
  const todayWindow = (report.operations.windows?.today ?? {}) as Record<string, number>;
  const last7Window = (report.operations.windows?.last_7_days ?? {}) as Record<string, number>;
  const jobDurationStats = (report.operations.job_duration_stats ?? {}) as Record<string, number>;
  const dataflowDurationStats = (report.operations.dataflow_duration_stats ?? {}) as Record<string, number>;
  const jobAvgDuration = jobDurationStats.avg_duration_seconds ?? kpis.avg_duration_seconds ?? 0;
  const dataflowAvgDuration = dataflowDurationStats.avg_duration_seconds ?? dataflowKpis.avg_duration_seconds ?? 0;
  const jobP50Duration = jobDurationStats.p50_duration_seconds ?? jobAvgDuration;
  const dataflowP50Duration = dataflowDurationStats.p50_duration_seconds ?? dataflowAvgDuration;
  const reportTimezone = report.summary.timezone || "UTC";
  const reportTimezoneSource = report.summary.timezone_source === "configured" ? "Studio override" : "server default";
  const jobExecutableRuns = (kpis.total_succeeded ?? 0) + (kpis.total_failures ?? 0);
  const dataflowExecutableRuns = (dataflowKpis.succeeded ?? 0) + (dataflowKpis.failed ?? 0);
  const jobRateIntent = successRateIntent(kpis.job_success_rate ?? 0, kpis.job_failure_rate ?? 0, jobExecutableRuns);
  const dataflowRateIntent = successRateIntent(dataflowKpis.success_rate ?? 0, dataflowKpis.failure_rate ?? 0, dataflowExecutableRuns);
  const jobDurationIntent = durationIntent(jobDurationStats, kpis.avg_duration_seconds ?? 0, kpis.p95_duration_seconds ?? 0);
  const dataflowDurationIntent = durationIntent(
    dataflowDurationStats,
    dataflowKpis.avg_duration_seconds ?? 0,
    dataflowKpis.p95_duration_seconds ?? 0
  );
  const healthReason = healthReasonSummary(report.health.reasons);
  const jobTrendRows = report.operations.jobs_by_date_status ?? [];
  const dataflowTrendRows = report.operations.dataflows_by_date_status ?? [];
  const runtimeContextRows = report.operations.jobs_by_engine_provider ?? [];
  const phaseHealthRows = report.operations.phase_health?.length
    ? report.operations.phase_health
    : report.operations.dataflow_status_by_stage ?? report.operations.status_by_stage ?? [];
  const attentionItems = report.attention;
  return (
    <div className="monitoring-page monitoring-overview-report">
      <section className={`overview-health-strip health-${healthIntent}`}>
        <HealthStripCard
          label="Environment health"
          value={report.health.label || report.health.status}
          detail={`${healthReason.primary}${healthReason.additionalCount > 0 ? ` +${healthReason.additionalCount} reasons` : ""}`}
          title={healthReasonsTooltip(report.health.reasons)}
          intent={healthIntent}
          accent="intent"
        />
        <HealthStripCard
          label="Today"
          value={<RunCountHeadline jobRuns={todayWindow.job_runs ?? 0} dataflowRuns={todayWindow.dataflow_runs ?? 0} />}
          detail={<FailureBreakdownDetail jobFailed={todayWindow.job_failed ?? 0} flowFailed={todayWindow.dataflow_failed ?? 0} />}
          title={`Today uses the current date in Studio global timezone: ${reportTimezone} (${reportTimezoneSource}).`}
          intent="neutral"
        />
        <HealthStripCard
          label="Last 7d"
          value={<RunCountHeadline jobRuns={last7Window.job_runs ?? 0} dataflowRuns={last7Window.dataflow_runs ?? 0} />}
          detail={<FailureBreakdownDetail jobFailed={last7Window.job_failed ?? 0} flowFailed={last7Window.dataflow_failed ?? 0} />}
          title={`Last 7d uses the rolling last 7 * 24 hours in Studio global timezone: ${reportTimezone}.`}
          intent="neutral"
        />
        <HealthStripCard
          label="Job rate"
          value={`${formatPercent(kpis.job_success_rate ?? 0)} success`}
          detail={<RateBreakdownDetail failedRate={kpis.job_failure_rate ?? 0} windowFailedRate={last7Window.job_failure_rate ?? 0} />}
          title="Job success rate = succeeded / (succeeded + failed). Skipped is not counted as failed."
          intent={jobRateIntent}
          accent="intent"
          className="overview-health-card-rate"
        />
        <HealthStripCard
          label="Dataflow rate"
          value={`${formatPercent(dataflowKpis.success_rate ?? 0)} success`}
          detail={
            <RateBreakdownDetail
              failedRate={dataflowKpis.failure_rate ?? 0}
              windowFailedRate={last7Window.dataflow_failure_rate ?? 0}
            />
          }
          title="Dataflow success rate = succeeded / (succeeded + failed). Skipped is not counted as failed."
          intent={dataflowRateIntent}
          accent="intent"
          className="overview-health-card-rate"
        />
        <HealthStripCard
          label="Job duration"
          value={<DurationHeadline avgSeconds={jobAvgDuration} p50Seconds={jobP50Duration} />}
          detail={durationPercentilesDetail(jobDurationStats)}
          title={durationStatsTitle("Job duration", jobDurationStats)}
          intent={jobDurationIntent}
          accent="intent"
          className="overview-health-card-duration"
        />
        <HealthStripCard
          label="Dataflow duration"
          value={<DurationHeadline avgSeconds={dataflowAvgDuration} p50Seconds={dataflowP50Duration} />}
          detail={durationPercentilesDetail(dataflowDurationStats)}
          title={durationStatsTitle("Dataflow duration", dataflowDurationStats)}
          intent={dataflowDurationIntent}
          accent="intent"
          className="overview-health-card-duration"
        />
      </section>

      <div className="monitoring-overview-content">
        <section className="overview-trends-grid">
          <ReportPanel title="Job status trend by date" headerAction={<StatusTrendLegend />}>
            <ReportChart
              option={jobStatusTrendOption(jobTrendRows, filters, report.summary.date_range, reportTimezone, report.summary.effective_grain ?? undefined)}
              height="100%"
            />
          </ReportPanel>
          <ReportPanel title="Dataflow status trend by date" headerAction={<StatusTrendLegend />}>
            <ReportChart
              option={dataflowStatusTrendOption(dataflowTrendRows, filters, report.summary.date_range, reportTimezone, report.summary.effective_grain ?? undefined)}
              height="100%"
            />
          </ReportPanel>
        </section>

        <section className="overview-diagnosis-grid">
          <ReportPanel title="Runtime context health" subtitle="engine / provider / platform">
            {runtimeContextRows.length ? (
              <EngineProviderHealth rows={runtimeContextRows} />
            ) : (
              <div className="table-empty">No runtime context signals in current filters.</div>
            )}
          </ReportPanel>
          <ReportPanel title="Operation health" subtitle="job and dataflow operation">
            <OperationHealthPanel
              jobRows={
                report.operations.job_runs_by_dataflow_operation_type?.length
                  ? report.operations.job_runs_by_dataflow_operation_type
                  : report.operations.job_runs_by_operation_type ?? []
              }
              dataflowRows={report.operations.dataflow_runs_by_operation_type ?? []}
            />
          </ReportPanel>
          <ReportPanel
            title="Runtime phase contribution"
            titleTooltip={runtimePhaseContributionTooltip("operation type")}
            headerAction={<RuntimePhaseLegend />}
          >
            {phaseHealthRows.length ? (
              <RuntimePhaseContribution rows={phaseHealthRows} showLegend={false} />
            ) : (
              <div className="table-empty">No phase signals in current filters.</div>
            )}
          </ReportPanel>
        </section>

        <section className="overview-bottom-grid">
          <ReportPanel
            title="Input / output workload"
            titleTooltip="Rows read is actual source input. Estimated rows written preserves observed lakehouse destination rows and estimates successful non-lakehouse writes from rows read. Lakehouse bytes added/removed come from destination file metrics."
            headerAction={<WorkloadVolumeLegend />}
          >
            <WorkloadVolumeContextPanel
              report={report}
              filters={filters}
              dateRange={report.summary.date_range}
              timezoneName={reportTimezone}
              effectiveGrain={report.summary.effective_grain ?? undefined}
            />
          </ReportPanel>
          <ReportPanel
            title="Failure categories"
            titleTooltip={failureCategoriesRuleTooltip()}
          >
            {report.failures.error_categories.length ? (
              <ReportChart option={failureCategoryOption(report.failures.error_categories)} height="100%" wheelDataZoomStep={1} />
            ) : (
              <div className="table-empty">No failed records in current filters.</div>
            )}
          </ReportPanel>
          <ReportPanel
            title="Attention queue"
            subtitle={`${report.attention.length} signals`}
            titleTooltip={attentionQueueRuleTooltip()}
            className="overview-pulse-attention"
          >
            <div className="overview-attention-compact">
              {attentionItems.length ? (
                attentionItems.map((item) => (
                  <button
                    key={item.code}
                    type="button"
                    className={`overview-attention-item attention-${item.severity}`}
                    onClick={() => onNavigate?.(resolveAttentionTarget(item.target))}
                  >
                    <strong>{item.title}</strong>
                    <span>{item.detail}</span>
                  </button>
                ))
              ) : (
                <div className="table-empty">No immediate monitoring issues.</div>
              )}
            </div>
          </ReportPanel>
        </section>
      </div>
    </div>
  );
}
