import { AlertTriangle, BarChart3, Boxes, CircleAlert, CircleCheck, Clock3, FileWarning, Gauge, HardDrive, Info, Table2, TriangleAlert, Workflow } from "lucide-react";
import type { MonitoringMetric, MonitoringReport } from "../../../shared/api/domainTypes";
import type { MetricValue } from "../../../shared/lib/numberFormat";
import { Help } from "../../../shared/components/Help";
import type { MonitoringFilters, MonitoringTabKey } from "../monitoringFilters";import { CompactNumberValue, DurationHeadline, HealthStripCard, ReportChart, ReportPanel, RuntimePhaseContribution, RuntimePhaseLegend, StatusTrendLegend, dataflowStatusTrendOption, durationIntent, durationPercentilesDetail, durationStatsTitle, failureCategoriesRuleTooltip, formatPercent, jobStatusTrendOption, metricNumber, metricValue, runtimePhaseContributionTooltip, successRateIntent } from "../components/monitoringPresentation";
import { EngineProviderHealth, FailureBreakdownDetail, OperationHealthPanel, RateBreakdownDetail, WorkloadVolumeContextPanel, WorkloadVolumeLegend, failureCategoryOption, healthReasonSummary, healthReasonsTooltip, resolveAttentionTarget } from "./MonitoringOverviewPageSupport";

const attentionSeverities = {
  bad: { Icon: CircleAlert, label: "Issue" },
  warning: { Icon: TriangleAlert, label: "Warning" },
  info: { Icon: Info, label: "Information" },
  good: { Icon: CircleCheck, label: "Healthy" },
};

const attentionTargetPages: Record<MonitoringTabKey, { Icon: typeof Gauge; label: string }> = {
  overview: { Icon: Gauge, label: "Overview" },
  jobs: { Icon: Table2, label: "Jobs" },
  dataflows: { Icon: Workflow, label: "Dataflows" },
  failures: { Icon: AlertTriangle, label: "Failures" },
  freshness: { Icon: Clock3, label: "Freshness" },
  performance: { Icon: BarChart3, label: "Performance" },
  volume: { Icon: Boxes, label: "Volume" },
  maintenance: { Icon: HardDrive, label: "Maintenance" },
  diagnostics: { Icon: FileWarning, label: "Diagnostics" },
};

function RunCountHeadline({ jobRuns, dataflowRuns }: { jobRuns: MetricValue; dataflowRuns: MetricValue }) {
  return (
    <>
      <CompactNumberValue value={jobRuns} /><span className="overview-run-count-label"> jobs / </span>
      <CompactNumberValue value={dataflowRuns} /><span className="overview-run-count-label"> flows</span>
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
  const todayWindow = (report.operations.windows?.today ?? {}) as Record<string, unknown>;
  const last7Window = (report.operations.windows?.last_7_days ?? {}) as Record<string, unknown>;
  const jobDurationStats = (report.operations.job_duration_stats ?? {}) as Record<string, MonitoringMetric>;
  const dataflowDurationStats = (report.operations.dataflow_duration_stats ?? {}) as Record<string, MonitoringMetric>;
  const metricOr = (row: Record<string, MonitoringMetric>, key: string, fallback: MetricValue): MetricValue => row[key] === undefined ? fallback : row[key];
  const jobAvgDuration = metricOr(jobDurationStats, "avg_duration_seconds", metricValue(kpis, "avg_duration_seconds"));
  const dataflowAvgDuration = metricOr(dataflowDurationStats, "avg_duration_seconds", metricValue(dataflowKpis, "avg_duration_seconds"));
  const jobP50Duration = metricOr(jobDurationStats, "p50_duration_seconds", jobAvgDuration);
  const dataflowP50Duration = metricOr(dataflowDurationStats, "p50_duration_seconds", dataflowAvgDuration);
  const reportTimezone = report.summary.timezone || "UTC";
  const reportTimezoneSource = report.summary.timezone_source === "configured" ? "Studio override" : "server default";
  const jobExecutableRuns = metricNumber(kpis.total_succeeded) + metricNumber(kpis.total_failures);
  const dataflowExecutableRuns = metricNumber(dataflowKpis.succeeded) + metricNumber(dataflowKpis.failed);
  const jobRateIntent = successRateIntent(metricNumber(kpis.job_success_rate), metricNumber(kpis.job_failure_rate), jobExecutableRuns);
  const dataflowRateIntent = successRateIntent(metricNumber(dataflowKpis.success_rate), metricNumber(dataflowKpis.failure_rate), dataflowExecutableRuns);
  const jobDurationIntent = durationIntent(jobDurationStats, metricNumber(kpis.avg_duration_seconds), metricNumber(kpis.p95_duration_seconds));
  const dataflowDurationIntent = durationIntent(
    dataflowDurationStats,
    metricNumber(dataflowKpis.avg_duration_seconds),
    metricNumber(dataflowKpis.p95_duration_seconds)
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
          detail={<Help as="span" tabIndex={0} content={{title:"Environment health reasons",body:healthReasonsTooltip(report.health.reasons)}}>{`${healthReason.primary}${healthReason.additionalCount > 0 ? ` +${healthReason.additionalCount} reasons` : ""}`}</Help>}
          title={healthReasonsTooltip(report.health.reasons)}
          intent={healthIntent}
          accent="intent"
        />
        <HealthStripCard
          label="Today"
          value={<RunCountHeadline jobRuns={metricValue(todayWindow, "job_runs")} dataflowRuns={metricValue(todayWindow, "dataflow_runs")} />}
          detail={<FailureBreakdownDetail jobFailed={metricValue(todayWindow, "job_failed")} flowFailed={metricValue(todayWindow, "dataflow_failed")} />}
          title={`Today uses the current date in Studio global timezone: ${reportTimezone} (${reportTimezoneSource}).`}
          intent="neutral"
        />
        <HealthStripCard
          label="Last 7d"
          value={<RunCountHeadline jobRuns={metricValue(last7Window, "job_runs")} dataflowRuns={metricValue(last7Window, "dataflow_runs")} />}
          detail={<FailureBreakdownDetail jobFailed={metricValue(last7Window, "job_failed")} flowFailed={metricValue(last7Window, "dataflow_failed")} />}
          title={`Last 7d uses the rolling last 7 * 24 hours in Studio global timezone: ${reportTimezone}.`}
          intent="neutral"
        />
        <HealthStripCard
          label="Job rate"
          value={`${formatPercent(metricValue(kpis, "job_success_rate"))} success`}
          detail={<RateBreakdownDetail failedRate={metricValue(kpis, "job_failure_rate")} windowFailedRate={metricValue(last7Window, "job_failure_rate")} />}
          title="Job success rate = succeeded / (succeeded + failed). Skipped is not counted as failed."
          intent={jobRateIntent}
          accent="intent"
          className="overview-health-card-rate"
        />
        <HealthStripCard
          label="Dataflow rate"
          value={`${formatPercent(metricValue(dataflowKpis, "success_rate"))} success`}
          detail={
            <RateBreakdownDetail
              failedRate={metricValue(dataflowKpis, "failure_rate")}
              windowFailedRate={metricValue(last7Window, "dataflow_failure_rate")}
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
          <ReportPanel title="Job status trend by date" titleTooltip="Job run counts by status and success rate over the selected time range. Hover a date to see its counts and rate." headerAction={<StatusTrendLegend />}>
            <ReportChart
              option={jobStatusTrendOption(jobTrendRows, filters, report.summary.date_range, reportTimezone, report.summary.effective_grain ?? undefined)}
              height="100%"
            />
          </ReportPanel>
          <ReportPanel title="Dataflow status trend by date" titleTooltip="Dataflow run counts by status and success rate over the selected time range. Hover a date to see its counts and rate." headerAction={<StatusTrendLegend />}>
            <ReportChart
              option={dataflowStatusTrendOption(dataflowTrendRows, filters, report.summary.date_range, reportTimezone, report.summary.effective_grain ?? undefined)}
              height="100%"
            />
          </ReportPanel>
        </section>

        <section className="overview-diagnosis-grid">
          <ReportPanel title="Runtime context health" subtitle="engine / provider / platform" titleTooltip="Job health grouped by execution engine, metadata provider and platform in the selected range. Success rate is succeeded jobs divided by all jobs in each group. AVG and P95 use recorded job durations.">
            {runtimeContextRows.length ? (
              <EngineProviderHealth rows={runtimeContextRows} />
            ) : (
              <div className="table-empty">No runtime context signals in current filters.</div>
            )}
          </ReportPanel>
          <ReportPanel title="Operation health" subtitle="job and dataflow operation" titleTooltip="Compares run status counts by operation, with job runs and dataflow runs shown separately. Hover a bar to see its operation and exact counts.">
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
            titleTooltip="Rows read is actual source input. Rows written preserves the transported destination metric across connection types. Lakehouse bytes added/removed come from destination file metrics; chart geometry is approximate for decimal-string metrics and hover tooltips show exact values."
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
              <ReportChart option={failureCategoryOption(report.failures.error_categories.map((item) => ({ ...item, count: metricNumber(item.count) })))} height="100%" wheelDataZoomStep={1} />
            ) : (
              <div className="table-empty">No failed records in current filters.</div>
            )}
          </ReportPanel>
          <ReportPanel
            title="Attention queue"
            titleTooltip="Up to eight signals requiring review, ordered by severity and impact. Each signal shows its own time window or scope. Select a signal to open the related Monitoring page."
            subtitle={`${report.attention.length.toLocaleString("en-US")} signals`}
            className="overview-pulse-attention"
          >
            <div className="overview-attention-compact">
              {attentionItems.length ? (
                attentionItems.map((item) => {
                  const severity = Object.prototype.hasOwnProperty.call(attentionSeverities, item.severity) ? item.severity as keyof typeof attentionSeverities : "info";
                  const { Icon: SeverityIcon, label: severityLabel } = attentionSeverities[severity];
                  const target = resolveAttentionTarget(item.target);
                  const { Icon: TargetIcon, label: targetLabel } = attentionTargetPages[target];
                  return (
                    <button
                      key={item.code}
                      type="button"
                      className={`overview-attention-item attention-${severity}`}
                      aria-label={`${severityLabel}: ${item.title}. ${item.detail}. Open ${targetLabel}.`}
                      onClick={() => onNavigate?.(target)}
                    >
                      <div className="overview-attention-heading"><SeverityIcon size={14} aria-hidden="true" /><strong>{item.title}</strong><TargetIcon className="overview-attention-target-icon" size={14} aria-hidden="true" /></div>
                      <span className="overview-attention-detail">{item.detail}</span>
                    </button>
                  );
                })
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
